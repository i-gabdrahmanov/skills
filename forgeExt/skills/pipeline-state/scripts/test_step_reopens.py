#!/usr/bin/env python3
"""Tests for update.py брейка ре-итераций (quality.max_step_reopens).

Два счётчика: reopens (переоткрытие completed|failed → pending|in_progress, блок ДО записи)
и failures (повторные транзишены в failed; провал фиксируется, затем exit 3). Оба лимитируются
quality.max_step_reopens (дефолт 3), эскейп — overrides/step-reopen-<step_id>.json.
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

SKILL = "feature-pipeline"
FEATURE = "feat"
STEP = "01-grounding"  # не-субагентная фаза, чтобы не мешал origin-чек


def _make_manifest(tmp: Path, status: str = "in_progress") -> None:
    d = tmp / "ground" / "statements" / SKILL / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "feature": FEATURE,
        "steps": [{"id": STEP, "status": status, "required_judges": []}],
    }), encoding="utf-8")
    # Содержательная выжимка — update._check_grounding_substance не закроет 01-grounding без неё.
    sa = tmp / "docs" / "system-analysis"
    sa.mkdir(parents=True, exist_ok=True)
    (sa / "grounding-excerpt.json").write_text(
        json.dumps({"modules": [{"name": "svc"}], "entities": []}), encoding="utf-8")


def _manifest_step(tmp: Path) -> dict:
    p = tmp / "ground" / "statements" / SKILL / FEATURE / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))["steps"][0]


def _run(tmp: Path, status: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(UPDATE), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, "--step-id", STEP, "--status", status],
        capture_output=True, text=True,
    )


def _write_override(tmp: Path) -> None:
    ov = tmp / "ground" / "statements" / SKILL / FEATURE / "overrides"
    ov.mkdir(parents=True, exist_ok=True)
    (ov / f"step-reopen-{STEP}.json").write_text(
        json.dumps({"reason": "тест: осознанная итерация"}), encoding="utf-8")


def _set_limit(tmp: Path, n: int) -> None:
    g = tmp / "ground"
    g.mkdir(parents=True, exist_ok=True)
    (g / "pipeline.json").write_text(
        json.dumps({"quality": {"max_step_reopens": n}}), encoding="utf-8")


class TestReopenLimit(unittest.TestCase):
    def test_reopen_counted_and_blocked_over_limit(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp)
            for i in range(3):  # 3 переоткрытия проходят (дефолтный лимит)
                self.assertEqual(_run(tmp, "completed").returncode, 0)
                r = _run(tmp, "in_progress")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(_manifest_step(tmp)["reopens"], i + 1)
            self.assertEqual(_run(tmp, "completed").returncode, 0)
            r = _run(tmp, "in_progress")  # 4-е — блок
            self.assertEqual(r.returncode, 3)
            self.assertIn("ESCALATE", r.stderr)
            self.assertEqual(_manifest_step(tmp)["status"], "completed")  # транзишен не записан

    def test_reopen_override_allows(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _set_limit(tmp, 1)
            self.assertEqual(_run(tmp, "completed").returncode, 0)
            self.assertEqual(_run(tmp, "in_progress").returncode, 0)
            self.assertEqual(_run(tmp, "completed").returncode, 0)
            self.assertEqual(_run(tmp, "in_progress").returncode, 3)
            _write_override(tmp)
            r = _run(tmp, "in_progress")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(_manifest_step(tmp).get("override_warnings"))

    def test_failures_recorded_then_escalate(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _set_limit(tmp, 2)
            self.assertEqual(_run(tmp, "failed").returncode, 0)
            self.assertEqual(_manifest_step(tmp)["failures"], 1)
            r = _run(tmp, "failed")  # 2-й провал — лимит: провал записан, но exit 3
            self.assertEqual(r.returncode, 3)
            self.assertIn("ESCALATE", r.stderr)
            self.assertEqual(_manifest_step(tmp)["failures"], 2)
            self.assertEqual(_manifest_step(tmp)["status"], "failed")

    def test_failures_override_suppresses_escalate(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp); _set_limit(tmp, 1)
            _write_override(tmp)
            r = _run(tmp, "failed")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_manifest_step(tmp)["failures"], 1)

    def test_legacy_manifest_without_counters_ok(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp, status="completed")
            r = _run(tmp, "in_progress")  # старый манифест без поля reopens
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_manifest_step(tmp)["reopens"], 1)

    def test_normal_flow_not_affected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_manifest(tmp, status="pending")
            self.assertEqual(_run(tmp, "in_progress").returncode, 0)
            self.assertEqual(_run(tmp, "completed").returncode, 0)
            self.assertNotIn("reopens", _manifest_step(tmp))


if __name__ == "__main__":
    unittest.main()
