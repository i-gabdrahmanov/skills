#!/usr/bin/env python3
"""Tests for hooks/risk_ladder.py"""
from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
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


class TestDryRefactor(unittest.TestCase):
    """DRY: pipeline_cfg остаётся legacy-only (только pipeline.json, policy.json игнорирует);
    config_get использует _config_loader.load_project_config (dual-read: policy → pipeline → {})."""

    def test_pipeline_cfg_legacy_only_returns_empty_on_v2_only(self):
        """pipeline_cfg должен возвращать только legacy pipeline.json (не policy.json).
        На v2-only проекте (нет pipeline.json) → {}."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 7}}),
                encoding="utf-8",
            )
            cfg = mod.pipeline_cfg(root)
            self.assertEqual(cfg, {}, f"pipeline_cfg should ignore policy.json, got {cfg}")

    def test_pipeline_cfg_legacy_only_reads_pipeline_json(self):
        """pipeline_cfg читает именно pipeline.json (legacy semantics preserved)."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "pipeline.json").write_text(
                _json.dumps({"autonomy": {"auto_max_risk": "R3"}}),
                encoding="utf-8",
            )
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 7}}),
                encoding="utf-8",
            )
            cfg = mod.pipeline_cfg(root)
            self.assertEqual(cfg.get("autonomy", {}).get("auto_max_risk"), "R3")
            self.assertNotIn("quality", cfg, "pipeline_cfg must not include policy.json content")

    def test_config_get_dual_read_picks_up_v2_policy(self):
        """config_get должен читать quality.* из policy.json на v2-only проекте."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 9}}),
                encoding="utf-8",
            )
            val = mod.config_get(root, "quality.max_step_reopens")
            self.assertEqual(val, 9, f"expected 9, got {val}")

    def test_config_get_dual_read_falls_back_to_legacy_pipeline(self):
        """config_get на v1-only проекте (только pipeline.json) должен вернуть значение через legacy fallback."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "pipeline.json").write_text(
                _json.dumps({"sources": {"spec": "PROJ-legacy"}}),
                encoding="utf-8",
            )
            val = mod.config_get(root, "sources.spec")
            self.assertEqual(val, "PROJ-legacy", f"expected legacy fallback, got {val}")

    def test_pipeline_cfg_no_warning_on_v2_only(self):
        """pipeline_cfg НЕ должен спамить DeprecationWarning на v2-only проектах
        (где pipeline.json не существует). Warning — только при ФАКТИЧЕСКОМ чтении legacy."""
        import json as _json
        import warnings as _warnings
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 7}}),
                encoding="utf-8",
            )
            with _warnings.catch_warnings(record=True) as caught:
                _warnings.simplefilter("always")  # не давить дедупликацию
                cfg = mod.pipeline_cfg(root)
            deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            self.assertEqual(cfg, {}, f"v2-only should return empty, got {cfg}")
            self.assertEqual(
                len(deprecation_warnings), 0,
                f"unexpected DeprecationWarning on v2-only project: {[str(w.message) for w in deprecation_warnings]}",
            )

    def test_pipeline_cfg_warning_on_legacy(self):
        """pipeline_cfg ДОЛЖЕН спамить DeprecationWarning когда legacy pipeline.json читается."""
        import json as _json
        import warnings as _warnings
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "pipeline.json").write_text(
                _json.dumps({"b": 2}),
                encoding="utf-8",
            )
            with _warnings.catch_warnings(record=True) as caught:
                _warnings.simplefilter("always")
                cfg = mod.pipeline_cfg(root)
            deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            self.assertEqual(cfg, {"b": 2})
            self.assertGreaterEqual(
                len(deprecation_warnings), 1,
                "expected DeprecationWarning on legacy read",
            )
            self.assertTrue(
                any("pipeline.json is deprecated" in str(w.message) for w in deprecation_warnings),
                f"unexpected warning text: {[str(w.message) for w in deprecation_warnings]}",
            )


class TestReadOnlyCommand(unittest.TestCase):
    """Задача 011: закрытый allowlist читателей. Любая неуверенность = не read-only."""

    READ_ONLY = [
        "cat src/main/resources/application.yml",
        "grep -rn foo src/main/java/com/x/auth/S.java",
        "ls -la", "git diff --stat", "git log --oneline -5",
        "cat x.yml 2>/dev/null", "sed -n '1,5p' file", "find . -name '*.java'",
        "head -5 a.txt | grep foo", "wc -l pom.xml",
    ]
    NOT_READ_ONLY = [
        "cat x.yml > y.yml", "sed -i 's/a/b/' file", "find . -name '*.tmp' -delete",
        "./gradlew test", "python3 x.py", "cat a | tee b", "git push origin main",
        "rm -rf build", "awk '{print > \"out\"}' f", "sudo cat /etc/shadow",
    ]

    def test_read_only(self):
        for cmd in self.READ_ONLY:
            self.assertTrue(mod.is_read_only_command(cmd), cmd)

    def test_not_read_only(self):
        for cmd in self.NOT_READ_ONLY:
            self.assertFalse(mod.is_read_only_command(cmd), cmd)

    def test_empty_is_not_read_only(self):
        self.assertFalse(mod.is_read_only_command(""))


class TestStepRequirementPhaseAliases(unittest.TestCase):
    """Задача 009: `level_requirements.steps` ссылается на full-имя фазы (`02-design`).

    В fix/lite-манифестах его нет НИКОГДА (там `fix-diag`/`lite-design`), поэтому требование
    R2 — а это `src/main/**.java`, весь прод-код — было невыполнимо в принципе."""

    REQ = {"mode": "require", "steps": ["02-design"]}

    def _check(self, status: dict):
        with unittest.mock.patch.object(mod, "manifest_status", lambda _root: status):
            return mod.check_requirement("R2", self.REQ, Path("/nonexistent"), "write")

    def test_fix_branch_alias_completed_allows(self):
        ok, why = self._check({"fix-intake": "completed", "fix-diag": "completed",
                               "fix-green": "pending"})
        self.assertTrue(ok, why)

    def test_fix_branch_alias_open_blocks(self):
        ok, why = self._check({"fix-intake": "completed", "fix-diag": "pending"})
        self.assertFalse(ok)
        self.assertIn("fix-diag", why)

    def test_lite_branch_alias(self):
        self.assertTrue(self._check({"lite-design": "completed"})[0])
        self.assertFalse(self._check({"lite-design": "pending"})[0])

    def test_full_branch_unchanged(self):
        self.assertTrue(self._check({"02-design": "completed"})[0])
        self.assertFalse(self._check({"02-design": "pending"})[0])

    def test_skipped_counts_as_done(self):
        self.assertTrue(self._check({"fix-diag": "skipped"})[0])

    def test_phase_absent_is_not_applicable(self):
        """Ветка без фазы дизайна вообще: требовать её — вечный тупик."""
        ok, why = self._check({"custom-1": "completed", "custom-2": "pending"})
        self.assertTrue(ok, why)

    def test_unreadable_manifest_still_denies(self):
        """Пустой status = манифест не распарсился. Это неясность → deny-first,
        а не «фазы нет, значит можно»."""
        ok, why = self._check({})
        self.assertFalse(ok)
        self.assertIn("манифест", why)


if __name__ == "__main__":
    unittest.main()