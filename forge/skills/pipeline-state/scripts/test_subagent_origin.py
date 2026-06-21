#!/usr/bin/env python3
"""Tests for update.py _check_subagent_origin (бывший subagent-enforcer, перенесён на закрытие шага).

Фазы из SUBAGENT_PHASE_PREFIXES (02-sdd/02-design/04-test/04-build/05-tests/06-spec) можно закрыть
completed только если запись пришла от субагента (--closed-by subagent, что делает state-recorder).
Inline-закрытие блокируется; не-субагентные фазы закрываются inline свободно.
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

    def test_subagent_close_of_build_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
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
