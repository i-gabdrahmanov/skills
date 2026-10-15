#!/usr/bin/env python3
"""test_canonical.py — каноническая модель (спека §6; INV-B1, B4, B5, B6, B10, B13; QG U5).

Пинит: строгая валидация с путями и сбором ВСЕХ нарушений; нормализация чисел (половина вверх,
альфа до 3 знаков, 5.0 == 5); детерминированные байты; subtree_hash (12 hex, не зависит от порядка
ключей, меняется при правке поддерева и не меняется от правки соседа); weight как минифицированные
байты и массовый weights() с точным совпадением; навигация; collect_texts — текст не теряется на
любой глубине, occluded исключён, content заменяет textProps, дедупликация «ребёнок = проп»;
запись/чтение атомарны и байт-в-байт повторяемы.
"""
from __future__ import annotations

import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402


def sample() -> dict:
    return {"id": "1", "kind": "frame", "name": "Screen", "size": {"w": 600, "h": 245},
            "layout": {"direction": "column", "gap": 16, "padding": {"top": 20, "right": 20, "bottom": 20, "left": 20},
                       "align": {"main": "start", "cross": "stretch"}, "stated": True},
            "style": {"background": "rgba(255, 255, 255, 1)", "radius": [12, 12, 12, 12],
                      "shadow": "0px 12px 48px rgba(11, 11, 30, 0.16)"},
            "children": [
                {"id": "2", "kind": "text", "name": "Title", "text": "Заголовок",
                 "font": {"family": "Fixture Sans", "size": 18, "weight": 600, "lineHeight": 24, "letterSpacing": -0.2},
                 "color": "rgba(31, 20, 51, 1)"},
                {"id": "3", "kind": "instance", "name": "Button",
                 "component": {"figmaName": "Button", "variants": {"Type": "Primary"}, "origin": "library",
                               "childrenRole": "content", "sized": "master",
                               "content": [{"id": "3;label", "name": "Label", "text": "Подтвердить",
                                            "overridden": True, "color": "rgba(255, 255, 255, 1)"}]},
                 "children": [{"id": "3a", "kind": "text", "text": "Подтвердить"}]}]}


def env(root=None) -> dict:
    return C.new_envelope(root or sample(), "pixso", {"nodesIn": 10, "nodesOut": 3})


