#!/usr/bin/env python3
"""test_map_gate.py — шаг graph-map: гейт, согласие, обрыв (задача 1.7).

Пинит `graph_gate.py map` (настоящий CLI, как его зовёт record_gate):
  • без конфига, с открытой очередью draft-file, без согласия на каталог, с изменённым после согласия
    каталогом, без очереди assign-section, без нарезки, с потерянным блоком, с правкой маркера — exit 1 и
    понятная причина;
  • весь шаг на «ответах модели» фикстуры — exit 0; брошенная единица перечисляется;
  • раскладку до согласия сдать нельзя (E-catalog-unapproved), очередь раскладки не строится;
  • «обрыв» посреди очереди: повторный цикл продолжает с той же единицы, без повторов и пропусков,
    и шаг доходит до exit 0;
  • в реестре веток продуктивные инструменты шага — graph_draft_put и graph_assign_put.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[2] / "hooks"))

import _branches as BR  # noqa: E402
import _testkit as K  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402


def gate(root: Path):
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "graph_gate.py"), "map", "--project", str(root)],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout + r.stderr


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_missing_piece_blocks(self):
        empty = self.base / "empty"
        empty.mkdir()
        self.assertEqual(gate(empty)[0], 1)
        root = K.make_project(self.base / "repo")
        T.call("graph_survey", root, {})
        rc, out = gate(root)
        self.assertEqual(rc, 1)
        self.assertIn("draft-file не закрыта", out)
        K.run_queue(root, "draft-file", K.draft_answer(root))
        T.call("graph_catalog", root, {})
        rc, out = gate(root)
        self.assertIn("каталог не утверждён", out)
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_assign_start", root, {})
        self.assertEqual(cm.exception.codes, ["E-catalog-unapproved"])
        K.approve_catalog(root)
        rc, out = gate(root)
        self.assertIn("assign-section не построена", out)
        T.call("graph_assign_start", root, {})
        K.run_queue(root, "assign-section", K.assign_answer(root))
        rc, out = gate(root)
        self.assertEqual(rc, 1)
        self.assertIn("нарезка не сделана", out)
        T.call("graph_split", root, {})
        rc, out = gate(root)
        self.assertEqual(rc, 0, out)
        self.assertIn("✓ graph-map", out)
        p = root / "docs/graph/services/task-service/schedulers/Шедулер закрытия регулярных задач.md"
        orig = p.read_bytes()
        p.write_bytes(orig.replace("23:00".encode("utf-8"), b"21:00"))
        rc, out = gate(root)
        self.assertEqual(rc, 1)
        self.assertIn("E-verbatim", out)
        p.write_bytes(orig)
        T.call("graph_catalog_decide", root, {"actions": [{"action": "drop", "node": "УПЗ", "why": "x"}]})
        T.call("graph_catalog", root, {})
        (root / CT.APPROVED_REL).write_bytes((root / CT.CATALOG_REL).read_bytes())
        rc, out = gate(root)
        self.assertEqual(rc, 1)
        self.assertIn("stale", out, "каталог изменился после согласия — утверждают заново")

    def test_lost_block_blocks(self):
        root = K.make_project(self.base / "repo2")
        K.through_split(root)
        plan_p = sorted((root / AS.PLAN_REL).glob("*.json"))[0]
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        plan["assign"][0]["blocks"].pop()
        plan_p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        rc, out = gate(root)
        self.assertEqual(rc, 1)
        self.assertIn("E-coverage", out)


class Resume(unittest.TestCase):
    def test_crash_mid_queue_then_continue(self):
        with tempfile.TemporaryDirectory() as td:
            root = K.make_project(Path(td) / "repo")
            K.through_catalog(root)
            T.call("graph_assign_start", root, {})
            ans = K.assign_answer(root)
            seen = []

            def first_three(unit):
                if len(seen) == 3:
                    return None                    # «обрыв»: единица осталась в аренде
                seen.append(unit["item_id"])
                return ans(unit)
            K.run_queue(root, "assign-section", first_three)
            resumed = T.call("graph_next", root, {"queue": "assign-section"})
            self.assertTrue(resumed["repeat"], "после обрыва — та же единица")
            rest = []

            def all_rest(unit):
                rest.append(unit["item_id"])
                return ans(unit)
            K.run_queue(root, "assign-section", all_rest)
            self.assertFalse(set(seen) & set(rest), "без повторов")
            total = len(json.loads((root / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))["items"])
            self.assertEqual(len(seen) + len(rest), total, "без пропусков")
            T.call("graph_split", root, {})
            self.assertEqual(gate(root)[0], 0)

    def test_abandoned_unit_is_listed(self):
        with tempfile.TemporaryDirectory() as td:
            root = K.make_project(Path(td) / "repo")
            T.call("graph_survey", root, {})
            for _ in range(3):
                unit = T.call("graph_next", root, {"queue": "draft-file"})
                with self.assertRaises(T.ToolError):
                    T.call("graph_draft_put", root, {"item_id": unit["item_id"], "nodes": [{"kind": "job"}]})
            K.run_queue(root, "draft-file", K.draft_answer(root))
            K.approve_catalog(root)
            T.call("graph_assign_start", root, {})
            K.run_queue(root, "assign-section", K.assign_answer(root))
            T.call("graph_split", root, {})
            rc, out = gate(root)
            self.assertIn("брошено единиц 1", out)


class Registry(unittest.TestCase):
    def test_productive_tools_of_the_step(self):
        self.assertEqual(BR.BRANCHES["specgraph"]["phase_tools"]["graph-map"], ("graph_draft_put", "graph_assign_put"))
        self.assertEqual(BR.BRANCHES["specgraph"]["phase_tools"]["graph-link"], ("graph_link_put", "graph_card_put"))
        for free in ("graph_next", "graph_catalog_decide", "graph_split", "graph_survey"):
            self.assertEqual(BR.tool_steps(f"mcp__spec-graph__{free}"), ())


if __name__ == "__main__":
    unittest.main()
