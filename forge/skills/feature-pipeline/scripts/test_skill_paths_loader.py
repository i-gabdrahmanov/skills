#!/usr/bin/env python3
"""Тесты единого загрузчика путей skill_paths.py.

Гарантируют, что пути берутся ИЗ РЕЕСТРА (skill-paths.json), а не из захардкоженных
литералов, и что scripts больше не дублируют локатор реестра.

Регрессия: раньше run_judge.py делал skill_paths.get("check_taskplan", default) на
ВЕСЬ JSON, где ключ лежит вложенно (skills.tech-design.scripts.check_taskplan) — значение
реестра не читалось НИКОГДА, всегда побеждал хардкод-fallback.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "skills/feature-pipeline/scripts"
sys.path.insert(0, str(SCRIPTS))
import skill_paths  # noqa: E402


class LoaderBehaviour(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        skill_paths._CACHE.clear()

    def tearDown(self):
        skill_paths._CACHE.clear()
        self._tmp.cleanup()

    def _write_registry(self, data: dict):
        ref = self.root / ".gigacode/skills/feature-pipeline/references"
        ref.mkdir(parents=True, exist_ok=True)
        (ref / "skill-paths.json").write_text(json.dumps(data))
        skill_paths._CACHE.clear()

    def test_registry_value_wins_over_default(self):
        self._write_registry({"skills": {"tech-design": {"scripts": {
            "check_taskplan": "CUSTOM/tp.py"}}}})
        p = skill_paths.script(self.root, "tech-design", "check_taskplan")
        self.assertEqual(p, self.root / "CUSTOM/tp.py",
                         "значение из реестра не использовано (вернулась P0-бага bypass)")

    def test_missing_key_uses_default(self):
        self._write_registry({"skills": {}})
        p = skill_paths.script(self.root, "minor-defect-fix", "check_coverage")
        # либо project-база, либо ~/.gigacode-фоллбэк — суффикс одинаков
        self.assertTrue(str(p).endswith(
            ".gigacode/skills/minor-defect-fix/scripts/check_coverage.py"), p)

    def test_no_registry_uses_default(self):
        # реестра нет вовсе
        p = skill_paths.script(self.root, "tech-design", "check_sdd")
        self.assertTrue(str(p).endswith(
            ".gigacode/skills/tech-design/scripts/check_sdd.py"), p)

    def test_nested_resolve(self):
        self._write_registry({"docs": {"feature_pipeline_dir": "docs/feature-pipeline"}})
        p = skill_paths.resolve(self.root, "docs", "feature_pipeline_dir")
        self.assertEqual(p, self.root / "docs/feature-pipeline")


class NoDuplicateLocators(unittest.TestCase):
    def test_run_judge_uses_loader_not_inline(self):
        src = (SCRIPTS / "run_judge.py").read_text()
        self.assertIn("import skill_paths", src)
        # старый inline-локатор реестра не должен вернуться
        self.assertNotIn('"references" / "skill-paths.json"', src)
        self.assertNotIn('skill_paths.get(', src)

    def test_check_paths_delegates_locator(self):
        src = (SCRIPTS / "check_paths.py").read_text()
        self.assertIn("skill_paths.find_registry", src)


class RegistryEntriesExist(unittest.TestCase):
    """Ключи, которые реально резолвит run_judge, должны быть в реестре репозитория."""
    def test_required_script_keys_present(self):
        reg = json.loads((SCRIPTS / ".." / "references" / "skill-paths.json").read_text())
        scripts = reg["skills"]
        self.assertIn("check_taskplan", scripts["tech-design"]["scripts"])
        self.assertIn("check_sdd", scripts["tech-design"]["scripts"])
        self.assertIn("check_coverage", scripts["minor-defect-fix"]["scripts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
