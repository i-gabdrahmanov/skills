# -*- coding: utf-8 -*-
"""test_run_directive.py — директива и прогон: разбор строки, run.json, режим, startsAt, blocked с supply, закрытие (задача 7.1, спека §15.1–15.3,
§15.5, §20.3; INV-B22, C38, D2, D3, D5, D7, D10, E1–E3, E7, E8, G8; QG-S12, U11, P5 (часть)).

Пинит:
  • S12 — таблица валидных строк с ожидаемым разбором и КАЖДОЙ malformed-формы §15.2: нарушение названо дословно, ничего не пишется (ни
    превью, ни открытием); не директива — не директива; предупреждение — не нарушение;
  • режим (E1–E3): mode= строки > embeddedMode (директива) > workflow.mode (человек); мусор embeddedMode — дефолт с предупреждением;
    режим записан в run.json и читается назад статусом, verify-state и отчётом;
  • startsAt: track / resolve / build / verify / done по ступеням трека; from=plan и автономная сборка проверяют контракт до кода;
  • blocked с supply у каждой причины §20.3; track-conflict не перезаписывает (D7); blocked — тоже запись, её закрывает finish (G8);
  • закрытие (C38, D10) — исход из записанной истории verify: clean / defects-remaining / blocked / never-run, until= — never-run;
  • устаревшая запись (E8): прогон директивы открыт, начинает человек — закрыт и открыт заново; та же строка — тот же прогон;
  • ключи строки ложатся в манифест кодом, кадры без требования — pending до pull;
  • D3: init и запись карты в автономном прогоне отказывают, report.html не рендерится.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_build_state as TB  # noqa: E402
import test_plan_contract as TPC  # noqa: E402
from daiver import config as CFG  # noqa: E402
from daiver import directive as DR  # noqa: E402
from daiver import run as RUN  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver import tools as T  # noqa: E402

TRACK = TPC.TRACK
URL = TPC.URL
P = "forge-ui:"

VALID = [
    (f"{P} track=STOR-1", {"track": "STOR-1", "primary": {}, "frames": {}}),
    (f"daiver: track=STOR-1 frame=https://p.example/a?id=1 mode=autonomous",
     {"track": "STOR-1", "primary": {"frame": "https://p.example/a?id=1"}, "frames": {}, "mode": "autonomous"}),
    (f"{P} track=STOR-1 frame=https://p/a file=.daiver/inbox/a.json url=http://localhost:3000/x specs=a.md,b.md until=build",
     {"track": "STOR-1", "primary": {"frame": "https://p/a", "file": ".daiver/inbox/a.json"}, "frames": {}, "url": "http://localhost:3000/x",
      "specs": ["a.md", "b.md"], "until": "build"}),
    (f"{P} track=STOR-1 frame.err=https://p/e state.err=main url.err=http://x/e sources.err=src/A.tsx,src/B.tsx scope=change",
     {"track": "STOR-1", "primary": {}, "frames": {"err": {"frame": "https://p/e", "state": "main", "url": "http://x/e",
                                                             "sources": ["src/A.tsx", "src/B.tsx"]}}, "scope": "change"}),
    (f"{P} track=STOR-1 from=plan mode=interactive", {"track": "STOR-1", "primary": {}, "frames": {}, "from": "plan", "mode": "interactive"}),
    (f"{P} track=STOR-1 revision=https://p/v2", {"track": "STOR-1", "primary": {"revision": "https://p/v2"}, "frames": {}}),
    (f"{P} track=STOR-1 frame.main=https://p/a", {"track": "STOR-1", "primary": {"frame": "https://p/a"}, "frames": {}}),
]

MALFORMED = [
    (f"{P} track=STOR-1 color=red", "«color=red»: неизвестный ключ"),
    (f"{P} track=STOR-1 autonomous=true", "«autonomous=true»: неизвестный ключ"),
    (f"{P} track=STOR-1 mode = autonomous", "«mode = autonomous»: значения без пробелов"),
    (f"{P} track=STOR-1 mode =autonomous", "«mode =autonomous»: значения без пробелов"),
    (f"{P} track=STOR-1 mode= autonomous", "«mode= autonomous»: значения без пробелов"),
    (f"{P} track=STOR-1 mode=auto", "«mode=auto»: допустимы autonomous | interactive"),
    (f"{P} track=STOR-1 from=plan frame=https://p/a", "«from=plan» вместе с «frame=»"),
    (f"{P} track=STOR-1 from=plan file=a.json", "«from=plan» вместе с «file=»"),
    (f"{P} track=STOR-1 from=plan revision=https://p/v2", "«from=plan» вместе с «revision=»"),
    (f"{P} track=STOR-1 scope=all", "«scope=all»: допустимы change | page"),
    (f"{P} track=STOR-1 frame.x=https://p/x revision.x=https://p/y", "«revision.x» рядом с «frame.x» того же кадра"),
    (f"{P} track=STOR-1 frame.x=https://p/x state.x=x", "«state.x=x»: кадр не может быть состоянием самого себя"),
    (f"{P} track=STOR-1 state.main=x frame.x=https://p/x", "«state.main=x»: main — первичный кадр"),
    (f"{P} track=STOR-1 until=done", "«until=done»: допустимы resolve | build | verify"),
    (f"{P} track=STOR-1 from=scratch", "«from=scratch»: допустимо только from=plan"),
    (f"{P} track=STOR-1 frame=pixso-link", "«frame=pixso-link»: ожидается http(s)-адрес"),
    (f"{P} track=../x", "«track=../x»"),
    (f"{P} frame=https://p/a", "нет track=<KEY>"),
    (f"{P} track=STOR-1 track=STOR-2", "«track» повторяется"),
    (f"{P} track=STOR-1 sources=../etc/passwd", "«sources=../etc/passwd»"),
    (f"{P} track=STOR-1 devCommand=npm-run-dev", "«devCommand=npm-run-dev»: неизвестный ключ"),
    (f"{P} track=STOR-1 frame", "«frame»: не пара ключ=значение"),
]


class Parse(unittest.TestCase):
    def test_s12_valid_lines_parse_to_the_expected_keys(self):
        for line, keys in VALID:
            with self.subTest(line=line):
                got = DR.parse(line)
                self.assertTrue(got["ok"], got["faults"])
                self.assertEqual(got["keys"], keys)
                self.assertEqual(got["line"], line.strip())

    def test_s12_every_malformed_form_is_named_verbatim_and_never_repaired(self):
        for line, needle in MALFORMED:
            with self.subTest(line=line):
                got = DR.parse(line)
                self.assertTrue(got["directive"])
                self.assertFalse(got["ok"])
                self.assertTrue(any(needle in f for f in got["faults"]), got["faults"])

    def test_not_a_directive_is_not_one(self):
        for line in ("track=STOR-1 mode=autonomous", "сделай фронт по макету", "", None, " forge: track=S-1"):
            with self.subTest(line=line):
                self.assertFalse(DR.parse(line)["directive"])

    def test_a_state_of_an_unnamed_screen_is_a_warning_not_a_fault(self):
        got = DR.parse(f"{P} track=STOR-1 frame.err=https://p/e state.err=list")
        self.assertTrue(got["ok"])
        self.assertIn("state.err=list", got["warnings"][0])
        self.assertFalse(DR.parse(f"{P} track=STOR-1 frame=https://p/a frame.err=https://p/e state.err=main")["warnings"])

    def test_a_frame_key_without_a_requirement_anywhere_is_malformed_against_the_track(self):
        for line in (f"{P} track=STOR-1 url.err=http://x/e", f"{P} track=STOR-1 sources=src/A.tsx"):
            with self.subTest(line=line):
                got = DR.check_against(DR.parse(line), None)
                self.assertFalse(got["ok"])
                self.assertIn("нет требования ни в треке, ни в строке", got["faults"][0])
        m = {"track": "STOR-1", "frames": [{"name": "Orders", "requirement": {"sha256": "a"}}, {"name": "err", "requirement": {"sha256": "b"}}]}
        self.assertTrue(DR.check_against(DR.parse(f"{P} track=STOR-1 url.err=http://x/e sources=src/A.tsx"), m)["ok"])
        bad = DR.check_against(DR.parse(f"{P} track=STOR-1 state.Orders=err"), m)
        self.assertIn("первичный кадр трека", bad["faults"][0], "первичный под настоящим именем — тоже не состояние")
        self.assertEqual(DR.frames_of(DR.parse(f"{P} track=STOR-1 sources=src/A.tsx")["keys"], m), {"Orders": {"sources": ["src/A.tsx"]}})


# ── мир на диске ──────────────────────────────────────────────────────────────

class World(TPC.Base):
    def run_(self, **args):
        return self.call("daiver_run", **args)

    def directive(self, tail="", track=TRACK):
        return self.run_(directive=f"{P} track={track} {tail}".strip())

    @property
    def rec(self):
        return RUN.read(self.root, TRACK)

    def history(self, records, frame="main"):
        m = TR.load(self.root, TRACK)
        p = TR.frame_paths(self.root, m, frame)["verifyState"]
        p.write_text(json.dumps({"schema": 1, "history": records}), encoding="utf-8")

    def built(self):
        """Подписанный мир «main», все секции собраны."""
        self.signed_world()
        bs = self.call("daiver_build_state", track=TRACK)
        budget = bs["weightBudget"]
        while bs["next"] is not None:
            sid = bs["next"]["sectionId"]
            self.call("daiver_build_state_set", track=TRACK, sectionId=sid, status="in_progress")
            h = self.call("daiver_section", track=TRACK, sectionId=sid, weightBudget=budget)["section"]["hash"]
            rel = f"src/modules/main/{sid.replace(':', '_')}.tsx"
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("export default () => null;\n", encoding="utf-8")
            bs = self.call("daiver_build_state_set", track=TRACK, sectionId=sid, status="done", outputs=[rel], hash=h)


class Malformed(World):
    def test_nothing_is_written_by_the_preview_or_by_a_malformed_open(self):
        self.repo()
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        for line, _needle in MALFORMED[:6]:
            with self.subTest(line=line):
                self.assertFalse(self.call("daiver_directive", line=line)["ok"])
                got = self.run_(directive=line.replace("track=STOR-1", f"track={TRACK}"))
                self.assertTrue(got["malformed"])
                self.assertFalse(got["written"])
                self.assertIn("СТОП", got["nextStep"])
        self.assertEqual(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")), before)
        self.assertIsNone(self.rec)

    def test_the_preview_reads_the_track_for_frame_keys(self):
        self.repo()
        self.assertTrue(self.call("daiver_directive", line=f"{P} track={TRACK} url=http://x/y")["ok"])
        got = self.call("daiver_directive", line=f"{P} track={TRACK} url.err=http://x/e")
        self.assertFalse(got["ok"])
        self.assertIn("err", got["faults"][0])
        self.assertFalse(self.call("daiver_directive", line="сверстай экран")["directive"])

    def test_the_tool_refuses_blocked_without_finish_and_finish_without_track(self):
        self.repo()
        self.assertIn("finish", self.refusal("daiver_run", track=TRACK, blocked="plan-unsigned"))
        self.assertIn("track", self.refusal("daiver_run", finish=True))
        self.assertIn("не директива", self.refusal("daiver_run", directive="track=STOR-1"))


class Mode(World):
    def test_priority_line_then_embedded_then_workflow(self):
        self.repo()
        got = self.directive("mode=interactive")
        self.assertEqual((got["mode"], got["modeSource"], got["startedBy"]), ("interactive", "line", "directive"))
        got = self.directive()
        self.assertEqual((got["mode"], got["modeSource"]), ("autonomous", "workflow.embeddedMode"), "нет mode= — не interactive, а дефолт директивы")
        got = self.run_(track=TRACK)
        self.assertEqual((got["mode"], got["modeSource"], got["startedBy"]), ("interactive", "workflow.mode", "human"))

    def test_garbage_embedded_mode_is_the_default_with_a_warning(self):
        self.repo()
        cfg = self.root / CFG.CONFIG_REL
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("embeddedMode: autonomous", "embeddedMode: auto"), encoding="utf-8")
        got = self.directive()
        self.assertEqual(got["mode"], "autonomous")
        self.assertTrue(any("embeddedMode" in w for w in got["warnings"]), got["warnings"])

    def test_the_recorded_mode_is_read_back_by_status_verify_state_and_the_report(self):
        self.repo()
        self.directive()
        self.assertEqual(RUN.mode_of(self.root, TRACK), ("autonomous", "run.json"))
        run = self.call("daiver_status")["tracks"][0]["run"]
        self.assertEqual((run["mode"], run["startedBy"], run["open"]), ("autonomous", "directive", True))
        it = TR.frame_paths(self.root, TR.load(self.root, TRACK), "main")["verify"] / "iter-1"
        it.mkdir(parents=True)
        rep = self.call("daiver_verify_report", track=TRACK, iterationDir=it.relative_to(self.root).as_posix())
        self.assertFalse(rep["rendered"], "автономный прогон report.html не рендерит (INV-D3)")
        self.run_(track=TRACK, finish=True)
        self.assertEqual(RUN.mode_of(self.root, TRACK), ("interactive", "workflow.mode"), "закрытый прогон режим не держит")


class StartsAt(World):
    def test_a_line_with_a_design_the_track_lacks_starts_at_track_and_keeps_frame_keys_pending(self):
        init = TB.init_repo
        init(self.root)
        got = self.directive(f"frame={URL} url.err=http://x/e frame.err=https://pixso.example/e")
        self.assertEqual(got["startsAt"], "track")
        self.assertEqual(got["pull"], ["err", "main"])
        self.assertEqual(got["pending"], ["err"])
        self.assertIn("daiver_pixso_pull", got["nextStep"])

    def test_the_table_by_the_stage_of_the_track(self):
        self.repo()
        self.assertEqual(self.run_(track=TRACK)["startsAt"], "resolve", "требование и канон без плана")
        self.resolve_screen()
        self.report()
        self.write_plan()
        got = self.run_(track=TRACK)
        self.assertEqual(got["startsAt"], "build")
        self.assertEqual(got["policy"]["confirmPlan"], "pause", "человек подписывает сам")
        self.assertIn("не подписан", got["nextStep"])

    def test_built_starts_at_verify_and_clean_at_done(self):
        self.built()
        self.assertEqual(self.run_(track=TRACK)["startsAt"], "verify")
        self.history([{"iteration": 1, "clean": True, "blocking": []}])
        got = self.run_(track=TRACK)
        self.assertEqual(got["startsAt"], "done")
        self.assertIn("finish", got["nextStep"])

    def test_from_plan_checks_the_contract_before_any_code(self):
        self.repo()
        got = self.directive("from=plan")
        self.assertTrue(got["blocked"].startswith("plan-incomplete ("), got.get("blocked"))
        self.assertIn("plan", got["blocked"])
        self.assertIn("daiver_plan_contract", got["supply"])

    def test_autonomous_build_needs_the_signature_and_then_it_is_satisfied(self):
        self.ready_world()
        got = self.directive()
        self.assertEqual((got["startsAt"], got["blocked"]), ("build", "plan-unsigned"))
        self.assertIn(f"ui-plan-{TRACK}", got["supply"])
        self.assertEqual(self.sign().returncode, 0)
        got = self.directive()
        self.assertNotIn("blocked", got)
        self.assertEqual(got["policy"]["confirmPlan"], "satisfied-by-signature")
        self.assertEqual(got["policy"]["verify"], {"reportHtml": False, "decision": "triage", "stop": ["clean", "cap", "stall"],
                                                   "iterationCap": 3, "warnAfter": None})
        self.assertIn("ui-build", got["nextStep"])


class Blocked(World):
    def test_every_reason_of_the_spec_has_a_supply(self):
        spec = ("not-initialized", "no-requirement", "track-conflict", "no-sources", "no-implementation", "no-open-recipe",
                "implementation-unreachable", "library-source-unreachable", "plan-incomplete", "plan-unsigned", "no-verification-url",
                "no-dev-command")
        for r in spec:
            with self.subTest(reason=r):
                self.assertIn(r, RUN.BLOCKED)
                self.assertTrue(RUN.supply_of(r, TRACK).strip())
        self.assertIn("a, b", RUN.supply_of("plan-incomplete (a, b)", TRACK))

    def test_not_initialized_is_recorded_and_init_is_refused_while_the_autonomous_run_is_open(self):
        (self.root / "package.json").write_text("{}", encoding="utf-8")
        got = self.directive(f"frame={URL}")
        self.assertEqual(got["blocked"], "not-initialized")
        self.assertTrue(got["written"])
        refused = self.call("daiver_init", answers={"framework": "react"})
        self.assertFalse(refused["ok"])
        self.assertIn("INV-D3", refused["problems"][0])
        fin = self.run_(track=TRACK, finish=True)
        self.assertEqual(fin["outcome"], "blocked:not-initialized")
        self.assertFalse(fin["checked"])
        self.assertIn("инициализация", fin["note"])
        self.assertFalse(RUN.autonomous_open(self.root))

    def test_a_human_on_a_fresh_project_gets_init_and_nothing_is_written(self):
        (self.root / "package.json").write_text("{}", encoding="utf-8")
        got = self.run_(track=TRACK)
        self.assertEqual((got["startsAt"], got["written"]), ("init", False))
        self.assertFalse((self.root / ".daiver").exists())

    def test_no_requirement_without_frame_or_with_a_file_but_no_link(self):
        TB.init_repo(self.root)
        self.assertEqual(self.directive()["blocked"], "no-requirement")
        got = self.directive("file=.daiver/inbox/x.json")
        self.assertEqual(got["blocked"], "no-requirement")

    def test_track_conflict_never_overwrites(self):
        self.repo()
        before = TR.manifest_path(self.root, TRACK).read_bytes()
        got = self.directive("frame=https://pixso.example/other url=http://x/y")
        self.assertEqual(got["blocked"], "track-conflict")
        self.assertIn("pixso.example/other", got["conflicts"][0])
        self.assertEqual(TR.manifest_path(self.root, TRACK).read_bytes(), before, "конфликт — ни перезаписи, ни слияния ключей")
        self.assertIn("revision=", got["supply"])

    def test_a_different_export_of_the_same_frame_is_a_conflict_and_the_same_one_resumes(self):
        inbox = self.root / ".daiver" / "inbox"
        TB.init_repo(self.root)
        TB.write_ds(self.root, ["ModalHeader", "ModalAction"])
        inbox.mkdir(parents=True)
        raw = TPC.FIXTURE.read_text(encoding="utf-8")
        (inbox / "a.json").write_text(raw, encoding="utf-8")
        (inbox / "b.json").write_text(raw.replace("Текст9 слово - 20", "Текст9 слово - 21", 1), encoding="utf-8")
        self.call("daiver_pixso_pull", track=TRACK, dslPath=".daiver/inbox/a.json", frameUrl=URL, frame="main")
        self.assertNotIn("blocked", self.directive(f"frame={URL} file=.daiver/inbox/a.json"))
        got = self.directive(f"frame={URL} file=.daiver/inbox/b.json")
        self.assertEqual(got["blocked"], "track-conflict")
        got = self.directive(f"revision={URL} file=.daiver/inbox/b.json")
        self.assertNotIn("blocked", got)
        self.assertEqual(got["pull"], ["main"], "ревизия — pull, а не конфликт")

    def test_finish_validates_the_reason(self):
        self.repo()
        self.run_(track=TRACK)
        self.assertIn("не из списка", self.refusal("daiver_run", track=TRACK, finish=True, blocked="tired"))
        got = self.run_(track=TRACK, finish=True, blocked="no-open-recipe")
        self.assertEqual(got["outcome"], "blocked:no-open-recipe")
        self.assertIn("рецепт", got["supply"])


class Finish(World):
    def setUp(self):
        super().setUp()
        self.repo()

    def test_outcome_comes_from_the_recorded_history(self):
        self.run_(track=TRACK)
        self.assertEqual(self.run_(track=TRACK, finish=True)["outcome"], "never-run", "истории нет — экран не проверен")
        self.run_(track=TRACK)
        self.history([{"iteration": 1, "clean": False, "blocking": ["a", "b", "c"]}, {"iteration": 2, "clean": False, "blocking": ["a", "b"]}])
        got = self.run_(track=TRACK, finish=True)
        self.assertEqual((got["outcome"], got["remaining"], got["iterations"]), ("defects-remaining", 2, 2))
        self.assertFalse(got["checked"])
        self.run_(track=TRACK)
        self.history([{"iteration": 1, "clean": False, "blocking": ["a"]}, {"iteration": 2, "clean": True, "blocking": []}])
        got = self.run_(track=TRACK, finish=True)
        self.assertEqual(got["outcome"], "clean")
        self.assertTrue(got["checked"])
        self.assertIn("итераций проверки 2", got["note"])
        self.assertIn("отметить", got["nextStep"])

    def test_until_ends_with_never_run_honestly(self):
        self.history([{"iteration": 1, "clean": True, "blocking": []}])
        self.directive("until=build mode=interactive")
        got = self.run_(track=TRACK, finish=True)
        self.assertEqual(got["outcome"], "never-run")
        self.assertIn("until=", got["why"])

    def test_a_closed_run_is_not_recounted(self):
        self.run_(track=TRACK)
        first = self.run_(track=TRACK, finish=True)
        self.history([{"iteration": 1, "clean": True, "blocking": []}])
        again = self.run_(track=TRACK, finish=True)
        self.assertTrue(again["alreadyClosed"])
        self.assertEqual(again["outcome"], first["outcome"])
        self.assertIn("нет", self.refusal("daiver_run", track="STOR-999", finish=True))


class Records(World):
    def setUp(self):
        super().setUp()
        self.repo()

    def test_a_stale_directive_record_is_closed_when_a_human_starts(self):
        line = f"{P} track={TRACK} url=http://localhost:3000/a"
        self.run_(directive=line)
        n = self.rec["n"]
        again = self.run_(directive=line)
        self.assertTrue(again["resumed"])
        self.assertEqual(again["n"], n, "та же строка — тот же прогон")
        self.assertFalse(again["written"], "повтор без изменений ничего не пишет")
        human = self.run_(track=TRACK)
        self.assertEqual(human["closedPrevious"], {"n": n, "outcome": "never-run", "closedBy": "stale"})
        self.assertEqual((human["n"], human["mode"], human["previous"]["closedBy"]), (n + 1, "interactive", "stale"))
        other = self.run_(directive=f"{P} track={TRACK} url=http://localhost:3000/b")
        self.assertEqual(other["closedPrevious"]["closedBy"], "superseded")

    def test_the_record_has_no_clock_and_names_who_started_and_the_line(self):
        line = f"{P} track={TRACK} url=http://localhost:3000/a"
        self.run_(directive=line)
        rec = self.rec
        self.assertEqual((rec["directive"], rec["startedBy"], rec["modeSource"]), (line, "directive", "workflow.embeddedMode"))
        text = RUN.run_path(self.root, TRACK).read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\d{4}-\d\d-\d\dT", "времени в записи нет — один вход, один выход")

    def test_line_keys_land_in_the_manifest_and_missing_frames_stay_pending(self):
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src" / "A.tsx").write_text("x", encoding="utf-8")
        got = self.run_(directive=f"{P} track={TRACK} url=http://localhost:3000/a specs=docs/a.md sources=src/A.tsx scope=change "
                                  f"frame.err=https://pixso.example/e url.err=http://localhost:3000/e")
        self.assertEqual(sorted(got["applied"]), ["implementation", "main.scope", "main.sources", "specs"])
        self.assertEqual(got["pending"], ["err"])
        m = TR.load(self.root, TRACK)
        self.assertEqual(m["implementation"]["url"], "http://localhost:3000/a")
        self.assertEqual((TR.get_frame(m, "main")["sources"], TR.get_frame(m, "main")["scope"]), (["src/A.tsx"], "change"))
        TR.upsert_frame(m, "err", frameUrl="https://pixso.example/e")       # то, что сделал бы pull
        TR.save(self.root, m)
        got = self.run_(directive=f"{P} track={TRACK} url=http://localhost:3000/a specs=docs/a.md sources=src/A.tsx scope=change "
                                  f"frame.err=https://pixso.example/e url.err=http://localhost:3000/e")
        self.assertEqual(got["applied"], ["err.url"])
        self.assertEqual(TR.get_frame(TR.load(self.root, TRACK), "err")["url"], "http://localhost:3000/e")

    def test_a_key_the_manifest_refuses_is_malformed_and_nothing_changes(self):
        before = TR.manifest_path(self.root, TRACK).read_bytes()
        got = self.run_(directive=f"{P} track={TRACK} sources=src/Nope.tsx scope=change url=http://localhost:3000/a")
        self.assertFalse(got.get("malformed"), "файла может ещё не быть — sources проверяет контракт, не манифест")
        bad = self.run_(directive=f"{P} track={TRACK} state.main=main")
        self.assertTrue(bad["malformed"])
        self.assertNotEqual(before, b"")


class AutonomyBans(World):
    def setUp(self):
        super().setUp()
        self.repo()
        self.directive()

    def test_init_and_map_writes_are_refused_but_a_dry_run_is_not(self):
        self.assertFalse(self.call("daiver_init", answers={})["ok"])
        rule = {"match": {"figmaName": "Badge"}, "component": "Badge"}
        err = self.refusal("daiver_map_append", track=TRACK, rule=rule)
        self.assertIn("INV-D3", err)
        self.assertIn("dryRun", err)
        try:
            self.call("daiver_map_append", track=TRACK, rule=rule, dryRun=True)
        except T.ToolError as e:
            self.assertNotIn("INV-D3", str(e), "проверка правила без записи — не запрет автономии")

    def test_a_human_run_lifts_the_bans(self):
        self.run_(track=TRACK)
        self.assertIsNone(RUN.refuse_autonomous(self.root, "x"))


if __name__ == "__main__":
    unittest.main()
