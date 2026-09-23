#!/usr/bin/env python3
"""
resolve_phases.py — динамический резолвер фаз feature-pipeline.

Аналог GrowthBook runtime feature gating из Claude Code, но в виде
детерминированного Python-скрипта.

Читает:
  - ground/policy.json — основная конфигурация (новый project-wide конфиг)
    [legacy: ground/pipeline.json — fallback с DeprecationWarning в stderr]
  - ground/feature-gates.json — runtime gate flags (дисковый кэш)
  - (опционально) --feature <slug> — контекст фичи для skip_if
    Манифест фичи: ground/statements/<skill>/<slug>/manifest.json
    (skill выводится из пути; проверка manifest.inputs.mode ↔ manifest.skill — exit 3 при mismatch).

Возвращает JSON-массив активных фаз в порядке выполнения.

Использование:
    python resolve_phases.py --project <root>
    python resolve_phases.py --project <root> --feature my-feature --gates ground/feature-gates.json
    python resolve_phases.py --project <root> --list    # показать все фазы с причинами включения

Exit code:
    0 — OK (stdout = JSON)
    1 — ошибка конфига
"""
import argparse, json, os, sys, re, warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_paths  # find_project_root — ре-экспорт из hooks/_project


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"JSON error in {path}: {e}", file=sys.stderr)
        sys.exit(1)


# ── Загрузка project-wide конфига ─────────────────────────────────────────────────────
# Новый канонический путь — ground/policy.json; legacy — ground/pipeline.json
# (использовался до переименования). На fallback эмитим DeprecationWarning —
# единый текст с hooks/_config_loader.py и hooks/risk_ladder.py, чтобы операторы
# видели одинаковое сообщение вне зависимости от того, какой модуль инициировал
# чтение legacy-конфига. Default warning filter дедуплицирует по source location.
_LEGACY_DEPRECATION_MSG = (
    "ground/pipeline.json is deprecated and will be removed in v3.0. "
    "Migrate to ground/policy.json via init_pipeline_config.py --migrate."
)


