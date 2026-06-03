#!/usr/bin/env python3
"""
Initialize a new pipeline manifest.

Usage:
    init.py --project <path> --skill <name> \\
        --steps <json-string-or-@file> \\
        [--context <json-string-or-@file>]

Creates: <project>/ground/statements/<skill>/pipeline/manifest.json

Steps format (JSON array):
    [
      {"id": "01-structure", "title": "Map structure", "depends_on": []},
      {"id": "02-api", "title": "Map API", "depends_on": ["01-structure"]},
      ...
    ]

If manifest already exists, fails with non-zero exit (use update.py for changes).
Caller should call read.py first and decide what to do.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_json_arg(value: str):
    """Accepts either inline JSON or @<filepath>."""
    if value.startswith("@"):
        with open(value[1:]) as f:
            return json.load(f)
    return json.loads(value)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# База данных скиллов внутри проекта (НЕ dot-папка — иначе рантайм режет доступ
# по path-гарду и seatbelt). Единый каталог для всех скиллов конвейера.
DATA_DIR = "ground"


def pipeline_dir(project: Path, skill: str) -> Path:
    return project / DATA_DIR / "statements" / skill / "pipeline"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="Absolute path to project root")
    p.add_argument("--skill", required=True, help="Skill name (e.g. system-analysis)")
    p.add_argument("--steps", required=True, help="Steps JSON array or @file")
    p.add_argument("--context", default="{}", help="Context JSON object or @file (optional)")
    p.add_argument("--force", action="store_true", help="Archive existing manifest and create fresh")
    args = p.parse_args()

    project = Path(args.project).resolve()
    if not project.exists():
        print(f"ERROR: project root not found: {project}", file=sys.stderr)
        sys.exit(2)

    steps_data = load_json_arg(args.steps)
    context_data = load_json_arg(args.context) if args.context else {}

    if not isinstance(steps_data, list) or not steps_data:
        print("ERROR: --steps must be a non-empty JSON array", file=sys.stderr)
        sys.exit(2)

    pdir = pipeline_dir(project, args.skill)
    manifest_path = pdir / "manifest.json"

    if manifest_path.exists():
        if not args.force:
            print(f"ERROR: manifest already exists at {manifest_path}", file=sys.stderr)
            print("Use read.py to inspect, or --force to archive and recreate", file=sys.stderr)
            sys.exit(3)
        # Archive existing
        archive_dir = project / DATA_DIR / "statements" / args.skill / "archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive_target = archive_dir / f"pipeline-{ts}"
        pdir.rename(archive_target)
        print(f"Archived previous state to {archive_target}", file=sys.stderr)

    pdir.mkdir(parents=True, exist_ok=True)

    now = iso_now()
    pipeline_id = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")

    steps = []
    for s in steps_data:
        if "id" not in s:
            print(f"ERROR: step missing 'id': {s}", file=sys.stderr)
            sys.exit(2)
        steps.append({
            "id": s["id"],
            "title": s.get("title", s["id"]),
            "status": s.get("status", "pending"),
            "depends_on": s.get("depends_on", []),
            "attempts": 0,
        })

    manifest = {
        "version": 1,
        "skill": args.skill,
        "pipeline_id": pipeline_id,
        "started_at": now,
        "last_update": now,
        "project_root": str(project),
        "context": context_data,
        "steps": steps,
    }

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    print(json.dumps({
        "status": "initialized",
        "manifest": str(manifest_path),
        "steps_count": len(steps),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
