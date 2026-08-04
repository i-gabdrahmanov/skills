#!/usr/bin/env python3
"""test_grounding-evidence.py — хук пишет read_grounding ТОЛЬКО на чтении grounding-index.

Гейт 01-grounding снимается по evidence-записи read_grounding (её читает gate-guard).
Пины: (1) чтение grounding-index → одна запись read_grounding в agent-evidence.jsonl нужной
фичи; (2) чтение прочего кода → evidence не пишется (не лог чтений); (3) пустой stdin →
exit 0 без падения.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "grounding-evidence.py"


def _run(payload, cwd) -> int:
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, cwd=str(cwd))
    return p.returncode


def _mk_project(tmp: Path) -> Path:
    root = tmp / "proj"
    man = root / "ground" / "statements" / "feature-pipeline" / "pipeline"
    man.mkdir(parents=True)
    (man / "manifest.json").write_text(
        json.dumps({"skill": "feature-pipeline",
                    "steps": [{"id": "01-grounding", "status": "in_progress"}]}),
        encoding="utf-8")
    return root


class TestGroundingEvidence(unittest.TestCase):
    def test_read_grounding_index_records_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = _mk_project(Path(td))
            rc = _run({"cwd": str(root), "tool_name": "Read",
                       "tool_input": {"file_path": "docs/system-analysis/grounding-index.json"}}, root)
            ev = root / "ground" / "phases" / "pipeline" / "agent-evidence.jsonl"
            self.assertEqual(rc, 0)
            self.assertTrue(ev.exists(), "evidence-файл должен появиться")
            text = ev.read_text(encoding="utf-8")
            self.assertIn("read_grounding", text)
            self.assertIn("grounding-index", text)

    def test_non_grounding_read_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _mk_project(Path(td))
            rc = _run({"cwd": str(root), "tool_name": "Read",
                       "tool_input": {"file_path": "src/main/java/Foo.java"}}, root)
            ev = root / "ground" / "phases" / "pipeline" / "agent-evidence.jsonl"
            self.assertEqual(rc, 0)
            self.assertFalse(ev.exists(), "на не-grounding чтение evidence писаться не должен")

    def test_empty_stdin_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run([sys.executable, str(HOOK)], input="",
                               capture_output=True, text=True, cwd=td)
            self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
