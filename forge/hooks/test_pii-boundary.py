#!/usr/bin/env python3
"""Smoke test for hooks/pii-boundary.py.

Раньше здесь был авто-стаб с `import pii-boundary as mod` — это SyntaxError (дефис в имени), поэтому
тест НИКОГДА не запускался (как и весь набор test_*.py хуков). Теперь: модуль грузится через
importlib (ловит регрессии синтаксиса/импорта) и проверяется fail-open на пустом stdin (общий
контракт хуков — не ронять инструмент на не-JSON входе). Поведенческое покрытие — hooks/evals/run-evals.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "pii-boundary.py"


def _run(tool_name: str, tool_input: dict):
    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": ".",
                          "tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
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


class TPythonWriteVector(unittest.TestCase):
    """M5: запись PII через inline-python (без shell-редиректа) — раньше проходила мимо _target."""

    def test_block_open_write_pii_to_src_main(self):
        cmd = "python3 -c \"open('src/main/java/X.java','w').write('user@example.com')\""
        r = _run("run_shell_command", {"command": cmd})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_pathlib_write_text_pii(self):
        cmd = ("python3 -c \"from pathlib import Path; "
               "Path('src/main/X.java').write_text('AKIA1234567890ABCDEF')\"")
        r = _run("run_shell_command", {"command": cmd})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allow_pii_into_test_scope(self):
        cmd = "python3 -c \"open('src/test/Fixtures.java','w').write('user@example.com')\""
        r = _run("run_shell_command", {"command": cmd})
        self.assertEqual(r.returncode, 0, r.stderr)


class TestPlaceholdersAreNotSecrets(unittest.TestCase):
    """Задача 011: `password: ${DB_PASSWORD}` — ссылка на секрет, а не секрет.

    Пока паттерн credential'а не отличал одно от другого, запись обычного application.yml
    блокировалась целиком."""

    def _write(self, content: str):
        return _run("write_file", {"file_path": "src/main/resources/application.yml",
                                   "content": content})

    def test_env_placeholder_allowed(self):
        for value in ("${DB_PASSWORD}", '"${DB_PASSWORD}"', "{{ vault_password }}",
                      "<your-token-here>", "changeit", "***REDACTED***"):
            self.assertEqual(self._write(f"  password: {value}\n").returncode, 0,
                             f"ложный блок на плейсхолдере {value}")

    def test_real_secret_still_blocked(self):
        self.assertEqual(self._write("  password: hunter2secret\n").returncode, 2)

    def test_aws_key_still_blocked(self):
        r = _run("write_file", {"file_path": "docs/x.md",
                                "content": "api_key = AKIAIOSFODNN7EXAMPLE"})
        self.assertEqual(r.returncode, 2)


class TestAllowedScopePrecedence(unittest.TestCase):
    """Задача 012: `A or B and C` связывает `and` сильнее, и ветка resources была мертва
    (is_test_path выше уже вернул False, значит второй операнд всегда False)."""

    def test_test_resources_allowed(self):
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("pii_boundary", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(m._allowed_scope("src/test/resources/fixtures/users.json"))
        self.assertTrue(m._allowed_scope("ground/inventory/scan.json"))
        self.assertFalse(m._allowed_scope("src/main/resources/application.yml"))


if __name__ == "__main__":
    unittest.main()
