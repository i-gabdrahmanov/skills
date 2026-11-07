#!/usr/bin/env python3
"""test_yamlite.py — YAML-подмножество движка: разбор, эмиттер и их согласованность.

Главное свойство: loads(dumps(x)) == x для любых вложенных словарей/списков/скаляров, включая
«неудобные» строки (двоеточия, решётки, кавычки, слэши, переводы строк, похожие на числа/булевы/
даты, пустые). Фаззинг детерминирован (сиды). Отдельно — эскейпы двойных кавычек, на которых
раньше расходились разбор и запись.
"""
from __future__ import annotations

import random
import string
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import yamlite as Y  # noqa: E402

TRICKY = ["true", "False", "null", "~", "123", "1.5", "-3", "1e3", "2026-10-15", "2026-10-15T12:00:00Z",
          "- x", "#c", "a: b", " pad ", "", '"q"', "'s'", "{a: 1}", "[1]", "x #y", "ends:", "a\\nb",
          "C:\\Users\\x", 'say "hi"', "line1\nline2", "tab\there", "ключ: значение", "на\\", "!tag", "&anchor",
          "*alias", "|", ">", "%x", "@x", "`x`", "yes", "No", "off", "On"]


class Escapes(unittest.TestCase):
    def test_double_quoted_escapes_single_pass(self):
        self.assertEqual(Y.loads('a: "x\\\\ny"')["a"], "x\\ny", "экранированный слэш + n — не перевод строки")
        self.assertEqual(Y.loads('a: "x\\ny"')["a"], "x\ny")
        self.assertEqual(Y.loads('a: "q\\"r"')["a"], 'q"r')
        self.assertEqual(Y.loads('a: "\\u041f\\t!"')["a"], "П\t!")
        self.assertEqual(Y.loads('a: "C:\\Users"')["a"], "C:\\Users", "неизвестный эскейп остаётся как написан")

    def test_hash_inside_quotes_is_not_a_comment(self):
        self.assertEqual(Y.loads('a: "x # y"  # коммент')["a"], "x # y")
        self.assertEqual(Y.loads('a: "x \\" # y"  # коммент')["a"], 'x " # y')
        self.assertEqual(Y.loads("a: 'x # y' # коммент")["a"], "x # y")
        self.assertEqual(Y.loads("a: plain # коммент")["a"], "plain")

    def test_flow_string_ending_with_escaped_backslash(self):
        self.assertEqual(Y.loads('c: {k: "a\\\\", j: 1}')["c"], {"k": "a\\", "j": 1})
        self.assertEqual(Y.loads('c: ["x\\"y", "z"]')["c"], ['x"y', "z"])

    def test_quoted_scalar_in_list_is_not_a_key(self):
        self.assertEqual(Y.loads('- "a: b"\n- x'), ["a: b", "x"])
        self.assertEqual(Y.loads('- "k": v\n  j: 2'), [{"k": "v", "j": 2}])


class Emitter(unittest.TestCase):
    def test_shape_of_output(self):
        text = Y.dumps({"track": "STOR-100", "frames": [{"name": "main", "box": {"x": 1, "w": 2}, "tags": []},
                                                        {"name": "dlg"}], "empty": {}, "none": None, "ok": True})
        self.assertEqual(text, "track: STOR-100\nframes:\n  - name: main\n    box:\n      x: 1\n      w: 2\n"
                               "    tags: []\n  - name: dlg\nempty: {}\nnone: null\nok: true\n")

    def test_yaml11_boolean_words_as_keys_are_quoted(self):
        """y/n/yes/no/on/off как ключи: PyYAML (YAML 1.1) читает их как булевы — пишем в кавычках."""
        text = Y.dumps({"y": 1, "n": 2, "on": 3, "x": 4})
        self.assertEqual(text, '"y": 1\n"n": 2\n"on": 3\nx: 4\n')
        self.assertEqual(Y.loads(text), {"y": 1, "n": 2, "on": 3, "x": 4})

    def test_header_is_written_and_split_back(self):
        text = Y.dumps({"a": 1}, header=["# шапка", "# вторая строка", ""])
        head, rest = Y.split_header(text)
        self.assertEqual(head, ["# шапка", "# вторая строка", ""])
        self.assertEqual(Y.loads(rest), {"a": 1})
        self.assertEqual(Y.split_header("a: 1\n# не шапка\n"), ([], "a: 1\n# не шапка"))

    def test_plain_when_safe_quoted_when_not(self):
        self.assertEqual(Y.dump_scalar("react"), "react")
        self.assertEqual(Y.dump_scalar("https://x.y/a?b=1"), "https://x.y/a?b=1")
        for s in ("true", "123", "a: b", " x", "", "#x", "2026-01-02", "{x}", "line\nbreak"):
            self.assertTrue(Y.dump_scalar(s).startswith('"'), s)
        self.assertEqual(Y.dump_scalar(None), "null")
        self.assertEqual(Y.dump_scalar(0.5), "0.5")

    def test_every_tricky_string_roundtrips_as_value_key_and_list_item(self):
        for s in TRICKY:
            with self.subTest(s=s):
                self.assertEqual(Y.loads(Y.dumps({"k": s}))["k"], s)
                self.assertEqual(Y.loads(Y.dumps([s, s])), [s, s])
                self.assertEqual(Y.loads(Y.dumps({s or "empty": 1})), {s or "empty": 1})
                self.assertEqual(Y.loads(Y.dumps({"a": [{"x": s, "y": [s]}]})), {"a": [{"x": s, "y": [s]}]})

    def test_fuzz_roundtrip(self):
        alpha = string.ascii_letters + string.digits + " _-./:#\"'\\,[]{}!&*|>%@`~=+?()$\n\t" + "яЖ№"
        for seed in (11, 12, 13):
            rnd = random.Random(seed)

            def rs():
                return "".join(rnd.choice(alpha) for _ in range(rnd.randint(0, 12)))

            def rv(d=0):
                k = rnd.random()
                if d > 3 or k < 0.5:
                    return rnd.choice([None, True, False, rnd.randint(-5, 10 ** 6), round(rnd.random() * 100, 3),
                                       rs(), rs(), rs()] + TRICKY)
                if k < 0.75:
                    return {(rs() or "k") + str(i): rv(d + 1) for i in range(rnd.randint(0, 4))}
                return [rv(d + 1) for _ in range(rnd.randint(0, 4))]

            for i in range(1500):
                v = {"root": rv()}
                self.assertEqual(Y.loads(Y.dumps(v)), v, f"seed={seed} i={i}\n{Y.dumps(v)}")


class Parser(unittest.TestCase):
    def test_spec_examples_and_flow(self):
        doc = Y.loads("verify:\n  viewport: { width: 1280, height: 900 }  # c\nrules:\n  - match: [Label, LabelCell]\n"
                      "    outcome: component\n")
        self.assertEqual(doc["verify"]["viewport"], {"width": 1280, "height": 900})
        self.assertEqual(doc["rules"], [{"match": ["Label", "LabelCell"], "outcome": "component"}])

    def test_errors_carry_line_and_duplicates_refused(self):
        with self.assertRaises(Y.YamlError) as cm:
            Y.loads("a: 1\n  b: 2\n")
        self.assertIn("строка", str(cm.exception))
        with self.assertRaises(Y.YamlError):
            Y.loads("a: 1\na: 2\n")
        with self.assertRaises(Y.YamlError):
            Y.loads("a: {x: 1\n")


if __name__ == "__main__":
    unittest.main()
