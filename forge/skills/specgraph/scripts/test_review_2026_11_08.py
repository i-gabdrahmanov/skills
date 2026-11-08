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
Второй заход того же дня:
  • брошенная единица раскладки — не тупик: её блоки в unplaced (гейт map проходит), graph_queue_retry по
    решению пользователя возвращает её в работу; отказ не по вине модели (согласие устарело) попытку не тратит;
  • один `METHOD /path` в двух сервисах — два эндпойнта, без E-key-dup и ошибки алиаса;
  • заметка, переименованная в Obsidian, — E-renamed; синк и нарезка отказывают, связи и заметка целы;
  • повторное «да» на изменённый каталог действует сразу, без лишнего graph_catalog;
  • решения каталога: лишнее поле и пустой why — отказ, retype меняет вид узла;
  • `.MD` — исходник, `.trash/` — нет; имена в NFC/NFD — один узел; имя узла — в пределах 200 байт;
  • карточка с содержимым снимает stub операции; graph_archive оставляет .obsidian/; код-блок не переписывается.
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
from sgraph import card as CD  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import consent as CS  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import extract as X  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import ontology as O  # noqa: E402
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



def _record_catalog_consent(root: Path) -> None:
    import subprocess
    res = T.call("graph_catalog", root, {})
    rec = K.SCRIPTS.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"
    r = subprocess.run([sys.executable, "-X", "utf8", str(rec), "--project", str(root), "--key", res["key"], "--approved-by",
                        "user", "--reason", "каталог", "--evidence", "да, утверждаю", "--bind-file", CT.CATALOG_REL],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr


class AbandonedUnit(Project):
    def _abandon_first(self):
        K.through_catalog(self.root)
        T.call("graph_assign_start", self.root, {})
        unit = T.call("graph_next", self.root, {"queue": "assign-section"})
        for _ in range(3):
            with self.assertRaises(T.ToolError):
                T.call("graph_assign_put", self.root, {"item_id": unit["item_id"], "assign": []})
        return unit["item_id"]

    def test_blocks_go_unplaced_and_retry_revives(self):
        item = self._abandon_first()
        K.run_queue(self.root, "assign-section", K.assign_answer(self.root))
        T.call("graph_split", self.root, {})
        code, out = E2E.gate(self.root, "map")
        self.assertEqual(code, 0, out)
        blocks = _queue_items(self.root, "assign-section")[item]["refs"]["blocks"]
        unplaced = (self.root / T._need_cfg(self.root)["vault"] / "_graph" / "unplaced.md").read_text(encoding="utf-8")
        self.assertIn(blocks[0], unplaced)
        with self.assertRaises(T.ToolError):
            T.call("graph_queue_retry", self.root, {"queue": "assign-section", "why": " "})
        res = T.call("graph_queue_retry", self.root, {"queue": "assign-section", "why": "пусть попробует ещё"})
        self.assertEqual(res["revived"], [item])
        self.assertEqual(T.call("graph_next", self.root, {"queue": "assign-section"})["item_id"], item)

    def test_stale_consent_does_not_spend_attempts(self):
        K.through_catalog(self.root)
        T.call("graph_assign_start", self.root, {})
        unit = T.call("graph_next", self.root, {"queue": "assign-section"})
        real = CT.approved
        CT.approved = lambda root: (False, "согласие graph-catalog: stale")
        try:
            for _ in range(4):
                with self.assertRaises(T.ToolError) as cm:
                    T.call("graph_assign_put", self.root, {"item_id": unit["item_id"], "assign": []})
                self.assertTrue(cm.exception.blameless)
        finally:
            CT.approved = real
        it = _queue_items(self.root, "assign-section")[unit["item_id"]]
        self.assertEqual((it["status"], it["attempts"]), ("leased", 0))


class SamePathInTwoServices(unittest.TestCase):
    def test_two_endpoints_no_errors(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            shutil.copytree(K.FIX / "sources", src)
            p = src / "rmoc-service" / "rmoc-service.md"
            p.write_text(p.read_text(encoding="utf-8").replace(
                "### 2.3 GET /rmoc/komplekt/items", "### 2.4 GET /api/v1/tasks — задачи РМОЦ\n\nСписок задач РМОЦ.\n\n"
                "### 2.3 GET /rmoc/komplekt/items"), encoding="utf-8")
            root = K.make_project(Path(d) / "repo", src)
            K.oracle_split(root)
            cat = CT.load_approved(root)
            got = sorted((n["service"], n["name"]) for n in cat["nodes"] if n.get("key") == "http:GET /api/v1/tasks")
            self.assertEqual([s for s, _ in got], ["rmoc-service", "task-service"])
            res = VA.validate(root, T._need_cfg(root), write=False)
            self.assertNotIn("E-key-dup", res["counts"])
            self.assertFalse([x for x in res["top"] if "алиас" in x])


class RenamedInObsidian(Project):
    def test_rename_is_named_and_nothing_is_lost(self):
        K.through_review(self.root)
        vault = self.root / T._need_cfg(self.root)["vault"]
        old, new = "Шедулер закрытия регулярных задач", "Закрытие регулярных задач"
        note = next(vault.rglob(f"{old}.md"))
        note.rename(note.with_name(f"{new}.md"))
        for q in vault.rglob("*.md"):
            text = q.read_text(encoding="utf-8")
            if f"[[{old}]]" in text:
                q.write_text(text.replace(f"[[{old}]]", f"[[{new}]]"), encoding="utf-8")
        for tool, code in (("graph_sync", "E-sync-renamed"), ("graph_split", "E-split-renamed")):
            with self.assertRaises(T.ToolError) as cm:
                T.call(tool, self.root, {})
            self.assertIn(code, cm.exception.codes)
        self.assertIn("E-renamed", VA.validate(self.root, T._need_cfg(self.root), write=False)["counts"])
        self.assertTrue(note.with_name(f"{new}.md").is_file(), "заметку не унесли в retired")
        self.assertEqual(sum(1 for x in E.load(self.root, old) if x["origin"] == "manual"), 0)


class CatalogDecisions(Project):
    def setUp(self):
        super().setUp()
        T.call("graph_survey", self.root, {})
        K.run_queue(self.root, "draft-file", K.draft_answer(self.root))

    def test_reapproval_takes_effect_without_graph_catalog(self):
        _record_catalog_consent(self.root)
        self.assertEqual(CT.approved(self.root), (True, ""))
        T.call("graph_catalog_decide", self.root, {"actions": [{"action": "drop", "node": "УПЗ", "why": "x"}]})
        _record_catalog_consent(self.root)
        self.assertEqual(CT.approved(self.root), (True, ""))

    def test_strict_fields_why_and_retype(self):
        node = "Алгоритм балансировки упаковок"
        for bad in ({"action": "rename", "node": node, "name": "Б", "kind": "scheduler", "why": "x"},
                    {"action": "rename", "node": node, "name": "Б"}):
            with self.assertRaises(T.ToolError):
                T.call("graph_catalog_decide", self.root, {"actions": [bad]})
        T.call("graph_catalog_decide", self.root, {"actions": [{"action": "retype", "node": node, "kind": "scheduler",
                                                                "why": "это шедулер"}]})
        cat = CT.build(self.root, T._need_cfg(self.root))
        self.assertEqual(next(n["kind"] for n in cat["nodes"] if n["name"] == node), "scheduler")


class SourcesAndNames(Project):
    def test_upper_md_in_hidden_dirs_out(self):
        (self.root / K.SRC_REL / "ui" / "Экран выдачи.MD").write_text("# Экран выдачи\n\nТекст.\n", encoding="utf-8")
        (self.root / K.SRC_REL / ".trash").mkdir()
        (self.root / K.SRC_REL / ".trash" / "удалённый.md").write_text("# Удалён\n", encoding="utf-8")
        files = B.source_files(self.root, T._need_cfg(self.root))
        self.assertIn(f"{K.SRC_REL}/ui/Экран выдачи.MD", files)
        self.assertFalse([f for f in files if ".trash" in f])

    def test_nfc_nfd_and_name_bytes(self):
        import unicodedata
        a = unicodedata.normalize("NFC", "Приём упаковок")
        b = unicodedata.normalize("NFD", a)
        self.assertEqual(CT.norm_name(a), CT.norm_name(b))
        self.assertEqual(O.fold(a), O.fold(b))
        self.assertEqual(X.safe_name(b, O.load().forbidden), a)
        long = " ".join(["Шедулер"] * 14)                # 111 знаков — в пределе знаков, но 209 байт
        self.assertTrue(O.load().check_name("scheduler", long, "svc"), "имя сверх 200 байт — ошибка")
        self.assertLessEqual(len(X.safe_name(long, O.load().forbidden).encode("utf-8")), O.NAME_MAX_BYTES)


class CardStubArchiveFence(Project):
    def test_card_with_content_clears_stub(self):
        K.through_review(self.root)
        cfg = T._need_cfg(self.root)
        items = _queue_items(self.root, "card")
        item = next(i for i, it in items.items() if it["refs"]["blocks"])
        note = items[item]["refs"]["note"]
        p = next((self.root / cfg["vault"]).rglob(f"{note}.md"))
        data, body = F.read(p.read_text(encoding="utf-8"))
        data["status"] = "stub"
        p.write_text(F.write(data, body, O.load().fm_keys("operation")), encoding="utf-8")
        blk = items[item]["refs"]["blocks"][0]
        CD.put(self.root, cfg, item, {k: [{"text": "есть описание", "blocks": [blk]}] for k, _ in CD.FIELDS})
        self.assertEqual(F.read(p.read_text(encoding="utf-8"))[0]["status"], "draft")

    def test_archive_keeps_obsidian_settings(self):
        K.through_split(self.root)
        vault = self.root / T._need_cfg(self.root)["vault"]
        (vault / ".obsidian").mkdir(exist_ok=True)
        (vault / ".obsidian" / "core-plugins.json").write_text('["bases"]', encoding="utf-8")
        res = T.call("graph_archive", self.root, {"reason": "строим новый"})
        self.assertTrue((vault / ".obsidian" / "core-plugins.json").is_file())
        self.assertTrue((self.root / res["archived"] / "vault" / "_graph" / "index.md").is_file())

    def test_link_inside_fence_is_not_rewritten(self):
        p = self.root / K.SRC_REL / "task-service" / "task-service.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("### 3.1", "```text\n[общее](#1-общее-описание)\n```\n\n### 3.1", 1), encoding="utf-8")
        T.call("graph_survey", self.root, {})       # исходник поменяли до разведки
        K.oracle_split(self.root)
        vault = self.root / T._need_cfg(self.root)["vault"]
        hits = [q for q in vault.rglob("*.md") if "[общее](#1-общее-описание)" in q.read_text(encoding="utf-8")]
        self.assertTrue(hits, "ссылка внутри фенса — как в исходнике")


if __name__ == "__main__":
    unittest.main()
