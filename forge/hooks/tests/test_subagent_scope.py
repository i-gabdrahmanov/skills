#!/usr/bin/env python3
"""test_subagent_scope.py — отметка «идёт работа субагента» при ПАЧКЕ параллельных субагентов.

Боевой /graph 2026-11-07: оркестратор запускал 10 субагентов одним ходом, ~10% сдач ловили DENY
inline-phase-guard «выполняй через agent(...)» при готовой раскладке. Причина — read-modify-write
маркера без замка (десять SubagentStart разом теряли до 9 отметок) и leave, снимавший чужую отметку.

Пинит: параллельные enter не теряют отметок; пока жив хоть один субагент пачки — active; общий ключ
(нет agent_id) считается по штукам; неизвестный id при нескольких живых чужую не снимает, при одном —
снимает (рассинхрон Start/Stop одного субагента); маркер старого формата читается.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
import unittest
import uuid
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))

import subagent_scope as SS  # noqa: E402


def _enter(args):
    sys.path.insert(0, str(HOOKS))
    import subagent_scope
    sid, i = args
    subagent_scope.enter(sid, f"general-purpose-{i}", "general-purpose")


class Batch(unittest.TestCase):
    def setUp(self):
        self.sid = f"test-{uuid.uuid4()}"

    def tearDown(self):
        for suffix in ("", ".lock"):
            Path(SS._path(self.sid) + suffix).unlink(missing_ok=True)

    def test_parallel_enter_keeps_every_mark(self):
        for _ in range(5):
            sid = f"test-{uuid.uuid4()}"
            with mp.Pool(10) as pool:
                pool.map(_enter, [(sid, i) for i in range(10)])
            self.assertEqual(len(SS._active(SS._load(sid))), 10)
            Path(SS._path(sid)).unlink()

    def test_active_until_last_of_batch_leaves(self):
        for i in range(3):
            SS.enter(self.sid, f"gp-{i}", "general-purpose")
        SS.leave(self.sid, "gp-0", "general-purpose")
        SS.leave(self.sid, "gp-1", "general-purpose")
        self.assertTrue(SS.active(self.sid), "gp-2 ещё работает")
        SS.leave(self.sid, "gp-2", "general-purpose")
        self.assertFalse(SS.active(self.sid))

    def test_shared_key_counts_each_subagent(self):
        SS.enter(self.sid, "", "general-purpose")
        SS.enter(self.sid, "", "general-purpose")
        SS.leave(self.sid, "", "general-purpose")
        self.assertTrue(SS.active(self.sid))
        SS.leave(self.sid, "", "general-purpose")
        self.assertFalse(SS.active(self.sid))

    def test_unknown_id_does_not_unmark_live_neighbours(self):
        SS.enter(self.sid, "gp-1", "general-purpose")
        SS.enter(self.sid, "gp-2", "general-purpose")
        SS.leave(self.sid, "чужой", "general-purpose")
        self.assertEqual(len(SS._active(SS._load(self.sid))), 2)

    def test_unknown_id_unmarks_the_only_one(self):
        SS.enter(self.sid, "gp-1", "general-purpose")
        SS.leave(self.sid, "gp-1-другой", "general-purpose")
        self.assertFalse(SS.active(self.sid))

    def test_legacy_marker_format_reads(self):
        import time
        Path(SS._path(self.sid)).write_text(json.dumps({"active": {"gp-1": time.time()}, "seen": True}),
                                            encoding="utf-8")
        self.assertTrue(SS.active(self.sid))
        SS.leave(self.sid, "gp-1", "general-purpose")
        self.assertFalse(SS.active(self.sid))


if __name__ == "__main__":
    unittest.main()
