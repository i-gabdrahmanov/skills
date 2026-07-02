#!/usr/bin/env python3
"""Tests for check_scope.py — детерминированный скоуп-чек lite."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_scope.py"

GOOD_DESC = ("Нужно добавить проверку статуса.\n"
             "Acceptance criteria:\n- при статусе CLOSED возвращать 409\n- иначе 200")


def _run(issue: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--issue-json", "-"],
                          input=json.dumps(issue), capture_output=True, text=True)


class TestCheckScope(unittest.TestCase):
    def test_good_subtask_passes(self):
        r = _run({"issuetype": "Sub-task", "summary": "Проверка статуса", "description": GOOD_DESC})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_epic_escalates(self):
        r = _run({"issuetype": "Epic", "summary": "X", "description": GOOD_DESC})
        self.assertEqual(r.returncode, 3)
        self.assertIn("ESCALATE", r.stderr)

    def test_story_escalates(self):
        self.assertEqual(_run({"issuetype": "Story", "summary": "X",
                               "description": GOOD_DESC}).returncode, 3)

    def test_empty_description_escalates(self):
        r = _run({"issuetype": "Task", "summary": "X", "description": ""})
        self.assertEqual(r.returncode, 3)
        self.assertIn("описание", r.stderr)

    def test_no_ac_escalates(self):
        r = _run({"issuetype": "Task", "summary": "X",
                  "description": "Просто сделать хорошо и чтобы работало как обсуждали на встрече."})
        self.assertEqual(r.returncode, 3)

    def test_refactor_keyword_escalates(self):
        r = _run({"issuetype": "Task", "summary": "Отрефакторить модуль платежей",
                  "description": GOOD_DESC})
        self.assertEqual(r.returncode, 3)

    def test_jira_rest_format(self):
        r = _run({"fields": {"issuetype": {"name": "Sub-task"}, "summary": "S",
                             "description": GOOD_DESC}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_broken_json_exit2(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--issue-json", "-"],
                           input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
