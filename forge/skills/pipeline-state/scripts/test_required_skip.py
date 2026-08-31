#!/usr/bin/env python3
"""Tests for update.py — запрет тихого skip обязательного шага (Thrust 1: fallback=STOP).

Обязательный шаг (REQUIRED_STEP_PREFIXES: 02-sdd/02-design/04-*/05-*/06-spec/lite-*) нельзя
перевести в status=skipped без override — иначе fallback «не спросил → пропущу фазу» молча
выкидывает качество-гейты. Escape: overrides/step-skip-<step_id>.json.
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
SKILL = "feature-pipeline"
FEATURE = "feat"


def _make(tmp: Path, step_id: str, status: str = "in_progress") -> None:
    d = tmp / "ground" / "statements" / SKILL / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "feature": FEATURE,
        "steps": [{"id": step_id, "status": status, "required_judges": []}],
    }), encoding="utf-8")


def _step(tmp: Path) -> dict:
    p = tmp / "ground" / "statements" / SKILL / FEATURE / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))["steps"][0]


def _run(tmp: Path, step_id: str, status: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(UPDATE), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, "--step-id", step_id, "--status", status],
        capture_output=True, text=True)


def _write_skip_override(tmp: Path, step_id: str) -> None:
    ov = tmp / "ground" / "statements" / SKILL / FEATURE / "overrides"
    ov.mkdir(parents=True, exist_ok=True)
    (ov / f"step-skip-{step_id}.json").write_text(
        json.dumps({"reason": "тест: пропуск согласован"}), encoding="utf-8")


class TestRequiredSkip(unittest.TestCase):
    def test_required_step_skip_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            r = _run(tmp, "02-design", "skipped")
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("STOP", r.stderr)
            self.assertEqual(_step(tmp)["status"], "in_progress")  # транзишен не записан

    def test_required_step_skip_override_allows(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "lite-design")
            self.assertEqual(_run(tmp, "lite-design", "skipped").returncode, 3)
            _write_skip_override(tmp, "lite-design")
            r = _run(tmp, "lite-design", "skipped")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_step(tmp)["status"], "skipped")
            self.assertTrue(_step(tmp).get("override_warnings"))

    def test_non_required_step_skip_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "01-grounding")
            r = _run(tmp, "01-grounding", "skipped")   # grounding не обязателен (reuse-skip)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_step(tmp)["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
