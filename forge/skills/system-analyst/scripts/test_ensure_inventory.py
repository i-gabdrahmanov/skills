#!/usr/bin/env python3
"""Тесты сборщика инвентаря (ensure_inventory).

Инвентарь — топливо детерминированных гейтов: по нему check_taskplan ловит выдуманные
классы и модули. Отсюда контракт, который здесь фиксируется:
  • он ЭФЕМЕРНЫЙ и лежит в ground/inventory (в git не едет, каталог самоигнорирующийся);
  • он идемпотентен — без изменений в коде второй прогон ничего не переписывает;
  • он замечает ЛЮБОЕ изменение состава: добавление, удаление и правку на месте
    (удаление — самое коварное: mtime соседей не двигает, а призрак в инвентаре остаётся);
  • он детерминирован — одинаковый код даёт одинаковые байты на любой машине.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import ensure_inventory as EI  # noqa: E402

ENTITY = """
package com.x.domain;
import javax.persistence.Entity;
@Entity
public class {name} {{ private Long id; }}
"""
CONTROLLER = """
package com.x.api;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/v1/things")
public class ThingController {
  @GetMapping("/list") public String list() { return ""; }
}
"""
UTIL = """
package com.x.common;
public final class DateUtils { public static String fmt(Object o) { return ""; } }
"""


class EnsureInventoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "build.gradle").write_text(
            "dependencies { implementation 'org.apache.commons:commons-lang3:3.14.0' }\n",
            encoding="utf-8")
        self.domain = self.root / "src/main/java/com/x/domain"
        self.domain.mkdir(parents=True)
        (self.domain / "Alpha.java").write_text(ENTITY.format(name="Alpha"), encoding="utf-8")
        api = self.root / "src/main/java/com/x/api"
        api.mkdir(parents=True)
        (api / "ThingController.java").write_text(CONTROLLER, encoding="utf-8")
        common = self.root / "src/main/java/com/x/common"
        common.mkdir(parents=True)
        (common / "DateUtils.java").write_text(UTIL, encoding="utf-8")
        self.inv = self.root / "ground" / "inventory"

    def tearDown(self):
        self._tmp.cleanup()

    def _excerpt(self) -> dict:
        return json.loads((self.inv / "grounding-excerpt.json").read_text(encoding="utf-8"))

    def test_lands_in_ground_and_self_ignores(self):
        res = EI.ensure(self.root)
        self.assertEqual(res["status"], "ok")
        self.assertTrue((self.inv / "grounding-excerpt.json").exists())
        self.assertTrue((self.inv / "scan" / "domain.json").exists())
        gi = self.inv / ".gitignore"
        self.assertTrue(gi.exists(), "инвентарь обязан быть самоигнорирующимся")
        self.assertIn("*", gi.read_text(encoding="utf-8").split())

    def test_collects_what_gates_need(self):
        EI.ensure(self.root)
        ex = self._excerpt()
        self.assertEqual({e["name"] for e in ex["entities"]}, {"Alpha"})
        self.assertIn("ThingController", {c["name"] for c in ex["components"]})
        self.assertEqual(len(ex["api_endpoints"]), 1)
        self.assertIn("com.x.common.DateUtils", ex["reuse"]["project_utils"])
        self.assertTrue(any(d.startswith("commons-lang3") for d in ex["reuse"]["dependencies"]))

    def test_idempotent_without_code_change(self):
        EI.ensure(self.root)
        before = (self.inv / "grounding-excerpt.json").read_bytes()
        res = EI.ensure(self.root)
        self.assertFalse(res["rescanned"], "второй прогон не должен пересканировать")
        self.assertEqual((self.inv / "grounding-excerpt.json").read_bytes(), before)

    def test_detects_added_file(self):
        EI.ensure(self.root)
        (self.domain / "Beta.java").write_text(ENTITY.format(name="Beta"), encoding="utf-8")
        res = EI.ensure(self.root)
        self.assertTrue(res["rescanned"])
        self.assertEqual({e["name"] for e in self._excerpt()["entities"]}, {"Alpha", "Beta"})

    def test_detects_deleted_file(self):
        """Удаление не двигает mtime соседей — по одному времени инвентарь считался бы
        актуальным и продолжал держать призрака удалённой сущности."""
        (self.domain / "Beta.java").write_text(ENTITY.format(name="Beta"), encoding="utf-8")
        EI.ensure(self.root)
        self.assertIn("Beta", {e["name"] for e in self._excerpt()["entities"]})

        (self.domain / "Beta.java").unlink()
        res = EI.ensure(self.root)
        self.assertTrue(res["rescanned"], "удаление обязано считаться устареванием")
        self.assertEqual({e["name"] for e in self._excerpt()["entities"]}, {"Alpha"})

    def test_detects_in_place_edit(self):
        EI.ensure(self.root)
        time.sleep(0.01)
        (self.domain / "Alpha.java").write_text(
            ENTITY.format(name="Alpha").replace("private Long id;", "private Long id; private String s;"),
            encoding="utf-8")
        self.assertTrue(EI.ensure(self.root)["rescanned"])

    def test_deterministic_order(self):
        for n in ("Zeta", "Mu", "Beta"):
            (self.domain / f"{n}.java").write_text(ENTITY.format(name=n), encoding="utf-8")
        EI.ensure(self.root)
        names = [e["name"] for e in self._excerpt()["entities"]]
        self.assertEqual(names, sorted(names))
        comps = [(c["name"], c["layer"]) for c in self._excerpt()["components"]]
        self.assertEqual(comps, sorted(comps))

    def test_empty_project_reports_empty(self):
        with tempfile.TemporaryDirectory() as empty:
            res = EI.ensure(Path(empty))
            self.assertEqual(res["status"], "empty",
                             "пустой инвентарь нельзя выдавать за собранный — гейты останутся без топлива")

    def test_no_absolute_paths_leak(self):
        EI.ensure(self.root)
        blob = (self.inv / "scan" / "domain.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), blob)
        self.assertIn("src/main/java/com/x/domain/Alpha.java", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
