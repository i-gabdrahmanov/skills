#!/usr/bin/env python3
from __future__ import annotations
"""
Добавляет шаги в manifest.json пайплайна и синхронизирует gate.json.
Идемпотентно: если шаг с таким id уже есть — не перезаписывает (кроме status).

ОБЯЗАТЕЛЬНАЯ синхронизация: после добавления шагов перестраивает gate.json
и phase-defs.json из актуального manifest.json. Это единственный источник
truth для state-recorder'а — без этой синхронизации новые шаги (например,
02-eval-plan, 04-test-*) остаются невидимыми для фазовой state-machine, и
state-recorder перепрыгивает через них.

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


def _load_phase_defs(project_root: Path, feature: str) -> dict | None:
    """Загрузить phase-defs.json фичи (с legacy fallback). Если нет — None."""
    defs_path = pp.defs_path(project_root, feature)
    if defs_path.exists():
        try:
            return json.loads(defs_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _load_or_create_defs(project_root: Path, steps: list, feature: str) -> dict:
    """Загрузить phase-defs.json фичи или создать из pipeline_phases."""
    defs = _load_phase_defs(project_root, feature)
    if defs:
        return defs
    return pp.build_defs(steps)


def _lookup_phase_meta(defs: dict, phase_id: str) -> dict | None:
    """Найти мету фазы в phase-defs.json по id."""
    for p in defs.get("phases", []):
        if p["id"] == phase_id:
            return p
    return None


# Единый источник истины фаз/судей — pipeline_phases.
import pipeline_phases as pp

PREFIX_PHASE = pp.PREFIX_PHASE
MAIN_PHASES = pp.MAIN_PHASES
REQUIRED_JUDGES_MASK = pp.REQUIRED_JUDGES_MASK
_match_required_judges = pp.match_required_judges
_guess_phase = pp.guess_phase


def _rebuild_gate(project_root: Path, manifest: dict, feature: str) -> dict:
    """Перестроить gate.json фичи из manifest.json через единый pp.build_gate."""
    steps = manifest.get("steps", [])

    # existing_meta: сохранить skip_allowed из текущего gate (могли поправить руками)
    existing_meta = {}
    cur_gate = pp.gate_path(project_root, feature)
    if cur_gate.exists():
        try:
            for p in json.loads(cur_gate.read_text()).get("phases", []):
                existing_meta[p["id"]] = {"skip_allowed": p.get("skip_allowed", True)}
        except (json.JSONDecodeError, OSError):
            pass

    # defs_meta: required_artifacts из phase-defs
    defs_meta = {}
    for p in _load_or_create_defs(project_root, steps, feature).get("phases", []):
        defs_meta[p["id"]] = {"required_artifacts": p.get("required_artifacts", [])}

    gate = pp.build_gate(steps, manifest, existing_meta=existing_meta, defs_meta=defs_meta)

    out = pp.gate_dir(project_root, feature) / "gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n")
    return gate


def _rebuild_phase_defs(project_root: Path, manifest: dict, feature: str) -> dict:
    """Перестроить phase-defs.json фичи из manifest.json.

    Не перезаписывает существующие метаданные фаз (allowed_skills и т.д.) —
    хранятся в `ground/phases/<feature>/phase-defs.json` как единый источник истины.
    Только добавляет фазы, которых ещё нет.
    """
    steps = manifest.get("steps", [])

    # Загружаем существующий phase-defs.json (если есть)
    existing = _load_phase_defs(project_root, feature)
    existing_map = {}
    if existing:
        for p in existing.get("phases", []):
            existing_map[p["id"]] = p

    seen = set(existing_map.keys())
    defs_list = list(existing.get("phases", [])) if existing else []

    # Добавляем фазы из шагов, которых ещё нет в phase-defs
    for step in steps:
        pid = _guess_phase(step["id"])
        if pid not in seen:
            seen.add(pid)
            # Новая фаза — дефолты из единого pipeline_phases
            allowed = pp.allowed_skills(pid)
            blocked_tools = pp.blocked_tools(pid)
            blocked_paths = pp.blocked_paths(pid)
            artifacts = pp.required_artifacts(pid)
            defs_list.append({
                "id": pid,
                "allowed_skills": allowed,
                "blocked_tools_until_complete": blocked_tools,
                "blocked_paths": blocked_paths,
                "required_artifacts": artifacts,
            })

    phase_defs = {"schema": "phase-defs@1", "phases": defs_list}
    out = pp.gate_dir(project_root, feature) / "phase-defs.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(phase_defs, ensure_ascii=False, indent=2) + "\n")
    return phase_defs


def add_steps(skill: str, feature: str, steps: list) -> dict:
    project_root = Path.cwd()
    manifest_path = get_manifest_path(skill, feature)

    if not manifest_path.exists():
        return {"status": "error", "error": f"Manifest not found: {manifest_path}"}

    try:
        manifest = json.loads(manifest_path.read_text())
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
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

        # ════════════════════════════════════════════════════════════════
        # СИНХРОНИЗАЦИЯ: перестроить gate.json и phase-defs.json
        # ════════════════════════════════════════════════════════════════
        gate = _rebuild_gate(project_root, manifest, feature)
        defs = _rebuild_phase_defs(project_root, manifest, feature)

        return {
            "status": "ok",
            "manifest": str(manifest_path),
            "added": added,
            "skipped": skipped,
            "total": len(manifest["steps"]),
            "gate_synced": True,
            "current_phase": gate["current_phase"],
            "phase_count": len(gate["phases"]),
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