#!/usr/bin/env python3
"""test_edge_proposals.py — предложения рёбер моделью по одной заметке (задача 2.3).

Пинит:
  • очередь link-note: единица на заметку, у вида которой есть исходящие рёбра (не у сервиса, топика,
    данных, внешней системы); вход — текст заметки, допустимые рёбра, подсказки резолвера, операции,
    уже поставленные правилами рёбра; вход с брифом — в бюджете; без утверждённого каталога — отказ;
  • механика + «ответы модели» фикстуры через настоящую очередь = ровно рёбра эталона (без belongs_to);
    общий шедулер закрытия даёт три closes_task;
  • отказ всей единицы: выдуманная цитата, недопустимая пара видов, belongs_to от модели;
  • неоднозначная или несуществующая цель — не отказ, а вопрос человеку; остальные рёбра единицы ставятся;
  • повтор механического ребра моделью не дублирует запись.
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
from sgraph import edges as E  # noqa: E402
from sgraph import link as L  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import tools as T  # noqa: E402

G = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def items(self):
        return json.loads((self.root / Q.QUEUE_REL / "link-note.json").read_text(encoding="utf-8"))["items"]

    def lease(self, note):
        ans = K.link_answer(self.root)
        while True:
            unit = T.call("graph_next", self.root, {"queue": "link-note"})
            it = next(i for i in self.items() if i["id"] == unit["item_id"])
            if it["refs"]["note"] == note:
                return unit
            T.call(unit["tool"], self.root, dict(ans(unit), item_id=unit["item_id"]))


class Queue(_Base):
    def test_units_and_inputs(self):
        with self.assertRaises(T.ToolError):
            T.call("graph_link_start", self.root, {})
        K.through_split(self.root)
        T.call("graph_edges_mechanical", self.root, {})
        res = T.call("graph_link_start", self.root, {})
        notes = {it["refs"]["note"] for it in self.items()}
        self.assertEqual(res["units"], 21)
        self.assertNotIn("task-service", notes)
        self.assertNotIn("pprbkid.task.closed.v1", notes)
        self.assertIn("Укомплектование", notes, "у операции есть related/part_of")
        it = next(i for i in self.items() if i["refs"]["note"] == "Шедулер формирования задачи укомплектования")
        text = Q._input_path(self.root, it["id"]).read_text(encoding="utf-8")
        for needle in ("`creates_task`", "«Укомплектование» — `task:KOMPLEKT`", "calls → «upz-adapter POST api-v1-upz-tasks»",
                       "Динамические атрибуты укомплектования", "### 2.1 Шедулер формирования задачи укомплектования"):
            self.assertIn(needle, text)
        self.assertNotIn("`belongs_to`", text)
        for it in self.items():
            self.assertLessEqual(len(Q._input_path(self.root, it["id"]).read_text(encoding="utf-8")) + L.brief_chars(),
                                 Q.DEFAULTS["budget_chars"])


class Flow(_Base):
    def test_mechanics_plus_model_equal_golden(self):
        K.through_link(self.root)
        got = {(p["from"], p["edge"], p["to"]) for p in E.load_all(self.root)}
        exp = {(e["from"], e["edge"], e["to"]) for e in G["edges"] if e["edge"] != "belongs_to"}
        self.assertEqual(got, exp)
        closes = [p for p in E.load(self.root, "Шедулер закрытия регулярных задач") if p["edge"] == "closes_task"]
        self.assertEqual(len(closes), 3)
        self.assertEqual(Q.closed(self.root, "link-note"), (True, []))
        self.assertEqual(L.load_questions(self.root), [])


class Answers(_Base):
    def setUp(self):
        super().setUp()
        K.through_split(self.root)
        T.call("graph_edges_mechanical", self.root, {})
        T.call("graph_link_start", self.root, {})

    def test_whole_unit_rejected(self):
        unit = self.lease("Шедулер закрытия регулярных задач")
        cases = {
            "E-edge-quote": {"edge": "closes_task", "to": "Регистрация", "quote": "закрывает регистрацию каждую минуту без исключений"},
            "E-edge-kind": {"edge": "ui_of", "to": "Регистрация", "quote": "`REGISTRATION` (Регистрация)"},
        }
        for code, p in cases.items():
            with self.subTest(code=code):
                again = T.call("graph_next", self.root, {"queue": "link-note"})
                self.assertEqual(again["item_id"], unit["item_id"], "отказ вернул ту же единицу в очередь")
                with self.assertRaises(T.ToolError) as cm:
                    T.call("graph_link_put", self.root, {"item_id": unit["item_id"], "proposals": [p]})
                self.assertIn(code, cm.exception.codes)
        st = Q.status(self.root, "link-note")
        self.assertEqual(st["abandoned"], 0, "два отказа — ещё не брошена")
        unit = T.call("graph_next", self.root, {"queue": "link-note"})
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_link_put", self.root, {"item_id": unit["item_id"], "proposals": [
                {"edge": "belongs_to", "to": "task-service", "quote": "Запускается ежедневно в 23:00. Закрывает"}]})
        self.assertIn("E-edge-kind", cm.exception.codes)

    def test_ambiguous_target_is_a_question_not_a_failure(self):
        unit = self.lease("Шедулер закрытия регулярных задач")
        res = T.call("graph_link_put", self.root, {"item_id": unit["item_id"], "proposals": [
            {"edge": "closes_task", "to": "Регистрация", "quote": "`REGISTRATION` (Регистрация)"},
            {"edge": "closes_task", "to": "Санация задач", "quote": "выполняет санацию задач, оставшихся без исполнителя"},
            {"edge": "invokes", "to": "Отправка события о закрытии задачи",
             "quote": "запускается отправка события о закрытии задачи (раздел 3.2)"},
        ]})
        self.assertEqual((res["accepted"], res["questions"]), (2, 1))
        q = L.load_questions(self.root)
        self.assertEqual((q[0]["target"], q[0]["edge"]), ("Санация задач", "closes_task"))

    def test_repeat_of_mechanical_edge_merges(self):
        unit = self.lease("UI регистрации")
        T.call("graph_link_put", self.root, {"item_id": unit["item_id"], "proposals": [
            {"edge": "calls", "to": "task-service GET api-v1-tasks", "quote": "`GET /api/v1/tasks` (task-service)"}]})
        calls = [p for p in E.load(self.root, "UI регистрации") if p["edge"] == "calls"]
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["origin"].startswith("rule:"))


if __name__ == "__main__":
    unittest.main()
