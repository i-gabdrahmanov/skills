#!/usr/bin/env python3
"""test_diag_bundle.py — пакет диагностики `/graph diag` (задача 4.3, D17).

Пинит:
  • на фикстуре после всего /graph: пакет пишется сам в конце graph_check, все 9 разделов, машинный блок —
    JSON; в пакете нет ни одного имени узла эталона; тренд здоровья растёт с каждой проверкой;
  • каждый вариант стресс-корпуса: пакет проходит самопроверку утечки;
  • мутант «вставить заголовок исходника» — отказ E-diag-leak с номерами строк, файл не записан, CLI — exit 3;
  • «раздутый» корпус и переполненные прогоны укладываются в 400 строк (сначала режутся списки, потом 7–8);
  • сломанное правило шедулеров видно в разделе 6: промахи вида, форма заголовка, id правила;
  • остановка гейта кладёт пакет; хранятся 10 последних; level excerpt — только с выбранными блоками и с пометкой.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import _testkit as K  # noqa: E402
import gen_variants as GV  # noqa: E402
import graph_diag as GD  # noqa: E402
import graph_gate as G  # noqa: E402
import test_e2e as E2E  # noqa: E402
from sgraph import config as C  # noqa: E402
from sgraph import diag as D  # noqa: E402
from sgraph import metrics as M  # noqa: E402
from sgraph import tools as T  # noqa: E402

GRAPH = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
_healthy = {}


def setUpModule():
    tmp = tempfile.mkdtemp()
    root = K.make_project(Path(tmp) / "repo")
    K.through_review(root)
    K.approve_edges(root)
    res = K.approve_accept(root)
    _healthy.update(tmp=tmp, root=root, check=res)


def tearDownModule():
    shutil.rmtree(_healthy["tmp"], ignore_errors=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(_healthy["root"], self.root, symlinks=True)
        self.cfg = T._need_cfg(self.root)
        self.diag_dir = self.root / "docs" / "graph" / D.DIAG_DIR

    def tearDown(self):
        self.tmp.cleanup()

    def bundles(self):
        return sorted(self.diag_dir.glob("diag-*.md")) if self.diag_dir.is_dir() else []


class Fixture(_Base):
    def test_written_by_check_and_shaped(self):
        path = _healthy["check"]["diag"]
        self.assertTrue(path.startswith("docs/graph/_graph/diag/diag-"), path)
        text = (self.root / path).read_text(encoding="utf-8")
        for n in range(1, 10):
            self.assertIn(f"## {n}. ", text)
        data = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(data["health"]["errors"], 0)
        self.assertEqual(data["steps"], {"map": 0, "link": 0, "check": 0})
        self.assertLessEqual(text.count("\n") + 1, D.MAX_LINES)

    def test_no_node_names(self):
        text = (self.root / _healthy["check"]["diag"]).read_text(encoding="utf-8").lower()
        leaked = [n["name"] for n in GRAPH["nodes"] if len(n["name"]) >= 6 and n["name"].lower() in text]
        self.assertEqual(leaked, [])

    def test_health_trend(self):
        before = len(json.loads((self.root / M.HEALTH_REL).read_text(encoding="utf-8"))["runs"])
        T.call("graph_check", self.root, {})
        hist = json.loads((self.root / M.HEALTH_REL).read_text(encoding="utf-8"))
        self.assertEqual(len(hist["runs"]), before + 1)
        self.assertEqual(hist["runs"][-1]["metrics"]["errors"], 0)
        res = T.call("graph_diag", self.root, {})
        self.assertIn("тренд (последние снимки): errors: 0", (self.root / res["file"]).read_text(encoding="utf-8"))


class Leak(_Base):
    def test_source_heading_refused(self):
        heading = "## 3.1 Обработка ответа УПЗ о создании задачи"
        self.assertIn(heading, (self.root / "docs/spec/task-service/task-service.md").read_text(encoding="utf-8"))
        before = self.bundles()
        orig = D.render
        D.render = lambda *a, **k: orig(*a, **k) + "\n" + heading + "\n"
        try:
            with self.assertRaises(T.ToolError) as cm:
                T.call("graph_diag", self.root, {})
            self.assertEqual(cm.exception.codes, ["E-diag-leak"])
            self.assertRegex(str(cm.exception), r"строках пакета \d+")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(GD.main(["--project", str(self.root)]), 3)
        finally:
            D.render = orig
        self.assertEqual(self.bundles(), before, "пакет с утечкой не записан")

    def test_short_phrase_is_not_a_leak(self):
        c = D.Corpus(["Шедулер закрытия регулярных задач работает по расписанию каждый день"])
        self.assertEqual(c.leaks("шедулер закрытия: 3"), [])
        self.assertEqual(c.leaks("x\nшедулер закрытия регулярных задач"), [2])
        self.assertEqual(c.leaks("по расписанию каждый день"), [1], "4 слова подряд")
        self.assertEqual(c.leaks("…регулярных задач работает…"), [1], "24 знака подряд от начала слова")
        short = D.Corpus(["код тип раз два три"])
        self.assertEqual(short.leaks("код тип раз два"), [1], "4 слова короче 24 знаков — тоже утечка")

    def test_vault_prose_is_corpus(self):
        p = self.root / "docs/graph/business/operations/Регистрация.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nАналитик дописал пояснение про особый порядок закрытия.\n",
                     encoding="utf-8")
        self.assertEqual(D.corpus(self.root, self.cfg).leaks("пояснение про особый порядок"), [1])

    def test_every_variant_passes(self):
        with tempfile.TemporaryDirectory() as td:
            for v in sorted(GV.VARIANTS):
                with self.subTest(variant=v):
                    root = K.make_project(Path(td) / v, GV.materialize(Path(td) / "_v", v))
                    E2E.whole_graph(root, oracle=True, variant=v)
                    res = D.build(root, T._need_cfg(root))
                    self.assertLessEqual(res["lines"], D.MAX_LINES)
                    shutil.rmtree(Path(td) / v)


class Limits(_Base):
    def test_bloated_corpus(self):
        with tempfile.TemporaryDirectory() as td:
            root = K.make_project(Path(td) / "repo", GV.bloated(Path(td) / "big"))
            T.call("graph_survey", root, {})
            K.run_queue(root, "draft-file", K.draft_oracle(root))
            res = D.build(root, T._need_cfg(root))
            self.assertLessEqual(res["lines"], D.MAX_LINES)

    def test_overflow_cuts_lists_then_sections(self):
        for i in range(12):
            for tool in [f"graph_t{j:02d}" for j in range(35)]:
                M.record_call(self.root, tool, "rejected", codes=[f"E-x{i % 13}"], refs=[f"u-{i}"], key=f"K-{i:02d}")
        res = D.build(self.root, self.cfg)
        self.assertLessEqual(res["lines"], D.MAX_LINES)
        self.assertNotEqual(res["cut"], "нет")
        text = (self.root / res["file"]).read_text(encoding="utf-8")
        for n in range(1, 7):
            self.assertIn(f"## {n}. ", text, "разделы 1–6 не режутся")


class Rules(unittest.TestCase):
    def test_broken_scheduler_rule_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = K.make_project(Path(td) / "repo")
            p = root / C.CONFIG_REL
            cfg = json.loads(p.read_text(encoding="utf-8"))
            cfg["extract"] = {"rules": [{"id": "R-scheduler-heading", "markers": ["несуществующий-маркер"]}]}
            p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            K.through_catalog(root)
            res = D.build(root, T._need_cfg(root))
            text = (root / res["file"]).read_text(encoding="utf-8")
            sec = text[text.index("## 6. Правила"):text.index("## 7.")]
            self.assertIn("| R-scheduler-heading | scheduler | 0 | 0 |", sec)
            self.assertRegex(sec, r"- scheduler: промахов 4; формы заголовков: h3:N\.N: 4; правила вида: R-scheduler-heading")


class Surface(_Base):
    def test_gate_stop_writes_bundle(self):
        before = len(self.bundles())
        (self.root / "docs/graph/_graph/Начало.md").unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(G.main(["check", "--project", str(self.root)]), 1)
        m = re.search(r"пакет диагностики: (\S+)", out.getvalue())
        self.assertTrue(m and (self.root / m.group(1)).is_file(), out.getvalue())
        self.assertEqual(len(self.bundles()), before + 1)

    def test_keeps_last_ten(self):
        for i in range(12):
            D.build(self.root, self.cfg, now=1_800_000_000 + i)
        self.assertEqual(len(self.bundles()), D.KEEP)

    def test_excerpt_only_with_chosen_blocks(self):
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_diag", self.root, {"level": "excerpt"})
        self.assertEqual(cm.exception.codes, ["E-arg-blocks"])
        bid = "docs/spec/business/типы-задач.md#4"
        res = T.call("graph_diag", self.root, {"level": "excerpt", "blocks": [bid]})
        text = (self.root / res["file"]).read_text(encoding="utf-8")
        self.assertIn("## Фрагменты — ПРОВЕРЬ ПЕРЕД ВЫНОСОМ", text)
        self.assertIn("обеспечить ВСП расходными материалами", text)
        self.assertEqual(text.split("## Фрагменты")[0].count("обеспечить ВСП"), 0)


if __name__ == "__main__":
    unittest.main()
