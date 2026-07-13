#!/usr/bin/env python3
"""Tests for hooks/evidence-enforcer.py"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
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

    def test_git_C_push_detected_as_delivery(self):
        """`git -C . push` — доставка: обходила детект (git\\s+push), без evidence-гейта.
        Вне пайплайна (нет task-plan/lite-манифеста) → block (fail-closed доставки)."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run({
                "cwd": d, "tool_input": {"command": "git -C . push origin main"},
            }), 2)

    def test_sdd_review_script_not_delivery(self):
        """sdd_review_push.py — санкционированный канал SDD-ревью: не матчится _DELIVER,
        его гейтит gate-guard approval-маркером (deny-first), не evidence-гейт."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run({
                "cwd": d, "tool_input": {"command":
                    "python3 .gigacode/skills/feature-pipeline/scripts/sdd_review_push.py "
                    "--feature STOR-7 --json"},
            }), 0)


class TestCommitMsgFloor(unittest.TestCase):
    """Пол сообщения HEAD-коммита перед push: запрет Co-Authored-By (оба потока) и
    обязательный ключ Jira для forgelite (feature манифеста = ключ)."""

    def _repo(self, msg: str) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        p = Path(self._tmp.name)
        (p / "f.txt").write_text("x", encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-qm", msg]):
            subprocess.run(cmd, cwd=str(p), capture_output=True, timeout=30)
        return p

    def tearDown(self):
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def _lite_manifest(self, p: Path, key: str):
        d = p / "ground" / "statements" / "forgelite" / key
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"steps": []}), encoding="utf-8")

    def test_co_authored_by_blocked(self):
        p = self._repo("KID-1: fix\n\nCo-Authored-By: Bot <b@b>")
        deny = mod._commit_msg_floor(p)
        self.assertIsNotNone(deny)
        self.assertIn("Co-Authored-By", deny)

    def test_clean_message_ok(self):
        p = self._repo("KID-1: fix NPE in mapper")
        self.assertIsNone(mod._commit_msg_floor(p))

    def test_lite_without_jira_key_blocked(self):
        p = self._repo("fix mapper")
        self._lite_manifest(p, "KID-7")
        deny = mod._commit_msg_floor(p)
        self.assertIsNotNone(deny)
        self.assertIn("KID-7", deny)

    def test_lite_with_jira_key_ok(self):
        p = self._repo("KID-7: fix mapper")
        self._lite_manifest(p, "KID-7")
        self.assertIsNone(mod._commit_msg_floor(p))

    def test_no_git_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(mod._commit_msg_floor(Path(d)))


if __name__ == "__main__":
    unittest.main()