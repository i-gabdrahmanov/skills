#!/usr/bin/env python3
"""test_spec_cli.py — тесты пользовательского входа в требования-мастер (/forge-spec)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import merge_delta_to_master as engine   # noqa: E402
import spec_cli                          # noqa: E402

PIPELINE = {"project": {"name": "claims"},
            "docs": {"mode": "in-repo", "docs_path": "docs", "master": {"enabled": True}},
            "spec": {"id_prefix": "REQ", "scenario_floor": True}}

SDD_A = """# SDD: Экспорт отчёта по заявкам

## 1. Назначение и результат (Purpose & Outcomes)
Оператор получает отчёт по заявкам за период.

## 3. Функциональные требования (Given-When-Then)
- **Given** есть заявки **When** оператор запросил отчёт **Then** отчёт сформирован
"""

SDD_B = """# SDD: Журнал действий оператора

## 1. Назначение и результат (Purpose & Outcomes)
Действия оператора попадают в неизменяемый журнал.

## 3. Функциональные требования (Given-When-Then)
- **Given** действие привилегированное **When** транзакция закрыта **Then** запись создана
"""

LEGACY_MASTER = """# Master Spec: claims

## 1. Назначение и результат (Purpose & Outcomes)
Капабилити закрывает потребность оператора.

## 2. Архитектурный контекст (Architectural context)
Внутренний сектор сети, бэковый компонент.

## 3. Границы охвата (Scope boundaries)
В рамках A; не в рамках B.

## 4. Ограничения и допущения (Constraints & assumptions)
Стек Java 21 + Spring Boot 3.3.

## 5. Требования (Requirements)
- Экспорт отчёта по заявкам  [from: report-export 2026-01-01]
- Журнал действий оператора  [from: audit-log 2026-02-01]

## 6. Сценарии (Given-When-Then)
- **Given** есть заявки **When** оператор запросил отчёт **Then** отчёт сформирован  [from: report-export 2026-01-01]
- **Given** действие привилегированное **When** транзакция закрыта **Then** запись создана  [from: audit-log 2026-02-01]

## 7. Критерии приёмки и верификация (Acceptance & verification)
Покрыто интеграционными тестами.

## 8. Модель угроз и безопасность (Security & threat model)
Не применимо: внутренний сервис.

## 9. Регуляторные требования (Regulatory & compliance)
Не применимо.

