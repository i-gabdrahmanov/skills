#!/usr/bin/env python3
"""Тесты для check_delivery.py — gate доставки (stacked-PR).

Запуск:
    python3 test_check_delivery.py
    python3 -m pytest test_check_delivery.py -v

Проверяет:
    1. Все deliver-шаги completed → pass
    2. Шаг в другом статусе → fail
    3. Отсутствует шаг → fail
    4. Пустой task-plan → pass
    5. bitbucket.enabled=false → skip
    6. Фильтр по префиксу
    7. --json вывод
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_delivery import main


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class TestMain(unittest.TestCase):
    """Тесты main() — интеграция CLI."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

        # task-plan
        self.plan = {
            "tasks": [
                {"id": "T1", "title": "Первая задача"},
                {"id": "T2", "title": "Вторая задача"},
            ],
        }
        self.plan_path = self.root / "task-plan.json"
        _write_json(self.plan_path, self.plan)

        # manifest
        self.manifest = {
            "steps": [
                {"id": "07-deliver-T1", "status": "completed"},
                {"id": "07-deliver-T2", "status": "completed"},
            ],
        }
        self.manifest_path = self.root / "manifest.json"
        _write_json(self.manifest_path, self.manifest)

        # pipeline-config (bitbucket enabled by default)
        self.pipeline_cfg = {"bitbucket": {"enabled": True}}
        self.pipeline_cfg_path = self.root / "pipeline.json"
        _write_json(self.pipeline_cfg_path, self.pipeline_cfg)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_main(self, extra_args: list[str] | None = None) -> int:
        args = [
            "check_delivery.py",
            str(self.plan_path),
            "--manifest", str(self.manifest_path),
            "--pipeline-config", str(self.pipeline_cfg_path),
            *(extra_args or []),
        ]
        sys.argv = args
        try:
            return main()
        except SystemExit as e:
            return e.code or 0

    def test_pass_all_delivered(self):
        """Все задачи доставлены → pass."""
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_fail_step_not_completed(self):
        """Шаг не completed → fail."""
        self.manifest["steps"][1]["status"] = "in_progress"
        _write_json(self.manifest_path, self.manifest)
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_fail_missing_step(self):
        """Шаг отсутствует в manifest → fail."""
        self.manifest["steps"] = [{"id": "07-deliver-T1", "status": "completed"}]
        _write_json(self.manifest_path, self.manifest)
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_pass_bitbucket_disabled(self):
        """bitbucket.enabled=false → skip (rc=0)."""
        self.pipeline_cfg["bitbucket"]["enabled"] = False
        _write_json(self.pipeline_cfg_path, self.pipeline_cfg)
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_pass_no_bitbucket_config(self):
        """Нет pipeline-config → проверка выполняется (rc считает как есть)."""
        rc = self._run_main(["--pipeline-config", "/nonexistent/path.json"])
        self.assertEqual(rc, 0)  # T1+T2 completed → pass

    def test_pass_no_bitbucket_section(self):
        """pipeline.json есть, но нет bitbucket → не skip, выполняется проверка."""
        self.pipeline_cfg = {}
        _write_json(self.pipeline_cfg_path, self.pipeline_cfg)
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_empty_tasks(self):
        """Пустой task-plan → pass."""
        _write_json(self.plan_path, {"tasks": []})
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_custom_prefix(self):
        """--prefix другой → проверка по другому префиксу."""
        self.manifest["steps"] = [
            {"id": "deliver-T1", "status": "completed"},
            {"id": "deliver-T2", "status": "completed"},
        ]
        _write_json(self.manifest_path, self.manifest)
        rc = self._run_main(["--prefix", "deliver-"])
        self.assertEqual(rc, 0)

    def test_json_output(self):
        """--json выдаёт валидный JSON."""
        rc = self._run_main(["--json"])
        self.assertEqual(rc, 0)

    def test_fail_one_task_json_output(self):
        """Одна задача не доставлена → JSON с ошибкой."""
        self.manifest["steps"] = [{"id": "07-deliver-T1", "status": "completed"}]
        _write_json(self.manifest_path, self.manifest)
        rc = self._run_main(["--json"])
        self.assertEqual(rc, 2)

    def test_task_without_id(self):
        """Задача без id игнорируется."""
        _write_json(self.plan_path, {"tasks": [{"title": "No ID"}]})
        rc = self._run_main()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()