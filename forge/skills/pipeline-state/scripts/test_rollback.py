#!/usr/bin/env python3
"""Tests for rollback.py — откат пайплайна к шагу X («X переделывается»).

Ключевые инварианты: reset-set = X + всё после + depends_on-замыкание; evidence
архивируется (не удаляется) и повторное закрытие reset-шага БЛОКИРУЕТСЯ update.py;
код восстанавливается точечно по скоупу журнала (ручные правки вне журнала целы);
без approval-маркера — exit 3 и ни одного изменения; --dry-run readonly.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import checkpoint  # noqa: E402

ROLLBACK = HERE / "rollback.py"
UPDATE = HERE / "update.py"

SKILL = "feature-pipeline"
FEATURE = "feat"
FUTURE_TS = "2099-01-01T00:00:00Z"  # заведомо позже любого чекпойнта


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _fdir(tmp: Path) -> Path:
    return tmp / "ground" / "statements" / SKILL / FEATURE


def _steps() -> list[dict]:
    return [
        {"id": "00-brd", "status": "completed", "depends_on": []},
        {"id": "01-grounding", "status": "completed", "depends_on": []},
        {"id": "02-sdd", "status": "completed", "depends_on": ["00-brd", "01-grounding"],
         "required_judges": ["sdd-judge"], "reopens": 2},
        {"id": "02-design", "status": "completed", "depends_on": ["02-sdd"],
         "required_judges": ["design-judge"]},
        {"id": "03-jira", "status": "completed", "depends_on": ["02-design"],
         "artifacts": {"jira-result": "docs/feature-pipeline/feat/jira-tasks-result.json"}},
        {"id": "04-test-T1", "status": "completed", "depends_on": ["02-design"]},
        {"id": "04-build-T1", "status": "completed", "depends_on": ["04-test-T1"],
         "failures": 1},
        {"id": "07-report", "status": "pending", "depends_on": []},
    ]


def _make_project(tmp: Path, steps: list[dict] | None = None) -> None:
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "src").mkdir()
    (tmp / "src" / "Main.java").write_text("v1", encoding="utf-8")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "init")
    d = _fdir(tmp)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "version": 1, "skill": SKILL, "feature": FEATURE,
        "steps": steps if steps is not None else _steps(),
    }), encoding="utf-8")


def _manifest(tmp: Path) -> dict:
    return json.loads((_fdir(tmp) / "manifest.json").read_text(encoding="utf-8"))


def _step(tmp: Path, sid: str) -> dict | None:
    return next((s for s in _manifest(tmp)["steps"] if s["id"] == sid), None)


def _write_approval(tmp: Path, key: str) -> None:
    d = tmp / "ground" / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps({
        "produced_by": "record_approval", "key": key,
        "approved_by": "user", "reason": "test",
    }), encoding="utf-8")


def _write_evidence(tmp: Path) -> None:
    d = _fdir(tmp)
    for sub, name, body in (
        ("_origins", "02-sdd.json", {"step_id": "02-sdd"}),
        ("judges", "sdd-judge.json", {"produced_by": "run_judge", "passed": True, "verdict": "PASS"}),
        ("judges", "design-judge.json", {"produced_by": "run_judge", "passed": True, "verdict": "PASS"}),
        ("gates", "04-build-T1.json", {"produced_by": "record_gate", "passed": True}),
    ):
        (d / sub).mkdir(parents=True, exist_ok=True)
        (d / sub / name).write_text(json.dumps(body), encoding="utf-8")
    ap = tmp / "ground" / "approvals"
    ap.mkdir(parents=True, exist_ok=True)
    (ap / f"sdd-approved-{FEATURE}.json").write_text(json.dumps({
        "produced_by": "record_approval", "key": f"sdd-approved-{FEATURE}",
    }), encoding="utf-8")


def _journal(tmp: Path, *paths: str, op: str = "write") -> None:
    d = _fdir(tmp) / "journal"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "files.jsonl", "a", encoding="utf-8") as f:
        for p in paths:
            f.write(json.dumps({"ts": FUTURE_TS, "op": op, "paths": [p]}) + "\n")


def _run(tmp: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROLLBACK), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, *extra],
        capture_output=True, text=True, timeout=60,
    )


class TStateSurgery(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        _make_project(self.tmp)
        checkpoint.create_checkpoint(self.tmp, FEATURE, "01-grounding")
        _write_evidence(self.tmp)
        _write_approval(self.tmp, f"rollback-{FEATURE}-02-sdd")

    def tearDown(self):
        self._td.cleanup()

    def _rollback(self) -> subprocess.CompletedProcess:
        return _run(self.tmp, "--to-step", "02-sdd", "--no-code")

    def test_reset_set_and_predecessor_intact(self):
        r = self._rollback()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_step(self.tmp, "02-sdd")["status"], "pending")
        self.assertEqual(_step(self.tmp, "03-jira")["status"], "pending")
        self.assertEqual(_step(self.tmp, "00-brd")["status"], "completed")
        self.assertEqual(_step(self.tmp, "01-grounding")["status"], "completed")

    def test_dynamic_steps_removed_on_design_reset(self):
        r = self._rollback()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(_step(self.tmp, "04-test-T1"))
        self.assertIsNone(_step(self.tmp, "04-build-T1"))
        # container/main-шаги остаются
        self.assertIsNotNone(_step(self.tmp, "07-report"))
        hist = _manifest(self.tmp)["rollback_history"][0]
        self.assertEqual({s["id"] for s in hist["dynamic_removed"]},
                         {"04-test-T1", "04-build-T1"})

    def test_dynamic_steps_kept_on_task_level_rollback(self):
        _write_approval(self.tmp, f"rollback-{FEATURE}-04-build-T1")
        r = _run(self.tmp, "--to-step", "04-build-T1", "--no-code")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_step(self.tmp, "04-build-T1")["status"], "pending")
        self.assertEqual(_step(self.tmp, "04-test-T1")["status"], "completed")
        self.assertEqual(_step(self.tmp, "02-design")["status"], "completed")

    def test_counters_zeroed_and_recorded(self):
        r = self._rollback()
        self.assertEqual(r.returncode, 0, r.stderr)
        step = _step(self.tmp, "02-sdd")
        self.assertNotIn("reopens", step)
        hist = _manifest(self.tmp)["rollback_history"][0]
        self.assertEqual(hist["prev_counters"]["02-sdd"]["reopens"], 2)

    def test_evidence_archived_not_deleted(self):
        r = self._rollback()
        self.assertEqual(r.returncode, 0, r.stderr)
        d = _fdir(self.tmp)
        self.assertFalse((d / "_origins" / "02-sdd.json").exists())
        self.assertFalse((d / "judges" / "sdd-judge.json").exists())
        self.assertFalse((d / "gates" / "04-build-T1.json").exists())
        self.assertFalse((self.tmp / "ground" / "approvals" / f"sdd-approved-{FEATURE}.json").exists())
        archives = list((d / "rollbacks").iterdir())
        self.assertEqual(len(archives), 1)
        arch = archives[0]
        self.assertTrue((arch / "_origins" / "02-sdd.json").exists())
        self.assertTrue((arch / "judges" / "sdd-judge.json").exists())
        self.assertTrue((arch / "gates" / "04-build-T1.json").exists())
        self.assertTrue((arch / "approvals" / f"sdd-approved-{FEATURE}.json").exists())

    def test_approval_marker_consumed(self):
        key = f"rollback-{FEATURE}-02-sdd"
        r = self._rollback()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.tmp / "ground" / "approvals" / f"{key}.json").exists())
        # второй откат тем же согласием невозможен
        r2 = self._rollback()
        self.assertEqual(r2.returncode, 3, r2.stdout + r2.stderr)

    def test_reclose_blocked_after_rollback(self):
        # КЛЮЧЕВОЙ негативный: после отката повторное закрытие 02-sdd не проходит
        # по старым доказательствам (origin/judges/approval архивированы)
        r = self._rollback()
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = subprocess.run(
            [sys.executable, str(UPDATE), "--project", str(self.tmp), "--skill", SKILL,
             "--feature", FEATURE, "--step-id", "02-sdd", "--status", "completed"],
            capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r2.returncode, 0)

    def test_orphan_warning_for_jira(self):
        jdir = self.tmp / "docs" / "feature-pipeline" / FEATURE
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / "jira-tasks-result.json").write_text(json.dumps({
            "story": {"key": "STOR-1"}, "tasks": [{"key": "STOR-2"}, {"key": "STOR-3"}],
        }), encoding="utf-8")
        r = self._rollback()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("СИРОТЫ Jira", r.stderr)
        self.assertIn("STOR-1", r.stderr)
        self.assertIn("Черновик комментария", r.stderr)


class TGuards(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        _make_project(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def test_no_approval_exit3_nothing_changed(self):
        _write_evidence(self.tmp)
        r = _run(self.tmp, "--to-step", "02-sdd", "--no-code")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(_step(self.tmp, "02-sdd")["status"], "completed")
        self.assertTrue((_fdir(self.tmp) / "judges" / "sdd-judge.json").exists())

    def test_handwritten_marker_rejected(self):
        d = self.tmp / "ground" / "approvals"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"rollback-{FEATURE}-02-sdd.json").write_text('{"approved_by": "user"}',
                                                           encoding="utf-8")
        r = _run(self.tmp, "--to-step", "02-sdd", "--no-code")
        self.assertEqual(r.returncode, 3)

    def test_dry_run_changes_nothing_without_approval(self):
        r = _run(self.tmp, "--to-step", "02-sdd", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertTrue(plan["dry_run"])
        self.assertIn("02-sdd", plan["reset_steps"])
        self.assertEqual(_step(self.tmp, "02-sdd")["status"], "completed")
        self.assertNotIn("rollback_history", _manifest(self.tmp))

    def test_in_progress_step_blocks(self):
        steps = _steps()
        steps[5]["status"] = "in_progress"  # 04-test-T1
        (_fdir(self.tmp) / "manifest.json").write_text(json.dumps({
            "version": 1, "skill": SKILL, "feature": FEATURE, "steps": steps,
        }), encoding="utf-8")
        _write_approval(self.tmp, f"rollback-{FEATURE}-02-sdd")
        r = _run(self.tmp, "--to-step", "02-sdd", "--no-code")
        self.assertEqual(r.returncode, 2)
        self.assertIn("in_progress", r.stderr)

    def test_unknown_step_errors(self):
        r = _run(self.tmp, "--to-step", "99-nope", "--dry-run")
        self.assertEqual(r.returncode, 2)

    def test_to_phase_resolves_first_step(self):
        r = _run(self.tmp, "--to-phase", "02-sdd", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["to_step"], "02-sdd")

    def test_list_readonly(self):
        r = _run(self.tmp, "--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("checkpoints", out)
        self.assertIn("rollback_history", out)


class TCodeRestore(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        _make_project(self.tmp)
        # чекпойнт «после 01-grounding»: Main.java=v1, Manual.java=m1
        (self.tmp / "src" / "Manual.java").write_text("m1", encoding="utf-8")
        checkpoint.create_checkpoint(self.tmp, FEATURE, "01-grounding")
        # изменения «фаз 02+»: Main.java правится, New.java создаётся (оба в журнале),
        # Manual.java правит человек мимо пайплайна (в журнале НЕТ)
        (self.tmp / "src" / "Main.java").write_text("v2", encoding="utf-8")
        (self.tmp / "src" / "New.java").write_text("new", encoding="utf-8")
        (self.tmp / "src" / "Manual.java").write_text("m2-manual", encoding="utf-8")
        _journal(self.tmp, "src/Main.java", "src/New.java")
        _write_evidence(self.tmp)
        _write_approval(self.tmp, f"rollback-{FEATURE}-02-sdd")

    def tearDown(self):
        self._td.cleanup()

    def test_scoped_restore(self):
        r = _run(self.tmp, "--to-step", "02-sdd")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / "src" / "Main.java").read_text(encoding="utf-8"), "v1")
        self.assertFalse((self.tmp / "src" / "New.java").exists())
        # ручная правка вне журнала НЕ тронута
        self.assertEqual((self.tmp / "src" / "Manual.java").read_text(encoding="utf-8"),
                         "m2-manual")
        out = json.loads(r.stdout)
        self.assertEqual(out["code_restored"], 1)
        self.assertEqual(out["code_deleted"], 1)

    def test_ground_never_restored(self):
        r = _run(self.tmp, "--to-step", "02-sdd")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # манифест после отката отражает хирургию, а не состояние чекпойнта
        self.assertEqual(_step(self.tmp, "02-sdd")["status"], "pending")
        self.assertIn("rollback_history", _manifest(self.tmp))

    def test_unscoped_restores_manual_edit_too(self):
        r = _run(self.tmp, "--to-step", "02-sdd", "--unscoped")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / "src" / "Manual.java").read_text(encoding="utf-8"), "m1")
        self.assertEqual((self.tmp / "src" / "Main.java").read_text(encoding="utf-8"), "v1")

    def test_empty_journal_warns_and_restores_nothing(self):
        (_fdir(self.tmp) / "journal" / "files.jsonl").unlink()
        r = _run(self.tmp, "--to-step", "02-sdd")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / "src" / "Main.java").read_text(encoding="utf-8"), "v2")
        self.assertIn("--unscoped", r.stderr)

    def test_no_checkpoint_refuses_code(self):
        checkpoint.delete_checkpoints(self.tmp, FEATURE)
        r = _run(self.tmp, "--to-step", "02-sdd")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # state откачен, код цел, warning про отсутствие чекпойнтов
        self.assertEqual(_step(self.tmp, "02-sdd")["status"], "pending")
        self.assertEqual((self.tmp / "src" / "Main.java").read_text(encoding="utf-8"), "v2")
        self.assertIn("чекпойнтов нет", r.stderr)

    def test_dry_run_shows_code_plan(self):
        r = _run(self.tmp, "--to-step", "02-sdd", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertIn("src/Main.java", plan["code"]["restore"])
        self.assertIn("src/New.java", plan["code"]["delete"])
        self.assertEqual(plan["code"]["skipped_out_of_scope"], 1)  # Manual.java
        # dry-run ничего не менял
        self.assertEqual((self.tmp / "src" / "Main.java").read_text(encoding="utf-8"), "v2")


if __name__ == "__main__":
    unittest.main()
