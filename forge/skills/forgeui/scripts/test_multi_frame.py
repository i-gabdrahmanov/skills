"""test_multi_frame.py — мультикадровый трек (задача 6.1, спека §4.2, §13.1, §14.1, INV-C3, C39, QG-P3).

Пинит: второй `daiver_pixso_pull` переносит семью первого кадра в `frames/main/` и пишет `frames/<имя>/` для нового; манифест, `dir` и пути требований сходятся; ответ называет переезд строкой
«LAYOUT — …»; собственные файлы трека (track.yaml, run.json, ui-plan.md, plan.lock.json, resolution-report.md, verify-report.md, verify-decisions.json, спеки) и чужие файлы пользователя остаются на
месте байт в байт; пути `.daiver/tracks/<T>/<семья>` в состоянии и артефактах итераций переписываются, а файлы под подписью (plan.json, plan.md, resolutions.json, resolution-report.md) — нет (подпись не
слетает); `verify-report.md` пересобирается; старый путь продолжает находить файл по новому месту (и не трогает остальные пути); обратно ничего не движется (третий кадр — без переезда); два кадра
на одном URL — отказ до единого изменения; сбой переезда откатывает всё (манифест не теряет первый кадр); занятая цель — отказ без движения; статус считает ступень по первому нечистому кадру;
план не-первичного кадра несёт решение о месте корня в едином модуле (`module`) — по умолчанию own-file, inside-primary с проверенным host, отказы для первичного кадра и кадра-состояния,
правка руками видна как stale, а у первичного кадра план не меняется ни байтом; дайджест подписи называет модуль только когда он есть; полоса кадров отчёта ссылается на последний отчёт соседнего кадра.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _testkit as K  # noqa: E402
from daiver import canon as C  # noqa: E402
from daiver import layout as LAY  # noqa: E402
from daiver import plan as PL  # noqa: E402
from daiver import plan_digest as PD  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from test_pixso_pull import synth  # noqa: E402
from test_verify_state import World, defect  # noqa: E402

URL2 = "https://pixso.example/app/design/abc?item-id=9:9"
URL3 = "https://pixso.example/app/design/abc?item-id=9:10"


class Layout(World):
    @property
    def tdir(self):
        return self.root / ".daiver" / "tracks" / self.TB.TRACK

    def pull(self, frame="dialog", url=URL2, doc=None):
        (self.root / f"{frame}.dsl.json").write_text(json.dumps(doc or synth(1)), encoding="utf-8")
        return T.call("daiver_pixso_pull", self.root, {"track": self.TB.TRACK, "dslPath": f"{frame}.dsl.json", "frameUrl": url, "frame": frame})

    def tree(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file() and "dsl.json" not in p.name}

    def own_files(self):
        t = self.tdir
        files = {"run.json": '{"mode": "interactive"}', "ui-plan.md": "# План\nпути: .daiver/tracks/STOR-100/plan.json\n", "plan.lock.json": '{"schema": 1}',
                 "verify-decisions.json": '{"schema": 1, "decisions": [], "marks": []}', "notes.md": "заметка пользователя .daiver/tracks/STOR-100/verify/iter-1",
                 "tele.json": "{}", "story.spec.md": "# спека"}
        for n, text in files.items():
            (t / n).write_text(text, encoding="utf-8")
        return {n: (t / n).read_bytes() for n in files} | {"resolution-report.md": (t / "resolution-report.md").read_bytes(), "track.yaml": None}

    # --- переезд ---
    def test_the_second_pull_moves_the_family_and_writes_the_new_frame_beside_it(self):
        before = (self.tdir / "plan.json").read_bytes(), (self.tdir / "visual-requirements.canonical.json").read_bytes()
        r = self.pull()
        self.assertTrue(r["layout"]["message"].startswith("LAYOUT — "))
        self.assertEqual(r["layout"]["movedFrame"], "main")
        self.assertIn("plan.json", r["layout"]["moved"])
        self.assertIn(r["layout"]["message"], r["notes"])
        m = TR.load(self.root, self.TB.TRACK)
        self.assertEqual([(f["name"], f["dir"]) for f in m["frames"]], [("main", "frames/main"), ("dialog", "frames/dialog")])
        for n in ("plan.json", "plan.md", "visual-requirements.canonical.json", "visual-requirements.pixso.json"):
            self.assertFalse((self.tdir / n).exists(), f"{n} уехал из корня трека")
            self.assertTrue((self.tdir / "frames" / "main" / n).is_file(), n)
        self.assertEqual(((self.tdir / "frames/main/plan.json").read_bytes(), (self.tdir / "frames/main/visual-requirements.canonical.json").read_bytes()), before,
                         "переезд — переименование: байты те же")
        self.assertTrue((self.tdir / "frames/dialog/visual-requirements.pixso.json").is_file())
        self.assertEqual(TR.requirement_status(self.root, m, "main"), "ok", "требование main находится по новому месту, хеш сходится")
        self.assertEqual(TR.requirement_status(self.root, m, "dialog"), "ok")

    def test_track_own_files_and_foreign_files_stay_put_byte_for_byte(self):
        own = self.own_files()
        self.pull()
        for n, data in own.items():
            if data is None:
                continue
            self.assertEqual((self.tdir / n).read_bytes(), data, f"{n} остался на месте и не изменился")
        self.assertFalse((self.tdir / "frames" / "main" / "notes.md").exists())
        self.assertTrue((self.tdir / "track.yaml").is_file())

    def test_paths_in_state_and_iteration_artifacts_are_rewritten_but_signed_files_are_not(self):
        it = self.fake_iteration(1, defects=[defect("a")])
        old = f".daiver/tracks/{self.TB.TRACK}/verify/iter-1"
        (it / "note.json").write_text(json.dumps({"iterationDir": old, "other": f".daiver/tracks/OTHER/verify/iter-1", "keep": f".daiver/tracks/{self.TB.TRACK}/frames/x/verify",
                                                  "prose": f"см. {old}/actual.png и {old}"}), encoding="utf-8")
        self.rec(1)
        state_before = self.state()
        self.assertEqual(state_before["history"][0]["dir"], old)
        plan_bytes = (self.tdir / "plan.json").read_bytes() + f' "{old}"'.encode()
        (self.tdir / "plan.json").write_bytes(plan_bytes)
        report = self.tdir / "resolution-report.md"
        report.write_text(f"отчёт: {old}\n", encoding="utf-8")
        report_bytes = report.read_bytes()
        r = self.pull()
        new = f".daiver/tracks/{self.TB.TRACK}/frames/main/verify/iter-1"
        st = json.loads((self.tdir / "frames/main/verify-state.json").read_text(encoding="utf-8"))
        self.assertEqual(st["history"][0]["dir"], new, "history[].dir переписан")
        note = json.loads((self.tdir / "frames/main/verify/iter-1/note.json").read_text(encoding="utf-8"))
        self.assertEqual(note["iterationDir"], new)
        self.assertEqual(note["prose"], f"см. {new}/actual.png и {new}", "и в середине строки, и на конце")
        self.assertEqual(note["other"], ".daiver/tracks/OTHER/verify/iter-1", "чужой трек не трогаем")
        self.assertEqual(note["keep"], f".daiver/tracks/{self.TB.TRACK}/frames/x/verify", "уже новый путь не переписывается второй раз")
        self.assertEqual((self.tdir / "frames/main/plan.json").read_bytes(), plan_bytes, "plan.json под подписью — не переписывается")
        self.assertEqual(report.read_bytes(), report_bytes, "resolution-report.md под подписью — не переписывается")
        self.assertIn("verify-state.json", " ".join(r["layout"]["rewritten"]))
        self.assertNotIn("plan.json", " ".join(r["layout"]["rewritten"]))

    def test_the_track_report_is_rebuilt_from_the_moved_state(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        rp = self.tdir / "verify-report.md"
        self.assertTrue(rp.is_file())
        rp.write_text("устаревший текст со старыми путями", encoding="utf-8")
        self.pull()
        text = rp.read_text(encoding="utf-8")
        self.assertNotIn("устаревший", text)
        self.assertIn("frame: main", text)

    def test_nothing_moves_backwards_and_a_third_frame_does_not_migrate(self):
        self.pull()
        snap = self.tree()
        r = self.pull("third", URL3)
        self.assertNotIn("layout", r, "переезд — один раз, на втором кадре")
        m = TR.load(self.root, self.TB.TRACK)
        self.assertEqual([f["dir"] for f in m["frames"]], ["frames/main", "frames/dialog", "frames/third"])
        after = self.tree()
        for k, v in snap.items():
            if k.endswith("track.yaml"):
                continue
            self.assertEqual(after.get(k), v, f"{k} не тронут третьим кадром")
        again = self.pull("dialog", URL2)
        self.assertTrue(again["unchanged"])
        self.assertNotIn("layout", again)

    def test_two_frames_on_one_url_are_refused_before_anything_moves(self):
        snap = self.tree()
        with self.assertRaises(T.ToolError) as cm:
            self.pull("dialog", self.TB.URL)
        self.assertIn("ошибка именования", str(cm.exception))
        self.assertEqual(self.tree(), snap, "ни переезда, ни нового кадра")
        self.assertEqual([f["dir"] for f in TR.load(self.root, self.TB.TRACK)["frames"]], ["."])

    def test_a_failed_move_rolls_everything_back_and_the_manifest_keeps_the_first_frame(self):
        snap = self.tree()
        real = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError(13, "Permission denied")
            return real(src, dst)

        with mock.patch.object(LAY.os, "replace", flaky):
            with self.assertRaises(T.ToolError) as cm:
                self.pull()
        self.assertIn("возвращено на место", str(cm.exception))
        self.assertEqual([f["dir"] for f in TR.load(self.root, self.TB.TRACK)["frames"]], ["."], "манифест не потерял первый кадр")
        self.assertFalse((self.tdir / "frames").exists(), "пустая цель и файлы нового кадра убраны")
        self.assertEqual(self.tree(), snap)

    def test_an_occupied_target_refuses_without_moving_a_single_file(self):
        (self.tdir / "frames" / "main").mkdir(parents=True)
        (self.tdir / "frames" / "main" / "plan.json").write_text("чужой", encoding="utf-8")
        snap = self.tree()
        with self.assertRaises(T.ToolError) as cm:
            self.pull()
        self.assertIn("уже содержит", str(cm.exception))
        self.assertEqual(self.tree(), snap, "ни файла нового кадра, ни переезда")

    # --- старые пути ---
    def test_the_old_path_resolves_to_the_new_place_only_when_nothing_is_there(self):
        it = self.fake_iteration(1)
        (it / "actual.png").write_bytes(K.png_bytes(10, 10))
        self.pull()
        old = self.tdir / "verify" / "iter-1" / "actual.png"
        self.assertFalse(old.exists())
        new = self.tdir / "frames/main/verify/iter-1/actual.png"
        self.assertEqual(LAY.redirect(self.root, old).resolve(), new.resolve())
        self.assertEqual(LAY.redirect(self.root, new), new, "новый путь — как есть")
        other = self.root / "src" / "verify" / "x.png"
        self.assertEqual(LAY.redirect(self.root, other), other, "не трек — как есть")
        ghost = self.tdir / "verify" / "iter-9" / "actual.png"
        self.assertEqual(LAY.redirect(self.root, ghost), ghost, "нет и нового места — путь остаётся, ошибку скажет вызывающий")
        own = self.tdir / "run.json"
        self.assertEqual(LAY.redirect(self.root, own), own)
        (self.tdir / "frames/main/verify-decisions.json").write_text("{}", encoding="utf-8")        # файл решений КАДРА — не подмена файла решений трека
        track_level = self.tdir / "verify-decisions.json"
        self.assertEqual(LAY.redirect(self.root, track_level), track_level, "собственные имена трека не переадресуются в кадр")
        (self.tdir / "verify").mkdir()
        (self.tdir / "verify" / "iter-1").mkdir()
        (self.tdir / "verify" / "iter-1" / "actual.png").write_bytes(b"x")
        self.assertEqual(LAY.redirect(self.root, old), old, "если по старому пути что-то лежит, его не подменяют")

    def test_a_tool_called_with_the_old_iteration_dir_still_works(self):
        it = self.fake_iteration(1, defects=[defect("a")])
        (it / "actual.png").write_bytes(K.png_bytes(20, 10))
        self.pull()
        r = T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "iterationDir": f".daiver/tracks/{self.TB.TRACK}/verify/iter-1"})
        self.assertTrue(r["rendered"])
        self.assertEqual(r["report"], f".daiver/tracks/{self.TB.TRACK}/frames/main/verify/iter-1/report.html")

    def test_redirect_is_a_noop_for_a_single_frame_track(self):
        p = self.tdir / "verify" / "iter-1" / "actual.png"
        self.assertEqual(LAY.redirect(self.root, p), p)

    # --- статус ---
    def test_status_counts_the_stage_by_the_first_unclean_frame(self):
        self.pull()
        tr = T.call("daiver_status", self.root, {})["tracks"][0]
        self.assertEqual([(f["name"], f["dir"]) for f in tr["frames"]], [("main", "frames/main"), ("dialog", "frames/dialog")])
        self.assertEqual((tr["stage"], tr["frame"]), (tr["frames"][0]["stage"], "main"))
        self.assertIn("dialog", tr["nextStep"], "остальные кадры названы в «Далее по треку»")
        self.assertNotEqual(tr["frames"][1]["stage"], tr["frames"][0]["stage"])


class Module(World):
    """План не-первичного кадра: решение о месте корня в едином модуле."""

    def setUp(self):
        super().setUp()
        self.td = self.root / ".daiver" / "tracks" / self.TB.TRACK
        (self.root / "d.json").write_text(json.dumps(synth(1)), encoding="utf-8")
        T.call("daiver_pixso_pull", self.root, {"track": self.TB.TRACK, "dslPath": "d.json", "frameUrl": URL2, "frame": "dialog"})
        self.canon_for("dialog")

    def canon_for(self, name, role=None, state_of=None):
        m = TR.load(self.root, self.TB.TRACK)
        paths = TR.frame_paths(self.root, m, name)
        f = TR.get_frame(m, name)
        paths["requirement"].write_text(json.dumps({"dsl": name}), encoding="utf-8")
        f["requirement"]["sha256"] = TR.sha256_file(paths["requirement"])
        if state_of:
            f["role"], f["stateOf"] = "state", state_of
        node = self.TB.frame("Dlg", [self.TB.leaf("d1", name="d1", text="Диалог")], name=self.TB.autoname("Dlg"), layout={"direction": "column", "gap": 8})
        C.write_canonical(paths["canonical"], C.new_envelope(node, "pixso", {"sourceSha256": f["requirement"]["sha256"]}))
        TR.save(self.root, m)

    def write(self, frame, plan=None):
        T.call("daiver_resolve_report", self.root, {"track": self.TB.TRACK})
        return T.call("daiver_plan_write", self.root, {"track": self.TB.TRACK, "frame": frame, "plan": plan or {}, "weightBudget": self.TB.BUDGET})

    def plan_of(self, frame):
        return json.loads((self.td / "frames" / frame / "plan.json").read_text(encoding="utf-8"))

    def test_a_non_primary_frame_defaults_to_its_own_component_in_the_primary_module(self):
        self.write("dialog")
        m = self.plan_of("dialog")["module"]
        self.assertEqual(m, {"name": "main", "root": {"mode": "own-file", "component": "Dialog"}})
        md = (self.td / "frames/dialog/plan.md").read_text(encoding="utf-8")
        self.assertIn("## Модуль", md)
        self.assertIn("ОДИН модуль с первичным кадром «main»", md)
        self.assertIn("`Dialog`", md)

    def test_the_primary_plan_never_gets_a_module_and_keeps_its_bytes_through_the_move(self):
        self.assertNotIn("module", self.plan_of("main"))
        again = self.write("main")
        self.assertFalse(again["written"], "переезд main не изменил план: подпись плана не слетает")
        self.assertNotIn("module", self.plan_of("main"))
        self.assertNotIn("Модуль", (self.td / "frames/main/plan.md").read_text(encoding="utf-8"))

    def test_an_explicit_placement_is_recorded_validated_and_survives_a_re_check(self):
        self.write("dialog", {"placement": {"mode": "inside-primary", "host": "t1", "component": "EditDialog", "reason": "  открывается   поверх экрана  "}})
        m = self.plan_of("dialog")["module"]
        self.assertEqual(m["root"], {"mode": "inside-primary", "component": "EditDialog", "host": "t1", "reason": "открывается поверх экрана"})
        st = T.call("daiver_plan_contract", self.root, {"track": self.TB.TRACK})
        stale = [o for o in st["open"] if o["id"] == "plan" and o.get("frame") == "dialog"]
        self.assertEqual(stale, [], "план с решением о месте проходит проверку «равен тому, что строит движок»")
        self.assertIn("EditDialog", (self.td / "frames/dialog/plan.md").read_text(encoding="utf-8"))

    def refuse(self, placement, needle):
        with self.assertRaises(T.ToolError) as cm:
            self.write("dialog", {"placement": placement})
        self.assertIn(needle, str(cm.exception))
        self.assertFalse((self.td / "frames/dialog/plan.json").exists(), "отказ ничего не записал")

    def test_bad_placements_are_refused_with_all_reasons(self):
        self.refuse({"mode": "somewhere"}, "plan.placement.mode")
        self.refuse({"mode": "inside-primary"}, "plan.placement.host")
        self.refuse({"mode": "inside-primary", "host": "нет-такого"}, "нет в каноне первичного кадра")
        self.refuse({"mode": "own-file", "host": "t1"}, "host бывает только у inside-primary")
        self.refuse({"mode": "own-file", "component": "dialog"}, "PascalCase")
        self.refuse({"mode": "own-file", "extra": 1}, "неизвестное поле")
        self.refuse({"mode": "own-file", "reason": "  "}, "plan.placement.reason")
        self.refuse("own-file", "ожидается объект")

    def test_placement_is_refused_for_the_primary_frame_and_for_a_state_frame(self):
        with self.assertRaises(T.ToolError) as cm:
            self.write("main", {"placement": {"mode": "own-file"}})
        self.assertIn("у первичного кадра корень и есть модуль", str(cm.exception))
        self.canon_for("dialog", state_of="main")
        with self.assertRaises(T.ToolError) as cm:
            self.write("dialog", {"placement": {"mode": "own-file"}})
        self.assertIn("stateOf", str(cm.exception))
        self.write("dialog")
        self.assertNotIn("module", self.plan_of("dialog"), "кадр-состояние строится внутри экрана: решения о корне у него нет (6.2)")

    def test_a_hand_edit_of_the_placement_is_seen_as_a_stale_plan(self):
        self.write("dialog")
        p = self.td / "frames/dialog/plan.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["module"]["root"]["component"] = "Другое"
        p.write_text(PL.dumps(data), encoding="utf-8")
        st = T.call("daiver_plan_contract", self.root, {"track": self.TB.TRACK})
        self.assertTrue([o for o in st["open"] if o["id"] == "plan" and o.get("frame") == "dialog"], st["open"])

    def test_the_digest_names_the_module_only_when_there_is_one(self):
        base = {"role": "screen", "open": False, "goalImage": False, "arts": [], "missingArts": []}
        plan = lambda **kw: {"stats": {"sections": 0, "buildable": 0, "candidates": 0, "component": 0, "local": 0, "ignore": 0, "sharedLocals": 0, "uncertain": 0},  # noqa: E731
                             "sections": [], "locals": [], "buildOrder": [], "design": {"tokens": [], "colors": []}, "specs": {}, **kw}
        one = [dict(base, name="main", plan=plan())]
        self.assertEqual(PD._module(one), [])
        two = one + [dict(base, name="dialog", plan=plan(module={"name": "main", "root": {"mode": "own-file", "component": "Dialog", "reason": "отдельный экран"}})),
                     dict(base, name="menu", plan=plan(module={"name": "main", "root": {"mode": "inside-primary", "component": "Menu", "host": "p2"}}))]
        text = "\n".join(PD._module(two))
        self.assertIn("ОДИН модуль — по первичному кадру «main»", text)
        self.assertIn("| dialog | отдельный компонент в каталоге модуля | Dialog | отдельный экран |", text)
        self.assertIn("| menu | внутри первичного кадра, узел `p2` | Menu | — |", text)


class Report(World):
    def test_the_frames_bar_links_to_the_latest_report_of_the_other_frame(self):
        self.TB.set_canon(self.root, self.TB.frame("Page", [self.TB.leaf("t1", name="t1", text="x")], name=self.TB.autoname("Page"), size={"w": 10, "h": 10}))
        (self.root / "d.json").write_text(json.dumps(synth(1)), encoding="utf-8")
        T.call("daiver_pixso_pull", self.root, {"track": self.TB.TRACK, "dslPath": "d.json", "frameUrl": URL2, "frame": "dialog"})
        m = TR.load(self.root, self.TB.TRACK)
        for name, iters in (("main", (1,)), ("dialog", (1, 2))):
            for n in iters:
                d = TR.frame_paths(self.root, m, name)["verify"] / f"iter-{n}"
                d.mkdir(parents=True, exist_ok=True)
                (d / "actual.png").write_bytes(K.png_bytes(10, 10))
                (d / "compare-summary.json").write_text(json.dumps({"status": "clean"}), encoding="utf-8")
        T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "frame": "main", "iterationDir": ".daiver/tracks/STOR-100/frames/main/verify/iter-1"})
        T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "frame": "dialog", "iterationDir": ".daiver/tracks/STOR-100/frames/dialog/verify/iter-1"})
        T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "frame": "dialog", "iterationDir": ".daiver/tracks/STOR-100/frames/dialog/verify/iter-2"})
        T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "frame": "main", "iterationDir": ".daiver/tracks/STOR-100/frames/main/verify/iter-1"})
        text = (self.root / ".daiver/tracks/STOR-100/frames/main/verify/iter-1/report.html").read_text(encoding="utf-8")
        self.assertIn('href="../../../dialog/verify/iter-2/report.html"', text, "последний отчёт соседа, ссылка относительная")
        self.assertNotIn("iter-1/report.html", text.split("<main>")[0], "старый отчёт соседа не ссылка")
        self.assertIn('<span class="chip static cur">main', text, "текущий кадр — не ссылка")
        dialog = (self.root / ".daiver/tracks/STOR-100/frames/dialog/verify/iter-2/report.html").read_text(encoding="utf-8")
        self.assertIn('href="../../../main/verify/iter-1/report.html"', dialog)
        self.assertNotIn("http", text.split("<main>")[0].replace("http-equiv", ""), "страница остаётся самодостаточной")


if __name__ == "__main__":
    unittest.main()
