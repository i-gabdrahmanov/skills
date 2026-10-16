#!/usr/bin/env python3
"""test_layout_inference.py — вывод раскладки для макета без auto-layout (задача 1.7; спека §8.1–8.2, §20.4;
QG U3, F6; INV B8).

Реального полноэкранного кадра без auto-layout у нас нет («Modal» целиком на auto-layout), поэтому каждое правило
§8.1 и каждый отказ доказаны СИНТЕТИКОЙ — маленькими деревьями канона, собранными прямо в файле (модуль
источник-независим), — а допущения, которых образец не проверяет, названы в docstring `ingest/layout.py`. На образце
пинится обратное: «Modal» с включённым выводом побайтно тот же, что с выключенным.

Доказательство проверяется ВТОРЫМ движком: `abs_rects` ниже заново считает экранные прямоугольники по одному лишь
канону (мини-flexbox, написанный независимо от модуля) и сверяет их с боксами макета ±1 px — «moves nothing».
F6 — на DSL «лендинга» 1280×800 без единого auto-layout: шапка из трёх величин зазора (вложенные группы), оверлей-фон
над контентом (остаётся абсолютным + подсказки), решётка карточек (один wrapping row), колонка секций, одиночный
текст в подвале; повторный normalize побайтно стабилен, id групп не зависят от порядка в источнике.
"""
from __future__ import annotations

import copy
import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver.ingest import layout as LY  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
URL = "https://pixso.example/a?item-id=2:153702"


# ── построители канона ────────────────────────────────────────────────────────
def leaf(i, x, y, w, h, kind="rect", **kw):
    n = {"id": i, "kind": kind, "box": {"x": x, "y": y, "w": w, "h": h}, "size": {"w": w, "h": h}}
    n.update(kw)
    return n


def frame(i, x, y, w, h, kids, **kw):
    n = {"id": i, "kind": "frame", "box": {"x": x, "y": y, "w": w, "h": h}, "size": {"w": w, "h": h},
         "children": list(kids)}
    n.update(kw)
    return n


def screen(kids, w=400, h=400):
    return frame("root", 0, 0, w, h, kids)


def ids(node):
    return [k["id"] for k in node.get("children", [])]


def rects_before(root):
    out = {}
    for n in C.iter_nodes(root):
        b = n.get("box")
        if b:
            out[n["id"]] = (b["x"], b["y"], b["w"], b["h"])
    return out


# ── второй движок: экранные прямоугольники по одному лишь канону ──────────────
def measure(n):
    """(w, h) узла в потоке: числа из size; hug у синтетической группы считается по её детям."""
    sz = n.get("size") or {}
    w, h = sz.get("w"), sz.get("h")
    if "hug" in (w, h):
        lay = n["layout"]
        row = lay["direction"] == "row"
        ms = [measure(k) for k in n["children"]]
        pad = lay.get("padding") or {}
        gap = lay.get("gap", 0)
        main = sum(m[0 if row else 1] for m in ms) + gap * (len(ms) - 1)
        cross = max(m[1 if row else 0] for m in ms)
        px = pad.get("left", 0) + pad.get("right", 0)
        py = pad.get("top", 0) + pad.get("bottom", 0)
        mw, mh = (main + px, cross + py) if row else (cross + px, main + py)
        w, h = (mw if w == "hug" else w), (mh if h == "hug" else h)
    assert isinstance(w, (int, float)) and isinstance(h, (int, float)), f"размер {n.get('id')} не число: {sz}"
    return w, h


def flex(lay, rect, kids):
    """Куда раскладка lay поставит детей kids в прямоугольнике rect (CSS flex на нужном подмножестве)."""
    X, Y, W, H = rect
    pad = lay.get("padding") or {}
    gap = lay.get("gap", 0)
    cross = (lay.get("align") or {}).get("cross", "start")
    row = lay["direction"] == "row"
    sizes = [measure(k) for k in kids]
    out = [None] * len(kids)
    if row:
        if lay.get("wrap"):
            room = W - pad.get("left", 0) - pad.get("right", 0)
            lines, used = [[]], 0
            for i, (w, _h) in enumerate(sizes):
                step = w if not lines[-1] else gap + w
                if lines[-1] and used + step > room + 0.005:
                    lines.append([])
                    used, step = 0, w
                lines[-1].append(i)
                used += step
        else:
            lines = [list(range(len(kids)))]
        y = Y + pad.get("top", 0)
        free_h = H - pad.get("top", 0) - pad.get("bottom", 0)
        for line in lines:
            lh = max(sizes[i][1] for i in line) if lay.get("wrap") else free_h
            x = X + pad.get("left", 0)
            for i in line:
                w, h = sizes[i]
                dy = {"start": 0, "center": (lh - h) / 2, "end": lh - h}[cross]
                out[i] = (x, y + dy, w, h)
                x += w + gap
            y += lh + gap
    else:
        assert not lay.get("wrap"), "перенос по колонке не выводится"
        x0 = X + pad.get("left", 0)
        free_w = W - pad.get("left", 0) - pad.get("right", 0)
        y = Y + pad.get("top", 0)
        for i, (w, h) in enumerate(sizes):
            dx = {"start": 0, "center": (free_w - w) / 2, "end": free_w - w}[cross]
            out[i] = (x0 + dx, y, w, h)
            y += h + gap
    return out


def abs_rects(root):
    """{id: (x, y, w, h)} ВСЕХ узлов канона: корень и дети с `box` — как есть, дети в потоке — по раскладке."""
    out = {}
    b = root["box"]
    stack = [(root, (b["x"], b["y"], b["w"], b["h"]))]
    while stack:
        node, rect = stack.pop()
        out[node["id"]] = rect
        kids = node.get("children") or []
        flow = [k for k in kids if "box" not in k]
        placed = {}
        if flow:
            assert node.get("layout"), f"{node['id']}: у детей нет box, а раскладки у контейнера нет"
            placed = dict(zip([k["id"] for k in flow], flex(node["layout"], rect, flow)))
        for k in kids:
            if "box" in k:
                kb = k["box"]
                stack.append((k, (kb["x"], kb["y"], kb["w"], kb["h"])))
            else:
                stack.append((k, placed[k["id"]]))
    return out


class Base(unittest.TestCase):
    def go(self, kids, w=400, h=400, rects=None, **kw):
        root = screen(kids, w, h)
        before = rects_before(root)
        rep = LY.infer_layout(root, rects, **kw)
        self.assertEqual(C.validate(C.new_envelope(root, "pixso")), [], "канон после вывода строго валиден")
        return root, rep, before

    def moves_nothing(self, before, root, tol=1.0):
        after = abs_rects(root)
        for i, r in before.items():
            self.assertIn(i, after, f"узел {i} пропал")
            a = after[i]
            for k, name in enumerate(("x", "y", "w", "h")):
                self.assertLessEqual(abs(a[k] - r[k]), tol + 1e-6, f"{i}: {name} {a[k]} вместо {r[k]}")

    def untouched(self, root, kids):
        for k in kids:
            self.assertIn("box", k, f"{k['id']}: бокс обязан остаться (контейнер не разобран)")
        self.assertNotIn("layout", root)

    def unproven(self, rep):
        return rep.get("unproven", {}).get("items", [])


