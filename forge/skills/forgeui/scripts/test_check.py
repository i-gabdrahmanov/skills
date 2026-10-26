"""test_check.py — правила проверки над парами матчера (задача 4.4, спека §14.4.3–14.4.4, INV-C27, QG-U16, F3).

Пинит по синтетическим деревьям и настоящему канону «Modal»: допуски (на грани тихо, сверх — находка, кап размера),
по тесту на каждую доктрину (числовые только на уверенных парах; слоты — только overridden; «чей размер»; контракт
библиотеки vs INVISIBLE; выведенный размер; отступы по сторонам и margin вместо gap; типографика, цвет, форма, текст,
overflow), падение правила → degraded, «пар нет», unverified по трём аспектам, детерминизм. Пары строит настоящий
матчер (`match.match`), кроме случаев, где нужна заведомо неуверенная пара.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver.ingest import pixso as PX  # noqa: E402
from daiver.verify import check as CK  # noqa: E402
from daiver.verify import match as MT  # noqa: E402
from test_match import FIXTURE, T, D, P, as_page, box, comp, inst, page, screen  # noqa: E402

BG = {"background": "rgba(255, 255, 255, 1)"}


def run(design, impl, tol=None, flags=None, plan=None, degraded_ok=False):
    res = CK.check(design, impl, MT.match(design, impl, plan), tol, flags)
    if res["degraded"] and not degraded_ok:                     # упавшее правило молчит находками: тест не должен принимать это за «чисто»
        raise AssertionError(f"правило упало: {res['degraded']}")
    return res


def codes(res):
    return sorted(((f["code"], f.get("prop")) for f in res["findings"]), key=lambda x: (x[0], x[1] or ""))


def dcodes(res):
    return sorted(((f["code"], f.get("prop")) for f in res["dismissals"]), key=lambda x: (x[0], x[1] or ""))


def text(id, s, font=None, color=None, y=0, w=60, h=14):
    n = D("text", id, text=s, box=box(0, y, w, h), size={"w": w, "h": h})
    if font:
        n["font"] = font
    if color:
        n["color"] = color
    return n


def ptext(id, s, font=None, color=None, y=0, w=60, h=14):
    n = text(id, s, font, color, y, w, h)
    n["tag"] = "span"
    return n


class Tolerances(unittest.TestCase):
    """F3: допуски и их границы."""

    def test_position_plus_three_px_is_a_finding_and_two_is_silent(self):
        for dx, want in ((3, [("POSITION", "x")]), (2, []), (-3, [("POSITION", "x")])):
            dkid = text("d:t", "Привет")
            dkid["box"]["x"] = 10
            d = screen([D("frame", "d:f", [dkid], box=box(10, 10, 100, 30), style=BG)])
            kid = ptext("p:t", "Привет")
            kid["box"]["x"] = 10 + dx                             # ребёнок едет вместе с родителем: его смещение внутри то же
            i = page([D("frame", "p:f", [kid], box=box(10 + dx, 10, 100, 30), tag="div", style=BG)])
            res = run(d, i)
            self.assertEqual([c for c in codes(res) if c[0] == "POSITION"], want, f"dx={dx}")
            self.assertEqual([c for c in dcodes(res) if c[0] == "POSITION"], [], "на грани — тихо, не dismissal")

    def test_color_channel_three_is_a_finding_two_is_silent(self):
        for delta, bad in ((3, True), (2, False)):
            d = screen([text("d:t", "Привет", color=f"rgba(100, 100, {100 + delta}, 1)")])
            i = page([ptext("p:t", "Привет", color="rgba(100, 100, 100, 1)")])
            self.assertEqual(bool(codes(run(d, i))), bad, f"Δ{delta}")
            if bad:
                self.assertEqual(codes(run(d, i)), [("COLOR", "color")])

    def test_font_size_one_is_a_finding_half_is_silent(self):
        for size, bad in ((21, True), (20.5, False)):
            d = screen([text("d:t", "Привет", font={"family": "Fixture Sans", "size": 20})])
            i = page([ptext("p:t", "Привет", font={"family": "Fixture Sans", "size": size})])
            self.assertEqual(codes(run(d, i)), [("TYPOGRAPHY", "размер шрифта")] if bad else [])

    def test_size_ratio_and_the_cap(self):
        def sized(w, got, tol=None):
            d = D("frame", "d:root", [D("frame", "d:f", [text("d:t", "Привет")], box=box(0, 0, w, 30), size={"w": w, "h": 30}, style=BG)],
                  box=box(0, 0, 3000, 300), size={"w": 3000, "h": 300})
            i = D("frame", "p:body", [D("frame", "p:f", [ptext("p:t", "Привет")], box=box(0, 0, got, 30), tag="div", style=BG)],
                  box=box(0, 0, 3000, 300), tag="body")
            return [c for c in codes(run(d, i, tol)) if c[0] == "SIZE"]
        self.assertEqual(sized(400, 405, {"sizeRatio": 0.02}), [], "1.25 % внутри пропорции 2 %")
        self.assertEqual(sized(400, 408.5, {"sizeRatio": 0.02}), [("SIZE", "w")], "2.1 % — за пропорцией")
        self.assertEqual(sized(1000, 1015, {"sizeRatio": 0.02}), [("SIZE", "w")], "1.5 % в пропорции, но 15 px сверх капа 8")
        self.assertEqual(sized(1000, 1008, {"sizeRatio": 0.02}), [], "ровно на капе — на грани, тихо")
        self.assertEqual(sized(100, 103), [("SIZE", "w")], "по умолчанию 1 % от 100 = 1 < px 2: пол px")
        self.assertEqual(sized(100, 102), [])

    def test_changed_text_is_critical(self):
        res = run(screen([text("d:t", "Сохранить")]), page([ptext("p:t", "Сохранитьь")]))
        self.assertEqual(codes(res), [("TEXT", "text")])
        self.assertEqual(res["findings"][0]["severity"], "critical")

    def test_tolerances_from_config_override_and_junk_is_ignored(self):
        res = CK.check(screen([]), page([]), MT.match(screen([]), page([])), {"px": 5, "bogus": 1, "color": "много", "fontSize": -1})
        self.assertEqual((res["tolerances"]["px"], res["tolerances"]["color"], res["tolerances"]["fontSize"]), (5, 2, 0.5))


class Confidence(unittest.TestCase):
    def test_numeric_rules_skip_low_pairs_identity_rules_run(self):
        d = screen([text("d:t", "Сохранить", color="rgba(0, 0, 0, 1)")])
        i = page([ptext("p:t", "Сохранитъ", color="rgba(200, 0, 0, 1)")])
        res = run(d, i)
        self.assertEqual(codes(res), [("TEXT", "text")], "пара по позиции — low: цвет не сверяется, текст — да")
        self.assertEqual(res["stats"]["skippedLowConfidence"], 1)


class Slots(unittest.TestCase):
    def _cell(self, slots, **kw):
        return screen([inst("d:cell", "Cell", comp={"origin": "library", "sized": "own", "content": slots}, **kw)])

    def _page(self, texts, **kw):
        return page([comp("p:cell", ["Cell"], texts, **kw)])

    def test_missing_slot(self):
        d = self._cell([{"id": "s1", "name": "Name", "text": "Иванов", "overridden": True},
                        {"id": "s2", "name": "Role", "text": "Менеджер", "overridden": True}])
        res = run(d, self._page([ptext("p:a", "Иванов")]))
        self.assertEqual(codes(res), [("SLOT_MISSING", "s2")])
        self.assertEqual(res["findings"][0]["severity"], "critical")

    def test_changed_slot_text(self):
        d = self._cell([{"id": "s1", "text": "Менеджер по продажам", "overridden": True}])
        res = run(d, self._page([ptext("p:a", "Менеджер по закупкам")]))
        self.assertEqual(codes(res), [("SLOT_TEXT", "s1")], "другая копия слота — SLOT_TEXT, а не ещё и EXTRA")

    def test_placeholder_slot_is_not_compared_and_is_unverified_copy(self):
        d = self._cell([{"id": "s1", "text": "Текст3", "overridden": False}, {"id": "s2", "text": "Ок", "overridden": True}])
        res = run(d, self._page([ptext("p:a", "Совсем другое"), ptext("p:b", "Ок", y=15)]))
        self.assertEqual(codes(res), [])
        self.assertEqual([u["aspects"] for u in res["unverified"]["items"]], [["copy"]])

    def test_slot_typography_and_color(self):
        f1 = {"family": "Fixture Sans", "size": 20, "weight": 600}
        d = self._cell([{"id": "s1", "name": "Title", "text": "Заголовок", "overridden": True, "font": f1, "color": "rgba(0, 0, 0, 1)"}])
        i = self._page([ptext("p:a", "Заголовок", font={"family": "Fixture Sans", "size": 20, "weight": 400}, color="rgba(90, 0, 0, 1)")])
        self.assertEqual(codes(run(d, i)), [("SLOT_COLOR", "s1:color"), ("SLOT_TYPOGRAPHY", "s1:насыщенность")])

    def test_swapped_slots_get_the_hint(self):
        big = {"family": "Fixture Sans", "size": 20, "weight": 600}
        small = {"family": "Fixture Sans", "size": 13, "weight": 400}
        d = self._cell([{"id": "s1", "name": "Caption", "text": "Подпись", "overridden": True, "font": small, "color": "rgba(120, 120, 120, 1)"},
                        {"id": "s2", "name": "Value", "text": "42", "overridden": True, "font": big, "color": "rgba(0, 0, 0, 1)"}])
        i = self._page([ptext("p:a", "Подпись", font=big, color="rgba(0, 0, 0, 1)"),
                        ptext("p:b", "42", font=small, color="rgba(120, 120, 120, 1)", y=20)])
        res = run(d, i)
        self.assertEqual(len(res["findings"]), 6, "по слоту: размер, насыщенность, цвет")
        self.assertTrue(all("перепутаны" in f.get("hint", "") for f in res["findings"]))

    def test_slot_text_rendered_elsewhere_is_a_dismissal(self):
        d = screen([inst("d:cell", "Cell", comp={"origin": "library", "sized": "own",
                                                "content": [{"id": "s1", "text": "Итого", "overridden": True}]}),
                    text("d:o", "Итого", y=100)])
        i = page([comp("p:cell", ["Cell"], []), ptext("p:o", "Итого", y=100)])
        res = run(d, i)
        self.assertEqual(codes(res), [])
        self.assertIn(("SLOT_MISSING", "s1"), dcodes(res))

    def test_case_only_difference_is_a_dismissal(self):
        d = self._cell([{"id": "s1", "text": "Сохранить", "overridden": True}])
        res = run(d, self._page([ptext("p:a", "СОХРАНИТЬ")]))
        self.assertEqual((codes(res), [c for c in dcodes(res) if c[0] == "SLOT_TEXT"]), ([], [("SLOT_TEXT", "s1")]))

    def test_text_props_are_the_slots_when_there_is_no_content(self):
        d = screen([inst("d:b", "Btn", comp={"origin": "library", "sized": "own", "textProps": {"label": "Отправить"}})])
        res = run(d, page([comp("p:b", ["Btn"], [ptext("p:a", "Отправить!")])]))
        self.assertEqual([c[0] for c in codes(res)], ["SLOT_TEXT"])


class Invisible(unittest.TestCase):
    def _d(self, **c):
        return screen([inst("d:b", "Btn", comp={"origin": "library", "sized": "own", "paints": True, "textProps": {"l": "Ок"}, **c})])

    def test_master_surface_with_unpainted_element_is_a_defect(self):
        res = run(self._d(), page([comp("p:b", ["Btn"], [ptext("p:a", "Ок")])]))
        self.assertEqual(codes(res), [("INVISIBLE", None)])

    def test_painted_element_or_covering_inner_surface_is_fine(self):
        painted = comp("p:b", ["Btn"], [ptext("p:a", "Ок")], style={"background": "rgba(1, 2, 3, 1)"})
        self.assertEqual(codes(run(self._d(), page([painted]))), [])
        inner = comp("p:b", ["Btn"], [D("frame", "p:s", [ptext("p:a", "Ок")], box=box(0, 0, 80, 30), tag="div", style=BG)])
        self.assertEqual(codes(run(self._d(), page([inner]))), [])

    def test_small_inner_paint_is_not_the_surface(self):
        dot = D("rect", "p:dot", box=box(0, 0, 6, 6), tag="span", style=BG)
        res = run(self._d(), page([comp("p:b", ["Btn"], [dot, ptext("p:a", "Ок", y=10)])]))
        self.assertEqual(codes(res), [("INVISIBLE", None)])

    def test_picture_is_not_an_unpainted_box(self):
        d = screen([inst("d:b", "Avatar", comp={"origin": "library", "sized": "own", "paints": True, "textProps": {"l": "Ок"}})])
        i = page([D("image", "p:img", box=box(0, 0, 40, 40), tag="img")])
        self.assertNotIn("INVISIBLE", [c[0] for c in codes(run(d, i))])


class Size(unittest.TestCase):
    def _pair(self, dsize, isize, comp_extra=None, kind="instance", extra=None):
        if kind == "instance":
            dn = inst("d:x", "Btn", comp={"origin": "library", **(comp_extra or {}), "textProps": {"l": "Ок"}}, size=dsize, box=box(0, 0, 10, 10))
            inn = comp("p:x", ["Btn"], [ptext("p:a", "Ок")], box=box(0, 0, *isize))
        else:
            dn = D("frame", "d:x", [text("d:t", "Привет")], size=dsize, box=box(0, 0, 10, 10), **(extra or {}))
            inn = D("frame", "p:x", [ptext("p:t", "Привет")], box=box(0, 0, *isize), tag="div", **(extra or {}))
        return screen([dn], {"size": {"w": 800, "h": 600}}), page([inn])

    def test_instance_size_only_when_own(self):
        d, i = self._pair({"w": 100, "h": 30}, (160, 30), {"sized": "own", "paints": True})
        self.assertEqual([c for c in codes(run(d, i)) if c[0] == "SIZE"], [("SIZE", "w")])
        d, i = self._pair({"w": 100, "h": 30}, (160, 30), {"sized": "master", "paints": True})
        res = run(d, i)
        self.assertEqual([c for c in codes(res) if c[0] == "SIZE"], [])
        self.assertIn(("SIZE", None), dcodes(res))

    def test_fill_and_hug_are_dismissals_per_axis(self):
        d, i = self._pair({"w": "fill", "h": "hug"}, (300, 99), kind="frame")
        res = run(d, i)
        self.assertEqual([c for c in codes(res) if c[0] == "SIZE"], [])
        self.assertEqual([c for c in dcodes(res) if c[0] == "SIZE" and c[1] in ("w", "h")], [("SIZE", "h"), ("SIZE", "w")])

    def test_number_bigger_than_parent_room_is_a_dismissal(self):
        d, i = self._pair({"w": 900, "h": 30}, (500, 30), kind="frame")
        res = run(d, i)
        self.assertEqual([c for c in codes(res) if c[0] == "SIZE"], [])
        self.assertTrue(any("комнаты родителя" in x["reason"] for x in res["dismissals"]))

    def test_width_equal_to_parent_room_is_a_dismissal_only_if_page_spans_too(self):
        def pair(page_w):
            d = D("frame", "d:root", [D("frame", "d:f", [text("d:t", "Привет")], size={"w": 800, "h": 30}, box=box(0, 0, 800, 30), style=BG)],
                  box=box(0, 0, 800, 300), size={"w": 800, "h": 300})
            i = D("frame", "p:body", [D("frame", "p:f", [ptext("p:t", "Привет")], box=box(0, 0, page_w, 30), tag="div", style=BG)],
                  box=box(0, 0, 1000, 300), tag="body")
            return run(d, i)
        res = pair(1000)
        self.assertEqual([c for c in codes(res) if c[0] == "SIZE"], [], "и макет, и страница заняли комнату родителя целиком")
        self.assertTrue(any("равна комнате" in x["reason"] for x in res["dismissals"]))
        self.assertEqual([c for c in codes(pair(900)) if c[0] == "SIZE"], [("SIZE", "w")], "страница родителя занята не целиком: репорт")

    def test_text_box_shorter_is_reported_taller_is_a_dismissal(self):
        d = screen([text("d:t", "Привет", h=20)])
        self.assertEqual([c for c in codes(run(d, page([ptext("p:t", "Привет", h=14)]))) if c[0] == "SIZE"], [("SIZE", "h")])
        res = run(d, page([ptext("p:t", "Привет", h=28)]))
        self.assertEqual([c for c in codes(res) if c[0] == "SIZE"], [])
        self.assertIn(("SIZE", "h"), dcodes(res))

    def test_text_box_width_is_reported(self):
        d = screen([text("d:t", "Привет", w=100)])
        self.assertEqual([c for c in codes(run(d, page([ptext("p:t", "Привет", w=130)]))) if c[0] == "SIZE"], [("SIZE", "w")])

    def test_root_frame_is_a_dismissal(self):
        res = run(screen([text("d:t", "Привет")]), page([ptext("p:t", "Привет")]))
        self.assertTrue(any(x["design"] == "d:root" and x["code"] == "SIZE" for x in res["dismissals"]))


class Spacing(unittest.TestCase):
    def _pair(self, dpad, ipad, **kw):
        d = screen([D("frame", "d:f", [text("d:t", "Привет")], layout={"direction": "column", "padding": dpad, **kw.get("dl", {})},
                      box=box(0, 0, 100, 40), style=BG)])
        i = page([D("frame", "p:f", [ptext("p:t", "Привет")], layout={"direction": "column", "padding": ipad, **kw.get("il", {})},
                    box=box(0, 0, 100, 40), tag="div", style=BG)])
        return d, i

    def test_padding_is_reported_per_side(self):
        side = lambda t, r, b, l: {"top": t, "right": r, "bottom": b, "left": l}
        d, i = self._pair(side(8, 16, 8, 16), side(8, 24, 8, 12))
        self.assertEqual([c for c in codes(run(d, i)) if c[0] == "SPACING"], [("SPACING", "padding.left"), ("SPACING", "padding.right")])

    def test_padding_inside_tolerance_is_silent(self):
        side = lambda v: {"top": v, "right": v, "bottom": v, "left": v}
        d, i = self._pair(side(8), side(10))
        self.assertEqual([c for c in codes(run(d, i)) if c[0] == "SPACING"], [])

    def test_library_internals_are_a_contract(self):
        side = lambda v: {"top": v, "right": v, "bottom": v, "left": v}
        d = screen([inst("d:b", "Btn", comp={"origin": "library", "sized": "own", "textProps": {"l": "Ок"}},
                         layout={"direction": "row", "padding": side(8)}, style=BG)])
        i = page([comp("p:b", ["Btn"], [ptext("p:a", "Ок")], layout={"direction": "row", "padding": side(20)}, style=BG)])
        res = run(d, i)
        self.assertEqual([c for c in codes(res) if c[0] == "SPACING"], [])
        self.assertEqual([c for c in dcodes(res) if c[0] == "SPACING"], [("SPACING", "padding.bottom"), ("SPACING", "padding.left"),
                                                                            ("SPACING", "padding.right"), ("SPACING", "padding.top")])

    def test_gap_difference_is_reported_but_margin_with_equal_boxes_is_a_dismissal(self):
        z = {"top": 0, "right": 0, "bottom": 0, "left": 0}
        dkids = [text("d:a", "Раз", y=0, h=14), text("d:b", "Два", y=30, h=14)]
        d = screen([D("frame", "d:f", dkids, layout={"direction": "column", "gap": 16, "padding": z}, box=box(0, 0, 100, 60), style=BG)])
        far = [ptext("p:a", "Раз", y=0, h=14), ptext("p:b", "Два", y=40, h=14)]             # измеренный зазор 26
        i = page([D("frame", "p:f", far, layout={"direction": "column", "gap": 26, "padding": z}, box=box(0, 0, 100, 60), tag="div", style=BG)])
        self.assertEqual([c for c in codes(run(d, i)) if c[0] == "SPACING"], [("SPACING", "gap")])
        margin = [ptext("p:a", "Раз", y=0, h=14), ptext("p:b", "Два", y=30, h=14)]          # измеренный зазор 16, но CSS gap 0
        i = page([D("frame", "p:f", margin, layout={"direction": "column", "gap": 0, "padding": z}, box=box(0, 0, 100, 60), tag="div", style=BG)])
        res = run(d, i)
        self.assertEqual([c for c in codes(res) if c[0] == "SPACING"], [])
        self.assertTrue(any("margin" in x["reason"] for x in res["dismissals"]))


class Typography(unittest.TestCase):
    def _t(self, a, b):
        return run(screen([text("d:t", "Привет", font=a)]), page([ptext("p:t", "Привет", font=b)]))

    def test_first_family_only(self):
        self.assertEqual(codes(self._t({"family": "SB Sans Text"}, {"family": "SB Sans Text, Arial, sans-serif"})), [])
        self.assertEqual(codes(self._t({"family": "SB Sans Text"}, {"family": "Arial, SB Sans Text"})), [("TYPOGRAPHY", "шрифт")])

    def test_omitted_weight_is_regular_on_both_sides(self):
        self.assertEqual(codes(self._t({"family": "A", "size": 13}, {"family": "A", "size": 13, "weight": 400})), [])
        self.assertEqual(codes(self._t({"family": "A", "weight": 600}, {"family": "A"})), [("TYPOGRAPHY", "насыщенность")])

    def test_align_and_line_height_only_where_both_declared(self):
        self.assertEqual(codes(self._t({"family": "A", "align": "center"}, {"family": "A"})), [])
        self.assertEqual(codes(self._t({"family": "A", "lineHeight": "auto"}, {"family": "A", "lineHeight": 24})), [])
        self.assertEqual(codes(self._t({"family": "A", "lineHeight": 20}, {"family": "A", "lineHeight": 24})), [("TYPOGRAPHY", "межстрочный")])
        self.assertEqual(codes(self._t({"family": "A", "lineHeight": 20}, {"family": "A", "lineHeight": 21})), [])
        self.assertEqual(codes(self._t({"align": "left"}, {"align": "right"})), [("TYPOGRAPHY", "выравнивание")])

    def test_letter_spacing_a_hair_wider_is_reported(self):
        self.assertEqual(codes(self._t({"letterSpacing": -0.5}, {"letterSpacing": -0.2})), [("TYPOGRAPHY", "межбуквенный")])
        self.assertEqual(codes(self._t({"letterSpacing": -0.5}, {"letterSpacing": -0.45})), [])


class Colors(unittest.TestCase):
    def _bg(self, a, b):
        d = screen([D("frame", "d:f", [text("d:t", "Привет")], box=box(0, 0, 100, 40), style=a)])
        i = page([D("frame", "p:f", [ptext("p:t", "Привет")], box=box(0, 0, 100, 40), tag="div", style=b)])
        return codes(run(d, i))

    def test_hex_keyword_and_rgba_are_one_color(self):
        self.assertEqual(self._bg({"background": "rgba(255, 255, 255, 1)"}, {"background": "#fff"}), [])
        self.assertEqual(self._bg({"background": "rgba(255, 255, 255, 1)"}, {"background": "white"}), [])
        self.assertEqual(self._bg({"background": "rgba(0, 120, 255, 0.5)"}, {"background": "#0078ff80"}), [])

    def test_no_fill_is_transparent(self):
        self.assertEqual(self._bg({}, {"background": "rgba(0, 0, 0, 0)"}), [])
        self.assertEqual(self._bg({}, {"background": "rgba(0, 0, 0, 1)"}), [("COLOR", "background")])
        self.assertEqual(self._bg({"background": "rgba(0, 0, 0, 1)"}, {}), [("COLOR", "background")])

    def test_alpha_rounded_token_passes_designer_choice_does_not(self):
        self.assertEqual(self._bg({"background": "rgba(0, 0, 0, 0.12)"}, {"background": "rgba(0, 0, 0, 0.1)"}), [])
        self.assertEqual(self._bg({"background": "rgba(0, 0, 0, 0.5)"}, {"background": "rgba(0, 0, 0, 0.3)"}), [("COLOR", "background")])

    def test_gradient_is_compared_as_text(self):
        g = "linear-gradient(90deg, #fff, #000)"
        self.assertEqual(self._bg({"gradient": g}, {"gradient": "linear-gradient(90deg,#fff,#000)"}), [])
        self.assertEqual(self._bg({"gradient": g}, {"background": "rgba(255, 255, 255, 1)"}), [("COLOR", "gradient")])
        self.assertEqual(self._bg({"gradient": g}, {"gradient": "linear-gradient(0deg, #fff, #000)"}), [("COLOR", "gradient")])

    def test_border_on_one_side_is_silent_for_color(self):
        b = {"width": 1, "color": "rgba(0, 0, 0, 1)"}
        self.assertEqual([c for c in self._bg({"border": b}, {}) if c[0] == "COLOR"], [])
        self.assertEqual(self._bg({"border": b}, {"border": {"width": 1, "color": "rgba(200, 0, 0, 1)"}}), [("COLOR", "border")])

    def test_unparsable_colour_is_a_dismissal_not_a_defect(self):
        d = screen([D("frame", "d:f", [text("d:t", "Привет")], box=box(0, 0, 100, 40), style={"background": "rgba(1, 2, 3, 1)"})])
        i = page([D("frame", "p:f", [ptext("p:t", "Привет")], box=box(0, 0, 100, 40), tag="div", style={"background": "oklch(0.5 0.1 200)"})])
        res = run(d, i)
        self.assertEqual(codes(res), [])
        self.assertIn(("COLOR", "background"), dcodes(res))


class Shape(unittest.TestCase):
    def _s(self, a, b, w=40, h=40):
        d = screen([D("frame", "d:f", [text("d:t", "Привет")], box=box(0, 0, w, h), style={**BG, **a})])
        i = page([D("frame", "p:f", [ptext("p:t", "Привет")], box=box(0, 0, w, h), tag="div", style={**BG, **b})])
        return run(d, i)

    def test_what_the_browser_draws_circle_is_a_circle(self):
        self.assertEqual(codes(self._s({"radius": 20}, {"radius": 9999})), [])
        self.assertEqual(codes(self._s({"radius": 20}, {"radius": 12})), [("SHAPE", "radius")])

    def test_corner_is_named(self):
        res = self._s({"radius": [16, 16, 0, 0]}, {"radius": [16, 0, 0, 0]})
        self.assertEqual(res["findings"][0]["corners"], ["top-right"])

    def test_border_and_shadow_presence_both_ways(self):
        b = {"width": 1, "color": "rgba(0, 0, 0, 1)"}
        self.assertEqual(codes(self._s({"border": b}, {})), [("SHAPE", "border")])
        self.assertEqual(codes(self._s({}, {"border": b})), [("SHAPE", "border")])
        self.assertEqual(codes(self._s({"shadow": "0px 4px 8px 0px rgba(0, 0, 0, 0.1)"}, {})), [("SHAPE", "shadow")])
        self.assertEqual(codes(self._s({"shadow": "0px 4px 8px 0px rgba(0, 0, 0, 0.1)"}, {"shadow": "0px 1px 2px 0px rgba(0, 0, 0, 0.9)"})), [],
                         "тень сравнивается присутствием, а не строкой")

    def test_library_radius_is_a_contract(self):
        d = screen([inst("d:b", "Btn", comp={"origin": "library", "sized": "own", "textProps": {"l": "Ок"}}, box=box(0, 0, 80, 30),
                         style={**BG, "radius": 4})])
        i = page([comp("p:b", ["Btn"], [ptext("p:a", "Ок")], box=box(0, 0, 80, 30), style={**BG, "radius": 12})])
        res = run(d, i)
        self.assertEqual(codes(res), [])
        self.assertIn(("SHAPE", "radius"), dcodes(res))


class TextRule(unittest.TestCase):
    def test_case_only_is_a_dismissal(self):
        res = run(screen([text("d:t", "Сохранить")]), page([ptext("p:t", "СОХРАНИТЬ")]))
        self.assertEqual((codes(res), [c for c in dcodes(res) if c[0] == "TEXT"]), ([], [("TEXT", "text")]))

    def test_reflow_whitespace_is_not_a_difference(self):
        self.assertEqual(codes(run(screen([text("d:t", "Раз  два\nтри")]), page([ptext("p:t", "Раз два три")]))), [])

    def test_empty_design_text_is_not_interpreted(self):
        d = screen([D("text", "d:t", text="", box=box(0, 0, 10, 10)), text("d:u", "Привет", y=20)])
        self.assertEqual(codes(run(d, page([ptext("p:u", "Привет", y=20)]))), [])


class Overflow(unittest.TestCase):
    def test_page_overflow_the_mockup_does_not_draw_is_reported(self):
        d = screen([D("frame", "d:f", [text("d:t", "Привет")], box=box(0, 0, 100, 40), style=BG)])
        i = page([D("frame", "p:f", [ptext("p:t", "Привет")], box=box(0, 0, 100, 40), tag="div", style=BG)])
        self.assertEqual(codes(run(d, i, flags={"p:f": {"overflowing": True}})), [("OVERFLOW", None)])
        self.assertEqual(codes(run(d, i)), [])


class Engine(unittest.TestCase):
    def test_a_throwing_rule_degrades_and_the_rest_run(self):
        d, i = screen([text("d:t", "Привет")]), page([ptext("p:t", "Приветт")])
        orig = list(CK.RULES)
        try:
            CK.RULES.insert(1, ("boom", lambda c: 1 / 0))
            res = run(d, i, degraded_ok=True)
        finally:
            CK.RULES[:] = orig
        self.assertEqual([x["rule"] for x in res["degraded"]], ["boom"])
        self.assertIn("ZeroDivisionError", res["degraded"][0]["error"])
        self.assertEqual(codes(res), [("TEXT", "text")], "остальные правила идут")
        self.assertNotIn("boom", res["executed"])

    def test_normal_run_is_not_degraded(self):
        res = run(screen([text("d:t", "Привет")]), page([ptext("p:t", "Привет")]))
        self.assertEqual(res["degraded"], [])
        self.assertEqual(res["executed"], [n for n, _ in CK.RULES])

    def test_no_pairs_means_every_rule_ran(self):
        res = CK.check(screen([text("d:t", "Привет")]), None, MT.match(screen([text("d:t", "Привет")]), None))
        self.assertTrue(res["stats"]["nothingToCompare"] or res["stats"]["pairs"] == 1)
        self.assertEqual(res["degraded"], [])
        self.assertEqual(res["executed"], [n for n, _ in CK.RULES])

    def test_structure_findings_come_from_the_matcher(self):
        d = screen([text("d:a", "Раз"), text("d:b", "Два", y=20)])
        i = page([ptext("p:a", "Раз"), ptext("p:x", "Лишний", y=15)])
        m = MT.match(d, i)
        res = CK.check(d, i, m)
        self.assertEqual(sorted((f["code"], f.get("design") or f.get("impl")) for f in res["findings"] if f["layer"] == "structure"),
                         sorted([("MISSING", "d:b")] + [("EXTRA", e["id"]) for e in m["extra"]]))

    def test_layers_are_ordered_and_output_is_deterministic(self):
        d = screen([text("d:t", "Привет", font={"family": "A", "size": 20}, color="rgba(0, 0, 0, 1)"), text("d:u", "Мир", y=20)])
        i = page([ptext("p:t", "Приветт", font={"family": "A", "size": 25}, color="rgba(90, 0, 0, 1)")])
        before = copy.deepcopy((d, i))
        a, b = run(d, i), run(d, i)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual((d, i), before)
        layers = [CK.LAYERS.index(f["layer"]) for f in a["findings"]]
        self.assertEqual(layers, sorted(layers))

    def test_bad_input_is_refused(self):
        with self.assertRaises(CK.CheckError):
            CK.check(screen([]), page([]), {"nope": 1})
        with self.assertRaises(CK.CheckError):
            CK.check({"x": 1}, page([]), {"pairs": []})


class Unverified(unittest.TestCase):
    def test_three_aspects(self):
        d = screen([
            inst("d:fb", "Ghost", comp={"childrenRole": "fallback"}),                                       # определения нет: content
            inst("d:ph", "Cell", comp={"origin": "library", "sized": "own", "content": [{"id": "s", "text": "Текст3", "overridden": False}]}),
            inst("d:sz", "Card", comp={"origin": "library", "textProps": {"l": "Ок"}}),                      # sized не атрибутирован: size
        ])
        res = CK.unverified(CK._Ctx(d, None, {"pairs": []}, dict(CK.CFG.DEFAULT_TOLERANCES), {}))
        self.assertEqual({u["id"]: u["aspects"] for u in res["items"]}, {"d:fb": ["content"], "d:ph": ["copy"], "d:sz": ["size"]})
        self.assertEqual(res["counts"], {"content": 1, "copy": 1, "size": 1})
        self.assertIn("не проверено: 3 элем.", res["summary"])

    def test_nothing_unverified_has_no_summary(self):
        d = screen([text("d:t", "Привет")])
        res = CK.unverified(CK._Ctx(d, None, {"pairs": []}, dict(CK.CFG.DEFAULT_TOLERANCES), {}))
        self.assertEqual((res["items"], res["summary"]), ([], None))

    def test_occluded_instances_are_not_listed(self):
        d = screen([inst("d:fb", "Ghost", comp={"childrenRole": "fallback"}, occluded=True)])
        self.assertEqual(CK.unverified(CK._Ctx(d, None, {"pairs": []}, dict(CK.CFG.DEFAULT_TOLERANCES), {}))["items"], [])


def real_page(design):
    """Страница настоящего «Modal»: узлы канона как есть (боксы по размерам макета), обёртки body > #root ровно по рамке."""
    pg = as_page(design)
    pg["box"] = copy.deepcopy(design["box"])
    for k in pg["children"]:
        k["box"]["w"] = 560 if k["box"]["w"] == 600 and k["component"]["figmaName"] == "Modal Action" else k["box"]["w"]
    inner = D("frame", "p:wrap", [pg], box=copy.deepcopy(design["box"]), tag="div")
    return D("frame", "p:body", [D("frame", "p:root", [inner], box=copy.deepcopy(design["box"]), tag="div")],
             box=copy.deepcopy(design["box"]), tag="body"), pg


