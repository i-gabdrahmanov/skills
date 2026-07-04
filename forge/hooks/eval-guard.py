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
import sys
from pathlib import Path

import risk_ladder as R

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


def _block(reason: str) -> int:
    print(f"[eval-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _load_eval_results(manifest_dir: Path) -> dict:
    """Кэш результатов eval'ов, который пишет run_pending_evals.py (имя файла — evals.json)."""
    path = manifest_dir / "evals.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _has_passed(results: dict, eval_id: str) -> bool:
    entry = results.get(eval_id)
    return isinstance(entry, dict) and entry.get("status") == "passed"


def _is_src_main(target_path: str | None) -> bool:
    if not target_path:
        return False
    return "/src/main/" in target_path.replace("\\", "/")


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

    # 1. Проверяем, включён ли eval (fail-open)
    cfg = R.pipeline_cfg(root)
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
    feature_slug = manifest.get("context", {}).get("feature", "")

    # 4. Ищем eval-plan.json (каталог фич резолвится по docs-конфигу: in-repo/separate-repo)
    import _project
    eval_plan_path = _project.feature_docs_dir(root, cfg) / feature_slug / "eval-plan.json"
    if not eval_plan_path.exists():
        return 0

    eval_plan = json.loads(eval_plan_path.read_text(encoding="utf-8"))
    evals = eval_plan.get("evals", [])
    if not evals:
        return 0

    # 5. Определяем текущую задачу по шагам манифеста (по соглашению build-шага)
    current_task_id = None
    for step in manifest.get("steps", []):
        tid = _build_task_id(step.get("id", ""))
        if tid and step.get("status") == "in_progress":
            current_task_id = tid
            break
    if not current_task_id:
        return 0

    # 6. Фильтруем eval'ы по текущей задаче
    task_evals = [e for e in evals if e.get("task_id") == current_task_id]
    if not task_evals:
        return 0

    # 7. Читаем кэш результатов (его пишет execution-gate run_pending_evals.py)
    manifest_dir = mp.parent
    eval_results = _load_eval_results(manifest_dir)

    # 8. Блокируем, если для задачи есть eval'ы без статуса passed в кэше
    failed_evals = [e["id"] for e in task_evals if not _has_passed(eval_results, e["id"])]
    if failed_evals:
        return _block(
            f"Eval-Driven Development: для задачи {current_task_id} не пройдены (или не прогонялись) "
            f"eval'ы: {failed_evals}. Прогони execution-gate: "
            f"python3 .gigacode/skills/feature-pipeline/scripts/run_pending_evals.py "
            f"--project . --feature {feature_slug} --task {current_task_id}  "
            f"(или отключи quality.eval_enabled в pipeline.json)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
