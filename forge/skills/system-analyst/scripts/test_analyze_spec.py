#!/usr/bin/env python3
"""test_analyze_spec.py — ресерч формата мастер-спеки: что детект обязан узнавать.

Три формы из жизни (форже-родная, OpenSpec-подобная, нумерованный СРС) плюс две ямы, в которые
детект проваливался: нумерованные РАЗДЕЛЫ принимались за требования, а плоский легаси-мастер
давал уверенную чушь и молча подменял грамматику.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "feature-pipeline" / "scripts"))
import analyze_spec as A  # noqa: E402
import spec_grammar as SG  # noqa: E402

FORGE = """# Master Spec: core

## 1. Назначение и результат
Возвраты платежей.

## 5. Требования и сценарии (Requirements)

### REQ-0001: Возврат платежа
Система возвращает платёж.  [from: pay 2026-01-01]
- **Given** платёж авторизован **When** запрошен возврат **Then** возвращён

### REQ-0002: Отказ по сроку
Позже суток возврат не делается.
- **Given** прошло 48 ч **When** запрошен возврат **Then** отказ 409

## 9. Журнал изменений (Audit trail)
- 2026-01-01 — pay: добавлено REQ-0001
"""

OPENSPEC = """# Payments

## Purpose
Refunds.

## Requirements

## Requirement: The system SHALL refund within 24h
Refunds an authorized payment.

#### Scenario: in time
- **WHEN** within 24h
- **THEN** refunded

## Requirement: The system SHALL reject late refunds
Requests after 24h are rejected.

#### Scenario: too late
- **WHEN** after 24h
- **THEN** rejected with 409

## Changelog
- 2026-01-01 initial
"""

SRS = """# СРС платёжного контура

## 3. Функциональные требования

### 3.1 Возврат платежа
Система возвращает авторизованный платёж.
Given платёж авторизован, When оператор запросил возврат, Then платёж возвращён.

### 3.2 Логирование возвратов
Каждый возврат пишется в журнал.
Given возврат выполнен, When операция завершена, Then запись есть.

## 4. История изменений
- 2026-01-01 — первая редакция
"""

LEGACY_FLAT = """# Master Spec: core

## 1. Назначение и результат
Возвраты платежей.

## 5. Требования (Requirements)
- Система возвращает платёж  [from: pay 2026-01-01]
- Система отклоняет поздний возврат  [from: pay 2026-01-01]

## 6. Сценарии (Given-When-Then)
- Given платёж авторизован When запрошен возврат Then возвращён  [from: pay 2026-01-01]