# ── п. 1: стек с равным ритмом, порядок по позиции ────────────────────────────
class Stack(Base):
    def test_column_with_equal_rhythm(self):
        a, b, c = leaf("a", 20, 20, 160, 40), leaf("b", 20, 80, 160, 40), leaf("c", 20, 140, 160, 40)
        root, rep, before = self.go([b, a, c], 200, 300)               # порядок массива — не порядок документа
        self.assertEqual(root["layout"], {"direction": "column", "gap": 20, "inferred": True,
                                          "padding": {"top": 20, "right": 20, "bottom": 120, "left": 20}})
        self.assertEqual(ids(root), ["a", "b", "c"], "порядок определяется ПОЗИЦИЕЙ")
        self.assertEqual(rep, {"inferred": 1})
        self.moves_nothing(before, root)

    def test_row_with_equal_rhythm(self):
        kids = [leaf("c", 140, 20, 40, 160), leaf("a", 20, 20, 40, 160), leaf("b", 80, 20, 40, 160)]
        root, rep, before = self.go(kids, 300, 200)
        self.assertEqual(root["layout"], {"direction": "row", "gap": 20, "inferred": True,
                                          "padding": {"top": 20, "right": 120, "bottom": 20, "left": 20}})
        self.assertEqual(ids(root), ["a", "b", "c"])
        self.moves_nothing(before, root)

    def test_children_lose_box_but_keep_every_size_axis(self):
        a = leaf("a", 0, 0, 50, 30)
        del a["size"]["h"]                                              # ось без размера в size
        b = leaf("b", 0, 40, 50, 30)
        b["size"] = {"w": "hug", "h": 30}                               # намерение размера не теряется
        root, rep, before = self.go([a, b], 100, 100)
        kids = {k["id"]: k for k in root["children"]}
        self.assertNotIn("box", kids["a"])
        self.assertEqual(kids["a"]["size"], {"w": 50, "h": 30}, "недостающая ось берётся числом из бокса")
        self.assertEqual(kids["b"]["size"], {"w": "hug", "h": 30})
        self.assertEqual(root["box"], {"x": 0, "y": 0, "w": 100, "h": 100}, "бокс самого контейнера остаётся")
        self.assertEqual(root["size"], {"w": 100, "h": 100})

    def test_gap_zero_and_zero_padding_are_not_written(self):
        root, _rep, before = self.go([leaf("a", 0, 0, 100, 50), leaf("b", 0, 50, 100, 50)], 100, 100)
        self.assertEqual(root["layout"], {"direction": "column", "inferred": True})
        self.moves_nothing(before, root)

    def test_nested_containers_are_inferred_independently(self):
        inner = frame("in", 20, 20, 160, 100, [leaf("x", 30, 30, 40, 40), leaf("y", 100, 30, 40, 40)])
        root, rep, before = self.go([inner], 200, 200)
        self.assertEqual(rep["inferred"], 2)
        self.assertEqual(root["layout"]["direction"], "column")                # одиночный ребёнок → padding
        self.assertEqual(inner["layout"]["direction"], "row")
        self.assertNotIn("box", inner)
        self.moves_nothing(before, root)


# ── п. 2: cross-ось ───────────────────────────────────────────────────────────
class CrossAxis(Base):
    def row(self, specs, h=60):
        return self.go([leaf(f"k{i}", x, y, w, hh) for i, (x, y, w, hh) in enumerate(specs)], 400, h)

    def test_same_height_start_aligned_children_have_no_align_even_when_centered(self):
        # те же дети стоят и ровно по центру контейнера (центр 30), но «проще читать» — start с padding
        root, rep, before = self.row([(10, 10, 50, 40), (80, 10, 50, 40), (150, 10, 50, 40)])
        self.assertNotIn("align", root["layout"])
        self.assertEqual(root["layout"]["padding"]["top"], 10)
        self.moves_nothing(before, root)

    def test_start_with_different_heights_is_written_explicitly(self):
        root, rep, before = self.row([(10, 10, 50, 40), (80, 10, 50, 20), (150, 10, 50, 30)])
        self.assertEqual(root["layout"]["align"], {"cross": "start"})
        self.moves_nothing(before, root)

    def test_common_bottom_edge_is_end(self):
        root, rep, before = self.row([(10, 10, 50, 40), (80, 30, 50, 20), (150, 20, 50, 30)])
        self.assertEqual(root["layout"]["align"], {"cross": "end"})
        self.assertEqual(root["layout"]["padding"]["bottom"], 10)
        self.moves_nothing(before, root)

    def test_center_when_centering_reproduces_the_frame(self):
        root, rep, before = self.row([(10, 10, 50, 40), (80, 20, 50, 20), (150, 15, 50, 30)])
        self.assertEqual(root["layout"]["align"], {"cross": "center"})
        self.assertEqual((root["layout"]["padding"]["top"], root["layout"]["padding"]["bottom"]), (10, 10))
        self.moves_nothing(before, root)

    def test_center_is_not_claimed_when_the_centre_is_not_the_frames(self):
        # общий центр детей — 30, центр контейнера — 50: центрирование рамку НЕ воспроизводит
        kids = [leaf("k0", 10, 10, 50, 40), leaf("k1", 80, 20, 50, 20), leaf("k2", 150, 15, 50, 30)]
        root, rep, _ = self.go(kids, 400, 100)
        self.untouched(root, kids)
        self.assertEqual([(u["id"], u["reason"]) for u in self.unproven(rep)], [("root", "cross")])

    def test_cross_positions_not_start_center_end_are_refused(self):
        kids = [leaf("a", 10, 10, 50, 30), leaf("b", 80, 25, 50, 30), leaf("c", 150, 5, 50, 20)]
        root, rep, _ = self.go(kids, 400, 100)
        self.untouched(root, kids)
        self.assertEqual(self.unproven(rep)[0]["reason"], "cross")

    def test_cross_tolerance_boundary(self):
        root, rep, before = self.row([(10, 10, 50, 40), (80, 11, 50, 40)])    # верхи различаются ровно на допуск
        self.assertNotIn("align", root["layout"])
        self.moves_nothing(before, root)
        root, rep, _ = self.row([(10, 10, 50, 40), (80, 11.01, 50, 40)])
        self.assertNotIn("layout", root)
        self.assertEqual(self.unproven(rep)[0]["reason"], "cross")

    def test_column_cross_axis_is_horizontal(self):
        kids = [leaf("a", 20, 10, 100, 30), leaf("b", 60, 50, 20, 30), leaf("c", 10, 90, 140, 30)]
        root, rep, before = self.go(kids, 160, 130)          # центры по x: 70, 70, 80 → не совпадают
        self.assertEqual(self.unproven(rep)[0]["reason"], "cross")
        kids = [leaf("a", 20, 10, 100, 30), leaf("b", 60, 50, 20, 30), leaf("c", 40, 90, 60, 30)]
        root, rep, before = self.go(kids, 140, 130)          # центры по x: 70, 70, 70 и центр контейнера 70
        self.assertEqual(root["layout"]["align"], {"cross": "center"})
        self.moves_nothing(before, root)


