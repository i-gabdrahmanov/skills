#!/usr/bin/env python3
"""test_windows_file_lock_fallback.py — регресс: fcntl (Unix-only) падал ImportError на Windows.

Находка Windows-аудита: безусловный `import fcntl` — модуля физически нет на Windows,
значит ImportError уже на загрузке модуля. Замок вынесен в ЕДИНЫЙ источник
`_project.append_locked` с platform-fallback'ом на msvcrt.locking, поэтому тест бьёт
именно по `_project`.

Тест эмулирует Windows in-process: sys.modules["fcntl"] = None форсит ImportError
при `import fcntl` (документированное поведение CPython), поддельный sys.modules
["msvcrt"] ловит вызовы вместо реального (которого на macOS/Linux физически нет).
Модуль грузится через importlib СВЕЖИМ — иначе моки в sys.modules теста не видны.
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


class TestProjectLockFallback(unittest.TestCase):
    def test_loads_without_fcntl_and_uses_msvcrt(self):
        fake = _FakeMsvcrt()
        mod = _load_module_without_fcntl("project", HOOKS / "_project.py", fake)
        self.assertIsNone(mod.fcntl, "должен деградировать в None, не пробросить ImportError")

    def test_append_locked_locks_and_unlocks_via_msvcrt(self):
        fake = _FakeMsvcrt()
        mod = _load_module_without_fcntl("project", HOOKS / "_project.py", fake)
        with tempfile.TemporaryDirectory() as td:
            p = str(Path(td) / "sub" / "agents.log")  # dirname создаётся append_locked
            mod.append_locked(p, "line1\n")
            self.assertEqual(Path(p).read_text(encoding="utf-8"), "line1\n")
        self.assertIn((mock.ANY, fake.LK_LOCK, 1), fake.calls)
        self.assertIn((mock.ANY, fake.LK_UNLCK, 1), fake.calls)


if __name__ == "__main__":
    unittest.main()
