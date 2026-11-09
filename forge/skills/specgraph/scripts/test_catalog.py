#!/usr/bin/env python3
"""test_catalog.py — дедуп и каталог узлов, решение человека до нарезки (задача 1.4).

Пинит:
  • сведение черновиков фикстуры: 29 узлов эталона; топик из двух файлов и операции из справочника и
    из бизнес-описаний склеены по ключу; источники копятся;
  • ловушки ТЗ: общий шедулер закрытия — один узел; «закрытие регулярных» и «закрытие передачи» — два
    узла, и пара стоит в «это один узел?», но не склеена;
  • решения человека: merge, split, rename, move, drop, scope; повтор того же решения не дублирует
    журнал; каталог с решениями воспроизводим; _graph/decisions.md пишется;
  • детерминизм: та же сборка — побайтно тот же catalog.json и catalog.md;
  • согласие graph-catalog: нет → none; record_approval с --bind-file на catalog.json → ok и снимок
    catalog.approved.json; правка решений → каталог изменился → stale, утверждённым снимок не считается;
  • противоречия черновиков (тот же ключ, разный сервис) и одинаковые имена разных узлов — в отчёте.
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

import _testkit as K  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import survey as SV  # noqa: E402
from sgraph import tools as T  # noqa: E402

G = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
RECORD = SCRIPTS.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"


def approve(root: Path, key: str, bind: str):
    return subprocess.run([sys.executable, "-X", "utf8", str(RECORD), "--project", str(root), "--key", key,
                           "--approved-by", "user", "--reason", "каталог утверждён", "--evidence", "да, утверждаю каталог",
                           "--bind-file", bind], capture_output=True, text=True, encoding="utf-8")


class _Proj(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")
        T.call("graph_survey", self.root, {})
        K.run_queue(self.root, "draft-file", K.draft_answer(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def cat(self):
        return json.loads((self.root / CT.CATALOG_REL).read_text(encoding="utf-8"))


class Build(_Proj):
    def test_fixture_catalog(self):
        res = T.call("graph_catalog", self.root, {})
        self.assertEqual(res["nodes"], 30)              # + таблица task из SQL (заглушка)
        cat = self.cat()
        self.assertEqual({(n["kind"], n["name"]) for n in cat["nodes"]}, {(n["kind"], n["name"]) for n in G["nodes"]})
        topic = next(n for n in cat["nodes"] if n["name"] == "pprbkid.upz.task.response.v1")
        self.assertEqual(sorted(s["file"] for s in topic["sources"]),
                         ["docs/spec/task-service/task-service.md", "docs/spec/upz-adapter/upz-adapter.md"])
        op = next(n for n in cat["nodes"] if n["name"] == "Укомплектование")
        self.assertEqual(len(op["sources"]), 2, "справочник task-service + бизнес-описание — один узел по ключу")
        self.assertEqual(sum(1 for n in cat["nodes"] if "закрытия регулярных" in n["name"]), 1)
        pair = {"Шедулер закрытия регулярных задач", "Шедулер закрытия задачи передачи упаковок в СЦ-архив"}
        self.assertTrue(any({r["a"], r["b"]} == pair for r in cat["review"]), cat["review"])
        self.assertEqual(cat["conflicts"], [])
        md = (self.root / "docs" / "graph" / "_graph" / "catalog.md").read_text(encoding="utf-8")
        for needle in ("## Шедулер — 4", "Это один узел?", "## Объём работ (из разведки)", "Кандидаты в новые виды"):
            self.assertIn(needle, md)
        self.assertEqual(res["consent"], "none")

    def test_deterministic(self):
        T.call("graph_catalog", self.root, {})
        a = (self.root / CT.CATALOG_REL).read_bytes()
        m = (self.root / "docs" / "graph" / "_graph" / "catalog.md").read_bytes()
        T.call("graph_catalog", self.root, {})
        self.assertEqual((self.root / CT.CATALOG_REL).read_bytes(), a)
        self.assertEqual((self.root / "docs" / "graph" / "_graph" / "catalog.md").read_bytes(), m)


class Decisions(_Proj):
    def names(self):
        return {n["name"]: n for n in self.cat()["nodes"]}

    def test_actions(self):
        T.call("graph_catalog_decide", self.root, {"actions": [
            {"action": "rename", "node": "UI регистрации", "name": "Экран регистрации", "why": "так зовут аналитики"},
            {"action": "merge", "into": "Шедулер закрытия регулярных задач",
             "names": ["Шедулер закрытия задачи передачи упаковок в СЦ-архив"], "why": "проверка механики"},
            {"action": "move", "node": "Алгоритм балансировки упаковок", "service": "rmoc-service", "why": "проверка"},
            {"action": "drop", "node": "УПЗ", "why": "внешнюю систему не показываем"},
            {"action": "scope", "include": ["task-service"], "why": "начинаем с одного сервиса"},
        ]})
        T.call("graph_catalog", self.root, {})
        n = self.names()
        self.assertIn("Экран регистрации", n)
        self.assertNotIn("UI регистрации", n)
        closer = n["Шедулер закрытия регулярных задач"]
        self.assertIn("Шедулер закрытия задачи передачи упаковок в СЦ-архив", closer["names_seen"])
        self.assertEqual(n["Алгоритм балансировки упаковок"]["service"], "rmoc-service")
        self.assertNotIn("УПЗ", n)
        self.assertEqual(self.cat()["scope"], ["task-service"])
        self.assertFalse(n["rmoc-service GET rmoc-packages"]["in_scope"])
        self.assertTrue(n["task-service GET api-v1-tasks"]["in_scope"])
        self.assertTrue(n["Укомплектование"]["in_scope"], "узлы без сервиса-владельца — в любом scope")
        md = (self.root / "docs" / "graph" / "_graph" / "decisions.md").read_text(encoding="utf-8")
        self.assertIn("внешнюю систему не показываем", md)

    def test_split_and_idempotent_log(self):
        act = {"action": "split", "node": "pprbkid.upz.task.response.v1", "file": "docs/spec/upz-adapter/upz-adapter.md",
               "name": "pprbkid.upz.task.response.v2", "why": "проверка"}
        T.call("graph_catalog_decide", self.root, {"actions": [act]})
        res = T.call("graph_catalog_decide", self.root, {"actions": [act]})
        self.assertEqual(res["added"], 0)
        T.call("graph_catalog", self.root, {})
        n = self.names()
        self.assertEqual([s["file"] for s in n["pprbkid.upz.task.response.v2"]["sources"]],
                         ["docs/spec/upz-adapter/upz-adapter.md"])
        self.assertEqual([s["file"] for s in n["pprbkid.upz.task.response.v1"]["sources"]],
                         ["docs/spec/task-service/task-service.md"])

    def test_bad_action(self):
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_catalog_decide", self.root, {"actions": [{"action": "explode"}, {"action": "rename", "node": "x"}]})
        self.assertEqual(cm.exception.codes, ["E-catalog-action"])


class Consent(_Proj):
    def test_bound_approval_snapshot_and_stale(self):
        res = T.call("graph_catalog", self.root, {})
        self.assertFalse(CT.approved(self.root)[0])
        self.assertIn("--bind-file .spec-graph/catalog.json", T.call("graph_catalog", self.root, {})["next"])
        r = approve(self.root, res["key"], ".spec-graph/catalog.json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        res = T.call("graph_catalog", self.root, {})
        self.assertEqual(res["consent"], "ok")
        self.assertTrue((self.root / CT.APPROVED_REL).is_file())
        self.assertEqual(CT.approved(self.root), (True, ""))
        T.call("graph_catalog_decide", self.root, {"actions": [{"action": "drop", "node": "УПЗ", "why": "x"}]})
        res = T.call("graph_catalog", self.root, {})
        self.assertEqual(res["consent"], "stale")
        self.assertTrue(CT.approved(self.root)[0], "старый снимок по-прежнему соответствует старому согласию")
        (self.root / CT.APPROVED_REL).write_bytes((self.root / CT.CATALOG_REL).read_bytes())
        ok, why = CT.approved(self.root)
        self.assertFalse(ok, "подменённый снимок без согласия не утверждён")
        self.assertIn("stale", why)


class Reports(_Proj):
    def test_conflicts_and_collisions(self):
        d = SV.load_drafts(self.root)[0]
        extra = {"item_id": "draft-extra", "file": "docs/spec/extra.md", "not_nodes": [{"candidate": "x", "reason": "r",
                                                                                         "suggested_kind": "rabbit-op"}],
                 "nodes": [{"kind": "endpoint", "name": "upz-adapter POST api-v1-upz-tasks", "service": "task-service",
                            "key": "http:POST /api/v1/upz/tasks"},
                           {"kind": "data", "subkind": "dto", "name": "pprbkid.task.closed.v1", "key": "kafka:pprbkid.task.closed.v1"},
                           {"kind": "screen", "name": "Укомплектование"}]}
        (self.root / SV.DRAFTS_REL / "draft-extra.json").write_text(json.dumps(extra, ensure_ascii=False), encoding="utf-8")
        # каталог читает черновики только сданных единиц текущей разведки — лишняя единица в очереди, сдана
        qp = self.root / ".spec-graph" / "queue" / "draft-file.json"
        q = json.loads(qp.read_text(encoding="utf-8"))
        q["items"].append({"id": "draft-extra", "brief": SV.BRIEF, "tool": SV.TOOL, "refs": {"file": "docs/spec/extra.md"},
                           "input_sha": "", "status": "done", "attempts": 0, "lease_until": 0, "last_codes": []})
        qp.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(d)
        T.call("graph_catalog", self.root, {})
        cat = self.cat()
        # тот же ключ у узла не того вида — противоречие; эндпойнт того же пути у другого сервиса — отдельный
        # узел, и одно имя у двух узлов уходит человеку коллизией (ревью 2026-11-08)
        self.assertTrue(any(c["field"] == "kind" for c in cat["conflicts"]))
        self.assertTrue(any(c["name"] == "upz-adapter POST api-v1-upz-tasks" for c in cat["collisions"]))
        self.assertTrue(any(c["name"] == "Укомплектование" for c in cat["collisions"]))
        md = (self.root / "docs" / "graph" / "_graph" / "catalog.md").read_text(encoding="utf-8")
        self.assertIn("rabbit-op", md)


if __name__ == "__main__":
    unittest.main()