# ── п. 3: одиночный ребёнок → padding ─────────────────────────────────────────
class LoneChild(Base):
    def test_lone_child_becomes_padding_of_the_parent(self):
        root, rep, before = self.go([leaf("a", 20, 30, 100, 50)], 600, 245)
        self.assertEqual(root["layout"], {"direction": "column", "inferred": True,
                                          "padding": {"top": 30, "right": 480, "bottom": 165, "left": 20}})
        self.assertNotIn("box", root["children"][0])
        self.assertEqual(rep, {"inferred": 1})
        self.moves_nothing(before, root)

    def test_lone_child_covering_the_container_has_no_padding_and_fill_hints(self):
        root, rep, before = self.go([leaf("bg", 0, 0, 400, 400)])
        self.assertEqual(root["layout"], {"direction": "column", "inferred": True})
        self.assertEqual(root["children"][0]["layoutHints"], {"h": "fill", "w": "fill"})
        self.assertEqual(rep["hints"], {"h": 1, "w": 1})

    def test_lone_child_that_does_not_fit_is_an_overlay_not_unproven(self):
        kid = leaf("a", 300, 10, 150, 50)                              # выпирает за край
        root, rep, _ = self.go([kid])
        self.untouched(root, [kid])
        self.assertEqual(rep, {}, "один абсолютный ребёнок — оверлей, а не раскладка: в unproven не идёт")


# ── п. 4: ритм допускает округление ───────────────────────────────────────────
class Rhythm(Base):
    def column(self, ys, h=40, total=300):
        return self.go([leaf(f"k{i}", 10, y, 100, h) for i, y in enumerate(ys)], 120, total)

    def test_ten_and_eleven_px_is_one_gap(self):
        root, rep, before = self.column([0, 50, 101])                  # зазоры 10 и 11
        self.assertEqual(root["layout"]["gap"], 10.5)
        self.assertNotIn("groups", rep)
        self.moves_nothing(before, root)

    def test_an_error_of_exactly_one_pixel_is_accepted(self):
        root, rep, before = self.column([0, 50, 102])                  # зазоры 10 и 12 → gap 11, ошибка ровно 1
        self.assertEqual(root["layout"]["gap"], 11)
        self.moves_nothing(before, root, tol=1.0)

    def test_a_real_break_is_not_one_gap(self):
        root, rep, _ = self.column([0, 50, 103])                       # зазоры 10 и 13: дрейф 1.5, разрыв не > 1.3×
        self.assertNotIn("layout", root)
        item = self.unproven(rep)[0]
        self.assertEqual((item["id"], item["reason"]), ("root", "rhythm"))
        self.assertIn("10…13", item["detail"])

    def test_the_tolerance_gates_acceptance(self):
        self.assertIn("layout", self.column([0, 50, 102])[0])
        kids = [leaf(f"k{i}", 10, y, 100, 40) for i, y in enumerate([0, 50, 102])]
        root = screen(kids, 120, 300)
        rep = LY.infer_layout(root, tolerance=0.2)
        self.assertNotIn("layout", root, "с допуском 0.2 тот же макет не доказан")
        self.assertEqual(self.unproven(rep)[0]["reason"], "rhythm")


# ── п. 5: две величины зазора → синтетические группы ──────────────────────────
class Grouping(Base):
    def test_two_gap_magnitudes_make_a_synthetic_group(self):
        kids = [leaf("t", 0, 0, 200, 20), leaf("s", 0, 24, 200, 16), leaf("btn", 0, 80, 200, 40)]
        root, rep, before = self.go(kids, 200, 300)
        top = root["children"]
        self.assertEqual([k["id"] for k in top], ["inferred:t+s", "btn"])
        group = top[0]
        self.assertEqual((group["kind"], group["name"]), ("frame", "Group"))
        self.assertEqual(group["size"], {"w": "hug", "h": "hug"})
        self.assertEqual(group["layout"], {"direction": "column", "gap": 4, "inferred": True})
        self.assertEqual(ids(group), ["t", "s"], "группа — настоящий элемент-обёртка с детьми")
        self.assertNotIn("box", group)
        self.assertEqual(root["layout"]["gap"], 40)
        self.assertEqual(rep["groups"], 1)
        self.moves_nothing(before, root)

    def test_a_third_magnitude_nests_instead_of_flattening(self):
        ys = {"a": 0, "b": 24, "c": 56, "d": 80, "e": 140, "f": 164}   # зазоры 4, 12, 4, 40, 4
        kids = [leaf(k, 0, y, 100, 20) for k, y in ys.items()]
        root, rep, before = self.go(kids, 100, 200)
        self.assertEqual(ids(root), ["inferred:a+b+c+d", "inferred:e+f"])
        abcd, ef = root["children"]
        self.assertEqual(ids(abcd), ["inferred:a+b", "inferred:c+d"], "12-px зазор вложен, а не сплющен в 4-px группы")
        self.assertEqual((root["layout"]["gap"], abcd["layout"]["gap"], ef["layout"]["gap"]), (40, 12, 4))
        self.assertEqual(abcd["children"][0]["layout"]["gap"], 4)
        self.assertEqual(rep["groups"], 4)
        self.moves_nothing(before, root)

    def test_separators_of_slightly_different_size_are_one_magnitude(self):
        ys = {"a": 0, "b": 24, "c": 84, "d": 108, "e": 169, "f": 193}      # зазоры 4, 40, 4, 41, 4
        kids = [leaf(k, 0, y, 100, 20) for k, y in ys.items()]
        root, rep, before = self.go(kids, 100, 230)
        self.assertEqual(ids(root), ["inferred:a+b", "inferred:c+d", "inferred:e+f"])
        self.assertEqual(root["layout"]["gap"], 40.5, "40 и 41 — один разделительный gap, как и 10 с 11")
        self.moves_nothing(before, root)

    def row8(self, big):
        xs, x = [], 0
        for g in (10, 10, 10, big, 10, 10, 10):
            xs.append(x)
            x += 20 + g
        xs.append(x)
        return self.go([leaf(f"k{i}", x, 10, 20, 20) for i, x in enumerate(xs)], 300, 40)

    def test_separator_must_exceed_the_grouping_gap_more_than_1_3_times(self):
        root, rep, _ = self.row8(13)                                    # 13 = 1.3 × 10 — не БОЛЕЕ
        self.assertNotIn("layout", root)
        self.assertEqual(self.unproven(rep)[0]["reason"], "rhythm")
        root, rep, before = self.row8(14)
        self.assertEqual(rep["groups"], 2)
        self.assertEqual(len(root["children"]), 2)
        self.moves_nothing(before, root)

    def test_gap_difference_within_the_tolerance_is_noise_not_a_separator(self):
        # зазоры 0,0,0,1,1 и 1.4,1.4,1.4: 1.4 > 1.3×1, но больше на 0.4 < допуска — это шум округления
        gaps, x, xs = [0, 0, 0, 1, 1, 1.4, 1.4, 1.4], 0, [0]
        for g in gaps:
            x += 10 + g
            xs.append(x)
        root, rep, _ = self.go([leaf(f"k{i}", x, 5, 10, 10) for i, x in enumerate(xs)], 200, 20)
        self.assertNotIn("layout", root)
        self.assertNotIn("groups", rep)
        self.assertEqual(self.unproven(rep)[0]["reason"], "rhythm")

    def test_group_members_keep_the_cross_alignment_of_the_group(self):
        # заголовок и подпись — по нижней кромке; кнопка далеко
        kids = [leaf("t", 0, 0, 100, 30), leaf("s", 110, 10, 60, 20), leaf("btn", 260, 0, 100, 30)]
        root, rep, before = self.go(kids, 360, 30)
        group = root["children"][0]
        self.assertEqual(group["layout"]["align"], {"cross": "end"})
        self.moves_nothing(before, root)


