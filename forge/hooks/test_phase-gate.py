#!/usr/bin/env python3
"""Smoke test for hooks/phase-gate.py.

Раньше здесь был авто-стаб с `import phase-gate as mod` — это SyntaxError (дефис в имени), поэтому
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
import uuid
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "phase-gate.py"


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)


def _seed(root: Path, steps: list, events: list | None = None) -> None:
    d = root / "ground" / "statements" / "forgefix" / "BUG-1"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
    if events:
        (d / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
            encoding="utf-8")


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


class TestDanglingDetection(unittest.TestCase):
    """Задача 012: хук ждал `in_progress`, а его никто не проставляет (update.py ведёт
    pending → completed) — гейт не срабатывал ни разу. Теперь висящим считается и pending-шаг,
    по которому в журнале уже есть gate-result."""

    GATE_EVENT = {"kind": "gate", "produced_by": "record_gate", "step_id": "fix-red",
                  "passed": True, "cmd": "./gradlew test"}

    def _payload(self, tmp: Path) -> dict:
        return {"hook_event_name": "Stop", "cwd": str(tmp),
                "session_id": uuid.uuid4().hex}

    def test_pending_with_gate_evidence_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed(tmp, [{"id": "fix-red", "status": "pending"}], events=[self.GATE_EVENT])
            r = _run(self._payload(tmp))
            self.assertIn('"decision": "block"', r.stdout)
            self.assertIn("fix-red", r.stdout)

    def test_pending_without_evidence_is_quiet(self):
        """Шаг просто не начат — это не «висящий», молчим."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed(tmp, [{"id": "fix-red", "status": "pending"}])
            self.assertEqual(_run(self._payload(tmp)).stdout.strip(), "")

    def test_completed_is_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed(tmp, [{"id": "fix-red", "status": "completed"}], events=[self.GATE_EVENT])
            self.assertEqual(_run(self._payload(tmp)).stdout.strip(), "")

    def test_in_progress_still_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed(tmp, [{"id": "fix-green", "status": "in_progress"}])
            self.assertIn('"decision": "block"', _run(self._payload(tmp)).stdout)


class TestLoopProtection(unittest.TestCase):
    """Задача 012: единственной защитой от петли было `stop_hook_active` — поле, которого нет
    в перечне подтверждённых для форка. Второй слой — одноразовый маркер на сессию."""

    def test_second_stop_in_same_session_does_not_block(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed(tmp, [{"id": "fix-green", "status": "in_progress"}])
            payload = {"hook_event_name": "Stop", "cwd": str(tmp),
                       "session_id": uuid.uuid4().hex}
            self.assertIn('"decision": "block"', _run(payload).stdout)
            self.assertEqual(_run(payload).stdout.strip(), "",
                             "повторный Stop в той же сессии по той же причине зациклил бы ход")

    def test_stop_hook_active_is_not_trusted(self):
        """qwen-code 0.21.14 шлёт stop_hook_active=true на КАЖДОМ Stop, включая первый в
        свежей сессии (замерено e2e). Полагаться на него = не блокировать никогда."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed(tmp, [{"id": "fix-green", "status": "in_progress"}])
            r = _run({"hook_event_name": "Stop", "cwd": str(tmp),
                      "session_id": uuid.uuid4().hex, "stop_hook_active": True})
            self.assertIn('"decision": "block"', r.stdout)


if __name__ == "__main__":
    unittest.main()
