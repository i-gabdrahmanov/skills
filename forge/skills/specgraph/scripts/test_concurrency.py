#!/usr/bin/env python3
"""test_concurrency.py — параллельные субагенты: блокировка проекта, пачки, атомарная запись.

Пинит:
  • 4 процесса одновременно сдают результаты одной пачки link-note (как 4 субагента) — итог байт-в-байт тот
    же, что у последовательного прогона: ни одно предложение не потеряно, очередь закрыта целиком, журнал
    вызовов цел;
  • блокировка межпроцессная: пока её держит один процесс, вызов из другого ждёт; дольше таймаута — отказ
    E-busy, а не порча состояния;
  • повторный вход в пределах процесса не блокирует сам себя;
  • атомарная запись не оставляет временных файлов.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import fsutil as FS  # noqa: E402
from sgraph import metrics as M  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import tools as T  # noqa: E402

WORKER = """
import json, sys
sys.path.insert(0, {scripts!r})
import _testkit as K
from sgraph import tools as T
from pathlib import Path
root = Path({root!r})
unit = json.loads({unit!r})
args = K.link_answer(root)({{"item_id": unit["item_id"]}})
T.call({tool!r}, root, dict(args, item_id=unit["item_id"]))
"""


def edges_state(root: Path) -> dict:
    d = root / ".spec-graph" / "edges"
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.json"))}


class Parallel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_four_subagents_at_once_equal_sequential(self):
        seq = K.make_project(Path(self.tmp.name) / "seq")
        K.through_link(seq)
        par = K.make_project(Path(self.tmp.name) / "par")
        K.through_split(par)
        T.call("graph_edges_mechanical", par, {})
        T.call("graph_link_start", par, {})
        batches = 0
        while True:
            res = T.call("graph_next", par, {"queue": "link-note", "batch": True})
            if res["status"] == Q.EMPTY:
                break
            batches += 1
            self.assertLessEqual(len(res["units"]), 4)
            procs = [subprocess.Popen([sys.executable, "-X", "utf8", "-c",
                                       WORKER.format(scripts=str(SCRIPTS), root=str(par), unit=json.dumps(u), tool=res["tool"])],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for u in res["units"]]
            for p in procs:
                out, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, err.decode("utf-8", "replace")[-1500:])
            self.assertLess(batches, 30, "пачки не кончаются")
        st = Q.status(par, "link-note")
        self.assertEqual((st["done"], st["pending"], st["leased"]), (st["total"], 0, 0))
        self.assertGreaterEqual(batches, 5)
        self.assertEqual(edges_state(par), edges_state(seq), "параллельная сдача не потеряла ни одного предложения")
        calls = M.read_calls(par)
        self.assertEqual(sum(1 for c in calls if c["tool"] == "graph_link_put" and c["outcome"] == "ok"), st["total"])
        leftovers = [p for p in (par / ".spec-graph").rglob("*.tmp")]
        self.assertEqual(leftovers, [], "атомарная запись не оставляет временных файлов")


class Lock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def child(self, code: str, timeout_s: str):
        env = dict(os.environ, SGRAPH_LOCK_TIMEOUT_S=timeout_s)
        script = f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n" + code
        return subprocess.run([sys.executable, "-X", "utf8", "-c", script], capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=60)

    def test_cross_process_busy_then_free(self):
        call = (f"from pathlib import Path\nfrom sgraph import tools as T\n"
                f"try:\n    T.call('graph_status', Path({str(self.root)!r}), {{}})\n    print('ok')\n"
                f"except T.ToolError as e:\n    print(e.codes)\n")
        with FS.project_lock(self.root):
            r = self.child(call, "0.3")
            self.assertEqual(r.stdout.strip(), "['E-busy']", r.stderr)
            orig, FS.TIMEOUT_S = FS.TIMEOUT_S, 2.0           # самоблокировка упала бы за 2 с, а не висела
            try:
                with FS.project_lock(self.root):            # повторный вход тем же потоком — без самоблокировки
                    T.call("graph_status", self.root, {})
            finally:
                FS.TIMEOUT_S = orig
        r = self.child(call, "0.3")
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)

    def test_waits_instead_of_failing(self):
        import threading
        import time
        hold = threading.Event()

        def holder():
            with FS.project_lock(self.root):
                hold.set()
                time.sleep(0.6)
        th = threading.Thread(target=holder)
        th.start()
        hold.wait(5)
        r = self.child(f"from pathlib import Path\nfrom sgraph import tools as T\n"
                       f"T.call('graph_status', Path({str(self.root)!r}), {{}}); print('ok')", "10")
        th.join()
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)

    def test_atomic_write(self):
        p = Path(self.tmp.name) / "a" / "b.json"
        FS.write_text(p, "один", encoding="utf-8")
        FS.write_text(p, "два", encoding="utf-8")
        self.assertEqual(p.read_text(encoding="utf-8"), "два")
        self.assertEqual(sorted(x.name for x in p.parent.iterdir()), ["b.json"])


if __name__ == "__main__":
    unittest.main()
