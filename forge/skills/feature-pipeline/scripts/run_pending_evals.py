#!/usr/bin/env python3
"""run_pending_evals.py — принудительный прогон eval'ов для задачи (PDLC v3.5, §7 шаг 5).

Когда хук `eval-guard` блокирует запись в src/main/, этот скрипт позволяет принудительно
выполнить compile + coverage + test_pass eval'ы для задачи. Записывает результаты
в ground/statements/feature-pipeline/<feature>/evals.json (или рядом).

Usage:
    run_pending_evals.py --project . --feature <slug> --task <taskId>
        [--eval-plan <eval-plan.json>] [--build-cmd <cmd>] [--test-cmd <cmd>] [--coverage-cmd <cmd>]
        [--json]

Exit: 0 = все eval'ы пройдены, 0 = не было pending eval'ов,
      2 = хотя бы один eval не пройден (compile/coverage/test_pass).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_json(p: str | Path) -> dict | None:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_cmd(cmd: str, cwd: str) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=300)
        return r.returncode, (r.stdout + r.stderr)[:2000]
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except Exception as e:
        return 1, str(e)


def _feature_docs_dir(project_root: str, override: str | None) -> Path:
    """Каталог документов фич: override → как есть; иначе резолв по docs-конфигу."""
    if override:
        return Path(project_root) / override
    try:
        import skill_paths  # type: ignore
        return skill_paths.feature_docs_dir(Path(project_root))
    except Exception:
        return Path(project_root) / "docs" / "feature-pipeline"


def _resolve_eval_plan_path(project_root: str, feature: str, eval_plan_arg: str | None, feature_docs_dir: str | None = None) -> Path:
    """Определяет путь к eval-plan.json."""
    if eval_plan_arg:
        return Path(eval_plan_arg)
    # Стандартный путь (каталог фич резолвится по docs-конфигу)
    p = _feature_docs_dir(project_root, feature_docs_dir) / feature / "eval-plan.json"
    if p.exists():
        return p
    # Fallback: рядом с task-plan
    alt = Path(project_root) / "ground" / f"{feature}-eval-plan.json"
    if alt.exists():
        return alt
    return p


def _results_path(project_root: str, feature: str, skill: str = "feature-pipeline") -> Path:
    return Path(project_root) / "ground" / "statements" / skill / feature / "evals.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Run pending evals for a task.")
    ap.add_argument("--project", default=".", help="project root")
    ap.add_argument("--feature", default="pipeline", help="feature slug")
    ap.add_argument("--task", required=True, help="task id (e.g. T1)")
    ap.add_argument("--skill", default="feature-pipeline", help="Имя скилла для резолвинга ground/statements/<skill>/")
    ap.add_argument("--feature-docs-dir", default=None, help="Путь к doc-папке фичи (от корня проекта; по умолчанию резолв по docs-конфигу)")
    ap.add_argument("--eval-plan", help="path to eval-plan.json (auto-detected if omitted)")
    ap.add_argument("--build-cmd", help="override compile command")
    ap.add_argument("--test-cmd", help="override test command")
    ap.add_argument("--coverage-cmd", help="override coverage command")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    eval_plan_path = _resolve_eval_plan_path(str(root), args.feature, args.eval_plan, args.feature_docs_dir)

    plan = _load_json(str(eval_plan_path))
    if plan is None:
        print(f"run-pending-evals: НЕТ eval-plan ({eval_plan_path})")
        return 0

    # Выбираем eval'ы для задачи
    task_evals = [e for e in plan.get("evals", []) if e.get("task_id") == args.task]
    if not task_evals:
        print(f"run-pending-evals: нет eval'ов для задачи {args.task}")
        return 0

    # Загружаем предыдущие результаты
    results_path = _results_path(str(root), args.feature, skill=args.skill)
    results = _load_json(str(results_path)) or {}

    # Определяем команды
    cfg = {}
    pipeline_cfg_path = root / "ground" / "pipeline.json"
    if pipeline_cfg_path.exists():
        cfg = _load_json(str(pipeline_cfg_path)) or {}

    build_cmd = args.build_cmd or cfg.get("quality", {}).get("build_command", "./gradlew clean build")
    test_cmd = args.test_cmd or cfg.get("quality", {}).get("test_command", "./gradlew test")

    passed = 0
    failed = 0
    skipped = 0
    eval_results = {}

    for ev in task_evals:
        eid = ev["id"]
        etype = ev.get("type", "unknown")
        cmd = ev.get("command", "")

        # Для compile и test_pass используем build_cmd / test_cmd если команда не задана
        if not cmd:
            if etype == "compile":
                cmd = build_cmd
            elif etype == "test_pass":
                cmd = build_cmd  # compile + test
            elif etype == "coverage":
                cmd = args.coverage_cmd or cfg.get("quality", {}).get("coverage_report", "")

        if not cmd:
            eval_results[eid] = {"status": "skipped", "reason": "нет команды"}
            skipped += 1
            continue

        rc, output = _run_cmd(cmd, str(root))
        eval_status = "passed" if rc == 0 else "failed"

        eval_results[eid] = {
            "status": eval_status,
            "exit_code": rc,
            "command": cmd,
            "output_preview": output[:500],
            "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        if eval_status == "passed":
            passed += 1
        else:
            failed += 1

    # Сохраняем результаты
    merged = dict(results)
    merged.update(eval_results)
    merged["_meta"] = {
        "feature": args.feature,
        "task": args.task,
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(task_evals),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }

    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "task": args.task,
        "total": len(task_evals),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "results": eval_results,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"run-pending-evals ({args.task}): {passed}/{failed}/{skipped} passed/failed/skipped")
        for eid, r in eval_results.items():
            mark = "✓" if r["status"] == "passed" else "✗"
            print(f"  {mark} {eid}: {r['status']}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())