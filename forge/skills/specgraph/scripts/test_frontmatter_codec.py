#!/usr/bin/env python3
"""test_frontmatter_codec.py — FM-кодек и маркеры тела без потерь (задача 0.3).

Пинит:
  • round-trip: `write(read(s)) == s` для канонического текста, `read(write(x)) == x` для моделей;
  • образцы «как пишет Properties Obsidian» (block-списки с отступом 2, `[]`, пустое значение, ссылки
    с `#заголовком` и `|алиасом`, кириллица в ключах и значениях, CRLF, BOM) читаются без потерь;
  • сломанные образцы из уроков прототипа — тройные скобки `[[[a]], [b]]`, некавычные `[[x]]` в
    скаляре, во flow- и в block-списке, дубль ключа, вложенный словарь, незакрытые кавычки и FM —
    ошибка с НОМЕРОМ СТРОКИ файла, а не тихое чтение;
  • чужие ключи человека сохраняются и идут после ключей онтологии;
  • маркеры: запись ставит пустые строки вокруг, проверка ругается на прижатые, непарные и
    вложенные; атрибуты — JSON, «-->» внутри значения маркер не ломает.
Данные — синтетические строки.
"""
from __future__ import annotations

import sys
import unittest
from collections import OrderedDict
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from sgraph import fm as F  # noqa: E402
from sgraph import ontology as O  # noqa: E402

CANON = """---
kind: scheduler
aliases: []
belongs_to:
  - "[[task-service]]"
closes_task:
  - "[[Укомплектование]]"
  - "[[Регистрация]]"
  - "[[Приём транспортных упаковок]]"
calls: []
status: draft
source:
  - "docs/system-requirements/task-service/task-service.md#4.2"
blocks: 12
---
# Шедулер закрытия регулярных задач

Текст.
"""

OBSIDIAN = """---
kind: operation
tags:
  - граф
  - "#важно"
created_by:
  - "[[Шедулер формирования задачи укомплектования]]"
screens:
  - "[[UI укомплектования#Список задач]]"
  - "[[UI укомплектования|экран]]"
related: []
Ответственный: Иванова
priority: 2
weight: 0.5
system: false
note:
status: draft
---
тело
"""


class RoundTrip(unittest.TestCase):
    def test_canonical_text_is_stable(self):
        data, body = F.read(CANON)
        self.assertEqual(F.write(data, body), CANON)
        self.assertEqual(data["closes_task"][2], "[[Приём транспортных упаковок]]")
        self.assertEqual(data["blocks"], 12)

    def test_models_survive(self):
        model = OrderedDict([("kind", "endpoint"), ("key", "http:POST /api/v1/tasks"), ("method", "POST"),
                             ("path", "/api/v1/tasks/{id}"), ("aliases", ["POST /api/v1/tasks/{id}"]),
                             ("calls", ["[[upz-adapter POST api-v1-tasks]]"]), ("empty", []), ("n", None),
                             ("flag", True), ("count", 3), ("ratio", 0.93), ("quoted", "true"), ("num_str", "12"),
                             ("colon", "a: b"), ("hash", "x #y"), ("space", " lead"), ("dash", "- item"),
                             ("cyr", "Шедулер: закрытие"), ("q", 'кавычка "внутри"'), ("bs", "C:\\path")])
        data, body = F.read(F.write(model, "тело\n"))
        self.assertEqual(dict(data), dict(model))
        self.assertEqual(body, "тело\n")

    def test_obsidian_style_reads_without_loss(self):
        data, body = F.read(OBSIDIAN)
        self.assertEqual(data["tags"], ["граф", "#важно"])
        self.assertEqual(data["screens"][1], "[[UI укомплектования|экран]]")
        self.assertEqual(data["Ответственный"], "Иванова")
        self.assertEqual((data["priority"], data["weight"], data["system"], data["note"]), (2, 0.5, False, None))
        self.assertEqual(F.write(data, body), OBSIDIAN, "стиль Obsidian = стиль записи кодека")
        self.assertEqual([F.link_target(v) for v in data["screens"]], ["UI укомплектования", "UI укомплектования"])
        self.assertIsNone(F.link_target("UI укомплектования"))

    def test_flow_lists_crlf_bom(self):
        text = '\ufeff---\r\nkind: topic\r\npublishers: ["[[a]]", \'[[b]]\', "[[c, d]]"]\r\nconsumers: []\r\n---\r\nbody'
        data, body = F.read(text)
        self.assertEqual(data["publishers"], ["[[a]]", "[[b]]", "[[c, d]]"])
        self.assertEqual(data["consumers"], [])
        self.assertEqual(body, "body")

    def test_no_frontmatter(self):
        self.assertEqual(F.read("# просто текст\n"), (OrderedDict(), "# просто текст\n"))


