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
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("resolve_hook_paths", HOOKS / "resolve_hook_paths.py")
rhp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rhp)


def _settings_with_command(cmd: str) -> dict:
    return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd, "name": "x"}]}]}}


class TestFindPythonCmd(unittest.TestCase):
    def test_includes_utf8_flag(self):
        # -X utf8 обязателен: без него хуки падают на кириллице/иконках в stdin/stdout
        # на не-английской Windows-локали (cp1251) — см. commit-историю.
        self.assertIn("-X utf8", rhp.find_python_cmd())

    def test_quotes_path_with_spaces(self):
        import sys
        orig = sys.executable
        try:
            sys.executable = r"C:\Program Files\Python313\python.exe"
            cmd = rhp.find_python_cmd()
            self.assertIn('"C:\\Program Files\\Python313\\python.exe"', cmd)
            self.assertIn("-X utf8", cmd)
        finally:
            sys.executable = orig


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


if __name__ == "__main__":
    unittest.main()