class RealModal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")["root"]

    def test_rendering_built_from_the_canon_is_clean(self):
        pg, _ = real_page(self.design)
        res = run(self.design, pg)
        self.assertEqual(res["degraded"], [])
        self.assertEqual(res["findings"], [])
        self.assertEqual(res["unverified"]["counts"], {"content": 0, "copy": 1, "size": 0}, "Modal Action: слот «Текст3» на плейсхолдере")
        self.assertIn("copy 1", res["unverified"]["summary"])

    def test_wrong_slot_font_on_a_real_header(self):
        outer, pg = real_page(self.design)
        header = pg["children"][1]                                   # 22:63141: заголовок и описание
        for t in header["children"]:
            t["font"] = {"family": "Fixture Sans", "size": 13, "weight": 400, "lineHeight": 16, "letterSpacing": -0.2}
        res = run(self.design, outer)
        got = [(f["code"], f["prop"]) for f in res["findings"]]
        self.assertIn(("SLOT_TYPOGRAPHY", "22:63141;2:170582:размер шрифта"), got, "заголовок 20 против 13")
        self.assertIn(("SLOT_TYPOGRAPHY", "22:63141;2:170582:насыщенность"), got)
        self.assertNotIn("22:63141;2:170586", " ".join(p for _, p in got), "описание нарисовано как в макете")

    def test_changed_real_slot_text(self):
        outer, pg = real_page(self.design)
        pg["children"][0]["children"][0]["text"] = "Текст12 слово 1-105_2-слово.2"
        res = run(self.design, outer)
        self.assertEqual([(f["code"], f["prop"]) for f in res["findings"]], [("SLOT_TEXT", "2:153703;2:170582")])


if __name__ == "__main__":
    unittest.main()
