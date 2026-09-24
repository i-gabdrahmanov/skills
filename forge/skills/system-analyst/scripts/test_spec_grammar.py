#!/usr/bin/env python3
"""test_spec_grammar.py — форма требований-мастера: профиль, слои, пригодность.

Главный инвариант — ПИН СОВМЕСТИМОСТИ: форже-родной профиль обязан разбирать и рендерить
мастер ровно так, как это делали литералы в merge_delta_to_master до выноса грамматики.
Разъедется — и проекты без профиля молча поменяют формат своего же мастера.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import merge_delta_to_master as engine  # noqa: E402
import spec_grammar as SG  # noqa: E402

NATIVE_MASTER = """# Master Spec: core

## 5. Требования и сценарии (Requirements)

### REQ-0001: Возврат платежа
Система возвращает авторизованный платёж.  [from: pay 2026-01-01]
- **Given** платёж авторизован **When** запрошен возврат **Then** платёж возвращён

### REQ-0002: Отказ по сроку
Позже суток возврат не делается.
- **Given** прошло 48 ч **When** запрошен возврат **Then** отказ 409

## 9. Журнал изменений (Audit trail)
- 2026-01-01 — pay: добавлено REQ-0001
"""

OPENSPEC_MASTER = """# Payments

## Purpose
Refunds.

## Requirements

## Requirement: The system SHALL refund within 24h
Refunds an authorized payment.

#### Scenario: in time
- **WHEN** within 24h
- **THEN** refunded

## Requirement: The system SHALL log refunds
Every refund is logged.

#### Scenario: logged
- **WHEN** refunded
- **THEN** audit record exists