# ── п. 6: решётки ─────────────────────────────────────────────────────────────
class Lattices(Base):
    def cards(self, lines, w=100, h=50, gx=20, gy=20, x0=20, y0=20, widths=None):
        kids, n = [], 0
        for r, count in enumerate(lines):
            for c in range(count):
                cw = widths[n] if widths else w
                kids.append(leaf(f"c{r}{c}", x0 + c * (w + gx), y0 + r * (h + gy), cw, h))
                n += 1
        return kids

    def test_equal_gap_both_ways_is_one_wrapping_row(self):
        root, rep, before = self.go(self.cards([3, 3]), 380, 160)
        self.assertEqual(root["layout"], {"direction": "row", "wrap": True, "gap": 20, "inferred": True,
                                          "padding": {"top": 20, "right": 20, "bottom": 20, "left": 20}})
        self.assertEqual(ids(root), ["c00", "c01", "c02", "c10", "c11", "c12"])
        self.assertEqual(root["layoutHints"], {"grid": {"confidence": "high", "columns": 3}},
                         "у переноса space-between не бывает, align у ряда одинаковой высоты не пишется")
        self.assertEqual(rep["wraps"], 1)
        self.assertNotIn("groups", rep)
        self.moves_nothing(before, root)

    def test_a_short_last_line_wraps_too(self):
        root, rep, before = self.go(self.cards([3, 3, 2]), 380, 230)
        self.assertTrue(root["layout"]["wrap"])
        self.assertEqual(len(root["children"]), 8)
        self.assertEqual(root["layoutHints"]["grid"], {"confidence": "high", "columns": 3})
        self.moves_nothing(before, root)

    def test_different_gaps_stay_nested_rows(self):
        root, rep, before = self.go(self.cards([3, 3], gy=40), 380, 180)
        self.assertNotIn("wrap", root["layout"])
        self.assertEqual(root["layout"]["direction"], "column")
        self.assertEqual(root["layout"]["gap"], 40)
        self.assertEqual(ids(root), ["inferred:c00+c01+c02", "inferred:c10+c11+c12"])
        for row in root["children"]:
            self.assertEqual(row["layout"], {"direction": "row", "gap": 20, "inferred": True})
        self.assertNotIn("wraps", rep)
        self.assertNotIn("grid", root.get("layoutHints", {}), "группы-ряды не равны решётке: grid-кандидата нет")
        self.moves_nothing(before, root)

    def test_a_column_of_full_weight_blocks_is_not_a_wrapping_row(self):
        kids = [leaf(f"b{i}", 20, 20 + i * 70, 360, 50) for i in range(3)]
        root, rep, before = self.go(kids, 400, 240)
        self.assertEqual(root["layout"]["direction"], "column")
        self.assertNotIn("wrap", root["layout"])
        self.moves_nothing(before, root)

    def test_a_short_middle_line_cannot_be_a_wrap_and_stays_nested(self):
        # 3 + 2 + 3: в узкую вторую строку «влез» бы первый из третьей — реплей переноса не сходится
        root, rep, before = self.go(self.cards([3, 2, 3]), 380, 230)
        self.assertNotIn("wrap", root["layout"])
        self.assertEqual(rep["groups"], 3)
        self.moves_nothing(before, root)

    def test_a_single_block_line_first_is_a_column_not_a_wrap(self):
        kids = [leaf("A", 20, 20, 340, 50), leaf("B", 20, 90, 160, 50), leaf("C", 200, 90, 160, 50)]
        root, rep, before = self.go(kids, 380, 160)
        self.assertNotIn("wrap", root["layout"])
        self.assertEqual(ids(root), ["A", "inferred:B+C"])
        self.moves_nothing(before, root)

    def test_wrap_line_widths_survive_float_noise(self):
        # 3 × 33.34 + 2 × 10: сумма в другом порядке отличается на 1e-14 — строка всё равно «влезает»
        kids = [leaf(f"c{r}{c}", 10 + c * 43.34, 10 + r * 30, 33.34, 20) for r in range(2) for c in range(3)]
        root, rep, before = self.go(kids, 600, 70)
        self.assertTrue(root["layout"]["wrap"])
        self.moves_nothing(before, root)

    def test_wrap_of_unequal_items_is_a_wrap_without_a_grid_hint(self):
        kids = [leaf("t1", 10, 10, 80, 30), leaf("t2", 100, 10, 120, 30), leaf("t3", 230, 10, 100, 30),
                leaf("t4", 10, 50, 100, 30), leaf("t5", 120, 50, 60, 30)]
        root, rep, before = self.go(kids, 340, 100)
        self.assertTrue(root["layout"]["wrap"])
        self.assertEqual(root["layout"]["gap"], 10)
        self.assertNotIn("layoutHints", root)
        self.moves_nothing(before, root)

    def test_gaps_that_differ_by_more_than_the_tolerance_are_not_one_gap(self):
        # по горизонтали 20, по вертикали 21.5: реплей с средним зазором 20.75 сошёлся бы, но «одинаковый зазор
        # в обе стороны» это не он — разные зазоры остаются вложенными рядами
        kids = [leaf("A", 20, 20, 100, 50), leaf("B", 140, 20, 100, 50), leaf("C", 20, 91.5, 100, 50)]
        root, rep, before = self.go(kids, 260, 170)
        self.assertNotIn("wrap", root["layout"])
        self.assertEqual(ids(root), ["inferred:A+B", "C"])
        self.assertEqual(root["layout"]["gap"], 21.5)
        self.moves_nothing(before, root)

    def test_lattice_with_misaligned_line_starts_is_not_a_wrap(self):
        kids = self.cards([3, 3])
        for k in kids[3:]:
            k["box"]["x"] += 7                                           # вторая строка начинается правее
        root, rep, _ = self.go(kids, 380, 160)
        self.assertNotIn("wrap", root.get("layout", {}))


