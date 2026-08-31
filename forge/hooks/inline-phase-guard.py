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
# Импорт форж-модулей — fail-CLOSED. Крэш на импорте отдаёт exit 1, а блокировка —
# exit 2; рантайм читает exit 1 как «хук не возражает» и ВЫПОЛНЯЕТ вызов. Так уже молча
# выключался весь enforcement (PEP 604 без future-импорта в _project.py под python 3.9 —
# на КАЖДОМ tool-call, без единой строки пользователю). Несобранный бандл обязан быть
# громким отказом, а не тишиной. Инвариант «пол интерпретатора» держит test_python_floor.py.
try:
    import risk_ladder as _R
except Exception as _e:  # pragma: no cover — сломанный бандл/интерпретатор
    # Форточка на команды ВОССТАНОВЛЕНИЯ: сплошной deny запирал и починку бандла
    # (баннер советовал `bash .gigacode/deploy-local.sh`, а матчер ^Bash$ её же и резал).
    # _failclosed — stdlib-only; если не грузится и он, поведение прежнее (exit 2).
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[inline-phase-guard] DENY: бандл forge не грузится ({_e}). Проверь интерпретатор в "
              f".gigacode/settings.json (нужен python 3.9+ с рабочим expat), затем "
              f"перезапусти: bash .gigacode/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("inline-phase-guard", _e))

# Единый источник истины «какие фазы обязаны идти субагентом» — pipeline_phases.
# best-effort импорт + inline-fallback (как в update.py), чтобы переименование префикса
# в одном месте не отключало enforcement молча.
_SUBAGENT_PREFIXES = ("02-sdd", "02-design", "04-test", "04-build", "05-tests", "06-spec",
                      "lite-design", "lite-red", "lite-green", "lite-verify",
                      "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec")
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "feature-pipeline" / "scripts"))
    import pipeline_phases as _pp
    _requires_subagent = _pp.requires_subagent
    _SUBAGENT_PREFIXES = _pp.SUBAGENT_PHASE_PREFIXES
except Exception:
    def _requires_subagent(step_id) -> bool:
        return isinstance(step_id, str) and step_id.startswith(tuple(_SUBAGENT_PREFIXES))

# Команда сборки/тестов/линта — Gradle ИЛИ Maven ИЛИ standalone-линтер (checkstyle/ktlint/…).
# Gradle/Maven-таск (напр. `./gradlew checkstyleMain`) уже покрыт `./gradlew`; standalone-инструмент
# добавлен явно, чтобы «checkstyle inline» ловился независимо от способа запуска.
BUILD_CMD_RE = r"(?:\./gradlew\s+|\bmvn\b|\b(?:checkstyle|ktlint|detekt|spotless)\b)"

# Python-subprocess обёртка над build-командой. Escape-hatch для двух путей:
#   1. orchestrator (main agent) → Bash(`python3 -c "import subprocess; subprocess.run('./gradlew test', ...)"`).
#      Inline-запуск build/test-команд в subagent-only фазах (04-test/04-build/05-tests/
#      lite-*/fix-*) через python-обёртку — для окружений, где субагент-рантайм режет
#      детерминированные команды (preview/lite/fix-ветки), а `autonomy.allow_inline_build`
#      не выставлен (двойной контур: явный флаг всегда побеждает).
#   2. subagent → Bash(`python3 check_tests_red.py ...`) → внутри Python вызов gradlew.
#      Сам субагентский bash-вызов хука не достигает (agent_type != "" → return 0),
#      но паттерн остаётся в whitelist'е на случай, если actor-aware проверка ослабнет.
# Без этого исключения любая python-обёртка с явной строкой './gradlew …' внутри
# блокировалась бы (напр. `python3 -c "import subprocess; subprocess.run('./gradlew test', …)"`).
# Узкое место: паттерн должен присутствовать в COMMAND-строке буквально — иначе тонкая обёртка
# `python3 wrapper.py --cmd './gradlew test'` всё равно блокируется, что и нужно: легитимный путь
# — через сам скрипт (который НЕ передаёт gradlew-строку в argv, а резолвит её из конфига),
# либо через явный subprocess-вызов в command. `_run_cmd` — обёртка в check_tests_red.py и других
# гейт-скриптах; `os.system`/`subprocess.Popen`/`subprocess.call`/`check_*` — стандартные Python API.
PYTHON_SUBPROCESS_RE = re.compile(
    r"\b(?:subprocess\.(?:run|Popen|call|check_call|check_output)|os\.system|_run_cmd)\s*\("
)

