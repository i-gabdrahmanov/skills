#!/usr/bin/env python3
"""Регрессии аудита 2026-11-07 в движке spec-graph: имена файлов заметок, коллизии по регистру,
прогон в машине состояний forge (`graph_status.run`), права записанных файлов."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import fsutil as FS  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import status as ST  # noqa: E402


class Names(unittest.TestCase):
    def test_windows_unsafe_names_rejected(self):
        ont = O.load()
        for name in ('Операция "Комплект"', "List<Task>", "Нужна ли проверка?", "a*b", "Задача.", "CON"):
            with self.subTest(name=name):
                self.assertTrue(ont.check_name("operation", name), f"{name!r} прошло")
        self.assertEqual(ont.check_name("operation", "Укомплектование архивных дел"), [])


class RunInfo(unittest.TestCase):
    def test_no_run_offers_init_then_step_commands(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            info = ST.run_info(root)
            self.assertIsNone(info["key"])
            self.assertEqual(info["next_key"], "GRAPH-1")
            self.assertIn("--skill specgraph --feature GRAPH-1", info["commands"]["init"])
            run = root / "ground" / "statements" / "specgraph" / "GRAPH-1"
            run.mkdir(parents=True)
            (run / "manifest.json").write_text(json.dumps({"steps": [
                {"id": "graph-map", "status": "completed"}, {"id": "graph-link", "status": "pending"},
                {"id": "graph-check", "status": "pending"}]}), encoding="utf-8")
            info = ST.run_info(root)
            self.assertEqual((info["key"], info["step"]), ("GRAPH-1", "graph-link"))
            self.assertIn("graph_gate.py link", info["commands"]["gate"])
            self.assertIn("--step-id graph-link --status completed", info["commands"]["close"])
            self.assertEqual(info["next_key"], "GRAPH-2")


class FileModes(unittest.TestCase):
    def test_write_keeps_readable_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "note.md"
            FS.write_text(p, "x", encoding="utf-8")
            mask = os.umask(0)
            os.umask(mask)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o666 & ~mask,
                             "mkstemp 0600 не должен доезжать до заметки")
            os.chmod(p, 0o664)
            FS.write_text(p, "y", encoding="utf-8")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o664, "режим прежнего файла сохраняется")


if __name__ == "__main__":
    unittest.main()
