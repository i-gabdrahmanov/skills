#!/usr/bin/env python3
"""test_windows_gradlew_wrapper.py — регресс: "./gradlew" не запускается на Windows.

cmd.exe (куда на Windows subprocess(shell=True) уходит ВСЕГДА, вне зависимости от
оболочки, из которой запущен сам python) не умеет ни в shebang, ни в "./" без
расширения — нужен gradlew.bat. Пять скриптов feature-pipeline выбирают обёртку по
sys.platform; этот тест фиксирует выбор для обеих платформ как контракт, чтобы
рефакторинг случайно не вернул жёсткий "./gradlew".

_GRADLEW в build_evals_from_design.py/check_tests_red.py/run_pending_evals.py/
init_pipeline_config.py — модульная константа (вычисляется при импорте) → грузим
модуль СВЕЖИМ importlib.util с пропатченным sys.platform. В module_tests.py та же
логика — локальная переменная внутри функции → патчим sys.platform на месте, без
перезагрузки модуля.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent


def _load_with_platform(name: str, platform: str):
    """Свежая загрузка модуля с пропатченным sys.platform на время exec_module —
    модульные константы вида _GRADLEW вычисляются при импорте, reload не нужен,
    нужна ИЗОЛИРОВАННАЯ загрузка (не sys.modules-кэш), чтобы не тащить старое значение."""
    spec = importlib.util.spec_from_file_location(f"{name}_{platform}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(sys, "platform", platform):
        spec.loader.exec_module(module)
    return module


class TestGradlewWrapperSelection(unittest.TestCase):
    MODULE_NAMES = [
        "build_evals_from_design",
        "check_tests_red",
        "run_pending_evals",
        "init_pipeline_config",
    ]

    def test_posix_uses_dot_slash_gradlew(self):
        for name in self.MODULE_NAMES:
            with self.subTest(module=name):
                m = _load_with_platform(name, "linux")
                self.assertEqual(m._GRADLEW, "./gradlew")

    def test_windows_uses_gradlew_bat(self):
        for name in self.MODULE_NAMES:
            with self.subTest(module=name):
                m = _load_with_platform(name, "win32")
                self.assertEqual(m._GRADLEW, "gradlew.bat")

    def test_windows_gradle_commands_use_bat_suffix(self):
        m = _load_with_platform("build_evals_from_design", "win32")
        self.assertEqual(m.GRADLE_COMPILE, "gradlew.bat compileJava")
        self.assertEqual(m.GRADLE_TEST, "gradlew.bat test")


class TestModuleTestsGradlewSelection(unittest.TestCase):
    """module_tests.py вычисляет обёртку локально внутри _module_test_cmd — патчим
    sys.platform без перезагрузки модуля (достаточно, т.к. значение не кэшируется
    на уровне модуля)."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("module_tests", SCRIPTS / "module_tests.py")
        cls.mt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mt)

    def test_posix(self):
        with mock.patch.object(self.mt.sys, "platform", "darwin"):
            cmd = self.mt._module_test_cmd("service-taskservice", "gradle")
        self.assertEqual(cmd[0], "./gradlew")

    def test_windows(self):
        with mock.patch.object(self.mt.sys, "platform", "win32"):
            cmd = self.mt._module_test_cmd("service-taskservice", "gradle")
        self.assertEqual(cmd[0], "gradlew.bat")

    def test_maven_unaffected_by_platform(self):
        # maven-команда не завязана на gradlew — платформа не должна на неё влиять.
        with mock.patch.object(self.mt.sys, "platform", "win32"):
            cmd = self.mt._module_test_cmd("service-taskservice", "maven")
        self.assertEqual(cmd[0], "mvn")


if __name__ == "__main__":
    unittest.main()
