#!/usr/bin/env python3
"""Tests for hooks/tdd-guard.py"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tdd-guard as mod


class TestBasic(unittest.TestCase):
    """Module imports correctly."""
    def test_function_main_exists(self):
        self.assertTrue(hasattr(mod, "main"))

class TestMain(unittest.TestCase):
    def test_help_exits(self):
        sys.argv = ["prog", "--help"]
        with self.assertRaises(SystemExit):
            mod.main()


if __name__ == "__main__":
    unittest.main()