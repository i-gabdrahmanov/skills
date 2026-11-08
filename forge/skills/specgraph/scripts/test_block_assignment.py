#!/usr/bin/env python3
"""test_block_assignment.py — раскладка блоков по утверждённым узлам (задача 1.5).

Пинит:
  • без согласия graph-catalog очередь assign-section не строится, и ответ не принимается (кодом);
  • единицы покрывают каждый блок каждого файла scope ровно один раз (полнота до ответа модели);
    вход единицы с брифом — в бюджете, крупный файл режется не внутри блока;
  • эталонные планы фикстуры через настоящую очередь принимаются, очередь закрывается;
  • отказы с кодами: потерянный блок, двойное назначение, чужой блок, цель вне каталога, блоки раздела
    соседнего узла, неверный skip, duplicate-of не повтор, обратный слэш;
  • новый узел (new_nodes с why) уходит в дельту catalog.delta.json и снимок каталога не меняет;
    duplicate-of для дословного повтора проходит.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import _testkit as K  # noqa: E402
import gen_variants as GV  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import blocks as B  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import tools as T  # noqa: E402


class _Proj(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def items(self):
        return json.loads((self.root / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))["items"]

    def lease(self, rel_part: str):
        """Арендовать единицу нужного файла (остальные сдаются эталоном)."""
        ans = K.assign_answer(self.root)
        while True:
            unit = T.call("graph_next", self.root, {"queue": "assign-section"})
            it = next(i for i in self.items() if i["id"] == unit["item_id"])
            if rel_part in it["refs"]["file"]:
                return unit, it, ans(unit)
            T.call(unit["tool"], self.root, dict(ans(unit), item_id=unit["item_id"]))


class Gate(_Proj):
    def test_no_consent_no_queue(self):
        T.call("graph_survey", self.root, {})
        K.run_queue(self.root, "draft-file", K.draft_answer(self.root))
        T.call("graph_catalog", self.root, {})
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_assign_start", self.root, {})
        self.assertEqual(cm.exception.codes, ["E-catalog-unapproved"])
        K.approve_catalog(self.root)
        T.call("graph_assign_start", self.root, {})
        T.call("graph_catalog_decide", self.root, {"actions": [{"action": "drop", "node": "УПЗ", "why": "x"}]})
        T.call("graph_catalog", self.root, {})
        (self.root / CT.APPROVED_REL).write_bytes((self.root / CT.CATALOG_REL).read_bytes())
        unit = T.call("graph_next", self.root, {"queue": "assign-section"})
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_assign_put", self.root, {"item_id": unit["item_id"], "assign": []})
        self.assertEqual(cm.exception.codes, ["E-catalog-unapproved"], "подменённый снимок не утверждён")


class Units(_Proj):
    def test_units_cover_every_block_once_and_fit_budget(self):
        K.through_catalog(self.root)
        res = T.call("graph_assign_start", self.root, {})
        self.assertEqual(res["files"], 8)
        cover = {}
        for it in self.items():
            for b in it["refs"]["blocks"]:
                cover[b] = cover.get(b, 0) + 1
            text = Q._input_path(self.root, it["id"]).read_text(encoding="utf-8")
            self.assertLessEqual(len(text) + AS.brief_chars(), Q.DEFAULTS["budget_chars"])
        for rel in {it["refs"]["file"] for it in self.items()}:
            for b in B.load(self.root, rel)["blocks"]:
                self.assertEqual(cover.get(b["id"]), 1, b["id"])

    def test_large_file_is_split_between_blocks(self):
        blocks, _ = B.parse((GV.bloated(Path(self.tmp.name) / "b", 1, 30) / "svc-01" / "svc-01.md").read_text(encoding="utf-8"),
                            "x.md")
        ranges = AS._ranges(blocks, 8000)
        self.assertGreater(len(ranges), 3)
        self.assertEqual([r for r in ranges if r[0] >= r[1]], [])
        self.assertEqual(sum(b - a for a, b in ranges), len(blocks))
        self.assertTrue(all(ranges[i][1] == ranges[i + 1][0] for i in range(len(ranges) - 1)))


class Answers(_Proj):
    def setUp(self):
        super().setUp()
        K.through_catalog(self.root)
        T.call("graph_assign_start", self.root, {})

    def test_fixture_plans_close_the_queue(self):
        done = K.run_queue(self.root, "assign-section", K.assign_answer(self.root))
        self.assertTrue(done and all(d["accepted"] for d in done))
        self.assertEqual(Q.closed(self.root, "assign-section"), (True, []))
        assigned = {b for p in AS.load_plans(self.root) for a in p["assign"] for b in a["blocks"]}
        skipped = {b for p in AS.load_plans(self.root) for s in p["skip"] for b in s["blocks"]}
        all_blocks = {b for it in self.items() for b in it["refs"]["blocks"]}
        self.assertEqual(assigned | skipped, all_blocks)

    def reset(self, item_id):
        q = json.loads((self.root / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))
        for it in q["items"]:
            if it["id"] == item_id:
                it.update(status="leased", attempts=0, lease_until=Q._clock() + 600)
        (self.root / Q.QUEUE_REL / "assign-section.json").write_text(json.dumps(q), encoding="utf-8")

    def test_rejections(self):
        unit, it, good = self.lease("task-service.md")
        blocks = it["refs"]["blocks"]
        sched = next(a for a in good["assign"] if a["node"] == "Шедулер закрытия регулярных задач")
        form = next(a for a in good["assign"] if a["node"] == "Шедулер формирования задачи укомплектования")

        def lost(a):
            a["assign"][0]["blocks"] = a["assign"][0]["blocks"][1:]

        def twice(a):
            a["skip"].append({"blocks": [a["assign"][0]["blocks"][0]], "reason": "toc"})

        def foreign(a):
            a["assign"][0]["blocks"].append("docs/spec/ui/регистрация.md#1")

        def ghost(a):
            a["assign"][0]["node"] = "Шедулер, которого нет"

        def split(a):
            for x in a["assign"]:
                if x["node"] == "Шедулер формирования задачи укомплектования":
                    x["blocks"] += sched["blocks"]
            a["assign"] = [x for x in a["assign"] if x["node"] != "Шедулер закрытия регулярных задач"]

        def badskip(a):
            b = a["assign"][0]["blocks"].pop()
            a["skip"].append({"blocks": [b], "reason": "лишнее"})

        def notdup(a):
            b = form["blocks"][-1]
            for x in a["assign"]:
                x["blocks"] = [y for y in x["blocks"] if y != b]
            a["skip"].append({"blocks": [b], "reason": "duplicate-of", "of": [blocks[0]]})

        def backslash(a):
            a["assign"][0]["node"] = "task-service\\x"

        cases = {"E-assign-lost": lost, "E-assign-twice": twice, "E-assign-foreign": foreign, "E-assign-node": ghost,
                 "E-assign-split": split, "E-assign-skip": badskip, "E-assign-dup": notdup, "E-assign-text": backslash}
        for code, mutate in cases.items():
            with self.subTest(code=code):
                bad = json.loads(json.dumps(good))
                mutate(bad)
                self.reset(unit["item_id"])
                with self.assertRaises(T.ToolError) as cm:
                    T.call("graph_assign_put", self.root, dict(bad, item_id=unit["item_id"]))
                self.assertIn(code, cm.exception.codes, str(cm.exception))

    def test_split_rule_ignores_same_name_node_of_other_file(self):
        """Боевой прогон 2026-11-07: раздел «Алгоритм обработки» файла А требовали отдать одноимённому
        узлу файла Б — субагент уносил его в unplaced. Узел раздела должен быть из этого же файла."""
        from unittest import mock
        unit, it, good = self.lease("task-service.md")
        sched = next(a for a in good["assign"] if a["node"] == "Шедулер закрытия регулярных задач")
        bad = json.loads(json.dumps(good))
        for x in bad["assign"]:
            if x["node"] == "Шедулер формирования задачи укомплектования":
                x["blocks"] += sched["blocks"]
        bad["assign"] = [x for x in bad["assign"] if x["node"] != "Шедулер закрытия регулярных задач"]
        cat = CT.load_approved(self.root)
        for n in cat["nodes"]:
            if n["name"] == "Шедулер закрытия регулярных задач":
                n["sources"] = [{"file": "docs/spec/другой.md", "sections": []}]
        self.reset(unit["item_id"])
        with mock.patch.object(AS.CT, "load_approved", return_value=cat):
            try:
                T.call("graph_assign_put", self.root, dict(bad, item_id=unit["item_id"]))
            except T.ToolError as e:
                self.assertNotIn("E-assign-split", e.codes, str(e))

    def test_new_node_goes_to_delta_and_duplicate_passes(self):
        unit, it, good = self.lease("rmoc-service.md")
        svc = next(a for a in good["assign"] if a["node"] == "rmoc-service")
        moved = svc["blocks"].pop()
        good["assign"].append({"node": "Справочник статусов упаковок", "blocks": [moved]})
        good["new_nodes"] = [{"kind": "data", "subkind": "enum", "name": "Справочник статусов упаковок",
                              "why": "раздел описывает статусы, в каталоге узла нет"}]
        snap = (self.root / CT.APPROVED_REL).read_bytes()
        res = T.call("graph_assign_put", self.root, dict(good, item_id=unit["item_id"]))
        self.assertEqual(res["new_nodes"], 1)
        delta = AS.load_delta(self.root)
        self.assertEqual([d["name"] for d in delta], ["Справочник статусов упаковок"])
        self.assertEqual((self.root / CT.APPROVED_REL).read_bytes(), snap, "дельта не меняет утверждённый снимок")
        unit, it, good = self.lease("upz-adapter.md")
        last = good["assign"][-1]["blocks"].pop()
        good["skip"].append({"blocks": [last], "reason": "duplicate-of", "of": [last]})
        self.reset(unit["item_id"])
        with self.assertRaises(T.ToolError):
            T.call("graph_assign_put", self.root, dict(good, item_id=unit["item_id"]))


if __name__ == "__main__":
    unittest.main()
