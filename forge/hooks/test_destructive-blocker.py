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
import tempfile
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


class TRmInsideProject(unittest.TestCase):
    """`rm -rf <абсолютный путь>`: снаружи проекта — деструктив, внутри — штатная уборка.

    tasks/011 расширил «опасную цель» с «ровно / или ~» до любого абсолютного пути, чтобы
    ловить `rm -rf /etc/passwd`. Побочно под блок попал `rm -rf /путь/к/проекту/build` —
    то, что gradle-разработчик набирает каждый день; eval `rm -rf <abs>/build → allow` пинил
    прежнее ожидание и с тех пор был красным. Здесь фиксируем границу целиком, в обе стороны:
    ослабление без этих тестов открыло бы обратно `rm -rf /etc`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name).resolve()
        (self.proj / ".git").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _rm(self, command: str, cwd=None):
        payload = json.dumps({"hook_event_name": "PreToolUse",
                              "cwd": str(self.proj) if cwd is None else cwd,
                              "tool_name": "run_shell_command",
                              "tool_input": {"command": command}})
        return subprocess.run([sys.executable, str(HOOK)], input=payload,
                              capture_output=True, text=True, timeout=30).returncode

    def test_allow_cleanup_inside_project(self):
        for target in ("build", "target", "build/classes", "build/../out"):
            with self.subTest(target=target):
                self.assertEqual(self._rm(f"rm -rf {self.proj}/{target}"), 0)

    def test_allow_several_targets_inside(self):
        self.assertEqual(self._rm(f"rm -rf {self.proj}/build {self.proj}/out"), 0)

    def test_block_project_root_itself(self):
        self.assertEqual(self._rm(f"rm -rf {self.proj}"), 2)

    def test_block_escape_above_root(self):
        self.assertEqual(self._rm(f"rm -rf {self.proj}/../neighbour"), 2)

    def test_block_outside_project(self):
        for target in ("/etc/passwd", "/", "~", "~/Documents", "/usr/local/lib"):
            with self.subTest(target=target):
                self.assertEqual(self._rm(f"rm -rf {target}"), 2)

    def test_block_symlink_pointing_out_of_project(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.proj / "link").symlink_to(outside)
            self.assertEqual(self._rm(f"rm -rf {self.proj}/link"), 2)

    def test_block_glob_inside_project(self):
        """Цель глоба известна только после раскрытия — исключение не выдаём."""
        self.assertEqual(self._rm(f"rm -rf {self.proj}/*"), 2)

    def test_block_mixed_targets(self):
        """Одна внешняя цель отменяет исключение для всей команды."""
        self.assertEqual(self._rm(f"rm -rf {self.proj}/build /etc/x"), 2)

    def test_fail_closed_when_root_unknown(self):
        """Корень не резолвится → остаёмся строгими, а не открываемся."""
        self.assertEqual(self._rm(f"rm -rf {self.proj}/build", cwd="/nonexistent-xyz"), 2)


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


class TestPrecision(unittest.TestCase):
    """Задача 011: блокировщик резал штатную работу и при этом пропускал настоящий деструктив.

    Дыра: у `\\b-` нет границы (перед дефисом пробел, оба символа не-словесные), поэтому
    лукахеды `\\b(?:-[a-z]*r[a-z]*)\\b` не срабатывали никогда — второй core-паттерн был мёртв,
    и `rm -rf /etc/passwd` проходил насквозь."""

    PASS = [
        "rm -rf build/tmp",                     # относительная цель — штатная уборка
        "rm -rf ./target",
        "rm -rf node_modules",
        'echo "-- DROP TABLE users" >> notes.md',   # SQL в тексте, не в исполнении
        "find . -name '*.tmp' | xargs rm",          # уборка в рабочем каталоге
    ]
    BLOCK = [
        "rm -rf /etc/passwd",                   # ← раньше проходило
        "rm -rf /usr/local/lib",                # ← раньше проходило
        "rm -rf ~/Documents",
        "rm -rf /",
        "rm -rf *",
        'psql -c "DROP TABLE users"',
        "DROP TABLE users;",
        "find / -name '*.log' | xargs rm",
    ]

    def test_ordinary_work_passes(self):
        for cmd in self.PASS:
            self.assertEqual(_run(cmd).returncode, 0, f"ложный блок: {cmd}")

    def test_destructive_blocked(self):
        for cmd in self.BLOCK:
            self.assertEqual(_run(cmd).returncode, 2, f"пропущен деструктив: {cmd}")


if __name__ == "__main__":
    unittest.main()
