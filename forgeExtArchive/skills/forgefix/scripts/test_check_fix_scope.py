#!/usr/bin/env python3
"""Tests for check_fix_scope.py — детерминированный скоуп-чек fix-пути."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_fix_scope.py"

BUG_DESC = ("При сохранении заявки с пустым email endpoint падает с NPE.\n"
            "Шаги воспроизведения:\n- POST /api/claims без email\n- ожидали 400, получили 500")


def _run(issue: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--issue-json", "-"],
                          input=json.dumps(issue), capture_output=True, text=True)


class TestCheckFixScope(unittest.TestCase):
    def test_bug_passes(self):
        r = _run({"issuetype": "Bug", "summary": "NPE при пустом email", "description": BUG_DESC})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bug_markers_without_bug_type_pass(self):
        """Тип не Bug, но текст явно про поломку — fix-путь применим."""
        r = _run({"issuetype": "Sub-task", "summary": "Падает сохранение", "description": BUG_DESC})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_story_escalates(self):
        r = _run({"issuetype": "Story", "summary": "X", "description": BUG_DESC})
        self.assertEqual(r.returncode, 3)
        self.assertIn("не дефект", r.stderr)

    def test_feature_request_escalates(self):
        """Главный кейс, ради которого заведён гейт: фича не должна уехать в fix-путь."""
        r = _run({"issuetype": "Task", "summary": "Экспорт в CSV",
                  "description": "Нужно добавить возможность выгружать список заявок в CSV "
                                 "с фильтрами по дате и статусу."})
        self.assertEqual(r.returncode, 3)
        self.assertIn("ESCALATE", r.stderr)

    def test_blocker_priority_escalates(self):
        r = _run({"issuetype": "Bug", "priority": "Blocker", "summary": "S", "description": BUG_DESC})
        self.assertEqual(r.returncode, 3)
        self.assertIn("приоритет", r.stderr)

    def test_refactor_keyword_escalates(self):
        r = _run({"issuetype": "Bug", "summary": "Отрефакторить модуль платежей",
                  "description": BUG_DESC})
        self.assertEqual(r.returncode, 3)

    def test_empty_description_escalates(self):
        r = _run({"issuetype": "Bug", "summary": "Всё сломалось", "description": ""})
        self.assertEqual(r.returncode, 3)
        self.assertIn("описание", r.stderr)

    def test_many_list_items_escalates(self):
        desc = BUG_DESC + "\n" + "\n".join(f"- пункт {i}" for i in range(10))
        r = _run({"issuetype": "Bug", "summary": "S", "description": desc})
        self.assertEqual(r.returncode, 3)

    def test_jira_rest_format(self):
        r = _run({"fields": {"issuetype": {"name": "Bug"}, "priority": {"name": "Minor"},
                             "summary": "S", "description": BUG_DESC}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_text_file_mode(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bug.txt"
            p.write_text(BUG_DESC, encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPT), "--text-file", str(p)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_broken_json_exit2(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--issue-json", "-"],
                           input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
