#!/usr/bin/env python3
"""Tests for hooks/evidence-enforcer.py"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Имя модуля содержит дефис — обычный import невозможен, грузим через importlib.
_spec = importlib.util.spec_from_file_location(
    "evidence_enforcer", Path(__file__).resolve().parent / "evidence-enforcer.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class TestBasic(unittest.TestCase):
    """Module imports correctly."""
    def test_function_main_exists(self):
        self.assertTrue(hasattr(mod, "main"))

class TestMain(unittest.TestCase):
    """Хук stdin-driven (не argparse): читает JSON со stdin, возвращает int."""

    def _run(self, payload) -> int:
        old = sys.stdin
        sys.stdin = io.StringIO("" if payload is None else json.dumps(payload))
        try:
            return mod.main()
        finally:
            sys.stdin = old

    def test_empty_stdin_passthrough(self):
        """Пустой stdin → fail-open пропуск (0)."""
        self.assertEqual(self._run(None), 0)

    def test_non_delivery_command_passthrough(self):
        """Команда не доставка (ls) → пропуск (0)."""
        self.assertEqual(self._run({"tool_input": {"command": "ls -la"}}), 0)


if __name__ == "__main__":
    unittest.main()