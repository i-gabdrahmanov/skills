#!/usr/bin/env python3
"""test_sync_master.py — тесты синхронизации клона мастер-репо перед grounding."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "sync_master.py"


def _run(project: Path, pull: bool = True):
    cmd = [sys.executable, str(SCRIPT), "--project", str(project)]
    if pull:
        cmd.append("--pull")
    return subprocess.run(cmd, capture_output=True, text=True)


def _write_cfg(project: Path, cfg: dict):
    (project / "ground").mkdir(parents=True, exist_ok=True)
    (project / "ground" / "pipeline.json").write_text(json.dumps(cfg), encoding="utf-8")


class SyncMasterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.P = Path(self._tmp.name) / "proj"
        self.P.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_colocated_noop(self):
        _write_cfg(self.P, {"docs": {"mode": "in-repo", "docs_path": "docs"}})
        r = _run(self.P)
        self.assertEqual(r.returncode, 0)
        self.assertIn("co-located", r.stdout)

    def test_missing_clone_stops(self):
        _write_cfg(self.P, {"docs": {"master": {"mode": "separate-repo",
                                                "repo_path": str(self.P / "nope"),
                                                "repo_url": "git@x:spec.git"}}})
        r = _run(self.P)
        self.assertEqual(r.returncode, 2)
        self.assertIn("git clone", r.stderr)

    def test_clone_exists_flag_off_skips_pull(self):
        master = Path(self._tmp.name) / "master"
        master.mkdir()
        subprocess.run(["git", "init", "-q", str(master)], check=False)
        _write_cfg(self.P, {"docs": {"master": {"mode": "separate-repo", "repo_path": str(master)}},
                            "sdd": {"pull_before_grounding": False}})
        r = _run(self.P)
        self.assertEqual(r.returncode, 0)
        self.assertIn("pull пропущен", r.stdout)

    def test_clone_exists_flag_on_attempts_pull(self):
        master = Path(self._tmp.name) / "master2"
        master.mkdir()
        subprocess.run(["git", "init", "-q", str(master)], check=False)
        _write_cfg(self.P, {"docs": {"master": {"mode": "separate-repo", "repo_path": str(master)}},
                            "sdd": {"pull_before_grounding": True}})
        r = _run(self.P)
        # нет remote → pull мягко провалится, но exit всё равно 0 (не блок)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
