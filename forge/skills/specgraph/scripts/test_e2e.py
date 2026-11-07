#!/usr/bin/env python3
"""test_e2e.py — весь /graph на фикстуре ТЗ: map → link → check через инструменты и гейты (задача 3.6).

Ответы модели подставляются из expected/ через те же очереди, согласия выписывает record_approval (как
оркестратор после «да»). Пинит:
  • все три гейта — exit 0; граф vault = эталон `expected/graph.json`: узлы (вид, имя, сервис), прямые рёбра
    (симметричное — без направления); все переходы `walks` проходят по шагам через graph_neighbors;
  • мутации ловятся: выпал блок из плана → гейт map (E-coverage); выдуманная цитата → ребро отклонено;
    правка frontmatter моделью → хук graph-guard отказывает; похожие шедулеры не склеились;
  • стресс-корпус: каждый вариант со статусом e2e=ok проходит весь конвейер на «модели-оракуле» до гейта check.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import _testkit as K  # noqa: E402
import gen_variants as GV  # noqa: E402
import graph_gate as G  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import tools as T  # noqa: E402
from sgraph import traverse as TR  # noqa: E402

GRAPH = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
MATRIX = json.loads((K.FIX / "variants.json").read_text(encoding="utf-8"))
GUARD = SCRIPTS.parents[2] / "hooks" / "graph-guard.py"


def gate(root: Path, step: str):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = G.main([step, "--project", str(root)])
    return code, out.getvalue()


def whole_graph(root: Path, oracle: bool = False, variant: str = ""):
    """Весь конвейер с гейтами; (коды гейтов map, link, check)."""
    if oracle:
        K.oracle_split(root)
    else:
        K.through_split(root)
    codes = [gate(root, "map")]
    T.call("graph_edges_mechanical", root, {})
    T.call("graph_link_start", root, {})
    K.run_queue(root, "link-note", link_oracle(root, variant) if oracle else K.link_answer(root))
    T.call("graph_card_start", root, {})
    K.run_queue(root, "card", K.card_answer(root))
    T.call("graph_sync", root, {})
    T.call("graph_edges_review", root, {})
    before = [gate(root, "link")]                        # до «да» человека шаг не закрывается
    K.approve_edges(root)
    codes.append(gate(root, "link"))
    T.call("graph_check", root, {})
    before.append(gate(root, "check"))
    K.approve_accept(root)
    codes.append(gate(root, "check"))
    return codes + before


def link_oracle(root: Path, variant: str):
    """Ответы модели эталона по заметкам в тексте варианта: имена и цитаты — через ту же трансформацию (модель
    цитирует то, что видит)."""
    from sgraph import queue as Q
    fn = GV.text_fn(variant) if variant else (lambda s: s)
    base = json.loads((K.FIX / "expected" / "edges" / "model-edges.json").read_text(encoding="utf-8"))
    edges = {name_of(fn, note): [dict(p, to=name_of(fn, p["to"]), quote=fn(p["quote"])) for p in props]
             for note, props in base.items()}
    data = json.loads((root / Q.QUEUE_REL / "link-note.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in data["items"]}
    return lambda unit: {"proposals": edges.get(by_id[unit["item_id"]]["refs"]["note"], [])}


def name_of(fn, s: str) -> str:
    """Имя узла в варианте: трансформация текста, а NBSP и лишние пробелы движок в именах сводит к пробелу."""
    return " ".join(fn(s).replace("\xa0", " ").split())


def graph_of(root: Path):
    idx = TR.build_index(root, T._need_cfg(root), use_cache=False)
    ont = O.load()
    nodes = {(d["kind"], n, d.get("service")) for n, d in idx.nodes.items()}
    edges = set()
    for a, e, b in idx.edges:
        edges.add((min(a, b), e, max(a, b)) if ont.is_symmetric(e) else (a, e, b))
    return nodes, edges, idx


def expected_graph():
    ont = O.load()
    nodes = {(n["kind"], n["name"], n.get("service")) for n in GRAPH["nodes"]}
    edges = {(min(e["from"], e["to"]), e["edge"], max(e["from"], e["to"])) if ont.is_symmetric(e["edge"])
             else (e["from"], e["edge"], e["to"]) for e in GRAPH["edges"]}
    return nodes, edges


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = K.make_project(Path(cls.tmp.name) / "repo")
        cls.codes = whole_graph(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_gates_pass(self):
        for step, (code, out) in zip(("map", "link", "check"), self.codes):
            self.assertEqual(code, 0, f"{step}: {out}")
        for step, (code, out) in zip(("link", "check"), self.codes[3:]):
            self.assertEqual(code, 1, f"{step} без согласия человека: {out}")
            self.assertIn("согласие", out)

    def test_cards_written(self):
        from sgraph import fm as F
        from sgraph import split as SP
        for op in ("Укомплектование", "Регистрация", "Приём транспортных упаковок", "Передача упаковок в СЦ-архив"):
            _, body = F.read(SP.read_note(self.root / "docs/graph/business/operations" / f"{op}.md"))
            card = F.get_block(body, "card")
            self.assertIsNotNone(card, op)
            self.assertIn("**Для чего:**", card["content"])
        review = (self.root / "docs/graph/_graph/edges-review.md").read_text(encoding="utf-8")
        self.assertEqual(review.count("- карточка — готова"), 4)

    def test_graph_equals_expected(self):
        nodes, edges, _ = graph_of(self.root)
        want_nodes, want_edges = expected_graph()
        self.assertEqual(nodes, want_nodes)
        self.assertEqual(edges - want_edges, set(), "лишние рёбра")
        self.assertEqual(want_edges - edges, set(), "недостающие рёбра")

    def test_walks(self):
        for w in GRAPH["walks"]:
            p = w["path"]
            with self.subTest(w["tz"]):
                for i in range(0, len(p) - 2, 2):
                    res = T.call("graph_neighbors", self.root, {"name": p[i], "edges": [p[i + 1]], "limit": 50})
                    self.assertIn(p[i + 2], [s["to"] for s in res["steps"]])

    def test_similar_schedulers_not_merged(self):
        _, edges, idx = graph_of(self.root)
        self.assertIn("Шедулер закрытия регулярных задач", idx.nodes)
        self.assertIn("Шедулер закрытия задачи передачи упаковок в СЦ-архив", idx.nodes)
        shared = sorted(b for a, e, b in edges if a == "Шедулер закрытия регулярных задач" and e == "closes_task")
        self.assertEqual(shared, ["Приём транспортных упаковок", "Регистрация", "Укомплектование"])


class Mutations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dropped_block_stops_map(self):
        K.through_split(self.root)
        p = next(p for p in sorted((self.root / AS.PLAN_REL).glob("*.json"))
                 if json.loads(p.read_text(encoding="utf-8"))["assign"])
        plan = json.loads(p.read_text(encoding="utf-8"))
        plan["assign"][-1]["blocks"].pop()
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        code, out = gate(self.root, "map")
        self.assertEqual(code, 1)
        self.assertIn("E-coverage", out)

    def test_invented_quote_rejected(self):
        K.through_split(self.root)
        T.call("graph_link_start", self.root, {})
        unit = T.call("graph_next", self.root, {"queue": "link-note"})
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_link_put", self.root, {"item_id": unit["item_id"], "proposals": [
                {"edge": "closes_task", "to": "Регистрация", "quote": "этот шедулер закрывает вообще все задачи ВСП разом",
                 "confidence": "high"}]})
        self.assertTrue({"E-edge-quote", "E-edge-kind"} & set(cm.exception.codes), cm.exception.codes)

    def test_fm_edit_blocked_by_hook(self):
        K.through_split(self.root)
        note = self.root / "docs/graph/business/operations/Регистрация.md"
        old = note.read_text(encoding="utf-8")
        new = old.replace("kind: operation", "kind: operation\nclosed_by:\n  - \"[[Шедулер формирования задачи укомплектования]]\"", 1)
        self.assertNotEqual(new, old)
        payload = {"tool_name": "Write", "cwd": str(self.root),
                   "tool_input": {"file_path": str(note), "content": new}}
        r = subprocess.run([sys.executable, "-X", "utf8", str(GUARD)], input=json.dumps(payload, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("frontmatter", r.stderr)


class Variants(unittest.TestCase):
    def test_every_ok_variant_passes_the_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            for v, row in sorted(MATRIX["variants"].items()):
                if row["e2e"] != "ok":
                    continue
                with self.subTest(variant=v):
                    root = K.make_project(Path(td) / v, GV.materialize(Path(td) / "_v", v))
                    codes = whole_graph(root, oracle=True, variant=v)
                    for step, (code, out) in zip(("map", "link", "check"), codes):
                        self.assertEqual(code, 0, f"{v} {step}: {out}")
                    tf = GV.text_fn(v)
                    nodes, edges, _ = graph_of(root)
                    want_nodes, want_edges = expected_graph()
                    self.assertEqual(nodes, {(k, name_of(tf, n), s and name_of(tf, s)) for k, n, s in want_nodes})
                    self.assertEqual(edges, {(name_of(tf, a), e, name_of(tf, b)) for a, e, b in want_edges})
                    shutil.rmtree(Path(td) / v)


if __name__ == "__main__":
    unittest.main()
