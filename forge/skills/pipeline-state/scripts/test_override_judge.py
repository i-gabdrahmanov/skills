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
            "produced_by": "run_judge",
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

    def test_create_override_record(self):
        """override_judge пишет в журнал прогона запись с нужными полями."""

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
        rec = ov_mod.FE.override(self.project, self.skill, self.feature, "red-judge")
        self.assertIsNotNone(rec, "override не найден в журнале прогона")
        self.assertEqual(rec["judge"], "red-judge")
        self.assertEqual(rec["reason"], "No DB in CI")
        self.assertEqual(rec["approved_by"], "user")
        self.assertEqual(rec["produced_by"], "override_judge")

    def test_remove_revokes_without_losing_history(self):
        """Отзыв гасит override для гейтов, но сам факт снятия остаётся в журнале."""

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

        self.assertEqual(ov_mod.cmd_create(Args(), self.project), 0)
        self.assertEqual(ov_mod.cmd_remove(Args(), self.project), 0)
        self.assertIsNone(ov_mod.FE.override(self.project, self.skill, self.feature, "red-judge"),
                          "отозванный override не должен сниматься гейтами")
        # …но обе записи (создание и отзыв) по-прежнему на диске — это аудит, а не удаление
        log = ov_mod.FE.read_events(self.project, self.skill, self.feature)
        kinds = [r for r in log if r.get("kind") == "override" and r.get("target") == "red-judge"]
        self.assertEqual(len(kinds), 2, f"ожидались создание+отзыв, в журнале: {kinds}")
        self.assertTrue(kinds[-1].get("revoked"))
        # повторный отзыв — уже нечего отзывать
        self.assertEqual(ov_mod.cmd_remove(Args(), self.project), 1)

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
        """override_judge --remove гасит override, в т.ч. лежащий файлом старой раскладки.

        Проверяем ЭФФЕКТ (гейты его больше не видят), а не механику: раньше команда
        удаляла файл, теперь дописывает запись отзыва — evidence не уничтожается.
        """
        self._write_override("red-judge")
        path = ov_mod.override_path(self.project, self.skill, self.feature, "red-judge")
        self.assertTrue(path.exists())
        self.assertIsNotNone(ov_mod.FE.override(self.project, self.skill, self.feature,
                                                "red-judge"))

        class Args:
            judge = "red-judge"
            feature = self.feature
            project = str(self.project)
            skill = self.skill
            json = False

        rc = ov_mod.cmd_remove(Args(), self.project)
        self.assertEqual(rc, 0)
        self.assertIsNone(ov_mod.FE.override(self.project, self.skill, self.feature, "red-judge"),
                          "отозванный override всё ещё снимает гейт")
        self.assertTrue(path.exists(), "файл-маркер не должен удаляться — это история")

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


