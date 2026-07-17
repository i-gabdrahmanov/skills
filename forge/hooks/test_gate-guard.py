#!/usr/bin/env python3
"""Smoke test for hooks/gate-guard.py.

Раньше здесь был авто-стаб с `import gate-guard as mod` — это SyntaxError (дефис в имени), поэтому
тест НИКОГДА не запускался (как и весь набор test_*.py хуков). Теперь: модуль грузится через
importlib (ловит регрессии синтаксиса/импорта) и проверяется fail-open на пустом stdin (общий
контракт хуков — не ронять инструмент на не-JSON входе). Поведенческое покрытие — hooks/evals/run-evals.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "gate-guard.py"


def _bash_payload(command: str, cwd: str) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "cwd": cwd,
                       "tool_name": "Bash", "tool_input": {"command": command}})


def _run(command: str, cwd: str):
    return subprocess.run([sys.executable, str(HOOK)], input=_bash_payload(command, cwd),
                          capture_output=True, text=True, timeout=30)


class T(unittest.TestCase):
    def test_module_loads(self):
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)          # регрессия синтаксиса/импорта
        self.assertTrue(hasattr(m, "main"))

    def test_failopen_empty_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


class TGateOverride(unittest.TestCase):
    """Пин: снятие детерминированного гейта (override_judge.py) — R4-класс.
    Раньше override требовал только --reason и печатался готовой командой в deny-баннерах —
    модель снимала step-reopen/gate-result/судей одной командой без человека."""

    CMD = ("python3 .gigacode/skills/pipeline-state/scripts/override_judge.py "
           "--judge step-reopen-04-build-T1 --feature f1 --step-id 04-build-T1 "
           "--reason \"ещё итерация\"")

    def test_create_without_approval_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("gate-override-step-reopen-04-build-T1.json", r.stderr)

    def test_create_with_approval_passes(self):
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            # маркер засчитывается только с провенансом record_approval (как пишет record_approval.py)
            (appr / "gate-override-step-reopen-04-build-T1.json").write_text(
                json.dumps({"produced_by": "record_approval", "approved_by": "user",
                            "reason": "ok"}), encoding="utf-8")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_handwritten_approval_without_provenance_blocked(self):
        # BLOCKER-1 backstop: маркер БЕЗ produced_by:"record_approval" (самовыписанный) не снимает гейт
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / "gate-override-step-reopen-04-build-T1.json").write_text(
                json.dumps({"approved_by": "user", "reason": "ok"}), encoding="utf-8")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "рукописный маркер без провенанса не должен снимать гейт")
            self.assertIn("провенанс", r.stderr.lower())

    def test_foreign_approval_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / "gate-override-coverage-judge.json").write_text("{}", encoding="utf-8")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "approval чужого судьи не должен снимать этот гейт")

    def test_reason_text_containing_list_is_not_readonly(self):
        # M2: --list ВНУТРИ значения --reason не должен трактоваться как readonly-флаг (обход)
        with tempfile.TemporaryDirectory() as td:
            cmd = ("python3 .gigacode/skills/pipeline-state/scripts/override_judge.py "
                   "--judge step-reopen-04-build-T1 --feature f1 --step-id 04-build-T1 "
                   "--reason \"cleanup --list marker\"")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 2,
                             "--list в тексте --reason не снимает approval-гейт")

    def test_list_and_remove_are_free(self):
        with tempfile.TemporaryDirectory() as td:
            base = "python3 .gigacode/skills/pipeline-state/scripts/override_judge.py --feature f1"
            r = _run(f"{base} --list", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(f"{base} --judge coverage-judge --remove", td)
            self.assertEqual(r.returncode, 0,
                             f"--remove (восстановление enforcement) не гейтится: {r.stderr}")


class TRollback(unittest.TestCase):
    """Пин: откат пайплайна (rollback.py) — R4-класс, deny-first. Уничтожает рабочие
    результаты (код, evidence шагов) и порождает сирот в Jira/PR — без approval-маркера
    rollback-<feature>-<to-step> (провенанс record_approval) скрипт не запускается;
    classify дал бы команде default-R1 — без deny-first прошёл бы авто."""

    CMD = ("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
           "--skill feature-pipeline --feature f1 --to-step 02-sdd")

    def _approve(self, td: str, key: str, provenance: bool = True) -> None:
        appr = Path(td) / "ground" / "approvals"
        appr.mkdir(parents=True, exist_ok=True)
        body = {"approved_by": "user", "reason": "ok"}
        if provenance:
            body["produced_by"] = "record_approval"
        (appr / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")

    def test_rollback_without_approval_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("rollback-f1-02-sdd.json", r.stderr)

    def test_rollback_with_approval_passes(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "rollback-f1-02-sdd")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_handwritten_marker_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "rollback-f1-02-sdd", provenance=False)
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "рукописный маркер без провенанса не снимает гейт")
            self.assertIn("провенанс", r.stderr.lower())

    def test_foreign_marker_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "rollback-f1-04-build-T1")  # согласие на ДРУГОЙ шаг
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "approval другого шага не снимает этот гейт")

    def test_dry_run_and_list_are_free(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(f"{self.CMD} --dry-run", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                     "--skill feature-pipeline --feature f1 --list", td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_dry_run_inside_value_is_not_readonly(self):
        # --dry-run внутри значения аргумента не должен трактоваться как readonly (обход)
        with tempfile.TemporaryDirectory() as td:
            cmd = ("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                   "--skill feature-pipeline --feature \"f1 --dry-run\" --to-step 02-sdd")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 2, "--dry-run в тексте значения не снимает гейт")

    def test_missing_target_args_blocked(self):
        # ключ маркера не резолвится без --feature/--to-step → deny с пояснением
        with tempfile.TemporaryDirectory() as td:
            r = _run("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                     "--skill feature-pipeline", td)
            self.assertEqual(r.returncode, 2)
            self.assertIn("не резолвится", r.stderr)

    def test_to_phase_uses_same_key_scheme(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = ("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                   "--skill feature-pipeline --feature f1 --to-phase 02-sdd")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self._approve(td, "rollback-f1-02-sdd")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 0, r.stderr)


def _write_run(file_path: str, cwd: str):
    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": cwd,
                          "tool_name": "write_file", "tool_input": {"file_path": file_path}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


class TRequiredDecisions(unittest.TestCase):
    """Thrust 1 fail-closed: продуктивная запись фазы блокируется без записанного решения."""

    @staticmethod
    def _mk(td: str, spec: str | None = None):
        d = Path(td) / "ground" / "statements" / "forgelite" / "f1"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"steps": [{"id": "lite-design", "status": "in_progress"}]}),
            encoding="utf-8")
        cfg = {"autonomy": {"criticality": "medium", "auto_max_risk": "R2"}}
        if spec:
            cfg["sources"] = {"spec": spec}
        (Path(td) / "ground" / "pipeline.json").write_text(json.dumps(cfg), encoding="utf-8")

    def test_write_blocked_without_required_decision(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td)
            r = _write_run("docs/feature-pipeline/f1/tech-design.md", td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("sources.spec", r.stderr)

    def test_write_passes_when_decision_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, spec="docs/feature-pipeline/f1/existing-spec.md")
            r = _write_run("docs/feature-pipeline/f1/tech-design.md", td)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
