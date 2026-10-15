#!/usr/bin/env python3
"""test_art.py — векторный арт: группа шейпов → один узел `art` (задача 1.6; спека §7.4; QG U1, F5; INV B7).

В экране образца «Modal» векторных групп нет (экран — фрейм и три инстанса; векторы живут в определениях:
четыре иконки — один составной VECTOR «Outline» 11–20 px в SYMBOL 24×24), поэтому правила §7.4 доказаны
синтетикой — веер безымянных фигур, группа с текстом/инстансом/bitmap, контейнер простых прямоугольников,
auto-layout контейнер, не-ASCII имя — и на анатомии local-композиций (виртуальные узлы материализации).
На реальных данных образца проверено обратное: его векторы остаются иконками-кандидатами (≤ 64 px), а те же
данные, увеличенные до размера рисунка, склеиваются. Пинит F5: slug стабилен между прогонами normalize
(в том числе при другом порядке ключей источника), два рисунка с одним именем разводятся суффиксом, а
сворачивание не теряет ни одного текста и не меняет канон образца.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402
from daiver.ingest import art as AR  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
WHITE = {"type": "SOLID", "color": {"r": 255, "g": 255, "b": 255, "a": 1}}
SHADOW = {"type": "DROP_SHADOW", "color": {"r": 0, "g": 0, "b": 0, "a": 0.2}, "offset": {"x": 0, "y": 2},
          "radius": 4, "spread": 0}


# ── построители DSL ───────────────────────────────────────────────────────────
def n(guid, typ="FRAME", name=None, w=None, h=None, left=0, top=0, kids=None, al=None, **kw):
    node = {"guid": guid, "type": typ, "name": name or guid, "left": left, "top": top}
    if w is not None:
        node["width"] = w
    if h is not None:
        node["height"] = h
    if al is not None:
        node["autoLayout"] = al
    if kids:
        node["childNode"] = list(kids)
    node.update(kw)
    return node


def vec(guid, name=None, w=30, h=30, left=0, top=0, **kw):
    return n(guid, "VECTOR", name=name or "Vector", w=w, h=h, left=left, top=top, **kw)


def group(guid, name, kids, w=200, h=150, left=0, top=0, **kw):
    return n(guid, "GROUP", name=name, w=w, h=h, left=left, top=top, kids=kids, **kw)


def fan(guid="g1", name="Group 12", count=8, left=10, top=10, w=200, h=150):
    return group(guid, name, [vec(f"{guid}v{i}", f"Vector {i}", left=i * 10, top=i * 5) for i in range(1, count + 1)],
                 w=w, h=h, left=left, top=top)


def text(guid, value, name="Label", left=0, top=0, w=100, h=20):
    return n(guid, "PARAGRAPH", name=name, w=w, h=h, left=left, top=top, nodeText=value)


def comp(guid, name, kids=(), published=True, w=100, h=40):
    top = {"guid": guid, "type": "SYMBOL", "layerType": "COMPONENT", "name": name, "width": w, "height": h}
    if kids:
        top["childNode"] = list(kids)
    if published:
        top["publishFile"] = "lib"
    return top


def inst(guid, main, name="Comp", w=100, h=40, left=0, top=0, **kw):
    return n(guid, "INSTANCE", name=name, w=w, h=h, left=left, top=top, mainComponent=main, **kw)


def doc(*kids, defs=(), name="Landing", w=400, h=400):
    return {"dsl": {"pixTreeDslNodes": [n("root", name=name, w=w, h=h, kids=kids)],
                    "pixComponentTreeDslNodes": list(defs)}}


def conv(*kids, defs=(), **kw) -> dict:
    return PX.normalize_doc(doc(*kids, defs=defs, **kw))


def by_id(env) -> dict:
    return C.index_nodes(env["root"])


def arts_of(env) -> list:
    return [x for x in C.iter_nodes(env["root"]) if "art" in x]


def slugs_of(env) -> list:
    return [x["art"]["slug"] for x in arts_of(env)]


def reorder_keys(o):
    """Те же данные, другой порядок ключей словарей (списки не трогаем — порядок в них смысл)."""
    if isinstance(o, dict):
        return {k: reorder_keys(o[k]) for k in reversed(list(o))}
    if isinstance(o, list):
        return [reorder_keys(x) for x in o]
    return o


def canon_bytes(env) -> str:
    return C.dumps(C.normalize(env))


# ── построители канона (правила проверяются без шума конвертера) ──────────────
def cn(id_, kind="frame", name=None, w=None, h=None, kids=None, **kw):
    node = {"id": id_, "kind": kind}
    if name is not None:
        node["name"] = name
    if w is not None:
        node["box"] = {"x": 0, "y": 0, "w": w, "h": w if h is None else h}
    if kids:
        node["children"] = list(kids)
    node.update(kw)
    return node


def shapes(prefix, count, w=30, kind="vector"):
    return [cn(f"{prefix}{i}", kind, f"Vector {i}", w) for i in range(1, count + 1)]


def grp(id_, name, count=3, w=100):
    return cn(id_, "frame", name, w, kids=shapes(id_ + "v", count))


def screen(*kids, name="Landing"):
    return cn("r", "frame", name, 400, kids=kids)


def slugs(root) -> list:
    return [a["slug"] for a in AR.collect_vector_art(root)]


# ── веер безымянных фигур (F5, U1) ────────────────────────────────────────────
class Fan(unittest.TestCase):
    def test_fan_of_unnamed_shapes_becomes_one_art_node(self):
        env = conv(fan(), text("t1", "Привет", top=200))
        found = arts_of(env)
        self.assertEqual(len(found), 1)
        a = found[0]
        self.assertEqual((a["id"], a["kind"], a["name"]), ("g1", "vector", "Group 12"), "id верхушки сохранён")
        self.assertNotIn("children", a)
        self.assertEqual(a["art"], {"slug": "landing", "nodes": 9}, "автоимя → ближайший осмысленный предок; 8 фигур + верхушка")
        self.assertFalse([i for i in by_id(env) if i.startswith("g1v")], "фигуры веера ушли из канона")
        self.assertEqual(env["root"]["kind"], "frame", "root остаётся фреймом")
        self.assertEqual(env["stats"]["art"], {"arts": 1, "collapsedNodes": 8})
        self.assertEqual(C.validate(env), [])

    def test_text_next_to_the_drawing_survives(self):
        env = conv(fan(), text("t1", "Привет", top=200))
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["Привет"])

    def test_nodes_out_counts_the_tree_after_the_collapse(self):
        env = conv(fan(), text("t1", "x", top=200))
        self.assertEqual(env["stats"]["nodesOut"], sum(1 for _ in C.iter_nodes(env["root"])))
        self.assertEqual(env["stats"]["nodesIn"], 1 + 1 + 8 + 1)

    def test_art_node_is_minimal(self):
        a = arts_of(conv(fan(), text("t1", "x", top=200)))[0]
        self.assertEqual(sorted(a), ["art", "box", "id", "kind", "name", "size"])
        self.assertEqual(a["box"], {"x": 10, "y": 10, "w": 200, "h": 150})

    def test_group_opacity_is_kept_but_color_and_tokens_are_not(self):
        g = fan()
        g["opacity"] = 0.5
        g["childNode"][0]["fillPaints"] = [WHITE]
        a = arts_of(conv(g, text("t1", "x", top=200)))[0]
        self.assertEqual(a["style"], {"opacity": 0.5})
        self.assertNotIn("color", a)

    def test_slug_is_stable_between_normalize_runs(self):
        d = doc(fan(), fan("g2", "Group 13", top=200), text("t1", "x", top=380))
        first = canon_bytes(PX.normalize_doc(d))
        self.assertEqual(canon_bytes(PX.normalize_doc(copy.deepcopy(d))), first)
        self.assertEqual(canon_bytes(PX.normalize_doc(reorder_keys(d))), first, "порядок ключей источника не влияет")
        again = json.loads(json.dumps(d, sort_keys=True))
        self.assertEqual(canon_bytes(PX.normalize_doc(again)), first, "и круг через JSON")
        self.assertEqual(slugs_of(PX.normalize_doc(d)), ["landing", "landing-2"])

    def test_two_unnamed_drawings_under_one_ancestor_are_told_apart_in_document_order(self):
        env = conv(fan("g2", "Group 13", top=200), fan("g1", "Group 12", top=10), text("t1", "x", top=380))
        self.assertEqual([(a["id"], a["art"]["slug"]) for a in arts_of(env)], [("g1", "landing"), ("g2", "landing-2")],
                         "порядок — по позиции на экране, а не по массиву источника")
        self.assertEqual(env["stats"]["art"]["slugSuffixed"], 1)

    def test_art_in_an_unnamed_drawing_inside_unnamed_frames_climbs_all_the_way(self):
        env = conv(n("f", "FRAME", name="Frame 5", w=300, h=300, kids=[fan("g", "Group 9"), text("t", "x", top=250)]),
                   name="Pricing")
        self.assertEqual(slugs_of(env), ["pricing"])


# ── slug ─────────────────────────────────────────────────────────────────────
class Slugs(unittest.TestCase):
    def one(self, name, **kw):
        root = screen(grp("g", name), **kw)
        AR.collapse_vector_art(root)
        return slugs(root)

    def test_meaningful_name_becomes_the_slug(self):
        for name, slug in [("Hero Banner", "hero-banner"), ("HeroBanner", "hero-banner"), ("hero_banner", "hero-banner"),
                           ("Logo / Primary", "logo-primary"), ("Sun 2", "sun-2"), ("2024", "2024")]:
            self.assertEqual(self.one(name), [slug], name)

    def test_auto_name_climbs_to_the_nearest_meaningful_ancestor(self):
        mid = cn("m", "frame", "Frame 5", 300, kids=[grp("g", "Group 7")])
        header = cn("h", "frame", "Header", 300, kids=[mid, cn("t", "text", "Title", 50, text="x")])
        root = screen(header)
        AR.collapse_vector_art(root)
        self.assertEqual([(a["id"], a["slug"]) for a in AR.collect_vector_art(root)], [("m", "header")],
                         "склеена верхушка (Frame 5), имя — у ближайшего осмысленного предка")

    def test_non_ascii_name_is_not_transliterated_and_falls_back(self):
        root = screen(grp("g1", "Логотип"), grp("g2", "Иллюстрация"))
        rep = AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["art", "art-2"])
        self.assertEqual((rep["slugFallback"], rep["slugSuffixed"]), (2, 1))

    def test_mixed_name_keeps_only_its_ascii_part(self):
        self.assertEqual(self.one("Logo Лого"), ["logo"])
        self.assertEqual(self.one("Café"), ["caf"], "é отброшен, а не превращён в e")

    def test_non_ascii_ancestor_stops_the_climb(self):
        frame = cn("f", "frame", "Иллюстрации", 300, kids=[grp("g", "Group 3"), cn("t", "text", "T", 50, text="x")])
        root = screen(frame)
        AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["art"], "осмысленное, но не ASCII имя — фолбэк, а не имя дальнего предка")

    def test_no_meaningful_name_anywhere_is_the_fallback(self):
        root = screen(grp("g", "Group 3"), name="Frame 1")
        AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["art"])

    def test_instance_ancestor_uses_the_component_name_when_its_own_is_a_variant_string(self):
        inst_ = {"id": "i", "kind": "instance", "name": "Steps=OFF", "component": {"figmaName": "Modal Header"},
                 "children": [grp("g", "Group 3")]}
        root = screen(inst_, name="Frame 9")
        AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["modal-header"])

    def test_instance_with_its_own_meaningful_name_keeps_it(self):
        inst_ = {"id": "i", "kind": "instance", "name": "Hero Card", "component": {"figmaName": "Card"},
                 "children": [grp("g", "Group 3")]}
        root = screen(inst_)
        AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["hero-card"])

    def test_unknown_component_name_is_not_a_name(self):
        inst_ = {"id": "i", "kind": "instance", "name": "Frame 4", "component": {"figmaName": "unknown"},
                 "children": [grp("g", "Group 3")]}
        root = screen(inst_)
        AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["landing"])

    def test_windows_device_names_get_a_suffix(self):
        for name, slug in [("con", "con-art"), ("NUL", "nul-art"), ("COM1", "com1-art"), ("lpt9", "lpt9-art")]:
            self.assertEqual(self.one(name), [slug], name)

    def test_length_is_capped_without_a_dangling_dash(self):
        for name in ("a" * 100, "word " * 30):
            slug = self.one(name)[0]
            self.assertLessEqual(len(slug), AR.SLUG_MAX)
            self.assertFalse(slug.endswith("-") or slug.startswith("-"), slug)

    def test_slugify_pins(self):
        for name, slug in [("A--B", "a-b"), ("  ", ""), ("ÄÖ", ""), ("Logo_v2", "logo-v2"), ("SVGLogo", "svglogo"),
                           ("heroBanner2", "hero-banner2")]:
            self.assertEqual(AR.slugify(name), slug, name)

    def test_collisions_are_disambiguated_by_suffix_and_never_hit_a_natural_slug(self):
        root = screen(grp("g1", "Logo"), grp("g2", "Logo"), grp("g3", "Logo 2"))
        AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["logo", "logo-2", "logo-2-2"])

    def test_existing_art_slug_is_reserved(self):
        existing = cn("e", "vector", "Logo", 100, art={"slug": "logo", "nodes": 5})
        root = screen(existing, grp("g", "Logo"))
        AR.collapse_vector_art(root)
        self.assertEqual(slugs(root), ["logo", "logo-2"])


# ── что НЕ рисунок ───────────────────────────────────────────────────────────
class NotArt(unittest.TestCase):
    def test_group_with_text_is_not_collapsed(self):
        env = conv(group("g", "Hero", [vec("v1"), vec("v2", left=40), vec("v3", left=80), text("t", "Заголовок", top=60)]))
        self.assertEqual(arts_of(env), [])
        self.assertEqual([k["kind"] for k in by_id(env)["g"]["children"]].count("vector"), 3, "фигуры на месте")
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["Заголовок"])
        self.assertEqual(env["stats"]["art"], {"iconCandidates": 3}, "малые одиночные фигуры — иконки-кандидаты")

    def test_group_with_a_component_instance_is_not_collapsed(self):
        lib = comp("L", "Button", [text("Lt", "OK")])
        env = conv(group("g", "Hero", [vec("v1"), vec("v2", left=40), inst("i", "L", left=80)]), defs=[lib])
        self.assertEqual(arts_of(env), [])
        self.assertEqual(by_id(env)["i"]["kind"], "instance")

    def test_group_with_a_bitmap_is_not_collapsed(self):
        bitmap = n("img", "RECTANGLE", w=50, h=50, left=80, fillPaints=[{"type": "IMAGE"}])
        env = conv(group("g", "Photo", [vec("v1"), vec("v2", left=40), bitmap]))
        self.assertEqual(arts_of(env), [])
        self.assertEqual(by_id(env)["img"]["kind"], "image")

    def test_icon_inside_blocks_the_group(self):
        icon = cn("i", "icon", "Close", 24, component={"figmaName": "Close"})
        root = screen(cn("g", "frame", "Hero", 200, kids=shapes("v", 3) + [icon]))
        AR.collapse_vector_art(root)
        self.assertEqual(AR.collect_vector_art(root), [])

    def test_container_of_simple_rectangles_is_a_background_not_a_drawing(self):
        rects = [n(f"r{i}", "RECTANGLE", w=40, h=40, left=i * 10) for i in range(5)]
        rects += [n("e1", "ELLIPSE", w=20, h=20), n("l1", "LINE", w=50, h=1)]
        env = conv(group("g", "Card background", rects))
        self.assertEqual(arts_of(env), [])
        self.assertEqual(len(by_id(env)["g"]["children"]), 7)
        self.assertNotIn("art", env["stats"])

    def test_one_vector_among_rectangles_makes_it_a_drawing(self):
        env = conv(group("g", "Badge", [n("r1", "RECTANGLE", w=40, h=40), n("r2", "ELLIPSE", w=20, h=20, left=5),
                                        vec("v1", left=10)]))
        self.assertEqual([(a["id"], a["art"]) for a in arts_of(env)], [("g", {"slug": "badge", "nodes": 4})])

    def test_auto_layout_container_is_not_a_drawing(self):
        col = n("c", "FRAME", name="Stack", w=200, h=200, al={"stackMode": "VERTICAL", "autoLayoutItemSpacing": 4},
                kids=[vec(f"v{i}", top=i * 34) for i in range(4)])
        env = conv(col)
        self.assertEqual(arts_of(env), [])
        c = by_id(env)["c"]
        self.assertEqual(c["layout"]["direction"], "column")
        self.assertEqual(len(c["children"]), 4)

    def test_layout_inside_blocks_the_group_but_the_clean_subgroup_collapses(self):
        stack = n("s", "FRAME", name="Stack", w=100, h=100, al={"stackMode": "VERTICAL"},
                  kids=[vec("a1"), vec("a2", top=40)])
        env = conv(group("g", "Outer", [stack, group("g2", "Inner", [vec("b1"), vec("b2", left=40)], w=80, h=40, left=120)]))
        self.assertEqual([(a["id"], a["art"]["slug"]) for a in arts_of(env)], [("g2", "inner")])
        self.assertEqual(by_id(env)["s"]["layout"]["direction"], "column")

    def test_drawing_in_an_auto_layout_container_collapses_and_keeps_its_size(self):
        row = n("c", "FRAME", name="Row", w=300, h=100, al={"stackMode": "HORIZONTAL"}, kids=[
            text("t", "Подпись"),
            group("g", "Sun", [vec("v1"), vec("v2", left=40), vec("v3", left=80)], w=120, h=60, left=110)])
        env = conv(row)
        a = by_id(env)["g"]
        self.assertEqual((a["kind"], a["art"]), ("vector", {"slug": "sun", "nodes": 4}))
        self.assertEqual(a["size"], {"w": 120, "h": 60})
        self.assertNotIn("box", a, "в потоке позицию выражает раскладка")
        self.assertEqual(by_id(env)["c"]["layout"]["direction"], "row")
        self.assertEqual(AR.collect_vector_art(env["root"])[0]["w"], 120)

    def test_frame_with_a_fill_is_a_ui_box_not_a_drawing(self):
        env = conv(n("f", "FRAME", name="Card", w=200, h=100, fillPaints=[WHITE],
                     kids=[vec("v1"), vec("v2", left=40), vec("v3", left=80)]))
        self.assertEqual(arts_of(env), [])
        self.assertEqual(by_id(env)["f"]["style"]["background"], "rgba(255, 255, 255, 1)")

    def test_frame_with_a_shadow_is_a_ui_box_too(self):
        env = conv(n("f", "FRAME", name="Card", w=200, h=100, effects=[SHADOW],
                     kids=[vec("v1"), vec("v2", left=40)]))
        self.assertEqual(arts_of(env), [])

    def test_icon_button_frame_with_one_glyph_is_untouched(self):
        env = conv(n("f", "FRAME", name="Icon button", w=40, h=40, fillPaints=[WHITE], kids=[vec("v1", w=20, h=20)]))
        self.assertEqual(arts_of(env), [])
        self.assertEqual(by_id(env)["v1"]["kind"], "vector")

    def test_drawing_inside_a_filled_frame_collapses_by_itself(self):
        env = conv(n("f", "FRAME", name="Card", w=300, h=200, fillPaints=[WHITE],
                     kids=[group("g", "Sun", [vec("v1"), vec("v2", left=40)], w=80, h=40), text("t", "x", top=100)]))
        self.assertEqual(slugs_of(env), ["sun"])
        self.assertEqual(by_id(env)["f"]["style"]["background"], "rgba(255, 255, 255, 1)")

    def test_frame_with_only_opacity_is_still_a_group(self):
        env = conv(n("f", "FRAME", name="Faded", w=200, h=100, opacity=0.4, kids=[vec("v1"), vec("v2", left=40)]))
        a = arts_of(env)[0]
        self.assertEqual((a["id"], a["style"]), ("f", {"opacity": 0.4}))

    def test_root_is_never_collapsed(self):
        env = conv(vec("v1"), vec("v2", left=40), vec("v3", left=80))
        self.assertEqual(env["root"]["kind"], "frame")
        self.assertEqual(arts_of(env), [])
        env = conv(group("g1", "Sun", [vec("a1"), vec("a2", left=40)], w=80, h=40),
                   group("g2", "Moon", [vec("b1"), vec("b2", left=40)], w=80, h=40, top=100))
        self.assertEqual((env["root"]["kind"], slugs_of(env)), ("frame", ["sun", "moon"]))

    def test_loose_shapes_beside_text_are_judged_one_by_one(self):
        small = conv(vec("v1"), vec("v2", left=40), text("t", "x", top=100))
        self.assertEqual(arts_of(small), [], "малые одиночные — иконки-кандидаты, а не рисунок")
        big = conv(group("g", "Hero", [vec("v1", w=100, h=100), vec("v2", w=100, h=100, left=110), text("t", "x", top=120)]))
        self.assertEqual([(a["id"], a["art"]) for a in arts_of(big)],
                         [("v1", {"slug": "hero", "nodes": 1}), ("v2", {"slug": "hero-2", "nodes": 1})],
                         "общей группы нет — синтетических не создаём; каждый крупный вектор — сам")

    def test_nothing_is_lost_but_the_collapsed_descendants(self):
        d = doc(fan(), group("g2", "Mixed", [vec("m1", w=100, h=100), text("mt", "Привет", top=120)], top=200),
                n("f", "FRAME", name="Card", w=100, h=100, fillPaints=[WHITE], top=300, kids=[vec("c1")]),
                text("t1", "Заголовок", top=380))
        with mock.patch.object(PX, "collapse_vector_art", lambda root: {}):
            before = PX.normalize_doc(d)
        after = PX.normalize_doc(d)
        self.assertEqual([t["text"] for t in C.collect_texts(after["root"])],
                         [t["text"] for t in C.collect_texts(before["root"])], "текст не теряется")
        gone = set(by_id(before)) - set(by_id(after))
        self.assertEqual(gone, {f"g1v{i}" for i in range(1, 9)})
        self.assertEqual(sum(a["art"]["nodes"] - 1 for a in arts_of(after)), len(gone))


# ── одиночный вектор и ICON_MAX ──────────────────────────────────────────────
class SingleVector(unittest.TestCase):
    def collapse(self, *kids):
        root = screen(*kids)
        rep = AR.collapse_vector_art(root)
        return root, rep

    def test_small_single_vector_is_an_icon_candidate(self):
        root, rep = self.collapse(cn("v", "vector", "Outline", 24, 24))
        self.assertEqual(AR.collect_vector_art(root), [])
        self.assertEqual((root["children"][0]["kind"], rep), ("vector", {"iconCandidates": 1}))

    def test_icon_max_boundary(self):
        cases = [((64, 64), False), ((64.004, 64), False), ((64, 64.01), True), ((65, 20), True), ((20, 65), True),
                 ((120, 24), True), ((1, 1), False)]
        for (w, h), is_art in cases:
            root, _ = self.collapse(cn("v", "vector", "Wordmark", w, h))
            self.assertEqual(bool(AR.collect_vector_art(root)), is_art, (w, h))

    def test_big_single_vector_is_a_wordmark_art(self):
        root, rep = self.collapse(cn("v", "vector", "Wordmark", 120, 24, color="rgba(0, 0, 0, 1)", tokens={"fill": "X"}))
        v = root["children"][0]
        self.assertEqual((v["art"], sorted(v)), ({"slug": "wordmark", "nodes": 1}, ["art", "box", "id", "kind", "name"]))
        self.assertEqual(rep, {"arts": 1})

    def test_chain_of_wrappers_around_one_shape_is_one_shape(self):
        chain = cn("g1", "frame", "Icon", 24, kids=[cn("g2", "frame", "Group 2", 24, kids=[cn("v", "vector", "Outline", 20)])])
        root, rep = self.collapse(chain)
        self.assertEqual((AR.collect_vector_art(root), rep), ([], {"iconCandidates": 1}))
        self.assertEqual(root["children"][0]["children"][0]["children"][0]["id"], "v", "дерево не тронуто")

    def test_the_top_wrappers_size_decides(self):
        root, _ = self.collapse(cn("g", "frame", "Banner", 200, kids=[cn("v", "vector", "Outline", 20)]))
        self.assertEqual([(a["id"], a["nodes"]) for a in AR.collect_vector_art(root)], [("g", 2)])

    def test_boolean_operation_is_one_shape(self):
        small = cn("b", "vector", "Stroke", 16, kids=[cn("o1", "vector", "Vector 13", 6), cn("o2", "rect", "Circle", 16)])
        root, rep = self.collapse(small)
        self.assertEqual((AR.collect_vector_art(root), rep), ([], {"iconCandidates": 1}))
        self.assertEqual(len(root["children"][0]["children"]), 2, "операнды малой операции не трогаем")
        big = cn("b", "vector", "Emblem", 120, kids=[cn("o1", "vector", "Vector 13", 60), cn("o2", "rect", "Circle", 120)])
        root, _ = self.collapse(big)
        self.assertEqual([(a["slug"], a["nodes"]) for a in AR.collect_vector_art(root)], [("emblem", 3)])

    def test_unknown_size_is_not_proof_of_a_drawing(self):
        v = {"id": "v", "kind": "vector", "name": "Line", "size": {"w": "fill", "h": 20}}
        root, rep = self.collapse(v)
        self.assertEqual((AR.collect_vector_art(root), rep), ([], {"sizeUnknown": 1}))
        v2 = {"id": "v", "kind": "vector", "name": "Line", "size": {"w": "fill", "h": 80}}
        root, _ = self.collapse(v2)
        self.assertEqual(len(AR.collect_vector_art(root)), 1, "одна известная сторона крупнее порога — уже рисунок")

    def test_group_of_several_small_shapes_collapses_at_any_size(self):
        root, rep = self.collapse(cn("g", "frame", "Chevron", 24, kids=shapes("v", 3, 8)))
        self.assertEqual([(a["slug"], a["nodes"]) for a in AR.collect_vector_art(root)], [("chevron", 4)])
        self.assertEqual(rep, {"arts": 1, "collapsedNodes": 3})

    def test_icon_max_is_defined_once(self):
        self.assertIs(PX.ICON_MAX, AR.ICON_MAX)
        self.assertEqual(AR.ICON_MAX, 64)


# ── реальные данные образца: анатомия local ──────────────────────────────────
class OnFixtureAnatomy(unittest.TestCase):
    ICON = "Stroke / User_Interface / Approve"

    def doc(self, scale=1):
        d = json.loads(FIXTURE.read_text(encoding="utf-8"))
        body = d["dsl"]
        top = next(t for t in body["pixComponentTreeDslNodes"] if t["name"] == self.ICON)
        top.pop("publishFile")                                     # неопубликованное определение — local
        if scale != 1:
            glyph = top["childNode"][0]
            glyph["width"], glyph["height"] = glyph["width"] * scale, glyph["height"] * scale
        body["pixTreeDslNodes"][0]["childNode"].append({
            "guid": "9:1", "type": "INSTANCE", "name": "Approve", "mainComponent": top["guid"],
            "width": 24, "height": 24, "left": 20, "top": 300})
        return d

    def test_fixture_canon_has_no_art(self):
        env = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")
        self.assertEqual(arts_of(env), [])
        self.assertNotIn("art", env["stats"])
        self.assertEqual(sorted(k["kind"] for k in C.iter_nodes(env["root"])), ["frame", "instance", "instance", "instance"])

    def test_real_vector_of_the_sample_stays_an_icon_candidate(self):
        env = PX.normalize_doc(self.doc())
        i = by_id(env)["9:1"]
        self.assertEqual((i["kind"], i["component"]["origin"]), ("instance", "local"))
        glyph = i["children"][0]
        self.assertEqual((glyph["id"], glyph["kind"], glyph["name"]), ("9:1;2:153988", "vector", "Outline"))
        self.assertNotIn("art", glyph)
        self.assertEqual(env["stats"]["art"], {"iconCandidates": 1})

    def test_same_data_at_drawing_size_collapses_with_the_real_layer_name(self):
        env = PX.normalize_doc(self.doc(scale=6))
        glyph = by_id(env)["9:1;2:153988"]
        self.assertEqual((glyph["kind"], glyph["art"]), ("vector", {"slug": "outline", "nodes": 1}))
        inst_ = by_id(env)["9:1"]
        self.assertEqual((inst_["kind"], inst_["component"]["childrenPath"]), ("instance", ["2:153988"]),
                         "инстанс остаётся инстансом, путь анатомии ведёт на тот же id")
        self.assertEqual(C.validate(env), [])


# ── анатомия local-композиций (виртуальные узлы) ─────────────────────────────
class LocalAnatomy(unittest.TestCase):
    def defs(self):
        lib = comp("L", "Button", [text("Lt", "Купить", name="Label")])
        sun = comp("S", "Illustration", [
            group("Sg", "Sun", [vec("Sv1"), vec("Sv2", left=40), vec("Sv3", left=80)], w=100, h=80),
            text("Sc", "Подпись", name="Caption", top=90)], published=False, w=200, h=120)
        return [lib, sun]

    def test_virtual_group_collapses_inside_a_local_instance(self):
        env = conv(inst("a", "S", w=200, h=120), defs=self.defs())
        a = by_id(env)["a"]
        self.assertEqual((a["kind"], a["component"]["origin"]), ("instance", "local"))
        art = by_id(env)["a;Sg"]
        self.assertEqual((art["kind"], art["art"]), ("vector", {"slug": "sun", "nodes": 4}))
        self.assertEqual([c["id"] for c in a["children"]], ["a;Sg", "a;Sc"])
        self.assertEqual(sorted(a["component"]["childrenPath"]), ["Sc", "Sg"])
        self.assertTrue(all(f"a;{p}" in by_id(env) for p in a["component"]["childrenPath"]), "id анатомии целы")
        self.assertEqual([t["text"] for t in C.collect_texts(env["root"])], ["Подпись"])
        self.assertEqual(C.validate(env), [])

    def test_two_cells_of_one_composition_are_two_assets(self):
        env = conv(inst("a", "S", w=200, h=120), inst("b", "S", w=200, h=120, top=150), defs=self.defs())
        self.assertEqual([(x["id"], x["art"]["slug"]) for x in arts_of(env)], [("a;Sg", "sun"), ("b;Sg", "sun-2")])

    def test_composition_made_of_one_drawing_keeps_the_instance(self):
        only = comp("O", "Mascot", [group("Og", "Group 4", [vec("Ov1"), vec("Ov2", left=40)], w=100, h=60)],
                    published=False, w=100, h=60)
        env = conv(inst("a", "O", name="Steps=OFF", w=100, h=60), defs=[self.defs()[0], only])
        self.assertEqual((by_id(env)["a"]["kind"], slugs_of(env)), ("instance", ["mascot"]),
                         "автоимя группы и имя-вариант инстанса → имя компонента")

    def test_library_black_box_has_no_anatomy_and_no_art(self):
        lib = comp("V", "Logo", [vec("Vv1", w=100, h=100), vec("Vv2", w=100, h=100)], w=120, h=120)
        env = conv(inst("a", "V", w=120, h=120), defs=[lib])
        self.assertEqual(arts_of(env), [])
        self.assertNotIn("children", by_id(env)["a"])


# ── контракт ─────────────────────────────────────────────────────────────────
class Contract(unittest.TestCase):
    def mixed(self):
        return screen(grp("g1", "Hero"), cn("t", "text", "T", 50, text="x"), cn("v", "vector", "Outline", 24),
                      cn("w", "vector", "Wordmark", 120, 24), grp("g2", "Hero"),
                      cn("f", "frame", "Card", 100, style={"background": "rgba(0, 0, 0, 1)"}, kids=shapes("fv", 2)))

    def test_collapsing_twice_is_the_identity(self):
        root = self.mixed()
        first = AR.collapse_vector_art(root)
        snapshot = copy.deepcopy(root)
        self.assertEqual(AR.collapse_vector_art(root), {"iconCandidates": first["iconCandidates"]})
        self.assertEqual(root, snapshot)

    def test_collect_reads_assets_back_in_document_order(self):
        root = self.mixed()
        AR.collapse_vector_art(root)
        got = AR.collect_vector_art(root)
        self.assertEqual([(a["slug"], a["id"], a["nodes"]) for a in got],
                         [("hero", "g1", 4), ("wordmark", "w", 1), ("hero-2", "g2", 4)])
        self.assertEqual((got[1]["name"], got[1]["w"], got[1]["h"]), ("Wordmark", 120, 24))

    def test_occluded_art_is_marked_and_so_is_art_inside_an_occluded_subtree(self):
        cover = n("c", "RECTANGLE", name="Backdrop", w=400, h=400, fillPaints=[WHITE])
        env = conv(cover, group("g", "Logo", [vec("v1"), vec("v2", left=40)], w=100, h=60, left=10, top=10),
                   text("t", "x", top=380))
        a = by_id(env)["g"]
        self.assertTrue(a["occluded"])
        self.assertEqual([(r["slug"], r.get("occluded")) for r in AR.collect_vector_art(env["root"])], [("logo", True)])
        root = screen(cn("p", "frame", "Page", 300, occluded=True, kids=[grp("g", "Logo"), cn("t", "text", "T", 5, text="x")]))
        AR.collapse_vector_art(root)
        self.assertEqual([r.get("occluded") for r in AR.collect_vector_art(root)], [True])

    def test_art_nodes_pass_strict_validation_and_have_no_children(self):
        root = self.mixed()
        AR.collapse_vector_art(root)
        self.assertEqual(C.validate_node(root), [])
        bad = copy.deepcopy(root)
        next(x for x in C.iter_nodes(bad) if "art" in x)["children"] = [cn("z", "vector", "z", 1)]
        self.assertTrue(any("арт" in p for p in C.validate_node(bad)))

    def test_deep_chain_does_not_recurse(self):
        inner = cn("leaf", "frame", "Leaf", 10, kids=shapes("v", 2))
        for i in range(3000):
            inner = cn(f"f{i}", "frame", "Frame 1" if i else "Deep", 10, kids=[inner])
        root = screen(inner)
        rep = AR.collapse_vector_art(root)
        self.assertEqual(rep["arts"], 1)
        self.assertEqual(AR.collect_vector_art(root)[0]["nodes"], 3000 + 1 + 2)

    def test_wide_group_collapses_in_one_pass(self):
        root = screen(cn("g", "frame", "Crowd", 400, kids=shapes("v", 20000, 4)))
        self.assertEqual(AR.collapse_vector_art(root), {"arts": 1, "collapsedNodes": 20000})

    def test_a_lone_small_vector_only_counts_as_an_icon_candidate_without_warnings(self):
        env = conv(text("t", "x"), vec("v", w=20, h=20))
        self.assertEqual(env["stats"]["art"], {"iconCandidates": 1})
        self.assertEqual(env["stats"]["warnings"], [])

    def test_fallback_slug_is_announced_once(self):
        env = conv(group("g1", "Логотип", [vec("a"), vec("b", left=40)]), group("g2", "Иллюстрация", [vec("c"), vec("d", left=40)], top=200))
        self.assertEqual(slugs_of(env), ["art", "art-2"])
        warns = [w for w in env["stats"]["warnings"] if "«art»" in w]
        self.assertEqual(len(warns), 1)
        self.assertIn("2", warns[0])


if __name__ == "__main__":
    unittest.main()
