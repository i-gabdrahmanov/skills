#!/usr/bin/env python3
"""Тесты set_criticality.py — связь критичность → auto_max_risk детерминирована."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import set_criticality as sc


class TestDeriveRisk(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(sc.derive_risk("low"), "R2")
        self.assertEqual(sc.derive_risk("medium"), "R1")
        self.assertEqual(sc.derive_risk("high"), "R0")

    def test_case_insensitive_and_whitespace(self):
        self.assertEqual(sc.derive_risk("  High "), "R0")
        self.assertEqual(sc.derive_risk("MEDIUM"), "R1")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            sc.derive_risk("critical")
        with self.assertRaises(ValueError):
            sc.derive_risk("")

    def test_map_matches_three_levels(self):
        # Карта покрывает ровно low/medium/high (никаких лишних/недостающих уровней)
        self.assertEqual(set(sc.CRITICALITY_TO_RISK), {"low", "medium", "high"})


class TestApply(unittest.TestCase):
    def test_writes_both_fields(self):
        cfg = {"autonomy": {"criticality": None, "auto_max_risk": "R1", "level": "L2"}}
        out = sc.apply(cfg, "high")
        self.assertEqual(out["autonomy"]["criticality"], "high")
        self.assertEqual(out["autonomy"]["auto_max_risk"], "R0")
        # не затирает соседние поля autonomy
        self.assertEqual(out["autonomy"]["level"], "L2")

    def test_low_sets_r2(self):
        cfg = {"autonomy": {"criticality": None, "auto_max_risk": "R1"}}
        sc.apply(cfg, "low")
        self.assertEqual(cfg["autonomy"]["auto_max_risk"], "R2")

    def test_creates_autonomy_if_missing(self):
        cfg = {}
        sc.apply(cfg, "medium")
        self.assertEqual(cfg["autonomy"]["criticality"], "medium")
        self.assertEqual(cfg["autonomy"]["auto_max_risk"], "R1")


class TestMain(unittest.TestCase):
    def _run(self, criticality, root):
        argv = sys.argv
        sys.argv = ["set_criticality.py", "--criticality", criticality, "--project-root", str(root)]
        try:
            return sc.main()
        finally:
            sys.argv = argv

    def test_main_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ground").mkdir()
            pj = root / "ground" / "pipeline.json"
            pj.write_text(json.dumps({"autonomy": {"criticality": None, "auto_max_risk": "R1"}}))
            rc = self._run("high", root)
            self.assertEqual(rc, 0)
            cfg = json.loads(pj.read_text())
            self.assertEqual(cfg["autonomy"]["criticality"], "high")
            self.assertEqual(cfg["autonomy"]["auto_max_risk"], "R0")

    def test_main_missing_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run("low", Path(d)), 2)

    def test_main_bad_criticality(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ground").mkdir()
            (root / "ground" / "pipeline.json").write_text("{}")
            self.assertEqual(self._run("nope", root), 2)


if __name__ == "__main__":
    unittest.main()
