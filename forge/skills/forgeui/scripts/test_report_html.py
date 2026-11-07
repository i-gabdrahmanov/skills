"""test_report_html.py — report.html итерации (задача 5.3, спека §14.10, INV-C32, QG-P6).

Пинит: одинаковый вход — побайтно одинаковый файл (дата — вход, не часы); самодостаточность (картинки data-URI, ни одного внешнего ресурса); экранирование
(`</script>` и кавычки в текстах дефектов и решений не ломают страницу, встроенный JSON читается обратно); пины — номер дефекта = номер пина, проценты считаются от размера
картинки со сдвигом clip; чипы только из фактических кодов; русские имена кодов и свойств; ссылки на узлы макета по nodeUrl; ранее принятые решения (в том числе переоткрытое);
полоса кадров (при двух кадрах, «не проверялся»); `stateRecorded: false`; автономный режим не рендерит; отказы. Страницу разбирает stdlib-парсер.
"""
from __future__ import annotations

import html.parser
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testkit as K  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import decisions as DEC  # noqa: E402
from daiver.verify import report_html as RH  # noqa: E402
from test_verify_state import World, defect  # noqa: E402


class Page(html.parser.HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.scripts, self._in_script, self.text = [], [], [], False, []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.append((tag, dict(attrs)))
        if tag == "script":
            self._in_script, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
            self.scripts.append("".join(self._buf))

    def handle_data(self, data):
        if self._in_script:
            self._buf.append(data)
        else:
            self.text.append(data)

    def by(self, tag):
        return [a for t, a in self.attrs if t == tag]


class Cases(World):
    def setUp(self):
        super().setUp()
        self.TB.set_canon(self.root, self.TB.frame("Page", [self.TB.leaf("t1", name="t1", text="Заголовок"), self.TB.leaf("t2", name="t2", text="Описание")],
                                                   name=self.TB.autoname("Page"), size={"w": 100, "h": 50}, layout={"direction": "column", "gap": 8}))
        self.it = self.verify / "iter-1"
        self.it.mkdir(parents=True, exist_ok=True)
        (self.it / "actual.png").write_bytes(K.png_bytes(100, 50))
        self.goal()

    def goal(self):
        m = TR.load(self.root, self.TB.TRACK)
        src = self.root / "g.png"
        src.write_bytes(K.png_bytes(100, 50, lambda x, y: (240, 240, 250, 255)))
        TR.attach_raster(self.root, m, None, src)
        TR.upsert_frame(m, "main", raster="visual-requirements.png")
        TR.save(self.root, m)

    def put_defects(self, defects, name="defects.json"):
        (self.it / "compare-summary.json").write_text(json.dumps({"status": "defects", "matchRate": 0.93, "fidelity": 0.8, "nextStep": "чини по одному"}), encoding="utf-8")
        (self.it / name).write_text(json.dumps({"defects": defects}), encoding="utf-8")

    def report(self, **kw):
        r = T.call("daiver_verify_report", self.root, dict({"track": self.TB.TRACK, "iterationDir": self.rel(self.it)}, **kw))
        return r, (self.it / "report.html").read_text(encoding="utf-8") if (self.it / "report.html").exists() else None

    def rel(self, p):
        return p.relative_to(self.root).as_posix()

    def sample(self):
        return [defect("a", "critical", code="SPACING", prop="padding.left", element="Заголовок", designLink="x", design="2:153703", coverage=3,
                       pin={"x": 30, "y": 30, "w": 20, "h": 10}, designPin={"x": 10, "y": 5, "w": 20, "h": 10}, lifecycle="persisting",
                       rows=[{"МАКЕТ": "16", "РЕАЛИЗАЦИЯ": "8", "count": 3}]),
                defect("b", "major", code="TYPOGRAPHY", prop="s1:размер шрифта", element="Описание"),
                defect("c", "major", code="COLOR", prop="background", element="Кнопка", location={"file": "src/A.tsx", "line": 12})]

    def test_same_input_same_bytes_and_the_date_is_an_input(self):
        self.put_defects(self.sample())
        _, a = self.report(date="2026-10-26")
        _, b = self.report(date="2026-10-26")
        self.assertEqual(a, b)
        _, c = self.report(date="2026-10-27")
        self.assertNotEqual(a, c)
        self.assertIn("2026-10-26", a)
        _, nodate = self.report()
        self.assertNotRegex(nodate, r"20\d\d-\d\d-\d\d")
        for bad in ("", "   ", "a\nb", "x" * 41):
            with self.assertRaises(T.ToolError):
                self.report(date=bad)

    def test_self_contained_no_external_resources(self):
        self.put_defects(self.sample())
        (self.it / "diff.png").write_bytes(K.png_bytes(100, 50))
        _, text = self.report()
        pg = Page(text)
        imgs = pg.by("img")
        self.assertEqual(len(imgs), 3, "макет, реализация, дифф")
        self.assertTrue(all(i["src"].startswith("data:image/png;base64,") for i in imgs))
        self.assertEqual(pg.by("link"), [])
        self.assertFalse([s for s in pg.by("script") if "src" in s])
        self.assertNotIn("@import", text)
        self.assertNotRegex(text, r"url\(\s*['\"]?https?:")
        self.assertIn("prefers-color-scheme:dark", text)
        self.assertIn('name="viewport"', text)

    def test_hostile_text_cannot_break_the_page_and_the_embedded_json_reads_back(self):
        evil = '</script><img src=x onerror=alert(1)> "кавычки" &  '
        self.put_defects([defect("a", "major", message=evil, element=evil, prop="gap")])
        (self.track_dir / DEC.FILE).write_text(json.dumps({"schema": 1, "decisions": [{"id": "a", "verdict": "fix", "note": evil, "reviewer": evil}]}), encoding="utf-8")
        _, text = self.report()
        pg = Page(text)
        self.assertEqual(text.count("</script>"), len(pg.scripts), "закрывающих тегов ровно столько, сколько скриптов")
        self.assertNotIn("<img src=x", text)
        self.assertEqual(len([a for a in pg.by("img")]), 2)
        seeded = json.loads(next(s for s in pg.scripts if s.startswith("{") and "verdict" in s))
        self.assertEqual(seeded["a"]["note"], evil)
        self.assertEqual(seeded["a"]["reviewer"], evil)
        self.assertTrue(all(chr(0x2028) not in s and chr(0x2029) not in s for s in pg.scripts), "в скриптах сырого разделителя строк нет (экранированный \\u2028 — можно)")
        self.assertIn("&lt;/script&gt;", text)

    def test_attribute_values_are_escaped_and_json_key_order_does_not_matter(self):
        self.put_defects([defect('a"><x', "major", prop="gap")])
        _, text = self.report()
        pg = Page(text)
        card = [a for a in pg.by("div") if "data-id" in a][0]
        self.assertEqual(card["data-id"], 'a"><x', "кавычка и скобка в id не вышли из атрибута")
        self.assertNotIn('data-id="a"><x"', text)
        dec = self.track_dir / DEC.FILE
        dec.write_text(json.dumps({"schema": 1, "decisions": [{"id": "a", "verdict": "fix", "note": "n", "reviewer": "r"}]}), encoding="utf-8")
        _, one = self.report()
        dec.write_text(json.dumps({"decisions": [{"reviewer": "r", "note": "n", "verdict": "fix", "id": "a"}], "schema": 1}), encoding="utf-8")
        _, two = self.report()
        self.assertEqual(one, two, "порядок ключей входа на байты не влияет")

    def test_a_root_without_a_numeric_size_has_no_design_pins_and_says_so(self):
        self.TB.set_canon(self.root, self.TB.frame("Page", [self.TB.leaf("t1", name="t1", text="Заголовок")], name=self.TB.autoname("Page"), layout={"direction": "column"}))
        self.put_defects(self.sample())
        _, text = self.report()
        self.assertIn("пины макета не показаны: у корня кадра нет числового размера", text)
        pins = re.findall(r'<div class="pin" data-n="(\d+)"', text)
        self.assertEqual(pins, ["1"], "пин реализации остался (бокс страницы известен), пин макета — нет")

    def test_chips_exist_only_for_present_codes_with_counts(self):
        self.put_defects(self.sample() + [defect("d", "major", code="COLOR", prop="color")])
        _, text = self.report()
        pg = Page(text)
        chips = {a["data-filter"]: t for a in pg.by("span") if "data-filter" in a for t in [a]}
        self.assertEqual(sorted(chips), ["COLOR", "SPACING", "TYPOGRAPHY"])
        self.assertIn("цвет · 2", text)
        self.assertNotIn('data-filter="SIZE"', text)
        self.assertIn("отступы · 1", text)

    def test_pins_are_numbered_like_the_defects_and_positioned_in_percent(self):
        self.put_defects(self.sample())
        sk = json.loads((self.it / "snapshot.json").read_text(encoding="utf-8")) if (self.it / "snapshot.json").exists() else {}
        (self.it / "snapshot.json").write_text(json.dumps({"capture": {"clip": {"x": 10, "y": 20, "w": 100, "h": 50}}}), encoding="utf-8")
        _, text = self.report()
        pins = re.findall(r'<div class="pin" data-n="(\d+)" data-code="(\w+)" style="([^"]+)"><b>(\d+)</b></div>', text)
        self.assertEqual([(n, c, b) for n, c, s, b in pins if s.startswith("left:20%")], [("1", "SPACING", "1")], "бокс страницы со сдвигом clip (10, 20): 20/100, 10/50")
        self.assertEqual(RH._pct(1, 3), "33.333")
        impl = [s for n, c, s, b in pins if n == "1"]
        self.assertIn("left:20%;top:20%;width:20%;height:20%", impl)
        self.assertIn("left:10%;top:10%;width:20%;height:20%", impl, "пин макета — процентами от размера корня кадра 100×50")
        cards = re.findall(r'<div class="defect" data-n="(\d+)" data-id="(\w+)"', text)
        self.assertEqual(cards, [("1", "a"), ("2", "b"), ("3", "c")])

    def test_codes_and_properties_speak_russian_and_rows_are_a_table(self):
        self.put_defects(self.sample())
        _, text = self.report()
        for needle in ("отступы", "типографика", "цвет", "внутренний отступ слева", "слот s1: размер шрифта", "цвет фона", "критичный", "значимый", "остался", "×3",
                       "<th>МАКЕТ</th><th>РЕАЛИЗАЦИЯ</th>", "<td>16</td><td>8</td><td>3</td>", "src/A.tsx:12"):
            self.assertIn(needle, text, needle)
        self.assertEqual(RH.prop_ru("w"), "ширина")
        self.assertEqual(RH.prop_ru("неизвестное"), "неизвестное")
        self.assertEqual(RH.prop_ru(None), "")

    def test_design_links_follow_the_frame_url(self):
        self.put_defects(self.sample())
        _, text = self.report()
        self.assertIn('<a href="https://pixso.example/a?item-id=2:153703">2:153703</a>', text)

    def test_earlier_decisions_are_shown_including_a_reopened_one(self):
        base = defect("a", "major", prop="gap", rows=[{"МАКЕТ": "1", "РЕАЛИЗАЦИЯ": "2", "count": 1}])
        self.put_defects([base, defect("b", "major", prop="w")])
        (self.track_dir / DEC.FILE).write_text(json.dumps({"schema": 1, "decisions": [
            {"id": "a", "verdict": "skip", "note": "так задумано", "reviewer": "Анна", "snapshot": {"rows": [{"МАКЕТ": "9", "РЕАЛИЗАЦИЯ": "9"}]}},
            {"id": "b", "verdict": "false_positive", "note": "сглаживание"}]}), encoding="utf-8")
        _, text = self.report()
        self.assertIn("решение человека: skip (Анна): так задумано — переоткрыт: значения уехали из-под решения", text)
        self.assertIn("решение человека: false_positive: сглаживание", text)
        self.assertIn("шум", text)

    def test_frames_bar_names_unchecked_frames(self):
        m = TR.load(self.root, self.TB.TRACK)
        TR.upsert_frame(m, "second", frameUrl="https://pixso.example/b?item-id=1:1")
        TR.save(self.root, m)
        self.it = self.verify / "iter-1"                       # с двумя кадрами раскладка папок кадра меняется — берём из манифеста
        self.it.mkdir(parents=True, exist_ok=True)
        (self.it / "actual.png").write_bytes(K.png_bytes(10, 10))
        self.put_defects([defect("a")])
        _, text = self.report()
        self.assertIn("cur", re.search(r'<span class="chip static([^"]*)">main', text).group(1))
        self.assertIn("second — не проверялся", text)

    def test_unrecorded_iteration_is_flagged_and_a_recorded_one_is_not(self):
        self.put_defects(self.sample())
        r, text = self.report()
        self.assertFalse(r["stateRecorded"])
        self.assertIn("stateRecorded: false", text)
        self.assertIn("сначала daiver_verify_state_record", r["nextStep"])
        self.rec(1)
        r, text = self.report()
        self.assertTrue(r["stateRecorded"])
        self.assertNotIn("stateRecorded: false", text)
        self.assertIn("ПУТЬ ОТЧЁТА", r["nextStep"])
        self.assertTrue(r["report"].endswith("iter-1/report.html"))

    def test_the_result_names_filters_ground_and_size(self):
        self.put_defects(self.sample())
        r, text = self.report()
        self.assertEqual((r["rendered"], r["defects"], r["filters"], r["ground"], r["bytes"]), (True, 3, ["COLOR", "SPACING", "TYPOGRAPHY"], "goal", len(text.encode("utf-8"))))

    def test_vision_findings_are_marked_unconfirmed_and_the_review_is_named(self):
        self.put_defects([defect("a")])
        (self.it / "visual-review.json").write_text(json.dumps({"mode": "residue", "ground": "goal", "nothingFound": False, "defects": [
            {"id": "v1", "code": "VISION", "layer": "pixel", "severity": "major", "zone": "vision", "source": "review", "element": "Иконка", "message": "нет иконки"}]}), encoding="utf-8")
        (self.it / "visual-review.md").write_text("# x\n", encoding="utf-8")
        _, text = self.report()
        self.assertIn("не подтверждено (обзор)", text)
        self.assertIn("наблюдение обзора", text)
        self.assertIn("Визуальный обзор", text)
        self.assertIn("режим: residue", text)

    def test_diff_zones_are_drawn_explained_and_not(self):
        self.put_defects([defect("a")])
        (self.it / "visual-diff.json").write_text(json.dumps({"diffPercent": 3.18, "zones": [{"x": 0, "y": 0, "w": 50, "h": 25, "explainedBy": ["a"]}, {"x": 50, "y": 25, "w": 50, "h": 25}]}),
                                                  encoding="utf-8")
        (self.it / "diff.png").write_bytes(K.png_bytes(100, 50))
        _, text = self.report()
        self.assertEqual(text.count('class="zone ex"'), 1)
        self.assertEqual(text.count('class="zone"'), 1)
        self.assertIn("дифф 3.18%", text)
        self.assertIn("сигнал, не вердикт", text)

    def test_without_a_design_image_the_pane_says_so(self):
        m = TR.load(self.root, self.TB.TRACK)
        for f in m["frames"]:
            f.pop("raster", None)
        TR.save(self.root, m)
        self.put_defects([defect("a")])
        _, text = self.report()
        self.assertIn("картинки дизайна у кадра нет", text)
        (self.it / "reference.png").write_bytes(K.png_bytes(100, 50))
        _, text = self.report()
        self.assertIn("эталонный рендер — реконструкция", text)

    def test_empty_report(self):
        (self.it / "compare-summary.json").write_text(json.dumps({"status": "clean", "matchRate": 1.0, "fidelity": 1.0}), encoding="utf-8")
        (self.it / "defects.json").write_text(json.dumps({"defects": []}), encoding="utf-8")
        _, text = self.report()
        self.assertIn("Дефектов нет.", text)
        self.assertIn("чисто", text)
        self.assertEqual([a for a in Page(text).by("span") if "data-filter" in a], [], "чипов нет, когда нечего фильтровать")

    def test_autonomous_mode_renders_nothing(self):
        cfg = self.root / ".daiver" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("mode: interactive", "mode: autonomous", 1), encoding="utf-8")
        self.put_defects(self.sample())
        r, text = self.report()
        self.assertEqual((r["rendered"], text), (False, None))
        self.assertIn("триаж", r["reason"])

    def test_errors_are_named(self):
        (self.it / "actual.png").unlink()
        with self.assertRaises(T.ToolError) as cm:
            self.report()
        self.assertIn("daiver_screenshot", str(cm.exception))
        (self.it / "actual.png").write_bytes(K.png_bytes(10, 10))
        with self.assertRaises(T.ToolError):
            T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "iterationDir": "src/x"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_verify_report", self.root, {"track": "NOPE-1", "iterationDir": ".daiver/tracks/NOPE-1/verify/i"})
        (self.track_dir / DEC.FILE).write_text(json.dumps({"schema": 9, "decisions": []}), encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            self.report()
        self.assertIn("версия схемы", str(cm.exception))

    def test_script_json_and_pin_style_units(self):
        s = RH.script_json({"k": "</script>&<>  "})
        for bad in ("<", ">", "&", " ", " "):
            self.assertNotIn(bad, s)
        self.assertEqual(json.loads(s), {"k": "</script>&<>  "})
        self.assertIsNone(RH.pin_style({"x": 1}, (0, 0), {"w": 10, "h": 10}))
        self.assertIsNone(RH.pin_style({"x": 1, "y": 1, "w": 1, "h": 1}, (0, 0), {"w": 0, "h": 10}))
        self.assertEqual(RH.pin_style({"x": 5, "y": 5, "w": 5, "h": 5}, (0, 0), {"w": 20, "h": 10}), "left:25%;top:50%;width:25%;height:50%")


if __name__ == "__main__":
    unittest.main()
