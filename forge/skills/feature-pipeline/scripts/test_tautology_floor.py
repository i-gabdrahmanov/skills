#!/usr/bin/env python3
"""Floor тавтологичных тестов в coverage-judge (run_judge._tautology_floor).

Раньше check_tautological_tests.py жил только guidance'ом в брифе 05-verify — модель могла
его не запустить, и пустые/тавтологичные @Test «покрывали» код. Теперь floor вшит в
check_coverage: закрытие 05-tests идёт через record_gate с `coverage --recheck`, значит
floor enforced. Дефолт ON; выключение — явное quality.tautology_check=false.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_judge  # noqa: E402

TAUT_TEST = """package t;
class FooTest {
    @Test void nothing() { }
    @Test void taut() { assertTrue(true); }
}
"""

GOOD_TEST = """package t;
class FooTest {
    @Test void real() { assertEquals(4, svc.sum(2, 2)); }
}
"""


class TautologyFloor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.tf = self.proj / "src/test/java/t/FooTest.java"
        self.tf.parent.mkdir(parents=True)
        self.tf.write_text(GOOD_TEST, encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=str(self.proj), capture_output=True, timeout=30)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tautological_test_blocks(self):
        self.tf.write_text(TAUT_TEST, encoding="utf-8")
        checks, blocking, _ = run_judge._tautology_floor(self.proj, {})
        self.assertTrue(blocking, checks)
        self.assertEqual(checks[0]["status"], "FAIL")

    def test_real_test_passes(self):
        self.tf.write_text(GOOD_TEST + "// touch\n", encoding="utf-8")
        checks, blocking, _ = run_judge._tautology_floor(self.proj, {})
        self.assertFalse(blocking, checks)

    def test_default_is_on(self):
        # пустой quality-конфиг = floor работает (дефолт ON)
        self.tf.write_text(TAUT_TEST, encoding="utf-8")
        _, blocking, _ = run_judge._tautology_floor(self.proj, {"coverage_threshold": 0.8})
        self.assertTrue(blocking)

    def test_explicit_false_disables(self):
        self.tf.write_text(TAUT_TEST, encoding="utf-8")
        checks, blocking, _ = run_judge._tautology_floor(self.proj, {"tautology_check": False})
        self.assertFalse(blocking)
        self.assertEqual(checks[0]["status"], "SKIP")

    def test_no_git_is_pass(self):
        # вне git detector видит 0 файлов — floor не валит (нечего проверять)
        with tempfile.TemporaryDirectory() as d:
            _, blocking, _ = run_judge._tautology_floor(Path(d), {})
            self.assertFalse(blocking)


if __name__ == "__main__":
    unittest.main(verbosity=2)
