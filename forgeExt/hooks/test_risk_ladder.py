#!/usr/bin/env python3
"""Tests for hooks/risk_ladder.py"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk_ladder as mod


class TestBasic(unittest.TestCase):
    """Module imports correctly."""
    def test_function_level_order_exists(self):
        self.assertTrue(hasattr(mod, "level_order"))
    def test_function_load_policy_exists(self):
        self.assertTrue(hasattr(mod, "load_policy"))
    def test_function_pipeline_cfg_exists(self):
        self.assertTrue(hasattr(mod, "pipeline_cfg"))
    def test_function_auto_max_risk_exists(self):
        self.assertTrue(hasattr(mod, "auto_max_risk"))
    def test_function_criticality_set_exists(self):
        self.assertTrue(hasattr(mod, "criticality_set"))


class TestNormalizeGit(unittest.TestCase):
    """Сворачивание глобальных опций git перед подкомандой — закрывает обход
    `git -C <p> push`/`git -c k=v commit` детекторов delivery/SoD/force-push/классификатора."""

    def test_strip_dash_C(self):
        self.assertEqual(mod.normalize_git_command("git -C . push origin main"),
                         "git push origin main")

    def test_strip_dash_c_config(self):
        self.assertEqual(mod.normalize_git_command("git -c user.name=x commit -m y"),
                         "git commit -m y")

    def test_strip_multiple_globals(self):
        self.assertEqual(mod.normalize_git_command("git -C /r -c a=b push -f"),
                         "git push -f")

    def test_strip_git_dir_eq(self):
        self.assertEqual(mod.normalize_git_command("git --git-dir=/x/.git push"),
                         "git push")

    def test_plain_push_unchanged(self):
        self.assertEqual(mod.normalize_git_command("git push origin x"), "git push origin x")

    def test_commit_reuse_C_preserved(self):
        # -C у самой подкоммандой commit (reuse message) НЕ трогаем — сворачиваем лишь ведущий кластер
        self.assertEqual(mod.normalize_git_command("git commit -C HEAD"), "git commit -C HEAD")

    def test_classify_git_push_not_escalated(self):
        # Доставка — на пользователе: git push/commit больше не классифицируются рисковыми
        info = mod.classify("run_shell_command", {"command": "git -C . push origin main"}, ".")
        self.assertEqual(info["level"], "R1")

    def test_classify_git_commit_not_escalated(self):
        info = mod.classify("run_shell_command", {"command": "git -c a=b commit -m x"}, ".")
        self.assertEqual(info["level"], "R1")


class TestPolicyLoaded(unittest.TestCase):
    """H2: load_policy различает loaded / missing / corrupt — fail-closed на пропаже политики."""
    def setUp(self):
        self._orig = mod._POLICY_PATH

    def tearDown(self):
        mod._POLICY_PATH = self._orig

    def test_loaded_real_policy(self):
        # Реальная co-located risk-policy.json парсится → loaded.
        self.assertTrue(mod.policy_loaded())

    def test_missing_policy(self):
        mod._POLICY_PATH = Path("/nonexistent/dir/risk-policy.json")
        self.assertFalse(mod.policy_loaded())
        self.assertEqual(mod.load_policy(), {})

    def test_corrupt_policy(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ broken json")
            bad = Path(f.name)
        try:
            mod._POLICY_PATH = bad
            self.assertFalse(mod.policy_loaded())
            self.assertEqual(mod.load_policy(), {})
        finally:
            bad.unlink()


if __name__ == "__main__":
    unittest.main()