#!/usr/bin/env python3
"""test_obsidian_layer.py — Obsidian-слой графа (задача 3.3).

Пинит:
  • graph.json: слияние сохраняет чужие ключи и группы пользователя; наш запрос с цветом пользователя — цвет
    остаётся; наша устаревшая группа снимается; повтор — 0 изменений; не-JSON — отказ, файл не тронут;
  • режим по факту: нет core-plugins.json — Markdown-виды; bases включён (список или объект) — .base, а
    Markdown-виды прежнего режима сняты;
  • Начало.md и виды детерминированы (две сборки — одинаковые байты); счётчики и легенда из онтологии;
  • оглавление сервиса (graph:toc) — операции по видам;
  • слой — данные: obsidian.json сверяется с онтологией; в obsidian.py нет имён видов и рёбер.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402

G = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
from sgraph import fm as F  # noqa: E402
from sgraph import obsidian as OB  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

_healthy = {}
OPS_QUERY = 'path:"business/operations/"'


def setUpModule():
    tmp = tempfile.mkdtemp()
    root = K.make_project(Path(tmp) / "repo")
    K.through_review(root)
    K.approve_edges(root)
    _healthy.update(tmp=tmp, root=root)


def tearDownModule():
    shutil.rmtree(_healthy["tmp"], ignore_errors=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(_healthy["root"], self.root, symlinks=True)
        self.vault = self.root / "docs" / "graph"
        self.obs = self.vault / ".obsidian"

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        return T.call("graph_obsidian", self.root, {})

    def graph_json(self):
        return json.loads((self.obs / "graph.json").read_text(encoding="utf-8"))


class GraphJson(_Base):
    def test_created_and_idempotent(self):
        res = self.build()
        self.assertEqual(res["mode"], "markdown")
        groups = self.graph_json()["colorGroups"]
        self.assertEqual(len(groups), len(O.load().kinds) + 1)
        queries = [g["query"] for g in groups]
        self.assertEqual(queries[0], "[status:stub]", "заглушка — первой группой: серая при любом виде")
        self.assertLess(queries.index('path:"services/" path:"/schedulers/"'), queries.index('path:"services/"'),
                        "узкий запрос раньше широкого: первая подходящая группа красит узел")
        self.assertEqual(self.build()["changed"], [])

    def test_merge_keeps_user_settings(self):
        self.obs.mkdir(parents=True, exist_ok=True)
        user = {"showTags": True, "scale": 1.3,
                "colorGroups": [{"query": "tag:#важно", "color": {"a": 1, "rgb": 255}},
                                {"query": OPS_QUERY, "color": {"a": 1, "rgb": 123}}]}
        (self.obs / "graph.json").write_text(json.dumps(user, ensure_ascii=False), encoding="utf-8")
        self.build()
        data = self.graph_json()
        self.assertEqual((data["showTags"], data["scale"]), (True, 1.3))
        self.assertEqual(data["colorGroups"][-1], user["colorGroups"][0], "группа пользователя — после наших")
        ops = next(g for g in data["colorGroups"] if g["query"] == OPS_QUERY)
        self.assertEqual(ops["color"]["rgb"], 123, "цвет, который выбрал пользователь, остаётся")
        self.assertEqual(self.build()["changed"], [])

    def test_stale_own_group_removed(self):
        self.build()
        data = self.graph_json()
        data["colorGroups"].insert(0, {"query": 'path:"old-kind/"', "color": {"a": 1, "rgb": 1}})
        (self.obs / "graph.json").write_text(json.dumps(data), encoding="utf-8")
        state_p = self.root / OB.STATE_REL
        state = json.loads(state_p.read_text(encoding="utf-8"))
        state["queries"].append('path:"old-kind/"')
        state_p.write_text(json.dumps(state), encoding="utf-8")
        self.build()
        self.assertNotIn('path:"old-kind/"', [g["query"] for g in self.graph_json()["colorGroups"]])

    def test_broken_json_refused(self):
        self.obs.mkdir(parents=True, exist_ok=True)
        (self.obs / "graph.json").write_text("{не json", encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            self.build()
        self.assertEqual(cm.exception.codes, ["E-obsidian"])
        self.assertEqual((self.obs / "graph.json").read_text(encoding="utf-8"), "{не json")


class Views(_Base):
    def test_markdown_then_bases(self):
        self.build()
        views = self.vault / "_graph" / "views"
        sched = (views / "Шедулеры.md").read_text(encoding="utf-8")
        self.assertIn("## [[task-service]]", sched)
        self.assertIn("| [[Шедулер закрытия регулярных задач]] | [[task-service]] |  | [[Приём транспортных упаковок]], "
                      "[[Регистрация]], [[Укомплектование]] |", sched)
        ops = (views / "Бизнес-операции.md").read_text(encoding="utf-8")
        self.assertIn("← формирует задачу", ops)
        for form in (["graph", "bases"], {"graph": True, "bases": True}):
            with self.subTest(form=type(form).__name__):
                (self.obs / "core-plugins.json").write_text(json.dumps(form), encoding="utf-8")
                self.assertEqual(self.build()["mode"], "bases")
                self.assertEqual(sorted(p.suffix for p in views.iterdir()), [".base"] * 4)
        base = (views / "Шедулеры.base").read_text(encoding="utf-8")
        self.assertIn("'note.kind == \"scheduler\"'", base)
        self.assertIn("      - note.closes_task", base)
        self.assertIn("[[_graph/views/Шедулеры.base|Шедулеры]]", (self.vault / "_graph" / OB.START_NAME).read_text(encoding="utf-8"))
        (self.obs / "core-plugins.json").write_text(json.dumps({"bases": False}), encoding="utf-8")
        self.assertEqual(self.build()["mode"], "markdown")
        self.assertEqual(sorted(p.suffix for p in views.iterdir()), [".md"] * 4)

    def test_start_and_determinism(self):
        self.build()
        first = {p.name: p.read_bytes() for p in (self.vault / "_graph").rglob("*") if p.is_file()}
        self.setUp()
        self.build()
        second = {p.name: p.read_bytes() for p in (self.vault / "_graph").rglob("*") if p.is_file()}
        self.assertEqual(first, second)
        start = first[OB.START_NAME].decode("utf-8")
        self.assertIn("- [[Укомплектование]] — формируют: 1, закрывают: 1, UI: 1", start)
        self.assertIn("- [[Регистрация]] — формируют: 0, закрывают: 1, UI: 1", start)
        self.assertIn("- [[task-service]] — операций: 9", start)
        self.assertIn("| `closes_task` | закрывает задачу | `closed_by` |", start)
        self.assertIn("| Шедулер | #", start)

    def test_broken_layer_refused(self):
        broken = copy.deepcopy(OB.load())
        broken["views"][0]["kind"] = "нет"
        orig, OB.load = OB.load, (lambda: broken)
        try:
            with self.assertRaises(T.ToolError) as cm:
                self.build()
            self.assertEqual(cm.exception.codes, ["E-obsidian"])
        finally:
            OB.load = orig
        self.assertFalse((self.vault / "_graph" / OB.START_NAME).exists())

    def test_service_toc(self):
        _, body = F.read(SP.read_note(self.vault / "services/task-service/task-service.md"))
        toc = F.get_block(body, "toc")["content"]
        self.assertIn("**Шедулер:** [[Шедулер закрытия задачи передачи упаковок в СЦ-архив]], [[Шедулер закрытия регулярных задач]]", toc)


class Data(unittest.TestCase):
    def test_layer_matches_ontology(self):
        self.assertEqual(OB.self_check(OB.load(), O.load()), [])
        for mutate, frag in ((lambda d: d["views"][0].update(kind="нет"), "не вид"),
                             (lambda d: d["views"][1]["columns"].append("consumes"), "ключа «consumes»"),
                             (lambda d: d["entries"][1]["counts"].append("closed_by"), "{key, label}"),
                             (lambda d: d["views"][1].update(file=d["views"][0]["file"]), "уникальные"),
                             (lambda d: d.update(palette=[]), "palette")):
            data = copy.deepcopy(OB.load())
            mutate(data)
            self.assertTrue(any(frag in e for e in OB.self_check(data, O.load())), frag)

    def test_no_kind_or_edge_literals(self):
        ont = O.load()
        names = sorted(set(ont.kinds) | set(ont.edges) | {e["inverse"] for e in ont.edges.values()}, key=len, reverse=True)
        rx = re.compile(r"""["'](%s)["']""" % "|".join(re.escape(n) for n in names))
        code = re.sub(r'"""[\s\S]*?"""', "", (SCRIPTS / "sgraph" / "obsidian.py").read_text(encoding="utf-8"))
        code = re.sub(r"""(\[\s*|\.get\(\s*|[{,]\s*)["'][\w-]+["'](\s*[\]:,)])""", "", code)
        self.assertEqual([m.group(0) for m in rx.finditer(code)], [])


class Maps(_Base):
    """Graph view не красит рёбра и не показывает их тип — карты Canvas: ребро подписано и окрашено."""

    def canvas(self, name):
        return json.loads((self.root / "docs" / "graph" / "_graph" / "maps" / f"{name}.canvas").read_text(encoding="utf-8"))

    def test_operation_map(self):
        res = self.build()
        self.assertEqual(res["maps"], sum(1 for n in G["nodes"] if n["kind"] == "operation"))
        m = self.canvas("Укомплектование")
        x = {n["file"].rsplit("/", 1)[-1][:-3]: n["x"] for n in m["nodes"]}
        self.assertEqual(x["Укомплектование"], 0)
        self.assertGreater(x["Шедулер формирования задачи укомплектования"], 0, "кто формирует — правее операции")
        self.assertGreater(x["upz-adapter POST api-v1-upz-tasks"], x["Шедулер формирования задачи укомплектования"],
                           "что вызывает шедулер — ещё правее")
        self.assertNotIn("task-service", x, "belongs_to на карте не рисуется")
        layer = OB.load()
        colors = OB.edge_colors(O.load(), layer)
        labels = {(e["label"], e["color"]) for e in m["edges"]}
        self.assertIn(("формирует задачу", colors["creates_task"]), labels)
        self.assertIn(("UI задачи", colors["ui_of"]), labels)
        stub = next(n for n in m["nodes"] if n["file"] == "data/table/task.md")
        self.assertEqual(stub["color"], "#%06x" % layer["status_groups"][0]["rgb"], "заглушка серая и на карте")
        start = (self.root / "docs" / "graph" / "_graph" / "Начало.md").read_text(encoding="utf-8")
        self.assertIn("[[_graph/maps/Укомплектование.canvas|карта]]", start)
        self.assertIn("| цвет на картах |", start)
        self.assertEqual(self.build()["changed"], [], "повтор — 0 изменений")

    def test_stale_map_removed(self):
        self.build()
        old = self.root / "docs" / "graph" / "_graph" / "maps" / "Старая операция.canvas"
        st_p = self.root / ".spec-graph" / "obsidian.json"
        st = json.loads(st_p.read_text(encoding="utf-8"))
        st["maps"].append(old.name)
        st_p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        old.write_text("{}", encoding="utf-8")
        self.assertIn(old.relative_to(self.root).as_posix(), self.build()["changed"])
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
