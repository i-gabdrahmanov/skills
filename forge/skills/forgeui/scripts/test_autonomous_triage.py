# -*- coding: utf-8 -*-
"""test_autonomous_triage.py — автономный verify и резолв без человека (задача 7.2, спека §14.12, §15.4; INV-D8, D9, E4, E5, C31, F12;
QG-P5 (часть), N4, N5, N20).

Пинит:
  • запись итерации в автономии отказывает без триажа по КАЖДОМУ блокирующему дефекту и vision-пункту; skip/noise/fix без причины,
    неизвестный id, повтор, fix не-блокирующего (minor, noise) и fix vision без самоподтверждения — отказ одним сообщением;
  • решения человека (`verify-decisions.json`) соблюдаются первыми и не переворачиваются (skip → declined, false_positive → noise);
  • решение и останов выводит движок: fix_all / fix_some по fix-записям, стоп — clean, потолок итераций (`maxVerifyIterations`), застой
    (не лучше прошлой, регресс — тоже), «нечего чинить»; после стопа новая итерация не открывается; `daiver_verify_decide` в автономии
    отказывает; agent-noise не делает итерацию чистой;
  • verify-report.md: режим из записи прогона, блок «Триаж» с действием и причиной, кто закончил цикл;
  • N20 — vision-пункт не чинится автономно без измерения; N5 — low-вердикт компонента в автономии только local; подтверждение резолва
    не спрашивается (`unconfirmed`), гейт не ждёт согласия, но ловит прежний low-компонент; N4 — нет init, записи карты, report.html;
  • dev-сервер: автономный захват поднимает записанный devCommand и гасит его после; нет команды — `blocked: implementation-unreachable`.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_plan_contract as TPC  # noqa: E402
import test_verify_state as TVS  # noqa: E402
from daiver import resolution_report as RR  # noqa: E402
from daiver import run as RUN  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import capture as CP  # noqa: E402
from daiver.verify import devserver as DEV  # noqa: E402
from daiver.verify import state as VS  # noqa: E402
from daiver.verify import verify_report as VR  # noqa: E402
from test_implementation_url import free_port  # noqa: E402

defect = TVS.defect


class Auto(TVS.World):
    """Мир verify-state (4.8) с открытым автономным прогоном."""

    def setUp(self):
        super().setUp()
        RUN.open_run(self.root, directive=f"forge-ui: track={self.TB.TRACK} mode=autonomous")
        rec = RUN.read(self.root, self.TB.TRACK)                 # мир verify-state без подписанного плана: останов открытия снят, режим — тот же
        rec.pop("blocked", None)
        rec.pop("supply", None)
        RUN._write(self.root, self.TB.TRACK, rec)
        self.assertEqual(RUN.mode_of(self.root, self.TB.TRACK), ("autonomous", "run.json"))

    def triage(self, *entries):
        return [dict(zip(("id", "action", "reason"), e)) if isinstance(e, tuple) else e for e in entries]

    def refusal(self, n, **kw):
        with self.assertRaises(T.ToolError) as cm:
            self.rec(n, **kw)
        return str(cm.exception)


class TriageRequired(Auto):
    def test_a_record_without_triage_of_every_blocking_defect_is_refused(self):
        self.fake_iteration(1, "defects", [defect("a"), defect("b")])
        self.opened()
        err = self.refusal(1)
        self.assertIn("без полного триажа", err)
        self.assertIn("a, b", err)
        err = self.refusal(1, triage=self.triage(("a", "fix", "padding 16 из канона")))
        self.assertIn("нет триажа у 1: b", err)
        self.assertEqual(self.state()["history"], [], "ничего не записано")

    def test_every_entry_needs_a_reason_a_known_id_and_one_record(self):
        self.fake_iteration(1, "defects", [defect("a")])
        self.opened()
        for bad, needle in ((self.triage(("a", "skip", " ")), "reason обязателен"),
                            (self.triage(("zz", "fix", "x"), ("a", "fix", "x")), "дефект этой итерации"),
                            (self.triage(("a", "fix", "x"), ("a", "skip", "y")), "одна запись на дефект"),
                            (self.triage(("a", "later", "x")), "action — fix | skip | noise"),
                            ([{"id": "a", "action": "fix", "reason": "x", "why": "?"}], "неизвестные поля why")):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.refusal(1, triage=bad))

    def test_decision_fixed_and_declined_are_not_passed_in_autonomy_and_triage_is_not_passed_by_a_human(self):
        self.fake_iteration(1, "defects", [defect("a")])
        self.opened()
        self.assertIn("не передаются", self.refusal(1, decision="fix_all", triage=self.triage(("a", "fix", "x"))))
        RUN.open_run(self.root, track=self.TB.TRACK)
        self.assertIn("только в автономном", self.refusal(1, triage=self.triage(("a", "fix", "x"))))

    def test_minor_and_noise_are_never_fixed_to_close(self):
        self.fake_iteration(1, "defects", [defect("a"), defect("m", sev="minor"), defect("n", noise=True)])
        self.opened()
        for did in ("m", "n"):
            with self.subTest(defect=did):
                err = self.refusal(1, triage=self.triage(("a", "fix", "x"), (did, "fix", "закрыть")))
                self.assertIn("не чинится ради закрытия", err)
        got = self.rec(1, triage=self.triage(("a", "fix", "gap 8 из канона"), ("m", "skip", "deprecated, но рабочий")))
        self.assertEqual(got["decision"], "fix_all")


class Vision(Auto):
    def test_n20_a_vision_item_is_triaged_and_fixed_only_self_confirmed(self):
        self.fake_iteration(1, "clean", [])
        self.opened()
        vision = [{"id": "v1", "zone": "vision", "severity": "major", "code": "VISION", "message": "иконка мельче"}]
        self.assertIn("нет триажа у 1: v1", self.refusal(1, defects=vision))
        self.assertIn("самоподтверждённым", self.refusal(1, defects=vision, triage=self.triage(("v1", "fix", "на глаз мельче"))))
        got = self.rec(1, defects=vision, triage=self.triage(("v1", "skip", "по картинке не измерить: размер у канона тот же")))
        self.assertEqual((got["clean"], got["decision"], got["stop"]), (True, "clean", "clean"), "vision не блокирует clean")
        tri = self.state()["history"][0]["triage"]
        self.assertEqual(tri, [{"id": "v1", "action": "skip", "reason": "по картинке не измерить: размер у канона тот же", "by": "agent"}])

    def test_a_measured_vision_fix_is_accepted(self):
        self.fake_iteration(1, "clean", [])
        self.opened()
        vision = [{"id": "v1", "zone": "vision", "severity": "major", "code": "VISION", "message": "нет иллюстрации"}]
        got = self.rec(1, defects=vision, triage=[{"id": "v1", "action": "fix", "reason": "файл arts/hero.svg отсутствует", "confirmed": "missing-file"}])
        self.assertEqual(got["decision"], "clean")
        self.assertEqual(self.state()["history"][0]["triage"][0]["confirmed"], "missing-file")


class HumanDecisionsFirst(Auto):
    def test_human_skip_and_false_positive_are_honoured_and_never_overturned(self):
        self.fake_iteration(1, "defects", [defect("a"), defect("b"), defect("c")])
        self.write_decisions([{"id": "a", "verdict": "skip", "note": "так задумано"}, {"id": "b", "verdict": "false_positive"}])
        self.opened()
        self.assertIn("решение человека — skip", self.refusal(1, triage=self.triage(("a", "fix", "x"), ("c", "fix", "y"))))
        got = self.rec(1, triage=self.triage(("c", "fix", "gap 8 из канона")))
        tri = {e["id"]: (e["action"], e["by"]) for e in self.state()["history"][0]["triage"]}
        self.assertEqual(tri, {"a": ("skip", "human"), "b": ("noise", "human"), "c": ("fix", "agent")})
        self.assertEqual(got["fixed"], ["c"])
        self.assertEqual(got["decision"], "fix_some", "отклонённое человеком остаётся блокирующим, но не чинится")


class StopRules(Auto):
    def test_fix_then_stall_stops(self):
        self.fake_iteration(1, "defects", [defect("a"), defect("b")])
        self.opened()
        got = self.rec(1, triage=self.triage(("a", "fix", "gap 8"), ("b", "fix", "padding 16")))
        self.assertEqual((got["decision"], got["fixed"]), ("fix_all", ["a", "b"]))
        self.assertIn("ui-build", got["nextStep"])
        self.fake_iteration(2, "defects", [defect("a"), defect("b")])
        self.opened()
        got = self.rec(2, triage=self.triage(("a", "fix", "gap 8"), ("b", "fix", "padding 16")))
        self.assertEqual((got["decision"], got["stop"]), ("stop", "stalled"))
        self.assertIn("finish", got["nextStep"])
        with self.assertRaises(T.ToolError) as cm:
            self.opened()
        self.assertIn("закончен", str(cm.exception))

    def test_the_iteration_cap_stops_the_cycle(self):
        cap = RUN.read(self.root, self.TB.TRACK)["policy"]["verify"]["iterationCap"]
        self.assertEqual(cap, 3)
        for n, ids in ((1, ("a", "b", "c", "d")), (2, ("a", "b", "c")), (3, ("a", "b"))):
            self.fake_iteration(n, "defects", [defect(i) for i in ids])
            self.opened()
            got = self.rec(n, triage=self.triage(*[(i, "fix", "по канону") for i in ids]))
        self.assertEqual((got["decision"], got["stop"]), ("stop", "capReached"))

    def test_nothing_to_fix_stops_and_agent_noise_does_not_make_it_clean(self):
        self.fake_iteration(1, "defects", [defect("a")])
        self.opened()
        got = self.rec(1, triage=self.triage(("a", "noise", "артефакт сравнения: шрифт подгружен позже")))
        self.assertEqual((got["clean"], got["decision"], got["stop"]), (False, "stop", "nothing-to-fix"))
        self.assertEqual(got["blocking"], 1, "шум по слову агента не снимает блокирующий дефект")

    def test_a_clean_iteration_ends_with_clean(self):
        self.fake_iteration(1, "clean", [])
        self.opened()
        got = self.rec(1, triage=[])
        self.assertEqual((got["decision"], got["stop"]), ("clean", "clean"))
        fin = T.call("daiver_run", self.root, {"track": self.TB.TRACK, "finish": True})
        self.assertEqual(fin["outcome"], "clean")

    def test_decide_is_refused_in_autonomy(self):
        self.fake_iteration(1, "defects", [defect("a")])
        self.opened()
        self.rec(1, triage=self.triage(("a", "fix", "gap 8")))
        with self.assertRaises(T.ToolError) as cm:
            self.decide(1, "stop")
        self.assertIn("автономный прогон", str(cm.exception))

    def test_pure_decision_table(self):
        tri = [{"id": "a", "action": "fix"}, {"id": "b", "action": "skip"}]
        self.assertEqual(VS.autonomous_decision(1, False, {"stalled": False}, tri, ["a", "b"], 3)["decision"], "fix_some")
        self.assertEqual(VS.autonomous_decision(1, False, {"stalled": False}, tri[:1], ["a"], 3)["decision"], "fix_all")
        self.assertEqual(VS.autonomous_decision(3, False, {"stalled": False}, tri, ["a"], 3)["stop"], "capReached")
        self.assertEqual(VS.autonomous_decision(2, False, {"stalled": True}, tri, ["a"], 3)["stop"], "stalled")
        self.assertEqual(VS.autonomous_decision(5, True, {"stalled": True}, [], [], 3)["stop"], "clean", "clean важнее потолка")
        self.assertIsNone(VS.autonomous_decision(1, False, {}, tri, ["a"], None)["stop"], "потолка нет — только застой и clean")


class InteractiveHasNoCap(TVS.World):
    def test_the_iteration_cap_is_ignored_when_a_human_runs_the_loop(self):
        # INV-E5: maxVerifyIterations — стоп-правило только автономии; цикл человека заканчивает человек (INV-E6, F12)
        cap = VS._cap(self.root, self.TB.TRACK, VS.CFG.load(self.root)["config"])
        self.assertEqual(cap, 3)
        for n, ids in ((1, ("a", "b", "c", "d")), (2, ("a", "b", "c")), (3, ("a", "b")), (4, ("a",))):
            self.fake_iteration(n, "defects", [defect(i) for i in ids])
            self.assertEqual(self.opened()["iteration"], n)
            got = self.rec(n)
            self.assertNotIn("stop", got)
            self.decide(n, "fix_all", fixed=list(ids))
        self.assertEqual(self.opened()["iteration"], 5, "после потолка цикл человека открывает следующую итерацию")


class Report(Auto):
    def test_the_report_carries_the_mode_the_triage_and_who_finished(self):
        self.fake_iteration(1, "defects", [defect("a"), defect("b")])
        self.opened()
        self.rec(1, triage=self.triage(("a", "fix", "gap 8 из канона"), ("b", "skip", "шелл приложения, не модуль")))
        self.fake_iteration(2, "defects", [defect("b")])
        self.opened()
        self.rec(2, triage=self.triage(("b", "skip", "шелл приложения, не модуль")))
        text = VR.report_path(self.root, self.TB.TRACK).read_text(encoding="utf-8")
        self.assertIn("mode: autonomous", text)
        self.assertIn("### Триаж (итерация 1)", text)
        self.assertIn("- `a` **fix** (агент) — gap 8 из канона", text)
        self.assertIn("- `b` **skip** (агент) — шелл приложения, не модуль", text)
        self.assertIn("по триажу чинить нечего", text)

    def test_n4_no_report_html_no_init_no_map_write(self):
        it = self.fake_iteration(1, "clean", [])
        rep = T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "iterationDir": it.relative_to(self.root).as_posix()})
        self.assertFalse(rep["rendered"])
        self.assertFalse((it / "report.html").exists())
        self.assertFalse(T.call("daiver_init", self.root, {"answers": {}})["ok"])
        with self.assertRaises(T.ToolError):
            T.call("daiver_map_append", self.root, {"track": self.TB.TRACK, "rule": {"match": {"figmaName": "X"}, "component": "X"}})


# ── резолв без человека ───────────────────────────────────────────────────────

class Resolve(TPC.Base):
    def autonomous(self):
        RUN.open_run(self.root, directive=f"forge-ui: track={TPC.TRACK} mode=autonomous")

    def test_n5_low_component_is_refused_and_local_is_taken(self):
        self.repo()
        self.autonomous()
        with self.assertRaises(T.ToolError) as cm:
            self.fin(TPC.comp_verdict("h1", "ModalHeader", confidence="low"))
        self.assertIn("decision: local", str(cm.exception))
        self.fin(TPC.verdict("h1", "local", confidence="low", rationale="не уверен в компоненте — local"))
        self.fin(TPC.comp_verdict("a", "ModalAction", confidence="medium"))
        self.fin(TPC.verdict("Page", "local", rationale="оболочка экрана"))
        rep = self.report()
        self.assertTrue(rep["autonomous"])
        self.assertTrue(rep["unconfirmed"], "вопросы не задаются, а называются")
        self.assertIn("вопросов нет", rep["next"])
        self.assertEqual(RR.check(self.root, TPC.TRACK), [], "в автономии гейт не ждёт согласия человека")

    def test_an_earlier_low_component_without_consent_stops_the_gate(self):
        self.repo()
        self.fin(TPC.comp_verdict("h1", "ModalHeader", confidence="low"))
        self.fin(TPC.comp_verdict("a", "ModalAction"))
        self.fin(TPC.verdict("Page", "local", rationale="оболочка экрана"))
        self.report()
        self.autonomous()
        self.report()
        probs = RR.check(self.root, TPC.TRACK)
        self.assertEqual(len(probs), 1)
        self.assertIn("только local", probs[0])

    def test_interactive_still_needs_the_consent(self):
        self.repo()
        self.fin(TPC.comp_verdict("h1", "ModalHeader", confidence="medium"))
        self.fin(TPC.comp_verdict("a", "ModalAction"))
        self.fin(TPC.verdict("Page", "local", rationale="оболочка экрана"))
        rep = self.report()
        self.assertNotIn("autonomous", rep)
        self.assertTrue(any("не подтверждены" in p for p in RR.check(self.root, TPC.TRACK)))


# ── dev-сервер автономного захвата ────────────────────────────────────────────

class DevServer(TPC.Base):
    def setUp(self):
        super().setUp()
        self.repo()
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.it = TR.frame_paths(self.root, TR.load(self.root, TPC.TRACK), "main")["verify"] / "iter-1"
        RUN.open_run(self.root, directive=f"forge-ui: track={TPC.TRACK} mode=autonomous")

    def shot(self):
        return CP.run(self.root, TPC.TRACK, None, self.url, self.it, 5000)

    def test_no_dev_command_is_a_named_stop(self):
        got = self.shot()
        self.assertEqual(got["blocked"], "implementation-unreachable")
        self.assertIn("devCommand", got["supply"])

    def test_the_recorded_command_is_started_and_always_stopped(self):
        cmd = f'"{sys.executable}" -m http.server {self.port} --bind 127.0.0.1'
        self.call("daiver_track_manifest", track=TPC.TRACK, set={"implementation": {"url": self.url, "devCommand": cmd}})
        seen = {}
        real = CP.CD.launch_or_skip

        def probe(*a, **kw):
            seen["up"] = DEV.reachable(self.url)
            return None, {"skipped": True, "reason": "браузер в тесте не нужен"}
        with mock.patch.object(CP.CD, "launch_or_skip", probe):
            got = self.shot()
        self.assertTrue(seen["up"], "сервер поднят до захвата")
        self.assertEqual(got, {"skipped": True, "reason": "браузер в тесте не нужен"})
        self.assertFalse(DEV.reachable(self.url), "после захвата сервер погашен")
        self.assertIs(CP.CD.launch_or_skip, real)

    def test_a_human_run_never_starts_the_server(self):
        RUN.open_run(self.root, track=TPC.TRACK)
        with mock.patch.object(CP.CD, "launch_or_skip", lambda *a, **kw: (None, {"skipped": True})), \
                mock.patch.object(DEV, "start", side_effect=AssertionError("interactive стартовал сервер")):
            self.assertEqual(self.shot(), {"skipped": True})


if __name__ == "__main__":
    unittest.main()