class TestOverrideJudgeBatch(unittest.TestCase):
    """Batch-режим (KIDPPRB-9254 п.6): один файл → N override'ов одной транзакцией.

    Проверяет:
      • YAML и JSON форматы (с обёрткой `overrides:` и баре-список);
      • атомарность (валидация ДО write; падение на одной записи отвергает весь батч);
      • идемпотентность (повторный прогон того же батча не дублирует записи);
      • поведение при уже активных override'ах (пропуск, не отказ);
      • совместимость с форматом журнала (FE.overrides видит записанное).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.skill = "feature-pipeline"
        self.feature = "KIDPPRB-9254"

    def tearDown(self):
        self._tmp.cleanup()

    def _batch_args(self, batch_path: Path):
        class Args:
            project = str(self.project)
            skill = self.skill
            batch = str(batch_path)
            json = False
            list = False
            remove = False
            judge = None
            reason = None
        return Args()

    def _write_batch_yaml(self, body: str) -> Path:
        p = self.project / "batch.yaml"
        p.write_text(body, encoding="utf-8")
        return p

    def _write_batch_json(self, body) -> Path:
        p = self.project / "batch.json"
        p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return p

    def _events_log(self) -> list:
        return ov_mod.FE.read_events(self.project, self.skill, self.feature)

    def _override_records(self) -> list:
        return [r for r in self._events_log() if r.get("kind") == "override"]

    def test_batch_yaml_with_overrides_wrapper(self):
        """YAML с обёрткой `overrides:` создаёт все override'ы одной транзакцией."""
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    decision: pass
    reason: "Quality confirmed by QA"
    evidence: "qa-report-2026-08-22.html"
    approver: "qa-lead"
  - task: {self.feature}
    gate: red
    reason: "Tests suppressed by supervisor"
    evidence: "ticket SUP-123"
    approver: "release-manager"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        # Оба override'а видны через FE.overrides
        self.assertIsNotNone(ov_mod.FE.override(self.project, self.skill, self.feature, "quality"))
        self.assertIsNotNone(ov_mod.FE.override(self.project, self.skill, self.feature, "red"))
        # В журнале — две записи override с правильными полями
        recs = self._override_records()
        self.assertEqual(len(recs), 2)
        by_gate = {r["target"]: r for r in recs}
        self.assertEqual(by_gate["quality"]["reason"], "Quality confirmed by QA")
        self.assertEqual(by_gate["quality"]["evidence"], "qa-report-2026-08-22.html")
        self.assertEqual(by_gate["quality"]["approved_by"], "qa-lead")
        self.assertEqual(by_gate["red"]["reason"], "Tests suppressed by supervisor")
        self.assertEqual(by_gate["red"]["approved_by"], "release-manager")

    def test_batch_yaml_bare_list(self):
        """YAML без обёртки `overrides:` — баре-список тоже принимается."""
        path = self._write_batch_yaml(f"""
- task: {self.feature}
  gate: quality
  reason: "QA OK"
  evidence: "qa.html"
  approver: "qa-lead"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._override_records()), 1)

    def test_batch_json_with_approvals_wrapper(self):
        """JSON формат с обёрткой `overrides:` — то же содержимое, JSON-синтаксис."""
        path = self._write_batch_json({
            "overrides": [
                {"task": self.feature, "gate": "quality", "reason": "QA OK",
                 "evidence": "qa.html", "approver": "qa-lead"},
                {"task": self.feature, "gate": "coverage",
                 "reason": "Coverage tool absent", "approver": "user"},
            ]
        })
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._override_records()), 2)
        self.assertIsNotNone(ov_mod.FE.override(
            self.project, self.skill, self.feature, "coverage"))

    def test_batch_json_bare_list(self):
        """JSON без обёртки — баре-список."""
        path = self._write_batch_json([
            {"task": self.feature, "gate": "quality", "reason": "QA OK",
             "evidence": "qa.html", "approver": "qa-lead"},
        ])
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._override_records()), 1)

    def test_batch_accepts_task_or_feature_aliases(self):
        """Маппинг YAML → CLI: `task` ≡ `feature`, `gate` ≡ `judge`."""
        path = self._write_batch_yaml(f"""
overrides:
  - feature: {self.feature}
    judge: quality
    reason: "QA OK"
    approver: "qa-lead"
  - task: {self.feature}
    gate: red
    reason: "Tests suppressed"
    approver: "release-manager"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._override_records()), 2)
        self.assertIsNotNone(ov_mod.FE.override(
            self.project, self.skill, self.feature, "quality"))
        self.assertIsNotNone(ov_mod.FE.override(
            self.project, self.skill, self.feature, "red"))

    def test_batch_atomic_on_missing_reason(self):
        """Падение валидации в одной записи → НИ одной записи в журнале."""
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "OK"
    approver: "qa-lead"
  - task: {self.feature}
    gate: red
    reason: ""
    approver: "release-manager"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 1, "батч с пустым reason должен быть отвергнут")
        # Атомарность: первая (валидная) запись НЕ попала на диск
        self.assertEqual(self._override_records(), [],
                         "атомарность нарушена — запись в журнале до валидации всего батча")
        self.assertIsNone(ov_mod.FE.override(
            self.project, self.skill, self.feature, "quality"))

    def test_batch_atomic_on_missing_task(self):
        """Запись без `task` отвергает весь батч."""
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "OK"
  - gate: red
    reason: "no task"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 1)
        self.assertEqual(self._override_records(), [])

    def test_batch_atomic_on_missing_gate(self):
        """Запись без `gate` отвергает весь батч."""
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "OK"
  - task: {self.feature}
    reason: "no gate"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 1)
        self.assertEqual(self._override_records(), [])

    def test_batch_idempotent_on_repeat(self):
        """Повторный прогон того же батча НЕ дублирует записи (target уже активен)."""
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "QA OK"
    evidence: "qa.html"
    approver: "qa-lead"
  - task: {self.feature}
    gate: red
    reason: "Tests suppressed"
    approver: "release-manager"
