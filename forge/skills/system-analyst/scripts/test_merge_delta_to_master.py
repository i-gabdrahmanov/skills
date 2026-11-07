#!/usr/bin/env python3
"""test_merge_delta_to_master.py — тесты движка слияния дельты в требования-мастер."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_master_spec as gate         # noqa: E402
import merge_delta_to_master as engine   # noqa: E402

TEMPLATE = SCRIPT_DIR.parent / "references" / "master-spec-template.md"

SDD = """# SDD: Экспорт отчёта по заявкам

**Spec ID:** r1

## 1. Назначение и результат (Purpose & Outcomes)
Оператор получает отчёт по заявкам за период, чтобы закрывать месячную сверку.

## 2. Границы охвата (Scope boundaries)
- В рамках: выгрузка CSV.

## 3. Функциональные требования (Given-When-Then)
- **Given** есть заявки за период **When** оператор запросил отчёт **Then** отчёт сформирован
- **Given** период пуст **When** оператор запросил отчёт **Then** запрос отклонён 400

## 7. Критерии приёмки (Acceptance & verification)
- **Given** отчёт сформирован **When** оператор скачал файл **Then** есть запись аудита
"""

SDD_STRUCTURED = """# SDD: Управление лимитами

## 1. Назначение и результат (Purpose & Outcomes)
Оператор управляет лимитами клиента.

## 3. Функциональные требования (Given-When-Then)
### Установка лимита
Оператор устанавливает суточный лимит клиенту.
- **Given** клиент активен **When** оператор задал лимит **Then** лимит сохранён

