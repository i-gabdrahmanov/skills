"""test_decisions_ingest.py — приём файла решений и ручных меток (задача 5.4, спека §14.10–§14.11, INV-C34, D9, QG-P6).

Пинит: отображение ответов в запись итерации по всем четырём видам (fix с заметкой дословно, skip → declined, false_positive → noise с именем решившего, reported — ручные метки)
и ту же сводку в ответе `daiver_verify_report`; метка → пункт `source: review`, `zone: vision`, `severity: major`, `problem` = заметка ДОСЛОВНО, `pin` = рамка при `side: impl`
(`designPin` на макете), `nodeId` → `impl`; метка не блокирует clean (vision) и едет по итерациям со СВОИМ id; метка на несуществующую картинку, метка поверх чужого id и негодная метка
(пустая заметка, чужая сторона, нечисловая рамка, чужой id) называются поимённо, а не молча теряются; метка другого кадра — в `foreign` и в этом проходе не применяется; позднее
`decidedAt` выигрывает, при равных кадр сильнее трека; чужая версия схемы — отказ; решение, принятое про другой кадр, — `answeredElsewhere`.
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
from daiver.verify import decisions as DEC  # noqa: E402
from test_verify_state import World, defect  # noqa: E402


def mark(n=1, it=1, **kw):
    m = {"id": f"user-{it}-{n}", "note": "кнопка съехала вправо", "side": "impl", "box": {"x": 10, "y": 20, "w": 30, "h": 40}, "iteration": f"iter-{it}"}
    m.update(kw)
    return m


class Marks(World):
    def put(self, marks, where="track", decisions=(), **top):
        path = (self.track_dir if where == "track" else self.verify.parent) / DEC.FILE
        path.write_text(json.dumps(dict({"schema": 1, "decisions": list(decisions), "marks": marks}, **top)), encoding="utf-8")
        return path

    def iteration(self, n=1, defects=(), picture=True):
        d = self.fake_iteration(n, defects=list(defects))
        if picture:
            (d / "actual.png").write_bytes(K.png_bytes(100, 50))
        return d

    def loaded(self):
        return DEC.load(self.root, TR.load(self.root, self.TB.TRACK), None)

    def test_a_mark_becomes_a_vision_item_with_the_note_verbatim_and_its_box_as_the_pin(self):
        self.iteration(1)
        note = "кнопка съехала вправо:\n  сдвиг 12px, а должен быть 0 — «вся постановка»"
        self.put([mark(1, note=note, nodeId="dom:0.1.2", reviewer="Анна")])
        res = self.rec(1)
        self.assertEqual(res["decisions"]["reported"], ["user-1-1"])
        got = self.state()["history"][0]["defects"]
        self.assertEqual([d["id"] for d in got], ["user-1-1"])
        d = got[0]
        self.assertEqual((d["source"], d["zone"], d["severity"]), ("review", "vision", "major"))
        self.assertEqual(d["problem"], note, "заметка — дословно, вся постановка")
        self.assertEqual(d["pin"], {"x": 10, "y": 20, "w": 30, "h": 40})
        self.assertTrue(res["clean"], "vision не блокирует clean (спека: чистоту решает детерминированная часть)")
        self.assertEqual(res["blockingIds"], [])

    def test_a_design_side_mark_carries_a_design_pin_and_no_impl_pin(self):
        self.iteration(1)
        self.put([mark(1, side="design", box={"x": 5, "y": 6, "w": 7, "h": 8}, nodeId="dom:x")])
        loaded = self.loaded()
        items, rejected = DEC.reported(loaded, self.verify, self.verify / "iter-1", None, set())
        self.assertEqual(rejected, [("метка user-1-1 указывает на картинку макета, которой нет у кадра")])
        goal = self.root / "goal.png"
        goal.write_bytes(K.png_bytes(100, 50))
        items, rejected = DEC.reported(loaded, self.verify, self.verify / "iter-1", goal, set())
        self.assertEqual((rejected, len(items)), ([], 1))
        self.assertEqual(items[0]["designPin"], {"x": 5, "y": 6, "w": 7, "h": 8})
        self.assertNotIn("pin", items[0])
        self.assertNotIn("impl", items[0], "nodeId страницы к макету не относится")

    def test_an_impl_node_id_becomes_the_impl_link(self):
        self.iteration(1)
        self.put([mark(1, nodeId="dom:0.1.2")])
        items, _ = DEC.reported(self.loaded(), self.verify, self.verify / "iter-1", None, set())
        self.assertEqual(items[0]["impl"], "dom:0.1.2")

    def test_the_element_label_is_a_short_one_line_form_of_the_note(self):
        self.iteration(1)
        self.put([mark(1, note="a\n  b " + "x" * 200)])
        items, _ = DEC.reported(self.loaded(), self.verify, self.verify / "iter-1", None, set())
        self.assertLessEqual(len(items[0]["element"]), 60)
        self.assertNotIn("\n", items[0]["element"])
        self.assertTrue(items[0]["message"].startswith("a\n  b"), "message — дословно")

    def test_a_mark_carries_over_iterations_under_the_old_id_and_points_at_its_own_picture(self):
        self.iteration(1)
        self.put([mark(1, it=1)])
        self.rec(1)
        self.iteration(2)
        res = self.rec(2)
        self.assertEqual(res["decisions"]["reported"], ["user-1-1"])
        self.assertEqual([d["id"] for d in self.state()["history"][1]["defects"]], ["user-1-1"])
        # картинки той итерации больше нет — метка названа, а не потеряна молча
        (self.verify / "iter-1" / "actual.png").unlink()
        self.iteration(3)
        res = self.rec(3)
        self.assertEqual(res["decisions"]["reported"], [])
        self.assertTrue(any("user-1-1" in w and "iter-1/actual.png" in w for w in res["warnings"]), res.get("warnings"))

    def test_a_mark_without_its_picture_is_rejected_by_name(self):
        self.iteration(1, picture=False)
        self.put([mark(1)])
        res = self.rec(1)
        self.assertEqual(res["decisions"]["reported"], [])
        self.assertTrue(any("user-1-1" in w and "actual.png" in w for w in res["warnings"]), res.get("warnings"))
        self.assertEqual(self.state()["history"][0]["defects"], [])

    def test_a_mark_cannot_overwrite_a_defect_with_the_same_id(self):
        self.iteration(1, defects=[defect("user-1-1")])
        self.put([mark(1)])
        res = self.rec(1)
        self.assertEqual(res["decisions"]["reported"], [])
        self.assertTrue(any("user-1-1" in w and "занят" in w for w in res["warnings"]), res.get("warnings"))
        self.assertEqual(self.state()["history"][0]["defects"][0]["message"], "m-user-1-1")

    def test_bad_marks_are_named_in_problems_and_do_not_stop_the_good_ones(self):
        self.put([mark(1), mark(2, note="  "), mark(3, side="both"), mark(4, box={"x": 1, "y": 1, "w": 0, "h": 5}), mark(5, box={"x": "1", "y": 1, "w": 2, "h": 5}),
                  mark(6, box={"x": -1, "y": 1, "w": 2, "h": 5}), {"id": "x-1-1", "note": "n", "side": "impl", "box": {"x": 1, "y": 1, "w": 1, "h": 1}}, "строка",
                  mark(8, box={"x": True, "y": 1, "w": 2, "h": 5})])
        got = self.loaded()
        self.assertEqual(list(got["marks"]), ["user-1-1"])
        self.assertEqual(len(got["problems"]), 8, got["problems"])
        text = "\n".join(got["problems"])
        for needle in ("user-1-2: пустая заметка", "user-1-3: side", "user-1-4: box", "user-1-5: box", "user-1-6: box", "id метки", "user-1-8: box"):
            self.assertIn(needle, text)

    def test_a_mark_of_another_frame_is_foreign_not_applied(self):
        self.iteration(1)
        self.put([mark(1), mark(2, frame="other")], decisions=[{"id": "d1", "verdict": "fix", "frame": "other"}])
        got = self.loaded()
        self.assertEqual(list(got["marks"]), ["user-1-1"])
        self.assertEqual([m["id"] for m in got["foreign"]["marks"]], ["user-1-2"])
        self.assertEqual([d["id"] for d in got["foreign"]["decisions"]], ["d1"])
        self.assertEqual(got["elsewhere"], ["d1"])
        res = self.rec(1)
        self.assertEqual(res["decisions"]["reported"], ["user-1-1"])
        self.assertEqual(res["decisions"]["answeredElsewhere"], ["d1"])

    def test_the_later_decided_at_wins_and_on_a_tie_the_frame_file_beats_the_track_file(self):
        self.put([mark(1, note="трек старый", decidedAt="2026-10-01T10:00:00Z"), mark(2, note="трек", decidedAt="2026-10-02T10:00:00Z")])
        self.put([mark(1, note="кадр новый", decidedAt="2026-10-02T10:00:00Z"), mark(2, note="кадр", decidedAt="2026-10-02T10:00:00Z")], where="frame")
        got = self.loaded()["marks"]
        self.assertEqual((got["user-1-1"]["note"], got["user-1-2"]["note"]), ("кадр новый", "кадр"))
        self.put([mark(1, note="трек поздний", decidedAt="2026-10-05T10:00:00Z")])
        self.assertEqual(self.loaded()["marks"]["user-1-1"]["note"], "трек поздний")

    def test_a_foreign_schema_is_refused_for_marks_too(self):
        (self.track_dir / DEC.FILE).write_text(json.dumps({"schema": 2, "decisions": [], "marks": [mark(1)]}), encoding="utf-8")
        with self.assertRaises(DEC.DecisionsError):
            self.loaded()

    def test_all_four_kinds_land_in_the_record_and_the_same_summary_comes_from_the_report(self):
        it = self.iteration(1, defects=[defect("a"), defect("b"), defect("c"), defect("d")])
        self.put([mark(1)], decisions=[{"id": "a", "verdict": "false_positive", "reviewer": "Анна"}, {"id": "b", "verdict": "skip", "note": "так задумано"},
                                       {"id": "c", "verdict": "fix", "note": "поправить дословно: отступ 16"}, {"id": "zzz", "verdict": "fix"},
                                       {"id": "e", "verdict": "fix", "frame": "other"}])
        res = self.rec(1)
        want = {"answered": {"fix": ["c"], "skip": ["b"], "false_positive": ["a"]}, "reported": ["user-1-1"], "answeredElsewhere": ["e"], "unknown": ["zzz"]}
        self.assertEqual(res["decisions"], want)
        d = {x["id"]: x for x in self.state()["history"][0]["defects"]}
        self.assertTrue(d["a"]["noise"])
        self.assertEqual(d["a"]["decision"], {"verdict": "false_positive", "reviewer": "Анна"})
        self.assertTrue(d["b"]["declined"])
        self.assertEqual(d["c"]["decision"]["note"], "поправить дословно: отступ 16")
        self.assertFalse(d["d"].get("decision"))
        self.assertEqual(d["user-1-1"]["problem"], mark()["note"])
        rep = T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "iterationDir": it.relative_to(self.root).as_posix()})
        self.assertEqual(rep["decisions"], dict(want, rejectedMarks=[]), "отчёт читает файл тем же кодом, что запись итерации")

    def test_the_report_names_rejected_marks_and_file_problems(self):
        it = self.iteration(1, picture=True)
        self.put([mark(1), mark(2, it=7, iteration="iter-7"), mark(3, note="")])
        rep = T.call("daiver_verify_report", self.root, {"track": self.TB.TRACK, "iterationDir": it.relative_to(self.root).as_posix()})
        self.assertEqual(rep["decisions"]["reported"], ["user-1-1"])
        self.assertEqual(len(rep["decisions"]["rejectedMarks"]), 1)
        self.assertIn("user-7-2", rep["decisions"]["rejectedMarks"][0])
        self.assertTrue(any("user-1-3" in p for p in rep["decisionProblems"]), rep.get("decisionProblems"))


if __name__ == "__main__":
    unittest.main()
