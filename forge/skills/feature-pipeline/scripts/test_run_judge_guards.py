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

    def test_assertion_loss_blocks(self):
        # раньше был WARN; для слабой модели «выкинуть проверки» — основной путь к GREEN → блок
        self._patch({"FooTest.java": {
            "added": [],
            "removed": ["assertEquals(1, x);", "assertNotNull(y);", "verify(z).f();"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertTrue(any("потеря проверок" in b for b in blocking))

    def test_single_assertion_loss_not_blocked(self):
        # потеря одного assert (< 2) — допускается как легитимный рефактор
        self._patch({"FooTest.java": {
            "added": [],
            "removed": ["assertEquals(1, x);"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertEqual(blocking, [])

    def test_expected_value_rewrite_blocks(self):
        # тот же скелет assert, другой литерал — «прогнули ожидаемое значение под новое поведение»
        self._patch({"FooTest.java": {
            "added": ["assertEquals(2, service.countTasks());"],
            "removed": ["assertEquals(1, service.countTasks());"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertTrue(any("ожидаемое значение" in b for b in blocking))

    def test_assert_refactor_same_literal_not_blocked(self):
        # перенос строки без изменения литералов — не блокируем
        self._patch({"FooTest.java": {
            "added": ["assertEquals(1, service.countTasks());"],
            "removed": ["assertEquals(1,  service.countTasks());"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertEqual(blocking, [])

    def test_removed_test_method_blocks(self):
        self._patch({"FooTest.java": {
            "added": [],
            "removed": ["    @Test", "    void shouldNotify() {"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertTrue(any("@Test" in b for b in blocking))

    def test_moved_test_method_not_blocked(self):
        # @Test удалён и добавлен (перенос) — не блокируем
        self._patch({"FooTest.java": {
            "added": ["    @Test"],
            "removed": ["    @Test"],
        }})
        _checks, blocking, _w = rj._test_integrity_floor("HEAD")
        self.assertEqual(blocking, [])


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
        }), encoding="utf-8")
        if max_iter is not None:
            (root / "ground").mkdir(exist_ok=True)
            (root / "ground" / "pipeline.json").write_text(
                json.dumps({"quality": {"max_judge_iterations": max_iter}}), encoding="utf-8")
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



class TestRedJudgePolarity(unittest.TestCase):
    """Полярность RED-судьи (найдено e2e на настоящей сборке Gradle+JaCoCo).

    `red-judge` судит фазу, смысл которой — «тесты обязаны падать», но засчитывал прогон при
    `returncode == 0`, то есть при ЗЕЛЁНЫХ тестах: корректное RED-состояние рубил, а состояние
    «RED не написан, всё зелено» пропускал. Один и тот же проект одновременно давал
    `check_tests_red: PASS` и `red-judge: FAIL`. В юнит-тестах не всплывало: без feat_classes
    судья до gradle не доходил (fail-open по scope)."""

    class _Proc:
        def __init__(self, rc, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def _run(self, tmp: Path, proc):
        """Прогоняет red-судью с подменённым subprocess.run и вернёт (passed, blocking)."""
        feature_dir = tmp / "docs" / "feature-pipeline" / "ORD-1"
        feature_dir.mkdir(parents=True)
        (feature_dir / "task-plan.json").write_text(json.dumps({
            "feature_slug": "ORD-1", "title": "t", "coverage_threshold": 0.8,
            "tasks": [{"id": "T1", "layers": ["service"], "acceptance": ["a"],
                       "sdd_ref": "REQ-1",
                       "artifacts": ["src/main/java/A.java",
                                     "src/test/java/ATest.java"]}]}), encoding="utf-8")
        # check_red импортирует subprocess внутри себя — патчим сам модуль subprocess.
        import subprocess as _sp
        orig = _sp.run
        _sp.run = lambda *a, **k: proc
        try:
            return rj.check_red("ORD-1", feature_dir)
        finally:
            _sp.run = orig

    def _verdict(self, tmp, proc):
        v = self._run(tmp, proc)
        return v.get("passed"), " ".join(v.get("blocking_issues") or [])

    def test_failing_tests_are_valid_red(self):
        with tempfile.TemporaryDirectory() as d:
            passed, blocking = self._verdict(Path(d), self._Proc(1, "> Task :test FAILED"))
            self.assertTrue(passed, f"падающие тесты — это и есть RED; blocking={blocking}")

    def test_green_tests_are_not_red(self):
        with tempfile.TemporaryDirectory() as d:
            passed, blocking = self._verdict(Path(d), self._Proc(0, "BUILD SUCCESSFUL"))
            self.assertFalse(passed, "зелёные тесты не могут быть валидным RED")
            self.assertIn("ЗЕЛЁНЫЕ", blocking)

    def test_empty_scope_is_blocking_not_pass(self):
        """Судья без тест-классов фичи не проверяет НИЧЕГО — вердикт обязан быть FAIL.

        Раньше это был WARN и `passed=True` со сводкой «0/1 modules passed»: фаза RED
        закрывалась, ни разу не запустив тесты."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            feature_dir = tmp / "docs" / "feature-pipeline" / "ORD-2"
            feature_dir.mkdir(parents=True)
            # задача пишет код и НЕ test-exempt, но тест-файлов в artifacts нет
            (feature_dir / "task-plan.json").write_text(json.dumps({
                "feature_slug": "ORD-2", "title": "t", "coverage_threshold": 0.8,
                "tasks": [{"id": "T1", "layers": ["service"], "acceptance": ["a"],
                           "sdd_ref": "REQ-1",
                           "artifacts": ["src/main/java/A.java"]}]}), encoding="utf-8")
            v = rj.check_red("ORD-2", feature_dir)
            self.assertFalse(v.get("passed"), "пустой scope не может давать PASS")
            self.assertIn("scope", " ".join(v.get("blocking_issues") or []))


    def test_compile_failure_is_not_red(self):
        with tempfile.TemporaryDirectory() as d:
            passed, blocking = self._verdict(
                Path(d), self._Proc(1, "> Task :compileTestJava FAILED"))
            self.assertFalse(passed, "RED обязан компилироваться")
            self.assertIn("компилируются", blocking)


if __name__ == "__main__":
    unittest.main()