### Снятие лимита
Оператор снимает ранее заданный лимит.
- **Given** лимит задан **When** оператор снял лимит **Then** лимит отсутствует
"""


class EngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sdd = self.tmp / "sdd.md"
        self.sdd.write_text(SDD, encoding="utf-8")
        self.spec = self.tmp / "specs" / "claims" / "spec.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _merge(self, **kw):
        return engine.merge(self.sdd, self.spec, TEMPLATE, kw.pop("feature", "report-export"),
                            "claims", **kw)

    # ── разбор дельты ──────────────────────────────────────────────────
    def test_flat_delta_gives_one_requirement_with_all_scenarios(self):
        cands = engine.parse_delta(SDD)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["title"], "Экспорт отчёта по заявкам")
        self.assertEqual(len(cands[0]["scenarios"]), 3)   # §3 (2) + §7 (1)
        self.assertIn("месячную сверку", cands[0]["statement"])

    def test_structured_delta_gives_requirement_per_subsection(self):
        cands = engine.parse_delta(SDD_STRUCTURED)
        self.assertEqual([c["title"] for c in cands], ["Установка лимита", "Снятие лимита"])
        self.assertEqual([len(c["scenarios"]) for c in cands], [1, 1])
        self.assertIn("суточный лимит", cands[0]["statement"])

    # ── операции ───────────────────────────────────────────────────────
    def test_add_creates_master_from_template_and_passes_gate(self):
        r = self._merge()
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["created"])
        self.assertEqual(r["added"], ["REQ-0001"])
        self.assertTrue(r["audit_added"])
        self.assertEqual(gate.check(self.spec, "applicability")["status"], "pass")

    def test_dry_run_writes_nothing(self):
        r = self._merge(dry_run=True)
        self.assertEqual(r["kinds"], ["add"])
        self.assertFalse(self.spec.exists())

    def test_second_merge_is_idempotent(self):
        self._merge()
        before = self.spec.read_text(encoding="utf-8")
        r = self._merge()
        self.assertEqual(r["kinds"], ["same"])
        self.assertEqual(r["added"], [])
        self.assertEqual(self.spec.read_text(encoding="utf-8"), before)

    def test_edited_delta_is_modify_and_blocked_by_default(self):
        self._merge()
        self.sdd.write_text(SDD.replace("отчёт сформирован\n", "отчёт сформирован и подписан\n"),
                            encoding="utf-8")
        r = self._merge()
        self.assertEqual(r["status"], "blocked")
        self.assertEqual(r["blocked"], ["REQ-0001"])
        self.assertEqual(r["modified"], [])
        self.assertIn("отчёт сформирован\n", self.spec.read_text(encoding="utf-8") + "\n")

    def test_allow_modify_applies_and_keeps_id(self):
        self._merge()
        self.sdd.write_text(SDD.replace("запрос отклонён 400", "запрос отклонён 422"),
                            encoding="utf-8")
        r = self._merge(allow_modify=True)
        self.assertEqual(r["modified"], ["REQ-0001"])
        text = self.spec.read_text(encoding="utf-8")
        self.assertIn("422", text)
        self.assertNotIn("отклонён 400", text)
        self.assertEqual(len(engine.parse_master(text)), 1)   # не дубль, а правка

    def test_modify_by_explicit_id(self):
        self._merge()
        self.sdd.write_text(SDD.replace("запрос отклонён 400", "запрос отклонён 422"),
                            encoding="utf-8")
        self.assertEqual(self._merge(modify_ids={"REQ-0002"})["status"], "blocked")
        self.assertEqual(self._merge(modify_ids={"REQ-0001"})["modified"], ["REQ-0001"])

    def test_second_feature_continues_numbering(self):
        self._merge()
        other = self.tmp / "sdd2.md"
        other.write_text(SDD_STRUCTURED, encoding="utf-8")
        r = engine.merge(other, self.spec, TEMPLATE, "limits", "claims")
        self.assertEqual(r["added"], ["REQ-0002", "REQ-0003"])
        self.assertEqual(len(engine.parse_master(self.spec.read_text(encoding="utf-8"))), 3)

    def test_renaming_requirement_in_master_keeps_id_and_is_not_duplicated(self):
        """Переименование в мастере — новое требование по названию, но ID прежних не трогает."""
        self._merge()
        text = self.spec.read_text(encoding="utf-8")
        text = text.replace("### REQ-0001: Экспорт отчёта по заявкам",
                            "### REQ-0001: Выгрузка реестра заявок")
        self.spec.write_text(text, encoding="utf-8")
        reqs = engine.parse_master(self.spec.read_text(encoding="utf-8"))
        self.assertEqual(reqs[0]["id"], "REQ-0001")
        self.assertEqual(reqs[0]["title"], "Выгрузка реестра заявок")

    def test_missing_delta_is_error(self):
        r = engine.merge(self.tmp / "нет.md", self.spec, TEMPLATE, "f", "claims")
        self.assertEqual(r["status"], "error")

    # ── remove ─────────────────────────────────────────────────────────
    def test_remove_drops_block_and_logs_reason(self):
        self._merge()
        res = engine.remove_requirement(self.spec.read_text(encoding="utf-8"), "REQ-0001",
                                        reason="передано в другой КЭ", today="2026-08-10")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(engine.parse_master(res["text"]), [])
        self.assertIn("передано в другой КЭ", res["text"])

    def test_remove_unknown_id_is_error(self):
        self._merge()
        res = engine.remove_requirement(self.spec.read_text(encoding="utf-8"), "REQ-9999",
                                        reason="x", today="2026-08-10")
        self.assertEqual(res["status"], "error")

    # ── разбор мастера ─────────────────────────────────────────────────
    def test_parse_master_splits_statement_and_scenarios(self):
        self._merge()
        r = engine.parse_master(self.spec.read_text(encoding="utf-8"))[0]
        self.assertEqual(r["id"], "REQ-0001")
        self.assertEqual(len(r["scenarios"]), 3)
        self.assertNotIn("[from:", r["statement"])
        self.assertEqual(r["tags"], ["[from: report-export " + engine.date.today().isoformat() + "]"])


class ColumnGwtAndSymlinkTest(unittest.TestCase):
    """Боевой прогон KIDPPRB-10815 (v0.6): нумерованный мастер без провенанса, симлинк из репо
    кода, сценарии в дельте столбиком."""

    MASTER = """# Спецификация

## 2. Требования к методам
#### 2.1.7 Поиск документа
Описание.
"""
    SDD = """# SDD: Поиск по всем статусам

