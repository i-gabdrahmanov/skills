#!/usr/bin/env python3
"""Тест резолвинга активной фичи в state-recorder (регрессия P0-3).

Раньше state-recorder писал в namespace 'pipeline' / '' и не находил manifest
feature-pipeline (namespace по slug) — авто-запись состояния молча не работала.
"""
from __future__ import annotations

import importlib.util
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
        mp.write_text("{}")
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
        (d / "manifest.json").write_text("{}")
        os.utime(d / "manifest.json", (now, now))
        self.assertEqual(SR._resolve_active_feature(self.root), "real")


if __name__ == "__main__":
    unittest.main(verbosity=2)
