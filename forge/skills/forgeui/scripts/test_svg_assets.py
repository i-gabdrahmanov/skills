#!/usr/bin/env python3
"""test_svg_assets.py — daiver_svg_assets: арты кадра ↔ файлы .svg (задача 1.6; спека §13.5 п.6; QG U23, N6; INV B7).

Пинит: список артов из канона кадра и `missingArt`; регистрацию экспорта Pixso как `<кадр>/assets/<slug>.svg`
(идемпотентна: повтор — `unchanged` без перезаписи; другой файл — `replaced`); отказы — slug вне канона,
не-svg, DOCTYPE/активное содержимое, пути вне репо; копию в модуль и ТОЧНЫЕ import + JSX для трёх режимов
`svg.render` (component — SVGR, url — `<img>`, inline) с определением формы импорта по package.json;
помеченный placeholder правильного размера и названный недостающий файл, когда файла нет (N6);
границы moduleDir (только внутри outputDir, не служебный каталог, не символическая ссылка наружу).
Синтетика: образец «Modal» арт-групп не содержит; svg — синтетический, с шапкой как у экспорта Pixso
(viewBox + width/height в 6 знаках, служебные `effect` в defs; реальный `Modal.svg` проверен валидатором вручную).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import canon as C  # noqa: E402
from daiver import svg_assets as SA  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402

URL = "https://pixso.example/a?item-id=2:1"
TRACK = "STOR-100"
ASSETS = ".daiver/tracks/STOR-100/assets"
WHITE = {"type": "SOLID", "color": {"r": 255, "g": 255, "b": 255, "a": 1}}


# ── построители ───────────────────────────────────────────────────────────────
def n(guid, typ="FRAME", name=None, w=None, h=None, left=0, top=0, kids=None, **kw):
    node = {"guid": guid, "type": typ, "name": name or guid, "left": left, "top": top}
    if w is not None:
        node["width"] = w
    if h is not None:
        node["height"] = h
    if kids:
        node["childNode"] = list(kids)
    node.update(kw)
    return node


def group(guid, name, count, w, h, left=0, top=0):
    return n(guid, "GROUP", name=name, w=w, h=h, left=left, top=top,
             kids=[n(f"{guid}v{i}", "VECTOR", name="Vector", w=30, h=30, left=i * 10) for i in range(1, count + 1)])


def dsl_doc(*extra, arts=True):
    kids = []
    if arts:
        kids += [group("g1", "Hero Banner", 3, 200, 120, left=10, top=10), group("g2", "Logo", 2, 100, 40, left=10, top=200)]
    kids += list(extra) or [n("t", "PARAGRAPH", name="Title", w=100, h=20, top=300, nodeText="Привет")]
    return {"dsl": {"pixTreeDslNodes": [n("root", name="Landing", w=400, h=400, kids=kids)],
                    "pixComponentTreeDslNodes": []}}


def svg_text(w=200, h=120, body='<path d="M0 0L10 10" fill="rgb(5,115,50)" fill-rule="nonzero" />', head=""):
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}.000000" height="{h}.000000" fill="none" customFrame="#000000">{head}'
            f'<defs><g id="pixso_custom_effect_0"><effect x="0.000000" y="12.000000" visibility="visible" '
            f'effectType="dropShadow" /></g></defs>{body}</svg>')


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, doc=None):
        (self.root / "dsl.json").write_text(json.dumps(doc or dsl_doc(), ensure_ascii=False), encoding="utf-8")
        T.call("daiver_pixso_pull", self.root, {"track": TRACK, "dslPath": "dsl.json", "frameUrl": URL})
        return T.call("daiver_normalize", self.root, {"track": TRACK})

    def put(self, rel, text=None, data=None) -> str:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if data is not None:
            p.write_bytes(data)
        else:
            p.write_text(svg_text() if text is None else text, encoding="utf-8")
        return rel

    def call(self, **kw):
        return T.call("daiver_svg_assets", self.root, dict({"track": TRACK}, **kw))

    def refusal(self, **kw) -> str:
        with self.assertRaises(T.ToolError) as cm:
            self.call(**kw)
        return str(cm.exception)

    def config(self, text):
        self.put(".daiver/config.yaml", text)

    def package(self, deps=None, dev=None):
        self.put("package.json", json.dumps({"dependencies": deps or {}, "devDependencies": dev or {}}))

    def assets(self) -> Path:
        return self.root / ASSETS

    def register_all(self):
        self.call(slug="hero-banner", svgPath=self.put("exports/hero.svg", svg_text(200, 120)))
        self.call(slug="logo", svgPath=self.put("exports/logo.svg", svg_text(100, 40, body="<circle r='5'/>")))


# ── арты кадра ────────────────────────────────────────────────────────────────
class Listing(Env):
    def test_lists_the_arts_of_the_canon_with_sizes_and_missing_files(self):
        self.build()
        out = self.call()
        self.assertEqual([a["slug"] for a in out["arts"]], ["hero-banner", "logo"])
        hero = out["arts"][0]
        self.assertEqual((hero["id"], hero["nodes"], hero["w"], hero["h"], hero["file"]), ("g1", 4, 200, 120, None))
        self.assertEqual(out["missingArt"], ["hero-banner", "logo"])
        self.assertEqual((out["track"], out["frame"], out["assetsDir"]), (TRACK, "main", ASSETS))
        self.assertEqual(out["next"], SA.NEXT_MISSING)
        self.assertNotIn("registered", out)
        self.assertNotIn("module", out)
        self.assertFalse(self.assets().exists(), "чтение ничего не создаёт")

    def test_frame_without_arts_needs_no_svg(self):
        self.build(dsl_doc(arts=False))
        out = self.call()
        self.assertEqual((out["arts"], out["missingArt"], out["next"]), ([], [], "в кадре нет артов — .svg не нужны"))

    def test_refuses_without_a_track_or_a_canon(self):
        self.assertIn("daiver_pixso_pull", self.refusal())
        (self.root / "dsl.json").write_text(json.dumps(dsl_doc()), encoding="utf-8")
        T.call("daiver_pixso_pull", self.root, {"track": TRACK, "dslPath": "dsl.json", "frameUrl": URL})
        self.assertIn("daiver_normalize", self.refusal())

    def test_broken_canon_is_named(self):
        self.build()
        (self.root / ".daiver/tracks/STOR-100/visual-requirements.canonical.json").write_text("{", encoding="utf-8")
        self.assertIn("не читается", self.refusal())

    def test_stale_canon_is_warned(self):
        self.build()
        path = self.root / ".daiver/tracks/STOR-100/visual-requirements.canonical.json"
        env = json.loads(path.read_text(encoding="utf-8"))
        env["stats"]["sourceSha256"] = "0" * 64
        C.write_canonical(path, env)
        self.assertIn("другому экспорту", self.call()["warnings"][0])

    def test_orphan_files_are_listed(self):
        self.build()
        self.put(f"{ASSETS}/old.svg")
        self.put(f"{ASSETS}/logo.svg")
        out = self.call()
        self.assertEqual((out["orphans"], out["missingArt"]), (["old.svg"], ["hero-banner"]))

    def test_unknown_argument_is_rejected(self):
        self.build()
        with self.assertRaises(T.ToolError) as cm:
            self.call(outPath="x.svg")
        self.assertIn("неизвестный аргумент", str(cm.exception))

    def test_occluded_art_asks_for_no_file(self):
        cover = n("c", "RECTANGLE", name="Backdrop", w=400, h=400, fillPaints=[WHITE])
        page = group("g", "Logo", 2, 100, 60, left=10, top=10)
        text = n("t", "PARAGRAPH", name="T", w=100, h=20, top=380, nodeText="x")
        self.build(dsl_doc(cover, page, text, arts=False))
        out = self.call()
        self.assertEqual(([a["slug"] for a in out["arts"]], out["arts"][0].get("occluded"), out["missingArt"]),
                         (["logo"], True, []))
        self.assertEqual(out["next"], "все арты кадра зарегистрированы")
        self.assertEqual(self.call(moduleDir="src/modules/Landing")["module"]["items"], [], "без slug — только видимые арты")
        item = self.call(slug="logo", moduleDir="src/modules/Landing")["module"]["items"][0]
        self.assertEqual(item["status"], "missing", "названный явно — обрабатывается")


# ── регистрация ───────────────────────────────────────────────────────────────
class Register(Env):
    def setUp(self):
        super().setUp()
        self.build()

    def test_registers_the_export_under_the_slug(self):
        src = self.put("exports/hero.svg")
        out = self.call(slug="hero-banner", svgPath=src)
        r = out["registered"]
        self.assertEqual((r["slug"], r["status"], r["file"]), ("hero-banner", "created", f"{ASSETS}/hero-banner.svg"))
        self.assertEqual(r["svg"], {"width": 200, "height": 120, "viewBox": "0 0 200 120"})
        self.assertEqual(r["bytes"], len((self.root / src).read_bytes()))
        self.assertEqual((self.assets() / "hero-banner.svg").read_bytes(), (self.root / src).read_bytes())
        self.assertEqual(out["missingArt"], ["logo"])
        self.assertEqual(out["arts"][0]["file"], r["file"])

    def test_registration_is_idempotent(self):
        src = self.put("exports/hero.svg")
        self.call(slug="hero-banner", svgPath=src)
        dest = self.assets() / "hero-banner.svg"
        before = dest.stat().st_mtime_ns
        again = self.call(slug="hero-banner", svgPath=src)
        self.assertEqual(again["registered"]["status"], "unchanged")
        self.assertEqual(dest.stat().st_mtime_ns, before, "тот же файл не перезаписывается")
        self.assertEqual(list(self.assets().glob("*.tmp")), [])

    def test_a_different_export_replaces_the_file(self):
        self.call(slug="hero-banner", svgPath=self.put("exports/hero.svg"))
        new = self.put("exports/hero2.svg", svg_text(body='<path d="M1 1"/>'))
        out = self.call(slug="hero-banner", svgPath=new)
        self.assertEqual(out["registered"]["status"], "replaced")
        self.assertEqual((self.assets() / "hero-banner.svg").read_bytes(), (self.root / new).read_bytes())

    def test_a_file_already_in_place_is_unchanged(self):
        self.put(f"{ASSETS}/logo.svg")
        self.assertEqual(self.call(slug="logo", svgPath=f"{ASSETS}/logo.svg")["registered"]["status"], "unchanged")

    def test_all_registered_closes_the_list(self):
        self.register_all()
        out = self.call()
        self.assertEqual((out["missingArt"], out["next"]), ([], "все арты кадра зарегистрированы"))
        self.assertTrue(all(a["file"] for a in out["arts"]))

    def test_bom_and_missing_sizes(self):
        bom = b"\xef\xbb\xbf" + svg_text().encode("utf-8")
        self.put("exports/bom.svg", data=bom)
        self.assertEqual(self.call(slug="logo", svgPath="exports/bom.svg")["registered"]["svg"]["width"], 200)
        self.put("exports/bare.svg", '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>')
        out = self.call(slug="hero-banner", svgPath="exports/bare.svg")
        self.assertEqual(out["registered"]["svg"], {})
        self.assertTrue(any("ни viewBox, ни width/height" in w for w in out["warnings"]))

    def test_refusals_name_the_reason_and_write_nothing(self):
        bad = {
            "ожидается <svg>": "<html><body/></html>",
            "не разбирается как XML": "это не xml",
            "DOCTYPE": '<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>',
            "активное содержимое": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "on*": '<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>',
            "javascript": '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>',
            "пуст": "  \n",
        }
        for key, body in bad.items():
            src = self.put("exports/bad.svg", body)
            msg = self.refusal(slug="logo", svgPath=src)
            self.assertIn("bad.svg", msg, key)
            self.assertTrue(any(w in msg for w in (key, "содержимое", "XML", "<svg>", "пуст")), (key, msg))
            self.assertFalse((self.assets() / "logo.svg").exists(), key)

    def test_non_utf8_and_oversized_files_are_refused(self):
        self.put("exports/bin.svg", data=b"\xff\xfe\x00<svg/>")
        self.assertIn("не UTF-8", self.refusal(slug="logo", svgPath="exports/bin.svg"))
        src = self.put("exports/big.svg")
        with mock.patch.object(SA, "MAX_SVG_BYTES", 50):
            self.assertIn("больше", self.refusal(slug="logo", svgPath=src))

    def test_slug_comes_from_the_canon_and_is_not_invented(self):
        src = self.put("exports/hero.svg")
        msg = self.refusal(slug="hero", svgPath=src)
        self.assertIn("hero-banner", msg)
        self.assertIn("logo", msg)
        self.assertIn("латиница", self.refusal(slug="../etc", svgPath=src))
        self.assertIn("латиница", self.refusal(slug="Hero Banner", svgPath=src))
        self.assertFalse(self.assets().exists())

    def test_svg_path_needs_a_slug_and_a_file(self):
        self.assertIn("без slug", self.refusal(svgPath=self.put("exports/hero.svg")))
        self.assertIn("нет", self.refusal(slug="logo", svgPath="exports/nope.svg"))

    def test_paths_outside_the_repo_are_refused(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "x.svg"
            outside.write_text(svg_text(), encoding="utf-8")
            self.assertIn("вне репозитория", self.refusal(slug="logo", svgPath=str(outside)))
        self.assertIn("..", self.refusal(slug="logo", svgPath="../x.svg"))


# ── копия в модуль и JSX под svg.render ──────────────────────────────────────
class Module(Env):
    MOD = "src/modules/Landing"

    def setUp(self):
        super().setUp()
        self.build()
        self.register_all()

    def one(self, slug="hero-banner", **kw):
        out = self.call(slug=slug, moduleDir=self.MOD, **kw)
        return out, out["module"]["items"][0]

    def test_component_with_vite_svgr(self):
        self.package(dev={"vite-plugin-svgr": "4.2.0"})
        out, item = self.one()
        self.assertEqual(out["module"], {"dir": self.MOD, "render": "component", "assetsDir": "assets", "items": [item]})
        self.assertEqual(item, {
            "slug": "hero-banner", "status": "created", "file": f"{self.MOD}/assets/hero-banner.svg",
            "import": 'import HeroBannerArt from "./assets/hero-banner.svg?react";',
            "jsx": "<HeroBannerArt width={200} height={120} />"})
        self.assertNotIn("warnings", out)
        self.assertEqual((self.root / item["file"]).read_bytes(), (self.assets() / "hero-banner.svg").read_bytes())

    def test_component_with_react_scripts(self):
        self.package(deps={"react-scripts": "5.0.1"})
        _, item = self.one("logo")
        self.assertEqual(item["import"], 'import { ReactComponent as LogoArt } from "./assets/logo.svg";')
        self.assertEqual(item["jsx"], "<LogoArt width={100} height={40} />")

    def test_component_without_a_known_loader_defaults_and_says_so(self):
        out, item = self.one()
        self.assertEqual(item["import"], 'import HeroBannerArt from "./assets/hero-banner.svg?react";')
        self.assertTrue(any("vite-plugin-svgr" in w for w in out["warnings"]))

    def test_url_mode(self):
        self.config("svg:\n  render: url\n")
        out, item = self.one()
        self.assertEqual(item["import"], 'import heroBannerUrl from "./assets/hero-banner.svg";')
        self.assertEqual(item["jsx"], '<img src={heroBannerUrl} width={200} height={120} alt="" />')
        self.assertNotIn("warnings", out)
        self.assertEqual(out["module"]["render"], "url")

    def test_inline_mode(self):
        self.config("svg:\n  render: inline\n")
        self.package(dev={"vite": "5.0.0"})
        out, item = self.one()
        self.assertEqual(item["import"], 'import heroBannerMarkup from "./assets/hero-banner.svg?raw";')
        self.assertEqual(item["jsx"], "<span dangerouslySetInnerHTML={{ __html: heroBannerMarkup }} />")
        self.assertNotIn("warnings", out)
        self.package()
        self.assertTrue(any("?raw" in w for w in self.call(slug="logo", moduleDir=self.MOD)["warnings"]))

    def test_assets_dir_comes_from_the_config(self):
        self.config("svg:\n  render: url\n  assetsDir: media/art\n")
        out, item = self.one()
        self.assertEqual(item["import"], 'import heroBannerUrl from "./media/art/hero-banner.svg";')
        self.assertEqual(item["file"], f"{self.MOD}/media/art/hero-banner.svg")
        self.assertTrue((self.root / item["file"]).is_file())

    def test_copy_is_idempotent_and_follows_a_re_registered_export(self):
        _, first = self.one()
        dest = self.root / first["file"]
        before = dest.stat().st_mtime_ns
        self.assertEqual(self.one()[1]["status"], "unchanged")
        self.assertEqual(dest.stat().st_mtime_ns, before)
        new = self.put("exports/hero2.svg", svg_text(body="<g/>"))
        self.call(slug="hero-banner", svgPath=new)
        self.assertEqual(self.one()[1]["status"], "replaced")
        self.assertEqual(dest.read_bytes(), (self.root / new).read_bytes())

    def test_without_a_slug_every_visible_art_of_the_frame(self):
        out = self.call(moduleDir=self.MOD)
        self.assertEqual([i["slug"] for i in out["module"]["items"]], ["hero-banner", "logo"])
        self.assertEqual([i["status"] for i in out["module"]["items"]], ["created", "created"])

    def test_registration_and_copy_in_one_call(self):
        new = self.put("exports/logo2.svg", svg_text(100, 40, body="<g/>"))
        out = self.call(slug="logo", svgPath=new, moduleDir=self.MOD)
        self.assertEqual((out["registered"]["status"], out["module"]["items"][0]["status"]), ("replaced", "created"))
        self.assertEqual((self.root / self.MOD / "assets" / "logo.svg").read_bytes(), (self.root / new).read_bytes())

    def test_missing_file_is_not_a_blocker_but_a_named_marked_placeholder(self):
        (self.assets() / "logo.svg").unlink()
        out, item = self.one("logo")
        self.assertEqual((item["status"], item["file"], item["import"], item["placeholder"]), ("missing", None, None, True))
        self.assertEqual(item["missingFile"], f"{ASSETS}/logo.svg")
        for part in ('data-art-placeholder="logo"', "ART MISSING: logo.svg", "width: 100", "height: 40"):
            self.assertIn(part, item["jsx"])
        self.assertFalse((self.root / self.MOD / "assets" / "logo.svg").exists(), "копировать нечего")
        self.assertEqual(out["missingArt"], ["logo"])

    def test_boundaries_of_module_dir(self):
        self.assertIn("вне outputDir", self.refusal(slug="logo", moduleDir="src/other/Landing"))
        self.assertIn("вне outputDir", self.refusal(slug="logo", moduleDir="src"))
        self.assertIn("..", self.refusal(slug="logo", moduleDir="../Landing"))
        with tempfile.TemporaryDirectory() as other:
            self.assertIn("вне репозитория", self.refusal(slug="logo", moduleDir=str(Path(other) / "m")))
        self.config("project:\n  outputDir: .\n")
        for bad in (".git/x", ".daiver/x", "node_modules/x"):
            self.assertIn("служебном каталоге", self.refusal(slug="logo", moduleDir=bad), bad)
        self.assertEqual(self.call(slug="logo", moduleDir=".")["module"]["dir"], ".", "корень outputDir допустим")

    def test_custom_output_dir_is_the_boundary(self):
        self.config("project:\n  outputDir: app/ui\n")
        self.assertEqual(self.call(slug="logo", moduleDir="app/ui/Landing")["module"]["dir"], "app/ui/Landing")
        self.assertIn("app/ui", self.refusal(slug="logo", moduleDir=self.MOD))

    def test_unsafe_assets_dir_in_the_config_is_refused(self):
        for bad in ("../escape", "/abs"):
            self.config(f"svg:\n  assetsDir: {bad}\n")
            self.assertIn("svg.assetsDir", self.refusal(slug="logo", moduleDir=self.MOD), bad)

    def test_symlinked_asset_dir_cannot_lead_out_of_the_repo(self):
        with tempfile.TemporaryDirectory() as other:
            (self.root / self.MOD).mkdir(parents=True)
            try:
                os.symlink(other, str(self.root / self.MOD / "assets"))
            except (OSError, NotImplementedError):
                self.skipTest("символические ссылки недоступны")
            self.assertIn("за пределы репо", self.refusal(slug="logo", moduleDir=self.MOD))
            self.assertEqual(os.listdir(other), [])

    def test_unknown_slug_is_refused_in_module_mode_too(self):
        self.assertIn("в каноне кадра нет", self.refusal(slug="nope", moduleDir=self.MOD))


# ── чистые функции ───────────────────────────────────────────────────────────
class Helpers(unittest.TestCase):
    def test_check_svg_reads_what_the_file_declares(self):
        def svg(attrs):
            return f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}/>'.encode("utf-8")
        self.assertEqual(SA.check_svg(svg('width="10px" height="20.5" viewBox="0,0,10,20.5"')),
                         {"width": 10, "height": 20.5, "viewBox": "0 0 10 20.5"})
        self.assertEqual(SA.check_svg(svg('width="100%" height="50%" viewBox="a b c d"')), {}, "проценты и мусор — не размер")
        self.assertEqual(SA.check_svg(svg('viewBox="0 0 24 24"')), {"viewBox": "0 0 24 24"})

    def test_names_of_the_imported_things(self):
        self.assertEqual((SA._component_name("hero-banner"), SA._var_name("hero-banner")), ("HeroBannerArt", "heroBanner"))
        self.assertEqual((SA._component_name("art"), SA._var_name("art")), ("Art", "art"))
        self.assertEqual((SA._component_name("404-page"), SA._var_name("404-page")), ("Art404Page", "art404Page"))
        self.assertEqual(SA._component_name("hero-banner-art"), "HeroBannerArt")

    def test_unknown_sizes_are_not_written(self):
        sn = SA.snippet("url", "url", "x", "./assets/x.svg", None, 40)
        self.assertEqual(sn["jsx"], '<img src={xUrl} height={40} alt="" />')
        self.assertEqual(SA.snippet("component", "react-suffix", "x", "./assets/x.svg", None, None)["jsx"], "<XArt />")
        ph = SA.placeholder("x", None, None)
        self.assertIn("minWidth: 32, minHeight: 32", ph)
        self.assertIn("data-art-placeholder", ph)
        self.assertIn("width: 10.5, height: 4", SA.placeholder("x", 10.5, 4))

    def test_loader_style_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "package.json"

            def style(render, text=None):
                if text is None:
                    if pkg.exists():
                        pkg.unlink()
                else:
                    pkg.write_text(text, encoding="utf-8")
                return SA.loader_style(root, render)

            self.assertEqual(style("component", json.dumps({"devDependencies": {"vite-plugin-svgr": "4"}})), ("react-suffix", []))
            self.assertEqual(style("component", json.dumps({"dependencies": {"react-scripts": "5"}})), ("named", []))
            both = json.dumps({"dependencies": {"react-scripts": "5", "vite-plugin-svgr": "4"}})
            self.assertEqual(style("component", both)[0], "react-suffix")
            for broken in (None, "{", "[]", json.dumps({"dependencies": []})):
                got = style("component", broken)
                self.assertEqual(got[0], "react-suffix")
                self.assertEqual(len(got[1]), 1, broken)
            self.assertEqual(style("url", None), ("url", []))
            self.assertEqual(style("inline", json.dumps({"devDependencies": {"vite": "5"}})), ("raw", []))
            self.assertEqual(len(style("inline", None)[1]), 1)

    def test_missing_slugs_skip_occluded_arts(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.svg").write_text("<svg/>", encoding="utf-8")
            arts = [{"slug": "a"}, {"slug": "b"}, {"slug": "c", "occluded": True}]
            self.assertEqual(SA.missing_slugs(arts, Path(tmp)), ["b"])

    def test_summary_is_empty_without_arts(self):
        self.assertEqual(SA.summary([], Path("nowhere")), {})


# ── реестр инструментов и сводка normalize ───────────────────────────────────
class Registry(Env):
    def test_tool_schema(self):
        tool = T.TOOLS["daiver_svg_assets"]
        self.assertEqual(sorted(tool["schema"]["properties"]), ["frame", "moduleDir", "slug", "svgPath", "track"])
        self.assertEqual((tool["schema"]["required"], tool["schema"]["additionalProperties"]), (["track"], False))
        self.assertIn("рисовать руками", tool["description"])

    def test_normalize_names_the_missing_art_right_away(self):
        out = self.build()
        self.assertEqual(out["art"], {"count": 2, "missingArt": ["hero-banner", "logo"], "next": SA.NEXT_MISSING})
        self.assertEqual(out["stats"]["art"]["arts"], 2)
        self.call(slug="hero-banner", svgPath=self.put("exports/hero.svg"))
        again = T.call("daiver_normalize", self.root, {"track": TRACK})
        self.assertEqual(again["art"], {"count": 2, "missingArt": ["logo"], "next": SA.NEXT_MISSING})
        self.call(slug="logo", svgPath=self.put("exports/logo.svg"))
        done = T.call("daiver_normalize", self.root, {"track": TRACK})
        self.assertEqual(done["art"], {"count": 2, "missingArt": []})

    def test_normalize_without_arts_has_no_art_key(self):
        self.assertNotIn("art", self.build(dsl_doc(arts=False)))

    def test_slugs_are_stable_across_repeated_normalize(self):
        self.build()
        path = self.root / ".daiver/tracks/STOR-100/visual-requirements.canonical.json"
        first = path.read_bytes()
        T.call("daiver_normalize", self.root, {"track": TRACK})
        self.assertEqual(path.read_bytes(), first, "канон побайтно тот же (F5)")
        self.assertEqual([a["slug"] for a in self.call()["arts"]], ["hero-banner", "logo"])


if __name__ == "__main__":
    unittest.main()
