"""test_e2e_plan.py — гейт e2e: план по сценариям ui-spec, прогон playwright, разбор JUnit (задача 5.5, спека §14.8, §11.2, INV-C36, B19, F15, QG-N17).

Пинит: имена файлов детерминированы по id сценария (`ui-spec#2` → `ui-spec-2.e2e.spec.ts`, без каталогов и странных символов, разные id не сливаются); контекст REMOVED → `skip` с
причиной и НЕТ в списке файлов (N17); `hasE2e: false` и «все сценарии удаляются» → гейт `skipped` с причиной, сказанной один раз; каталог тестов — `testDir` из playwright.config, иначе
`e2e/`, и никогда не внутри outputDir (там пишет только ui-build); разбор JUnit (passed / failed / error / skipped, вложенные наборы, строка из стека, обрезка длинного сообщения,
негодный XML); падения → дефекты `zone: e2e` со стабильным id; прогон: нет файлов / нет `@playwright/test` / не установлен / нет npx → `skipped` с причиной и без запуска, код 0 и отчёт → `pass`,
падения → `fail` с дефектами, прогон без отчёта и по таймауту → `fail` (не «прошёл»), чистый отчёт при коде ≠ 0 → `fail`; настоящий подпроцесс через поддельный `npx` в PATH; запись итерации берёт
статус и падения из `e2e-summary.json`: слова агента проигрывают, «pass» без прогона — `skipped`, падение блокирует clean, решение «ложное срабатывание» снимает блокировку; инструмент в реестре.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import tools as T  # noqa: E402
from daiver.verify import e2e as E  # noqa: E402
from test_verify_state import World, SKIPPED  # noqa: E402

JUNIT_FAIL = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites id="" name="" tests="3" failures="1" skipped="1" errors="0" time="1.2">
<testsuite name="ui-spec-1.e2e.spec.ts" timestamp="2026-10-26T10:00:00.000Z" hostname="x" tests="3" failures="1" skipped="1" errors="0" time="1.1">
<testcase name="заголовок виден" classname="ui-spec-1.e2e.spec.ts" time="0.4"></testcase>
<testcase name="кнопка отправляет" classname="ui-spec-1.e2e.spec.ts" time="0.5"><failure message="expect(locator).toHaveText(expected)" type="FAILURE"><![CDATA[Error: expect(locator).toHaveText
    at e2e/ui-spec-1.e2e.spec.ts:14:30]]></failure></testcase>
<testcase name="отложено" classname="ui-spec-1.e2e.spec.ts" time="0"><skipped/></testcase>
</testsuite>
</testsuites>"""
JUNIT_PASS = """<testsuites><testsuite name="ui-spec-1.e2e.spec.ts" tests="1" failures="0"><testcase name="ok" classname="ui-spec-1.e2e.spec.ts"/></testsuite></testsuites>"""

SPEC = """# Спека

## Сценарии

#### Scenario: заголовок виден
Given экран открыт
Then виден «Заголовок»

#### Scenario: кнопка отправляет
Given форма заполнена
When нажата кнопка
Then уходит запрос

## REMOVED Requirements

### Requirement: старая кнопка

#### Scenario: старая кнопка пропала
Given экран открыт
Then кнопки нет
"""


def sc(i, **kw):
    return dict({"id": i, "title": f"t-{i}", "body": f"b-{i}", "context": [], "line": 3, "file": "ui-spec.md"}, **kw)


class Names(unittest.TestCase):
    def test_file_name_follows_the_scenario_id(self):
        self.assertEqual(E.file_name("ui-spec#2"), "ui-spec-2.e2e.spec.ts")
        self.assertEqual(E.file_name("promo-spec#1"), "promo-spec-1.e2e.spec.ts")
        self.assertEqual(E.file_name("ui-spec#2"), E.file_name("ui-spec#2"))
        self.assertEqual(E.file_name("спека#3"), "спека-3.e2e.spec.ts", "кириллица в имени остаётся")

    def test_a_name_never_carries_directories_or_odd_characters(self):
        for bad in ("a/b#1", "..\\x#1", "a b:c#1", "../../etc#1", "x\n#1"):
            n = E.file_name(bad)
            self.assertNotIn("/", n)
            self.assertNotIn("\\", n)
            self.assertTrue(n.endswith(E.SUFFIX) and not n.startswith("."), n)
            self.assertEqual(Path(n).name, n)


