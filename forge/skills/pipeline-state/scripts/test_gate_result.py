#!/usr/bin/env python3
"""Tests for record_gate.py + update.py._check_gate_result.

Build/verify-шаги (04-test/04-build/05-tests, fix-red/green/verify) закрываются completed
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
# `_util` живёт в двух scripts-каталогах (config-helper и здесь) с разным API.
# `hooks/risk_ladder.py` предзагружает config-helperский `_util` в sys.modules — без сброса
# получим его копию, которая не кладёт hooks/ в sys.path (а это и есть смысл `import _util`
# ниже). Дропаем кэш, если он чужой.
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != HERE:
    del sys.modules["_util"]

import _util  # noqa: E402,F401  — кладёт hooks/ в sys.path
import forge_events as FE  # noqa: E402  — evidence гейтов живёт в журнале прогона

SKILL = "forgefix"
FEATURE = "KID-1"

# Плоская ветка (fix) — представитель шагов, требующих gate-result. Full-namespace нужен
# отдельно: 01-grounding — единственный шаг ВНЕ GATE_RESULT_PREFIXES, на нём проверяется,
# что гейт evidence не цепляет чужие шаги.
FULL_SKILL = "feature-pipeline"

_FIX_STEPS = ["fix-intake", "fix-diag", "fix-green", "fix-red"]


def _make_manifest(tmp: Path, skill: str = SKILL, steps=None, inputs=None) -> None:
    d = tmp / "ground" / "statements" / skill / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    man = {
        "feature": FEATURE,
        "skill": skill,
        "steps": [{"id": sid, "status": "in_progress", "required_judges": []}
                  for sid in (steps if steps is not None else _FIX_STEPS)],
    }
    if inputs:
        man["inputs"] = inputs
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")


def _write_origin(tmp: Path, step_id: str, skill: str = SKILL) -> None:
    d = tmp / "ground" / "statements" / skill / FEATURE / "_origins"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{step_id}.json").write_text(json.dumps({"step_id": step_id}), encoding="utf-8")


def _diag_docs_dir(tmp: Path) -> Path:
    """Каталог артефактов задачи так, как его резолвит сам update.py (_docs_dir_for)."""
    sys.path.insert(0, str(HERE))
    from _util import task_docs_dir
    return task_docs_dir(tmp, SKILL, FEATURE)


def _write_design_docs(tmp: Path) -> None:
    """Артефакты fix-diag под каноническими именами — без них шаг не закрывается
    (по этим именам их читают fix-red/fix-green)."""
    d = _diag_docs_dir(tmp)
    d.mkdir(parents=True, exist_ok=True)
    (d / "fix-plan.md").write_text("# fix plan", encoding="utf-8")
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


def _close(tmp: Path, step_id: str, skill: str = SKILL) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(UPDATE), "--project", str(tmp), "--skill", skill,
         "--feature", FEATURE, "--step-id", step_id, "--status", "completed",
         "--closed-by", "subagent"],
        capture_output=True, text=True,
    )


def _record(tmp: Path, step_id: str, *extra: str,
            skill: str = SKILL) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RECORD), "--project", str(tmp), "--skill", skill,
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


def _green(tmp: Path, exit_code: int = 0) -> str:
    """Команда GREEN-гейта: сборка + гейт приёмки (check_acceptance --expect green).

    Для fix-green/04-build policy принимает ТОЛЬКО check_acceptance.py: голая сборка закрывала
    шаг, не проверив ни одного критерия приёмки. Шим отдаёт заданный исход."""
    _gradlew(tmp, 0)
    shim = tmp / "check_acceptance.py"
    shim.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    shim.chmod(0o755)
    return "./gradlew build && ./check_acceptance.py plan --expect green"


class TestRecordGate(unittest.TestCase):
    def test_green_without_acceptance_gate_refused(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "fix-green", "--cmd", _gradlew(tmp, 0))
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("check_acceptance.py", r.stderr)

    def test_success_gate_passed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "fix-green", "--cmd", _green(tmp, 0))
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = _gate_rec(tmp, "fix-green")
            self.assertTrue(rec["passed"])
            self.assertEqual(rec["produced_by"], "record_gate")

    def test_success_gate_failed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "fix-green", "--cmd", _green(tmp, 1))
            self.assertEqual(r.returncode, 1)
            self.assertFalse(_gate_rec(tmp, "fix-green")["passed"])

    def test_red_gate_all_tests_red_passes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [("t1", "red"), ("t2", "red"), ("t3", "red")])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = _gate_rec(tmp, "fix-red")
            self.assertTrue(rec["passed"])
            self.assertEqual(rec["tests_red"], 3)
            self.assertEqual(rec["tests_green"], 0)

    def test_red_gate_one_red_rest_green_fails(self):
        # ПИН бага прогона: один красный тест валит раннер (exit!=0) → раньше «RED пройден»,
        # хотя остальные новые тесты зелёные (вакуумные — проходят без реализации)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [("t1", "red"), ("t2", "green"), ("t3", "green")])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 1, "1 red + 2 green — НЕ успех RED")
            rec = _gate_rec(tmp, "fix-red")
            self.assertFalse(rec["passed"])
            self.assertEqual(rec["tests_green"], 2)
            self.assertIn("ЗЕЛЁНЫЕ", rec["reason"])
            self.assertIn("com.x.FooTest.t2", "".join(rec["green_tests"]))

    def test_red_gate_no_junit_reports_fails(self):
        # exit!=0 без JUnit-отчётов — не доказательство RED (fail-closed)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", _gradlew(tmp, 1))
            self.assertEqual(r.returncode, 1)
            rec = _gate_rec(tmp, "fix-red")
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
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", _gradlew(tmp, 1))
            self.assertEqual(r.returncode, 1, "залежавшийся отчёт не доказывает RED")

    def test_red_gate_zero_executed_tests_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 1, "0 выполненных тестов — не RED")

    def test_red_gate_green_tests_fail(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [("t1", "green")], exit_code=0)
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 1)

    def test_red_gate_compile_broken_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "false", "--cmd", _gradlew(tmp, 1))
            self.assertEqual(r.returncode, 1)

    # ── KIDPPRB-9254 п.3: mixed RED/green для ИНВАРИАНТОВ ─────────────────────
    # Покрывают флаг --allow-invariants / --invariant-pattern в record_gate.py.
    # Поведение red_reason детально тестируется в test_junit_report.py; здесь —
    # только что флаг проброшен через CLI и evidence записан корректно.

    def test_red_gate_invariant_green_with_flag_passes(self):
        """1 red + 1 GREEN-invariant + --allow-invariants → passed:true.

        Прецедент KIDPPRB-9254: 2 из 4 тестов T1 проверяли TASK_SERVICE_AUTO_CLOSE
        (не меняется), были зелёные по дизайну. С allow_invariants=True они больше
        не валят RED-гейт."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [
                ("t1_new_behavior", "red"),
                ("t2_INVARIANT_TASK_SERVICE_AUTO_CLOSE", "green"),
            ])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd,
                        "--allow-invariants")
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = _gate_rec(tmp, "fix-red")
            self.assertTrue(rec["passed"])
            self.assertTrue(rec["allow_invariants"])
            self.assertEqual(rec["tests_red"], 1)
            self.assertEqual(rec["tests_green"], 1)

    def test_red_gate_invariant_green_without_flag_fails(self):
        """1 red + 1 GREEN-invariant без флага → passed:false (default-strict)."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [
                ("t1_new", "red"),
                ("t2_INVARIANT_x", "green"),
            ])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 1)
            rec = _gate_rec(tmp, "fix-red")
            self.assertFalse(rec["passed"])
            self.assertIn("ЗЕЛЁНЫЕ", rec["reason"])

    def test_red_gate_vacuum_green_with_flag_fails(self):
        """1 red + 1 GREEN-vacuum (без маркера) + --allow-invariants → fail.

        Маркер обязателен — иначе allow_invariants тривиально снимает любую защиту
        от вакуумных зелёных (regression на tolerate_green)."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [
                ("t1_new", "red"),
                ("t2_vacuum_green", "green"),  # НЕ содержит INVARIANT
            ])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd,
                        "--allow-invariants")
            self.assertEqual(r.returncode, 1)
            rec = _gate_rec(tmp, "fix-red")
            self.assertFalse(rec["passed"])
            self.assertIn("RED не чистый", rec["reason"])
            self.assertIn("t2_vacuum_green", "".join(rec.get("green_tests", [])))

    def test_red_gate_custom_invariant_pattern(self):
        """Кастомный --invariant-pattern применяется к классификации."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cmd = _mk_test_runner(tmp, [
                ("t1_new", "red"),
                ("t2_KEEP_ME_old_logic", "green"),
            ])
            # дефолтный 'INVARIANT' НЕ матчит KEEP_ME → fail
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd,
                        "--allow-invariants")
            self.assertEqual(r.returncode, 1)
            rec = _gate_rec(tmp, "fix-red")
            self.assertFalse(rec["passed"])
            # с правильным pattern → pass
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd,
                        "--allow-invariants", "--invariant-pattern", "KEEP_ME")
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = _gate_rec(tmp, "fix-red")
            self.assertTrue(rec["passed"])
            self.assertEqual(rec["invariant_pattern"], "KEEP_ME")

    def test_red_gate_invariants_from_pipeline_json(self):
        """quality.red_gate.allow_invariants=true в ground/pipeline.json — без CLI-флагов."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "ground").mkdir(parents=True)
            (tmp / "ground" / "pipeline.json").write_text(json.dumps(
                {"quality": {"red_gate": {"allow_invariants": True,
                                           "invariant_pattern": "INVARIANT"}}},
            ), encoding="utf-8")
            cmd = _mk_test_runner(tmp, [
                ("t1_new", "red"),
                ("t2_INVARIANT_keep", "green"),
            ])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd)
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = _gate_rec(tmp, "fix-red")
            self.assertTrue(rec["passed"])
            self.assertTrue(rec["allow_invariants"])
            self.assertEqual(rec["invariant_pattern"], "INVARIANT")

    def test_red_gate_strict_invariants_overrides_pipeline_json(self):
        """--strict-invariants принудительно выключает allow_invariants из pipeline.json."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "ground").mkdir(parents=True)
            (tmp / "ground" / "pipeline.json").write_text(json.dumps(
                {"quality": {"red_gate": {"allow_invariants": True}}},
            ), encoding="utf-8")
            cmd = _mk_test_runner(tmp, [
                ("t1_new", "red"),
                ("t2_INVARIANT_keep", "green"),
            ])
            r = _record(tmp, "fix-red", "--expect", "red",
                        "--compile-cmd", "true", "--cmd", cmd,
                        "--strict-invariants")
            self.assertEqual(r.returncode, 1,
                             msg="--strict-invariants должен побить pipeline.json=true")
            rec = _gate_rec(tmp, "fix-red")
            self.assertFalse(rec["passed"])
            self.assertFalse(rec["allow_invariants"])


class TestGateResultCheck(unittest.TestCase):
    def test_close_without_artifact_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-green")
            r = _close(tmp, "fix-green")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("record_gate", r.stderr)

    def test_close_with_passed_artifact_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-green")
            self.assertEqual(_record(tmp, "fix-green", "--cmd", _green(tmp, 0)).returncode, 0)
            r = _close(tmp, "fix-green")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_close_with_failed_artifact_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-green")
            _record(tmp, "fix-green", "--cmd", _green(tmp, 1))
            self.assertNotEqual(_close(tmp, "fix-green").returncode, 0)

    def test_handwritten_legacy_artifact_blocked(self):
        """Старая раскладка читается как фолбэк, но провенанс с неё требуется тот же."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-green")
            gf = tmp / "ground" / "statements" / SKILL / FEATURE / "gates" / "fix-green.json"
            gf.parent.mkdir(parents=True, exist_ok=True)
            gf.write_text(json.dumps({"passed": True}), encoding="utf-8")  # без провенанса
            self.assertNotEqual(_close(tmp, "fix-green").returncode, 0)

    def test_handwritten_log_line_blocked(self):
        """Дописанная руками строка журнала без produced_by не считается evidence.

        Это главное, что даёт журнал против россыпи файлов: провенанс проверяется
        свёрткой единообразно, а не «где-то проверяется, где-то .exists()»."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-green")
            log = FE.events_path(tmp, SKILL, FEATURE)
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(json.dumps({"kind": "gate", "step_id": "fix-green",
                                       "passed": True}) + "\n", encoding="utf-8")
            self.assertIsNone(FE.gate(tmp, SKILL, FEATURE, "fix-green"))
            self.assertNotEqual(_close(tmp, "fix-green").returncode, 0)

    def test_forged_provenance_in_payload_blocked(self):
        """record_gate не даёт вердикту самому назначить себе provenance."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            FE.append_event(tmp, SKILL, FEATURE, "gate", step_id="fix-green",
                            passed=True, produced_by="totally-legit")
            rec = FE.gate(tmp, SKILL, FEATURE, "fix-green")
            self.assertIsNotNone(rec)
            self.assertEqual(rec["produced_by"], "record_gate",
                             "payload перебил служебное поле записи")

    def test_override_allows_close(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-green")
            ov = tmp / "ground" / "statements" / SKILL / FEATURE / "overrides"
            ov.mkdir(parents=True, exist_ok=True)
            (ov / "gate-result-fix-green.json").write_text(
                json.dumps({"reason": "тест: гейт неприменим"}), encoding="utf-8")
            r = _close(tmp, "fix-green")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_gate_step_not_affected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp, skill=FULL_SKILL, steps=["01-grounding"])
            # 01-grounding не в GATE_RESULT_PREFIXES, но требует содержательный инвентарь:
            # по нему работает check_taskplan, и пустой инвентарь молча выродил бы его
            # кросс-чеки. Кладём инвентарь, чтобы проверять именно gate-result.
            inv = tmp / "ground" / "inventory"
            inv.mkdir(parents=True, exist_ok=True)
            (inv / "grounding-excerpt.json").write_text(
                json.dumps({"modules": [{"name": "svc"}], "entities": []}), encoding="utf-8")
            r = _close(tmp, "01-grounding", skill=FULL_SKILL)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_grounding_blocked_on_empty_inventory(self):
        """Пустой инвентарь не закрывает 01-grounding: дальше пайплайн зовёт check_taskplan,
        и без инвентаря его кросс-чеки reuses/модулей молча не выполняются."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp, skill=FULL_SKILL, steps=["01-grounding"])
            inv = tmp / "ground" / "inventory"
            inv.mkdir(parents=True, exist_ok=True)
            (inv / "grounding-excerpt.json").write_text(
                json.dumps({"modules": [], "entities": []}), encoding="utf-8")
            r = _close(tmp, "01-grounding", skill=FULL_SKILL)
            # rc=2 — осознанный отказ гейта (StepGateBlocked), не крах скрипта. Раньше здесь
            # был rc=1 от НЕПЕРЕХВАЧЕННОГО RuntimeError: причина отказа тонула в хвосте
            # трейсбека, а код возврата был неотличим от настоящей поломки.
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("инвентарь пуст", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_fix_diag_without_artifact_blocked(self):
        # fix-diag закрывался «со слов субагента» — судей у fix-* нет, evidence обязателен
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-diag")
            r = _close(tmp, "fix-diag")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("record_gate", r.stderr)

    def test_fix_diag_with_passed_artifact_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-diag")
            _write_design_docs(tmp)
            self.assertEqual(_record(tmp, "fix-diag", "--cmd", _stub_gate(tmp, "check_taskplan.py")).returncode, 0)
            r = _close(tmp, "fix-diag")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_fix_diag_blocked_when_doc_named_by_task_slug(self):
        """Гейт прошёл, но план записан как <KEY>.md — для fix-red/fix-green его нет."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_origin(tmp, "fix-diag")
            docs = _diag_docs_dir(tmp)
            docs.mkdir(parents=True, exist_ok=True)
            (docs / f"{FEATURE}.md").write_text("# fix plan", encoding="utf-8")
            (docs / "task-plan.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_record(tmp, "fix-diag", "--cmd", _stub_gate(tmp, "check_taskplan.py")).returncode, 0)
            r = _close(tmp, "fix-diag")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("fix-plan.md", r.stderr)

    def test_record_gate_rejects_command_without_the_gate(self):
        """Подделка СУБСТАНЦИИ: `--cmd "true"` давал валидный passed:true и закрывал шаг."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            r = _record(tmp, "fix-diag", "--cmd", "true")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("check_taskplan.py", r.stderr)
            self.assertIsNone(FE.gate(tmp, SKILL, FEATURE, "fix-diag"),
                              "evidence не должно записываться при отказе")

    def test_fix_intake_scope_gate_required(self):
        # скоуп-чек (check_fix_scope) нельзя молча пропустить: без evidence fix-intake не закрыть.
        # inputs.story в манифесте — отдельный гейт (required_decisions_on_close), здесь он
        # удовлетворён заранее, чтобы пин проверял именно evidence скоуп-чека.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp, inputs={"story": "STOR-1"})
            self.assertNotEqual(_close(tmp, "fix-intake").returncode, 0)
            self.assertEqual(_record(tmp, "fix-intake", "--cmd", _stub_gate(tmp, "check_fix_scope.py")).returncode, 0)
            self.assertEqual(_close(tmp, "fix-intake").returncode, 0)



class TestGateDenyShape(unittest.TestCase):
    """Форма отказа гейта закрытия (найдено e2e-прогоном на qwen CLI).

    Семь гейтов `update.py` бросали голый RuntimeError, а main() звался без обёртки — рантайм
    получал ТРЕЙСБЕК с rc=1. Для модели это «скрипт сломан» (лезет чинить update.py вместо
    того, чтобы выполнить требование), текст отказа — в хвосте stderr после стека, а rc=1
    неотличим от настоящего краха."""

    def test_deny_is_clean_and_rc2(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            r = _close(tmp, "fix-diag")          # нет ни origin, ни gate-evidence
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("[update] DENY:", r.stderr)

    def test_override_recipe_points_at_record_approval(self):
        """Рецепт снятия гейта не должен вести в стену: рукописный маркер в approvals/
        режет state-write-guard, и gate-guard его не засчитывает без провенанса."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            r = _close(tmp, "fix-diag")
            self.assertIn("record_approval.py", r.stderr)
            self.assertNotIn('{"approved_by": "user"', r.stderr)


if __name__ == "__main__":
    unittest.main()