# ── п. 7: отказы ──────────────────────────────────────────────────────────────
class Refusals(Base):
    def test_overlapping_children_stay_as_they_are(self):
        kids = [leaf("a", 10, 10, 100, 100), leaf("b", 60, 60, 100, 100)]
        root, rep, _ = self.go(kids)
        self.untouched(root, kids)
        self.assertEqual(self.unproven(rep), [{"id": "root", "reason": "overlap", "detail": "a × b"}])
        self.assertEqual(rep["unproven"]["count"], 1)

    def test_overlap_within_the_tolerance_is_not_an_overlap(self):
        root, rep, before = self.go([leaf("a", 0, 0, 100, 50), leaf("b", 99.5, 0, 100, 50)], 200, 50)
        self.assertEqual(root["layout"]["direction"], "row")
        self.moves_nothing(before, root)

    def test_overlap_within_the_tolerance_holds_for_a_column_too_and_beyond_it_refuses(self):
        root, rep, before = self.go([leaf("a", 0, 0, 100, 50), leaf("b", 0, 49.5, 100, 50)], 100, 120)
        self.assertEqual(root["layout"]["direction"], "column")
        self.moves_nothing(before, root)
        kids = [leaf("a", 0, 0, 100, 50), leaf("b", 0, 48.5, 100, 50)]       # перекрытие 1.5 > допуска
        root, rep, _ = self.go(kids, 100, 120)
        self.untouched(root, kids)
        self.assertEqual(self.unproven(rep)[0]["reason"], "overlap")

    def test_tolerance_boundaries_of_outside_and_overflow(self):
        root, rep, before = self.go([leaf("a", -1, 10, 100, 30), leaf("b", 110, 10, 100, 30)], 400, 50)
        self.assertEqual(root["layout"]["direction"], "row", "начало на допуск левее кромки — ещё внутри")
        self.moves_nothing(before, root)
        kids = [leaf("a", -1.5, 10, 100, 30), leaf("b", 110, 10, 100, 30)]
        root, rep, _ = self.go(kids, 400, 50)
        self.assertEqual(self.unproven(rep)[0]["reason"], "outside")
        root, rep, before = self.go([leaf("a", 10, 10, 100, 30), leaf("b", 300, 10, 101, 30)], 400, 50)
        self.assertEqual(root["layout"]["direction"], "row", "конец на допуск правее кромки — ещё внутри")
        kids = [leaf("a", 10, 10, 100, 30), leaf("b", 300, 10, 101.5, 30)]
        root, rep, _ = self.go(kids, 400, 50)
        self.assertEqual(self.unproven(rep)[0]["reason"], "overflow")

    def test_container_with_its_own_layout_is_not_touched_with_its_pinned_children(self):
        flow_a = {"id": "fa", "kind": "rect", "size": {"w": 50, "h": 20}}
        flow_b = {"id": "fb", "kind": "rect", "size": {"w": 50, "h": 20}}
        pinned = leaf("pin", 300, 10, 40, 40)
        root = screen([flow_a, flow_b, pinned], 400, 100)
        root["layout"] = {"direction": "column", "stated": True, "gap": 8}
        snapshot = copy.deepcopy(root)
        rep = LY.infer_layout(root)
        self.assertEqual(root, snapshot, "объявленная раскладка и pinned-дети — как были")
        self.assertEqual(rep, {}, "объявленный контейнер молчит: в unproven его нет")

    def test_descendants_of_a_declared_container_are_still_inferred(self):
        card = frame("card", 20, 20, 200, 100, [leaf("l", 50, 60, 100, 20)])
        del card["box"]                                                  # в потоке чужой раскладки box нет
        root = screen([card], 240, 140)
        root["layout"] = {"direction": "column", "stated": True}
        before = {"l": (50, 60, 100, 20)}
        rep = LY.infer_layout(root, {"card": (20, 20, 200, 100)})
        self.assertEqual(card["layout"], {"direction": "column", "inferred": True,
                                          "padding": {"top": 40, "right": 70, "bottom": 40, "left": 30}})
        self.assertEqual(rep["inferred"], 1)
        self.assertNotIn("box", card["children"][0])
        self.assertEqual(abs_rects.__name__, "abs_rects")                # помощник на месте
        self.assertEqual(before["l"][:2], (50, 60))

    def test_child_starting_outside_the_container(self):
        kids = [leaf("a", -50, 10, 100, 30), leaf("b", 80, 10, 100, 30)]
        root, rep, _ = self.go(kids)
        self.untouched(root, kids)
        self.assertEqual(self.unproven(rep), [{"id": "root", "reason": "outside", "detail": "a"}])
        kids = [leaf("a", 10, 10, 100, 30), leaf("b", 450, 10, 100, 30)]
        root, rep, _ = self.go(kids)
        self.assertEqual(self.unproven(rep)[0], {"id": "root", "reason": "outside", "detail": "b"})

    def test_content_overflowing_the_trailing_edge(self):
        kids = [leaf("a", 10, 10, 100, 30), leaf("b", 300, 10, 150, 30)]
        root, rep, _ = self.go(kids)
        self.untouched(root, kids)
        self.assertEqual(self.unproven(rep), [{"id": "root", "reason": "overflow", "detail": "b"}])

    def test_child_without_a_box(self):
        nobox = {"id": "x", "kind": "rect"}
        root = screen([leaf("a", 10, 10, 50, 50), nobox])
        rep = LY.infer_layout(root)
        self.assertNotIn("layout", root)
        self.assertEqual(self.unproven(rep), [{"id": "root", "reason": "no-box", "detail": "x"}])

    def test_container_without_a_rectangle_needs_the_adapters_one(self):
        def tree():
            card = frame("card", 0, 0, 200, 100, [leaf("a", 10, 10, 50, 50), leaf("b", 100, 10, 50, 50)])
            del card["box"]
            root = screen([card], 240, 140)
            root["layout"] = {"direction": "column", "stated": True}
            return root, card
        root, card = tree()
        rep = LY.infer_layout(root)
        self.assertNotIn("layout", card)
        self.assertEqual([(u["id"], u["reason"]) for u in self.unproven(rep)], [("card", "no-geometry")])
        root, card = tree()
        rep = LY.infer_layout(root, {"card": (0, 0, 200, 100)})
        self.assertEqual(card["layout"]["direction"], "row")
        self.assertNotIn("unproven", rep)

    def test_unproven_list_is_capped_with_an_honest_counter(self):
        self.assertEqual(LY.UNPROVEN_LIST_LIMIT, 20, "размер списка в stats закреплён (как UNMATCHED_LIST_LIMIT)")
        boxes = []
        for i in range(25):
            boxes.append(frame(f"f{i}", 0, i * 100, 100, 100, [leaf(f"a{i}", 0, i * 100, 80, 80),
                                                                 leaf(f"b{i}", 40, i * 100 + 40, 60, 60)]))
        root = frame("root", 0, 0, 100, 100 * len(boxes), boxes)
        rep = LY.infer_layout(root)
        self.assertEqual(rep["unproven"]["count"], 25)
        self.assertEqual(len(rep["unproven"]["items"]), 20)
        self.assertEqual(rep["unproven"]["dropped"], 5)
        self.assertTrue(set(u["reason"] for u in rep["unproven"]["items"]) == {"overlap"})
        self.assertIn(rep["unproven"]["items"][0]["reason"], LY.REASONS)

    def test_things_that_are_not_containers_are_left_alone(self):
        pair = [leaf("a", 0, 0, 50, 50), leaf("b", 60, 0, 50, 50)]
        for kind, extra in (("instance", {"component": {"figmaName": "Btn", "childrenRole": "fallback"}}),
                            ("icon", {}), ("vector", {})):
            node = {"id": "n", "kind": kind, "box": {"x": 0, "y": 0, "w": 200, "h": 100}, "children": copy.deepcopy(pair)}
            node.update(extra)
            root = screen([node], 200, 100)
            rep = LY.infer_layout(root)
            self.assertNotIn("layout", node, kind)
            self.assertEqual(rep.get("inferred"), 1, f"{kind}: разобран только корень (одиночный ребёнок)")
            self.assertNotIn("groups", rep)

    def test_occluded_subtrees_and_library_black_boxes_are_skipped(self):
        pair = lambda p: [leaf(p + "a", 0, 0, 50, 50), leaf(p + "b", 60, 0, 50, 50)]  # noqa: E731
        hidden = frame("hid", 0, 0, 200, 100, pair("h"), occluded=True)
        lib = {"id": "lib", "kind": "instance", "box": {"x": 0, "y": 100, "w": 200, "h": 100},
               "component": {"figmaName": "Table", "childrenRole": "content"},
               "children": [frame("anat", 0, 100, 200, 100, pair("l"))]}
        wrap = frame("inferred:roots:1", 0, 200, 200, 100, pair("w"))
        root = screen([hidden, lib, wrap], 200, 300)
        rep = LY.infer_layout(root)
        for n in (hidden, lib["children"][0], wrap):
            self.assertNotIn("layout", n, n["id"])
        self.assertEqual((rep.get("inferred"), rep.get("groups"), rep.get("unproven")), (1, None, None),
                         "разобран только корень; закрытое, ящик и обёртка адаптера — нет")

    def test_second_run_over_an_inferred_tree_changes_nothing(self):
        root, rep, _ = self.go([leaf("a", 20, 20, 160, 40), leaf("b", 20, 80, 160, 40)], 200, 200)
        again = copy.deepcopy(root)
        self.assertEqual(LY.infer_layout(root), {})
        self.assertEqual(root, again)