class Validation(unittest.TestCase):
    def test_sample_is_valid(self):
        self.assertEqual(C.validate(env()), [])

    def test_all_problems_reported_with_paths(self):
        bad = sample()
        bad["children"][0]["kind"] = "button"
        bad["children"][0]["colour"] = "red"
        bad["layout"]["gap"] = float("nan")
        bad["style"]["radius"] = [1, 2, 3]
        bad["size"]["w"] = "wide"
        problems = C.validate(env(bad))
        text = "\n".join(problems)
        self.assertIn("root.children[0].kind", text)
        self.assertIn("root.children[0].colour: неизвестное поле", text)
        self.assertIn("root.layout.gap: ожидается число", text)
        self.assertIn("root.style.radius", text)
        self.assertIn("root.size.w", text)
        self.assertGreaterEqual(len(problems), 5, "собраны ВСЕ нарушения, а не первое")

    def test_numbers_must_be_real_numbers(self):
        for bad in (True, "5", None, float("inf")):
            n = sample()
            n["layout"]["gap"] = bad
            self.assertTrue(C.validate(env(n)), repr(bad))

    def test_envelope_rules(self):
        e = env()
        e["extra"] = 1
        self.assertTrue(any("extra" in p for p in C.validate(e)))
        e = env()
        e["daiver"]["sourceFormat"] = "sketch"
        self.assertTrue(any("sourceFormat" in p for p in C.validate(e)))
        e = env()
        e["daiver"]["role"] = "plan"
        self.assertTrue(C.validate(e))
        for missing in ("daiver", "root"):
            e = env()
            del e[missing]
            self.assertTrue(C.validate(e), missing)
        self.assertEqual(C.validate([]), ["конверт: ожидается объект"])
        for fmt in C.SOURCE_FORMATS:                       # dom и code — та же схема
            self.assertEqual(C.validate(C.new_envelope(sample(), fmt)), [])

    def test_ids_are_unique(self):
        n = sample()
        n["children"][1]["id"] = "2"
        problems = C.validate(env(n))
        self.assertTrue(any("id «2» повторяется" in p and "root.children[1]" in p for p in problems), problems)

    def test_art_node_has_no_children(self):
        n = {"id": "r", "kind": "frame", "children": [
            {"id": "a", "kind": "vector", "art": {"slug": "logo", "nodes": 29},
             "children": [{"id": "b", "kind": "vector"}]}]}
        self.assertTrue(any("арт" in p for p in C.validate(env(n))))
        n["children"][0]["children"] = []
        self.assertEqual(C.validate(env(n)), [])

    def test_required_fields_and_component_slot(self):
        n = {"id": "r", "kind": "instance", "component": {"content": [{"text": "x"}]}}
        text = "\n".join(C.validate(env(n)))
        self.assertIn("нет обязательного поля «figmaName»", text)
        self.assertIn("нет обязательного поля «id»", text)
        self.assertIn("нет обязательного поля «overridden»", text)

    def test_integral_float_is_accepted_for_integers(self):
        n = sample()
        n["children"][0]["font"]["weight"] = 600.0
        self.assertEqual(C.validate(env(n)), [])
        n["children"][0]["font"]["weight"] = 600.5
        self.assertTrue(C.validate(env(n)))

    def test_deep_tree_does_not_hit_recursion_limit(self):
        root = {"id": "0", "kind": "frame"}
        cur = root
        for i in range(1, 3000):
            nxt = {"id": str(i), "kind": "frame"}
            cur["children"] = [nxt]
            cur = nxt
        self.assertEqual(C.validate(env(root)), [])
        self.assertEqual(sum(1 for _ in C.iter_nodes(root)), 3000)
        self.assertEqual(len(C.weights(root)), 3000)


class Numbers(unittest.TestCase):
    def test_rounding_half_up_and_integers(self):
        self.assertEqual(C.round_num(0.125), 0.13)
        self.assertEqual(C.round_num(-0.125), -0.12, "как JS Math.round: половина вверх")
        self.assertEqual(C.round_num(5.0), 5)
        self.assertIsInstance(C.round_num(5.0), int)
        self.assertEqual(C.round_num(0.1234, 3), 0.123)
        self.assertEqual(C.round_num(7), 7)

    def test_normalize_alpha_has_three_decimals(self):
        n = C.normalize({"style": {"opacity": 0.12345, "x": 1.23456}, "box": {"x": 3.0}})
        self.assertEqual(n["style"]["opacity"], 0.123)
        self.assertEqual(n["style"]["x"], 1.23)
        self.assertEqual(n["box"]["x"], 3)

    def test_fmt_rgba(self):
        self.assertEqual(C.fmt_rgba(31, 20, 51, 0.12), "rgba(31, 20, 51, 0.12)")
        self.assertEqual(C.fmt_rgba(254.6, 0, 300, 1), "rgba(255, 0, 255, 1)")
        self.assertEqual(C.fmt_rgba(0, 0, 0, 0.12349), "rgba(0, 0, 0, 0.123)")

    def test_five_and_five_point_zero_hash_equal_after_normalize(self):
        a, b = sample(), sample()
        a["layout"]["gap"] = 16
        b["layout"]["gap"] = 16.0
        self.assertEqual(C.subtree_hash(C.normalize(a)), C.subtree_hash(C.normalize(b)))


