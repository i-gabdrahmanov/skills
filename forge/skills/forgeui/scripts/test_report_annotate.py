"""test_report_annotate.py — интерактив report.html: решения на дефектах, «Пометить проблему», экспорт `verify-decisions.json` (задача 5.4, спека §14.10, INV-C32, C34, QG-P6).

Настоящий Chrome через CDP-клиент 4.1 (skip без Chrome; FORGEUI_TEST_REQUIRE_BROWSER=1 — провал) кликает по отчёту мышью и забирает экспорт. Пинит: hit-test по вшитым боксам снимка —
наименьший бокс под курсором, повторный клик в том же месте — шаг наружу и по кругу; прямоугольник мышью (координаты страницы со сдвигом clip, у макета — координаты корня кадра),
нативный drag картинки отменён; без режима «Пометить проблему» клики ничего не делают; метка без заметки не принимается; id меток `user-<итерация>-<n>` не сталкиваются со старыми;
кнопки fix / skip / false_positive кладут в экспорт решение с заметкой, кадром, снимком значений и именем решившего; ранее принятые решения, метки и записи ДРУГИХ кадров едут в экспорт
нетронутыми; удалённая метка из экспорта уходит; экспорт читается движком обратно (круг: отчёт → файл → запись итерации); `</script>`, кавычки и перевод строки в заметке доезжают без потерь.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testkit as K  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.browser import cdp as CD  # noqa: E402
from daiver.verify import decisions as DEC  # noqa: E402
from test_screenshot import skip_without_browser  # noqa: E402
from test_verify_state import World, defect  # noqa: E402

SNAPSHOT = {"v": 1, "root": {"id": "dom:0", "kind": "frame", "box": {"x": 0, "y": 0, "w": 100, "h": 50}, "children": [
    {"id": "dom:0.0", "kind": "frame", "box": {"x": 10, "y": 10, "w": 40, "h": 20}, "children": [
        {"id": "dom:0.0.0", "kind": "text", "box": {"x": 12, "y": 12, "w": 10, "h": 10}}]},
    {"id": "dom:0.1", "kind": "frame", "box": {"x": 60, "y": 5, "w": 30, "h": 30}}]},
    "capture": {"clip": {"x": 0, "y": 0, "w": 100, "h": 50}}}


class Annotate(World):
    def setUp(self):
        super().setUp()
        self.TB.set_canon(self.root, self.TB.frame("Page", [self.TB.leaf("t1", name="t1", text="Заголовок")], name=self.TB.autoname("Page"),
                                                   size={"w": 100, "h": 50}, layout={"direction": "column", "gap": 8}))
        self.it = self.verify / "iter-1"
        self.it.mkdir(parents=True, exist_ok=True)
        (self.it / "actual.png").write_bytes(K.png_bytes(100, 50))
        (self.it / "snapshot.json").write_text(json.dumps(SNAPSHOT), encoding="utf-8")
        (self.it / "compare-summary.json").write_text(json.dumps({"status": "defects", "matchRate": 0.9, "fidelity": 0.8}), encoding="utf-8")
        (self.it / "defects.json").write_text(json.dumps({"defects": [defect("a", element="Заголовок"), defect("b", element="Кнопка")]}), encoding="utf-8")
        m = TR.load(self.root, self.TB.TRACK)
        src = self.root / "g.png"
        src.write_bytes(K.png_bytes(100, 50, lambda x, y: (240, 240, 250, 255)))
        TR.attach_raster(self.root, m, None, src)
        TR.upsert_frame(m, "main", raster="visual-requirements.png")
        TR.save(self.root, m)
        self.page = None

    def put(self, marks=(), decisions=()):
        (self.track_dir / DEC.FILE).write_text(json.dumps({"schema": 1, "decisions": list(decisions), "marks": list(marks)}), encoding="utf-8")

    def open(self, **kw):
        exe = skip_without_browser(self)
        r = T.call("daiver_verify_report", self.root, dict({"track": self.TB.TRACK, "iterationDir": self.it.relative_to(self.root).as_posix()}, **kw))
        self.assertTrue(r["rendered"], r)
        chrome = CD.Chrome.launch(exe, file_roots=[self.root.resolve()])
        self.addCleanup(chrome.close)
        self.page = chrome.new_page((self.it / "report.html").resolve().as_uri(), viewport=(1280, 2200))
        return r

    # --- помощники ---
    def js(self, expr):
        return self.page.evaluate(expr)

    def export(self):
        return json.loads(self.js("JSON.stringify(window.__daiverExport())"))

    def text(self, sel):
        return self.js(f"document.querySelector({json.dumps(sel)}).textContent")

    def at(self, side, px, py, w=100, h=50):
        """Точка картинки в координатах viewport по доле (px/w, py/h)."""
        b = self.page.element_box(f'.shot[data-side="{side}"] img')
        return b["x"] + px / w * b["width"], b["y"] + py / h * b["height"]

    def click_el(self, sel):
        b = self.page.element_box(sel)
        self.assertIsNotNone(b, sel)
        self.page.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)

    def mode_on(self):
        self.click_el("#mark-toggle")

    def drag(self, side, p0, p1):
        x0, y0 = self.at(side, *p0)
        x1, y1 = self.at(side, *p1)
        send = lambda t, x, y, **kw: self.page.send("Input.dispatchMouseEvent", dict({"type": t, "x": x, "y": y}, **kw))
        send("mouseMoved", x0, y0)
        send("mousePressed", x0, y0, button="left", clickCount=1)
        send("mouseMoved", (x0 + x1) / 2, (y0 + y1) / 2, button="left")
        send("mouseMoved", x1, y1, button="left")
        send("mouseReleased", x1, y1, button="left", clickCount=1)

    def add_mark(self, note):
        self.js(f"document.getElementById('mark-note').value = {json.dumps(note)}")
        self.click_el("#mark-add")

    # --- тесты ---
    def test_a_click_picks_the_smallest_box_and_repeated_clicks_step_outward_and_wrap(self):
        self.open()
        self.mode_on()
        seen = []
        for _ in range(4):
            self.page.click(*self.at("impl", 15, 15))
            seen.append(self.text("#mark-what"))
        self.assertIn("dom:0.0.0", seen[0])
        self.assertIn("dom:0.0 ", seen[1] + " ", "второй клик в том же месте — шаг наружу")
        self.assertNotIn("dom:0.0.0", seen[1])
        self.assertIn("dom:0 ", seen[2] + " ")
        self.assertNotIn("dom:0.0", seen[2])
        self.assertEqual(seen[3], seen[0], "после самого внешнего — по кругу")
        self.page.click(*self.at("impl", 75, 20))
        self.assertIn("dom:0.1", self.text("#mark-what"), "клик в другом месте начинает с наименьшего бокса там")
        self.assertIn("30×30", self.text("#mark-what"))

    def test_a_clicked_element_becomes_a_mark_with_its_node_id_and_page_box(self):
        self.open()
        self.mode_on()
        self.page.click(*self.at("impl", 15, 15))
        self.add_mark("заголовок не того размера")
        got = self.export()
        self.assertEqual(len(got["marks"]), 1)
        m = got["marks"][0]
        self.assertEqual((m["id"], m["side"], m["nodeId"], m["frame"], m["iteration"]), ("user-1-1", "impl", "dom:0.0.0", "main", "iter-1"))
        self.assertEqual((m["box"], m["note"]), ({"x": 12, "y": 12, "w": 10, "h": 10}, "заголовок не того размера"))
        self.assertRegex(m["decidedAt"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d")
        self.assertEqual(self.js("document.querySelectorAll('.pin.user').length"), 1, "метка нарисована пином")
        self.assertIn("user-1-1", self.text("#my-marks"))

    def test_a_dragged_rectangle_is_a_box_in_page_coordinates_shifted_by_the_clip(self):
        snap = dict(SNAPSHOT, capture={"clip": {"x": 7, "y": 3, "w": 100, "h": 50}})
        (self.it / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
        self.open()
        self.mode_on()
        self.drag("impl", (20, 10), (60, 40))
        self.assertIn("40×30", self.text("#mark-what"))
        self.add_mark("прямоугольник")
        m = self.export()["marks"][0]
        self.assertEqual((m["side"], m["box"]), ("impl", {"x": 27, "y": 13, "w": 40, "h": 30}), "координаты страницы = пиксели картинки + clip")
        self.assertNotIn("nodeId", m)
        self.assertEqual(self.js("document.querySelectorAll('.mark-draft').length"), 0, "черновик убран после добавления")

    def test_a_drag_in_any_direction_and_beyond_the_picture_is_clamped(self):
        self.open()
        self.mode_on()
        self.drag("impl", (60, 40), (-30, -20))
        m_text = self.text("#mark-what")
        self.assertIn("60×40", m_text, "от (60,40) до края картинки (0,0): рамка не уходит за картинку")
        self.add_mark("x")
        b = self.export()["marks"][0]["box"]
        self.assertEqual(b, {"x": 0, "y": 0, "w": 60, "h": 40})

    def test_native_image_drag_is_cancelled(self):
        self.open()
        self.assertFalse(self.js("document.querySelector('.shot[data-side=\"impl\"] img').dispatchEvent(new Event('dragstart', {cancelable: true}))"))

    def test_without_the_mode_clicks_do_nothing_and_a_note_is_required(self):
        self.open()
        self.page.click(*self.at("impl", 15, 15))
        self.assertTrue(self.js("document.getElementById('mark-form').hidden"))
        self.mode_on()
        self.page.click(*self.at("impl", 15, 15))
        self.assertFalse(self.js("document.getElementById('mark-form').hidden"))
        self.add_mark("   ")
        self.assertEqual(self.export()["marks"], [])
        self.assertIn("Заметка обязательна", self.text("#mark-status"))
        self.click_el("#mark-cancel")
        self.assertTrue(self.js("document.getElementById('mark-form').hidden"))
        self.assertEqual(self.js("document.querySelectorAll('.mark-draft').length"), 0)

    def test_a_click_on_the_design_asks_for_a_rectangle_and_a_rectangle_is_in_root_coordinates(self):
        self.open()
        self.mode_on()
        self.page.click(*self.at("design", 15, 15))
        self.assertIn("протяни рамку", self.text("#mark-status"))
        self.assertTrue(self.js("document.getElementById('mark-form').hidden"))
        self.drag("design", (10, 5), (50, 25))
        self.add_mark("на макете иначе")
        m = self.export()["marks"][0]
        self.assertEqual((m["side"], m["box"]), ("design", {"x": 10, "y": 5, "w": 40, "h": 20}))

    def with_root_at(self, x, y):
        self.TB.set_canon(self.root, self.TB.frame("Page", [self.TB.leaf("t1", name="t1", text="Заголовок")], name=self.TB.autoname("Page"),
                                                   size={"w": 100, "h": 50}, box={"x": x, "y": y, "w": 100, "h": 50}, layout={"direction": "column", "gap": 8}))

    def test_a_design_rectangle_is_measured_from_the_root_origin_of_the_frame(self):
        self.with_root_at(50, 20)
        self.open()
        self.mode_on()
        self.drag("design", (10, 5), (50, 25))
        self.add_mark("на макете иначе")
        self.assertEqual(self.export()["marks"][0]["box"], {"x": 60, "y": 25, "w": 40, "h": 20}, "координаты корня кадра, а не картинки")

    def test_a_design_rectangle_that_leaves_through_negative_coordinates_is_refused_with_a_reason(self):
        self.with_root_at(-30, 0)
        self.open()
        self.mode_on()
        self.drag("design", (0, 10), (10, 30))
        self.assertIn("отрицательные координаты", self.text("#mark-status"))
        self.assertTrue(self.js("document.getElementById('mark-form').hidden"))
        self.assertEqual(self.js("document.querySelectorAll('.mark-draft').length"), 0)
        self.assertEqual(self.export()["marks"], [])

    def test_an_id_taken_by_a_defect_or_an_answer_is_skipped_for_a_new_mark(self):
        (self.it / "defects.json").write_text(json.dumps({"defects": [defect("user-1-1"), defect("b")]}), encoding="utf-8")
        self.open()
        self.mode_on()
        self.page.click(*self.at("impl", 75, 20))
        self.add_mark("новая")
        self.assertEqual([m["id"] for m in self.export()["marks"]], ["user-1-2"], "id дефекта не затирается меткой")

    def test_new_ids_do_not_collide_with_old_marks_and_old_ids_stay(self):
        self.put([{"id": "user-1-1", "note": "старая", "side": "impl", "box": {"x": 1, "y": 1, "w": 5, "h": 5}, "iteration": "iter-1", "frame": "main", "reviewer": "Анна",
                   "decidedAt": "2026-10-01T10:00:00Z"}])
        self.open()
        self.mode_on()
        self.page.click(*self.at("impl", 75, 20))
        self.add_mark("новая")
        got = self.export()["marks"]
        self.assertEqual([m["id"] for m in got], ["user-1-1", "user-1-2"])
        self.assertEqual(got[0]["note"], "старая")
        self.assertEqual(got[0]["reviewer"], "Анна", "старая запись едет нетронутой")

    def test_decision_buttons_write_the_decision_with_note_frame_snapshot_and_reviewer(self):
        self.open()
        self.js("document.getElementById('reviewer').value = 'Борис'")
        self.js("document.querySelector('.defect[data-id=\"a\"] .dnote').value = 'поправить дословно: отступ 16'")
        self.click_el('.defect[data-id="a"] [data-verdict="fix"]')
        self.click_el('.defect[data-id="b"] [data-verdict="false_positive"]')
        got = self.export()
        by = {d["id"]: d for d in got["decisions"]}
        self.assertEqual(set(by), {"a", "b"})
        self.assertEqual((by["a"]["verdict"], by["a"]["note"], by["a"]["reviewer"], by["a"]["frame"]), ("fix", "поправить дословно: отступ 16", "Борис", "main"))
        self.assertEqual(by["a"]["snapshot"], DEC.snapshot_of(defect("a")), "снимок — значения, про которые человек решал")
        self.assertNotIn("note", by["b"], "пустая заметка не пишется")
        self.assertRegex(by["a"]["decidedAt"], r"^20\d\d-\d\d-\d\dT")
        self.assertEqual(got["schema"], 1)
        self.assertEqual(self.js("document.querySelector('.defect[data-id=\"a\"] [data-verdict=\"fix\"]').classList.contains('on')"), True)
        self.click_el('.defect[data-id="a"] [data-verdict="skip"]')
        self.assertEqual({d["id"]: d["verdict"] for d in self.export()["decisions"]}["a"], "skip", "второй ответ заменяет первый")
        self.js("document.querySelector('.defect[data-id=\"a\"] .dnote').value = 'позже'; document.querySelector('.defect[data-id=\"a\"] .dnote').dispatchEvent(new Event('input'))")
        self.assertEqual({d["id"]: d.get("note") for d in self.export()["decisions"]}["a"], "позже", "правка заметки меняет уже данный ответ")
        self.js("document.querySelector('.defect[data-id=\"a\"] .dnote').value = '  '; document.querySelector('.defect[data-id=\"a\"] .dnote').dispatchEvent(new Event('input'))")
        self.assertNotIn("note", {d["id"]: d for d in self.export()["decisions"]}["a"], "стёртая заметка не остаётся пробелом")
        self.click_el('.defect[data-id="a"] .vb:nth-of-type(4)')
        self.assertEqual([d["id"] for d in self.export()["decisions"]], ["b"], "«снять» убирает ответ")

    def test_earlier_answers_and_other_frames_travel_through_the_export_untouched(self):
        old = {"id": "b", "verdict": "skip", "note": "так задумано", "reviewer": "Анна", "decidedAt": "2026-10-01T10:00:00Z", "extra": "сохрани",
               "snapshot": {"rows": [{"МАКЕТ": "10", "РЕАЛИЗАЦИЯ": "20"}]}}
        other = {"id": "z", "verdict": "fix", "frame": "other", "note": "про другой кадр"}
        other_mark = {"id": "user-9-1", "note": "чужая метка", "side": "impl", "box": {"x": 1, "y": 1, "w": 2, "h": 2}, "frame": "other"}
        self.put(marks=[other_mark], decisions=[old, other])
        self.open()
        self.click_el('.defect[data-id="a"] [data-verdict="fix"]')
        got = self.export()
        ids = [d["id"] for d in got["decisions"]]
        self.assertEqual(ids, ["b", "a", "z"], "старые, новые, затем записи других кадров")
        self.assertEqual(got["decisions"][0], old)
        self.assertEqual(got["decisions"][2], other)
        self.assertEqual(got["marks"], [other_mark])

    def test_a_removed_mark_leaves_the_export_and_its_pin_and_decisions_go_with_it(self):
        self.put([{"id": "user-1-1", "note": "старая", "side": "impl", "box": {"x": 1, "y": 1, "w": 5, "h": 5}, "iteration": "iter-1", "frame": "main"}])
        self.open()
        self.assertEqual(self.js("document.querySelectorAll('.pin').length"), 1, "метка из файла нарисована пином")
        self.click_el('.defect[data-id="user-1-1"] .vb:last-of-type')
        self.assertEqual(self.export()["marks"], [])
        self.assertEqual(self.js("document.querySelectorAll('.pin:not([hidden])').length"), 0)
        self.assertIn("удалена", self.text('.defect[data-id="user-1-1"]'))

    def test_hostile_text_survives_the_round_trip_and_the_engine_reads_the_file_back(self):
        note = 'a</script><b>"кавычки" \'и\' \\ слэш\nвторая строка & <!-- --> \u2028 конец'
        self.open()
        self.mode_on()
        self.drag("impl", (10, 10), (30, 30))
        self.add_mark(note)
        self.click_el('.defect[data-id="b"] [data-verdict="skip"]')
        exported = self.export()
        self.assertEqual(exported["marks"][0]["note"], note)
        self.assertEqual(self.js("document.getElementById('export-json').value"), json.dumps(exported, ensure_ascii=False, indent=2) + "\n",
                         "текстовое поле и объект — один и тот же файл")
        (self.track_dir / DEC.FILE).write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")
        r = self.rec(1)
        self.assertEqual(r["decisions"]["reported"], ["user-1-1"])
        self.assertEqual(r["decisions"]["answered"], {"skip": ["b"]})
        d = {x["id"]: x for x in self.state()["history"][0]["defects"]}
        self.assertEqual(d["user-1-1"]["problem"], note, "заметка из браузера доехала до записи итерации дословно")
        self.assertEqual(d["user-1-1"]["pin"], {"x": 10, "y": 10, "w": 20, "h": 20})
        again = T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "iterationDir": self.it.relative_to(self.root).as_posix()})
        self.assertEqual(again["decisions"]["reported"], ["user-1-1"])
        self.assertEqual(again["decisions"]["answered"], {"skip": ["b"]})

    def test_the_page_tells_where_to_put_the_file_and_counts_what_it_holds(self):
        self.open()
        self.assertIn("verify-decisions.json", self.text("body"))
        self.assertIn(f".daiver/tracks/{self.TB.TRACK}/verify-decisions.json", self.text("body"))
        self.assertEqual(self.text("#export-count"), "решений: 0 · меток: 0")
        self.click_el('.defect[data-id="a"] [data-verdict="fix"]')
        self.assertEqual(self.text("#export-count"), "решений: 1 · меток: 0")


class Static(World):
    """Без браузера: страница не теряет данные для интерактива."""

    def test_the_embedded_data_carries_everything_the_script_needs(self):
        self.TB.set_canon(self.root, self.TB.frame("Page", [self.TB.leaf("t1", name="t1", text="x")], name=self.TB.autoname("Page"), size={"w": 100, "h": 50}))
        it = self.verify / "iter-3"
        it.mkdir(parents=True, exist_ok=True)
        (it / "actual.png").write_bytes(K.png_bytes(100, 50))
        (it / "snapshot.json").write_text(json.dumps(SNAPSHOT), encoding="utf-8")
        (it / "compare-summary.json").write_text(json.dumps({"status": "defects"}), encoding="utf-8")
        (it / "defects.json").write_text(json.dumps({"defects": [defect("a")]}), encoding="utf-8")
        (self.track_dir / DEC.FILE).write_text(json.dumps({"schema": 1, "decisions": [{"id": "a", "verdict": "fix"}], "marks": [
            {"id": "user-3-4", "note": "n", "side": "impl", "box": {"x": 1, "y": 1, "w": 2, "h": 2}, "iteration": "iter-3"}]}), encoding="utf-8")
        T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "iterationDir": it.relative_to(self.root).as_posix()})
        text = (it / "report.html").read_text(encoding="utf-8")
        start = text.index('id="report-data">') + len('id="report-data">')
        data = json.loads(text[start:text.index("</script>", start)])
        self.assertEqual((data["markPrefix"], data["nextMark"]), ("user-3-", 5), "следующий номер — после занятых этой итерации")
        self.assertEqual([b[0] for b in data["boxes"]], ["dom:0", "dom:0.0", "dom:0.1", "dom:0.0.0"], "боксы снимка вшиты обходом в ширину")
        self.assertEqual(data["decisionsFile"]["a"]["verdict"], "fix")
        self.assertEqual(list(data["marksFile"]), ["user-3-4"])
        self.assertEqual(data["images"]["impl"], {"w": 100, "h": 50})
        self.assertEqual(data["snapshots"]["a"], DEC.snapshot_of(defect("a")))
        self.assertIn("user-3-4", data["defects"], "метка — пункт отчёта наравне с дефектами")
        self.assertEqual(data["foreign"], {"decisions": [], "marks": []})
        RH = __import__("daiver.verify.report_html", fromlist=["x"])
        deep = {"id": "dom:r", "box": {"x": 0, "y": 0, "w": 9, "h": 9}, "children": [{"id": "dom:z", "box": {"x": 1, "y": 1, "w": 0, "h": 5}}, {"id": "dom:h", "box": {"x": 1, "y": 1, "w": 5, "h": 0}},
                                                                                       {"id": "dom:n", "box": {"x": 1, "y": 1, "w": "5", "h": 5}}, {"id": "dom:ok", "box": {"x": 1, "y": 1, "w": 2, "h": 2}}, "не узел", {"box": {"x": 1, "y": 1, "w": 2, "h": 2}}]}
        self.assertEqual([b[0] for b in RH._boxes(deep)], ["dom:r", "dom:ok"], "нулевые, нечисловые и безымянные боксы в hit-test не попадают")
        self.assertEqual(RH._boxes(None), [])
        self.assertEqual(len(RH._boxes({"id": "r", "box": {"x": 0, "y": 0, "w": 1, "h": 1}, "children": [{"id": f"c{i}", "box": {"x": 0, "y": 0, "w": 1, "h": 1}} for i in range(RH.MAX_BOXES + 50)]})), RH.MAX_BOXES)
        self.assertNotIn("</script", __import__("daiver.verify.report_html", fromlist=["x"]).ANNOTATE_JS)


if __name__ == "__main__":
    unittest.main()
