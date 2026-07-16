#!/usr/bin/env python3
"""Тесты для check_tests_red.py — gate RED тестов (TDD).

Запуск:
    python3 test_check_tests_red.py
    python3 -m unittest test_check_tests_red -v

Проверяет:
    1. _extract_tasks: фильтрация задач с main-слоем
    2. ПО-ТЕСТОВЫЙ RED-вердикт (JUnit XML): все red → pass; 1 red + N green → fail
       (пин бага прогона: судья засчитывал это успехом); нет отчётов → fail
    3. main(): нет задач с main → pass
    4. main(): compile fail → fail
    5. --json вывод
    6. --task фильтр
    7. _run_cmd: базовый запуск
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


def _junit_xml(cases: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<testcase classname="com.x.FooTest" name="{n}">'
        + ('<failure message="boom"/>' if s == "red" else "") + "</testcase>"
        for n, s in cases)
    return (f'<?xml version="1.0"?>'
            f'<testsuite name="FooTest" tests="{len(cases)}">{items}</testsuite>')


class TestPerTestRed(unittest.TestCase):
    """ПО-ТЕСТОВЫЙ RED-вердикт (JUnit XML текущего прогона) — end-to-end через main()."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        plan = {"tasks": [{"id": "T1", "layers": ["main"],
                           "artifacts": ["src/main/java/Foo.java"]}]}
        self.plan_path = self.root / "task-plan.json"
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _runner(self, cases: list[tuple[str, str]], exit_code: int = 1) -> str:
        """«Тест-раннер»: пишет JUnit XML и выходит с exit_code (как gradle test)."""
        (self.root / "report.xml").write_text(_junit_xml(cases), encoding="utf-8")
        runner = self.root / "runner.py"
        runner.write_text(
            "import pathlib, shutil, sys\n"
            "d = pathlib.Path('build/test-results/test'); d.mkdir(parents=True, exist_ok=True)\n"
            "shutil.copy('report.xml', d / 'TEST-com.x.FooTest.xml')\n"
            f"sys.exit({exit_code})\n", encoding="utf-8")
        return f'"{sys.executable}" runner.py'

    def _main(self, test_cmd: str, compile_cmd: str = "true") -> int:
        sys.argv = ["check_tests_red.py", str(self.plan_path), "--root", str(self.root),
                    "--compile-cmd", compile_cmd, "--test-cmd", test_cmd]
        try:
            return ctr.main()
        except SystemExit as e:
            return e.code or 0

    def test_all_red_passes(self):
        rc = self._main(self._runner([("t1", "red"), ("t2", "red")]))
        self.assertEqual(rc, 0)

    def test_one_red_rest_green_fails(self):
        # ПИН бага прогона: 1 red валит раннер (exit!=0) → раньше «RED пройден»,
        # хотя остальные новые тесты зелёные (вакуумные)
        rc = self._main(self._runner([("t1", "red"), ("t2", "green"), ("t3", "green")]))
        self.assertEqual(rc, 2, "1 red + 2 green — НЕ успех RED")

    def test_all_green_fails(self):
        rc = self._main(self._runner([("t1", "green")], exit_code=0))
        self.assertEqual(rc, 2)

    def test_no_reports_fails(self):
        rc = self._main("false")
        self.assertEqual(rc, 2, "exit!=0 без JUnit-отчётов — не доказательство RED")

    def test_zero_executed_fails(self):
        rc = self._main(self._runner([]))
        self.assertEqual(rc, 2)

    def test_compile_fail_fails(self):
        rc = self._main(self._runner([("t1", "red")]), compile_cmd="false")
        self.assertEqual(rc, 2)


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