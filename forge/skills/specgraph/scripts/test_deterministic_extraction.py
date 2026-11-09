#!/usr/bin/env python3
"""Детерминированное извлечение графа (после боевого /graph 2026-11-08: одна спека — 34 и 1 бизнес-операция,
606 вопросов `uses_data` к таблицам без узлов).

Корпус — раскладка боевой спеки в миниатюре: папки операций `[код] Имя`, реестр типов задач в
task-service, коды в нижнем регистре, SQL в шедулере, таблица разделом. Пинит:
  • узлы правил (auto) идут в каталог без модели: черновики, которые их НЕ называют, каталог не обедняют;
  • операция — по папке `[код] Имя` (README — её текст; без README — заглушка, текст даёт реестр);
    реестр — разделы с кодами папок у одного родителя; одиночный код в заголовке шедулера — не операция;
    код в строке «Тип задачи: kid_x» — в любом регистре; метки без кода (`[UI]`) операцией не становятся;
  • таблицы из SQL (без CTE) — узлы-заглушки `table:<имя>`, статус stub, не цель раскладки; таблица,
    описанная разделом, — обычный узел; `uses_data` к ним ставит механика, в why — чтение/запись;
  • экран в папке операции — `ui_of` к ней механикой (structure, не слово модели);
  • сдвиг каталога к прошлому утверждённому и коды из спеки без узла — в to_decide первыми;
  • дозапрос по перечню: крупный раздел без узла — E-draft-uncovered, not_nodes {section, reason} снимает;
  • кандидат без ключа, выброшенный моделью, ловится, даже если в черновике есть другие узлы без ключа;
  • отчёт связей: вопросы по целям с числом ссылок; graph_stubs_decide — заглушки и связи из вопросов.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import extract as X  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import review as RV  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import sqlref as SQ  # noqa: E402
from sgraph import tools as T  # noqa: E402

BIG = " ".join(["Длинное описание истории изменений спецификации без отдельного узла."] * 30)
FILES = {
    "rmoc-service/[kid_seizure] Изъятие документов/README.md":
        "# [kid_seizure] Изъятие документов\n\nБизнес-операция изъятия исполнительных документов по запросу.\n",
    "rmoc-service/[kid_seizure] Изъятие документов/UI.md":
        "# [kid_seizure] UI изъятия\n\nЭкран работы с задачей изъятия: список документов к изъятию.\n",
    "rmoc-service/[kid_search] Розыск/UI.md":
        "# [kid_search] UI розыска\n\nЭкран розыска документов по номеру.\n",
    "rmoc-service/[UI] Общие экраны/UI.md":
        "# [UI] UI общих настроек\n\nЭкран настроек.\n",
    "task-service/task-service.md": (
        "# task-service\n\n## 1. Общее описание\n\nСервис задач.\n\n## 2. Шедулеры\n\n"
        "### 2.1 Шедулер закрытия задач kid_seizure\n\nЗапускается ежедневно.\n\n```sql\n"
        "WITH old AS (SELECT id FROM task WHERE due_date < now())\n"
        "UPDATE task SET status = 'CLOSED' WHERE id IN (SELECT id FROM old);\n"
        "SELECT * FROM batch b JOIN task t ON t.batch_id = b.id;\n```\n\n"
        "## 3. Типы задач\n\n### 3.1 Изъятие документов (kid_seizure)\n\nИзъятие по запросу взыскателя.\n\n"
        "### 3.2 Розыск (kid_search)\n\nРозыск документа по номеру.\n\n"
        "### 3.3 Выдача нарочно\n\nТип задачи: kid_narochno\n\n"
        "## 4. Модель данных\n\n### 4.1 Таблица `batch`\n\nПакеты документов.\n\n"
        f"## 5. История изменений\n\n{BIG}\n"),
}
TS = "docs/spec/task-service/task-service.md"


def _project(d: str) -> Path:
    root = Path(d) / "repo"
    for rel, text in FILES.items():
        p = root / "docs" / "spec" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    T.call("graph_init", root, {"sources": ["docs/spec"]})
    return root


def _weak_model(root: Path, skip_uncovered: bool = True):
    """Черновик слабой модели: только кандидаты экстракторов, узлы правил не называет вовсе."""
    oracle = K.draft_oracle(root)

    def answer(unit):
        a = oracle(unit)
        auto = {c["name_hint"] for p in (root / X.CANDIDATES_REL).rglob("*.json")
                for c in json.loads(p.read_text(encoding="utf-8"))["candidates"] if c.get("auto")}
        a["nodes"] = [n for n in a["nodes"] if n["name"] not in auto]
        if skip_uncovered:
            a["not_nodes"] = [{"section": s, "reason": "история изменений"} for s in _units(root)[unit["item_id"]]["sections"]
                              if s.endswith(_section_id(root, "История изменений"))]
        return a
    return answer


def _units(root: Path):
    return {it["id"]: it["refs"] for it in json.loads((root / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))["items"]}


def _section_id(root: Path, title: str) -> str:
    from sgraph import blocks as B
    blocks = B.load(root, TS)["blocks"]
    s = next(s for s in X.sections(blocks) if s["title"] == title)
    return "#" + blocks[s["head"]]["id"].rsplit("#", 1)[1]


def _cat(root: Path):
    return {n["name"]: n for n in json.loads((root / CT.CATALOG_REL).read_text(encoding="utf-8"))["nodes"]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _project(self.tmp.name)
        T.call("graph_survey", self.root, {})

    def tearDown(self):
        self.tmp.cleanup()

    def drafts(self, **kw):
        K.run_queue(self.root, "draft-file", _weak_model(self.root, **kw))


class RuleNodes(Base):
    def test_catalog_without_model_naming_rule_nodes(self):
        self.drafts()
        res = T.call("graph_catalog", self.root, {})
        cat = _cat(self.root)
        ops = {n: c for n, c in cat.items() if c["kind"] == "operation"}
        self.assertEqual({n: c.get("key") for n, c in ops.items()},
                         {"Изъятие документов": "task:kid_seizure", "Розыск": "task:kid_search",
                          "Выдача нарочно": "task:kid_narochno"})
        seiz = ops["Изъятие документов"]
        self.assertEqual(sorted(s["file"] for s in seiz["sources"]),
                         ["docs/spec/rmoc-service/[kid_seizure] Изъятие документов/README.md", TS],
                         "папка (README) и реестр — один узел по ключу")
        self.assertNotIn("stub", ops["Розыск"], "у папки без README текст операции дал реестр")
        self.assertFalse(any("Шедулер" in s["file"] for s in seiz["sources"]))
        self.assertTrue(all(c["kind"] != "operation" for n, c in cat.items() if "общих" in n), "[UI] — не код")
        self.assertEqual(cat["task"]["key"], "table:task")
        self.assertTrue(cat["task"].get("stub") and cat["task"]["sources"] == [], "таблица из SQL — заглушка")
        self.assertNotIn("old", cat, "имя CTE — не таблица")
        self.assertNotIn("stub", cat["batch"], "таблица описана разделом — обычный узел")
        self.assertEqual(cat["batch"]["key"], "table:batch")
        self.assertIn("Шедулер закрытия задач kid_seizure", cat)
        self.assertEqual(res["to_decide"].get("missing_codes"), None)

    def test_stub_and_mechanics_after_split(self):
        self.drafts()
        K.approve_catalog(self.root)
        T.call("graph_assign_start", self.root, {})
        K.run_queue(self.root, "assign-section", K.assign_oracle(self.root))
        self.assertEqual(T.call("graph_split", self.root, {})["verify"]["errors"], 0)
        vault = self.root / "docs" / "graph"
        data, body = F.read(SP.read_note(vault / "data/table/task.md"))
        self.assertEqual((data["status"], data["key"]), ("stub", "table:task"))
        self.assertNotIn("graph:source", body)
        T.call("graph_edges_mechanical", self.root, {})
        props = {(p["from"], p["edge"], p["to"]): p for p in E.load_all(self.root)}
        sched = "Шедулер закрытия задач kid_seizure"
        self.assertEqual(props[(sched, "uses_data", "task")]["why"], "SQL: чтение и запись")
        self.assertTrue(props[(sched, "uses_data", "task")]["quote"].startswith("WITH old AS"))
        self.assertEqual(props[(sched, "uses_data", "batch")]["why"], "SQL: чтение")
        ui = props[("UI изъятия", "ui_of", "Изъятие документов")]
        self.assertEqual(ui["origin"], "rule:R-edge-folder")
        self.assertIn(("UI розыска", "ui_of", "Розыск"), props)


class CodeMismatch(unittest.TestCase):
    def test_same_name_other_code_hints_merge(self):
        with tempfile.TemporaryDirectory() as d:
            files = dict(FILES)
            ts = files.pop("task-service/task-service.md").replace("Розыск (kid_search)", "Розыск (kid_searche)")
            files["task-service/task-service.md"] = ts
            files["rmoc-service/[kid_searche] Розыск/README.md"] = "# [kid_searche] Розыск\n\nКод реестра другой.\n"
            root = Path(d) / "repo"
            for rel, text in files.items():
                p = root / "docs" / "spec" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            T.call("graph_init", root, {"sources": ["docs/spec"]})
            T.call("graph_survey", root, {})
            K.run_queue(root, "draft-file", _weak_model(root))
            res = T.call("graph_catalog", root, {})
            line = next(x for x in res["to_decide"]["collisions"] if x.startswith("«Розыск»"))
            self.assertIn("task:kid_search / task:kid_searche", line)
            self.assertIn("merge", line)


class Drift(Base):
    def test_drop_shows_drift_and_missing_code(self):
        self.drafts()
        K.approve_catalog(self.root)
        T.call("graph_catalog_decide", self.root, {"actions": [{"action": "drop", "node": "Выдача нарочно", "why": "тест"}]})
        res = T.call("graph_catalog", self.root, {})
        self.assertEqual(list(res["to_decide"])[:2], ["drift", "missing_codes"])
        self.assertIn("«Выдача нарочно»", res["to_decide"]["drift"][0])
        self.assertEqual(res["to_decide"]["missing_codes"], ["task:kid_narochno «Выдача нарочно»"])
        self.assertTrue(res["next"].startswith("первыми покажи to_decide.drift"))
        md = (self.root / "docs" / "graph" / "_graph" / "catalog.md").read_text(encoding="utf-8")
        self.assertIn("## Сравнение с прошлым каталогом", md)


class Gleaning(Base):
    def _unit(self):
        while True:
            unit = T.call("graph_next", self.root, {"queue": "draft-file"})
            if _units(self.root)[unit["item_id"]]["file"] == TS:
                return unit
            T.call(unit["tool"], self.root, dict(_weak_model(self.root)(unit), item_id=unit["item_id"]))

    def test_large_uncovered_section_named(self):
        unit = self._unit()
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_draft_put", self.root, dict(_weak_model(self.root, skip_uncovered=False)(unit), item_id=unit["item_id"]))
        self.assertIn("E-draft-uncovered", cm.exception.codes)
        self.assertIn("История изменений", str(cm.exception))
        T.call("graph_draft_put", self.root, dict(_weak_model(self.root)(unit), item_id=unit["item_id"]))

    def test_dropped_keyless_candidate_caught(self):
        unit = self._unit()
        a = _weak_model(self.root)(unit)
        self.assertTrue(any(not n.get("key") for n in a["nodes"]))
        a["nodes"] = [n for n in a["nodes"] if n["kind"] != "scheduler"]
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_draft_put", self.root, dict(a, item_id=unit["item_id"]))
        self.assertIn("E-draft-candidate", cm.exception.codes)


class Sql(unittest.TestCase):
    def test_tables_modes_and_cte(self):
        self.assertEqual(SQ.tables("WITH x AS (SELECT * FROM a) INSERT INTO b SELECT * FROM x, c"),
                         [("a", "read"), ("b", "write"), ("c", "read")])
        self.assertEqual(SQ.tables("DELETE FROM d WHERE id IN (SELECT id FROM generate_series(1, 3))"), [("d", "write")])
        self.assertEqual(SQ.tables("select 'from fake' as v from \"T\" -- from comment"), [("T", "read")])
        self.assertFalse(SQ.is_sql("update the docs later"))
        self.assertEqual([r["table"] for r in SQ.refs("Текст `SELECT id FROM inline_t WHERE x` и\n\n```\nUPDATE f SET a = 1\n```\n")],
                         ["f", "inline_t"])


class Stubs(unittest.TestCase):
    def test_frequent_target_becomes_stub_and_edges(self):
        with tempfile.TemporaryDirectory() as d:
            root = K.make_project(Path(d) / "repo")
            K.through_review(root)
            g = E.Graph(root, T._need_cfg(root))
            notes = [n["name"] for n in g.index if n["kind"] == "scheduler"][:2]
            qs = [{"note": n, "edge": "uses_data", "target": "doc_archive", "reason": "цель «doc_archive»: нет такого узла",
                   "quote": next(l.strip() for l in g.text(n).split("\n") if len(l.strip()) >= 20)} for n in notes]
            (root / ".spec-graph" / "questions.json").write_text(json.dumps(qs, ensure_ascii=False), encoding="utf-8")
            T.call("graph_edges_review", root, {})
            report = (root / "docs" / "graph" / "_graph" / "edges-review.md").read_text(encoding="utf-8")
            self.assertIn("«doc_archive» — uses_data, 2 заметок", report)
            with self.assertRaises(T.ToolError):
                T.call("graph_stubs_decide", root, {"targets": ["нет такой цели"], "why": "x"})
            res = T.call("graph_stubs_decide", root, {"targets": ["doc_archive"], "why": "пользователь: создать"})
            self.assertEqual((res["stubs"], res["linked"], res["questions_left"]), (["doc_archive"], 2, 0))
            data, _ = F.read(SP.read_note(root / "docs" / "graph" / "data/table/doc_archive.md"))
            self.assertEqual((data["status"], data["key"]), ("stub", "table:doc_archive"))
            self.assertEqual(RV.undecided_delta(root), [])
            self.assertEqual(sum(1 for p in E.load_all(root) if p["to"] == "doc_archive"), 2)


if __name__ == "__main__":
    unittest.main()
