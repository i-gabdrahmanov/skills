#!/usr/bin/env python3
"""Smoke test for hooks/state-recorder.py.

Раньше здесь был авто-стаб с `import state-recorder as mod` — это SyntaxError (дефис в имени), поэтому
тест НИКОГДА не запускался (как и весь набор test_*.py хуков). Теперь: модуль грузится через
importlib (ловит регрессии синтаксиса/импорта) и проверяется fail-open на пустом stdin (общий
контракт хуков — не ронять инструмент на не-JSON входе). Поведенческое покрытие — hooks/evals/run-evals.py.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "state-recorder.py"


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


def _mod():
    sys.path.insert(0, str(HOOK.parent))
    spec = importlib.util.spec_from_file_location("sr_under_test", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class ExtractFinalJson(unittest.TestCase):
    """Выбор ФИНАЛЬНОГО ответа субагента.

    Регресс: кандидаты сортировались по ДЛИНЕ, и любой более длинный JSON в сообщении (вывод
    record_gate, отчёт покрытия, эхо task-plan) вытеснял контрактный. `step_id` в нём нет →
    хук молча не писал ни origin-evidence, ни закрытие шага: фаза оставалась открытой при
    отработавшем субагенте."""

    def setUp(self):
        self.m = _mod()

    def test_contract_wins_over_longer_gate_output(self):
        gate = ('{"status":"ok","passed":true,"cmd":"./gradlew build",'
                '"modules":["a","b"],"duration_ms":123456,"note":"' + "x" * 200 + '"}')
        final = '{"step_id":"lite-green","status":"completed","build_ok":true}'
        got = self.m._extract_json(f"Гейт:\n```json\n{gate}\n```\nИтог:\n```json\n{final}\n```")
        self.assertEqual(got.get("step_id"), "lite-green", got)
        self.assertEqual(got.get("status"), "completed", got)

    def test_last_contract_json_wins_on_repeat(self):
        a = '{"step_id":"fix-red","status":"failed"}'
        b = '{"step_id":"fix-red","status":"completed","tests_failed":true}'
        got = self.m._extract_json(f"{a}\nпереписал тест\n{b}")
        self.assertEqual(got.get("status"), "completed", got)

    def test_nested_object_does_not_shadow_outer(self):
        # вложенный объект НЕ должен побеждать: иначе теряются step_id и status
        final = ('{"step_id":"lite-verify","status":"completed",'
                 '"coverage_gate":{"status":"ok","percent":0.91,"files":["A.java","B.java"]}}')
        got = self.m._extract_json(f"Итог:\n{final}")
        self.assertEqual(got.get("step_id"), "lite-verify", got)
        self.assertEqual(self.m._status_from(got), "completed", got)

    def test_no_contract_json_returns_last_object(self):
        got = self.m._extract_json('{"a":1}\nтекст\n{"b":2}')
        self.assertEqual(got, {"b": 2}, got)

    def test_prose_braces_do_not_break(self):
        self.assertIsNone(self.m._extract_json("используй {скобки} в тексте"))


if __name__ == "__main__":
    unittest.main()
