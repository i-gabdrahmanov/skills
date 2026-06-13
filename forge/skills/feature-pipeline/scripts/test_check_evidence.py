#!/usr/bin/env python3
"""Тесты для check_evidence.py — gate полноты evidence bundle.

Запуск:
    python3 test_check_evidence.py
    python3 -m pytest test_check_evidence.py -v

Проверяет:
    1. Все evidence completeness >= порога → pass
    2. Отсутствует evidence файл → fail
    3. completeness < порога → fail
    4. Порог из pipeline.json
    5. --task фильтр
    6. Пустой task-plan → pass
    7. --json вывод
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_evidence import main


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class TestMain(unittest.TestCase):
    """Тесты main() — интеграция CLI."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

        # task-plan: две задачи
        self.plan = {
            "tasks": [
                {"id": "T1", "title": "Первая"},
                {"id": "T2", "title": "Вторая"},
            ],
        }
        self.plan_path = self.root / "task-plan.json"
        _write_json(self.plan_path, self.plan)

        # evidence bundle: T1 = 0.95, T2 = 1.0 (все выше порога)
        evidence_dir = self.root / "ground" / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_json(evidence_dir / "T1.json", {"completeness": 0.95, "task": "T1"})
        _write_json(evidence_dir / "T2.json", {"completeness": 1.0, "task": "T2"})

        # pipeline config
        self.pipeline_cfg = {"evidence": {"threshold": 0.95}}
        self.pipeline_cfg_path = self.root / "pipeline.json"
        _write_json(self.pipeline_cfg_path, self.pipeline_cfg)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_main(self, extra_args: list[str] | None = None) -> int:
        args = [
            "check_evidence.py",
            str(self.plan_path),
            "--root", str(self.root),
            "--pipeline-config", str(self.pipeline_cfg_path),
            *(extra_args or []),
        ]
        sys.argv = args
        try:
            return main()
        except SystemExit as e:
            return e.code or 0

    def test_pass_all_above_threshold(self):
        """Все evidence >= порога → pass."""
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_fail_missing_evidence(self):
        """Нет файла evidence для задачи → fail."""
        (self.root / "ground" / "evidence" / "T2.json").unlink()
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_fail_below_threshold(self):
        """completeness < порога → fail."""
        evidence_dir = self.root / "ground" / "evidence"
        _write_json(evidence_dir / "T1.json", {"completeness": 0.50, "task": "T1"})
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_threshold_from_pipeline_config(self):
        """Порог из pipeline.json (0.95). T1=0.80 < 0.95 → fail."""
        evidence_dir = self.root / "ground" / "evidence"
        _write_json(evidence_dir / "T1.json", {"completeness": 0.80, "task": "T1"})
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_default_threshold(self):
        """Без pipeline-config порог 0.95 по умолчанию."""
        rc = self._run_main(["--pipeline-config", "/nonexistent/path.json"])
        self.assertEqual(rc, 0)  # T1=0.95, T2=1.0 >= 0.95

    def test_explicit_threshold_override(self):
        """--threshold переопределяет pipeline.json."""
        rc = self._run_main(["--threshold", "0.99"])
        self.assertEqual(rc, 2)  # T1=0.95 < 0.99

    def test_filter_by_task(self):
        """--task T1 — проверяется только T1."""
        rc = self._run_main(["--task", "T1"])
        self.assertEqual(rc, 0)

    def test_filter_by_task_missing(self):
        """--task для задачи без evidence → fail."""
        rc = self._run_main(["--task", "BOGUS"])
        self.assertEqual(rc, 2)

    def test_empty_tasks(self):
        """Пустой task-plan → pass."""
        _write_json(self.plan_path, {"tasks": []})
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_json_output_pass(self):
        """--json при pass."""
        rc = self._run_main(["--json"])
        self.assertEqual(rc, 0)

    def test_json_output_fail(self):
        """--json при fail."""
        (self.root / "ground" / "evidence" / "T2.json").unlink()
        rc = self._run_main(["--json"])
        self.assertEqual(rc, 2)

    def test_completeness_0(self):
        """completeness=0 → fail."""
        evidence_dir = self.root / "ground" / "evidence"
        _write_json(evidence_dir / "T1.json", {"completeness": 0.0, "task": "T1"})
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_no_evidence_dir(self):
        """Нет директории ground/evidence → оба не найдены → fail."""
        import shutil
        shutil.rmtree(self.root / "ground" / "evidence")
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_corrupted_evidence_json(self):
        """Битый JSON в evidence → считается как отсутствующий."""
        (self.root / "ground" / "evidence" / "T1.json").write_text("not json\n")
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_completeness_not_numeric(self):
        """completeness не число → fail (float() бросит исключение, _load вернёт как есть, а float(bundle.get(...)) упадёт)."""
        evidence_dir = self.root / "ground" / "evidence"
        _write_json(evidence_dir / "T1.json", {"completeness": "high", "task": "T1"})
        with self.assertRaises(ValueError):
            self._run_main()


class TestMainNoPipelineConfig(unittest.TestCase):
    """Тесты без pipeline-config — порог 0.95 по умолчанию."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.plan = {"tasks": [{"id": "T1"}]}
        self.plan_path = self.root / "task-plan.json"
        _write_json(self.plan_path, self.plan)
        evidence_dir = self.root / "ground" / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_json(evidence_dir / "T1.json", {"completeness": 0.95, "task": "T1"})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_default_threshold_no_config(self):
        """Без --pipeline-config порог 0.95."""
        args = [
            "check_evidence.py",
            str(self.plan_path),
            "--root", str(self.root),
        ]
        sys.argv = args
        rc = main()
        try:
            self.assertEqual(rc, 0)
        except SystemExit as e:
            self.assertEqual(e.code, 0)


if __name__ == "__main__":
    unittest.main()