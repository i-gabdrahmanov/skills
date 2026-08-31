#!/usr/bin/env python3
"""Тесты check_traceability.py — сквозной judge трассируемости (P2-11)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_traceability import analyze, md_anchors, _slug, _ref_anchor

SDD = "# Spec\n## T1: Entity\nGiven x When y Then z\n## T2 Service\ntext\n<a name=\"custom\"></a>\n"
PLAN = {"tasks": [
    {"id": "T1", "sdd_ref": "sdd.md#t1-entity", "acceptance": ["Given a When b Then c"]},
    {"id": "T2", "sdd_ref": "sdd.md#t2-service", "acceptance": ["crud"]},
]}
EVAL = {"evals": [
    {"id": "compile-t1", "task_id": "T1"}, {"id": "coverage-t1", "task_id": "T1"},
    {"id": "compile-t2", "task_id": "T2"},
]}


class TestSlugAnchors(unittest.TestCase):
    def test_slug_basic(self):
        self.assertEqual(_slug("## T1: Entity ExportJob"), "t1-entity-exportjob")

    def test_slug_cyrillic(self):
        self.assertEqual(_slug("Сущность Job"), "сущность-job")

    def test_md_anchors_headings_and_explicit(self):
        a = md_anchors(SDD)
        self.assertIn("t1-entity", a)
        self.assertIn("t2-service", a)
        self.assertIn("custom", a)

    def test_ref_anchor(self):
        self.assertEqual(_ref_anchor("docs/x/sdd.md#t1"), "t1")
        self.assertIsNone(_ref_anchor("sdd.md"))
        self.assertIsNone(_ref_anchor(None))


class TestTraceClean(unittest.TestCase):
    def test_full_chain_passes(self):
        r = analyze(PLAN, SDD, EVAL)
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["error"], 0)
        m = {row["task_id"]: row for row in r["matrix"]}
        self.assertTrue(m["T1"]["sdd_resolved"])
        self.assertEqual(m["T1"]["evals"], 2)
        self.assertEqual(m["T2"]["evals"], 1)


class TestBrokenSddRef(unittest.TestCase):
    def test_dangling_anchor_is_error(self):
        plan = {"tasks": [{"id": "T1", "sdd_ref": "sdd.md#nonexistent", "acceptance": ["a"]}]}
        r = analyze(plan, SDD, EVAL)
        self.assertEqual(r["status"], "fail")
        self.assertTrue(any("битая ссылка" in e for e in r["errors"]))

    def test_ref_without_anchor_warns(self):
        plan = {"tasks": [{"id": "T1", "sdd_ref": "sdd.md", "acceptance": ["a"]}]}
        r = analyze(plan, SDD, {"evals": [{"id": "c", "task_id": "T1"}]})
        self.assertEqual(r["status"], "pass")  # warning, не error
        self.assertTrue(any("без якоря" in w for w in r["warnings"]))

    def test_no_sdd_text_skips_resolution(self):
        r = analyze(PLAN, None, EVAL)
        self.assertTrue(all(row["sdd_resolved"] is None for row in r["matrix"]))


class TestEvalCoverage(unittest.TestCase):
    def test_task_without_eval_is_error(self):
        plan = {"tasks": [
            {"id": "T1", "sdd_ref": "sdd.md#t1-entity", "acceptance": ["a"]},
            {"id": "T3", "sdd_ref": "sdd.md#t1-entity", "acceptance": ["b"]},  # нет eval
        ]}
        r = analyze(plan, SDD, EVAL)
        self.assertEqual(r["status"], "fail")
        self.assertTrue(any("T3" in e and "нет ни одного eval" in e for e in r["errors"]))

    def test_no_eval_plan_skips_eval_chain(self):
        r = analyze(PLAN, SDD, None)
        self.assertEqual(r["status"], "pass")
        self.assertTrue(all(row["evals"] is None for row in r["matrix"]))

    def test_orphan_eval_warns(self):
        ep = {"evals": [{"id": "compile-t1", "task_id": "T1"},
                        {"id": "compile-t2", "task_id": "T2"},
                        {"id": "ghost", "task_id": "T99"}]}
        r = analyze(PLAN, SDD, ep)
        self.assertTrue(any("сирота" in w for w in r["warnings"]))


class TestAcceptance(unittest.TestCase):
    def test_empty_acceptance_is_error(self):
        plan = {"tasks": [{"id": "T1", "sdd_ref": "sdd.md#t1-entity", "acceptance": []}]}
        r = analyze(plan, SDD, {"evals": [{"id": "c", "task_id": "T1"}]})
        self.assertTrue(any("acceptance" in e for e in r["errors"]))


class TestMatrix(unittest.TestCase):
    def test_matrix_shape(self):
        r = analyze(PLAN, SDD, EVAL)
        row = r["matrix"][0]
        self.assertEqual(set(row), {"task_id", "sdd_ref", "sdd_resolved", "evals", "acceptance"})


if __name__ == "__main__":
    unittest.main()
