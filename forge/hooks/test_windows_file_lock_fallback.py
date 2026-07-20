#!/usr/bin/env python3
"""test_windows_file_lock_fallback.py — регресс: fcntl (Unix-only) падал ImportError на Windows.

Самая критичная находка Windows-аудита: budget-meter.py и log-agent.py безусловно
делали `import fcntl` — модуля физически нет на Windows, значит ImportError уже на
загрузке модуля, а log-agent висит почти на КАЖДОМ событии харнесса (PreToolUse/
PostToolUse/Stop/...). Это не runtime-сбой отдельной функции, а гарантированный
краш всей цепочки хуков на Windows. Починено platform-fallback'ом на msvcrt.locking.

Тест эмулирует Windows in-process: sys.modules["fcntl"] = None форсит ImportError
при `import fcntl` (документированное поведение CPython), поддельный sys.modules
["msvcrt"] ловит вызовы вместо реального (которого на macOS/Linux физически нет).
Модуль грузится через importlib СВЕЖИМ (не subprocess, как smoke-тесты в
test_log-agent.py/test_budget-meter.py) — иначе моки в sys.modules теста не видны
дочернему процессу.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent


class _FakeMsvcrt(types.ModuleType):
    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self):
        super().__init__("msvcrt")
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd, mode, nbytes):
        self.calls.append((fd, mode, nbytes))


def _load_module_without_fcntl(name: str, path: Path, fake_msvcrt: _FakeMsvcrt):
    spec = importlib.util.spec_from_file_location(f"{name}_win", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"fcntl": None, "msvcrt": fake_msvcrt}):
        spec.loader.exec_module(module)
    return module


class TestLogAgentFallback(unittest.TestCase):
    def test_loads_without_fcntl_and_uses_msvcrt(self):
        fake = _FakeMsvcrt()
        mod = _load_module_without_fcntl("log_agent", HOOKS / "log-agent.py", fake)
        self.assertIsNone(mod.fcntl, "должен деградировать в None, не пробросить ImportError")

    def test_append_locks_and_unlocks_via_msvcrt(self):
        fake = _FakeMsvcrt()
        mod = _load_module_without_fcntl("log_agent", HOOKS / "log-agent.py", fake)
        with tempfile.TemporaryDirectory() as td:
            p = str(Path(td) / "sub" / "agents.log")
            mod._append(p, "line1\n")
            self.assertEqual(Path(p).read_text(encoding="utf-8"), "line1\n")
        self.assertIn((mock.ANY, fake.LK_LOCK, 1), fake.calls)
        self.assertIn((mock.ANY, fake.LK_UNLCK, 1), fake.calls)


class TestBudgetMeterFallback(unittest.TestCase):
    def test_loads_without_fcntl_and_uses_msvcrt(self):
        fake = _FakeMsvcrt()
        mod = _load_module_without_fcntl("budget_meter", HOOKS / "budget-meter.py", fake)
        self.assertIsNone(mod.fcntl)

    def test_tally_locks_and_unlocks_via_msvcrt(self):
        fake = _FakeMsvcrt()
        mod = _load_module_without_fcntl("budget_meter", HOOKS / "budget-meter.py", fake)
        with tempfile.TemporaryDirectory() as td:
            p = str(Path(td) / "budget.json")
            state = mod._tally(p, tokens=100, budget=1000, phase="design")
        self.assertEqual(state["total_spent"], 100)
        self.assertIn((mock.ANY, fake.LK_LOCK, 1), fake.calls)
        self.assertIn((mock.ANY, fake.LK_UNLCK, 1), fake.calls)


if __name__ == "__main__":
    unittest.main()