## 10. Журнал изменений (Audit trail)
- 2026-01-01 — report-export: слито
"""


def run(*argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = spec_cli.main(list(argv))
    return rc, buf.getvalue()


class SpecCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ground").mkdir()
        (self.root / "ground" / "pipeline.json").write_text(
            json.dumps(PIPELINE, ensure_ascii=False), encoding="utf-8")
        for slug, body in (("report-export", SDD_A), ("audit-log", SDD_B)):
            d = self.root / "docs" / "feature-pipeline" / slug
            d.mkdir(parents=True)
            (d / "sdd.md").write_text(body, encoding="utf-8")
        self.spec = self.root / "docs" / "specs" / "claims" / "spec.md"

    def tearDown(self):
        spec_cli._GRAMMAR_CACHE.clear()   # профиль кэшируется по корню
        self._tmp.cleanup()

    def _r(self, *argv):
        spec_cli._GRAMMAR_CACHE.pop(str(self.root), None)
        return run("--project-root", str(self.root), *argv)

    def _set_grammar(self, **grammar):
        """Подтверждённый профиль формы мастера в policy (как это делает config.py set)."""
        cfg = json.loads((self.root / "ground" / "pipeline.json").read_text(encoding="utf-8"))
        cfg.setdefault("spec", {})["grammar"] = grammar
        (self.root / "ground" / "pipeline.json").write_text(json.dumps(cfg, ensure_ascii=False),
                                                            encoding="utf-8")
        spec_cli._GRAMMAR_CACHE.clear()

    # ── status ─────────────────────────────────────────────────────────
    def test_status_reports_pending_before_merge(self):
        rc, out = self._r("status", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["exists"])
        self.assertEqual(sorted(data["new"]), ["audit-log", "report-export"])
        self.assertEqual(data["drifted"], [])

    def test_status_detects_drift_after_delta_edit(self):
        self._r("merge", "--all", "-y")
        sdd = self.root / "docs" / "feature-pipeline" / "report-export" / "sdd.md"
        sdd.write_text(SDD_A.replace("отчёт сформирован", "отчёт сформирован и подписан"),
                       encoding="utf-8")
        rc, out = self._r("status", "--json")
        data = json.loads(out)
        # провенанс в мастере есть, но дельта разошлась — это НЕ «слито»
        self.assertEqual(data["drifted"], ["report-export"])
        self.assertEqual(data["merged"], ["audit-log"])

    def test_global_flag_accepted_after_subcommand(self):
        rc, out = run("status", "--project-root", str(self.root), "--json")
        self.assertEqual(rc, 0)
        self.assertIn("capability", json.loads(out))

    # ── diff / merge ───────────────────────────────────────────────────
    def test_diff_writes_nothing(self):
        rc, out = self._r("diff", "--all")
        self.assertEqual(rc, 0)
        self.assertIn("+ <new>", out)
        self.assertFalse(self.spec.exists())

    def test_merge_all_creates_master_and_numbers_ids(self):
        rc, out = self._r("merge", "--all", "-y")
        self.assertEqual(rc, 0)
        reqs = engine.parse_master(self.spec.read_text(encoding="utf-8"))
        self.assertEqual([r["id"] for r in reqs], ["REQ-0001", "REQ-0002"])

    def test_merge_all_skips_conflict_but_applies_the_rest(self):
        """Одна конфликтная дельта не должна блокировать слияние остальных."""
        first = self.root / "docs" / "feature-pipeline" / "report-export"
        rc, _ = self._r("merge", "report-export", "-y")
        self.assertEqual(rc, 0)
        (first / "sdd.md").write_text(SDD_A.replace("отчёт сформирован", "отчёт подписан"),
                                      encoding="utf-8")
        rc, out = self._r("merge", "--all", "-y")
        self.assertEqual(rc, 3)                       # нужно решение пользователя
        self.assertIn("пропущено: report-export", out)
        reqs = engine.parse_master(self.spec.read_text(encoding="utf-8"))
        self.assertIn("Журнал действий оператора", [r["title"] for r in reqs])  # вторая прошла
        self.assertIn("отчёт сформирован", self.spec.read_text(encoding="utf-8"))  # первая цела

    def test_merge_skips_already_actual_delta(self):
        self._r("merge", "--all", "-y")
        rc, out = self._r("merge", "--all", "-y")
        self.assertEqual(rc, 0)
        self.assertIn("мастер актуален", out)

    def test_unknown_slug_is_error(self):
        rc, _ = self._r("merge", "нет-такой", "-y")
        self.assertEqual(rc, 2)

    # ── check / remove ─────────────────────────────────────────────────
    def test_check_passes_after_merge(self):
        self._r("merge", "--all", "-y")
        rc, out = self._r("check")
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out)

    def test_remove_requires_reason_and_logs_it(self):
        self._r("merge", "--all", "-y")
        rc, _ = self._r("remove", "REQ-0001", "--reason", "передано в другой КЭ", "-y")
        self.assertEqual(rc, 0)
        text = self.spec.read_text(encoding="utf-8")
        self.assertNotIn("### REQ-0001:", text)
        self.assertIn("передано в другой КЭ", text)

    # ── migrate ────────────────────────────────────────────────────────
    def test_migrate_flat_master_to_ids(self):
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(LEGACY_MASTER, encoding="utf-8")
        rc, out = self._r("migrate")
        self.assertEqual(rc, 0)
        reqs = engine.parse_master(self.spec.read_text(encoding="utf-8"))
        self.assertEqual([r["id"] for r in reqs], ["REQ-0001", "REQ-0002"])
        # сценарии разошлись по требованиям по провенансу, а не свалились в кучу
        self.assertEqual([len(r["scenarios"]) for r in reqs], [1, 1])
        self.assertIn("отчёт сформирован", reqs[0]["scenarios"][0])

    def test_migrate_is_noop_on_already_migrated(self):
        self._r("merge", "--all", "-y")
        rc, out = self._r("migrate")
        self.assertEqual(rc, 0)
        self.assertIn("миграция не нужна", out)


# Дельта ФИКСА лежит внутри папки своей стори (<стори>/fixes/<баг>/sdd.md): фикс не заводит
# отдельную «фичу». Пины ниже держат три свойства этой раскладки: дельта видна CLI, слаг бага
# сам по себе достаточен для merge/diff, а в мастер правка уходит с провенансом СТОРИ —
# иначе find_spec_anchor следующего бага потеряет связь «это требование стори STOR-100».
    # ── чужой формат мастера ───────────────────────────────────────────
    def test_unsupported_grammar_blocks_merge_without_writing(self):
        """Форма не описана профилем → exit 3 и НИ ОДНОЙ записи в мастер."""
        self._set_grammar(requirement_kind="table-like")
        rc, _ = self._r("merge", "--all", "-y")
        self.assertEqual(rc, 3)
        self.assertFalse(self.spec.exists(), "в мастер писать было нельзя")

    def test_unsupported_grammar_blocks_diff(self):
        self._set_grammar(scenario_style="table")
        self.assertEqual(self._r("diff", "report-export")[0], 3)

    def test_unsupported_grammar_gives_unknown_format_state(self):
        self._set_grammar(requirement_kind="table-like")
        self.assertEqual(spec_cli.delta_state(self.root, "report-export"), "unknown-format")

    def test_project_grammar_merges_in_project_shape(self):
        """Профиль проекта — merge пишет ЕГО формой, а не форже-блоками."""
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(
            "# Claims\n\n## Requirements\n\n"
            "## Requirement: Журнал действий оператора\nДействия попадают в журнал.\n\n"
            "#### Scenario: приват\n- **Given** действие привилегированное **When** транзакция "
            "закрыта **Then** запись создана\n\n## Changelog\n- 2026-01-01\n",
            encoding="utf-8")
        self._set_grammar(requirement_kind="title-only", requirement_level=2,
                          requirement_lead="Requirement", scenario_style="gwt-block",
                          requirements_section="requirements", audit_section="changelog",
                          provenance="none")
        rc, out = self._r("merge", "report-export", "-y", "--no-archive")
        self.assertEqual(rc, 0, out)
        text = self.spec.read_text(encoding="utf-8")
        self.assertIn("## Requirement: Экспорт отчёта по заявкам", text)
        self.assertNotIn("### REQ-", text)
        self.assertIn("#### Scenario: ", text)
        self.assertEqual(text.count("## Requirement: Журнал действий оператора"), 1)

    def test_project_grammar_merge_is_idempotent(self):
        self.test_project_grammar_merges_in_project_shape()
        rc, out = self._r("merge", "report-export", "-y", "--no-archive")
        self.assertEqual(rc, 0, out)
        self.assertIn("актуален", out)

    def test_research_writes_profile(self):
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(
            "# Claims\n\n## Requirements\n\n"
            "## Requirement: Раз\nТекст.\n\n#### Scenario: a\n- **WHEN** x **THEN** y\n\n"
            "## Requirement: Два\nТекст.\n\n#### Scenario: b\n- **WHEN** x **THEN** y\n",
            encoding="utf-8")
        rc, _ = self._r("research")
        self.assertIn(rc, (0, 3))
        prof = json.loads((self.root / "ground" / "inventory" / "spec-conventions.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(prof["grammar"]["requirement"]["kind"], "title-only")
        self.assertFalse(prof["matches_native"])

    def test_migrate_refuses_foreign_format(self):
        """migrate — про легаси форже-формат; чужой мастер он не переписывает."""
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(LEGACY_MASTER, encoding="utf-8")
        self._set_grammar(requirement_kind="title-only", requirement_level=2,
                          requirement_lead="Requirement")
        rc, _ = self._r("migrate")
        self.assertEqual(rc, 3)
        self.assertIn("## 5. Требования (Requirements)",
                      self.spec.read_text(encoding="utf-8"), "мастер не тронут")

FIX_DELTA = """# SDD: Экспорт отчёта по заявкам

