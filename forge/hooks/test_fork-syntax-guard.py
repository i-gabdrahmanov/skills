#!/usr/bin/env python3
"""Tests for fork-syntax-guard.py — инструктивный блок синтаксиса, который режет форк."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "fork-syntax-guard.py"


def _run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_input": {"command": cmd}}),
        capture_output=True, text=True,
    )


class TestForkSyntaxGuard(unittest.TestCase):
    def test_command_substitution_blocked(self):
        r = _run("echo $(date)")
        self.assertEqual(r.returncode, 2)
        self.assertIn("fork-syntax-guard", r.stderr)
        self.assertIn("$(", r.stderr)

    def test_backticks_blocked(self):
        self.assertEqual(_run("echo `pwd`").returncode, 2)

    def test_find_exec_blocked(self):
        r = _run("find src -name '*.java' -exec cat {} \\;")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Glob", r.stderr)

    def test_ls_recursive_blocked(self):
        self.assertEqual(_run("ls -laR src").returncode, 2)

    def test_plain_commands_pass(self):
        for cmd in ["./gradlew build", "git log -30 --pretty=format:%s",
                    "python3 script.py --project .", "ls -la src", "find src -name '*.java'"]:
            self.assertEqual(_run(cmd).returncode, 0, cmd)

    def test_garbage_stdin_fail_open(self):
        r = subprocess.run([sys.executable, str(GUARD)], input="not json",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
