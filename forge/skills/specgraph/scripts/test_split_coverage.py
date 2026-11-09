#!/usr/bin/env python3
"""test_split_coverage.py — нарезка в vault и проверка полноты (задача 1.6).

Пинит:
  • фикстура через настоящие очереди: заметка на узел эталона (с таблицей-заглушкой из SQL) по путям онтологии, «не назначено» = 0, дословность
    цела, каждый блок кода исходника целиком внутри маркера заметки, frontmatter — все ключи вида (рёбра
    `[]`), belongs_to, key/method/path/aliases эндпойнта, source — существующий путь + заголовок;
  • ссылки: якорь `](#ручная-заявка)` → `[[#5.1 Ручная заявка|…]]`, картинка → копия в `_attachments/` и
    `![[…|alt]]`; обратное переписывание даёт исходник;
  • повторная нарезка — 0 изменённых файлов; проза человека вне маркеров, его ключ FM и рёбра в FM
    переживают повторную нарезку;
  • мутанты: блок выпал из плана → E-coverage с id блока; правка внутри маркера source → E-verbatim;
  • _graph/index.md — реестр с путями, которые существуют; _graph/unplaced.md — неразмещённое дословно;
  • стресс-корпус: «модель-оракул» проходит весь graph-map на каждом варианте с coverage = ok —
    «не назначено» 0 и 0 ошибок дословности (CRLF/BOM — побайтно).
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import _testkit as K  # noqa: E402
import gen_variants as GV  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import blocks as B  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import tools as T  # noqa: E402

G = json.loads((K.FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
MATRIX = json.loads((K.FIX / "variants.json").read_text(encoding="utf-8"))


class _Split(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")
        self.res = K.through_split(self.root)
        self.vault = self.root / "docs" / "graph"

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, rel):
        return F.read(SP.read_note(self.vault / rel))


class Fixture(_Split):
    def test_notes_coverage_verbatim(self):
        self.assertEqual((self.res["notes"], self.res["unassigned"], self.res["unplaced"]), (len(G["nodes"]), 0, 0))
        self.assertEqual(self.res["verify"]["errors"], 0, self.res["verify"]["first"])
        paths = {p.relative_to(self.vault).as_posix() for p in self.vault.rglob("*.md") if "_graph" not in p.parts}
        self.assertEqual(len(paths), len(G["nodes"]))
        self.assertIn("services/task-service/schedulers/Шедулер закрытия регулярных задач.md", paths)
        stub, stub_body = self.note("data/table/task.md")        # таблица из SQL: заглушка без текста спеки
        self.assertEqual((stub["kind"], stub["subkind"], stub["key"], stub["status"]), ("data", "table", "table:task", "stub"))
        self.assertNotIn("graph:source", stub_body)
        self.assertIn("data/attributes/Динамические атрибуты укомплектования.md", paths)
        bodies = "".join(SP.read_note(self.vault / p) for p in paths)
        for rel in {it.split("#")[0] for it in AS.load_plans(self.root)[0]["assign"][0]["blocks"]} | {
                "docs/spec/task-service/task-service.md"}:
            for b in B.load(self.root, rel)["blocks"]:
                if b["type"] == "fence":
                    self.assertIn(b["text"].strip("\n"), bodies, f"фенс {b['id']} целиком в заметке")

    def test_frontmatter(self):
        data, body = self.note("services/task-service/endpoints/task-service GET api-v1-tasks.md")
        self.assertEqual((data["kind"], data["key"], data["method"], data["path"]),
                         ("endpoint", "http:GET /api/v1/tasks", "GET", "/api/v1/tasks"))
        self.assertEqual(data["aliases"], ["GET /api/v1/tasks"])
        self.assertEqual(data["belongs_to"], ["[[task-service]]"])
        self.assertNotIn("called_by", data, "пустой ключ-ребро не пишется")
        self.assertEqual(data["status"], "draft")
        src, _, head = data["source"][0].partition("#")
        self.assertTrue((self.root / src).is_file())
        self.assertEqual(head, "4.1 GET /api/v1/tasks — список задач сотрудника")
        op, _ = self.note("business/operations/Укомплектование.md")
        self.assertEqual(op["key"], "task:KOMPLEKT")
        self.assertFalse({"created_by", "closed_by", "screens", "aliases"} & set(op), "пустые свойства не пишутся")
        svc, sbody = self.note("services/task-service/task-service.md")
        self.assertIsNone(F.get_block(sbody, "toc"), "строку ссылок по видам сменил обзор сервиса")
        ov = F.get_block(sbody, "overview")["content"]
        self.assertIn("[[Шедулер закрытия регулярных задач]]", ov)
        self.assertLess(sbody.index("graph:overview:start"), sbody.index("graph:source:start"), "обзор — сразу под именем")

    def test_links_rewritten_and_reversible(self):
        _, body = self.note("services/task-service/algorithms/Алгоритм балансировки упаковок.md")
        self.assertIn("[[#5.1 Ручная заявка|формируем ручную заявку]]", body)
        _, sbody = self.note("services/task-service/task-service.md")
        m = re.search(r"!\[\[([0-9a-f]{8}-task-flow\.png)\|Схема задач\]\]", sbody)
        self.assertTrue(m, sbody[:400])
        self.assertTrue((self.vault / "_attachments" / m.group(1)).is_file())
        rewrites = [r for r in json.loads((self.root / SP.REWRITES_REL).read_text(encoding="utf-8")) if "insert" not in r]
        self.assertEqual(len(rewrites), 2)
        self.assertEqual(SP.unrewrite(rewrites[0]["to"], rewrites), rewrites[0]["from"])

    def test_index_and_unplaced(self):
        idx = (self.vault / "_graph" / "index.md").read_text(encoding="utf-8")
        rows = [l for l in idx.splitlines() if l.startswith("| [[")]
        self.assertEqual(len(rows), len(G["nodes"]))
        for r in rows:
            self.assertTrue((self.vault / r.rstrip(" |").rsplit("| ", 1)[1]).is_file(), r)
        self.assertIn("_нет_", (self.vault / "_graph" / "unplaced.md").read_text(encoding="utf-8"))


class Idempotent(_Split):
    def test_resplit_keeps_human_work(self):
        again = T.call("graph_split", self.root, {})
        self.assertEqual(again["written"], 0)
        p = self.vault / "services/task-service/schedulers/Шедулер закрытия регулярных задач.md"
        data, body = F.read(SP.read_note(p))
        data["closes_task"] = ["[[Укомплектование]]"]
        data["Ответственный"] = "аналитик"
        body = body + "\nПримечание аналитика: проверить расписание.\n"
        p.write_bytes(F.write(data, body).encode("utf-8"))
        T.call("graph_split", self.root, {})
        data2, body2 = F.read(SP.read_note(p))
        self.assertEqual(data2["closes_task"], ["[[Укомплектование]]"])
        self.assertEqual(data2["Ответственный"], "аналитик")
        self.assertIn("Примечание аналитика", body2)
        self.assertEqual(T.call("graph_split", self.root, {})["written"], 0)


class Mutants(_Split):
    def test_lost_block_and_edited_marker(self):
        plan_p = sorted((self.root / AS.PLAN_REL).glob("*.json"))[0]
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        lost = plan["assign"][0]["blocks"].pop()
        plan_p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        errs = SP.verify(self.root, {"vault": "docs/graph"})
        self.assertIn(("E-coverage", f"блок {lost} не назначен ни узлу, ни skip, ни unplaced"), errs)
        p = self.vault / "services/task-service/schedulers/Шедулер закрытия регулярных задач.md"
        p.write_bytes(SP.read_note(p).replace("23:00", "22:00").encode("utf-8"))
        codes = [c for c, _ in SP.verify(self.root, {"vault": "docs/graph"})]
        self.assertIn("E-verbatim", codes)


class Variants(unittest.TestCase):
    def test_oracle_map_on_every_variant(self):
        with tempfile.TemporaryDirectory() as td:
            for v, row in MATRIX["variants"].items():
                if row["coverage"] != "ok":
                    continue
                with self.subTest(variant=v):
                    root = K.make_project(Path(td) / v, GV.materialize(Path(td) / "_v", v))
                    res = K.oracle_split(root)
                    self.assertEqual((res["notes"], res["unassigned"], res["verify"]["errors"]), (len(G["nodes"]), 0, 0),
                                     res["verify"]["first"])


if __name__ == "__main__":
    unittest.main()
