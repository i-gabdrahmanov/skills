#!/usr/bin/env python3
"""Гейт «Jira-ключ обязателен» в init.py.

Инвариант: для скиллов из SKILLS_REQUIRING_JIRA_KEY (feature-pipeline) папка стейта
создаётся ТОЛЬКО с валидным Jira-ключом в --feature. Дефолт 'pipeline' и свободные
kebab-слаги отвергаются (exit ≠ 0, папка не создана), оверрайда нет. Прочие скиллы не задеты.
Ловит баг: MCP не подключён → оркестратор ушёл в «без Jira» → безслаговая ground/.../pipeline/.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _util  # noqa: E402

INIT = HERE / "init.py"
STEPS = json.dumps([{"id": "01-grounding", "title": "g"}])


def _run_init(tmp: Path, skill: str, feature: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INIT), "--project", str(tmp), "--skill", skill,
         "--feature", feature, "--steps", STEPS],
        capture_output=True, text=True,
    )


def _feature_dir(tmp: Path, skill: str, feature: str) -> Path:
    return tmp / "ground" / "statements" / skill / feature


class IsJiraKeyUnit(unittest.TestCase):
    def test_accepts_real_keys(self):
        for k in ("STOR-123", "KID-1", "KIDPPRB-8639", "AB1-42"):
            self.assertTrue(_util.is_jira_key(k), k)

    def test_rejects_non_keys(self):
        for k in ("pipeline", "user-notifications", "feat", "a-1", "STOR-", "-1", "STOR123", "", None):
            self.assertFalse(_util.is_jira_key(k), repr(k))


class InitJiraKeyGate(unittest.TestCase):
    def test_default_pipeline_slug_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            r = _run_init(tmp, "feature-pipeline", "pipeline")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("Jira-ключ", r.stderr)
            self.assertFalse(_feature_dir(tmp, "feature-pipeline", "pipeline").exists())

    def test_kebab_idea_slug_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            r = _run_init(tmp, "feature-pipeline", "user-notifications")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertFalse(_feature_dir(tmp, "feature-pipeline", "user-notifications").exists())

    def test_valid_key_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            r = _run_init(tmp, "feature-pipeline", "STOR-123")
            self.assertEqual(r.returncode, 0, r.stderr)
            manifest = _feature_dir(tmp, "feature-pipeline", "STOR-123") / "manifest.json"
            self.assertTrue(manifest.exists())
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["feature"], "STOR-123")

    def test_other_skill_unaffected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            r = _run_init(tmp, "system-analyst", "pipeline")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((_feature_dir(tmp, "system-analyst", "pipeline") / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
