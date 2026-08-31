#!/usr/bin/env python3
"""Тесты jira_discover.discover_conventions — задачи «в едином ключе» (A)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_discover as j


class TestConventions(unittest.TestCase):
    ISSUES = [
        {"summary": "[KID] Автозакрытие", "components": [{"name": "task-service"}],
         "labels": ["kid", "backend"], "epic": "KIDPPRB-100"},
        {"summary": "[KID] Рассылка", "components": ["task-service", "notify"],
         "labels": ["kid"], "epic": "KIDPPRB-100"},
        {"summary": "Прочее", "components": [], "labels": [], "epic": "KIDPPRB-200"},
    ]

    def test_components_ranked(self):
        c = j.discover_conventions(self.ISSUES)
        self.assertEqual(c["common_components"][0], "task-service")  # самый частый
        self.assertIn("notify", c["common_components"])

    def test_labels_and_epic(self):
        c = j.discover_conventions(self.ISSUES)
        self.assertEqual(c["common_labels"][0], "kid")
        self.assertEqual(c["frequent_epic"], "KIDPPRB-100")  # 2× против 1×

    def test_summary_prefix(self):
        self.assertEqual(j.discover_conventions(self.ISSUES)["summary_prefix"], "[KID]")

    def test_empty(self):
        c = j.discover_conventions([])
        self.assertEqual(c["common_components"], [])
        self.assertIsNone(c["frequent_epic"])
        self.assertEqual(c["sampled_issues"], 0)

    def test_handles_dict_and_str_components(self):
        c = j.discover_conventions([
            {"summary": "x", "components": [{"name": "A"}, "B"], "labels": [], "epic": None}])
        self.assertCountEqual(c["common_components"], ["A", "B"])

    def test_build_config_includes_conventions(self):
        cfg = j.build_jira_config({"project_key": "KIDPPRB", "issues": self.ISSUES})
        self.assertIn("conventions", cfg)
        self.assertEqual(cfg["conventions"]["frequent_epic"], "KIDPPRB-100")


if __name__ == "__main__":
    unittest.main()
