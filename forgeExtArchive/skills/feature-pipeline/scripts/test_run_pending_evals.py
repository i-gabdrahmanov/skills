#!/usr/bin/env python3
"""Тесты для run_pending_evals.py — принудительный прогон eval'ов для задачи.

Запуск:
    python3 test_run_pending_evals.py
    python3 -m unittest test_run_pending_evals -v

Проверяет:
    1. Нет eval-plan → pass (rc=0)
    2. Нет eval'ов для задачи → pass
    3. Все eval'ы пройдены → pass
    4. Хотя бы один eval не пройден → fail (rc=2)
    5. Сохранение результатов в evals.json
    6. _resolve_eval_plan_path: автодетект пути
    7. _run_cmd: базовый запуск
    8. --json вывод
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_pending_evals as rpe


class TestResolveEvalPlanPath(unittest.TestCase):
    """Тесты _resolve_eval_plan_path — автодетект пути."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_explicit_path(self):
        """Явный --eval-plan используется."""
        p = self.root / "custom.json"
        p.write_text("{}", encoding="utf-8")
        result = rpe._resolve_eval_plan_path(str(self.root), "test", str(p))
        self.assertEqual(result, p)

    def test_standard_path(self):
        """docs/feature-pipeline/<feature>/eval-plan.json."""
        p = self.root / "docs" / "feature-pipeline" / "my-feature" / "eval-plan.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
        result = rpe._resolve_eval_plan_path(str(self.root), "my-feature", None)
        self.assertEqual(result, p)


class TestRunCmd(unittest.TestCase):
    """Тесты _run_cmd."""

    def test_echo(self):
        rc, out = rpe._run_cmd("echo hello", "/tmp")
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)

    def test_false(self):
        rc, _ = rpe._run_cmd("false", "/tmp")
        self.assertNotEqual(rc, 0)


class TestMain(unittest.TestCase):
    """Тесты main() — интеграция CLI."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

        # eval-plan с eval'ами для T1
        self.eval_plan = {
            "$schema": "feature-pipeline/evals@1",
            "feature_slug": "test-feature",
            "evals": [
                {
                    "id": "compile-t1",
                    "type": "compile",
                    "task_id": "T1",
                    "command": "echo compile ok",
                    "threshold": 0,
                },
                {
                    "id": "coverage-t1",
                    "type": "coverage",
                    "task_id": "T1",
                    "command": "echo coverage 0.85",
                    "threshold": 0.80,
                },
            ],
        }
        self.eval_plan_path = self.root / "eval-plan.json"
        self.eval_plan_path.write_text(json.dumps(self.eval_plan), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_main(self, extra_args: list[str] | None = None) -> int:
        args = [
            "run_pending_evals.py",
            "--project", str(self.root),
            "--feature", "test-feature",
            "--task", "T1",
            "--eval-plan", str(self.eval_plan_path),
            "--skill", "feature-pipeline",
            "--feature-docs-dir", "docs/feature-pipeline",
            *(extra_args or []),
        ]
        sys.argv = args
        try:
            return rpe.main()
        except SystemExit as e:
            return e.code or 0

    def test_all_passed(self):
        """Все eval'ы проходят → rc=0."""
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_failed_eval(self):
        """Eval с fail-командой → rc=2."""
        self.eval_plan["evals"][0]["command"] = "false"
        self.eval_plan_path.write_text(json.dumps(self.eval_plan), encoding="utf-8")
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_no_eval_plan(self):
        """Нет eval-plan → rc=0."""
        rc = self._run_main(["--eval-plan", "/nonexistent/path.json"])
        self.assertEqual(rc, 0)

    def test_no_evals_for_task(self):
        """Нет eval'ов для задачи → rc=0."""
        rc = self._run_main(["--task", "BOGUS"])
        self.assertEqual(rc, 0)

    def test_results_file_created(self):
        """После прогона создаётся evals.json."""
        self._run_main()
        results_path = self.root / "ground" / "statements" / "feature-pipeline" / "test-feature" / "evals.json"
        self.assertTrue(results_path.exists())

    def test_results_content(self):
        """В evals.json есть корректные результаты."""
        self._run_main()
        results_path = self.root / "ground" / "statements" / "feature-pipeline" / "test-feature" / "evals.json"
        data = json.loads(results_path.read_text(encoding="utf-8"))
        self.assertIn("compile-t1", data)
        self.assertEqual(data["compile-t1"]["status"], "passed")
        self.assertIn("_meta", data)
        self.assertEqual(data["_meta"]["task"], "T1")

    def test_json_output(self):
        """--json выдаёт summary."""
        rc = self._run_main(["--json"])
        self.assertEqual(rc, 0)

    def test_partial_fail(self):
        """Один eval pass, другой fail → rc=2."""
        self.eval_plan["evals"][1]["command"] = "false"
        self.eval_plan_path.write_text(json.dumps(self.eval_plan), encoding="utf-8")
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_compile_and_coverage_both_pass(self):
        """compile + coverage оба проходят."""
        rc = self._run_main()
        self.assertEqual(rc, 0)


class TestMainWithoutPipeline(unittest.TestCase):
    """Тесты main() без pipeline.json."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.eval_plan = {
            "evals": [
                {"id": "compile-t1", "type": "compile", "task_id": "T1",
                 "command": "echo ok", "threshold": 0},
            ],
        }
        self.eval_plan_path = self.root / "eval-plan.json"
        self.eval_plan_path.write_text(json.dumps(self.eval_plan), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_without_pipeline_config(self):
        """Без pipeline.json — работает."""
        args = [
            "run_pending_evals.py",
            "--project", str(self.root),
            "--feature", "test",
            "--task", "T1",
            "--eval-plan", str(self.eval_plan_path),
            "--skill", "feature-pipeline",
            "--feature-docs-dir", "docs/feature-pipeline",
        ]
        sys.argv = args
        try:
            rc = rpe.main()
        except SystemExit as e:
            rc = e.code or 0
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()