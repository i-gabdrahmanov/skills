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

    def test_design_role_blocks_build_cmd(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew build"}}), 2)

    def test_git_commit_push_not_gated(self):
        # Доставка — на пользователе: git commit/push роли не гейтят ни в одной фазе.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}}), 0)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git push origin main"}}), 0)

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

    def test_spec_role_blocks_build_but_not_git(self):
        # spec-фаза не билдит, но git-команды свободны (доставка — на пользователе).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew build"}}), 2)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}}), 0)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git push origin main"}}), 0)


def _make_real(tmp: Path, order: list[str], done_upto: str, slug: str = "feat") -> None:
    """Манифест КАК НА ЖИВОМ ПРОГОНЕ: закрытые шаги + pending-хвост, БЕЗ in_progress."""
    d = tmp / "ground" / "statements" / "forgefix" / slug
    d.mkdir(parents=True, exist_ok=True)
    idx = order.index(done_upto)
    steps = [{"id": s, "status": "completed" if i <= idx else "pending",
              "depends_on": [order[i - 1]] if i else []} for i, s in enumerate(order)]
    (d / "manifest.json").write_text(json.dumps(
        {"context": {"feature": slug}, "steps": steps}), encoding="utf-8")


class RealManifestNoInProgress(unittest.TestCase):
    """Регресс: роль резолвилась только по `in_progress`, которого на живых прогонах нет →
    SoD молчал всегда, хотя числился активным enforcement'ом."""

    FIX = ["fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"]

    def test_red_phase_blocks_src_main_without_in_progress(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-diag", slug="BUG-1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file", "tool_input": {
                "file_path": str(tmp / "src/main/java/A.java")}}), 2)

    def test_green_phase_allows_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-red", slug="BUG-1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file", "tool_input": {
                "file_path": str(tmp / "src/main/java/A.java")}}), 0)

    def test_verify_phase_hint_points_to_reopening_impl_step(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-green", slug="BUG-1")
            r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(
                {"cwd": str(tmp), "tool_name": "write_file",
                 "tool_input": {"file_path": str(tmp / "src/main/java/A.java")}}),
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("fix-green", r.stderr)      # куда вернуться легально
            self.assertIn("max_step_reopens", r.stderr)


if __name__ == "__main__":
    unittest.main()