## Changelog
- 2026-01-01 initial
"""

OPENSPEC_PROFILE = {
    "requirement": {"level": 2, "kind": "title-only", "lead": "Requirement"},
    "requirements_section": ["requirements"],
    "audit_section": ["changelog"],
    "scenario": {"style": "gwt-block", "level": 4},
    "provenance": "none",
}


class NativeParityTest(unittest.TestCase):
    """NATIVE обязан быть неотличим от прежних литералов движка."""

    def test_parse_matches_engine(self):
        old = engine.parse_master(NATIVE_MASTER, "REQ")
        new = SG.native().parse(NATIVE_MASTER)
        keys = ("id", "num", "title", "statement", "scenarios", "tags", "start", "end")
        self.assertEqual([[o[k] for k in keys] for o in old],
                         [[n[k] for k in keys] for n in new])

    def test_render_matches_engine(self):
        args = ("REQ-0003", "Новое", "Утверждение", ["- Given a When b Then c"], ["[from: x 2026]"])
        self.assertEqual(engine.render_requirement(*args), SG.native().render(*args))

    def test_native_profile_is_native(self):
        self.assertTrue(SG.native().is_native())
        self.assertTrue(SG.native().supported()[0])

    def test_section_spans_match_engine(self):
        lines = NATIVE_MASTER.splitlines()
        g = SG.native()
        self.assertEqual(g.section_span(lines, "requirements"),
                         engine._h2_span(lines, engine._SEC_REQUIREMENTS))
        self.assertEqual(g.section_span(lines, "audit"),
                         engine._h2_span(lines, engine._SEC_AUDIT))

    def test_next_id_continues_numbering(self):
        g = SG.native()
        self.assertEqual(g.next_id(g.parse(NATIVE_MASTER)), "REQ-0003")


class ForeignGrammarTest(unittest.TestCase):
    def setUp(self):
        self.g = SG.Grammar(OPENSPEC_PROFILE)

    def test_parses_title_only_with_block_scenarios(self):
        reqs = self.g.parse(OPENSPEC_MASTER)
        self.assertEqual([r["title"] for r in reqs],
                         ["The system SHALL refund within 24h", "The system SHALL log refunds"])
        self.assertEqual([len(r["scenarios"]) for r in reqs], [1, 1])
        self.assertIn("Scenario: in time", reqs[0]["scenarios"][0])

    def test_lead_case_is_not_significant(self):
        g = SG.Grammar({**OPENSPEC_PROFILE,
                        "requirement": {**OPENSPEC_PROFILE["requirement"], "lead": "requirement"}})
        self.assertEqual(len(g.parse(OPENSPEC_MASTER)), 2)

    def test_requirement_heading_does_not_close_its_own_section(self):
        """Требование уровня `##` внутри раздела `##` — раздел не схлопывается в одну строку."""
        span = self.g.section_span(OPENSPEC_MASTER.splitlines(), "requirements")
        self.assertIsNotNone(span)
        self.assertGreater(span[1] - span[0], 10)

    def test_render_uses_project_shape(self):
        out = self.g.render(None, "SHALL notify", "Уведомление.",
                            ["- **Given** оплата **When** возврат **Then** ушло"], [])
        self.assertEqual(out[0], "## Requirement: SHALL notify")
        self.assertTrue(any(l.startswith("#### Scenario: ") for l in out), out)
        self.assertNotIn("**", [l for l in out if l.startswith("#### Scenario")][0])

    def test_scenario_key_ignores_presentation(self):
        """Тождество сценария — содержание, а не оформление: иначе повторный merge = modify."""
        inline = "- **Given** оплата **When** возврат **Then** ушло"
        block = "#### Scenario: возврат\n" + inline
        self.assertEqual(self.g.scenario_key(block), self.g.scenario_key(inline))

    def test_title_only_without_lead_searches_only_in_section(self):
        g = SG.Grammar({**OPENSPEC_PROFILE,
                        "requirement": {"level": 2, "kind": "title-only", "lead": ""}})
        self.assertEqual(g.scope, "section")
        titles = [r["title"] for r in g.parse(OPENSPEC_MASTER)]
        self.assertNotIn("Purpose", titles)

    def test_numbered_next_id_continues_same_section(self):
        g = SG.Grammar({"requirement": {"level": 3, "kind": "numbered"},
                        "requirements_section": ["требования"], "scenario": {"style": "none"}})
        doc = "## 3. Требования\n### 3.1 Раз\nТекст.\n### 3.2 Два\nТекст.\n"
        self.assertEqual(g.next_id(g.parse(doc)), "3.3")


class SupportedTest(unittest.TestCase):
    def test_unknown_kind_is_refused(self):
        ok, why = SG.Grammar({"requirement": {"kind": "mixed"}}).supported()
        self.assertFalse(ok)
        self.assertTrue(any("mixed" in w for w in why))

    def test_unknown_kind_parses_to_nothing_instead_of_crashing(self):
        """Отказ даёт supported(); парсер обязан вернуть «требований нет», а не исключение."""
        g = SG.Grammar({"requirement": {"kind": "table-row"}, "scenario": {"style": "table"}})
        self.assertEqual(g.parse(NATIVE_MASTER), [])
        self.assertIsNone(g.match_requirement("### REQ-0001: Возврат платежа"))

    def test_unknown_scenario_style_is_refused(self):
        ok, why = SG.Grammar({"scenario": {"style": "table"}}).supported()
        self.assertFalse(ok)
        self.assertTrue(any("table" in w for w in why))


class LayersTest(unittest.TestCase):
    """policy.json > детект > NATIVE, и неуверенный детект не применяется вовсе."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "ground" / "inventory").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _detect(self, grammar: dict, confidence: float = 1.0, native: bool = False,
                research: dict = None) -> None:
        data = {"schema_version": 1, "grammar": grammar, "confidence": confidence,
                "matches_native": native}
        if research:
            data["research"] = research
        (self.root / "ground" / "inventory" / "spec-conventions.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_no_detect_no_config_is_native(self):
        self.assertTrue(SG.load_profile(self.root, cfg={}).is_native())

    def test_confident_detect_applies(self):
        self._detect(OPENSPEC_PROFILE)
        g = SG.load_profile(self.root, cfg={})
        self.assertEqual(g.kind, "title-only")
        self.assertEqual(g.scenario_style, "gwt-block")
        self.assertEqual(g.layers.get("requirement"), "detected")

    def test_low_confidence_detect_is_ignored(self):
        """Слабая догадка молча подменившая грамматику хуже отсутствия детекта."""
        self._detect(OPENSPEC_PROFILE, confidence=0.2)
        self.assertTrue(SG.load_profile(self.root, cfg={}).is_native())

    def test_low_confidence_with_research_applies(self):
        self._detect(OPENSPEC_PROFILE, confidence=0.2, research={"by": "spec-researcher"})
        self.assertEqual(SG.load_profile(self.root, cfg={}).kind, "title-only")

    def test_policy_wins_over_detect(self):
        self._detect(OPENSPEC_PROFILE)
        cfg = {"spec": {"grammar": {"requirement_kind": "numbered", "requirement_level": 3},
                        "id_prefix": "KE"}}
        g = SG.load_profile(self.root, cfg=cfg)
        self.assertEqual((g.kind, g.level, g.id_prefix), ("numbered", 3, "KE"))
        self.assertEqual(g.layers.get("requirement"), "policy")
        self.assertEqual(g.scenario_style, "gwt-block")   # ось, которую policy не трогала

    def test_markers_compatible_keeps_native_verdict(self):
        """Детект отдаёт один реальный якорь; NATIVE держит список синонимов — это не «чужой»."""
        self._detect({**OPENSPEC_PROFILE,
                      "requirement": {"level": 3, "kind": "id-colon", "id_prefix": "REQ"},
                      "requirements_section": ["требования и сценарии"],
                      "audit_section": ["журнал изменений"],
                      "scenario": {"style": "gwt-inline", "level": 4},
                      "provenance": "from-bracket"})
        self.assertTrue(SG.load_profile(self.root, cfg={}).is_native())


if __name__ == "__main__":
    unittest.main(verbosity=2)
