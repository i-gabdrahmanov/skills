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
        self._tmp.cleanup()

    def _r(self, *argv):
        return run("--project-root", str(self.root), *argv)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
