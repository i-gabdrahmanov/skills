#!/usr/bin/env python3
"""test_completeness.py — проверки полноты графа по ТЗ (задача 3.2).

Пинит:
  • правила completeness.json согласованы с онтологией; сломанное правило ловит self_check;
  • фикстура: ровно её собственные дыры (кто формирует «Регистрацию» и «Приём» — спека молчит; топик закрытия
    без читателя) и ни одного W-op-no-closer / W-op-no-ui;
  • каждое правило поднимается снятым ребром; флаг аналитика (system, public, external_side) его глушит;
  • E-consume-no-topic — ошибка (гейт), остальное — предупреждения;
  • отчёт группирует дыры по бизнес-операциям; open-questions.md — вопросы link и дыры, повтор без дублей.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import completeness as CM  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

OP = "business/operations/Регистрация.md"
CLOSER = "services/task-service/schedulers/Шедулер закрытия регулярных задач.md"
EP = "services/task-service/endpoints/task-service GET api-v1-tasks.md"
SCREEN = "ui/UI регистрации.md"
CONSUMER = "services/task-service/kafka/Обработка ответа УПЗ о создании задачи.md"
TOPIC = "topics/pprbkid.task.closed.v1.md"
FIXTURE_HOLES = {"W-op-no-creator": 2, "W-topic-side": 1}

_healthy = {}


def setUpModule():
    tmp = tempfile.mkdtemp()
    root = K.make_project(Path(tmp) / "repo")
    K.through_review(root)
    K.approve_edges(root)
    _healthy.update(tmp=tmp, root=root)


def tearDownModule():
    shutil.rmtree(_healthy["tmp"], ignore_errors=True)


class Rules(unittest.TestCase):
    def test_rules_match_ontology(self):
        self.assertEqual(CM.self_check(CM.load(), O.load()), [])

    def test_broken_rules_are_caught(self):
        base = CM.load()
        for mutate, frag in ((lambda r: r["rules"][0].update(kind="нет-вида"), "не вид онтологии"),
                             (lambda r: r["rules"][0].update(empty_all=["consumes"]), "ключа «consumes» нет"),
                             (lambda r: r["rules"][0].update(severity="error"), "severity"),
                             (lambda r: r["rules"][0].update(empty_any=["closed_by"]), "ровно одно"),
                             (lambda r: r["rules"][0].update(question="без имени"), "{name}"),
                             (lambda r: r["rules"][-1].update(role="publishx"), "роли"),
                             (lambda r: r.update(group_by="x"), "group_by")):
            rules = copy.deepcopy(base)
            mutate(rules)
            errs = CM.self_check(rules, O.load())
            self.assertTrue(any(frag in e for e in errs), (frag, errs))


    def test_check_refuses_broken_rules(self):
        broken = copy.deepcopy(CM.load())
        broken["rules"][0]["kind"] = "нет-вида"
        orig = CM.load
        CM.load = lambda: broken
        try:
            with self.assertRaises(O.OntologyError):
                CM.check(Path(tempfile.gettempdir()), {})
        finally:
            CM.load = orig


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(_healthy["root"], self.root, symlinks=True)
        self.vault = self.root / "docs" / "graph"

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, rel, **fm):
        p = self.vault / rel
        data, body = F.read(SP.read_note(p))
        data.update(fm)
        p.write_bytes(F.write(data, body).encode("utf-8"))

    def run_check(self):
        return T.call("graph_validate", self.root, {})

    def holes(self):
        return {k: v for k, v in self.run_check()["counts"].items() if k in {r["id"] for r in CM.load()["rules"]}}


class Fixture(_Base):
    def test_only_own_holes(self):
        self.assertEqual(self.holes(), FIXTURE_HOLES)
        text = (self.vault / "_graph" / "report.md").read_text(encoding="utf-8")
        sec = text[text.index("## Дыры графа"):]
        self.assertIn("### [[Регистрация]]\n\n- W-op-no-creator [[Регистрация]]: пусто: created_by", sec)
        self.assertIn("### Без связи с бизнес-операцией\n\n- W-topic-side [[pprbkid.task.closed.v1]]: пусто: consumers", sec)

    def test_questions_idempotent(self):
        p = self.vault / "_graph" / CM.QUESTIONS_NAME
        self.run_check()
        first = p.read_text(encoding="utf-8")
        self.run_check()
        self.assertEqual(p.read_text(encoding="utf-8"), first)
        rows = [x for x in first.split("\n") if x.startswith("- [[")]
        self.assertEqual(len(rows), len(set(rows)))
        self.assertIn("Кто формирует задачу «Регистрация»?", first)

    def test_link_questions_included(self):
        qs = [{"note": "Регистрация", "edge": "card", "target": "", "reason": "у бизнес-операции нет описания", "quote": ""},
              {"note": "UI регистрации", "edge": "calls", "target": "rmoc GET x", "reason": "нет такого узла", "quote": "q"}]
        (self.root / CM.LINK_QUESTIONS_REL).write_text(json.dumps(qs + qs, ensure_ascii=False), encoding="utf-8")
        self.run_check()
        text = (self.vault / "_graph" / CM.QUESTIONS_NAME).read_text(encoding="utf-8")
        self.assertEqual(text.count("у бизнес-операции нет описания"), 1)
        self.assertIn("- [[UI регистрации]] — calls → «rmoc GET x»: нет такого узла", text)


class EachRule(_Base):
    def assertHole(self, code, rel=None, flag=None, **fm):
        if rel:
            self.edit(rel, **fm)
        self.assertIn(code, self.holes())
        if flag:
            self.edit(rel, **{flag: True})
            self.assertNotIn(code, self.holes(), f"{flag}: true глушит {code}")

    def test_op_no_closer(self):
        self.assertHole("W-op-no-closer", OP, closed_by=[])
        text = (self.vault / "_graph" / "report.md").read_text(encoding="utf-8")
        self.assertIn("- W-op-no-closer [[Регистрация]]: пусто: closed_by", text[text.index("### [[Регистрация]]"):])

    def test_op_no_ui(self):
        self.assertHole("W-op-no-ui", OP, flag="system", screens=[])

    def test_sched_idle(self):
        self.assertHole("W-sched-idle", CLOSER, closes_task=[], invokes=[])
        self.assertEqual(self.holes()["W-sched-idle"], 1)

    def test_ep_no_caller(self):
        self.assertHole("W-ep-no-caller", EP, flag="public", called_by=[])

    def test_topic_side(self):
        self.assertHole("W-topic-side", TOPIC, flag="external_side")

    def test_screen_no_op(self):
        self.assertHole("W-screen-no-op", SCREEN, ui_of=[])

    def test_consume_no_topic_is_error(self):
        self.edit(CONSUMER, consumes=[])
        res = self.run_check()
        self.assertIn("E-consume-no-topic", res["counts"])
        self.assertGreaterEqual(res["errors"], 1)


if __name__ == "__main__":
    unittest.main()
