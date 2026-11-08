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
  • согласие без повторного graph_catalog даёт снимок; кандидаты источников без gitignored-архивов;
  • повторный graph_split по заметке с source-2…source-N не оставляет непарных маркеров;
  • одноимённые узлы не проходят утверждение каталога (раньше — падение на graph_link_start);
  • rename в каталоге после раскладки: блоки идут за переименованием, заметка старого имени уходит из
    vault в .spec-graph/retired, связи строятся; неоднозначное имя в плане — отказ, а не тихая потеря;
  • ответ graph_next пачкой: бриф и инструмент — один раз, у единицы только item_id и input (413);
  • graph_status на шаге graph-map ведёт по очередям, а не всегда к разведке; непринятые решения
    каталога (decision_problems) — до согласия;
  • одноимённые узлы разных сервисов и (для экранов/алгоритмов/шедулеров/kafka-операций) разных папок не
    склеиваются, а дают коллизию с предложенными именами; общие виды (данные) склеиваются по имени; id
    решений прежнего формата резолвятся;
  • цикл очереди не попадает под отсечку хода рантайма: у graph_next каждый раз свой after=cursor;
  • graph_catalog отдаёт пункты решения (to_decide) — catalog.md целиком оркестратору не нужен;
  • graph_archive: граф заново — состояние движка и vault в архив, конфиг остаётся; живой прогон — отказ.
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
from sgraph import fm as F  # noqa: E402
from sgraph import queue as Q  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import status as ST  # noqa: E402
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
        self.assertTrue(res["next"].startswith("сначала decision_problems"), "к согласию — только разобрав их")
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