# Bash control-plane — оркестратору эти команды можно даже в subagent-фазе (это управление
# состоянием/гейтами/судьями, а не productive-работа фазы). Блок только для productive bash.
_CONTROL_BASH_RE = re.compile(
    r"(pipeline-state/scripts/|feature-pipeline/scripts/|run_judge\.py|check_[A-Za-z_]+\.py"
    r"|record_gate\.py|add_steps\.py|preflight[\w-]*\.py|override_judge\.py"
    r"|ensure_inventory\.py|\bgit\s+(status|diff|log|rev-parse|branch|show)\b)"
)

WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")
BASH_TOOLS = ("Bash", "run_shell_command")


def _candidate_step_ids(root: Path) -> list[str]:
    """Шаги, работой которых может быть текущее действие.

    Берём ВСЕ готовые к работе шаги, а не один: на full-пути с параллельными задачами
    `current_step_id` намеренно отдаёт None (04-build-T1 и 04-test-T2 — разные фазы), и хук
    снова оказался бы мёртвым ровно на build-фазе. Для actor-aware проверки неоднозначность не
    мешает: «оркестратор делает продуктивную работу субагентной фазы» — ответ один при любом
    кандидате."""
    try:
        if hasattr(_R, "ready_step_ids"):
            return [s for s in _R.ready_step_ids(root) if s]
    except Exception:
        return []
    sid = _active_step_id(root)
    return [sid] if sid else []


def _active_step_id(root: Path) -> str | None:
    """Шаг, работа над которым идёт сейчас — через ЕДИНЫЙ резолвер `risk_ladder.current_step_id`.

    Раньше здесь искался шаг со статусом `in_progress` — и хук был МЁРТВЫМ на живых прогонах:
    `in_progress` не проставляет никто (update.py ведёт шаг pending → completed, промежуточную
    пометку брифы не делают). Резолвер возвращал None → fail-open → оркестратор мог писать
    sdd.md/tech-design.md/код фазы inline, то есть ровно то, ради чего хук и существует. Тесты
    этого не ловили: они выставляли `in_progress` руками. `gate-guard` был переведён на
    `current_step_id` раньше — здесь та же правка (fallback на in_progress оставлен для случая,
    когда шаг не разрешился)."""
    try:
        if hasattr(_R, "current_step_id"):
            return _R.current_step_id(root)
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


def _allow_inline_build(root: Path) -> bool:
    """Разрешено ли оркестратору гонять build/test-команды inline в subagent-фазах.

    Флаг `autonomy.allow_inline_build` в ground/pipeline.json. Escape-hatch для окружений,
    где gradle/maven НЕ может запускаться субагентом (субагент-структура/рантайм режет
    детерминированные команды), previewполностью — оркестратор запускает их как управленческие
    гейт-команды. По умолчанию False (инвариант «productive-работа фазы — только субагентом»)."""
    try:
        v = _R.config_get(root, "autonomy.allow_inline_build")
        return bool(v)
    except Exception:
        return False


