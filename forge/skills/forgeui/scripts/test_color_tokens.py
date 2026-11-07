#!/usr/bin/env python3
"""test_color_tokens.py — daiver_color_tokens: карта «цвет макета → переменная проекта» (задача 3.2; спека §13.2 п.2, §13.5 п.9; INV-B18, A1).

Пинит как свойства КОДА (не текста брифа):
  • цвет приводится к виду канона `rgba(R, G, B, A)` (`canon.fmt_rgba`): регистр hex, короткая форма (#FFF), запятые или пробелы и «/ альфа»,
    альфа числом или процентом — один и тот же цвет; канонические строки переводятся в себя (идемпотентность); не литерал цвета
    (hsl(), darken(), #ggg, канал > 255) — None, а не догадка;
  • ТОЧНОЕ совпадение (INV-B18): цвет, отличный на единицу канала или на тысячную альфы, — другой цвет, переменная для него не находится;
    округление альфы — до 3 знаков, как в каноне;
  • разбор файла: @less, $scss (флаги !default/!global), --css (в т.ч. в одну строку), комментарии, алиасы цепочкой и через разные записи,
    циклы и слишком длинные цепочки, переменные с РАЗНЫМИ значениями (конфликт — в карту не входят, называются), функции и карты
    (пропущены с причиной), первая по файлу переменная для значения с литералом раньше алиаса, мусорный вход не роняет и не виснет;
  • цвета экрана — из канона: фон, градиент, цвет обводки, цвета внутри строки тени, цвет текста; occluded и библиотечные чёрные ящики
    не в счёт, локальная композиция — в счёт; порядок документа, без повторов; тексты макета цветами не считаются;
  • инструмент: «Modal» из Pixso (4 цвета экрана — фон, обводка, две тени), файл цветов в репо; нет colorsFile / формат вне .less .scss
    .css — все цвета сырые, без ошибки; файла нет, путь вне репо, не UTF-8, слишком большой — отказ с лечением; ответ ничего не пишет,
    детерминирован и короткий при любом числе переменных; схема закрыта (произвольных путей нет), инструмент в реестре хука.

Данные. Цвета «Modal» (`rgba(255, 255, 255, 1)`, `rgba(31, 20, 51, 0.12)`, тени `rgba(11, 11, 30, 0.161)` и `rgba(11, 11, 30, 0.039)`) — из настоящего
образца Pixso. Файлы цветов — СИНТЕТИКА: боевого `colors.less` / `variables.scss` в образцах нет (README → «Нужно от пользователя»); формы
записи выведены из текста спеки. Что НЕ проверено данными: реальный файл цветов команды (темы, функции, интерполяция, вложенные блоки).
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

from daiver import canon as C  # noqa: E402
from daiver import color_tokens as CT  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import tools as T  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
TRACK = "STOR-100"
URL = "https://pixso.example/a?item-id=2:153702"
WHITE, BORDER, SHADOW_BIG, SHADOW_SMALL = ("rgba(255, 255, 255, 1)", "rgba(31, 20, 51, 0.12)", "rgba(11, 11, 30, 0.161)",
                                           "rgba(11, 11, 30, 0.039)")

LESS = """\
// палитра проекта
@white: #FFF;                    // короткая запись, верхний регистр
@ink: #1f1433;
@border: rgba(31,20,51,.12);
@shadow-big: rgba(11, 11, 30, 0.161);
@shadow-small: rgba(11, 11, 30, 0.04);   /* почти 0.039, но НЕ 0.039 */
@surface: @white;
"""


def init_repo(root: Path, **answers: Any) -> None:
    (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text("{}", encoding="utf-8")
    base = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
            "buildCommand": "npm run build", "librarySource": "stories", "scanPaths": ["lib"]}
    res = I.init(root, dict(base, **answers))
    assert res["ok"], res


def put(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def snapshot(root: Path) -> Dict[str, Any]:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def pull_modal(root: Path) -> None:
    inbox = root / ".daiver" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(FIXTURE), str(inbox / "main.dsl.json"))
    T.call("daiver_pixso_pull", root, {"track": TRACK, "dslPath": ".daiver/inbox/main.dsl.json", "frameUrl": URL, "frame": "main"})
    T.call("daiver_normalize", root, {"track": TRACK})


def node(i: str, kind: str = "frame", children=(), **kw) -> dict:
    n = {"id": i, "kind": kind, "name": i}
    n.update(kw)
    if children:
        n["children"] = list(children)
    return n


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()

    def call(self, **args):
        return T.call("daiver_color_tokens", self.root, args)

    def refusal(self, **args) -> str:
        with self.assertRaises(T.ToolError) as cm:
            T.call("daiver_color_tokens", self.root, args)
        return str(cm.exception)


# ── цвет → канон ─────────────────────────────────────────────────────────────

class NormalizeColor(unittest.TestCase):
    def test_hex_in_any_case_and_short_form_is_the_same_canonical_color(self):
        for raw in ("#FFF", "#fff", "#ffffff", "#FFFFFF", " #FfFfFf ", "#ffff", "#ffffffff", "#FFFFFFFF"):
            self.assertEqual(CT.normalize_color(raw), WHITE, raw)

    def test_hex_with_alpha_is_rounded_to_three_places_like_the_canon(self):
        self.assertEqual(CT.normalize_color("#1f143380"), "rgba(31, 20, 51, 0.502)")
        self.assertEqual(CT.normalize_color("#0000"), "rgba(0, 0, 0, 0)")
        self.assertEqual(CT.normalize_color("#f008"), "rgba(255, 0, 0, 0.533)")

    def test_rgb_functions_with_commas_or_spaces_and_alpha_as_number_or_percent(self):
        for raw in ("rgba(31, 20, 51, 0.12)", "rgba(31,20,51,.12)", "RGBA(31 , 20 , 51 , 0.120)", "rgb(31 20 51 / 0.12)",
                    "rgba(31 20 51 / 12%)", "rgb(31 20 51/12%)", "rgba( 31 ,20,51,  0.12 )"):
            self.assertEqual(CT.normalize_color(raw), BORDER, raw)
        self.assertEqual(CT.normalize_color("rgb(31, 20, 51)"), "rgba(31, 20, 51, 1)")

    def test_the_three_color_keywords(self):
        self.assertEqual(CT.normalize_color("White"), WHITE)
        self.assertEqual(CT.normalize_color("BLACK"), "rgba(0, 0, 0, 1)")
        self.assertEqual(CT.normalize_color("transparent"), "rgba(0, 0, 0, 0)")

    def test_a_canonical_string_maps_to_itself(self):
        for canon in (WHITE, BORDER, SHADOW_BIG, SHADOW_SMALL, "rgba(0, 0, 0, 0)", "rgba(255, 0, 7, 0.001)", "rgba(1, 2, 3, 0.5)"):
            self.assertEqual(CT.normalize_color(canon), canon)
            self.assertEqual(C.fmt_rgba(*[float(x) for x in canon[5:-1].split(", ")]), canon, "формат — тот же, что у канона")

    def test_what_is_not_a_color_literal_is_none_not_a_guess(self):
        for raw in ("hsl(0, 0%, 100%)", "darken(@a, 10%)", "#ggg", "#12", "#12345", "rgb(300, 0, 0)", "rgba(1, 2)", "rgba(1,2,3,)",
                    "rgba(1, 2, 3, 4)", "rgba(1, 2, 3, -0.1)", "rgb(10% 20% 30%)", "rgba(#fff, 0.5)", "red", "", "  ", "var(--x)", None, 5,
                    ["#fff"]):
            self.assertIsNone(CT.normalize_color(raw), repr(raw))

    def test_a_near_color_is_another_color(self):
        """INV-B18: ни допуска по каналу, ни по альфе — цвет либо тот же, либо другой."""
        self.assertNotEqual(CT.normalize_color("#fffffe"), WHITE)
        self.assertNotEqual(CT.normalize_color("rgba(31, 20, 51, 0.121)"), BORDER)
        self.assertNotEqual(CT.normalize_color("rgba(31, 20, 52, 0.12)"), BORDER)
        self.assertEqual(CT.normalize_color("rgba(0, 0, 0, 0.1234)"), "rgba(0, 0, 0, 0.123)", "альфа — до 3 знаков, как в каноне")


# ── разбор файла ─────────────────────────────────────────────────────────────

class ParseColors(unittest.TestCase):
    def table(self, text: str, fmt: str = "less") -> Dict[str, str]:
        return CT.parse_colors(text, fmt)["table"]

    def test_less_variables_comments_and_the_exact_match(self):
        r = CT.parse_colors(LESS, "less")
        self.assertEqual(r["table"], {WHITE: "@white", "rgba(31, 20, 51, 1)": "@ink", BORDER: "@border", SHADOW_BIG: "@shadow-big",
                                      "rgba(11, 11, 30, 0.04)": "@shadow-small"})
        self.assertEqual(r["ambiguous"], 1, "@surface — второе имя белого")
        self.assertEqual(CT.lookup(r["table"], SHADOW_SMALL), None, "0.039 — не 0.04: близкий цвет не угадывается")
        self.assertEqual(CT.lookup(r["table"], "#FFFFFF"), "@white", "искомый цвет тоже приводится к канону")
        self.assertEqual(r["conflicts"] + r["skipped"], [])

    def test_the_order_of_the_map_is_the_order_of_the_file(self):
        self.assertEqual(CT.parse_colors(LESS, "less")["order"][:3], [WHITE, "rgba(31, 20, 51, 1)", BORDER])

    def test_scss_flags_and_maps(self):
        r = CT.parse_colors("$primary: #1890FF !default;\n$ink: rgb(0,0,0) !global;\n$palette: (a: #fff, b: #000);\n", "scss")
        self.assertEqual(r["table"], {"rgba(24, 144, 255, 1)": "$primary", "rgba(0, 0, 0, 1)": "$ink"})
        self.assertEqual([n for n, _why in r["skipped"]], ["$palette"], "карта — не литерал: значения из неё не вытаскиваются")

    def test_css_custom_properties_even_on_one_line_and_var_aliases(self):
        r = CT.parse_colors(":root{--a:#fff;--b:#000}\n.card { --c: var(--a); --d : #ABC }", "css")
        self.assertEqual(r["table"], {WHITE: "var(--a)", "rgba(0, 0, 0, 1)": "var(--b)", "rgba(170, 187, 204, 1)": "var(--d)"})
        self.assertEqual(r["ambiguous"], 1)

    def test_alias_chains_resolve_and_cross_the_variable_kinds(self):
        r = CT.parse_colors("@base: #123456;\n@a: @base;\n@b: @a;\n:root { --p: @b; --q: var(--p); }\n", "less")
        self.assertEqual(r["values"], {n: "rgba(18, 52, 86, 1)" for n in ("@base", "@a", "@b", "--p", "--q")})
        self.assertEqual(r["table"], {"rgba(18, 52, 86, 1)": "@base"}, "литерал раньше алиасов: палитра важнее синонимов")

    def test_literal_beats_an_earlier_alias_and_earlier_beats_later(self):
        r = CT.parse_colors("@semantic: @brand;\n@brand: #0af;\n@brand-2: #00aaff;\n", "less")
        self.assertEqual(r["table"], {"rgba(0, 170, 255, 1)": "@brand"})
        self.assertEqual(r["ambiguous"], 1)

    def test_a_variable_with_two_different_values_is_a_conflict_and_does_not_enter_the_map(self):
        r = CT.parse_colors("@a: #111;\n@b: #222;\n@a: #333;\n@c: @a;\n@d: #444;\n", "less")
        self.assertEqual(r["conflicts"], ["@a"])
        self.assertEqual(r["table"], {"rgba(34, 34, 34, 1)": "@b", "rgba(68, 68, 68, 1)": "@d"})
        self.assertEqual([n for n, _w in r["skipped"]], ["@c"], "алиас на неоднозначную переменную тоже не годится")
        r = CT.parse_colors(":root { --bg: #fff; }\n.dark { --bg: #000; }\n", "css")
        self.assertEqual((r["conflicts"], r["table"]), (["--bg"], {}), "тема переопределила значение — какое из двух, не угадать")

    def test_the_same_value_written_differently_is_not_a_conflict(self):
        r = CT.parse_colors("@w: #fff;\n@w: white;\n@w: rgb(255 255 255);\n", "less")
        self.assertEqual((r["conflicts"], r["table"]), ([], {WHITE: "@w"}))

    def test_non_literals_are_skipped_with_a_reason_and_never_guessed(self):
        r = CT.parse_colors("@w: #fff;\n@dark: darken(@w, 10%);\n@s: ~\"calc(1px)\";\n@h: hsl(0, 0%, 50%);\n@n: 12px;\n@u: @missing;\n", "less")
        names = [n for n, _why in r["skipped"]]
        self.assertEqual(names, ["@dark", "@s", "@h", "@n", "@u"])
        self.assertIn("не литерал цвета", r["skipped"][0][1])
        self.assertIn("не определена", r["skipped"][4][1])
        self.assertEqual(r["table"], {WHITE: "@w"})

    def test_alias_cycles_and_too_long_chains_are_skipped_not_looped(self):
        r = CT.parse_colors("@a: @b;\n@b: @a;\n@self: @self;\n", "less")
        self.assertEqual([n for n, _w in r["skipped"]], ["@a", "@b", "@self"])
        top = CT.MAX_DEPTH + 4
        forward = "@v0: #fff;\n" + "".join(f"@v{i}: @v{i - 1};\n" for i in range(1, top + 1))
        backward = "".join(f"@v{i}: @v{i - 1};\n" for i in range(top, 0, -1)) + "@v0: #fff;\n"
        for text in (forward, backward):
            r = CT.parse_colors(text, "less")
            self.assertIn(f"@v{top}", [n for n, _w in r["skipped"]], "слишком длинная цепочка не разворачивается")
            self.assertEqual(r["values"]["@v1"], WHITE)
            self.assertEqual(r["values"]["@v5"], WHITE)
        a, b = CT.parse_colors(forward, "less"), CT.parse_colors(backward, "less")
        self.assertEqual(sorted(a["values"]), sorted(b["values"]), "что разворачивается, не зависит от порядка объявлений в файле")
        self.assertIn(f"@v{CT.MAX_DEPTH}", a["values"], "MAX_DEPTH переходов по алиасам ещё разворачиваются")
        self.assertNotIn(f"@v{CT.MAX_DEPTH + 1}", a["values"], "а MAX_DEPTH + 1 — уже нет")

    def test_comments_are_cut_but_urls_are_not_eaten(self):
        text = "/* @gone: #111; */\n// @gone2: #222;\n@kept: #333; // хвост\n@img: url(http://x.test/a.png);\n@after: #444;\n"
        r = CT.parse_colors(text, "less")
        self.assertEqual(set(r["values"]), {"@kept", "@after"})
        self.assertEqual([n for n, _w in r["skipped"]], ["@img"])
        r = CT.parse_colors("--u: url(//cdn.test/a.png); --a: #111;\n", "css")
        self.assertEqual(r["values"], {"--a": "rgba(17, 17, 17, 1)"}, "в .css `//` не комментарий: хвост строки не отрезается")

    def test_mixin_parameters_and_detached_rulesets_are_not_variables(self):
        text = ".m(@c: #fff) { color: @c; }\n@detached: { background: red; };\n@real: #000;\n.a { @local: #123; }\n"
        r = CT.parse_colors(text, "less")
        self.assertEqual(r["table"], {"rgba(0, 0, 0, 1)": "@real", "rgba(17, 34, 51, 1)": "@local"})

    def test_garbage_does_not_crash_and_does_not_hang(self):
        started = time.monotonic()
        for text in ("", "\x00\x01\x02", "@a:", "@a: ;", "{{{{", "@a: #fff", "@a: #fff }", "$:", "--:", "@: #fff;"):
            CT.parse_colors(text, "less")
        CT.parse_colors("@a:\n" * 30000, "less")                        # нет ни «;», ни «}»: не квадратично
        CT.parse_colors("@a: x\n" * 30000 + "{", "less")
        CT.parse_colors("@a: " + "x" * 1000000, "less")
        self.assertLess(time.monotonic() - started, 10, "разбор мусора не должен зависать")

    def test_the_result_is_a_pure_function_of_the_text(self):
        a, b = CT.parse_colors(LESS, "less"), CT.parse_colors(LESS, "less")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_how_a_variable_is_written_in_code(self):
        self.assertEqual((CT.ref("@x"), CT.ref("$x"), CT.ref("--x")), ("@x", "$x", "var(--x)"))


# ── цвета экрана ─────────────────────────────────────────────────────────────

class ScreenColors(unittest.TestCase):
    def test_background_gradient_border_shadow_and_text_color_in_document_order_without_repeats(self):
        root = node("r", style={"background": "rgba(1, 2, 3, 1)", "border": {"width": 1, "color": "rgba(4, 5, 6, 0.5)"},
                                "shadow": "0px 1px 2px 0px rgba(7, 8, 9, 0.1), 0px 3px 4px 0px rgba(10, 11, 12, 0.2)"},
                    children=[node("t", "text", color="rgba(1, 2, 3, 1)", text="rgba(5, 5, 5, 1)"),
                              node("g", style={"gradient": "linear-gradient(90deg, rgba(20, 21, 22, 1) 0%, rgba(23, 24, 25, 0.5) 100%)"})])
        self.assertEqual(CT.screen_colors(root), ["rgba(1, 2, 3, 1)", "rgba(4, 5, 6, 0.5)", "rgba(7, 8, 9, 0.1)", "rgba(10, 11, 12, 0.2)",
                                                   "rgba(20, 21, 22, 1)", "rgba(23, 24, 25, 0.5)"])

    def test_occluded_black_boxes_and_texts_do_not_count_but_local_composition_does(self):
        lib = node("lib", "instance", component={"figmaName": "Btn", "childrenRole": "content"}, style={"background": "rgba(98, 98, 98, 1)"},
                   children=[node("x", style={"background": "rgba(97, 97, 97, 1)"})])
        local = node("loc", "instance", component={"figmaName": "Card", "childrenRole": "fallback"},
                     children=[node("y", style={"background": "rgba(20, 21, 22, 1)"})])
        occ = node("occ", occluded=True, style={"background": "rgba(99, 99, 99, 1)"},
                   children=[node("z", style={"background": "rgba(96, 96, 96, 1)"})])
        root = node("r", children=[lib, local, occ, node("t", "text", text="rgba(5, 5, 5, 1)")])
        self.assertEqual(CT.screen_colors(root), ["rgba(20, 21, 22, 1)"])

    def test_a_deep_tree_does_not_hit_the_recursion_limit(self):
        n = node("leaf", style={"background": "rgba(1, 1, 1, 1)"})
        for i in range(3000):
            n = node(f"n{i}", children=[n])
        self.assertEqual(CT.screen_colors(n), ["rgba(1, 1, 1, 1)"])

    def test_values_that_are_not_colors_are_ignored(self):
        root = node("r", style={"background": "none", "shadow": "0px 1px 2px 0px rgba(300, 0, 0, 1)", "gradient": "rgba(1, 2)"})
        self.assertEqual(CT.screen_colors(root), [])


# ── файл в репо ──────────────────────────────────────────────────────────────

class ColorsFile(Repo):
    def test_the_file_is_read_inside_the_repo_only_and_the_cause_is_named_with_the_fix(self):
        init_repo(self.root, colorsFile="src/styles/colors.less")
        msg = self.refusal()
        self.assertIn("src/styles/colors.less", msg)
        self.assertIn("файла нет", msg)
        self.assertIn("project.styling.colorsFile", msg)
        self.assertIn("убери ключ", msg, "лечение названо")
        for rel in ("../outside.less", "/etc/hosts", "~/x.less"):
            with self.subTest(rel=rel):
                with self.assertRaises(CT.ColorTokensError) as cm:
                    CT.read_colors_file(self.root, rel)
                self.assertIn(rel, str(cm.exception))

    def test_a_link_out_of_the_repo_is_refused(self):
        init_repo(self.root, colorsFile="src/colors.less")
        outside = Path(self.tmp.name) / "outside.less"
        outside.write_text("@a: #fff;\n", encoding="utf-8")
        try:
            (self.root / "src" / "colors.less").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки недоступны")
        self.assertIn("выходит за корень", self.refusal())

    def test_not_utf8_and_too_big_are_refused_and_a_bom_is_fine(self):
        init_repo(self.root, colorsFile="src/colors.less")
        (self.root / "src" / "colors.less").write_bytes(b"@a: #fff;\n\xff\xfe\xfa")
        self.assertIn("не в UTF-8", self.refusal())
        (self.root / "src" / "colors.less").write_bytes(b"x" * (CT.MAX_BYTES + 1))
        self.assertIn("больше", self.refusal())
        (self.root / "src" / "colors.less").write_bytes("\ufeff@a: #fff;\n".encode("utf-8"))
        self.assertEqual(self.call()["map"], [f"{WHITE} → @a"])

    def test_no_colors_file_in_the_config_means_raw_colors_without_an_error(self):
        init_repo(self.root)
        out = self.call()
        self.assertIsNone(out["colorsFile"])
        self.assertEqual(out["map"], [])
        self.assertIn("сырыми значениями", out["nextStep"])
        self.assertNotIn("variables", out)

    def test_a_format_the_engine_does_not_read_is_named_not_refused(self):
        init_repo(self.root, colorsFile="src/theme.ts")
        put(self.root, "src/theme.ts", "export const colors = { white: '#fff' };\n")
        out = self.call()
        self.assertEqual((out["colorsFile"], out["format"], out["map"]), ("src/theme.ts", None, []))
        self.assertIn(".less, .scss, .css", out["nextStep"])
        self.assertIn("сырыми", out["nextStep"])

    def test_scss_and_css_files_are_read_by_their_extension(self):
        init_repo(self.root, colorsFile="src/vars.scss")
        put(self.root, "src/vars.scss", "$white: #FFF;\n")
        out = self.call()
        self.assertEqual((out["format"], out["variables"], out["map"]), ("scss", 1, [f"{WHITE} → $white"]))
        put(self.root, ".daiver/config.yaml", (self.root / ".daiver" / "config.yaml").read_text(encoding="utf-8")
            .replace("src/vars.scss", "src/vars.css"))
        put(self.root, "src/vars.css", ":root { --white: #fff; }\n")
        out = self.call()
        self.assertEqual((out["format"], out["map"]), ("css", [f"{WHITE} → var(--white)"]))


# ── инструмент на «Modal» ────────────────────────────────────────────────────

class ModalSample(Repo):
    def world(self):
        init_repo(self.root, colorsFile="src/styles/colors.less")
        put(self.root, "src/styles/colors.less", LESS)
        pull_modal(self.root)

    def test_the_four_colors_of_the_sample_are_matched_exactly_and_the_near_one_stays_raw(self):
        self.world()
        out = self.call(track=TRACK)
        self.assertEqual(out["frame"], "main")
        self.assertEqual(out["screenColors"], 4, "фон, обводка и два цвета теней — в образце их нет в каком-либо ином месте")
        self.assertEqual(out["matched"], [f"{WHITE} → @white", f"{BORDER} → @border", f"{SHADOW_BIG} → @shadow-big"])
        self.assertEqual(out["raw"], [SHADOW_SMALL], "0.039 в файле записан как 0.04 — это другой цвет, переменная не подбирается")
        self.assertEqual(out["variables"], 6)
        self.assertEqual(out["ambiguous"], 1)
        self.assertIn("matched", out["nextStep"])
        self.assertNotIn("map", out, "с треком — цвета кадра, а не весь файл")

    def test_without_a_track_the_whole_file_map_is_returned_in_file_order(self):
        self.world()
        out = self.call()
        self.assertEqual(out["map"][:3], [f"{WHITE} → @white", "rgba(31, 20, 51, 1) → @ink", f"{BORDER} → @border"])
        self.assertNotIn("matched", out)

    def test_an_unmatched_screen_without_variables_is_all_raw(self):
        init_repo(self.root, colorsFile="src/colors.less")
        put(self.root, "src/colors.less", "@unrelated: #123456;\n")
        pull_modal(self.root)
        out = self.call(track=TRACK)
        self.assertEqual((out["matched"], out["raw"]), ([], [WHITE, BORDER, SHADOW_BIG, SHADOW_SMALL]))

    def test_the_call_writes_nothing_and_answers_the_same_every_time(self):
        self.world()
        before = snapshot(self.root)
        first = self.call(track=TRACK)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(json.dumps(self.call(track=TRACK), sort_keys=True), json.dumps(first, sort_keys=True))
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        init_repo(other, colorsFile="src/styles/colors.less")
        put(other, "src/styles/colors.less", LESS)
        pull_modal(other)
        self.assertEqual(json.dumps(T.call("daiver_color_tokens", other, {"track": TRACK}), sort_keys=True),
                         json.dumps(first, sort_keys=True), "один вход — побайтно один ответ в любом репо")

    def test_the_answer_stays_short_however_many_variables_the_file_has(self):
        init_repo(self.root, colorsFile="src/colors.less")
        put(self.root, "src/colors.less", "".join(f"@c{i}: rgb({i % 256}, {i // 256}, 7);\n" for i in range(600))
            + "".join(f"@d{i}: darken(@c1, 5%);\n" for i in range(30)) + "@dup: #111;\n@dup: #222;\n")
        pull_modal(self.root)
        out = self.call()
        self.assertEqual(len(out["map"]), CT.MAP_SHOWN)
        self.assertEqual((out["variables"], out["mapMore"]), (600, 600 - CT.MAP_SHOWN))
        self.assertEqual((len(out["skipped"]), out["skippedMore"], out["conflicts"]), (CT.SHOWN, 30 - CT.SHOWN, ["@dup"]),
                         "перечни ограничены, остаток считается")
        self.assertLess(len(json.dumps(out, ensure_ascii=False)), 6000)
        self.assertLess(len(json.dumps(self.call(track=TRACK), ensure_ascii=False)), 4000)

    def test_unknown_track_missing_canon_and_a_frame_without_a_track_are_tool_errors(self):
        self.world()
        self.assertIn("нет", self.refusal(track="STOR-999"))
        self.assertIn("frame называется вместе с track", self.refusal(frame="main"))
        self.assertIn("nope", self.refusal(track=TRACK, frame="nope"))
        shutil.rmtree(str(self.root / ".daiver" / "tracks"))
        self.assertIn("сначала daiver_pixso_pull", self.refusal(track=TRACK))


# ── реестр, схема, гигиена ───────────────────────────────────────────────────

class Registry(unittest.TestCase):
    def test_the_tool_is_registered_with_a_closed_schema_and_no_paths(self):
        tool = T.TOOLS["daiver_color_tokens"]
        schema = tool["schema"]
        self.assertEqual(sorted(schema["properties"]), ["frame", "track"])
        self.assertNotIn("required", schema, "вызов без аргументов — карта файла: так он и называется в задаче")
        self.assertIs(schema["additionalProperties"], False)
        for forbidden in ("path", "file", "colorsFile", "outPath", "colors"):
            self.assertNotIn(forbidden, schema["properties"], "файл цветов выбирает конфиг, а не модель")

    def test_the_description_carries_the_rules_the_model_must_follow(self):
        d = T.TOOLS["daiver_color_tokens"]["description"]
        for part in ("точном совпадении", "не подбирай", "matched", "raw", "conflicts", "ambiguous", "ОДИН вызов"):
            self.assertIn(part, d)

    def test_the_hook_inventory_lists_it(self):
        text = (HERE.parents[2] / "hooks" / "test_daiver_mcp.py").read_text(encoding="utf-8")
        self.assertIn('"daiver_color_tokens"', text)

    def test_argument_errors_are_tool_errors_not_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for args in ({"bogus": 1}, {"track": 5}, {"track": "A", "frame": 5}, {"track": "../x"}, {"frame": "main"}):
                with self.subTest(args=args):
                    with self.assertRaises(T.ToolError):
                        T.call("daiver_color_tokens", Path(tmp), args)


class Hygiene(unittest.TestCase):
    PATH = HERE / "daiver" / "color_tokens.py"

    def test_the_module_only_reads_has_no_time_or_randomness_and_keeps_the_python_floor(self):
        text = self.PATH.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(self.PATH))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
        self.assertFalse(imported & {"time", "datetime", "random", "uuid", "secrets", "subprocess", "os"}, sorted(imported))
        self.assertIn("from __future__ import annotations", text)
        for needle in (".write_text(", ".write_bytes(", "os.environ", "open("):
            self.assertNotIn(needle, text, f"модуль только читает: {needle}")
        for n in ast.walk(tree):
            self.assertNotIsInstance(n, ast.NamedExpr, f"{getattr(n, 'lineno', '?')}: walrus")


if __name__ == "__main__":
    unittest.main()
