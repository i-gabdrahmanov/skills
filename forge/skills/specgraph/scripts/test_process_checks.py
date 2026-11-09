#!/usr/bin/env python3
"""Проверки процесса целиком (2026-11-09, вслед за отзывом аналитика): цепочка «вход → шаги → результат».

Правила полноты смотрели на узел по одному; цепочку целиком не проверяло ничего. Пинит:
  • W-service-no-entry — у сервиса нет ни эндпойнта, ни обработчика сообщений, ни шедулера (Hybrid Mail,
    где чтение топика описано внутри алгоритма); отправка в Kafka — не вход; флаг passive глушит;
  • W-step-unordered — у узла два и больше шагов, и часть не упомянута в его тексте (порядок угадан);
    один шаг — порядок не важен, вопроса нет;
  • W-step-cycle — алгоритмы запускают друг друга по кругу: один вопрос на цикл, «Ход обработки» не виснет;
  • W-algo-unreachable — алгоритм запускают другие алгоритмы, но ни один вход до них не доходит (в том
    числе цикл без входа); дошедший через цепочку — молчит;
  • связи-догадки — «запускает» только по имени в тексте, ещё в свойствах, — в «Сомнительных связях» отчёта
    связей (до «да») и постоянным списком в report.md (с экранированным `|`); общее «да» его не очищает,
    отклонённое — уходит;
  • сломанное правило процесса ловит self_check.
"""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import completeness as CM  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import overview as OV  # noqa: E402
from sgraph import validate as VA  # noqa: E402

SVC = "mail-service"


def _node(name, kind, role=None, text="", **edges):
    data = {"kind": kind}
    if role:
        data["role"] = role
    if kind != "service" and O.load().owned_by_service(kind):
        edges.setdefault("belongs_to", [SVC])
    for k, v in edges.items():
        data[k] = [F.link(t) for t in v]
    body = F.set_block(f"# {name}\n", "source", text or f"Описание узла {name}.", attrs={"file": "spec.md"})
    owner = edges.get("belongs_to", [None])[0]
    return {"name": name, "kind": kind, "role": role, "service": owner, "path": f"{name}.md", "data": data, "body": body}


def _graph(*nodes):
    """Узлы, как их пишет синк: с сервисом и обратными ключами (invoked_by, components…)."""
    out = {n["name"]: n for n in nodes}
    out.setdefault(SVC, _node(SVC, "service"))
    ont = O.load()
    for n in list(out.values()):
        for edge, spec in ont.edges.items():
            for t in n["data"].get(edge, []):
                inv = out[F.link_target(t)]["data"].setdefault(spec["inverse"], [])
                if F.link(n["name"]) not in inv:
                    inv.append(F.link(n["name"]))
    return out


class _Holes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def holes(self, nodes, code):
        return [h for h in CM.check(self.root, nodes) if h["code"] == code]


class ServiceNoEntry(_Holes):
    def test_only_algorithms_is_a_hole(self):
        g = _graph(_node("Алгоритм отправки письма", "algorithm"))
        hs = self.holes(g, "W-service-no-entry")
        self.assertEqual([h["name"] for h in hs], [SVC])
        self.assertIn("Через что сервис", hs[0]["question"])

    def test_publish_is_not_an_entry(self):
        g = _graph(_node("Отправка статуса письма", "kafka-op", "publish"))
        self.assertEqual(len(self.holes(g, "W-service-no-entry")), 1, "отправка в топик работу сервису не приносит")

    def test_consumer_is_an_entry(self):
        g = _graph(_node("Обработка сообщений mail.request.v1", "kafka-op", "consume"))
        self.assertEqual(self.holes(g, "W-service-no-entry"), [])

    def test_passive_flag(self):
        g = _graph(_node("Алгоритм отправки письма", "algorithm"))
        g[SVC]["data"]["passive"] = True
        self.assertEqual(self.holes(g, "W-service-no-entry"), [])


