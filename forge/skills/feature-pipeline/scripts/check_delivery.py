#!/usr/bin/env python3
"""check_delivery.py — по PR на задачу (gate фазы Deliver, stacked-PR).

Проверяет в pipeline-state manifest: на каждую задачу task-plan есть закрытый шаг
`07-deliver-<id>` (completed). Не дёргает Bitbucket API — проверяет метаданные пайплайна.

Usage:
    check_delivery.py <task-plan.json> --manifest <manifest.json> [--pipeline-config pipeline.json] [--prefix 07-deliver-] [--json]
Exit: 0 = pass/skip, 2 = не на все задачи есть закрытый deliver-шаг.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-task delivery (stacked PR) gate.")
    ap.add_argument("plan")
    ap.add_argument("--manifest", required=True, help="pipeline-state manifest.json")
    ap.add_argument("--pipeline-config", help="pipeline.json (для bitbucket.enabled)")
    ap.add_argument("--prefix", default="07-deliver-", help="delivery step id prefix")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.pipeline_config:
        try:
            cfg = json.loads(Path(args.pipeline_config).read_text(encoding="utf-8"))
            if not cfg.get("bitbucket", {}).get("enabled"):
                print("Delivery gate: SKIPPED (bitbucket.enabled=false/null)")
                return 0
        except Exception:
            pass

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    task_ids = [t.get("id") for t in plan.get("tasks", []) if t.get("id")]
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    # Ключи нормализуем в lower: оркестратор (Qwen) иногда создаёт шаг как
    # '07-deliver-t1', а task-id в task-plan — 'T1'. Сопоставление суффикса
    # делаем регистронезависимо, иначе шаг «не найден» (DEBAG-ORDERS P5).
    by_id = {(s.get("id") or "").lower(): s for s in manifest.get("steps", [])}

    errors = []
    for tid in task_ids:
        want = f"{args.prefix}{tid}"
        step = by_id.get(want.lower())
        if step is None:
            errors.append(f"задача {tid}: нет шага {want}")
        elif step.get("status") != "completed":
            errors.append(f"задача {tid}: шаг {want} = {step.get('status')}")

    status = "pass" if not errors else "fail"
    verdict = {"status": status, "tasks": len(task_ids), "errors": errors}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if status == "pass" else "✗ FAIL"
        print(f"Delivery gate: {mark}  (задач: {len(task_ids)})")
        for e in errors:
            print(f"  ✗ {e}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
