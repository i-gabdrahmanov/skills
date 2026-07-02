#!/usr/bin/env python3
"""Tests for record_gate.py + update.py._check_gate_result.

Build/verify-шаги (04-test/04-build/05-tests, lite-red/green/verify) закрываются completed
только при gates/<step_id>.json с produced_by:"record_gate" и passed:true — самоотчёт
субагента («status: completed») не доказательство. Артефакт пишет record_gate.py по
фактическому exit-коду гейта. Escape-hatch: overrides/gate-result-<step_id>.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATE = HERE / "update.py"
RECORD = HERE / "record_gate.py"

SKILL = "forgelite"
FEATURE = "KID-1"


def _make_manifest(tmp: Path) -> None:
    d = tmp / "ground" / "statements" / SKILL / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "feature": FEATURE,
        "steps": [
            {"id": "lite-green", "status": "in_progress", "required_judges": []},
            {"id": "lite-red", "status": "in_progress", "required_judges": []},
            {"id": "lite-plan", "status": "in_progress", "required_judges": []},
        ],
    }), encoding="utf-8")


def _write_origin(tmp: Path, step_id: str) -> None:
    d = tmp / "ground" / "statements" / SKILL / FEATURE / "_origins"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{step_id}.json").write_text(json.dumps({"step_id": step_id}), encoding="utf-8")


def _close(tmp: Path, step_id: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(UPDATE), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, "--step-id", step_id, "--status", "completed",
         "--closed-by", "subagent"],
        capture_output=True, text=True,
    )


def _record(tmp: Path, step_id: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RECORD), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, "--step-id", step_id, *extra],
        capture_output=True, text=True,
    )


def _gate_file(tmp: Path, step_id: str) -> Path:
    return tmp / "ground" / "statements" / SKILL / FEATURE / "gates" / f"{step_id}.json"


class TestRecordGate(unittest.TestCase):
    def test_success_gate_passed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-green", "--cmd", "true")
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = json.loads(_gate_file(tmp, "lite-green").read_text())
            self.assertTrue(rec["passed"])
            self.assertEqual(rec["produced_by"], "record_gate")

    def test_success_gate_failed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-green", "--cmd", "false")
            self.assertEqual(r.returncode, 1)
            self.assertFalse(json.loads(_gate_file(tmp, "lite-green").read_text())["passed"])

    def test_red_gate_compile_ok_tests_fail_passes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", "false")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(_gate_file(tmp, "lite-red").read_text())["passed"])

    def test_red_gate_green_tests_fail(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", "true")
            self.assertEqual(r.returncode, 1)

    def test_red_gate_compile_broken_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "false", "--cmd", "false")
            self.assertEqual(r.returncode, 1)


class TestGateResultCheck(unittest.TestCase):
    def test_close_without_artifact_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            r = _close(tmp, "lite-green")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("record_gate", r.stderr)

    def test_close_with_passed_artifact_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            self.assertEqual(_record(tmp, "lite-green", "--cmd", "true").returncode, 0)
            r = _close(tmp, "lite-green")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_close_with_failed_artifact_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            _record(tmp, "lite-green", "--cmd", "false")
            self.assertNotEqual(_close(tmp, "lite-green").returncode, 0)

    def test_handwritten_artifact_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            gf = _gate_file(tmp, "lite-green")
            gf.parent.mkdir(parents=True, exist_ok=True)
            gf.write_text(json.dumps({"passed": True}), encoding="utf-8")  # без провенанса
            self.assertNotEqual(_close(tmp, "lite-green").returncode, 0)

    def test_override_allows_close(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            ov = tmp / "ground" / "statements" / SKILL / FEATURE / "overrides"
            ov.mkdir(parents=True, exist_ok=True)
            (ov / "gate-result-lite-green.json").write_text(
                json.dumps({"reason": "тест: гейт неприменим"}), encoding="utf-8")
            r = _close(tmp, "lite-green")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_gate_step_not_affected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            r = _close(tmp, "lite-plan")
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
