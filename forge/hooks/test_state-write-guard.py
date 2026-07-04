#!/usr/bin/env python3
"""Тесты hooks/state-write-guard.py (BLOCKER-1): запрет прямой записи в control-plane state."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "state-write-guard.py"


def _run(tool_name: str, tool_input: dict):
    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": ".",
                          "tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


def _write(path: str):
    return _run("write_file", {"file_path": path})


def _bash(cmd: str):
    return _run("run_shell_command", {"command": cmd})


class TWriteVector(unittest.TestCase):
    def test_block_manifest(self):
        r = _write("ground/statements/feature-pipeline/f1/manifest.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_approvals(self):
        r = _write("ground/approvals/human-approval.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_pipeline_json(self):
        r = _write("ground/pipeline.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_overrides(self):
        r = _write("ground/statements/forgelite/f1/overrides/subagent-origin.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_gates(self):
        r = _write("ground/statements/feature-pipeline/f1/gates/04-build-T1.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_origins(self):
        r = _write("ground/statements/feature-pipeline/f1/_origins/04-build-T1.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_edit_tool_too(self):
        r = _run("edit", {"file_path": "ground/statements/feature-pipeline/f1/manifest.json"})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_pass_normal_src(self):
        r = _write("src/main/java/com/x/Foo.java")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pass_regular_ground_doc(self):
        # прочие файлы в ground/ (не control-plane) — не наша забота
        r = _write("ground/brd-grounding/notes.md")
        self.assertEqual(r.returncode, 0, r.stderr)


class TBashVector(unittest.TestCase):
    def test_block_redirect_into_manifest(self):
        r = _bash("echo '{}' > ground/statements/feature-pipeline/f1/manifest.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_tee_into_approvals(self):
        r = _bash("echo x | tee ground/approvals/human-approval.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_python_c_open_write(self):
        r = _bash("python3 -c \"open('ground/statements/feature-pipeline/f1/manifest.json','w').write('{}')\"")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_pass_sanctioned_update_script(self):
        # легальный путь: update.py пишет manifest через open() ВНУТРИ python — в тексте команды
        # нет ни редиректа, ни литерала manifest.json → не блокируется
        r = _bash("python3 .gigacode/skills/pipeline-state/scripts/update.py "
                  "--skill feature-pipeline --feature f1 --step-id 04-build-T1 --status completed")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pass_read_of_manifest(self):
        # чтение control-plane файла — можно (нет токена записи)
        r = _bash("cat ground/statements/feature-pipeline/f1/manifest.json")
        self.assertEqual(r.returncode, 0, r.stderr)


class TContract(unittest.TestCase):
    def test_failopen_empty_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
