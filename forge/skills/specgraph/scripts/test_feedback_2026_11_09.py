#!/usr/bin/env python3
"""Регрессии обратной связи аналитика по графу (2026-11-09): декомпозиция есть, связности нет.

Синтетика той же формы, что в отзыве (History Service, ЦАРС, File Adapter, UI комплектования), через
настоящий конвейер на «модели-оракуле» (кандидаты экстракторов как есть):
  • п.5 — обработчик Kafka с вложенными разделами алгоритмов не пропадает (групповое подавление — только по
    тому же виду), рёбра «запускает» ставит структура (R-edge-nested) и ссылка/имя (R-edge-mention), у
    обработчика — «Ход обработки» (топик → обработчик → шаги по порядку), у алгоритма — «Где выполняется»,
    в тексте родителя на месте вынесенного раздела — «↳ [[…]]»; алгоритм без запуска — вопрос аналитику;
  • п.3 — таблица: `|` алиаса ссылки в ячейке экранирован, таблица, прижатая к абзацу, отбита пустой
    строкой; дословность проверяется по месту вставки;
  • п.6 — ссылки: якорь с тире (слаг GitHub `--`), HTML-якорь в заголовке, ссылка на другой файл спеки — в
    рабочие `[[…]]`; ссылка без цели — в отчёте (dead_links, W-link-dead), а не молча;
  • п.4 — у сервиса «Обзор сервиса» под именем: назначение, входы с цепочками, алгоритмы, связанные сервисы;
  • п.2 — пустые свойства не пишутся, прежний `operations` снимается, `_graph/Свойства.md` объясняет каждое;
  • п.1, п.7 — имена: «… — экран» / «Экран …» → «UI …» (и склеиваются), шедулер без слова «шедулер» →
    «Шедулер <родительный>», общий заголовок Kafka-раздела → по топику, прежнее имя — в names_seen и
    решения по нему находят узел.
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

import _testkit as K  # noqa: E402
from sgraph import catalog as CT  # noqa: E402
from sgraph import edges as E  # noqa: E402
from sgraph import extract as X  # noqa: E402
from sgraph import fm as F  # noqa: E402
from sgraph import naming as NM  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import split as SP  # noqa: E402
from sgraph import survey as SV  # noqa: E402
from sgraph import tools as T  # noqa: E402

TOPIC = "pprbkid.case.changed.v1"
HANDLER = "Получение события — изменение дела"
DUP = "Алгоритм проверки дубликата"
WRITE = "Алгоритм записи истории"
ORPHAN = "Алгоритм архивации истории"
EP = "history-service GET history-{caseId}"
SCREEN = "UI истории дела"

HISTORY = f"""# history-service

## 1. Общее описание

history-service ведёт историю изменений архивных дел и отдаёт её экранам комплектования.

## 2. Kafka

### 2.1 Получение события — изменение дела

Сервис читает топик `{TOPIC}` и обрабатывает событие по шагам ниже.

