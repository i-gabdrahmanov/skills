#!/usr/bin/env python3
"""test_work_queue.py — очередь работ: задачи собирает движок, модель исполняет по одной (задача 0.6).

Пинит:
  • одинаковые спецификации единиц → побайтно одинаковая очередь; вход сверх бюджета — отказ сборки;
  • аренда: `graph_next` выдаёт единицу с брифом, путём ко входу, инструментом и прогрессом; при
    parallel = 1 повторный вызов до сдачи — та же единица (продолжение после обрыва без пропусков);
    истёкшая аренда возвращает единицу в pending; пустая очередь — «пусто»;
  • инструмент результата (`x-queue`): принятый результат закрывает единицу, отказ — попытка + 1, после
    max_attempts — abandoned с последними кодами (в отчёт человеку, счётчик abandoned); чужой,
    неизвестный или закрытый item_id — отказ E-queue-item ДО работы инструмента;
  • пересборка бережная: закрытое с тем же входом остаётся закрытым, изменившийся вход переоткрывает
    единицу, исчезнувшая незакрытая уходит;
  • гейт `closed`: всё done/abandoned, брошенные перечислены.
Данные — синтетические единицы во временном каталоге; время — подменённые часы.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import metrics as M  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import tools as T  # noqa: E402


def _specs(n=3, text="вход"):
    return [{"id": f"task-service.md#s{i}", "brief": "assign-section.md", "tool": "graph_probe_put",
             "input": f"{text} {i}\n"} for i in range(n)]


class _Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = [1000.0]
        self._clock = Q._clock
        Q._clock = lambda: self.now[0]
        self._saved = dict(T.TOOLS)
        self.calls = []

        def probe(root, args):
            self.calls.append(args["item_id"])
            if args.get("bad"):
                raise T.ToolError("план не прошёл", ["E-plan-lost-block"])
            return {"accepted": True}
        T.TOOLS["graph_probe_put"] = {
            "fn": probe, "description": "test",
            "schema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bad": {"type": "boolean"}},
                       "required": ["item_id"], "x-queue": "assign-section", "additionalProperties": False}}

    def tearDown(self):
        Q._clock = self._clock
        T.TOOLS.clear()
        T.TOOLS.update(self._saved)
        self.tmp.cleanup()

    def next(self):
        return T.call("graph_next", self.root, {"queue": "assign-section"})


class Build(_Repo):
    def test_same_specs_same_bytes(self):
        Q.build(self.root, "assign-section", _specs())
        a = (self.root / Q.QUEUE_REL / "assign-section.json").read_bytes()
        Q.build(self.root, "assign-section", _specs())
        self.assertEqual((self.root / Q.QUEUE_REL / "assign-section.json").read_bytes(), a)

    def test_over_budget_and_bad_ids_refuse_the_build(self):
        big = _specs(1, "x" * 30000)
        with self.assertRaises(Q.QueueError) as cm:
            Q.build(self.root, "assign-section", big)
        self.assertEqual(cm.exception.code, "E-queue-build")
        self.assertIn("бюджет", str(cm.exception))
        with self.assertRaises(Q.QueueError):
            Q.build(self.root, "assign-section", _specs(1, "x" * 100), cfg={"queue": {"budget_chars": 50}})
        with self.assertRaises(Q.QueueError):
            Q.build(self.root, "assign-section", _specs(1, "x"), brief_chars={"assign-section.md": 24000})
        with self.assertRaises(Q.QueueError):
            Q.build(self.root, "assign-section", [{"id": "с пробелом", "input": ""}])
        with self.assertRaises(Q.QueueError):
            Q.build(self.root, "assign-section", _specs(1) + _specs(1))
        with self.assertRaises(Q.QueueError):
            Q.build(self.root, "nope", _specs(1))
        self.assertFalse((self.root / Q.QUEUE_REL / "assign-section.json").exists(), "отказ ничего не пишет")

    def test_rebuild_keeps_closed_reopens_changed_drops_vanished(self):
        Q.build(self.root, "assign-section", _specs(3))
        for _ in range(2):
            T.call("graph_probe_put", self.root, {"item_id": self.next()["item_id"]})
        specs = _specs(3)
        specs[1]["input"] = "изменилось\n"
        specs = specs[:2] + [{"id": "new#1", "brief": "b", "tool": "graph_probe_put", "input": "n"}]
        Q.build(self.root, "assign-section", specs)
        st = {it["id"]: it["status"] for it in json.loads(
            (self.root / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))["items"]}
        self.assertEqual(st, {"task-service.md#s0": "done", "task-service.md#s1": "pending", "new#1": "pending"})


class Lease(_Repo):
    def test_cycle_until_empty(self):
        Q.build(self.root, "assign-section", _specs(2))
        first = self.next()
        self.assertEqual((first["status"], first["item_id"], first["progress"]), ("единица", "task-service.md#s0", "1 из 2"))
        self.assertEqual(first["tool"], "graph_probe_put")
        self.assertEqual((self.root / first["input"]).read_text(encoding="utf-8"), "вход 0\n")
        self.assertIn("item_id «task-service.md#s0»", first["next"])
        T.call("graph_probe_put", self.root, {"item_id": first["item_id"]})
        second = self.next()
        self.assertEqual((second["item_id"], second["progress"]), ("task-service.md#s1", "2 из 2"))
        T.call("graph_probe_put", self.root, {"item_id": second["item_id"]})
        self.assertEqual(self.next()["status"], Q.EMPTY)
        self.assertEqual(Q.closed(self.root, "assign-section"), (True, []))

    def test_resume_after_crash_returns_the_same_item(self):
        Q.build(self.root, "assign-section", _specs(2))
        a = self.next()
        b = self.next()                       # оркестратор «оборвался» и начал заново
        self.assertEqual(b["item_id"], a["item_id"])
        self.assertTrue(b["repeat"])
        self.assertEqual(Q.closed(self.root, "assign-section")[0], False)

    def test_expired_lease_goes_back_to_pending(self):
        Q.build(self.root, "assign-section", _specs(2), cfg=None)
        a = self.next()
        self.now[0] += Q.DEFAULTS["lease_s"] + 1
        st = Q.status(self.root, "assign-section")
        b = self.next()
        self.assertEqual(b["item_id"], a["item_id"])
        self.assertFalse(b["repeat"], "после истечения — новая аренда, не повтор")
        self.assertEqual(st["leased"], 1)

    def test_parallel_two(self):
        cfg_path = self.root / ".spec-graph" / "config.json"
        cfg_path.parent.mkdir(parents=True)
        (self.root / "docs").mkdir()
        cfg_path.write_text(json.dumps({"version": 1, "vault": "graph", "sources": [{"root": "docs"}],
                                        "queue": {"parallel": 2}}), encoding="utf-8")
        Q.build(self.root, "assign-section", _specs(3))
        ids = {self.next()["item_id"], self.next()["item_id"]}
        self.assertEqual(len(ids), 2)
        self.assertTrue(self.next()["repeat"])


class Results(_Repo):
    def test_rejections_then_abandoned(self):
        Q.build(self.root, "assign-section", _specs(1))
        for attempt in (1, 2, 3):
            it = self.next()
            self.assertEqual(it["attempt"], attempt)
            with self.assertRaises(T.ToolError):
                T.call("graph_probe_put", self.root, {"item_id": it["item_id"], "bad": True})
        self.assertEqual(self.next()["status"], Q.EMPTY)
        ok, abandoned = Q.closed(self.root, "assign-section")
        self.assertEqual((ok, abandoned), (True, ["task-service.md#s0"]))
        st = Q.status(self.root, "assign-section")
        self.assertEqual(st["abandoned_items"], [{"item_id": "task-service.md#s0", "codes": ["E-plan-lost-block"]}])
        self.assertEqual(M.summarize(self.root)["counters"], {"queue.assign-section.abandoned": 1})
        self.assertEqual(M.summarize(self.root)["most_rejected_refs"][0], ["task-service.md#s0", 3])

    def test_foreign_unknown_or_closed_item_is_refused_before_the_tool_runs(self):
        Q.build(self.root, "assign-section", _specs(2))
        it = self.next()
        for bad in ("nope#1", "task-service.md#s1"):          # неизвестная и не арендованная
            with self.assertRaises(T.ToolError) as cm:
                T.call("graph_probe_put", self.root, {"item_id": bad})
            self.assertEqual(cm.exception.codes, ["E-queue-item"])
        T.call("graph_probe_put", self.root, {"item_id": it["item_id"]})
        with self.assertRaises(T.ToolError):                   # уже закрыта
            T.call("graph_probe_put", self.root, {"item_id": it["item_id"]})
        self.assertEqual(self.calls, [it["item_id"]], "инструмент не запускался на чужих единицах")

    def test_engine_error_keeps_the_lease(self):
        Q.build(self.root, "assign-section", _specs(1))

        def boom(root, args):
            raise RuntimeError("сбой движка")
        T.TOOLS["graph_probe_put"]["fn"] = boom
        it = self.next()
        with self.assertRaises(RuntimeError):
            T.call("graph_probe_put", self.root, {"item_id": it["item_id"]})
        st = Q.status(self.root, "assign-section")
        self.assertEqual((st["leased"], st["pending"]), (1, 0), "попытка не списана: вина не модели")

    def test_queue_name_is_checked(self):
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_next", self.root, {"queue": "everything"})
        self.assertEqual(cm.exception.codes, ["E-queue-name"])
        self.assertEqual(T.call("graph_queue_status", self.root, {"queue": "card"})["total"], 0)


if __name__ == "__main__":
    unittest.main()
