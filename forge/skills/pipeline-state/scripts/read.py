#!/usr/bin/env python3
"""
Read pipeline state. Three modes:

Usage:
    read.py --project <path> --skill <name>                # summary
    read.py --project <path> --skill <name> --full         # raw manifest
    read.py --project <path> --skill <name> --excerpt-of <step-id>
                                                            # compact excerpt of step's output

Returns JSON to stdout. Exit code 0 on success.
"""
import argparse
import json
import sys
from pathlib import Path

from _util import repo_root, safe_load_json


# База данных скиллов внутри проекта (НЕ dot-папка — иначе рантайм режет доступ).
DATA_DIR = "ground"


def pipeline_dir(project: Path, skill: str, feature: str = "pipeline") -> Path:
    return project / DATA_DIR / "statements" / skill / feature


def list_features(project: Path, skill: str) -> dict:
    """Без --feature: перечислить все фичи скилла (кроме archived) с их статусами — для резюма."""
    base = project / DATA_DIR / "statements" / skill
    feats = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name == "archived":
                continue
            mp = d / "manifest.json"
            if not mp.exists():
                continue
            try:
                man = json.load(open(mp, encoding="utf-8"))
            except Exception:
                continue
            s = summarize(man)
            feats.append({"feature": d.name, "status": s["status"], "counts": s.get("counts")})
    overall = "no_state" if not feats else (
        "in_flight" if any(f["status"] == "in_flight" for f in feats) else "completed")
    return {"status": overall, "skill": skill, "features": feats}


def summarize(manifest: dict) -> dict:
    steps = manifest.get("steps", [])
    by_status = {}
    for s in steps:
        by_status.setdefault(s["status"], []).append(s["id"])

    # resolved = completed + skipped (skipped считается выполненным для depends_on)
    resolved_ids = {s["id"] for s in steps if s["status"] in ("completed", "skipped")}
    runnable = []
    for s in steps:
        if s["status"] in ("pending", "failed"):
            deps = s.get("depends_on", [])
            if all(d in resolved_ids for d in deps):
                runnable.append(s["id"])

    # Collect artifacts for completed steps
    artifacts_map = {}
    for s in steps:
        if s.get("artifacts"):
            artifacts_map[s["id"]] = s["artifacts"]

    # skipped не считается in_flight
    non_terminal = {"pending", "in_progress", "failed"}
    overall = "completed" if all(
        s["status"] in ("completed", "skipped") for s in steps
    ) else "in_flight" if any(
        s["status"] in non_terminal for s in steps
    ) else "unknown"

    return {
        "status": overall,
        "skill": manifest.get("skill"),
        "pipeline_id": manifest.get("pipeline_id"),
        "started_at": manifest.get("started_at"),
        "last_update": manifest.get("last_update"),
        "context": manifest.get("context", {}),
        "counts": {
            "completed": len(by_status.get("completed", [])),
            "failed": len(by_status.get("failed", [])),
            "pending": len(by_status.get("pending", [])),
            "in_progress": len(by_status.get("in_progress", [])),
            "skipped": len(by_status.get("skipped", [])),
            "total": len(steps),
        },
        "by_status": by_status,
        "next_runnable": runnable,
        "artifacts": artifacts_map if artifacts_map else None,
    }


def excerpt(output: dict, max_items: int = 5) -> dict:
    """Generic compact excerpt: keys + counts of arrays + first N items per array."""
    if not isinstance(output, dict):
        return {"_type": str(type(output).__name__), "_value": output if not isinstance(output, list) else f"list[{len(output)}]"}

    result = {}
    for k, v in output.items():
        if isinstance(v, list):
            result[k] = {
                "_count": len(v),
                "_sample": v[:max_items],
            }
        elif isinstance(v, dict):
            # For dicts, keep top-level keys with their value types
            inner = {}
            for ik, iv in v.items():
                if isinstance(iv, list):
                    inner[ik] = f"list[{len(iv)}]"
                elif isinstance(iv, dict):
                    inner[ik] = f"dict[{len(iv)} keys]"
                else:
                    inner[ik] = iv
            result[k] = inner
        else:
            result[k] = v
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    p.add_argument("--skill", required=True)
    p.add_argument("--feature", default="pipeline", help="Фича (slug/Jira-key). По умолчанию 'pipeline' (совместимость).")
    p.add_argument("--list", action="store_true", help="Перечислить ВСЕ фичи скилла с их статусами (для резюма §0.5)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--full", action="store_true", help="Return full manifest")
    g.add_argument("--excerpt-of", help="Return compact excerpt of given step's output")
    p.add_argument("--max-items", type=int, default=5, help="Max items per array in excerpt")
    args = p.parse_args()

    project = Path(args.project or repo_root()).resolve()

    # --list → какие фичи в работе/завершены (резюм §0.5), независимо от конкретной фичи.
    if args.list:
        print(json.dumps(list_features(project, args.skill), ensure_ascii=False, indent=2))
        sys.exit(0)

    pdir = pipeline_dir(project, args.skill, args.feature)
    manifest_path = pdir / "manifest.json"

    if not manifest_path.exists():
        print(json.dumps({"status": "no_state", "feature": args.feature, "expected_at": str(manifest_path)}, ensure_ascii=False))
        sys.exit(0)

    manifest = safe_load_json(manifest_path, what="manifest")

    if args.full:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    if args.excerpt_of:
        step = next((s for s in manifest["steps"] if s["id"] == args.excerpt_of), None)
        if step is None:
            print(json.dumps({"error": f"step '{args.excerpt_of}' not found"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(2)
        out_name = step.get("output_file")
        if not out_name:
            print(json.dumps({"error": f"step '{args.excerpt_of}' has no saved output"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(2)
        out_path = pdir / out_name
        if not out_path.exists():
            print(json.dumps({"error": f"output file missing: {out_path}"}, ensure_ascii=False), file=sys.stderr)
            sys.exit(2)
        with open(out_path) as f:
            output = json.load(f)
        print(json.dumps({
            "step_id": args.excerpt_of,
            "excerpt": excerpt(output, args.max_items),
        }, ensure_ascii=False, indent=2))
        return

    # Default: summary
    print(json.dumps(summarize(manifest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
