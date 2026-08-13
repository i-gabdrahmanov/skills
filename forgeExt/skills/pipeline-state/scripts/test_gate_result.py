#!/usr/bin/env python3
"""Tests for record_gate.py + update.py._check_gate_result.

Build/verify-шаги (04-test/04-build/05-tests, lite-red/green/verify) закрываются completed
только при gates/<step_id>.json с produced_by:"record_gate" и passed:true — самоотчёт
субагента («status: completed») не доказательство. Артефакт пишет record_gate.py по
фактическому exit-коду гейта. Escape-hatch: overrides/gate-result-<step_id>.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATE = HERE / "update.py"
RECORD = HERE / "record_gate.py"

sys.path.insert(0, str(HERE))
import _util  # noqa: E402,F401  — кладёт hooks/ в sys.path
import forge_events as FE  # noqa: E402  — evidence гейтов живёт в журнале прогона

SKILL = "forgelite"
FEATURE = "KID-1"


def _make_manifest(tmp: Path) -> None:
    d = tmp / "ground" / "statements" / SKILL / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "feature": FEATURE,
        "steps": [
            {"id": "lite-jira", "status": "in_progress", "required_judges": []},
            {"id": "lite-design", "status": "in_progress", "required_judges": []},
            {"id": "lite-green", "status": "in_progress", "required_judges": []},
            {"id": "lite-red", "status": "in_progress", "required_judges": []},
            {"id": "lite-ground", "status": "in_progress", "required_judges": []},
        ],
    }), encoding="utf-8")


def _write_origin(tmp: Path, step_id: str) -> None:
    d = tmp / "ground" / "statements" / SKILL / FEATURE / "_origins"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{step_id}.json").write_text(json.dumps({"step_id": step_id}), encoding="utf-8")


def _write_design_docs(tmp: Path) -> None:
    """Артефакты lite-design под каноническими именами — без них шаг не закрывается
    (по этим именам их читают lite-red/lite-green)."""
    d = tmp / "docs" / "feature-pipeline" / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "tech-design.md").write_text("# design", encoding="utf-8")
    (d / "task-plan.json").write_text("{}", encoding="utf-8")


def _gradlew(tmp: Path, exit_code: int = 0) -> str:
    """Команда сборки для --cmd: шим `./gradlew` с заданным исходом.

    Нужен потому, что record_gate теперь сверяет СУБСТАНЦИЮ команды с risk-policy
    (`gate_cmd_expect`): для сборочных шагов в команде обязан быть реальный раннер, а не
    произвольное `true`. Тесты ниже проверяют механику record_gate, поэтому раннер — заглушка,
    но вызывается он честно, как `./gradlew`."""
    shim = tmp / "gradlew"
    shim.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    shim.chmod(0o755)
    return "./gradlew build"


def _stub_gate(tmp: Path, name: str, exit_code: int = 0) -> str:
    """Команда гейта-заглушки с настоящим ИМЕНЕМ скрипта (его сверяет gate_cmd_expect)."""
    (tmp / name).write_text(f"import sys\nsys.exit({exit_code})\n", encoding="utf-8")
    return f'"{sys.executable}" {name}'


def _close(tmp: Path, step_id: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(UPDATE), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, "--step-id", step_id, "--status", "completed",
         "--closed-by", "subagent"],
        capture_output=True, text=True,
    )


def _record(tmp: Path, step_id: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RECORD), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, "--step-id", step_id, *extra],
        capture_output=True, text=True,
    )


def _gate_rec(tmp: Path, step_id: str) -> dict:
    """Evidence гейта из журнала прогона (раньше — файл gates/<step_id>.json)."""
    rec = FE.gate(tmp, SKILL, FEATURE, step_id)
    assert rec is not None, f"нет evidence гейта '{step_id}' в журнале {tmp}"
    return rec


def _junit_xml(cases: list[tuple[str, str]]) -> str:
    """JUnit XML: cases = [(имя, 'red'|'green'), ...]."""
    items = "".join(
        f'<testcase classname="com.x.FooTest" name="{n}">'
        + ('<failure message="boom"/>' if s == "red" else "") + "</testcase>"
        for n, s in cases)
    return (f'<?xml version="1.0"?>'
            f'<testsuite name="FooTest" tests="{len(cases)}">{items}</testsuite>')


def _mk_test_runner(tmp: Path, cases: list[tuple[str, str]], exit_code: int = 1) -> str:
    """Команда-«тест-раннер»: пишет JUnit XML текущего прогона и выходит с exit_code —
    как gradle test (отчёт в build/test-results независимо от исхода)."""
    (tmp / "report.xml").write_text(_junit_xml(cases), encoding="utf-8")
    # раннер зовётся `./gradlew`: record_gate сверяет субстанцию команды с risk-policy
    # (gate_cmd_expect), и для тест-шага в ней обязан быть настоящий раннер
    runner = tmp / "gradlew"
    runner.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, shutil, sys\n"
        "d = pathlib.Path('build/test-results/test'); d.mkdir(parents=True, exist_ok=True)\n"
        "shutil.copy('report.xml', d / 'TEST-com.x.FooTest.xml')\n"
        f"sys.exit({exit_code})\n", encoding="utf-8")
    runner.chmod(0o755)
    return "./gradlew test"


class TestRecordGate(unittest.TestCase):
    def test_success_gate_passed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-green", "--cmd", _gradlew(tmp, 0))
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = _gate_rec(tmp, "lite-green")
            self.assertTrue(rec["passed"])
            self.assertEqual(rec["produced_by"], "record_gate")

    def test_success_gate_failed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-green", "--cmd", _gradlew(tmp, 1))
            self.assertEqual(r.returncode, 1)
            self.assertFalse(_gate_rec(tmp, "lite-green")["passed"])

    def test_red_gate_all_tests_red_passes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [("t1", "red"), ("t2", "red"), ("t3", "red")])
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = _gate_rec(tmp, "lite-red")
            self.assertTrue(rec["passed"])
            self.assertEqual(rec["tests_red"], 3)
            self.assertEqual(rec["tests_green"], 0)

    def test_red_gate_one_red_rest_green_fails(self):
        # ПИН бага прогона: один красный тест валит раннер (exit!=0) → раньше «RED пройден»,
        # хотя остальные новые тесты зелёные (вакуумные — проходят без реализации)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [("t1", "red"), ("t2", "green"), ("t3", "green")])
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 1, "1 red + 2 green — НЕ успех RED")
            rec = _gate_rec(tmp, "lite-red")
            self.assertFalse(rec["passed"])
            self.assertEqual(rec["tests_green"], 2)
            self.assertIn("ЗЕЛЁНЫЕ", rec["reason"])
            self.assertIn("com.x.FooTest.t2", "".join(rec["green_tests"]))

    def test_red_gate_no_junit_reports_fails(self):
        # exit!=0 без JUnit-отчётов — не доказательство RED (fail-closed)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", _gradlew(tmp, 1))
            self.assertEqual(r.returncode, 1)
            rec = _gate_rec(tmp, "lite-red")
            self.assertIn("JUnit", rec["reason"])

    def test_red_gate_stale_reports_not_counted(self):
        # отчёты ПРОШЛОГО прогона (старый mtime) не засчитываются за текущий
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            rep = tmp / "build" / "test-results" / "test" / "TEST-com.x.FooTest.xml"
            rep.parent.mkdir(parents=True)
            rep.write_text(_junit_xml([("t1", "red")]), encoding="utf-8")
            old = time.time() - 3600
            os.utime(rep, (old, old))
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", _gradlew(tmp, 1))
            self.assertEqual(r.returncode, 1, "залежавшийся отчёт не доказывает RED")

    def test_red_gate_zero_executed_tests_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [])
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 1, "0 выполненных тестов — не RED")

    def test_red_gate_green_tests_fail(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [("t1", "green")], exit_code=0)
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 1)

    def test_red_gate_compile_broken_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "lite-red", "--expect", "red",
                        "--compile-cmd", "false", "--cmd", _gradlew(tmp, 1))
            self.assertEqual(r.returncode, 1)


class TestGateResultCheck(unittest.TestCase):
    def test_close_without_artifact_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            r = _close(tmp, "lite-green")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("record_gate", r.stderr)

    def test_close_with_passed_artifact_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            self.assertEqual(_record(tmp, "lite-green", "--cmd", _gradlew(tmp, 0)).returncode, 0)
            r = _close(tmp, "lite-green")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_close_with_failed_artifact_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            _record(tmp, "lite-green", "--cmd", _gradlew(tmp, 1))
            self.assertNotEqual(_close(tmp, "lite-green").returncode, 0)

    def test_handwritten_legacy_artifact_blocked(self):
        """Старая раскладка читается как фолбэк, но провенанс с неё требуется тот же."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            gf = tmp / "ground" / "statements" / SKILL / FEATURE / "gates" / "lite-green.json"
            gf.parent.mkdir(parents=True, exist_ok=True)
            gf.write_text(json.dumps({"passed": True}), encoding="utf-8")  # без провенанса
            self.assertNotEqual(_close(tmp, "lite-green").returncode, 0)

    def test_handwritten_log_line_blocked(self):
        """Дописанная руками строка журнала без produced_by не считается evidence.

        Это главное, что даёт журнал против россыпи файлов: провенанс проверяется
        свёрткой единообразно, а не «где-то проверяется, где-то .exists()»."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            log = FE.events_path(tmp, SKILL, FEATURE)
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(json.dumps({"kind": "gate", "step_id": "lite-green",
                                       "passed": True}) + "\n", encoding="utf-8")
            self.assertIsNone(FE.gate(tmp, SKILL, FEATURE, "lite-green"))
            self.assertNotEqual(_close(tmp, "lite-green").returncode, 0)

    def test_forged_provenance_in_payload_blocked(self):
        """record_gate не даёт вердикту самому назначить себе provenance."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            FE.append_event(tmp, SKILL, FEATURE, "gate", step_id="lite-green",
                            passed=True, produced_by="totally-legit")
            rec = FE.gate(tmp, SKILL, FEATURE, "lite-green")
            self.assertIsNotNone(rec)
            self.assertEqual(rec["produced_by"], "record_gate",
                             "payload перебил служебное поле записи")

    def test_override_allows_close(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-green")
            ov = tmp / "ground" / "statements" / SKILL / FEATURE / "overrides"
            ov.mkdir(parents=True, exist_ok=True)
            (ov / "gate-result-lite-green.json").write_text(
                json.dumps({"reason": "тест: гейт неприменим"}), encoding="utf-8")
            r = _close(tmp, "lite-green")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_gate_step_not_affected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            r = _close(tmp, "lite-ground")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_lite_design_without_artifact_blocked(self):
        # lite-design закрывался «со слов субагента» — судей у lite-* нет, evidence обязателен
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-design")
            r = _close(tmp, "lite-design")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("record_gate", r.stderr)

    def test_lite_design_with_passed_artifact_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-design")
            _write_design_docs(tmp)
            self.assertEqual(_record(tmp, "lite-design", "--cmd", _stub_gate(tmp, "check_taskplan.py")).returncode, 0)
            r = _close(tmp, "lite-design")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_lite_design_blocked_when_doc_named_by_task_slug(self):
        """Гейт прошёл, но дизайн записан как <KEY>.md — для lite-red/lite-green его нет."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "lite-design")
            docs = tmp / "docs" / "feature-pipeline" / FEATURE
            docs.mkdir(parents=True)
            (docs / f"{FEATURE}.md").write_text("# design", encoding="utf-8")
            (docs / "task-plan.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_record(tmp, "lite-design", "--cmd", _stub_gate(tmp, "check_taskplan.py")).returncode, 0)
            r = _close(tmp, "lite-design")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("tech-design.md", r.stderr)

    def test_record_gate_rejects_command_without_the_gate(self):
        """Подделка СУБСТАНЦИИ: `--cmd "true"` давал валидный passed:true и закрывал шаг."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            r = _record(tmp, "lite-design", "--cmd", "true")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("check_taskplan.py", r.stderr)
            self.assertIsNone(FE.gate(tmp, SKILL, FEATURE, "lite-design"),
                              "evidence не должно записываться при отказе")

    def test_lite_jira_scope_gate_required(self):
        # скоуп-чек (check_scope) нельзя молча пропустить: без evidence lite-jira не закрыть
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            self.assertNotEqual(_close(tmp, "lite-jira").returncode, 0)
            self.assertEqual(_record(tmp, "lite-jira", "--cmd", _stub_gate(tmp, "check_scope.py")).returncode, 0)
            self.assertEqual(_close(tmp, "lite-jira").returncode, 0)


if __name__ == "__main__":
    unittest.main()
