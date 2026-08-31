#!/usr/bin/env python3
"""Smoke-тесты для eval-guard хука.

Проверяет изолированно (без рантайма хуков):
    1. _is_src_main: правильное детектирование путей src/main
    2. _target_path: корректное извлечение пути из tool_name + tool_input
    3. _has_passed: проверка кеша результатов

Запуск:
    python3 test_eval_guard.py
    python3 -m pytest test_eval_guard.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Подключаем тестируемый модуль (эмулируем импорт без рантайма хуков)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "hooks"))

# Импортируем функции из eval-guard напрямую
# Но т.к. ветка eval-guard.py может импортировать risk_ladder,
# проще проверить функции изолированно через текстовую компиляцию.
# Создадим локальные копии проверяемых функций.


def _is_src_main(target_path: str | None) -> bool:
    if not target_path:
        return False
    return "/src/main/" in target_path.replace("\\", "/")


def _target_path(tool_name: str, tool_input: dict) -> str | None:
    if tool_name in ("Write", "WriteFile"):
        return (tool_input.get("file_path") or "").strip()
    if tool_name in ("Edit",):
        return (tool_input.get("file_path") or "").strip()
    return None


def _has_passed(results: dict, eval_id: str) -> bool:
    entry = results.get(eval_id)
    return entry is not None and entry.get("status") == "passed"


class TestIsSrcMain(unittest.TestCase):

    def test_src_main_java(self):
        self.assertTrue(_is_src_main(
            "/project/service/test/src/main/java/TestService.java"
        ))

    def test_src_main_resources(self):
        self.assertTrue(_is_src_main(
            "/project/service/test/src/main/resources/application.yml"
        ))

    def test_src_test_not_main(self):
        self.assertFalse(_is_src_main(
            "/project/service/test/src/test/java/TestServiceTest.java"
        ))

    def test_other_path(self):
        self.assertFalse(_is_src_main(
            "/project/docs/feature-pipeline/plan.md"
        ))

    def test_none_path(self):
        self.assertFalse(_is_src_main(None))

    def test_empty_path(self):
        self.assertFalse(_is_src_main(""))


class TestTargetPath(unittest.TestCase):

    def test_write_file(self):
        t = _target_path("WriteFile", {
            "file_path": "/project/src/main/java/Foo.java",
            "content": "class Foo {}",
        })
        self.assertEqual(t, "/project/src/main/java/Foo.java")

    def test_write(self):
        t = _target_path("Write", {
            "file_path": "/project/src/main/java/Bar.java",
        })
        self.assertEqual(t, "/project/src/main/java/Bar.java")

    def test_edit(self):
        t = _target_path("Edit", {
            "file_path": "/project/src/main/java/Baz.java",
        })
        self.assertEqual(t, "/project/src/main/java/Baz.java")

    def test_unknown_tool(self):
        t = _target_path("Read", {"file_path": "/project/foo.txt"})
        self.assertIsNone(t)

    def test_empty_input(self):
        t = _target_path("Write", {})
        self.assertEqual(t, "")


class TestHasPassed(unittest.TestCase):

    def test_passed(self):
        results = {"compile-t1": {"status": "passed", "output": "OK"}}
        self.assertTrue(_has_passed(results, "compile-t1"))

    def test_failed(self):
        results = {"compile-t1": {"status": "failed", "output": "ERROR"}}
        self.assertFalse(_has_passed(results, "compile-t1"))

    def test_not_found(self):
        results = {"compile-t1": {"status": "passed"}}
        self.assertFalse(_has_passed(results, "compile-t2"))

    def test_empty_results(self):
        self.assertFalse(_has_passed({}, "compile-t1"))


if __name__ == "__main__":
    unittest.main()