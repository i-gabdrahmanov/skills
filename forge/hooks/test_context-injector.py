#!/usr/bin/env python3
"""Smoke test for hooks/context-injector.py.

Раньше здесь был авто-стаб с `import context-injector as mod` — это SyntaxError (дефис в имени), поэтому
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

HOOK = Path(__file__).resolve().parent / "context-injector.py"


def _run_in(tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HOOK)],
                          input=json.dumps({"cwd": str(tmp)}),
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

    def test_broken_excerpt_json_not_injected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            sa = tmp / "docs" / "system-analysis"
            sa.mkdir(parents=True)
            (sa / "grounding-excerpt.json").write_text('{"modules": [broken', encoding="utf-8")
            r = _run_in(tmp)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("grounding-excerpt", r.stdout)  # битый файл не инъектится
            self.assertIn("битый JSON", r.stderr)

    def test_valid_excerpt_injected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            sa = tmp / "docs" / "system-analysis"
            sa.mkdir(parents=True)
            (sa / "grounding-excerpt.json").write_text(
                json.dumps({"modules": ["m1"], "conventions": {"build": "gradle"}}),
                encoding="utf-8")
            r = _run_in(tmp)
            self.assertEqual(r.returncode, 0)
            self.assertIn("grounding-excerpt", r.stdout)
            self.assertIn("additionalContext", r.stdout)

    def test_excerpt_missing_keys_warns_but_injects(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            sa = tmp / "docs" / "system-analysis"
            sa.mkdir(parents=True)
            (sa / "grounding-excerpt.json").write_text(json.dumps({"foo": 1}), encoding="utf-8")
            r = _run_in(tmp)
            self.assertEqual(r.returncode, 0)
            self.assertIn("grounding-excerpt", r.stdout)
            self.assertIn("WARNING", r.stderr)


if __name__ == "__main__":
    unittest.main()