class Broken(unittest.TestCase):
    CASES = {
        "тройные скобки": ("---\nkind: flow\nsteps: [[[a]], [b]]\n---\n", 3, "без кавычек"),
        "ссылка скаляром без кавычек": ("---\nkind: x\nbelongs_to: [[task-service]]\n---\n", 3, "без кавычек"),
        "ссылка во flow без кавычек": ('---\nkind: x\ncalls: ["[[a]]", [[b]]]\n---\n', 3, "без кавычек"),
        "ссылка в block без кавычек": ("---\nkind: x\ncalls:\n  - \"[[a]]\"\n  - [[b]]\n---\n", 5, "без кавычек"),
        "дубль ключа": ("---\nkind: x\ncalls: []\nstatus: draft\ncalls: []\n---\n", 5, "повторяется"),
        "вложенный словарь": ("---\nkind: x\nmeta:\n  owner: team\n---\n", 4, "вложен"),
        "незакрытая кавычка": ('---\nkind: "x\n---\n', 2, "кавычк"),
        "двоеточие без кавычек": ("---\nkind: a: b\n---\n", 2, "двоеточие"),
        "якорь": ("---\nkind: &a x\n---\n", 2, "неподдерживаемая"),
        "незакрытый FM": ("---\nkind: x\n", 1, "не закрыт"),
        "элемент без ключа": ("---\n- a\n---\n", 2, "без ключа"),
        "пустой элемент": ('---\ncalls: ["[[a]]", , "[[b]]"]\n---\n', 2, "пустой элемент"),
    }

    def test_each_case_names_the_line(self):
        for title, (text, line, needle) in self.CASES.items():
            with self.subTest(title):
                with self.assertRaises(F.FMError) as cm:
                    F.read(text)
                self.assertEqual(cm.exception.line, line, str(cm.exception))
                self.assertIn(needle, str(cm.exception))

    def test_writer_refuses_nested(self):
        with self.assertRaises(TypeError):
            F.dump({"meta": {"a": 1}})
        with self.assertRaises(TypeError):
            F.dump({"calls": [["[[a]]"]]})


class KeyOrder(unittest.TestCase):
    def test_ontology_keys_first_then_human_keys(self):
        o = O.load()
        order = o.fm_keys("scheduler")
        data = OrderedDict([("Заметка", "моя"), ("status", "draft"), ("kind", "scheduler"),
                            ("closes_task", ["[[Укомплектование]]"]), ("tags", ["x"])])
        text = F.dump(data, order)
        keys = [l.split(":")[0] for l in text.splitlines()[1:-1] if not l.startswith(" ")]
        self.assertEqual(keys, ["kind", "closes_task", "status", "Заметка", "tags"])


class Markers(unittest.TestCase):
    def test_set_block_adds_air_and_replaces_in_place(self):
        body = "# Узел\n\nпроза человека\n"
        b1 = F.set_block(body, "source", "| a | b |\n|---|---|\n| 1 | 2 |", {"file": "docs/a b.md", "blocks": "3-5"})
        self.assertEqual(F.marker_errors(b1), [])
        self.assertIn("проза человека", b1)
        blk = F.get_block(b1, "source")
        self.assertEqual(blk["attrs"], {"blocks": "3-5", "file": "docs/a b.md"})
        self.assertEqual(blk["content"], "| a | b |\n|---|---|\n| 1 | 2 |")
        b2 = F.set_block(b1 + "\nхвост\n", "source", "новое")
        self.assertEqual(F.get_block(b2, "source")["content"], "новое")
        self.assertIn("хвост", b2)
        self.assertIn("проза человека", b2)
        self.assertEqual(F.set_block(b2, "source", "новое"), b2, "повтор — без изменений")
        b3 = F.set_block(b2, "edges", "e")
        self.assertEqual([b["name"] for b in F.blocks(b3)], ["source", "edges"])
        self.assertEqual(F.marker_errors(b3), [])

    def test_attr_with_arrow_does_not_break_marker(self):
        b = F.set_block("", "source", "x", {"file": "a-->b.md"})
        self.assertEqual(F.get_block(b, "source")["attrs"]["file"], "a-->b.md")

    def test_marker_errors(self):
        tight = "<!-- graph:source:start -->\n| a |\n<!-- graph:source:end -->\n"
        self.assertTrue(any("пустая строка" in e for e in F.marker_errors(tight)))
        self.assertTrue(F.marker_errors("\n<!-- graph:source:start -->\n\nx\n"))
        self.assertTrue(F.marker_errors("\n<!-- graph:source:end -->\n"))
        nested = "\n<!-- graph:a:start -->\n\n<!-- graph:b:start -->\n\n<!-- graph:b:end -->\n\n<!-- graph:a:end -->\n"
        self.assertTrue(F.marker_errors(nested))


if __name__ == "__main__":
    unittest.main()
