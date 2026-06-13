#!/usr/bin/env python3
"""Тесты для build_evidence.py — сборка evidence bundle для задачи.

Запуск:
    python3 test_build_evidence.py
    python3 -m pytest test_build_evidence.py -v

Проверяет:
    1. Сборка bundle из существующих stейтов
    2. _completeness() — правильный расчёт
    3. REQUIRED поля все заполнены
    4. Сборка с пустыми полями → низкая completeness
    5. Запись в ground/evidence/
    6. --json вывод
    7. Работа с кастомным feature
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_evidence import _completeness, _step_dir, main


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class TestCompleteness(unittest.TestCase):
    """Тесты _completeness() — расчёт полноты пакета."""

    def test_all_fields_present(self):
        """Все 7 полей REQUIRED заполнены → 1.0."""
        bundle = {
            "task": "T1",
            "tests": {"passed": 5},
            "coverage": 0.85,
            "gates": {"build": "pass"},
            "artifacts": ["file.java"],
            "rationale": "Потому что",
            "sdd_ref": "sdd.md#t1",
        }
        self.assertAlmostEqual(_completeness(bundle), 1.0)

    def test_one_field_missing(self):
        """Одно поле пусто → ~0.857."""
        bundle = {
            "task": "T1",
            "tests": {"passed": 5},
            "coverage": 0.85,
            "gates": {"build": "pass"},
            "artifacts": ["file.java"],
            "rationale": "",
            "sdd_ref": "sdd.md#t1",
        }
        self.assertAlmostEqual(_completeness(bundle), 0.857, places=2)

    def test_two_fields_missing(self):
        """Два поля пусты → ~0.714."""
        bundle = {
            "task": "T1",
            "tests": {"passed": 5},
            "coverage": 0.85,
            "gates": {"build": "pass"},
            "artifacts": [],         # [] → пусто
            "rationale": "",         # пустая строка
            "sdd_ref": "sdd.md#t1",
        }
        # Присутствуют: task, tests, coverage, gates, sdd_ref = 5/7 = ~0.714
        self.assertAlmostEqual(_completeness(bundle), 5/7, places=2)

    def test_all_fields_empty(self):
        """Все поля пусты → 0.0."""
        bundle = {
            "task": "",
            "tests": {},
            "coverage": None,
            "gates": {},
            "artifacts": [],
            "rationale": "",
            "sdd_ref": "",
        }
        self.assertEqual(_completeness(bundle), 0.0)

    def test_none_is_also_empty(self):
        """None считается пустым."""
        bundle = {
            "task": "T1",
            "tests": None,
            "coverage": None,
            "gates": {"build": "pass"},
            "artifacts": None,
            "rationale": "text",
            "sdd_ref": None,
        }
        # Присутствуют: task, gates, rationale = 3/7 = ~0.429
        self.assertAlmostEqual(_completeness(bundle), 3/7, places=2)


class TestMain(unittest.TestCase):
    """Тесты main() — интеграция сборки evidence."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

        # task-plan
        self.plan = {
            "feature_slug": "test-feature",
            "tasks": [
                {
                    "id": "T1",
                    "title": "Первая задача",
                    "artifacts": ["src/main/java/Foo.java"],
                    "rationale": "Нужен для теста",
                    "sdd_ref": "sdd.md#t1",
                    "acceptance": "Given X — When Y",
                },
            ],
        }
        self.plan_path = self.root / "task-plan.json"
        _write_json(self.plan_path, self.plan)

        # step directory (ground/statements/feature-pipeline/pipeline/)
        step_dir = self.root / "ground" / "statements" / "feature-pipeline" / "pipeline"
        _write_json(step_dir / "04-build-T1.json", {"status": "pass", "artifacts": ["src/main/java/Foo.java"]})
        _write_json(step_dir / "05-tests.json", {"tests": 5, "summary": "5 passed", "coverage": 0.85, "gate": "pass"})
        _write_json(step_dir / "07-deliver-T1.json", {"gate": "pass"})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_main(self, extra_args: list[str] | None = None) -> int:
        args = [
            "build_evidence.py",
            str(self.plan_path),
            "--task", "T1",
            "--root", str(self.root),
            *(extra_args or []),
        ]
        sys.argv = args
        try:
            return main()
        except SystemExit as e:
            return e.code or 0

    def test_bundle_created(self):
        """Evidence bundle создан в ground/evidence/."""
        self._run_main()
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        self.assertTrue(evidence_path.exists())

    def test_bundle_has_all_fields(self):
        """В bundle есть все поля из REQUIRED."""
        self._run_main()
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        for k in ("task", "tests", "coverage", "gates", "artifacts", "rationale", "sdd_ref"):
            self.assertIn(k, bundle)

    def test_completeness_calculated(self):
        """completeness > 0."""
        self._run_main()
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertGreater(bundle["completeness"], 0)

    def test_completeness_above_threshold(self):
        """Все поля заполнены → completeness >= 0.95."""
        self._run_main()
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(bundle["completeness"], 0.95)

    def test_timestamp_iso(self):
        """timestamp в ISO формате."""
        self._run_main()
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertIn("T", bundle.get("timestamp", ""))

    def test_json_output(self):
        """--json выдаёт bundle."""
        rc = self._run_main(["--json"])
        self.assertEqual(rc, 0)

    def test_rationale_from_arg(self):
        """--rationale подставляет rationale."""
        self._run_main(["--rationale", "Тестовое обоснование"])
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["rationale"], "Тестовое обоснование")

    def test_rationale_empty(self):
        """Без --rationale rationale из task."""
        self._run_main()
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["rationale"], "Нужен для теста")

    def test_custom_feature_namespace(self):
        """--feature меняет namespace стейта."""
        # Создаём step dir под кастомным именем
        custom_dir = self.root / "ground" / "statements" / "feature-pipeline" / "custom"
        _write_json(custom_dir / "04-build-T1.json", {"status": "pass"})
        _write_json(custom_dir / "05-tests.json", {"tests": 3})

        self._run_main(["--feature", "custom"])
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertIsNotNone(bundle.get("tests"))

    def test_bundle_not_empty_plan(self):
        """Пустой task-plan → bundle с task T1 всё равно."""
        _write_json(self.plan_path, {})
        self._run_main()
        evidence_path = self.root / "ground" / "evidence" / "T1.json"
        bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["task"], "T1")


if __name__ == "__main__":
    unittest.main()