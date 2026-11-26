#!/usr/bin/env python3
"""Пауза прогона одинаково видна хукам и движку графа.

Движок (stdlib-only) хуки не импортирует и держит свою копию предиката паузы
(sgraph.fsutil.run_paused ↔ hooks/_project.run_paused). Разойдутся — граф сочтёт активным
прогон, которого хуки не видят, и update.py откажет на close шага.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[2] / "hooks"))

from sgraph import fsutil as FS  # noqa: E402
from sgraph import status as ST  # noqa: E402
from sgraph import metrics as M  # noqa: E402
import _project as P  # noqa: E402

MARKERS = {
    "нет маркера": None,
    "маркер pause.py": {"produced_by": "pause", "paused_at": "x"},
    "рукописный без провенанса": {"paused_at": "x"},
    "чужой провенанс": {"produced_by": "model"},
    "не объект": ["pause"],
    "битый JSON": "{не json",
}


class Parity(unittest.TestCase):
    def test_same_verdict_on_every_marker(self):
        for name, body in MARKERS.items():
            with self.subTest(marker=name), tempfile.TemporaryDirectory() as td:
                d = Path(td)
                if body is not None:
                    (d / "paused.json").write_text(
                        body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
                self.assertEqual(FS.run_paused(d), P.run_paused(d))


class GraphSeesPause(unittest.TestCase):
    def _run(self, root: Path, key: str, paused: bool) -> None:
        d = root / "ground/statements/specgraph" / key
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(
            {"steps": [{"id": "graph-map", "status": "pending"}]}), encoding="utf-8")
        if paused:
            (d / "paused.json").write_text(json.dumps({"produced_by": "pause"}), encoding="utf-8")

    def test_paused_graph_is_not_the_active_step(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._run(root, "GRAPH-1", paused=True)
            info = ST.run_info(root)
            self.assertIsNone(info["step"], "граф на паузе выдан активным шагом")
            self.assertEqual(info["paused"], ["GRAPH-1"])
            self.assertIn("pause.py", info["commands"]["resume"])
            self.assertNotEqual(M.current_key(root), "GRAPH-1",
                                "счётчики пишутся в прогон на паузе")

    def test_live_graph_still_active(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._run(root, "GRAPH-1", paused=False)
            info = ST.run_info(root)
            self.assertEqual((info["key"], info["step"]), ("GRAPH-1", "graph-map"))
            self.assertNotIn("paused", info)


if __name__ == "__main__":
    unittest.main(verbosity=2)