class Plan(World):
    def plan(self, **spec):
        base = {"e2e": [], "removedScenarios": [], "hasE2e": False}
        base.update(spec)
        return E.plan(self.root, self.TB.TRACK, base)

    def test_scenarios_become_files_in_a_stable_order(self):
        p = self.plan(e2e=[sc("ui-spec#2"), sc("ui-spec#1")], hasE2e=True)
        self.assertEqual([s["id"] for s in p["scenarios"]], ["ui-spec#1", "ui-spec#2"])
        self.assertEqual(p["files"], ["e2e/ui-spec-1.e2e.spec.ts", "e2e/ui-spec-2.e2e.spec.ts"])
        self.assertEqual(p["dir"], "e2e")
        self.assertNotIn("gate", p)
        self.assertIn("e2e/", p["nextStep"])
        self.assertEqual(p, self.plan(e2e=[sc("ui-spec#1"), sc("ui-spec#2")], hasE2e=True), "порядок входа на план не влияет")

    def test_removed_behaviour_is_skipped_with_a_reason_and_has_no_file_to_write(self):
        p = self.plan(e2e=[sc("ui-spec#1")], removedScenarios=[sc("ui-spec#2", context=["REMOVED Requirements"])], hasE2e=True)
        by = {s["id"]: s for s in p["scenarios"]}
        self.assertIn("REMOVED", by["ui-spec#2"]["skip"])
        self.assertNotIn("skip", by["ui-spec#1"])
        self.assertEqual(p["files"], ["e2e/ui-spec-1.e2e.spec.ts"], "теста на удаляемое поведение нет в списке файлов (N17)")
        self.assertEqual([s["id"] for s in p["skipped"]], ["ui-spec#2"])
        self.assertIn("скипнут", p["nextStep"].lower().replace("скипнутые", "скипнут"))

    def test_no_scenarios_means_a_skipped_gate_with_the_reason(self):
        p = self.plan()
        self.assertEqual(p["gate"]["status"], "skipped")
        self.assertIn("hasE2e: false", p["gate"]["reason"])
        self.assertEqual((p["files"], p["scenarios"]), ([], []))
        self.assertIn("ОДИН раз", p["nextStep"])

    def test_only_removed_scenarios_also_skip_the_gate(self):
        p = self.plan(removedScenarios=[sc("ui-spec#1", context=["REMOVED Requirements"])])
        self.assertEqual(p["gate"]["status"], "skipped")
        self.assertIn("REMOVED", p["gate"]["reason"])
        self.assertEqual(p["files"], [])

    def test_names_that_collapse_to_one_file_stay_distinct(self):
        p = self.plan(e2e=[sc("a/b#1"), sc("a_b#1")], hasE2e=True)
        self.assertEqual(len(set(p["files"])), 2, p["files"])

    def test_the_test_dir_comes_from_the_playwright_config(self):
        (self.root / "playwright.config.ts").write_text("export default defineConfig({\n  testDir: './tests/e2e',\n});\n", encoding="utf-8")
        self.assertEqual(self.plan(e2e=[sc("s#1")], hasE2e=True)["files"], ["tests/e2e/s-1.e2e.spec.ts"])

    def test_a_test_dir_inside_the_output_dir_is_refused_by_name(self):
        out = "src/modules"
        (self.root / "playwright.config.ts").write_text(f"export default {{ testDir: '{out}/tests' }}", encoding="utf-8")
        p = self.plan(e2e=[sc("s#1")], hasE2e=True)
        self.assertEqual(p["dir"], "e2e")
        self.assertTrue(any("outputDir" in w and out in w for w in p["warnings"]), p.get("warnings"))
        (self.root / "playwright.config.ts").write_text("export default { testDir: '.' }", encoding="utf-8")
        self.assertEqual(self.plan(e2e=[sc("s#1")], hasE2e=True)["dir"], "e2e", "весь репо = внутри outputDir")

    def test_a_test_dir_outside_the_repo_or_not_a_literal_falls_back(self):
        (self.root / "playwright.config.ts").write_text("export default { testDir: '../outside' }", encoding="utf-8")
        p = self.plan(e2e=[sc("s#1")], hasE2e=True)
        self.assertEqual(p["dir"], "e2e")
        self.assertTrue(p.get("warnings"))
        (self.root / "playwright.config.ts").write_text("export default { testDir: path.join(__dirname, 'x') }", encoding="utf-8")
        p = self.plan(e2e=[sc("s#1")], hasE2e=True)
        self.assertEqual(p["dir"], "e2e")
        self.assertNotIn("warnings", p, "не литерал — молча по умолчанию: гадать нечего")


