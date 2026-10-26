#!/usr/bin/env python3
"""test_screenshot.py — захват реализации: dom-snapshot.js, модель реализации, готовность, daiver_screenshot (задача 4.2; спека §14.3,
§14.4.1, §14.6, §20.4; INV C22, C23, G10, D6, F13; QG U20).

Без браузера (всегда):
  • Readiness    — готово = нет признаков загрузки И DOM тих 1500 мс И два одинаковых замера подряд; потолок waitMs не перелетает;
                   не готово — причины по-русски; сбой замера ≠ готово; константы §20.4 пригвождены;
  • KeyEvents    — рецепт `press`: имена клавиш, модификаторы, текст только у печатных;
  • ImplModel    — снимок → модель канона: цвета в rgba (альфа по байту возвращается «авторским» числом), тень строкой, stamps в
                   модель не попадают, имена компонентов, пустой документ, валидность схемы;
  • CaptureArgs  — аргументы и рецепт (глаголы, JS нет), потолок waitMs, папка итерации только под verify/baseline, повторный
                   идентичный вызов — отказ (а с поднятым waitMs — нет), `blocked: no-browser` без исключения и без файлов;
  • ScreenshotTool — инструмент в реестре: схема, список объектов в actions, отказы словами.
С браузером (настоящий Chrome; нет его или FORGEUI_TEST_NO_BROWSER=1 — skip с причиной; FORGEUI_TEST_REQUIRE_BROWSER=1 — провал):
  • SnapshotScript — каждое правило §14.4.1 на синтетической странице (цвета, дефолты, box в координатах страницы, числа, id не
                   сдвигаются, svg одной нодой, обёртки, пароль, line-height: normal, капы, пустой документ, NaN, fiber-имена,
                   stamps вне дерева, раскладка), признаки загрузки и тишина DOM, проверка шрифтов;
  • CaptureRun   — один вызов целиком: исчезновение спиннера, clip, рецепт открывает диалог, alert, недорисованная страница,
                   ready:false с записанными файлами, target=reference.

Данные. Chrome 155 (macOS) — настоящий; страницы синтетические, написаны под правила. Что НЕ проверено данными: настоящее React-
приложение (fiber — подделка по форме из React 17/18: поля tag/type/return), настоящий dev-сервер (Vite/Next), шрифт SB Sans
(системные и объявленные @font-face шрифты проверяются, а «шрифт загружен с продакшена» — нет), Windows/Linux, страницы с iframe
и shadow DOM (в снимок не входят), пиксельное совпадение с обычным (не headless) Chrome.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daiver import canon as CN  # noqa: E402
from daiver import recipe as RC  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.browser import cdp as CD  # noqa: E402
from daiver.browser import find as BF  # noqa: E402
from daiver.verify import capture as CP  # noqa: E402
from daiver.verify import impl_model as IM  # noqa: E402
from daiver.verify import readiness as RD  # noqa: E402

REQUIRE_BROWSER = os.environ.get("FORGEUI_TEST_REQUIRE_BROWSER") == "1"
SRC = CP.script()


# ═════════════════════════════ без браузера ═════════════════════════════════════════════════════════════════════════

class FakeClock:
    """Часы и сон, которые идут сами: `sleep` двигает время; `script(now_ms) → замер` строит ответ страницы."""

    def __init__(self, script: Callable[[float], Dict[str, Any]]):
        self.t = 100.0
        self.start = self.t
        self.script = script
        self.polls: List[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)

    def poll(self) -> Dict[str, Any]:
        ms = (self.t - self.start) * 1000
        self.polls.append(ms)
        return self.script(ms)

    def run(self, wait_ms: float, **kw: Any) -> Dict[str, Any]:
        return RD.wait_ready(self.poll, wait_ms, sleep=self.sleep, clock=self.clock, **kw)


def calm(ms: float, **over: Any) -> Dict[str, Any]:
    out = {"sig": "same", "loading": [], "sinceMutationMs": ms}      # страница тиха с самого начала
    out.update(over)
    return out


class Readiness(unittest.TestCase):
    def test_the_constants_of_the_spec(self):
        self.assertEqual((RD.SETTLE_MS, RD.DOM_SETTLE_MS, RD.POLL_MS, RD.STABLE_SAMPLES), (400, 1500, 250, 2))
        self.assertEqual((CD.NAV_TIMEOUT, CD.ACTION_TIMEOUT), (30.0, 5.0))

    def test_a_quiet_page_is_ready_after_the_dom_quiet_window_not_at_the_ceiling(self):
        fc = FakeClock(lambda ms: calm(ms))
        out = fc.run(25000)
        self.assertTrue(out["ready"], out)
        self.assertEqual(out["reasons"], [])
        self.assertGreaterEqual(out["waitedMs"], 1500, "тишина DOM 1500 мс обязательна")
        self.assertLess(out["waitedMs"], 2200, "готовая страница не ждёт потолка: waitMs — потолок, не цена")
        self.assertAlmostEqual(fc.polls[0], 400, delta=1, msg="первый замер — после settleMs")

    def test_a_loader_blocks_readiness_until_it_goes_away(self):
        loader = {"reason": "loader", "element": "div.spinner"}
        fc = FakeClock(lambda ms: calm(ms, loading=[loader] if ms < 3000 else []))
        out = fc.run(25000)
        self.assertTrue(out["ready"])
        self.assertGreaterEqual(out["waitedMs"], 3000)

    def test_a_page_that_never_loads_is_not_ready_and_says_why_in_words(self):
        loader = {"reason": "loader", "element": "div.spinner"}
        fc = FakeClock(lambda ms: calm(ms, loading=[loader]))
        out = fc.run(5000)
        self.assertFalse(out["ready"])
        self.assertTrue(any("признак загрузки: loader (div.spinner)" in r for r in out["reasons"]), out["reasons"])
        self.assertLessEqual(out["waitedMs"], 5000, "потолок не перелетается")
        self.assertGreaterEqual(out["waitedMs"], 4990)

    def test_the_ceiling_is_exact_even_when_the_poll_step_does_not_divide_it(self):
        fc = FakeClock(lambda ms: calm(ms, loading=[{"reason": "x", "element": "y"}]))
        out = fc.run(2333)
        self.assertLessEqual(out["waitedMs"], 2333)

    def test_a_dom_that_keeps_changing_is_not_ready(self):
        counter = {"n": 0}

        def changing(ms: float) -> Dict[str, Any]:
            counter["n"] += 1
            return {"sig": f"s{counter['n']}", "loading": [], "sinceMutationMs": 10}
        out = FakeClock(changing).run(3000)
        self.assertFalse(out["ready"])
        self.assertTrue(any("DOM менялся 10 мс назад" in r for r in out["reasons"]), out["reasons"])
        self.assertTrue(any("содержимое менялось между замерами" in r for r in out["reasons"]), out["reasons"])

    def test_dom_quiet_window_is_the_gate_even_when_the_signature_is_stable(self):
        out = FakeClock(lambda ms: {"sig": "a", "loading": [], "sinceMutationMs": min(ms, 1499)}).run(4000)
        self.assertFalse(out["ready"], "1499 < 1500: тишины ещё нет")
        out = FakeClock(lambda ms: {"sig": "a", "loading": [], "sinceMutationMs": min(ms, 1500)}).run(4000)
        self.assertTrue(out["ready"])

    def test_two_equal_samples_in_a_row_are_required(self):
        sigs = iter(["a", "b", "b", "b", "b", "b", "b", "b", "b", "b", "b", "b", "b"])
        seen: List[str] = []

        def poll() -> Dict[str, Any]:
            s = next(sigs)
            seen.append(s)
            return {"sig": s, "loading": [], "sinceMutationMs": 9999}
        fc = FakeClock(lambda ms: {})
        out = RD.wait_ready(poll, 10000, sleep=fc.sleep, clock=fc.clock)
        self.assertTrue(out["ready"])
        self.assertEqual(seen, ["a", "b", "b"], "a→b смена, потом b,b: готово на втором одинаковом")
        self.assertEqual(out["polls"], 3)

    def test_a_failed_sample_is_never_ready(self):
        out = FakeClock(lambda ms: {"sig": None, "loading": [{"reason": "page", "element": "navigation"}], "sinceMutationMs": 9999}).run(2500)
        self.assertFalse(out["ready"])

    def test_a_missing_signature_alone_blocks(self):
        out = FakeClock(lambda ms: {"sig": None, "loading": [], "sinceMutationMs": 9999}).run(2500)
        self.assertFalse(out["ready"])

    def test_a_short_ceiling_cuts_the_settle_pause_too(self):
        fc = FakeClock(lambda ms: calm(ms, loading=[{"reason": "x", "element": "y"}]))
        out = fc.run(100)
        self.assertFalse(out["ready"])
        self.assertLessEqual(out["waitedMs"], 100)

    def test_at_most_five_reasons_are_named(self):
        many = [{"reason": f"r{i}", "element": "e"} for i in range(9)]
        out = FakeClock(lambda ms: calm(ms, loading=many)).run(2000)
        self.assertEqual(len([r for r in out["reasons"] if r.startswith("признак загрузки")]), RD.MAX_REASONS)

    def test_sample_without_quiet_value_is_not_quiet(self):
        out = FakeClock(lambda ms: {"sig": "a", "loading": []}).run(2500)
        self.assertFalse(out["ready"])


class KeyEvents(unittest.TestCase):
    def test_named_keys(self):
        down, up = CP.key_event_params("Enter")
        self.assertEqual((down["type"], down["key"], down["code"], down["windowsVirtualKeyCode"], down["text"]), ("keyDown", "Enter", "Enter", 13, "\r"))
        self.assertEqual((up["type"], up["key"]), ("keyUp", "Enter"))
        self.assertNotIn("text", up)
        self.assertEqual(CP.key_event_params("Escape")[0]["windowsVirtualKeyCode"], 27)
        self.assertEqual(CP.key_event_params("Tab")[0]["windowsVirtualKeyCode"], 9)
        self.assertNotIn("text", CP.key_event_params("ArrowDown")[0])

    def test_printable_characters_carry_text_and_codes(self):
        down = CP.key_event_params("a")[0]
        self.assertEqual((down["key"], down["code"], down["windowsVirtualKeyCode"], down["text"]), ("a", "KeyA", 65, "a"))
        self.assertEqual(CP.key_event_params("7")[0]["code"], "Digit7")
        space = CP.key_event_params("Space")[0]
        self.assertEqual((space["key"], space["text"]), (" ", " "))

    def test_modifiers_make_a_shortcut_not_a_character(self):
        down = CP.key_event_params("Control+A")[0]
        self.assertEqual(down["modifiers"], 2)
        self.assertNotIn("text", down, "Ctrl+A — не ввод буквы")
        self.assertEqual(CP.key_event_params("Shift+Tab")[0]["modifiers"], 8)
        self.assertEqual(CP.key_event_params("Control+Shift+Z")[0]["modifiers"], 10)
        self.assertEqual(CP.key_event_params("Shift+a")[0]["text"], "a", "Shift сам ввод не отменяет")

    def test_plus_itself_is_a_key(self):
        down = CP.key_event_params("+")[0]
        self.assertEqual(down["key"], "+")

    def test_unknown_key_or_modifier_is_named(self):
        with self.assertRaisesRegex(ValueError, "клавиша «Foo» неизвестна"):
            CP.key_event_params("Foo")
        with self.assertRaisesRegex(ValueError, "модификатор «Hyper» неизвестен"):
            CP.key_event_params("Hyper+A")


def snap_node(**kw: Any) -> Dict[str, Any]:
    n: Dict[str, Any] = {"id": "dom:0.1", "kind": "frame", "tag": "div", "name": "div", "box": {"x": 1, "y": 2, "w": 30, "h": 40}}
    n["size"] = {"w": n["box"]["w"], "h": n["box"]["h"]}
    n.update(kw)
    return n


def snapshot_of(root: Optional[Dict[str, Any]], **kw: Any) -> Dict[str, Any]:
    snap = {"v": 1, "elements": 1, "domElements": 1, "truncated": {"nodes": False, "depth": False, "text": 0}, "react": {"found": False},
            "viewport": {"w": 1280, "h": 900}, "page": {"w": 1280, "h": 900}, "stamps": {}, "root": root}
    snap.update(kw)
    return snap


class ImplModel(unittest.TestCase):
    def test_colors_become_canonical_rgba(self):
        self.assertEqual(IM.color_to_canon("#0A141E"), "rgba(10, 20, 30, 1)")
        self.assertEqual(IM.color_to_canon("#0A141E80"), "rgba(10, 20, 30, 0.5)")
        self.assertEqual(IM.color_to_canon("#00000000"), "rgba(0, 0, 0, 0)")
        self.assertEqual(IM.color_to_canon("#ffffff"), "rgba(255, 255, 255, 1)", "регистр hex не важен")

    def test_an_unrecognised_color_passes_verbatim_and_empty_is_none(self):
        self.assertEqual(IM.color_to_canon("oklch(0.5 0.2 30)"), "oklch(0.5 0.2 30)")
        self.assertIsNone(IM.color_to_canon(""))
        self.assertIsNone(IM.color_to_canon(None))
        self.assertIsNone(IM.color_to_canon(7))

    def test_alpha_returns_as_the_author_wrote_it(self):
        """Снимок хранит альфу байтом; автор писал 0.1/0.25/0.5 — отдаём самое короткое десятичное, дающее тот же байт."""
        for written in (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 0.8, 0.9, 0.95):
            byte = int(written * 255 + 0.5)
            self.assertEqual(IM.alpha_from_byte(byte), written, f"{written}: байт {byte}")
        self.assertEqual(IM.alpha_from_byte(255), 1)
        self.assertEqual(IM.alpha_from_byte(0), 0)

    def test_shadow_is_one_css_string_like_the_mockup_side(self):
        css = IM.shadow_to_css([{"x": 0, "y": 4, "blur": 6, "spread": 0, "color": "#0000001A", "inset": False},
                                {"x": 0, "y": 0, "blur": 0, "spread": 1, "color": "#FF0000", "inset": True}])
        self.assertEqual(css, "0px 4px 6px 0px rgba(0, 0, 0, 0.1), inset 0px 0px 0px 1px rgba(255, 0, 0, 1)")
        self.assertIsNone(IM.shadow_to_css(None))
        self.assertIsNone(IM.shadow_to_css([]))

    def test_the_model_is_valid_canon_of_source_format_dom(self):
        root = snap_node(style={"background": "#FFFFFF", "radius": 12, "border": {"width": 1, "color": "#CCCCCC"}, "opacity": 0.5,
                                "shadow": [{"x": 0, "y": 2, "blur": 4, "spread": 0, "color": "#00000033", "inset": False}]},
                         layout={"stated": True, "direction": "column", "gap": 8, "padding": {"top": 1, "right": 1, "bottom": 1, "left": 1}},
                         children=[snap_node(id="dom:0.1.0", kind="text", tag="h1", text="Привет", color="#000000",
                                             font={"family": "Arial", "size": 20, "weight": 700, "lineHeight": 23, "textTransform": "uppercase"})])
        env = IM.to_model(snapshot_of(root))
        self.assertEqual(CN.validate(env), [])
        self.assertEqual(env["daiver"]["sourceFormat"], "dom")
        r = env["root"]
        self.assertEqual(r["style"]["background"], "rgba(255, 255, 255, 1)")
        self.assertEqual(r["style"]["border"], {"width": 1, "color": "rgba(204, 204, 204, 1)"})
        self.assertEqual(r["layout"]["gap"], 8)
        kid = r["children"][0]
        self.assertEqual((kid["kind"], kid["text"], kid["color"], kid["tag"]), ("text", "Привет", "rgba(0, 0, 0, 1)", "h1"))
        self.assertEqual(kid["font"]["textTransform"], "uppercase")
        self.assertEqual(kid["box"], {"x": 1, "y": 2, "w": 30, "h": 40}, "box измерен у каждого узла")

    def test_every_node_keeps_its_box_and_numeric_size(self):
        kids = [snap_node(id=f"dom:0.1.{i}", box={"x": i, "y": 0, "w": 10.456, "h": 5}) for i in range(3)]
        env = IM.to_model(snapshot_of(snap_node(children=kids)))
        for n in CN.iter_nodes(env["root"]):
            self.assertEqual(sorted(n["box"]), ["h", "w", "x", "y"])
            self.assertTrue(all(isinstance(v, (int, float)) for v in n["size"].values()))
        self.assertEqual(env["root"]["children"][0]["box"]["w"], 10.46, "числа нормализованы каноном (2 знака)")

    def test_stamps_never_reach_the_model(self):
        env = IM.to_model(snapshot_of(snap_node(), stamps={"dom:0.1": "secret-stamp"}))
        self.assertNotIn("secret-stamp", json.dumps(env))
        self.assertNotIn("stamps", json.dumps(env))

    def test_component_chain_outer_first(self):
        env = IM.to_model(snapshot_of(snap_node(components=["Button", "ButtonBase"])))
        self.assertEqual(env["root"]["component"], {"figmaName": "Button", "implName": "Button", "implNames": ["Button", "ButtonBase"]})
        env = IM.to_model(snapshot_of(snap_node()))
        self.assertNotIn("component", env["root"])

    def test_what_canon_has_no_room_for_is_counted_not_hidden(self):
        root = snap_node(attrs={"role": "button"}, bgImage=True, scrolls=True,
                         children=[snap_node(id="dom:0.1.0", textBox={"x": 1, "y": 1, "w": 1, "h": 1}, overflowing=True, cut=True)])
        env = IM.to_model(snapshot_of(root))
        self.assertEqual(env["stats"]["dropped"], {"attrs": 1, "bgImage": 1, "scrolls": 1, "textBox": 1, "overflowing": 1, "cut": 1})
        self.assertNotIn("attrs", json.dumps(env["root"]))

    def test_empty_document_is_an_empty_root_not_an_exception(self):
        env = IM.to_model(snapshot_of(None, elements=0))
        self.assertEqual(CN.validate(env), [])
        self.assertEqual(env["root"]["children"], [])
        self.assertTrue(env["stats"]["empty"])
        self.assertEqual(env["root"]["box"], {"x": 0, "y": 0, "w": 0, "h": 0})

    def test_bad_snapshots_are_named(self):
        for bad in (None, [], {}, {"v": "1"}):
            with self.subTest(bad=bad), self.assertRaises(IM.ImplModelError):
                IM.to_model(bad)
        with self.assertRaisesRegex(IM.ImplModelError, "версия формата 2"):
            IM.to_model({"v": 2})

    def test_the_conversion_is_iterative_and_the_cap_depth_fits_the_recursive_normalizer(self):
        """convert() не рекурсивен (снимок бывает глубже лимита стека); а нормализатор канона рекурсивен — глубину ограничивает
        dom-snapshot.js (MAX_DEPTH 120), и модель такой глубины обязана собираться."""
        def chain(n: int) -> Dict[str, Any]:
            node = snap_node(id="dom:0.1.9999")
            for i in range(n):
                node = snap_node(id=f"dom:0.1.{i}", children=[node])
            return node
        got = IM.convert(chain(3000))["root"]
        depth, n = 0, got
        while n.get("children"):
            n, depth = n["children"][0], depth + 1
        self.assertEqual(depth, 3000)
        env = IM.to_model(snapshot_of(chain(121)))
        self.assertEqual(CN.validate(env), [])


# ── аргументы и репо ───────────────────────────────────────────────────────────────────────────────────────────────

def make_repo(tmp: Path, track: str = "T-1", frame: str = "main") -> Dict[str, Any]:
    root = tmp / "repo"
    root.mkdir()
    m = TR.new_manifest(track)
    TR.upsert_frame(m, frame)
    TR.save(root, m)
    paths = TR.frame_paths(root, m, None)
    return {"root": root, "manifest": m, "paths": paths}


NO_BROWSER = {"CHROMIUM_EXECUTABLE_PATH": "/definitely/not/a/browser", "FORGEUI_TEST_NO_BROWSER": "1"}


class CaptureArgs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = make_repo(Path(self.tmp.name).resolve())
        self.root = self.r["root"]
        self.it = self.r["paths"]["verify"] / "iter-1"

    def run_(self, **kw: Any) -> Dict[str, Any]:
        args: Dict[str, Any] = dict(track="T-1", frame=None, url="http://127.0.0.1:1/", iteration_dir=self.it, wait_ms=5000)
        args.update(kw)
        with mock.patch.dict(os.environ, NO_BROWSER):
            return CP.run(self.root, **args)

    def test_no_browser_is_a_named_stop_not_an_exception_and_writes_nothing(self):
        out = self.run_()
        self.assertEqual(out["blocked"], "no-browser")
        self.assertTrue(out["skipped"])
        self.assertIn("reason", out)
        self.assertIn("supply", out)
        self.assertFalse(self.it.exists(), "останов не оставляет папку итерации")

    def test_wait_ms_bounds(self):
        for bad in (0, 1999, 300001, "5000", None, True, float("nan")):
            with self.subTest(bad=bad), self.assertRaisesRegex(CP.CaptureError, "waitMs"):
                self.run_(wait_ms=bad)
        self.assertEqual(self.run_(wait_ms=2000)["blocked"], "no-browser", "границы включены")
        self.assertEqual(self.run_(wait_ms=300000)["blocked"], "no-browser")

    def test_unknown_target(self):
        with self.assertRaisesRegex(CP.CaptureError, "target"):
            self.run_(target="both")

    def test_recipe_is_validated_before_anything_starts(self):
        with self.assertRaisesRegex(CP.CaptureError, "evaluate"):
            self.run_(actions=[{"verb": "evaluate", "code": "1"}])
        with self.assertRaisesRegex(CP.CaptureError, "нужен аргумент «selector»"):
            self.run_(actions=[{"verb": "click"}])
        with self.assertRaisesRegex(CP.CaptureError, "clipSelector"):
            self.run_(clip_selector="")
        with self.assertRaisesRegex(CP.CaptureError, "waitForGone"):
            self.run_(wait_for_gone="")
        with self.assertRaisesRegex(CP.CaptureError, "не больше"):
            self.run_(actions=[{"verb": "wait", "ms": 1}] * (RC.MAX_ACTIONS + 1))

    def test_every_recipe_verb_is_played_by_the_driver(self):
        src = Path(CP.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_do")
        text = ast.get_source_segment(src, fn) or ""
        for verb in RC.ACTION_VERBS:
            self.assertIn(f'"{verb}"', text, f"глагол {verb} рецепта не исполняется драйвером")

    def test_the_iteration_dir_must_live_under_verify_or_baseline_of_the_frame(self):
        p = self.r["paths"]
        for ok in (p["verify"] / "iter-3", p["baseline"] / "v1", p["verify"] / "a" / "b"):
            with self.subTest(ok=str(ok)):
                self.assertTrue(CP.iteration_dir_ok(p, ok))
        for bad in (p["verify"], p["baseline"], p["dir"], p["dir"] / "assets", p["dir"] / "versions" / "x", self.root, self.root / "src",
                    p["verify"] / ".." / "plan.json", Path(self.tmp.name) / "elsewhere"):
            with self.subTest(bad=str(bad)):
                self.assertFalse(CP.iteration_dir_ok(p, bad))
        with self.assertRaisesRegex(CP.CaptureError, "iterationDir"):
            self.run_(iteration_dir=self.root / "src")

    def test_a_symlink_out_of_verify_is_not_a_way_around(self):
        self.r["paths"]["verify"].mkdir(parents=True)
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.r["paths"]["verify"] / "evil"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("символьные ссылки недоступны")
        self.assertFalse(CP.iteration_dir_ok(self.r["paths"], link / "iter"))

    def test_unknown_track_and_frame_are_named(self):
        with self.assertRaisesRegex(CP.CaptureError, "трек «T-9» не найден"):
            self.run_(track="T-9")
        with self.assertRaisesRegex(CP.CaptureError, "кадра «ghost» в треке нет"):
            self.run_(frame="ghost")

    def parts(self, **over: Any) -> Dict[str, Any]:
        p: Dict[str, Any] = {"target": "implementation", "url": "http://127.0.0.1:1/", "recipe": {}, "waitMs": 5000, "viewport": [1280, 900]}
        p.update(over)
        return p

    def leave_previous(self, fp: str, name: str = "snapshot.json") -> None:
        self.it.mkdir(parents=True)
        (self.it / name).write_text(json.dumps({"v": 1, "capture": {"fingerprint": fp}}), encoding="utf-8")

    def test_the_identical_repeat_is_refused_with_the_cure(self):
        self.leave_previous(CP.fingerprint(self.parts()))
        with self.assertRaises(CP.CaptureError) as cm:
            self.run_()
        msg = str(cm.exception)
        self.assertIn("отказ", msg)
        self.assertIn("waitMs=5000", msg)
        self.assertIn("Подними waitMs", msg)
        self.assertIn("waitForGone", msg)

    def test_raising_wait_ms_is_the_right_move_not_a_repeat(self):
        self.leave_previous(CP.fingerprint(self.parts()))
        self.assertEqual(self.run_(wait_ms=9000)["blocked"], "no-browser", "дошло до браузера: не отказ")

    def test_a_changed_recipe_or_url_or_target_is_not_a_repeat(self):
        self.leave_previous(CP.fingerprint(self.parts()))
        self.assertEqual(self.run_(clip_selector="#dlg")["blocked"], "no-browser")
        self.assertEqual(self.run_(url="http://127.0.0.1:1/other")["blocked"], "no-browser")
        self.assertEqual(self.run_(wait_for_gone=".spinner")["blocked"], "no-browser")

    def test_the_reference_has_its_own_snapshot_file_so_it_never_trips_the_implementation_refusal(self):
        self.leave_previous(CP.fingerprint(self.parts()), "snapshot.json")
        self.assertEqual(self.run_(target="reference")["blocked"], "no-browser")
        self.leave_previous.__func__  # noqa: B018 — метод жив
        (self.it / "reference-snapshot.json").write_text(json.dumps({"capture": {"fingerprint": CP.fingerprint(self.parts(target="reference"))}}),
                                                         encoding="utf-8")
        with self.assertRaisesRegex(CP.CaptureError, "отказ"):
            self.run_(target="reference")

    def test_a_broken_previous_snapshot_is_not_a_repeat(self):
        self.it.mkdir(parents=True)
        for junk in ("", "{", "[]", '{"capture": 5}', '{"capture": {"fingerprint": 7}}'):
            with self.subTest(junk=junk):
                (self.it / "snapshot.json").write_text(junk, encoding="utf-8")
                self.assertEqual(self.run_()["blocked"], "no-browser")

    def test_the_viewport_is_part_of_the_fingerprint(self):
        self.assertNotEqual(CP.fingerprint(self.parts()), CP.fingerprint(self.parts(viewport=[1440, 900])))

    def test_the_fingerprint_is_stable_and_order_free(self):
        a = CP.fingerprint({"a": 1, "b": {"x": [1, 2], "y": 3}})
        b = CP.fingerprint({"b": {"y": 3, "x": [1, 2]}, "a": 1})
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{16}$")

    def test_a_file_url_is_not_accepted_for_the_implementation_in_the_args_layer_either(self):
        """Адрес проверяет клиент браузера (check_url); здесь — что без браузера он до запуска не доходит и ничего не пишет."""
        self.assertEqual(self.run_(url="file:///etc/hosts")["blocked"], "no-browser")
        self.assertFalse(self.it.exists())

    def test_png_size_reads_the_header_only(self):
        import struct
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 332, 180) + b"\x08\x06\x00\x00\x00"
        self.assertEqual(CP.png_size(png), {"w": 332, "h": 180})
        self.assertIsNone(CP.png_size(b"not a png at all, but long enough......."))
        self.assertIsNone(CP.png_size(b""))


class ScreenshotTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = make_repo(Path(self.tmp.name).resolve())
        self.root = self.r["root"]
        self.args = {"track": "T-1", "url": "http://127.0.0.1:1/", "iterationDir": ".daiver/tracks/T-1/verify/iter-1", "waitMs": 5000}

    def call(self, **over: Any) -> Dict[str, Any]:
        args = dict(self.args)
        args.update(over)
        with mock.patch.dict(os.environ, NO_BROWSER):
            return T.call("daiver_screenshot", self.root, args)

    def test_it_is_registered_with_the_required_arguments(self):
        tool = T.TOOLS["daiver_screenshot"]
        self.assertEqual(sorted(tool["schema"]["required"]), ["iterationDir", "track", "url", "waitMs"])
        self.assertFalse(tool["schema"]["additionalProperties"])
        self.assertEqual(tool["schema"]["properties"]["target"]["enum"], ["implementation", "reference"])
        self.assertEqual(tool["schema"]["properties"]["actions"]["items"], {"type": "object"})

    def test_the_description_carries_the_rules_a_weak_model_must_obey(self):
        d = T.TOOLS["daiver_screenshot"]["description"]
        for needle in ("ready: false", "НЕ сравнивай", "waitForGone", "ИДЕНТИЧНЫЙ", "underRendered", "blocked: no-browser", "выполнить JS",
                       "clipSelector", "60000", "25000", "5000"):
            self.assertIn(needle, d)

    def test_arguments_are_checked_by_hand_and_named_together(self):
        with self.assertRaises(T.ToolError) as cm:
            self.call(bogus=1, waitMs="x")
        msg = str(cm.exception)
        self.assertIn("неизвестный аргумент «bogus»", msg)
        self.assertIn("«waitMs»: ожидается number", msg)
        with self.assertRaisesRegex(T.ToolError, "нет обязательного аргумента «url»"):
            T.call("daiver_screenshot", self.root, {"track": "T-1", "iterationDir": "x", "waitMs": 5000})
        with self.assertRaisesRegex(T.ToolError, "«actions»: ожидается список объектов"):
            self.call(actions=["click"])
        with self.assertRaisesRegex(T.ToolError, "«actions»: ожидается список объектов"):
            self.call(actions="click")

    def test_capture_errors_become_tool_errors(self):
        with self.assertRaisesRegex(T.ToolError, "iterationDir"):
            self.call(iterationDir="src")
        with self.assertRaisesRegex(T.ToolError, "evaluate"):
            self.call(actions=[{"verb": "evaluate"}])
        with self.assertRaisesRegex(T.ToolError, "waitMs"):
            self.call(waitMs=10)
        with self.assertRaisesRegex(T.ToolError, "трек|имя"):
            self.call(track="../x")

    def test_paths_outside_the_repo_are_refused(self):
        with self.assertRaises(T.ToolError):
            self.call(iterationDir="../../../etc")
        with self.assertRaises(T.ToolError):
            self.call(iterationDir="/etc")

    def test_no_browser_comes_back_as_an_answer(self):
        out = self.call()
        self.assertEqual(out["blocked"], "no-browser")
        self.assertIn("nextStep", out)

    def test_the_tool_is_owned_by_ui_verify(self):
        hooks = str(Path(__file__).resolve().parents[3] / "hooks")
        sys.path.insert(0, hooks)
        try:
            import _branches as BR
        finally:
            sys.path.remove(hooks)
        self.assertEqual(BR.tool_steps("mcp__forge-ui__daiver_screenshot"), ("ui-verify",))

    def test_no_network_and_no_download_in_the_capture_code(self):
        """N8/F11: захват не ходит в сеть сам и ничего не ставит. Статически: ни сетевых библиотек, ни командных строк."""
        for path in (Path(CP.__file__), Path(IM.__file__), Path(RD.__file__)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
            mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
            for banned in ("urllib", "http", "socket", "requests", "subprocess", "ssl", "ftplib"):
                self.assertNotIn(banned, mods, f"{path.name}: импорт {banned}")
        js = Path(CP.SCRIPT_PATH).read_text(encoding="utf-8")
        for banned in ("eval(", "new Function", "fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "import("):
            self.assertNotIn(banned, js, f"dom-snapshot.js: {banned} — снимок не исполняет чужой код и не ходит в сеть")


# ═════════════════════════════ с браузером ══════════════════════════════════════════════════════════════════════════

class Site:
    """Крошечный http-сервер синтетических страниц на 127.0.0.1 (file: для захвата закрыт check_url, а data: не держит размер)."""

    def __init__(self) -> None:
        self.pages: Dict[str, bytes] = {}
        site = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = site.pages.get(self.path.split("?")[0])
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.n = 0

    def put(self, html: str) -> str:
        self.n += 1
        path = f"/p{self.n}.html"
        self.pages[path] = html.encode("utf-8")
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def skip_without_browser(case: unittest.TestCase) -> str:
    if os.environ.get("FORGEUI_TEST_NO_BROWSER") == "1":
        raise unittest.SkipTest("FORGEUI_TEST_NO_BROWSER=1")
    found = BF.find_browser()
    if not found["found"]:
        if REQUIRE_BROWSER:
            raise AssertionError(f"FORGEUI_TEST_REQUIRE_BROWSER=1, а браузера нет: {found['reason']}")
        raise unittest.SkipTest(f"Chrome/Chromium не найден: {found['reason']}")
    return found["path"]


def _no_constant(name: str) -> Any:
    raise AssertionError(f"в снимке {name}: JSON не знает таких чисел (NaN невозможен)")


def walk(node: Optional[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    stack = [node] if node else []
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.get("children") or []))


STYLE = "<!doctype html><meta charset=utf-8><style>body{margin:0;font:16px/20px Arial}</style>"


class SnapshotScript(unittest.TestCase):
    """Каждое правило §14.4.1 — на странице, написанной под него. Один браузер на класс."""

    @classmethod
    def setUpClass(cls):
        path = skip_without_browser(cls)
        cls.site = Site()
        cls.addClassCleanup(cls.site.close)
        try:
            cls.chrome = CD.Chrome.launch(path, viewport=(800, 600), launch_timeout=45)
        except CD.BrowserLaunchError as e:
            if REQUIRE_BROWSER:
                raise
            raise unittest.SkipTest(f"Chrome найден ({path}), но не запустился: {e}")
        cls.addClassCleanup(cls.chrome.close)

    def page(self, body: str, head: str = "") -> CD.Page:
        page = self.chrome.new_page(self.site.put(STYLE + head + body), nav_timeout=20)
        self.addCleanup(page.close)
        return page

    def snap(self, body: str, head: str = "", opts: Optional[Dict[str, Any]] = None, setup: str = "") -> Dict[str, Any]:
        page = self.page(body, head)
        if setup:
            page.evaluate(setup)
        return self.take(page, opts)

    def take(self, page: CD.Page, opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = page.evaluate("(" + SRC + ")().snapshot(" + json.dumps(opts or {}) + ")", timeout=30)
        self.assertIsInstance(text, str, "результат — JSON-СТРОКА, не граф")
        return json.loads(text, parse_constant=_no_constant)

    def node(self, snap: Dict[str, Any], name: str) -> Dict[str, Any]:
        got = [n for n in walk(snap["root"]) if n.get("name") == name]
        self.assertEqual(len(got), 1, f"{name}: ожидался один узел, найдено {len(got)} (есть: {[n.get('name') for n in walk(snap['root'])][:12]})")
        return got[0]

    def texts(self, snap: Dict[str, Any]) -> List[str]:
        return [n["text"] for n in walk(snap["root"]) if "text" in n]

    # ── цвета и дефолты ──
    def test_colors_are_normalized_in_the_page_to_hex(self):
        s = self.snap('<div id=a style="background:rgb(10,20,30);width:50px;height:50px"></div>'
                      '<div id=b style="background:rgba(10,20,30,.5);width:50px;height:50px"></div>'
                      '<div id=c style="background:oklch(0.5 0.2 30);width:50px;height:50px"></div>'
                      '<div id=d style="background:transparent;border:2px solid rgb(255,0,0);width:50px;height:50px"></div>'
                      '<p id=e style="color:rgb(1,2,3)">T</p>'
                      '<div id=f style="background:color-mix(in srgb, rgb(255,0,0) 50%, rgb(0,0,255));width:5px;height:5px"></div>')
        self.assertEqual(self.node(s, "div#a")["style"]["background"], "#0A141E")
        self.assertEqual(self.node(s, "div#b")["style"]["background"], "#0A141E80")
        self.assertTrue(self.node(s, "div#c")["style"]["background"].startswith("oklch("), "нераспознанное — как есть")
        d = self.node(s, "div#d")
        self.assertNotIn("background", d["style"], "transparent — нет краски")
        self.assertEqual(d["style"]["border"], {"width": 2, "color": "#FF0000"})
        self.assertEqual(self.node(s, "p#e")["color"], "#010203")
        self.assertRegex(self.node(s, "div#f")["style"]["background"], r"^(#[0-9A-F]{6}|color\(|rgb)", "color-mix разрешается браузером")

    def test_gradient_colors_are_normalized_too(self):
        s = self.snap('<div id=g style="width:50px;height:50px;background:linear-gradient(rgb(255,0,0), rgba(0,0,255,.5))"></div>')
        g = self.node(s, "div#g")["style"]["gradient"]
        self.assertIn("#FF0000", g)
        self.assertIn("#0000FF80", g)
        self.assertNotIn("rgb", g)

    def test_absent_groups_are_defaults_not_unknown(self):
        s = self.snap('<div id=plain style="width:40px;height:40px;background:#fff"></div>'
                      '<div id=half style="width:40px;height:40px;background:#fff;opacity:.5"></div>'
                      '<div id=wrap><i>a</i><i>b</i></div>')
        self.assertEqual(self.node(s, "div#plain")["style"], {"background": "#FFFFFF"}, "ни radius, ни border, ни shadow, ни opacity")
        self.assertEqual(self.node(s, "div#half")["style"]["opacity"], 0.5)
        lay = self.node(s, "div#wrap")["layout"]
        self.assertNotIn("padding", lay)
        self.assertNotIn("gap", lay)

    # ── геометрия ──
    def test_box_is_measured_in_page_coordinates_even_when_scrolled(self):
        s = self.snap('<div style="height:3000px"></div><div id=far style="position:absolute;top:1000px;left:20px;width:100px;height:50px;background:#000"></div>',
                      setup="window.scrollTo(0, 500)")
        far = self.node(s, "div#far")
        self.assertEqual(far["box"], {"x": 20, "y": 1000, "w": 100, "h": 50})
        self.assertEqual(s["page"]["scrollY"], 500)
        self.assertGreaterEqual(s["page"]["h"], 3000)

    def test_size_and_box_are_numbers_on_every_node(self):
        s = self.snap('<div id=full style="width:100%;background:#eee"><span id=in style="display:inline-block;width:30px;height:10px;background:#000"></span>'
                      '<p>text</p></div><div style="display:flex"><div style="flex:1;background:#ccc;height:9px"></div></div>')
        count = 0
        for n in walk(s["root"]):
            count += 1
            for key in ("w", "h"):
                self.assertIsInstance(n["size"][key], (int, float), n["id"])
                self.assertNotIsInstance(n["size"][key], bool)
            self.assertEqual(sorted(n["box"]), ["h", "w", "x", "y"])
            self.assertEqual((n["size"]["w"], n["size"]["h"]), (n["box"]["w"], n["box"]["h"]), "size — измеренное, не fill/hug")
        self.assertEqual(count, s["elements"], "elements = число узлов дерева")
        self.assertEqual(self.node(s, "div#full")["box"]["w"], 800)

    def test_ids_do_not_shift_after_a_hidden_sibling(self):
        s = self.snap('<div id=wrap><div id=h style="display:none">x</div><script>0</script><p id=p1>One</p><p id=p2>Two</p></div>')
        wrap = self.node(s, "div#wrap")
        p1, p2 = self.node(s, "p#p1"), self.node(s, "p#p2")
        self.assertEqual(p1["id"], wrap["id"] + ".2", "индексы считаются по ВСЕМ элементам-детям: скрытый и script место держат")
        self.assertEqual(p2["id"], wrap["id"] + ".3")
        s2 = self.snap('<div id=wrap><div id=h>x</div><script>0</script><p id=p1>One</p><p id=p2>Two</p></div>')
        self.assertEqual(self.node(s2, "p#p1")["id"], p1["id"], "показали скрытого — id остальных те же")

    def test_ids_are_dom_paths_from_html(self):
        s = self.snap("<p id=x>t</p>")
        self.assertEqual(s["root"]["id"], "dom:0.1", "body — второй ребёнок html (после head)")
        self.assertEqual(self.node(s, "p#x")["id"], "dom:0.1.0")

    # ── обёртки, svg, формы ──
    def test_empty_wrappers_are_dropped_but_a_collapsed_one_with_content_stays(self):
        s = self.snap('<div id=empty></div><div id=spacer style="height:20px"></div>'
                      '<div id=collapsed style="height:0"><span id=inner>Visible</span></div>'
                      '<div id=contents style="display:contents"><p id=cp>Y</p></div>'
                      '<div id=painted style="width:10px;height:10px;background:#f00"></div>')
        names = [n["name"] for n in walk(s["root"])]
        self.assertNotIn("div#empty", names)
        self.assertNotIn("div#spacer", names, "ни содержимого, ни краски — не узел")
        self.assertIn("div#painted", names, "пустой, но с краской — остаётся (rect)")
        self.assertEqual(self.node(s, "div#painted")["kind"], "rect")
        col = self.node(s, "div#collapsed")
        self.assertEqual(col["box"]["h"], 0)
        self.assertEqual([k["name"] for k in col["children"]], ["span#inner"], "схлопнувшаяся обёртка с видимым содержимым остаётся")
        con = self.node(s, "div#contents")
        self.assertEqual(con["box"], {"x": 0, "y": con["box"]["y"], "w": 0, "h": 0}, "display:contents — нет бокса: нули, не NaN")
        self.assertEqual(con["children"][0]["name"], "p#cp")

    def test_svg_is_one_node_not_traversed(self):
        s = self.snap('<svg id=s width=30 height=30><g><path d="M0 0h10v10z"/><circle r=3 /></g></svg>')
        v = self.node(s, "svg#s")
        self.assertEqual(v["kind"], "vector")
        self.assertNotIn("children", v)
        self.assertGreaterEqual(s["domElements"], 5)
        self.assertEqual(s["elements"], 2, "body и svg")

    def test_password_and_form_values_are_read(self):
        s = self.snap('<input id=pw type=password value=hunter2><input id=ph placeholder="Имя"><select id=sel><option value=a>Alpha</option>'
                      '<option value=b selected>Beta</option></select><textarea id=ta>Строка</textarea><input id=cb type=checkbox checked>')
        pw = self.node(s, "input#pw")
        self.assertEqual(pw["text"], "hunter2")
        self.assertEqual(pw["attrs"]["type"], "password")
        ph = self.node(s, "input#ph")
        self.assertNotIn("text", ph)
        self.assertEqual(ph["attrs"]["placeholder"], "Имя")
        self.assertEqual(self.node(s, "select#sel")["text"], "Beta")
        self.assertEqual(self.node(s, "textarea#ta")["text"], "Строка")
        cb = self.node(s, "input#cb")
        self.assertNotIn("text", cb)
        self.assertTrue(cb["attrs"]["checked"])

    def test_replaced_elements_are_images_without_children(self):
        s = self.snap('<img id=im width=40 height=30 alt="Лого" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">'
                      '<canvas id=cv width=20 height=10></canvas>')
        self.assertEqual(self.node(s, "img#im")["kind"], "image")
        self.assertEqual(self.node(s, "img#im")["attrs"]["alt"], "Лого")
        self.assertEqual(self.node(s, "canvas#cv")["kind"], "image")

    # ── текст и шрифт ──
    def test_text_leaf_mixed_content_and_runs(self):
        s = self.snap("<h1 id=h>Title</h1><p id=p>Hello <b id=b>world</b> again</p>")
        h = self.node(s, "h1#h")
        self.assertEqual((h["kind"], h["text"]), ("text", "Title"), "единственный кусок текста без краски — сам узел и есть текст")
        self.assertNotIn("children", h)
        p = self.node(s, "p#p")
        self.assertEqual([k["text"] for k in p["children"]], ["Hello", "world", "again"])
        self.assertTrue(p["children"][0]["id"].startswith(p["id"] + "#"), "кусок текста: <путь элемента>#<номер узла>")
        self.assertEqual(p["layout"]["direction"], "row", "строчные дети — строка")

    def test_painted_button_is_a_frame_with_a_text_child(self):
        s = self.snap('<button id=bt style="background:#00f;color:#fff;padding:4px 8px;border:0">Save</button>')
        b = self.node(s, "button#bt")
        self.assertEqual(b["kind"], "frame")
        self.assertEqual(b["layout"]["padding"], {"top": 4, "right": 8, "bottom": 4, "left": 8})
        self.assertEqual([(k["kind"], k["text"], k["color"]) for k in b["children"]], [("text", "Save", "#FFFFFF")])

    def test_line_height_normal_is_measured_not_guessed(self):
        s = self.snap('<div id=ln class=n>Hello</div><div id=ex class=x>Hello</div><div id=un class=u>Hello</div>',
                      head="<style>.n{font:20px Arial;line-height:normal}.x{font:20px Arial;line-height:30px}.u{font:20px Arial;line-height:1.5}</style>")
        ln = self.node(s, "div#ln")
        self.assertAlmostEqual(ln["font"]["lineHeight"], ln["box"]["h"], delta=0.51, msg="однострочный блок высотой в строку: проба = факт")
        self.assertIsInstance(ln["font"]["lineHeight"], (int, float))
        self.assertEqual(self.node(s, "div#ex")["font"]["lineHeight"], 30)
        self.assertEqual(self.node(s, "div#un")["font"]["lineHeight"], 30, "безразмерный 1.5 × 20px")

    def test_the_probe_leaves_nothing_in_the_document(self):
        page = self.page('<div class=n>Hello</div>', head="<style>.n{font:20px Arial;line-height:normal}</style>")
        before = page.evaluate("document.documentElement.outerHTML")
        self.take(page)
        self.assertEqual(page.evaluate("document.documentElement.outerHTML"), before)

    def test_font_group_and_text_transform(self):
        s = self.snap('<p id=up style="font:italic 700 18px/24px \'Fira Sans\', Arial;text-transform:uppercase;letter-spacing:1px;text-align:center">hello</p>')
        p = self.node(s, "p#up")
        self.assertEqual(p["text"], "hello", "текст сырой; регистр рисует страница")
        self.assertEqual(p["font"], {"family": "Fira Sans", "size": 18, "weight": 700, "lineHeight": 24, "align": "center", "letterSpacing": 1,
                                     "italic": True, "textTransform": "uppercase"})

    def test_whitespace_is_collapsed_unless_the_page_keeps_it(self):
        s = self.snap('<p id=a>  one \n   two  </p><pre id=b style="margin:0">x  y\nz</pre>')
        self.assertEqual(self.node(s, "p#a")["text"], "one two")
        self.assertEqual(self.node(s, "pre#b")["text"], "x  y\nz")

    # ── скрытое ──
    def test_visibility_hidden_text_is_dropped_but_visible_children_stay(self):
        s = self.snap('<div id=vh style="visibility:hidden"><p id=vv style="visibility:visible">Seen</p><p>Unseen</p></div><p id=zero style="opacity:0">Faint</p>')
        self.assertIn("Seen", self.texts(s))
        self.assertNotIn("Unseen", self.texts(s))
        self.assertEqual(self.node(s, "p#zero")["style"]["opacity"], 0, "opacity:0 — узел остаётся (INVISIBLE ловит позже)")

    # ── капы ──
    def test_node_cap_sets_a_truncation_flag(self):
        s = self.snap("<i>x</i>" * 8100)
        self.assertTrue(s["truncated"]["nodes"])
        self.assertLessEqual(s["elements"], 8010, "кап ≈ 8000 (предки закрываются после детей)")
        self.assertGreater(s["elements"], 7000)
        self.assertEqual(s["domElements"] > 8100, True)

    def test_text_cap_sets_a_flag_and_cuts_to_2000(self):
        s = self.snap("<p id=long>" + "x" * 3000 + "</p><p id=short>ok</p>")
        self.assertEqual(len(self.node(s, "p#long")["text"]), 2000)
        self.assertEqual(s["truncated"]["text"], 1)
        self.assertFalse(s["truncated"]["nodes"])

    def test_depth_cap_sets_a_flag(self):
        s = self.snap("<div>" * 140 + "deep" + "</div>" * 140)
        self.assertTrue(s["truncated"]["depth"])
        self.assertIsNotNone(s["root"], "усечение по глубине не превращает документ в пустой")
        depth, n = 0, s["root"]
        while n.get("children"):
            n, depth = n["children"][0], depth + 1
        self.assertLessEqual(depth, 125)
        self.assertTrue(n.get("cut"), "граничный элемент помечен cut")

    def test_nothing_is_truncated_on_an_ordinary_page(self):
        s = self.snap("<p>one</p>")
        self.assertEqual(s["truncated"], {"nodes": False, "depth": False, "text": 0})

    # ── пустое и NaN ──
    def test_an_empty_document_is_an_empty_document_not_an_exception(self):
        for body in ("", "   \n  ", "<script>0</script>", "<div></div><div></div>", '<div style="display:none">x</div>'):
            with self.subTest(body=body):
                s = self.snap(body)
                self.assertIsNone(s["root"])
                self.assertEqual(s["elements"], 0)
                self.assertFalse(s["rootMissing"])

    def test_a_display_none_body_is_an_empty_document(self):
        s = self.snap("<p>x</p>", head="<style>body{display:none}</style>")
        self.assertIsNone(s["root"])

    def test_nan_and_infinity_cannot_appear(self):
        page = self.page('<div style="display:contents"><p>a</p></div><details><summary>s</summary>hidden</details><img width=0 height=0 src="data:,">'
                         '<br><svg width=0 height=0></svg><input style="width:0;height:0"><div style="width:0;height:0;overflow:hidden">z</div>'
                         '<div style="font-size:0">zero</div><div style="line-height:0">x</div>')
        text = page.evaluate("(" + SRC + ")().snapshot({})")
        for bad in ("NaN", "Infinity", "null,", ":null"):
            if bad in ("null,", ":null"):
                continue
            self.assertNotIn(bad, text)
        json.loads(text, parse_constant=_no_constant)

    # ── fiber ──
    FIBERS = """<div id=card><button id=btn>Go</button><i id=min>m</i><i id=two>t</i><u id=memo>u</u><s id=same>s</s><em id=old>o</em></div>
