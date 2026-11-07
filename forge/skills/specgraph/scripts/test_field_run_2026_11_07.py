#!/usr/bin/env python3
"""Регрессии боевого прогона /graph на GLM 5.3 Flash (2026-11-07, репо спеки pprb-kid-spec).

Пинит:
  • раскладка: жирный файл (сотни узлов каталога) собирает очередь при бюджете по умолчанию — шапка
    со списком узлов входит в бюджет; блок, который один не влезает, идёт неделимой единицей и сборку
    остальных не роняет;
  • разведка: часть оглавления с длинными кандидатами укладывается в бюджет по настоящему тексту;
  • очередь: отказ валидатора оставляет аренду — тот же субагент сдаёт исправленное тем же item_id;
  • короткие id разделов/блоков (`#12`) принимаются; HTML-якоря и «/» не попадают в имена кандидатов;
  • решения каталога: неоднозначное имя — отказ с id, ссылка по id работает, split последнего файла —
    отказ, split в существующий узел сливает, а не затирает, `type` вместо `action` назван прямо,
    revoke снимает запись, записи прежнего формата применяются как раньше;
  • согласие без повторного graph_catalog даёт снимок; кандидаты источников без gitignored-архивов.
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
from sgraph import assign as AS  # noqa: E402
from sgraph import blocks as B  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import config as C  # noqa: E402
from sgraph import extract as X  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import survey as SV  # noqa: E402
from sgraph import tools as T  # noqa: E402

REC = SCRIPTS.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"


def _project(d: str, files: dict) -> Path:
    root = Path(d) / "repo"
    for rel, text in files.items():
        p = root / "docs" / "spec" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    T.call("graph_init", root, {"sources": ["docs/spec"]})
    return root


def _draft(root: Path, item_id: str, rel: str, nodes: list) -> None:
    p = root / SV.DRAFTS_REL / f"{item_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"item_id": item_id, "file": f"docs/spec/{rel}", "nodes": nodes, "not_nodes": []},
                            ensure_ascii=False), encoding="utf-8")


def _record_consent(root: Path) -> None:
    key = T.call("graph_catalog", root, {})["key"]
    r = subprocess.run([sys.executable, "-X", "utf8", str(REC), "--project", str(root), "--key", key,
                        "--approved-by", "user", "--reason", "каталог", "--evidence", "Утвердить (Рекомендуется)",
                        "--bind-file", ".spec-graph/catalog.json"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)


class FatFileAssign(unittest.TestCase):
    """rmoc-спека: ≈250 узлов ссылаются на один файл, шапка входа ≈26 тыс. знаков — сборка assign-section
    падала при любом бюджете (24к, 30к, 50к), потому что из room вычитались константные 2000."""

    def test_queue_builds_at_default_budget_and_covers_every_block(self):
        name = "Процесс номер {} с длинным именем для раздутой шапки входа"
        text = "# Сервис big\n\n" + "".join(f"## {name.format(i)}\n\nТекст раздела {i} " + "слово " * 60 + "\n\n"
                                           for i in range(300))
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"big.md": text})
            blocks = B.scan_file(root, "docs/spec/big.md")["blocks"]
            heads = [b["id"] for b in blocks if b["type"] == "heading" and b["level"] == 2]
            _draft(root, "draft-big", "big.md", [{"kind": "process", "name": name.format(i), "sections": [h]}
                                                 for i, h in enumerate(heads)])
            _record_consent(root)
            res = T.call("graph_assign_start", root, {})
            self.assertNotIn("oversize", res)
            limit = Q.settings(C.load(root)[0])["budget_chars"] - AS.brief_chars()
            items = json.loads((root / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))["items"]
            seen = []
            for it in items:
                body = Q._input_path(root, it["id"]).read_text(encoding="utf-8")
                self.assertLessEqual(len(body), limit, it["id"])
                seen += it["refs"]["blocks"]
            self.assertEqual(seen, [b["id"] for b in blocks], "каждый блок ровно в одной единице, по порядку")

    def test_single_block_over_budget_is_indivisible_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"one.md": "# Один\n\n" + "длинный абзац " * 3000 + "\n", "two.md": "# Два\n\nкоротко\n"})
            _draft(root, "draft-one", "one.md", [{"kind": "process", "name": "Один", "sections": []}])
            _draft(root, "draft-two", "two.md", [{"kind": "process", "name": "Два", "sections": []}])
            _record_consent(root)
            res = T.call("graph_assign_start", root, {})
            self.assertIn("oversize", res)
            self.assertEqual(res["queue"]["total"], res["units"])
            self.assertGreaterEqual(res["units"], 2, "второй файл в очереди, несмотря на неделимый блок")


class SurveyFit(unittest.TestCase):
    def test_toc_with_many_long_candidates_fits_budget(self):
        """Две части разведки вышли на 523 знака сверх бюджета: нарезка шла по оценке длины строки."""
        heads = "".join(f"### POST /very/long/path/segment/number/{i}/with/extra/tail/for/a/long/key\n\nтекст\n\n"
                        for i in range(400))
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"svc/api.md": "# svc\n\n## API\n\n" + heads})
            T.call("graph_survey", root, {})
            limit = Q.settings(C.load(root)[0])["budget_chars"] - SV.brief_chars()
            items = json.loads((root / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))["items"]
            self.assertGreater(len(items), 1)
            cands = []
            for it in items:
                self.assertLessEqual(len(Q._input_path(root, it["id"]).read_text(encoding="utf-8")), limit, it["id"])
                cands += it["refs"]["candidates"]
            self.assertEqual(len(cands), len(set(cands)), "кандидат — ровно в одной части")
            self.assertEqual(len(cands), 401)

    def test_single_row_halves_by_candidates(self):
        row = [{"id": "f.md#2", "title": "API"}]
        cc = [{"name_hint": f"c{i}", "section": "f.md#2"} for i in range(10)]
        parts = SV._fit([(row, cc)], lambda ch, c: 100 * len(c), 300)
        self.assertEqual([len(c) for _, c, _ in parts], [2, 3, 2, 3])
        self.assertTrue(all(ch == row and not ind for ch, _, ind in parts))
        self.assertTrue(SV._fit([(row, cc[:1])], lambda ch, c: 1000, 300)[0][2], "неделимый остаток помечен")


class LeaseAndIds(unittest.TestCase):
    def test_rejection_keeps_lease_and_short_ids_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = K.make_project(Path(d) / "repo")
            T.call("graph_survey", root, {})
            unit = T.call("graph_next", root, {"queue": "draft-file"})
            good = K.draft_answer(root)(unit)
            with self.assertRaises(T.ToolError) as cm:
                T.call("graph_draft_put", root, dict(good, item_id=unit["item_id"], nodes=[{"kind": "нет-такого"}]))
            self.assertIn("аренда за тобой", str(cm.exception))
            short = [dict(n, sections=["#" + s.rsplit("#", 1)[1] for s in n.get("sections") or []]) for n in good["nodes"]]
            res = T.call("graph_draft_put", root, dict(good, item_id=unit["item_id"], nodes=short))
            self.assertEqual(res["queue_item"]["status"], "done")
            self.assertEqual(B.full_id("a.md", "#7"), "a.md#7")
            self.assertEqual(B.full_id("a.md", "a.md#7"), "a.md#7")


class Names(unittest.TestCase):
    def test_html_anchor_and_slash_do_not_reach_names(self):
        self.assertEqual(X.clean_title('<a name="21-регистрация-ид-коботом"></a>Регистрация ИД КОБОТом'),
                         "Регистрация ИД КОБОТом")
        ont = O.load()
        safe = X.safe_name("UI/UX Описание", ont.forbidden)
        self.assertEqual(safe, "UI-UX Описание")
        self.assertEqual(ont.check_name("screen", safe), [])


class CatalogDecisions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _project(self.tmp.name, {"a.md": "# A\n", "b.md": "# B\n"})
        _draft(self.root, "draft-a", "a.md", [
            {"kind": "screen", "name": "Розыск", "sections": []},
            {"kind": "operation", "name": "Розыск", "key": "task:kid_searche", "sections": []},
            {"kind": "screen", "name": "Общий экран", "sections": []}])
        _draft(self.root, "draft-b", "b.md", [
            {"kind": "screen", "name": "Экран Б", "sections": []},
            {"kind": "screen", "name": "Общий экран", "sections": []}])
        self.cat = T.call("graph_catalog", self.root, {})

    def tearDown(self):
        self.tmp.cleanup()

    def nodes(self):
        return json.loads((self.root / CT.CATALOG_REL).read_text(encoding="utf-8"))["nodes"]

    def decide(self, *actions):
        return T.call("graph_catalog_decide", self.root, {"actions": list(actions)})

    def test_ambiguous_name_refused_with_ids(self):
        with self.assertRaises(T.ToolError) as cm:
            self.decide({"action": "rename", "node": "Розыск", "name": "Розыск ИД", "why": "x"})
        ids = [n["id"] for n in self.nodes() if n["name"] == "Розыск"]
        for i in ids:
            self.assertIn(i, str(cm.exception))
        self.assertEqual(CT.load_decisions(self.root), [], "журнал не тронут")

    def test_id_reference_applies(self):
        op = next(n for n in self.nodes() if n["kind"] == "operation")
        self.decide({"action": "rename", "node": op["id"], "name": "Розыск (тип задачи)", "why": "x"})
        T.call("graph_catalog", self.root, {})
        self.assertIn("Розыск (тип задачи)", [n["name"] for n in self.nodes()])

    def test_split_of_last_file_refused(self):
        with self.assertRaises(T.ToolError) as cm:
            self.decide({"action": "split", "node": "Экран Б", "file": "docs/spec/b.md", "name": "Другой", "why": "x"})
        self.assertIn("rename", str(cm.exception))

    def test_split_into_existing_identity_merges_not_overwrites(self):
        res = self.decide({"action": "split", "node": "Общий экран", "file": "docs/spec/a.md", "name": "Экран Б",
                           "why": "x"})
        self.assertTrue(res.get("notes"))
        T.call("graph_catalog", self.root, {})
        b = next(n for n in self.nodes() if n["name"] == "Экран Б")
        self.assertEqual(len(b["sources"]), 2, "источник слит в существующий узел, а не затёр его")

    def test_type_instead_of_action_named(self):
        with self.assertRaises(T.ToolError) as cm:
            self.decide({"type": "drop", "node": "Экран Б", "why": "x"}, {"type": "drop", "node": "Общий экран", "why": "x"})
        self.assertIn("нет ключа action", str(cm.exception))
        self.assertEqual(str(cm.exception).count("нет ключа action"), 1, "одинаковые ошибки сведены")

    def test_revoke_removes_entry(self):
        self.decide({"action": "drop", "node": "Экран Б", "why": "x"})
        res = self.decide({"action": "revoke", "index": 1, "why": "передумал"})
        self.assertEqual((res["total"], res["revoked"]), (0, 1))

    def test_legacy_entry_keeps_last_wins_and_is_reported(self):
        (self.root / CT.DECISIONS_REL).write_text(json.dumps(
            [{"action": "rename", "node": "Розыск", "name": "Розыск ИД", "why": "x"}], ensure_ascii=False),
            encoding="utf-8")
        res = T.call("graph_catalog", self.root, {})
        self.assertTrue(res["decision_problems"])
        renamed = next(n for n in self.nodes() if n["name"] == "Розыск ИД")
        self.assertEqual(renamed["kind"], "operation", "как раньше: последний вставленный одноимённый")


class Consent(unittest.TestCase):
    def test_snapshot_made_without_second_catalog_call(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"a.md": "# A\n\nтекст\n"})
            _draft(root, "draft-a", "a.md", [{"kind": "process", "name": "A", "sections": []}])
            _record_consent(root)
            self.assertFalse((root / CT.APPROVED_REL).exists())
            self.assertEqual(CT.approved(root), (True, ""))


class Detect(unittest.TestCase):
    def test_gitignored_archives_are_not_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            if subprocess.run(["git", "init", "-q", str(root)], capture_output=True).returncode != 0:
                self.skipTest("git недоступен")
            (root / ".gitignore").write_text("docs/spec-graph-archive/\n", encoding="utf-8")
            for rel in ("docs/spec-graph-archive/v1/a.md", "docs/spec-graph-archive/v1/b.md", "docs/spec/x.md"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("# x\n", encoding="utf-8")
            self.assertEqual([c["root"] for c in C.detect(root)], ["docs/spec"])


if __name__ == "__main__":
    unittest.main()