class StepOrder(_Holes):
    def test_unmentioned_step_among_several(self):
        g = _graph(_node("Обработка сообщений mail.request.v1", "kafka-op", "consume",
                         text="Сначала [[Алгоритм проверки адреса]], потом письмо уходит.",
                         invokes=["Алгоритм проверки адреса", "Алгоритм отправки письма"]),
                   _node("Алгоритм проверки адреса", "algorithm"), _node("Алгоритм отправки письма", "algorithm"))
        hs = self.holes(g, "W-step-unordered")
        self.assertEqual([h["name"] for h in hs], ["Обработка сообщений mail.request.v1"])
        self.assertIn(F.link("Алгоритм отправки письма"), hs[0]["msg"])
        self.assertNotIn(F.link("Алгоритм проверки адреса"), hs[0]["msg"])

    def test_single_step_needs_no_order(self):
        g = _graph(_node("Обработка сообщений mail.request.v1", "kafka-op", "consume", invokes=["Алгоритм отправки письма"]),
                   _node("Алгоритм отправки письма", "algorithm"))
        self.assertEqual(self.holes(g, "W-step-unordered"), [])

    def test_all_mentioned_is_quiet(self):
        g = _graph(_node("Обработка сообщений mail.request.v1", "kafka-op", "consume",
                         text="Алгоритм проверки адреса, затем алгоритм отправки письма.",
                         invokes=["Алгоритм проверки адреса", "Алгоритм отправки письма"]),
                   _node("Алгоритм проверки адреса", "algorithm"), _node("Алгоритм отправки письма", "algorithm"))
        self.assertEqual(self.holes(g, "W-step-unordered"), [])


class Cycles(_Holes):
    def g(self):
        return _graph(_node("Обработка сообщений mail.request.v1", "kafka-op", "consume", invokes=["Алгоритм Б"]),
                      _node("Алгоритм Б", "algorithm", invokes=["Алгоритм В"]),
                      _node("Алгоритм В", "algorithm", invokes=["Алгоритм Б"]))

    def test_one_question_per_cycle(self):
        hs = self.holes(self.g(), "W-step-cycle")
        self.assertEqual([h["name"] for h in hs], ["Алгоритм Б"])
        self.assertIn(f"{F.link('Алгоритм Б')} → {F.link('Алгоритм В')} → {F.link('Алгоритм Б')}", hs[0]["msg"])

    def test_self_loop(self):
        g = _graph(_node("Шедулер повтора", "scheduler", invokes=["Алгоритм повтора"]),
                   _node("Алгоритм повтора", "algorithm", invokes=["Алгоритм повтора"]))
        self.assertEqual([h["name"] for h in self.holes(g, "W-step-cycle")], ["Алгоритм повтора"])

    def test_cycle_reachable_from_entry_is_not_unreachable(self):
        self.assertEqual(self.holes(self.g(), "W-algo-unreachable"), [])

    def test_flow_blocks_terminate(self):
        g = self.g()
        v = OV.from_notes(O.load(), g)
        for name in g:
            OV.flow(v, name)
        self.assertIn("цикл", OV.flow(v, "Обработка сообщений mail.request.v1"))


class Unreachable(_Holes):
    def test_chain_without_entry(self):
        g = _graph(_node("Алгоритм В", "algorithm", invokes=["Алгоритм Г"]), _node("Алгоритм Г", "algorithm"))
        self.assertEqual([h["name"] for h in self.holes(g, "W-algo-unreachable")], ["Алгоритм Г"])
        self.assertEqual([h["name"] for h in self.holes(g, "W-algo-no-trigger")], ["Алгоритм В"],
                         "голова цепочки — свой вопрос, без дубля")

    def test_cycle_without_entry(self):
        g = _graph(_node("Алгоритм Д", "algorithm", invokes=["Алгоритм Е"]),
                   _node("Алгоритм Е", "algorithm", invokes=["Алгоритм Д"]))
        self.assertEqual(sorted(h["name"] for h in self.holes(g, "W-algo-unreachable")), ["Алгоритм Д", "Алгоритм Е"])

    def test_deep_chain_from_entry(self):
        names = [f"Алгоритм шага {i}" for i in range(9)]
        nodes = [_node(n, "algorithm", invokes=[names[i + 1]] if i + 1 < len(names) else []) for i, n in enumerate(names)]
        g = _graph(_node("Шедулер рассылки", "scheduler", invokes=[names[0]]), *nodes)
        self.assertEqual(self.holes(g, "W-algo-unreachable"), [], "глубина цепочки — не повод")

    def test_publish_op_does_not_count_as_entry(self):
        g = _graph(_node("Отправка статуса письма", "kafka-op", "publish", invokes=["Алгоритм Ж"]),
                   _node("Алгоритм Ж", "algorithm"))
        self.assertEqual([h["name"] for h in self.holes(g, "W-algo-unreachable")], ["Алгоритм Ж"])


