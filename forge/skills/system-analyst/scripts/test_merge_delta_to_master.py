#!/usr/bin/env python3
"""test_merge_delta_to_master.py — тесты слияния дельты (sdd.md) в требования-мастер."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import merge_delta_to_master as merge_mod  # noqa: E402
import check_master_spec as gate  # noqa: E402

TEMPLATE = SCRIPT_DIR.parent / "references" / "master-spec-template.md"

DELTA = """# SDD: авто-закрытие пустых заявок

**Jira:** DEMO-1

## 1. Назначение и результат
Автозакрытие пустых заявок разгружает оператора.

## 2. Границы охвата
- В рамках: закрытие пустых заявок.

## 3. Функциональные требования (Given-When-Then)
- **Given** заявка пуста и старше 24ч **When** прогон **Then** статус CLOSED.
- **Given** заявка с позициями **When** прогон **Then** статус не меняется.

## 7. Критерии приёмки (Acceptance)
- Given пустая заявка When прогон Then есть запись в аудите.
"""


class MergeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sdd = self.root / "sdd.md"
        self.sdd.write_text(DELTA, encoding="utf-8")
        self.spec = self.root / "specs" / "claims" / "spec.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _merge(self):
        return merge_mod.merge(self.sdd, self.spec, TEMPLATE, "demo-close", "claims")

    def test_creates_from_template_and_merges(self):
        r = self._merge()
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["created"])
        self.assertTrue(self.spec.exists())
        # 3 GWT в дельте (§3: 2, §7: 1)
        self.assertEqual(r["gwt_in_delta"], 3)
        self.assertEqual(r["scenarios_added"], 3)
        self.assertEqual(r["requirement_added"], 1)
        self.assertEqual(r["audit_added"], 1)
        text = self.spec.read_text(encoding="utf-8")
        self.assertIn("from: demo-close", text)
        self.assertIn("CLOSED", text)

    def test_idempotent(self):
        self._merge()
        r2 = self._merge()
        self.assertFalse(r2["created"])
        self.assertEqual(r2["scenarios_added"], 0)
        self.assertEqual(r2["requirement_added"], 0)
        self.assertEqual(r2["audit_added"], 0)
        # сценарии не задублировались
        text = self.spec.read_text(encoding="utf-8")
        self.assertEqual(text.count("статус CLOSED"), 1)

    def test_merged_master_passes_composition_gate(self):
        self._merge()
        v = gate.check(self.spec, "applicability")
        self.assertEqual(v["status"], "pass", v["errors"])

    def test_second_feature_appends_not_overwrites(self):
        self._merge()
        # вторая фича с другим сценарием
        sdd2 = self.root / "sdd2.md"
        sdd2.write_text(DELTA.replace("CLOSED", "ARCHIVED").replace("демо", "d2"), encoding="utf-8")
        r = merge_mod.merge(sdd2, self.spec, TEMPLATE, "feature-two", "claims")
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["created"])
        self.assertGreaterEqual(r["scenarios_added"], 1)
        text = self.spec.read_text(encoding="utf-8")
        self.assertIn("from: demo-close", text)
        self.assertIn("from: feature-two", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
