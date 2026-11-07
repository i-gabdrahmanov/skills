#!/usr/bin/env python3
"""test_paints_typography.py — Pixso → канон: краски, типографика, токены (задача 1.5; QG U2, F1, INV B13/B18).

Образец «Modal» сверен с его картинкой: заголовок 20/600 тёмным (`rgba(31, 20, 51, 1)`), описание 13/400
серым (альфа 0.55), подпись главной кнопки белым; корень — белая карточка с радиусом 16, обводкой 1 px
(альфа 0.12) и двухслойной тенью. Синтетика пинит правила `ingest/style.py`: единый масштаб каналов,
эффективная альфа, верхняя видимая краска, градиент со всеми stop'ами, радиус-массив только при
разных углах, обводка по сторонам, тень (inset, блюр слоя — не тень), вес шрифта числом, проценты,
стиль как источник недостающего (значение узла сильнее), нерезолвимый стиль, image-fill, оверрайды слотов.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402
from daiver.ingest import style as ST  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"


def col(r, g, b, a=1):
    return {"r": r, "g": g, "b": b, "a": a}


def solid(r=0, g=0, b=0, a=1, **kw):
    return dict({"type": "SOLID", "color": col(r, g, b, a), "blendMode": "NORMAL"}, **kw)


def n(guid, typ="FRAME", w=10, h=10, left=0, top=0, kids=None, **kw):
    node = {"guid": guid, "type": typ, "name": guid, "left": left, "top": top, "width": w, "height": h}
    if kids:
        node["childNode"] = list(kids)
    node.update(kw)
    return node


def conv(*kids, styles=None, defs=(), w=400, h=400) -> dict:
    body = {"pixTreeDslNodes": [n("root", w=w, h=h, kids=kids)], "pixComponentTreeDslNodes": list(defs)}
    if styles is not None:
        body["localStyleMap"] = styles
    return PX.normalize_doc({"dsl": body})


def by_id(env) -> dict:
    return C.index_nodes(env["root"])


def one(node, **kw):
    """Канон единственного ребёнка корня."""
    return by_id(conv(node, **kw))[node["guid"]]


# ── образец ───────────────────────────────────────────────────────────────────
class OnFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")
        cls.ids = by_id(cls.env)

    def test_root_card_style_matches_the_picture(self):
        s = self.env["root"]["style"]
        self.assertEqual(s["background"], "rgba(255, 255, 255, 1)")
        self.assertEqual(s["radius"], 16)
        self.assertEqual(s["border"], {"width": 1, "color": "rgba(31, 20, 51, 0.12)", "align": "inside"})

    def test_root_shadow_is_one_css_string_with_both_layers(self):
        self.assertEqual(self.env["root"]["style"]["shadow"],
                         "0px 12px 24px 0px rgba(11, 11, 30, 0.161), 0px 1px 2px 0px rgba(11, 11, 30, 0.039)")

    def test_instances_carry_their_own_corner_radii_in_css_order(self):
        self.assertEqual(self.ids["2:153703"]["style"]["radius"], [16, 16, 0, 0], "верхние углы шапки")
        self.assertEqual(self.ids["22:63141"]["style"]["radius"], [16, 16, 0, 0])
        self.assertEqual(self.ids["2:153704"]["style"]["radius"], [0, 0, 16, 16], "нижние углы блока действий")

    def slot(self, inst, name_text):
        for s in self.ids[inst]["component"]["content"]:
            if s["text"].startswith(name_text):
                return s
        raise AssertionError(name_text)

    def test_header_title_typography_and_color(self):
        s = self.slot("22:63141", "Текст9")
        self.assertEqual(s["font"], {"family": "Fixture Sans", "size": 20, "weight": 600, "lineHeight": 24,
                                     "letterSpacing": -0.5, "token": "Headline/H2"})
        self.assertEqual(s["color"], "rgba(31, 20, 51, 1)")

    def test_description_is_regular_and_muted(self):
        s = self.slot("22:63141", "Текст10")
        self.assertEqual((s["font"]["size"], s["font"]["weight"], s["font"]["token"]), (13, 400, "Text/B1"))
        self.assertEqual(s["color"], "rgba(31, 20, 51, 0.55)")

    def test_button_labels_white_on_primary_and_ink_on_secondary(self):
        self.assertEqual(self.slot("2:153704", "Текст11")["color"], "rgba(255, 255, 255, 1)")
        self.assertEqual(self.slot("2:153704", "Текст3")["color"], "rgba(31, 20, 51, 1)")
        self.assertEqual(self.slot("2:153704", "Текст11")["font"]["weight"], 500)

    def test_every_slot_has_a_font_and_a_color(self):
        slots = [s for k in self.ids.values() for s in (k.get("component") or {}).get("content", [])]
        self.assertEqual(len(slots), 5)
        for s in slots:
            self.assertTrue({"family", "size", "weight", "lineHeight"} <= set(s["font"]))
            self.assertIn("color", s)

    def test_style_stats_name_the_library_styles_the_export_lacks(self):
        st = self.env["stats"]["styles"]
        self.assertEqual(st["colorScale"], 255)
        self.assertEqual(st["unresolvedStyles"], {"effect": {"count": 1, "ids": ["5:41495"]},
                                                  "fill": {"count": 1, "ids": ["25:44062"]},
                                                  "stroke": {"count": 1, "ids": ["25:44295"]}})
        self.assertNotIn("tokens", self.env["root"], "у корня стили библиотечные — имён нет, токен не выдумывается")

    def test_validates_and_is_deterministic(self):
        self.assertEqual(C.validate(self.env), [])
        again = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")
        self.assertEqual(C.dumps(again), C.dumps(self.env))


# ── цвет ──────────────────────────────────────────────────────────────────────
class Colors(unittest.TestCase):
    def bg(self, *paints, **kw):
        return one(n("a", fillPaints=list(paints), **kw)).get("style", {}).get("background")

    def test_channels_0_255(self):
        self.assertEqual(self.bg(solid(31, 20, 51, 0.12)), "rgba(31, 20, 51, 0.12)")

    def test_unit_scale_is_detected_once_for_the_whole_file(self):
        env = conv(n("a", fillPaints=[solid(1, 0.5, 0)]), n("b", top=20, fillPaints=[solid(0.2, 0.2, 0.2)]))
        ids = by_id(env)
        self.assertEqual(ids["a"]["style"]["background"], "rgba(255, 128, 0, 1)")
        self.assertEqual(ids["b"]["style"]["background"], "rgba(51, 51, 51, 1)")
        self.assertEqual(env["stats"]["styles"]["colorScale"], 1)

    def test_one_big_channel_anywhere_keeps_the_file_in_255(self):
        env = conv(n("a", fillPaints=[solid(200, 0, 0)]), n("b", top=20, fillPaints=[solid(0.5, 0.5, 0.5)]))
        self.assertEqual(by_id(env)["b"]["style"]["background"], "rgba(1, 1, 1, 1)", "масштабы не смешиваются")

    def test_scale_sees_style_effect_and_stop_colors_too(self):
        styles = {"s": {"name": "X", "styleType": "FILL", "fillPaints": [solid(0.5, 0.5, 0.5)]}}
        self.assertEqual(ST.detect_color_scale({"localStyleMap": styles, "a": {"effects": [{"color": col(0, 0, 0)}]}}), 1)
        self.assertEqual(ST.detect_color_scale({"x": {"stops": [{"color": col(0, 0, 255)}]}}), 255)
        self.assertEqual(ST.detect_color_scale({}), 255)

    def test_effective_alpha_is_the_smaller_of_color_and_paint_opacity(self):
        self.assertEqual(self.bg(solid(0, 0, 0, 0.5, opacity=0.5)), "rgba(0, 0, 0, 0.5)", "дубль, не произведение")
        self.assertEqual(self.bg(solid(0, 0, 0, 1, opacity=0.4)), "rgba(0, 0, 0, 0.4)")

    def test_alpha_has_three_decimals(self):
        self.assertEqual(self.bg(solid(11, 11, 30, 0.160784)), "rgba(11, 11, 30, 0.161)")

    def test_invisible_and_transparent_paints_are_skipped(self):
        self.assertIsNone(self.bg(solid(1, 2, 3, visible=False)))
        self.assertIsNone(self.bg(solid(1, 2, 3, 0)))
        self.assertEqual(self.bg(solid(1, 2, 3, visible=False), solid(4, 5, 6)), "rgba(4, 5, 6, 1)")

    def test_first_visible_of_several_is_taken_and_counted(self):
        env = conv(n("a", fillPaints=[solid(1, 1, 1), solid(2, 2, 2)]))
        self.assertEqual(by_id(env)["a"]["style"]["background"], "rgba(1, 1, 1, 1)")
        self.assertEqual(env["stats"]["styles"]["counts"]["multiFill"], 1)
        self.assertTrue(any("несколько видимых заливок" in w for w in env["stats"]["warnings"]))

    def test_no_fill_means_no_background(self):
        self.assertNotIn("style", one(n("a")))
        self.assertNotIn("style", one(n("a", fillPaints=[])))


class Gradients(unittest.TestCase):
    def grad(self, **kw):
        p = dict({"type": "GRADIENT_LINEAR", "stops": [{"color": col(255, 0, 0), "position": 1},
                                                        {"color": col(0, 0, 255, 0.5), "position": 0}]}, **kw)
        return one(n("a", fillPaints=[p])).get("style", {})

    def test_all_stops_sorted_by_position_with_percent_positions(self):
        s = self.grad(startPt={"x": 0, "y": 0}, endPt={"x": 10, "y": 0})
        self.assertEqual(s["gradient"], "linear-gradient(90deg, rgba(0, 0, 255, 0.5) 0%, rgba(255, 0, 0, 1) 100%)")
        self.assertNotIn("background", s)

    def test_direction_from_the_handles(self):
        self.assertTrue(self.grad(startPt={"x": 0, "y": 0}, endPt={"x": 0, "y": 10})["gradient"].startswith("linear-gradient(180deg"))
        self.assertTrue(self.grad(startPt={"x": 0, "y": 10}, endPt={"x": 0, "y": 0})["gradient"].startswith("linear-gradient(0deg"))
        self.assertTrue(self.grad(startPt={"x": 10, "y": 0}, endPt={"x": 0, "y": 0})["gradient"].startswith("linear-gradient(270deg"))

    def test_no_handles_means_no_angle_and_is_counted(self):
        env = conv(n("a", fillPaints=[{"type": "GRADIENT_LINEAR", "stops": [{"color": col(0, 0, 0), "position": 0},
                                                                               {"color": col(9, 9, 9), "position": 1}]}]))
        self.assertEqual(by_id(env)["a"]["style"]["gradient"], "linear-gradient(rgba(0, 0, 0, 1) 0%, rgba(9, 9, 9, 1) 100%)")
        self.assertEqual(env["stats"]["styles"]["counts"]["gradientAngleUnknown"], 1)

    def test_radial_and_angular(self):
        stops = [{"color": col(0, 0, 0), "position": 0.25}, {"color": col(9, 9, 9), "position": 0.75}]
        self.assertTrue(one(n("a", fillPaints=[{"type": "GRADIENT_RADIAL", "stops": stops}]))["style"]["gradient"]
                        .startswith("radial-gradient(rgba(0, 0, 0, 1) 25%"))
        self.assertTrue(one(n("a", fillPaints=[{"type": "GRADIENT_ANGULAR", "stops": stops}]))["style"]["gradient"]
                        .startswith("conic-gradient("))

    def test_gradient_without_stops_is_not_invented(self):
        self.assertNotIn("style", one(n("a", fillPaints=[{"type": "GRADIENT_LINEAR", "stops": []}])))


# ── радиус, обводка, тень, прозрачность, картинка ─────────────────────────────
class Geometry(unittest.TestCase):
    def style(self, **kw):
        return one(n("a", **kw)).get("style", {})

    def test_equal_corners_are_one_number(self):
        self.assertEqual(self.style(cornerRadius=8)["radius"], 8)
        self.assertEqual(self.style(rectangleTopLeftCornerRadius=4, rectangleTopRightCornerRadius=4,
                                    rectangleBottomRightCornerRadius=4, rectangleBottomLeftCornerRadius=4)["radius"], 4)

    def test_different_corners_are_an_array_in_css_order(self):
        s = self.style(rectangleTopLeftCornerRadius=1, rectangleTopRightCornerRadius=2,
                       rectangleBottomRightCornerRadius=3, rectangleBottomLeftCornerRadius=4)
        self.assertEqual(s["radius"], [1, 2, 3, 4])

    def test_missing_corner_is_zero(self):
        self.assertEqual(self.style(rectangleTopLeftCornerRadius=16, rectangleTopRightCornerRadius=16)["radius"],
                         [16, 16, 0, 0])

    def test_zero_radius_is_silent(self):
        self.assertNotIn("style", one(n("a", cornerRadius=0)))

    def test_ellipse_is_half_of_the_shorter_side(self):
        self.assertEqual(one(n("e", "ELLIPSE", w=20, h=12))["style"]["radius"], 6)

    def test_border_from_stroke_weight(self):
        s = self.style(strokeWeight=2, strokePaints=[solid(1, 2, 3)], strokeAlign="OUTSIDE")
        self.assertEqual(s["border"], {"width": 2, "color": "rgba(1, 2, 3, 1)", "align": "outside"})

    def test_per_side_weights_win_and_collapse_when_equal(self):
        eq = self.style(strokeWeight=2, borderTopWeight=1, borderRightWeight=1, borderBottomWeight=1,
                        borderLeftWeight=1, strokePaints=[solid()])
        self.assertEqual(eq["border"]["width"], 1)
        diff = self.style(borderTopWeight=1, borderBottomWeight=3, strokePaints=[solid()])
        self.assertEqual(diff["border"]["width"], {"top": 1, "right": 0, "bottom": 3, "left": 0})

    def test_no_border_without_a_visible_paint_or_weight(self):
        self.assertNotIn("border", self.style(strokeWeight=1, strokePaints=[solid(visible=False)]))
        self.assertNotIn("border", self.style(strokeWeight=0, strokePaints=[solid()]))
        self.assertNotIn("border", self.style(strokeWeight=1))

    def test_opacity(self):
        self.assertEqual(self.style(opacity=0.5)["opacity"], 0.5)
        self.assertNotIn("style", one(n("a", opacity=1)))


class Shadows(unittest.TestCase):
    def shadow(self, *effects):
        return one(n("a", effects=list(effects))).get("style", {}).get("shadow")

    @staticmethod
    def eff(t="DROP_SHADOW", x=0, y=4, blur=8, spread=0, color=None, **kw):
        return dict({"type": t, "offset": {"x": x, "y": y}, "radius": blur, "spread": spread,
                     "color": color or col(0, 0, 0, 0.25), "visible": True}, **kw)

    def test_drop_shadow(self):
        self.assertEqual(self.shadow(self.eff()), "0px 4px 8px 0px rgba(0, 0, 0, 0.25)")

    def test_inner_shadow_is_inset_and_negative_offsets_stay(self):
        self.assertEqual(self.shadow(self.eff("INNER_SHADOW", x=-1, y=-2, blur=0, spread=3)),
                         "inset -1px -2px 0px 3px rgba(0, 0, 0, 0.25)")

    def test_layers_are_joined_in_array_order(self):
        self.assertEqual(self.shadow(self.eff(y=1), self.eff(y=2)),
                         "0px 1px 8px 0px rgba(0, 0, 0, 0.25), 0px 2px 8px 0px rgba(0, 0, 0, 0.25)")

    def test_invisible_is_skipped_and_blur_effects_are_not_shadows(self):
        env = conv(n("a", effects=[self.eff(visible=False), {"type": "LAYER_BLUR", "radius": 4, "visible": True}]))
        self.assertNotIn("style", by_id(env)["a"])
        self.assertEqual(env["stats"]["styles"]["counts"]["effectsDropped"], 1)


class ImageFill(unittest.TestCase):
    def test_image_fill_makes_the_node_an_image_not_a_background(self):
        env = conv(n("a", fillPaints=[{"type": "IMAGE", "blendMode": "NORMAL"}], cornerRadius=8, opacity=0.9,
                     kids=[n("c", w=2, h=2)]))
        a = by_id(env)["a"]
        self.assertEqual(a["kind"], "image")
        self.assertNotIn("background", a.get("style", {}))
        self.assertEqual(a["style"], {"opacity": 0.9}, "для картинки — только прозрачность")
        self.assertIn("c", by_id(env), "дети картинки не теряются")
        self.assertEqual(env["stats"]["imageFills"], 1)

    def test_instance_with_image_paint_stays_an_instance(self):
        env = conv(n("i", "INSTANCE", mainComponent="d", fillPaints=[{"type": "IMAGE"}]),
                   defs=[{"guid": "d", "type": "SYMBOL", "layerType": "COMPONENT", "name": "D", "width": 5, "height": 5}])
        self.assertEqual(by_id(env)["i"]["kind"], "instance")


# ── типографика ───────────────────────────────────────────────────────────────
class Typography(unittest.TestCase):
    def font(self, **kw):
        node = n("t", "PARAGRAPH", nodeText="Текст")
        node.update(dict({"fontFamily": "F", "fontSize": 10, "fontStyle": "Regular"}, **kw))
        return one(node).get("font", {})

    def test_named_weights_become_numbers(self):
        for name, num in (("Thin", 100), ("Light", 300), ("Regular", 400), ("Medium", 500), ("Semibold", 600),
                          ("SemiBold", 600), ("Bold", 700), ("ExtraBold", 800), ("Black", 900)):
            self.assertEqual(self.font(**{"fontStyle": name})["weight"], num, name)

    def test_italic_is_kept_by_name_and_numeric_style_is_a_weight(self):
        f = self.font(fontStyle="Bold Italic")
        self.assertEqual((f["weight"], f["italic"]), (700, True))
        f = self.font(fontStyle="Italic")
        self.assertEqual((f["weight"], f["italic"]), (400, True))
        self.assertEqual(self.font(fontStyle="600")["weight"], 600)

    def test_unknown_style_name_is_not_guessed(self):
        env = conv(n("t", "PARAGRAPH", nodeText="x", fontFamily="F", fontSize=10, fontStyle="Zany"),
                   n("u", "PARAGRAPH", top=20, nodeText="y", fontFamily="F", fontSize=10, fontStyle="Zany"))
        self.assertNotIn("weight", by_id(env)["t"]["font"])
        self.assertEqual(env["stats"]["styles"]["unknownFontStyles"], ["Zany"])
        self.assertEqual(sum("Zany" in w for w in env["stats"]["warnings"]), 1, "предупреждение одно на имя")

    def test_pixels(self):
        f = self.font(lineHeightNumber=16, lineHeightUnit="PIXELS", letterSpacingNumber=-0.2, letterSpacingUnit="PIXELS")
        self.assertEqual((f["lineHeight"], f["letterSpacing"], f["size"], f["family"]), (16, -0.2, 10, "F"))

    def test_percent_is_a_share_of_the_font_size(self):
        f = self.font(fontSize=20, lineHeightNumber=120, lineHeightUnit="PERCENT", letterSpacingNumber=-2,
                      letterSpacingUnit="PERCENT")
        self.assertEqual((f["lineHeight"], f["letterSpacing"]), (24, -0.4))

    def test_auto_line_height_and_zero_spacing(self):
        f = self.font(lineHeightUnit="AUTO", letterSpacingNumber=0, letterSpacingUnit="PIXELS")
        self.assertEqual(f["lineHeight"], "auto")
        self.assertNotIn("letterSpacing", f, "ноль — значение по умолчанию")

    def test_text_align(self):
        self.assertEqual(self.font(textAlignHorizontal="CENTER")["align"], "center")
        self.assertEqual(self.font(textAlignHorizontal="JUSTIFIED")["align"], "justify")
        self.assertNotIn("align", self.font())

    def test_text_color_comes_from_the_fill(self):
        t = one(n("t", "PARAGRAPH", nodeText="x", fillPaints=[solid(31, 20, 51)]))
        self.assertEqual(t["color"], "rgba(31, 20, 51, 1)")
        self.assertNotIn("style", t, "у текста цвет — color, а не background")

    def test_gradient_text_has_no_color_and_is_counted(self):
        env = conv(n("t", "PARAGRAPH", nodeText="x", fillPaints=[{"type": "GRADIENT_LINEAR",
                                                                  "stops": [{"color": col(0, 0, 0), "position": 0}]}]))
        self.assertNotIn("color", by_id(env)["t"])
        self.assertEqual(env["stats"]["styles"]["counts"]["textGradientDropped"], 1)

    def test_vector_fill_is_a_color_not_a_background(self):
        v = one(n("v", "VECTOR", fillPaints=[solid(9, 9, 9)]))
        self.assertEqual(v["color"], "rgba(9, 9, 9, 1)")
        self.assertNotIn("style", v)


# ── токены: стиль как источник и как имя ──────────────────────────────────────
STYLES = {
    "f1": {"name": "Static/Ink", "styleType": "FILL", "fillPaints": [solid(31, 20, 51)]},
    "t1": {"name": "Text/B1", "styleType": "TEXT", "text": {
        "fontFamily": "Fam", "fontSize": 13, "fontStyle": "Medium", "lineHeightNumber": 16, "lineHeightUnit": "PIXELS",
        "letterSpacingNumber": -0.2, "letterSpacingUnit": "PIXELS"}},
    "s1": {"name": "Static/Line", "styleType": "FILL", "fillPaints": [solid(0, 0, 0, 0.12)]},
}


class Tokens(unittest.TestCase):
    def test_bound_fill_gives_the_value_and_the_name(self):
        a = one(n("a", inheritFillStyleID="f1"), styles=STYLES)
        self.assertEqual(a["style"]["background"], "rgba(31, 20, 51, 1)")
        self.assertEqual(a["tokens"], {"fill": "Static/Ink"})

    def test_the_node_value_beats_the_style_but_the_token_stays(self):
        a = one(n("a", inheritFillStyleID="f1", fillPaints=[solid(9, 9, 9)]), styles=STYLES)
        self.assertEqual(a["style"]["background"], "rgba(9, 9, 9, 1)")
        self.assertEqual(a["tokens"]["fill"], "Static/Ink")

    def test_explicit_empty_fill_of_the_node_is_no_fill_even_with_a_style(self):
        a = one(n("a", inheritFillStyleID="f1", fillPaints=[]), styles=STYLES)
        self.assertNotIn("style", a)
        self.assertNotIn("tokens", a, "стиль ничего не красит — токена нет")

    def test_text_style_fills_in_what_the_node_lacks(self):
        t = one(n("t", "PARAGRAPH", nodeText="x", inheritTextStyleID="t1", fontSize=14), styles=STYLES)
        self.assertEqual(t["font"], {"family": "Fam", "size": 14, "weight": 500, "lineHeight": 16,
                                     "letterSpacing": -0.2, "token": "Text/B1"})
        self.assertEqual(t["tokens"], {"text": "Text/B1"})

    def test_bound_stroke_style(self):
        a = one(n("a", strokeWeight=1, inheritStrokeStyleID="s1"), styles=STYLES)
        self.assertEqual(a["style"]["border"]["color"], "rgba(0, 0, 0, 0.12)")
        self.assertEqual(a["tokens"], {"stroke": "Static/Line"})

    def test_unresolved_style_gives_no_name_and_is_counted(self):
        env = conv(n("a", inheritFillStyleID="gone", fillPaints=[solid()]), n("b", top=20, inheritFillStyleID="gone",
                                                                         fillPaints=[solid()]), styles=STYLES)
        self.assertNotIn("tokens", by_id(env)["a"])
        self.assertEqual(env["stats"]["styles"]["unresolvedStyles"], {"fill": {"count": 1, "ids": ["gone"]}},
                         "считаются различные идентификаторы")

    def test_effect_token(self):
        styles = dict(STYLES, e1={"name": "Elevation/1", "styleType": "EFFECT"})
        eff = [{"type": "DROP_SHADOW", "offset": {"x": 0, "y": 1}, "radius": 2, "spread": 0, "color": col(0, 0, 0, 0.1)}]
        a = one(n("a", effects=eff, inheritEffectStyleID="e1"), styles=styles)
        self.assertEqual(a["tokens"], {"effect": "Elevation/1"})

    def test_icon_keeps_only_the_component(self):
        lib = {"guid": "I", "type": "SYMBOL", "layerType": "COMPONENT", "name": "Close", "width": 24, "height": 24,
               "publishFile": "p", "childNode": [n("v", "VECTOR", w=10, h=10)]}
        env = conv(n("i", "INSTANCE", w=24, h=24, mainComponent="I", inheritFillStyleID="f1", cornerRadius=4), defs=[lib],
                   styles=STYLES)
        i = by_id(env)["i"]
        self.assertEqual(i["kind"], "icon")
        for f in ("style", "tokens", "font", "color"):
            self.assertNotIn(f, i)


# ── слоты content: оверрайды цвета и стиля ────────────────────────────────────
class SlotLook(unittest.TestCase):
    def lib(self, text_kw=None):
        text = n("T", "PARAGRAPH", nodeText="Мастер", fontFamily="F", fontSize=12, fontStyle="Regular",
                 fillPaints=[solid(10, 10, 10)], inheritFillStyleID="f1")
        text.update(text_kw or {})
        return {"guid": "L", "type": "SYMBOL", "layerType": "COMPONENT", "name": "Lib", "publishFile": "p", "width": 50,
                "height": 20, "childNode": [text]}

    def slot(self, props, **kw):
        inst = n("a", "INSTANCE", mainComponent="L", props=props, w=50, h=20)
        return by_id(conv(inst, defs=[self.lib(**kw)], styles=STYLES))["a"]["component"]["content"][0]

    def test_master_look_without_overrides(self):
        s = self.slot([])
        self.assertEqual(s["color"], "rgba(10, 10, 10, 1)")
        self.assertEqual(s["font"], {"family": "F", "size": 12, "weight": 400})

    def test_override_fill_replaces_the_color(self):
        s = self.slot([{"pathString": "T", "fillPaints": [solid(255, 255, 255)], "inheritFillStyleID": "f1"}])
        self.assertEqual(s["color"], "rgba(255, 255, 255, 1)")

    def test_empty_fill_list_in_an_override_is_not_an_override(self):
        s = self.slot([{"pathString": "T", "fillPaints": []}])
        self.assertEqual(s["color"], "rgba(10, 10, 10, 1)", "так экспорт пишет шапку образца, а на картинке цвет есть")

    def test_override_style_without_a_paint_takes_the_color_from_the_style(self):
        s = self.slot([{"pathString": "T", "inheritFillStyleID": "f1"}])
        self.assertEqual(s["color"], "rgba(31, 20, 51, 1)", "устаревшая краска мастера не живёт рядом с новым стилем")

    def test_text_style_gives_the_token_of_the_slot_font(self):
        s = self.slot([], text_kw={"inheritTextStyleID": "t1", "fontSize": None})
        self.assertEqual(s["font"]["token"], "Text/B1")
        self.assertEqual(s["font"]["size"], 13)


class LocalAnatomyLook(unittest.TestCase):
    def test_virtual_nodes_get_the_overridden_color_and_keep_their_background(self):
        lib = {"guid": "L", "type": "SYMBOL", "layerType": "COMPONENT", "name": "Lib", "publishFile": "p", "width": 5,
               "height": 5}
        loc = {"guid": "C", "type": "SYMBOL", "layerType": "COMPONENT", "name": "Card", "width": 100, "height": 50,
               "fillPaints": [solid(240, 240, 240)], "childNode": [
                   n("T", "PARAGRAPH", nodeText="Заг", fontFamily="F", fontSize=12, fontStyle="Bold",
                     fillPaints=[solid(0, 0, 0)])]}
        inst = n("s", "INSTANCE", mainComponent="C", w=100, h=50, fillPaints=[solid(240, 240, 240)],
                 props=[{"pathString": "T", "fillPaints": [solid(200, 0, 0)]}])
        env = conv(inst, defs=[lib, loc])
        t = by_id(env)["s;T"]
        self.assertEqual(t["color"], "rgba(200, 0, 0, 1)")
        self.assertEqual(t["font"]["weight"], 700)
        self.assertEqual(by_id(env)["s"]["style"]["background"], "rgba(240, 240, 240, 1)")


if __name__ == "__main__":
    unittest.main()
