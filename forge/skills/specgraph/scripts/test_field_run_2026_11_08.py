#!/usr/bin/env python3
"""Регрессии боевого /graph 2026-11-08 (GLM 5.3 Flash, репо спеки, шаг graph-link после link-note 337/337).

Пинит:
  • повторный graph_link_start (после graph_delta_decide) не возвращает в работу сделанные единицы:
    свои прошлые предложения модели во вход не попадают — вход и его sha те же;
  • нарезка длинной заметки на части: резерв под суффикс — худший настоящий («часть K из N»),
    абзац сверх бюджета режется по строкам; вход каждой части в бюджете (было 50003 при 50000);
  • команда согласия — готовая: интерпретатор движка и абсолютный путь record_approval.py, без
    «python3 <поставка>/…» (модель нашла копию в ~/Downloads, shell форка отклонил вызов);
  • отчёт связей с нерешённым разделом 6 ведёт к решениям по узлам, а не к согласию;
  • graph_next пачкой отдаёт готовый промпт субагента один раз, с местами под item_id и input (413).
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import link as LK  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import status as ST  # noqa: E402
from sgraph import tools as T  # noqa: E402
from test_link_step import inject_delta  # noqa: E402


def _items(root: Path):
    return json.loads((root / Q.QUEUE_REL / "link-note.json").read_text(encoding="utf-8"))["items"]


def _set_budget(root: Path, chars: int) -> None:
    p = root / ".spec-graph" / "config.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("queue", {})["budget_chars"] = chars
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def _grow_note(root: Path, name: str, extra: str) -> None:
    """Дописать текст в первый блок graph:source заметки (как длинный раздел спеки)."""
    cfg = T._need_cfg(root)
    g = E.Graph(root, cfg)
    p = root / cfg["vault"] / g.by_name[name]["path"]
    lines = p.read_text(encoding="utf-8").split("\n")
    end = next(i for i, l in enumerate(lines) if re.match(r"^<!-- graph:source[a-z0-9-]*:end -->$", l.strip()))
    lines[end:end] = ["", extra]
    p.write_text("\n".join(lines), encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()


class RerunKeepsDone(Base):
    def test_second_link_start_leaves_done_units_done(self):
        K.through_link(self.root)
        before = {it["id"]: it["input_sha"] for it in _items(self.root)}
        self.assertTrue(any(p.get("origin") == "model" for p in E.load_all(self.root)))
        res = T.call("graph_link_start", self.root, {})
        self.assertEqual(res["queue"]["done"], res["queue"]["total"])
        self.assertEqual({it["id"]: it["input_sha"] for it in _items(self.root)}, before)


class PartsFitBudget(Base):
    def _note(self):
        K.through_split(self.root)
        T.call("graph_edges_mechanical", self.root, {})
        g = E.Graph(self.root, T._need_cfg(self.root))
        return next(n["name"] for n in g.index
                    if [e for e in g.ont.edges_from(n) if e not in LK._SET_BY_SPLIT])

    def _check(self, extra: str):
        name = self._note()
        _grow_note(self.root, name, extra)
        g = E.Graph(self.root, T._need_cfg(self.root))
        ops = [n for n in g.index if n["kind"] in g.ont.target_kinds("creates_task")]
        known = [p for p in E.load(self.root, name) if str(p.get("origin", "")).startswith("rule:")]
        head = len(LK._input(g, g.by_name[name], "", ops, known, " — часть 999 из 999"))
        budget = LK.brief_chars() + head + 400
        _set_budget(self.root, budget)
        T.call("graph_link_start", self.root, {})          # раньше: «вход … — N знаков с брифом, бюджет …: режь мельче»
        parts = [it for it in _items(self.root) if it["refs"]["note"] == name]
        self.assertGreaterEqual(len(parts), 10)
        bodies = []
        for it in parts:
            text = Q._input_path(self.root, it["id"]).read_text(encoding="utf-8")
            self.assertLessEqual(len(text) + LK.brief_chars(), budget, it["id"])
            bodies.append(text.split("цитаты — только отсюда)\n\n", 1)[1].rsplit("\n\nСдай рёбра", 1)[0])
            self.assertTrue(bodies[-1].strip(), it["id"])          # нет частей из одного разделителя
        self.assertEqual(re.sub(r"\s+", "", "".join(bodies)), re.sub(r"\s+", "", g.text(name)))

    def test_many_short_paragraphs(self):
        self._check("\n\n".join("ж" for _ in range(3000)))

    def test_one_long_paragraph_splits_by_lines(self):
        self._check("\n".join(f"строка {i}" for i in range(1500)))


class ConsentCommand(Base):
    def test_record_approval_command_is_ready(self):
        res = K.through_review(self.root)
        cmd = ST.script("pipeline-state/scripts/record_approval.py")
        self.assertIn(cmd, res["next"])
        self.assertIn(Path(sys.executable).as_posix(), cmd)
        self.assertTrue((ST.ENGINE_SKILLS / "pipeline-state" / "scripts" / "record_approval.py").is_file())
        self.assertNotIn("<поставка>", res["next"])
        self.assertNotIn("<поставка>", K.approve_edges(self.root)["next"])

    def test_no_placeholder_left_in_engine(self):
        for p in (SCRIPTS / "sgraph").glob("*.py"):
            code = "\n".join(l for l in p.read_text(encoding="utf-8").split("\n") if not l.lstrip().startswith(("#", "«")))
            self.assertNotIn('<поставка>/', code.replace('«python3 <поставка>/…»', ""), p.name)


class DeltaBeforeConsent(Base):
    def test_undecided_delta_asks_decisions_first(self):
        res = K.through_review(self.root, inject_delta)
        self.assertEqual(res["delta_undecided"], 1)
        self.assertIn("сначала раздел 6", res["next"])
        self.assertNotIn("record_approval", res["next"])


class BatchPrompt(Base):
    def test_prompt_once_per_batch(self):
        K.through_split(self.root)
        T.call("graph_edges_mechanical", self.root, {})
        T.call("graph_link_start", self.root, {})
        res = T.call("graph_next", self.root, {"queue": "link-note", "batch": True})
        prompt = res["prompt"]
        for part in ("{item_id}", "{input}", '"step_id": "graph-link"', "graph_link_put", "без пересказа"):
            self.assertIn(part, prompt)
        brief = re.search(r"бриф (\S+\.md)", prompt).group(1)
        self.assertTrue(Path(brief).is_file())
        self.assertTrue(all(set(u) <= {"item_id", "input", "attempt", "repeat"} for u in res["units"]))


if __name__ == "__main__":
    unittest.main()
