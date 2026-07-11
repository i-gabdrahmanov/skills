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

    def test_block_judges(self):
        # подделанный вердикт с produced_by:"run_judge" прошёл бы провенанс update._check_judges
        r = _write("ground/statements/feature-pipeline/f1/judges/brd-judge.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_phase_gate(self):
        # gate.json читает phase-lock gate-guard — подделка снимала бы фазовую блокировку
        r = _write("ground/phases/f1/gate.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_phase_defs_legacy_path(self):
        r = _write("ground/phases/phase-defs.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_evals_json(self):
        # evals.json — кэш EDD (eval-guard читает status:passed); прямой Write со всеми passed
        # снимал бы eval-гейт (тот же класс, что judges/gates)
        r = _write("ground/statements/feature-pipeline/f1/evals.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_double_slash_bypass(self):
        # ground//pipeline.json пишет в тот же файл, но обходил бы CP-regex без нормализации
        r = _write("ground//pipeline.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_dot_segment_bypass(self):
        r = _write("ground/./pipeline.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_dotdot_traversal_bypass(self):
        r = _write("ground/statements/feature-pipeline/f1/../f1/manifest.json")
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

    def test_block_redirect_into_judges(self):
        r = _bash("echo '{}' > ground/statements/forgelite/f1/judges/coverage-judge.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_python_write_phase_gate(self):
        r = _bash("python3 -c \"open('ground/phases/f1/gate.json','w').write('{}')\"")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_redirect_into_evals(self):
        r = _bash("echo '{}' > ground/statements/feature-pipeline/f1/evals.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_redirect_double_slash(self):
        r = _bash("echo '{}' > ground//pipeline.json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_pass_run_judge_ingest_command(self):
        # легальный путь вердикта: run_judge пишет judges/ внутри python — в тексте команды
        # нет ни редиректа, ни control-plane-пути → не блокируется
        r = _bash("python3 .gigacode/skills/feature-pipeline/scripts/run_judge.py brd feat "
                  "--from-output verdict.json --project-root .")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pass_read_phase_gate(self):
        r = _bash("cat ground/phases/f1/gate.json")
        self.assertEqual(r.returncode, 0, r.stderr)


class TContract(unittest.TestCase):
    def test_failopen_empty_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
