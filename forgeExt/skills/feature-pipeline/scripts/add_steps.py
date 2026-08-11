#!/usr/bin/env python3
from __future__ import annotations
"""
Добавляет шаги в manifest.json пайплайна.
Идемпотентно: если шаг с таким id уже есть — не перезаписывает (кроме status).

Синхронизировать нечего: фазовое состояние ВЫЧИСЛЯЕТСЯ из манифеста
(pipeline_phases.live_state), поэтому новые шаги (02-eval-plan, 04-test-*)
видны фазовой машине сразу. Прежние gate.json/phase-defs.json были кэшем этой
же деривации — их приходилось перестраивать здесь, и пропуск ребилда делал
новые шаги невидимыми.

Usage:
    python3 add_steps.py --skill feature-pipeline --feature <slug> --steps '<json_array>'

Пример steps:
    [{"id":"04-test-T1","title":"TDD RED: T1","depends_on":["02-design"]}]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def get_manifest_path(skill: str, feature: str, project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return (
        root
        / "ground"
        / "statements"
        / skill
        / feature
        / "manifest.json"
    )


# Единый источник истины фаз/судей — pipeline_phases.
import pipeline_phases as pp

PREFIX_PHASE = pp.PREFIX_PHASE
MAIN_PHASES = pp.MAIN_PHASES
REQUIRED_JUDGES_MASK = pp.REQUIRED_JUDGES_MASK
_match_required_judges = pp.match_required_judges
_guess_phase = pp.guess_phase


def add_steps(skill: str, feature: str, steps: list) -> dict:
    project_root = Path.cwd()
    manifest_path = get_manifest_path(skill, feature)

    if not manifest_path.exists():
        return {"status": "error", "error": f"Manifest not found: {manifest_path}"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"status": "error", "error": f"Manifest повреждён ({manifest_path}): {e}"}
    existing_ids = {s["id"] for s in manifest.get("steps", [])}

    added = 0
    skipped = 0

    for step in steps:
        if step["id"] in existing_ids:
            skipped += 1
            continue
        step["status"] = "pending"
        step["attempts"] = 0
        # Применяем required_judges по той же маске, что и init.py
        req = _match_required_judges(step["id"])
        if req:
            step["required_judges"] = req
        manifest["steps"].append(step)
        added += 1

    if added > 0:
        manifest["last_update"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # Фазовое состояние никуда не синхронизируется: оно ВЫЧИСЛЯЕТСЯ из манифеста
        # (pipeline_phases.live_state). Прежние gate.json/phase-defs.json были кэшем
        # этой же деривации и требовали ребилда на каждое изменение шагов.
        decision = pp.live_phase_decision(manifest)

        return {
            "status": "ok",
            "manifest": str(manifest_path),
            "added": added,
            "skipped": skipped,
            "total": len(manifest["steps"]),
            "current_phase": decision["current_phase"],
            "phase_count": len(decision["phases"]),
        }

    return {
        "status": "ok",
        "manifest": str(manifest_path),
        "added": added,
        "skipped": skipped,
        "total": len(manifest["steps"]),
        "gate_synced": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--project-root", default=None, help="Корень проекта (по умолчанию cwd)")
    parser.add_argument("--steps", required=True, help="JSON array string")
    args = parser.parse_args()

    try:
        steps = json.loads(args.steps)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    result = add_steps(args.skill, args.feature, steps)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["status"] == "ok" else 1)