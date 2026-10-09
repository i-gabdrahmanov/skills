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
import os
import subprocess
import sys
import time
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
        self.assertEqual(_run("git push --force-with-lease upstream hotfix").returncode, 0)

    def test_allow_force_with_lease_to_main(self):
        """Боевой прогон: policy-строка `push.*--force(-with-lease)?.*(main|master|origin)` резала
        единственный безопасный force-push (lease сверяет удалённую ветку перед перезаписью),
        а голый --force на те же ветки и так держит core."""
        for cmd in ("git push --force-with-lease origin main",
                    "git push --force-with-lease=main:abc123 origin main",
                    "git push origin main --force-with-lease"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 0, f"ложный блок: {cmd}")
        for cmd in ("git push --force origin main", "git push origin main -f"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 2, f"пропущен force-push: {cmd}")

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


class TNoReDoS(unittest.TestCase):
    """Время проверки линейно по длине команды.

    Паттерн `\brm\b(?:\s+(?:-\S+|--\w[\w-]*))*…` имел двусмысленную альтернативу под `*`
    (`--recursive` подходит обеим ветвям) → экспоненциальный перебор: `rm --recursive`×28
    занимал 131.8 с при таймауте хука 40 с. Рантайм читает таймаут как «возражений нет»,
    то есть одна длинная строка флагов снимала блокировщик целиком."""

    BUDGET_S = 5.0        # с запасом на холодный старт интерпретатора (сам матч — единицы мс)

    def test_long_flag_run_is_fast(self):
        for n in (28, 32, 64):
            with self.subTest(n=n):
                cmd = "rm " + " ".join(["--recursive"] * n) + " X"
                t0 = time.time()
                r = _run(cmd)
                elapsed = time.time() - t0
                self.assertLess(elapsed, self.BUDGET_S,
                                f"ReDoS: n={n} занял {elapsed:.1f}s (таймаут хука 40 s)")
                self.assertEqual(r.returncode, 0, "относительная цель — не деструктив")

    def test_long_pathological_target_is_fast(self):
        t0 = time.time()
        _run("rm " + " ".join(["-abc"] * 40) + " " + "a" * 500)
        self.assertLess(time.time() - t0, self.BUDGET_S)