class Junit(unittest.TestCase):
    def test_passed_failed_skipped_and_the_line_from_the_stack(self):
        got = E.parse_junit(JUNIT_FAIL)
        self.assertEqual((got["tests"], got["passed"], got["failed"], got["skipped"]), (3, 1, 1, 1))
        bad = [c for c in got["cases"] if c["status"] == "failed"][0]
        self.assertEqual((bad["title"], bad["line"]), ("кнопка отправляет", 14))
        self.assertIn("toHaveText", bad["message"])

    def test_error_elements_count_as_failures_and_a_single_suite_root_is_read(self):
        got = E.parse_junit('<testsuite name="x.e2e.spec.ts"><testcase name="a"><error message="boom"/></testcase></testsuite>')
        self.assertEqual((got["tests"], got["failed"]), (1, 1))
        self.assertEqual(got["cases"][0]["message"], "boom")

    def test_a_long_message_is_clipped_and_whitespace_collapsed(self):
        got = E.parse_junit(f'<testsuite name="x"><testcase name="a"><failure message="{"слово " * 400}"/></testcase></testsuite>')
        m = got["cases"][0]["message"]
        self.assertLessEqual(len(m), E.MAX_MESSAGE)
        self.assertTrue(m.endswith("…"))

    def test_bad_xml_is_named_not_swallowed(self):
        with self.assertRaises(E.E2eError):
            E.parse_junit("<testsuites><testsuite>")

    def test_defects_have_stable_ids_zone_and_location_from_the_plan(self):
        planned = [{"id": "ui-spec#1", "file": "e2e/ui-spec-1.e2e.spec.ts"}]
        parsed = E.parse_junit(JUNIT_FAIL)
        a = E.defects_of(parsed, planned)
        b = E.defects_of(E.parse_junit(JUNIT_FAIL), planned)
        self.assertEqual(a, b, "id стабилен между прогонами")
        self.assertEqual(len(a), 1)
        d = a[0]
        self.assertEqual((d["zone"], d["source"], d["severity"], d["code"], d["rule"]), ("e2e", "e2e", "major", "E2E_FAIL", "ui-spec#1"))
        self.assertEqual(d["location"], {"file": "e2e/ui-spec-1.e2e.spec.ts", "line": 14})
        self.assertEqual(d["element"], "кнопка отправляет")
        self.assertTrue(d["id"].startswith("e2e-"))
        other = E.defects_of(parsed, [{"id": "ui-spec#9", "file": "e2e/ui-spec-1.e2e.spec.ts"}])
        self.assertNotEqual(other[0]["id"], d["id"], "другой сценарий — другой id")

    def test_two_failures_in_one_scenario_keep_their_own_ids_whatever_the_order(self):
        planned = [{"id": "ui-spec#1", "file": "e2e/ui-spec-1.e2e.spec.ts"}]
        one = '<testcase name="первый" classname="ui-spec-1.e2e.spec.ts"><failure message="a"/></testcase>'
        two = '<testcase name="второй" classname="ui-spec-1.e2e.spec.ts"><failure message="b"/></testcase>'
        a = {d["element"]: d["id"] for d in E.defects_of(E.parse_junit(f'<testsuite name="x">{one}{two}</testsuite>'), planned)}
        b = {d["element"]: d["id"] for d in E.defects_of(E.parse_junit(f'<testsuite name="x">{two}{one}</testsuite>'), planned)}
        self.assertEqual(a, b)
        self.assertEqual(len(set(a.values())), 2, "падения разных тестов одного сценария — разные пункты")

    def test_a_file_outside_the_plan_is_named_and_same_titles_get_distinct_ids(self):
        xml = ('<testsuite name="x"><testcase name="t" classname="foreign.spec.ts"><failure message="a"/></testcase>'
               '<testcase name="t" classname="foreign.spec.ts"><failure message="b"/></testcase></testsuite>')
        got = E.defects_of(E.parse_junit(xml), [])
        self.assertEqual(len(got), 2)
        self.assertEqual(len({d["id"] for d in got}), 2)
        self.assertIn("не из плана", got[0]["message"])


