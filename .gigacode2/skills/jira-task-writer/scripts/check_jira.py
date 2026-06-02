#!/usr/bin/env python3
"""check_jira.py — паритет созданных Jira-задач против task-plan (gate фазы Jira).

Сверяет: создано issues == 1 Story + N tasks, и у каждой задачи есть Jira key. Источник —
`jira-result.json`, который пишет jira-task-writer после создания (`{story, tasks:{T1:KEY}}`).
Не дёргает Jira API — проверяет метаданные пайплайна.

Usage:
    check_jira.py <task-plan.json> --result <jira-result.json> [--pipeline-config pipeline.json] [--json]
Exit: 0 = pass/skip, 2 = недобор.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Jira creation parity gate.")
    ap.add_argument("plan")
    ap.add_argument("--result", help="jira-result.json: {story:KEY, tasks:{T1:KEY,...}}")
    ap.add_argument("--pipeline-config", help="pipeline.json (для jira.enabled)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.pipeline_config:
        try:
            cfg = json.loads(Path(args.pipeline_config).read_text(encoding="utf-8"))
            if not cfg.get("jira", {}).get("enabled"):
                print("Jira gate: SKIPPED (jira.enabled=false/null)")
                return 0
        except Exception:
            pass

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    task_ids = [t.get("id") for t in plan.get("tasks", []) if t.get("id")]

    if not args.result or not Path(args.result).exists():
        print("Jira gate: ✗ FAIL — нет jira-tasks-result.json (задачи не созданы или ключи не записаны)")
        return 2

    res = json.loads(Path(args.result).read_text(encoding="utf-8"))
    if res.get("skipped"):
        print("Jira gate: SKIPPED (jira-tasks-result.json: skipped=true)")
        return 0

    # Принимаем: контракт jira-task-writer ({story:{key}, subtasks:[{task_id,key}]}),
    # структурный {story, tasks:{}} и плоский {T1:KEY,...}.
    story = res.get("story") or res.get("__story__")
    if isinstance(story, dict):
        story = story.get("key")
    if isinstance(res.get("tasks"), dict):
        created = res["tasks"]
    elif isinstance(res.get("subtasks"), list):
        created = {s.get("task_id"): s.get("key") for s in res["subtasks"] if s.get("task_id")}
    else:
        created = {k: v for k, v in res.items()
                   if isinstance(v, str) and k not in ("story", "__story__", "skipped", "project_key")}
    errors = []
    if not story:
        errors.append("нет Story key")
    for tid in task_ids:
        if not created.get(tid):
            errors.append(f"задача {tid} без Jira key")

    got = (1 if story else 0) + sum(1 for t in task_ids if created.get(t))
    expected = 1 + len(task_ids)
    status = "pass" if not errors else "fail"
    verdict = {"status": status, "expected": expected, "created": got, "errors": errors}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if status == "pass" else "✗ FAIL"
        print(f"Jira gate: {mark}  (создано {got}/{expected}: 1 Story + {len(task_ids)} задач)")
        for e in errors:
            print(f"  ✗ {e}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
