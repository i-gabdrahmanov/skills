#!/usr/bin/env python3
"""init_phase_gate.py — генератор ground/phases/gate.json + phase-defs.json из steps.json.

Создаёт файловую state-machine для пайплайна — gate.json (текущее состояние фаз)
и phase-defs.json (декларация: allowed_skills, blocked_tools, required_artifacts).

Использование:
    python init_phase_gate.py --project <root> --steps steps.json    # создать (не перезапишет)
    python init_phase_gate.py --project <root> --steps steps.json --force  # перезаписать
    python init_phase_gate.py --project <root> --steps steps.json --status manifest.json  # восстановить из манифеста

Выход: <root>/ground/phases/gate.json, <root>/ground/phases/phase-defs.json

Группировка:
  - Фазы с префиксом "0x-" (00-brd, 01-grounding, 02-design...) — основные фазы
  - Префикс "04-*" — TDD-циклы (04-test-*, 04-build-*) — маппятся в фазу "04-tdd"
  - Остальные — каждая как отдельная фаза (02-eval-plan, 03-jira, 05-tests, 06-spec)

  Основные фазы блокируют Read/Grep в src/ (01-grounding обязателен).
"""
import argparse, json, os, sys, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_phases as pp

# Единый источник истины — pipeline_phases. Имена ниже сохранены для обратной
# совместимости со старыми импортерами (re-export тонкими алиасами).
PREFIX_PHASE = pp.PREFIX_PHASE
MAIN_PHASES = pp.MAIN_PHASES
_guess_phase = pp.guess_phase
_blocked_tools = pp.blocked_tools
_blocked_paths = pp.blocked_paths
_allowed_skills = pp.allowed_skills
_required_artifacts = pp.required_artifacts
build_gate = pp.build_gate
build_defs = pp.build_defs


def main() -> int:
    ap = argparse.ArgumentParser(description="Создать ground/phases/gate.json + phase-defs.json")
    ap.add_argument("--project", default=None, help="Корень проекта (git root или cwd)")
    ap.add_argument("--steps", required=True, help="path к steps.json (относительно project)")
    ap.add_argument("--status", default=None, help="path к manifest.json для восстановления статусов")
    ap.add_argument("--feature", default=None, help="namespace фичи (по умолчанию из manifest или 'pipeline')")
    ap.add_argument("--force", action="store_true", help="Перезаписать существующие файлы")
    args = ap.parse_args()

    root = Path(args.project or os.getcwd())
    steps_path = root / args.steps
    if not steps_path.exists():
        print(f"[init-phase-gate] ERROR: steps не найден: {steps_path}", file=sys.stderr)
        return 1

    with open(steps_path, encoding="utf-8") as f:
        steps = json.load(f)

    manifest = None
    if args.status:
        mpath = root / args.status
        if mpath.exists():
            with open(mpath, encoding="utf-8") as f:
                manifest = json.load(f)

    feature = (args.feature
               or (manifest or {}).get("feature")
               or ((manifest or {}).get("context") or {}).get("feature")
               or "pipeline")
    phases_dir = pp.gate_dir(root, feature)
    phases_dir.mkdir(parents=True, exist_ok=True)

    gate_path = phases_dir / "gate.json"
    defs_path = phases_dir / "phase-defs.json"

    if gate_path.exists() and not args.force:
        print(f"[init-phase-gate] {gate_path} существует (--force для перезаписи)")
    else:
        gate = build_gate(steps, manifest)
        with open(gate_path, "w", encoding="utf-8") as f:
            json.dump(gate, f, ensure_ascii=False, indent=2)
        print(f"[init-phase-gate] {gate_path} created")
        print(f"             current_phase: {gate['current_phase']}")

    if defs_path.exists() and not args.force:
        print(f"[init-phase-gate] {defs_path} существует (--force для перезаписи)")
    else:
        defs = build_defs(steps)
        with open(defs_path, "w", encoding="utf-8") as f:
            json.dump(defs, f, ensure_ascii=False, indent=2)
        print(f"[init-phase-gate] {defs_path} created")

    return 0


if __name__ == "__main__":
    sys.exit(main())