#!/usr/bin/env python3
"""test_guide.py — руководство аналитика не врёт о графе (задача X.3).

Пинит:
  • руководство едет в vault (`_graph/Руководство аналитика.md`), «Начало» на него ссылается;
  • каждое имя узла из прогулок (жирное «…» и сервисы) — узел графа фикстуры; каждый шаг прогулки «ключ →
    узел» — настоящий переход (graph_neighbors);
  • каждый ключ свойства из руководства — ключ онтологии; флаги «так задумано» — флаги правил полноты;
  • отчёты и разделы, которые руководство называет, существуют после /graph.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import _testkit as K  # noqa: E402
from sgraph import completeness as CM  # noqa: E402
from sgraph import obsidian as OB  # noqa: E402
from sgraph import ontology as O  # noqa: E402
from sgraph import tools as T  # noqa: E402

GUIDE = OB.GUIDE.read_text(encoding="utf-8")
TRANSFER = "Передача упаковок в СЦ-архив"
CLOSER = "Шедулер закрытия регулярных задач"
# шаги трёх прогулок руководства (раздел 3) — каждый обязан быть переходом графа
STEPS = [("Укомплектование", "closed_by", CLOSER), (CLOSER, "closes_task", "Регистрация"),
         (CLOSER, "closes_task", "Приём транспортных упаковок"), ("Регистрация", "screens", "UI регистрации"),
         ("UI регистрации", "calls", "task-service GET api-v1-tasks"),
         ("task-service GET api-v1-tasks", "belongs_to", "task-service"),
         (TRANSFER, "created_by", "Шедулер формирования задачи передачи упаковок в СЦ-архив"),
         (TRANSFER, "closed_by", "Шедулер закрытия задачи передачи упаковок в СЦ-архив"),
         ("Шедулер формирования задачи передачи упаковок в СЦ-архив", "invokes", "Алгоритм балансировки упаковок"),
         ("Шедулер закрытия задачи передачи упаковок в СЦ-архив", "calls", "rmoc-service GET rmoc-packages"),
         (TRANSFER, "related", "Укомплектование"),
         ("rmoc-service", "operations", "rmoc-service POST rmoc-packages-transfer"),
         ("rmoc-service POST rmoc-packages-transfer", "called_by", "UI передачи упаковок"),
         ("UI передачи упаковок", "ui_of", TRANSFER)]
_healthy = {}


def setUpModule():
    tmp = tempfile.mkdtemp()
    root = K.make_project(Path(tmp) / "repo")
    K.through_review(root)
    K.approve_edges(root)
    K.approve_accept(root)
    _healthy.update(tmp=tmp, root=root)


def tearDownModule():
    shutil.rmtree(_healthy["tmp"], ignore_errors=True)


class Guide(unittest.TestCase):
    def setUp(self):
        self.root = _healthy["root"]
        self.vault = self.root / "docs" / "graph"

    def test_in_vault_and_linked(self):
        self.assertEqual((self.vault / "_graph" / OB.GUIDE_NAME).read_text(encoding="utf-8"), GUIDE)
        self.assertIn("[[_graph/Руководство аналитика|Руководство аналитика]]",
                      (self.vault / "_graph" / OB.START_NAME).read_text(encoding="utf-8"))

    def test_walk_names_and_steps_are_real(self):
        walks = GUIDE[GUIDE.index("## 3. Три прогулки"):GUIDE.index("## 4.")]
        names = set(re.findall(r"\*\*«([^»]+)»\*\*", walks)) | set(re.findall(r"\*\*([a-z]+-[a-z-]+)\*\*", walks))
        self.assertGreaterEqual(len(names), 10, names)
        for n in sorted(names):
            with self.subTest(name=n):
                self.assertEqual(T.call("graph_find", self.root, {"q": n})["hits"][0]["name"], n)
        for a, key, b in STEPS:
            with self.subTest(step=f"{a} —{key}→ {b}"):
                self.assertIn(f"`{key}`", walks, "ключ шага назван в прогулке")
                res = T.call("graph_neighbors", self.root, {"name": a, "edges": [key], "limit": 50})
                self.assertIn(b, [s["to"] for s in res["steps"]])

    def test_keys_and_flags_are_real(self):
        ont = O.load()
        known = set(ont.edge_keys()) | set(ont.scalars)
        props = GUIDE[GUIDE.index("## 2."):GUIDE.index("Ниже — тело")]
        for k in re.findall(r"`([a-z_]+)`", props):
            self.assertIn(k, known, k)
        flags = set(re.findall(r"`([a-z_]+): true`", GUIDE))
        self.assertEqual(flags, {r["unless"] for r in CM.load()["rules"] if r.get("unless")})

    def test_named_reports_exist(self):
        for name in re.findall(r"\*\*`([a-z-]+\.md)`\*\*", GUIDE):
            with self.subTest(report=name):
                self.assertTrue((self.vault / "_graph" / name).is_file(), name)
        for sec in ("Дыры графа", "Перенос исходников"):
            self.assertIn(f"## {sec}", (self.vault / "_graph" / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
