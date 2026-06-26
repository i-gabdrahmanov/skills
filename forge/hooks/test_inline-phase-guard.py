#!/usr/bin/env python3
"""Tests for hooks/inline-phase-guard.py — actor-aware блок inline-работы subagent-фаз.

Главный агент (agent_type пуст) не может производить артефакты/код subagent-only фазы;
субагент (agent_type задан) — может; control-plane и не-subagent-фазы — fail-open;
override снимает блок.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "inline-phase-guard.py"


def _make(tmp: Path, active_step: str | None, slug: str = "feat", override_step=...) -> None:
    d = tmp / "ground" / "statements" / "feature-pipeline" / slug
    d.mkdir(parents=True, exist_ok=True)
    steps = [{"id": active_step, "status": "in_progress"}] if active_step else []
    (d / "manifest.json").write_text(json.dumps({
        "context": {"feature": slug}, "steps": steps,
    }), encoding="utf-8")
    if override_step is not ...:
        ov = d / "overrides"
        ov.mkdir(parents=True, exist_ok=True)
        payload = {"reason": "agent unavailable"}
        if override_step is not None:
            payload["step_id"] = override_step
        (ov / "subagent-origin.json").write_text(json.dumps(payload), encoding="utf-8")


def _run(tmp: Path, payload: dict) -> int:
    payload = {**payload, "cwd": str(tmp)}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True).returncode


class TestInlinePhaseGuard(unittest.TestCase):
    # ── главный агент блокируется на productive-работе фазы ──
    def test_main_agent_blocked_writing_tech_design(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 2)

    def test_main_agent_blocked_writing_task_plan(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/task-plan.json")}}), 2)

    def test_main_agent_blocked_writing_sdd(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/sdd.md")}}), 2)

    def test_main_agent_blocked_writing_src_main_in_build(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)

    def test_main_agent_blocked_writing_src_test_in_red(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/test/java/XTest.java")}}), 2)

    def test_main_agent_blocked_gradle_in_tests_phase(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "05-tests")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew test"}}), 2)

    # ── субагент НЕ блокируется ──
    def test_subagent_allowed_writing_tech_design(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "agent_type": "general-purpose",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)

    def test_subagent_allowed_writing_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "agent_type": "general-purpose",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    # ── control-plane и нерелевантные действия — fail-open ──
    def test_control_plane_update_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "python3 .gigacode/skills/pipeline-state/scripts/update.py --step-id 02-design"}}), 0)

    def test_read_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Read",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)

    def test_unrelated_write_in_design_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "README.md")}}), 0)

    def test_no_active_step_failopen(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, None)
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    def test_non_subagent_phase_failopen(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "03-jira")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    # ── override снимает блок ──
    def test_override_for_step_allows(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design", override_step="02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)

    def test_general_override_allows(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design", override_step=None)
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)


if __name__ == "__main__":
    unittest.main()
