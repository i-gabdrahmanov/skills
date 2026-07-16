#!/usr/bin/env python3
"""Тесты story_branch_push.py — создание интеграционной ветки фичи feature/<slug>.

Пины: ветка создаётся ТОЛЬКО от default-tip origin (никаких локальных коммитов на ней);
существующая ветка никогда не двигается (идемпотентность); локальные ref/worktree не
трогаются; скоуп — только фичи feature-pipeline; аргументов для абьюза нет по построению.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "story_branch_push.py"
SLUG = "STOR-100"
BRANCH = f"feature/{SLUG}"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=30)


def _mk_project(td: str, slug: str = SLUG, remote: bool = True,
                manifest: bool = True) -> Path:
    project = Path(td) / "proj"
    project.mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    _git(project, "config", "user.email", "t@t")
    _git(project, "config", "user.name", "t")
    (project / "README.md").write_text("seed\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-q", "-m", "seed")
    if remote:
        bare = Path(td) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], capture_output=True,
                       text=True, timeout=30)
        _git(project, "remote", "add", "origin", str(bare))
        _git(project, "push", "-q", "-u", "origin", "main")
    if manifest:
        feat = project / "ground" / "statements" / "feature-pipeline" / slug
        feat.mkdir(parents=True)
        (feat / "manifest.json").write_text(
            json.dumps({"steps": [{"id": "07-deliver-T1", "status": "in_progress"}]}),
            encoding="utf-8")
    return project


def _run(project: Path, *extra: str, slug: str = SLUG) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT),
                           "--feature", slug, "--json", *extra],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, cwd=str(project))


class TStoryBranchPush(unittest.TestCase):
    def test_created_from_default_tip_no_local_refs(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            main_tip = _git(project, "rev-parse", "refs/heads/main").stdout.strip()
            (project / "dirty.txt").write_text("не трогать", encoding="utf-8")

            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "created")
            self.assertEqual(out["branch"], BRANCH)
            self.assertEqual(out["commit"], main_tip, "ветка — ровно default-tip, без коммитов")

            bare = Path(td) / "remote.git"
            self.assertEqual(_git(bare, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip(),
                             main_tip)
            # локальных следов нет
            r2 = _git(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}")
            self.assertNotEqual(r2.returncode, 0, "локальная ветка не создаётся")
            self.assertIn("dirty.txt", _git(project, "status", "--porcelain").stdout)

    def test_existing_branch_never_moved(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)

            # в ветку смерджили PR сабветки (эмуляция: коммит через клон)
            clone = Path(td) / "dev"
            subprocess.run(["git", "clone", "-q", "-b", BRANCH,
                            str(Path(td) / "remote.git"), str(clone)],
                           capture_output=True, text=True, timeout=30)
            _git(clone, "config", "user.email", "d@d")
            _git(clone, "config", "user.name", "dev")
            (clone / "x.txt").write_text("merged PR", encoding="utf-8")
            _git(clone, "add", "x.txt")
            _git(clone, "commit", "-qm", "STOR-101 merged")
            _git(clone, "push", "-q", "origin", BRANCH)
            merged_tip = _git(clone, "rev-parse", "HEAD").stdout.strip()

            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "exists")
            self.assertEqual(out["commit"], merged_tip)
            bare = Path(td) / "remote.git"
            self.assertEqual(_git(bare, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip(),
                             merged_tip, "существующая ветка не двигается")

    def test_foreign_namespace_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, manifest=False)
            lite = project / "ground" / "statements" / "forgelite" / SLUG
            lite.mkdir(parents=True)
            (lite / "manifest.json").write_text("{}", encoding="utf-8")
            r = _run(project)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("feature-pipeline", r.stderr)

    def test_traversal_slug_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            r = _run(project, slug="../evil")
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_project_mismatch_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            other = Path(td) / "other"
            other.mkdir()
            _git(other, "init", "-q", "-b", "main")
            r = _run(project, "--project", str(other))
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_no_remote_and_empty_remote(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, remote=False)
            r = _run(project)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("origin", r.stderr)
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, remote=False)
            bare = Path(td) / "empty.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)],
                           capture_output=True, text=True, timeout=30)
            _git(project, "remote", "add", "origin", str(bare))
            r = _run(project)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("пуст", r.stderr)

    def test_abuse_args_do_not_exist(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            for arg in (("--branch", "main"), ("--base", "evil"), ("--force",),
                        ("--commit", "deadbeef")):
                r = _run(project, *arg)
                self.assertEqual(r.returncode, 2, f"{arg}: argparse обязан отвергнуть")

    def test_status_is_free_readonly(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, manifest=False)  # даже без манифеста
            r = _run(project, "--status")
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "status")
            self.assertIsNone(out["remote"])


if __name__ == "__main__":
    unittest.main()
