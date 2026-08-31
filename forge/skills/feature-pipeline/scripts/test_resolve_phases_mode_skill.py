#!/usr/bin/env python3
"""Smoke-тесты resolve_phases.py — валидация manifest.inputs.mode ↔ manifest.skill.

Покрывает _validate_mode_skill и её интеграцию в resolve_phases() и current_phase():
- mode == skill (forgefix / forgelite / feature-pipeline) → OK, exit 0
- mode != skill → exit 3 + диагностическое сообщение (mode/mismatch/skill)
- inputs.mode отсутствует → валидация пропускается (back-compat со старыми манифестами)

Запуск: python3 test_resolve_phases_mode_skill.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "resolve_phases.py"
PASSED = 0
FAILED = 0


FULL_POLICY = {
    "jira": {"enabled": True},
    "quality": {"tdd": True, "eval_enabled": True},
}


def _make_project(tmpdir, policy_dict, manifest_skill, manifest_feature, manifest_dict):
    """Фикстура: ground/policy.json + ground/statements/<skill>/<feature>/manifest.json.
    Возвращает project root (Path)."""
    project = Path(tmpdir)
    (project / "ground").mkdir(parents=True, exist_ok=True)
    if policy_dict is not None:
        (project / "ground" / "policy.json").write_text(
            json.dumps(policy_dict), encoding="utf-8"
        )
    manifest_dir = project / "ground" / "statements" / manifest_skill / manifest_feature
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(manifest_dict), encoding="utf-8"
    )
    return project


def _run(project, *extra_args):
    """Запуск resolve_phases.py с произвольными доп. аргументами CLI."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(project), *extra_args],
        capture_output=True,
        text=True,
    )


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


def test_match_forgefix():
    """manifest.skill='forgefix' + inputs.mode='forgefix' → валидация OK, exit 0."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {"mode": "forgefix"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        check("test_match_forgefix: exit 0",
              r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_match_forgelite():
    """manifest.skill='forgelite' + inputs.mode='forgelite' → exit 0."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgelite",
            "inputs": {"mode": "forgelite"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgelite", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        check("test_match_forgelite: exit 0",
              r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_match_feature_pipeline():
    """manifest.skill='feature-pipeline' + inputs.mode='feature-pipeline' → exit 0."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "feature-pipeline",
            "inputs": {"mode": "feature-pipeline"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "feature-pipeline", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        check("test_match_feature_pipeline: exit 0",
              r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_mismatch_exits_3():
    """manifest.skill='forgefix' при inputs.mode='forgelite' → exit 3, диагностика mode/mismatch/skill."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {"mode": "forgelite"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        combined = (r.stdout + r.stderr).lower()
        check("test_mismatch_exits_3: rc == 3",
              r.returncode == 3,
              f"rc={r.returncode} stderr={r.stderr!r}")
        check("test_mismatch_exits_3: stderr содержит mode/mismatch/skill",
              any(kw in combined for kw in ("mode", "mismatch", "skill")),
              f"combined={combined!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_mode_absent_skips_validation():
    """Манифест без inputs.mode → _validate_mode_skill возвращает None, exit 0 (back-compat)."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {},  # mode отсутствует → валидация пропускается
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        check("test_mode_absent_skips_validation: exit 0 (валидация пропущена)",
              r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_current_phase_mismatch_exits_3():
    """current_phase() тоже вызывает _validate_mode_skill — mismatch через --current → exit 3."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {"mode": "forgelite"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x", "--current")
        check("test_current_phase_mismatch_exits_3: rc == 3",
              r.returncode == 3,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    test_match_forgefix()
    test_match_forgelite()
    test_match_feature_pipeline()
    test_mismatch_exits_3()
    test_mode_absent_skips_validation()
    test_current_phase_mismatch_exits_3()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
