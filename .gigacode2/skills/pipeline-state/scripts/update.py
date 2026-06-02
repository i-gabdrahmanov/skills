#!/usr/bin/env python3
"""
Update a step's status in the pipeline manifest.

Usage:
    update.py --project <path> --skill <name> --step-id <id> --status <status> \\
        [--output-file <path>]        # path to JSON file with subagent output
        [--output-json <inline>]      # OR inline JSON string
        [--output-stdin]              # OR read JSON from stdin
        [--error <msg>]               # error message (for status=failed)

Statuses: pending | in_progress | completed | failed | skipped

If status=completed and output is provided, saves it to
<project>/.gigacode/statements/<skill>/pipeline/<step-id>.json
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


VALID_STATUSES = {"pending", "in_progress", "completed", "failed", "skipped"}


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pipeline_dir(project: Path, skill: str) -> Path:
    return project / ".gigacode" / "statements" / skill / "pipeline"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--skill", required=True)
    p.add_argument("--step-id", required=True)
    p.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--output-file", help="Path to JSON file with subagent's output")
    g.add_argument("--output-json", help="Inline JSON string of subagent's output")
    g.add_argument("--output-stdin", action="store_true", help="Read JSON output from stdin")
    p.add_argument("--error", help="Error message (use with status=failed)")
    args = p.parse_args()

    project = Path(args.project).resolve()
    pdir = pipeline_dir(project, args.skill)
    manifest_path = pdir / "manifest.json"

    if not manifest_path.exists():
        print(f"ERROR: manifest not found at {manifest_path}. Run init.py first.", file=sys.stderr)
        sys.exit(3)

    with open(manifest_path) as f:
        manifest = json.load(f)

    step = next((s for s in manifest["steps"] if s["id"] == args.step_id), None)
    if step is None:
        print(f"ERROR: step '{args.step_id}' not found in manifest", file=sys.stderr)
        sys.exit(2)

    now = iso_now()
    prev_status = step.get("status")
    step["status"] = args.status

    # Track timestamps
    if args.status == "in_progress" and prev_status != "in_progress":
        step["started_at"] = now
        step["attempts"] = step.get("attempts", 0) + 1
    elif args.status in ("completed", "failed"):
        step["completed_at"] = now
        if "started_at" in step:
            try:
                started = datetime.strptime(step["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                ended = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                step["duration_ms"] = int((ended - started).total_seconds() * 1000)
            except Exception:
                pass

    # Handle output
    output_data = None
    if args.output_file:
        with open(args.output_file) as f:
            output_data = json.load(f)
    elif args.output_json:
        output_data = json.loads(args.output_json)
    elif args.output_stdin:
        raw = sys.stdin.read().strip()
        if raw:
            output_data = json.loads(raw)

    if output_data is not None and args.status == "completed":
        out_file = pdir / f"{args.step_id}.json"
        tmp = out_file.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, out_file)
        step["output_file"] = out_file.name

    if args.error:
        step["error"] = args.error
    elif args.status != "failed" and "error" in step:
        del step["error"]

    manifest["last_update"] = now

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    print(json.dumps({
        "status": "updated",
        "step_id": args.step_id,
        "new_status": args.status,
        "output_saved": step.get("output_file") is not None and args.status == "completed",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
