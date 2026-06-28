#!/usr/bin/env python3
"""Tests for update.py _check_subagent_origin (evidence-based, не доверяет --closed-by).

Фазы из SUBAGENT_PHASE_PREFIXES (02-sdd/02-design/04-test/04-build/05-tests/06-spec) можно закрыть
completed только при НАЛИЧИИ evidence-маркера _origins/<step_id>.json (его пишет state-recorder на
реальном SubagentStop). Флаг --closed-by subagent сам по себе больше НЕ доказывает происхождение.
Inline-закрытие без маркера блокируется; не-субагентные фазы закрываются inline свободно.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATE = HERE / "update.py"


def _make_manifest(tmp: Path, feature: str = "feat") -> None:
    d = tmp / "ground" / "statements" / "feature-pipeline" / feature
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "feature": feature,
        "steps": [
            {"id": "04-build-T1", "status": "in_progress", "required_judges": []},
            {"id": "01-grounding", "status": "in_progress", "required_judges": []},
        ],
    }), encoding="utf-8")


def _write_marker(tmp: Path, step_id: str, feature: str = "feat") -> None:
    """Симулирует evidence-маркер, который пишет state-recorder на реальном SubagentStop."""
    d = tmp / "ground" / "statements" / "feature-pipeline" / feature / "_origins"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{step_id}.json").write_text(
        json.dumps({"step_id": step_id, "agent_type": "general-purpose"}), encoding="utf-8")


def _run(tmp: Path, step_id: str, closed_by: str, feature: str = "feat") -> int:
    return subprocess.run(
        [sys.executable, str(UPDATE), "--project", str(tmp), "--skill", "feature-pipeline",
         "--feature", feature, "--step-id", step_id, "--status", "completed",
         "--closed-by", closed_by],
        capture_output=True, text=True,
    ).returncode


class TestSubagentOrigin(unittest.TestCase):
    def test_inline_close_of_build_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            self.assertNotEqual(_run(tmp, "04-build-T1", "inline"), 0)

    def test_closed_by_subagent_without_marker_blocked(self):
        # Дыра, которую закрываем: флаг --closed-by subagent БЕЗ реального evidence — блок.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            self.assertNotEqual(_run(tmp, "04-build-T1", "subagent"), 0)

    def test_subagent_close_with_marker_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _write_marker(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, "04-build-T1", "subagent"), 0)

    def test_inline_close_of_non_subagent_phase_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            self.assertEqual(_run(tmp, "01-grounding", "inline"), 0)

    def test_override_allows_inline(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            ov = tmp / "ground" / "statements" / "feature-pipeline" / "feat" / "overrides"
            ov.mkdir(parents=True, exist_ok=True)
            (ov / "subagent-origin.json").write_text(
                json.dumps({"reason": "тест: ручное закрытие"}), encoding="utf-8")
            self.assertEqual(_run(tmp, "04-build-T1", "inline"), 0)


if __name__ == "__main__":
    unittest.main()
