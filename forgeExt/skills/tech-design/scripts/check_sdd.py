#!/usr/bin/env python3
"""check_sdd.py — gate линковки task-plan ↔ sdd.md (PDLC v3.5). Дополняет check_taskplan.py.

Запускается на фазе 02-design (tech-design). Сам документ sdd.md (обязательные секции,
Given-When-Then) уже провалидирован на фазе 02-sdd гейтом `sdd/scripts/check_sdd_doc.py`,
поэтому здесь проверяется только связь плана со спецификацией:
  1. Существует sdd.md (путь задаётся или выводится рядом с task-plan) — факт наличия.
  2. У каждой задачи task-plan есть непустой `acceptance` (≥1, желательно Given-When-Then) и `sdd_ref`.

Usage:
    check_sdd.py <task-plan.json> [--sdd <sdd.md>] [--json]
Exit: 0 = pass, 2 = чего-то не хватает.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_GWT = re.compile(r"(?i)given.*when.*then")


def main() -> int:
    ap = argparse.ArgumentParser(description="Strict SDD gate.")
    ap.add_argument("plan")
    ap.add_argument("--sdd", help="путь к sdd.md (по умолчанию <папка task-plan>/sdd.md)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plan_path = Path(args.plan)
    errors: list[str] = []
    warnings: list[str] = []

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"status": "fail", "errors": [f"invalid task-plan JSON: {e}"]}, ensure_ascii=False))
        return 2

    sdd_path = Path(args.sdd) if args.sdd else plan_path.parent / "sdd.md"
    if not sdd_path.exists():
        errors.append(f"нет SDD-документа: {sdd_path} (должен быть создан на фазе 02-sdd)")

    for t in plan.get("tasks", []):
        tid = t.get("id", "?")
        acc = t.get("acceptance")
        if not acc or not isinstance(acc, list) or not any(str(a).strip() for a in acc):
            errors.append(f"task {tid}: пустой/отсутствует acceptance")
        elif not any(_GWT.search(str(a)) for a in acc):
            warnings.append(f"task {tid}: acceptance без Given-When-Then формата")
        if not t.get("sdd_ref"):
            errors.append(f"task {tid}: нет sdd_ref (ссылки на раздел SDD)")

    status = "pass" if not errors else "fail"
    verdict = {"status": status, "tasks": len(plan.get("tasks", [])),
               "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"SDD check: {'✓ PASS' if status == 'pass' else '✗ FAIL'}")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warnings:
            print(f"  · warn: {w}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
