#!/usr/bin/env python3
from __future__ import annotations
"""Тесты механизма ручного override гейта судьи.

Проверяет:
1. override_judge.py создаёт / удаляет файл
2. _check_judges в update.py пропускает заблокированный гейт при наличии override
3. _check_judges блокирует без override
4. Факт override фиксируется в step['override_warnings']
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ov_mod = _load("override_judge", SCRIPTS / "override_judge.py")
up_mod = _load("update", SCRIPTS / "update.py")


class TestOverrideJudge(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.skill = "feature-pipeline"
        self.feature = "test-feature"
        self.judges_dir = (
            self.project / "ground" / "statements" / self.skill / self.feature / "judges"
        )
        self.overrides_dir = (
            self.project / "ground" / "statements" / self.skill / self.feature / "overrides"
        )
        self.judges_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_verdict(self, judge: str, passed: bool, issues: list | None = None):
        verdict = {
            "$schema": "feature-pipeline/judge-verdict@1",
            "judge": judge,
            "feature_slug": self.feature,
            "passed": passed,
            "verdict": "PASS" if passed else "FAIL",
            "blocking_issues": issues or ([] if passed else ["test failed"]),
            "checks": [],
            "warnings": [],
            "summary": "ok" if passed else "fail",
            "evaluated_at": "2026-01-01T00:00:00Z",
        }
        (self.judges_dir / f"{judge}.json").write_text(
            json.dumps(verdict), encoding="utf-8"
        )

    def _write_override(self, judge: str, reason: str = "test reason"):
        self.overrides_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "$schema": "pipeline/judge-override@1",
            "judge": judge,
            "feature_slug": self.feature,
            "step_id": "04-test-T1",
            "override_at": "2026-01-01T00:00:00Z",
            "reason": reason,
            "approved_by": "user",
        }
        (self.overrides_dir / f"{judge}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )

    def _make_step(self, judges: list) -> dict:
        return {"id": "04-test-T1", "required_judges": judges}

    # ------------------------------------------------------------------
    # _check_judges поведение
    # ------------------------------------------------------------------

    def test_pass_when_all_verdicts_pass(self):
        """Все судьи PASS → _check_judges не бросает."""
        self._write_verdict("red-judge", True)
        step = self._make_step(["red-judge"])
        up_mod._check_judges(step, self.project, self.skill, self.feature)  # no exception

    def test_block_when_verdict_fail_no_override(self):
        """FAIL и нет override → RuntimeError."""
        self._write_verdict("red-judge", False, ["test failed"])
        step = self._make_step(["red-judge"])
        with self.assertRaises(RuntimeError) as ctx:
            up_mod._check_judges(step, self.project, self.skill, self.feature)
        self.assertIn("red-judge", str(ctx.exception))

    def test_block_when_verdict_missing_no_override(self):
        """Вердикт вообще не создан, нет override → RuntimeError."""
        step = self._make_step(["red-judge"])
        with self.assertRaises(RuntimeError) as ctx:
            up_mod._check_judges(step, self.project, self.skill, self.feature)
        self.assertIn("не найден", str(ctx.exception))

    def test_override_allows_failed_verdict(self):
        """FAIL + override → _check_judges не бросает."""
        self._write_verdict("red-judge", False, ["no DB available"])
        self._write_override("red-judge", "Database unavailable in CI")
        step = self._make_step(["red-judge"])
        up_mod._check_judges(step, self.project, self.skill, self.feature)  # no exception

    def test_override_allows_missing_verdict(self):
        """Нет вердикта + override → _check_judges не бросает."""
        self._write_override("coverage-judge", "Coverage tool not configured")
        step = self._make_step(["coverage-judge"])
        up_mod._check_judges(step, self.project, self.skill, self.feature)  # no exception

    def test_override_warning_recorded_in_step(self):
        """После override _check_judges записывает warning в step."""
        self._write_verdict("red-judge", False, ["test failed"])
        self._write_override("red-judge", "Manual verification done")
        step = self._make_step(["red-judge"])
        up_mod._check_judges(step, self.project, self.skill, self.feature)
        self.assertIn("override_warnings", step)
        self.assertTrue(any("red-judge" in w for w in step["override_warnings"]))

    def test_partial_override_blocks_remaining(self):
        """Один из двух судей override, второй FAIL без override → всё равно блок."""
        self._write_verdict("build-judge", True)
        self._write_verdict("reuse-judge", False, ["wheel found"])
        self._write_override("build-judge", "whatever")  # перекрывает PASS — не нужен
        step = self._make_step(["build-judge", "reuse-judge"])
        with self.assertRaises(RuntimeError) as ctx:
            up_mod._check_judges(step, self.project, self.skill, self.feature)
        self.assertIn("reuse-judge", str(ctx.exception))

    def test_all_overrides_no_block(self):
        """Оба судьи FAIL, оба overrided → не блокирует."""
        self._write_verdict("build-judge", False)
        self._write_verdict("reuse-judge", False)
        self._write_override("build-judge", "reason A")
        self._write_override("reuse-judge", "reason B")
        step = self._make_step(["build-judge", "reuse-judge"])
        up_mod._check_judges(step, self.project, self.skill, self.feature)  # no exception

    # ------------------------------------------------------------------
    # override_judge.py CLI
    # ------------------------------------------------------------------

    def test_create_override_file(self):
        """override_judge создаёт файл с нужными полями."""

        class Args:
            judge = "red-judge"
            feature = self.feature
            step_id = "04-test-T1"
            reason = "No DB in CI"
            project = str(self.project)
            skill = self.skill
            list = False
            remove = False
            json = False

        rc = ov_mod.cmd_create(Args(), self.project)
        self.assertEqual(rc, 0)
        path = ov_mod.override_path(self.project, self.skill, self.feature, "red-judge")
        self.assertTrue(path.exists())
        rec = json.loads(path.read_text())
        self.assertEqual(rec["judge"], "red-judge")
        self.assertEqual(rec["reason"], "No DB in CI")
        self.assertEqual(rec["approved_by"], "user")

    def test_create_requires_reason(self):
        """override_judge без --reason → rc=1."""

        class Args:
            judge = "red-judge"
            feature = self.feature
            step_id = None
            reason = None
            project = str(self.project)
            skill = self.skill
            list = False
            remove = False
            json = False

        rc = ov_mod.cmd_create(Args(), self.project)
        self.assertEqual(rc, 1)

    def test_remove_override(self):
        """override_judge --remove удаляет файл."""
        self._write_override("red-judge")
        path = ov_mod.override_path(self.project, self.skill, self.feature, "red-judge")
        self.assertTrue(path.exists())

        class Args:
            judge = "red-judge"
            feature = self.feature
            project = str(self.project)
            skill = self.skill
            json = False

        rc = ov_mod.cmd_remove(Args(), self.project)
        self.assertEqual(rc, 0)
        self.assertFalse(path.exists())

    def test_remove_nonexistent_returns_1(self):
        """Удаление несуществующего override → rc=1."""

        class Args:
            judge = "nonexistent-judge"
            feature = self.feature
            project = str(self.project)
            skill = self.skill
            json = False

        rc = ov_mod.cmd_remove(Args(), self.project)
        self.assertEqual(rc, 1)

    def test_error_message_contains_override_hint(self):
        """RuntimeError из _check_judges содержит команду для создания override."""
        step = self._make_step(["red-judge"])
        with self.assertRaises(RuntimeError) as ctx:
            up_mod._check_judges(step, self.project, self.skill, self.feature)
        self.assertIn("override_judge.py", str(ctx.exception))
        self.assertIn("--reason", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
