#!/usr/bin/env python3
"""Tests for checkpoint.py — git-чекпойнты worktree на границах шагов.

Инварианты: снапшот захватывает untracked, уважает .gitignore, не трогает
пользовательский индекс/ветки; повторный чекпойнт перезаписывает ref; вне
git-репо — None без исключений (fail-soft, update.py не падает).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import checkpoint  # noqa: E402

UPDATE = HERE / "update.py"
INIT = HERE / "init.py"

SKILL = "feature-pipeline"
FEATURE = "feat"
STEP = "01-grounding"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def _make_repo(tmp: Path) -> None:
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "tracked.txt").write_text("v1", encoding="utf-8")
    _git(tmp, "add", "tracked.txt")
    _git(tmp, "commit", "-q", "-m", "init")


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


def _run_update(tmp: Path, status: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(UPDATE), "--project", str(tmp), "--skill", SKILL,
         "--feature", FEATURE, "--step-id", STEP, "--status", status],
        capture_output=True, text=True,
    )


class CreateCheckpoint(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        _make_repo(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def test_creates_ref_and_returns_sha(self):
        sha = checkpoint.create_checkpoint(self.tmp, FEATURE, STEP)
        self.assertIsNotNone(sha)
        self.assertEqual(sha, checkpoint.checkpoint_for(self.tmp, FEATURE, STEP))

    def test_untracked_in_tree_gitignored_not(self):
        (self.tmp / ".gitignore").write_text("build/\n", encoding="utf-8")
        (self.tmp / "new-untracked.txt").write_text("x", encoding="utf-8")
        (self.tmp / "build").mkdir()
        (self.tmp / "build" / "artifact.jar").write_text("bin", encoding="utf-8")
        checkpoint.create_checkpoint(self.tmp, FEATURE, STEP)
        paths = checkpoint.read_tree_paths(self.tmp, checkpoint.checkpoint_ref(FEATURE, STEP))
        self.assertIn("new-untracked.txt", paths)
        self.assertIn("tracked.txt", paths)
        self.assertNotIn("build/artifact.jar", paths)

    def test_user_index_untouched(self):
        (self.tmp / "new-untracked.txt").write_text("x", encoding="utf-8")
        checkpoint.create_checkpoint(self.tmp, FEATURE, STEP)
        status = _git(self.tmp, "status", "--porcelain").stdout
        # файл остался untracked (??), а не staged — рабочий индекс не тронут
        self.assertIn("?? new-untracked.txt", status)

    def test_repeat_overwrites_ref(self):
        sha1 = checkpoint.create_checkpoint(self.tmp, FEATURE, STEP)
        (self.tmp / "tracked.txt").write_text("v2", encoding="utf-8")
        sha2 = checkpoint.create_checkpoint(self.tmp, FEATURE, STEP)
        self.assertNotEqual(sha1, sha2)
        self.assertEqual(sha2, checkpoint.checkpoint_for(self.tmp, FEATURE, STEP))

    def test_path_in_ref_and_meta(self):
        checkpoint.create_checkpoint(self.tmp, FEATURE, STEP)
        ref = checkpoint.checkpoint_ref(FEATURE, STEP)
        self.assertTrue(checkpoint.path_in_ref(self.tmp, ref, "tracked.txt"))
        self.assertFalse(checkpoint.path_in_ref(self.tmp, ref, "nope.txt"))
        meta = checkpoint.checkpoint_meta(self.tmp, FEATURE, STEP)
        self.assertEqual(meta["ref"], ref)
        self.assertTrue(meta["ts"])

    def test_list_and_delete(self):
        checkpoint.create_checkpoint(self.tmp, FEATURE, "00-brd")
        checkpoint.create_checkpoint(self.tmp, FEATURE, "02-sdd")
        lst = checkpoint.list_checkpoints(self.tmp, FEATURE)
        self.assertEqual({c["step_id"] for c in lst}, {"00-brd", "02-sdd"})
        n = checkpoint.delete_checkpoints(self.tmp, FEATURE)
        self.assertEqual(n, 2)
        self.assertEqual(checkpoint.list_checkpoints(self.tmp, FEATURE), [])

    def test_safe_ref_part_sanitizes(self):
        self.assertEqual(checkpoint.safe_ref_part("04-build-T1"), "04-build-T1")
        self.assertEqual(checkpoint.safe_ref_part("a b/c"), "a-b-c")
        self.assertEqual(checkpoint.safe_ref_part("///"), "x")


class NonGitFailSoft(unittest.TestCase):
    def test_non_git_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(checkpoint.create_checkpoint(Path(td), FEATURE, STEP))
            self.assertIsNone(checkpoint.checkpoint_for(Path(td), FEATURE, STEP))
            self.assertEqual(checkpoint.list_checkpoints(Path(td), FEATURE), [])

    def test_update_py_survives_without_git(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            _make_manifest(tmp, status="in_progress")
            r = _run_update(tmp, "completed")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_manifest_step(tmp)["status"], "completed")
            self.assertNotIn("checkpoint", _manifest_step(tmp))


class UpdateIntegration(unittest.TestCase):
    def test_update_completed_writes_checkpoint_field(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            _make_repo(tmp)
            _make_manifest(tmp, status="in_progress")
            r = _run_update(tmp, "completed")
            self.assertEqual(r.returncode, 0, r.stderr)
            step = _manifest_step(tmp)
            self.assertEqual(step["status"], "completed")
            sha = checkpoint.checkpoint_for(tmp, FEATURE, STEP)
            self.assertIsNotNone(sha)
            self.assertEqual(step.get("checkpoint"), sha[:12])


class InitIntegration(unittest.TestCase):
    def _run_init(self, tmp: Path, *extra: str) -> subprocess.CompletedProcess:
        steps = json.dumps([{"id": STEP, "title": "g"}])
        return subprocess.run(
            [sys.executable, str(INIT), "--project", str(tmp), "--skill", SKILL,
             "--feature", FEATURE, "--steps", steps, *extra],
            capture_output=True, text=True,
        )

    def test_init_writes_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            _make_repo(tmp)
            r = self._run_init(tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNotNone(checkpoint.checkpoint_for(tmp, FEATURE, "00-baseline"))

    def test_init_force_deletes_stale_refs(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            _make_repo(tmp)
            self.assertEqual(self._run_init(tmp).returncode, 0)
            checkpoint.create_checkpoint(tmp, FEATURE, "02-sdd")
            r = self._run_init(tmp, "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            # старые refs снесены, свежий baseline создан заново
            steps = {c["step_id"] for c in checkpoint.list_checkpoints(tmp, FEATURE)}
            self.assertEqual(steps, {"00-baseline"})


if __name__ == "__main__":
    unittest.main()
