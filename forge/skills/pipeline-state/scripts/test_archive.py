#!/usr/bin/env python3
"""test_archive.py — архив доков завершённых строек.

Архивация физически УНОСИТ каталог с рабочего стола, и все её ошибки тихие: заархивировал
живой прогон — фаза не найдёт свои артефакты; заархивировал несведённую дельту — требование
пропало из `/forge-spec status` вместе с папкой, потому что дельты ищутся обходом
docs/feature-pipeline/. Поэтому гейты здесь проверяются поимённо, а не «в целом».

Запуск: python3 -m unittest test_archive
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
# `_util` живёт в двух scripts-каталогах с разным API — чужая копия в sys.modules даст
# ImportError на половине имён (см. шапку test_state_paths.py).
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != SCRIPTS:
    del sys.modules["_util"]

import archive  # noqa: E402

POLICY = {
    "project": {"name": "claims"},
    "docs": {"mode": "in-repo", "docs_path": "docs", "master": {"enabled": False}},
    "spec": {"id_prefix": "REQ", "scenario_floor": True},
}

SDD = """# SDD: Экспорт отчёта по заявкам

## 1. Назначение и результат (Purpose & Outcomes)
Оператор получает отчёт по заявкам за период.

## 3. Функциональные требования (Given-When-Then)
- **Given** есть заявки **When** оператор запросил отчёт **Then** отчёт сформирован
"""

FULL_STEPS = ["02-sdd", "02-design", "04-test-T1", "04-build-T1", "05-tests", "06-spec"]


def _steps(ids, status="completed", **override):
    out = []
    for i in ids:
        out.append({"id": i, "title": i, "status": override.get(i, status), "depends_on": []})
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ground").mkdir()
        self.policy = json.loads(json.dumps(POLICY))
        self._write_policy()

    def tearDown(self):
        self._tmp.cleanup()

    def _write_policy(self):
        (self.root / "ground" / "policy.json").write_text(
            json.dumps(self.policy, ensure_ascii=False), encoding="utf-8")

    def _run(self, skill, feature, steps, **extra):
        d = self.root / "ground" / "statements" / skill / feature
        d.mkdir(parents=True, exist_ok=True)
        man = {"version": 2, "skill": skill, "pipeline_id": "2026-09-22-100000",
               "started_at": "2026-09-22T10:00:00Z", "steps": steps}
        man.update(extra)
        (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
        return d

    def _docs(self, rel, sdd=False):
        d = self.root / "docs" / "feature-pipeline" / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "tech-design.md").write_text("# план\n", encoding="utf-8")
        if sdd:
            (d / "sdd.md").write_text(SDD, encoding="utf-8")
        return d

    def _arc(self, rel):
        return self.root / "docs" / "archive" / rel


class TestReadyRun(Base):
    """Готовая стройка уезжает целиком, вместе с провенансом переноса."""

    def setUp(self):
        super().setUp()
        self._run("feature-pipeline", "STOR-100", _steps(FULL_STEPS))
        self.src = self._docs("STOR-100")

    def test_put_moves_docs_and_writes_meta(self):
        res = archive.archive_feature(self.root, "STOR-100")
        self.assertTrue(res["moved"])
        self.assertFalse(self.src.exists())
        target = self._arc("STOR-100")
        self.assertTrue((target / "tech-design.md").is_file())
        meta = json.loads((target / archive.META_NAME).read_text(encoding="utf-8"))
        self.assertEqual(meta["slug"], "STOR-100")
        self.assertEqual(meta["skill"], "feature-pipeline")
        self.assertEqual(meta["source"], "STOR-100")
        self.assertEqual(meta["steps"]["06-spec"], "completed")

    def test_state_is_not_touched(self):
        archive.archive_feature(self.root, "STOR-100")
        man = self.root / "ground/statements/feature-pipeline/STOR-100/manifest.json"
        self.assertTrue(man.is_file(), "стейт прогона архивация трогать не должна")

    def test_dry_run_writes_nothing(self):
        res = archive.archive_feature(self.root, "STOR-100", dry_run=True)
        self.assertFalse(res["moved"])
        self.assertTrue(self.src.is_dir())
        self.assertFalse((self.root / "docs" / "archive").exists())

    def test_collision_gets_timestamp_suffix(self):
        self._arc("STOR-100").mkdir(parents=True)
        res = archive.archive_feature(self.root, "STOR-100")
        self.assertNotEqual(Path(res["target"]).name, "STOR-100")
        self.assertTrue(Path(res["target"]).name.startswith("STOR-100-"))
        self.assertTrue((Path(res["target"]) / "tech-design.md").is_file())

    def test_restore_returns_docs(self):
        archive.archive_feature(self.root, "STOR-100")
        res = archive.restore_feature(self.root, "STOR-100")
        self.assertTrue(res["moved"])
        self.assertTrue((self.src / "tech-design.md").is_file())
        self.assertFalse((self.src / archive.META_NAME).exists())
        self.assertFalse(self._arc("STOR-100").exists())

    def test_restore_refuses_when_place_taken(self):
        archive.archive_feature(self.root, "STOR-100")
        self._docs("STOR-100")
        with self.assertRaises(archive.Fail):
            archive.restore_feature(self.root, "STOR-100")

    def test_list_reports_archived(self):
        archive.archive_feature(self.root, "STOR-100")
        rows = archive.list_archived(self.root)
        self.assertEqual([r["slug"] for r in rows], ["STOR-100"])


class TestCompletionGate(Base):
    """«Готово» вычисляется, а не лежит полем — и последнее слово за финальным шагом."""

    def test_refuses_unfinished_run(self):
        self._run("feature-pipeline", "STOR-100",
                  _steps(FULL_STEPS, **{"05-tests": "pending"}))
        src = self._docs("STOR-100")
        with self.assertRaises(archive.Fail) as cm:
            archive.archive_feature(self.root, "STOR-100")
        self.assertIn("05-tests", str(cm.exception))
        self.assertTrue(src.is_dir())
        self.assertFalse((self.root / "docs" / "archive").exists())

    def test_refuses_when_final_step_only_skipped(self):
        # Все шаги терминальны (summarize == completed), но спеку никто не писал.
        self._run("feature-pipeline", "STOR-100",
                  _steps(FULL_STEPS, **{"06-spec": "skipped"}))
        self._docs("STOR-100")
        with self.assertRaises(archive.Fail):
            archive.archive_feature(self.root, "STOR-100")

    def test_force_passes_unfinished_and_records_reason(self):
        self._run("feature-pipeline", "STOR-100",
                  _steps(FULL_STEPS, **{"05-tests": "pending"}))
        self._docs("STOR-100")
        archive.archive_feature(self.root, "STOR-100", force=True, reason="закрыли вне forge")
        meta = json.loads((self._arc("STOR-100") / archive.META_NAME).read_text(encoding="utf-8"))
        self.assertTrue(meta["forced"])
        self.assertEqual(meta["reason"], "закрыли вне forge")

    def test_per_task_steps_after_final_do_not_shift_the_final(self):
        # add_steps дописывает 04-* В КОНЕЦ, уже после 06-spec: «последний шаг манифеста»
        # финальным считать нельзя.
        man_steps = _steps(FULL_STEPS) + _steps(["04-test-T9", "04-build-T9"])
        self._run("feature-pipeline", "STOR-100", man_steps)
        self.assertEqual(archive.final_step_ids("feature-pipeline", {"steps": man_steps}),
                         ["06-spec"])

    def test_final_steps_come_from_registries(self):
        for skill, expect in (("forgefix", "fix-spec"), ("forgelite", "lite-verify")):
            reg = SCRIPTS.parents[1] / skill / "references" / "manifest-steps.json"
            ids = [s["id"] for s in json.loads(reg.read_text(encoding="utf-8"))]
            self.assertEqual(archive.final_step_ids(skill, {"steps": _steps(ids)}), [expect],
                             f"{skill}: финальный шаг разошёлся с реестром")


class TestFixLayout(Base):
    """Фикс живёт ВНУТРИ папки своей стори — путь обязан сохраниться."""

    def setUp(self):
        super().setUp()
        self._run("forgefix", "BUG-512",
                  _steps(["fix-intake", "fix-diag", "fix-red", "fix-green",
                          "fix-verify", "fix-spec"]),
                  inputs={"story": "STOR-100"})
        self._docs("STOR-100")
        self.fix = self._docs("STOR-100/fixes/BUG-512")

    def test_fix_keeps_story_path_in_archive(self):
        res = archive.archive_feature(self.root, "STOR-100/fixes/BUG-512")
        self.assertTrue((self._arc("STOR-100/fixes/BUG-512") / "tech-design.md").is_file())
        self.assertEqual(res["slug"], "STOR-100/fixes/BUG-512")
        # husk-каталог fixes/ опустел — его не оставляем
        self.assertFalse((self.root / "docs/feature-pipeline/STOR-100/fixes").exists())

    def test_short_bug_key_resolves(self):
        res = archive.archive_feature(self.root, "BUG-512")
        self.assertEqual(res["slug"], "STOR-100/fixes/BUG-512")

    def test_short_bug_key_ambiguous_asks_for_decision(self):
        self._run("forgefix", "BUG-512x", _steps(["fix-spec"]))
        self._docs("STOR-200/fixes/BUG-512")
        with self.assertRaises(archive.Fail) as cm:
            archive.archive_feature(self.root, "BUG-512")
        self.assertEqual(cm.exception.code, 3)

    def test_refuses_story_while_fix_inside_is_live(self):
        self._run("feature-pipeline", "STOR-100", _steps(FULL_STEPS))
        self._run("forgefix", "BUG-777",
                  _steps(["fix-intake", "fix-diag", "fix-spec"], **{"fix-spec": "in_progress"}),
                  inputs={"story": "STOR-100"})
        self._docs("STOR-100/fixes/BUG-777")
        with self.assertRaises(archive.Fail) as cm:
            archive.archive_feature(self.root, "STOR-100")
        self.assertIn("BUG-777", str(cm.exception))
        self.assertTrue((self.root / "docs/feature-pipeline/STOR-100").is_dir())


class TestDeltaGate(Base):
    """Дельта уезжает из обхода `_features` вместе с папкой — несведённую не отпускаем."""

    def setUp(self):
        super().setUp()
        self._run("feature-pipeline", "STOR-100", _steps(FULL_STEPS))
        self._docs("STOR-100", sdd=True)

    def test_master_disabled_does_not_block(self):
        self.assertEqual(archive.delta_state(self.root, "STOR-100"), "no-master")
        archive.archive_feature(self.root, "STOR-100")
        self.assertTrue(self._arc("STOR-100").is_dir())

    def test_unmerged_delta_blocks(self):
        self.policy["docs"]["master"]["enabled"] = True
        self._write_policy()
        self.assertEqual(archive.delta_state(self.root, "STOR-100"), "new")
        with self.assertRaises(archive.Fail) as cm:
            archive.archive_feature(self.root, "STOR-100")
        self.assertIn("не сведена", str(cm.exception))

    def test_archived_delta_leaves_spec_scan(self):
        sys.path.insert(0, str(SCRIPTS.parents[1] / "system-analyst" / "scripts"))
        import spec_cli
        self.assertEqual([s for s, _ in spec_cli._features(self.root)], ["STOR-100"])
        archive.archive_feature(self.root, "STOR-100")
        self.assertEqual(spec_cli._features(self.root), [],
                         "архив-сиблинг не должен попадать в обход дельт")


class TestSeparateRepoDocs(Base):
    """separate-repo: архив обязан ехать в репо доков, а не в корень проекта."""

    def test_target_is_inside_docs_repo(self):
        specrepo = self.root / "spec-repo"
        (specrepo / "feature-pipeline").mkdir(parents=True)
        self.policy["docs"].update({"mode": "separate-repo", "repo_path": str(specrepo)})
        self._write_policy()
        self._run("feature-pipeline", "STOR-100", _steps(FULL_STEPS))
        d = specrepo / "feature-pipeline" / "STOR-100"
        d.mkdir(parents=True)
        (d / "tech-design.md").write_text("# план\n", encoding="utf-8")
        res = archive.archive_feature(self.root, "STOR-100")
        self.assertEqual(Path(res["target"]), specrepo / "archive" / "STOR-100")
        self.assertTrue((specrepo / "archive" / "STOR-100" / "tech-design.md").is_file())


class TestSlugSafety(Base):
    def test_traversal_is_refused(self):
        for bad in ("../../etc", "/etc/passwd", "~/x", "a/b", "a/fixes", ""):
            with self.subTest(slug=bad), self.assertRaises(archive.Fail):
                archive.norm_slug(bad)

    def test_unknown_feature_is_refused(self):
        self._docs("STOR-404")
        with self.assertRaises(archive.Fail) as cm:
            archive.archive_feature(self.root, "STOR-404")
        self.assertIn("нет прогона", str(cm.exception))


class TestStatusAndCli(Base):
    def setUp(self):
        super().setUp()
        self._run("feature-pipeline", "STOR-100", _steps(FULL_STEPS))
        self._docs("STOR-100")
        self._run("forgelite", "STOR-200",
                  _steps(["lite-jira", "lite-design", "lite-red", "lite-green", "lite-verify"],
                         **{"lite-verify": "pending"}))
        self._docs("STOR-200")

    def test_status_splits_ready_and_held(self):
        data = archive.status(self.root)
        ready = [r["slug"] for r in data["candidates"] if r["archivable"]]
        held = [r["slug"] for r in data["candidates"] if not r["archivable"]]
        self.assertEqual(ready, ["STOR-100"])
        self.assertEqual(held, ["STOR-200"])
        self.assertEqual(data["master_source"], "delta-first")

    def test_cli_put_and_exit_codes(self):
        self.assertEqual(archive.main(["--project", str(self.root), "put", "STOR-100"]), 0)
        self.assertEqual(archive.main(["--project", str(self.root), "put", "STOR-200"]), 2)
        self.assertEqual(archive.main(["--project", str(self.root), "put", "STOR-404"]), 2)

    def test_cli_force_requires_reason(self):
        rc = archive.main(["--project", str(self.root), "put", "STOR-200", "--force"])
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "docs" / "archive").exists())

    def test_cli_default_subcommand_is_status(self):
        self.assertEqual(archive.main(["--project", str(self.root)]), 0)


if __name__ == "__main__":
    unittest.main()
