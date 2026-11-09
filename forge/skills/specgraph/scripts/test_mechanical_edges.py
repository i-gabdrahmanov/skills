#!/usr/bin/env python3
"""test_mechanical_edges.py — механические рёбра и общая проверка предложения (задача 2.2).

Пинит:
  • на нарезанной фикстуре правила дают РОВНО рёбра эталона с `mechanical: true` (precision и recall
    100 %): обработчик → топик, публикация, METHOD /path чужого сервиса и экрана, имя узла данных;
  • «См. также» рёбер не даёт; эндпойнт не вызывает сам себя; публикация от обработчика не ставится;
  • проверка предложения (одна на правила и модель): выдуманная цитата, короткая цитата, недопустимая
    пара видов, цель-призрак и неоднозначная цель — отказ с кодом; повтор правил — без дублей;
  • хранилище: тот же (ребро, цель) — одна запись, цитаты копятся; отклонённое не воскрешается.
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
from sgraph import config as C  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import tools as T  # noqa: E402

G = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = K.make_project(Path(cls.tmp.name) / "repo")
        K.through_split(cls.root)
        cls.cfg = C.load(cls.root)[0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Rules(_Base):
    def test_exactly_the_mechanical_edges_of_the_golden(self):
        res = T.call("graph_edges_mechanical", self.root, {})
        got = {(p["from"], p["edge"], p["to"]) for p in E.load_all(self.root)}
        exp = {(e["from"], e["edge"], e["to"]) for e in G["edges"] if e["mechanical"] and e["edge"] != "belongs_to"}
        self.assertEqual(got, exp)
        self.assertEqual(res["by_edge"], {"calls": 12, "consumes": 1, "publishes_to": 2, "uses_data": 4})
        again = T.call("graph_edges_mechanical", self.root, {})
        self.assertEqual(again["added"], 0, "повтор правил — без новых записей")
        for p in E.load_all(self.root):
            self.assertTrue(p["origin"].startswith("rule:R-edge-"))
            self.assertEqual(p["status"], "proposed")

    def test_see_also_and_self(self):
        lines = E._lines("## 2. Шедулеры\n\nвызывает `GET /a`\n\n## 7. См. также\n\n- `GET /b` и топик a.b.c.v1\n\n## 8. Итог\n\nтекст\n",
                         ["см. также"])
        self.assertIn("вызывает `GET /a`", lines)
        self.assertNotIn("- `GET /b` и топик a.b.c.v1", lines)
        self.assertIn("текст", lines, "раздел после «См. также» снова читается")
        got = {(p["from"], p["edge"], p["to"]) for p in E.load_all(self.root)} or {
            (p["from"], p["edge"], p["to"]) for ps in E.mechanical(E.Graph(self.root, self.cfg)).values() for p in ps}
        self.assertFalse([x for x in got if x[0] == x[2]], "эндпойнт не вызывает сам себя")
        self.assertFalse([x for x in got if x[0] == "Обработка ответа УПЗ о создании задачи" and x[1] == "publishes_to"])


class Check(_Base):
    def test_one_check_for_rules_and_model(self):
        g = E.Graph(self.root, self.cfg)
        ok, err = g.check("Шедулер закрытия регулярных задач",
                          {"edge": "closes_task", "to": "Регистрация", "quote": "`REGISTRATION` (Регистрация)"})
        self.assertIsNone(err)
        self.assertEqual(ok["to"], "Регистрация")
        cases = {
            "E-edge-quote": {"edge": "closes_task", "to": "Регистрация", "quote": "закрывает все задачи регистрации немедленно"},
            "E-edge-kind": {"edge": "ui_of", "to": "Регистрация", "quote": "`REGISTRATION` (Регистрация)"},
            "E-edge-target": {"edge": "calls", "to": "GET /nothing/here", "quote": "`REGISTRATION` (Регистрация)"},
        }
        for code, p in cases.items():
            with self.subTest(code=code):
                self.assertEqual(g.check("Шедулер закрытия регулярных задач", p)[1][0], code)
        short = g.check("Шедулер закрытия регулярных задач", {"edge": "closes_task", "to": "Регистрация", "quote": "Регистрация"})
        self.assertEqual(short[1][0], "E-edge-quote")
        by_key = g.check("Шедулер формирования задачи укомплектования",
                         {"edge": "calls", "to": "POST /api/v1/upz/tasks", "quote": "вызвать `POST /api/v1/upz/tasks` УПЗ-адаптера"})
        self.assertEqual(by_key[0]["to"], "upz-adapter POST api-v1-upz-tasks", "цель по ключу через резолвер")
        g.index.append(dict(g.by_name["rmoc-service GET rmoc-packages"], name="archive GET rmoc-packages", service="archive"))
        amb = g.check("UI передачи упаковок", {"edge": "calls", "to": "GET /rmoc/packages",
                                               "quote": "вызовом `GET /rmoc/packages` (rmoc-service)"})
        self.assertEqual(amb[1][0], "E-edge-target")
        self.assertIn("неоднозначно", amb[1][1])

    def test_store_merges_and_keeps_rejected(self):
        name = "UI регистрации"
        base = {"from": name, "edge": "ui_of", "to": "Регистрация", "quote": "Экран работы с задачей «Регистрация».",
                "origin": "model", "confidence": "high", "status": "rejected"}
        E.add(self.root, name, [base])
        E.add(self.root, name, [dict(base, status="proposed", quote="Список задач — `GET /api/v1/tasks` (task-service)")])
        rec = [p for p in E.load(self.root, name) if p["edge"] == "ui_of"]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["status"], "rejected", "отклонённое не воскрешается")
        self.assertEqual(len(rec[0]["quotes"]), 2)


if __name__ == "__main__":
    unittest.main()
