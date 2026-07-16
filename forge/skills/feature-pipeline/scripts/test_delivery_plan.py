#!/usr/bin/env python3
"""Тесты delivery_plan.py — идемпотентный resume-aware план доставки (P1-7)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from delivery_plan import build_plan, _branch_name, _topo_order, _jira_keys

PLAN = {
    "feature_slug": "bulk-export",
    "tasks": [
        {"id": "T1", "depends_on": []},
        {"id": "T2", "depends_on": ["T1"]},
        {"id": "T3", "depends_on": ["T2"]},
    ],
}
CFG = {"project": {"default_branch": "main"}, "delivery": {"branch_prefix": "feature/"}}


def _manifest(deliver_status: dict) -> dict:
    steps = [{"id": f"07-deliver-{tid}", "status": st} for tid, st in deliver_status.items()]
    return {"context": {"feature": "bulk-export"}, "steps": steps}


def _plan(local=None, remote=None, remote_checked=False, deliver=None, jira=None):
    return build_plan(PLAN, _manifest(deliver or {}), CFG, jira or {}, Path("/tmp/x"),
                      "07-deliver-", set(local or []), set(remote or []), remote_checked)


class TestBranchNaming(unittest.TestCase):
    def test_with_jira_key(self):
        self.assertEqual(_branch_name("T1", {"T1": "STOR-201"}, "slug", "feature/"), "feature/STOR-201")

    def test_without_jira(self):
        self.assertEqual(_branch_name("T1", {}, "bulk-export", "feature/"), "feature/bulk-export-T1")

    def test_custom_prefix(self):
        self.assertEqual(_branch_name("T2", {}, "s", "feat/"), "feat/s-T2")


class TestTopoOrder(unittest.TestCase):
    def test_linear_chain(self):
        self.assertEqual(_topo_order(PLAN["tasks"]), ["T1", "T2", "T3"])

    def test_dep_before_dependent_even_if_unsorted(self):
        tasks = [{"id": "B", "depends_on": ["A"]}, {"id": "A", "depends_on": []}]
        self.assertEqual(_topo_order(tasks), ["A", "B"])

    def test_cycle_does_not_crash(self):
        tasks = [{"id": "A", "depends_on": ["B"]}, {"id": "B", "depends_on": ["A"]}]
        self.assertEqual(set(_topo_order(tasks)), {"A", "B"})


class TestActions(unittest.TestCase):
    def test_all_create_on_clean_repo(self):
        p = _plan()
        self.assertEqual([r["action"] for r in p["tasks"]], ["create", "create", "create"])
        self.assertFalse(p["summary"]["all_done"])
        self.assertEqual(p["summary"]["by_action"]["create"], 3)

    def test_delivered_step_is_skip(self):
        p = _plan(deliver={"T1": "completed"})
        actions = {r["task_id"]: r["action"] for r in p["tasks"]}
        self.assertEqual(actions["T1"], "skip")
        self.assertEqual(actions["T2"], "create")

    def test_existing_local_branch_is_resume(self):
        # T1 ветка уже создана, но deliver-шаг не закрыт → resume (не пересоздавать)
        p = _plan(local=["feature/bulk-export-T1"])
        actions = {r["task_id"]: r["action"] for r in p["tasks"]}
        self.assertEqual(actions["T1"], "resume")

    def test_existing_remote_branch_is_resume(self):
        p = _plan(remote=["feature/bulk-export-T2"], remote_checked=True)
        actions = {r["task_id"]: r["action"] for r in p["tasks"]}
        self.assertEqual(actions["T2"], "resume")

    def test_delivered_beats_existing_branch(self):
        # шаг completed важнее наличия ветки → skip (не resume)
        p = _plan(local=["feature/bulk-export-T1"], deliver={"T1": "completed"})
        self.assertEqual(p["tasks"][0]["action"], "skip")

    def test_all_done_when_every_task_delivered(self):
        p = _plan(deliver={"T1": "completed", "T2": "completed", "T3": "completed"})
        self.assertTrue(p["summary"]["all_done"])
        self.assertEqual(p["summary"]["by_action"]["skip"], 3)

    def test_case_insensitive_deliver_step(self):
        # оркестратор иногда пишет шаг в lower ('07-deliver-t1') — должно матчиться
        m = {"context": {"feature": "bulk-export"},
             "steps": [{"id": "07-deliver-t1", "status": "completed"}]}
        p = build_plan(PLAN, m, CFG, {}, Path("/tmp/x"), "07-deliver-", set(), set(), False)
        self.assertEqual(p["tasks"][0]["action"], "skip")


class TestTargetBranch(unittest.TestCase):
    def test_root_task_targets_story_branch(self):
        # корневые PR целятся в интеграционную ветку фичи, НЕ в default:
        # в default уходит один финальный PR feature/<slug> → main
        p = _plan()
        self.assertEqual(p["tasks"][0]["target"], "feature/bulk-export")
        self.assertEqual(p["story_branch"], "feature/bulk-export")
        self.assertEqual(p["default_branch"], "main")

    def test_dependent_targets_parent_branch(self):
        p = _plan(jira={"tasks": {"T1": "STOR-201", "T2": "STOR-202"}})
        rows = {r["task_id"]: r for r in p["tasks"]}
        self.assertEqual(rows["T2"]["target"], "feature/STOR-201")


class TestRemoteUnknown(unittest.TestCase):
    def test_remote_none_when_not_checked(self):
        p = _plan(local=["feature/bulk-export-T1"])
        self.assertIsNone(p["tasks"][0]["branch_remote"])  # offline → None, не False


class TestJiraKeys(unittest.TestCase):
    def test_skipped_ledger_yields_no_keys(self):
        self.assertEqual(_jira_keys({"skipped": True}), {})

    def test_subtasks_format(self):
        self.assertEqual(_jira_keys({"subtasks": [{"task_id": "T1", "key": "K-1"}]}), {"T1": "K-1"})


if __name__ == "__main__":
    unittest.main()