<script>
var root={tag:3}, host=function(el,key,fiber){document.getElementById(el)[key]=fiber;};
var cardF={tag:5,return:{tag:0,type:{name:'Card'},return:{tag:11,type:{render:{name:'Frame'}},return:{tag:10,return:root}}}};
host('card','__reactFiber$x1',cardF);
host('btn','__reactFiber$x1',{tag:5,return:{tag:0,type:{name:'Button'},return:cardF}});
host('min','__reactFiber$x1',{tag:5,return:{tag:0,type:{name:'e'},return:cardF}});
host('two','__reactFiber$x1',{tag:5,return:{tag:15,type:{name:'Xy'},return:cardF}});
host('memo','__reactFiber$x1',{tag:5,return:{tag:15,type:{name:'Row'},return:{tag:14,type:{type:{name:'Row'}},return:cardF}}});
host('same','__reactFiber$x1',{tag:5,return:{tag:0,type:{name:'Same'},return:{tag:0,type:{name:'Same'},return:cardF}}});
host('old','__reactInternalInstance$y',{tag:5,return:{tag:1,type:{displayName:'Legacy'},return:cardF}});
</script>"""

    def test_fake_fibers_give_component_names_outer_first(self):
        s = self.snap(self.FIBERS)
        self.assertEqual(self.node(s, "div#card")["components"], ["Frame", "Card"], "корень компонентов: внешний → внутренний; провайдер (tag 10) не компонент")
        self.assertEqual(self.node(s, "button#btn")["components"], ["Button"], "поднимаемся только до родительского хоста")
        self.assertEqual(self.node(s, "u#memo")["components"], ["Row"], "memo-обёртка (tag 14) имени не добавляет")
        self.assertEqual(self.node(s, "s#same")["components"], ["Same"], "подряд идущие одинаковые имена схлопнуты")
        self.assertEqual(self.node(s, "em#old")["components"], ["Legacy"], "React 16: __reactInternalInstance$, displayName")

    def test_a_minified_name_is_not_a_component(self):
        s = self.snap(self.FIBERS)
        self.assertNotIn("components", self.node(s, "i#min"), "однобуквенное имя — не компонент")
        self.assertNotIn("components", self.node(s, "i#two"), "двухбуквенное — тоже минифицированное")
        self.assertEqual(s["react"], {"found": True, "fibers": 7, "named": 6, "minified": 2})

    def test_react_found_is_false_when_there_are_no_usable_names(self):
        s = self.snap("<p>plain</p>")
        self.assertEqual(s["react"], {"found": False, "fibers": 0, "named": 0, "minified": 0})
        prod = self.snap("<div id=a></div><script>document.getElementById('a')['__reactFiber$z']={tag:5,return:{tag:0,type:{name:'t'},return:{tag:3}}};</script>")
        self.assertEqual(prod["react"], {"found": False, "fibers": 1, "named": 0, "minified": 1},
                         "прод-сборка: fiber есть, имён нет — гейт идентичности НЕ исполнялся")

    # ── stamps ──
    def test_stamps_live_outside_the_tree(self):
        s = self.snap('<div id=s1 data-daiver-id="ref-42" style="width:10px;height:10px;background:#000"></div><p id=s2 data-daiver-id="ref-43">t</p>')
        n1, n2 = self.node(s, "div#s1"), self.node(s, "p#s2")
        self.assertEqual(s["stamps"], {n1["id"]: "ref-42", n2["id"]: "ref-43"})
        tree = json.dumps(s["root"])
        self.assertNotIn("ref-42", tree)
        self.assertNotIn("ref-43", tree)
        self.assertNotIn("daiver", tree)
        self.assertFalse(any("stamp" in k.lower() for n in walk(s["root"]) for k in n))

    # ── стили ──
    def test_radius_is_what_the_browser_draws(self):
        s = self.snap('<div id=pill style="width:100px;height:40px;border-radius:9999px;background:#000"></div>'
                      '<div id=circle style="width:80px;height:80px;border-radius:50%;background:#000"></div>'
                      '<div id=corners style="width:80px;height:80px;border-radius:4px 8px 12px 16px;background:#000"></div>'
                      '<div id=sq style="width:80px;height:80px;background:#000"></div>')
        self.assertEqual(self.node(s, "div#pill")["style"]["radius"], 20, "9999px рисуется как половина меньшей стороны")
        self.assertEqual(self.node(s, "div#circle")["style"]["radius"], 40)
        self.assertEqual(self.node(s, "div#corners")["style"]["radius"], [4, 8, 12, 16])
        self.assertNotIn("radius", self.node(s, "div#sq")["style"])

    def test_shadows_are_structured_with_hex_colors(self):
        s = self.snap('<div id=sh style="width:50px;height:50px;box-shadow:0 4px 6px rgba(0,0,0,.1), inset 0 0 0 1px #f00"></div>')
        self.assertEqual(self.node(s, "div#sh")["style"]["shadow"], [
            {"x": 0, "y": 4, "blur": 6, "spread": 0, "color": "#0000001A", "inset": False},
            {"x": 0, "y": 0, "blur": 0, "spread": 1, "color": "#FF0000", "inset": True}])

    def test_border_per_side_and_styles(self):
        s = self.snap('<div id=bb style="width:50px;height:50px;border-bottom:2px dashed #000"></div>'
                      '<div id=none style="width:50px;height:50px;border:3px none #000;background:#fff"></div>')
        self.assertEqual(self.node(s, "div#bb")["style"]["border"], {"width": {"top": 0, "right": 0, "bottom": 2, "left": 0}, "color": "#000000", "style": "dashed"})
        self.assertNotIn("border", self.node(s, "div#none")["style"], "border-style:none — рамки нет, ширина не считается")

    def test_background_image_is_flagged_and_counted(self):
        s = self.snap('<div id=bi style="width:30px;height:30px;background-image:url(data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7)"></div>')
        b = self.node(s, "div#bi")
        self.assertTrue(b["bgImage"])
        self.assertEqual(b["kind"], "rect")
        self.assertEqual(s["bgImages"], 1)

    # ── раскладка ──
    def test_flex_layout_is_stated(self):
        s = self.snap('<div id=fx style="display:flex;flex-direction:column;gap:12px;align-items:center;justify-content:space-between;padding:8px;height:200px">'
                      '<i>a</i><i>b</i></div>'
                      '<div id=wr style="display:flex;flex-wrap:wrap;gap:4px 10px"><i>a</i><i>b</i></div>')
        fx = self.node(s, "div#fx")["layout"]
        self.assertEqual(fx, {"stated": True, "direction": "column", "gap": 12, "padding": {"top": 8, "right": 8, "bottom": 8, "left": 8},
                              "align": {"main": "space-between", "cross": "center"}})
        wr = self.node(s, "div#wr")["layout"]
        self.assertEqual((wr["direction"], wr["gap"], wr["crossGap"], wr["wrap"]), ("row", 10, 4, True))

    def test_grid_layout_has_a_columns_hint(self):
        s = self.snap('<div id=gr style="display:grid;grid-template-columns:repeat(3,100px);gap:5px"><i>a</i><i>b</i><i>c</i><i>d</i></div>')
        gr = self.node(s, "div#gr")
        self.assertEqual(gr["layoutHints"], {"grid": {"confidence": "high", "columns": 3}})
        self.assertEqual((gr["layout"]["direction"], gr["layout"]["wrap"], gr["layout"]["gap"]), ("row", True, 5))
        self.assertNotIn("_grid", gr["layout"])

    def test_block_layout_is_inferred_from_the_boxes_of_children(self):
        s = self.snap('<div id=col><div style="height:20px;margin-bottom:10px;background:#eee"></div><div style="height:20px;margin-bottom:10px;background:#eee"></div>'
                      '<div style="height:20px;background:#eee"></div></div>'
                      '<div id=row><span style="display:inline-block;width:20px;height:20px;margin-right:6px;background:#ccc"></span>'
                      '<span style="display:inline-block;width:20px;height:20px;margin-right:6px;background:#ccc"></span></div>')
        col = self.node(s, "div#col")["layout"]
        self.assertEqual(col, {"inferred": True, "direction": "column", "gap": 10})
        row = self.node(s, "div#row")["layout"]
        self.assertEqual((row["inferred"], row["direction"], row["gap"]), (True, "row", 6))
        self.assertNotIn("stated", col)

    def test_uneven_gaps_are_not_invented(self):
        s = self.snap('<div id=u><div style="height:20px;margin-bottom:5px;background:#eee"></div><div style="height:20px;margin-bottom:30px;background:#eee"></div>'
                      '<div style="height:20px;background:#eee"></div></div>')
        self.assertNotIn("gap", self.node(s, "div#u")["layout"])

    # ── корень, возможности страницы ──
    def test_root_selector_makes_the_subtree_the_document(self):
        body = '<p>outside</p><div id=dlg style="width:200px;height:100px;background:#fff"><h2 id=t>Заголовок</h2></div>'
        s = self.snap(body, opts={"rootSelector": "#dlg"})
        self.assertEqual(s["root"]["name"], "div#dlg")
        self.assertEqual(self.texts(s), ["Заголовок"])
        self.assertEqual(s["root"]["id"], "dom:0.1.1", "id те же, что при снимке всей страницы")
        gone = self.snap(body, opts={"rootSelector": "#nope"})
        self.assertTrue(gone["rootMissing"])
        self.assertIsNone(gone["root"])
        bad = self.snap(body, opts={"rootSelector": "##"})
        self.assertTrue(bad["rootMissing"], "некорректный селектор — не исключение")

    def test_the_snapshot_reports_page_facts(self):
        s = self.snap("<p>x</p>")
        self.assertEqual(s["v"], 1)
        self.assertEqual(s["viewport"], {"w": 800, "h": 600})
        self.assertEqual(s["fonts"]["used"], ["Arial"])
        self.assertEqual(s["stamps"], {})
        self.assertTrue(s["url"].startswith("http://127.0.0.1:"))

    # ── готовность в странице ──
    def poll(self, body: str, head: str = "") -> Dict[str, Any]:
        page = self.page(body, head)
        page.evaluate("window.__daiver = (" + SRC + ")(); window.__daiver.install()")
        return page.evaluate("window.__daiver.poll()")

    def loading(self, body: str, head: str = "") -> List[str]:
        return [x["reason"] for x in self.poll(body, head)["loading"]]

    def test_loader_signs_are_seen(self):
        box = "width:10px;height:10px"
        self.assertEqual(self.loading(f'<div class="spinner" style="{box}"></div>'), ["loader"])
        self.assertEqual(self.loading(f'<div class="MuiSkeleton-root" style="{box}"></div>'), ["loader"])
        self.assertEqual(self.loading(f'<div class="btn isLoading" style="{box}"></div>'), ["loader"], "camelCase")
        self.assertEqual(self.loading(f'<div data-testid="page-loader" style="{box}"></div>'), ["loader"])
        self.assertEqual(self.loading(f'<div id="skeleton-1" style="{box}"></div>'), ["loader"])
        self.assertEqual(self.loading('<div aria-busy="true"></div>'), ["aria-busy"])
        self.assertEqual(self.loading('<div role="progressbar"></div>'.replace("<div", f'<div style="{box}"')), ["progressbar"])
        self.assertEqual(self.loading(f'<progress style="{box}"></progress>'), ["progressbar"])

    def test_what_looks_similar_is_not_a_loader(self):
        box = "width:10px;height:10px"
        self.assertEqual(self.loading(f'<div class="loaded downloaded upload-btn pinned broadcast" style="{box}"></div>'), [])
        self.assertEqual(self.loading(f'<div class="spinner" style="display:none"></div>'), [], "скрытый спиннер не в счёт")
        self.assertEqual(self.loading(f'<div class="spinner" style="{box};visibility:hidden"></div>'), [])
        self.assertEqual(self.loading(f'<div class="spinner" style="{box};opacity:0"></div>'), [])
        self.assertEqual(self.loading(f'<div class="spinner"></div>'), [], "нулевой размер — не виден")
        self.assertEqual(self.loading(f'<div role="progressbar" aria-valuenow="40" style="{box}"></div>'), [], "определённый прогресс — не загрузка страницы")
        self.assertEqual(self.loading(f'<progress value="3" max="10" style="{box}"></progress>'), [])
        self.assertEqual(self.loading('<p>text</p>'), [])

    def test_an_unfinished_image_is_loading(self):
        import socket
        hang = socket.socket()
        hang.bind(("127.0.0.1", 0))
        hang.listen(5)                                  # принимает соединения и молчит: картинка не докачивается
        self.addCleanup(hang.close)
        port = hang.getsockname()[1]
        page = self.chrome.new_page("about:blank")
        self.addCleanup(page.close)
        page.navigate(self.site.put(STYLE + f'<img width=20 height=20 src="http://127.0.0.1:{port}/never.png">'), wait="domcontentloaded")
        page.evaluate("window.__daiver = (" + SRC + ")(); window.__daiver.install()")
        reasons = [x["reason"] for x in page.evaluate("window.__daiver.poll()")["loading"]]
        self.assertIn("image-loading", reasons)
        self.assertIn("document", reasons, "load ещё не случился: документ не complete")

    def test_dom_changes_reset_the_quiet_clock_and_are_counted(self):
        page = self.page("<p id=p>a</p>")
        page.evaluate("window.__daiver = (" + SRC + ")(); window.__daiver.install()")
        first = page.evaluate("window.__daiver.poll()")
        page.send("Runtime.evaluate", {"expression": "new Promise(r => setTimeout(r, 700))", "awaitPromise": True})
        quiet = page.evaluate("window.__daiver.poll()")
        self.assertGreaterEqual(quiet["sinceMutationMs"], first["sinceMutationMs"] + 500)
        page.evaluate("document.getElementById('p').textContent = 'changed'")
        after = page.evaluate("window.__daiver.poll()")
        self.assertLess(after["sinceMutationMs"], 400, "после правки DOM тишина начинается заново")
        self.assertGreater(after["mutations"], quiet["mutations"])
        self.assertNotEqual(after["sig"], quiet["sig"], "подпись содержимого изменилась")

    def test_the_snapshot_itself_does_not_count_as_a_dom_change(self):
        page = self.page('<div class=n>Hello</div>', head="<style>.n{font:20px Arial;line-height:normal}</style>")
        page.evaluate("window.__daiver = (" + SRC + ")(); window.__daiver.install()")
        before = page.evaluate("window.__daiver.poll()")["mutations"]
        page.evaluate("window.__daiver.snapshot({})")
        page.send("Runtime.evaluate", {"expression": "new Promise(r => setTimeout(r, 50))", "awaitPromise": True})
        self.assertEqual(page.evaluate("window.__daiver.poll()")["mutations"], before, "проба line-height не мешает тишине")

    # ── помощники рецепта и шрифты ──
    def test_locate_scrolls_and_reports_visibility(self):
        page = self.page('<div style="height:2000px"></div><button id=b style="width:60px;height:20px">B</button><div id=h style="display:none">h</div>')
        page.evaluate("window.__daiver = (" + SRC + ")()")
        r = page.evaluate("window.__daiver.locate('#b', true)")
        self.assertTrue(r["found"] and r["visible"])
        self.assertTrue(0 <= r["y"] <= 600, "прокручено в видимую область, координаты — окна")
        self.assertGreater(r["box"]["y"], 1900, "box — в координатах страницы")
        self.assertEqual(page.evaluate("window.__daiver.locate('#nope', false)"), {"found": False})
        self.assertFalse(page.evaluate("window.__daiver.locate('#h', false)")["visible"])
        self.assertTrue(page.evaluate("window.__daiver.locate('##', false)")["bad"])
        self.assertTrue(page.evaluate("window.__daiver.gone('#h')")["gone"])
        self.assertTrue(page.evaluate("window.__daiver.gone('#nope')")["gone"])
        self.assertFalse(page.evaluate("window.__daiver.gone('#b')")["gone"])

    def test_fonts_a_missing_family_is_false_and_a_declared_one_follows_the_browser(self):
        page = self.page("<p style=\"font-family:'Local Face'\">x</p>",
                         head='<style>@font-face{font-family:"Local Face";src:local("Arial"),local("Helvetica"),local("DejaVu Sans"),local("Liberation Sans")}'
                              '@font-face{font-family:"Broken Face";src:url(/nope.woff2)}</style>')
        page.evaluate("window.__daiver = (" + SRC + ")()")
        got = page.evaluate('window.__daiver.fonts(["Definitely Not A Font 123", "Broken Face", "Local Face"])', await_promise=True, timeout=10)
        self.assertFalse(got["Definitely Not A Font 123"])
        self.assertFalse(got["Broken Face"], "объявлен, но не загрузился")
        oracle = page.evaluate("document.fonts.load('16px \"Local Face\"').then(f => f.length > 0)", await_promise=True)
        self.assertEqual(got["Local Face"], oracle, "объявленный шрифт: ответ совпадает с тем, что загрузил сам браузер")


class CaptureRun(unittest.TestCase):
    """Один вызов целиком: настоящий браузер на каждый вызов (так работает инструмент), синтетический сайт на 127.0.0.1."""

    @classmethod
    def setUpClass(cls):
        skip_without_browser(cls)
        cls.site = Site()
        cls.addClassCleanup(cls.site.close)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = make_repo(Path(self.tmp.name).resolve())
        self.root = self.r["root"]
        self.n = 0

    def it(self) -> Path:
        self.n += 1
        return self.r["paths"]["verify"] / f"iter-{self.n}"

    def shoot(self, html: str, **kw: Any) -> Dict[str, Any]:
        args: Dict[str, Any] = dict(track="T-1", frame=None, url=self.site.put(STYLE + html), iteration_dir=self.it(), wait_ms=6000)
        args.update(kw)
        try:
            out = CP.run(self.root, **args)
        except CD.BrowserLaunchError as e:
            if REQUIRE_BROWSER:
                raise
            self.skipTest(f"Chrome не запустился: {e}")
        self.assertNotIn("blocked", out, out)
        self.last_dir = Path(args["iteration_dir"])
        return out

    def snapshot_file(self, name: str = "snapshot.json") -> Dict[str, Any]:
        return json.loads((self.last_dir / name).read_text(encoding="utf-8"))

    SPINNER = ('<div id=c style="padding:16px;background:#f3f4f6;width:300px"><h1 id=t>Title</h1><div id=sp class="spinner" '
               'style="width:20px;height:20px;border:2px solid #ccc;border-radius:50%"></div></div>')

    def test_the_spinner_goes_away_and_the_capture_is_ready_with_all_three_files(self):
        out = self.shoot(self.SPINNER + "<script>setTimeout(function(){document.getElementById('sp').remove();"
                         "var p=document.createElement('p');p.id='done';p.textContent='Loaded';document.getElementById('c').appendChild(p)},1300)</script>")
        self.assertTrue(out["ready"], out["readiness"])
        self.assertGreaterEqual(out["readiness"]["waitedMs"], 1300, "спиннер держал страницу")
        names = sorted(p.name for p in self.last_dir.iterdir())
        self.assertEqual(names, ["actual.html", "actual.png", "snapshot.json"], "один вызов — три файла, без временных")
        self.assertEqual(out["files"], {k: f".daiver/tracks/T-1/verify/iter-{self.n}/{v}" for k, v in
                                        {"png": "actual.png", "html": "actual.html", "snapshot": "snapshot.json"}.items()})
        snap = self.snapshot_file()
        self.assertIn("Loaded", [n.get("text") for n in walk(snap["root"])])
        self.assertNotIn("sp", json.dumps(snap["root"]).replace("spinner", ""), "спиннера в снимке нет")
        html = (self.last_dir / "actual.html").read_text(encoding="utf-8")
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("Loaded", html)
        png = (self.last_dir / "actual.png").read_bytes()
        self.assertEqual(CP.png_size(png), {"w": 1280, "h": 900}, "без clip — весь viewport из конфига")
        self.assertEqual(out["png"], {"w": 1280, "h": 900})
        self.assertEqual(snap["capture"]["ready"], True)
        self.assertIn("fingerprint", snap["capture"])
        self.assertEqual(snap["capture"]["viewport"], {"width": 1280, "height": 900})

    def test_a_page_that_never_loads_is_ready_false_but_still_written(self):
        out = self.shoot(self.SPINNER, wait_ms=2200)
        self.assertFalse(out["ready"])
        self.assertTrue(any("признак загрузки: loader" in r for r in out["reasons"]) if "reasons" in out else
                        any("признак загрузки: loader" in r for r in out["readiness"]["reasons"]), out["readiness"])
        self.assertLessEqual(out["readiness"]["waitedMs"], 2400)
        self.assertIn("НЕ готова", out["nextStep"])
        self.assertIn("НЕ сравнивай", out["nextStep"])
        self.assertTrue((self.last_dir / "actual.png").is_file(), "агенту нужно видеть, что там")
        self.assertFalse(self.snapshot_file()["capture"]["ready"])

    def test_the_identical_second_call_is_refused_and_a_raised_wait_is_not(self):
        url = self.site.put(STYLE + "<p>x</p>")
        d = self.it()
        first = CP.run(self.root, "T-1", None, url, d, 2500)
        self.assertTrue(first["ready"])
        with self.assertRaisesRegex(CP.CaptureError, "отказ"):
            CP.run(self.root, "T-1", None, url, d, 2500)
        again = CP.run(self.root, "T-1", None, url, d, 3000)
        self.assertTrue(again["ready"])
        with self.assertRaisesRegex(CP.CaptureError, "отказ"):
            CP.run(self.root, "T-1", None, url, d, 3000)

    def test_clip_captures_the_element_and_the_snapshot_is_its_subtree(self):
        out = self.shoot('<p>outside</p><div id=dlg style="margin:30px 50px;width:240px;height:120px;background:#fff"><h2 id=t>Заголовок</h2></div>',
                         clip_selector="#dlg")
        self.assertEqual(out["clip"], {"x": 50, "y": 66, "width": 240, "height": 120})
        self.assertEqual(CP.png_size((self.last_dir / "actual.png").read_bytes()), {"w": 240, "h": 120})
        snap = self.snapshot_file()
        self.assertEqual(snap["root"]["name"], "div#dlg")
        self.assertEqual([n.get("text") for n in walk(snap["root"]) if "text" in n], ["Заголовок"])
        self.assertEqual((snap["root"]["box"]["x"], snap["root"]["box"]["y"]), (50, 66), "box остаётся в координатах страницы")

    def test_clip_beyond_the_viewport_and_below_the_fold_is_captured_whole(self):
        out = self.shoot('<div style="height:1500px"></div><div id=tall style="width:200px;height:1200px;background:#ccc"></div>', clip_selector="#tall")
        self.assertEqual(out["png"], {"w": 200, "h": 1200}, "элемент выше окна снимается целиком (captureBeyondViewport)")
        self.assertEqual(out["clip"]["y"], 1500)

    def test_a_missing_clip_element_is_refused_not_replaced_by_the_whole_page(self):
        with self.assertRaisesRegex(CP.CaptureError, "clipSelector «#nope».*нет"):
            self.shoot("<p>x</p>", clip_selector="#nope", wait_ms=2000)
        with self.assertRaisesRegex(CP.CaptureError, "нулевой размер"):
            self.shoot('<div id=z style="width:0;height:0"></div>', clip_selector="#z", wait_ms=2000)
        with self.assertRaisesRegex(CP.CaptureError, "некорректен"):
            self.shoot("<p>x</p>", clip_selector="##", wait_ms=2000)

    DIALOG_PAGE = """<button id=open style="width:80px;height:30px">Open</button>