1. Проверить, не дубликат ли событие, — [проверка дубликата](#dup).
2. Записать историю — [запись истории](#24-алгоритм-записи-истории).

#### <a name="dup"></a>Алгоритм проверки дубликата

Ищет событие с тем же `eventId` в таблице истории за последние сутки; дубликат пропускается.

#### 2.4 Алгоритм записи истории

Записывает событие в историю дела и отметку времени обработки.

## 3. Поля истории
Поля записи истории:
| поле | описание |
|---|---|
| eventId | ключ события, см. [проверку](#dup) |
| caseId | дело, см. [обработку](#21-получение-события--изменение-дела) |

## 4. REST API

### GET /history/{{caseId}} — история дела

Отдаёт историю дела. Подробнее об экране — [экран истории](../ui/экран-истории.md).

Неработающая ссылка: [нет такого](#нет-такого-раздела).

## 5. Алгоритм архивации истории

Переносит записи истории старше года в архивную таблицу раз в квартал.
"""

SCREEN_MD = """# Экран истории дела

Показывает историю дела: данные — `GET /history/{caseId}` (history-service).

Как событие попадает в историю — [обработка](../history-service/history-service.md#21-получение-события--изменение-дела),
сервис целиком — [history-service](../history-service/history-service.md).
"""


def _sources(d: Path) -> Path:
    src = d / "src"
    (src / "history-service").mkdir(parents=True)
    (src / "ui").mkdir()
    (src / "history-service" / "history-service.md").write_text(HISTORY, encoding="utf-8")
    (src / "ui" / "экран-истории.md").write_text(SCREEN_MD, encoding="utf-8")
    return src


class _Built(unittest.TestCase):
    """Корпус через graph-map (оракул) → механические рёбра → синк → Obsidian-слой → проверка."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.root = K.make_project(d / "repo", _sources(d))
        cls.split = K.oracle_split(cls.root)
        T.call("graph_edges_mechanical", cls.root, {})
        cls.sync = T.call("graph_sync", cls.root, {})
        T.call("graph_obsidian", cls.root, {})
        cls.check = T.call("graph_validate", cls.root, {})
        cls.vault = cls.root / "docs" / "graph"
        cls.index = {n["name"]: n for n in __import__("sgraph.resolve", fromlist=["x"]).vault_index(
            cls.root, json.loads((cls.root / ".spec-graph" / "config.json").read_text(encoding="utf-8")))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def note(self, name):
        text = SP.read_note(self.vault / self.index[name]["path"])
        return F.read(text)

    def codes(self):
        return self.check["counts"]


class HandlerAndAlgorithms(_Built):
    """п.5: History Service — «четыре алгоритма, непонятно, какое сообщение их запускает и в каком порядке»."""

    def test_handler_kept_with_nested_algorithms(self):
        self.assertIn(HANDLER, self.index, "обработчик с вложенными алгоритмами не подавлен как «групповой»")
        self.assertEqual(self.index[HANDLER]["role"], "consume")
        for a in (DUP, WRITE, ORPHAN):
            self.assertIn(a, self.index)

    def test_invokes_by_structure_and_mention(self):
        props = {(p["to"], p["origin"]) for p in E.load(self.root, HANDLER) if p["edge"] == "invokes"}
        self.assertIn((DUP, "rule:R-edge-nested"), props)
        self.assertIn((WRITE, "rule:R-edge-nested"), props)
        data, _ = self.note(HANDLER)
        self.assertEqual(sorted(data["invokes"]), sorted([F.link(DUP), F.link(WRITE)]))
        self.assertEqual(data["consumes"], [F.link(TOPIC)])

    def test_flow_of_handler_in_text_order(self):
        _, body = self.note(HANDLER)
        fl = F.get_block(body, "flow")["content"]
        self.assertIn(f"1. Сообщение из топика {F.link(TOPIC)}", fl)
        self.assertIn(f"2. **{HANDLER}** — эта заметка", fl)
        self.assertLess(fl.index(f"1. {F.link(DUP)}"), fl.index(f"2. {F.link(WRITE)}"), "шаги — по порядку в тексте")
        self.assertLess(body.index("graph:flow:start"), body.index("graph:edges:start"), "ход — над таблицей оснований")

    def test_algorithm_knows_where_it_runs(self):
        _, body = self.note(DUP)
        fl = F.get_block(body, "flow")["content"]
        self.assertIn(f"{F.link(HANDLER)} → **{DUP}** — шаг 1 из 2, дальше {F.link(WRITE)}", fl)
        self.assertIn(f"Сообщение из топика {F.link(TOPIC)}", fl)

    def test_carved_line_in_parent_and_not_evidence(self):
        _, body = self.note(HANDLER)
        self.assertRegex(body, r"↳ \[\[" + re.escape(DUP) + r"\]\], \[\[" + re.escape(WRITE) + r"\]\] <!-- graph:carved -->")
        text = E.source_text(self.root, json.loads((self.root / ".spec-graph" / "config.json").read_text(encoding="utf-8")),
                             self.index[HANDLER])
        self.assertNotIn("↳", text, "строка «↳» — навигация, не текст спеки")
        st = json.loads((self.root / SP.STRUCTURE_REL).read_text(encoding="utf-8"))
        self.assertEqual((st[DUP]["parent"], st[WRITE]["parent"]), (HANDLER, HANDLER))

    def test_top_level_carved_section_labelled(self):
        _, body = self.note("history-service")
        self.assertIn(f"↳ «{ORPHAN}» — {F.link(ORPHAN)} <!-- graph:carved -->", body,
                      "раздел верхнего уровня не читается как часть «4. REST API»")

    def test_untriggered_algorithm_is_a_question(self):
        self.assertEqual(self.codes().get("W-algo-no-trigger"), 1)
        q = (self.vault / "_graph" / "open-questions.md").read_text(encoding="utf-8")
        self.assertIn(f"Кто запускает алгоритм «{ORPHAN}»?", q)
        _, body = self.note(ORPHAN)
        self.assertIn("Кто запускает, в спеке не указано", F.get_block(body, "flow")["content"])

    def test_process_checks_quiet_on_whole_chain(self):
        """Цепочка топик → обработчик → два шага по тексту: проверкам процесса спросить нечего."""
        for code in ("W-service-no-entry", "W-step-unordered", "W-step-cycle", "W-algo-unreachable"):
            self.assertNotIn(code, self.codes(), code)


class StepOrder(unittest.TestCase):
    def test_steps_follow_text_not_names(self):
        from sgraph import overview as OV
        ont = O.load()
        idx = [{"name": n, "kind": k, "service": "s"} for n, k in
               (("H", "scheduler"), ("Алгоритм А", "algorithm"), ("Алгоритм Б", "algorithm"), ("Алгоритм В", "algorithm"))]
        v = OV.View(ont, idx, {"H": {"invokes": ["Алгоритм А", "Алгоритм Б", "Алгоритм В"]}},
                    {"H": "Сначала [[Алгоритм Б]], затем алгоритм в — то есть «Алгоритм В», и в конце [[Алгоритм А#x|а]]."})
        self.assertEqual(v.steps_of("H"), ["Алгоритм Б", "Алгоритм В", "Алгоритм А"])


class Tables(_Built):
    """п.3: таблицы ЦАРС и File Adapter в Obsidian «съезжали»."""

    def test_pipe_escaped_and_blank_line_before(self):
        _, body = self.note("history-service")
        self.assertIn(f"| eventId | ключ события, см. [[{DUP}#{DUP}\\|проверку]] |", body)
        self.assertIn("Поля записи истории:\n\n| поле | описание |", body)
        self.assertFalse({"W-table-pipe", "W-table-blank", "E-verbatim"} & set(self.codes()), self.codes())

    def test_verbatim_by_replay_catches_edit(self):
        p = self.vault / self.index["history-service"]["path"]
        orig = p.read_bytes()
        try:
            p.write_bytes(orig.replace("Поля записи истории:\n\n|".encode(), "Поля записи истории:\n|".encode()))
            errs = [c for c, _ in SP.verify(self.root, json.loads((self.root / ".spec-graph" / "config.json")
                                                                 .read_text(encoding="utf-8")))]
            self.assertIn("E-verbatim", errs, "убранная вставка — расхождение, а не «дословно»")
        finally:
            p.write_bytes(orig)

    def test_validator_sees_raw_breakage(self):
        from sgraph import validate as VA
        run = VA._Run(self.root, json.loads((self.root / ".spec-graph" / "config.json").read_text(encoding="utf-8")))
        nodes = {"x": {"path": "x.md", "body": "Текст\n| a | b |\n|---|---|\n| [[Y|y]] | 1 |\n"}}
        run.tables(nodes)
        self.assertEqual(sorted(i["code"] for i in run.issues), ["W-table-blank", "W-table-pipe"])


class Links(_Built):
    """п.6: «переход по ссылке оставляет на текущей странице»."""

    def test_dash_html_and_file_links_resolved(self):
        _, hb = self.note(HANDLER)
        self.assertIn(f"[[{DUP}#{DUP}|проверка дубликата]]", hb, "HTML-якорь <a name>")
        self.assertIn(f"[[{WRITE}#2.4 {WRITE}|запись истории]]", hb)
        _, sb = self.note(SCREEN)
        self.assertIn(f"[[{HANDLER}#2.1 {HANDLER}|обработка]]", sb, "якорь с тире, слаг GitHub `--`")
        self.assertIn("[[history-service|history-service]]", sb, "ссылка на файл спеки — на его заметку")
        _, eb = self.note(EP)
        self.assertIn(f"[[{SCREEN}|экран истории]]", eb)

    def test_heading_anchor_tag_stripped(self):
        _, body = self.note(DUP)
        self.assertIn(f"#### {DUP}\n", body)
        self.assertNotIn("<a name", body)

    def test_dead_link_reported(self):
        self.assertEqual(self.split["dead_links"]["count"], 1, self.split.get("dead_links"))
        self.assertIn("#нет-такого-раздела", self.split["dead_links"]["top"][0])
        self.assertEqual(self.codes().get("W-link-dead"), 1, self.codes())


class ServiceOverview(_Built):
    """п.4: главная страница сервиса — точка входа, а не общий список ссылок."""

    def test_overview_on_top(self):
        data, body = self.note("history-service")
        self.assertLess(body.index("graph:overview:start"), body.index("graph:source:start"))
        ov = F.get_block(body, "overview")["content"]
        self.assertIn("**Назначение.** history-service ведёт историю изменений архивных дел", ov)
        self.assertIn(f"- Kafka, чтение — 1:\n  - {F.link(TOPIC)} → {F.link(HANDLER)} — запускает {F.link(DUP)} → {F.link(WRITE)}", ov)
        self.assertIn(f"  - {F.link(EP)} · вызывают {F.link(SCREEN)}", ov)
        self.assertIn(f"- {F.link(ORPHAN)} — кто запускает, в спеке не указано", ov)
        self.assertIsNone(F.get_block(body, "toc"))


class Properties(_Built):
    """п.2: «назначение Properties непонятно; не добавлять метаданные ради метаданных»."""

    def test_no_empty_values_and_legend(self):
        ont = O.load(self.root)
        legend = (self.vault / "_graph" / "Свойства.md").read_text(encoding="utf-8")
        used = set()
        for n in self.index.values():
            data, _ = F.read(SP.read_note(self.vault / n["path"]))
            for k, v in data.items():
                self.assertNotIn(v, ([], None, ""), f"{n['name']}: пустое свойство {k}")
                used.add(k)
        for k in used:
            self.assertIn(f"| `{k}` |", legend, f"свойство {k} не объяснено")
            self.assertTrue(ont.key_ru(k), k)

    def test_legacy_operations_key_removed(self):
        p = self.vault / self.index["history-service"]["path"]
        orig = p.read_bytes()
        try:
            data, body = F.read(orig.decode("utf-8"))
            data["operations"] = [F.link(EP)]
            p.write_bytes(F.write(data, body).encode("utf-8"))
            T.call("graph_sync", self.root, {})
            data, _ = F.read(SP.read_note(p))
            self.assertNotIn("operations", data)
            self.assertIn(F.link(EP), data["components"])
        finally:
            p.write_bytes(orig)

    def test_repeat_is_idempotent(self):
        self.assertEqual(T.call("graph_split", self.root, {})["written"], 0)
        self.assertEqual(T.call("graph_sync", self.root, {})["written"], 0)


class Naming(unittest.TestCase):
    """п.1 и п.7: «Шаги обработки», «Получение данных по заявке REST», «UI» / «Экран» / «… — экран»."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "repo"
        files = {"cars/ui.md": "# Гибридная почта — экран\n\nтекст\n",
                 "cars/ui-2.md": "# UI Гибридная почта\n\nтекст\n",
                 "cars/cars.md": (f"# cars\n\n## Kafka\n\n### Шаги обработки\n\nЧитает `{TOPIC}`.\n\n"
                                  "## Шедулеры\n\n### Получение данных по заявке REST\n\nРаз в час.\n")}
        for rel, text in files.items():
            p = root / "docs" / "spec" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        T.call("graph_init", root, {"sources": ["docs/spec"]})
        self.root = root
        for rel in files:
            X.extract_file(root, f"docs/spec/{rel}", {})

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, item, rel, nodes):
        p = self.root / SV.DRAFTS_REL / f"{item}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"item_id": item, "file": f"docs/spec/{rel}", "nodes": nodes, "not_nodes": []},
                                ensure_ascii=False), encoding="utf-8")

    def sec(self, rel, title):
        from sgraph import blocks as B
        return next(b["id"] for b in B.scan_file(self.root, f"docs/spec/{rel}")["blocks"]
                    if b["type"] == "heading" and b["title"] == title)

    def test_catalog_names_one_format(self):
        self.draft("d1", "cars/ui.md", [{"kind": "screen", "name": "Гибридная почта — экран", "sections": []}])
        self.draft("d2", "cars/ui-2.md", [{"kind": "screen", "name": "UI Гибридная почта", "sections": []}])
        self.draft("d3", "cars/cars.md", [
            {"kind": "kafka-op", "role": "consume", "service": "cars", "name": "Шаги обработки",
             "sections": [self.sec("cars/cars.md", "Шаги обработки")]},
            {"kind": "scheduler", "service": "cars", "name": "Получение данных по заявке REST",
             "sections": [self.sec("cars/cars.md", "Получение данных по заявке REST")]}])
        T.call("graph_catalog", self.root, {})
        nodes = {n["name"]: n for n in json.loads((self.root / CT.CATALOG_REL).read_text(encoding="utf-8"))["nodes"]}
        self.assertIn("UI Гибридная почта", nodes)
        self.assertEqual(len(nodes["UI Гибридная почта"]["sources"]), 2, "«… — экран» и «UI …» одной папки — один экран")
        self.assertIn("Гибридная почта — экран", nodes["UI Гибридная почта"]["names_seen"])
        self.assertIn(f"Обработка сообщений {TOPIC}", nodes)
        self.assertIn("Шедулер получения данных по заявке REST", nodes)
        res = T.call("graph_catalog_decide", self.root, {"actions": [
            {"action": "rename", "node": "Получение данных по заявке REST", "name": "Шедулер опроса ЦАРС", "why": "x"}]})
        self.assertEqual(res["added"], 1, "решение по прежнему имени находит узел")
        T.call("graph_catalog", self.root, {})
        names = {n["name"] for n in json.loads((self.root / CT.CATALOG_REL).read_text(encoding="utf-8"))["nodes"]}
        self.assertIn("Шедулер опроса ЦАРС", names)

    def test_extractor_candidates_already_named(self):
        res = json.loads((self.root / X.CANDIDATES_REL / "docs/spec/cars/ui.md.json").read_text(encoding="utf-8"))
        self.assertEqual([c["name_hint"] for c in res["candidates"] if c["kind"] == "screen"], [])
        cars = json.loads((self.root / X.CANDIDATES_REL / "docs/spec/cars/cars.md.json").read_text(encoding="utf-8"))
        kafka = [c["name_hint"] for c in cars["candidates"] if c["kind"] == "kafka-op"]
        self.assertEqual(kafka, [f"Обработка сообщений {TOPIC}"], "общий заголовок Kafka-раздела — по топику")

    def test_rules(self):
        cfg = NM.settings(X.load_rules({}))
        cases = [("screen", None, "Экран комплектования архивных дел", {}, "UI комплектования архивных дел"),
                 ("screen", None, "UI-UX Описание", {"folder": "[kid_x] Изъятие", "file": "UI"}, "UI Изъятие"),
                 ("scheduler", None, "Регламентное задание закрытия задач", {}, "Регламентное задание закрытия задач"),
                 ("scheduler", None, "Ежедневная выгрузка", {}, "Шедулер — Ежедневная выгрузка"),
                 ("kafka-op", "consume", "Шаги обработки", {"parents": ["Kafka", "Приём заявок ЦАРС"]},
                  "Шаги обработки (Приём заявок ЦАРС)"),
                 ("algorithm", None, "Алгоритм балансировки упаковок", {}, "Алгоритм балансировки упаковок")]
        for kind, role, name, ctx, want in cases:
            self.assertEqual(NM.normalize(kind, role, name, ctx, cfg), want, name)

    def test_project_can_override(self):
        rules = X.load_rules({"extract": {"naming": {"kinds": {"screen": {"prefix": "Экран"}}}}})
        cfg = NM.settings(rules)
        self.assertEqual(NM.normalize("screen", None, "UI выдачи", {}, cfg), "Экран выдачи")
        self.assertTrue(cfg["kinds"]["screen"]["strip"], "kinds сливаются по виду: strip из умолчаний остался")


if __name__ == "__main__":
    unittest.main()