""")
        args = self._batch_args(path)
        self.assertEqual(ov_mod.cmd_batch(args, self.project), 0)
        first_count = len(self._override_records())
        self.assertEqual(first_count, 2)
        # Повторный прогон — те же записи, ничего не дублируется
        self.assertEqual(ov_mod.cmd_batch(args, self.project), 0)
        self.assertEqual(len(self._override_records()), 2,
                         "идемпотентность нарушена — записи дублируются")

    def test_batch_idempotent_partial(self):
        """Часть target'ов уже активна, часть — нет: пишем только новые."""
        # Предварительно создаём один override
        class SingleArgs:
            judge = "quality"
            feature = self.feature
            step_id = "01"
            reason = "pre-existing"
            project = str(self.project)
            skill = self.skill
            list = False
            remove = False
            json = False

        self.assertEqual(ov_mod.cmd_create(SingleArgs(), self.project), 0)
        self.assertEqual(len(self._override_records()), 1)

        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "QA OK"
    approver: "qa-lead"
  - task: {self.feature}
    gate: red
    reason: "Tests suppressed"
    approver: "release-manager"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        recs = self._override_records()
        # Итого 2 записи: quality (pre-existing) + red (новый из батча)
        self.assertEqual(len(recs), 2)
        targets = {r["target"] for r in recs}
        self.assertEqual(targets, {"quality", "red"})
        # quality остался со старой reason (pre-existing), не перезаписан
        quality_rec = next(r for r in recs if r["target"] == "quality")
        self.assertEqual(quality_rec["reason"], "pre-existing",
                         "идемпотентность: существующая запись перезаписана?")

    def test_batch_skipped_revoke_then_re_add(self):
        """После revoke старый target снова доступен — батч добавляет запись."""
        class SingleArgs:
            judge = "quality"
            feature = self.feature
            step_id = "01"
            reason = "OK"
            project = str(self.project)
            skill = self.skill
            list = False
            remove = False
            json = False

        ov_mod.cmd_create(SingleArgs(), self.project)
        # Снимаем override
        class RemoveArgs:
            judge = "quality"
            feature = self.feature
            project = str(self.project)
            skill = self.skill
            json = False

        self.assertEqual(ov_mod.cmd_remove(RemoveArgs(), self.project), 0)
        # Повторный батч должен ПРОПИСАТЬ quality заново
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "re-approved"
    approver: "qa-lead"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        self.assertIsNotNone(ov_mod.FE.override(
            self.project, self.skill, self.feature, "quality"))
        # И в журнале — 3 записи (create + revoke + new grant)
        self.assertEqual(len(self._override_records()), 3)

    def test_batch_writes_all_lines_in_single_journal_append(self):
        """Атомарность: все записи батча пишутся одним flock-вызовом.

        Косвенный признак: ВСЕ записи имеют одинаковый ts (один _iso_now() на батч).
        Разные ts означали бы, что записи прошли через FE.append_event по одной — а это
        не атомарно."""
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: a
    reason: "r1"
    approver: "qa"
  - task: {self.feature}
    gate: b
    reason: "r2"
    approver: "qa"
  - task: {self.feature}
    gate: c
    reason: "r3"
    approver: "qa"
