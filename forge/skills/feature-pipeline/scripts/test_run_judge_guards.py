#!/usr/bin/env python3
"""Тесты verify-стражей run_judge: C1 (лимит ре-итераций) и C2 (целостность тестов)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_judge as rj


class TestTestIntegrityFloor(unittest.TestCase):
    """C2 — floor «GREEN любой ценой» ловит ослабление существующих тестов."""

    def tearDown(self):
        # вернуть оригинальный сборщик diff, если монкипатчили
        if hasattr(self, "_orig"):
            rj._git_diff_test_changes = self._orig

    def _patch(self, per_file):
        self._orig = rj._git_diff_test_changes
        rj._git_diff_test_changes = lambda base: per_file

    def test_no_test_changes_passes(self):
        self._patch({})
        checks, blocking, warnings = rj._test_integrity_floor("HEAD")
        self.assertEqual(blocking, [])
        self.assertTrue(any(c["status"] == "PASS" for c in checks))

    def test_added_disabled_blocks(self):
        self._patch({"FooTest.java": {"added": ["    @Disabled(\"flaky\")"], "removed": []}})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertTrue(any("Disabled" in b or "отключён" in b for b in blocking))

    def test_disabled_move_not_blocked(self):
        # аннотация была и осталась (перенос) — не блокируем
        self._patch({"FooTest.java": {"added": ["@Disabled"], "removed": ["@Disabled"]}})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertEqual(blocking, [])

    def test_verify_times_increase_blocks(self):
        self._patch({"FooTest.java": {
            "added": ["verify(monitoringService, times(2)).notifyEvent(X);"],
            "removed": ["verify(monitoringService, times(1)).notifyEvent(X);"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertTrue(any("times" in b for b in blocking))

    def test_verify_times_decrease_not_blocked(self):
        # ужесточение (2→1) — это не ослабление, не блокируем
        self._patch({"FooTest.java": {
            "added": ["verify(x, times(1)).f();"],
            "removed": ["verify(x, times(2)).f();"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertEqual(blocking, [])

    def test_assertion_loss_warns_not_blocks(self):
        self._patch({"FooTest.java": {
            "added": [],
            "removed": ["assertEquals(1, x);", "assertNotNull(y);", "verify(z).f();"],
        }})
        _checks, blocking, warnings = rj._test_integrity_floor("HEAD")
        self.assertEqual(blocking, [])
        self.assertTrue(any("потеря проверок" in w for w in warnings))


class TestIterationCap(unittest.TestCase):
    """C1 — лимит ре-итераций судьи (errors.json) форсит эскалацию."""

    def _setup(self, iterations, max_iter=None):
        d = tempfile.mkdtemp()
        root = Path(d)
        rj._set_paths(root, skill="feature-pipeline")
        slug = "feat-x"
        store_dir = root / "ground" / "statements" / "feature-pipeline" / slug / "judges"
        store_dir.mkdir(parents=True)
        (store_dir / "errors.json").write_text(json.dumps({
            "iterations": iterations, "accumulated_errors": [],
        }))
        if max_iter is not None:
            (root / "ground").mkdir(exist_ok=True)
            (root / "ground" / "pipeline.json").write_text(
                json.dumps({"quality": {"max_judge_iterations": max_iter}}))
        return root, slug

    def test_count_per_judge(self):
        root, slug = self._setup([
            {"judge": "coverage-judge"}, {"judge": "coverage-judge"},
            {"judge": "red-judge"},
        ])
        self.assertEqual(rj._judge_iteration_count(slug, "coverage-judge"), 2)
        self.assertEqual(rj._judge_iteration_count(slug, "red-judge"), 1)

    def test_escalates_at_limit(self):
        root, slug = self._setup([{"judge": "coverage-judge"}] * 3)
        self.assertTrue(rj._maybe_escalate(slug, "coverage-judge", root))

    def test_no_escalate_below_limit(self):
        root, slug = self._setup([{"judge": "coverage-judge"}] * 2)
        self.assertFalse(rj._maybe_escalate(slug, "coverage-judge", root))

    def test_custom_limit_from_pipeline(self):
        root, slug = self._setup([{"judge": "coverage-judge"}] * 2, max_iter=2)
        self.assertEqual(rj._max_iterations(root), 2)
        self.assertTrue(rj._maybe_escalate(slug, "coverage-judge", root))

    def test_default_limit_is_three(self):
        root, _slug = self._setup([])
        self.assertEqual(rj._max_iterations(root), 3)


if __name__ == "__main__":
    unittest.main()
