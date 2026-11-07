#!/usr/bin/env python3
"""test_keys_resolver.py — ключи склейки и резолвер упоминаний (задача 2.1).

Пинит:
  • нормализация http-ключа (таблица прототипа): регистр метода, хост, query, хвостовой «/», `{id}` и
    `{taskId}` → `{}`, версия в пути сохраняется;
  • резолвер на vault фикстуры: METHOD /path, имя топика, код задачи, имя и алиас узла → узел;
    срезаемый gateway-префикс (`http.strip_prefixes`) — один раз на vault;
  • неоднозначность никогда не решается сама: один путь в двух сервисах без сервиса-контекста →
    ambiguous с кандидатами; с контекстом — свой сервис;
  • опечатка пути → ghost с подсказкой path_typo и кандидатом, но НЕ узел; неизвестное → ghost.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import config as C  # noqa: E402
from sgraph import keys as KY  # noqa: E402
from sgraph import resolve as RS  # noqa: E402
from sgraph import tools as T  # noqa: E402


class Keys(unittest.TestCase):
    def test_normalization_table(self):
        for raw, key in (("get /api/v1/orders/", "http:GET /api/v1/orders"),
                         ("POST https://gw.local/api/v1/tasks?x=1", "http:POST /api/v1/tasks"),
                         ("GET /api/v1/tasks/{id}", "http:GET /api/v1/tasks/{}"),
                         ("GET /api/v1/tasks/{taskId}/items", "http:GET /api/v1/tasks/{}/items"),
                         ("DELETE /v2/x", "http:DELETE /v2/x")):
            with self.subTest(raw=raw):
                m, p = raw.split(" ", 1)
                self.assertEqual(KY.http_key(m, p), key)
        self.assertEqual(KY.endpoint_name("svc", "get", "/api/v1/tasks/{id}/"), "svc GET api-v1-tasks-{id}")
        self.assertEqual(KY.find_http("вызвать `POST /api/v1/upz/tasks` УПЗ"), [("POST", "/api/v1/upz/tasks")])


class Resolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = K.make_project(Path(cls.tmp.name) / "repo")
        K.through_split(cls.root)
        cls.cfg = C.load(cls.root)[0]
        cls.idx = RS.vault_index(cls.root, cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def r(self, m, **kw):
        return RS.resolve(self.idx, m, kw.pop("cfg", self.cfg), **kw)

    def test_index(self):
        self.assertEqual(len(self.idx), 29)
        ep = next(n for n in self.idx if n["name"] == "upz-adapter POST api-v1-upz-tasks")
        self.assertEqual((ep["kind"], ep["service"], ep["key"]), ("endpoint", "upz-adapter", "http:POST /api/v1/upz/tasks"))

    def test_mentions(self):
        self.assertEqual(self.r("POST /api/v1/upz/tasks")["node"], "upz-adapter POST api-v1-upz-tasks")
        self.assertEqual(self.r("`POST /api/v1/tasks/{taskId}/complete`")["node"],
                         "task-service POST api-v1-tasks-{id}-complete")
        self.assertEqual(self.r("pprbkid.task.closed.v1")["node"], "pprbkid.task.closed.v1")
        self.assertEqual(self.r("kafka:pprbkid.task.closed.v1")["node"], "pprbkid.task.closed.v1")
        self.assertEqual(self.r("SC_ARCHIVE")["node"], "Передача упаковок в СЦ-архив")
        self.assertEqual(self.r("шедулер закрытия регулярных задач")["node"], "Шедулер закрытия регулярных задач")
        self.assertEqual(self.r("GET /rmoc/packages")["node"], "rmoc-service GET rmoc-packages", "алиас METHOD path")

    def test_gateway_prefix(self):
        self.assertEqual(self.r("GET /api/rmoc/packages")["status"], "ghost")
        cfg = dict(self.cfg, http={"strip_prefixes": ["/api"]})
        self.assertEqual(self.r("GET /api/rmoc/packages", cfg=cfg)["node"], "rmoc-service GET rmoc-packages")
        self.assertTrue(any(e.startswith("http") for e in C.validate(dict(self.cfg, http={"strip_prefixes": ["api"]}))))

    def test_ambiguity_is_never_resolved_silently(self):
        twin = dict(next(n for n in self.idx if n["name"] == "rmoc-service GET rmoc-packages"),
                    name="archive-service GET rmoc-packages", service="archive-service")
        idx = self.idx + [twin]
        res = RS.resolve(idx, "GET /rmoc/packages", self.cfg)
        self.assertEqual(res["status"], "ambiguous")
        self.assertEqual(res["candidates"], ["archive-service GET rmoc-packages", "rmoc-service GET rmoc-packages"])
        self.assertEqual(RS.resolve(idx, "GET /rmoc/packages", self.cfg, service="rmoc-service")["node"],
                         "rmoc-service GET rmoc-packages")

    def test_typo_and_ghost(self):
        res = self.r("GET /rmoc/pakages")
        self.assertEqual((res["status"], res.get("hint")), ("ghost", "path_typo"))
        self.assertEqual(res["candidates"], ["rmoc-service GET rmoc-packages"])
        self.assertEqual(self.r("GET /nothing/here")["status"], "ghost")
        self.assertEqual(self.r("Шедулер, которого нет")["status"], "ghost")

    def test_tool(self):
        res = T.call("graph_resolve", self.root, {"mentions": ["POST /api/v1/upz/tasks", "GET /x"]})
        self.assertEqual([x["status"] for x in res["results"]], ["node", "ghost"])


if __name__ == "__main__":
    unittest.main()
