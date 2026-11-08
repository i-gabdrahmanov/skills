#!/usr/bin/env python3
"""Регрессии ревью spec-graph 2026-11-08: полнота и дословность «по построению» держались не везде.

Пинит (фикстура tz-mini и синтетика рядом, ответы модели — эталон через очереди):
  • закрытый фенс с `# комментарием` внутри (bash, YAML, без языка) — один блок без W-fence-unclosed; незакрытый
    фенс по-прежнему кончается перед заголовком раздела, в том числе когда дальше идёт фенс с языком;
  • файл разведки, где модель не нашла ни одного узла, получает единицу раскладки и попадает в покрытие: его
    текст — в _graph/unplaced.md, а не мимо vault при гейте «нарезка полная»;
  • правка исходника после приёмки видна: E-source-changed, «перенесён полностью» снят, гейты map и check не
    проходят, раскладка по устаревшим блокам — отказ;
  • результаты единиц прошлой сборки очереди не читаются: план исчезнувшей единицы не кладёт блок второй раз,
    черновики прошлой разведки не попадают в каталог при узком scope;
  • SQL: `SELECT … INTO v`, FROM в аргументах функции и `IS DISTINCT FROM` — не таблицы, подзапросы — таблицы;
  • отчёт связей называет каждую живую связь, которую утверждает «да» (раздел 7 «Прочие связи»): раньше
    `invokes` модели с high и `uses_data` правил не было в нём ни строкой;
  • готовые команды (согласие, прогон) с путём, где пробел, разбираются shell в один аргумент;
  • исходник не в UTF-8 — отказ инструмента с именем файла, а не трейсбек.
"""
from __future__ import annotations

import json
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "tools"))

import _testkit as K  # noqa: E402
import test_e2e as E2E  # noqa: E402
from sgraph import assign as AS  # noqa: E402
from sgraph import blocks as B  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import consent as CS  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import review as RV  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import sqlref as SQ  # noqa: E402
from sgraph import status as ST  # noqa: E402
from sgraph import tools as T  # noqa: E402
from sgraph import validate as VA  # noqa: E402

GENERAL = "общее/общие-требования.md"
GENERAL_TEXT = ("# Общие требования\n\nВсе сервисы пишут операции в журнал аудита, срок хранения — 5 лет.\n\n"
                "## Безопасность\n\nДоступ к API только по mTLS, токены живут 15 минут.\n")


def _types(text: str):
    blocks, warnings = B.parse(text, "f.md")
    assert "".join(b["text"] for b in blocks) == text
    return [b["type"] for b in blocks], [w["code"] for w in warnings]


def _set_budget(root: Path, chars: int) -> None:
    p = root / ".spec-graph" / "config.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("queue", {})["budget_chars"] = chars
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def _queue_items(root: Path, name: str):
    return {it["id"]: it for it in json.loads((root / Q.QUEUE_REL / f"{name}.json").read_text(encoding="utf-8"))["items"]}


class Fences(unittest.TestCase):
    def test_hash_comment_inside_closed_fence_is_code(self):
        for lang in ("bash", "yaml", ""):
            text = f"## Настройка\n\n```{lang}\n# установить зависимости\npip install x\n\n# DB\n\nrun\n```\n\nТекст после.\n"
            self.assertEqual(_types(text), (["heading", "fence", "paragraph"], []), lang or "без языка")

    def test_markdown_example_in_longer_fence(self):
        text = "## Пример\n\n````markdown\n# Заголовок\n\n```json\n{}\n```\n````\n\nпосле\n"
        self.assertEqual(_types(text), (["heading", "fence", "paragraph"], []))

    def test_unclosed_fence_still_stops_at_section_heading(self):
        text = "### 3.1 О\n\n```json\n{\"a\": 1}\n\n### 3.2 Отправка\n\n```json\n{}\n```\n"
        blocks, warnings = B.parse(text, "k.md")
        self.assertEqual([b["type"] for b in blocks], ["heading", "fence", "heading", "fence"])
        self.assertEqual([w["code"] for w in warnings], ["W-fence-unclosed"])
        text = "### 3.1 О\n\n```json\n{\"a\": 1}\n\n### 3.2 Отправка\n\nтекст\n\n```\ncode\n```\n"
        heads = [b["title"] for b in B.parse(text, "k.md")[0] if b["type"] == "heading"]
        self.assertIn("3.2 Отправка", heads, "раздел после незакрытого json-фенса не проглочен")


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()


class FileWithoutNodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        src = Path(self.tmp.name) / "src"
        shutil.copytree(K.FIX / "sources", src)
        (src / GENERAL).parent.mkdir()
        (src / GENERAL).write_text(GENERAL_TEXT, encoding="utf-8")
        self.root = K.make_project(Path(self.tmp.name) / "repo", src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_reaches_vault_as_unplaced(self):
        rel = f"{K.SRC_REL}/{GENERAL}"
        T.call("graph_survey", self.root, {})
        base = K.draft_answer(self.root)
        drafts = _queue_items(self.root, "draft-file")
        K.run_queue(self.root, "draft-file", lambda u: {"nodes": [], "not_nodes": []}
                    if drafts[u["item_id"]]["refs"]["file"] == rel else base(u))
        K.approve_catalog(self.root)
        self.assertFalse(any(s["file"] == rel for n in CT.load_approved(self.root)["nodes"] for s in n["sources"]))
        T.call("graph_assign_start", self.root, {})
        units = _queue_items(self.root, "assign-section")
        mine = [u for u in units.values() if u["refs"]["file"] == rel]
        self.assertTrue(mine, "у файла без узлов есть единица раскладки")
        cov = SP.coverage(self.root, T._need_cfg(self.root))
        self.assertIn(rel, cov["files"], "файл без узлов — в покрытии, пока его блоки не разложены")
        self.assertGreater(cov["files"][rel]["unassigned"], 0)
        plan = K.assign_answer(self.root)
        K.run_queue(self.root, "assign-section", lambda u: {"assign": [], "unplaced": [{"blocks": units[u["item_id"]]["refs"]["blocks"]}]}
                    if units[u["item_id"]]["refs"]["file"] == rel else plan(u))
        T.call("graph_split", self.root, {})
        code, out = E2E.gate(self.root, "map")
        self.assertEqual(code, 0, out)
        cfg = T._need_cfg(self.root)
        self.assertIn("mTLS", (self.root / cfg["vault"] / "_graph" / "unplaced.md").read_text(encoding="utf-8"))
        transfer = {t["file"]: t for t in VA.validate(self.root, cfg, write=False)["transfer"]}
        self.assertIn(rel, transfer)


class SourceChangedAfterAcceptance(Project):
    def test_change_is_seen_by_validator_gates_and_assign(self):
        codes = E2E.whole_graph(self.root)
        self.assertEqual([c for c, _ in codes[:3]], [0, 0, 0])
        rel = f"{K.SRC_REL}/task-service/task-service.md"
        src = self.root / rel
        src.write_text(src.read_text(encoding="utf-8") + "\n## Новый раздел\n\nТекста нет в графе.\n", encoding="utf-8")
        cfg = T._need_cfg(self.root)
        res = VA.validate(self.root, cfg, write=False)
        self.assertGreater(res["counts"].get("E-source-changed", 0), 0)
        t = next(x for x in res["transfer"] if x["file"] == rel)
        self.assertFalse(t["full"])
        self.assertTrue(any("изменён" in w for w in t["why"]))
        self.assertNotEqual(E2E.gate(self.root, "map")[0], 0)
        self.assertNotEqual(E2E.gate(self.root, "check")[0], 0)
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_assign_start", self.root, {})
        self.assertIn("E-source-changed", cm.exception.codes)


class StaleUnitResults(Project):
    def test_plan_of_vanished_unit_is_not_read(self):
        _set_budget(self.root, 6000)
        K.through_split(self.root)
        _set_budget(self.root, 24000)
        T.call("graph_assign_start", self.root, {})
        units = _queue_items(self.root, "assign-section")
        K.run_queue(self.root, "assign-section", lambda u: {"assign": [], "unplaced": [{"blocks": units[u["item_id"]]["refs"]["blocks"]}]})
        plans = AS.load_plans(self.root)
        self.assertEqual({p["item_id"] for p in plans}, set(units))
        owner, _, unplaced = SP._assignment(self.root)
        self.assertEqual([b for b in owner if b in unplaced], [], "блок не лежит и в заметке, и в unplaced")

    def test_drafts_of_previous_survey_are_not_in_catalog(self):
        T.call("graph_survey", self.root, {})
        K.run_queue(self.root, "draft-file", K.draft_answer(self.root))
        T.call("graph_survey", self.root, {"scope": f"{K.SRC_REL}/task-service"})
        K.run_queue(self.root, "draft-file", K.draft_answer(self.root))
        cat = CT.build(self.root, T._need_cfg(self.root))
        files = {s["file"] for n in cat["nodes"] for s in n["sources"]}
        self.assertEqual(files, {f"{K.SRC_REL}/task-service/task-service.md"})


class EdgesReportNamesEveryEdge(Project):
    def test_every_live_edge_is_named(self):
        res = K.through_review(self.root)
        text = RV.review_path(self.root, T._need_cfg(self.root)).read_text(encoding="utf-8")
        lines, blocks = text.split("\n"), text.split("\n\n")
        unseen = []
        for x in E.load_all(self.root):
            if x["status"] == "rejected":
                continue
            a, b = F.link(x["from"]), F.link(x["to"])
            named = (RV.edge_id(x) in text or any(a in l and b in l for l in lines)
                     # раздел 1: операция — жирным заголовком, другие концы — строками её блока
                     or any((f"**{x['from']}**" in blk and b in blk) or (f"**{x['to']}**" in blk and a in blk)
                            for blk in blocks))
            if not named:
                unseen.append((x["from"], x["edge"], x["to"]))
        self.assertEqual(unseen, [], "«да» утверждает связи, которых нет в отчёте")
        self.assertGreater(res["other"], 0)
        self.assertIn("## 7. Прочие связи", text)


class SqlTables(unittest.TestCase):
    def test_not_tables(self):
        self.assertEqual(SQ.tables("SELECT count(*) INTO v_cnt FROM task WHERE status = 1"), [("task", "read")])
        self.assertEqual(SQ.tables("SELECT extract(epoch FROM t.created_at) FROM task t"), [("task", "read")])
        self.assertEqual(SQ.tables("SELECT * FROM task WHERE a IS NOT DISTINCT FROM b"), [("task", "read")])
        self.assertEqual(SQ.tables("SELECT trim(both from name), substring(code FROM 2) FROM operator"),
                         [("operator", "read")])

    def test_tables_still_found(self):
        self.assertEqual(SQ.tables("SELECT * FROM task WHERE id IN (SELECT task_id FROM package)"),
                         [("task", "read"), ("package", "read")])
        self.assertEqual(SQ.tables("INSERT INTO log (a) SELECT a FROM task"), [("log", "write"), ("task", "read")])
        self.assertEqual(SQ.tables("INSERT ALL INTO a (x) VALUES (1) INTO b (y) VALUES (2) SELECT 1 FROM dual"),
                         [("a", "write"), ("b", "write")])


class CommandsWithSpaces(unittest.TestCase):
    def test_project_path_is_one_argument(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "My Spec Repo"
            (root / ".spec-graph").mkdir(parents=True)
            (root / ".spec-graph" / "catalog.json").write_text("{}", encoding="utf-8")
            how = CS.state(root, "graph-catalog", root / ".spec-graph" / "catalog.json")["how"]
            for cmd in (how.split("как есть: ", 1)[1], ST.run_info(root)["commands"]["init"]):
                argv = shlex.split(cmd)
                self.assertEqual(argv[argv.index("--project") + 1], root.as_posix())


class NonUtf8Source(Project):
    def test_refusal_names_the_file(self):
        (self.root / K.SRC_REL / "ui" / "старый.md").write_bytes("# Экран\n\nТекст\n".encode("cp1251"))
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_survey", self.root, {})
        self.assertIn("E-source-encoding", cm.exception.codes)
        self.assertIn("старый.md", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
