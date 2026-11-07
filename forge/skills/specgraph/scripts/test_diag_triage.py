#!/usr/bin/env python3
"""test_diag_triage.py — разбор пакета диагностики по директиве (задача 4.4).

Пинит:
  • пакет фикстуры: только дыры полноты (класс «данные», P3) — ни одной находки P0–P2;
  • сломанное правило шедулеров: класс «правило», id правила, число промахов и форма заголовка;
  • ошибка движка — «баг» P1; E-evidence / E-verbatim / E-coverage — «баг» P0; брошенные единицы — «бриф»;
  • пробный цикл 4.4 на синтетике: вариант «регламентное задание» до правки маркеров даёт находку «правило»,
    после правки — нет, динамика против прошлого пакета «промахи вида scheduler: 4 → 0»;
  • CLI: пакет без машинного блока — exit 2; прогонов нет — вопросы пользователю.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import _testkit as K  # noqa: E402
import diag_triage as DT  # noqa: E402
import gen_variants as GV  # noqa: E402
from sgraph import config as C  # noqa: E402
from sgraph import diag as D  # noqa: E402
from sgraph import metrics as M  # noqa: E402
from sgraph import tools as T  # noqa: E402

PRE_FIX_MARKERS = ["шедулер", "планировщик", "scheduler", "job", "джоб"]      # до пробного цикла 4.4


def bundle(root: Path) -> dict:
    return DT.load(root / D.build(root, T._need_cfg(root))["file"])


def classes(findings, sev=None):
    return sorted({(f["class"], f["severity"]) for f in findings if sev is None or f["severity"] in sev})


def set_markers(root: Path, markers) -> None:
    p = root / C.CONFIG_REL
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["extract"] = {"rules": [{"id": "R-scheduler-heading", "markers": markers}]}
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = K.make_project(Path(cls.tmp.name) / "repo")
        K.through_review(cls.root)
        K.approve_edges(cls.root)
        K.approve_accept(cls.root)
        cls.data = bundle(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_only_data_holes(self):
        f = DT.triage(self.data)
        self.assertEqual(classes(f), [("данные", "P3")])
        self.assertIn("W-op-no-creator ×2", f[0]["evidence"])

    def test_bug_classes(self):
        d = json.loads(json.dumps(self.data))
        d["health"]["counts"]["E-evidence"] = 2
        d["runs"][0]["engine_errors"] = [["KeyError", 3]]
        d["steps"]["link"] = -1
        d["queues"]["card"]["abandoned"] = [{"id": "card-x", "codes": ["E-card-ref"]}]
        f = DT.triage(d)
        self.assertIn(("баг", "P0"), classes(f))
        self.assertTrue(any(x["what"] == "ошибка движка KeyError" and x["severity"] == "P1" for x in f))
        self.assertTrue(any("гейт link не отработал" in x["what"] for x in f))
        self.assertTrue(any(x["class"] == "бриф" and "card" in x["what"] for x in f))
        self.assertEqual(f[0]["severity"], "P0", "самое серьёзное — первым")

    def test_brief_codes_and_environment(self):
        d = json.loads(json.dumps(self.data))
        d["runs"][0]["top_codes"] = [["E-edge-quote", 7], ["E-arg-type", 6], ["E-card-ref", 2]]
        d["versions"]["forge"] = "forge/v0.6.0"
        d["environment"].update(mcp_spec_graph=False, hook_graph_guard=True)
        f = DT.triage(d)
        self.assertTrue(any(x["what"] == "модель буксует на E-edge-quote" for x in f))
        self.assertTrue(any("аргументы" in x["what"] for x in f))
        self.assertFalse(any("E-card-ref" in x["what"] for x in f), "ниже порога")
        self.assertTrue(any(x["class"] == "окружение" and "MCP" in x["what"] for x in f))
        sev = [x["severity"] for x in f]
        self.assertEqual(sev, sorted(sev), "по серьёзности: P1 окружения раньше P2 брифа")

    def test_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(DT.main([str(sorted((self.root / "docs/graph" / D.DIAG_DIR).glob("*.md"))[-1])]), 0)
        self.assertIn("| P3 | данные |", out.getvalue())
        bad = Path(self.tmp.name) / "x.md"
        bad.write_text("# не пакет\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(DT.main([str(bad)]), 2)
        d = json.loads(json.dumps(self.data))
        d["runs"] = []
        self.assertIn("Вопросы пользователю", DT.render(DT.triage(d), d))


class BrokenRule(unittest.TestCase):
    def test_scheduler_rule(self):
        with tempfile.TemporaryDirectory() as td:
            root = K.make_project(Path(td) / "repo")
            set_markers(root, ["несуществующий-маркер"])
            K.through_catalog(root)
            f = DT.triage(bundle(root))
            rule = [x for x in f if x["class"] == "правило"]
            self.assertEqual(len(rule), 1, f)
            self.assertIn("scheduler (R-scheduler-heading)", rule[0]["what"])
            self.assertEqual(rule[0]["evidence"], "промахов 4; формы заголовков: h3:N.N ×4")


class TrialCycle(unittest.TestCase):
    """Пробный цикл директивы на синтетике (журнал docs/specgraph/field/2026-11-06-trial.md)."""

    def run_variant(self, td: Path, markers=None) -> dict:
        v = "sched-reglament"
        root = K.make_project(td / ("pre" if markers else "post"), GV.materialize(td / "_v", v))
        if markers:
            set_markers(root, markers)
        T.call("graph_survey", root, {})
        K.run_queue(root, "draft-file", K.draft_answer(root, GV.text_fn(v)))
        K.approve_catalog(root)
        return bundle(root)

    def test_find_fix_regress(self):
        with tempfile.TemporaryDirectory() as td:
            before = self.run_variant(Path(td), PRE_FIX_MARKERS)
            found = [x for x in DT.triage(before) if x["class"] == "правило"]
            self.assertEqual([x["what"] for x in found], ["экстрактор не видит узлы вида scheduler (R-scheduler-heading)"])
            after = self.run_variant(Path(td))
            self.assertEqual([x for x in DT.triage(after) if x["class"] == "правило"], [])
            dyn = [x["what"] for x in DT.triage(after, before) if x["class"] == "динамика"]
            self.assertIn("промахи вида scheduler: 4 → 0", dyn)


if __name__ == "__main__":
    unittest.main()
