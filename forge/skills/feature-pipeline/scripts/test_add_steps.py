#!/usr/bin/env python3
"""Тесты add_steps.py (feature-pipeline версия) — добавление шагов в manifest + ОБЯЗАТЕЛЬНАЯ
пересборка gate.json/phase-defs.json и проставление required_judges по маске. Раньше прямого
теста не было (логика косвенно пинилась test_phase_consistency). Здесь фиксируем поведение
рантайма: идемпотентность, маска судей для 04-build-*, синхронизацию gate.

Запуск: python3 test_add_steps.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "add_steps.py"
SKILL = "feature-pipeline"
FEATURE = "demo"
PASSED = 0
FAILED = 0


def _project(td: str) -> Path:
    project = Path(td)
    d = project / "ground" / "statements" / SKILL / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "skill": SKILL, "feature": FEATURE, "context": {},
        "steps": [{"id": "02-design", "status": "completed"}],
    }), encoding="utf-8")
    return project


def run(project: Path, steps: list):
    """add_steps использует Path.cwd() — запускаем с cwd=project."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--skill", SKILL, "--feature", FEATURE,
         "--steps", json.dumps(steps)],
        capture_output=True, text=True, cwd=str(project))
    try:
        parsed = json.loads(r.stdout.strip())
    except json.JSONDecodeError:
        parsed = {}
    return r.returncode, parsed, (r.stdout + r.stderr).strip()


def _manifest(project: Path) -> dict:
    return json.loads(
        (project / "ground" / "statements" / SKILL / FEATURE / "manifest.json").read_text(encoding="utf-8"))


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def main() -> int:
    # 1. Добавление новых шагов → added=2, gate_synced=True
    with tempfile.TemporaryDirectory() as td:
        project = _project(td)
        rc, j, out = run(project, [
            {"id": "04-test-T1", "title": "RED T1", "depends_on": ["02-design"]},
            {"id": "04-build-T1", "title": "GREEN T1", "depends_on": ["04-test-T1"]},
        ])
        check("exit 0", rc == 0, out)
        check("added=2", j.get("added") == 2, out)
        check("gate_synced=True", j.get("gate_synced") is True, out)
        check("phase_count>0", j.get("phase_count", 0) > 0, out)

        # required_judges проставлены по маске: 04-build-* → содержит build-judge
        man = _manifest(project)
        build_step = next(s for s in man["steps"] if s["id"] == "04-build-T1")
        check("04-build-T1 имеет required_judges", bool(build_step.get("required_judges")), str(build_step))
        check("04-build-T1 включает build-judge",
              "build-judge" in build_step.get("required_judges", []), str(build_step))

        # gate.json реально записан на диск
        gate_files = list(project.glob("ground/**/gate.json"))
        check("gate.json создан на диске", len(gate_files) == 1, str(gate_files))

        # 2. Идемпотентность: повторное добавление тех же id → added=0, skipped=2
        rc, j2, out2 = run(project, [
            {"id": "04-test-T1", "title": "RED T1"},
            {"id": "04-build-T1", "title": "GREEN T1"},
        ])
        check("повтор → added=0", j2.get("added") == 0, out2)
        check("повтор → skipped=2", j2.get("skipped") == 2, out2)
        # шаг не задублировался
        man2 = _manifest(project)
        ids = [s["id"] for s in man2["steps"]]
        check("нет дублей id", len(ids) == len(set(ids)), str(ids))

    # 3. Нет манифеста → status=error
    with tempfile.TemporaryDirectory() as td:
        rc, j, out = run(Path(td), [{"id": "04-test-T1"}])
        check("нет манифеста → error", j.get("status") == "error", out)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
