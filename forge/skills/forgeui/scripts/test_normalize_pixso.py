#!/usr/bin/env python3
"""test_normalize_pixso.py — Pixso DSL → канон: структура и геометрия (задача 1.3; QG U2, F1, INV B2).

Пинит на фикстуре «Modal»: корень 600×245 с column-раскладкой, три инстанса в порядке позиции
(y 20, 64, 144), канон валиден и при повторе побайтно тот же. На синтетике — каждое правило адаптера
(они выведены по данным образца, см. docstring ingest/pixso.py): раскладка и выравнивание, намерение
размера (fixed/hug/fill, текст), box только вне потока и в координатах экрана, порядок детей,
скрытые узлы, посторонние и множественные корни, варианты/имя компонента, отказы.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402
from daiver import names as N  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.ingest import detect as DT  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402
from daiver.pixso import dsl as D  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
URL = "https://pixso.example/a?item-id=2:153702"


def n(guid, typ="FRAME", name=None, w=None, h=None, left=0, top=0, kids=None, al=None, **kw):
    node = {"guid": guid, "type": typ, "name": name or guid, "left": left, "top": top}
    if w is not None:
        node["width"] = w
    if h is not None:
        node["height"] = h
    if al is not None:
        node["autoLayout"] = al
    if kids:
        node["childNode"] = kids
    node.update(kw)
    return node


def doc(*roots, defs=()):
    return {"dsl": {"pixTreeDslNodes": list(roots), "pixComponentTreeDslNodes": list(defs)}}


def conv(*roots, defs=()) -> dict:
    return PX.normalize_doc(doc(*roots, defs=defs))


def flow(**kw):
    """autoLayout ребёнка в потоке: autoLayoutByParent + коды размера."""
    return dict({"autoLayoutByParent": True}, **kw)


def by_id(env) -> dict:
    return C.index_nodes(env["root"])


class OnFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copyfile(str(FIXTURE), str(self.root / "dsl.json"))
        T.call("daiver_pixso_pull", self.root, {"track": "STOR-100", "dslPath": "dsl.json", "frameUrl": URL})
        self.out = T.call("daiver_normalize", self.root, {"track": "STOR-100"})
        self.path = self.root / ".daiver/tracks/STOR-100/visual-requirements.canonical.json"
        self.env = C.read_canonical(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_root_is_modal_column_layout(self):
        r = self.env["root"]
        self.assertEqual((r["id"], r["kind"], r["name"]), ("2:153702", "frame", "Modal"))
        self.assertEqual(r["box"], {"x": 0, "y": 0, "w": 600, "h": 245})
        self.assertEqual(r["size"], {"w": 600, "h": "hug"}, "высота по содержимому: число DSL верить нельзя")
        lay = r["layout"]
        self.assertEqual(lay["direction"], "column")
        self.assertEqual(lay["padding"], {"top": 20, "right": 20, "bottom": 20, "left": 20})
        self.assertEqual(lay["align"], {"main": "start", "cross": "center"})
        self.assertTrue(lay["stated"])

    def test_three_instances_in_position_order(self):
        kids = self.env["root"]["children"]
        self.assertEqual([k["id"] for k in kids], ["2:153703", "22:63141", "2:153704"], "y 20, 64, 144")
        self.assertTrue(all(k["kind"] == "instance" for k in kids))
        self.assertEqual([k["component"]["figmaName"] for k in kids], ["Modal Header", "Modal Header", "Modal Action"])
        self.assertEqual(kids[0]["component"]["variants"], {"Steps": "OFF"})
        self.assertEqual(kids[2]["component"]["variants"], {"Variant": "Default"})
        for k in kids:
            self.assertNotIn("box", k, "внутри потока позицию выражает раскладка")
        self.assertEqual(kids[0]["size"], {"w": "fill", "h": "hug"})
        self.assertEqual(kids[2]["size"], {"w": 600, "h": "hug"}, "код 0 — фиксированная ширина, как задано в макете")

    def test_no_hidden_nodes_and_stats(self):
        s = self.env["stats"]
        self.assertEqual((s["nodesIn"], s["nodesOut"], s["screenRoots"], s["instances"]), (4, 4, 1, 3))
        self.assertNotIn("hiddenDropped", s)
        self.assertEqual(s["warnings"], [])
        m = TR.load(self.root, "STOR-100")
        self.assertEqual(s["sourceSha256"], m["frames"][0]["requirement"]["sha256"])
        self.assertEqual(self.out["rootHash"], C.subtree_hash(self.env["root"]))
        self.assertEqual(C.validate(self.env), [])

    def test_repeat_is_byte_identical(self):
        first = self.path.read_bytes()
        T.call("daiver_normalize", self.root, {"track": "STOR-100"})
        self.assertEqual(self.path.read_bytes(), first)

    def test_refusals(self):
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_normalize", self.root, {"track": "NOPE-1"})
        self.assertIn("daiver_pixso_pull", str(cm.exception))
        req = self.root / ".daiver/tracks/STOR-100/visual-requirements.pixso.json"
        original = req.read_text(encoding="utf-8")
        req.write_text(original + " ", encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_normalize", self.root, {"track": "STOR-100"})
        self.assertIn("мимо pull", str(cm.exception))
        req.unlink()
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_normalize", self.root, {"track": "STOR-100"})
        self.assertIn("нет", str(cm.exception))
        with self.assertRaises(T.ToolError):
            T.call("daiver_normalize", self.root, {"track": "STOR-100", "frame": "ghost"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_normalize", self.root, {"track": "STOR-100", "outPath": "x.json"})   # outPath не принимается

    def test_unknown_and_figma_formats_are_named_refusals(self):
        m = TR.load(self.root, "STOR-100")
        req = TR.frame_paths(self.root, m)["requirement"]
        for content, needle in (('{"a": 1}', "не распознан"),
                                ('{"type": "FRAME", "children": []}', "X.2"),
                                ("<html><body></body></html>", "X.2")):
            req.write_text(content, encoding="utf-8")
            m["frames"][0]["requirement"]["sha256"] = TR.sha256_file(req)
            TR.save(self.root, m)
            with self.assertRaises(T.ToolError, msg=content) as cm:
                T.call("daiver_normalize", self.root, {"track": "STOR-100"})
            self.assertIn(needle, str(cm.exception))


class Layout(unittest.TestCase):
    def test_direction_gap_padding_align(self):
        al = {"stackMode": "HORIZONTAL", "autoLayoutItemSpacing": 8, "autoLayoutPaddingLeft": 12,
              "autoLayoutPaddingTop": 4, "autoLayoutPrimaryAlign": "space-between", "autoLayoutCounterAlign": "flex-start",
              "autoLayoutAlignType": "center"}
        lay = conv(n("r", w=100, h=40, al=al))["root"]["layout"]
        self.assertEqual(lay, {"direction": "row", "stated": True, "gap": 8,
                               "padding": {"top": 4, "right": 0, "bottom": 0, "left": 12},
                               "align": {"main": "space-between", "cross": "start"}})
        al = {"stackMode": "VERTICAL", "autoLayoutPrimaryAlign": "flex-end", "autoLayoutCounterAlign": "center"}
        lay = conv(n("r", w=100, h=40, al=al))["root"]["layout"]
        self.assertEqual(lay["direction"], "column")
        self.assertEqual(lay["align"], {"main": "end", "cross": "center"})
        self.assertNotIn("gap", lay)
        self.assertNotIn("padding", lay, "нулевые отступы не пишутся")

    def test_alignType_is_not_the_main_axis_alignment(self):
        al = {"stackMode": "VERTICAL", "autoLayoutAlignType": "space-between"}
        lay = conv(n("r", w=10, h=10, al=al))["root"]["layout"]
        self.assertNotIn("align", lay)

    def test_unknown_align_value_warns_once_and_is_omitted(self):
        kid = lambda g: n(g, w=5, h=5, al=flow(stackMode="HORIZONTAL", autoLayoutPrimaryAlign="weird"))  # noqa: E731
        env = conv(n("r", w=50, h=50, al={"stackMode": "VERTICAL", "autoLayoutPrimaryAlign": "weird"},
                     kids=[kid("a"), kid("b")]))
        self.assertEqual(sum("weird" in w for w in env["stats"]["warnings"]), 1)
        self.assertNotIn("align", env["root"]["layout"])

    def test_direction_code_fallback_needs_children(self):
        a = conv(n("r", w=9, h=9, al={"autoLayoutDirection": 1}, kids=[n("k", w=1, h=1)]))
        self.assertEqual(a["root"]["layout"]["direction"], "row")
        self.assertEqual(a["stats"]["layoutFromDirectionCode"], 1)
        b = conv(n("r", w=9, h=9, al={"autoLayoutDirection": 1}))
        self.assertNotIn("layout", b["root"], "без stackMode и без детей — не контейнер")

    def test_plain_frame_has_no_layout(self):
        env = conv(n("r", w=9, h=9, al={"autoLayoutByParent": True}, kids=[n("k", w=1, h=1)]))
        self.assertNotIn("layout", env["root"])


class Sizing(unittest.TestCase):
    def parent(self, kid):
        return conv(n("r", w=200, h=100, al={"stackMode": "VERTICAL"}, kids=[kid]))

    def test_codes_in_flow(self):
        for codes, expect in (((0, 0), {"w": 30, "h": 20}), ((1, 1), {"w": "hug", "h": "hug"}),
                              ((2, 1), {"w": "fill", "h": "hug"}), ((0, 2), {"w": 30, "h": "fill"})):
            kid = n("k", w=30, h=20, al=flow(autoLayoutWidthResize=codes[0], autoLayoutHeightResize=codes[1]))
            self.assertEqual(by_id(self.parent(kid))["k"]["size"], expect, codes)

    def test_fill_needs_a_flow_container_else_number(self):
        kid = n("k", w=30, h=20, al={"autoLayoutWidthResize": 2, "autoLayoutHeightResize": 1})
        env = conv(n("r", w=200, h=100, kids=[kid]))                 # родитель — не контейнер
        self.assertEqual(by_id(env)["k"]["size"], {"w": 30, "h": "hug"})

    def test_container_hug_from_stack_sizing(self):
        h = n("c", w=10, h=10, al={"stackMode": "HORIZONTAL", "stackPrimarySizing": "RESIZE_TO_FIT",
                                   "stackCounterSizing": "FIXED"})
        self.assertEqual(conv(h)["root"]["size"], {"w": "hug", "h": 10}, "горизонтальный: primary — ширина")
        v = n("c", w=10, h=10, al={"stackMode": "VERTICAL", "stackPrimarySizing": "RESIZE_TO_FIT",
                                   "stackCounterSizing": "FIXED"})
        self.assertEqual(conv(v)["root"]["size"], {"w": 10, "h": "hug"}, "вертикальный: primary — высота")

    def test_text_hug_comes_from_text_auto_resize(self):
        def txt(**kw):
            return by_id(self.parent(n("t", "PARAGRAPH", w=40, h=14, nodeText="Привет",
                                       al=flow(autoLayoutWidthResize=0, autoLayoutHeightResize=0), **kw)))["t"]
        self.assertEqual(txt(textAutoResize="WIDTH_AND_HEIGHT")["size"], {"w": "hug", "h": "hug"})
        self.assertEqual(txt(textAutoResize="HEIGHT")["size"], {"w": 40, "h": "hug"})
        self.assertEqual(txt()["size"], {"w": 40, "h": 14})
        t = by_id(self.parent(n("t", "PARAGRAPH", w=40, h=14, nodeText="x", textAutoResize="HEIGHT",
                                al=flow(autoLayoutWidthResize=2, autoLayoutHeightResize=0))))["t"]
        self.assertEqual(t["size"], {"w": "fill", "h": "hug"})
        self.assertEqual((t["kind"], t["text"]), ("text", "Привет" if False else "x"))

    def test_missing_dimensions_are_omitted(self):
        env = conv(n("r", w=10, h=10, kids=[n("k")]))
        self.assertNotIn("size", by_id(env)["k"])
        self.assertNotIn("box", by_id(env)["k"])


class Positions(unittest.TestCase):
    def test_root_is_normalized_to_origin_and_absolute_boxes_accumulate(self):
        inner = n("c", w=20, h=10, left=5, top=7)
        mid = n("b", w=60, h=40, left=30, top=20, kids=[inner])
        env = conv(n("r", w=200, h=100, left=500, top=300, kids=[mid]))
        ids = by_id(env)
        self.assertEqual(ids["r"]["box"], {"x": 0, "y": 0, "w": 200, "h": 100})
        self.assertEqual(ids["b"]["box"], {"x": 30, "y": 20, "w": 60, "h": 40})
        self.assertEqual(ids["c"]["box"], {"x": 35, "y": 27, "w": 20, "h": 10}, "координаты экрана, не родителя")

    def test_flow_children_have_no_box_but_descendant_origin_still_accumulates(self):
        grand = n("g", w=4, h=4, left=1, top=2)
        kid = n("k", w=30, h=30, left=10, top=40, al=flow(), kids=[grand])
        env = conv(n("r", w=100, h=100, left=9, top=9, al={"stackMode": "VERTICAL"}, kids=[kid]))
        ids = by_id(env)
        self.assertNotIn("box", ids["k"])
        self.assertEqual(ids["g"]["box"], {"x": 11, "y": 42, "w": 4, "h": 4})

    def test_absolute_child_of_container_has_box_and_follows_flow(self):
        a = n("a", w=10, h=10, left=0, top=0, al=flow())
        b = n("b", w=10, h=10, left=0, top=50, al=flow())
        over = n("over", w=5, h=5, left=3, top=1, al={"autoLayoutItemAbsolutePos": True})
        env = conv(n("r", w=60, h=100, al={"stackMode": "VERTICAL"}, kids=[over, b, a]))
        self.assertEqual([k["id"] for k in env["root"]["children"]], ["a", "b", "over"])
        ids = by_id(env)
        self.assertNotIn("box", ids["a"])
        self.assertEqual(ids["over"]["box"], {"x": 3, "y": 1, "w": 5, "h": 5})


class Ordering(unittest.TestCase):
    def kids(self, mode, specs):
        al = {"stackMode": mode} if mode else None
        env = conv(n("r", w=200, h=200, al=al, kids=[n(g, w=10, h=10, left=x, top=y, al=flow() if mode else None)
                                                     for g, x, y in specs]))
        return [k["id"] for k in env["root"]["children"]]

    def test_row_sorts_by_left_column_by_top(self):
        self.assertEqual(self.kids("HORIZONTAL", [("c", 50, 0), ("a", 0, 0), ("b", 20, 0)]), ["a", "b", "c"])
        self.assertEqual(self.kids("VERTICAL", [("c", 0, 50), ("a", 0, 0), ("b", 0, 20)]), ["a", "b", "c"])

    def test_non_container_sorts_by_reading_order(self):
        self.assertEqual(self.kids(None, [("d", 50, 20), ("a", 0, 0), ("c", 0, 20), ("b", 50, 0)]),
                         ["a", "b", "c", "d"])

    def test_equal_positions_follow_drawing_order(self):
        """Дерево Pixso — «topmost first»: последний в массиве рисуется первым, значит идёт первым."""
        self.assertEqual(self.kids("VERTICAL", [("top", 0, 5), ("mid", 0, 5), ("bottom", 0, 5)]),
                         ["bottom", "mid", "top"])


class Structure(unittest.TestCase):
    def test_hidden_nodes_are_dropped_with_subtree(self):
        hidden = n("h", w=5, h=5, visible=False, kids=[n("h1", w=1, h=1), n("h2", w=1, h=1)])
        env = conv(n("r", w=50, h=50, kids=[hidden, n("ok", w=5, h=5)]))
        self.assertEqual(list(by_id(env)), ["r", "ok"])
        self.assertEqual((env["stats"]["hiddenDropped"], env["stats"]["hiddenNodesDropped"]), (1, 3))

    def test_stray_root_is_dropped_with_message(self):
        env = conv(n("frame", w=1000, h=800), n("icon", w=24, h=24, left=1100, top=10))
        self.assertEqual(env["root"]["id"], "frame")
        self.assertEqual(env["stats"]["strayRootsDropped"], 1)
        self.assertEqual(env["stats"]["screenRoots"], 1)
        self.assertTrue(any("icon" in w and "посторонний" in w for w in env["stats"]["warnings"]))

    def test_two_real_roots_are_wrapped_and_reported(self):
        env = conv(n("s2", w=300, h=200, left=400, top=100), n("s1", w=300, h=200, left=0, top=100))
        r = env["root"]
        self.assertEqual(r["id"], "inferred:roots:s2")
        self.assertEqual(r["box"], {"x": 0, "y": 0, "w": 700, "h": 200})
        self.assertEqual([k["id"] for k in r["children"]], ["s1", "s2"], "по позиции")
        self.assertEqual(r["children"][1]["box"]["x"], 400)
        self.assertEqual(env["stats"]["screenRoots"], 2)
        self.assertTrue(any("2 корня экрана" in w for w in env["stats"]["warnings"]))
        self.assertEqual(C.validate(env), [])

    def test_hidden_root_and_all_hidden(self):
        env = conv(n("a", w=10, h=10, visible=False), n("b", w=10, h=10))
        self.assertEqual(env["root"]["id"], "b")
        with self.assertRaises(ValueError):
            conv(n("a", w=10, h=10, visible=False))

    def test_kinds_and_text(self):
        kids = [n("t", "PARAGRAPH", w=5, h=5, nodeText="Текст"), n("e", "ELLIPSE", w=5, h=5),
                n("v", "VECTOR", w=5, h=5), n("bo", "BOOLEAN_OPERATION", w=5, h=5), n("rc", "RECTANGLE", w=5, h=5),
                n("q", "WEIRD_TYPE", w=5, h=5, kids=[n("q1", w=1, h=1)]), n("e2", "PARAGRAPH", w=5, h=5)]
        env = conv(n("r", w=50, h=50, kids=kids))
        ids = by_id(env)
        self.assertEqual([ids[g]["kind"] for g in ("t", "e", "v", "bo", "rc", "q", "q1")],
                         ["text", "rect", "vector", "vector", "rect", "frame", "frame"])
        self.assertEqual(ids["t"]["text"], "Текст")
        self.assertNotIn("text", ids["e2"], "пустой текст не пишется")
        self.assertEqual(sum("WEIRD_TYPE" in w for w in env["stats"]["warnings"]), 1)

    def test_auto_names_are_counted_not_renamed(self):
        env = conv(n("r", name="Frame 123", w=5, h=5, kids=[n("a", name="Group 5", w=1, h=1),
                                                            n("b", name="Header", w=1, h=1)]))
        self.assertEqual(env["root"]["name"], "Frame 123")
        self.assertEqual(env["stats"]["autoNamed"], 2)


class Instances(unittest.TestCase):
    DEFS = [{"guid": "d1", "type": "SYMBOL", "name": "Button", "layerType": "COMPONENT_SET", "childNode": [
        {"guid": "d1v", "type": "SYMBOL", "name": "State=hover, Size=L"}]},
        {"guid": "d2", "type": "SYMBOL", "name": "Divider", "layerType": "COMPONENT"}]

    def test_figma_name_from_state_group_and_variants_from_prop_ref(self):
        inst = n("i", "INSTANCE", name="whatever", w=10, h=10, mainComponent="d1v", mainStateGroup="d1",
                 propRefMap={"variant": [{"name": "State", "value": "hover", "type": "variant"},
                                         {"name": "Size", "value": "L", "type": "variant"}]})
        c = by_id(conv(n("r", w=50, h=50, kids=[inst]), defs=self.DEFS))["i"]["component"]
        self.assertEqual(c, {"figmaName": "Button", "variants": {"State": "hover", "Size": "L"}})

    def test_variants_fall_back_to_the_name_and_standalone_component(self):
        inst = n("i", "INSTANCE", name="State=hover, Size=L", w=10, h=10, mainComponent="d1v", mainStateGroup="d1")
        c = by_id(conv(n("r", w=50, h=50, kids=[inst]), defs=self.DEFS))["i"]["component"]
        self.assertEqual(c["variants"], {"State": "hover", "Size": "L"})
        solo = n("s", "INSTANCE", name="line", w=10, h=1, mainComponent="d2")
        c = by_id(conv(n("r", w=50, h=50, kids=[solo]), defs=self.DEFS))["s"]["component"]
        self.assertEqual(c, {"figmaName": "Divider"}, "обычный компонент: без mainStateGroup, без вариантов")

    def test_unresolved_instance_is_fallback_with_warning(self):
        ghost = n("g", "INSTANCE", name="Ghost", w=10, h=10, mainComponent="nope",
                  kids=[n("gc", "PARAGRAPH", w=5, h=5, nodeText="внутри")])
        env = conv(n("r", w=50, h=50, kids=[ghost]), defs=self.DEFS)
        ids = by_id(env)
        self.assertEqual(ids["g"]["component"], {"figmaName": "Ghost", "childrenRole": "fallback"})
        self.assertEqual(ids["gc"]["text"], "внутри", "дети fallback-инстанса сохранены как разметка")
        self.assertEqual(env["stats"]["instancesUnresolved"], 1)
        self.assertTrue(any("fallback" in w for w in env["stats"]["warnings"]))

    def test_generic_named_root_instance_becomes_a_frame(self):
        env = conv(n("r", "INSTANCE", name="Frame 12", w=50, h=50, mainComponent="d2", kids=[n("k", w=5, h=5)]),
                   defs=self.DEFS)
        self.assertEqual(env["root"]["kind"], "frame")
        self.assertNotIn("component", env["root"])
        self.assertEqual(env["stats"]["genericRootFallback"], 1)
        self.assertEqual([k["id"] for k in env["root"]["children"]], ["k"])


class Determinism(unittest.TestCase):
    def test_same_document_same_bytes_and_valid(self):
        d = doc(n("r", w=200, h=100, al={"stackMode": "VERTICAL"}, kids=[
            n("a", w=10, h=10, top=30, al=flow(autoLayoutWidthResize=2, autoLayoutHeightResize=1)),
            n("b", w=10, h=10, top=0, al=flow())]))
        a = PX.normalize_doc(json.loads(json.dumps(d)))
        b = PX.normalize_doc(json.loads(json.dumps(d)))
        self.assertEqual(C.dumps(a), C.dumps(b))
        self.assertEqual(C.validate(C.normalize(a)), [])
        self.assertEqual([k["id"] for k in a["root"]["children"]], ["b", "a"])

    def test_not_pixso_is_refused(self):
        for bad in ({}, {"dsl": {"pixTreeDslNodes": []}}, {"children": []}):
            with self.assertRaises(D.DslError, msg=repr(bad)):
                PX.normalize_doc(bad)


class Names(unittest.TestCase):
    def test_auto_names(self):
        for yes in ("Frame 123", "Group 5", "TEXT", "Rectangle", "rectangle 12", "Vector", "Фрейм 7", "Text", "Ellipse 3"):
            self.assertTrue(N.is_auto_name(yes), yes)
        for no in ("Header", "Frame 1 copy", "header_bf_01", "Modal", "", None, 5, "Group of cards"):
            self.assertFalse(N.is_auto_name(no), no)

    def test_variants(self):
        self.assertEqual(N.parse_variants("State=hover, Size=L"), {"State": "hover", "Size": "L"})
        self.assertEqual(N.parse_variants("Size=Large,Type=Primary,State=Default"),
                         {"Size": "Large", "Type": "Primary", "State": "Default"})
        self.assertEqual(N.parse_variants("Has icon=Yes, Left side=Short text"),
                         {"Has icon": "Yes", "Left side": "Short text"})
        self.assertEqual(N.parse_variants("Label=Hello, world"), {"Label": "Hello, world"},
                         "запятая, за которой не идёт «ключ=», — часть значения")
        for no in ("Button", "a=", "=b", "a=b, c=", "", None):
            self.assertIsNone(N.parse_variants(no), no)


class Detect(unittest.TestCase):
    def test_object_detection(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(DT.detect_obj(raw)[0], "pixso")
        self.assertEqual(DT.detect_obj(raw["dsl"])[0], "pixso", "слимнутое тело без обёртки тоже")
        self.assertEqual(DT.detect_obj({"type": "FRAME", "children": []})[0], "figma-json-a")
        self.assertEqual(DT.detect_obj({"document": {"type": "DOCUMENT", "children": []}})[0], "figma-json-a")
        self.assertIsNone(DT.detect_obj({"a": 1})[0])
        self.assertIsNone(DT.detect_obj([1, 2])[0])

    def test_file_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x"
            p.write_text("﻿" + FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")   # BOM
            self.assertEqual(DT.detect_file(p)[0], "pixso")
            p.write_text("  <!doctype html><div></div>", encoding="utf-8")
            self.assertEqual(DT.detect_file(p)[0], "figma-html")
            for content in ("{oops", "plain text", ""):
                p.write_text(content, encoding="utf-8")
                self.assertIsNone(DT.detect_file(p)[0], content)
            self.assertIsNone(DT.detect_file(Path(tmp) / "нет")[0])
            p.write_bytes(b"\xff\xfe\x00")
            self.assertIsNone(DT.detect_file(p)[0])


if __name__ == "__main__":
    unittest.main()