def _is_phase_work(step_id: str, tool_name: str, tool_input: dict, root: Path) -> str | None:
    """Возвращает человекочитаемое описание productive-работы фазы, если действие ею является.
    Иначе None (действие не относится к productive-работе данной subagent-фазы)."""
    target = _target_path(tool_name, tool_input)
    if not target:
        return None
    norm = target.replace("\\", "/")

    # Bash: productive только build/test-команды; control-plane и python-subprocess обёртки
    # всегда пропускаем. Флаг autonomy.allow_inline_build принудительно опускает блок
    # build/test-комманд для оркестратора (окружения, где градл не может идти субагентом).
    # Запись кода (Write) при этом НЕ разрешается — хук и дальше блокирует productive-артефакты
    # фазы.
    if tool_name in BASH_TOOLS:
        if _CONTROL_BASH_RE.search(norm):
            return None
        if re.search(BUILD_CMD_RE, norm):
            if _allow_inline_build(root):
                return None
            # Python-subprocess обёртка (subprocess.run/Popen/call/check_*/os.system/_run_cmd).
            # Легитимный escape-hatch для orchestrator→Bash→`python3 -c "subprocess.run('./gradlew test', ...)"`:
            # gradle/mvn вызывается через python-API, поэтому bypass соразмерен с control-plane
            # скриптами вроде check_tests_red.py. Узкое место: паттерн subprocess.run( и т.п.
            # должен присутствовать в COMMAND-строке буквально — тонкая обёртка
            # `python3 wrapper.py --cmd "./gradlew test"` блокируется, что и нужно
            # (легитимный путь — через сам скрипт, который резолвит команду из конфига, либо
            # через явный subprocess-вызов в command).
            if PYTHON_SUBPROCESS_RE.search(norm):
                return None
            if step_id.startswith(("04-test", "04-build", "05-tests",
                                   "lite-red", "lite-green", "lite-verify",
                                   "fix-red", "fix-green", "fix-verify")):
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
    elif step_id.startswith("lite-design"):
        if re.search(r"(^|/)(tech-design\.md|task-plan\.json)$", norm):
            return "запись tech-design.md / task-plan.json"
    elif step_id.startswith("lite-red"):
        if "src/test/" in norm:
            return "запись RED-тестов в src/test/"
    elif step_id.startswith("lite-green"):
        if "src/main/" in norm or norm.endswith(".java"):
            return "запись кода в src/main/ (*.java)"
    elif step_id.startswith("lite-verify"):
        if "src/" in norm:
            return "правка src/ в фазе прогона тестов"
    # Fix-ветка (forgefix): минорный дефект.
    elif step_id.startswith("fix-diag"):
        if re.search(r"(^|/)(fix-plan\.md|task-plan\.json)$", norm):
            return "запись fix-plan.md / task-plan.json"
    elif step_id.startswith("fix-red"):
        if "src/test/" in norm:
            return "запись RED-теста в src/test/"
    elif step_id.startswith("fix-green"):
        if "src/main/" in norm or norm.endswith(".java"):
            return "запись кода фикса в src/main/ (*.java)"
    elif step_id.startswith("fix-verify"):
        if "src/" in norm:
            return "правка src/ в фазе прогона тестов"
    elif step_id.startswith("fix-spec"):
        if re.search(r"(^|/)sdd\.md$", norm):
            return "запись дельты спеки (sdd.md)"
    return None


def _is_subagent(data: dict) -> bool:
    """Действие идёт ИЗ субагента (тогда хук не вмешивается), а не от оркестратора.

    Три признака по убыванию прямоты:

    1. непустой `agent_type` в payload'е — рантайм сам сказал, кто действует;
    2. непустой `agent_id` при полном отсутствии ключа `agent_type` — рантайм это поле не
       поддерживает, но идентификатор агента даёт (условие узкое намеренно: если ключ
       `agent_type` ЕСТЬ, пусть и пустой, доверяем ему);
    3. отметка активного субагента в сессии (`subagent_scope`), которую ведут SubagentStart/
       SubagentStop.

    Третий пункт — не запас прочности, а основной путь на живом рантайме. Замер на
    qwen-code 0.21.14 (e2e-прогон на метапроекте): в PreToolUse НЕТ ни `agent_type`, ни
    `agent_id`, а `session_id`/`transcript_path` у субагента те же, что у оркестратора —
    по одному payload'у субагент неотличим. Из-за этого хук блокировал работу САМОГО
    субагента: тот получал «выполняй через agent(...)», возвращал текст отказа наверх,
    оркестратор предлагал запустить субагента — цикл без выхода, прод-код не пишется."""
    if data.get("agent_type"):
        return True
    if "agent_type" not in data and data.get("agent_id"):
        return True
    try:
        import subagent_scope
        return subagent_scope.active(str(data.get("session_id") or ""))
    except Exception:
        return False


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

    # Субагент — не наша забота; его ограничивает sod-enforcer.
    if _is_subagent(data):
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in WRITE_TOOLS + BASH_TOOLS:
        return 0

    root = Path(_R.project_root(data.get("cwd", ""))) if _R else Path(data.get("cwd") or ".")
    tool_input = data.get("tool_input") or {}
    step_id, what = None, None
    for sid in _candidate_step_ids(root):
        if not _requires_subagent(sid):
            continue
        w = _is_phase_work(sid, tool_name, tool_input, root)
        if w:
            step_id, what = sid, w
            break
    if not what:
        return 0  # вне subagent-фазы либо действие не её productive-работа (control-plane, чтение)

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