## 9. Журнал изменений
- 2026-01-01 — pay
"""


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "ground").mkdir(parents=True)
        (self.root / "ground" / "policy.json").write_text(
            json.dumps({"docs": {"master": {"enabled": True, "capability": "core"}}}),
            encoding="utf-8")
        self.spec = self.root / "docs" / "specs" / "core" / "spec.md"
        self.spec.parent.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _analyze(self, text: str) -> dict:
        self.spec.write_text(text, encoding="utf-8")
        return A.analyze(self.root)

    def test_forge_native_is_recognized_as_native(self):
        r = self._analyze(FORGE)
        self.assertTrue(r["matches_native"], r)
        self.assertGreaterEqual(r["confidence"], A.CONFIDENCE_FLOOR)
        self.assertEqual(r["suggested_config"], [])
        self.assertEqual(r["grammar"]["requirement"]["kind"], "id-colon")

    def test_numbered_sections_are_not_mistaken_for_requirements(self):
        """`## 5. Требования и сценарии` — раздел, а требование внутри него."""
        r = self._analyze(FORGE)
        self.assertEqual(r["grammar"]["requirement"]["level"], 3)

    def test_openspec_shape(self):
        r = self._analyze(OPENSPEC)
        g = r["grammar"]
        self.assertFalse(r["matches_native"])
        self.assertEqual(g["requirement"]["kind"], "title-only")
        self.assertEqual(g["requirement"]["level"], 2)
        self.assertEqual(g["requirement"]["lead"], "Requirement")
        self.assertEqual(g["scenario"]["style"], "gwt-block")
        self.assertEqual(g["scenario"]["level"], 4)
        self.assertEqual(g["provenance"], "none")
        self.assertEqual(g["audit_section"], ["changelog"])
        self.assertGreaterEqual(r["confidence"], A.CONFIDENCE_FLOOR)

    def test_openspec_profile_round_trips_through_grammar(self):
        """Детект обязан отдавать профиль, которым этот же мастер и разбирается."""
        r = self._analyze(OPENSPEC)
        reqs = SG.Grammar(r["grammar"]).parse(OPENSPEC)
        self.assertEqual(len(reqs), 2)
        self.assertEqual([len(x["scenarios"]) for x in reqs], [1, 1])

    def test_numbered_srs_shape(self):
        r = self._analyze(SRS)
        g = r["grammar"]
        self.assertEqual(g["requirement"]["kind"], "numbered")
        self.assertEqual(g["requirement"]["level"], 3)
        self.assertEqual(g["scenario"]["style"], "gwt-inline")
        self.assertEqual(len(SG.Grammar(g).parse(SRS)), 2)

    def test_legacy_flat_master_does_not_produce_confident_guess(self):
        """Плоский легаси-мастер: требований с ID нет — догадка не должна подменять грамматику."""
        r = self._analyze(LEGACY_FLAT)
        A.write(r, A.out_path(self.root))
        self.assertTrue(SG.load_profile(self.root, cfg={}).is_native(), r["grammar"])

    def test_no_master_is_not_an_error(self):
        r = A.analyze(self.root)
        self.assertIn("no_master", r["warnings"])
        self.assertTrue(r["matches_native"])

    def test_suggested_config_keys_exist_in_registry(self):
        """Предложенные команды обязаны быть исполнимы: config.py пишет только по реестру."""
        reg = json.loads((SCRIPT_DIR.parents[1] / "config-helper" / "references"
                          / "params-registry.json").read_text(encoding="utf-8"))
        known = {p["id"] for p in reg["params"]}
        for cmd in self._analyze(OPENSPEC)["suggested_config"]:
            self.assertIn(cmd.split()[2], known, cmd)   # config.py set <id> <value>


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "ground").mkdir(parents=True)
        (self.root / "ground" / "policy.json").write_text(
            json.dumps({"docs": {"master": {"enabled": True, "capability": "core"}}}),
            encoding="utf-8")
        self.spec = self.root / "docs" / "specs" / "core" / "spec.md"
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(FORGE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_written_where_inventory_lives_and_self_ignored(self):
        A.ensure(self.root)
        out = A.out_path(self.root)
        self.assertEqual(out.name, "spec-conventions.json")
        self.assertIn("inventory", out.parts)
        self.assertTrue((out.parent / ".gitignore").exists())

    def test_second_run_reuses_cache_while_master_unchanged(self):
        first = A.ensure(self.root)
        second = A.ensure(self.root)
        self.assertEqual(first["generated_at"], second["generated_at"])

    def test_changed_master_invalidates_cache(self):
        A.ensure(self.root)
        self.spec.write_text(OPENSPEC, encoding="utf-8")
        self.assertFalse(A.is_fresh(A.load_cache(A.out_path(self.root)), self.root))
        self.assertEqual(A.ensure(self.root)["grammar"]["requirement"]["kind"], "title-only")

    def test_research_survives_rescan_and_lifts_low_confidence(self):
        A.ensure(self.root)
        path = A.out_path(self.root)
        data = A.load_cache(path)
        data["research"] = {"grammar": {"requirement": {"kind": "bullet-id"}}, "confidence": 0.9}
        data = A._apply_research(data, data["research"])
        A.write(data, path)
        self.spec.write_text(FORGE + "\n<!-- правка -->\n", encoding="utf-8")
        again = A.ensure(self.root)
        self.assertEqual(again["grammar"]["requirement"]["kind"], "bullet-id")
        self.assertIn("research", again)


if __name__ == "__main__":
    unittest.main(verbosity=2)
