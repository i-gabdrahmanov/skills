#!/usr/bin/env python3
"""Тесты для _project.py — единого resolv'ера проекта."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _project import (
    active_feature_with_skill,
    gigacode_home,
    skills_dir,
    find_project_root,
    load_pipeline_config,
    resolve_active_run,
    verify_environment,
    resolve_skill_path,
    resolve_hook_path,
)


class TestProjectResolver(unittest.TestCase):
    """Тесты для _project.py — единого resolv'ера."""

    def test_gigacode_home_exists(self):
        """ПРОЕКТНАЯ модель: gigacode_home() = база кода (родитель hooks/), а НЕ ~/.gigacode.
        В source-репо это корень репо; в развёрнутом проекте — <project>/.gigacode."""
        path = gigacode_home()
        self.assertTrue(path.exists(), f"{path} does not exist")
        # База = родитель каталога hooks/, где лежит _project.py.
        expected = Path(__file__).resolve().parents[2]
        self.assertEqual(path, expected)
        self.assertNotEqual(path, Path.home() / ".gigacode",
                            "регресс: вернулись к мёртвому ~/.gigacode-контракту")

    def test_skills_dir(self):
        """skills_dir = <база>/skills (проектная модель, не ~/.gigacode/skills)."""
        path = skills_dir()
        self.assertEqual(path, gigacode_home() / "skills")
        self.assertTrue(path.exists(), f"{path} does not exist")

    def test_find_project_root(self):
        """find_project_root поднимается от вложенного каталога к корню с .git.

        Раньше тест звал find_project_root() от cwd процесса и требовал .git/build.gradle в
        ответе — то есть проверял, ГДЕ лежит копия форжа, а не резолвер: в распакованном
        дистрибутиве (боевой прогон v0.4.5, tasks/001) база всегда была красной, 117/118,
        и настоящий фейл на её фоне не видно."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            (root / ".git").mkdir()
            nested = root / "service" / "src" / "main"
            nested.mkdir(parents=True)
            self.assertEqual(find_project_root(nested).resolve(), root)

    def test_find_project_root_fallback(self):
        """find_project_root возвращает cwd если ничего не нашёл."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = find_project_root(Path(tmpdir))
            self.assertEqual(root, Path(tmpdir))

    def test_load_pipeline_config(self):
        """load_pipeline_config всегда возвращает dict (не падает)."""
        cfg = load_pipeline_config()
        self.assertIsInstance(cfg, dict)

    def test_verify_environment(self):
        """verify_environment = True (runtime установлен)."""
        self.assertTrue(verify_environment())

    def test_resolve_skill_path(self):
        """resolve_skill_path строит путь к существующему скрипту."""
        path = resolve_skill_path("pipeline-state", "scripts", "update.py")
        self.assertTrue(path.exists(), f"{path} not found")
        self.assertEqual(path.suffix, ".py")
        self.assertIn("pipeline-state", str(path))

    def test_resolve_hook_path(self):
        """resolve_hook_path строит путь к существующему хуку."""
        path = resolve_hook_path("state-recorder")
        self.assertTrue(path.exists(), f"{path} not found")
        self.assertEqual(path.name, "state-recorder.py")


