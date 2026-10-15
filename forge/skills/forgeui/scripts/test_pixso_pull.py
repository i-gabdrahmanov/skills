#!/usr/bin/env python3
"""test_pixso_pull.py — daiver_pixso_pull: slim, сырой рядом, регистрация кадра (задача 1.2; QG U10, N12).

Пинит: экспорт принимается только файлом (dslJson — отказ); формат определяется по структуре;
slim выбрасывает недостижимые определения, служебные поля и null, оставляя нужное; стили, шрифты и
specialNode — только используемые; диагностики (нет определения, пропавшая группа, цикл); кадр
регистрируется в track.yaml с хешем; повторный pull того же экспорта ничего не меняет, изменённый —
сбрасывает канон и называет устаревшее; собранный кадр и второй кадр отклоняются с причиной; сбой
записи сырого — предупреждение, а не отказ.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.pixso import dsl as D  # noqa: E402
from daiver.pixso import slim as S  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
URL = "https://pixso.example/app/design/abc?item-id=2:153702"


def synth(n_lib: int = 30, roots: int = 1) -> dict:
    inst = {"guid": "2:11", "type": "INSTANCE", "name": "Card", "mainComponent": "2:21", "mainStateGroup": "2:20",
            "overlayPositionType": "CENTER", "interactions": [], "maxWidth": None, "inheritFillStyleID": "2:900"}
    screen = [{"guid": "2:10", "type": "FRAME", "name": "Screen", "width": 100, "height": 50, "childNode": [inst]}]
    for r in range(1, roots):
        screen.append({"guid": f"2:1{r}0", "type": "FRAME", "name": f"Other{r}", "width": 10, "height": 10})
    defs = [{"guid": "2:20", "type": "SYMBOL", "name": "Card", "layerType": "COMPONENT_SET", "childNode": [
        {"guid": "2:21", "type": "SYMBOL", "name": "Variant=Default", "childNode": [
            {"guid": "2:22", "type": "PARAGRAPH", "name": "Title", "nodeText": "Hi", "fontFamily": "Acme",
             "fontStyle": "Regular"}]}]}]
    for i in range(n_lib):
        defs.append({"guid": f"3:{i}", "type": "SYMBOL", "name": f"Lib{i}", "childNode": [
            {"guid": f"3:{i}:{j}", "type": "RECTANGLE", "name": "r", "width": 5, "overlayPositionType": "CENTER",
             "fillPaints": []} for j in range(30)]})
    return {"dsl": {
        "converterVersion": "x", "dslVersion": "y",
        "localStyleMap": {"2:900": {"name": "used"}, "2:901": {"name": "unused"}},
        "sourceMapByUrl": {"AcmeRegular": {"fontKey": {"family": "Acme", "style": "Regular"}},
                           "OtherBold": {"fontKey": {"family": "Other", "style": "Bold"}}},
        "specialNode": [{"id": "2:22", "type": 6}, {"id": "9:9", "type": 6}],
        "editorJunk": {"a": 1},
        "pixTreeDslNodes": screen, "pixComponentTreeDslNodes": defs}}


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, doc, name="dsl.json") -> str:
        (self.root / name).write_text(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False),
                                      encoding="utf-8")
        return name

    def pull(self, doc=None, track="STOR-100", url=URL, **extra):
        name = self.put(doc if doc is not None else FIXTURE.read_text(encoding="utf-8"))
        args = {"track": track, "dslPath": name, "frameUrl": url}
        args.update(extra)
        return T.call("daiver_pixso_pull", self.root, args)

    def req(self, track="STOR-100") -> Path:
        return self.root / ".daiver" / "tracks" / track / "visual-requirements.pixso.json"


class OnFixture(Env):
    def test_pull_registers_frame_with_hash_and_writes_both_files(self):
        out = self.pull()
        self.assertTrue(out["created"])
        self.assertEqual((out["frame"], out["unchanged"]), ("main", False))
        req = self.req()
        self.assertEqual(hashlib.sha256(req.read_bytes()).hexdigest(), out["requirement"]["sha256"])
        m = TR.load(self.root, "STOR-100")
        f = m["frames"][0]
        self.assertEqual((f["frameUrl"], f["dir"]), (URL, "."))
        self.assertEqual(f["requirement"]["sha256"], out["requirement"]["sha256"])
        self.assertEqual(f["requirement"]["path"], "visual-requirements.pixso.json")
        self.assertEqual(TR.requirement_status(self.root, m), "ok")
        raw = TR.frame_paths(self.root, m)["raw"]
        self.assertEqual(json.loads(raw.read_text(encoding="utf-8")), json.loads(FIXTURE.read_text(encoding="utf-8")),
                         "сырой рядом — тот же документ, минифицированный")
        self.assertTrue(out["raw"]["saved"])

    def test_slim_of_fixture_keeps_structure_drops_service_fields(self):
        out = self.pull()
        stats = out["stats"]
        self.assertEqual((stats["nodesIn"], stats["nodesOut"], stats["definitionsOut"], stats["screenRoots"]),
                         (364, 364, 20, 1))
        self.assertGreater(stats["bytesIn"], stats["bytesOut"])
        self.assertEqual(out["ratio"], stats["ratio"])
        slim = json.loads(self.req().read_text(encoding="utf-8"))
        body, _ = D.unwrap(slim)
        self.assertEqual(sum(1 for _ in D.all_nodes(body)), stats["nodesOut"], "stats согласованы с деревом")
        text = self.req().read_text(encoding="utf-8")
        for gone in ("overlayBackgroundAppearance", "overlayPositionType", "overlayBackgroundInteraction"):
            self.assertNotIn(gone, text)
        self.assertNotIn(":null", text)
        self.assertEqual(stats["stylesOut"], 42)
        self.assertEqual(out["diagnostics"]["MISSING_COMPONENT_DEFINITION"]["count"], 6)
        self.assertNotIn("MISSING_COMPONENT_BASE", out["diagnostics"], "обычный компонент без mainStateGroup — норма")
        self.assertNotIn("MISSING_MAIN_COMPONENT", out["diagnostics"])

    def test_repeat_pull_changes_nothing(self):
        first = self.pull()
        manifest = (self.root / ".daiver/tracks/STOR-100/track.yaml").read_text(encoding="utf-8")
        second = self.pull()
        self.assertTrue(second["unchanged"])
        self.assertFalse(second["created"])
        self.assertEqual(second["requirement"]["sha256"], first["requirement"]["sha256"])
        self.assertEqual((self.root / ".daiver/tracks/STOR-100/track.yaml").read_text(encoding="utf-8"), manifest,
                         "pulledAt и манифест не переписываются")
        self.assertEqual(len(TR.load(self.root, "STOR-100")["frames"]), 1, "без дублей кадра")


class SlimBehaviour(Env):
    def test_library_dropped_and_only_used_styles_fonts_special_nodes_kept(self):
        out = self.pull(synth(30))
        s = out["stats"]
        self.assertEqual((s["definitionsIn"], s["definitionsOut"], s["definitionsDropped"]), (31, 1, 30))
        self.assertGreater(out["ratio"], 5, "библиотека выброшена — сжатие кратное")
        body, _ = D.unwrap(json.loads(self.req().read_text(encoding="utf-8")))
        self.assertEqual(sorted(body["localStyleMap"]), ["2:900"])
        self.assertEqual(sorted(body["sourceMapByUrl"]), ["AcmeRegular"])
        self.assertEqual([x["id"] for x in body["specialNode"]], ["2:22"])
        self.assertNotIn("editorJunk", body)
        self.assertTrue(any("editorJunk" in w for w in out["warnings"]), "выброшенные ключи названы")
        texts = [n["nodeText"] for n in D.all_nodes(body) if "nodeText" in n]
        self.assertEqual(texts, ["Hi"], "содержимое достижимых определений не потеряно")
        inst = body["pixTreeDslNodes"][0]["childNode"][0]
        self.assertNotIn("maxWidth", inst)
        self.assertNotIn("interactions", inst)
        self.assertEqual(inst["mainComponent"], "2:21")

    def test_not_losing_false_zero_and_empty_values(self):
        doc = synth(0)
        doc["dsl"]["pixTreeDslNodes"][0].update(visible=False, left=0, fillPaints=[], note=None)
        self.pull(doc)
        node = json.loads(self.req().read_text(encoding="utf-8"))["dsl"]["pixTreeDslNodes"][0]
        self.assertIs(node["visible"], False)
        self.assertEqual((node["left"], node["fillPaints"]), (0, []))
        self.assertNotIn("note", node)

    def test_diagnostics_missing_main_base_and_cycle(self):
        doc = synth(0)
        screen_kids = doc["dsl"]["pixTreeDslNodes"][0]["childNode"]
        screen_kids.append({"guid": "2:12", "type": "INSTANCE", "name": "Ghost", "mainComponent": "9:9"})
        screen_kids.append({"guid": "2:13", "type": "INSTANCE", "name": "NoBase", "mainComponent": "2:21",
                            "mainStateGroup": "9:8"})
        defs = doc["dsl"]["pixComponentTreeDslNodes"]
        defs[0]["childNode"][0]["childNode"].append(
            {"guid": "2:40", "type": "INSTANCE", "name": "ToB", "mainComponent": "2:31", "mainStateGroup": "2:30"})
        defs.append({"guid": "2:30", "type": "SYMBOL", "name": "B", "childNode": [
            {"guid": "2:31", "type": "SYMBOL", "name": "v", "childNode": [
                {"guid": "2:41", "type": "INSTANCE", "name": "ToA", "mainComponent": "2:21",
                 "mainStateGroup": "2:20"}]}]})
        diag = self.pull(doc)["diagnostics"]
        self.assertEqual(diag["MISSING_MAIN_COMPONENT"]["count"], 1)
        self.assertEqual(diag["MISSING_MAIN_COMPONENT"]["examples"][0]["name"], "Ghost")
        self.assertEqual(diag["MISSING_COMPONENT_BASE"]["count"], 1)
        self.assertGreaterEqual(diag["COMPONENT_EXPANSION_CYCLE"]["count"], 1)

    def test_several_screen_roots_warn(self):
        out = self.pull(synth(0, roots=3))
        self.assertEqual(out["stats"]["screenRoots"], 3)
        self.assertTrue(any("3 корня экрана" in w for w in out["warnings"]))

    def test_slim_does_not_mutate_input_and_is_deterministic(self):
        doc = synth(5)
        before = copy.deepcopy(doc)
        a, _ = S.slim(doc)
        b, _ = S.slim(copy.deepcopy(doc))
        self.assertEqual(doc, before)
        self.assertEqual(S.dumps(a), S.dumps(b))


class Registration(Env):
    def test_changed_repull_drops_canonical_and_names_stale_artifacts(self):
        self.pull(synth(2))
        d = self.root / ".daiver/tracks/STOR-100"
        (d / "visual-requirements.canonical.json").write_text("{}", encoding="utf-8")
        (d / "plan.json").write_text("{}", encoding="utf-8")
        changed = synth(2)
        changed["dsl"]["pixComponentTreeDslNodes"][0]["childNode"][0]["childNode"][0]["nodeText"] = "Bye"
        out = self.pull(changed)
        self.assertFalse(out["unchanged"])
        self.assertEqual(out["invalidated"], ["visual-requirements.canonical.json"])
        self.assertFalse((d / "visual-requirements.canonical.json").exists())
        self.assertEqual(out["stale"], ["plan.json"])
        self.assertTrue(any("plan.json" in w for w in out["warnings"]))

    def test_same_url_in_other_track_is_named(self):
        self.pull(track="STOR-1")
        out = self.pull(track="STOR-2")
        self.assertTrue(any("STOR-1" in w and "один URL" in w for w in out["warnings"]), out["warnings"])

    def test_built_frame_is_refused_with_reason(self):
        self.pull()
        (self.root / ".daiver/tracks/STOR-100/build-state.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            self.pull(synth(1))
        self.assertIn("6.3", str(cm.exception))
        self.assertIn("уже собран", str(cm.exception))

    def test_second_frame_is_refused_until_multi_frame_task(self):
        self.pull()
        with self.assertRaises(T.ToolError) as cm:
            self.pull(synth(1), frame="dialog")
        self.assertIn("6.1", str(cm.exception))

    def test_raw_write_failure_is_a_warning_not_a_refusal(self):
        d = self.root / ".daiver/tracks/STOR-100"
        (d / "visual-requirements.pixso.raw.json").mkdir(parents=True)     # на месте файла — каталог
        out = self.pull()
        self.assertFalse(out["raw"]["saved"])
        self.assertTrue(any("сырой экспорт рядом не сохранён" in w for w in out["warnings"]))
        self.assertTrue(self.req().is_file(), "slim и регистрация прошли")


class Refusals(Env):
    def call(self, **args):
        return T.call("daiver_pixso_pull", self.root, args)

    def test_inline_json_and_missing_path(self):
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", frameUrl=URL, dslJson="{}")
        self.assertIn("контекст модели", str(cm.exception))
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", frameUrl=URL)
        self.assertIn("dslPath", str(cm.exception))
        with self.assertRaises(T.ToolError):
            self.call(track="STOR-100", dslPath="x.json")                   # нет обязательного frameUrl

    def test_frame_url_is_required_and_not_invented(self):
        self.put(FIXTURE.read_text(encoding="utf-8"))
        for bad in ("", "ftp://x/y", "pixso app", "https://"):
            with self.assertRaises(T.ToolError, msg=repr(bad)) as cm:
                self.call(track="STOR-100", dslPath="dsl.json", frameUrl=bad)
            self.assertIn("frameUrl", str(cm.exception))

    def test_not_pixso_not_json_missing_and_outside(self):
        self.put('{"a": 1}', "other.json")
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", dslPath="other.json", frameUrl=URL)
        self.assertIn("не Pixso", str(cm.exception))
        self.put("{oops", "bad.json")
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", dslPath="bad.json", frameUrl=URL)
        self.assertIn("не JSON", str(cm.exception))
        with self.assertRaises(T.ToolError) as cm:
            self.call(track="STOR-100", dslPath="нет.json", frameUrl=URL)
        self.assertIn("нет", str(cm.exception))
        for outside in ("/etc/passwd", "../x.json"):
            with self.assertRaises(T.ToolError, msg=outside):
                self.call(track="STOR-100", dslPath=outside, frameUrl=URL)
        with self.assertRaises(T.ToolError):
            self.call(track="../evil", dslPath="other.json", frameUrl=URL)
        self.assertFalse((self.root / ".daiver").exists(), "отказы ничего не создают")

    def test_absolute_path_inside_repo_is_accepted(self):
        self.put(FIXTURE.read_text(encoding="utf-8"))
        out = self.call(track="STOR-100", dslPath=str(self.root / "dsl.json"), frameUrl=URL)
        self.assertTrue(out["created"])


if __name__ == "__main__":
    unittest.main()