class HashAndWeight(unittest.TestCase):
    def test_hash_shape_and_key_order_independence(self):
        a = sample()
        b = json.loads(json.dumps(a, sort_keys=True))
        h = C.subtree_hash(a)
        self.assertRegex(h, r"^[0-9a-f]{12}$")
        self.assertEqual(h, C.subtree_hash(b))
        self.assertEqual(C.canonical_bytes(a), C.canonical_bytes(b))

    def test_hash_changes_with_subtree_edit_but_not_with_sibling_edit(self):
        base = sample()
        edited = copy.deepcopy(base)
        edited["children"][1]["component"]["content"][0]["text"] = "Отправить"
        sib = copy.deepcopy(base)
        sib["children"][0]["text"] = "Другой заголовок"
        title_before = C.subtree_hash(base["children"][0])
        self.assertNotEqual(C.subtree_hash(base), C.subtree_hash(edited), "правка внутри — хеш корня другой")
        self.assertNotEqual(C.subtree_hash(base["children"][1]), C.subtree_hash(edited["children"][1]))
        self.assertEqual(C.subtree_hash(base["children"][1]), C.subtree_hash(sib["children"][1]),
                         "правка соседа секцию не стухляет")
        self.assertNotEqual(title_before, C.subtree_hash(sib["children"][0]))

    def test_weight_is_minified_utf8_bytes(self):
        n = {"id": "1", "kind": "text", "text": "Привет"}
        self.assertEqual(C.weight(n), len(json.dumps(n, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":")).encode("utf-8")))
        ascii_only = {"id": "1", "kind": "text", "text": "Privet"}
        self.assertEqual(C.weight(n) - C.weight(ascii_only), 6, "кириллица — по 2 байта на букву")

    def test_weights_bulk_matches_per_node_weight(self):
        rnd = random.Random(7)

        def build(depth: int, prefix: str) -> dict:
            node = {"id": prefix, "kind": "frame" if depth else "text"}
            if rnd.random() < 0.5:
                node["name"] = "узел-" + prefix
            if rnd.random() < 0.3:
                node["text"] = "т" * rnd.randint(0, 5)
            if depth and rnd.random() < 0.9:
                node["children"] = [build(depth - 1, f"{prefix}.{i}") for i in range(rnd.randint(0, 4))]
            return node

        for seed_depth in (2, 3, 4):
            root = build(seed_depth, "r")
            bulk = C.weights(root)
            for n in C.iter_nodes(root):
                self.assertEqual(bulk[n["id"]], C.weight(n), n["id"])
        # пустой словарь признаков и пустой children
        tiny = {"children": [], "id": "x", "kind": "frame"}
        self.assertEqual(C.weights(tiny)["x"], C.weight(tiny))


class Navigation(unittest.TestCase):
    def setUp(self):
        self.root = sample()

    def test_iter_is_document_order(self):
        self.assertEqual([n["id"] for n in C.iter_nodes(self.root)], ["1", "2", "3", "3a"])

    def test_find_and_ancestors(self):
        self.assertEqual(C.find_node(self.root, "3a")["text"], "Подтвердить")
        self.assertIsNone(C.find_node(self.root, "nope"))
        self.assertEqual([n["id"] for n in C.ancestors_of(self.root, "3a")], ["1", "3"])
        self.assertEqual(C.ancestors_of(self.root, "1"), [])
        self.assertIsNone(C.ancestors_of(self.root, "nope"))
        self.assertEqual(sorted(C.index_nodes(self.root)), ["1", "2", "3", "3a"])
        self.assertEqual(C.descendants_count(self.root), 3)


