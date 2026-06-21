#!/usr/bin/env python3
"""Tests for hooks/risk_ladder.py"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk_ladder as mod


class TestBasic(unittest.TestCase):
    """Module imports correctly."""
    def test_function_level_order_exists(self):
        self.assertTrue(hasattr(mod, "level_order"))
    def test_function_load_policy_exists(self):
        self.assertTrue(hasattr(mod, "load_policy"))
    def test_function_pipeline_cfg_exists(self):
        self.assertTrue(hasattr(mod, "pipeline_cfg"))
    def test_function_auto_max_risk_exists(self):
        self.assertTrue(hasattr(mod, "auto_max_risk"))
    def test_function_criticality_set_exists(self):
        self.assertTrue(hasattr(mod, "criticality_set"))


class TestPolicyLoaded(unittest.TestCase):
    """H2: load_policy различает loaded / missing / corrupt — fail-closed на пропаже политики."""
    def setUp(self):
        self._orig = mod._POLICY_PATH

    def tearDown(self):
        mod._POLICY_PATH = self._orig

    def test_loaded_real_policy(self):
        # Реальная co-located risk-policy.json парсится → loaded.
        self.assertTrue(mod.policy_loaded())

    def test_missing_policy(self):
        mod._POLICY_PATH = Path("/nonexistent/dir/risk-policy.json")
        self.assertFalse(mod.policy_loaded())
        self.assertEqual(mod.load_policy(), {})

    def test_corrupt_policy(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ broken json")
            bad = Path(f.name)
        try:
            mod._POLICY_PATH = bad
            self.assertFalse(mod.policy_loaded())
            self.assertEqual(mod.load_policy(), {})
        finally:
            bad.unlink()


if __name__ == "__main__":
    unittest.main()