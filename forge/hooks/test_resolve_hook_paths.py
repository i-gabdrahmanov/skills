#!/usr/bin/env python3
"""test_resolve_hook_paths.py — регрессия Windows-путей + -X utf8 в резолвере хуков.

До этого файла у resolve_hook_paths.py не было ни одного теста — именно поэтому
регресс (свои же хуки на Windows считались "путями вне проекта") прошёл незамеченным
и вылез только на живой Windows-машине пользователя. Причина: project_root на Windows
из Path(...).resolve() — обратные слэши ("C:\\Work\\..."), а хвост из шаблона
settings.hooks.json — прямые ("/.gigacode/hooks/x.py"); итоговый путь в command —
смешанный. Тесты ниже фиксируют оба формата (POSIX и Windows-mixed) как контракт.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("resolve_hook_paths", HOOKS / "resolve_hook_paths.py")
rhp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rhp)


def _settings_with_command(cmd: str) -> dict:
    return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd, "name": "x"}]}]}}


def _forge_cmd(root: str, script: str) -> str:
    return f"/usr/bin/python3 -X utf8 {root}/.gigacode/hooks/{script}"


class TestFindPythonCmd(unittest.TestCase):
    def test_includes_utf8_flag(self):
        # -X utf8 обязателен: без него хуки падают на кириллице/иконках в stdin/stdout
        # на не-английской Windows-локали (cp1251) — см. commit-историю.
        self.assertIn("-X utf8", rhp.find_python_cmd())

    def test_quotes_path_with_spaces_forward_slashes(self):
        # Путь с пробелом квотится, слэши — прямые (backslash рантайм режет по POSIX-разбору).
        import sys
        orig = sys.executable
        try:
            sys.executable = r"C:\Program Files\Python313\python.exe"
            cmd = rhp.find_python_cmd()
            self.assertIn('"C:/Program Files/Python313/python.exe"', cmd)
            self.assertNotIn("\\", cmd)
            self.assertIn("-X utf8", cmd)
        finally:
            sys.executable = orig

    def test_no_space_windows_path_survives_posix_split(self):
        """python по пути без пробела шёл неквотированным → backslash'и съедались рантаймом
        ("C:\\Python313\\python.exe" → "C:Python313python.exe"). Прямые слэши это чинят."""
        import shlex
        import sys
        orig = sys.executable
        try:
            sys.executable = r"C:\Python313\python.exe"
            cmd = rhp.find_python_cmd()
            self.assertNotIn("\\", cmd)
            # интерпретатор — первый токен после POSIX-разбора, путь цел
            exe = shlex.split(cmd, posix=True)[0]
            self.assertEqual(exe, "C:/Python313/python.exe")
        finally:
            sys.executable = orig


class TestToCommandPath(unittest.TestCase):
    def test_windows_backslashes_to_forward(self):
        self.assertEqual(rhp.to_command_path(r"C:\Users\bandura-ev\proj"),
                         "C:/Users/bandura-ev/proj")

    def test_posix_is_noop(self):
        self.assertEqual(rhp.to_command_path("/home/user/proj"), "/home/user/proj")


class TestWindowsCommandSurvivesShlex(unittest.TestCase):
    """Ядро бага: рантайм режет command в argv по POSIX-правилам shlex ("\\" = экранирование).
    Неквотированный windows-путь "C:\\Users\\...\\pprb-kid" схлопывался в "C:Users...pprb-kid"
    (drive-relative) → хук искался относительно CWD → "can't open file ...prompt-guard.py"
    на КАЖДОМ вызове. Резолвер обязан подставлять прямые слэши, они переживают разбор."""

    def test_resolved_command_script_intact_after_posix_split(self):
        import shlex
        # main() нормализует project_root к прямым слэшам ровно через to_command_path:
        win_root = r"C:\Users\bandura-ev\IdeaProjects\kid\pprb-kid"
        root_fwd = rhp.to_command_path(win_root)
        block = {"PreToolUse": [{"hooks": [
            {"command": "${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/prompt-guard.py"}]}]}
        resolved = rhp.resolve_hooks_block(block, root_fwd, rhp.find_python_cmd())
        cmd = resolved["PreToolUse"][0]["hooks"][0]["command"]
        script = shlex.split(cmd, posix=True)[-1]
        # backslash'и не съедены: полный путь проекта на месте, не drive-relative
        self.assertEqual(script, f"{root_fwd}/.gigacode/hooks/prompt-guard.py")
        self.assertNotIn("\\", script)
        self.assertTrue(script.startswith("C:/Users/bandura-ev/"), script)


class TestHasAbsoluteHookPaths(unittest.TestCase):
    """Регрессия: свои же хуки на Windows (смешанный разделитель) НЕ должны считаться
    "путями вне проекта". Раньше regex "(/\\S+\\.py)\\b" якорился на первый "/" в
    строке и резал префикс проекта, оставляя только хвост "/.gigacode/hooks/x.py"."""

    def test_posix_own_path_extracted_whole(self):
        cmd = "/usr/bin/python3 -X utf8 /home/user/proj/.gigacode/hooks/destructive-blocker.py"
        found = rhp.has_absolute_hook_paths(_settings_with_command(cmd))
        self.assertEqual(found, ["/home/user/proj/.gigacode/hooks/destructive-blocker.py"])

    def test_windows_mixed_separator_own_path_extracted_whole(self):
        project_root = r"C:\Work\JavaProjects\pprb-kid"
        cmd = (r'"C:\Program Files\Python313\python.exe" -X utf8 '
               rf"{project_root}/.gigacode/hooks/destructive-blocker.py")
        found = rhp.has_absolute_hook_paths(_settings_with_command(cmd))
        self.assertEqual(len(found), 1)
        # Должен вернуть ПОЛНЫЙ путь, а не только хвост "/.gigacode/hooks/...".
        self.assertTrue(found[0].startswith(project_root), found[0])
        expected_prefix = f"{project_root}/.gigacode/hooks/"
        self.assertTrue(found[0].startswith(expected_prefix), found[0])

    def test_windows_foreign_path_still_flagged_as_different(self):
        project_root = r"C:\Work\JavaProjects\pprb-kid"
        cmd = (r'"C:\Program Files\Python313\python.exe" -X utf8 '
               r"C:\Other\proj/.gigacode/hooks/destructive-blocker.py")
        found = rhp.has_absolute_hook_paths(_settings_with_command(cmd))
        expected_prefix = f"{project_root}/.gigacode/hooks/"
        foreign = [p for p in found if not p.startswith(expected_prefix)]
        self.assertEqual(len(foreign), 1)


class TestResolveHooksBlock(unittest.TestCase):
    def test_substitutes_both_placeholders(self):
        block = {"PreToolUse": [{"hooks": [
            {"command": "${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/x.py"}
        ]}]}
        resolved = rhp.resolve_hooks_block(block, "/proj", "/usr/bin/python3 -X utf8")
        cmd = resolved["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, "/usr/bin/python3 -X utf8 /proj/.gigacode/hooks/x.py")
        self.assertNotIn("${PYTHON}", cmd)
        self.assertNotIn("${PROJECT_ROOT}", cmd)


class TestStripForgeHooks(unittest.TestCase):
    """--remove (деинсталляция): снимаем ТОЛЬКО forge-хуки, чужое не трогаем.

    Контракт снятия живёт здесь же, где постановка — иначе два владельца блока hooks
    разъедутся, и uninstall оставит в settings.json хуки на удалённые файлы (рантайм тогда
    падает на КАЖДОМ вызове инструмента).
    """
    ROOT = "/home/user/proj"

    def _settings(self):
        return {
            "$version": 3,
            "permissions": {"allow": ["Bash(ls:*)"]},
            "mcpServers": {"atlassian": {"command": "mcp-atlassian"}},
            "hooks": {
                "PreToolUse": [{"matcher": "^Bash$", "hooks": [
                    {"command": _forge_cmd(self.ROOT, "gate-guard.py"), "name": "gate-guard"},
                    {"command": "python3 /opt/corp/audit.py", "name": "corp-audit"},
                ]}],
                "Stop": [{"hooks": [
                    {"command": _forge_cmd(self.ROOT, "phase-gate.py"), "name": "phase-gate"},
                ]}],
            },
        }

    def test_removes_forge_keeps_operator_hook(self):
        out, removed, stale, kept = rhp.strip_forge_hooks(self._settings(), self.ROOT)
        self.assertEqual(sorted(removed), ["gate-guard", "phase-gate"])
        self.assertEqual(stale, [])
        self.assertEqual(kept, ["corp-audit"])
        # хук оператора остался, forge-хуки ушли
        entries = out["hooks"]["PreToolUse"][0]["hooks"]
        self.assertEqual([e["name"] for e in entries], ["corp-audit"])

    def test_empty_groups_and_events_collapse(self):
        # Stop содержал только forge-хук → событие исчезает целиком, а не остаётся пустым
        out, _, _, _ = rhp.strip_forge_hooks(self._settings(), self.ROOT)
        self.assertNotIn("Stop", out["hooks"])

    def test_other_sections_preserved(self):
        out, _, _, _ = rhp.strip_forge_hooks(self._settings(), self.ROOT)
        self.assertEqual(out["permissions"], {"allow": ["Bash(ls:*)"]})
        self.assertIn("mcpServers", out)
        self.assertEqual(out["$version"], 3)

    def test_hooks_key_dropped_when_only_forge(self):
        s = {"$version": 3, "hooks": {"Stop": [{"hooks": [
            {"command": _forge_cmd(self.ROOT, "phase-gate.py"), "name": "phase-gate"}]}]}}
        out, removed, _, _ = rhp.strip_forge_hooks(s, self.ROOT)
        self.assertNotIn("hooks", out)       # пустой блок — мусор, ключа быть не должно
        self.assertEqual(removed, ["phase-gate"])
        self.assertEqual(out["$version"], 3)

    def test_stale_path_after_project_move_still_removed(self):
        """Проект переехал → в settings.json путь старого места. Такие хуки тоже снимаем:
        иначе останутся записи на несуществующие файлы (регрессия «0 hook entries» наоборот)."""
        s = _settings_with_command(_forge_cmd("/old/location", "gate-guard.py"))
        out, removed, stale, kept = rhp.strip_forge_hooks(s, self.ROOT)
        self.assertEqual(removed, [])
        self.assertEqual(stale, ["x"])
        self.assertEqual(kept, [])
        self.assertNotIn("hooks", out)

    def test_windows_mixed_separator_removed(self):
        root = r"C:\Work\proj"
        cmd = rf'"C:\Program Files\Python313\python.exe" -X utf8 {root}/.gigacode/hooks/gate-guard.py'
        out, removed, stale, _ = rhp.strip_forge_hooks(_settings_with_command(cmd), root)
        self.assertEqual(removed, ["x"], f"stale={stale}")
        self.assertNotIn("hooks", out)

    def test_no_hooks_block_is_noop(self):
        s = {"$version": 3, "permissions": {}}
        out, removed, stale, kept = rhp.strip_forge_hooks(s, self.ROOT)
        self.assertEqual((removed, stale, kept), ([], [], []))
        self.assertEqual(out, s)

    def test_idempotent(self):
        once, _, _, _ = rhp.strip_forge_hooks(self._settings(), self.ROOT)
        twice, removed, stale, _ = rhp.strip_forge_hooks(once, self.ROOT)
        self.assertEqual(once, twice)
        self.assertEqual((removed, stale), ([], []))


class TestRemoveCli(unittest.TestCase):
    """--remove через CLI: пишет файл, --dry-run не пишет."""

    def _run(self, project: Path, *extra):
        return subprocess.run(
            [sys.executable, str(HOOKS / "resolve_hook_paths.py"),
             "--project", str(project), "--remove", *extra],
            capture_output=True, text=True, timeout=30)

    def test_writes_and_dry_run_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            gig = proj / ".gigacode"
            gig.mkdir()
            settings = gig / "settings.json"
            # project_root резолвится (.resolve()) — на macOS /var → /private/var,
            # поэтому путь в команде строим из того же резолва, что и сам скрипт.
            root = str(proj.resolve())
            settings.write_text(json.dumps({
                "$version": 3,
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {"Stop": [{"hooks": [
                    {"command": _forge_cmd(root, "phase-gate.py"), "name": "phase-gate"}]}]},
            }), encoding="utf-8")
            before = settings.read_text(encoding="utf-8")

            r = self._run(proj, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(settings.read_text(encoding="utf-8"), before, "--dry-run не должен писать")
            self.assertTrue(json.loads(r.stdout)["dry_run"])

            r = self._run(proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["removed_entries"], 1)
            self.assertTrue(out["hooks_key_removed"])
            written = json.loads(settings.read_text(encoding="utf-8"))
            self.assertNotIn("hooks", written)
            self.assertIn("permissions", written, "чужие секции обязаны пережить снятие")

    def test_missing_settings_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / ".gigacode").mkdir()
            r = self._run(proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["removed_entries"], 0)

    def test_broken_settings_fails_closed(self):
        """Нечитаемый settings.json не переписываем — иначе снесём конфиг оператора."""
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            gig = proj / ".gigacode"
            gig.mkdir()
            (gig / "settings.json").write_text("{ broken json", encoding="utf-8")
            r = self._run(proj)
            self.assertEqual(r.returncode, 1)
            self.assertEqual((gig / "settings.json").read_text(encoding="utf-8"), "{ broken json")


class TestResolveCli(unittest.TestCase):
    """resolve-режим через реальный main(): эмитит settings.json, команды выживают POSIX-разбор."""

    def test_emitted_commands_are_posix_safe(self):
        import shlex
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            hooks = proj / ".gigacode" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "settings.hooks.json").write_text(json.dumps({"hooks": {"Stop": [
                {"hooks": [{"command": "${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/phase-gate.py",
                            "name": "phase-gate"}]}]}}), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(HOOKS / "resolve_hook_paths.py"), "--project", str(proj)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            written = json.loads((proj / ".gigacode" / "settings.json").read_text(encoding="utf-8"))
            cmd = written["hooks"]["Stop"][0]["hooks"][0]["command"]
            self.assertNotIn("${PROJECT_ROOT}", cmd)
            self.assertNotIn("${PYTHON}", cmd)
            # хук-скрипт после POSIX-разбора цел и указывает в .gigacode/hooks/ этого проекта
            script = shlex.split(cmd, posix=True)[-1]
            self.assertTrue(script.endswith("/.gigacode/hooks/phase-gate.py"), script)
            root_fwd = str(proj.resolve()).replace("\\", "/")
            self.assertEqual(script, f"{root_fwd}/.gigacode/hooks/phase-gate.py")


if __name__ == "__main__":
    unittest.main()
