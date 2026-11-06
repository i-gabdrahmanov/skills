#!/usr/bin/env python3
"""test_run_metrics.py — счётчики прогона и история здоровья графа (задача 4.1).

Пинит:
  • каждый вызов инструмента через `tools.call` оставляет запись: `ok`, `rejected` с кодами отказа,
    `error` с ТИПОМ исключения (без сообщения); id единиц — только из аргументов `x-ref-args`;
  • тест-утечка: после вызовов с текстом спеки в аргументах и в сообщении исключения ни одна
    последовательность из 4 слов исходника не встречается в `.spec-graph/runs/` и `health.json`;
  • коды — только по формату `E-…`: свободный текст кодом не прикинется;
  • `health.json` хранит не больше 30 снимков, только числа, находки — хешами; метка стабилизации —
    N снимков подряд без новых находок;
  • ключ прогона — свежайший манифест ветки specgraph, вне прогона — adhoc.
Данные — синтетический репо спеки во временном каталоге.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import metrics as M  # noqa: E402
from sgraph import tools as T  # noqa: E402

SPEC = ("# task-service\n\nШедулер формирования задачи укомплектования запускается каждые пять минут "
        "и отправляет запрос в УПЗ-адаптер.\n\nЗакрытие регулярных задач выполняет общий шедулер.\n")


def _four_word_runs(text: str) -> set:
    words = re.findall(r"\w+", text)
    return {" ".join(words[i:i + 4]) for i in range(len(words) - 3)}


def _dump_tree(root: Path) -> str:
    parts = []
    for base in (root / M.RUNS_REL, root / M.HEALTH_REL):
        files = [base] if base.is_file() else (sorted(base.rglob("*")) if base.is_dir() else [])
        for f in files:
            if f.is_file():
                parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)


class _Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "spec").mkdir(parents=True)
        (self.root / "docs" / "spec" / "task-service.md").write_text(SPEC, encoding="utf-8")
        self._saved = dict(T.TOOLS)

    def tearDown(self):
        T.TOOLS.clear()
        T.TOOLS.update(self._saved)
        self.tmp.cleanup()

    def _fake_tool(self, name: str, fn):
        T.TOOLS[name] = {"fn": fn, "description": "test",
                         "schema": {"type": "object", "properties": {"item_id": {"type": "string"},
                                                                    "text": {"type": "string"}},
                                    "x-ref-args": ["item_id"], "additionalProperties": False}}


class CallRecords(_Repo):
    def test_ok_rejected_error_are_all_recorded(self):
        T.call("graph_status", self.root, {})
        with self.assertRaises(T.ToolError):
            T.call("graph_status", self.root, {"bogus": 1})

        def boom(root, args):
            raise RuntimeError("упало на: " + SPEC)
        self._fake_tool("graph_probe", boom)
        with self.assertRaises(RuntimeError):
            T.call("graph_probe", self.root, {"item_id": "task-service.md#3", "text": SPEC})
        calls = M.read_calls(self.root)
        self.assertEqual([c["outcome"] for c in calls], ["ok", "rejected", "error"])
        self.assertEqual(calls[1]["codes"], ["E-arg-unknown"])
        self.assertEqual(calls[2]["error"], "RuntimeError")
        self.assertEqual(calls[2]["refs"], ["task-service.md#3"])
        self.assertGreater(calls[0]["size"], 0)
        self.assertEqual({c["tool"] for c in calls}, {"graph_status", "graph_probe"})

    def test_rejections_by_item_show_where_the_model_struggles(self):
        def strict(root, args):
            raise T.ToolError("блок потерян", ["E-plan-lost-block", "свободный текст не код"])
        self._fake_tool("graph_probe", strict)
        for _ in range(3):
            with self.assertRaises(T.ToolError):
                T.call("graph_probe", self.root, {"item_id": "s-7"})
        with self.assertRaises(T.ToolError):
            T.call("graph_probe", self.root, {"item_id": "s-9"})
        s = M.summarize(self.root)
        self.assertEqual(s["calls"], 4)
        self.assertEqual(s["top_codes"], [["E-plan-lost-block", 4]])
        self.assertEqual(s["most_rejected_refs"][0], ["s-7", 3])
        self.assertEqual(s["by_tool"]["graph_probe"], {"rejected": 4})

    def test_codes_are_format_checked(self):
        self.assertEqual(M.clean_codes(["E-ok", "W-x-1", "I-note", "E-", "Шедулер формирования", "e-low", 5, "E-ok"]),
                         ["E-ok", "W-x-1", "I-note"])

    def test_counters(self):
        self.assertEqual(M.bump(self.root, "assign.attempts"), 1)
        self.assertEqual(M.bump(self.root, "assign.attempts", 2), 3)
        self.assertEqual(M.summarize(self.root)["counters"], {"assign.attempts": 3})
        with self.assertRaises(ValueError):
            M.bump(self.root, "Шедулер формирования")


class NoLeak(_Repo):
    def test_no_source_text_in_counters_or_health(self):
        T.call("graph_status", self.root, {})
        sentence = SPEC.splitlines()[2]
        with self.assertRaises(T.ToolError):
            T.call("graph_init", self.root, {"sources": ["docs/nope"], "exclude": [sentence]})
        T.call("graph_init", self.root, {"sources": ["docs/spec"], "exclude": [sentence]})
        with self.assertRaises(T.ToolError):
            T.call("graph_status", self.root, {sentence: sentence})

        def boom(root, args):
            raise ValueError(SPEC)
        self._fake_tool("graph_probe", boom)
        with self.assertRaises(ValueError):
            T.call("graph_probe", self.root, {"item_id": sentence, "text": SPEC})
        M.record_health(self.root, {"nodes": {"scheduler": 2}, "title": sentence}, findings=[sentence])
        dumped = _dump_tree(self.root)
        self.assertTrue(dumped, "счётчики записаны")
        leaks = [run for run in _four_word_runs(SPEC) if run in " ".join(re.findall(r"\w+", dumped))]
        self.assertEqual(leaks, [], "текст исходника в счётчиках")


class Health(_Repo):
    def test_keeps_30_numbers_only_and_hashes_findings(self):
        for i in range(31):
            M.record_health(self.root, {"nodes": i, "by_kind": {"endpoint": i}, "label": "текст"}, findings=["E-x a"])
        data = json.loads((self.root / M.HEALTH_REL).read_text(encoding="utf-8"))
        self.assertEqual(len(data["runs"]), 30)
        last = data["runs"][-1]
        self.assertEqual(last["metrics"], {"nodes": 30, "by_kind": {"endpoint": 30}})
        self.assertNotIn("E-x a", json.dumps(data, ensure_ascii=False))
        self.assertEqual(len(last["findings"][0]), 12)

    def test_stabilization_streak(self):
        r = M.record_health(self.root, {"n": 1}, findings=["a"])
        self.assertEqual((r["new_findings"], r["stable"]), (1, False))
        for _ in range(2):
            r = M.record_health(self.root, {"n": 1}, findings=["a"])
        self.assertEqual((r["stable_streak"], r["stable"]), (2, False))
        r = M.record_health(self.root, {"n": 1}, findings=[])
        self.assertEqual((r["stable_streak"], r["stable"]), (3, True))
        r = M.record_health(self.root, {"n": 1}, findings=["b"])
        self.assertEqual((r["new_findings"], r["stable_streak"], r["stable"]), (1, 0, False))


class RunKey(_Repo):
    def test_adhoc_then_freshest_manifest(self):
        self.assertEqual(M.current_key(self.root), M.ADHOC)
        base = self.root / "ground" / "statements" / "specgraph"
        for key, age in (("GRAPH-1", 100), ("GRAPH-2", 10)):
            m = base / key / "manifest.json"
            m.parent.mkdir(parents=True)
            m.write_text("{}", encoding="utf-8")
            t = time.time() - age
            os.utime(m, (t, t))
        self.assertEqual(M.current_key(self.root), "GRAPH-2")
        T.call("graph_status", self.root, {})
        self.assertTrue((self.root / M.RUNS_REL / "GRAPH-2" / "calls.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
