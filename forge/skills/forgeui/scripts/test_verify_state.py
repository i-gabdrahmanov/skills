"""test_verify_state.py — цикл verify: состояние, запись, решения, verify-report.md, гейт шага (задача 4.8, спека §14.1–14.2, §14.9, §14.11, §14.13,
INV-C21, C24, C25, C33, C34, C35, F12, F15, QG-U14, P6).

Пинит: нумерацию итераций и `unrecordedIterations` (повтор до записи номер не двигает); запись — ровно пять гейтов, факты движка вместо слов агента
(статусы compare/code, дефекты из артефактов итерации, измеренное выигрывает у кода), проценты 0–100; матрицу правила clean (noise, vision, pre-existing,
регрессия, hard gate, failed-гейты, skipped e2e/review, code fail / skip, compare не исполнялся); progress и stall; решения человека
(`verify-decisions.json`: схема, заморозка снимка → REOPENED, skip → declined, false_positive → noise, reviewer, пустая заметка, неизвестные id, кадр против трека,
позже `decidedAt`); `daiver_verify_decide`; формат verify-report.md (frontmatter, секции кадров, не исполнявшиеся гейты, нерешённое с пометками, кто закончил,
непроверенные кадры, детерминизм); гейт `ui_gate.py verify` (clean / stop с согласием / без согласия — нет). Итерации строятся на настоящем треке.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import decisions as DEC  # noqa: E402
from daiver.verify import state as ST  # noqa: E402
from daiver.verify import verify_gate as VG  # noqa: E402
from daiver.verify import verify_report as VR  # noqa: E402
from test_verify_compare import snapshot  # noqa: E402

HERE = Path(__file__).resolve().parent
GATE = HERE / "ui_gate.py"
RECORD_APPROVAL = HERE.parents[1] / "skills" / "pipeline-state" / "scripts" / "record_approval.py"
if not RECORD_APPROVAL.exists():
    RECORD_APPROVAL = HERE.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"
SKIPPED = {"visualDiff": {"status": "skipped", "reason": "гейт не реализован"}, "review": "skipped", "e2e": "skipped"}


def defect(i, sev="major", **kw):
    d = {"id": i, "code": "SIZE", "layer": "geometry", "severity": sev, "zone": "deterministic", "element": f"e-{i}", "message": f"m-{i}",
         "coverage": 1, "rows": [{"МАКЕТ": "10", "РЕАЛИЗАЦИЯ": "20", "count": 1}], "noise": False, "lifecycle": "new"}
    d.update(kw)
    return d


class World(unittest.TestCase):
    def setUp(self):
        import test_build_state as TB
        self.TB = TB
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        node = TB.frame("Page", [TB.leaf("t1", name="t1", text="Заголовок"), TB.leaf("t2", name="t2", text="Описание")],
                        name=TB.autoname("Page"), layout={"direction": "column", "gap": 8})
        TB.make_world(self.root, node)
        self.track_dir = self.root / ".daiver" / "tracks" / TB.TRACK

    @property
    def verify(self):
        """Папка итераций первичного кадра — всегда через манифест (раскладка меняется, когда кадров становится два)."""
        return TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), None)["verify"]

    def tearDown(self):
        self.tmp.cleanup()

    # --- помощники ---
    def opened(self, **kw):
        return T.call("daiver_verify_state", self.root, dict({"track": self.TB.TRACK}, **kw))

    def fake_iteration(self, n, compare="clean", defects=(), code=None, code_defects=(), match=1.0):
        """Артефакты итерации, как их оставляют daiver_verify_compare / daiver_code_compare."""
        d = self.verify / f"iter-{n}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "snapshot.json").write_text("{}", encoding="utf-8")
        if compare is not None:
            (d / "compare-summary.json").write_text(json.dumps({"status": compare, "matchRate": match, "fidelity": 1.0}), encoding="utf-8")
            (d / "defects.json").write_text(json.dumps({"defects": list(defects)}), encoding="utf-8")
        if code is not None:
            (d / "code-summary.json").write_text(json.dumps({"status": code, **({"reason": "нет build-state"} if code == "skipped" else {})}), encoding="utf-8")
            (d / "code-defects.json").write_text(json.dumps({"defects": list(code_defects)}), encoding="utf-8")
        return d

    def rec(self, n, gates=None, **kw):
        g = dict({"compare": "pass", "code": "skipped"}, **SKIPPED)
        g.update(gates or {})
        return T.call("daiver_verify_state_record", self.root, dict({"track": self.TB.TRACK, "iteration": n, "gates": g}, **kw))

    def decide(self, n, decision, **kw):
        return T.call("daiver_verify_decide", self.root, dict({"track": self.TB.TRACK, "iteration": n, "decision": decision}, **kw))

    def state_path(self):
        return TR.frame_paths(self.root, TR.load(self.root, self.TB.TRACK), None)["verifyState"]

    def state(self):
        p = self.state_path()
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"history": []}

    def write_decisions(self, items, where="track", **top):
        path = (self.track_dir if where == "track" else self.verify.parent) / DEC.FILE
        path.write_text(json.dumps(dict({"schema": 1, "decisions": items}, **top)), encoding="utf-8")
        return path

    def approve(self, key, quote="да, останавливаем"):
        cmd = [sys.executable, "-X", "utf8", str(RECORD_APPROVAL), "--project", str(self.root), "--key", key, "--approved-by", "user",
               "--reason", "остановка цикла", "--evidence", quote]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)


class Numbering(World):
    def test_first_iteration_and_idempotent_repeat(self):
        a = self.opened()
        self.assertEqual((a["iteration"], a["unrecordedIterations"], a["priorReview"], a["history"]), (1, [], None, []))
        self.assertTrue(a["iterationDir"].endswith("verify/iter-1"))
        self.assertTrue((self.root / a["iterationDir"]).is_dir())
        marker = self.root / a["iterationDir"] / "keep.txt"
        marker.write_text("x", encoding="utf-8")
        b = self.opened()
        self.assertEqual((b["iteration"], b["iterationDir"]), (1, a["iterationDir"]), "повтор до записи номер не двигает")
        self.assertTrue(marker.is_file(), "папка не пересоздаётся")
        self.assertFalse((self.verify / "iter-2").exists())

    def test_first_iteration_asks_for_the_goal_image_and_the_url(self):
        a = self.opened()
        self.assertIsNone(a["goalImage"])
        self.assertTrue(any("goal image" in w for w in a["warnings"]))
        self.assertIsNone(a["implementation"]["url"])
        self.assertIn("адреса страницы", " ".join(a["implementation"]["need"]))
        self.assertEqual(len(a["implementation"]["ask"]), 1, "человека спрашивают ОДИН раз и об одном")
        self.assertEqual((a["mode"], a["maxVerifyIterations"]), ("interactive", 3))

    def test_executed_but_unrecorded_is_named_and_the_number_does_not_move(self):
        self.opened()
        self.fake_iteration(1)
        a = self.opened()
        self.assertEqual((a["iteration"], a["unrecordedIterations"]), (1, [1]))
        self.assertIn("НЕ записана", a["nextStep"])
        self.assertFalse((self.verify / "iter-2").exists(), "новая папка до записи не заводится")
        self.rec(1)
        b = self.opened()
        self.assertEqual((b["iteration"], b["unrecordedIterations"]), (2, []))
        self.assertTrue((self.verify / "iter-2").is_dir())

    def test_several_unrecorded_are_all_listed_lowest_first(self):
        self.fake_iteration(1)
        self.fake_iteration(2)
        a = self.opened()
        self.assertEqual((a["iteration"], a["unrecordedIterations"]), (1, [1, 2]))

    def test_history_prior_review_and_warn_stalling(self):
        for n in (1, 2, 3):
            self.fake_iteration(n, compare="defects", defects=[defect("a")])
            self.rec(n, reviewReport={"fixPlan": [f"план {n}"]})
        a = self.opened()
        self.assertEqual(a["iteration"], 4)
        self.assertEqual(a["priorReview"], {"fixPlan": ["план 3"]})
        self.assertEqual([h["iteration"] for h in a["history"]], [1, 2, 3])
        self.assertEqual(a["history"][0]["blocking"], 1)
        self.assertEqual(a["warnStalling"]["after"], 3)
        self.assertIn("спроси человека", a["warnStalling"]["text"])

    def test_no_warning_when_the_last_iteration_is_clean(self):
        for n in (1, 2, 3):
            self.fake_iteration(n)
            self.rec(n)
        self.assertIsNone(self.opened()["warnStalling"])

    def test_missing_art_comes_from_the_canon(self):
        self.assertEqual(self.opened()["missingArt"], [])

    def test_errors(self):
        with self.assertRaises(T.ToolError):
            T.call("daiver_verify_state", self.root, {"track": "NOPE-1"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_verify_state", self.root, {})
        self.state_path().write_text("не json", encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            self.opened()
        self.assertIn("не читается", str(cm.exception))


class RecordValidation(World):
    def test_exactly_five_gates(self):
        self.fake_iteration(1)
        for bad in ({"compare": "pass"}, {**{"compare": "pass", "code": "pass", "visualDiff": "pass", "review": "pass", "e2e": "pass"}, "structural": "pass"}):
            with self.assertRaises(T.ToolError) as cm:
                T.call("daiver_verify_state_record", self.root, {"track": self.TB.TRACK, "iteration": 1, "gates": bad})
            self.assertIn("ровно пять", str(cm.exception))
        with self.assertRaises(T.ToolError) as cm:
            self.rec(1, gates={"e2e": "maybe"})
        self.assertIn("gates.e2e", str(cm.exception))

    def test_diff_percent_is_a_percent(self):
        d = self.fake_iteration(1)
        for bad in (-1, 101, "3.18"):
            with self.assertRaises(T.ToolError):
                self.rec(1, diffPercent=bad)
        self.assertEqual(self.state()["history"], [])
        (d / "visual-diff.json").write_text(json.dumps({"diffPercent": 3.18}), encoding="utf-8")
        self.rec(1, diffPercent=3.18)
        self.assertEqual(self.state()["history"][0]["diffPercent"], 3.18)

    def test_the_diff_percent_comes_from_the_artifact_not_from_the_agent(self):
        d = self.fake_iteration(1)
        res = self.rec(1, diffPercent=12.5)
        self.assertEqual(self.state()["history"][0]["diffPercent"], None)
        self.assertTrue(any("visual-diff.json" in w for w in res["warnings"]))
        d2 = self.fake_iteration(2)
        (d2 / "visual-diff.json").write_text(json.dumps({"diffPercent": 3.18}), encoding="utf-8")
        res = self.rec(2, diffPercent=0.0318)
        self.assertEqual(self.state()["history"][1]["diffPercent"], 3.18)
        self.assertTrue(any("0.0318" in w for w in res["warnings"]))
        self.assertEqual(res["gates"]["visualDiff"], "pass", "артефакт есть — гейт pass, как бы его ни назвал агент")

    def test_iteration_must_have_been_executed_and_not_recorded_twice(self):
        with self.assertRaises(T.ToolError) as cm:
            self.rec(1)
        self.assertIn("нет следов", str(cm.exception))
        self.fake_iteration(1)
        self.rec(1)
        with self.assertRaises(T.ToolError) as cm:
            self.rec(1)
        self.assertIn("уже записана", str(cm.exception))
        self.fake_iteration(3)
        self.rec(3)
        self.fake_iteration(2)
        with self.assertRaises(T.ToolError) as cm:
            self.rec(2)
        self.assertIn("раньше последней", str(cm.exception))
        for bad in (0, -1, True, "1"):
            with self.assertRaises(T.ToolError):
                self.rec(bad)

    def test_decision_must_be_known(self):
        self.fake_iteration(1)
        with self.assertRaises(T.ToolError):
            self.rec(1, decision="whatever")

    def test_extra_defects_are_only_vision_or_e2e(self):
        self.fake_iteration(1)
        with self.assertRaises(T.ToolError) as cm:
            self.rec(1, defects=[defect("x")])
        self.assertIn("vision и e2e", str(cm.exception))
        with self.assertRaises(T.ToolError):
            self.rec(1, defects=[{"zone": "vision", "severity": "major"}])
        with self.assertRaises(T.ToolError):
            self.rec(1, defects=[defect("x", zone="vision", severity="huge")])
        with self.assertRaises(T.ToolError):
            self.rec(1, defects="не список")

    def test_the_agents_words_lose_to_the_engines_facts(self):
        self.fake_iteration(1, compare="hard-gate-fail", match=0.2)
        res = self.rec(1, gates={"compare": "pass", "code": "pass"})
        self.assertEqual((res["clean"], res["gates"]["compare"], res["gates"]["code"]), (False, "fail", "skipped"))
        self.assertEqual(len(res["warnings"]), 2)
        rec = self.state()["history"][0]
        self.assertEqual(rec["matchRate"], 0.2)
        self.assertIn("hard gate", rec["gates"]["compare"]["reason"])

    def test_defects_of_both_gates_are_taken_from_the_artifacts_measured_wins(self):
        self.fake_iteration(1, compare="defects", defects=[defect("same", "major"), defect("dom-only")], code="fail",
                            code_defects=[defect("same", "critical"), defect("code-only", "critical")])
        res = self.rec(1, gates={"compare": "pass", "code": "fail"})
        rec = self.state()["history"][0]
        got = {d["id"]: d["source"] for d in rec["defects"]}
        self.assertEqual(got, {"same": "dom", "dom-only": "dom", "code-only": "code"})
        self.assertEqual(sorted(res["blockingIds"]), ["code-only", "dom-only", "same"])
        self.assertEqual([d["severity"] for d in rec["defects"] if d["id"] == "same"], ["major"])


class CleanRule(World):
    """Матрица §14.9: каждая строка — отдельная итерация на настоящем треке."""

    def check(self, expect_clean, compare="defects", defects=(), code=None, code_defects=(), gates=None, extra=None, label=""):
        n = len(self.state()["history"]) + 1
        self.fake_iteration(n, compare=compare, defects=defects, code=code, code_defects=code_defects)
        kw = {"defects": extra} if extra else {}
        res = self.rec(n, gates=gates, **kw)
        self.assertEqual(res["clean"], expect_clean, f"{label}: {res}")
        return res

    def test_blocking_only_non_noise_critical_and_major(self):
        self.check(False, defects=[defect("a", "critical")], label="critical")
        self.check(False, defects=[defect("b", "major")], label="major")
        self.check(True, defects=[defect("c", "minor")], label="minor не блокирует")
        self.check(True, defects=[defect("d", "critical", noise=True)], label="noise не блокирует")

    def test_vision_never_blocks(self):
        self.check(True, compare="clean", extra=[{"id": "v1", "zone": "vision", "severity": "critical", "message": "глазом"}], label="vision critical")

    def test_pre_existing_does_not_block_but_regression_does(self):
        self.check(True, defects=[defect("p", "major", standing="pre-existing")], label="pre-existing")
        res = self.check(False, defects=[defect("r", "major", lifecycle="regressed")], label="регрессия")
        self.assertEqual(res["progress"]["regressed"], ["r"])

    def test_hard_gate_fail_is_never_clean_even_without_defects(self):
        self.check(False, compare="hard-gate-fail", defects=[], label="hard gate")

    def test_recapture_is_never_clean(self):
        self.check(False, compare="recapture", defects=[], label="recapture")

    def test_compare_not_run_is_never_clean(self):
        self.check(False, compare=None, label="compare не исполнялся")

    def test_any_other_failed_gate_is_not_clean_skipped_e2e_and_review_do_not_matter(self):
        self.check(False, compare="clean", gates={"e2e": "fail"}, label="e2e fail")
        self.check(True, compare="clean", gates={"review": "skipped", "e2e": "skipped"}, label="skipped не мешает")
        self.check(True, compare="clean", gates={"visualDiff": "degraded"}, label="degraded не fail")

    def test_diff_and_review_are_signals_a_fail_there_is_refused(self):
        for gate in ("visualDiff", "review"):
            self.fake_iteration(1, compare="clean")
            with self.assertRaises(T.ToolError) as cm:
                self.rec(1, gates={gate: "fail"})
            self.assertIn("сигнал, а не вердикт", str(cm.exception))

    def test_code_fail_blocks_code_skip_does_not(self):
        self.check(False, compare="clean", code="fail", code_defects=[defect("c1", "critical")], label="code fail")
        self.check(True, compare="clean", code="skipped", label="code skip")
        res = self.check(True, compare="clean", code="fail", code_defects=[defect("c2", "critical", noise=True)], label="code fail, но всё noise")
        self.assertEqual(res["gates"]["code"], "pass")

    def test_clean_decision_is_refused_on_a_dirty_iteration(self):
        self.fake_iteration(1, defects=[defect("a")])
        with self.assertRaises(T.ToolError) as cm:
            self.rec(1, decision="clean")
        self.assertIn("не чистая", str(cm.exception))
        self.assertEqual(self.state()["history"], [], "отказ ничего не записал")


class Progress(World):
    def test_first_iteration_is_not_stalled_a_better_one_is_not_stalled_not_better_is(self):
        self.fake_iteration(1, defects=[defect("a"), defect("b")])
        self.assertEqual(self.rec(1)["progress"], {"blocking": 2, "regressed": [], "previous": None, "delta": None, "stalled": False})
        self.fake_iteration(2, defects=[defect("a")])
        self.assertEqual((self.rec(2)["progress"]["stalled"], self.rec_progress(2)["delta"]), (False, -1))
        self.fake_iteration(3, defects=[defect("a")])
        self.assertTrue(self.rec(3)["progress"]["stalled"], "не лучше прошлой")
        self.fake_iteration(4, defects=[defect("a"), defect("b")])
        p = self.rec(4)["progress"]
        self.assertEqual((p["stalled"], p["delta"]), (True, 1))

    def rec_progress(self, n):
        return next(h for h in self.state()["history"] if h["iteration"] == n)["progress"]

    def test_regression_is_a_stall_even_when_the_count_dropped(self):
        self.fake_iteration(1, defects=[defect("a"), defect("b"), defect("c")])
        self.rec(1)
        self.fake_iteration(2, defects=[defect("a", lifecycle="regressed")])
        p = self.rec(2)["progress"]
        self.assertEqual((p["delta"], p["stalled"], p["regressed"]), (-2, True, ["a"]))

    def test_a_clean_iteration_after_dirty_one_is_not_stalled(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        self.fake_iteration(2, compare="clean")
        self.assertFalse(self.rec(2)["progress"]["stalled"])


class Decide(World):
    def test_stop_is_always_possible_clean_only_on_a_clean_iteration(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        with self.assertRaises(T.ToolError):
            self.decide(1, "clean")
        res = self.decide(1, "stop", declined=["a"])
        self.assertEqual((res["decision"], res["declined"], res["clean"]), ("stop", ["a"], False))
        self.fake_iteration(2, compare="clean")
        self.rec(2)
        self.assertEqual(self.decide(2, "clean")["decision"], "clean")

    def test_decision_is_written_once(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        self.decide(1, "fix_all", fixed=["a"])
        self.decide(1, "fix_all", fixed=["a"])
        with self.assertRaises(T.ToolError) as cm:
            self.decide(1, "stop")
        self.assertIn("решение уже записано", str(cm.exception))

    def test_unknown_iteration_or_defect_ids_are_named(self):
        with self.assertRaises(T.ToolError) as cm:
            self.decide(1, "stop")
        self.assertIn("не записана", str(cm.exception))
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        with self.assertRaises(T.ToolError) as cm:
            self.decide(1, "fix_some", fixed=["нет-такого"])
        self.assertIn("нет-такого", str(cm.exception))
        with self.assertRaises(T.ToolError):
            self.decide(1, "bogus")

    def test_decision_shows_up_in_history_and_declined_is_not_asked_again(self):
        self.fake_iteration(1, defects=[defect("a"), defect("b")])
        self.rec(1)
        self.decide(1, "fix_some", fixed=["a"], declined=["b"])
        a = self.opened()
        self.assertEqual(a["declinedSoFar"], ["b"])
        self.assertEqual(a["history"][0]["decision"], "fix_some")


class Decisions(World):
    def test_false_positive_becomes_noise_skip_becomes_declined_fix_keeps_the_note(self):
        self.fake_iteration(1, defects=[defect("a"), defect("b"), defect("c")])
        self.write_decisions([{"id": "a", "verdict": "false_positive", "reviewer": "Анна"}, {"id": "b", "verdict": "skip", "note": "так задумано"},
                              {"id": "c", "verdict": "fix", "note": "поправить дословно: отступ 16"}])
        res = self.rec(1)
        d = {x["id"]: x for x in self.state()["history"][0]["defects"]}
        self.assertTrue(d["a"]["noise"])
        self.assertEqual(d["a"]["decision"], {"verdict": "false_positive", "reviewer": "Анна"})
        self.assertTrue(d["b"]["declined"])
        self.assertEqual(d["c"]["decision"], {"verdict": "fix", "note": "поправить дословно: отступ 16"})
        self.assertEqual(sorted(res["blockingIds"]), ["b", "c"], "declined остаётся блокирующим: цикл заканчивает человек")
        self.assertEqual(res["decisions"], {"noise": ["a"], "declined": ["b"], "fix": ["c"]})

    def test_a_frozen_snapshot_reopens_when_the_values_moved_but_not_when_only_the_text_changed(self):
        base = defect("a")
        snap = DEC.snapshot_of(base)
        self.write_decisions([{"id": "a", "verdict": "false_positive", "snapshot": snap}])
        self.fake_iteration(1, defects=[dict(base, message="переформулировано")])
        res = self.rec(1)
        self.assertEqual(res["blockingIds"], [], "переформулировка сама дефект не переоткрывает")
        moved = defect("a", rows=[{"МАКЕТ": "10", "РЕАЛИЗАЦИЯ": "30", "count": 1}])
        self.fake_iteration(2, defects=[moved])
        res = self.rec(2)
        self.assertEqual((res["blockingIds"], res["decisions"]["reopened"]), (["a"], ["a"]))
        d = self.state()["history"][1]["defects"][0]
        self.assertEqual(d["decision"]["status"], "reopened")
        self.assertIn("уехали", d["decision"]["reopenedNote"])
        self.assertFalse(d["noise"])

    def test_unknown_ids_problems_and_foreign_frames_are_named_not_ignored(self):
        self.write_decisions([{"id": "нет", "verdict": "skip"}, {"id": "x"}, {"verdict": "fix"}, {"id": "e", "verdict": "fix", "frame": "other"}])
        self.fake_iteration(1, defects=[defect("a")])
        res = self.rec(1)
        self.assertEqual(res["decisions"]["unknown"], ["нет"])
        self.assertEqual(len(res["decisionProblems"]), 2)
        loaded = DEC.load(self.root, TR.load(self.root, self.TB.TRACK), None)
        self.assertEqual(loaded["elsewhere"], ["e"])

    def test_reviewer_carries_and_an_empty_note_does_not(self):
        d = defect("a")
        DEC.apply([d], {"decisions": {"a": {"id": "a", "verdict": "fix", "note": "  ", "reviewer": " Борис "}}})
        self.assertEqual(d["decision"], {"verdict": "fix", "reviewer": "Борис"})

    def test_a_foreign_schema_is_refused_loudly(self):
        self.write_decisions([], )
        (self.track_dir / DEC.FILE).write_text(json.dumps({"schema": 2, "decisions": []}), encoding="utf-8")
        self.fake_iteration(1)
        with self.assertRaises(T.ToolError) as cm:
            self.rec(1)
        self.assertIn("версия схемы", str(cm.exception))
        (self.track_dir / DEC.FILE).write_text("не json", encoding="utf-8")
        with self.assertRaises(T.ToolError):
            self.rec(1)
        a = self.opened()
        self.assertTrue(a["decisions"]["problems"], "state называет проблему, а не падает")

    def test_later_decided_at_wins_and_the_frame_file_beats_the_track_file_on_a_tie(self):
        m = TR.load(self.root, self.TB.TRACK)
        TR.upsert_frame(m, "second", frameUrl="https://pixso.example/b?item-id=1:1")
        TR.save(self.root, m)
        manifest = TR.load(self.root, self.TB.TRACK)
        self.write_decisions([{"id": "a", "verdict": "skip", "decidedAt": "2026-10-01T10:00:00Z"},
                              {"id": "b", "verdict": "skip", "decidedAt": "2026-10-01T10:00:00Z"},
                              {"id": "c", "verdict": "skip", "decidedAt": "2026-10-03T10:00:00Z"}])
        frame_file = TR.frame_paths(self.root, manifest, "second")["dir"] / DEC.FILE
        self.assertNotEqual(frame_file, self.track_dir / DEC.FILE)
        frame_file.parent.mkdir(parents=True, exist_ok=True)
        frame_file.write_text(json.dumps({"schema": 1, "decisions": [{"id": "a", "verdict": "fix", "decidedAt": "2026-10-02T10:00:00Z"},
                                                                   {"id": "b", "verdict": "fix", "decidedAt": "2026-10-01T10:00:00Z"},
                                                                   {"id": "c", "verdict": "fix", "decidedAt": "2026-10-02T10:00:00Z"}]}), encoding="utf-8")
        got = DEC.load(self.root, manifest, "second")
        self.assertEqual({k: v["verdict"] for k, v in got["decisions"].items()}, {"a": "fix", "b": "fix", "c": "skip"},
                         "a: кадр позже; b: ничья — кадр сильнее; c: файл трека позже")
        self.assertEqual(got["sources"], ["трека", "кадра"])

    def test_the_snapshot_helper_matches_what_apply_compares(self):
        d = defect("a")
        self.assertEqual(DEC.snapshot_of(d), {"rows": [{"МАКЕТ": "10", "РЕАЛИЗАЦИЯ": "20"}]})


class Report(World):
    def second_frame(self):
        m = TR.load(self.root, self.TB.TRACK)
        TR.upsert_frame(m, "second", frameUrl="https://pixso.example/b?item-id=1:1")
        TR.save(self.root, m)

    def text(self):
        return (self.track_dir / "verify-report.md").read_text(encoding="utf-8")

    def test_format_of_a_finished_cycle(self):
        self.fake_iteration(1, compare="defects", defects=[defect("a", "major", location={"file": "src/A.tsx", "line": 12}), defect("m", "minor"),
                                                           defect("n", "major", noise=True)], code="skipped", match=0.93)
        (self.verify / "iter-1" / "visual-diff.json").write_text(json.dumps({"diffPercent": 3.18}), encoding="utf-8")
        res = self.rec(1, diffPercent=3.18, reviewSkipped="обзора нет")
        self.assertTrue(res["report"].endswith("verify-report.md"))
        self.decide(1, "stop", declined=["a"])
        t = self.text()
        head = t.split("---\n")[1]
        self.assertEqual(head, "track: STOR-100\noutcome: defects-remaining\niterations: 1\ndefectsRemaining: 1\nmode: interactive\n")
        for needle in ("# Проверка STOR-100", "## frame: main", "### Итерация 1", "сравнение: pass · код-гейт: skipped · пиксельный дифф: pass · визуальный обзор: skipped · e2e: skipped",
                       "matchRate 0.93", "diff 3.18%", "обзор пропущен: обзора нет", "блокирующих: 1", "решение: stop", "### Итог кадра main",
                       "не исполнялись или исполнены не полностью: код-гейт — skipped: нет build-state; визуальный обзор — skipped: обзора нет; e2e — skipped",
                       "кто закончил цикл: пользователь остановил цикл на итерации 1 — согласия ui-accept нет",
                       "Нерешённое (последняя запись, включая minor, noise и vision):", "`a` **major** SIZE", "src/A.tsx:12", "_[noise]_", "`m` **minor**"):
            self.assertIn(needle, t, needle)

    def test_consent_changes_the_finisher_line(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        self.decide(1, "stop")
        self.assertTrue(self.approve(f"ui-accept-{self.TB.TRACK}").returncode == 0)
        self.rec_report()
        self.assertIn("согласие ui-accept записано", self.text())

    def rec_report(self):
        manifest = TR.load(self.root, self.TB.TRACK)
        VR.write(self.root, manifest)

    def test_clean_cycle_outcome_and_unchecked_frames_are_named(self):
        self.second_frame()
        self.fake_iteration(1)
        self.rec(1)
        self.decide(1, "clean")
        t = self.text()
        self.assertIn("outcome: defects-remaining", t, "второй кадр не проверялся — «чисто» всего трека не заявляется")
        self.assertIn("frames:\n  main: {iterations: 1, outcome: clean}\n  second: {iterations: 0, outcome: not-checked}", t)
        self.assertIn("**Не проверялись:** second.", t)
        self.assertIn("## frame: second", t)
        self.assertIn("Кадр не проверялся", t)
        self.assertIn("пользователь принял результат как чистый", t)

    def test_single_frame_has_no_frames_map_and_a_clean_cycle_is_clean(self):
        self.fake_iteration(1)
        self.rec(1)
        self.decide(1, "clean")
        t = self.text()
        self.assertIn("outcome: clean", t)
        self.assertNotIn("frames:", t)
        self.assertIn("Нерешённого нет.", t)

    def test_report_is_deterministic(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        first = (self.track_dir / "verify-report.md").read_bytes()
        self.rec_report()
        self.assertEqual((self.track_dir / "verify-report.md").read_bytes(), first)

    def test_report_html_link_appears_only_when_the_file_exists(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        self.assertNotIn("report.html", self.text())
        (self.verify / "iter-1" / "report.html").write_text("<html/>", encoding="utf-8")
        self.rec_report()
        self.assertIn("[report.html](", self.text())


class Gate(World):
    def run_gate(self):
        return subprocess.run([sys.executable, "-X", "utf8", str(GATE), "verify", "--project", str(self.root), "--feature", self.TB.TRACK],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_not_checked_is_not_ready(self):
        r = self.run_gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("не проверялся", r.stderr)
        self.assertEqual(VG.check(self.root, self.TB.TRACK)["frames"], {"main": "not-checked"})

    def test_clean_last_iteration_closes_the_step(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        self.assertEqual(self.run_gate().returncode, 1)
        self.fake_iteration(2, compare="clean")
        self.rec(2)
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("clean", r.stdout)

    def test_stop_with_open_defects_needs_the_consent(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        self.decide(1, "stop")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"ui-accept-{self.TB.TRACK}", r.stderr)
        self.assertEqual(self.approve(f"ui-accept-{self.TB.TRACK}").returncode, 0)
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("defects-remaining", r.stdout)

    def test_dirty_without_a_stop_decision_is_not_ready(self):
        self.fake_iteration(1, defects=[defect("a")])
        self.rec(1)
        self.decide(1, "fix_all", fixed=["a"])
        r = self.run_gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn("решения «стоп» нет", r.stderr)

    def test_unknown_track_and_broken_state(self):
        self.assertIn("не найден", VG.check(self.root, "NOPE-1")["problems"][0])
        self.state_path().write_text("не json", encoding="utf-8")
        res = VG.check(self.root, self.TB.TRACK)
        self.assertEqual(res["frames"], {"main": "unreadable"})

    def test_status_ladder_still_reads_what_record_writes(self):
        from daiver import status as STS
        self.fake_iteration(1, compare="clean")
        self.rec(1)
        text = json.dumps(T.call("daiver_status", self.root, {}), ensure_ascii=False)
        self.assertIn("iterations", text, "лестница (1.10) читает history[].clean — запись её контракт не ломает")


if __name__ == "__main__":
    unittest.main()
