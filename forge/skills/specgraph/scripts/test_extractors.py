#!/usr/bin/env python3
"""test_extractors.py — экстракторы кандидатов (задача 1.2).

Пинит:
  • на фикстуре tz-mini кандидаты ВСЕХ файлов (кроме черновика) = узлы эталона по (вид, имя), с ролью,
    подвидом, ключом и сервисом-владельцем; на каждом из 16 вариантов стресс-корпуса — так же (столбец
    `extract` матрицы variants.json: ok);
  • каждое правило: позитивный и негативный пример на синтетике; групповой заголовок с дочерними
    кандидатами — не кандидат; «См. также» не даёт кандидатов из упоминаний; публикация/обработка Kafka
    без топика в разделе — не Kafka-операция;
  • правила — данные: проектный `extract.markers_add` и переопределение правила по id работают, ошибка
    в правилах проекта — ошибка конфига; неизвестный тип матчера и вид — отказ;
  • доменных литералов в коде и правилах по умолчанию нет.
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

import gen_variants as GV  # noqa: E402
from sgraph import blocks as B  # noqa: E402
from sgraph import config as C  # noqa: E402
from sgraph import extract as X  # noqa: E402

FIX = SCRIPTS.parent / "fixtures" / "tz-mini"
G = json.loads((FIX / "expected" / "graph.json").read_text(encoding="utf-8"))
MATRIX = json.loads((FIX / "variants.json").read_text(encoding="utf-8"))
CFG = {"sources": [{"root": "s"}], "exclude": ["**/*-draft.md"]}


def run_corpus(src: Path, cfg=CFG):
    got = {}
    for f in sorted(src.rglob("*.md")):
        rel = "s/" + f.relative_to(src).as_posix()
        if rel.endswith("-draft.md"):
            continue
        bl, _ = B.parse(f.read_bytes().decode("utf-8"), rel)
        for c in X.extract_blocks(bl, rel, cfg)["candidates"]:
            got.setdefault((c["kind"], c["name_hint"]), c)
    return got


def one(text, rel="s/svc/part.md", cfg=CFG):
    bl, _ = B.parse(text, rel)
    return X.extract_blocks(bl, rel, cfg)["candidates"]


class Fixture(unittest.TestCase):
    def test_candidates_equal_expected_nodes(self):
        got = run_corpus(FIX / "sources")
        exp = {(n["kind"], n["name"]): n for n in G["nodes"]}
        self.assertEqual(set(got), set(exp))
        for k, n in exp.items():
            c = got[k]
            with self.subTest(node=k):
                for f in ("role", "subkind", "key", "service"):
                    if f in n:
                        self.assertEqual(c.get(f), n[f], f)
                self.assertTrue(c["blocks"])
                self.assertTrue(c["rule"].startswith("R-"))

    def test_every_variant(self):
        with tempfile.TemporaryDirectory() as td:
            for v, row in MATRIX["variants"].items():
                if row["extract"] != "ok":
                    continue
                with self.subTest(variant=v):
                    got = set(run_corpus(GV.materialize(Path(td), v)))
                    fn = GV.text_fn(v)
                    exp = {(n["kind"], X.norm_ws(fn(n["name"]))) for n in G["nodes"]}
                    self.assertEqual(got, exp)


class Rules(unittest.TestCase):
    def kinds(self, text, **kw):
        return sorted((c["kind"], c.get("role") or c.get("subkind") or "", c["name_hint"]) for c in one(text, **kw))

    def test_endpoint_forms(self):
        self.assertEqual(self.kinds("### 4.1 GET /api/v1/items/{id} — карточка\n\nтекст\n"),
                         [("endpoint", "", "svc GET api-v1-items-{id}")])
        self.assertEqual(self.kinds("### Карточка\n\nМетод: `POST /api/x`\n\nтекст\n"), [("endpoint", "", "svc POST api-x")])
        self.assertEqual(self.kinds("### Карточка\n\n```yaml\npaths:\n  /api/x:\n    get:\n      summary: x\n```\n"),
                         [("endpoint", "", "svc GET api-x")])
        self.assertEqual(self.kinds("### Карточка\n\nСервис вызывает `GET /api/x` и обрабатывает ответ.\n"), [],
                         "упоминание вызова в прозе — не эндпойнт сервиса")
        c = one("### GET /api/v1/items/ — список\n")[0]
        self.assertEqual(c["key"], "http:GET /api/v1/items")

    def test_scheduler_kafka_algorithm_attributes(self):
        self.assertEqual(self.kinds("### Планировщик очистки\n\nтекст\n"), [("scheduler", "", "Планировщик очистки")])
        self.assertEqual(self.kinds("### Обработка события\n\nЧитает топик `a.b.c.v1`.\n"),
                         [("kafka-op", "consume", "Обработка события"), ("topic", "", "a.b.c.v1")])
        self.assertEqual(self.kinds("### Обработка платежа\n\nПроверяет сумму.\n"), [],
                         "без топика «обработка» — не Kafka-операция")
        self.assertEqual(self.kinds("### Отправка уведомления\n\nПубликует в `n.o.p.v2`.\n"),
                         [("kafka-op", "publish", "Отправка уведомления"), ("topic", "", "n.o.p.v2")])
        self.assertEqual(self.kinds("## Расчёт лимита\n\nформула\n"), [("algorithm", "", "Расчёт лимита")])
        self.assertEqual(self.kinds("### Динамические атрибуты выдачи\n\n| a | b |\n|---|---|\n"),
                         [("data", "attributes", "Динамические атрибуты выдачи")])
        self.assertEqual(self.kinds("### Подготовка отчёта\n\nтекст\n"), [])

    def test_group_heading_is_not_a_candidate(self):
        text = "## 2. Шедулеры\n\nобщее\n\n### 2.1 Шедулер А\n\nа\n\n### 2.2 Шедулер Б\n\nб\n"
        self.assertEqual([c["name_hint"] for c in one(text)], ["Шедулер А", "Шедулер Б"])
        alone = "## 5. Алгоритм распределения\n\nшаги\n\n### 5.1 Ручная заявка\n\nподробно\n"
        self.assertEqual([c["name_hint"] for c in one(alone)], ["Алгоритм распределения"],
                         "подраздел без кандидата не делает родителя группой")

    def test_operations_screen_service_topic_external(self):
        self.assertEqual(self.kinds("## Выдача\n\nКод типа: `ISSUE`.\n", rel="s/biz/ops.md"),
                         [("operation", "", "Выдача")])
        self.assertEqual(one("## Выдача\n\nКод типа: `ISSUE`.\n", rel="s/biz/ops.md")[0]["key"], "task:ISSUE")
        table = "| код типа | тип задачи |\n|---|---|\n| ISSUE | Выдача |\n| `RETURN` | Возврат |\n"
        self.assertEqual(self.kinds(table, rel="s/x/y.md"), [("operation", "", "Возврат"), ("operation", "", "Выдача")])
        self.assertEqual(self.kinds("# Экран выдачи\n\nтекст\n", rel="s/ui/a.md"), [("screen", "", "UI выдачи")])
        self.assertEqual(self.kinds("# Отчёт\n\nтекст\n", rel="s/ui/a.md"), [])
        self.assertEqual(self.kinds("# billing\n\n## Общее описание\n\nтекст\n", rel="s/billing/billing.md"),
                         [("service", "", "billing")])
        self.assertEqual(self.kinds("# Заметки\n\nтекст\n", rel="s/billing/notes.md"), [])
        self.assertEqual(self.kinds("Шлёт запрос во внешнюю систему ЕРКЦ и ждёт ответа.\n"),
                         [("external", "", "ЕРКЦ")])

    def test_see_also_gives_nothing(self):
        text = "# Заметки\n\n## См. также\n\n- топик `a.b.c.v1` и внешняя система ЕРКЦ\n"
        self.assertEqual([c for c in one(text, rel="s/svc/notes.md")], [])

    def test_service_hint(self):
        cfg = dict(CFG, services={"s/legacy": "billing"})
        self.assertEqual(X.service_of("s/legacy/a/b.md", cfg), "billing")
        self.assertEqual(X.service_of("s/task-service/x.md", CFG), "task-service")
        self.assertEqual(X.service_of("s/flat.md", CFG), "flat")


class RulesAreData(unittest.TestCase):
    def test_markers_add_and_override(self):
        self.assertEqual([c["kind"] for c in one("### Регламентное задание закрытия\n\nзапускается ночью\n")],
                         ["scheduler"], "регламентное задание — шедулер (пробный цикл 4.4, вариант sched-reglament)")
        text = "### Ночная процедура закрытия\n\nзапускается ночью\n"
        self.assertEqual(one(text), [])
        learned = dict(CFG, extract={"markers_add": {"R-scheduler-heading": ["ночная процедура"]}})
        self.assertEqual([c["kind"] for c in one(text, cfg=learned)], ["scheduler"])
        off = dict(CFG, extract={"rules": [{"id": "R-topic-name", "kind": "topic", "match": "topic_regex",
                                            "confidence": "low"}], "kafka_topic_re": r"\bevt\.[a-z]+\b"})
        cs = one("Публикует в evt.created и a.b.c.v1\n", cfg=off)
        self.assertEqual([(c["name_hint"], c["confidence"]) for c in cs], [("evt.created", "low")])

    def test_bad_rules_are_config_errors(self):
        for bad in ({"markers_add": {"R-nope": ["x"]}}, {"rules": [{"id": "R-x", "match": "magic", "kind": "topic"}]},
                    {"rules": [{"id": "R-x", "match": "phrase", "kind": "ghost", "regex": "x"}]},
                    {"rules": [{"id": "R-x", "match": "phrase", "kind": "topic", "regex": "("}]}, []):
            with self.subTest(bad=bad):
                cfg = {"version": 1, "vault": "g", "sources": [{"root": "s"}], "extract": bad}
                self.assertTrue(any(e.startswith("extract") for e in C.validate(cfg)))
        ok = {"version": 1, "vault": "g", "sources": [{"root": "s"}],
              "extract": {"markers_add": {"R-scheduler-heading": ["задание"]}}}
        self.assertEqual(C.validate(ok), [])

    def test_no_domain_literals(self):
        rx = re.compile(r"(?i)\b(kid|rmoc|upz|pprb|kidtopic|укомплект)")
        files = [SCRIPTS / "sgraph" / "extract.py", SCRIPTS / "sgraph" / "keys.py",
                 SCRIPTS.parent / "references" / "extract-defaults.json"]
        for f in files:
            with self.subTest(file=f.name):
                self.assertEqual(rx.findall(f.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
