#!/usr/bin/env python3
"""check_evidence.py — gate полноты evidence bundle (PDLC v3.5, MVP-ворота стр. 244).

Проверяет, что на каждую задачу task-plan есть ground/evidence/<id>.json и его completeness
>= порога (по умолчанию 0.95 или из pipeline.json evidence.threshold). Это «evidence-bundle
completeness >= 95%» — ворота перехода к доставке.

Usage:
    check_evidence.py <task-plan.json> --root . [--threshold 0.95] [--pipeline-config pipeline.json]
        [--task <id>] [--json]
Exit: 0 = pass, 2 = неполный/отсутствует пакет.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Evidence bundle completeness gate.")
    ap.add_argument("plan")
    ap.add_argument("--root", default=".")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--pipeline-config")
    ap.add_argument("--task")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    threshold = args.threshold
    if threshold is None and args.pipeline_config:
        cfg = _load(Path(args.pipeline_config)) or {}
        threshold = (cfg.get("evidence") or {}).get("threshold")
    if threshold is None:
        threshold = 0.95

    plan = _load(Path(args.plan)) or {}
    task_ids = [t.get("id") for t in plan.get("tasks", []) if t.get("id")]
    if args.task:
        task_ids = [args.task]

    errors = []
    details = {}
    for tid in task_ids:
        bp = root / "ground" / "evidence" / f"{tid}.json"
        bundle = _load(bp)
        if bundle is None:
            errors.append(f"задача {tid}: нет evidence ({bp.name})")
            details[tid] = None
            continue
        c = float(bundle.get("completeness", 0))
        details[tid] = c
        if c < threshold:
            errors.append(f"задача {tid}: completeness {c:.0%} < {threshold:.0%}")

    status = "pass" if not errors else "fail"
    verdict = {"status": status, "threshold": threshold, "tasks": details, "errors": errors}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if status == "pass" else "✗ FAIL"
        print(f"Evidence gate: {mark} (порог {threshold:.0%}, задач {len(task_ids)})")
        for e in errors:
            print(f"  ✗ {e}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
