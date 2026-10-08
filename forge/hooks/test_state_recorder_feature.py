#!/usr/bin/env python3
"""Тест резолвинга активной фичи в state-recorder (регрессия P0-3).

Раньше state-recorder писал в namespace 'pipeline' / '' и не находил manifest
feature-pipeline (namespace по slug) — авто-запись состояния молча не работала.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS))
_spec = importlib.util.spec_from_file_location("state_recorder_mod", HOOKS / "state-recorder.py")
SR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SR)


class ResolveActiveFeature(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.base = self.root / "ground/statements/feature-pipeline"

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest(self, feature, mtime=None):
        d = self.base / feature
        d.mkdir(parents=True, exist_ok=True)
        mp = d / "manifest.json"
        mp.write_text("{}", encoding="utf-8")
        if mtime is not None:
            os.utime(mp, (mtime, mtime))
        return mp

    def test_no_state_defaults_to_pipeline(self):
        self.assertEqual(SR._resolve_active_feature(self.root), "pipeline")

    def test_picks_newest_manifest(self):
        now = time.time()
        self._manifest("old-feature", mtime=now - 1000)
        self._manifest("new-feature", mtime=now)
        self.assertEqual(SR._resolve_active_feature(self.root), "new-feature")

    def test_ignores_archived(self):
        now = time.time()
        self._manifest("real", mtime=now - 100)
        # archived должен игнорироваться, даже если новее
        d = self.base / "archived"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text("{}", encoding="utf-8")
        os.utime(d / "manifest.json", (now, now))
        self.assertEqual(SR._resolve_active_feature(self.root), "real")


class ResolveRunByStep(unittest.TestCase):
    """Origin шага пишется в прогон, которому шаг принадлежит, а не в свежайший по mtime.

    Регрессия (боевой прогон, A8): фикс внутри стори — законные два живых прогона. Пока
    манифест стори свежее, SubagentStop шага `fix-red` писал origin в журнал стори
    («step 'fix-red' not found»), и закрытие фикса вечно упиралось в origin-гейт."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, skill, feature, steps, mtime):
        d = self.root / "ground" / "statements" / skill / feature
        d.mkdir(parents=True, exist_ok=True)
        mp = d / "manifest.json"
        mp.write_text(json.dumps({"skill": skill, "feature": feature,
                                  "steps": [{"id": s, "status": st} for s, st in steps]}),
                      encoding="utf-8")
        os.utime(mp, (mtime, mtime))

    def test_fix_step_goes_to_fix_run_even_if_story_is_fresher(self):
        now = time.time()
        self._run("forgefix", "fix-order-id", [("fix-diag", "completed"),
                                               ("fix-red", "pending")], now - 100)
        self._run("feature-pipeline", "FORGE-2", [("02-sdd", "pending")], now)
        self.assertEqual(SR._resolve_run(self.root, "fix-red"), ("forgefix", "fix-order-id"))
        self.assertEqual(SR._resolve_run(self.root, "02-sdd"), ("feature-pipeline", "FORGE-2"))

    def test_live_owner_wins_over_completed_one(self):
        """Шаг с тем же id есть у вчерашней (завершённой) фичи и у идущей — пишем в идущую."""
        now = time.time()
        self._run("feature-pipeline", "OLD", [("02-sdd", "completed")], now)
        self._run("feature-pipeline", "NEW", [("02-sdd", "pending")], now - 100)
        self.assertEqual(SR._resolve_run(self.root, "02-sdd"), ("feature-pipeline", "NEW"))

    def test_unknown_step_falls_back_to_active_run(self):
        now = time.time()
        self._run("feature-pipeline", "ONLY", [("01-grounding", "pending")], now)
        self.assertEqual(SR._resolve_run(self.root, "99-nope"), ("feature-pipeline", "ONLY"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
