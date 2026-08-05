#!/usr/bin/env python3
"""Тесты инкрементального обогащения grounding (enrich_grounding).

Ключевые гарантии, на которые опирается SDD:
  • свежесть: enrich пересканирует код, а не читает устаревший scan;
  • удаления: артефакт, удалённый фичей, выпадает из excerpt (не остаётся призраком).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import enrich_grounding  # noqa: E402

ENTITY = """
package com.x.domain;
import javax.persistence.Entity;
@Entity
public class {name} {{ private Long id; }}
"""

TASK_PLAN = {"feature_slug": "feat-x", "tasks": [{"id": "T1", "title": "add entity", "modules": ["app"]}]}


class EnrichFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "build.gradle").write_text("dependencies {}\n", encoding="utf-8")
        self.src = self.root / "src/main/java/com/x/domain"
        self.src.mkdir(parents=True, exist_ok=True)
        self.analysis = self.root / "docs/system-analysis"
        self.scan = self.analysis / "scan"
        self.analysis.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_entity(self, name: str):
        (self.src / f"{name}.java").write_text(ENTITY.format(name=name), encoding="utf-8")

    def _excerpt(self) -> dict:
        return json.loads((self.analysis / "grounding-excerpt.json").read_text(encoding="utf-8"))

    def test_rescan_picks_up_new_entity(self):
        # Кода ещё нет в scan — enrich должен сам пересканировать и увидеть новую сущность.
        self._write_entity("Alpha")
        enrich_grounding.enrich(self.analysis, self.scan, TASK_PLAN,
                                code_root=self.root, rescan=True)
        names = {e["name"] for e in self._excerpt()["entities"]}
        self.assertIn("Alpha", names)

    def test_deletion_propagates(self):
        # Прогон 1: две сущности.
        self._write_entity("Alpha")
        self._write_entity("Beta")
        enrich_grounding.enrich(self.analysis, self.scan, TASK_PLAN,
                                code_root=self.root, rescan=True)
        self.assertEqual({e["name"] for e in self._excerpt()["entities"]}, {"Alpha", "Beta"})

        # Прогон 2: Beta удалена из кода — должна выпасть из excerpt, не остаться призраком.
        (self.src / "Beta.java").unlink()
        enrich_grounding.enrich(self.analysis, self.scan, TASK_PLAN,
                                code_root=self.root, rescan=True)
        names = {e["name"] for e in self._excerpt()["entities"]}
        self.assertEqual(names, {"Alpha"}, "удалённая сущность не должна остаться в grounding")

    def test_sources_preserved_across_runs(self):
        self._write_entity("Alpha")
        enrich_grounding.enrich(self.analysis, self.scan, TASK_PLAN, feature_slug="feat-1",
                                code_root=self.root, rescan=True)
        enrich_grounding.enrich(self.analysis, self.scan, TASK_PLAN, feature_slug="feat-2",
                                code_root=self.root, rescan=True)
        alpha = next(e for e in self._excerpt()["entities"] if e["name"] == "Alpha")
        # _sources накапливает историю появления, не теряя первый источник.
        self.assertIn("feat-1", alpha["_sources"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