class TBareDangerousTargets(unittest.TestCase):
    """Голая опасная цель ловится разбором argv (замена ReDoS-регулярки)."""

    def test_blocked(self):
        for cmd in ("rm -rf /", "rm -rf ~", "rm -rf $HOME", "rm -rf *", "rm -rf /*",
                    "rm -fr .", "rm --recursive --force /", "rm -rf '${HOME}'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 2, f"не заблокировано: {cmd}")

    def test_relative_cleanup_still_allowed(self):
        for cmd in ("rm -rf build/", "rm -rf ./target", "rm -f src/main/java/A.java",
                    "rm -rf build/tmp ./out"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 0, f"ложный блок: {cmd}")

    def test_absolute_outside_project_still_blocked(self):
        """Регресс tasks/011: `rm -rf /etc/passwd` держит _rm_recursive_dangerous_targets."""
        self.assertEqual(_run("rm -rf /etc/passwd").returncode, 2)


class TRecursiveRmByArgv(unittest.TestCase):
    """Боевой прогон (A1/A7): регулярка требовала одновременно -r и -f и видела цель только без
    кавычек — `rm -r /etc`, `rm -rf "/etc/passwd"`, `rm -rf ${HOME}/x`, перенос строки
    проходили с exit 0. Теперь rm разбирается по argv."""

    BLOCK = [
        "rm -r /etc/passwd", "rm -R /etc", "rm --recursive /etc",
        'rm -rf "/etc/passwd"', "rm -rf '/usr/local/lib'", 'rm -rf "$HOME/x"',
        "rm -rf ${HOME}/x", "rm -r ~/Documents", "sudo rm -r /var/lib/x",
        "rm -rf \\\n  /etc/passwd", "echo ok\nrm -r /etc", "cd /tmp && rm -fr -- /opt/app",
    ]
    PASS = [
        "rm /tmp/forge-probe.txt",         # не рекурсивный: свой временный файл
        "rm -f /tmp/forge-probe.txt",
        "rm -rf build/ ./target",          # относительная уборка
        "git rm --cached -r src/old",
        'echo "rm -rf /etc"',              # текст, а не вызов
    ]

    def test_blocked(self):
        for cmd in self.BLOCK:
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 2, f"пропущен деструктив: {cmd!r}")

    def test_passes(self):
        for cmd in self.PASS:
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 0, f"ложный блок: {cmd!r}")

    def test_quoted_and_home_targets_inside_project_allowed(self):
        """Исключение «уборка внутри проекта» работает и для кавычек, и для ~ / $HOME."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td).resolve()              # подменённый HOME: в настоящий не пишем
            proj = home / "proj"
            (proj / ".git").mkdir(parents=True)
            env = dict(os.environ, HOME=str(home))
            for cmd in (f'rm -rf "{proj}/build"', "rm -r ~/proj/build",
                        'rm -rf "$HOME/proj/build"', "rm -rf ${HOME}/proj/target"):
                with self.subTest(cmd=cmd):
                    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": str(proj),
                                          "tool_name": "run_shell_command",
                                          "tool_input": {"command": cmd}})
                    r = subprocess.run([sys.executable, str(HOOK)], input=payload, env=env,
                                       capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, 0, f"ложный блок: {cmd} → {r.stderr}")
            r = subprocess.run([sys.executable, str(HOOK)], env=env, input=json.dumps(
                {"hook_event_name": "PreToolUse", "cwd": str(proj),
                 "tool_name": "run_shell_command", "tool_input": {"command": "rm -rf ~/other"}}),
                capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2, "каталог дома вне проекта обязан блокироваться")



class TChmodWorldWritable(unittest.TestCase):
    """Боевой прогон (A2): policy-строка требовала дефис (`chmod\\s+-R?\\s*777`), и голый
    `chmod 777 /etc` проходил; формы 0777/7777/a+rwx не ловились вовсе, в ядре chmod не было."""

    def test_blocked(self):
        for cmd in ("chmod 777 /etc", "chmod -R 777 /etc", "chmod -Rf 777 .", "chmod 0777 x",
                    "chmod 7777 x", "chmod a+rwx x", "chmod +rwx x", "chmod --recursive 777 d"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 2, f"пропущено: {cmd}")

    def test_ordinary_modes_pass(self):
        for cmd in ("chmod +x gradlew", "chmod 755 build/run.sh", "chmod u+rwx x", "chmod 644 a"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 0, f"ложный блок: {cmd}")

class TBattle046(unittest.TestCase):
    """Боевой прогон v0.4.6: обходы блокировщика, каждый — ALLOWED всей Bash-цепочкой."""

    def _cwd(self, command: str, cwd: str):
        payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": cwd,
                              "tool_name": "run_shell_command",
                              "tool_input": {"command": command}})
        return subprocess.run([sys.executable, str(HOOK)], input=payload,
                              capture_output=True, text=True, timeout=30).returncode

    def test_root_without_project_is_not_a_project(self):
        """E-CWD-ROOT: сессия из `/` — резолвер без маркеров отдаёт cwd, и `rm -rf /usr`
        считался «уборкой внутри проекта»."""
        for cmd in ("rm -rf /usr", "rm -rf /etc/services"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._cwd(cmd, "/"), 2)
        with tempfile.TemporaryDirectory() as bare:          # каталог без маркеров проекта
            self.assertEqual(self._cwd(f"rm -rf {Path(bare).resolve()}/x", bare), 2)

    def test_shell_wrappers_are_unwrapped(self):
        """E-08: скрипт `-c` приходил одним токеном — rm внутри не видел никто."""
        for cmd in ("bash -c 'rm -rf /etc/services'", "sh -c \"rm -rf /usr/local/x\"",
                    "sudo bash -c \"rm -rf /etc/services\"", "eval \"rm -rf /etc\"",
                    "bash -c 'rm -rf /'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 2, f"пропущено: {cmd}")

    def test_find_execdir_and_ok(self):
        for cmd in ("find / -maxdepth 2 -execdir rm -rf {} \\;", "find /etc -ok rm {} \\;",
                    "find ~ -okdir rm {} \\;"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 2, f"пропущено: {cmd}")

    def test_dd_to_device_any_operand_order(self):
        for cmd in ("dd of=/dev/sda if=x.iso bs=1M", "dd of=/dev/disk2 bs=1m"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_run(cmd).returncode, 2, f"пропущено: {cmd}")
        self.assertEqual(_run("dd if=/dev/zero of=/dev/null bs=1M count=10").returncode, 0,
                         "приёмник /dev/null — не диск")

    def test_rsync_delete_outside_project(self):
        self.assertEqual(_run("rsync -a --delete /tmp/empty/ /etc/").returncode, 2)
        self.assertEqual(_run("rsync -a --delete build/ dist/").returncode, 0)


if __name__ == "__main__":
    unittest.main()