""")
        ov_mod.cmd_batch(self._batch_args(path), self.project)
        recs = self._override_records()
        self.assertEqual(len(recs), 3)
        ts_values = {r["ts"] for r in recs}
        self.assertEqual(len(ts_values), 1,
                         f"атомарность: ожидался один ts на батч, "
                         f"получено {len(ts_values)} ({ts_values})")

    def test_batch_empty_file_returns_error(self):
        """Файл без override'ов (или пустой) → rc=1, ничего не записано."""
        path = self._write_batch_yaml("")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 1)
        self.assertEqual(self._override_records(), [])

    def test_batch_invalid_wrapper_returns_error(self):
        """Файл с объектом без `overrides:` и не список → rc=1."""
        path = self._write_batch_yaml("foo: bar\n")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 1)
        self.assertEqual(self._override_records(), [])

    def test_batch_nonexistent_file_returns_error(self):
        """Несуществующий файл → rc=1, не падать с traceback."""
        args = self._batch_args(self.project / "does-not-exist.yaml")
        rc = ov_mod.cmd_batch(args, self.project)
        self.assertEqual(rc, 1)

    def test_batch_cli_incompatible_with_remove(self):
        """--batch + --remove → rc=1 (CLI-уровень, проверяется через main())."""
        import subprocess
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "OK"
""")
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "override_judge.py"),
             "--project", str(self.project),
             "--skill", self.skill,
             "--batch", str(path),
             "--remove"],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 1, f"stderr: {rc.stderr}")
        self.assertIn("несовместим", rc.stderr)

    def test_batch_skipped_existing_via_journal(self):
        """Идемпотентность читает journal, а не legacy-файлы — фиксируем поведение.

        Создаём запись через legacy-файл (старая раскладка) → батч с тем же target
        должен ПРОПУСТИТЬ запись (FE.overrides агрегирует legacy+journal)."""
        # Legacy override
        ov_dir = (self.project / "ground" / "statements" / self.skill / self.feature
                  / "overrides")
        ov_dir.mkdir(parents=True, exist_ok=True)
        (ov_dir / "quality.json").write_text(json.dumps({
            "$schema": "pipeline/judge-override@1",
            "judge": "quality", "feature_slug": self.feature,
            "step_id": "01", "override_at": "2026-01-01T00:00:00Z",
            "reason": "legacy", "approved_by": "user",
        }), encoding="utf-8")
        # Проверяем: FE.overrides видит legacy
        self.assertTrue(any(o["target"] == "quality" for o in
                            ov_mod.FE.overrides(self.project, self.skill, self.feature)))
        # Батч с тем же target — должен пропустить
        path = self._write_batch_yaml(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "would-be duplicate"
    approver: "qa"
""")
        rc = ov_mod.cmd_batch(self._batch_args(path), self.project)
        self.assertEqual(rc, 0)
        # В journal — ничего (всё пропущено как уже активное)
        self.assertEqual(self._override_records(), [])


class TestOverrideJudgeCLIArgs(unittest.TestCase):
    """CLI argparse: новые флаги --evidence, --approver, --batch работают."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.skill = "feature-pipeline"
        self.feature = "KIDPPRB-9254"

    def tearDown(self):
        self._tmp.cleanup()

    def test_evidence_and_approver_in_record(self):
        """--evidence и --approver пишутся в запись override."""
        import subprocess
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "override_judge.py"),
             "--project", str(self.project),
             "--skill", self.skill,
             "--feature", self.feature,
             "--judge", "quality",
             "--step-id", "01-quality",
             "--reason", "QA OK",
             "--evidence", "qa-report.html",
             "--approver", "qa-lead"],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 0, f"stderr: {rc.stderr}")
        rec = ov_mod.FE.override(self.project, self.skill, self.feature, "quality")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["evidence"], "qa-report.html")
        self.assertEqual(rec["approved_by"], "qa-lead")

    def test_batch_cli_yaml(self):
        """CLI: --batch <yaml-файл> создаёт overrides из YAML."""
        import subprocess
        path = self.project / "batch.yaml"
        path.write_text(f"""
overrides:
  - task: {self.feature}
    gate: quality
    reason: "QA OK"
    evidence: "qa.html"
    approver: "qa-lead"
""", encoding="utf-8")
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "override_judge.py"),
             "--project", str(self.project),
             "--skill", self.skill,
             "--batch", str(path)],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 0, f"stderr: {rc.stderr}\nstdout: {rc.stdout}")
        self.assertIsNotNone(ov_mod.FE.override(
            self.project, self.skill, self.feature, "quality"))

    def test_batch_cli_missing_file(self):
        """CLI: --batch <несуществующий> → rc=1."""
        import subprocess
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "override_judge.py"),
             "--project", str(self.project),
             "--skill", self.skill,
             "--batch", str(self.project / "nope.yaml")],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
