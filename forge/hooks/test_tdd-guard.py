#!/usr/bin/env python3
"""Smoke test for hooks/tdd-guard.py.

Раньше здесь был авто-стаб с `import tdd-guard as mod` — это SyntaxError (дефис в имени), поэтому
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

HOOK = Path(__file__).resolve().parent / "tdd-guard.py"


def _seed_red_pending(project: Path) -> None:
    """Мини-пайплайн: quality.tdd on + незакрытый RED-шаг lite-red → запись src/main блокируется."""
    (project / "ground" / "statements" / "forgelite" / "f1").mkdir(parents=True)
    (project / "ground" / "pipeline.json").write_text(
        json.dumps({"quality": {"tdd": True, "tdd_integration_skip": False}}), encoding="utf-8")
    (project / "ground" / "statements" / "forgelite" / "f1" / "manifest.json").write_text(
        json.dumps({"steps": [{"id": "lite-red", "status": "pending"}]}), encoding="utf-8")


def _payload(project: Path, cwd: Path) -> str:
    return json.dumps({
        "hook_event_name": "PreToolUse", "cwd": str(cwd), "tool_name": "Write",
        "tool_input": {"file_path": str(project / "service" / "src" / "main" / "java" / "A.java"),
                       "content": "class A {}"},
    })


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

    def test_blocks_src_main_before_red(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_red_pending(project)
            r = subprocess.run([sys.executable, str(HOOK)], input=_payload(project, project),
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("lite-red", r.stderr)

    def test_blocks_when_cwd_is_subdir(self):
        """Пин m1: root = git-toplevel(cwd), а не сырой cwd. Раньше при cwd=подкаталог
        репозитория хук не находил ground/ и молча fail-open'ил (соседи по цепочке
        gate/sod/inline работали от toplevel — enforcement 'раздваивался')."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_red_pending(project)
            subprocess.run(["git", "init", "-q", str(project)], capture_output=True, timeout=30)
            subdir = project / "service" / "sub"
            subdir.mkdir(parents=True)
            r = subprocess.run([sys.executable, str(HOOK)], input=_payload(project, subdir),
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2,
                             f"cwd=подкаталог должен резолвиться в toplevel; stderr: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