# ── п. 8.2: доказательство реплеем ────────────────────────────────────────────
class Proof(Base):
    def pair(self):
        return [leaf("a", 10, 10, 50, 50), leaf("b", 80, 10, 50, 50)]

    def test_the_replay_is_the_judge(self):
        orig = LY._Run._gaps
        lie = lambda self, items, d: [g + 7 for g in orig(self, items, d)]  # noqa: E731 — расчёт зазора соврал
        kids = self.pair()
        root = screen(kids, 200, 100)
        with mock.patch.object(LY._Run, "_gaps", lie):
            rep = LY.infer_layout(root)
        self.untouched(root, kids)
        item = self.unproven(rep)[0]
        self.assertEqual((item["reason"], item["id"]), ("replay", "root"))
        self.assertTrue(item["detail"].startswith("b: сдвиг 7.00"), item["detail"])
        # без судьи та же ложь ушла бы в канон: значит отказал именно реплей
        root = screen(self.pair(), 200, 100)
        with mock.patch.object(LY._Run, "_gaps", lie), mock.patch.object(LY._Run, "_prove", lambda *a, **k: None):
            LY.infer_layout(root)
        self.assertEqual(root["layout"]["gap"], 27)

    def test_every_inferred_layout_is_marked_inferred_and_never_stated(self):
        kids = [leaf("t", 0, 0, 200, 20), leaf("s", 0, 24, 200, 16), leaf("btn", 0, 80, 200, 40)]
        root, rep, _ = self.go(kids, 200, 300)
        seen = [n["layout"] for n in C.iter_nodes(root) if "layout" in n]
        self.assertEqual(len(seen), 2)
        for lay in seen:
            self.assertIs(lay["inferred"], True)
            self.assertNotIn("stated", lay)

    def test_output_is_deterministic_and_independent_of_array_order(self):
        def build(order):
            ys = {"a": 0, "b": 24, "c": 56, "d": 80, "e": 140, "f": 164}
            return screen([leaf(k, 0, ys[k], 100, 20) for k in order], 100, 200)
        outs = []
        for order in ("abcdef", "fedcba", "cafebd"):
            root = build(order)
            LY.infer_layout(root)
            outs.append(C.dumps(root))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])

    def test_numbers_are_the_canons_two_decimals(self):
        kids = [leaf("a", 0, 0, 100, 33.333), leaf("b", 0, 43.337, 100, 33.333)]
        root, rep, before = self.go(kids, 100, 100)
        self.assertEqual(root["layout"]["gap"], 10.01, "зазор считается по числам канона (43.34 − 33.33), не по сырым")
        self.assertEqual(root["children"][0]["size"], {"w": 100, "h": 33.333}, "числа детей не трогаются")
        self.moves_nothing(before, C.normalize(root))


# ── layoutHints: только кандидаты ─────────────────────────────────────────────
class Hints(Base):
    def test_fill_for_a_child_that_covers_the_container_edge_to_edge(self):
        header, body = leaf("h", 0, 0, 400, 40), leaf("b", 0, 40, 400, 60)
        root, rep, _ = self.go([header, body], 400, 100)
        self.assertEqual(header["layoutHints"], {"w": "fill"})
        self.assertEqual(body["layoutHints"], {"w": "fill"})
        self.assertNotIn("main", root.get("layoutHints", {}), "зазор нулевой — раскидывать нечего")
        self.assertEqual(rep["hints"], {"w": 2})

    def test_fill_needs_both_edges_not_one(self):
        a, b = leaf("a", 0, 0, 200, 40), leaf("b", 200, 0, 200, 40)      # a прижат к левой кромке, b — к правой
        root, rep, _ = self.go([a, b], 400, 100)
        self.assertNotIn("layoutHints", a)
        self.assertNotIn("layoutHints", b)
        self.assertNotIn("hints", rep)

    def test_hints_stay_on_an_unexplained_container_with_absolute_children(self):
        bg = leaf("bg", 0, 0, 400, 300)
        title = leaf("title", 20, 20, 100, 30)
        root, rep, _ = self.go([title, bg], 400, 300)
        self.untouched(root, [title, bg])
        self.assertEqual(bg["layoutHints"], {"h": "fill", "w": "fill"})
        self.assertNotIn("layoutHints", title)
        self.assertEqual(self.unproven(rep)[0]["reason"], "overlap")

    def test_hints_do_not_change_geometry_or_size(self):
        bg = leaf("bg", 0, 0, 400, 300)
        root, rep, _ = self.go([bg], 400, 300)
        self.assertEqual(bg["size"], {"w": 400, "h": 300}, "fill — кандидат: размер остаётся числом")
        self.assertEqual(root["layout"], {"direction": "column", "inferred": True})
        self.assertNotIn("align", root["layout"])

    def test_grid_candidate_for_a_row_of_equal_items(self):
        kids = [leaf(f"c{i}", 10 + i * 60, 10, 50, 50) for i in range(3)]
        root, rep, _ = self.go(kids, 400, 70)
        self.assertEqual(root["layoutHints"]["grid"], {"confidence": "medium", "columns": 3})
        self.assertEqual(rep["hints"]["grid"], 1)

    def test_no_grid_for_a_pair_unequal_items_or_a_column(self):
        for kids, h in (([leaf("a", 10, 10, 50, 50), leaf("b", 70, 10, 50, 50)], 70),
                        ([leaf("a", 10, 10, 50, 50), leaf("b", 70, 10, 80, 50), leaf("c", 160, 10, 50, 50)], 70),
                        ([leaf(f"c{i}", 10, 10 + i * 60, 50, 50) for i in range(3)], 200)):
            root, rep, _ = self.go(kids, 400, h)
            self.assertNotIn("grid", root.get("layoutHints", {}))

    def test_space_between_candidate_only_when_pressed_to_both_edges(self):
        root, rep, _ = self.go([leaf("a", 20, 10, 60, 30), leaf("b", 320, 10, 60, 30)], 400, 50)
        self.assertEqual(root["layoutHints"]["main"], "space-between")
        self.assertNotIn("align", root["layout"].get("align", {}) and {} or {})   # align.main не пишется
        self.assertNotIn("main", root["layout"].get("align", {}))
        root, rep, _ = self.go([leaf("a", 20, 10, 60, 30), leaf("b", 300, 10, 60, 30)], 400, 50)
        self.assertNotIn("main", root.get("layoutHints", {}), "ведущий и замыкающий отступы различаются")

    def test_no_space_between_without_a_gap(self):
        root, rep, _ = self.go([leaf("a", 0, 0, 200, 30), leaf("b", 200, 0, 200, 30)], 400, 30)
        self.assertNotIn("main", root.get("layoutHints", {}))


