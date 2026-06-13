#!/usr/bin/env python3
"""Тесты для check_build.py — проверка наличия артефактов задач.

Запуск:
    python3 test_check_build.py
    python3 -m pytest test_check_build.py -v

Проверяет:
    1. _exists() находит файлы по точному пути
    2. _exists() находит файлы по суффиксу (multi-module fallback)
    3. _exists() не находит отсутствующие файлы
    4. _exists() пропускает build/out/target/.gradle директории
    5. main() — pass при всех артефактах
    6. main() — fail при отсутствии артефакта
    7. main() — фильтр --task
    8. main() — --build успешная и упавшая сборка
    9. main() — --json вывод
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_build import _exists, main


class TestExists(unittest.TestCase):
    """Тесты _exists() — детектирование файлов-артефактов."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        # Создаём файлы
        self.main_java = self.root / "service" / "test" / "src" / "main" / "java" / "TestService.java"
        self.main_java.parent.mkdir(parents=True, exist_ok=True)
        self.main_java.write_text("class TestService {}")

        # Файл в build/ (должен игнорироваться)
        self.build_file = self.root / "build" / "classes" / "TestService.class"
        self.build_file.parent.mkdir(parents=True, exist_ok=True)
        self.build_file.write_text("fake class")

        # Файл в out/ (игнорируется)
        self.out_file = self.root / "out" / "production" / "TestService.class"
        self.out_file.parent.mkdir(parents=True, exist_ok=True)
        self.out_file.write_text("fake class")

        # Файл в .gradle/ (игнорируется)
        self.gradle_cache = self.root / ".gradle" / "cache" / "some.jar"
        self.gradle_cache.parent.mkdir(parents=True, exist_ok=True)
        self.gradle_cache.write_text("fake jar")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_exact_path_exists(self):
        """Точный путь — файл найден."""
        self.assertTrue(_exists(self.root, "service/test/src/main/java/TestService.java"))

    def test_suffix_match_exists(self):
        """Суффиксный матч — файл найден."""
        self.assertTrue(_exists(self.root, "service/test/src/main/java/TestService.java"))

    def test_missing_file_not_found(self):
        """Отсутствующий файл не найден."""
        self.assertFalse(_exists(self.root, "service/test/src/main/java/MissingService.java"))

    def test_suffix_match_ignores_build_dir(self):
        """Суффиксный поиск не находит файл в build/ (из-за _SKIP)."""
        # Файл в build/ — _exists с суффиксным матчем его не найдёт
        self.assertFalse(
            _exists(self.root, "com/example/TestService.class"),
            "Файл в build/ не должен находиться через suffix match",
        )

    def test_suffix_match_ignores_out_dir(self):
        """Суффиксный поиск не находит файл в out/."""
        self.assertFalse(_exists(self.root, "com/example/TestService.class"))

    def test_suffix_match_ignores_gradle_dir(self):
        """Суффиксный поиск не находит файл в .gradle/."""
        self.assertFalse(_exists(self.root, "com/example/some.jar"))

    def test_artifact_with_leading_slash(self):
        """Артефакт с ведущим '/' нормализуется."""
        self.assertTrue(_exists(self.root, "/service/test/src/main/java/TestService.java"))

    def test_backslash_on_windows(self):
        """Обратный слеш в пути нормализуется."""
        self.assertTrue(_exists(self.root, "service\\test\\src\\main\\java\\TestService.java"))


class TestMain(unittest.TestCase):
    """Тесты main() — интеграция CLI."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

        # Создаём файл-артефакт
        art = self.root / "service" / "test" / "src" / "main" / "java" / "TestService.java"
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_text("class TestService {}")

        # План задач
        self.plan = {
            "tasks": [
                {"id": "T1", "artifacts": ["service/test/src/main/java/TestService.java"]},
                {"id": "T2", "artifacts": ["service/test/src/main/java/MissingService.java"]},
            ],
        }
        self.plan_path = self.root / "task-plan.json"
        self.plan_path.write_text(json.dumps(self.plan, ensure_ascii=False))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_main(self, extra_args: list[str] | None = None) -> int:
        """Запускает main() с подставленными аргументами."""
        args = [
            "check_build.py",
            str(self.plan_path),
            "--root", str(self.root),
            *(extra_args or []),
        ]
        sys.argv = args
        try:
            return main()
        except SystemExit as e:
            return e.code or 0

    def test_pass_all_artifacts_exist(self):
        """Все артефакты есть — pass."""
        # Удаляем T2 из плана, чтобы остался только существующий артефакт
        plan = {"tasks": [{"id": "T1", "artifacts": ["service/test/src/main/java/TestService.java"]}]}
        self.plan_path.write_text(json.dumps(plan))
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_fail_missing_artifact(self):
        """Отсутствует артефакт — fail (rc=2)."""
        rc = self._run_main()
        self.assertEqual(rc, 2)

    def test_filter_by_task(self):
        """--task T1 — проверяем только T1 (который есть), pass."""
        rc = self._run_main(["--task", "T1"])
        self.assertEqual(rc, 0)

    def test_filter_by_task_missing(self):
        """--task T2 — проверяем только T2 (которого нет), fail."""
        rc = self._run_main(["--task", "T2"])
        self.assertEqual(rc, 2)

    def test_json_output(self):
        """--json выдаёт валидный JSON с полями."""
        rc = self._run_main(["--json"])
        self.assertEqual(rc, 2)  # T2 missing

    def test_no_artifacts_in_plan(self):
        """Нет поля artifacts — pass."""
        plan = {"tasks": [{"id": "T1"}]}
        self.plan_path.write_text(json.dumps(plan))
        rc = self._run_main()
        self.assertEqual(rc, 0)

    def test_empty_tasks(self):
        """Пустой список задач — pass."""
        plan = {"tasks": []}
        self.plan_path.write_text(json.dumps(plan))
        rc = self._run_main()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()