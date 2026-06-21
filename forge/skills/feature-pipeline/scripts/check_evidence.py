#!/usr/bin/env python3
"""check_evidence.py — gate полноты evidence bundle (PDLC v3.5, MVP-ворота стр. 244).

Проверяет, что на каждую задачу task-plan есть ground/evidence/<id>.json и его completeness
>= порога (по умолчанию 0.95 или из pipeline.json evidence.threshold). Это «evidence-bundle
completeness >= 95%» — ворота перехода к доставке.

P0-3: дополнительно блокирует доставку, если у задачи есть degraded-гейты — гейты, которые
НЕ смогли подтвердить результат (skipped/missing/error). «Гейт неприменим/не отработал» ≠
«гейт пройден»: тихий пропуск становится видимым долгом и по умолчанию (fail-closed) валит
ворота. Escape: --degraded-policy warn или evidence.degraded_policy в pipeline.json.

Usage:
    check_evidence.py <task-plan.json> --root . [--threshold 0.95] [--pipeline-config pipeline.json]
        [--task <id>] [--degraded-policy block|warn] [--json]
Exit: 0 = pass, 2 = неполный/отсутствует пакет / degraded-гейт при policy=block.
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
    ap.add_argument("--evidence-dir", default=None, help="Директория с evidence (по умолчанию <root>/ground/evidence)")
    ap.add_argument("--degraded-policy", choices=["block", "warn"], default=None,
                    help="Гейты-degraded в bundle: block (по умолчанию, fail-closed) | warn")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else root / "ground" / "evidence"
    cfg = _load(Path(args.pipeline_config)) if args.pipeline_config else None
    threshold = args.threshold
    if threshold is None and cfg:
        threshold = (cfg.get("evidence") or {}).get("threshold")
    if threshold is None:
        threshold = 0.95

    # P0-3: политика для degraded-гейтов (приоритет: CLI > pipeline.json > block)
    degraded_policy = args.degraded_policy
    if degraded_policy is None and cfg:
        dp = (cfg.get("evidence") or {}).get("degraded_policy")
        if dp in ("block", "warn"):
            degraded_policy = dp
    if degraded_policy is None:
        degraded_policy = "block"

    plan = _load(Path(args.plan)) or {}
    task_ids = [t.get("id") for t in plan.get("tasks", []) if t.get("id")]
    if args.task:
        task_ids = [args.task]

    errors = []
    warnings = []
    details = {}
    degraded = {}
    for tid in task_ids:
        bp = evidence_dir / f"{tid}.json"
        bundle = _load(bp)
        if bundle is None:
            errors.append(f"задача {tid}: нет evidence ({bp.name})")
            details[tid] = None
            continue
        try:
            c = float(bundle.get("completeness", 0))
        except (ValueError, TypeError):
            c = 0.0
            errors.append(f"задача {tid}: completeness '{bundle.get('completeness')}' не число")
            details[tid] = bundle.get("completeness")
            continue
        details[tid] = c
        if c < threshold:
            errors.append(f"задача {tid}: completeness {c:.0%} < {threshold:.0%}")

        # P0-3: degraded-гейты — гейт не подтвердил результат, это не pass
        deg = bundle.get("degraded_gates") or []
        if deg:
            degraded[tid] = deg
            msg = f"задача {tid}: degraded-гейты (результат не подтверждён): {', '.join(deg)}"
            if degraded_policy == "block":
                errors.append(msg)
            else:
                warnings.append(msg)

    status = "pass" if not errors else "fail"
    verdict = {"status": status, "threshold": threshold, "tasks": details,
               "degraded": degraded, "degraded_policy": degraded_policy,
               "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if status == "pass" else "✗ FAIL"
        print(f"Evidence gate: {mark} (порог {threshold:.0%}, задач {len(task_ids)})")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warnings:
            print(f"  ⚠ {w}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
