#!/usr/bin/env python3
"""inline-phase-guard.py — actor-aware PreToolUse хук: не дать ГЛАВНОМУ агенту делать
productive-работу subagent-only фазы inline.

Проблема, которую закрывает: на прогоне модель сама писала артефакты/код фазы и закрывала
шаги, не вызывая субагентов — а без субагента нет SubagentStop, значит молчат хуки проверки
состояния. SKILL.md форсит субагентов только guidance'ом; этот хук переводит требование в
enforcement.

Чем отличается от sod-enforcer: тот проверяет РОЛЬ активной фазы (что действие не выходит за
её границы), но НЕ проверяет, КТО действует — и для фаз 02-design/04-build роль вообще без
ограничений по путям, поэтому оркестратор пишет tech-design.md / *.java inline безнаказанно.
Здесь же ключ — `agent_type`: пусто = главный агент (оркестратор); непусто = субагент.

Изначальный subagent-enforcer удалили, т.к. PreToolUse срабатывает и ВНУТРИ субагента и
блокировал бы сам субагент. Решение: блокируем ТОЛЬКО когда agent_type пуст (главный агент).

Логика:
  активный (in_progress) шаг — subagent-only фаза (pipeline_phases.requires_subagent)
  И agent_type пуст (главный агент)
  И действие — productive-работа этой фазы (см. _is_phase_work)
  → BLOCK (exit 2 + stderr).

Escape-hatch (деградация, когда agent() реально недоступен): overrides/subagent-origin.json
активной фичи — снимает блок с предупреждением (как у судей).

fail-open везде: нет манифеста/активного шага/не subagent-фаза/не-JSON stdin → exit 0.
Хук не должен ронять прогон.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# risk_ladder (co-located) — те же резолверы project_root/active_manifest, что у sod/gate-guard.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import risk_ladder as _R
except Exception:  # pragma: no cover
    _R = None

# Единый источник истины «какие фазы обязаны идти субагентом» — pipeline_phases.
# best-effort импорт + inline-fallback (как в update.py), чтобы переименование префикса
# в одном месте не отключало enforcement молча.
_SUBAGENT_PREFIXES = ("02-sdd", "02-design", "04-test", "04-build", "05-tests", "06-spec",
                      "lite-red", "lite-green", "lite-verify")
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "feature-pipeline" / "scripts"))
    import pipeline_phases as _pp
    _requires_subagent = _pp.requires_subagent
    _SUBAGENT_PREFIXES = _pp.SUBAGENT_PHASE_PREFIXES
except Exception:
    def _requires_subagent(step_id) -> bool:
        return isinstance(step_id, str) and step_id.startswith(tuple(_SUBAGENT_PREFIXES))

# Команда сборки/тестов — Gradle ИЛИ Maven (как в sod-enforcer.BUILD_CMD_RE).
BUILD_CMD_RE = r"(?:\./gradlew\s+|\bmvn\b)"

# Bash control-plane — оркестратору эти команды можно даже в subagent-фазе (это управление
# состоянием/гейтами/судьями, а не productive-работа фазы). Блок только для productive bash.
_CONTROL_BASH_RE = re.compile(
    r"(pipeline-state/scripts/|feature-pipeline/scripts/|run_judge\.py|check_[A-Za-z_]+\.py"
    r"|add_steps\.py|preflight[\w-]*\.py|override_judge\.py"
    r"|\bgit\s+(status|diff|log|rev-parse|branch|show)\b)"
)

WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")
BASH_TOOLS = ("Bash", "run_shell_command")


def _active_step_id(root: Path) -> str | None:
    """id активного (in_progress) шага самого свежего манифеста активной фичи."""
    if _R is None:
        return None
    try:
        mp = _R.active_manifest(root)
        if not mp or not mp.exists():
            return None
        manifest = json.loads(mp.read_text(encoding="utf-8"))
        for step in manifest.get("steps", []):
            if step.get("status") == "in_progress":
                return step.get("id") or None
    except Exception:
        return None
    return None


def _active_feature_skill(root: Path) -> tuple[str | None, str | None]:
    """(skill, feature) активной фичи из пути манифеста ground/statements/<skill>/<feature>/."""
    if _R is None:
        return None, None
    try:
        mp = _R.active_manifest(root)
        if not mp or not mp.exists():
            return None, None
        feature = mp.parent.name
        skill = mp.parent.parent.name
        return skill, feature
    except Exception:
        return None, None


def _target_path(tool_name: str, tool_input: dict) -> str:
    if tool_name in WRITE_TOOLS:
        return str(tool_input.get("file_path") or tool_input.get("path") or "")
    if tool_name in BASH_TOOLS:
        return str(tool_input.get("command") or "")
    return ""


def _is_phase_work(step_id: str, tool_name: str, tool_input: dict) -> str | None:
    """Возвращает человекочитаемое описание productive-работы фазы, если действие ею является.
    Иначе None (действие не относится к productive-работе данной subagent-фазы)."""
    target = _target_path(tool_name, tool_input)
    if not target:
        return None
    norm = target.replace("\\", "/")

    # Bash: productive только build/test-команды; control-plane всегда пропускаем.
    if tool_name in BASH_TOOLS:
        if _CONTROL_BASH_RE.search(norm):
            return None
        if step_id.startswith(("04-test", "04-build", "05-tests",
                               "lite-red", "lite-green", "lite-verify")) and re.search(BUILD_CMD_RE, norm):
            return f"запуск сборки/тестов ({BUILD_CMD_RE})"
        return None

    # Write/Edit: артефакты/код, которые обязан производить субагент фазы.
    if step_id.startswith("02-sdd"):
        if re.search(r"(^|/)sdd\.md$", norm):
            return "запись sdd.md"
    elif step_id.startswith("02-design"):
        if re.search(r"(^|/)(tech-design\.md|task-plan\.json)$", norm):
            return "запись tech-design.md / task-plan.json"
    elif step_id.startswith("04-test"):
        if "src/test/" in norm:
            return "запись тестов в src/test/"
    elif step_id.startswith("04-build"):
        if "src/main/" in norm or norm.endswith(".java"):
            return "запись кода в src/main/ (*.java)"
    elif step_id.startswith("05-tests"):
        if "src/" in norm:
            return "правка src/ в фазе полного прогона тестов"
    elif step_id.startswith("06-spec"):
        if (norm.endswith(".md") or norm.endswith(".puml")) and ("docs/" in norm or "ground/system-analysis" in norm):
            return "запись артефактов спецификации"
    # Lite-ветка (forgelite)
    elif step_id.startswith("lite-red"):
        if "src/test/" in norm:
            return "запись RED-тестов в src/test/"
    elif step_id.startswith("lite-green"):
        if "src/main/" in norm or norm.endswith(".java"):
            return "запись кода в src/main/ (*.java)"
    elif step_id.startswith("lite-verify"):
        if "src/" in norm:
            return "правка src/ в фазе прогона тестов"
    return None


def _block(step_id: str, what: str, feature: str | None) -> int:
    feat = feature or "<slug>"
    print(
        f"[inline-phase-guard] DENY: фаза '{step_id}' обязана выполняться ЧЕРЕЗ "
        f"agent(subagent_type=...), а не inline главным агентом. Заблокировано: {what}.\n"
        f"  Запусти эту работу субагентом. Если agent() реально недоступен (деградация) — "
        f"снятие гейта только через override_judge (судья subagent-origin), и это R4: "
        f"gate-guard пропустит его ТОЛЬКО при approval-маркере "
        f"ground/approvals/gate-override-subagent-origin.json, который фиксируется после "
        f"ЯВНОГО «да» пользователя (спроси, покажи причину; feature={feat}, step={step_id}).",
        file=sys.stderr,
    )
    return 2


def _has_override(root: Path, skill: str | None, feature: str | None, step_id: str) -> bool:
    """overrides/subagent-origin.json активной фичи снимает блок (как у судей)."""
    if not skill or not feature:
        return False
    path = root / "ground" / "statements" / skill / feature / "overrides" / "subagent-origin.json"
    if not path.exists():
        return False
    try:
        ov = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    # override без привязки к шагу — общий; с step_id — только для своего шага.
    ov_step = ov.get("step_id") if isinstance(ov, dict) else None
    return ov_step in (None, "", step_id)


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0  # не-JSON stdin — fail-open
    if not isinstance(data, dict):
        return 0

    # Субагент (agent_type непустой) — не наша забота; его ограничивает sod-enforcer.
    if data.get("agent_type"):
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in WRITE_TOOLS + BASH_TOOLS:
        return 0

    root = Path(_R.project_root(data.get("cwd", ""))) if _R else Path(data.get("cwd") or ".")
    step_id = _active_step_id(root)
    if not step_id or not _requires_subagent(step_id):
        return 0  # вне subagent-only фазы — fail-open

    what = _is_phase_work(step_id, tool_name, data.get("tool_input") or {})
    if not what:
        return 0  # действие не productive-работа фазы (control-plane, чтение и т.п.)

    skill, feature = _active_feature_skill(root)
    if _has_override(root, skill, feature, step_id):
        print(
            f"[inline-phase-guard] WARN: фаза '{step_id}' исполняется inline — пропущено "
            f"по override subagent-origin ({what}).",
            file=sys.stderr,
        )
        return 0

    return _block(step_id, what, feature)


if __name__ == "__main__":
    raise SystemExit(main())
