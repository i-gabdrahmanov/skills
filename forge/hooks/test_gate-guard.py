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


if __name__ == "__main__":
    unittest.main()