## 3. Функциональные требования (Given-When-Then)
### Сценарий 1. Флаг не передан
- **Given** клиент вызывает поиск без флага
- **When** система фильтрует по статусам
- **Then** возвращаются документы из статусов 2, 10, 12
"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.real = self.tmp / "spec-repo" / "tps.md"
        self.real.parent.mkdir()
        self.real.write_text(self.MASTER, encoding="utf-8")
        self.link = self.tmp / "code" / "docs" / "specs" / "tps" / "spec.md"
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to(self.real)
        self.sdd = self.tmp / "sdd.md"
        self.sdd.write_text(self.SDD, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def g(self, style):
        return engine.SG.Grammar({"requirement": {"level": 4, "kind": "numbered"},
                                  "requirements_section": ["требования к методам"],
                                  "audit_section": [], "scenario": {"style": style},
                                  "provenance": "none"})

    def _merge(self, g, dry_run=False):
        return engine.merge(self.sdd, self.link, TEMPLATE, "K-1", "tps", grammar=g,
                            dry_run=dry_run)

    def test_column_steps_are_one_scenario_not_statement(self):
        [c] = engine.parse_delta(self.SDD)
        self.assertEqual(len(c["scenarios"]), 1)
        self.assertIn("**When** система фильтрует", c["scenarios"][0])
        self.assertNotIn("Given", c["statement"])

    def test_write_goes_through_symlink(self):
        self._merge(self.g("gwt-inline"))
        self.assertTrue(self.link.is_symlink(), "запись подменила симлинк обычным файлом")
        self.assertIn("#### 2.1.8 Сценарий 1. Флаг не передан",
                      self.real.read_text(encoding="utf-8"))

    def test_replan_after_write_is_same_for_every_style(self):
        for style in engine.SG.SCENARIO_STYLES:
            with self.subTest(style=style):
                self.real.write_text(self.MASTER, encoding="utf-8")
                g = self.g(style)
                self.assertEqual(self._merge(g)["kinds"], ["add"])
                self.assertEqual(self._merge(g, dry_run=True)["kinds"], ["same"])

    def test_gwt_in_statement_still_replans_same(self):
        """Сравнение идёт глазами разбора мастера: утверждение, которое мастер прочтёт
        сценарием (так раньше склеивался столбик), — не повод для `~` по своей записи."""
        g = self.g("gwt-inline")
        cand = {"title": "Флаг", "statement": "- **Given** a - **When** b - **Then** c",
                "scenarios": []}
        res = engine.apply_ops(self.MASTER, engine.plan_ops([], [cand], g), prefix="REQ",
                               feature="K-1", today="2026-11-07", grammar=g)
        ops = engine.plan_ops(engine.parse_master(res["text"], grammar=g), [cand], g)
        self.assertEqual([o["op"] for o in ops], ["same"])


class Battle046Test(unittest.TestCase):
    """Боевой прогон v0.4.6: SA-1 (гонка без замка), SA-2 (пустая дельта), SA-3 (дубли)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.spec = self.tmp / "specs" / "claims" / "spec.md"
        engine.merge(self._sdd("base", SDD_STRUCTURED), self.spec, TEMPLATE, "base", "claims")
        self.base = self.spec.read_text(encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _sdd(self, name: str, text: str) -> Path:
        p = self.tmp / f"{name}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_empty_delta_writes_nothing(self):
        res = engine.merge(self._sdd("empty", ""), self.spec, TEMPLATE, "empty", "claims")
        self.assertEqual((res["status"], res.get("kind")), ("error", "empty-delta"), res)
        self.assertEqual(self.spec.read_text(encoding="utf-8"), self.base,
                         "пустая дельта записала в мастер мусорный блок")

    def test_duplicate_titles_refused_before_write(self):
        dup = SDD_STRUCTURED.replace("### Снятие лимита", "### Установка лимита")
        res = engine.merge(self._sdd("dup", dup), self.spec, TEMPLATE, "dup", "claims")
        self.assertEqual((res["status"], res.get("kind")), ("error", "duplicate-titles"), res)
        self.assertIn("Установка лимита", res["error"])
        self.assertEqual(self.spec.read_text(encoding="utf-8"), self.base)

    def test_parallel_merges_lose_nothing(self):
        import subprocess
        n = 60
        for name in ("fa", "fb"):
            body = "\n\n".join(f"### {name} требование {i}\nОписание {name} {i}.\n"
                                 f"- **Given** a **When** b{i} **Then** c" for i in range(n))
            self._sdd(name, f"# SDD: {name}\n\n## 1. Назначение и результат\nx\n\n"
                             f"## 3. Функциональные требования (Given-When-Then)\n{body}\n")
        procs = [subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / "merge_delta_to_master.py"), "--sdd",
             str(self.tmp / f"{name}.md"), "--feature", name, "--spec", str(self.spec),
             "--capability", "claims", "--template", str(TEMPLATE)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for name in ("fa", "fb")]
        self.assertEqual([p.wait(timeout=120) for p in procs], [0, 0])
        text = self.spec.read_text(encoding="utf-8")
        for name in ("fa", "fb"):
            self.assertEqual(sum(f"{name} требование {i}" in text for i in range(n)), n,
                             f"требования {name} потеряны параллельным merge")
        import re as _re
        ids = _re.findall(r"^### (REQ-\d+):", text, _re.M)
        self.assertEqual(len(ids), len(set(ids)), "один ID у разных требований")


if __name__ == "__main__":
    unittest.main(verbosity=2)
