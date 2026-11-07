#!/usr/bin/env python3
"""test_ontology.py — онтология графа как данные (задача 0.2).

Пинит:
  • базовая онтология самосогласована; самопроверка ловит дубль обратного ключа, ребро на
    несуществующий вид или роль, путь без {name}, inverse = прямое ребро, симметричное ребро с
    чужим inverse, обязательный ключ вне scalar_keys, расхождение owner и {service} в пути;
  • таблица примеров ТЗ (§1.3, §3, §4.1–4.3, §5, §7): каждая связь-пример — допустимое ребро, и
    набор допустимых рёбер для пары видов закреплён. Пара «шедулер → операция» допускает ровно два
    ребра (формирует / закрывает) — ТЗ §8.4 требует их различать, выбор делает смысл текста;
    у остальных пар — ровно одно;
  • запреты: обработчик не публикует, публикация не читает, `invokes` — только внутри сервиса;
  • локальное расширение — только добавления, переопределение базы — отказ;
  • в коде движка вне ontology.py нет литералов видов (виды знает только онтология).
"""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import ontology as O  # noqa: E402

BASE = json.loads(O.BASE.read_text(encoding="utf-8"))


def node(kind, role=None, service=None, subkind=None):
    n = {"kind": kind}
    if role:
        n["role"] = role
    if service:
        n["service"] = service
    if subkind:
        n["subkind"] = subkind
    return n


# (раздел ТЗ, от, к, ожидаемое ребро, полный набор допустимых рёбер для пары)
TZ_EXAMPLES = [
    ("§3/§4.1 шедулер формирует задачи операции", node("scheduler"), node("operation"), "creates_task",
     {"creates_task", "closes_task"}),
    ("§3/§4.2/§5 шедулер закрывает задачи операции", node("scheduler"), node("operation"), "closes_task",
     {"creates_task", "closes_task"}),
    ("§3/§4.3 UI операции", node("screen"), node("operation"), "ui_of", {"ui_of"}),
    ("§3/§4.3 UI использует эндпоинт", node("screen"), node("endpoint"), "calls", {"calls"}),
    ("§3 эндпоинт принадлежит сервису", node("endpoint"), node("service"), "belongs_to", {"belongs_to"}),
    ("§1.2 шедулер принадлежит сервису", node("scheduler"), node("service"), "belongs_to", {"belongs_to"}),
    ("§3 отправитель публикует в топик", node("kafka-op", "publish"), node("topic"), "publishes_to", {"publishes_to"}),
    ("§3 обработчик читает топик", node("kafka-op", "consume"), node("topic"), "consumes", {"consumes"}),
    ("§3/§4.1 шедулер и динамические атрибуты", node("scheduler"), node("data", subkind="attributes"), "uses_data",
     {"uses_data"}),
    ("§4.1 шедулер и операции УПЗ-адаптера", node("scheduler", service="task-service"),
     node("endpoint", service="upz-adapter"), "calls", {"calls"}),
    ("§1.3 эндпоинт вызывает эндпоинт", node("endpoint"), node("endpoint"), "calls", {"calls"}),
    ("§1.3 шедулер запускает алгоритм", node("scheduler", service="task-service"),
     node("algorithm", service="task-service"), "invokes", {"invokes"}),
    ("§7 операция связана с операцией", node("operation"), node("operation"), "related", {"related"}),
    ("§7 операция входит в процесс", node("operation"), node("process"), "part_of", {"part_of"}),
]


