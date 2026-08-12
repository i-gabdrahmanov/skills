#!/usr/bin/env python3
"""Smoke test for hooks/destructive-blocker.py.

Раньше здесь был авто-стаб с `import destructive-blocker as mod` — это SyntaxError (дефис в имени), поэтому
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

HOOK = Path(__file__).resolve().parent / "destructive-blocker.py"


def _run(command: str):
    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": ".",
                          "tool_name": "run_shell_command", "tool_input": {"command": command}})
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


class TBlacklistForms(unittest.TestCase):
    """M4: формы деструктива, мимо которых проходил policy-regex."""

    def test_block_short_force_push(self):
        self.assertEqual(_run("git push -f origin main").returncode, 2)

    def test_block_short_force_push_cluster(self):
        self.assertEqual(_run("git push -fv origin main").returncode, 2)

    def test_allow_force_with_lease_non_protected(self):
        # моя правка (core) НЕ блокирует --force-with-lease; protected-ветку (origin/main/master)
        # отдельно режет предсуществующая policy-строка — здесь ветка непротектед → проходит.
        self.assertEqual(_run("git push --force-with-lease upstream hotfix").returncode, 0)

    def test_block_python_rmtree_root(self):
        self.assertEqual(
            _run("python3 -c \"import shutil; shutil.rmtree('/')\"").returncode, 2)

    def test_block_base64_pipe_sh(self):
        self.assertEqual(_run("echo aGVsbG8= | base64 -d | bash").returncode, 2)

    def test_block_xargs_rm(self):
        self.assertEqual(_run("echo /tmp/x | xargs rm -rf").returncode, 2)

    def test_allow_benign_push(self):
        self.assertEqual(_run("git push origin feature/x").returncode, 0)

    def test_block_force_push_via_git_C(self):
        # `git -C <path>` перед push обходил force-push-паттерн (детект по git\s+push)
        self.assertEqual(_run("git -C . push --force origin main").returncode, 2)

    def test_block_force_push_via_git_c_config(self):
        self.assertEqual(_run("git -c user.name=x push -f origin main").returncode, 2)


if __name__ == "__main__":
    unittest.main()