class Texts(unittest.TestCase):
    def test_no_content_loss_at_any_depth_including_resolved_inside_resolved(self):
        inner = {"id": "i2", "kind": "instance", "component": {"figmaName": "Badge", "textProps": {"v": "Внутри"}}}
        outer = {"id": "i1", "kind": "instance",
                 "component": {"figmaName": "Card", "content": [{"id": "i1;t", "text": "Снаружи", "overridden": True}]},
                 "children": [{"id": "f", "kind": "frame", "children": [inner,
                                                                         {"id": "t", "kind": "text", "text": "Глубоко"}]}]}
        root = {"id": "r", "kind": "frame", "children": [outer]}
        self.assertEqual(C.texts(root), ["Снаружи", "Внутри", "Глубоко"])

    def test_child_equal_to_prop_is_not_counted_twice_but_distinct_ones_stay(self):
        inst = {"id": "i", "kind": "instance",
                "component": {"figmaName": "Btn", "textProps": {"label": "OK", "hint": "Подсказка"}},
                "children": [{"id": "a", "kind": "text", "text": "OK"},
                             {"id": "b", "kind": "text", "text": "Другой ребёнок"},
                             {"id": "c", "kind": "text", "text": "OK"}]}
        # один проп «OK» гасит ОДНОГО потомка; второй «OK» — distinct, остаётся
        self.assertEqual(C.texts(inst), ["OK", "Подсказка", "Другой ребёнок", "OK"])

    def test_content_replaces_textprops(self):
        inst = {"id": "i", "kind": "instance",
                "component": {"figmaName": "Btn", "textProps": {"label": "Старое"},
                              "content": [{"id": "i;l", "text": "Новое", "overridden": False}]}}
        got = C.collect_texts(inst)
        self.assertEqual([t["text"] for t in got], ["Новое"])
        self.assertEqual((got[0]["via"], got[0]["overridden"]), ("content", False),
                         "плейсхолдер библиотеки в списке, но с флагом overridden=false")

    def test_occluded_subtree_is_excluded(self):
        root = {"id": "r", "kind": "frame", "children": [
            {"id": "page", "kind": "frame", "occluded": True,
             "children": [{"id": "x", "kind": "text", "text": "Страница под модалкой"}]},
            {"id": "modal", "kind": "frame", "children": [{"id": "y", "kind": "text", "text": "Модалка"}]}]}
        self.assertEqual(C.texts(root), ["Модалка"])

    def test_nested_instance_without_own_texts_keeps_outer_dedup_scope(self):
        inner = {"id": "n", "kind": "instance", "component": {"figmaName": "Label"},
                 "children": [{"id": "t", "kind": "text", "text": "OK"}]}
        outer = {"id": "o", "kind": "instance", "component": {"figmaName": "Btn", "textProps": {"l": "OK"}},
                 "children": [inner]}
        self.assertEqual(C.texts(outer), ["OK"])

    def test_text_on_any_kind_and_empty_ignored(self):
        root = {"id": "r", "kind": "frame", "children": [
            {"id": "a", "kind": "icon", "text": "Иконка-подпись"}, {"id": "b", "kind": "text", "text": ""}]}
        self.assertEqual(C.texts(root), ["Иконка-подпись"])


class Files(unittest.TestCase):
    def test_roundtrip_deterministic_minified_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frames" / "main" / "visual-requirements.canonical.json"
            written = C.write_canonical(path, env())
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("\n", raw)
            self.assertNotIn(": ", raw)
            self.assertEqual(json.loads(raw), written)
            self.assertEqual(C.read_canonical(path), written)
            # другой порядок ключей и 16.0 вместо 16 — те же байты
            shuffled = json.loads(json.dumps(env(), sort_keys=True))
            shuffled["root"]["layout"]["gap"] = 16.0
            path2 = Path(tmp) / "b.json"
            C.write_canonical(path2, shuffled)
            self.assertEqual(path2.read_text(encoding="utf-8"), raw)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name], "tmp-файла не осталось")

    def test_invalid_is_refused_and_nothing_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = env()
            bad["root"]["kind"] = "button"
            path = Path(tmp) / "c.json"
            with self.assertRaises(C.CanonError) as cm:
                C.write_canonical(path, bad)
            self.assertTrue(any("kind" in p for p in cm.exception.problems))
            self.assertFalse(path.exists())

    def test_read_reports_all_failure_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in (("a.json", "{oops"), ("b.json", '{"daiver": 1}')):
                (Path(tmp) / name).write_text(content, encoding="utf-8")
                with self.assertRaises(C.CanonError):
                    C.read_canonical(Path(tmp) / name)
            with self.assertRaises(C.CanonError):
                C.read_canonical(Path(tmp) / "нет.json")


if __name__ == "__main__":
    unittest.main()