class Base(unittest.TestCase):
    def test_base_is_consistent(self):
        self.assertEqual(O.self_check(BASE), [])
        o = O.load()
        self.assertEqual(o.version, 1)
        self.assertIn("draft", o.statuses)

    def test_tz_examples(self):
        o = O.load()
        for title, src, dst, edge, allowed in TZ_EXAMPLES:
            with self.subTest(title):
                ok, why = o.edge_allowed(edge, src, dst)
                self.assertTrue(ok, why)
                self.assertEqual(set(o.allowed_edges(src, dst)), allowed)

    def test_forbidden_ends(self):
        o = O.load()
        for edge, src, dst in (
                ("publishes_to", node("kafka-op", "consume"), node("topic")),
                ("consumes", node("kafka-op", "publish"), node("topic")),
                ("ui_of", node("endpoint"), node("operation")),
                ("belongs_to", node("topic"), node("service")),
                ("calls", node("operation"), node("endpoint")),
                ("invokes", node("scheduler", service="a"), node("algorithm", service="b")),
                ("invokes", node("scheduler", service="a"), node("kafka-op", "consume", service="a")),
                ("nope", node("scheduler"), node("operation"))):
            with self.subTest(edge=edge, src=src, dst=dst):
                ok, why = o.edge_allowed(edge, src, dst)
                self.assertFalse(ok)
                self.assertTrue(why)

    def test_inverse_keys(self):
        o = O.load()
        self.assertEqual(o.inverse_of("closes_task"), "closed_by")
        self.assertEqual(o.edge_of_inverse("screens"), "ui_of")
        self.assertTrue(o.is_symmetric("related"))
        keys = o.edge_keys()
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(keys.count("related"), 1)

    def test_fm_keys_by_kind_and_role(self):
        o = O.load()
        op = o.fm_keys("operation")
        self.assertEqual(op[0], "kind")
        for k in ("created_by", "closed_by", "screens", "related", "part_of", "status", "source"):
            self.assertIn(k, op)
        self.assertNotIn("calls", op)
        consumer = o.fm_keys("kafka-op", "consume")
        self.assertIn("consumes", consumer)
        self.assertNotIn("publishes_to", consumer)
        self.assertIn("publishes_to", o.fm_keys("kafka-op", "publish"))
        self.assertEqual(o.fm_keys("endpoint")[:4], ["kind", "key", "method", "path"])
        with self.assertRaises(O.OntologyError):
            o.fm_keys("kafka-op", "nope")

    def test_paths(self):
        o = O.load()
        self.assertEqual(o.path_for("scheduler", "Шедулер закрытия регулярных задач", "task-service"),
                         "services/task-service/schedulers/Шедулер закрытия регулярных задач.md")
        self.assertEqual(o.path_for("service", "task-service"), "services/task-service/task-service.md")
        self.assertEqual(o.path_for("data", "Атрибуты укомплектования", subkind="attributes"),
                         "data/attributes/Атрибуты укомплектования.md")
        self.assertEqual(o.path_for("operation", "Укомплектование"), "business/operations/Укомплектование.md")
        with self.assertRaises(O.OntologyError):
            o.path_for("endpoint", "x GET a")
        with self.assertRaises(O.OntologyError):
            o.path_for("data", "x", subkind="blob")

    def test_names(self):
        o = O.load()
        self.assertEqual(o.check_name("endpoint", "task-service POST api-v1-tasks-{id}", "task-service"), [])
        self.assertTrue(o.check_name("endpoint", "POST /api/v1/tasks"))
        self.assertTrue(o.check_name("endpoint", "rmoc-service POST api", "task-service"))
        self.assertTrue(o.check_name("endpoint", "task-service FETCH api", "task-service"))
        self.assertEqual(o.check_name("topic", "pprbkid.document.taskservice.request.v1"), [])
        self.assertTrue(o.check_name("topic", "kafka:topic"))
        self.assertEqual(o.check_name("scheduler", "Шедулер закрытия регулярных задач"), [])
        for bad in ("", " Шедулер", "Шедулер  двойной", "Шедулер [x]", "a/b", "`x`", "x" * 151, "a\tb"):
            with self.subTest(bad=bad):
                self.assertTrue(o.check_name("scheduler", bad))