## 1. Назначение и результат (Purpose & Outcomes)
Оператор получает отчёт по заявкам за период.

## 3. Функциональные требования (Given-When-Then)
- **Given** есть заявки **When** оператор запросил отчёт **Then** отчёт сформирован
- **Given** заявок за период нет **When** оператор запросил отчёт **Then** отчёт пуст, ошибки нет
"""

class FixDeltaInsideStoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ground").mkdir()
        (self.root / "ground" / "pipeline.json").write_text(
            json.dumps(PIPELINE, ensure_ascii=False), encoding="utf-8")
        story = self.root / "docs" / "feature-pipeline" / "report-export"
        story.mkdir(parents=True)
        (story / "sdd.md").write_text(SDD_A, encoding="utf-8")
        d = story / "fixes" / "BUG-512"
        d.mkdir(parents=True)
        (d / "sdd.md").write_text(FIX_DELTA, encoding="utf-8")
        self.spec = self.root / "docs" / "specs" / "claims" / "spec.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _r(self, *argv):
        spec_cli._GRAMMAR_CACHE.pop(str(self.root), None)
        return run("--project-root", str(self.root), *argv)

    def test_nested_fix_delta_is_discovered(self):
        rc, out = self._r("status", "--json")
        self.assertEqual(rc, 0)
        self.assertIn("report-export/fixes/BUG-512", json.loads(out)["new"])

    def test_merge_by_short_bug_key(self):
        rc, out = self._r("diff", "BUG-512")
        self.assertEqual(rc, 0)
        self.assertIn("report-export/fixes/BUG-512", out)

    def test_master_provenance_keeps_story_first(self):
        self._r("merge", "report-export", "-y")
        rc, _ = self._r("merge", "BUG-512", "--allow-modify", "-y")
        self.assertEqual(rc, 0)
        text = self.spec.read_text(encoding="utf-8")
        # первый токен провенанса — стори (его и парсит find_spec_anchor), баг рядом
        self.assertIn("[from: report-export fix/BUG-512", text)
        self.assertIn("отчёт пуст, ошибки нет", text)

    def test_ambiguous_short_key_is_refused(self):
        d = self.root / "docs" / "feature-pipeline" / "audit-log" / "fixes" / "BUG-512"
        d.mkdir(parents=True)
        (d / "sdd.md").write_text(FIX_DELTA, encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = spec_cli.main(["--project-root", str(self.root), "diff", "BUG-512"])
        self.assertEqual(rc, 2)


# ── режим мастера + архивация доков на merge ───────────────────────────────

FULL_STEPS = ["02-sdd", "02-design", "05-tests", "06-spec"]


def _manifest(skill: str, ids, **status):
    return {"version": 2, "skill": skill, "pipeline_id": "2026-09-22-100000",
            "started_at": "2026-09-22T10:00:00Z",
            "steps": [{"id": i, "title": i, "status": status.get(i, "completed"),
                       "depends_on": []} for i in ids]}


class MergeArchiveBase(unittest.TestCase):
    """Проект с ЗАВЕРШЁННЫМ прогоном: только у такого merge вправе убрать доки."""

    MASTER_SOURCE = "delta-first"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        cfg = json.loads(json.dumps(PIPELINE))
        cfg["spec"]["master_source"] = self.MASTER_SOURCE
        (self.root / "ground").mkdir()
        (self.root / "ground" / "pipeline.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        d = self.root / "docs" / "feature-pipeline" / "report-export"
        d.mkdir(parents=True)
        (d / "sdd.md").write_text(SDD_A, encoding="utf-8")
        st = self.root / "ground" / "statements" / "feature-pipeline" / "report-export"
        st.mkdir(parents=True)
        (st / "manifest.json").write_text(
            json.dumps(_manifest("feature-pipeline", FULL_STEPS), ensure_ascii=False),
            encoding="utf-8")
        self.docs = d
        self.archived = self.root / "docs" / "archive" / "report-export"
        self.spec = self.root / "docs" / "specs" / "claims" / "spec.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _r(self, *argv):
        spec_cli._GRAMMAR_CACHE.pop(str(self.root), None)
        return run("--project-root", str(self.root), *argv)


class ArchiveOnMergeTest(MergeArchiveBase):
    """delta-first: слили — и доки уехали с рабочего стола."""

    def test_merge_archives_docs(self):
        rc, out = self._r("merge", "report-export", "-y")
        self.assertEqual(rc, 0)
        self.assertTrue(self.spec.exists())
        self.assertFalse(self.docs.exists())
        self.assertTrue((self.archived / "sdd.md").is_file())
        self.assertIn("доки report-export", out)

    def test_no_archive_keeps_docs(self):
        rc, _ = self._r("merge", "report-export", "-y", "--no-archive")
        self.assertEqual(rc, 0)
        self.assertTrue((self.docs / "sdd.md").is_file())
        self.assertFalse(self.archived.exists())

    def test_dry_run_touches_nothing(self):
        rc, out = self._r("merge", "report-export", "-y", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertFalse(self.spec.exists())
        self.assertTrue((self.docs / "sdd.md").is_file())
        self.assertIn("dry-run", out)

    def test_unfinished_run_keeps_docs_but_merge_succeeds(self):
        """Архивация — best-effort: её отказ не делает слияние неуспешным."""
        st = self.root / "ground/statements/feature-pipeline/report-export/manifest.json"
        st.write_text(json.dumps(_manifest("feature-pipeline", FULL_STEPS,
                                           **{"05-tests": "pending"}), ensure_ascii=False),
                      encoding="utf-8")
        rc, out = self._r("merge", "report-export", "-y")
        self.assertEqual(rc, 0)
        self.assertTrue(self.spec.exists())
        self.assertTrue((self.docs / "sdd.md").is_file())
        self.assertIn("не заархивированы", out)
        self.assertIn("/forge-archive put report-export", out)


class MasterFirstTest(MergeArchiveBase):
    """master-first: sdd выделяется ИЗ мастера, поэтому merge сверяет и в мастер не пишет."""

    MASTER_SOURCE = "master-first"

    def test_master_source_is_read_from_config(self):
        self.assertEqual(spec_cli.master_source(self.root), "master-first")

    def test_divergence_exits_3_and_leaves_master_untouched(self):
        rc, out = self._r("merge", "report-export", "-y")
        self.assertEqual(rc, 3)
        self.assertFalse(self.spec.exists(), "master-first не имеет права писать мастер")
        self.assertTrue((self.docs / "sdd.md").is_file())
        self.assertIn("расходится с мастером", out)
        self.assertIn("--allow-merge", out)

    def test_allow_merge_is_the_explicit_escape(self):
        rc, _ = self._r("merge", "report-export", "-y", "--allow-merge", "--no-archive")
        self.assertEqual(rc, 0)
        self.assertTrue(self.spec.exists())

    def test_matching_delta_is_verified_and_archived(self):
        self._r("merge", "report-export", "-y", "--allow-merge", "--no-archive")
        rc, out = self._r("merge", "report-export", "-y")
        self.assertEqual(rc, 0)
        self.assertIn("сверка прошла", out)
        self.assertFalse(self.docs.exists())
        self.assertTrue((self.archived / "sdd.md").is_file())

    def test_status_calls_it_a_divergence(self):
        rc, out = self._r("status")
        self.assertEqual(rc, 0)
        self.assertIn("мастер первичен", out)
        self.assertIn("РАСХОЖДЕНИЕ", out)

    def test_status_json_carries_mode(self):
        rc, out = self._r("status", "--json")
        self.assertEqual(json.loads(out)["master_source"], "master-first")

    def test_unknown_value_falls_back_to_default(self):
        cfg = json.loads((self.root / "ground" / "pipeline.json").read_text(encoding="utf-8"))
        cfg["spec"]["master_source"] = "whatever"
        (self.root / "ground" / "pipeline.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(spec_cli.master_source(self.root), "delta-first")


class DeltaStateTest(MergeArchiveBase):
    """Публичный вход, которым архивация проверяет, видел ли мастер эту дельту."""

    def test_new_then_merged(self):
        self.assertEqual(spec_cli.delta_state(self.root, "report-export"), "new")
        self._r("merge", "report-export", "-y", "--no-archive")
        self.assertEqual(spec_cli.delta_state(self.root, "report-export"), "merged")

    def test_unknown_slug_is_no_delta(self):
        self.assertEqual(spec_cli.delta_state(self.root, "нет-такой"), "no-delta")

    def test_master_disabled_is_no_master(self):
        cfg = json.loads((self.root / "ground" / "pipeline.json").read_text(encoding="utf-8"))
        cfg["docs"]["master"]["enabled"] = False
        (self.root / "ground" / "pipeline.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(spec_cli.delta_state(self.root, "report-export"), "no-master")


if __name__ == "__main__":
    unittest.main(verbosity=2)
