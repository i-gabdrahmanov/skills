#!/usr/bin/env python3
"""Тесты doc_review_push.py — доставка дока (brd|sdd) на ветку задачи docs/<slug>.

У каждой Jira-задачи своя ветка docs/<slug>, общая для её brd.md и sdd.md; база — default-
ветка origin. Пины анти-абьюза: без валидного approval-маркера (провенанс record_approval)
и PASS <doc>-judge скрипт не пушит; коммитится ТОЛЬКО <doc>.md; force-push и произвольные
--path/--message/--branch отсутствуют по построению; worktree/HEAD/ЛОКАЛЬНЫЕ ветки не
трогаются (обновляется только remote-ветка задачи); default-ветка remote не трогается;
история remote не переписывается.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "doc_review_push.py"
SLUG = "STOR-7"
BRANCH = f"docs/{SLUG}"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=30)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "seed")


def _add_remote(repo: Path, base: Path) -> Path:
    bare = base / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], capture_output=True,
                   text=True, timeout=30)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare


def _mk_project(td: str, doc: str = "sdd", doc_text: str = "# DOC\n\nтело\n",
                slug: str = SLUG, marker: dict | None = ..., judge: dict | None = ...,
                remote: bool = True) -> Path:
    """Полный fixture: git-проект + manifest + judge + marker + <doc>.md + bare-remote."""
    project = Path(td) / "proj"
    _init_repo(project)
    if remote:
        _add_remote(project, Path(td))

    feat = project / "ground" / "statements" / "feature-pipeline" / slug
    feat.mkdir(parents=True)
    (feat / "manifest.json").write_text(
        json.dumps({"steps": [{"id": "02-sdd", "status": "in_progress"}]}), encoding="utf-8")

    if judge is ...:
        judge = {"produced_by": "run_judge", "passed": True}
    if judge is not None:
        (feat / "judges").mkdir()
        (feat / "judges" / f"{doc}-judge.json").write_text(json.dumps(judge), encoding="utf-8")

    if marker is ...:
        marker = {"produced_by": "record_approval", "key": f"{doc}-review-{slug}",
                  "approved_by": "user", "reason": "test"}
    if marker is not None:
        appr = project / "ground" / "approvals"
        appr.mkdir(parents=True)
        (appr / f"{doc}-review-{slug}.json").write_text(json.dumps(marker), encoding="utf-8")

    doc_dir = project / "docs" / "feature-pipeline" / slug
    doc_dir.mkdir(parents=True)
    (doc_dir / f"{doc}.md").write_text(doc_text, encoding="utf-8")
    return project


def _run(project: Path, *extra: str, doc: str = "sdd",
         slug: str = SLUG) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT),
                           "--doc", doc, "--feature", slug, "--json", *extra],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, cwd=str(project))


class TDocReviewPush(unittest.TestCase):
    def test_happy_path_task_branch_local_and_default_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            head_before = _git(project, "rev-parse", "HEAD").stdout.strip()
            main_before = _git(project, "rev-parse", "refs/heads/main").stdout.strip()
            branch_before = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            (project / "dirty.txt").write_text("не коммить меня", encoding="utf-8")

            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "pushed")
            self.assertEqual(out["branch"], BRANCH, "у задачи — своя ветка docs/<slug>")

            bare = Path(td) / "remote.git"
            tip = _git(bare, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip()
            self.assertEqual(tip, out["commit"])
            files = _git(bare, "diff-tree", "--no-commit-id", "--name-only", "-r",
                         tip).stdout.split()
            self.assertEqual(files, [f"docs/feature-pipeline/{SLUG}/sdd.md"],
                             "в коммите должен быть ровно один путь — sdd.md")
            self.assertEqual(out["parent"],
                             _git(bare, "rev-parse", "refs/heads/main").stdout.strip(),
                             "база новой ветки задачи — default-ветка origin")

            # default-ветка remote НЕ тронута
            self.assertEqual(_git(bare, "rev-parse", "refs/heads/main").stdout.strip(),
                             main_before, "remote main не должна двигаться")
            # worktree/HEAD/локальные ветки пользователя не тронуты; локальной docs/<slug> нет
            self.assertEqual(_git(project, "rev-parse", "HEAD").stdout.strip(), head_before)
            self.assertEqual(_git(project, "rev-parse", "refs/heads/main").stdout.strip(),
                             main_before)
            self.assertEqual(_git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                             branch_before)
            r2 = _git(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}")
            self.assertNotEqual(r2.returncode, 0, "локальная ветка задачи не создаётся")
            self.assertTrue((project / "dirty.txt").exists())
            self.assertIn("dirty.txt", _git(project, "status", "--porcelain").stdout)

    def test_both_docs_share_task_branch(self):
        # BRD приезжает в фазе 00, SDD — в фазе 02: оба на ОДНОЙ ветке docs/<slug>
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, doc="brd")
            r = _run(project, doc="brd")
            self.assertEqual(r.returncode, 0, r.stderr)
            first = json.loads(r.stdout)
            self.assertEqual(first["branch"], BRANCH)
            msg = _git(project, "log", "-1", "--pretty=%B", first["commit"]).stdout
            self.assertIn("BRD", msg)

            # позже — SDD той же фичи (судья+маркер для sdd)
            feat = project / "ground" / "statements" / "feature-pipeline" / SLUG
            (feat / "judges" / "sdd-judge.json").write_text(json.dumps(
                {"produced_by": "run_judge", "passed": True}), encoding="utf-8")
            key = f"sdd-review-{SLUG}"
            (project / "ground" / "approvals" / f"{key}.json").write_text(json.dumps(
                {"produced_by": "record_approval", "key": key, "approved_by": "user",
                 "reason": "test"}), encoding="utf-8")
            (project / "docs" / "feature-pipeline" / SLUG / "sdd.md").write_text(
                "# SDD\n\nтело\n", encoding="utf-8")

            r = _run(project, doc="sdd")
            self.assertEqual(r.returncode, 0, r.stderr)
            second = json.loads(r.stdout)
            self.assertEqual(second["branch"], BRANCH, "та же ветка задачи")
            self.assertEqual(second["parent"], first["commit"],
                             "SDD ложится ПОВЕРХ BRD-коммита ветки задачи")

            bare = Path(td) / "remote.git"
            files = _git(bare, "ls-tree", "-r", "--name-only",
                         f"refs/heads/{BRANCH}").stdout.split()
            self.assertIn(f"docs/feature-pipeline/{SLUG}/brd.md", files)
            self.assertIn(f"docs/feature-pipeline/{SLUG}/sdd.md", files)

    def test_doc_arg_required_and_validated(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT),
                                "--feature", SLUG], capture_output=True, text=True,
                               timeout=60, cwd=str(project))
            self.assertEqual(r.returncode, 2, "без --doc argparse обязан отвергнуть")
            r = _run(project, doc="tech-design")
            self.assertEqual(r.returncode, 2, "--doc вне {brd,sdd} обязан отвергаться")

    def test_cross_doc_marker_blocked(self):
        # маркер выписан на brd, доставить пытаются sdd — ключ не совпадает
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, marker=None)
            appr = project / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / f"sdd-review-{SLUG}.json").write_text(json.dumps(
                {"produced_by": "record_approval", "key": f"brd-review-{SLUG}",
                 "approved_by": "user", "reason": "x"}), encoding="utf-8")
            r = _run(project)
            self.assertEqual(r.returncode, 2, "маркер другого дока не должен пройти")

    def test_no_marker_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, marker=None)
            r = _run(project)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("record_approval", r.stderr)
            self.assertIn(f"sdd-review-{SLUG}", r.stderr)

    def test_handwritten_marker_without_provenance_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, marker={"approved_by": "user", "reason": "x",
                                              "key": f"sdd-review-{SLUG}"})
            r = _run(project)
            self.assertEqual(r.returncode, 2, "маркер без провенанса не должен пройти")

    def test_marker_with_foreign_key_blocked(self):
        # переименованный чужой маркер: файл на месте, но key внутри — другой фичи
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, marker={"produced_by": "record_approval",
                                              "key": "sdd-review-OTHER-1",
                                              "approved_by": "user", "reason": "x"})
            r = _run(project)
            self.assertEqual(r.returncode, 2, "маркер с чужим key не должен пройти")

    def test_judge_missing_or_failed_blocked(self):
        for judge in (None, {"produced_by": "run_judge", "passed": False},
                      {"produced_by": "self", "passed": True}):
            with tempfile.TemporaryDirectory() as td:
                project = _mk_project(td, judge=judge)
                r = _run(project)
                self.assertEqual(r.returncode, 2, f"judge={judge}: {r.stderr}")
                self.assertIn("sdd-judge", r.stderr)

    def test_missing_or_empty_doc_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, doc_text="   \n")
            r = _run(project)
            self.assertEqual(r.returncode, 2, r.stderr)
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            (project / "docs" / "feature-pipeline" / SLUG / "sdd.md").unlink()
            r = _run(project)
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_abuse_args_do_not_exist(self):
        # пин анти-абьюза: скриптом нельзя доставить произвольный файл/ветку/сообщение
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            for arg in (("--path", "src/Evil.java"), ("--message", "evil"),
                        ("--branch", "release"), ("--force",), ("--remote", "evil")):
                r = _run(project, *arg)
                self.assertEqual(r.returncode, 2, f"{arg}: argparse обязан отвергнуть")

    def test_commit_message_floor(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            r = _run(project, "--jira-key", "STOR-99")
            self.assertEqual(r.returncode, 0, r.stderr)
            tip = json.loads(r.stdout)["commit"]
            msg = _git(project, "log", "-1", "--pretty=%B", tip).stdout
            self.assertIn(SLUG, msg)
            self.assertIn("STOR-99", msg)
            self.assertNotIn("co-authored-by", msg.lower())

    def test_diverged_remote_then_retry_builds_on_new_tip(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            first = json.loads(r.stdout)["commit"]

            # аналитик дописал коммит в ветку задачи на remote (через клон)
            clone = Path(td) / "analyst"
            subprocess.run(["git", "clone", "-q", "-b", BRANCH,
                            str(Path(td) / "remote.git"), str(clone)],
                           capture_output=True, text=True, timeout=30)
            _git(clone, "config", "user.email", "a@a")
            _git(clone, "config", "user.name", "analyst")
            (clone / "docs" / "feature-pipeline" / SLUG / "sdd.md").write_text(
                "# SDD\n\nправки аналитика\n", encoding="utf-8")
            _git(clone, "commit", "-aqm", "review notes")
            _git(clone, "push", "-q", "origin", BRANCH)
            analyst_tip = _git(clone, "rev-parse", "HEAD").stdout.strip()

            # наш sdd.md изменился → повтор: fetch возьмёт remote-tip ветки родителем
            (project / "docs" / "feature-pipeline" / SLUG / "sdd.md").write_text(
                "# SDD\n\nверсия 2\n", encoding="utf-8")
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["parent"], analyst_tip, "родитель — remote-tip аналитика")

            bare = Path(td) / "remote.git"
            log = _git(bare, "rev-list", f"refs/heads/{BRANCH}").stdout.split()
            self.assertIn(first, log, "история не переписана (без force)")
            self.assertIn(analyst_tip, log)

    def test_idempotent_rerun_and_second_commit_on_change(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            first = json.loads(r.stdout)["commit"]

            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "up-to-date")
            self.assertEqual(out["commit"], first, "повтор без изменений не создаёт коммитов")

            (project / "docs" / "feature-pipeline" / SLUG / "sdd.md").write_text(
                "# SDD\n\nдополнено\n", encoding="utf-8")
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "pushed")
            self.assertEqual(out["parent"], first, "второй коммит — поверх первого")

    def test_no_remote_origin(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, remote=False)
            status_before = _git(project, "status", "--porcelain").stdout
            r = _run(project)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("origin", r.stderr)
            self.assertEqual(_git(project, "status", "--porcelain").stdout,
                             status_before, "локальное состояние не испорчено")

    def test_empty_remote_root_commit(self):
        # свежий пустой спек-remote: корневой коммит ТОЛЬКО с доком на ветке задачи
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, remote=False)
            bare = Path(td) / "empty.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)],
                           capture_output=True, text=True, timeout=30)
            _git(project, "remote", "add", "origin", str(bare))
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["branch"], BRANCH)
            self.assertIsNone(out["parent"])
            files = _git(bare, "ls-tree", "-r", "--name-only",
                         out["commit"]).stdout.split()
            self.assertEqual(files, [f"docs/feature-pipeline/{SLUG}/sdd.md"],
                             "корневой коммит не должен тащить локальную историю")

    def test_detached_head(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            head = _git(project, "rev-parse", "HEAD").stdout.strip()
            _git(project, "checkout", "-q", "--detach", head)
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_git(project, "rev-parse", "HEAD").stdout.strip(), head)

    def test_separate_repo_docs_mode(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            # выносим доки во внешний спек-репо со своим bare-remote
            spec = Path(td) / "spec-repo"
            _init_repo(spec)
            spec_base = Path(td) / "spec-remote-base"
            spec_base.mkdir()
            bare = spec_base / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)],
                           capture_output=True, text=True, timeout=30)
            _git(spec, "remote", "add", "origin", str(bare))
            _git(spec, "push", "-q", "-u", "origin", "main")
            doc_dir = spec / "feature-pipeline" / SLUG
            doc_dir.mkdir(parents=True)
            (doc_dir / "sdd.md").write_text("# SDD\n\nspec-repo\n", encoding="utf-8")
            (project / "ground" / "pipeline.json").write_text(json.dumps(
                {"docs": {"mode": "separate-repo", "repo_path": str(spec)}}), encoding="utf-8")

            proj_main_before = _git(project, "rev-parse", "refs/heads/main").stdout.strip()
            r = _run(project)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(Path(out["repo"]).resolve(), spec.resolve(),
                             "ветка задачи создаётся в репо спеки")
            tip = _git(bare, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip()
            self.assertEqual(tip, out["commit"])
            # remote ПРОЕКТА не тронут, ветки задачи в проекте нет
            self.assertEqual(_git(project, "rev-parse", "refs/heads/main").stdout.strip(),
                             proj_main_before)
            proj_bare = Path(td) / "remote.git"
            self.assertEqual(_git(proj_bare, "rev-parse", "refs/heads/main").stdout.strip(),
                             proj_main_before)
            r2 = _git(proj_bare, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}")
            self.assertNotEqual(r2.returncode, 0)

    def test_status_is_free_readonly(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, marker=None)  # без маркера
            r = _run(project, "--status")
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "status")
            self.assertFalse(out["approval"])
            self.assertTrue(out["judge_pass"])

    def test_project_mismatch_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            other = Path(td) / "other"
            _init_repo(other)
            r = _run(project, "--project", str(other))
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_traversal_slug_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            r = _run(project, slug="../evil")
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_secret_in_doc_blocked_before_git(self):
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td, doc_text="# SDD\n\nkey AKIAIOSFODNN7EXAMPLE\n")
            bare = Path(td) / "remote.git"
            r = _run(project)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("секрет", r.stderr)
            r2 = _git(bare, "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}")
            self.assertNotEqual(r2.returncode, 0, "до git-действий дойти не должно")

    def test_foreign_namespace_blocked(self):
        # фича есть только в forgelite → скоуп «только feature-pipeline» не пускает
        with tempfile.TemporaryDirectory() as td:
            project = _mk_project(td)
            feat = project / "ground" / "statements" / "feature-pipeline" / SLUG
            lite = project / "ground" / "statements" / "forgelite" / SLUG
            lite.parent.mkdir(parents=True, exist_ok=True)
            feat.rename(lite)
            r = _run(project)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("feature-pipeline", r.stderr)


if __name__ == "__main__":
    unittest.main()
