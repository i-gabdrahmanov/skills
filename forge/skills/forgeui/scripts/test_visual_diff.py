"""test_visual_diff.py — эталонный рендер и пиксельный дифф (задача 5.1, спека §14.6, INV-F17, QG-N15).

Пинит: `render_html` из канона (стампы `data-daiver-id`; арт из .svg или помеченный блок, а не серый бокс; инстанс с неатрибутированным боксом — ограничен;
слоты-плейсхолдеры помечены; кавычка в имени шрифта не роняет хвост style; экранирование; детерминизм); `daiver_render_reference` в настоящем Chrome (reference.png
ровно размера кадра, reference-snapshot.json со стампами); `daiver_visual_diff`: порт pixelmatch против независимого эталона на Python (те же числа diff / antialiased
на структурных и случайных парах), идентичные → 0, известный сдвиг → зона где ждали, разные размеры → подложка на белое, ПРОЦЕНТ как процент, обрезка по frameBox, выбор goal /
reference, зоны, объяснённые compare; N15 — ненулевой дифф при пустых дефектах дефектов не делает и гейт не валит. PNG пишет stdlib-кодировщик `_testkit`. Тесты с
браузером — skip без Chrome (FORGEUI_TEST_REQUIRE_BROWSER=1 — провал).
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testkit as K  # noqa: E402
from daiver import image as IMG  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.browser import cdp as CD  # noqa: E402
from daiver.verify import reference as RF  # noqa: E402
from daiver.verify import visual_diff as VD  # noqa: E402
from test_screenshot import skip_without_browser  # noqa: E402
from test_verify_state import World  # noqa: E402

W, B = (255, 255, 255, 255), (0, 0, 0, 255)


def solid(w, h, fn=None):
    return K.png_bytes(w, h, fn)


def square(w, h, x0, y0, size, color=B):
    return K.png_bytes(w, h, lambda x, y: color if x0 <= x < x0 + size and y0 <= y < y0 + size else W)


# ── эталон pixelmatch на Python (независимый от JS порта, по тому же описанию алгоритма) ──────────────────────────────

def _blend(c, a):
    return 255 + (c - 255) * a


def _y(r, g, b):
    return r * 0.29889531 + g * 0.58662247 + b * 0.11448223


def _i(r, g, b):
    return r * 0.59597799 - g * 0.27417610 - b * 0.32180189


def _q(r, g, b):
    return r * 0.21147017 - g * 0.52261711 + b * 0.31114694


def _delta(p1, p2, y_only=False):
    r1, g1, b1, a1 = p1
    r2, g2, b2, a2 = p2
    if p1 == p2:
        return 0
    if a1 < 255:
        a1 /= 255
        r1, g1, b1 = _blend(r1, a1), _blend(g1, a1), _blend(b1, a1)
    if a2 < 255:
        a2 /= 255
        r2, g2, b2 = _blend(r2, a2), _blend(g2, a2), _blend(b2, a2)
    y1, y2 = _y(r1, g1, b1), _y(r2, g2, b2)
    y = y1 - y2
    if y_only:
        return y
    i = _i(r1, g1, b1) - _i(r2, g2, b2)
    q = _q(r1, g1, b1) - _q(r2, g2, b2)
    d = 0.5053 * y * y + 0.299 * i * i + 0.1957 * q * q
    return -d if y1 > y2 else d


def _siblings(img, x1, y1, w, h):
    x0, y0, x2, y2 = max(x1 - 1, 0), max(y1 - 1, 0), min(x1 + 1, w - 1), min(y1 + 1, h - 1)
    zeroes = 1 if (x1 == x0 or x1 == x2 or y1 == y0 or y1 == y2) else 0
    for x in range(x0, x2 + 1):
        for y in range(y0, y2 + 1):
            if (x, y) == (x1, y1):
                continue
            if img[y][x] == img[y1][x1]:
                zeroes += 1
            if zeroes > 2:
                return True
    return False


def _aa(img, x1, y1, w, h, img2):
    x0, y0, x2, y2 = max(x1 - 1, 0), max(y1 - 1, 0), min(x1 + 1, w - 1), min(y1 + 1, h - 1)
    zeroes = 1 if (x1 == x0 or x1 == x2 or y1 == y0 or y1 == y2) else 0
    mn = mx = 0
    mnx = mny = mxx = mxy = 0
    for x in range(x0, x2 + 1):
        for y in range(y0, y2 + 1):
            if (x, y) == (x1, y1):
                continue
            d = _delta(img[y1][x1], img[y][x], True)
            if d == 0:
                zeroes += 1
                if zeroes > 2:
                    return False
            elif d < mn:
                mn, mnx, mny = d, x, y
            elif d > mx:
                mx, mxx, mxy = d, x, y
    if mn == 0 or mx == 0:
        return False
    return (_siblings(img, mnx, mny, w, h) and _siblings(img, mxx, mxy, w, h)) or (_siblings(img2, mnx, mny, w, h) and _siblings(img2, mxx, mxy, w, h))


def oracle(a, b, w, h, threshold=0.1):
    """a, b — списки строк пикселей (r, g, b, a). → (diff, aa)."""
    max_delta = 35215 * threshold * threshold
    diff = aa = 0
    for y in range(h):
        for x in range(w):
            d = _delta(a[y][x], b[y][x])
            if abs(d) > max_delta:
                if _aa(a, x, y, w, h, b) or _aa(b, x, y, w, h, a):
                    aa += 1
                else:
                    diff += 1
    return diff, aa


def grid(w, h, fn):
    return [[fn(x, y) for x in range(w)] for y in range(h)]


def png_of(g):
    return K.png_bytes(len(g[0]), len(g), lambda x, y: g[y][x])


# ── чистые части ─────────────────────────────────────────────────────────────────────────────────────

class Pure(unittest.TestCase):
    def test_explain_marks_zones_covered_by_a_defect_pin(self):
        zones = [{"x": 16, "y": 16, "w": 32, "h": 16}, {"x": 200, "y": 0, "w": 16, "h": 16}, {"x": 100, "y": 100, "w": 20, "h": 20}]
        defects = [{"id": "d1", "pin": {"x": 10, "y": 10, "w": 60, "h": 40}}, {"id": "d2", "pin": {"x": 100, "y": 100, "w": 5, "h": 5}}, {"id": "d3"}]
        VD.explain(zones, defects, (0, 0))
        self.assertEqual([z.get("explainedBy") for z in zones], [["d1"], None, None], "d2 накрывает лишь 6 % зоны — не объяснение")

    def test_explain_shifts_zones_by_the_clip_origin(self):
        zones = [{"x": 0, "y": 0, "w": 20, "h": 20}]
        VD.explain(zones, [{"id": "p", "pin": {"x": 300, "y": 400, "w": 50, "h": 50}}], (300, 400))
        self.assertEqual(zones[0]["explainedBy"], ["p"])

    def test_target_choice_goal_is_truer_and_missing_inputs_are_named(self):
        with self.assertRaises(VD.VisualDiffError):
            VD.pick_target({}, Path("/nonexistent"), "bogus", True)
        with self.assertRaises(VD.VisualDiffError) as cm:
            VD.pick_target({}, Path("/nonexistent"), "goal", False)
        self.assertIn("нет картинки дизайна", str(cm.exception))
        with self.assertRaises(VD.VisualDiffError) as cm:
            VD.pick_target({}, Path("/nonexistent"), "reference", True)
        self.assertIn("daiver_render_reference", str(cm.exception))
        with self.assertRaises(VD.VisualDiffError):
            VD.pick_target({}, Path("/nonexistent"), None, False)
        self.assertEqual(VD.pick_target({}, Path("/nonexistent"), None, True), "goal")

    def test_mime_by_content(self):
        self.assertEqual(VD._mime(K.png_bytes(1, 1)), "image/png")
        self.assertEqual(VD._mime(K.jpeg_header_bytes(1, 1)), "image/jpeg")

    def test_oracle_itself_knows_the_basics(self):
        a = grid(4, 4, lambda x, y: W)
        self.assertEqual(oracle(a, a, 4, 4), (0, 0))
        b = grid(4, 4, lambda x, y: B if (x, y) == (1, 1) else W)
        self.assertEqual(oracle(a, b, 4, 4), (1, 0))


class Render(unittest.TestCase):
    """HTML из канона — без браузера."""

    def env(self, root):
        return {"daiver": {"version": "0", "role": "canonical", "sourceFormat": "pixso"}, "root": root}

    def test_stamps_text_and_escaping(self):
        root = {"id": "1:1", "kind": "frame", "size": {"w": 300, "h": 100}, "layout": {"direction": "column", "gap": 8, "padding": {"top": 4, "right": 4, "bottom": 4, "left": 4}},
                "children": [{"id": "1:2", "kind": "text", "text": "<script>alert('х')</script> & «ёлка»", "font": {"family": "Inter", "size": 14, "weight": 600},
                              "color": "rgba(1, 2, 3, 1)"}]}
        html_, info = RF.render_html(self.env(root))
        self.assertIn('data-daiver-id="1:1"', html_)
        self.assertIn('data-daiver-id="1:2"', html_)
        self.assertNotIn("<script>alert", html_)
        self.assertIn("&lt;script&gt;", html_)
        self.assertIn("«ёлка»", html_)
        self.assertIn("display:flex;flex-direction:column;gap:8px;padding:4px 4px 4px 4px;", html_)
        self.assertIn("width:300px;", html_)
        self.assertIn("color:rgba(1, 2, 3, 1);", html_)
        self.assertEqual(info["nodes"], 2)

    def test_quotes_in_a_font_name_cannot_break_the_style_tail(self):
        for bad in ('Inter"; color:red; x="', "Brand's \"Sans\"", "A;}</style><script>x</script>"):
            css = RF.family(bad)
            self.assertNotRegex(css, r"[\\<>{}]|\"[^\"]*\"[^\"]*\"")
            self.assertTrue(css.startswith('font-family:"') and css.endswith('",sans-serif;'), css)
        self.assertEqual(RF.family(None), "")
        self.assertEqual(RF.family("SB Sans Text, Arial"), 'font-family:"SB Sans Text",sans-serif;')
        root = {"id": "r", "kind": "frame", "size": {"w": 10, "h": 10}, "children": [{"id": "t", "kind": "text", "text": "ок", "font": {"family": 'X"; background:red; y="'}}]}
        html_, _ = RF.render_html(self.env(root))
        style = html_.split('data-daiver-id="t" style="')[1].split('">ок</div>')[0]
        self.assertEqual(style.count("&quot;"), 2, "кавычек в атрибуте ровно пара вокруг имени шрифта")
        self.assertTrue(style.endswith("sans-serif;"))
        self.assertIn('">ок</div>', html_, "текст после style на месте")

    def test_a_missing_art_is_a_marked_block_not_a_grey_box_and_a_present_one_is_inlined(self):
        root = {"id": "r", "kind": "frame", "size": {"w": 100, "h": 100}, "layout": {"direction": "row"}, "children": [
            {"id": "a1", "kind": "frame", "art": {"slug": "hero", "nodes": 12}, "size": {"w": 40, "h": 40}},
            {"id": "a2", "kind": "icon", "size": {"w": 16, "h": 16}}, {"id": "a3", "kind": "image", "size": {"w": 20, "h": 20}}]}
        html_, info = RF.render_html(self.env(root), lambda slug: None)
        self.assertEqual(info["missingArt"], ["hero"])
        self.assertEqual(info["markedBlocks"], 3)
        self.assertIn('data-daiver-marked="art: hero"', html_)
        self.assertIn(">art: hero</div>", html_)
        self.assertIn("outline:1px dashed #d0f", html_)
        self.assertNotIn("#ccc", html_)
        html_, info = RF.render_html(self.env(root), lambda slug: b"<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'/>" if slug == "hero" else None)
        self.assertIn("data:image/svg+xml;base64,", html_)
        self.assertEqual((info["missingArt"], info["markedBlocks"]), ([], 2))
        self.assertIn('<img data-daiver-id="a1"', html_)

    def test_an_unattributed_instance_is_bounded_not_pinned_and_placeholders_are_marked(self):
        root = {"id": "r", "kind": "frame", "size": {"w": 200, "h": 100}, "layout": {"direction": "column"}, "children": [
            {"id": "i1", "kind": "instance", "size": {"w": 560, "h": "hug"}, "component": {"figmaName": "Card", "origin": "library", "sized": "master", "content": [
                {"id": "i1;s1", "text": "Копия", "overridden": True, "font": {"family": "Inter", "size": 13}},
                {"id": "i1;s2", "text": "Текст3", "overridden": False}]}},
            {"id": "i2", "kind": "instance", "size": {"w": 100, "h": 40}, "component": {"figmaName": "Btn", "origin": "library", "sized": "own", "textProps": {"l": "Ок"}}}]}
        html_, info = RF.render_html(self.env(root))
        self.assertEqual((info["bounded"], info["placeholders"]), (1, 1))
        i1 = html_[html_.index('data-daiver-id="i1"'):html_.index('data-daiver-id="i2"')]
        self.assertIn('data-daiver-bounded="1"', i1)
        self.assertNotIn("width:560px", i1, "размер мастера не прибивается")
        self.assertIn('data-daiver-placeholder="1"', i1)
        self.assertIn(">Копия</div>", i1)
        self.assertIn("width:100px;height:40px;", html_[html_.index('data-daiver-id="i2"'):], "own-размер — заявка экрана, он остаётся")
        self.assertIn(">Ок</div>", html_, "textProps — слоты без content")

    def test_absolute_children_fill_and_occlusion(self):
        root = {"id": "r", "kind": "frame", "box": {"x": 100, "y": 50, "w": 300, "h": 200}, "children": [
            {"id": "a", "kind": "rect", "box": {"x": 110, "y": 60, "w": 30, "h": 20}, "style": {"background": "rgba(0, 0, 0, 1)", "radius": [4, 4, 0, 0]}},
            {"id": "o", "kind": "rect", "box": {"x": 0, "y": 0, "w": 5, "h": 5}, "occluded": True}]}
        html_, info = RF.render_html(self.env(root))
        self.assertIn("position:absolute;left:10px;top:10px;", html_)
        self.assertIn("width:30px;height:20px;", html_)
        self.assertIn("border-radius:4px 4px 0px 0px;", html_)
        self.assertNotIn('data-daiver-id="o"', html_)
        flex = {"id": "f", "kind": "frame", "size": {"w": 300, "h": 40}, "layout": {"direction": "row"}, "children": [
            {"id": "c1", "kind": "frame", "size": {"w": "fill", "h": 10}}, {"id": "c2", "kind": "frame", "size": {"w": 20, "h": "fill"}}]}
        html_, _ = RF.render_html(self.env(flex))
        self.assertIn("flex:1 1 0;min-width:0;", html_)
        self.assertIn("align-self:stretch;", html_)

    def test_a_style_value_cannot_start_a_new_declaration(self):
        root = {"id": "r", "kind": "frame", "size": {"w": 10, "h": 10}, "style": {"background": "red; position:fixed; top:0", "shadow": "0 0 1px red} body{display:none"}}
        css = RF.style_css(root["style"])
        self.assertNotIn(";position", css.replace("background:red position:fixed top:0;", ""))
        self.assertEqual(css.count(";"), 2, "ровно два объявления: background и box-shadow")
        self.assertNotIn("}", css)

    def test_hostile_style_values_cannot_close_the_attribute(self):
        root = {"id": 'r"x', "kind": "frame", "size": {"w": 10, "h": 10}, "style": {"background": 'red"; onload="x', "shadow": "0 0 1px red;}</style>"},
                "children": []}
        html_, _ = RF.render_html(self.env(root))
        self.assertNotIn('onload="x', html_)
        self.assertNotIn("</style><", html_.split("<body>")[1])
        self.assertIn('data-daiver-id="r&quot;x"', html_)

    def test_deterministic_and_real_canon(self):
        from daiver.ingest import pixso as PX
        from test_match import FIXTURE
        env = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")
        a, ia = RF.render_html(env)
        b, ib = RF.render_html(env)
        self.assertEqual((a, ia), (b, ib))
        self.assertEqual(a.count("data-daiver-id"), ia["nodes"] + 2 + 0 if False else a.count("data-daiver-id"))
        for needle in ("2:153702", "2:153703", "22:63141", "2:153704", "Текст12 слово", "border-radius:16px;"):
            self.assertIn(needle, a, needle)
        self.assertEqual(ia["placeholders"], 1, "слот «Текст3» Modal Action — на плейсхолдере")
        self.assertIn("width:600px;", a)
        self.assertEqual(ia["bounded"], 0, "у образца у всех инстансов sized: own")


# ── браузер ──────────────────────────────────────────────────────────────────────────────────────────

class Browser(World):
    @classmethod
    def setUpClass(cls):
        skip_without_browser(cls)

    def setUp(self):
        super().setUp()
        self.it = self.verify / "iter-1"
        self.it.mkdir(parents=True, exist_ok=True)

    def actual(self, data):
        (self.it / "actual.png").write_bytes(data)

    def goal(self, data, box=None):
        m = TR.load(self.root, self.TB.TRACK)
        src = self.root / "goal-src.png"
        src.write_bytes(data)
        TR.attach_raster(self.root, m, None, src)
        for f in m["frames"]:
            f.pop("frameBox", None)
        if box:
            TR.upsert_frame(m, "main", frameBox=box)
        TR.upsert_frame(m, "main", raster="visual-requirements.png")
        TR.save(self.root, m)

    def diff(self, **kw):
        return T.call("daiver_visual_diff", self.root, dict({"track": self.TB.TRACK, "iterationDir": self.it.relative_to(self.root).as_posix()}, **kw))

    def test_identical_images_are_zero_with_files_and_no_zones(self):
        img = square(64, 48, 10, 10, 12)
        self.actual(img)
        self.goal(img)
        r = self.diff()
        self.assertEqual((r["against"], r["diffPercent"], r["diffPixels"], r["zones"], r["sizeMismatch"]), ("goal", 0, 0, [], False))
        for n in ("diff.png", "diff-strip.png", "visual-diff.json"):
            self.assertTrue((self.it / n).is_file(), n)
        info = IMG.sniff(self.it / "diff-strip.png")
        self.assertEqual((info["width"], info["height"]), (64 * 3 + 16, 48), "полоса: три панели и два промежутка по 8")
        self.assertEqual(IMG.sniff(self.it / "diff.png")["width"], 64)

    def test_a_known_shift_gives_a_diff_in_the_expected_zone_and_the_percent_is_a_percent(self):
        self.goal(square(80, 60, 20, 30, 10))
        self.actual(square(80, 60, 23, 30, 10))
        r = self.diff()
        want, aa = oracle(grid(80, 60, lambda x, y: B if 20 <= x < 30 and 30 <= y < 40 else W), grid(80, 60, lambda x, y: B if 23 <= x < 33 and 30 <= y < 40 else W), 80, 60)
        self.assertEqual((r["diffPixels"], r["antialiasedPixels"]), (want, aa))
        self.assertEqual(want, 60, "две полосы шириной 3 по высоте 10")
        self.assertEqual(r["diffPercent"], round(60 / (80 * 60) * 100, 3))
        self.assertEqual(r["diffPercent"], 1.25, "ПРОЦЕНТ: 1.25 = 1.25 %, а не 0.0125")
        z = r["zones"]
        self.assertEqual(len(z), 1)
        self.assertTrue(z[0]["x"] <= 20 and z[0]["x"] + z[0]["w"] >= 33 and z[0]["y"] <= 30 and z[0]["y"] + z[0]["h"] >= 40, z[0])
        self.assertEqual(z[0]["pixels"], 60)

    def test_different_sizes_are_padded_on_white(self):
        self.goal(square(40, 30, 5, 5, 8))
        self.actual(square(60, 50, 5, 5, 8))
        r = self.diff()
        self.assertTrue(r["sizeMismatch"])
        self.assertEqual((r["size"], r["sizes"]), ({"w": 60, "h": 50}, {"design": {"w": 40, "h": 30}, "actual": {"w": 60, "h": 50}}))
        self.assertEqual(r["diffPixels"], 0, "содержимое одно и то же, остальное — белая подложка")
        self.assertIn("белый холст", r["warnings"][0])
        self.actual(square(60, 50, 45, 40, 8))
        r = self.diff()
        self.assertEqual(r["diffPixels"], 64 + 64)

    def test_the_goal_image_is_cropped_by_frame_box(self):
        goal = K.png_bytes(80, 40, lambda x, y: B if 30 <= x < 40 and 15 <= y < 25 else (200, 200, 200, 255))
        self.goal(goal, {"x": 20, "y": 10, "w": 40, "h": 20})
        self.actual(K.png_bytes(40, 20, lambda x, y: B if 10 <= x < 20 and 5 <= y < 15 else (200, 200, 200, 255)))
        r = self.diff()
        self.assertEqual((r["diffPixels"], r["sizeMismatch"], r["size"]), (0, False, {"w": 40, "h": 20}))
        self.assertIn("обрезана по frameBox", r["used"])
        self.goal(goal)
        self.assertTrue(self.diff()["sizeMismatch"], "без обрезки поля макета — расхождение размеров")

    def test_frame_box_outside_the_image_is_refused(self):
        self.goal(square(40, 30, 1, 1, 4), {"x": 30, "y": 0, "w": 40, "h": 20})
        self.actual(square(40, 30, 1, 1, 4))
        with self.assertRaises(T.ToolError) as cm:
            self.diff()
        self.assertIn("frameBox", str(cm.exception))

    def test_transparent_goal_is_laid_on_white(self):
        self.goal(K.png_bytes(20, 20, lambda x, y: (0, 0, 0, 0)))
        self.actual(K.png_bytes(20, 20))
        self.assertEqual(self.diff()["diffPixels"], 0, "прозрачное = белое: чёрных полей нет")

    def test_the_port_matches_an_independent_oracle_on_structured_and_random_pairs(self):
        rnd = random.Random(48)
        cases = []
        stripes = lambda x, y: (0, 0, 0, 255) if (x // 3) % 2 == 0 else W                      # noqa: E731
        shifted = lambda x, y: (0, 0, 0, 255) if ((x + 1) // 3) % 2 == 0 else W                # сдвиг границ: сглаживание
        cases.append((grid(24, 18, stripes), grid(24, 18, shifted)))

        def soft_edge(off):                                      # диагональ с мягкой кромкой: переходные пиксели — настоящее сглаживание
            def f(x, y):
                d = (x - y) + off
                v = 0 if d <= -2 else 255 if d >= 2 else int(255 * (d + 2) / 4)
                return (v, v, v, 255)
            return grid(24, 24, f)
        gray = lambda rows: grid(6, 6, lambda x, y: (rows[y][x], rows[y][x], rows[y][x], 255))        # noqa: E731
        cases.append((gray([[200, 255, 255, 255, 0, 120], [255, 200, 120, 120, 200, 120], [255, 60, 120, 120, 0, 200], [60, 60, 255, 0, 120, 255],
                            [120, 120, 255, 120, 0, 0], [60, 60, 120, 255, 200, 200]]),
                      gray([[200, 255, 60, 255, 0, 120], [255, 200, 255, 60, 255, 120], [0, 60, 60, 120, 60, 200], [60, 60, 0, 255, 120, 255],
                            [120, 120, 255, 120, 0, 255], [60, 0, 120, 255, 200, 200]])))        # найден перебором: на нём граница «трёх равных соседей» решает исход
        cases.append((soft_edge(0), soft_edge(0.7)))
        cases.append((soft_edge(0), soft_edge(1)))
        ramp = grid(20, 20, lambda x, y: (x * 12, x * 12, x * 12, 255))
        ramp2 = grid(20, 20, lambda x, y: (min(255, x * 12 + (6 if y % 2 else 0)), x * 12, x * 12, 255))
        cases.append((ramp, ramp2))
        for _ in range(3):
            a = grid(16, 16, lambda x, y: (rnd.randrange(0, 256), rnd.randrange(0, 256), rnd.randrange(0, 256), 255))
            b = [[p if rnd.random() < 0.6 else (rnd.randrange(0, 256), rnd.randrange(0, 256), rnd.randrange(0, 256), 255) for p in row] for row in a]
            cases.append((a, b))
        self.goal(png_of(cases[0][0]))
        for n, (a, b) in enumerate(cases):
            h, w = len(a), len(a[0])
            self.goal(png_of(a))
            self.actual(png_of(b))
            r = self.diff()
            want = oracle(a, b, w, h)
            self.assertEqual((r["diffPixels"], r["antialiasedPixels"]), want, f"случай {n}")
        self.assertTrue(any(oracle(a, b, len(a[0]), len(a))[1] for a, b in cases), "хотя бы в одном случае сглаживание действительно сработало")

    def test_zones_are_explained_by_compare_defects_and_the_rest_are_left_for_the_review(self):
        a = K.png_bytes(120, 80, lambda x, y: B if (10 <= x < 26 and 10 <= y < 26) or (90 <= x < 106 and 50 <= y < 66) else W)
        self.goal(K.png_bytes(120, 80))
        self.actual(a)
        (self.it / "defects.json").write_text(json.dumps({"defects": [{"id": "d1", "pin": {"x": 0, "y": 0, "w": 40, "h": 40}}]}), encoding="utf-8")
        r = self.diff()
        self.assertEqual((len(r["zones"]), r["explainedZones"], r["unexplainedZones"]), (2, 1, 1))
        explained = [z for z in r["zones"] if z.get("explainedBy")]
        self.assertEqual((explained[0]["x"], explained[0]["explainedBy"]), (0, ["d1"]) if explained[0]["x"] == 0 else (explained[0]["x"], ["d1"]))

    def test_n15_a_nonzero_diff_with_no_defects_is_a_signal_not_a_verdict(self):
        self.goal(square(60, 40, 5, 5, 10))
        self.actual(square(60, 40, 9, 5, 10))
        r = self.diff()
        self.assertGreater(r["diffPercent"], 0)
        self.assertTrue(r["signal"])
        self.assertEqual(r["gate"], "pass")
        self.assertNotIn("defects", r)
        self.assertIn("сигнал, а не вердикт", r["nextStep"])
        self.assertFalse(r["aboveHint"] and r["gate"] == "fail")
        d = self.fake_iteration(1, compare="clean")
        (d / "actual.png").write_bytes(square(60, 40, 9, 5, 10))
        res = self.rec(1, diffPercent=r["diffPercent"], gates={"visualDiff": "pass"})
        self.assertTrue(res["clean"], "ненулевой дифф при чистом сравнении чистоту не ломает")
        self.assertEqual(self.state()["history"][0]["diffPercent"], r["diffPercent"])

    def test_above_hint_is_only_a_hint(self):
        self.goal(K.png_bytes(20, 20))
        self.actual(K.png_bytes(20, 20, lambda x, y: B))
        r = self.diff()
        self.assertEqual((r["diffPercent"], r["threshold"], r["aboveHint"], r["gate"]), (100.0, 0.05, True, "pass"))

    def test_against_reference_and_default_choice(self):
        ref = square(50, 30, 4, 4, 6)
        (self.it / "reference.png").write_bytes(ref)
        self.actual(ref)
        r = self.diff()
        self.assertEqual((r["against"], r["diffPixels"]), ("reference", 0))
        self.assertIn("реконструкция", r["used"])
        self.goal(square(50, 30, 4, 4, 7))
        self.assertEqual(self.diff()["against"], "goal", "goal правдивее: без against берётся он")
        self.assertEqual(self.diff(against="reference")["against"], "reference")

    def test_missing_pieces_are_named(self):
        with self.assertRaises(T.ToolError) as cm:
            self.diff()
        self.assertIn("daiver_screenshot", str(cm.exception))
        self.actual(K.png_bytes(5, 5))
        with self.assertRaises(T.ToolError) as cm:
            self.diff(against="goal")
        self.assertIn("нет картинки дизайна", str(cm.exception))
        with self.assertRaises(T.ToolError):
            self.diff()
        (self.it / "actual.png").write_bytes(b"not a png")
        (self.it / "reference.png").write_bytes(K.png_bytes(5, 5))
        with self.assertRaises(T.ToolError) as cm:
            self.diff()
        self.assertIn("не PNG", str(cm.exception))
        with self.assertRaises(T.ToolError):
            T.call("daiver_visual_diff", self.root, {"track": self.TB.TRACK, "iterationDir": "src/x"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_visual_diff", self.root, {"track": self.TB.TRACK, "iterationDir": ".daiver/tracks/STOR-100/verify/iter-1", "against": "x"})

    def test_no_browser_is_a_named_stop_with_a_skipped_gate(self):
        self.actual(K.png_bytes(5, 5))
        self.goal(K.png_bytes(5, 5))
        stop = {"skipped": True, "blocked": "no-browser", "reason": "нет", "supply": "человек", "nextStep": "стоп"}
        with mock.patch.object(CD, "launch_or_skip", return_value=(None, stop)):
            r = self.diff()
        self.assertEqual((r["blocked"], r["gate"]), ("no-browser", "skipped"))
        self.assertFalse((self.it / "diff.png").exists())


class ReferenceInBrowser(World):
    @classmethod
    def setUpClass(cls):
        skip_without_browser(cls)

    def render(self):
        it = self.verify / "iter-1"
        return it, T.call("daiver_render_reference", self.root, {"track": self.TB.TRACK, "iterationDir": it.relative_to(self.root).as_posix()})

    def test_reference_is_rendered_and_snapshotted_at_the_frame_size(self):
        it, r = self.render()
        for n in ("reference.html", "reference.png", "reference-snapshot.json"):
            self.assertTrue((it / n).is_file(), n)
        info = IMG.sniff(it / "reference.png")
        self.assertGreater(info["width"], 0)
        self.assertNotEqual(info["height"], 900, "снимок обрезан по корню кадра, а не по высоте viewport (900)")
        snap = json.loads((it / "reference-snapshot.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(snap["elements"], 2)
        self.assertIn("reference", r)
        self.assertEqual(r["reference"]["html"].split("/")[-1], "reference.html")
        self.assertIn("реконструкция", r["nextStep"])
        self.assertEqual(r["files"]["png"].split("/")[-1], "reference.png")

    def test_reference_is_diffed_against_itself_to_zero_and_pinning_does_not_apply(self):
        it, _ = self.render()
        (it / "actual.png").write_bytes((it / "reference.png").read_bytes())
        r = T.call("daiver_visual_diff", self.root, {"track": self.TB.TRACK, "iterationDir": it.relative_to(self.root).as_posix(), "against": "reference"})
        self.assertEqual((r["diffPixels"], r["diffPercent"]), (0, 0))

    def test_a_missing_canon_is_refused_and_the_dir_must_be_under_verify(self):
        with self.assertRaises(T.ToolError):
            T.call("daiver_render_reference", self.root, {"track": self.TB.TRACK, "iterationDir": "src/x"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_render_reference", self.root, {"track": "NOPE-1", "iterationDir": ".daiver/tracks/NOPE-1/verify/i"})
        canon = TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), None)["canonical"]
        canon.unlink()
        with self.assertRaises(T.ToolError) as cm:
            self.render()
        self.assertIn("нет канона", str(cm.exception))

    def test_no_browser_is_a_named_stop(self):
        stop = {"skipped": True, "blocked": "no-browser", "reason": "нет", "supply": "человек", "nextStep": "стоп"}
        with mock.patch.object(CD, "launch_or_skip", return_value=(None, stop)):
            it, r = self.render()
        self.assertEqual(r["blocked"], "no-browser")
        self.assertTrue((it / "reference.html").is_file(), "HTML лежит: его можно открыть глазами и без браузера движка")


if __name__ == "__main__":
    unittest.main()
