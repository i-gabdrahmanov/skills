#!/usr/bin/env python3
"""test_track_manifest.py — манифест трека, goal image, рецепты, source_nodes (задача 1.9; QG U6).

Пинит: создание/чтение/правка track.yaml с проверкой каждого значения и сохранением шапки;
раскладка путей кадра через манифест; статус требования по хешу; правка полей с отказом на
неизвестные и чужие (pull) поля одним сообщением; стейт-кадры; прикрепление растра PNG/JPEG
(по содержимому, а не по расширению); рецепт без «выполнить JS»; source_nodes отдаёт только
декларации и не больше 40 узлов; `daiver_spec` подхватывает спеки, зарегистрированные в манифесте.
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

import _testkit as K  # noqa: E402
from daiver import image as IMG  # noqa: E402
from daiver import recipe as R  # noqa: E402
from daiver import source_nodes as SN  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
SHA = "a" * 64


def manifest(frames=("main",)) -> dict:
    m = TR.new_manifest("STOR-100", created="2026-10-15T12:00:00Z")
    for name in frames:
        TR.upsert_frame(m, name, frameUrl=f"https://pixso.example/{name}",
                        requirement={"kind": "pixso", "path": "visual-requirements.pixso.json",
                                     "sha256": SHA, "pulledAt": "2026-10-15T12:00:00Z"})
    return m


class Names(unittest.TestCase):
    def test_track_and_frame_names_are_safe(self):
        for bad in ("", "../x", "a/b", ".hidden", "a b", None, 5, "x" * 65):
            with self.assertRaises(TR.TrackError, msg=repr(bad)):
                TR.check_track_name(bad)
        for good in ("STOR-100", "tech-bump.v2", "A1"):
            self.assertEqual(TR.check_track_name(good), good)
        for bad in ("a/b", "", "..", "a.b"):
            with self.assertRaises(TR.TrackError, msg=repr(bad)):
                TR.check_frame_name(bad)
        self.assertEqual(TR.check_frame_name("confirm-modal"), "confirm-modal")


class Validation(unittest.TestCase):
    def problems(self, mutate) -> str:
        m = manifest()
        mutate(m)
        return "\n".join(TR.validate(m))

    def test_valid(self):
        self.assertEqual(TR.validate(manifest()), [])
        self.assertEqual(TR.validate(manifest(("main", "dlg"))), [])

    def test_unknown_and_wrong_values(self):
        self.assertIn("неизвестное поле кадра", self.problems(lambda m: m["frames"][0].update(colour="x")))
        self.assertIn("role", self.problems(lambda m: m["frames"][0].update(role="popup")))
        self.assertIn("sha256", self.problems(lambda m: m["frames"][0]["requirement"].update(sha256="xyz")))
        self.assertIn("requirement.kind", self.problems(lambda m: m["frames"][0]["requirement"].update(kind="sketch")))
        self.assertIn("frameBox", self.problems(lambda m: m["frames"][0].update(frameBox={"x": -1, "y": 0, "w": 5, "h": 5})))
        self.assertIn("url", self.problems(lambda m: m["frames"][0].update(url="localhost:5173")))
        self.assertIn("implementation.devCommand",
                      self.problems(lambda m: m.update(implementation={"devCommand": "npm run dev\nrm -rf /"})))
        self.assertIn("schema", self.problems(lambda m: m.update(schema=2)))
        self.assertIn("неизвестное поле манифеста", self.problems(lambda m: m.update(sign="x")))

    def test_paths_stay_inside(self):
        for bad in ("/etc/passwd", "../x.json", "a\\b", "~/x", "C:\\x"):
            self.assertIn("относительный путь",
                          self.problems(lambda m: m["frames"][0]["requirement"].update(path=bad)), bad)
            self.assertIn("относительный путь", self.problems(lambda m: m.update(specs=[bad])), bad)

    def test_frame_names_unique_and_layout_rule(self):
        m = manifest(("main", "dlg"))
        m["frames"][1]["name"] = "main"
        self.assertIn("повторяется", "\n".join(TR.validate(m)))
        m = manifest(("main", "dlg"))
        m["frames"][0]["dir"] = "."
        self.assertIn("frames/<имя>", "\n".join(TR.validate(m)), "со вторым кадром все уезжают в frames/")

    def test_state_frames(self):
        m = manifest(("main", "err"))
        m["frames"][1].update(role="state", stateOf="main")
        self.assertEqual(TR.validate(m), [])
        m["frames"][1]["stateOf"] = "err"
        self.assertIn("самого себя", "\n".join(TR.validate(m)))
        m["frames"][1]["stateOf"] = "nope"
        self.assertIn("в треке нет", "\n".join(TR.validate(m)))
        m = manifest(("main", "err"))
        m["frames"][1]["stateOf"] = "main"
        self.assertIn("роль state", "\n".join(TR.validate(m)))
        m = manifest(("main", "err"))
        m["frames"][0].update(role="state", stateOf="err")
        self.assertIn("первичный кадр", "\n".join(TR.validate(m)))

    def test_page_base_sources_and_scope(self):
        """Кадр, правящий существующую страницу (задача 2.6): sources — файлы от корня репо, scope — change | page и только с sources."""
        m = manifest()
        m["frames"][0].update(sources=["src/pages/Orders.tsx", "src/pages/Orders.less"], scope="change")
        self.assertEqual(TR.validate(m), [])
        for bad in ("/etc/passwd", "../x.tsx", "a\\b", "~/x.tsx"):
            self.assertIn("относительный путь", self.problems(lambda m: m["frames"][0].update(sources=[bad])), bad)
        self.assertIn("список файлов", self.problems(lambda m: m["frames"][0].update(sources="src/a.tsx")))
        self.assertIn("повторяется", self.problems(lambda m: m["frames"][0].update(sources=["a.tsx", "a.tsx"])))
        self.assertIn("scope", self.problems(lambda m: m["frames"][0].update(sources=["a.tsx"], scope="all")))
        self.assertIn("только вместе с sources", self.problems(lambda m: m["frames"][0].update(scope="page")))
        self.assertIn("только вместе с sources", self.problems(lambda m: m["frames"][0].update(sources=[], scope="page")))
        self.assertEqual(TR.validate(manifest()), [], "нет sources — кадр строится с нуля, это не ошибка")

    def test_sources_and_scope_keep_their_place_in_the_file(self):
        m = manifest()
        m["frames"][0].update(url="http://x.y/a", sources=["src/a.tsx"], scope="page", notes="n")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        TR.save(Path(tmp.name), m)
        body = (Path(tmp.name) / ".daiver/tracks/STOR-100/track.yaml").read_text(encoding="utf-8")
        self.assertLess(body.index("url: http://x.y/a"), body.index("sources:"))
        self.assertLess(body.index("sources:"), body.index("scope: page"))
        self.assertLess(body.index("scope: page"), body.index("notes"))
        self.assertEqual(TR.load(Path(tmp.name), "STOR-100"), m)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_roundtrip_and_header_survives_edits(self):
        m = manifest()
        path = TR.save(self.root, m)
        self.assertEqual(path, self.root / ".daiver" / "tracks" / "STOR-100" / "track.yaml")
        self.assertEqual(TR.load(self.root, "STOR-100"), m)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# track.yaml — манифест трека"))
        path.write_text(text.replace("# Пути — от папки трека; specs — от корня репо.",
                                     "# Пути — от папки трека; specs — от корня репо.\n# своя пометка команды"),
                        encoding="utf-8")
        m2 = TR.load(self.root, "STOR-100")
        m2["implementation"] = {"url": "http://localhost:5173"}
        TR.save(self.root, m2)
        self.assertIn("# своя пометка команды", path.read_text(encoding="utf-8"))
        self.assertEqual(TR.load(self.root, "STOR-100")["implementation"], {"url": "http://localhost:5173"})

    def test_key_order_is_stable(self):
        m = manifest()
        m["frames"][0]["url"] = "http://x.y/a"
        m["frames"][0]["notes"] = "n"
        TR.save(self.root, m)
        text = (self.root / ".daiver/tracks/STOR-100/track.yaml").read_text(encoding="utf-8")
        order = [l.strip().split(":")[0] for l in text.splitlines() if l.startswith("    ")][:1]
        self.assertLess(text.index("name: main"), text.index("frameUrl"))
        self.assertLess(text.index("frameUrl"), text.index("requirement"))
        self.assertLess(text.index("requirement"), text.index("url: http://x.y/a"))
        self.assertLess(text.index("url: http://x.y/a"), text.index("notes"))
        self.assertTrue(order)

    def test_missing_corrupt_and_wrong_name(self):
        self.assertIsNone(TR.load(self.root, "STOR-100"))
        d = self.root / ".daiver/tracks/STOR-100"
        d.mkdir(parents=True)
        (d / "track.yaml").write_text("track: [oops\n", encoding="utf-8")
        with self.assertRaises(TR.TrackError):
            TR.load(self.root, "STOR-100")
        m, warns = TR.load_safe(self.root, "STOR-100")
        self.assertIsNone(m)
        self.assertIn("повреждён", warns[0])
        (d / "track.yaml").write_text("track: STOR-999\nschema: 1\nframes: []\n", encoding="utf-8")
        with self.assertRaises(TR.TrackError):
            TR.load(self.root, "STOR-100")

    def test_save_refuses_invalid_and_writes_nothing(self):
        m = manifest()
        m["frames"][0]["role"] = "popup"
        with self.assertRaises(TR.TrackError):
            TR.save(self.root, m)
        self.assertFalse((self.root / ".daiver").exists())

    def test_frame_paths_single_and_multi(self):
        m = manifest()
        p = TR.frame_paths(self.root, m)
        base = self.root / ".daiver/tracks/STOR-100"
        self.assertEqual(p["dir"], base)
        self.assertEqual(p["canonical"], base / "visual-requirements.canonical.json")
        self.assertEqual(p["raw"], base / "visual-requirements.pixso.raw.json")
        self.assertEqual(p["buildState"], base / "build-state.json")
        TR.upsert_frame(m, "dlg", frameUrl="https://x/y")
        self.assertEqual([f["dir"] for f in m["frames"]], ["frames/main", "frames/dlg"],
                         "со вторым кадром первый переезжает в frames/")
        self.assertEqual(TR.frame_paths(self.root, m, "dlg")["canonical"],
                         base / "frames/dlg/visual-requirements.canonical.json")
        with self.assertRaises(TR.TrackError):
            TR.get_frame(m, "nope")
        with self.assertRaises(TR.TrackError):
            TR.get_frame({"frames": []})

    def test_requirement_status_by_hash(self):
        m = manifest()
        self.assertEqual(TR.requirement_status(self.root, m), "missing")
        req = TR.frame_paths(self.root, m)["requirement"]
        req.parent.mkdir(parents=True)
        req.write_text('{"dsl": 1}', encoding="utf-8")
        self.assertEqual(TR.requirement_status(self.root, m), "stale")
        m["frames"][0]["requirement"]["sha256"] = TR.sha256_file(req)
        self.assertEqual(TR.requirement_status(self.root, m), "ok")
        req.write_text('{"dsl": 2}', encoding="utf-8")
        self.assertEqual(TR.requirement_status(self.root, m), "stale", "требование пере-pull'или — хеш разошёлся")
        del m["frames"][0]["requirement"]
        self.assertEqual(TR.requirement_status(self.root, m), "none")


class SetFields(unittest.TestCase):
    def test_frame_fields_and_clearing(self):
        m = manifest()
        new, changed, problems = TR.apply_set(m, "main", {"url": "http://localhost:5173/storage", "notes": "x"})
        self.assertEqual((problems, sorted(changed)), ([], ["notes", "url"]))
        self.assertNotIn("url", m["frames"][0], "исходный манифест не тронут")
        new2, changed2, _ = TR.apply_set(new, "main", {"url": None})
        self.assertNotIn("url", new2["frames"][0])
        self.assertEqual(changed2, ["url"])
        _, changed3, _ = TR.apply_set(new, "main", {"notes": "x"})
        self.assertEqual(changed3, [], "то же значение — не изменение")

    def test_track_level_and_wrong_level(self):
        m = manifest()
        new, changed, problems = TR.apply_set(m, None, {"implementation": {"url": "http://localhost:3000",
                                                                         "devCommand": "npm run dev"},
                                                       "specs": ["docs/feature-pipeline/STOR-100/ui-spec.md"]})
        self.assertEqual((problems, sorted(changed)), ([], ["implementation", "specs"]))
        _, _, p = TR.apply_set(m, None, {"url": "http://x.y"})
        self.assertIn("поле кадра — укажите frame", p[0])
        _, _, p = TR.apply_set(m, "main", {"specs": []})
        self.assertIn("поле трека — уберите frame", p[0])

    def test_pull_owned_and_unknown_refused_in_one_message(self):
        _, _, p = TR.apply_set(manifest(), "main", {"frameUrl": "x", "raster": "a.png", "colour": 1, "dir": "z"})
        text = "\n".join(p)
        self.assertEqual(len(p), 4, p)
        self.assertIn("frameUrl", text)
        self.assertIn("rasterPath", text, "про растр подсказано, как прикрепить")
        self.assertIn("colour", text)

    def test_sources_and_scope_are_settable_together_and_cleared_together(self):
        m = manifest()
        new, changed, problems = TR.apply_set(m, "main", {"sources": ["src/pages/Orders.tsx"], "scope": "change"})
        self.assertEqual((problems, sorted(changed)), ([], ["scope", "sources"]))
        _, _, p = TR.apply_set(new, "main", {"sources": None})
        self.assertIn("только вместе с sources", "\n".join(p), "sources без scope не снять: scope осиротеет")
        cleared, changed2, p2 = TR.apply_set(new, "main", {"sources": None, "scope": None})
        self.assertEqual((p2, sorted(changed2)), ([], ["scope", "sources"]))
        self.assertNotIn("sources", cleared["frames"][0])
        _, _, p3 = TR.apply_set(m, None, {"sources": ["a.tsx"]})
        self.assertIn("поле кадра — укажите frame", p3[0])

    def test_state_of_sets_role_and_is_validated(self):
        m = manifest(("main", "err"))
        new, changed, problems = TR.apply_set(m, "err", {"stateOf": "main"})
        self.assertEqual(problems, [])
        self.assertEqual(new["frames"][1]["role"], "state")
        self.assertIn("role", changed)
        _, _, p = TR.apply_set(m, "err", {"stateOf": "nope"})
        self.assertIn("в треке нет", "\n".join(p))

    def test_open_recipe_validated_without_js(self):
        m = manifest()
        ok = {"actions": [{"verb": "click", "selector": "[data-test=open]"},
                          {"verb": "waitFor", "selector": ".modal", "ms": 3000}], "clipSelector": ".modal"}
        _, _, p = TR.apply_set(m, "main", {"open": ok})
        self.assertEqual(p, [])
        _, _, p = TR.apply_set(m, "main", {"open": {"actions": [{"verb": "evaluate", "script": "x()"}]}})
        self.assertIn("«выполнить JS» среди глаголов нет", "\n".join(p))
        _, _, p = TR.apply_set(m, "main", {"open": {"actions": [{"verb": "fill", "selector": "#a"}]}})
        self.assertIn("нужен аргумент «text»", "\n".join(p))
        _, _, p = TR.apply_set(m, "main", {"open": {"actions": [{"verb": "wait", "ms": 999999}]}})
        self.assertIn("ms", "\n".join(p))
        self.assertTrue(R.validate_recipe({"actions": [{"verb": "click", "selector": "a", "extra": 1}]}))
        self.assertEqual(R.validate_recipe({}), [])
        self.assertTrue(R.validate_actions([{"verb": "click", "selector": "a"}] * 31))


class Rasters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sniff_png_and_jpeg(self):
        self.assertEqual(IMG.sniff_bytes(K.png_bytes(648, 293)), {"format": "png", "width": 648, "height": 293})
        self.assertEqual(IMG.sniff_bytes(K.jpeg_header_bytes(648, 293)), {"format": "jpeg", "width": 648, "height": 293})
        for junk in (b"", b"GIF89a....", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff\xd9", b"not an image at all"):
            self.assertIsNone(IMG.sniff_bytes(junk), junk)

    def test_real_sample_raster_when_present(self):
        sample = HERE.parents[2] / "docs" / "daiver" / "IMG_20261014_185514_465.jpg"
        if not sample.is_file():
            self.skipTest("образец растра docs/daiver/*.jpg не лежит локально (он не в git)")
        self.assertEqual(IMG.sniff(sample), {"format": "jpeg", "width": 648, "height": 293})

    def test_attach_by_content_replaces_other_format(self):
        m = manifest()
        png = self.root / "shot.dat"                    # расширение источника ничего не значит
        png.write_bytes(K.png_bytes(64, 32))
        info = TR.attach_raster(self.root, m, None, png)
        self.assertEqual(info, {"path": "visual-requirements.png", "format": "png", "width": 64, "height": 32})
        d = TR.frame_dir(self.root, m)
        self.assertTrue((d / "visual-requirements.png").is_file())
        jpg = self.root / "other.png"                   # лжёт расширением
        jpg.write_bytes(K.jpeg_header_bytes(10, 20))
        info = TR.attach_raster(self.root, m, "main", jpg)
        self.assertEqual(info["path"], "visual-requirements.jpg")
        self.assertFalse((d / "visual-requirements.png").exists(), "двух растров одного кадра не бывает")
        self.assertEqual(m["frames"][0]["raster"], "visual-requirements.jpg")
        junk = self.root / "junk.png"
        junk.write_bytes(b"hello")
        with self.assertRaises(TR.TrackError):
            TR.attach_raster(self.root, m, None, junk)
        with self.assertRaises(TR.TrackError):
            TR.attach_raster(self.root, m, None, self.root / "нет.png")


class Tool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, **args):
        return T.call("daiver_track_manifest", self.root, args)

    def seed(self, frames=("main",)):
        TR.save(self.root, manifest(frames))

    def test_read_missing_track_and_creation_by_track_fields(self):
        out = self.call(track="STOR-100")
        self.assertFalse(out["exists"])
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", frame="main", set={"role": "dialog"})
        self.assertIn("daiver_pixso_pull", str(cm.exception))
        out = self.call(track="STOR-100", set={"implementation": {"url": "http://localhost:5173",
                                                                   "devCommand": "npm run dev"}})
        self.assertTrue(out["created"])
        self.assertEqual(out["frames"], [])
        self.assertEqual(TR.load(self.root, "STOR-100")["implementation"]["devCommand"], "npm run dev")

    def test_set_read_and_summary(self):
        self.seed(("main", "err"))
        out = self.call(track="STOR-100", frame="err", set={"stateOf": "main", "open": {"actions": [
            {"verb": "click", "selector": "#toggle"}]}})
        self.assertEqual(sorted(out["changed"]), ["open", "role", "stateOf"])
        err = next(f for f in out["frames"] if f["name"] == "err")
        self.assertEqual((err["role"], err["stateOf"]), ("state", "main"))
        self.assertEqual(err["requirement"]["status"], "missing")
        self.assertFalse(err["canonical"])
        again = self.call(track="STOR-100")
        self.assertEqual(again["frames"], out["frames"], "чтение не меняет манифест")
        self.assertEqual(again["changed"], [])

    def test_errors_are_one_message(self):
        self.seed()
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", frame="main", set={"url": "bad", "frameUrl": "x", "zzz": 1})
        msg = str(cm.exception)
        self.assertIn("frameUrl", msg)
        self.assertIn("zzz", msg)
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", frame="main", set={"url": "bad"})
        self.assertIn("http(s)", str(cm.exception))
        with self.assertRaises(T.ToolError):
            self.call(track="../x")
        with self.assertRaises(T.ToolError):
            self.call(track="STOR-100", frame="nope", set={"notes": "x"})
        with self.assertRaises(T.ToolError):
            self.call(track="STOR-100", bogus=True)

    def test_raster_attach_and_escape_refused(self):
        self.seed()
        (self.root / "shots").mkdir()
        (self.root / "shots" / "goal.png").write_bytes(K.png_bytes(48, 24))
        out = self.call(track="STOR-100", rasterPath="shots/goal.png")
        self.assertEqual(out["rasterAttached"]["width"], 48)
        self.assertEqual(out["frames"][0]["raster"]["path"], "visual-requirements.png")
        self.assertTrue(out["frames"][0]["raster"]["exists"])
        with self.assertRaises(T.ToolError):
            self.call(track="STOR-100", rasterPath="../outside.png")
        with self.assertRaises(T.ToolError):
            self.call(track="STOR-100", rasterPath="/etc/passwd")
        (self.root / "shots" / "x.png").write_bytes(b"nope")
        with self.assertRaises(T.ToolError):
            self.call(track="STOR-100", rasterPath="shots/x.png")

    def test_frame_box_and_missing_frame_warning(self):
        self.seed()
        out = self.call(track="STOR-100", frame="main", set={"frameBox": {"x": 24, "y": 24, "w": 600, "h": 245}})
        self.assertEqual(out["frames"][0]["frameBox"], {"x": 24, "y": 24, "w": 600, "h": 245})
        out = self.call(track="STOR-100", set={"implementation": {"url": "http://x.y"}})
        self.assertNotIn("warnings", out)

    def test_spec_tool_picks_up_registered_specs(self):
        story = self.root / "docs" / "feature-pipeline" / "STOR-100"
        story.mkdir(parents=True)
        (story / "ui-spec.md").write_text("## Сценарии\nGiven a x, When b y, Then c z\n", encoding="utf-8")
        elsewhere = self.root / "specs" / "extra.spec.md"
        elsewhere.parent.mkdir()
        elsewhere.write_text("## Сценарии\nGiven q w, When e r, Then t y\n", encoding="utf-8")
        self.call(track="STOR-100", set={"specs": ["specs/extra.spec.md", "specs/gone.md"]})
        res = T.call("daiver_spec", self.root, {"feature": "STOR-100"})
        self.assertEqual(sorted(s["id"] for s in res["e2e"]), ["extra#1", "ui-spec#1"])
        self.assertEqual(len(res["warnings"]), 1)
        self.assertIn("gone.md", res["warnings"][0], "зарегистрирована, но файла нет — предупреждение")


class SourceNodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        TR.save(self.root, manifest())
        raw = TR.frame_paths(self.root, manifest())["raw"]
        raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(FIXTURE), str(raw))

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, ids, **kw):
        return T.call("daiver_source_nodes", self.root, dict(track="STOR-100", nodeIds=ids, **kw))

    def test_declarations_only_never_the_file(self):
        out = self.probe(["2:153702", "22:63141", "nope", "inferred:abc"])
        root = out["found"]["2:153702"]
        self.assertEqual((root["name"], root["type"]), ("Modal", "FRAME"))
        self.assertEqual(root["geometry"], {"left": 0, "top": 0, "width": 600, "height": 245})
        self.assertEqual(root["autoLayout"]["autoLayoutDirection"], 2)
        self.assertEqual(root["autoLayout"]["autoLayoutPaddingLeft"], 20)
        self.assertNotIn("maxWidth", root["autoLayout"], "null-поля не возвращаются")
        inst = out["found"]["22:63141"]
        self.assertEqual(inst["autoLayout"]["autoLayoutWidthResize"], 2)
        self.assertEqual(out["missing"], ["nope"])
        self.assertIn("inferred:abc", out["skipped"])
        dumped = json.dumps(out, ensure_ascii=False)
        self.assertLess(len(dumped), 4000, "ответ — несколько деклараций, не файл")
        for forbidden in ("pixComponentTreeDslNodes", "localStyleMap", "childNode", "fillPaints"):
            self.assertNotIn(forbidden, dumped)

    def test_constraints_come_from_nodes_that_declare_them(self):
        d = json.loads(FIXTURE.read_text(encoding="utf-8"))["dsl"]
        guid = next(n["guid"] for n in SN.D.all_nodes(d) if n.get("horizontalConstraint") == "SCALE")
        out = self.probe([guid])
        self.assertEqual(out["found"][guid]["horizontalConstraint"], "SCALE")
        self.assertIn("verticalConstraint", out["found"][guid])

    def test_limits_and_errors(self):
        with self.assertRaises(T.ToolError) as cm:
            self.probe([f"2:{i}" for i in range(41)])
        self.assertIn("40", str(cm.exception))
        self.assertEqual(len(self.probe([f"2:{i}" for i in range(40)])["missing"]) + len(
            self.probe([f"2:{i}" for i in range(40)])["found"]), 40)
        with self.assertRaises(T.ToolError):
            self.probe([])
        with self.assertRaises(T.ToolError):
            T.call("daiver_source_nodes", self.root, {"track": "NOPE-1", "nodeIds": ["2:1"]})
        shutil.rmtree(self.root / ".daiver" / "tracks" / "STOR-100")
        TR.save(self.root, manifest())
        with self.assertRaises(T.ToolError) as cm:
            self.probe(["2:153702"])
        self.assertIn("сырого экспорта нет", str(cm.exception))

    def test_not_a_pixso_file(self):
        TR.frame_paths(self.root, manifest())["raw"].write_text('{"a": 1}', encoding="utf-8")
        with self.assertRaises(T.ToolError):
            self.probe(["2:153702"])


if __name__ == "__main__":
    unittest.main()