def _load_policy(root):
    """ground/policy.json → ground/pipeline.json (legacy). Возвращает dict или None.

    None/пустой root / оба файла отсутствуют → None (вызывающий решает — exit 1).
    Битый JSON в любом из файлов → sys.exit(1) через load_json.

    На legacy-фоллбэке — DeprecationWarning вместо stderr print (раньше печатали
    в stderr — менее шумно и согласованно с _config_loader/risk_ladder).
    """
    if not root:
        return None
    policy_path = Path(root) / "ground" / "policy.json"
    if policy_path.is_file():
        return _with_run_snapshot(root, load_json(str(policy_path)))
    legacy_path = Path(root) / "ground" / "pipeline.json"
    if legacy_path.is_file():
        warnings.warn(_LEGACY_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return _with_run_snapshot(root, load_json(str(legacy_path)))
    return None


def _with_run_snapshot(root, cfg):
    """Наложить снимок политики живого прогона (см. _config_loader.apply_policy_snapshot).

    Резолвер фаз решает, какие фазы вообще существуют в прогоне (quality.eval_enabled,
    jira.enabled). Читай он текущий policy.json, а гейты — снимок прогона, фазовая машина
    расходилась бы с enforcement'ом на ровном месте.

    Импорт внутри функции: hooks/ попадает в sys.path при импорте _phase_eligibility ниже
    по модулю, а _load_policy определён выше него.
    """
    if not isinstance(cfg, dict):
        return cfg
    try:
        from _config_loader import apply_policy_snapshot
    except Exception:  # noqa: BLE001 — кривой деплой не должен ронять резолв фаз
        return cfg
    return apply_policy_snapshot(root, cfg)


# ── Маппинг mode → допустимые skill (для manifest.inputs.mode ↔ manifest.skill) ─────
# Фича кладётся в ground/statements/<SKILL>/<slug>/; её manifest.inputs.mode должен
# согласовываться с manifest.skill (защита от «запустили forgefix-фичу с mode=forgelite» —
# разные ветки пайплайна, разные гейты/судьи, смешивать нельзя). mode отсутствует/None —
# пропуск (default mode, обратная совместимость со старыми манифестами без inputs).
_MODE_TO_SKILLS = {
    "forgefix":        {"forgefix"},
    "forgelite":       {"forgelite"},
    "feature-pipeline": {"feature-pipeline"},
}


def _find_manifest(root, feature_slug):
    """Найти манифест фичи по ground/statements/<SKILL>/<slug>/manifest.json.

    Возвращает (skill_from_path, manifest_dict) или (None, None), если манифест не найден
    либо найдено несколько (неоднозначность — не угадываем, вызывающий решает).
    """
    if not root or not feature_slug:
        return None, None
    base = Path(root) / "ground" / "statements"
    if not base.is_dir():
        return None, None
    candidates = list(base.glob(f"*/{feature_slug}/manifest.json"))
    if len(candidates) != 1:
        return None, None
    path = candidates[0]
    # path.parts: [..., 'ground', 'statements', <SKILL>, <slug>, 'manifest.json'] → [-3]
    skill_from_path = path.parts[-3]
    return skill_from_path, load_json(str(path))


def _validate_mode_skill(manifest):
    """Проверка manifest.inputs.mode ↔ manifest.skill. None — OK; иначе строка ошибки.

    mode отсутствует/None → пропуск (default mode, обратная совместимость).
    mode вне _MODE_TO_SKILLS → ошибка (unknown mode).
    mode="X", manifest.skill не в _MODE_TO_SKILLS[X] → ошибка (mismatch).
    """
    if not isinstance(manifest, dict):
        return None
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        return None
    mode = inputs.get("mode")
    if not mode:
        return None
    allowed_skills = _MODE_TO_SKILLS.get(mode)
    if allowed_skills is None:
        return (f"unknown inputs.mode={mode!r} "
                f"(allowed: {sorted(_MODE_TO_SKILLS)})")
    declared = manifest.get("skill")
    if declared not in allowed_skills:
        return (f"inputs.mode={mode!r} требует manifest.skill ∈ "
                f"{sorted(allowed_skills)}, но manifest.skill={declared!r}")
    return None


# ── Единая проверка фазы (enabled_by / skip_if) — DRY-извлечение ─────────────────
# resolve_phases.resolve_phases() и pipeline_phases.live_phase_decision() используют
# одну функцию phase_eligibility(). Раньше live-снимок игнорировал enabled_by/skip_if
# и расходился с резолвером → фазовый снимок застревал на опорожнённой фазе (п.8
# KIDPPRB-9254). Расширения (env:VAR-префикс) — additive: старые выражения ведут
# себя идентично.
from _phase_eligibility import (  # noqa: F401 — re-export для обратной совместимости
    ENABLED_BY_DEFAULTS,
    ShouldExecute,
    _evaluate_enabled_by,
    _evaluate_skip_if,
    _resolve_jpath,
    phase_eligibility,
)


# Мастер-флаг бизнес-анализа — единый источник pipeline_phases.BRD_ENABLED. Импорт защищённый:
# если pipeline_phases недоступен рядом (кривой деплой) — считаем BRD выключенным (безопасный
# дефолт: пайплайн стартует с 02-sdd, а не молча включает бизнес-анализ).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import pipeline_phases as _pp
    _BRD_ENABLED = bool(getattr(_pp, "BRD_ENABLED", False))
except Exception:
    _BRD_ENABLED = False

# База фаз пайплайна (id/порядок — стабильны; phases_override в pipeline.json может дополнить).
# id фаз ДОЛЖНЫ быть подмножеством pipeline_phases.MAIN_PHASES и идти в каноническом порядке —
# это пинит test_phase_consistency (раньше resolve_phases был вторым нескоординированным
# источником списка фаз).
# 04-tdd: enabled_by НЕТ намеренно. `quality.tdd` — ручка про ПОРЯДОК внутри фазы (писать
# ли тесты до кода), а не про наличие фазы: завязка на неё вырезала фазу Build целиком, и
# при tdd:false пайплайн уходил с дизайна прямо в Verify, ни разу не написав код. Флаг
# живёт в двух местах, где он и должен: 02-design/add_steps не заводят шаги `04-test-*`,
# а tdd-guard не требует RED перед записью в src/main.
# 00-brd: enabled_by завязан на BRD_ENABLED — при выключенном BRD фаза остаётся в списке
# (подмножество MAIN_PHASES для test_phase_consistency), но резолвится в skipped.
DEFAULT_PHASES = [
    {"id": "00-brd",          "skill": "business-requirements", "enabled_by": (None if _BRD_ENABLED else False), "skip_if": None,   "gates": ["brd"],        "description": "Discovery / BRD"},
    {"id": "01-grounding",    "skill": "project-grounder",      "enabled_by": None,              "skip_if": "grounding.exists", "gates": None,       "description": "System overview ensured"},
    {"id": "02-sdd",          "skill": "sdd",                   "enabled_by": None,              "skip_if": None,           "gates": ["sdd"],        "description": "SDD specification"},
    {"id": "02-design",       "skill": "tech-design",           "enabled_by": None,              "skip_if": None,           "gates": ["design"],     "description": "Tech design + task plan"},
    {"id": "02-eval-plan",    "skill": None,                    "enabled_by": "quality.eval_enabled", "skip_if": None,     "gates": None,           "description": "Eval-plan generated"},
    {"id": "03-jira",         "skill": "jira-task-writer",      "enabled_by": "jira.enabled",     "skip_if": None,           "gates": ["jira"],       "description": "Jira issues created"},
    {"id": "04-tdd",          "skill": "java-spring-dev",       "enabled_by": None,              "skip_if": None,           "gates": None,           "description": "Build per task (TDD RED→GREEN при quality.tdd)"},
    {"id": "05-verify",       "skill": None,                    "enabled_by": None,              "skip_if": None,           "gates": None,           "description": "Full test run + coverage"},
    {"id": "06-document",     "skill": None,                    "enabled_by": None,              "skip_if": None,           "gates": None,           "description": "Spec updated"},
]
# Бриф фазы (оркестрационная инструкция) — читается оркестратором ПЕРЕД фазой.
# Путь относительно каталога скилла feature-pipeline; переопределяем через phases_override.
for _p in DEFAULT_PHASES:
    _p["brief"] = f"references/phases/{_p['id']}.md"


def resolve_phases(project_root, feature_slug=None, gates_path=None):
    """Основная функция: возвращает список активных фаз."""
    pipeline = _load_policy(project_root)
    if not pipeline:
        print("policy.json (or legacy pipeline.json) not found", file=sys.stderr)
        sys.exit(1)

    gates = load_json(gates_path) if gates_path else None
    if gates_path and gates is None:
        print(f"feature-gates.json not found at {gates_path}, using defaults", file=sys.stderr)
        gates = {"gates": {}}

    feature_ctx = {}
    if feature_slug:
        _path_skill, manifest = _find_manifest(project_root, feature_slug)
        if manifest:
            # manifest.inputs.mode ↔ manifest.skill: ловим «запустили forgefix-фичу с
            # mode=forgelite» и подобные mismatch'и — разные ветки пайплайна, смешивать нельзя.
            err = _validate_mode_skill(manifest)
            if err:
                print(f"mode/skill mismatch для фичи '{feature_slug}': {err}",
                      file=sys.stderr)
                sys.exit(3)
            feature_ctx = manifest.get("context", {})

    # База фаз — модульная константа DEFAULT_PHASES (порядок/id пинятся test_phase_consistency
    # против pipeline_phases.MAIN_PHASES). Копируем, чтобы phases_override не мутировал константу.
    phases_definitions = [dict(p) for p in DEFAULT_PHASES]

    # Позволяем pipeline.json переопределить фазы через phases_override
    override = pipeline.get("phases_override")
    if override:
        override_index = {p["id"]: p for p in override}
        for i, phase in enumerate(phases_definitions):
            if phase["id"] in override_index:
                phases_definitions[i] = {**phase, **override_index[phase["id"]]}
        # Новые id (которых нет в DEFAULT_PHASES) ДОБАВЛЯЮТСЯ — так работает
        # «добавить новую фазу без правки кода скилла» (config.py phase add).
        # Позиция — ключ "after": "<phase-id>" (вставка сразу после), без него — в конец.
        # Сортировать по id нельзя: канонический порядок не лексикографический
        # (02-sdd → 02-design → 02-eval-plan).
        known = {p["id"] for p in phases_definitions}
        for entry in override:
            if entry.get("id") in known or not entry.get("id"):
                continue
            new_phase = {
                "skill": None, "enabled_by": None, "skip_if": None,
                "gates": None, "description": "",
                "brief": f"references/phases/{entry['id']}.md",
                **entry,
            }
            after = new_phase.pop("after", None)
            idx = len(phases_definitions)
            if after:
                for i, p in enumerate(phases_definitions):
                    if p["id"] == after:
                        idx = i + 1
                        break
            phases_definitions.insert(idx, new_phase)
            known.add(entry["id"])

    result = []
    skipped = []
    for phase in phases_definitions:
        # enabled_by + skip_if — ЕДИНЫЙ предикат (см. _phase_eligibility.phase_eligibility).
        # Раньше инлайн-логика здесь и в pipeline_phases.live_phase_decision расходилась:
        # резолвер видел отключённую фазу и выкидывал её, а live-снимок её показывал
        # как current → фазовый синхростейк застревал (п.8 KIDPPRB-9254).
        eligibility = phase_eligibility(phase, pipeline, gates, feature_ctx)
        if not eligibility.should_execute:
            skipped.append({"id": phase["id"], "reason": eligibility.skip_reason})
            continue

        result.append({
            "id": phase["id"],
            "skill": phase["skill"],
            "gates": phase.get("gates", []),
            "description": phase.get("description", ""),
            "brief": phase.get("brief", f"references/phases/{phase['id']}.md"),
        })

    return {"phases": result, "skipped": skipped, "total": len(result), "skipped_count": len(skipped)}


def current_phase(project_root, feature_slug, gates_path=None):
    """«Где я и что читать» одним вызовом: текущая фаза из live-снимка pipeline-state
    (pipeline_phases.live_phase_decision — источник истины manifest, не кэш gate.json)
    + бриф/гейты фазы из активного реестра."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pipeline_phases

    _path_skill, manifest = _find_manifest(project_root, feature_slug)
    if manifest is None:
        print(f"manifest.json фичи '{feature_slug}' не найден — сначала init.py", file=sys.stderr)
        sys.exit(1)
    # Та же валидация mode↔skill, что и в resolve_phases — вызывается при --current --feature.
    err = _validate_mode_skill(manifest)
    if err:
        print(f"mode/skill mismatch для фичи '{feature_slug}': {err}",
              file=sys.stderr)
        sys.exit(3)
    decision = pipeline_phases.live_phase_decision(manifest)
    cur = decision.get("current_phase", "")

    resolved = resolve_phases(project_root, feature_slug, gates_path)
    # Фазы вне конфигурации (02-eval-plan при quality.eval_enabled=false, 03-jira при
    # jira.enabled=false, 00-brd при BRD_ENABLED=false) исключаем из live-снимка: иначе
    # фазовый синхростейк застревал на опорожнённой фазе и не двигался дальше (п.8
    # KIDPPRB-9254). Пересчитываем решение с учётом активного множества фаз.
    enabled_ids = {p["id"] for p in resolved["phases"]}
    if enabled_ids:
        decision = pipeline_phases.live_phase_decision(manifest, enabled_phases=enabled_ids)
        cur = decision.get("current_phase", "")
    info = next((p for p in resolved["phases"] if p["id"] == cur), None)
    return {
        "current_phase": cur,  # "" — все фазы завершены
        "brief": (info or {}).get("brief", f"references/phases/{cur}.md" if cur else None),
        "gates": (info or {}).get("gates") or [],
        "skill": (info or {}).get("skill"),
        "done": cur == "",
    }


def main():
    parser = argparse.ArgumentParser(description="Resolve active feature-pipeline phases")
    parser.add_argument("--project", default=None, help="Project root directory")
    parser.add_argument("--feature", help="Feature slug (for skip_if context)")
    parser.add_argument("--gates", help="Path to feature-gates.json")
    parser.add_argument("--list", action="store_true", help="Show all phases with reasons")
    parser.add_argument("--current", action="store_true",
                        help="Текущая фаза + бриф (требует --feature): {current_phase, brief, gates}")
    args = parser.parse_args()
    # Корень: явный --project > live-маркеры (.git/build.gradle/pipeline.json) вверх от cwd.
    if not args.project:
        args.project = skill_paths.find_project_root()

    if args.current:
        if not args.feature:
            print("--current требует --feature <slug>", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(current_phase(args.project, args.feature, args.gates),
                         indent=2, ensure_ascii=False))
        return

    result = resolve_phases(args.project, args.feature, args.gates)

    if args.list:
        print(f"Total active phases: {result['total']}, skipped: {result['skipped_count']}")
        print()
        for p in result["phases"]:
            gates_str = f" gates=[{','.join(p['gates'])}]" if p["gates"] else ""
            skill_str = f" skill={p['skill']}" if p["skill"] else ""
            print(f"  ✅ {p['id']}: {p['description']}{skill_str}{gates_str}")
        for s in result["skipped"]:
            print(f"  ⏭️  {s['id']}: {s['reason']}")
        return

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()