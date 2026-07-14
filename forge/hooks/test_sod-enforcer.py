#!/usr/bin/env python3
"""Tests for hooks/sod-enforcer.py — роль из активного шага манифеста.

test-фаза не пишет src/main; design/spec не коммитят/пушат/билдят; dev пишет src/main свободно.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "sod-enforcer.py"


def _make(tmp: Path, active_step: str | None, slug: str = "feat") -> None:
    d = tmp / "ground" / "statements" / "feature-pipeline" / slug
    d.mkdir(parents=True, exist_ok=True)
    steps = []
    if active_step:
        steps = [{"id": active_step, "status": "in_progress"}]
    (d / "manifest.json").write_text(json.dumps({
        "context": {"feature": slug}, "steps": steps,
    }), encoding="utf-8")


def _run(tmp: Path, payload: dict) -> int:
    payload = {**payload, "cwd": str(tmp)}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True).returncode


class TestSod(unittest.TestCase):
    def test_test_role_blocks_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)

    def test_test_role_allows_src_test(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/test/java/XTest.java")}}), 0)

    def test_dev_role_allows_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    def test_design_role_blocks_push(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git push origin main"}}), 2)

    def test_test_role_blocks_commit(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}}), 2)

    def test_no_active_step_failopen(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, None)
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    def test_jira_role_blocks_src_write(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "03-jira")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)

    def test_spec_role_blocks_raw_git_but_passes_sdd_review_script(self):
        # Гейт SDD-ревью: sdd_review_push.py — санкционированный канал (его гейтит
        # gate-guard approval-маркером); сырые git commit/push в spec-фазе — по-прежнему блок.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}}), 2)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git push origin sdd-review/feat"}}), 2)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "python3 .gigacode/skills/feature-pipeline/scripts/"
                                          "sdd_review_push.py --feature feat --json"}}), 0)


if __name__ == "__main__":
    unittest.main()
