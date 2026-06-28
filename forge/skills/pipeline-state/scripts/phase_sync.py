#!/usr/bin/env python3
from __future__ import annotations
"""
phase_sync.py — синхронизация gate.json из manifest.json.

Читает manifest.json и актуализирует gate.json:
- Для каждой фазы из gate — все ли шаги manifest, относящиеся к этой фазе,
  имеют статус completed/skipped → фаза переводится в completed
- current_phase → первая фаза не completed (или '' если всё пройдено)

Usage:
    python3 phase_sync.py --project <root> --feature <slug> [--skill feature-pipeline]
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Единый источник истины фаз — pipeline_phases (из feature-pipeline/scripts).
# best-effort импорт: pipeline-state может жить отдельно — тогда inline-fallback.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "feature-pipeline" / "scripts"))
    import pipeline_phases as pp
    MAIN_PHASES = pp.MAIN_PHASES
    PREFIX_PHASE = pp.PREFIX_PHASE
    _guess_phase = pp.guess_phase
    _build_gate = pp.build_gate
except Exception:  # pragma: no cover — fallback при отдельном деплое
    pp = None
    MAIN_PHASES = ["00-brd", "01-grounding", "02-sdd", "02-design", "02-eval-plan",
                   "03-jira", "04-tdd", "05-verify", "06-document",
                   "07-deliver", "07-report"]
    PREFIX_PHASE = {
        "02-sdd": "02-sdd",
        "02-eval-plan": "02-eval-plan",
        "00-": "00-brd", "01-": "01-grounding", "02-": "02-design",
        "03-": "03-jira", "04-": "04-tdd", "05-": "05-verify",
        "06-": "06-document", "07-deliver-": "07-deliver",
        "07-report": "07-report", "07-": "07-deliver",
    }

    def _guess_phase(step_id: str) -> str:
        for prefix, phase in sorted(PREFIX_PHASE.items(), key=lambda x: -len(x[0])):
            if step_id.startswith(prefix):
                return phase
        return step_id
    _build_gate = None


def _is_container_step(step_id: str) -> bool:
    """Main-phase шаги (04-tdd, 05-verify, 06-doc, 07-deliver) — контейнеры,
    их статус не отражает реальную завершённость динамических шагов фазы.
    ЕДИНОЕ определение — pipeline_phases.is_container_step (fallback при отдельном деплое)."""
    if pp is not None:
        return pp.is_container_step(step_id)
    return step_id in MAIN_PHASES


def _gate_read_path(root: str, feature: str) -> str:
    """gate.json фичи (с legacy fallback) — для чтения."""
    if pp is not None:
        return str(pp.gate_path(Path(root), feature))
    per = os.path.join(root, "ground", "phases", feature, "gate.json")
    if os.path.exists(per):
        return per
    return os.path.join(root, "ground", "phases", "gate.json")


def _gate_write_path(root: str, feature: str) -> str:
    """gate.json фичи — для записи (всегда per-feature, мигрирует с legacy)."""
    return os.path.join(root, "ground", "phases", feature, "gate.json")


def sync_gate_from_manifest(project_root: str, feature: str, skill: str = "feature-pipeline") -> dict | None:
    """Синхронизирует gate.json из manifest.json — manifest источник истины, gate производный.

    ЕДИНАЯ деривация: всегда перестраиваем gate из manifest через build_gate (одна реализация
    «steps → статусы фаз»), сохраняя авторитетную мету (skip_allowed из gate, артефакты из
    phase-defs). Раньше тут был отдельный инкрементальный проход со СВОЕЙ копией container-логики,
    который мог разойтись с build_gate (P1-4). Теперь источник деривации один.

    Возвращает обновлённый gate dict или None, если manifest/gate не найдены.
    """
    manifest_path = os.path.join(
        project_root, "ground", "statements", skill, feature, "manifest.json",
    )
    gate_path = _gate_read_path(project_root, feature)

    if not os.path.exists(manifest_path):
        return None
    if not os.path.exists(gate_path):
        return None

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # Fail-soft как и при отсутствии файла: не роняем вызывающий процесс (update.py),
        # только сообщаем — синхронизация gate пропускается, а не валит всю команду.
        print(f"phase_sync: manifest нечитаем/повреждён ({manifest_path}): {e}", file=sys.stderr)
        return None

    return _regenerate_gate(project_root, feature, skill, manifest)


def _regenerate_gate(project_root: str, feature: str, skill: str,
                     manifest: dict) -> dict | None:
    """Пересоздать gate.json фичи из manifest, если manifest изменился."""
    # читаем существующий (с legacy fallback), пишем — в per-feature путь (миграция)
    read_gate = _gate_read_path(project_root, feature)
    gate_path = _gate_write_path(project_root, feature)

    # Пытаемся прочитать существующий gate.json и phase-defs.json
    # для сохранения skip_allowed, allowed_skills и blocked_tools
    existing_meta = {}
    if os.path.exists(read_gate):
        try:
            with open(read_gate) as f:
                gate_data = json.load(f)
            for p in gate_data.get("phases", []):
                existing_meta[p["id"]] = {
                    "skip_allowed": p.get("skip_allowed", True),
                }
        except Exception:
            pass

    defs_meta = {}
    defs_path = str(pp.defs_path(Path(project_root), feature)) if pp is not None \
        else os.path.join(project_root, "ground", "phases", "phase-defs.json")
    if os.path.exists(defs_path):
        try:
            with open(defs_path) as f:
                defs_data = json.load(f)
            for p in defs_data.get("phases", []):
                pid = p["id"]
                defs_meta[pid] = {
                    "allowed_skills": p.get("allowed_skills", []),
                    "blocked_tools_until_complete": p.get("blocked_tools_until_complete", []),
                }
        except Exception:
            pass

    steps = manifest.get("steps", [])

    # Единая реализация построения gate — pipeline_phases.build_gate (с fallback).
    if _build_gate is not None:
        gate = _build_gate(steps, manifest, existing_meta=existing_meta, defs_meta=defs_meta)
        gate["feature"] = feature
    else:
        # inline-fallback (если pipeline_phases недоступен при отдельном деплое)
        seen = set()
        phases = []
        for step in steps:
            pid = _guess_phase(step.get("id", ""))
            if pid not in seen:
                seen.add(pid)
                phases.append({"id": pid, "label": step.get("title", pid),
                               "skip_allowed": existing_meta.get(pid, {}).get("skip_allowed", pid != "01-grounding"),
                               "status": "pending", "depends_on": [], "artifacts": []})
        phases.sort(key=lambda p: MAIN_PHASES.index(p["id"]) if p["id"] in MAIN_PHASES else 999)
        step_status = {s["id"]: s["status"] for s in steps}
        for phase in phases:
            ps = [s["id"] for s in steps if _guess_phase(s["id"]) == phase["id"]
                  and not _is_container_step(s["id"])]
            if ps and all(step_status.get(sid) in ("completed", "skipped") for sid in ps):
                phase["status"] = "completed"
        current = ""
        for phase in phases:
            if phase["status"] != "completed":
                current = phase["id"]; phase["status"] = "in_progress"; break
        main_order = [m for m in MAIN_PHASES if m in seen]
        for i, pid in enumerate(main_order):
            for p in phases:
                if p["id"] == pid and i > 0 and main_order[i - 1] not in p["depends_on"]:
                    p["depends_on"].append(main_order[i - 1])
        gate = {"pipeline_id": manifest.get("pipeline_id", ""), "feature": feature,
                "schema": "phase-gate@1", "current_phase": current, "phases": phases}

    os.makedirs(os.path.dirname(gate_path), exist_ok=True)
    tmp = gate_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(gate, f, indent=2, ensure_ascii=False)
    os.replace(tmp, gate_path)
    # phases/current читаем из готового gate (в build_gate-ветке локальных переменных нет — был NameError)
    print(f"phase_sync: regenerated gate.json "
          f"({len(gate.get('phases', []))} phases, current={gate.get('current_phase', '')})",
          file=sys.stderr)
    return gate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", default=os.getcwd())
    p.add_argument("--feature", required=True)
    p.add_argument("--skill", default="feature-pipeline")
    args = p.parse_args()

    result = sync_gate_from_manifest(args.project, args.feature, args.skill)
    if result is None:
        print("phase_sync: manifest or gate.json not found — nothing to sync", file=sys.stderr)
        sys.exit(0)

    print(json.dumps({
        "status": "synced" if result else "noop",
        "current_phase": result.get("current_phase", ""),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()