<div id=dlg style="display:none;width:260px;height:110px;background:#fff;padding:10px"><h2 id=title>Диалог</h2><input id=name style="width:120px"><span id=echo></span>
<select id=pick><option value=a>Alpha</option><option value=b>Beta</option></select><span id=picked></span></div>
<script>
document.getElementById('open').onclick=function(){document.getElementById('dlg').style.display='block'};
document.getElementById('name').oninput=function(e){document.getElementById('echo').textContent='typed:'+e.target.value};
document.getElementById('name').onkeydown=function(e){if(e.key==='Enter')document.getElementById('echo').textContent+=' [enter]'};
document.getElementById('pick').onchange=function(e){document.getElementById('picked').textContent='picked:'+e.target.value};
</script>"""

    def test_the_recipe_opens_the_dialog_and_plays_every_verb(self):
        actions = [{"verb": "click", "selector": "#open"}, {"verb": "waitFor", "selector": "#dlg", "ms": 3000},
                   {"verb": "fill", "selector": "#name", "text": "Привет"}, {"verb": "press", "key": "Enter", "selector": "#name"},
                   {"verb": "select", "selector": "#pick", "value": "Beta"}, {"verb": "hover", "selector": "#title"},
                   {"verb": "dblclick", "selector": "#title"}, {"verb": "scrollTo", "selector": "#title"},
                   {"verb": "wait", "ms": 50}, {"verb": "waitForGone", "selector": "#nothing-like-this", "ms": 500}]
        out = self.shoot(self.DIALOG_PAGE, actions=actions, clip_selector="#dlg")
        self.assertTrue(out["ready"], out["readiness"])
        self.assertEqual(out["readiness"]["actionsPlayed"], len(actions))
        texts = [n.get("text") for n in walk(self.snapshot_file()["root"]) if "text" in n]
        self.assertIn("Диалог", texts, "диалог открыт кликом")
        self.assertIn("typed:Привет [enter]", texts, "fill вводит как пользователь (событие input), press Enter доходит до страницы")
        self.assertIn("picked:b", texts, "select по тексту пункта")
        self.assertEqual(CP.png_size((self.last_dir / "actual.png").read_bytes()), {"w": out["clip"]["width"], "h": out["clip"]["height"]})
        self.assertEqual(self.snapshot_file()["capture"]["recipe"]["actions"], actions, "рецепт записан рядом со снимком")

    def test_fill_replaces_the_previous_value(self):
        page = '<input id=i value="old text" style="width:150px"><span id=echo></span><script>document.getElementById("i").oninput=function(e){document.getElementById("echo").textContent="v="+e.target.value}</script>'
        self.shoot(page, actions=[{"verb": "fill", "selector": "#i", "text": "new"}])
        texts = [n.get("text") for n in walk(self.snapshot_file()["root"]) if "text" in n]
        self.assertIn("v=new", texts, "старое значение выделено и заменено, не дописано")
        self.assertIn("new", texts)

    def test_a_page_dialog_is_closed_by_itself_and_reported(self):
        out = self.shoot('<button id=b style="width:60px;height:20px" onclick="alert(\'hello alert\')">A</button>',
                         actions=[{"verb": "click", "selector": "#b"}])
        self.assertTrue(out["ready"], out["readiness"])
        self.assertEqual([(d["type"], d["message"]) for d in out["dialogs"]], [("alert", "hello alert")])

    def test_a_failed_step_names_its_number_verb_and_selector(self):
        with self.assertRaisesRegex(CP.CaptureError, r"рецепт, шаг 2 \(waitFor #ghost\).*не появился|рецепт, шаг 2 \(waitFor #ghost\).*нет"):
            self.shoot("<p id=x>x</p>", actions=[{"verb": "hover", "selector": "#x"}, {"verb": "waitFor", "selector": "#ghost", "ms": 300}],
                       wait_ms=2500)
        with self.assertRaisesRegex(CP.CaptureError, r"шаг 1 \(click ##\).*некорректен"):
            self.shoot("<p>x</p>", actions=[{"verb": "click", "selector": "##"}], wait_ms=2500)
        with self.assertRaisesRegex(CP.CaptureError, r"шаг 1 \(press\).*клавиша «Nope»"):
            self.shoot("<p>x</p>", actions=[{"verb": "press", "key": "Nope"}], wait_ms=2500)
        with self.assertRaisesRegex(CP.CaptureError, r"шаг 1 \(select #s\).*нет пункта"):
            self.shoot("<select id=s><option>a</option></select>", actions=[{"verb": "select", "selector": "#s", "value": "zzz"}], wait_ms=2500)
        with self.assertRaisesRegex(CP.CaptureError, r"шаг 1 \(fill #d\).*не поле ввода"):
            self.shoot("<div id=d tabindex=-1>d</div>", actions=[{"verb": "fill", "selector": "#d", "text": "x"}], wait_ms=2500)

    def test_an_injection_through_a_selector_does_not_run_as_code(self):
        evil = 'a"); window.__hacked = 1; ("'
        with self.assertRaises(CP.CaptureError):
            self.shoot("<p>x</p>", actions=[{"verb": "click", "selector": evil}], wait_ms=2500)
        out = self.shoot("<p id=x>x</p>", clip_selector="#x", wait_ms=2500)
        self.assertTrue(out["ready"])
        # селектор попадает в страницу только json-литералом: проверяем форму вызова напрямую
        self.assertEqual(CP._js(evil), json.dumps(evil, ensure_ascii=True))
        self.assertNotIn("\n", CP._js("a\nb"))
        self.assertNotIn(" ", CP._js("a b"))

    def test_the_recipe_is_not_played_on_a_page_that_never_got_ready(self):
        out = self.shoot(self.SPINNER + "<button id=b>B</button>", actions=[{"verb": "click", "selector": "#b"}], wait_ms=2200)
        self.assertFalse(out["ready"])
        self.assertEqual(out["readiness"]["actionsPlayed"], 0)
        self.assertTrue(any("рецепт не проигран" in r for r in out["readiness"]["reasons"]))

    def test_wait_for_gone_names_a_loader_the_page_does_not_betray(self):
        page = ('<div id=c>content</div><div id=quiet style="width:20px;height:20px;background:#ccc"></div>'
                "<script>setTimeout(function(){document.getElementById('quiet').remove()},1800)</script>")
        out = self.shoot(page, wait_for_gone="#quiet")
        self.assertTrue(out["ready"], out["readiness"])
        self.assertNotIn("div#quiet", [n["name"] for n in walk(self.snapshot_file()["root"])])
        out = self.shoot('<div id=quiet style="width:20px;height:20px;background:#ccc"></div>', wait_for_gone="#quiet", wait_ms=2200)
        self.assertFalse(out["ready"])
        self.assertTrue(any("waitForGone" in r and "не исчез" in r for r in out["readiness"]["reasons"]), out["readiness"])

    def test_wait_for_selector_is_a_ready_condition(self):
        late = "<script>setTimeout(function(){var d=document.createElement('div');d.id='late';d.textContent='L';document.body.appendChild(d)},1700)</script>"
        out = self.shoot("<p>x</p>" + late, wait_for_selector="#late")
        self.assertTrue(out["ready"], out["readiness"])
        self.assertIn("L", [n.get("text") for n in walk(self.snapshot_file()["root"]) if "text" in n])
        out = self.shoot("<p>x</p>", wait_for_selector="#never", wait_ms=2200)
        self.assertFalse(out["ready"])
        self.assertTrue(any("waitForSelector" in r for r in out["readiness"]["reasons"]))

    def test_a_ticking_page_is_not_ready_and_says_the_dom_keeps_changing(self):
        out = self.shoot("<p id=t>0</p><script>var n=0;setInterval(function(){document.getElementById('t').textContent=++n},80)</script>", wait_ms=2600)
        self.assertFalse(out["ready"])
        self.assertTrue(any("DOM менялся" in r or "содержимое менялось" in r for r in out["readiness"]["reasons"]), out["readiness"])

    def test_an_under_rendered_page_is_flagged_against_the_mockup_node_count(self):
        kids = [{"id": f"n{i}", "kind": "text", "text": f"t{i}", "font": {"family": "Zzz Not Installed Font"}} for i in range(40)]
        env = CN.new_envelope({"id": "root", "kind": "frame", "children": kids}, "pixso")
        CN.write_canonical(self.r["paths"]["canonical"], env)
        out = self.shoot("<p>only one</p>", wait_ms=2500)
        self.assertEqual(out["sanity"]["designNodes"], 41)
        self.assertTrue(out["sanity"]["underRendered"])
        self.assertTrue(any("недорисован" in w for w in out["warnings"]), out["warnings"])
        self.assertTrue(any("«Zzz Not Installed Font»" in w for w in out["warnings"]), "нет шрифта макета — предупреждение, не дефект")
        self.assertEqual(out["fonts"], {"Zzz Not Installed Font": False})
        full = self.shoot("".join(f"<p>t{i}</p>" for i in range(40)), wait_ms=2500)
        self.assertFalse(full["sanity"]["underRendered"])
        self.assertFalse(any("недорисован" in w for w in full["warnings"]))

    def test_an_unreadable_canonical_is_a_note_not_a_failure(self):
        self.r["paths"]["canonical"].write_text("{broken", encoding="utf-8")
        out = self.shoot("<p>x</p>", wait_ms=2500)
        self.assertNotIn("sanity", out)
        self.assertTrue(any("каноническая модель кадра не прочитана" in w for w in out["warnings"]))

    def test_react_names_are_reported_and_their_absence_is_a_warning(self):
        out = self.shoot("<p>plain</p>", wait_ms=2500)
        self.assertFalse(out["react"]["found"])
        self.assertTrue(any("НЕ исполнялась" in w for w in out["warnings"]))
        page = ("<div id=a>a</div><script>document.getElementById('a')['__reactFiber$q']={tag:5,return:{tag:0,type:{name:'Card'},return:{tag:3}}}</script>")
        out = self.shoot(page, wait_ms=2500)
        self.assertTrue(out["react"]["found"])
        self.assertFalse(any("НЕ исполнялась" in w for w in out["warnings"]))
        self.assertEqual([n for n in walk(self.snapshot_file()["root"]) if n.get("name") == "div#a"][0]["components"], ["Card"])

    def test_the_model_built_from_a_real_capture_is_valid_canon(self):
        self.shoot('<div style="display:flex;gap:8px;padding:8px;background:#eee;border-radius:6px"><button style="background:#00f;color:#fff;border:0;padding:4px 8px">Go</button>'
                   '<svg width=10 height=10><rect width=10 height=10 /></svg><img width=10 height=10 alt=x src="data:,"><input value="v"></div><p style="text-transform:uppercase">abc</p>')
        env = IM.to_model(self.snapshot_file())
        self.assertEqual(CN.validate(env), [])
        self.assertEqual(env["daiver"]["sourceFormat"], "dom")
        self.assertTrue(all("box" in n for n in CN.iter_nodes(env["root"])), "box у ВСЕХ узлов модели")

    def test_the_reference_target_writes_its_own_files_and_opens_file_urls_only_inside_the_iteration_dir(self):
        d = self.it()
        d.mkdir(parents=True)
        (d / "reference.html").write_text(STYLE + '<div id=r data-daiver-id="n:1" style="width:50px;height:20px;background:#abc">ref</div>', encoding="utf-8")
        out = CP.run(self.root, "T-1", None, (d / "reference.html").resolve().as_uri(), d, 2500, target="reference")
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["reference-snapshot.json", "reference.html", "reference.png"], "html эталона не перезаписан, actual.* нет")
        self.assertEqual(out["files"], {"png": f".daiver/tracks/T-1/verify/iter-{self.n}/reference.png",
                                        "snapshot": f".daiver/tracks/T-1/verify/iter-{self.n}/reference-snapshot.json"})
        snap = json.loads((d / "reference-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(list(snap["stamps"].values()), ["n:1"], "стампы эталона лежат в снимке — для маркеров отчёта")
        self.assertNotIn("sanity", out)
        outside = Path(self.tmp.name) / "outside.html"
        outside.write_text("<p>x</p>", encoding="utf-8")
        with self.assertRaisesRegex(CP.CaptureError, "адрес не пропущен"):
            CP.run(self.root, "T-1", None, outside.resolve().as_uri(), self.it(), 2500, target="reference")
        with self.assertRaisesRegex(CP.CaptureError, "адрес не пропущен"):
            CP.run(self.root, "T-1", None, (d / "reference.html").resolve().as_uri(), self.it(), 2500)

    def test_an_unreachable_page_is_named_with_the_cure(self):
        with self.assertRaisesRegex(CP.CaptureError, "не открылась.*dev-сервер"):
            self.shoot("<p>x</p>", url="http://127.0.0.1:1/", wait_ms=2500)

    def test_the_tool_runs_end_to_end(self):
        url = self.site.put(STYLE + "<p>via tool</p>")
        out = T.call("daiver_screenshot", self.root, {"track": "T-1", "url": url, "iterationDir": ".daiver/tracks/T-1/verify/iter-1", "waitMs": 2500})
        self.assertTrue(out["ready"])
        self.assertEqual(out["target"], "implementation")
        self.assertTrue((self.root / out["files"]["png"]).is_file())
        with self.assertRaisesRegex(T.ToolError, "отказ"):
            T.call("daiver_screenshot", self.root, {"track": "T-1", "url": url, "iterationDir": ".daiver/tracks/T-1/verify/iter-1", "waitMs": 2500})

    def test_no_process_or_profile_is_left_behind(self):
        before = {p for p in Path(tempfile.gettempdir()).glob(CD.PROFILE_PREFIX + "*")}
        self.shoot("<p>x</p>", wait_ms=2500)
        with self.assertRaises(CP.CaptureError):
            self.shoot("<p>x</p>", clip_selector="#nope", wait_ms=2000)
        after = {p for p in Path(tempfile.gettempdir()).glob(CD.PROFILE_PREFIX + "*")}
        self.assertEqual(after - before, set(), "профиль браузера удалён и после отказа")


if __name__ == "__main__":
    unittest.main()
