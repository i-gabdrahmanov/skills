#!/usr/bin/env python3
"""
Append steps to an existing pipeline manifest (idempotent).

Usage:
    add_steps.py --project <path> --skill <name> --steps <json-string-or-@file>

Steps format (JSON array) — same shape as init.py:
    [
      {"id": "04-build-T1", "title": "Build T1", "depends_on": ["02-design"]},
      {"id": "05-tests",    "title": "Tests green", "depends_on": ["04-build-T1"]}
    ]

Use this when the step list is only known mid-run (e.g. feature-pipeline learns the
task breakdown after the design phase and must add `04-build-<taskId>` / `05-tests`).

Idempotent: steps whose `id` already exists are left untouched and reported as skipped,
so it is safe to re-run after a resume. Fails if the manifest does not exist yet
(run init.py first). Does not modify existing steps' status or output.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import repo_root


def load_json_arg(value: str):
    """Accepts either inline JSON or @<filepath>."""
    if value.startswith("@"):
        with open(value[1:]) as f:
            return json.load(f)
    return json.loads(value)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# База данных скиллов внутри проекта (НЕ dot-папка — иначе рантайм режет доступ).
DATA_DIR = "ground"


def pipeline_dir(project: Path, skill: str, feature: str = "pipeline") -> Path:
    return project / DATA_DIR / "statements" / skill / feature


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    p.add_argument("--skill", required=True)
    p.add_argument("--feature", default="pipeline", help="Namespace стейта на фичу (как в init.py)")
    p.add_argument("--steps", required=True, help="Steps JSON array or @file")
    args = p.parse_args()

    project = Path(args.project or repo_root()).resolve()
    pdir = pipeline_dir(project, args.skill, args.feature)
    manifest_path = pdir / "manifest.json"

    if not manifest_path.exists():
        print(f"ERROR: manifest not found at {manifest_path}. Run init.py first.", file=sys.stderr)
        sys.exit(3)

    steps_data = load_json_arg(args.steps)
    if not isinstance(steps_data, list) or not steps_data:
        print("ERROR: --steps must be a non-empty JSON array", file=sys.stderr)
        sys.exit(2)

    with open(manifest_path) as f:
        manifest = json.load(f)

    existing_ids = {s["id"] for s in manifest.get("steps", [])}
    added, skipped = [], []
    for s in steps_data:
        if "id" not in s:
            print(f"ERROR: step missing 'id': {s}", file=sys.stderr)
            sys.exit(2)
        if s["id"] in existing_ids:
            skipped.append(s["id"])
            continue
        manifest["steps"].append({
            "id": s["id"],
            "title": s.get("title", s["id"]),
            "status": s.get("status", "pending"),
            "depends_on": s.get("depends_on", []),
            "attempts": 0,
        })
        existing_ids.add(s["id"])
        added.append(s["id"])

    if added:
        manifest["last_update"] = iso_now()
        tmp = manifest_path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp, manifest_path)

    print(json.dumps({
        "status": "updated",
        "added": added,
        "skipped_existing": skipped,
        "steps_total": len(manifest["steps"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