class Guesses(_Holes):
    def test_name_mentions_still_in_properties(self):
        src = "Обработка сообщений mail.request.v1"
        g = _graph(_node(src, "kafka-op", "consume", invokes=["Алгоритм А", "Алгоритм Б", "Алгоритм В"]),
                   _node("Алгоритм А", "algorithm"), _node("Алгоритм Б", "algorithm"), _node("Алгоритм В", "algorithm"),
                   _node("Алгоритм Г", "algorithm"))

        def p(to, **kw):
            base = {"from": src, "edge": "invokes", "to": to, "quote": "в отличие от | алгоритма " + to,
                    "origin": "rule:R-edge-mention", "confidence": "medium", "status": "proposed"}
            return dict(base, **kw)
        E.save(self.root, src, [p("Алгоритм А"), p("Алгоритм Б", confidence="high"), p("Алгоритм В", status="rejected"),
                                p("Алгоритм Г")])
        got = VA.guesses(self.root, g)
        self.assertEqual([x["to"] for x in got], ["Алгоритм А"],
                         "ссылка [[…]] (high), отклонённое и снятое из свойств (Г) — не догадки")
        E.save(self.root, src, [p("Алгоритм А", status="accepted")])
        self.assertEqual([x["to"] for x in VA.guesses(self.root, g)], ["Алгоритм А"],
                         "общее «да» отчёту связей — не проверка каждой: догадка остаётся в списке")
        report = VA._render_report({"notes": 1, "errors": 0, "warnings": 0, "counts": {}, "scope": "", "issues": [],
                                    "transfer": [], "guesses": got})
        self.assertIn("## Связи-догадки: проверьте", report)
        self.assertIn("в отличие от \\| алгоритма Алгоритм А", report, "`|` цитаты не ломает таблицу")
        self.assertIn("| запускает |", report)


class FixtureGuesses(unittest.TestCase):
    """Фикстура: «Запустить «Алгоритм балансировки упаковок»» и «запускается отправка события о закрытии» —
    «запускает» только по имени. Видно до «да» (раздел 3 отчёта связей) и после (report.md)."""

    @classmethod
    def setUpClass(cls):
        import _testkit as K
        from sgraph import tools as T
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = K.make_project(Path(cls.tmp.name) / "repo")
        K.through_review(cls.root)
        cls.review = (cls.root / "docs" / "graph" / "_graph" / "edges-review.md").read_text(encoding="utf-8")
        T.call("graph_obsidian", cls.root, {})
        cls.check = T.call("graph_check", cls.root, {})
        cls.report = (cls.root / "docs" / "graph" / "_graph" / "report.md").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_doubtful_before_yes(self):
        sec = self.review[self.review.index("## 3."):self.review.index("## 4.")]
        self.assertIn(f"{F.link('Шедулер формирования задачи передачи упаковок в СЦ-архив')} — запускает → "
                      f"{F.link('Алгоритм балансировки упаковок')}", sec)

    def test_listed_after_check(self):
        self.assertEqual(self.check["guesses"], 2)
        self.assertIn(f"| {F.link('Шедулер закрытия регулярных задач')} | запускает | "
                      f"{F.link('Отправка события о закрытии задачи')} |", self.report)


class RuleSelfCheck(unittest.TestCase):
    def test_broken_process_rules(self):
        base = CM.load()
        for patch, frag in (({"check": "nope"}, "проверки «nope» нет"),
                            ({"kind": "algorithm"}, "у правила с check нет kind")):
            rules = copy.deepcopy(base)
            r = next(x for x in rules["rules"] if x.get("check"))
            r.update(patch)
            with self.subTest(patch=patch):
                self.assertTrue(any(frag in e for e in CM.self_check(rules, O.load())))
        rules = copy.deepcopy(base)
        next(x for x in rules["rules"] if x.get("targets"))["targets"].append({"kind": "kafka-op", "role": "listen"})
        self.assertTrue(any("роли «listen»" in e for e in CM.self_check(rules, O.load())))

    def test_shipped_rules_clean(self):
        self.assertEqual(CM.self_check(CM.load(), O.load()), [])


if __name__ == "__main__":
    unittest.main()