class Resplit(unittest.TestCase):
    """«маркер graph:source-3 не закрыт»: старые блоки source-N снимались по индексам первого разбора,
    после среза верхнего нижние указывали мимо — повторный graph_split падал на первой такой заметке."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ont = O.load(_project(self.tmp.name, {"a.md": "# A\n"}))
        self.node = {"kind": "process", "name": "X"}

    def tearDown(self):
        self.tmp.cleanup()

    def groups(self, n):
        return [(f"docs/spec/f{i}.md", [{"id": f"b{i}", "type": "para", "title": "", "text": f"текст {i}\n" * (i + 2)}],
                 f"текст {i}\n" * (i + 2)) for i in range(n)]

    def names(self, text):
        return [b["name"] for b in F.blocks(F.read(text)[1])]

    def test_same_sources_resplit_is_stable(self):
        first = SP._note_text(self.node, self.ont, None, self.groups(4), None)
        self.assertEqual(self.names(first), ["source", "source-2", "source-3", "source-4"])
        self.assertEqual(SP._note_text(self.node, self.ont, first, self.groups(4), None), first)

    def test_fewer_sources_drop_tail_blocks_and_keep_own_text(self):
        first = SP._note_text(self.node, self.ont, None, self.groups(4), None)
        fm, body = F.read(first)
        first = F.write(fm, body + "\nзаметка аналитика\n", list(fm))
        second = SP._note_text(self.node, self.ont, first, self.groups(2), None)
        self.assertEqual(self.names(second), ["source", "source-2"])
        self.assertNotIn("текст 3", second)
        self.assertIn("заметка аналитика", second)


class CollisionGate(unittest.TestCase):
    """Два узла «Обработка ошибок от сервиса УПЗ» (kafka-операция и шедулер) прошли согласие, раскладку
    и нарезку, а упали на graph_link_start: id единицы link-note — хеш имени."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _project(self.tmp.name, {"a.md": "# A\n\nтекст\n"})
        _draft(self.root, "draft-a", "a.md", [
            {"kind": "screen", "name": "Розыск", "sections": []},
            {"kind": "operation", "name": "Розыск", "key": "task:kid_search", "sections": []}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_consent_does_not_pass_collisions(self):
        res = T.call("graph_catalog", self.root, {})
        self.assertIn("одинаковые имена", res["next"])
        _record_consent(self.root)
        ok, why = CT.approved(self.root)
        self.assertFalse(ok)
        self.assertIn("«Розыск»", why)
        with self.assertRaises(T.ToolError):
            T.call("graph_assign_start", self.root, {})

    def test_rename_by_id_reopens_the_gate(self):
        T.call("graph_catalog", self.root, {})
        nodes = json.loads((self.root / CT.CATALOG_REL).read_text(encoding="utf-8"))["nodes"]
        op = next(n for n in nodes if n["kind"] == "operation")
        T.call("graph_catalog_decide", self.root,
               {"actions": [{"action": "rename", "node": op["id"], "name": "Розыск (тип задачи)", "why": "x"}]})
        _record_consent(self.root)
        T.call("graph_catalog", self.root, {})
        self.assertEqual(CT.approved(self.root), (True, ""))


class RenameAfterAssign(unittest.TestCase):
    """Ремонт коллизии в поле: rename по id после раскладки и нарезки, новое согласие, повторный split."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = K.make_project(Path(self.tmp.name) / "repo")
        K.through_split(self.root)
        self.vault = self.root / "docs" / "graph"
        owners = {a["node"] for p in AS.load_plans(self.root) for a in p["assign"]}
        cat = CT.load_approved(self.root)
        self.node = next(n for n in cat["nodes"] if n["name"] in owners and n["kind"] != "service")
        self.old = next(self.vault.rglob(f"{self.node['name']}.md"))

    def tearDown(self):
        self.tmp.cleanup()

    def rename(self, name):
        T.call("graph_catalog_decide", self.root,
               {"actions": [{"action": "rename", "node": self.node["id"], "name": name, "why": "x"}]})
        K.approve_catalog(self.root)

    def test_blocks_follow_rename_and_old_note_retires(self):
        body_before = F.read(self.old.read_text(encoding="utf-8"))[1]
        self.rename(self.node["name"] + " (переименован)")
        res = T.call("graph_split", self.root, {})
        self.assertEqual(res["verify"]["errors"], 0, res["verify"])
        self.assertFalse(self.old.exists())
        self.assertIn(self.old.relative_to(self.vault).as_posix(), res["retired"])
        self.assertTrue((self.root / SP.RETIRED_REL / self.old.relative_to(self.vault)).is_file())
        new = next(self.vault.rglob(self.node["name"] + " (переименован).md"))
        src = F.get_block(F.read(new.read_text(encoding="utf-8"))[1], "source")
        self.assertEqual(src["content"], F.get_block(body_before, "source")["content"], "текст не потерян")
        T.call("graph_link_start", self.root, {})

    def test_ambiguous_plan_name_refused(self):
        for p in sorted((self.root / AS.PLAN_REL).glob("*.json")):
            plan = json.loads(p.read_text(encoding="utf-8"))
            for a in plan["assign"]:
                if a["node"] == self.node["name"]:
                    a["node"] = "Узел, которого не было"
            p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_split", self.root, {})
        self.assertIn("Узел, которого не было", str(cm.exception))

    def test_duplicate_note_name_named_by_link_start(self):
        twin = self.vault / "_twin" / self.old.name
        twin.parent.mkdir()
        twin.write_bytes(self.old.read_bytes())
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_link_start", self.root, {})
        self.assertIn("две заметки", str(cm.exception))


class CompactBatch(unittest.TestCase):
    """24 ответа graph_next до первого 413 — 108 тыс. знаков в контексте оркестратора: бриф, инструмент,
    очередь, прогресс и фраза next повторялись в каждой из 10 единиц пачки."""

    def test_batch_of_ten_names_brief_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"a.md": "# A\n"})
            cfg = json.loads((root / ".spec-graph" / "config.json").read_text(encoding="utf-8"))
            cfg["queue"] = {"parallel": 10}
            (root / ".spec-graph" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            Q.build(root, "assign-section", [{"id": f"assign-{i:012x}", "brief": "assign-section.md",
                                              "tool": "graph_assign_put", "input": f"вход {i}\n"} for i in range(10)])
            res = T.call("graph_next", root, {"queue": "assign-section", "batch": True})
            self.assertEqual((res["brief"], res["tool"], len(res["units"])), ("assign-section.md", "graph_assign_put", 10))
            self.assertEqual(set(res["units"][0]), {"item_id", "input"})
            self.assertIn('"step_id": "graph-map"', res["prompt"])   # промпт субагента — один на пачку (2026-11-08)
            self.assertLess(len(json.dumps(res, ensure_ascii=False)) - len(res["prompt"]), 1800)


class MapNext(unittest.TestCase):
    def test_status_follows_map_progress(self):
        with tempfile.TemporaryDirectory() as d:
            root = K.make_project(Path(d) / "repo")
            self.assertIn("graph_survey", ST._map_next(root))
            K.through_catalog(root)
            self.assertIn("graph_assign_start", ST._map_next(root))
            T.call("graph_assign_start", root, {})
            self.assertIn("assign-section", ST._map_next(root))
            K.run_queue(root, "assign-section", K.assign_answer(root))
            self.assertIn("graph_split", ST._map_next(root))


class NoGlueByHeading(unittest.TestCase):
    """«Алгоритм обработки» склеил алгоритмы 4 операций rmoc-service, «UI-UX Описание» — 3 экрана,
    шедулеры cars-service и shpi-service стали одним узлом с сервисом первого."""

    FILES = {"rmoc/[kid_seizure] Изъятие/UI.md": "# A\n", "rmoc/[kid_seizure] Изъятие/README.md": "# A\n",
             "rmoc/[kid_search] Розыск/UI.md": "# B\n", "specs/cars.md": "# C\n", "specs/shpi.md": "# D\n"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _project(self.tmp.name, self.FILES)
        algo = {"kind": "algorithm", "name": "Алгоритм обработки", "service": "rmoc-service", "sections": []}
        screen = {"kind": "screen", "name": "UI-UX Описание", "sections": []}
        data = {"kind": "data", "subkind": "table", "name": "Таблица задач", "sections": []}
        sched = {"kind": "scheduler", "name": "Получение данных по заявке по Rest", "sections": []}
        reg = {"kind": "algorithm", "name": "Алгоритм регистрации", "service": "rmoc-service", "sections": []}
        _draft(self.root, "d1", "rmoc/[kid_seizure] Изъятие/UI.md", [algo, screen, data, reg])
        _draft(self.root, "d2", "rmoc/[kid_seizure] Изъятие/README.md", [screen])
        _draft(self.root, "d3", "rmoc/[kid_search] Розыск/UI.md", [algo, screen, data])
        _draft(self.root, "d4", "specs/cars.md", [dict(sched, service="cars-service")])
        _draft(self.root, "d5", "specs/shpi.md", [dict(sched, service="shpi-service")])
        self.res = T.call("graph_catalog", self.root, {})
        self.cat = json.loads((self.root / CT.CATALOG_REL).read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def named(self, name):
        return [n for n in self.cat["nodes"] if n["name"] == name]

    def test_owned_kinds_split_by_service(self):
        # одна папка specs/ — разводит именно сервис, а не правило папки
        sched = self.named("Получение данных по заявке по Rest")
        self.assertEqual(sorted(n["service"] for n in sched), ["cars-service", "shpi-service"])
        self.assertFalse([c for c in self.cat["conflicts"] if c["field"] == "service"])

    def test_folder_kinds_split_by_folder_and_merge_inside_it(self):
        algo, screen = self.named("Алгоритм обработки"), self.named("UI-UX Описание")
        self.assertEqual((len(algo), len(screen)), (2, 2))
        seizure = next(n for n in screen if "kid_seizure" in n["sources"][0]["file"])
        self.assertEqual(len(seizure["sources"]), 2, "UI.md и README.md одной операции — один экран")

    def test_shared_kinds_still_merge_by_name(self):
        self.assertEqual(len(self.named("Таблица задач")), 1)

    def test_collisions_suggest_distinct_names_and_block_consent(self):
        by = {c["name"]: c for c in self.cat["collisions"]}
        self.assertEqual(sorted(by), ["UI-UX Описание", "Алгоритм обработки", "Получение данных по заявке по Rest"])
        for c in by.values():
            self.assertEqual(len(set(c["suggest"])), len(c["suggest"]), c)
            self.assertEqual(len(c["ids"]), len(c["suggest"]))
        self.assertIn("[kid_search] Розыск", " ".join(by["Алгоритм обработки"]["suggest"]))
        self.assertIn("cars-service", " ".join(by["Получение данных по заявке по Rest"]["suggest"]))
        self.assertIn("одинаковые имена", self.res["next"])
        rows = self.res["to_decide"]["collisions"]
        self.assertEqual(len(rows), 3, "что решать — в ответе, без чтения catalog.md")
        self.assertTrue(all("`n-" in r for r in rows), rows)

    def test_rename_by_new_id_and_legacy_id(self):
        cars = next(n for n in self.named("Получение данных по заявке по Rest") if n["service"] == "cars-service")
        T.call("graph_catalog_decide", self.root,
               {"actions": [{"action": "rename", "node": cars["id"], "name": "Опрос ЦАРС", "why": "x"}]})
        legacy = CT.node_id(CT.legacy_identity({"kind": "algorithm", "name": "Алгоритм регистрации"}))
        self.assertNotIn(legacy, [n["id"] for n in self.cat["nodes"]], "id сменился — проверяем именно старый")
        T.call("graph_catalog_decide", self.root,
               {"actions": [{"action": "rename", "node": legacy, "name": "Задачи", "why": "x"}]})
        T.call("graph_catalog", self.root, {})
        names = {n["name"] for n in json.loads((self.root / CT.CATALOG_REL).read_text(encoding="utf-8"))["nodes"]}
        self.assertTrue({"Опрос ЦАРС", "Задачи"} <= names)

    def test_legacy_id_shared_by_now_split_nodes_is_refused(self):
        legacy = CT.node_id(CT.legacy_identity({"kind": "algorithm", "name": "Алгоритм обработки"}))
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_catalog_decide", self.root,
                   {"actions": [{"action": "rename", "node": legacy, "name": "X", "why": "x"}]})
        self.assertIn("прежнего формата", str(cm.exception))


def _turn_halts(calls) -> bool:
    """qwen-code 0.21.14 shouldHaltOnTurnToolCallCap: >100 вызовов в ходе и один ключ (имя+аргументы)
    повторён ≥ GLOBAL_DUPLICATE_THRESHOLD (6)."""
    seen: dict = {}
    for i, c in enumerate(calls, 1):
        k = json.dumps(c, ensure_ascii=False, sort_keys=True)
        seen[k] = seen.get(k, 0) + 1
        if i > 100 and max(seen.values()) >= 6:
            return True
    return False


class TurnCap(unittest.TestCase):
    """«Loop detection has been disabled for this session» трижды за прогон: цикл очереди — один ход,
    graph_next с одними и теми же аргументами на каждую пачку."""

    def run_queue_turn(self, with_cursor: bool):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"a.md": "# A\n"})
            cfg = json.loads((root / ".spec-graph" / "config.json").read_text(encoding="utf-8"))
            cfg["queue"] = {"parallel": 10}
            (root / ".spec-graph" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            Q.build(root, "assign-section", [{"id": f"assign-{i:012x}", "brief": "assign-section.md",
                                              "tool": "graph_assign_put", "input": f"вход {i}\n"} for i in range(140)])
            calls, after = [], None
            while True:
                args = {"queue": "assign-section", "batch": True}
                if with_cursor and after:
                    args["after"] = after
                calls.append(["graph_next", args])
                res = T.call("graph_next", root, args)
                if res["status"] == Q.EMPTY:
                    return calls
                after = res["cursor"]
                for u in res["units"]:
                    calls.append(["agent", u["item_id"]])
                    Q.settle(root, "assign-section", u["item_id"], True)

    def test_without_cursor_the_runtime_halts(self):
        self.assertTrue(_turn_halts(self.run_queue_turn(False)), "модель рантайма воспроизводит обрыв")

    def test_with_cursor_the_turn_survives(self):
        self.assertFalse(_turn_halts(self.run_queue_turn(True)))

    def test_cursor_repeats_when_nothing_was_settled(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"a.md": "# A\n"})
            Q.build(root, "assign-section", [{"id": "assign-000000000001", "brief": "assign-section.md",
                                              "tool": "graph_assign_put", "input": "x"}])
            a = T.call("graph_next", root, {"queue": "assign-section", "batch": True})
            b = T.call("graph_next", root, {"queue": "assign-section", "batch": True, "after": a["cursor"]})
            self.assertEqual(a["cursor"], b["cursor"], "не сдали — тот же cursor: остановка рантайма по делу")
            self.assertIn(a["cursor"], a["next"])


class ArchiveGraph(unittest.TestCase):
    """«Заархивируй граф, строим новый»: модель унесла vault руками, журнал решений (36 записей) и очереди
    остались и перешли в новый прогон непринятыми decision_problems."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _project(self.tmp.name, {"a.md": "# A\n"})
        _draft(self.root, "draft-a", "a.md", [{"kind": "screen", "name": "Экран", "sections": []}])
        T.call("graph_catalog", self.root, {})
        T.call("graph_catalog_decide", self.root,
               {"actions": [{"action": "rename", "node": "Экран", "name": "Экран А", "why": "x"}]})
        note = self.root / "docs" / "graph" / "ui" / "Экран А.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\nkind: screen\n---\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_moves_state_and_vault_keeps_config(self):
        res = T.call("graph_archive", self.root, {"reason": "заархивируй граф, строим новый"})
        arch = self.root / res["archived"]
        self.assertTrue((arch / "state" / "decisions.json").is_file())
        self.assertTrue((arch / "vault" / "ui" / "Экран А.md").is_file())
        self.assertFalse((self.root / CT.DECISIONS_REL).exists())
        self.assertFalse((self.root / "docs" / "graph").exists())
        self.assertTrue((self.root / ".spec-graph" / "config.json").is_file())
        self.assertTrue(T.call("graph_status", self.root, {})["initialized"])
        self.assertEqual(T.call("graph_queue_status", self.root, {"queue": "draft-file"})["total"], 0)

    def test_live_run_refused_with_abandon_command(self):
        run = self.root / "ground" / "statements" / "specgraph" / "GRAPH-1"
        run.mkdir(parents=True)
        (run / "manifest.json").write_text(json.dumps({"steps": [{"id": "graph-map", "status": "pending"},
                                                                 {"id": "graph-link", "status": "pending"},
                                                                 {"id": "graph-check", "status": "pending"}]}),
                                           encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            T.call("graph_archive", self.root, {"reason": "x"})
        self.assertIn("abandon GRAPH-1", str(cm.exception))
        self.assertTrue((self.root / CT.DECISIONS_REL).is_file(), "ничего не тронуто")


if __name__ == "__main__":
    unittest.main()
