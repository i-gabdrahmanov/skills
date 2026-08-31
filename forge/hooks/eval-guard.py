#!/usr/bin/env python3
"""eval-guard.py — PreToolUse хук: блокирует запись кода (src/main), пока eval'ы задачи не пройдены.

PDLC v3.5 — Eval-Driven Development: eval'ы пишутся ДО кода (фаза Design).
Этот хук форсирует: файлы в src/main/ не создаются/изменяются, пока для соответствующей
задачи есть непройденные eval'ы.

**Read-only (важно):** хук НЕ запускает eval-команды сам. Тяжёлый прогон (compile/coverage/
test_pass, до 300с) — это execution-gate `run_pending_evals.py`, который запускает ОРКЕСТРАТОР
и который пишет результаты в `ground/statements/feature-pipeline/<slug>/evals.json`. Хук лишь
ЧИТАЕТ этот кэш. Так мы не кладём тяжёлый subprocess в hook hot-path (рантайм убивает хук >60с
и трактует как fail-open — то есть запись бы прошла молча; см. FORGE.md «известные ограничения»).

Матчится на Write/Edit/WriteFile в src/main/. Блок: exit 2 + stderr.
fail-open: если eval-plan.json нет, eval_enabled=false, фичи/задачи нет — пропускает.
Если кэша `evals.json` нет или задача в нём не пройдена — блок с инструкцией прогнать
`run_pending_evals.py`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Импорт форж-модулей — fail-CLOSED. Крэш на импорте отдаёт exit 1, а блокировка —
# exit 2; рантайм читает exit 1 как «хук не возражает» и ВЫПОЛНЯЕТ вызов. Так уже молча
# выключался весь enforcement (PEP 604 без future-импорта в _project.py под python 3.9 —
# на КАЖДОМ tool-call, без единой строки пользователю). Несобранный бандл обязан быть
# громким отказом, а не тишиной. Инвариант «пол интерпретатора» держит test_python_floor.py.
try:
    import risk_ladder as R
except Exception as _e:  # pragma: no cover — сломанный бандл/интерпретатор
    # Форточка на команды ВОССТАНОВЛЕНИЯ: сплошной deny запирал и починку бандла
    # (баннер советовал `bash .gigacode/deploy-local.sh`, а матчер ^Bash$ её же и резал).
    # _failclosed — stdlib-only; если не грузится и он, поведение прежнее (exit 2).
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[eval-guard] DENY: бандл forge не грузится ({_e}). Проверь интерпретатор в "
              f".gigacode/settings.json (нужен python 3.9+ с рабочим expat), затем "
              f"перезапусти: bash .gigacode/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("eval-guard", _e))


def _runner_script() -> Path:
    """Абсолютный путь к run_pending_evals.py (execution-gate).

    Литерал `.gigacode/skills/...` в подсказке годился только для legacy-деплоя: в
    extension-раскладке такого каталога в проекте нет, модель получала «can't open file» — и,
    следуя тому же сообщению, выключала `quality.eval_enabled` вместо прогона гейта. Путь
    выводится от расположения хука (как в `_project.resolve_skill_path`)."""
    from _project import resolve_skill_path
    return resolve_skill_path("feature-pipeline", "scripts", "run_pending_evals.py")


# Соглашения об id шагов — ЕДИНЫЙ источник pipeline_phases (co-located с хуками в .gigacode).
# best-effort импорт + inline-fallback (пинится test_phase_consistency), чтобы переименование
# префикса '04-build-' в одном месте не отключало enforcement молча.
_BUILD_STEP_PREFIX = "04-build-"
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "feature-pipeline" / "scripts"))
    import pipeline_phases as _pp
    _build_task_id = _pp.build_task_id
    _BUILD_STEP_PREFIX = _pp.BUILD_STEP_PREFIX
except Exception:
    def _build_task_id(step_id):
        if isinstance(step_id, str) and step_id.startswith(_BUILD_STEP_PREFIX):
            return step_id[len(_BUILD_STEP_PREFIX):] or None
        return None


# Типы eval'ов, которые МОГУТ быть зелёными до того, как код задачи написан. Только они
# участвуют в pre-write гейте.
#
# Почему не все. build_evals_from_design заводит на задачу три eval'а: compile, coverage и
# test_pass («вся тест-сюита зелёная»). Фаза 04-build стартует сразу после 04-test (RED), то
# есть при заведомо красной сюите — значит требование «все три passed ДО записи в src/main»
# невыполнимо by design: кода нет → сюита красная → писать код нельзя. run_pending_evals.py,
# который советует баннер, в этот момент лишь перезапишет те же fail. Классический тупик
# курицы и яйца (tasks/010).
#
# Enforcement при этом не теряется: coverage и test_pass форсятся при ЗАКРЫТИИ шага —
# risk-policy.json.gate_cmd_expect требует check_build.py для 04-build и
# check_coverage.py/run_judge.py для 05-tests, а record_gate без них не выдаст evidence.
_PREWRITE_EVAL_TYPES = frozenset({"compile"})


def _block(reason: str) -> int:
    print(f"[eval-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _load_eval_results(manifest_dir: Path) -> dict:
    """Кэш результатов eval'ов, который пишет run_pending_evals.py (имя файла — evals.json).

    Провенанс (BLOCKER-1 класс): засчитываем кэш ТОЛЬКО с _meta.produced_by=="run_pending_evals".
    Подделанный evals.json со всеми status:"passed", но без этого маркера (прямой Write режет
    state-write-guard; Bash-редирект — best-effort) → возвращаем {}, значит ни один eval не
    passed → eval-guard блокирует запись в src/main. Легитимный кэш всегда несёт маркер."""
    path = manifest_dir / "evals.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        meta = data.get("_meta") if isinstance(data, dict) else None
        if not isinstance(meta, dict) or meta.get("produced_by") != "run_pending_evals":
            print("[eval-guard] evals.json без провенанса produced_by:'run_pending_evals' — "
                  "кэш не засчитан (прогони run_pending_evals.py заново).", file=sys.stderr)
            return {}
        return data
    return {}


def _has_passed(results: dict, eval_id: str) -> bool:
    entry = results.get(eval_id)
    return isinstance(entry, dict) and entry.get("status") == "passed"


def _is_src_main(target_path: str | None) -> bool:
    # `(?:^|/)src/main/` ловит и абсолютный, и ОТНОСИТЕЛЬНЫЙ путь (рантайм Qwen может отдать
    # relative — так делает tdd-guard). Требование ведущего слэша (`/src/main/`) молча fail-
    # open'ило EDD-гейт на `src/main/...` от tool_input с относительным file_path.
    if not target_path:
        return False
    return bool(re.search(r"(?:^|/)src/main/", target_path.replace("\\", "/")))


def _task_of_target(task_plan_path: Path, target: str | None) -> str | None:
    """Задача, которой принадлежит файл — по `artifacts` task-plan (единый предикат
    pipeline_phases.task_of_artifact). None, если плана нет либо владелец неоднозначен."""
    if not target or _pp is None or not hasattr(_pp, "task_of_artifact"):
        return None
    try:
        plan = json.loads(Path(task_plan_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _pp.task_of_artifact(plan, target)


def _target_path(tool_name: str, tool_input: dict) -> str | None:
    # канон-имена рантайма (write_file/edit/notebook_edit) + Claude-алиасы — иначе на реальном
    # рантайме (tool_name=write_file/edit) target был бы None и eval-guard молча fail-open'ил.
    if tool_name in ("Write", "WriteFile", "Edit", "edit", "write_file",
                     "NotebookEdit", "notebook_edit"):
        return (tool_input.get("file_path") or tool_input.get("path") or "").strip()
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0  # не-JSON stdin — fail-open, не роняем инструмент
    if not isinstance(data, dict):
        return 0

    cwd = data.get("cwd", "")
    # git-toplevel, как у соседей по цепочке (gate/sod/inline): при cwd=подкаталог
    # сырой Path(cwd) не находил ground/ и единственный форсер EDD молча fail-open'ил
    root = Path(R.project_root(cwd))
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    target = _target_path(tool_name, tool_input)

    # 1. Проверяем, включён ли eval (fail-open).
    # B2 fix: используем канонический v2-reader с dual-read fallback (policy.json → pipeline.json).
    # R.pipeline_cfg(root) читает ТОЛЬКО legacy pipeline.json → на v2-only проектах
    # (где есть только policy.json) возвращает {} и quality.eval_enabled не работает.
    from _config_loader import load_project_config
    cfg = load_project_config(root)
    quality_cfg = cfg.get("quality", {})
    if not quality_cfg.get("eval_enabled", True):
        return 0

    # 2. Фильтр: только запись в src/main (не src/test)
    if not _is_src_main(target):
        return 0

    # 3. Находим активную фичу
    mp = R.active_manifest(root)
    if not mp or not mp.exists():
        return 0

    manifest = json.loads(mp.read_text(encoding="utf-8"))
    # Слаг: top-level `feature` (его ВСЕГДА пишет init.py) → каталог стейта → context.feature.
    # Раньше читался только `context.feature`, а `--context` у init.py опционален (дефолт `{}`):
    # на любом прогоне, где бриф его не передал, слаг был пустым → путь к eval-plan.json не
    # складывался → хук молча fail-open'ил, и EDD-гейта не существовало.
    feature_slug = (manifest.get("feature")
                    or (manifest.get("context") or {}).get("feature")
                    or mp.parent.name or "")

    # 4. Ищем eval-plan.json (каталог фич резолвится по docs-конфигу: in-repo/separate-repo)
    import _project
    eval_plan_path = _project.feature_docs_dir(root, cfg) / feature_slug / "eval-plan.json"
    if not eval_plan_path.exists():
        return 0

    eval_plan = json.loads(eval_plan_path.read_text(encoding="utf-8"))
    evals = eval_plan.get("evals", [])
    if not evals:
        return 0

    # 5. Определяем задачу, к которой относится запись. Три источника по убыванию точности:
    #    (1) единый резолвер фазы current_step_id → build-шаг → task-id;
    #    (2) явный in_progress (его на живых прогонах не проставляют — раньше это был
    #        ЕДИНСТВЕННЫЙ источник, поэтому EDD-гейта фактически не существовало);
    #    (3) владелец самого файла по `artifacts` task-plan — на параллельных задачах
    #        резолвер фазы намеренно отдаёт None, и без этого шага гейт снова молчал бы.
    current_task_id = None
    if hasattr(R, "current_step_id"):
        current_task_id = _build_task_id(R.current_step_id(root) or "")
    if not current_task_id:
        for step in manifest.get("steps", []):
            tid = _build_task_id(step.get("id", ""))
            if tid and step.get("status") == "in_progress":
                current_task_id = tid
                break
    if not current_task_id:
        # На параллельных задачах current_step_id намеренно отдаёт None — определяем задачу по
        # самому файлу (её `artifacts` в task-plan), иначе EDD-гейт молча пропускает запись.
        current_task_id = _task_of_target(eval_plan_path.parent / "task-plan.json", target)
    if not current_task_id:
        return 0

    # 6. Фильтруем eval'ы по текущей задаче И по выполнимости ДО кода (см. _PREWRITE_EVAL_TYPES)
    task_evals = [e for e in evals if e.get("task_id") == current_task_id]
    if not task_evals:
        return 0
    prewrite_evals = [e for e in task_evals
                      if str(e.get("type") or "").strip().lower() in _PREWRITE_EVAL_TYPES]
    if not prewrite_evals:
        return 0

    # 7. Читаем кэш результатов (его пишет execution-gate run_pending_evals.py)
    manifest_dir = mp.parent
    eval_results = _load_eval_results(manifest_dir)

    # 8. Блокируем, если для задачи есть pre-write eval'ы без статуса passed в кэше
    failed_evals = [e.get("id") for e in prewrite_evals
                    if not _has_passed(eval_results, e.get("id"))]
    if failed_evals:
        return _block(
            f"Eval-Driven Development: для задачи {current_task_id} не пройдены (или не прогонялись) "
            f"eval'ы: {failed_evals}. Прогони execution-gate: "
            f"python3 {_runner_script()} "
            f"--project . --feature {feature_slug} --task {current_task_id}  "
            f"(или отключи quality.eval_enabled в pipeline.json)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
