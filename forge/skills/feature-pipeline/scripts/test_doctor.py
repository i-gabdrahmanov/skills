#!/usr/bin/env python3
"""C7: doctor.py — self-check целостности. Зелёный на репо, красный при подломе."""
from __future__ import annotations

import importlib.util
import sys
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
