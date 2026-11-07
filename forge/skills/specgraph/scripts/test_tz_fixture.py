#!/usr/bin/env python3
"""test_tz_fixture.py — синтетическая фикстура по ТЗ и эталонный граф (задача 0.4).

Пинит самосогласованность эталона `fixtures/tz-mini/expected/graph.json` с онтологией и исходниками:
  • каждый узел — вид онтологии, имя по грамматике, путь вычисляется; источник существует;
  • каждое ребро допустимо онтологией для своих концов (с ролями и same_service); цитата-основание
    дословно (с нормализацией пробелов) есть в указанном файле; концы — узлы эталона;
  • каждый переход `walks` (ТЗ §10) — цепочка рёбер эталона, прямых или обратных;
  • ловушки ТЗ: общий шедулер закрытия — один узел с тремя closes_task; похожий шедулер передачи —
    отдельный; «См. также» рёбер не даёт; черновик исключён и его текста нет ни в одной цитате;
  • пример декомпозиции ТЗ §1: у task-service четыре названных шедулера, Kafka-операции и эндпойнты.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import ontology as O  # noqa: E402

FIX = SCRIPTS.parent / "fixtures" / "tz-mini"
SRC = FIX / "sources"
G = json.loads((FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
NODES = {n["name"]: n for n in G["nodes"]}
ONT = O.load()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _owner(name: str):
    for e in G["edges"]:
        if e["from"] == name and e["edge"] == "belongs_to":
            return e["to"]
    return None


def _has(src: str, key: str, dst: str) -> bool:
    edge = key if key in ONT.edges else ONT.edge_of_inverse(key)
    if edge is None:
        return False
    if key in ONT.edges and not ONT.is_symmetric(key):
        pairs = [(src, dst)]
    elif ONT.is_symmetric(edge):
        pairs = [(src, dst), (dst, src)]
    else:
        pairs = [(dst, src)]
    return any(e["edge"] == edge and (e["from"], e["to"]) in pairs for e in G["edges"])


class Nodes(unittest.TestCase):
    def test_unique_names_and_known_kinds(self):
        self.assertEqual(len(NODES), len(G["nodes"]), "имена узлов уникальны во всём vault")
        for n in G["nodes"]:
            with self.subTest(n["name"]):
                self.assertIn(n["kind"], ONT.kinds)
                self.assertEqual(ONT.check_name(n["kind"], n["name"], n.get("service")), [])
                path = ONT.path_for(n["kind"], n["name"], n.get("service"), n.get("subkind"))
                self.assertTrue(path.endswith(".md"))
                file = n["source"].split("#", 1)[0]
                self.assertTrue((SRC / file).is_file(), file)
                if ONT.kinds[n["kind"]].get("roles"):
                    self.assertIn(n.get("role"), ONT.kinds[n["kind"]]["roles"])
                if ONT.kinds[n["kind"]]["owner"] == "service":
                    self.assertEqual(_owner(n["name"]), n["service"], "владелец = belongs_to")

    def test_every_source_except_draft_gives_nodes(self):
        files = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.md")}
        used = {n["source"].split("#", 1)[0] for n in G["nodes"]}
        self.assertEqual(files - used, set(G["exclude"]))


class Edges(unittest.TestCase):
    def test_allowed_and_grounded(self):
        for e in G["edges"]:
            with self.subTest(f"{e['from']} {e['edge']} {e['to']}"):
                self.assertIn(e["from"], NODES)
                self.assertIn(e["to"], NODES)
                src, dst = dict(NODES[e["from"]]), dict(NODES[e["to"]])
                ok, why = ONT.edge_allowed(e["edge"], src, dst)
                self.assertTrue(ok, why)
                if e["edge"] != "belongs_to":
                    text = (SRC / e["file"]).read_text(encoding="utf-8")
                    self.assertIn(_norm(e["quote"]), _norm(text), "цитата-основание дословно в исходнике")
                    self.assertGreaterEqual(len(_norm(e["quote"])), 20)
                self.assertIsInstance(e["mechanical"], bool)

    def test_no_duplicate_edges(self):
        keys = [(e["from"], e["edge"], e["to"]) for e in G["edges"]]
        self.assertEqual(len(keys), len(set(keys)))


class Walks(unittest.TestCase):
    def test_tz_section_10_walks(self):
        for w in G["walks"]:
            p = w["path"]
            with self.subTest(w["tz"]):
                self.assertEqual(len(p) % 2, 1)
                for i in range(0, len(p) - 2, 2):
                    self.assertTrue(_has(p[i], p[i + 1], p[i + 2]), f"нет перехода {p[i]} —{p[i + 1]}→ {p[i + 2]}")


class Traps(unittest.TestCase):
    def test_shared_closer_is_one_node_with_three_operations(self):
        closes = sorted(e["to"] for e in G["edges"]
                        if e["from"] == "Шедулер закрытия регулярных задач" and e["edge"] == "closes_task")
        self.assertEqual(closes, ["Приём транспортных упаковок", "Регистрация", "Укомплектование"])
        self.assertEqual(sum(1 for n in G["nodes"] if "закрытия регулярных" in n["name"]), 1)

    def test_transfer_has_its_own_schedulers(self):
        mine = {e["from"] for e in G["edges"] if e["to"] == "Передача упаковок в СЦ-архив"
                and e["edge"] in ("creates_task", "closes_task")}
        self.assertEqual(mine, {"Шедулер формирования задачи передачи упаковок в СЦ-архив",
                                "Шедулер закрытия задачи передачи упаковок в СЦ-архив"})
        shared = {e["to"] for e in G["edges"] if e["from"] in mine and e["edge"] == "closes_task"}
        self.assertEqual(shared, {"Передача упаковок в СЦ-архив"}, "шедулеры передачи не общие")

    def test_see_also_gives_no_edges(self):
        text = (SRC / "task-service" / "task-service.md").read_text(encoding="utf-8")
        see_also = _norm(text.split("## 7. См. также", 1)[1])
        for e in G["edges"]:
            if e.get("quote"):
                self.assertNotIn(_norm(e["quote"]), see_also)
        services = {"task-service", "rmoc-service", "upz-adapter"}
        self.assertFalse([e for e in G["edges"] if e["from"] in services], "у сервиса нет прямых рёбер")

    def test_draft_is_excluded(self):
        draft = (SRC / "task-service" / "task-service-draft.md").read_text(encoding="utf-8")
        self.assertIn("22:00", draft)
        self.assertFalse([e for e in G["edges"] if "22:00" in (e.get("quote") or "")])

    def test_tz_decomposition_example(self):
        schedulers = {n["name"] for n in G["nodes"] if n["kind"] == "scheduler" and n.get("service") == "task-service"}
        self.assertEqual(len(schedulers), 4)
        kinds = {n["kind"] for n in G["nodes"] if n.get("service") == "task-service"}
        self.assertEqual(kinds, {"scheduler", "kafka-op", "endpoint", "algorithm"})


if __name__ == "__main__":
    unittest.main()
