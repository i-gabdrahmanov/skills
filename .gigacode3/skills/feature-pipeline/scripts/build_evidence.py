#!/usr/bin/env python3
"""build_evidence.py — собрать evidence bundle на задачу (PDLC v3.5, стр. 62/155).

Доказательный пакет = всё, что подтверждает, что задача сделана правильно: тесты, покрытие,
результаты гейтов, артефакты, обоснование, ссылка на SDD. Собирается из выводов субагентов/
гейтов, сохранённых pipeline-state (<root>/ground/statements/feature-pipeline/pipeline/*.json),
и из task-plan. Пишет <root>/ground/evidence/<taskId>.json с полем completeness (0..1).

Usage:
    build_evidence.py <task-plan.json> --task <id> [--root .] [--rationale "..."] [--json]
Exit: 0 всегда (сборка не гейт; гейт — check_evidence.py).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

# обязательные поля пакета (для completeness)
REQUIRED = ["task", "tests", "coverage", "gates", "artifacts", "rationale", "sdd_ref"]


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _step_dir(root: Path) -> Path:
    return root / "ground" / "statements" / "feature-pipeline" / "pipeline"


def _completeness(bundle: dict) -> float:
    present = 0
    for k in REQUIRED:
        v = bundle.get(k)
        if v not in (None, "", [], {}):
            present += 1
    return round(present / len(REQUIRED), 3)


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble evidence bundle for a task.")
    ap.add_argument("plan")
    ap.add_argument("--task", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--rationale", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    plan = _load(Path(args.plan)) or {}
    task = next((t for t in plan.get("tasks", []) if t.get("id") == args.task), {})
    sd = _step_dir(root)

    build_out = _load(sd / f"04-build-{args.task}.json") or {}
    tests_out = _load(sd / "05-tests.json") or {}
    deliver_out = _load(sd / f"07-deliver-{args.task}.json") or {}

    bundle = {
        "task": args.task,
        "title": task.get("title", ""),
        "tests": tests_out.get("tests") or tests_out.get("summary") or tests_out or None,
        "coverage": tests_out.get("coverage"),
        "gates": {
            "build": build_out.get("gate", build_out.get("status")),
            "coverage": tests_out.get("gate", tests_out.get("status")),
            "delivery": deliver_out.get("gate", deliver_out.get("status")),
        },
        "artifacts": task.get("artifacts", []) or build_out.get("artifacts", []),
        "rationale": args.rationale or task.get("rationale", ""),
        "sdd_ref": task.get("sdd_ref", "") or plan.get("sdd_ref", ""),
        "acceptance": task.get("acceptance"),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    bundle["completeness"] = _completeness(bundle)

    out_dir = root / "ground" / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.task}.json"
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
    else:
        print(f"Evidence bundle: {out_path}  completeness={bundle['completeness']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
