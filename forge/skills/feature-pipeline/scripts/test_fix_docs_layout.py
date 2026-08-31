#!/usr/bin/env python3
"""test_fix_docs_layout.py — раскладка артефактов ФИКСА относительно фичи.

Фикс минорного дефекта не заводит собственную «фичу»: он чинит поведение, которое уже описано
стори, поэтому его артефакты (fix-plan.md, task-plan.json, дельта sdd.md) живут ВНУТРИ папки
стори — <docs>/feature-pipeline/<стори>/fixes/<баг>. Раньше они ложились плоско, рядом со
стори, и в docs/feature-pipeline баги стояли в одном ряду с фичами без всякой связи.

Пины: путь со стори, фолбэк без стори ('none' — это ответ пользователя, а не пропуск вопроса),
слаг дельты для /forge-spec, работа CLI (брифы зовут именно его) и анти-traversal.

Exit: 0 — ок, 1 — раскладка разъехалась.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

S = Path(__file__).resolve().parent
sys.path.insert(0, str(S))
import skill_paths  # noqa: E402


def _project(td: str, docs: dict | None = None) -> Path:
    root = Path(td)
    (root / "ground").mkdir(parents=True, exist_ok=True)
    (root / "ground" / "pipeline.json").write_text(
        json.dumps({"docs": docs or {"mode": "in-repo", "docs_path": "docs",
                                     "feature_subdir": "feature-pipeline"}}),
        encoding="utf-8")
    return root


class FixDocsDir(unittest.TestCase):
    def test_fix_lives_inside_story_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            self.assertEqual(
                skill_paths.fix_docs_dir(root, "BUG-512", "STOR-100"),
                root / "docs/feature-pipeline/STOR-100/fixes/BUG-512")

    def test_unknown_story_falls_back_to_flat(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            flat = root / "docs/feature-pipeline/BUG-512"
            for story in (None, "", "none", "NONE", "-"):
                self.assertEqual(skill_paths.fix_docs_dir(root, "BUG-512", story), flat)

    def test_separate_repo_keeps_nesting(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as spec:
            root = _project(td, {"mode": "separate-repo", "repo_path": spec})
            self.assertEqual(skill_paths.fix_docs_dir(root, "BUG-512", "STOR-100"),
                             Path(spec) / "feature-pipeline/STOR-100/fixes/BUG-512")

    def test_delta_slug_matches_layout(self):
        self.assertEqual(skill_paths.fix_delta_slug("BUG-512", "STOR-100"),
                         "STOR-100/fixes/BUG-512")
        self.assertEqual(skill_paths.fix_delta_slug("BUG-512", "none"), "BUG-512")

    def test_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            for bad in ("../etc", "a/b", "~", ".."):
                with self.assertRaises(ValueError):
                    skill_paths.fix_docs_dir(root, bad, "STOR-100")
                with self.assertRaises(ValueError):
                    skill_paths.fix_docs_dir(root, "BUG-512", bad)


class FixDocsCli(unittest.TestCase):
    """Бриф forgefix резолвит путь ИМЕННО этой командой — нерезолвнутый плейсхолдер уводил
    запись артефактов в каталог харнеса."""

    def _run(self, root: Path, *args) -> str:
        r = subprocess.run([sys.executable, str(S / "skill_paths.py"), "fix-docs",
                            "--project", str(root), *args],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_cli_prints_nested_path_and_slug(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            # CLI резолвит --project (symlink /var → /private/var на macOS) — сравниваем resolve()
            self.assertEqual(
                Path(self._run(root, "--feature", "BUG-512", "--story", "STOR-100")),
                (root / "docs/feature-pipeline/STOR-100/fixes/BUG-512").resolve())
            self.assertEqual(self._run(root, "--feature", "BUG-512", "--story", "STOR-100",
                                       "--print-slug"), "STOR-100/fixes/BUG-512")

    def test_cli_requires_bug_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = subprocess.run([sys.executable, str(S / "skill_paths.py"), "fix-docs",
                                "--project", str(root)],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2, r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
