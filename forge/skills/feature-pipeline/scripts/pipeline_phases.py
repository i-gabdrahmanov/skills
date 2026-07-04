#!/usr/bin/env python3
"""pipeline_phases.py — ЕДИНЫЙ источник истины фазовой машины feature-pipeline.

Здесь и только здесь живут:
  • PREFIX_PHASE / MAIN_PHASES / REQUIRED_JUDGES_MASK
  • guess_phase / match_required_judges
  • метаданные фаз (allowed_skills / blocked_tools / blocked_paths / required_artifacts)
  • build_gate / build_defs — единственная реализация «manifest/steps → gate.json/phase-defs»
  • active_feature / gate_dir — резолв per-feature стейта (для namespacing gate)

Все скрипты (add_steps, preflight-validate, phase_sync, init_phase_gate) импортируют отсюда,
чтобы PREFIX_PHASE/порядок фаз/маска судей не расходились между копиями (раньше так ловили
06-doc и неканонический порядок фаз). Хуки (другая база деплоя) импортируют best-effort с
inline-fallback, который пинится тестом test_phase_consistency.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

# ── Маппинг префиксов шагов → фазы ────────────────────────────────────
PREFIX_PHASE = {
    "00-": "00-brd",
    "01-": "01-grounding",
    "02-sdd": "02-sdd",                   # спецификация (BRD → sdd.md), до tech-design
    "02-eval-plan": "02-eval-plan",       # отдельная фаза между design и jira
    "02-": "02-design",
    "03-": "03-jira",
    "04-": "04-tdd",
    "05-": "05-verify",
    "06-": "06-document",
    "07-deliver-": "07-deliver",
    "07-report": "07-report",
    "07-": "07-deliver",
}

# Главные фазы в КАНОНИЧЕСКОМ порядке (по нему сортируется gate, а не по появлению шагов).
MAIN_PHASES = ["00-brd", "01-grounding", "02-sdd", "02-design", "02-eval-plan",
               "03-jira", "04-tdd", "05-verify", "06-document",
               "07-deliver", "07-report"]

# Маска судей по id шага. ЕДИНЫЙ источник — references/judges-registry.json (pipeline-state),
# читается через judges_registry. Раньше маска дублировалась здесь, в init.py и
# patch_manifest_judges.py и расходилась (00-brd добавили не во все копии). Имена судей ДОЛЖНЫ
# совпадать с вердиктами run_judge.py (<phase>-judge.json).
_PSTATE_SCRIPTS = Path(__file__).resolve().parents[1].parent / "pipeline-state" / "scripts"
if str(_PSTATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PSTATE_SCRIPTS))
try:
    import judges_registry as _judges_registry
    REQUIRED_JUDGES_MASK = _judges_registry.step_masks()
except Exception as _e:  # реестр недоступен (pipeline-state не развёрнут рядом) — деградируем мягко
    REQUIRED_JUDGES_MASK = {}
    # Это мягкий fail-open enforcement: без маски шаги закрываются без судейских гейтов.
    # Раньше падение глоталось молча — теперь оно видно (срабатывает лишь при кривом деплое).
    print(f"[pipeline_phases] WARNING: judges-registry недоступен ({_e}) — "
          f"REQUIRED_JUDGES_MASK пуст, судейские гейты не форсятся.", file=sys.stderr)


def guess_phase(step_id: str) -> str:
    """id шага ('04-test-foo') → id фазы ('04-tdd'). Длинный префикс побеждает."""
    if not isinstance(step_id, str):
        return ""  # малформед-манифест (None/число вместо id) — не роняем фазовую машину
    for prefix, phase in sorted(PREFIX_PHASE.items(), key=lambda x: -len(x[0])):
        if step_id.startswith(prefix):
            return phase
    return step_id


def is_container_step(step_id: str) -> bool:
    """Container-шаг — main-phase placeholder, чей id ТОЧНО совпадает с фазой ('04-tdd').
    Его собственный статус не отражает завершённость динамических шагов фазы
    (04-test-T1/04-build-T1), поэтому при наличии динамических шагов он исключается из
    расчёта завершённости фазы. ЕДИНОЕ определение — им же пользуется phase_sync."""
    return step_id in MAIN_PHASES


# ── Соглашения об id динамических шагов (ЕДИНЫЙ источник; копии в хуках пинит ───────────
#    test_phase_consistency). Раньше эти префиксы были «магическими строками» в eval-guard,
#    tdd-guard, update._check_subagent_origin, preflight — переименуй в одном месте,
#    enforcement тихо отвалится.
BUILD_STEP_PREFIX = "04-build-"      # 04-build-<taskId> — GREEN-фаза задачи (пишет src/main)
TEST_STEP_PREFIX = "04-test-"        # 04-test-<taskId>  — RED-фаза задачи (пишет src/test)
DELIVER_STEP_PREFIX = "07-deliver-"  # 07-deliver-<taskId> — доставка задачи (PR/commit)

# Фазы, ОБЯЗАННЫЕ исполняться субагентом (не inline). Совпадает с префиксами шагов.
# Хвост lite-* — плоские шаги lite-ветки (forgelite): RED/GREEN/verify тоже идут субагентом.
SUBAGENT_PHASE_PREFIXES = ("02-sdd", "02-design", "04-test", "04-build", "05-tests", "06-spec",
                           "lite-design", "lite-red", "lite-green", "lite-verify")

# Фазы, закрытие которых требует gate-result артефакта (gates/<step_id>.json от record_gate.py):
# «шаг закрыт, потому что детерминированный гейт РЕАЛЬНО прошёл», а не потому что субагент
# вернул status:"completed". Подмножество SUBAGENT_PHASE_PREFIXES — только код/тесты/сборка.
GATE_RESULT_PREFIXES = ("04-test", "04-build", "05-tests",
                        "lite-red", "lite-green", "lite-verify")


def requires_gate_result(step_id) -> bool:
    """Требует ли закрытие шага evidence-артефакта детерминированного гейта."""
    return isinstance(step_id, str) and step_id.startswith(GATE_RESULT_PREFIXES)


# Обязательные шаги: их НЕЛЬЗЯ тихо пропустить (status=skipped) без override — иначе fallback
# «не смог спросить → пропущу фазу» тихо выкидывает качество-гейты (Thrust 1: fallback=STOP).
# grounding/brd/report сюда НЕ входят (grounding легитимно reuse-skip, report — пост-доставка).
# lite-design уже в SUBAGENT_PHASE_PREFIXES (tech-design обязан идти субагентом).
REQUIRED_STEP_PREFIXES = SUBAGENT_PHASE_PREFIXES


def requires_no_silent_skip(step_id) -> bool:
    """True — шаг обязательный, skip только через override (иначе exit 3 ESCALATE)."""
    return isinstance(step_id, str) and step_id.startswith(REQUIRED_STEP_PREFIXES)


def _task_id_after(step_id, prefix: str):
    """task-id из id шага по префиксу ('04-build-T1' → 'T1'); иначе None."""
    if isinstance(step_id, str) and step_id.startswith(prefix):
        return step_id[len(prefix):] or None
    return None


def build_task_id(step_id):
    """task-id из build-шага ('04-build-T1' → 'T1'), иначе None."""
    return _task_id_after(step_id, BUILD_STEP_PREFIX)


def test_task_id(step_id):
    """task-id из RED-test-шага ('04-test-T1' → 'T1'), иначе None."""
    return _task_id_after(step_id, TEST_STEP_PREFIX)


def deliver_task_id(step_id):
    """task-id из delivery-шага ('07-deliver-T1' → 'T1'), иначе None."""
    return _task_id_after(step_id, DELIVER_STEP_PREFIX)


def is_build_step(step_id) -> bool:
    return build_task_id(step_id) is not None


def requires_subagent(step_id) -> bool:
    """Должен ли шаг исполняться субагентом (а не inline-оркестратором)."""
    return isinstance(step_id, str) and step_id.startswith(SUBAGENT_PHASE_PREFIXES)


def match_required_judges(step_id: str) -> list:
    """required_judges для шага по маске (точное совпадение → wildcard *)."""
    if step_id in REQUIRED_JUDGES_MASK:
        return list(REQUIRED_JUDGES_MASK[step_id])
    for mask, judges in REQUIRED_JUDGES_MASK.items():
        if mask.endswith("*") and step_id.startswith(mask[:-1]):
            return list(judges)
    return []


# ── Метаданные фаз (phase-defs) ───────────────────────────────────────
def blocked_tools(phase_id: str) -> list:
    return ["Read", "GrepSearch", "Glob", "Grep"] if phase_id == "01-grounding" else []


def blocked_paths(phase_id: str) -> list:
    return ["src/"] if phase_id == "01-grounding" else []


def allowed_skills(phase_id: str) -> list:
    return {
        "00-brd":       ["brd-grounder", "brd-interview", "business-requirements"],
        "01-grounding": ["project-grounder", "system-analyst", "Explore"],
        "02-sdd":       ["sdd"],
        "02-design":    ["tech-design"],
        "02-eval-plan": ["general-purpose"],
        "03-jira":      ["jira-task-writer"],
        "04-tdd":       ["java-spring-dev", "bugfix-developer", "minor-defect-fix", "Explore"],
        "05-verify":    ["Explore"],
        "06-document":  ["general-purpose", "Explore"],
        "07-deliver":   ["Explore"],
        "07-report":    ["Explore"],
    }.get(phase_id, [])


def required_artifacts(phase_id: str) -> list:
    return {
        "00-brd":       ["docs/brd.md"],
        "01-grounding": ["ground/grounding-index.json"],
        "02-sdd":       ["docs/sdd.md"],
        "02-design":    ["docs/task-plan.json", "docs/tech-design.md"],
        "02-eval-plan": ["docs/eval-plan.json",
                         "ground/statements/feature-pipeline/**/judges/eval-judge.json"],
    }.get(phase_id, [])


def _ordered_unique_phases(steps: list) -> list:
    """Уникальные фазы по шагам, отсортированные по каноническому MAIN_PHASES."""
    seen = []
    for step in steps:
        pid = guess_phase(step.get("id", ""))
        if pid not in seen:
            seen.append(pid)
    seen.sort(key=lambda p: MAIN_PHASES.index(p) if p in MAIN_PHASES else 999)
    return seen


def build_gate(steps: list, manifest: Optional[dict] = None,
               existing_meta: Optional[dict] = None,
               defs_meta: Optional[dict] = None) -> dict:
    """Единственная реализация «steps/manifest → gate.json».

    existing_meta: {phase_id: {"skip_allowed": bool}} — сохранить ранее заданные значения.
    defs_meta:     {phase_id: {"required_artifacts": [...]}} — из phase-defs (иначе дефолт).
    Порядок фаз — КАНОНИЧЕСКИЙ (MAIN_PHASES), статусы восстанавливаются из манифеста.
    """
    existing_meta = existing_meta or {}
    defs_meta = defs_meta or {}
    step_status = {}
    if manifest:
        step_status = {s["id"]: s["status"] for s in manifest.get("steps", [])}

    phases = []
    for pid in _ordered_unique_phases(steps):
        em = existing_meta.get(pid, {})
        dm = defs_meta.get(pid, {})
        phases.append({
            "id": pid,
            "label": next((s.get("title", pid) for s in steps if guess_phase(s.get("id", "")) == pid), pid),
            "skip_allowed": em.get("skip_allowed", pid != "01-grounding"),
            "status": "pending",
            "depends_on": [],
            "artifacts": dm.get("required_artifacts", required_artifacts(pid)),
        })

    # depends_on — каждая главная фаза зависит от предыдущей главной
    present = [p["id"] for p in phases]
    main_order = [m for m in MAIN_PHASES if m in present]
    for i, pid in enumerate(main_order):
        if i == 0:
            continue
        for p in phases:
            if p["id"] == pid:
                p["depends_on"].append(main_order[i - 1])
                break

    # Статусы из манифеста (ЕДИНАЯ семантика, ею же пользуется phase_sync): фаза completed,
    # если все её ДИНАМИЧЕСКИЕ шаги completed/skipped. Container-шаг (04-tdd и т.п.) не
    # учитывается, пока есть динамические; если динамических нет — смотрим по самому container.
    if step_status:
        for phase in phases:
            dynamic = [s["id"] for s in steps
                       if guess_phase(s.get("id", "")) == phase["id"]
                       and not is_container_step(s["id"])]
            if dynamic:
                if all(step_status.get(sid) in ("completed", "skipped") for sid in dynamic):
                    phase["status"] = "completed"
            elif step_status.get(phase["id"]) in ("completed", "skipped"):
                phase["status"] = "completed"

    # current_phase — первая не-completed (по каноническому порядку)
    current_phase = ""
    for phase in phases:
        if phase["status"] != "completed":
            current_phase = phase["id"]
            phase["status"] = "in_progress"
            break

    return {
        "pipeline_id": (manifest or {}).get("pipeline_id", ""),
        "feature": (manifest or {}).get("feature",
                    ((manifest or {}).get("context") or {}).get("feature", "")),
        "schema": "phase-gate@1",
        "current_phase": current_phase,
        "phases": phases,
    }


def live_phase_decision(manifest: Optional[dict]) -> dict:
    """Живой фазовый снимок из manifest (источник истины), без чтения gate.json с диска.

    gate.json — лишь кэш этого расчёта; его персистентный статус может устареть, если sync
    был пропущен/упал. Поэтому решения фазовой машины (current_phase + статусы фаз) считаем
    ОТСЮДА. Возвращает {"current_phase": str, "phases": [...]} — та же деривация, что build_gate.
    """
    steps = (manifest or {}).get("steps", [])
    gate = build_gate(steps, manifest)
    return {"current_phase": gate.get("current_phase", ""), "phases": gate.get("phases", [])}


def build_defs(steps: list) -> dict:
    """Единственная реализация «steps → phase-defs.json»."""
    defs = []
    for pid in _ordered_unique_phases(steps):
        defs.append({
            "id": pid,
            "allowed_skills": allowed_skills(pid),
            "blocked_tools_until_complete": blocked_tools(pid),
            "blocked_paths": blocked_paths(pid),
            "required_artifacts": required_artifacts(pid),
        })
    return {"schema": "phase-defs@1", "phases": defs}


# ── Per-feature резолв стейта (C1: gate под фичу) ─────────────────────
def active_feature(root: Path, skill: str = "feature-pipeline") -> str:
    """Активная фича = самый свежий manifest.json в ground/statements/<skill>/<feature>/.
    'pipeline' (back-compat) если ни одного манифеста нет."""
    base = Path(root) / "ground" / "statements" / skill
    if not base.is_dir():
        return "pipeline"
    best, best_mtime = None, -1.0
    for d in base.iterdir():
        if not d.is_dir() or d.name == "archived":
            continue
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        try:
            mtime = mp.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = d.name, mtime
    return best or "pipeline"


def gate_dir(root: Path, feature: str) -> Path:
    """Каталог фазовой машины фичи: ground/phases/<feature>/."""
    return Path(root) / "ground" / "phases" / feature


def gate_path(root: Path, feature: str) -> Path:
    """Путь к gate.json фичи; при отсутствии — legacy ground/phases/gate.json (back-compat)."""
    per_feature = gate_dir(root, feature) / "gate.json"
    if per_feature.exists():
        return per_feature
    legacy = Path(root) / "ground" / "phases" / "gate.json"
    if legacy.exists():
        return legacy
    return per_feature  # для записи нового


def defs_path(root: Path, feature: str) -> Path:
    per_feature = gate_dir(root, feature) / "phase-defs.json"
    if per_feature.exists():
        return per_feature
    legacy = Path(root) / "ground" / "phases" / "phase-defs.json"
    if legacy.exists():
        return legacy
    return per_feature
