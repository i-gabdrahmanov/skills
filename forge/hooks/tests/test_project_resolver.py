#!/usr/bin/env python3
"""Тесты для _project.py — единого resolv'ера проекта."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _project import (
    gigacode_home,
    skills_dir,
    find_project_root,
    load_pipeline_config,
    verify_environment,
    resolve_skill_path,
    resolve_hook_path,
)


class TestProjectResolver(unittest.TestCase):
    """Тесты для _project.py — единого resolv'ера."""

    def test_gigacode_home_exists(self):
        """GIGACODE_HOME = ~/.gigacode должен существовать."""
        path = gigacode_home()
        self.assertTrue(path.exists(), f"{path} does not exist")
        self.assertEqual(path, Path.home() / ".gigacode")

    def test_skills_dir(self):
        """skills_dir = ~/.gigacode/skills/"""
        path = skills_dir()
        self.assertEqual(path, Path.home() / ".gigacode" / "skills")
        self.assertTrue(path.exists(), f"{path} does not exist")

    def test_find_project_root(self):
        """find_project_root находит корень проекта по .git."""
        root = find_project_root()
        self.assertTrue(root.exists())
        # Должен содержать .git или build.gradle
        has_git = (root / ".git").exists()
        has_build = (root / "build.gradle").exists()
        self.assertTrue(has_git or has_build, f"{root} не содержит .git или build.gradle")

    def test_find_project_root_fallback(self):
        """find_project_root возвращает cwd если ничего не нашёл."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = find_project_root(Path(tmpdir))
            self.assertEqual(root, Path(tmpdir))

    def test_load_pipeline_config(self):
        """load_pipeline_config всегда возвращает dict (не падает)."""
        cfg = load_pipeline_config()
        self.assertIsInstance(cfg, dict)

    def test_verify_environment(self):
        """verify_environment = True (runtime установлен)."""
        self.assertTrue(verify_environment())

    def test_resolve_skill_path(self):
        """resolve_skill_path строит путь к существующему скрипту."""
        path = resolve_skill_path("pipeline-state", "scripts", "update.py")
        self.assertTrue(path.exists(), f"{path} not found")
        self.assertEqual(path.suffix, ".py")
        self.assertIn("pipeline-state", str(path))

    def test_resolve_hook_path(self):
        """resolve_hook_path строит путь к существующему хуку."""
        path = resolve_hook_path("state-recorder")
        self.assertTrue(path.exists(), f"{path} not found")
        self.assertEqual(path.name, "state-recorder.py")


if __name__ == "__main__":
    unittest.main()