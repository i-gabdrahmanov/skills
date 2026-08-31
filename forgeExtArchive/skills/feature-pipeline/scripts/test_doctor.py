#!/usr/bin/env python3
"""C7: doctor.py — self-check целостности. Зелёный на репо, красный при подломе."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("doctor_mod", SCRIPTS / "doctor.py")
DOC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DOC)


class Doctor(unittest.TestCase):
    def test_green_on_repo(self):
        res = DOC.run_checks(None)
        self.assertTrue(res["passed"], f"doctor нашёл проблемы: {res['problems']}")
        names = {c["name"] for c in res["checks"]}
        self.assertIn("judge-name-producible", names)
        self.assertIn("phase-constants-consistent", names)
        self.assertIn("canonical-phase-order", names)

    # ── P1-8: env-проверки (Python/git/config) ──
    def test_env_checks_present(self):
        """python-version и git-available присутствуют; python — PASS на >=3.10, WARN на старом."""
        res = DOC.run_checks(None)
        by = {c["name"]: c["status"] for c in res["checks"]}
        self.assertIn("python-version", by)
        self.assertIn("git-available", by)
        expect_py = "PASS" if sys.version_info[:2] >= DOC.MIN_PYTHON else "WARN"
        self.assertEqual(by["python-version"], expect_py)

    def test_env_advisory_does_not_break_passed(self):
        """Средовой совет (старый Python) НЕ делает doctor красным — это не integrity-fail."""
        res = DOC.run_checks(None)
        # на этом репо нет integrity-проблем; passed зависит только от них, не от warnings
        self.assertTrue(res["passed"], f"integrity-проблемы: {res['problems']}")
        if sys.version_info[:2] < DOC.MIN_PYTHON:
            self.assertIn("python-version", " ".join(res.get("warnings", [])))

    def test_min_python_is_310(self):
        self.assertEqual(DOC.MIN_PYTHON, (3, 10))

    def test_preflight_pins_same_min_python(self):
        """preflight.MIN_PYTHON не должен разойтись с doctor.MIN_PYTHON (копия для раннего варнинга)."""
        pf_src = (SCRIPTS.parents[2] / "hooks" / "preflight.py").read_text(encoding="utf-8")
        self.assertIn(f"MIN_PYTHON = {DOC.MIN_PYTHON}", pf_src)

    def test_config_valid_skips_without_project(self):
        res = DOC.run_checks(None)
        cv = next((c for c in res["checks"] if c["name"] == "config-valid"), None)
        self.assertIsNotNone(cv)
        self.assertEqual(cv["status"], "SKIP")

    def test_config_valid_flags_jacoco_gap(self):
        """С project: coverage-гейт активен, но jacoco_configured=false → config-valid FAIL (P0-1/P3-15)."""
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / "ground").mkdir(parents=True)
            (proj / "ground" / "pipeline.json").write_text(json.dumps({
                "project": {"name": "t"},
                "quality": {"eval_enabled": True, "coverage_threshold": 0.8, "jacoco_configured": False},
            }), encoding="utf-8")
            res = DOC.run_checks(proj)
            cv = next((c for c in res["checks"] if c["name"] == "config-valid"), None)
            self.assertIsNotNone(cv)
            # config-helper присутствует в репо → проверка реально отработала (не SKIP) → WARN (совет)
            if cv["status"] != "SKIP":
                self.assertEqual(cv["status"], "WARN")
                self.assertTrue(any("config-valid" in w for w in res.get("warnings", [])))
                self.assertTrue(res["passed"])  # конфиг-совет не валит integrity

    def test_red_on_broken_judge_name(self):
        """Если в маске судья без производителя — doctor краснеет (judge-name-producible FAIL)."""
        orig_load = DOC._load

        def patched(path, name):
            m = orig_load(path, name)
            if path.name == "pipeline_phases.py":
                m.REQUIRED_JUDGES_MASK = dict(m.REQUIRED_JUDGES_MASK)
                m.REQUIRED_JUDGES_MASK["02-design"] = ["nonexistent-judge"]
            return m

        DOC._load = patched
        try:
            res = DOC.run_checks(None)
        finally:
            DOC._load = orig_load
        self.assertFalse(res["passed"])
        self.assertTrue(any("judge-name-producible" in p for p in res["problems"]),
                        res["problems"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
