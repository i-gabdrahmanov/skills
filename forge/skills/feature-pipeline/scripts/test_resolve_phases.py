#!/usr/bin/env python3
"""Smoke-тесты resolve_phases.py — CORE-резолвер активных фаз (динамический feature-gating).
Раньше прямого теста не было (id/порядок косвенно пинились test_phase_consistency). Здесь
фиксируем рантайм-поведение: enabled_by отключает фазу (jira/tdd/eval), полный конфиг даёт
все фазы, отсутствие pipeline.json → exit 1.

Запуск: python3 test_resolve_phases.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "resolve_phases.py"
PASSED = 0
FAILED = 0


def _project(td: str, pipeline: dict | None) -> Path:
    project = Path(td)
    (project / "ground").mkdir(parents=True, exist_ok=True)
    if pipeline is not None:
        (project / "ground" / "pipeline.json").write_text(json.dumps(pipeline), encoding="utf-8")
    return project


def run(project: Path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--project", str(project)],
                       capture_output=True, text=True)
    try:
        parsed = json.loads(r.stdout.strip())
    except json.JSONDecodeError:
        parsed = {}
    return r.returncode, parsed, (r.stdout + r.stderr).strip()


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def _ids(j: dict) -> set[str]:
    return {p["id"] for p in j.get("phases", [])}


def _skipped(j: dict) -> set[str]:
    return {s["id"] for s in j.get("skipped", [])}


FULL = {
    "jira": {"enabled": True},
    "quality": {"tdd": True, "eval_enabled": True},
}


def main() -> int:
    # 1. Полный конфиг → все ключевые фазы активны
    with tempfile.TemporaryDirectory() as td:
        rc, j, out = run(_project(td, FULL))
        ids = _ids(j)
        check("exit 0 на полном конфиге", rc == 0, out)
        check("00-brd активна", "00-brd" in ids, str(ids))
        check("03-jira активна (jira.enabled)", "03-jira" in ids, str(ids))
        check("04-tdd активна (quality.tdd)", "04-tdd" in ids, str(ids))
        check("02-eval-plan активна (eval_enabled)", "02-eval-plan" in ids, str(ids))

    # 2. jira.enabled=false → 03-jira в skipped
    with tempfile.TemporaryDirectory() as td:
        cfg = {"jira": {"enabled": False}, "quality": {"tdd": True, "eval_enabled": True}}
        rc, j, out = run(_project(td, cfg))
        check("jira off → 03-jira skipped", "03-jira" in _skipped(j) and "03-jira" not in _ids(j), out)

    # 3. quality.tdd=false → 04-tdd в skipped
    with tempfile.TemporaryDirectory() as td:
        cfg = {"jira": {"enabled": True}, "quality": {"tdd": False, "eval_enabled": True}}
        rc, j, out = run(_project(td, cfg))
        check("tdd off → 04-tdd skipped", "04-tdd" in _skipped(j), out)

    # 4. eval_enabled=false → 02-eval-plan в skipped
    with tempfile.TemporaryDirectory() as td:
        cfg = {"jira": {"enabled": True}, "quality": {"tdd": True, "eval_enabled": False}}
        rc, j, out = run(_project(td, cfg))
        check("eval off → 02-eval-plan skipped", "02-eval-plan" in _skipped(j), out)

    # 5. Нет pipeline.json → exit 1
    with tempfile.TemporaryDirectory() as td:
        rc, j, out = run(_project(td, None))
        check("нет pipeline.json → exit 1", rc == 1, f"rc={rc} {out}")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
