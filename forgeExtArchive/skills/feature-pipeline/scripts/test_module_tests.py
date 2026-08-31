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

    def setUp(self):
        self._saved = {"run_suite": mt.run_suite, "modules_from_diff": mt.modules_from_diff,
                       "_module_has_tests": mt._module_has_tests}

    def tearDown(self):
        for name, fn in getattr(self, "_saved", {}).items():
            setattr(mt, name, fn)

    def _patch_suite(self, results, no_results=None):
        mt.run_suite = lambda root, modules, bs, timeout: (dict(results), list(no_results or []))

    def _patch_suite_by_module(self, mapping):
        """mapping: module -> (results_dict, no_results_list). Роутит run_suite по модулям прогона."""
        def _fake(root, modules, bs, timeout):
            res, no = {}, []
            for m in modules:
                r, n = mapping.get(m, ({}, [m]))
                res.update(r)
                no.extend(n)
            return res, no
        mt.run_suite = _fake

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

    def _baseline_green(self, d, modules=("m",)):
        bl = Path(d) / "baseline.json"
        bl.write_text(json.dumps({"modules": list(modules), "build_system": "gradle",
                                  "tests": {"A#x": "passed"}}), encoding="utf-8")
        return bl

    def test_compare_blocks_on_unbaselined_affected_failure(self):
        """Тронут второй сервис (diff), в baseline его нет, его тест красный → fail-closed."""
        with tempfile.TemporaryDirectory() as d:
            bl = self._baseline_green(d)
            self._patch_suite_by_module({"m": ({"A#x": "passed"}, []),
                                         "svc-b": ({"B#y": "failed"}, [])})
            mt.modules_from_diff = lambda root, base: ["m", "svc-b"]
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=True, baseline=str(bl), from_diff="HEAD")
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = mt.cmd_compare(args)
            self.assertEqual(rc, 2)
            out = json.loads(buf.getvalue().strip().splitlines()[-1])
            self.assertEqual(out["unbaselined_failures"], ["B#y"])
            self.assertEqual(out["affected_unbaselined_modules"], ["svc-b"])

    def test_compare_blocks_on_untested_affected_module(self):
        """Тронут второй сервис, у него ЕСТЬ тесты, но прогон не дал результатов → fail-closed."""
        with tempfile.TemporaryDirectory() as d:
            bl = self._baseline_green(d)
            (Path(d) / "svc-b" / "src" / "test").mkdir(parents=True)
            self._patch_suite_by_module({"m": ({"A#x": "passed"}, []),
                                         "svc-b": ({}, ["svc-b"])})
            mt.modules_from_diff = lambda root, base: ["m", "svc-b"]
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=False, baseline=str(bl), from_diff="HEAD")
            self.assertEqual(mt.cmd_compare(args), 2)

    def test_compare_ignores_unbaselined_module_without_tests(self):
        """Тронут модуль без src/test и без результатов — регрессировать нечего, не блок."""
        with tempfile.TemporaryDirectory() as d:
            bl = self._baseline_green(d)
            self._patch_suite_by_module({"m": ({"A#x": "passed"}, []),
                                         "libs-proto": ({}, ["libs-proto"])})
            mt.modules_from_diff = lambda root, base: ["m", "libs-proto"]
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=False, baseline=str(bl), from_diff="HEAD")
            self.assertEqual(mt.cmd_compare(args), 0)

    def test_compare_passes_when_unbaselined_affected_green(self):
        """Тронут второй сервис, но его тесты зелёные → проходит."""
        with tempfile.TemporaryDirectory() as d:
            bl = self._baseline_green(d)
            self._patch_suite_by_module({"m": ({"A#x": "passed"}, []),
                                         "svc-b": ({"B#y": "passed"}, [])})
            mt.modules_from_diff = lambda root, base: ["m", "svc-b"]
            args = self._args(root=d, modules=None, build_system="gradle", timeout=60,
                              json=False, baseline=str(bl), from_diff="HEAD")
            self.assertEqual(mt.cmd_compare(args), 0)


class TestGuard(unittest.TestCase):
    """guard — self-contained регресс через git stash (lite/minor). git и прогон замоканы."""

    def setUp(self):
        self._saved = {k: getattr(mt, k) for k in
                       ("run_suite", "modules_from_diff", "_module_has_tests", "_git")}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(mt, k, v)

    def _args(self, **kw):
        ns = type("NS", (), {})()
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _git_fake(self, dirty=True, push_rc=0, pop_rc=0):
        import subprocess as sp

        def fake(root, *args, timeout=60):
            if args[0] == "status":
                return sp.CompletedProcess(args, 0, "M x\n" if dirty else "", "")
            if args[:2] == ("stash", "push"):
                return sp.CompletedProcess(args, push_rc, "", "")
            if args[:2] == ("stash", "pop"):
                return sp.CompletedProcess(args, pop_rc, "", "" if pop_rc == 0 else "conflict")
            return sp.CompletedProcess(args, 0, "", "")
        return fake

    def _suite_seq(self, *returns):
        it = iter(returns)
        mt.run_suite = lambda root, modules, bs, timeout: next(it)

    def _run(self, **over):
        mt.modules_from_diff = over.get("mods_fn", lambda root, base: ["svc-a"])
        mt._git = over.get("git", self._git_fake())
        mt._module_has_tests = over.get("has_tests", lambda root, m: True)
        if "suite" in over:
            self._suite_seq(*over["suite"])
        args = self._args(root=".", base="HEAD", build_system="gradle", timeout=60, json=False)
        return mt.cmd_guard(args)

    def test_no_modules_passes(self):
        self.assertEqual(self._run(mods_fn=lambda root, base: []), 0)

    def test_clean_tree_passes(self):
        self.assertEqual(self._run(git=self._git_fake(dirty=False)), 0)

    def test_stash_push_fails_failclosed(self):
        self.assertEqual(self._run(git=self._git_fake(push_rc=1)), 2)

    def test_regression_blocks(self):
        self.assertEqual(self._run(suite=[({"A#x": "passed"}, []), ({"A#x": "failed"}, [])]), 2)

    def test_no_regression_passes(self):
        self.assertEqual(self._run(suite=[({"A#x": "passed"}, []), ({"A#x": "passed"}, [])]), 0)

    def test_untested_affected_blocks(self):
        # затронутый модуль с тестами не дал результатов в текущем прогоне → fail-closed
        self.assertEqual(self._run(suite=[({"A#x": "passed"}, []), ({}, ["svc-a"])]), 2)

    def test_stash_pop_failure_failclosed(self):
        self.assertEqual(self._run(git=self._git_fake(pop_rc=1),
                                   suite=[({"A#x": "passed"}, []), ({"A#x": "passed"}, [])]), 2)


if __name__ == "__main__":
    unittest.main()
