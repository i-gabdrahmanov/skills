#!/usr/bin/env python3
"""Smoke test for hooks/log-agent.py.

Раньше здесь был авто-стаб с `import log-agent as mod` — это SyntaxError (дефис в имени), поэтому
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
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "log-agent.py"


def _run_isolated(payload: str, td: str, extra_env: dict | None = None):
    """Запуск хука с ПОЛНОЙ изоляцией записи. log-agent выводит run-dir из git-toplevel
    cwd, а кросс-прогонный архив — из расположения ФАЙЛА хука; без tmp-cwd и
    GIGACODE_AILOG_ARCHIVE смоук-запуск замусоривал боевой ai-logs-archive/ all-null
    записями и создавал ground/ в чужом репозитории (git-toplevel каталога запуска тестов)."""
    env = {**os.environ, "GIGACODE_AILOG_ARCHIVE": str(Path(td) / "archive")}
    # изоляция от возможного GIGACODE_RUN_ID в окружении разработчика
    env.pop("GIGACODE_RUN_ID", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30,
                          cwd=td, env=env)


class T(unittest.TestCase):
    def test_module_loads(self):
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)          # регрессия синтаксиса/импорта
        self.assertTrue(hasattr(m, "main"))

    def test_failopen_empty_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run_isolated("", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            # запись ушла в ИЗОЛИРОВАННЫЙ архив (пин: _archive_path уважает env-override)
            archived = list((Path(td) / "archive").glob("agents-*.jsonl"))
            self.assertTrue(archived, "GIGACODE_AILOG_ARCHIVE не сработал — запись утекла")

    def test_real_payload_lands_in_isolated_paths(self):
        payload = json.dumps({
            "hook_event_name": "PreToolUse", "session_id": "testsess-1234",
            "tool_name": "Bash", "tool_input": {"command": "echo hi"},
        })
        with tempfile.TemporaryDirectory() as td:
            payload_obj = json.loads(payload)
            payload_obj["cwd"] = td
            r = _run_isolated(json.dumps(payload_obj), td)
            self.assertEqual(r.returncode, 0, r.stderr)
            run_dirs = list((Path(td) / "ground" / "ai-logs").glob("run-*"))
            self.assertTrue(run_dirs, "run-dir не создан в изолированном cwd")
            rec_lines = (run_dirs[0] / "agents.jsonl").read_text("utf-8").splitlines()
            rec = json.loads(rec_lines[-1])
            self.assertEqual(rec["event"], "PreToolUse")
            self.assertEqual(rec["tool_name"], "Bash")

    def test_run_id_env_overrides_dir(self):
        # Thrust 4: GIGACODE_RUN_ID даёт стабильный каталог прогона независимо от session_id
        with tempfile.TemporaryDirectory() as td:
            base = {"hook_event_name": "PreToolUse", "session_id": "sessAAAA",
                    "tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": td}
            r = _run_isolated(json.dumps(base), td, extra_env={"GIGACODE_RUN_ID": "myrun42"})
            self.assertEqual(r.returncode, 0, r.stderr)
            dirs = [p.name for p in (Path(td) / "ground" / "ai-logs").glob("run-*")]
            self.assertIn("run-myrun42", dirs)
            self.assertNotIn("run-sessAAAA", dirs)  # env приоритетнее session_id


if __name__ == "__main__":
    unittest.main()