class TestActiveRunResolver(unittest.TestCase):
    """resolve_active_run — ЕДИНЫЙ предикат «какой прогон активен».

    Пин на инцидент: брошенный прогон (чужой, подтянутый git'ом, или свой, умерший на
    первом шаге) был свежее по mtime и становился «активной фичей» для config.py,
    gate-guard и risk_ladder — per-feature вход новой фичи молча уезжал в ЕГО манифест.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / ".git").mkdir()
        self.addCleanup(self._td.cleanup)

    def _run(self, skill, feature, steps, *, mtime=None):
        d = self.root / "ground" / "statements" / skill / feature
        d.mkdir(parents=True, exist_ok=True)
        mp = d / "manifest.json"
        mp.write_text(json.dumps({"version": 2, "skill": skill, "feature": feature,
                                  "steps": steps}), encoding="utf-8")
        if mtime is not None:
            os.utime(mp, (mtime, mtime))
        return mp

    def test_no_runs(self):
        self.assertIsNone(resolve_active_run(self.root)["path"])
        self.assertIsNone(active_feature_with_skill(self.root))

    def test_single_run_wins_without_parsing(self):
        """Один прогон — активен он, и манифест даже не парсится: цена резолва та же, что
        была (один stat), а битый JSON не должен делать проект «без активной фичи»."""
        d = self.root / "ground" / "statements" / "feature-pipeline" / "F1"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{битый", encoding="utf-8")
        run = resolve_active_run(self.root)
        self.assertEqual((run["skill"], run["feature"]), ("feature-pipeline", "F1"))
        self.assertFalse(run["ambiguous"])

    def test_completed_run_loses_to_live_even_if_newer(self):
        """Завершённый прогон СВЕЖЕЕ живого — активным всё равно остаётся живой.
        Это «второй прогон в репозитории»: вчерашняя фича лежит на месте, пока не заархивирована."""
        self._run("feature-pipeline", "OLD", [{"id": "01", "status": "pending"}], mtime=1000)
        self._run("forgefix", "DONE", [{"id": "01", "status": "completed"}], mtime=9000)
        run = resolve_active_run(self.root)
        self.assertEqual((run["skill"], run["feature"]), ("feature-pipeline", "OLD"))
        self.assertEqual(run["live"], [("feature-pipeline", "OLD")])
        self.assertFalse(run["ambiguous"])
        self.assertEqual(active_feature_with_skill(self.root), ("feature-pipeline", "OLD"))

    def test_two_live_runs_are_ambiguous(self):
        """Брошенный прогон живой ПО СТАТУСУ (шаги остались pending) — фильтр живости его не
        выбивает. Поэтому резолвер обязан честно сказать «неоднозначно», чтобы писатель
        per-feature значений отказал вместо угадывания по mtime."""
        self._run("forgelite", "SQUATTER", [{"id": "01", "status": "in_progress"}], mtime=9000)
        self._run("feature-pipeline", "MINE", [{"id": "01", "status": "pending"}], mtime=1000)
        run = resolve_active_run(self.root)
        self.assertTrue(run["ambiguous"])
        self.assertEqual(sorted(run["live"]),
                         [("feature-pipeline", "MINE"), ("forgelite", "SQUATTER")])
        # Тай-брейк для хуков остаётся mtime: ответить они обязаны хоть что-то.
        self.assertEqual((run["skill"], run["feature"]), ("forgelite", "SQUATTER"))

    def test_no_live_runs_falls_back_to_newest(self):
        """Живых нет вовсе — поведение как было (свежайший), иначе хуки разом стали бы noop."""
        self._run("feature-pipeline", "A", [{"id": "01", "status": "completed"}], mtime=1000)
        self._run("feature-pipeline", "B", [{"id": "01", "status": "skipped"}], mtime=9000)
        run = resolve_active_run(self.root)
        self.assertEqual(run["feature"], "B")
        self.assertEqual(run["live"], [])
        # Живых нет, а прогонов два — выбор всё равно тай-брейк, и запись per-feature
        # значения обязана отказать, а не угадать.
        self.assertTrue(run["ambiguous"])

    def test_archived_dir_is_not_a_run(self):
        self._run("feature-pipeline", "archived", [{"id": "01", "status": "pending"}], mtime=9000)
        self._run("feature-pipeline", "REAL", [{"id": "01", "status": "pending"}], mtime=1000)
        self.assertEqual(resolve_active_run(self.root)["feature"], "REAL")

    def test_skill_scopes_the_search(self):
        self._run("forgefix", "FIX", [{"id": "01", "status": "pending"}], mtime=9000)
        self._run("feature-pipeline", "FEAT", [{"id": "01", "status": "pending"}], mtime=1000)
        run = resolve_active_run(self.root, skill="feature-pipeline")
        self.assertEqual((run["skill"], run["feature"]), ("feature-pipeline", "FEAT"))

    def test_risk_ladder_shares_the_predicate(self):
        """Третья копия обхода (risk_ladder.active_manifest) сведена к тому же резолверу —
        иначе хуки и config.py расходились бы в том, какая фича активна."""
        import risk_ladder as R
        self._run("forgefix", "DONE", [{"id": "01", "status": "completed"}], mtime=9000)
        mine = self._run("feature-pipeline", "MINE", [{"id": "01", "status": "pending"}],
                         mtime=1000)
        self.assertEqual(R.active_manifest(self.root), mine)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()