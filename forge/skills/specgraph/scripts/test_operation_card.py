#!/usr/bin/env python3
"""test_operation_card.py — карточка бизнес-операции (задача 2.5, ТЗ §4).

Пинит на фикстуре:
  • 4 операции — 4 единицы очереди card; карточка в маркере graph:card, у каждого утверждения ↗ блок этой
    операции; повтор синка после карточек — 0 изменений;
  • отказы: ссылка на чужой или несуществующий блок, пропущенное поле, больше 3 утверждений, обратный слэш;
  • операция без бизнес-текста — только «нет в источнике», status: stub, вопрос аналитику;
  • related «Передача упаковок» ↔ «Укомплектование»: одно основание на пару, повторное предложение с другой
    стороны вливается в него.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import card as CD  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import link as L  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

OP = "business/operations/Укомплектование.md"
TRANSFER = "Передача упаковок в СЦ-архив"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")
        K.through_link(self.root)
        T.call("graph_sync", self.root, {})
        self.vault = self.root / "docs" / "graph"

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, rel):
        return F.read(SP.read_note(self.vault / rel))

    def lease(self, name):
        """Арендовать единицу card нужной операции (остальные пропускаются по очереди)."""
        want = CD.unit_id(name)
        while True:
            unit = T.call("graph_next", self.root, {"queue": "card"})
            self.assertEqual(unit["status"], "единица")
            if unit["item_id"] == want:
                return unit
            T.call("graph_card_put", self.root, dict(K.card_answer(self.root)(unit), item_id=unit["item_id"]))

    def put_codes(self, unit, fields):
        """Коды отказа движка (без очереди: отказ инструментом возвращает единицу и копит попытки)."""
        res, errs = CD.put(self.root, T._need_cfg(self.root), unit["item_id"], fields)
        self.assertEqual(res, {})
        return {c for c, _ in errs}


class Cards(_Base):
    def test_fixture_cards(self):
        self.assertEqual(T.call("graph_card_start", self.root, {})["units"], 4)
        done = K.run_queue(self.root, "card", K.card_answer(self.root))
        self.assertEqual(sorted(d["note"] for d in done),
                         ["Передача упаковок в СЦ-архив", "Приём транспортных упаковок", "Регистрация", "Укомплектование"])
        self.assertFalse(any(d["stub"] for d in done))
        data, body = self.note(OP)
        card = F.get_block(body, "card")["content"]
        for _, title in CD.FIELDS:
            self.assertIn(f"**{title}:**", card)
        self.assertIn("отметить недостачу ↗ `docs/spec/business/типы-задач.md#6`", card)
        self.assertEqual(data["status"], "draft")
        self.assertEqual(T.call("graph_sync", self.root, {})["written"], 0)

    def test_rejects(self):
        T.call("graph_card_start", self.root, {})
        unit = self.lease("Укомплектование")
        good = K.card_answer(self.root)(unit)["fields"]
        own = good["what"][0]["blocks"][0]
        reg = json.loads((self.root / Q.QUEUE_REL / "card.json").read_text(encoding="utf-8"))
        foreign = next(it for it in reg["items"] if it["refs"]["note"] == "Регистрация")["refs"]["blocks"][-1]
        self.assertEqual(self.put_codes(unit, dict(good, what=[{"text": "чужое", "blocks": [foreign]}])), {"E-card-ref"})
        self.assertEqual(self.put_codes(unit, dict(good, what=[{"text": "нет блока", "blocks": [own + "9"]}])), {"E-card-ref"})
        self.assertEqual(self.put_codes(unit, dict(good, what=[{"text": "без ссылки"}])), {"E-card-ref"})
        self.assertEqual(self.put_codes(unit, {k: v for k, v in good.items() if k != "result"}), {"E-card-missing"})
        self.assertEqual(self.put_codes(unit, dict(good, what=[{"text": "x", "blocks": [own]}] * 4)), {"E-card-shape"})
        self.assertEqual(self.put_codes(unit, dict(good, what=[{"text": "a\\b", "blocks": [own]}])), {"E-card-text"})
        with self.assertRaises(T.ToolError) as cm:          # инструмент отдаёт те же коды и возвращает единицу
            T.call("graph_card_put", self.root, {"item_id": unit["item_id"], "fields": dict(good, purpose=None)})
        self.assertEqual(cm.exception.codes, ["E-card-missing"])
        unit = self.lease("Укомплектование")
        self.assertFalse(T.call("graph_card_put", self.root, {"item_id": unit["item_id"], "fields": good})["stub"])

    def test_unapproved_catalog(self):
        root = K.make_project(Path(self.tmp.name) / "bare")
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_card_start", root, {})
        self.assertIn("E-catalog-unapproved", cm.exception.codes)


class Stub(_Base):
    def test_operation_without_text(self):
        p = self.vault / OP
        data, body = F.read(SP.read_note(p))
        lines = body.split("\n")
        for b in reversed([b for b in F.blocks(body) if b["name"].startswith("source")]):
            del lines[b["start"]:b["end"] + 1]
        p.write_bytes(F.write(data, "\n".join(lines)).encode("utf-8"))
        T.call("graph_card_start", self.root, {})
        unit = self.lease("Укомплектование")
        self.assertEqual(self.put_codes(unit, {"purpose": [{"text": "домысел", "blocks": ["x#1"]}], "when": CD.NONE,
                                               "what": CD.NONE, "result": CD.NONE}), {"E-card-stub"})
        res = T.call("graph_card_put", self.root, {"item_id": unit["item_id"], "fields": {k: CD.NONE for k, _ in CD.FIELDS}})
        self.assertTrue(res["stub"])
        data, body = self.note(OP)
        self.assertEqual(data["status"], "stub")
        self.assertEqual(F.get_block(body, "card")["content"].count(f"_{CD.NONE}_"), 4)
        qs = L.load_questions(self.root)
        self.assertIn("Укомплектование", [q["note"] for q in qs if q["edge"] == "card"])


class Related(_Base):
    def pair(self):
        return [p for n in ("Укомплектование", TRANSFER) for p in E.load(self.root, n) if p["edge"] == "related"]

    def test_one_basis_per_pair(self):
        self.assertEqual(len(self.pair()), 1)
        first = self.pair()[0]
        side = first["to"]
        items = json.loads((self.root / Q.QUEUE_REL / "link-note.json").read_text(encoding="utf-8"))["items"]
        iid = next(it["id"] for it in items if it["refs"]["note"] == side)
        res, errs = L.put(self.root, T._need_cfg(self.root), iid,
                          [{"edge": "related", "to": first["from"], "quote": first["quote"], "confidence": "high"}])
        self.assertEqual(errs, [])
        self.assertEqual((res["added"], res["merged"]), (0, 1))
        self.assertEqual(len(self.pair()), 1)


if __name__ == "__main__":
    unittest.main()
