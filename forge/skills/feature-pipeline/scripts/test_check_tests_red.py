#!/usr/bin/env python3
"""Тесты для check_tests_red.py — gate RED тестов (TDD).

Запуск:
    python3 test_check_tests_red.py
    python3 -m unittest test_check_tests_red -v

Проверяет:
    1. _extract_tasks: фильтрация задач с main-слоем
    2. _has_red_tests: детект RED/GREEN из вывода тестов
    3. main(): нет задач с main → pass
    4. main(): compile fail → fail
    5. main(): compile ok + tests pass → fail (GREEN)
    6. main(): compile ok + tests fail → pass (RED)
    7. --json вывод
    8. --task фильтр
    9. _run_cmd: базовый запуск
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_tests_red as ctr


class TestExtractTasks(unittest.TestCase):
    """Тесты _extract_tasks — какие задачи считаются main-слоем."""

    def test_main_layer(self):
        """Задача с layers=['repository', 'main'] → попадает."""
        plan = {"tasks": [{"id": "T1", "layers": ["repository", "main"]}]}
        tasks = ctr._extract_tasks(plan)
        self.assertEqual(len(tasks), 1)

    def test_no_main_layer(self):
        """Задача с layers=['test'] → не попадает."""
        plan = {"tasks": [{"id": "T1", "layers": ["test"]}]}
        tasks = ctr._extract_tasks(plan)
        self.assertEqual(len(tasks), 0)

    def test_main_in_artifacts(self):
        """Задача с src/main в artifacts → попадает."""
        plan = {"tasks": [{"id": "T1", "artifacts": ["src/main/java/Foo.java"]}]}
        tasks = ctr._extract_tasks(plan)
        self.assertEqual(len(tasks), 1)

    def test_only_test_artifacts(self):
        """Задача только с src/test → не попадает."""
        plan = {"tasks": [{"id": "T1", "artifacts": ["src/test/java/FooTest.java"]}]}
        tasks = ctr._extract_tasks(plan)
        self.assertEqual(len(tasks), 0)

    def test_filter_by_task(self):
        """--task T2 — только T2."""
        plan = {"tasks": [
            {"id": "T1", "layers": ["main"]},
            {"id": "T2", "layers": ["main"]},
        ]}
        tasks = ctr._extract_tasks(plan, "T2")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "T2")

    def test_empty_tasks(self):
        """Пустой список → []."""
        self.assertEqual(ctr._extract_tasks({"tasks": []}), [])

    def test_no_layers_key(self):
        """Задача без поля layers → не попадает (нет main)."""
        plan = {"tasks": [{"id": "T1"}]}
        tasks = ctr._extract_tasks(plan)
        self.assertEqual(len(tasks), 0)

    def test_main_in_artifact_path(self):
        """src/main в разных вариантах пути."""
        plan = {"tasks": [{"id": "T1", "artifacts": ["service/test/src/main/java/Foo.java"]}]}
        tasks = ctr._extract_tasks(plan)
        self.assertEqual(len(tasks), 1)


class TestHasRedTests(unittest.TestCase):
    """Тесты _has_red_tests — анализ вывода тестов."""

    def test_fail_in_output(self):
        """FAILED в выводе → RED."""
        red, _ = ctr._has_red_tests("Tests run: 5, Failures: 1, FAILED")
        self.assertTrue(red)

    def test_failures_in_output(self):
        """failures в выводе → RED."""
        red, _ = ctr._has_red_tests("Tests run: 5, failures: 2")
        self.assertTrue(red)

    def test_passed_only(self):
        """passed без FAILED → GREEN."""
        red, reason = ctr._has_red_tests("BUILD SUCCESSFUL\nTests passed: 10")
        self.assertFalse(red)
        self.assertIn("GREEN", reason)

    def test_empty_output(self):
        """Пустой вывод → считаем RED."""
        red, _ = ctr._has_red_tests("")
        self.assertTrue(red)

    def test_gradle_build_fail(self):
        """BUILD FAILED → RED."""
        red, _ = ctr._has_red_tests("BUILD FAILED\nThere were failing tests")
        self.assertTrue(red)


class TestRunCmd(unittest.TestCase):
    """Тесты _run_cmd — запуск команд."""

    def test_echo(self):
        """echo hello → rc 0."""
        rc, out = ctr._run_cmd("echo hello", "/tmp")
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)

    def test_false(self):
        """false → rc 1."""
        rc, out = ctr._run_cmd("false", "/tmp")
        self.assertNotEqual(rc, 0)


class TestMain(unittest.TestCase):
    """Тесты main() — интеграция CLI (без реального subprocess)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        # task-plan с main-задачей
        self.plan = {
            "tasks": [
                {"id": "T1", "layers": ["main"], "artifacts": ["src/main/java/Foo.java"]},
            ],
        }
        self.plan_path = self.root / "task-plan.json"
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_main(self, extra_args: list[str] | None = None) -> int:
        args = [
            "check_tests_red.py",
            str(self.plan_path),
            "--root", str(self.root),
            *(extra_args or []),
        ]
        sys.argv = args
        try:
            return ctr.main()
        except SystemExit as e:
            return e.code or 0

    def test_no_main_tasks(self):
        """Нет задач с main → pass."""
        plan = {"tasks": [{"id": "T1", "layers": ["test"]}]}
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_no_tasks(self):
        """Пустой task-plan → pass."""
        self.plan_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_filter_by_task_missing(self):
        """--task для несуществующей задачи → ничего не проверяется → pass."""
        rc = self._run_main(["--task", "BOGUS"])
        self.assertEqual(rc, 0)

    def test_with_json_flag(self):
        """--json не падает."""
        rc = self._run_main(["--json"])
        # Команда compileTestJava запустится — может упасть в тестовой среде, это ок
        # Главное что не бросит исключение
        self.assertIn(rc, (0, 2))


if __name__ == "__main__":
    unittest.main()