# ── id синтетических групп ────────────────────────────────────────────────────
class GroupIds(Base):
    def test_id_is_made_of_the_members_and_stable_between_runs(self):
        outs = []
        for _ in range(2):
            root = screen([leaf("t", 0, 0, 200, 20), leaf("s", 0, 24, 200, 16), leaf("btn", 0, 80, 200, 40)], 200, 300)
            LY.infer_layout(root)
            outs.append([n["id"] for n in C.iter_nodes(root)])
        self.assertEqual(outs[0], outs[1])
        self.assertIn("inferred:t+s", outs[0])

    def test_long_member_lists_are_abbreviated_first_last_count(self):
        kids = [leaf(f"i{k}", k * 24, 0, 20, 20) for k in range(1, 6)] + [leaf("far", 300, 0, 20, 20)]
        root, rep, before = self.go(kids, 340, 20)
        self.assertEqual(root["children"][0]["id"], "inferred:i1..i5#5")
        self.moves_nothing(before, root)

    def test_a_group_id_never_collides_with_a_source_id(self):
        clash = leaf("inferred:t+s", 10, 10, 50, 50)
        other = frame("other", 0, 300, 100, 100, [clash])
        kids = [leaf("t", 0, 0, 200, 20), leaf("s", 0, 24, 200, 16), leaf("btn", 0, 80, 200, 40), other]
        root = screen(kids, 200, 400)
        LY.infer_layout(root)
        all_ids = [n["id"] for n in C.iter_nodes(root)]
        self.assertEqual(len(all_ids), len(set(all_ids)), "id уникальны")
        self.assertIn("inferred:t+s~2", all_ids)
        self.assertEqual(C.validate(C.new_envelope(root, "pixso")), [])


# ── DSL: проводка в normalize_doc и F6 ────────────────────────────────────────
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


def dsl(*roots):
    return {"dsl": {"pixTreeDslNodes": list(roots), "pixComponentTreeDslNodes": []}}


def landing(order=None, rng=None):
    header = n("hd", name="Header", w=1280, h=64, kids=[
        n("logo", "RECTANGLE", name="Logo", w=100, h=32, left=24, top=16),
        n("la", "PARAGRAPH", name="Link A", w=50, h=16, left=200, top=24, nodeText="Продукт"),
        n("lb", "PARAGRAPH", name="Link B", w=50, h=16, left=270, top=24, nodeText="Цены"),
        n("lc", "PARAGRAPH", name="Link C", w=50, h=16, left=340, top=24, nodeText="Блог"),
        n("cta", "RECTANGLE", name="Cta", w=156, h=40, left=1100, top=12)])
    hero = n("hero", name="Hero", w=1280, h=400, top=64, kids=[
        n("title", "PARAGRAPH", name="Title", w=600, h=48, left=80, top=120, nodeText="Заголовок"),
        n("hbtn", "RECTANGLE", name="Hero button", w=200, h=48, left=80, top=200),
        n("hbg", "RECTANGLE", name="Hero bg", w=1280, h=400)])
    cards = n("cards", name="Cards", w=1280, h=280, top=464, kids=[
        n(f"card{r}{c}", "RECTANGLE", name=f"Card {r}{c}", w=360, h=100, left=60 + 400 * c, top=20 + 140 * r)
        for r in range(2) for c in range(3)])
    footer = n("ft", name="Footer", w=1280, h=56, top=744, kids=[
        n("copy", "PARAGRAPH", name="Copyright", w=200, h=20, left=60, top=18, nodeText="© 2026")])
    root = n("root", name="Landing", w=1280, h=800, kids=[header, hero, cards, footer])
    if rng is not None:
        for node in _walk(root):
            if node.get("childNode"):
                rng.shuffle(node["childNode"])
    return dsl(root)


def _walk(node):
    yield node
    for k in node.get("childNode") or []:
        yield from _walk(k)


def dsl_rects(doc):
    """Абсолютные боксы узлов по DSL (left/top — от родителя)."""
    out = {}

    def go(node, ox, oy):
        x, y = ox + node.get("left", 0), oy + node.get("top", 0)
        out[node["guid"]] = (x, y, node["width"], node["height"])
        for k in node.get("childNode") or []:
            go(k, x, y)
    for r in doc["dsl"]["pixTreeDslNodes"]:
        go(r, 0, 0)
    return out


def canon_bytes(env):
    return C.dumps(C.normalize(env))


