#!/usr/bin/env python3
"""test_traversal.py — инструменты обхода графа агентом (задача 3.4).

Пинит:
  • каждый переход `walks` эталона (ТЗ §10): каждый шаг — в graph_neighbors по ключу шага; если переход —
    кратчайший путь, graph_path возвращает его цепочку ровно; принадлежность сервису не срезает путь;
  • graph_impact от общего шедулера закрытия — три операции и их экраны, больше ничего; от эндпойнта — вверх
    через вызывающих до операций;
  • graph_service_links — вызовы и топик между сервисами; graph_find — ключ, алиас, порядок, потолок 20;
    graph_node — рёбра по ключам и часть тела с обрезкой; неизвестное имя — отказ с кандидатами;
  • кэш индекса: повтор без чтения заметок, правка заметки — пересборка;
  • синтетический vault в 1000 заметок: индекс быстрее 1 с, потолки выдачи соблюдаются;
  • в traverse.py нет имён видов и рёбер.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import tools as T  # noqa: E402
from sgraph import traverse as TR  # noqa: E402

CLOSER = "Шедулер закрытия регулярных задач"
WALKS = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))["walks"]
_healthy = {}


def setUpModule():
    tmp = tempfile.mkdtemp()
    root = K.make_project(Path(tmp) / "repo")
    K.through_review(root)
    K.approve_edges(root)
    _healthy.update(tmp=tmp, root=root)


def tearDownModule():
    shutil.rmtree(_healthy["tmp"], ignore_errors=True)


def call(tool, **args):
    return T.call(tool, _healthy["root"], args)


class Walks(unittest.TestCase):
    def test_every_step_is_a_neighbor(self):
        for w in WALKS:
            p = w["path"]
            with self.subTest(w["tz"]):
                for i in range(0, len(p) - 2, 2):
                    res = call("graph_neighbors", name=p[i], edges=[p[i + 1]], limit=50)
                    self.assertIn(p[i + 2], [s["to"] for s in res["steps"] if s["key"] == p[i + 1]], f"{p[i]} —{p[i + 1]}→ ?")

    def test_shortest_walks_returned_exactly(self):
        exact = 0
        for w in WALKS:
            p = w["path"]
            with self.subTest(w["tz"]):
                res = call("graph_path", **{"from": p[0], "to": p[-1]})
                self.assertTrue(res["paths"])
                self.assertLessEqual(len(res["paths"][0]), len(p))
                if len(res["paths"][0]) == len(p):
                    self.assertIn(p, res["paths"])
                    exact += 1
        self.assertEqual(exact, 6, "все переходы, кроме длинного повествовательного §10 абзац 2, — кратчайшие")

    def test_service_does_not_shortcut(self):
        res = call("graph_path", **{"from": "Шедулер формирования задачи укомплектования",
                                    "to": "Обработка ответа УПЗ о создании задачи"})
        self.assertNotIn("belongs_to", res["paths"][0])
        self.assertEqual(res["paths"][0][1::2], ["calls", "publishes_to", "consumers"])
        self.assertIn("—calls→", res["lines"][0])
        far = call("graph_path", **{"from": "UI регистрации", "to": "pprbkid.task.closed.v1", "max_depth": 2})
        self.assertEqual(far["paths"], [])


class Impact(unittest.TestCase):
    def test_closer_hits_three_operations_and_their_screens(self):
        res = call("graph_impact", name=CLOSER)
        self.assertEqual(res["affected"], {
            "operation": ["Приём транспортных упаковок", "Регистрация", "Укомплектование"],
            "screen": ["UI приёма транспортных упаковок", "UI регистрации", "UI укомплектования"]})
        self.assertIn("[[Регистрация]] (operation) ← [[Шедулер закрытия регулярных задач]] —closes_task→", res["lines"])

    def test_endpoint_goes_up_through_callers(self):
        res = call("graph_impact", name="task-service GET api-v1-tasks")
        self.assertEqual(res["affected"]["screen"], ["UI приёма транспортных упаковок", "UI регистрации", "UI укомплектования"])
        self.assertEqual(res["affected"]["operation"], ["Приём транспортных упаковок", "Регистрация", "Укомплектование"])
        self.assertNotIn("service", res["affected"], "принадлежность не распространяет")


class Lookup(unittest.TestCase):
    def test_service_links(self):
        lines = call("graph_service_links", svc="task-service")["lines"]
        self.assertTrue(any(x.startswith("[[task-service]] → [[upz-adapter]]: ") for x in lines))
        self.assertIn("[[upz-adapter]] → [[task-service]] через [[pprbkid.upz.task.response.v1]] (topic): "
                      "[[upz-adapter POST api-v1-upz-tasks]] —publishes_to→ … ←consumes— [[Обработка ответа УПЗ о создании задачи]]",
                      lines)
        for x in lines:
            a, b = re.match(r"^(\[\[[^\]]+\]\]) → (\[\[[^\]]+\]\])", x).groups()
            self.assertNotEqual(a, b, "связь внутри сервиса — не межсервисная")
        with self.assertRaises(T.ToolError) as cm:
            call("graph_service_links", svc="Регистрация")
        self.assertEqual(cm.exception.codes, ["E-arg-svc"])

    def test_symmetric_neighbor_once(self):
        lines = call("graph_neighbors", name="Укомплектование", edges=["related"])["lines"]
        self.assertEqual(len(lines), 1, lines)

    def test_find_exact_alias_beats_shorter_prefix(self):
        ont = O.load()
        idx = TR.Index(ont, {"Очень длинное имя операции": {"kind": "operation", "aliases": ["Рег"], "key": None},
                             "Регистрация": {"kind": "operation", "aliases": [], "key": None}}, [])
        self.assertEqual(TR.find(idx, "Рег")["hits"][0]["name"], "Очень длинное имя операции")

    def test_find(self):
        self.assertEqual(call("graph_find", q="task:REGISTRATION")["lines"], ["[[Регистрация]] (operation) key=task:REGISTRATION"])
        res = call("graph_find", q="шедулер", kind="scheduler")
        self.assertEqual(len(res["hits"]), 4)
        self.assertEqual(call("graph_find", q="Регистрация")["hits"][0]["name"], "Регистрация", "точное совпадение первым")
        self.assertLessEqual(len(call("graph_find", q="а", limit=500)["hits"]), 20)
        with self.assertRaises(T.ToolError) as cm:
            call("graph_find", q="x", kind="нет")
        self.assertEqual(cm.exception.codes, ["E-arg-kind"])

    def test_node(self):
        res = call("graph_node", name="Регистрация", body="card")
        self.assertEqual(res["lines"], ["[[Регистрация]] (operation)",
                                        "- closed_by (← закрывает задачу): [[Шедулер закрытия регулярных задач]]",
                                        "- screens (← UI задачи): [[UI регистрации]]"])
        self.assertIn("**Для чего:**", res["body"])
        ev = call("graph_node", name=CLOSER, body="evidence", max_chars=80)["body"]
        self.assertIn("…(обрезано", ev)
        self.assertLessEqual(len(ev.split("\n…")[0]), 80)
        self.assertEqual(call("graph_node", name="task:KOMPLEKT")["name"], "Укомплектование", "имя по ключу")

    def test_unknown_name(self):
        with self.assertRaises(T.ToolError) as cm:
            call("graph_node", name="Регистрац")
        self.assertEqual(cm.exception.codes, ["E-node-unknown"])
        self.assertIn("Регистрация", str(cm.exception))

    def test_neighbors_filters(self):
        out = call("graph_neighbors", name="Регистрация", dir="out")["lines"]
        self.assertEqual(out, [])
        inc = call("graph_neighbors", name="Регистрация", edges=["closed_by"])["lines"]
        self.assertEqual(inc, ["[[Шедулер закрытия регулярных задач]] (scheduler, task-service) —closes_task→ [[Регистрация]] (operation)"])
        res = call("graph_neighbors", name=CLOSER, depth=10, limit=5)
        self.assertTrue(res["clipped"])
        self.assertEqual(len(res["lines"]), 5)
        self.assertLessEqual(max(s["depth"] for s in res["steps"]), TR.DEPTH_MAX)


class Cache(unittest.TestCase):
    def test_cache_and_invalidation(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name) / "repo"
        shutil.copytree(_healthy["root"], root, symlinks=True)
        cfg = T._need_cfg(root)
        TR.build_index(root, cfg)
        orig = F.read
        F.read = lambda *a, **k: (_ for _ in ()).throw(AssertionError("кэш не использован"))
        try:
            TR.build_index(root, cfg)
        finally:
            F.read = orig
        p = root / "docs/graph/business/operations/Регистрация.md"
        data, body = F.read(p.read_text(encoding="utf-8"))
        data["related"] = ["[[Укомплектование]]"]
        p.write_text(F.write(data, body), encoding="utf-8")
        idx = TR.build_index(root, cfg)
        self.assertIn(("Регистрация", "related", "Укомплектование"), idx.edges)
        self.assertIn(("Укомплектование", "related", "Регистрация"), idx.edges, "симметричное — в обе стороны, даже с одной")
        tmp.cleanup()


class Large(unittest.TestCase):
    def test_thousand_notes(self):
        tmp = tempfile.TemporaryDirectory()
        root = K.make_project(Path(tmp.name) / "repo")
        vault = root / "docs" / "graph"
        ont = O.load()

        def note(rel, data):
            p = vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(F.write(data, f"# {p.stem}\n", ont.fm_keys(data["kind"])), encoding="utf-8")
        note("services/svc/svc.md", {"kind": "service"})
        for i in range(500):
            note(f"business/operations/Операция {i}.md", {"kind": "operation", "key": f"task:T{i}"})
            note(f"services/svc/schedulers/Шедулер {i}.md",
                 {"kind": "scheduler", "belongs_to": ["[[svc]]"], "closes_task": [f"[[Операция {i}]]", "[[Операция 0]]"]})
        t0 = time.perf_counter()
        idx = TR.build_index(root, T._need_cfg(root), use_cache=False)
        took = time.perf_counter() - t0
        self.assertEqual(len(idx.nodes), 1001)
        self.assertLess(took, 1.0, f"индекс 1000 заметок — {took:.2f} с")
        res = T.call("graph_neighbors", root, {"name": "Операция 0", "limit": 1000})
        self.assertEqual(len(res["lines"]), TR.LIMIT_MAX)
        self.assertEqual(res["total"], 500)
        self.assertLessEqual(len(T.call("graph_impact", root, {"name": "svc", "limit": 999})["lines"]), TR.LIMIT_MAX)
        self.assertEqual(len(T.call("graph_find", root, {"q": "Операция"})["hits"]), 20)
        many = T.call("graph_path", root, {"from": "Операция 0", "to": "svc"})
        self.assertEqual(len(many["paths"]), TR.PATHS_MAX, "500 кратчайших путей — в ответе не больше потолка")
        tmp.cleanup()


class NoLiterals(unittest.TestCase):
    def test_no_kind_or_edge_literals(self):
        ont = O.load()
        names = sorted(set(ont.kinds) | set(ont.edges) | {e["inverse"] for e in ont.edges.values()}, key=len, reverse=True)
        rx = re.compile(r"""["'](%s)["']""" % "|".join(re.escape(n) for n in names))
        code = re.sub(r'"""[\s\S]*?"""', "", (SCRIPTS / "sgraph" / "traverse.py").read_text(encoding="utf-8"))
        code = re.sub(r"""(\[\s*|\.get\(\s*|[{,]\s*)["'][\w-]+["'](\s*[\]:,)])""", "", code)
        self.assertEqual([m.group(0) for m in rx.finditer(code)], [])


if __name__ == "__main__":
    unittest.main()
