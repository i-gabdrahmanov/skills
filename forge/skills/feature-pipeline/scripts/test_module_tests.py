#!/usr/bin/env python3
"""Тесты module_tests.py — baseline зелёного и детекция регрессий (C/D)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import module_tests as mt


class TestModuleDerivation(unittest.TestCase):
    def test_gradle_path(self):
        self.assertEqual(mt.gradle_module_path("service-taskservice"), ":service:taskservice")
        self.assertEqual(mt.gradle_module_path(":service:taskservice"), ":service:taskservice")
        self.assertEqual(mt.gradle_module_path("service:taskservice"), ":service:taskservice")
        self.assertEqual(mt.gradle_module_path("taskservice"), ":taskservice")

    def test_module_from_path(self):
        self.assertEqual(
            mt.module_from_path("service/taskservice/src/main/java/x/Foo.java"),
            "service-taskservice")
        self.assertEqual(
            mt.module_from_path("database/src/test/java/x/BarTest.java"), "database")
        self.assertIsNone(mt.module_from_path("src/main/java/x/Foo.java"))

    def test_modules_from_taskplan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "task-plan.json"
            p.write_text(json.dumps({"tasks": [
                {"id": "T1", "modules": ["service-taskservice", "utils-web"]},
                {"id": "T2", "module": "database"},
                {"id": "T3"},
            ]}), encoding="utf-8")
            self.assertEqual(mt.modules_from_taskplan(p),
                             ["database", "service-taskservice", "utils-web"])


class TestParseJunit(unittest.TestCase):
    def _write(self, d, name, body):
        (d / name).write_text(body, encoding="utf-8")

    def test_parse_pass_fail_skip(self):
        with tempfile.TemporaryDirectory() as dd:
            d = Path(dd)
            self._write(d, "TEST-x.FooTest.xml", """<?xml version="1.0"?>
<testsuite name="x.FooTest">
  <testcase classname="x.FooTest" name="ok"/>
  <testcase classname="x.FooTest" name="bad"><failure>boom</failure></testcase>
  <testcase classname="x.FooTest" name="err"><error>e</error></testcase>
  <testcase classname="x.FooTest" name="skip"><skipped/></testcase>
</testsuite>""")
            res = mt.parse_junit_dir(d)
            self.assertEqual(res["x.FooTest#ok"], "passed")
            self.assertEqual(res["x.FooTest#bad"], "failed")
            self.assertEqual(res["x.FooTest#err"], "failed")
            self.assertEqual(res["x.FooTest#skip"], "skipped")

    def test_parse_missing_dir(self):
        self.assertEqual(mt.parse_junit_dir(Path("/no/such/dir")), {})


class TestDiff(unittest.TestCase):
    def test_regression_and_kinds(self):
        base = {"A#x": "passed", "B#y": "passed", "C#z": "failed"}
        cur = {"A#x": "failed",   # регрессия
               "B#y": "passed",   # стабильно зелёный
               "C#z": "failed",   # pre-existing
               "D#w": "failed"}   # новый красный
        d = mt.diff_baseline(base, cur)
        self.assertEqual(d["regressions"], ["A#x"])
        self.assertEqual(d["pre_existing_failures"], ["C#z"])
        self.assertEqual(d["new_failures"], ["D#w"])
        self.assertEqual(d["fixed"], [])

    def test_fixed(self):
        d = mt.diff_baseline({"A#x": "failed"}, {"A#x": "passed"})
        self.assertEqual(d["fixed"], ["A#x"])
        self.assertEqual(d["regressions"], [])


class TestSnapshotCompare(unittest.TestCase):
    """snapshot/compare с монкипатчем прогона (без реального gradle)."""

    def tearDown(self):
        if hasattr(self, "_orig"):
            mt.run_suite = self._orig

    def _patch_suite(self, results, no_results=None):
        self._orig = mt.run_suite
        mt.run_suite = lambda root, modules, bs, timeout: (dict(results), list(no_results or []))

    def _args(self, **kw):
        ns = type("NS", (), {})()
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_snapshot_writes_baseline(self):
        self._patch_suite({"A#x": "passed", "B#y": "failed"})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "baseline.json"
            args = self._args(root=d, modules="service-taskservice", from_taskplan=None,
                              from_diff=None, build_system="gradle", timeout=60,
                              json=False, out=str(out))
            self.assertEqual(mt.cmd_snapshot(args), 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["tests"]["A#x"], "passed")
            self.assertEqual(data["modules"], ["service-taskservice"])

    def test_compare_blocks_on_regression(self):
        with tempfile.TemporaryDirectory() as d:
            bl = Path(d) / "baseline.json"
            bl.write_text(json.dumps({"modules": ["m"], "build_system": "gradle",
                                      "tests": {"A#x": "passed", "C#z": "failed"}}), encoding="utf-8")
            # теперь A#x падает (регрессия), C#z всё ещё красный (pre-existing — не блок)
            self._patch_suite({"A#x": "failed", "C#z": "failed"})
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=False, baseline=str(bl), from_diff=None)
            self.assertEqual(mt.cmd_compare(args), 2)

    def test_compare_passes_when_no_regression(self):
        with tempfile.TemporaryDirectory() as d:
            bl = Path(d) / "baseline.json"
            bl.write_text(json.dumps({"modules": ["m"], "build_system": "gradle",
                                      "tests": {"A#x": "passed", "C#z": "failed"}}), encoding="utf-8")
            self._patch_suite({"A#x": "passed", "C#z": "failed"})  # без регрессий
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=False, baseline=str(bl), from_diff=None)
            self.assertEqual(mt.cmd_compare(args), 0)

    def test_compare_failclosed_missing_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=False, baseline=str(Path(d) / "nope.json"), from_diff=None)
            self.assertEqual(mt.cmd_compare(args), 2)

    def test_compare_failclosed_module_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            bl = Path(d) / "baseline.json"
            bl.write_text(json.dumps({"modules": ["m"], "build_system": "gradle",
                                      "tests": {"A#x": "passed"}}), encoding="utf-8")
            # модуль 'm' не дал результатов — нельзя подтвердить зелёное → fail-closed
            self._patch_suite({}, no_results=["m"])
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=False, baseline=str(bl), from_diff=None)
            self.assertEqual(mt.cmd_compare(args), 2)


if __name__ == "__main__":
    unittest.main()
