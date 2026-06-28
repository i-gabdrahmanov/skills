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

import judges_registry
from _util import repo_root, feature_docs_dir, safe_slug, safe_load_json


def load_json_arg(value: str):
    """Accepts either inline JSON or @<filepath>."""
    if value.startswith("@"):
        return safe_load_json(value[1:], what="--steps файл")
    return json.loads(value)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# База данных скиллов внутри проекта (НЕ dot-папка — иначе рантайм режет доступ
# по path-гарду и seatbelt). Единый каталог для всех скиллов конвейера.
DATA_DIR = "ground"


def pipeline_dir(project: Path, skill: str, feature: str = "pipeline") -> Path:
    # feature намеспейсит стейт на фичу: statements/<skill>/<feature>/.
    # Дефолт "pipeline" сохраняет прежнее поведение (system-analyst/minor-defect-fix).
    return project / DATA_DIR / "statements" / skill / feature


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    p.add_argument("--skill", required=True, help="Skill name (e.g. system-analysis)")
    p.add_argument("--steps", required=True, help="Steps JSON array or @file")
    p.add_argument("--context", default="{}", help="Context JSON object or @file (optional)")
    p.add_argument("--force", action="store_true", help="Archive existing manifest and create fresh")
    p.add_argument("--feature", default="pipeline", help="Namespace стейта на фичу (slug/Jira-key). По умолчанию 'pipeline'.")
    args = p.parse_args()

    # slug идёт в пути (statements/<skill>/<feature>/, docs/.../<feature>/) — fail-closed на traversal
    try:
        safe_slug(args.feature)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    project = Path(args.project or repo_root()).resolve()
    if not project.exists():
        print(f"ERROR: project root not found: {project}", file=sys.stderr)
        sys.exit(2)

    steps_data = load_json_arg(args.steps)
    context_data = load_json_arg(args.context) if args.context else {}

    if not isinstance(steps_data, list) or not steps_data:
        print("ERROR: --steps must be a non-empty JSON array", file=sys.stderr)
        sys.exit(2)

    pdir = pipeline_dir(project, args.skill, args.feature)
    manifest_path = pdir / "manifest.json"

    if manifest_path.exists():
        if not args.force:
            print(f"ERROR: manifest already exists at {manifest_path}", file=sys.stderr)
            print("Use read.py to inspect, or --force to archive and recreate", file=sys.stderr)
            sys.exit(3)
        # Archive existing (per-feature)
        archive_dir = project / DATA_DIR / "statements" / args.skill / "archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive_target = archive_dir / f"{args.feature}-{ts}"
        pdir.rename(archive_target)
        print(f"Archived previous state to {archive_target}", file=sys.stderr)

    pdir.mkdir(parents=True, exist_ok=True)

    now = iso_now()
    pipeline_id = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")

    # Маска required_judges — из единого реестра references/judges-registry.json
    # (judges_registry.match_step). Раньше дублировалась здесь и в patch_manifest_judges.py.
    steps = []
    for s in steps_data:
        if "id" not in s:
            print(f"ERROR: step missing 'id': {s}", file=sys.stderr)
            sys.exit(2)
        req = judges_registry.match_step(s["id"])
        step = {
            "id": s["id"],
            "title": s.get("title", s["id"]),
            "status": s.get("status", "pending"),
            "depends_on": s.get("depends_on", []),
            "attempts": 0,
        }
        if req:
            step["required_judges"] = req
        steps.append(step)

    manifest = {
        "version": 1,
        "skill": args.skill,
        "feature": args.feature,
        "pipeline_id": pipeline_id,
        "started_at": now,
        "last_update": now,
        "project_root": str(project),
        "context": context_data,
        "steps": steps,
    }

    # Auto-resolve artifacts for existing files by convention.
    # Файлы лежат в <feature_docs_dir>/<feature>/<name> — каталог резолвится по docs-конфигу
    # (in-repo / separate-repo), а не хардкодом docs/feature-pipeline.
    # Map: step-id prefix → list of (artifact_key, filename)
    ARTIFACT_CONVENTIONS = {
        "00-brd":       [("brd", "brd.md")],
        "02-sdd":       [("sdd", "sdd.md")],
        "02-design":    [("tech-design", "tech-design.md"), ("task-plan", "task-plan.json")],
        "02-eval-plan": [("eval-plan", "eval-plan.json")],
        "03-jira":      [("jira-result", "jira-tasks-result.json")],
        "07-deliver-":  [("pr-info", "pr-info.json")],
    }
    fdir = feature_docs_dir(project) / args.feature
    for step in steps:
        step_id = step["id"]
        # Find matching convention prefix
        matched = None
        for prefix, convs in ARTIFACT_CONVENTIONS.items():
            if step_id == prefix or step_id.startswith(prefix):
                matched = convs
                break
        if not matched:
            continue
        artifacts = {}
        for key, name in matched:
            candidate = fdir / name
            if candidate.exists():
                # relative — если под проектом (in-repo); иначе absolute (separate-repo)
                try:
                    artifacts[key] = str(candidate.relative_to(project))
                except ValueError:
                    artifacts[key] = str(candidate)
        if artifacts:
            step["artifacts"] = artifacts

    manifest["steps"] = steps

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    print(json.dumps({
        "status": "initialized",
        "manifest": str(manifest_path),
        "steps_count": len(steps),
        "artifacts_resolved": any(s.get("artifacts") for s in steps),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
