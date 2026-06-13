#!/usr/bin/env python3
"""Tests for hooks/risk_ladder.py"""
from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()