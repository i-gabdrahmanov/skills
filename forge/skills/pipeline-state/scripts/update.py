#!/usr/bin/env python3
from __future__ import annotations
"""
Update a step's status in the pipeline manifest.

Usage:
    update.py --project <path> --skill <name> --step-id <id> --status <status> \\
        [--artifacts '<json>']        # JSON mapping of artifact keys→paths
        [--output-file <path>]        # path to JSON file with subagent output
        [--output-json <inline>]      # OR inline JSON string
        [--output-stdin]              # OR read JSON from stdin
        [--error <msg>]               # error message (for status=failed)

Statuses: pending | in_progress | completed | failed | skipped

If status=completed and output is provided, saves it to
<project>/ground/statements/<skill>/pipeline/<step-id>.json

--artifacts stores a key→path mapping in the step (e.g.
  '{"tech-design":"docs/feature-pipeline/slug/tech-design.md","task-plan":"..."}'
Paths are normalized to be relative to project root.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import repo_root
from phase_sync import sync_gate_from_manifest


VALID_STATUSES = {"pending", "in_progress", "completed", "failed", "skipped"}


# Абсолютный путь к override_judge.py (тот же каталог) — чтобы подсказка была
# исполняемой как есть, без подстановки <project> рантаймом Qwen.
_OVERRIDE_SCRIPT = Path(__file__).resolve().parent / "override_judge.py"


def _judges_dir(project: Path, skill: str, feature: str) -> Path:
    """Путь к каталогу вердиктов судей."""
    return project / "ground" / "statements" / skill / feature / "judges"


def _overrides_dir(project: Path, skill: str, feature: str) -> Path:
    """Путь к каталогу ручных override-файлов."""
    return project / "ground" / "statements" / skill / feature / "overrides"


def _load_override(project: Path, skill: str, feature: str, judge_name: str) -> dict | None:
    """Читает override-файл судьи, если существует. None — нет override."""
    path = _overrides_dir(project, skill, feature) / f"{judge_name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _check_judges(step: dict, project: Path, skill: str, feature: str):
    """
    Детерминированная блокировка: если шаг помечен completed, но не все его
    required_judges пройдены — выкинуть исключение.

    Исключение: если для судьи есть ручной override-файл (overrides/<judge>.json),
    блокировка снимается и факт отклонения фиксируется в manifest-step как предупреждение.
    Создать override: python3 override_judge.py --judge <name> --feature <slug> --reason "..."
    """
    required = step.get("required_judges", [])
    if not required:
        return

    judges_dir = _judges_dir(project, skill, feature)
    blocking = []
    overridden = []

    for judge_name in required:
        verdict_path = judges_dir / f"{judge_name}.json"

        # 1. Нет вердикта вообще
        if not verdict_path.exists():
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                overridden.append(
                    f"⚠️  '{judge_name}' не запускался — пропущен вручную. "
                    f"Причина: {ov.get('reason', '?')}"
                )
                continue
            blocking.append(
                f"❌ Вердикт '{judge_name}.json' не найден — судья не запускался.\n"
                f"   Чтобы пропустить: python3 {_OVERRIDE_SCRIPT} "
                f"--judge {judge_name} --feature {feature} --step-id {step['id']} "
                f"--reason \"<объяснение>\""
            )
            continue

        # 2. Вердикт есть, но повреждён
        try:
            verdict = json.loads(verdict_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                overridden.append(
                    f"⚠️  '{judge_name}' повреждён — пропущен вручную. "
                    f"Причина: {ov.get('reason', '?')}"
                )
                continue
            blocking.append(f"❌ Вердикт '{judge_name}.json' повреждён: {e}")
            continue

        # 3. Вердикт есть, но FAIL
        if not verdict.get("passed", False):
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                issues = verdict.get("blocking_issues", [])
                overridden.append(
                    f"⚠️  '{judge_name}' FAIL — пропущен вручную.\n"
                    f"   Причина override: {ov.get('reason', '?')}\n"
                    f"   Заблокированные issues ({len(issues)}): "
                    + (issues[0][:120] if issues else "нет") +
                    (" ..." if len(issues) > 1 else "")
                )
                continue
            issues = verdict.get("blocking_issues", ["не указаны"])
            blocking.append(
                f"❌ Вердикт '{judge_name}.json' — FAIL.\n"
                f"   Blocking issues: {issues}\n"
                f"   Чтобы пропустить: python3 {_OVERRIDE_SCRIPT} "
                f"--judge {judge_name} --feature {feature} --step-id {step['id']} "
                f"--reason \"<объяснение>\""
            )

    # Записываем предупреждения об override в step (для аудита)
    if overridden:
        step.setdefault("override_warnings", [])
        for msg in overridden:
            if msg not in step["override_warnings"]:
                step["override_warnings"].append(msg)
        print("\n".join(f"  {m}" for m in overridden), file=sys.stderr)

    if blocking:
        raise RuntimeError(
            f"Шаг {step['id']} не может быть закрыт: {len(blocking)} блокирующих проблем(ы).\n" +
            "\n".join(blocking)
        )


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
    p.add_argument("--step-id", required=True)
    p.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    p.add_argument("--artifacts", help='JSON mapping of artifact keys to file paths, e.g. \'{"tech-design":"docs/.../tech-design.md","task-plan":"..."}\'')
    g = p.add_mutually_exclusive_group()
    g.add_argument("--output-file", help="Path to JSON file with subagent's output")
    g.add_argument("--output-json", help="Inline JSON string of subagent's output")
    g.add_argument("--output-stdin", action="store_true", help="Read JSON output from stdin")
    p.add_argument("--error", help="Error message (use with status=failed)")
    p.add_argument("--skip-judges", action="store_true", help="Skip judge check (use when restoring state after init --force)")
    args = p.parse_args()

    project = Path(args.project or repo_root()).resolve()
    pdir = pipeline_dir(project, args.skill, args.feature)
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

    # Детерминированная блокировка: не даём закрыть шаг без судей
    if not args.skip_judges and args.status == "completed" and prev_status != "completed":
        _check_judges(step, project, args.skill, args.feature)

    step["status"] = args.status

    # Track timestamps
    if args.status == "in_progress" and prev_status != "in_progress":
        step["started_at"] = now
        step["attempts"] = step.get("attempts", 0) + 1
    elif args.status in ("completed", "failed", "skipped"):
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

    # Handle artifacts mapping
    if args.artifacts and args.status == "completed":
        try:
            artifacts = json.loads(args.artifacts)
            if not isinstance(artifacts, dict):
                print("WARNING: --artifacts must be a JSON object (dict), ignoring", file=sys.stderr)
            else:
                # Normalize paths to be relative to project root
                project_str = str(project)
                normalized = {}
                for key, path in artifacts.items():
                    if not isinstance(path, str):
                        continue
                    p_abs = Path(path)
                    if p_abs.is_absolute():
                        try:
                            rel = p_abs.relative_to(project)
                            normalized[key] = str(rel)
                        except ValueError:
                            normalized[key] = path
                    else:
                        normalized[key] = path
                step["artifacts"] = normalized
        except json.JSONDecodeError as e:
            print(f"WARNING: --artifacts invalid JSON: {e}, ignoring", file=sys.stderr)

    if args.error:
        step["error"] = args.error
    elif args.status != "failed" and "error" in step:
        del step["error"]

    manifest["last_update"] = now

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    # Синхронизация gate.json из manifest.json
    try:
        sync_gate_from_manifest(str(project), args.feature, args.skill)
    except Exception as e:
        print(f"WARNING: phase_sync failed: {e}", file=sys.stderr)

    print(json.dumps({
        "status": "updated",
        "step_id": args.step_id,
        "new_status": args.status,
        "output_saved": step.get("output_file") is not None and args.status == "completed",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