class SelfCheckCatches(unittest.TestCase):
    def broken(self, mutate):
        data = copy.deepcopy(BASE)
        mutate(data)
        return "\n".join(O.self_check(data))

    def test_cases(self):
        cases = {
            "уже занят": lambda d: d["edges"]["closes_task"].update(inverse="created_by"),
            "нет вида «ghost»": lambda d: d["edges"]["calls"]["to"].append("ghost"),
            "нет роли «listen»": lambda d: d["edges"]["consumes"].update({"from": ["kafka-op:listen"]}),
            "{name}": lambda d: d["kinds"]["screen"].update(path="ui/screen.md"),
            "совпадает с прямым": lambda d: d["edges"]["calls"].update(inverse="invokes"),
            "inverse совпадает с ним самим": lambda d: d["edges"]["related"].update(inverse="linked"),
            "scalar_keys": lambda d: d["kinds"]["scheduler"].update(required=["cron"]),
            "{service} в path": lambda d: d["kinds"]["algorithm"].update(path="algorithms/{name}.md"),
            "impact": lambda d: d["edges"]["calls"].update(impact="up"),
            "неизвестное поле": lambda d: d["kinds"]["topic"].update(colour="red"),
            "same_service": lambda d: d["edges"]["calls"].update(same_service=True),
        }
        for needle, mutate in cases.items():
            with self.subTest(needle):
                self.assertIn(needle, self.broken(mutate))
        with self.assertRaises(O.OntologyError):
            O.Ontology(dict(BASE, kinds={}))


class Local(unittest.TestCase):
    def test_additions_only(self):
        local = {"kinds": {"rabbit-op": {"title_ru": "RabbitMQ-операция", "path": "services/{service}/rabbit/{name}.md",
                                         "owner": "service", "name": "phrase"}},
                 "edges": {"rabbit_calls": {"title_ru": "шлёт в очередь", "from": ["rabbit-op"], "to": ["external"],
                                            "inverse": "rabbit_callers", "evidence": "required", "impact": "inverse"}}}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / O.LOCAL_REL
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(local, ensure_ascii=False), encoding="utf-8")
            o = O.load(Path(td))
            self.assertIn("rabbit-op", o.kinds)
            self.assertTrue(o.edge_allowed("rabbit_calls", node("rabbit-op"), node("external"))[0])
            self.assertNotIn("rabbit-op", O.load().kinds, "база не тронута")
            p.write_text(json.dumps({"kinds": {"scheduler": BASE["kinds"]["scheduler"]}}), encoding="utf-8")
            with self.assertRaises(O.OntologyError):
                O.load(Path(td))
            p.write_text(json.dumps({"statuses": ["x"]}), encoding="utf-8")
            with self.assertRaises(O.OntologyError):
                O.load(Path(td))


def branching_rx():
    """Запрещено ВЕТВЛЕНИЕ по виду (`kind == "scheduler"`, `"kind": "topic"`, `kind="data"`, `in ("endpoint",`);
    имя поля, совпавшее с видом (`c["service"]`), — не ветвление и не флагуется."""
    kinds = sorted(BASE["kinds"], key=len, reverse=True)
    lit = r"""["'](%s)["']""" % "|".join(re.escape(k) for k in kinds)
    return re.compile(r"(?:[!=]=\s*|\bin\s*[(\[]\s*|[\"']kind[\"']\s*:\s*|\bkind\s*=\s*)" + lit + "|" + lit + r"\s*[!=]=")


class NoKindLiteralsInEngine(unittest.TestCase):
    def test_guard_catches_branching(self):
        rx = branching_rx()
        for bad in ('if kind == "scheduler":', 'x = {"kind": "topic"}', 'f(kind="data")', 'if k in ("endpoint", "x")',
                    'if "screen" == k:'):
            self.assertTrue(rx.search(bad), bad)
        for ok in ('c["service"] = svc', 'out = {"service": svc}', 'args.get("service")'):
            self.assertFalse(rx.search(ok), ok)

    def test_only_the_ontology_knows_kinds(self):
        rx = branching_rx()
        offenders = []
        for py in sorted((SCRIPTS / "sgraph").rglob("*.py")):
            if py.name == "ontology.py":
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    offenders.append(f"{py.name}:{i}: {line.strip()}")
        self.assertEqual(offenders, [], "виды узлов — только из онтологии")


if __name__ == "__main__":
    unittest.main()
