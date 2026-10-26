"""test_visual_review.py — визуальный обзор, рукопожатие двух вызовов (задача 5.2, спека §14.7, §3.4, INV-C30, C31, A8, F3, QG-U19, N19, N20).

Пинит: первый вызов ничего не пишет и отдаёт images (картинка дизайна ПЕРВОЙ) / prompt / reportPath / priorFixes / slices; промпт в обоих режимах (остаток после доказанных
дефектов — БЕЗ значений и без таблицы `expected`, полный — семь осей и формат плана ремонта); разбор Fix plan прошлого файла и вердикты FIXED / NOT FIXED / REGRESSED;
приём ответа по форме (opened, prior, findings | nothingFound, значения запрещены в остатке, числа обязательны и оговорки запрещены в полном); отказ при `verify.vision: off`;
`skipped` без файла; второй вызов пишет visual-review.md и visual-review.json, а запись итерации берёт находки и reviewReport из артефактов (находки vision чистоту не блокируют);
`priorReview` читается только из последней записи; геометрия нарезки длинного экрана; JPEG-копии через canvas (skip без Chrome).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testkit as K  # noqa: E402
from daiver import image as IMG  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import visual_review as VR  # noqa: E402
from test_screenshot import skip_without_browser  # noqa: E402
from test_verify_state import World, defect  # noqa: E402


# ── чистые части ──────────────────────────────────────────────────────────────

class Geometry(unittest.TestCase):
    def test_short_screens_are_not_sliced(self):
        for h in (1, 600, VR.MAX_SLICE_H):
            self.assertEqual(VR.slices(h), [])

    def test_long_screens_get_overlapping_bands_that_reach_the_bottom(self):
        for h in (VR.MAX_SLICE_H + 1, 2500, 5000, 3 * VR.MAX_SLICE_H):
            bands = VR.slices(h)
            self.assertGreater(len(bands), 1, h)
            self.assertEqual([b["n"] for b in bands], list(range(1, len(bands) + 1)))
            self.assertEqual(bands[0]["y"], 0)
            self.assertEqual(bands[-1]["y"] + bands[-1]["h"], h, f"последняя полоса доходит до низа: {h}")
            self.assertTrue(all(0 < b["h"] <= VR.MAX_SLICE_H for b in bands))
            for a, b in zip(bands, bands[1:]):
                self.assertEqual(a["y"] + a["h"] - b["y"], VR.SLICE_OVERLAP, "соседние полосы перекрываются, а не оставляют щель")

    def test_custom_limits(self):
        self.assertEqual(VR.slices(250, max_h=100, overlap=10), [{"n": 1, "y": 0, "h": 100}, {"n": 2, "y": 90, "h": 100}, {"n": 3, "y": 180, "h": 70}])


class FixPlan(unittest.TestCase):
    MD = "# Обзор\n\n## Находки\n\n1. это не пункт плана\n\n## Fix plan\n\n1. Иконка — заменить на 16×16\n2. Перенос — ширина 320\n\n## Хвост\n\n3. и это тоже нет\n"

    def test_items_come_only_from_the_fix_plan_section(self):
        self.assertEqual(VR.parse_fix_plan(self.MD), [{"n": 1, "text": "Иконка — заменить на 16×16"}, {"n": 2, "text": "Перенос — ширина 320"}])

    def test_an_empty_or_bare_plan_is_absent(self):
        for md in ("## Fix plan\n", "## Fix plan\n\n   \n", "# x\n## Fix plan\n\n## Другое\n1. нет\n", "", None, "без разделов"):
            self.assertEqual(VR.parse_fix_plan(md), [], repr(md))


class Prompts(unittest.TestCase):
    DEFECTS = [{"id": "a", "code": "SPACING", "prop": "gap", "element": "Заголовок", "message": "промежуток 16 против 24px",
                "rows": [{"МАКЕТ": "16", "РЕАЛИЗАЦИЯ": "24", "count": 2}], "expected": "16", "actual": "24"},
               {"id": "b", "code": "COLOR", "prop": "background", "element": "Кнопка #ff0000"}, {"id": "c", "code": "MISSING", "noise": True}]

    def test_residue_lists_proven_defects_without_values_and_never_the_expected_table(self):
        lines = VR.proven_lines([d for d in self.DEFECTS if not d.get("noise")])
        self.assertEqual(lines, ["SPACING (gap) — «Заголовок»", "COLOR (background)"])
        p = VR.build_prompt("residue", "goal", lines, None, [], "r.md")
        for banned in ("16", "24", "px", "#ff0000", "МАКЕТ", "РЕАЛИЗАЦИЯ", "expected", "промежуток 16"):
            self.assertNotIn(banned, p, banned)
        for needle in ("УЖЕ нашло и доказало", "SPACING (gap)", "ОСТАТОК", "иконки и изображения", "перекрытия и обрезка", "перенос и усечение текста", "визуальный порядок",
                       "сбои рендера", "ЗАПРЕЩЕНЫ любые значения", "«Ничего не нашёл» — законный ответ", "nothingFound: true", "ПЕРВАЯ картинка — дизайн кадра"):
            self.assertIn(needle, p, needle)
        self.assertNotIn("Сейчас", p)
        self.assertNotIn("семи осям", p)

    def test_full_review_has_seven_axes_the_repair_format_and_bans_hedges(self):
        p = VR.build_prompt("full", "goal", [], None, [], "r.md")
        for needle in ("ПОЛНЫЙ обзор по семи осям", "7. сбои рендера", "`now`", "`shouldBe`", "`fix`", "`check`", "Числа ОБЯЗАТЕЛЬНЫ", "«слегка», «примерно»", "nothingFound: true"):
            self.assertIn(needle, p, needle)
        self.assertEqual(len(VR.FULL_AXES), 7)
        self.assertNotIn("УЖЕ нашло", p)

    def test_the_reference_is_named_as_a_reconstruction(self):
        p = VR.build_prompt("full", "reference", [], None, [], "r.md")
        self.assertIn("РЕКОНСТРУКЦИЯ", p)
        self.assertIn("Картинки дизайна у кадра нет", p)

    def test_prior_fixes_and_slices_are_in_the_prompt(self):
        p = VR.build_prompt("full", "goal", [], [{"n": 1, "text": "Иконка — 16×16"}, {"n": 2, "text": "Перенос"}], VR.slices(2500), "r.md")
        self.assertIn("FIXED, NOT FIXED или REGRESSED", p)
        self.assertIn("1. Иконка — 16×16", p)
        self.assertIn("Экран длинный (", p)
        no_prior = VR.build_prompt("full", "goal", [], None, [], "r.md")
        self.assertNotIn("Прежний Fix plan", no_prior)
        self.assertNotIn("Экран длинный", no_prior)


class Validation(unittest.TestCase):
    IMAGES = ["a/design.png", "a/actual.png"]

    def ok(self, **kw):
        base = {"opened": self.IMAGES, "findings": [{"title": "Иконка", "now": "нет иконки слева от текста"}]}
        base.update(kw)
        return base

    def fail(self, review, mode="residue", prior=None):
        with self.assertRaises(VR.ReviewError) as cm:
            VR.validate(review, self.IMAGES, mode, prior)
        return str(cm.exception)

    def test_shape(self):
        for bad in (None, "текст", [], 5):
            self.assertIn("ожидается объект", self.fail(bad))
        self.assertIn("неизвестные поля", self.fail(self.ok(extra=1)))

    def test_opened_must_be_exactly_the_given_images_in_order(self):
        for opened in (None, [], ["a/design.png"], ["a/actual.png", "a/design.png"], ["a/design.png", "a/actual.png", "x"]):
            self.assertIn("opened:", self.fail(self.ok(opened=opened)), repr(opened))
        self.assertEqual(VR.validate(self.ok(), self.IMAGES, "residue", None)["findings"][0]["title"], "Иконка")

    def test_an_empty_review_must_say_nothing_found_explicitly(self):
        self.assertIn("пустой обзор", self.fail({"opened": self.IMAGES}))
        self.assertIn("пустой обзор", self.fail({"opened": self.IMAGES, "findings": []}))
        self.assertIn("несовместимо", self.fail(self.ok(nothingFound=True)))
        res = VR.validate({"opened": self.IMAGES, "nothingFound": True}, self.IMAGES, "residue", None)
        self.assertEqual((res["findings"], res["nothingFound"]), ([], True))

    def test_residue_forbids_values(self):
        for text in ("отступ 12px", "ширина 50 %", "цвет #fff", "цвет #ff00aa80", "rgba(0, 0, 0, 0.5)", "rgb (1,2,3)", "hsl(10, 20%, 30%)", "шрифт 14 px", "размер 2rem"):
            msg = self.fail(self.ok(findings=[{"title": "Находка", "now": text}]))
            self.assertIn("значения запрещены", msg, text)
        for text in ("иконка слева отсутствует", "текст обрезан многоточием", "кнопка накладывается на подпись", "в списке 3 строки вместо 4"):
            VR.validate(self.ok(findings=[{"title": "Находка", "now": text}]), self.IMAGES, "residue", None)

    def test_full_requires_fields_numbers_and_forbids_hedges(self):
        good = {"title": "Отступ", "now": "отступ меньше", "shouldBe": "16 px", "fix": "padding-left: 16px", "check": "сверить с макетом"}
        VR.validate(self.ok(findings=[good]), self.IMAGES, "full", None)
        for drop in ("now", "shouldBe", "fix", "check"):
            self.assertIn(f"нет поля «{drop}»", self.fail(self.ok(findings=[{k: v for k, v in good.items() if k != drop}]), "full"))
        self.assertIn("нужны числа", self.fail(self.ok(findings=[dict(good, shouldBe="больше", fix="увеличить")]), "full"))
        for hedge in ("слегка", "примерно", "чуть шире", "немного", "около 16", "приблизительно"):
            self.assertIn("слова-оговорки", self.fail(self.ok(findings=[dict(good, now=f"{hedge} отличается")]), "full"), hedge)

    def test_prior_verdicts_are_required_complete_and_known(self):
        prior = [{"n": 1, "text": "Иконка"}, {"n": 2, "text": "Перенос"}]
        self.assertIn("prior: вердикт", self.fail(self.ok(), prior=prior))
        self.assertIn("нет вердикта на пункты 2", self.fail(self.ok(prior=[{"n": 1, "verdict": "FIXED"}]), prior=prior))
        self.assertIn("нет таких прежних пунктов: 9", self.fail(self.ok(prior=[{"n": 1, "verdict": "FIXED"}, {"n": 2, "verdict": "NOT FIXED"}, {"n": 9, "verdict": "FIXED"}]), prior=prior))
        self.assertIn("verdict", self.fail(self.ok(prior=[{"n": 1, "verdict": "DONE"}, {"n": 2, "verdict": "FIXED"}]), prior=prior))
        res = VR.validate(self.ok(prior=[{"n": 2, "verdict": "REGRESSED", "note": " вернулся "}, {"n": 1, "verdict": "FIXED"}]), self.IMAGES, "residue", prior)
        self.assertEqual([(p["n"], p["verdict"], p["note"]) for p in res["prior"]], [(1, "FIXED", ""), (2, "REGRESSED", "вернулся")])
        self.assertIn("переотчитываться не о чем", self.fail(self.ok(prior=[{"n": 1, "verdict": "FIXED"}])))

    def test_all_problems_come_in_one_message(self):
        msg = self.fail({"opened": [], "findings": [{"title": "x", "now": "12px"}, {"now": "без заголовка"}]})
        self.assertIn("обзор не принят (", msg)
        self.assertGreaterEqual(msg.count(";"), 2)

    def test_finding_ids_are_stable_and_do_not_depend_on_the_wording_of_the_rest(self):
        a = VR.finding_id({"axis": "иконки", "title": "Нет иконки"})
        self.assertEqual(a, VR.finding_id({"axis": "иконки", "title": "  нет   иконки "}))
        self.assertNotEqual(a, VR.finding_id({"axis": "иконки", "title": "Другая иконка"}))
        self.assertEqual(len(a), 12)

    def test_the_md_round_trips_through_the_plan_parser(self):
        norm = {"prior": [{"n": 1, "text": "Иконка", "verdict": "FIXED", "note": ""}],
                "findings": [{"title": "Перенос", "axis": "текст", "now": "лишний перенос", "shouldBe": "в одну строку, 320", "fix": "ширина 320", "check": "виден в одну строку"},
                             {"title": "Подпись", "now": "нет подписи"}], "nothingFound": False}
        md = VR.render_md("iter-2", "full", "goal", ["a", "b"], norm)
        self.assertEqual(VR.parse_fix_plan(md), [{"n": 1, "text": "Перенос — ширина 320"}])
        for needle in ("iteration: iter-2", "mode: full", "ground: goal", "1. [FIXED] Иконка", "### 1. Перенос", "- Сейчас: лишний перенос", "- Должно быть: в одну строку, 320",
                       "НЕПОДТВЕРЖДЁННЫЕ (zone: vision)"):
            self.assertIn(needle, md, needle)
        empty = VR.render_md("iter-1", "residue", "reference", ["a", "b"], {"prior": [], "findings": [], "nothingFound": True})
        self.assertIn("Ничего не найдено.", empty)
        self.assertEqual(VR.parse_fix_plan(empty), [])
        self.assertIn("реконструкция", empty)


# ── на настоящем треке ────────────────────────────────────────────────────────

class Flow(World):
    def setUp(self):
        super().setUp()
        self.it = self.verify / "iter-1"
        self.it.mkdir(parents=True, exist_ok=True)

    def rel(self, p):
        return p.relative_to(self.root).as_posix()

    def actual(self, w=40, h=30, name="iter-1"):
        d = self.verify / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "actual.png").write_bytes(K.png_bytes(w, h))
        return d

    def goal(self, w=40, h=30, box=None):
        m = TR.load(self.root, self.TB.TRACK)
        src = self.root / "goal-src.png"
        src.write_bytes(K.png_bytes(w, h))
        TR.attach_raster(self.root, m, None, src)
        for f in m["frames"]:
            f.pop("frameBox", None)
        if box:
            TR.upsert_frame(m, "main", frameBox=box)
        TR.upsert_frame(m, "main", raster="visual-requirements.png")
        TR.save(self.root, m)

    def call(self, name="iter-1", **kw):
        return T.call("daiver_visual_review", self.root, dict({"track": self.TB.TRACK, "iterationDir": f".daiver/tracks/{self.TB.TRACK}/verify/{name}"}, **kw))

    def listing(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())

    def images(self, r):
        return [i["path"] for i in r["images"]]

    def test_the_first_call_writes_nothing_and_orders_the_images(self):
        self.actual()
        self.goal()
        before = self.listing()
        r = self.call()
        self.assertEqual(self.listing(), before, "первый вызов ничего не пишет")
        self.assertEqual(r["images"][0]["role"], "design")
        self.assertTrue(r["images"][0]["path"].endswith("visual-requirements.png"))
        self.assertTrue(r["images"][1]["path"].endswith("iter-1/actual.png"))
        self.assertEqual((r["mode"], r["ground"], r["proven"], r["priorFixes"], r["slices"], r["written"], r["vision"]), ("full", "goal", 0, None, None, False, True))
        self.assertTrue(r["reportPath"].endswith("iter-1/visual-review.md"))
        self.assertIn("не можешь открыть", r["nextStep"])
        self.assertIn("ПЕРВАЯ картинка — дизайн кадра", r["prompt"])

    def test_proven_defects_narrow_the_prompt_without_the_values(self):
        self.actual()
        self.goal()
        (self.it / "defects.json").write_text(json.dumps({"defects": [defect("a", rows=[{"МАКЕТ": "777", "РЕАЛИЗАЦИЯ": "888", "count": 1}], message="отступ 777px", prop="padding.left"),
                                                                       defect("n", noise=True)]}), encoding="utf-8")
        (self.it / "code-defects.json").write_text(json.dumps({"defects": [defect("a"), defect("c", code="TEXT", prop="text")]}), encoding="utf-8")
        r = self.call()
        self.assertEqual((r["mode"], r["proven"]), ("residue", 2), "noise не в счёт, общий id — один")
        for banned in ("777", "888", "МАКЕТ", "px"):
            self.assertNotIn(banned, r["prompt"])
        self.assertIn("SIZE (padding.left)", r["prompt"])
        self.assertIn("TEXT (text)", r["prompt"])

    def test_without_a_goal_image_the_reference_is_used_and_said_so(self):
        self.actual()
        (self.it / "reference.png").write_bytes(K.png_bytes(40, 30))
        r = self.call()
        self.assertEqual((r["ground"], r["images"][0]["role"]), ("reference", "reference"))
        self.assertIn("реконструкция", r["warnings"][0])
        self.assertIn("РЕКОНСТРУКЦИЯ", r["prompt"])

    def test_frame_box_sets_the_design_size_and_a_long_screen_is_sliced(self):
        self.actual(40, 2500)
        self.goal(60, 2600, {"x": 10, "y": 10, "w": 40, "h": 2500})
        r = self.call()
        self.assertEqual(r["images"][0]["cropBox"], {"x": 10, "y": 10, "w": 40, "h": 2500})
        self.assertEqual(r["images"][0]["size"], {"w": 40, "h": 2500})
        self.assertEqual(r["slices"], VR.slices(2500))
        self.assertIn("Экран длинный", r["prompt"])

    def test_the_second_call_writes_the_md_and_the_json_and_returns_vision_defects(self):
        self.actual()
        self.goal()
        first = self.call()
        review = {"opened": self.images(first), "findings": [{"title": "Нет иконки", "axis": "иконки и изображения", "now": "слева от заголовка нет иконки",
                                                              "shouldBe": "иконка 16×16 слева, отступ 8", "fix": "добавить иконку 16×16", "check": "иконка видна"}]}
        r = self.call(review=review)
        self.assertEqual((r["gate"], r["written"], r["findings"], r["nothingFound"], r["mode"]), ("pass", True, 1, False, "full"))
        md = (self.it / "visual-review.md").read_text(encoding="utf-8")
        self.assertIn("### 1. Нет иконки", md)
        side = json.loads((self.it / "visual-review.json").read_text(encoding="utf-8"))
        [d] = side["defects"]
        self.assertEqual((d["zone"], d["source"], d["severity"], d["code"]), ("vision", "review", "major", "VISION"))
        self.assertEqual(r["defects"], [d])
        self.assertEqual(r["reviewReport"]["path"], self.rel(self.it / "visual-review.md"))
        self.assertIn("НЕПОДТВЕРЖДЁННЫЕ", r["nextStep"])

    def test_a_rejected_review_writes_nothing(self):
        self.actual()
        self.goal()
        before = self.listing()
        with self.assertRaises(T.ToolError) as cm:
            self.call(review={"opened": [], "nothingFound": True})
        self.assertIn("opened:", str(cm.exception))
        with self.assertRaises(T.ToolError):
            self.call(review={"opened": self.images(self.call()), "findings": [{"title": "x", "now": "x", "shouldBe": "1", "fix": "1", "check": "1"}, {"title": "y"}]})
        self.assertEqual(self.listing(), before)

    def test_nothing_found_is_a_legal_answer(self):
        self.actual()
        self.goal()
        r = self.call(review={"opened": self.images(self.call()), "nothingFound": True})
        self.assertEqual((r["gate"], r["findings"], r["nothingFound"], r["defects"]), ("pass", 0, True, []))
        self.assertIn("Ничего не найдено.", (self.it / "visual-review.md").read_text(encoding="utf-8"))

    def test_skipped_writes_no_file_and_needs_a_reason(self):
        self.actual()
        self.goal()
        before = self.listing()
        r = self.call(review={"skipped": "  модель не   принимает изображения  "})
        self.assertEqual((r["gate"], r["reviewSkipped"], r["written"]), ("skipped", "модель не принимает изображения", False))
        self.assertEqual(self.listing(), before, "N19: файл не пишется, картинка не описана")
        for bad in ("", "   ", None, 5):
            with self.assertRaises(T.ToolError):
                self.call(review={"skipped": bad})

    def test_vision_off_is_a_skipped_gate_and_an_answer_is_refused(self):
        self.actual()
        self.goal()
        cfg = self.root / ".daiver" / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("vision: on", text)
        cfg.write_text(text.replace("vision: on", "vision: off"), encoding="utf-8")
        r = self.call()
        self.assertEqual((r["gate"], r["written"]), ("skipped", False))
        self.assertIn("verify.vision: off", r["reviewSkipped"])
        self.assertNotIn("prompt", r)
        with self.assertRaises(T.ToolError) as cm:
            self.call(review={"opened": ["x", "y"], "nothingFound": True})
        self.assertIn("выключен конфигом", str(cm.exception))
        self.assertEqual(self.call(review={"skipped": "off"})["gate"], "skipped")
        self.assertFalse((self.it / "visual-review.md").exists())

    def test_the_record_takes_gate_findings_and_report_from_the_artifacts_and_vision_never_blocks(self):
        d = self.fake_iteration(1, compare="clean")
        (d / "actual.png").write_bytes(K.png_bytes(40, 30))
        self.goal()
        first = self.call()
        self.call(review={"opened": self.images(first), "findings": [{"title": "Нет иконки", "now": "нет иконки", "shouldBe": "иконка 16", "fix": "добавить иконку 16", "check": "видна"}]})
        res = self.rec(1, gates={"review": "skipped"})
        self.assertTrue(res["clean"], "находка vision чистоту не блокирует")
        self.assertEqual(res["gates"]["review"], "pass")
        self.assertTrue(any("артефакты итерации говорят «pass»" in w for w in res["warnings"]))
        rec = self.state()["history"][0]
        self.assertEqual(rec["reviewReport"]["path"], self.rel(d / "visual-review.md"))
        self.assertEqual([(x["zone"], x["source"]) for x in rec["defects"]], [("vision", "review")])
        self.assertEqual(rec["blocking"], [])

    def test_a_review_claimed_without_the_artifact_is_recorded_as_skipped(self):
        self.fake_iteration(1, compare="clean")
        res = self.rec(1, gates={"review": "pass"}, reviewSkipped="картинки не открылись")
        self.assertEqual(res["gates"]["review"], "skipped")
        self.assertTrue(any("артефактов итерации нет" in w for w in res["warnings"]))
        self.assertEqual(self.state()["history"][0]["reviewSkipped"], "картинки не открылись")

    def test_prior_fixes_come_from_the_last_recorded_review_only(self):
        d1 = self.fake_iteration(1, compare="clean")
        (d1 / "actual.png").write_bytes(K.png_bytes(40, 30))
        self.goal()
        f1 = self.call()
        self.call(review={"opened": self.images(f1), "findings": [{"title": "Иконка", "now": "нет иконки", "shouldBe": "иконка 16", "fix": "добавить 16", "check": "видна"},
                                                                  {"title": "Перенос", "now": "лишний перенос", "shouldBe": "320", "fix": "ширина 320", "check": "одна строка"}]})
        self.rec(1)
        self.assertEqual(self.opened()["priorReview"]["path"], self.rel(d1 / "visual-review.md"), "priorReview в state — Fix plan прошлой итерации")
        d2 = self.fake_iteration(2, compare="clean")
        (d2 / "actual.png").write_bytes(K.png_bytes(40, 30))
        f2 = self.call(name="iter-2")
        self.assertEqual([p["n"] for p in f2["priorFixes"]], [1, 2])
        self.assertIn("Иконка — добавить 16", f2["prompt"])
        with self.assertRaises(T.ToolError) as cm:
            self.call(name="iter-2", review={"opened": self.images(f2), "nothingFound": True})
        self.assertIn("prior:", str(cm.exception))
        self.call(name="iter-2", review={"opened": self.images(f2), "nothingFound": True, "prior": [{"n": 1, "verdict": "FIXED"}, {"n": 2, "verdict": "NOT FIXED", "note": "всё ещё"}]})
        md = (d2 / "visual-review.md").read_text(encoding="utf-8")
        self.assertIn("1. [FIXED] Иконка — добавить 16", md)
        self.assertIn("2. [NOT FIXED] Перенос — ширина 320 — всё ещё", md)

    def test_the_latest_recorded_review_wins_over_older_ones(self):
        self.goal()
        for n, title in ((1, "Старый пункт"), (2, "Новый пункт")):
            d = self.fake_iteration(n, compare="clean")
            (d / "actual.png").write_bytes(K.png_bytes(40, 30))
            f = self.call(name=f"iter-{n}")
            prior = [{"n": p["n"], "verdict": "FIXED"} for p in f["priorFixes"] or []]
            self.call(name=f"iter-{n}", review={"opened": self.images(f), "findings": [{"title": title, "now": "x", "shouldBe": "1", "fix": "поправить 1", "check": "ok"}],
                                                **({"prior": prior} if prior else {})})
            self.rec(n)
        d3 = self.fake_iteration(3, compare="clean")
        (d3 / "actual.png").write_bytes(K.png_bytes(40, 30))
        f3 = self.call(name="iter-3")
        self.assertEqual([p["text"] for p in f3["priorFixes"]], ["Новый пункт — поправить 1"])

    def test_without_a_prior_review_no_other_file_is_substituted(self):
        d1 = self.fake_iteration(1, compare="clean")
        (d1 / "actual.png").write_bytes(K.png_bytes(40, 30))
        self.goal()
        (d1 / "visual-review.md").write_text("## Fix plan\n\n1. Чужой пункт — фикс\n", encoding="utf-8")        # файл есть, но итерация записана БЕЗ обзора
        self.rec(1)
        d2 = self.fake_iteration(2, compare="clean")
        (d2 / "actual.png").write_bytes(K.png_bytes(40, 30))
        r = self.call(name="iter-2")
        self.assertIsNone(r["priorFixes"], "priorReview=null → пунктов нет")
        self.assertNotIn("Чужой пункт", r["prompt"])

    def test_a_prior_report_pointing_outside_verify_is_not_read(self):
        d1 = self.fake_iteration(1, compare="clean")
        (d1 / "actual.png").write_bytes(K.png_bytes(40, 30))
        self.goal()
        self.rec(1)
        st = self.state()
        (self.root / "evil.md").write_text("## Fix plan\n\n1. Подмена\n", encoding="utf-8")
        st["history"][0]["reviewReport"] = {"path": "evil.md"}
        self.state_path().write_text(json.dumps(st), encoding="utf-8")
        d2 = self.fake_iteration(2, compare="clean")
        (d2 / "actual.png").write_bytes(K.png_bytes(40, 30))
        r = self.call(name="iter-2")
        self.assertIsNone(r["priorFixes"])
        self.assertTrue(any("вне verify/" in w for w in r["warnings"]))

    def test_errors_are_named(self):
        with self.assertRaises(T.ToolError) as cm:
            self.call()
        self.assertIn("daiver_screenshot", str(cm.exception))
        self.actual()
        with self.assertRaises(T.ToolError):
            self.call()
        self.goal()
        with self.assertRaises(T.ToolError) as cm:
            self.call(prepare="png")
        self.assertIn("prepare", str(cm.exception))
        with self.assertRaises(T.ToolError):
            T.call("daiver_visual_review", self.root, {"track": self.TB.TRACK, "iterationDir": "src/x"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_visual_review", self.root, {"track": "NOPE-1", "iterationDir": ".daiver/tracks/NOPE-1/verify/i"})

    def test_jpeg_copies_through_the_canvas(self):
        skip_without_browser(self)
        self.actual(40, 30)
        self.goal(60, 50, {"x": 10, "y": 10, "w": 40, "h": 30})
        r = self.call(prepare="jpeg")
        self.assertTrue(r["written"])
        got = r["jpegCopies"]
        self.assertEqual(sorted(got), ["actual.jpg", "design.jpg"])
        for name in got:
            data = (self.root / got[name]).read_bytes()
            info = IMG.sniff_bytes(data)
            self.assertEqual((info["format"], info["width"], info["height"]), ("jpeg", 40, 30), name)
        self.assertIn("JPEG-копии", r["nextStep"])

    def test_long_screens_get_jpeg_slices(self):
        skip_without_browser(self)
        self.actual(40, 1300)
        self.goal(40, 1300)
        r = self.call(prepare="jpeg")
        bands = VR.slices(1300)
        self.assertEqual(len(bands), 2)
        self.assertEqual(sorted(r["jpegCopies"]), ["actual-1.jpg", "actual-2.jpg", "design-1.jpg", "design-2.jpg"])
        self.assertEqual(IMG.sniff_bytes((self.root / r["jpegCopies"]["design-2.jpg"]).read_bytes())["height"], bands[1]["h"])


if __name__ == "__main__":
    unittest.main()