class Run(World):
    def setUp(self):
        super().setUp()
        self.spec_path = self.root / "spec" / "ui-spec.md"
        self.spec_path.parent.mkdir(parents=True, exist_ok=True)
        self.spec_path.write_text(SPEC, encoding="utf-8")
        T.call("daiver_track_manifest", self.root, {"track": self.TB.TRACK, "set": {"specs": ["spec/ui-spec.md"]}})
        self.it = self.fake_iteration(1)

    def rel(self):
        return self.it.relative_to(self.root).as_posix()

    def spec(self):
        return T.call("daiver_spec", self.root, {"feature": self.TB.TRACK})

    def tests(self, *names):
        for n in names:
            p = self.root / "e2e" / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("import { test } from '@playwright/test';\n", encoding="utf-8")

    def playwright(self, installed=True, declared=True):
        if declared:
            (self.root / "package.json").write_text(json.dumps({"devDependencies": {"@playwright/test": "^1.50.0"}}), encoding="utf-8")
        if installed:
            p = self.root / "node_modules" / "@playwright" / "test" / "package.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"version": "1.50.1"}), encoding="utf-8")

    def go(self, runner=None, **kw):
        return E.run(self.root, self.TB.TRACK, None, self.it, self.spec(), runner=runner, **kw)

    def summary(self):
        return json.loads((self.it / E.SUMMARY).read_text(encoding="utf-8"))

    def defects(self):
        return json.loads((self.it / E.DEFECTS).read_text(encoding="utf-8"))["defects"]

    @staticmethod
    def runner(xml=None, rc=1, tail="", timed_out=False, calls=None):
        def run(root, files, junit, timeout):
            if calls is not None:
                calls.append(list(files))
            if xml is not None:
                junit.write_text(xml, encoding="utf-8")
            return (None if timed_out else rc), tail, timed_out
        return run

    # --- планирование по настоящему разбору спек ---
    def test_the_plan_comes_from_the_real_parsed_spec_and_skips_removed(self):
        p = T.call("daiver_e2e_plan", self.root, {"track": self.TB.TRACK})
        self.assertTrue(p["hasE2e"])
        self.assertEqual(p["files"], ["e2e/ui-spec-1.e2e.spec.ts", "e2e/ui-spec-2.e2e.spec.ts"])
        self.assertEqual([s["id"] for s in p["skipped"]], ["ui-spec#3"], "сценарий из REMOVED Requirements скипнут и не пишется (N17)")
        self.assertEqual(p["scenarios"][0]["title"], "заголовок виден")
        self.assertIn("Заголовок", p["scenarios"][0]["body"])

    def test_without_specs_the_gate_is_skipped_once_with_a_reason_and_artifacts(self):
        self.spec_path.unlink()
        T.call("daiver_track_manifest", self.root, {"track": self.TB.TRACK, "set": {"specs": None}})
        p = T.call("daiver_e2e_plan", self.root, {"track": self.TB.TRACK})
        self.assertFalse(p["hasE2e"])
        self.assertEqual(p["gate"]["status"], "skipped")
        r = T.call("daiver_e2e_run", self.root, {"track": self.TB.TRACK, "iterationDir": self.rel()})
        self.assertEqual((r["status"], r["ran"]), ("skipped", False))
        self.assertIn("hasE2e: false", r["reason"])
        self.assertEqual(self.defects(), [])

    # --- пропуски ---
    def test_scenarios_without_written_tests_skip_and_name_the_files(self):
        calls = []
        r = self.go(self.runner(calls=calls))
        self.assertEqual((r["status"], r["ran"]), ("skipped", False))
        self.assertEqual(r["notWritten"], ["e2e/ui-spec-1.e2e.spec.ts", "e2e/ui-spec-2.e2e.spec.ts"])
        self.assertIn("тестов нет", r["reason"])
        self.assertEqual(calls, [], "без файлов прогон не запускается")

    def test_no_playwright_package_skips_with_the_reason_and_installs_nothing(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        calls = []
        r = self.go(self.runner(calls=calls))
        self.assertEqual(r["status"], "skipped")
        self.assertIn("package.json", r["reason"])
        self.assertEqual(calls, [])
        self.assertFalse((self.root / "node_modules").exists())

    def test_declared_but_not_installed_is_a_different_reason(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright(installed=False)
        calls = []
        r = self.go(self.runner(calls=calls))
        self.assertEqual(r["status"], "skipped")
        self.assertIn("не установлен", r["reason"])
        self.assertEqual(calls, [])

    def test_an_npx_that_cannot_be_started_skips_with_its_own_reason(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        broken = self.root / "fakebin" / "npx"
        broken.parent.mkdir()
        broken.write_text("не исполняемый файл", encoding="utf-8")
        broken.chmod(0o755)
        with mock.patch.object(E.shutil, "which", return_value=str(broken)):
            r = self.go()
        self.assertEqual((r["status"], r["ran"]), ("skipped", False))
        self.assertIn("не запустился", r["reason"])

    def test_a_missing_npx_skips_with_its_own_reason(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        with mock.patch.object(E.shutil, "which", return_value=None):
            r = self.go()
        self.assertEqual(r["status"], "skipped")
        self.assertIn("npx", r["reason"])

    # --- прогон ---
    def test_green_run_passes_and_only_written_files_are_run(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        calls = []
        r = self.go(self.runner(JUNIT_PASS, rc=0, calls=calls))
        self.assertEqual((r["status"], r["ran"], r["tests"], r["passed"], r["failed"]), ("pass", True, 1, 1, 0))
        self.assertEqual(calls, [["e2e/ui-spec-1.e2e.spec.ts"]])
        self.assertEqual(r["notWritten"], ["e2e/ui-spec-2.e2e.spec.ts"], "не написанное названо, а не замолчано")
        self.assertEqual(self.defects(), [])
        self.assertEqual(self.summary()["status"], "pass")

    def test_failures_become_defects_and_fail_the_gate(self):
        self.tests("ui-spec-1.e2e.spec.ts", "ui-spec-2.e2e.spec.ts")
        self.playwright()
        r = self.go(self.runner(JUNIT_FAIL, rc=1))
        self.assertEqual((r["status"], r["failed"], r["defects"]), ("fail", 1, 1))
        self.assertIn("упало тестов: 1", r["reason"])
        d = self.defects()[0]
        self.assertEqual((d["zone"], d["location"]["file"]), ("e2e", "e2e/ui-spec-1.e2e.spec.ts"))
        self.assertIn("не глуши", r["nextStep"])

    def test_a_run_without_a_report_fails_and_does_not_pass(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        r = self.go(self.runner(None, rc=1, tail="Error: Cannot find module 'x' в playwright.config"))
        self.assertEqual(r["status"], "fail")
        self.assertIn("без JUnit-отчёта", r["reason"])
        self.assertIn("Cannot find module", r["reason"])
        self.assertEqual(self.defects(), [])

    def test_a_clean_report_with_a_bad_exit_code_is_not_a_pass(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        r = self.go(self.runner(JUNIT_PASS, rc=2, tail="webServer exited"))
        self.assertEqual(r["status"], "fail")
        self.assertIn("код возврата 2", r["reason"])

    def test_an_empty_report_is_not_a_pass(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        r = self.go(self.runner("<testsuites></testsuites>", rc=0))
        self.assertEqual(r["status"], "fail")
        self.assertIn("ни одного теста", r["reason"])

    def test_a_broken_report_fails_with_the_parse_error(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        r = self.go(self.runner("<testsuites><testsuite>", rc=0))
        self.assertEqual(r["status"], "fail")
        self.assertIn("не читается", r["reason"])

    def test_a_timeout_fails_and_says_so(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        r = self.go(self.runner(None, timed_out=True, tail="waiting for webServer"), timeout=7)
        self.assertEqual(r["status"], "fail")
        self.assertIn("не уложился в 7 с", r["reason"])
        self.assertIn("waiting for webServer", r["reason"])

    def test_a_stale_report_from_an_earlier_run_is_not_read(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        (self.it / E.JUNIT).write_text(JUNIT_PASS, encoding="utf-8")
        r = self.go(self.runner(None, rc=0, tail="crash"))
        self.assertEqual(r["status"], "fail", "старый зелёный отчёт не засчитывается новому прогону")
        self.assertIn("без JUnit-отчёта", r["reason"])

    def test_a_folder_outside_verify_is_refused(self):
        with self.assertRaises(E.E2eError):
            E.run(self.root, self.TB.TRACK, None, self.root / "src", self.spec())
        with self.assertRaises(T.ToolError):
            T.call("daiver_e2e_run", self.root, {"track": self.TB.TRACK, "iterationDir": "src/x"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_e2e_run", self.root, {"track": "NOPE-1", "iterationDir": self.rel()})

    @unittest.skipIf(os.name != "posix", "поддельный npx — shell-скрипт")
    def test_a_real_subprocess_through_a_fake_npx(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        bin_dir = self.root / "fakebin"
        bin_dir.mkdir()
        npx = bin_dir / "npx"
        npx.write_text('#!/bin/sh\necho "args: $@" >&2\ncat > "$PLAYWRIGHT_JUNIT_OUTPUT_FILE" <<\'XML\'\n' + JUNIT_FAIL + '\nXML\nexit 1\n', encoding="utf-8")
        npx.chmod(npx.stat().st_mode | stat.S_IXUSR)
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}):
            r = T.call("daiver_e2e_run", self.root, {"track": self.TB.TRACK, "iterationDir": self.rel()})
        self.assertEqual((r["status"], r["failed"], r["tests"]), ("fail", 1, 3), r)
        self.assertTrue((self.it / E.JUNIT).is_file())

    # --- запись итерации берёт факт ---
    def record(self, e2e):
        g = dict({"compare": "pass", "code": "skipped"}, **SKIPPED)
        g["e2e"] = e2e
        return T.call("daiver_verify_state_record", self.root, {"track": self.TB.TRACK, "iteration": 1, "gates": g})

    def test_the_record_takes_the_status_and_the_failures_from_the_run(self):
        self.tests("ui-spec-1.e2e.spec.ts", "ui-spec-2.e2e.spec.ts")
        self.playwright()
        self.go(self.runner(JUNIT_FAIL, rc=1))
        res = self.record("pass")
        self.assertEqual(res["gates"]["e2e"], "fail")
        self.assertFalse(res["clean"])
        self.assertEqual(len(res["blockingIds"]), 1)
        self.assertTrue(any("gates.e2e" in w and "fail" in w for w in res["warnings"]), res.get("warnings"))
        h = self.state()["history"][0]
        self.assertEqual(h["gates"]["e2e"]["status"], "fail")
        self.assertEqual([d["zone"] for d in h["defects"]], ["e2e"])
        self.assertEqual(h["defects"][0]["source"], "e2e")

    def test_a_green_run_is_clean_and_an_unsupported_pass_is_skipped(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        self.go(self.runner(JUNIT_PASS, rc=0))
        res = self.record("skipped")
        self.assertEqual((res["gates"]["e2e"], res["clean"]), ("pass", True))

    def test_pass_without_a_run_is_recorded_as_skipped(self):
        res = self.record("pass")
        self.assertEqual((res["gates"]["e2e"], res["clean"]), ("skipped", True))
        self.assertTrue(any("daiver_e2e_run не вызывался" in w for w in res["warnings"]), res.get("warnings"))
        self.assertIn("не исполнялся", self.state()["history"][0]["gates"]["e2e"]["reason"])

    def test_fail_without_a_run_stays_failed(self):
        res = self.record({"status": "fail", "reason": "руками упало"})
        self.assertEqual((res["gates"]["e2e"], res["clean"]), ("fail", False))

    def test_a_skipped_run_does_not_block_and_keeps_its_reason(self):
        self.go()
        res = self.record("skipped")
        self.assertEqual((res["gates"]["e2e"], res["clean"]), ("skipped", True))
        self.assertIn("тестов нет", self.state()["history"][0]["gates"]["e2e"]["reason"])

    def test_a_human_false_positive_on_the_only_failure_lifts_the_block(self):
        self.tests("ui-spec-1.e2e.spec.ts", "ui-spec-2.e2e.spec.ts")
        self.playwright()
        self.go(self.runner(JUNIT_FAIL, rc=1))
        did = self.defects()[0]["id"]
        (self.track_dir / "verify-decisions.json").write_text(json.dumps({"schema": 1, "decisions": [{"id": did, "verdict": "false_positive", "reviewer": "Анна"}]}), encoding="utf-8")
        res = self.record("fail")
        self.assertEqual((res["clean"], res["blockingIds"]), (True, []))
        self.assertEqual(res["gates"]["e2e"], "pass")

    def test_a_timeout_without_defects_keeps_the_gate_failed(self):
        self.tests("ui-spec-1.e2e.spec.ts")
        self.playwright()
        self.go(self.runner(None, timed_out=True))
        res = self.record("pass")
        self.assertEqual((res["gates"]["e2e"], res["clean"]), ("fail", False), "падение без пунктов решением человека не снять")


class Registry(unittest.TestCase):
    def test_the_tools_are_registered_with_required_arguments(self):
        self.assertIn("daiver_e2e_plan", T.TOOLS)
        self.assertIn("daiver_e2e_run", T.TOOLS)
        self.assertEqual(T.TOOLS["daiver_e2e_plan"]["schema"]["required"], ["track"])
        self.assertEqual(T.TOOLS["daiver_e2e_run"]["schema"]["required"], ["track", "iterationDir"])


if __name__ == "__main__":
    unittest.main()
