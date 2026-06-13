#!/usr/bin/env python3
"""Тесты для build_evals_from_design.py.

Запуск:
    python3 test_build_evals.py
    python3 -m pytest test_build_evals.py -v   (если pytest установлен)

Проверяет:
    1. Генерацию из реального task-plan.json
    2. Правильное количество evals на задачу
    3. Корректные типы (compile, coverage, test_pass)
    4. Пороги из pipeline.json
    5. Отсутствие evals при пустом task-plan
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Подключаем тестируемый модуль
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_evals_from_design import build_evals, SCHEMA_VERSION

# Заглушка для coverage_script — тесты не проверяют существование файла
FAKE_COVERAGE_SCRIPT = "/dev/null"

# --- Test data ---

SAMPLE_TASK_PLAN = {
    "feature_slug": "test-feature",
    "title": "Тестовая фича",
    "brd_path": "docs/brd/test.md",
    "design_path": "docs/design/test.md",
    "modules": ["service:test"],
    "coverage_threshold": 0.75,
    "migrations": [],
    "tasks": [
        {
            "id": "T1",
            "title": "Первая задача",
            "modules": ["service:test"],
            "layers": ["service"],
            "artifacts": [
                "service/test/src/main/java/TestService.java"
            ],
            "acceptance": [
                "Given X — When Y — Then Z"
            ],
            "depends_on": [],
            "sdd_ref": "sdd.md#t1",
        },
        {
            "id": "T2",
            "title": "Вторая задача",
            "modules": ["service:test"],
            "layers": ["repository", "service"],
            "artifacts": [
                "service/test/src/main/java/TestRepo.java",
            ],
            "acceptance": [
                "Given A — When B — Then C"
            ],
            "depends_on": ["T1"],
            "sdd_ref": "sdd.md#t2",
        },
    ],
}

SAMPLE_PIPELINE_CONFIG = {
    "quality": {
        "coverage_threshold": 0.80,
        "build_command": "./gradlew compileJava",
    },
}


class TestBuildEvals(unittest.TestCase):

    def test_generates_correct_number_of_evals(self):
        """Проверка: для 2 задач генерируется 6 evals (3 на задачу)."""
        result = build_evals(SAMPLE_TASK_PLAN, SAMPLE_PIPELINE_CONFIG, coverage_script=FAKE_COVERAGE_SCRIPT)
        self.assertEqual(result["summary"]["total"], 6)
        self.assertEqual(len(result["evals"]), 6)

    def test_evals_per_task(self):
        """Проверка: у каждой задачи ровно 3 evals."""
        result = build_evals(SAMPLE_TASK_PLAN, SAMPLE_PIPELINE_CONFIG, coverage_script=FAKE_COVERAGE_SCRIPT)
        for task in SAMPLE_TASK_PLAN["tasks"]:
            tid = task["id"]
            task_evals = [e for e in result["evals"] if e["task_id"] == tid]
            self.assertEqual(len(task_evals), 3, f"Задача {tid}: ожидается 3 evals")

    def test_eval_types_correct(self):
        """Проверка: типы evals — compile, coverage, test_pass."""
        result = build_evals(SAMPLE_TASK_PLAN, SAMPLE_PIPELINE_CONFIG, coverage_script=FAKE_COVERAGE_SCRIPT)
        types = set(e["type"] for e in result["evals"])
        self.assertEqual(types, {"compile", "coverage", "test_pass"})

    def test_coverage_threshold_from_pipeline_config(self):
        """Проверка: если в task-plan нет порога, берётся из pipeline.json (0.80)."""
        # Создаём task-plan без coverage_threshold
        plan_no_threshold = dict(SAMPLE_TASK_PLAN)
        plan_no_threshold.pop("coverage_threshold", None)
        for t in plan_no_threshold["tasks"]:
            t.pop("coverage_threshold", None)

        result = build_evals(plan_no_threshold, SAMPLE_PIPELINE_CONFIG, coverage_script=FAKE_COVERAGE_SCRIPT)
        coverage_evals = [e for e in result["evals"] if e["type"] == "coverage"]
        for e in coverage_evals:
            self.assertEqual(e["threshold"], 0.80)

    def test_coverage_threshold_from_task_plan(self):
        """Проверка: порог покрытия из task-plan (0.75) имеет приоритет над pipeline.json (0.80)."""
        result = build_evals(SAMPLE_TASK_PLAN, SAMPLE_PIPELINE_CONFIG, coverage_script=FAKE_COVERAGE_SCRIPT)
        # В SAMPLE_TASK_PLAN coverage_threshold = 0.75
        coverage_evals = [e for e in result["evals"] if e["type"] == "coverage"]
        for e in coverage_evals:
            self.assertEqual(e["threshold"], 0.75)

    def test_compile_threshold_is_zero(self):
        """Проверка: у compile threshold = 0 (просто exit code)."""
        result = build_evals(SAMPLE_TASK_PLAN, coverage_script=FAKE_COVERAGE_SCRIPT)
        compile_evals = [e for e in result["evals"] if e["type"] == "compile"]
        for e in compile_evals:
            self.assertEqual(e["threshold"], 0)

    def test_schema_version(self):
        """Проверка: в результате есть $schema."""
        result = build_evals(SAMPLE_TASK_PLAN, coverage_script=FAKE_COVERAGE_SCRIPT)
        self.assertEqual(result["$schema"], SCHEMA_VERSION)

    def test_summary_counts(self):
        """Проверка: сводка by_type и by_task корректны."""
        result = build_evals(SAMPLE_TASK_PLAN, coverage_script=FAKE_COVERAGE_SCRIPT)
        self.assertEqual(result["summary"]["by_type"]["compile"], 2)
        self.assertEqual(result["summary"]["by_type"]["coverage"], 2)
        self.assertEqual(result["summary"]["by_type"]["test_pass"], 2)
        self.assertEqual(result["summary"]["by_task"]["T1"], 3)
        self.assertEqual(result["summary"]["by_task"]["T2"], 3)

    def test_empty_task_plan(self):
        """Проверка: пустой task-plan даёт 0 evals."""
        empty = {"feature_slug": "empty", "tasks": []}
        result = build_evals(empty, coverage_script=FAKE_COVERAGE_SCRIPT)
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(len(result["evals"]), 0)

    def test_eval_ids_are_unique(self):
        """Проверка: все eval_id уникальны."""
        result = build_evals(SAMPLE_TASK_PLAN, SAMPLE_PIPELINE_CONFIG, coverage_script=FAKE_COVERAGE_SCRIPT)
        ids = [e["id"] for e in result["evals"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_generated_coverage_command_contains_threshold(self):
        """Проверка: команда coverage содержит --threshold."""
        result = build_evals(SAMPLE_TASK_PLAN, SAMPLE_PIPELINE_CONFIG, coverage_script=FAKE_COVERAGE_SCRIPT)
        coverage_evals = [e for e in result["evals"] if e["type"] == "coverage"]
        for e in coverage_evals:
            self.assertIn("--threshold", e["command"])

    def test_output_file_is_valid_json(self):
        """Проверка: при записи через main получается валидный JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            task_plan_path = Path(tmp) / "task-plan.json"
            with open(task_plan_path, "w") as f:
                json.dump(SAMPLE_TASK_PLAN, f)

            # Создаём фиктивный coverage-скрипт
            cov_script = Path(tmp) / "check_coverage.py"
            cov_script.write_text("#!/usr/bin/env python3\nprint('ok')\n")
            cov_script.chmod(0o755)

            out_path = Path(tmp) / "eval-plan.json"

            # Имитируем вызов main
            from build_evals_from_design import main as run_main
            sys.argv = [
                "build_evals_from_design.py",
                str(task_plan_path),
                "--out", str(out_path),
                "--coverage-script", str(cov_script),
            ]
            # Не даём main'у выйти с sys.exit
            try:
                run_main()
            except SystemExit:
                pass

            self.assertTrue(out_path.exists())
            with open(out_path) as f:
                loaded = json.load(f)
            self.assertEqual(loaded["summary"]["total"], 6)


if __name__ == "__main__":
    unittest.main()