class Golden(Base):
    """F6: макет без auto-layout."""

    def setUp(self):
        self.doc = landing()
        self.env = PX.normalize_doc(self.doc, infer_layout=True)
        self.root = self.env["root"]
        self.nodes = C.index_nodes(self.root)

    def test_canon_is_valid_and_every_node_replays_within_one_pixel(self):
        self.assertEqual(C.validate(C.normalize(self.env)), [])
        want = dsl_rects(self.doc)
        got = abs_rects(self.root)
        self.assertEqual(set(want) - set(got), set(), "ни один узел макета не потерян")
        for guid, r in want.items():
            for k in range(4):
                self.assertLessEqual(abs(got[guid][k] - r[k]), 1.0 + 1e-6, f"{guid}: {got[guid]} вместо {r}")

    def test_header_with_three_gap_magnitudes_is_nested_groups_and_space_between_candidate(self):
        header = self.nodes["hd"]
        self.assertEqual(ids(header), ["inferred:logo+la+lb+lc", "cta"])
        outer = header["children"][0]
        self.assertEqual(ids(outer), ["logo", "inferred:la+lb+lc"])
        self.assertEqual(outer["children"][1]["layout"], {"direction": "row", "gap": 20, "inferred": True})
        self.assertEqual(header["layout"]["align"], {"cross": "center"})
        self.assertEqual(header["layoutHints"]["main"], "space-between")

    def test_cards_are_one_wrapping_row_and_a_grid_candidate(self):
        cards = self.nodes["cards"]
        self.assertTrue(cards["layout"]["wrap"])
        self.assertEqual(cards["layout"]["gap"], 40)
        self.assertEqual(cards["layoutHints"]["grid"], {"confidence": "high", "columns": 3})

    def test_unexplained_container_stays_absolute_with_hints(self):
        hero = self.nodes["hero"]
        self.assertNotIn("layout", hero)
        for k in hero["children"]:
            self.assertIn("box", k, "необъяснимый контейнер: дети остаются абсолютными")
        self.assertEqual(self.nodes["hbg"]["layoutHints"], {"h": "fill", "w": "fill"})
        lay = self.env["stats"]["layout"]
        self.assertEqual(lay["unproven"]["count"], 1)
        self.assertEqual(lay["unproven"]["items"], [{"id": "hero", "reason": "overlap", "detail": "hbg × title"}])

    def test_sections_form_a_column_of_full_width_blocks_and_lone_text_is_padding(self):
        self.assertEqual(self.root["layout"], {"direction": "column", "inferred": True})
        self.assertEqual(self.nodes["ft"]["layout"]["padding"], {"top": 18, "right": 1020, "bottom": 18, "left": 60})
        for sid in ("hd", "hero", "cards", "ft"):
            self.assertEqual(self.nodes[sid]["layoutHints"]["w"], "fill")

    def test_stats_count_the_groups_among_nodes_out(self):
        self.assertEqual(self.env["stats"]["nodesOut"], sum(1 for _ in C.iter_nodes(self.root)))
        lay = self.env["stats"]["layout"]
        self.assertEqual((lay["groups"], lay["wraps"]), (2, 1))

    def test_normalize_twice_is_byte_stable(self):
        again = PX.normalize_doc(landing(), infer_layout=True)
        self.assertEqual(canon_bytes(again), canon_bytes(self.env))

    def test_source_order_does_not_change_the_canon_or_the_group_ids(self):
        base = canon_bytes(self.env)
        for seed in (1, 2, 3):
            shuffled = PX.normalize_doc(landing(rng=random.Random(seed)), infer_layout=True)
            self.assertEqual(canon_bytes(shuffled), base, seed)

    def test_switched_off_the_tree_keeps_every_absolute_box(self):
        off = PX.normalize_doc(landing())
        self.assertNotIn("layout", off["stats"])
        for node in C.iter_nodes(off["root"]):
            if node["id"] != "root":
                self.assertIn("box", node, node["id"])
        self.assertNotIn("layout", off["root"])


class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def pull(self, doc=None):
        if doc is None:
            shutil.copyfile(str(FIXTURE), str(self.root / "dsl.json"))
        else:
            (self.root / "dsl.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        T.call("daiver_pixso_pull", self.root, {"track": "STOR-100", "dslPath": "dsl.json", "frameUrl": URL})

    def config(self, text):
        p = self.root / ".daiver" / "config.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def normalize(self):
        out = T.call("daiver_normalize", self.root, {"track": "STOR-100"})
        path = self.root / out["canonical"]
        return out, path.read_bytes(), json.loads(path.read_text(encoding="utf-8"))

    def test_modal_is_byte_identical_with_the_inference_on_and_off(self):
        self.pull()
        out_on, on, env_on = self.normalize()                            # по умолчанию layout.infer — on
        self.config("layout:\n  infer: off\n")
        out_off, off, env_off = self.normalize()
        self.assertEqual(on, off, "на макете с настоящим auto-layout модуль ничего не меняет")
        self.assertNotIn("layout", env_on["stats"])
        self.assertNotIn("layout", out_on)
        self.assertEqual(out_on["stats"], out_off["stats"])
        for node in C.iter_nodes(env_on["root"]):
            self.assertNotIn("layoutHints", node)
            self.assertNotIn("inferred", node.get("layout", {}))

    def test_switch_off_means_the_module_is_not_called(self):
        self.pull()
        self.config("layout:\n  infer: off\n")
        with mock.patch.object(LY, "infer_layout", side_effect=AssertionError("модуль вызван при layout.infer: off")):
            self.normalize()
        self.config("layout:\n  infer: false\n")
        with mock.patch.object(LY, "infer_layout", side_effect=AssertionError("модуль вызван при layout.infer: false")):
            self.normalize()

    def test_on_by_default_and_a_typo_does_not_switch_it_off(self):
        self.pull()
        for cfg in (None, "layout:\n  infer: on\n", "layout:\n  infer: of\n"):
            if cfg:
                self.config(cfg)
            with mock.patch.object(LY, "infer_layout", return_value={}) as m:
                self.normalize()
            self.assertEqual(m.call_count, 1, cfg)

    def test_normalize_doc_alone_does_not_infer_unless_asked(self):
        with mock.patch.object(LY, "infer_layout", side_effect=AssertionError("вызван")):
            PX.normalize_doc(landing())

    def test_response_carries_a_short_summary_and_the_list_stays_in_the_canon(self):
        self.pull(landing())
        out, _raw, env = self.normalize()
        self.assertEqual(out["layout"]["inferred"], 4)
        self.assertEqual(out["layout"]["groups"], 2)
        self.assertEqual(out["layout"]["unproven"], 1)
        self.assertIn("resolve", out["layout"]["next"])
        self.assertNotIn("layout", out["stats"], "список unproven не идёт через контекст модели")
        self.assertEqual(env["stats"]["layout"]["unproven"]["items"][0]["id"], "hero")

    def test_container_in_a_foreign_flow_gets_its_rectangle_from_the_adapter(self):
        card = n("card", name="Card", w=200, h=100, left=20, top=20, al={"autoLayoutByParent": True}, kids=[
            n("label", "PARAGRAPH", name="Label", w=100, h=20, left=30, top=40, nodeText="Привет")])
        al = {"stackMode": "VERTICAL", "autoLayoutPaddingTop": 20, "autoLayoutPaddingLeft": 20}
        doc = dsl(n("r", name="Screen", w=240, h=140, al=al, kids=[card]))
        env = PX.normalize_doc(doc, infer_layout=True)
        nodes = C.index_nodes(env["root"])
        self.assertNotIn("box", nodes["card"], "в потоке чужой раскладки box нет")
        self.assertEqual(nodes["card"]["layout"], {"direction": "column", "inferred": True,
                                                   "padding": {"top": 40, "right": 70, "bottom": 40, "left": 30}})
        self.assertNotIn("box", nodes["label"])
        self.assertTrue(env["root"]["layout"]["stated"], "объявленный корень не тронут")
        off = PX.normalize_doc(doc)
        self.assertEqual(C.index_nodes(off["root"])["label"]["box"], {"x": 50, "y": 60, "w": 100, "h": 20})


if __name__ == "__main__":
    unittest.main()
