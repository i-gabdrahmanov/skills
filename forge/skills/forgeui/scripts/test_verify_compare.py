"""test_verify_compare.py — `daiver_verify_compare`: id, свёртка, жизненный цикл, метрики, конвейер и инструмент (задача 4.6, спека §14.4.6–14.4.7,
§16, INV-C25, C28, QG-U16, U17, F2, F4, X3).

Пинит: id не от значений (X3) и общий у DOM- и код-гейта; свёртку 12 строк с одним неверным gap в один дефект с coverage и то, что общий предок
не сливает; жизненный цикл new / persisting / fixed / regressed и частичный фикс; declaredNoise; конвейер на «идеальной» реализации — настоящем
каноне «Modal» (F2: 0 дефектов, matchRate 1, fidelity 1); hard gate; LOW_MATCH_RATE словами; перезахват; DS-входы; сводку для код-гейта; и
`run` на настоящем треке (артефакты, итерации, шум по `decisions.json`, отказы). Снимок страницы строит маленький построитель формата `dom-snapshot` v1.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import canon as C  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver.ingest import pixso as PX  # noqa: E402
from daiver.verify import group as G  # noqa: E402
from daiver.verify import ids as I  # noqa: E402
from daiver.verify import impl_model as IM  # noqa: E402
from daiver.verify import lifecycle as LC  # noqa: E402
from daiver.verify import verify_compare as VC  # noqa: E402
from test_check import real_page  # noqa: E402
from test_ds_gate import APP, HDR, btn_design, library, plan_of  # noqa: E402
from test_match import FIXTURE, D, P, T as TXT, box, comp, page, screen  # noqa: E402


def rows_design(n, gap=16):
    """Таблица из n строк-карточек одного вида: у каждой gap между детьми."""
    kids = []
    for k in range(n):
        kids.append(D("frame", f"d:r{k}", [TXT(f"d:r{k}a", f"Ячейка {k}", y=k * 40), TXT(f"d:r{k}b", f"Значение {k}", y=k * 40 + 20)],
                      name="Row", layout={"direction": "column", "gap": gap, "padding": {"top": 0, "right": 0, "bottom": 0, "left": 0}},
                      box=box(0, k * 40, 200, 36)))
    return screen(kids)


def rows_page(n, gap=8):
    kids = []
    for k in range(n):
        kids.append(D("frame", f"p:r{k}", [P(f"p:r{k}a", f"Ячейка {k}", y=k * 40), P(f"p:r{k}b", f"Значение {k}", y=k * 40 + 20)], tag="div",
                      layout={"direction": "column", "gap": gap, "padding": {"top": 0, "right": 0, "bottom": 0, "left": 0}},
                      box=box(0, k * 40, 200, 36)))
    return page(kids)


def report(design, impl, plan=None, flags=None, ds=None, history=(), noise=(), tol=None):
    return VC.build_report(design, impl, plan, tol, flags, ds, list(history), set(noise), None)


class Ids(unittest.TestCase):
    def test_not_from_values_x3(self):
        a = I.defect_id("SPACING", "d:1", "p:1", "gap")
        self.assertEqual(a, I.defect_id("SPACING", "d:1", "p:1", "gap"))
        self.assertNotEqual(a, I.defect_id("SPACING", "d:1", "p:1", "padding.left"))
        self.assertNotEqual(a, I.defect_id("SPACING", "d:2", "p:1", "gap"))
        self.assertEqual(len(a), 12)

    def test_missing_ignores_the_impl_node_so_dom_and_code_gates_agree(self):
        self.assertEqual(I.defect_id("MISSING", "d:1", "dom:3", None), I.defect_id("MISSING", "d:1", "code:s1/0", None))
        self.assertNotEqual(I.defect_id("TEXT", "d:1", "dom:3", "text"), I.defect_id("TEXT", "d:1", "code:s1/0", "text"))

    def test_fold_id_is_about_the_kind_not_the_representative(self):
        self.assertEqual(I.fold_id("SPACING", "frame:Row", "gap"), I.fold_id("SPACING", "frame:Row", "gap"))
        self.assertNotEqual(I.fold_id("SPACING", "frame:Row", "gap"), I.fold_id("SPACING", "frame:Card", "gap"))
        self.assertEqual(I.signature({"kind": "instance", "name": "Variant=Default", "component": {"figmaName": "Modal Action"}}), "instance:Modal Action")
        self.assertEqual(I.signature(None), "")

    def test_code_gate_uses_the_same_function(self):
        from daiver.verify import code_compare as CC
        self.assertIs(CC.defect_id, I.defect_id)


class Grouping(unittest.TestCase):
    def test_twelve_rows_with_one_wrong_gap_are_one_defect_with_coverage(self):
        res = report(rows_design(12), rows_page(12))
        gap = [d for d in res["defects"] if d.get("prop") == "gap"]
        self.assertEqual(len(gap), 1)
        self.assertEqual(gap[0]["coverage"], 12)
        self.assertEqual(gap[0]["rows"], [{"МАКЕТ": "16", "РЕАЛИЗАЦИЯ": "8", "count": 12}])
        self.assertEqual(len(gap[0]["designIds"]), 12)
        self.assertEqual(res["counts"]["findings"], 12)
        self.assertEqual(res["counts"]["defects"], 1)

    def test_id_survives_when_the_value_changes_and_when_the_count_shrinks(self):
        a = report(rows_design(12), rows_page(12, gap=8))["defects"][0]
        b = report(rows_design(12), rows_page(12, gap=4))["defects"][0]
        self.assertEqual(a["id"], b["id"], "значение поменялось — дефект тот же")
        self.assertEqual(b["rows"][0]["РЕАЛИЗАЦИЯ"], "4")
        partial = rows_page(12, gap=16)
        for k in range(5):                                          # частичный фикс: пять строк остались неверными
            partial["children"][0]["children"][0]["children"][k]["layout"]["gap"] = 8
        c = report(rows_design(12), partial)["defects"][0]
        self.assertEqual((c["id"], c["coverage"]), (a["id"], 5), "12 → 5: тот же дефект, а не новый")

    def test_two_value_pairs_of_one_address_keep_both(self):
        pg = rows_page(4, gap=8)
        pg["children"][0]["children"][0]["children"][3]["layout"]["gap"] = 4
        [d] = report(rows_design(4), pg)["defects"]
        self.assertEqual(d["coverage"], 4)
        self.assertEqual(sorted((r["РЕАЛИЗАЦИЯ"], r["count"]) for r in d["rows"]), [("4", 1), ("8", 3)])

    def test_findings_that_share_only_an_ancestor_do_not_merge(self):
        d = screen([D("frame", "d:a", [TXT("d:at", "Один")], name="Alpha", layout={"direction": "column", "gap": 16}, box=box(0, 0, 100, 30)),
                    D("frame", "d:b", [TXT("d:bt", "Два", y=40)], name="Beta", layout={"direction": "column", "gap": 16}, box=box(0, 40, 100, 30))])
        i = page([D("frame", "p:a", [P("p:at", "Один")], tag="div", layout={"direction": "column", "gap": 8}, box=box(0, 0, 100, 30)),
                  D("frame", "p:b", [P("p:bt", "Два", y=40)], tag="div", layout={"direction": "column", "gap": 8}, box=box(0, 40, 100, 30))])
        res = report(d, i)
        self.assertEqual(sorted((x["code"], x["coverage"]) for x in res["defects"]), [("SPACING", 1), ("SPACING", 1)])

    def test_structure_findings_are_never_folded(self):
        d = screen([TXT("d:a", "Раз"), TXT("d:b", "Два", y=20), TXT("d:c", "Три", y=40), TXT("d:e", "Четыре", y=60)])
        res = report(d, page([P("p:a", "Раз"), P("p:b", "Два", y=20), P("p:c", "Три", y=40)]))
        self.assertEqual([(x["code"], x["coverage"]) for x in res["defects"]], [("MISSING", 1)])

    def test_group_fold_unit(self):
        fs = [{"id": f"f{k}", "code": "COLOR", "layer": "color", "design": f"d{k}", "prop": "color", "expected": "a", "actual": "b"} for k in range(3)]
        fs.append({"id": "t", "code": "COLOR", "layer": "color", "design": "d0", "prop": "color", "expected": "x", "actual": "y", "suppressedBy": "f0"})
        idx = {f"d{k}": {"id": f"d{k}", "kind": "text", "name": "Cap"} for k in range(3)}
        [g] = G.fold(fs, idx)
        self.assertEqual((g["coverage"], g["suppressedCount"], g["signature"]), (3, 1, "text:Cap"))


class Lifecycle(unittest.TestCase):
    def d(self, i, cov=1):
        return {"id": i, "code": "SIZE", "coverage": cov, "element": f"e{i}"}

    def test_transitions(self):
        hist = [{"name": "iter-1", "defects": [self.d("a"), self.d("b"), self.d("c", 12)]}, {"name": "iter-2", "defects": [self.d("a"), self.d("c", 12)]}]
        now = [self.d("a"), self.d("b"), self.d("c", 5), self.d("n")]
        res = LC.apply(now, hist, set())
        self.assertEqual({x["id"]: x["lifecycle"] for x in now}, {"a": "persisting", "b": "regressed", "c": "persisting", "n": "new"})
        self.assertEqual(now[2]["coverageDelta"], -7, "частичный фикс — не регресс")
        self.assertEqual(res["counts"], {"new": 1, "persisting": 2, "regressed": 1, "fixed": 0})

    def test_fixed_are_listed_not_defects(self):
        hist = [{"name": "iter-1", "defects": [self.d("a"), self.d("b")]}]
        res = LC.apply([self.d("a")], hist, set())
        self.assertEqual(res["fixed"], [{"id": "b", "code": "SIZE", "element": "eb"}])
        self.assertEqual(res["counts"]["fixed"], 1)

    def test_first_iteration_everything_is_new(self):
        now = [self.d("a")]
        LC.apply(now, [], set())
        self.assertEqual(now[0]["lifecycle"], "new")

    def test_history_is_natural_sorted_and_excludes_current_and_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Path(tmp)
            for name, ids in (("iter-2", ["a"]), ("iter-10", ["b"]), ("iter-9", ["c"]), ("iter-11", ["z"])):
                (v / name).mkdir()
                (v / name / "defects.json").write_text(json.dumps({"defects": [{"id": i} for i in ids]}), encoding="utf-8")
            (v / "iter-3").mkdir()
            (v / "iter-3" / "defects.json").write_text("{не json", encoding="utf-8")
            (v / "iter-4").mkdir()
            h = LC.load_history(v, "iter-10")
            self.assertEqual([x["name"] for x in h], ["iter-2", "iter-3", "iter-9"], "iter-9 раньше iter-10; битый файл — пустая итерация; iter-4 без файла нет")
            self.assertEqual(h[1]["defects"], [])

    def test_declared_noise_from_history_and_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist = [{"name": "iter-1", "defects": [{"id": "a", "noise": True}, {"id": "b"}]}]
            dec = Path(tmp) / "decisions.json"
            dec.write_text(json.dumps([{"id": "c", "verdict": "false_positive"}, {"id": "d", "verdict": "fixed"}, "мусор"]), encoding="utf-8")
            self.assertEqual(LC.declared_noise(hist, dec), {"a", "c"})
            dec.write_text(json.dumps({"decisions": [{"id": "e", "verdict": "false_positive"}]}), encoding="utf-8")
            self.assertEqual(LC.declared_noise([], dec), {"e"})
            dec.write_text("не json", encoding="utf-8")
            self.assertEqual(LC.declared_noise([], dec), set())
            self.assertEqual(LC.declared_noise([], Path(tmp) / "нет.json"), set())


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = PX.normalize_doc(json.loads(FIXTURE.read_text(encoding="utf-8")), "sha")["root"]

    def test_f2_a_perfect_implementation_has_no_defects(self):
        outer, _ = real_page(self.design)
        res = report(self.design, outer)
        self.assertEqual(res["defects"], [])
        self.assertEqual((res["status"], res["matchRate"], res["fidelity"], res["unmatched"]), ("clean", 1.0, 1.0, 0))
        self.assertEqual(res["degraded"], [])
        self.assertEqual(res["counts"]["blocking"], 0)
        self.assertIn("модельное сравнение чисто", res["nextStep"])
        self.assertIn("copy 1", res["unverified"]["summary"], "нечего было сравнивать — читается вслух рядом с «чисто»")
        self.assertIn("copy 1", res["nextStep"])

    def test_a_wrong_slot_is_a_blocking_defect_and_lowers_fidelity(self):
        outer, pg = real_page(self.design)
        pg["children"][0]["children"][0]["text"] = "Текст12 слово 1-105_2-слово.2"
        res = report(self.design, outer)
        self.assertEqual([(d["code"], d["severity"], d["lifecycle"]) for d in res["defects"]], [("SLOT_TEXT", "critical", "new")])
        d = res["defects"][0]
        self.assertEqual(d["design"], "2:153703")
        self.assertEqual(d["zone"], "deterministic")
        self.assertEqual(d["rows"], [{"МАКЕТ": "Текст12 слово 1-105_2-слово.1", "РЕАЛИЗАЦИЯ": "Текст12 слово 1-105_2-слово.2", "count": 1}])
        self.assertEqual((res["status"], res["fidelity"]), ("defects", round(2 / 3, 4)))
        self.assertIn("блокирующих дефектов: 1", res["nextStep"])

    def test_hard_gate_emits_no_defects_and_names_the_capture(self):
        d = screen([TXT("d:a", "Раз"), TXT("d:b", "Два", y=20), TXT("d:c", "Три", y=40)])
        res = report(d, page([P("p:x", "Совсем", y=0), P("p:y", "Другое", y=20)]))
        self.assertEqual((res["status"], res["defects"], res["fidelity"]), ("hard-gate-fail", [], None))
        self.assertEqual((res["gates"]["match"], res["gates"]["pixel"], res["gates"]["vision"]), ("fail", "skipped", "skipped"))
        self.assertIn("не этот кадр", res["nextStep"])
        self.assertTrue(res["findings"], "находки остаются уликами")

    def test_low_match_rate_is_explained_in_words(self):
        d = screen([TXT(f"d:t{n}", f"Текст{n}", y=n * 20) for n in range(10)])
        res = report(d, page([P(f"p:t{n}", f"Текст{n}", y=n * 20) for n in range(7)]))
        self.assertEqual((res["matchRate"], res["unmatched"], res["lowMatchRate"]), (0.7, 3, True))
        self.assertIn("unmatched 3", res["nextStep"])
        self.assertIn("fidelity выглядит хорошо", res["nextStep"])
        self.assertEqual(res["fidelity"], 1.0, "по найденному всё хорошо — именно поэтому читается оба числа")

    def test_layer_counters_and_nothing_is_dropped(self):
        res = report(rows_design(4), rows_page(4))
        self.assertEqual(res["layers"], {"geometry": {"total": 4, "suppressed": 0}})
        self.assertEqual(res["counts"]["findings"], len(res["findings"]))
        self.assertEqual(res["counts"]["suppressed"], 0)
        self.assertTrue(all(f["id"] for f in res["findings"]))

    def test_declared_noise_does_not_block_and_leaves_the_denominator(self):
        d, i = rows_design(3), rows_page(3)
        first = report(d, i)
        did = first["defects"][0]["id"]
        res = report(d, i, noise={did})
        self.assertTrue(res["defects"][0]["noise"])
        self.assertEqual((res["status"], res["counts"]["blocking"], res["counts"]["noise"]), ("clean", 0, 1))
        self.assertIn("шум по прошлым решениям", res["nextStep"])
        self.assertLess(first["fidelity"], 1.0)
        self.assertEqual(res["fidelity"], 1.0)

    def test_lifecycle_is_applied_with_history(self):
        d, i = rows_design(3), rows_page(3)
        did = report(d, i)["defects"][0]["id"]
        res = report(d, i, history=[{"name": "iter-1", "defects": [{"id": did, "coverage": 3, "code": "SPACING"}, {"id": "gone", "code": "SIZE"}]}])
        self.assertEqual(res["defects"][0]["lifecycle"], "persisting")
        self.assertEqual(res["lifecycle"], {"new": 0, "persisting": 1, "regressed": 0, "fixed": 1})
        self.assertEqual([x["id"] for x in res["fixed"]], ["gone"])

    def test_ds_gate_findings_join_the_defects(self):
        d = btn_design()
        i = page([comp("p:b", ["Button"], [P("p:t", "Ок")])])
        ds = {"lib": library(), "sources": {"a.tsx": HDR + 'export const A = () => <><Button size="xl" /><OldTag tone="a" /></>;\n'}}
        res = report(d, i, plan_of({"id": "d:b", "component": "Button"}), ds=ds)
        got = sorted((x["code"], x["severity"]) for x in res["defects"] if x["layer"] == "ds")
        self.assertIn(("DS_PROP_WRONG", "major"), got)
        self.assertIn(("DS_DEPRECATED", "minor"), got)
        wrong = [x for x in res["defects"] if x["code"] == "DS_PROP_WRONG"][0]
        self.assertTrue(wrong["rule"])
        self.assertEqual(wrong["location"]["file"], "a.tsx")
        self.assertEqual(res["gates"]["ds"], "pass")

    def test_minor_findings_warn_but_do_not_block(self):
        d = btn_design()
        i = page([comp("p:b", ["Button"], [P("p:t", "Ок")])])
        ds = {"lib": library(), "sources": {"a.tsx": HDR + 'export const A = () => <OldTag tone="a">x</OldTag>;\n'}}
        res = report(d, i, plan_of({"id": "d:b", "component": "Button"}), ds=ds)
        self.assertEqual([x["severity"] for x in res["defects"]], ["minor", "minor"])
        self.assertEqual((res["status"], res["counts"]["blocking"]), ("clean", 0))

    def test_without_ds_inputs_the_gate_is_skipped_with_a_reason(self):
        res = report(screen([TXT("d:a", "Раз")]), page([P("p:a", "Раз")]))
        self.assertEqual((res["gates"]["ds"], res["ds"]["ran"]), ("skipped", False))

    def test_summary_carries_pairs_and_ids_for_the_code_gate(self):
        res = report(rows_design(2), rows_page(2))
        self.assertTrue(all({"design", "impl", "anchor", "confidence"} <= set(p) for p in res["pairs"]))
        self.assertEqual(res["defectIds"], [d["id"] for d in res["defects"]])

    def test_design_link_uses_the_frame_url_template(self):
        outer, pg = real_page(self.design)
        pg["children"][0]["children"][0]["text"] = "Текст12 слово 1-105_2-слово.2"
        res = VC.build_report(self.design, outer, None, None, None, None, [], set(), "https://pixso.example/a?item-id={id}")
        d = res["defects"][0]
        self.assertEqual(d["designUrl"], "https://pixso.example/a?item-id=2:153703")
        self.assertEqual(d["designLink"], "[2:153703](https://pixso.example/a?item-id=2:153703)")
        bare = report(self.design, outer)["defects"][0]
        self.assertEqual((bare["designLink"], "designUrl" in bare), ("`2:153703`", False), "адреса нет — голый id, ссылка не выдумывается")

    def test_element_is_named_by_visible_text(self):
        self.assertEqual(VC.element_name({"id": "1", "kind": "text", "text": "Привет  мир"}), "Привет мир")
        self.assertEqual(VC.element_name({"id": "2", "kind": "frame", "name": "Card", "children": [{"id": "3", "kind": "text", "text": "Заголовок"}]}), "Заголовок")
        self.assertEqual(VC.element_name({"id": "4", "kind": "instance", "component": {"figmaName": "Btn", "content": [{"id": "s", "text": "Ок", "overridden": True}]}}),
                         "Btn «Ок»")
        self.assertEqual(VC.element_name({"id": "5", "kind": "frame", "name": "Frame 7"}), "Frame 7")
        self.assertIsNone(VC.element_name(None))

    def test_deterministic(self):
        a = report(rows_design(5), rows_page(5))
        b = report(rows_design(5), rows_page(5))
        a.pop("matched"), b.pop("matched")
        self.assertEqual(json.dumps(a, sort_keys=True, ensure_ascii=False), json.dumps(b, sort_keys=True, ensure_ascii=False))

    def test_input_is_not_mutated(self):
        d, i = rows_design(3), rows_page(3)
        before = copy.deepcopy((d, i))
        report(d, i)
        self.assertEqual((d, i), before)


# ── настоящий трек ────────────────────────────────────────────────────────────

def snap_node(i, kind="frame", kids=(), **kw):
    n = {"id": i, "kind": kind, "tag": kw.pop("tag", "div"), "box": kw.pop("box", {"x": 0, "y": 0, "w": 100, "h": 20})}
    n["size"] = {"w": n["box"]["w"], "h": n["box"]["h"]}
    n.update(kw)
    if kids:
        n["children"] = list(kids)
    return n


def snapshot(texts, ready=True, elements=None):
    kids = [snap_node(f"dom:0.{k}", "text", tag="span", text=t, box={"x": 0, "y": k * 20, "w": 80, "h": 14}) for k, t in enumerate(texts)]
    root = snap_node("dom:0", "frame", kids, tag="body", box={"x": 0, "y": 0, "w": 400, "h": 300})
    return {"v": 1, "root": root, "elements": len(texts) + 1 if elements is None else elements, "domElements": len(texts) + 1, "react": {"found": False},
            "viewport": {"width": 400, "height": 300}, "capture": {"ready": ready, "readiness": {"ready": ready}}}


class World(unittest.TestCase):
    def setUp(self):
        import test_build_state as TB
        self.TB = TB
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        node = TB.frame("Page", [TB.leaf("t1", name="t1", text="Заголовок"), TB.leaf("t2", name="t2", text="Описание")],
                        name=TB.autoname("Page"), layout={"direction": "column", "gap": 8})
        TB.make_world(self.root, node)
        self.verify = self.root / ".daiver" / "tracks" / TB.TRACK / "verify"

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, snap):
        d = self.verify / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
        return d.relative_to(self.root).as_posix()

    def call(self, name, **kw):
        return T.call("daiver_verify_compare", self.root, dict({"track": self.TB.TRACK, "iterationDir": f".daiver/tracks/{self.TB.TRACK}/verify/{name}"}, **kw))

    def read(self, name, file):
        return json.loads((self.verify / name / file).read_text(encoding="utf-8"))

    def test_clean_run_writes_four_artifacts(self):
        self.put("iter-1", snapshot(["Заголовок", "Описание"]))
        res = self.call("iter-1")
        self.assertEqual((res["status"], res["matchRate"], res["fidelity"]), ("clean", 1.0, 1.0), res)
        for n in VC.ARTIFACTS:
            self.assertTrue((self.verify / "iter-1" / n).is_file(), n)
        s = self.read("iter-1", "compare-summary.json")
        self.assertEqual((s["frame"], s["gates"]["match"], s["defectIds"]), ("main", "pass", []))
        self.assertTrue(s["pairs"])
        self.assertEqual(self.read("iter-1", "impl-model.json")["daiver"]["sourceFormat"], "dom")
        self.assertFalse(s["ds"]["ran"])

    def test_changed_text_is_a_defect_with_lifecycle_across_iterations(self):
        self.put("iter-1", snapshot(["Заголовок!", "Описание"]))
        r1 = self.call("iter-1")
        self.assertEqual([(d["code"], d["lifecycle"]) for d in r1["defects"]], [("TEXT", "new")])
        self.put("iter-2", snapshot(["Заголовок!!", "Описание"]))
        r2 = self.call("iter-2")
        self.assertEqual([(d["code"], d["lifecycle"]) for d in r2["defects"]], [("TEXT", "persisting")])
        self.assertEqual(r1["defects"][0]["id"], r2["defects"][0]["id"], "значение поменялось, id тот же")
        self.put("iter-3", snapshot(["Заголовок", "Описание"]))
        r3 = self.call("iter-3")
        self.assertEqual((r3["status"], r3["lifecycle"]["fixed"]), ("clean", 1))
        self.assertEqual(self.read("iter-3", "defects.json")["fixed"][0]["id"], r1["defects"][0]["id"])
        self.put("iter-4", snapshot(["Заголовок!", "Описание"]))
        r4 = self.call("iter-4")
        self.assertEqual([d["lifecycle"] for d in r4["defects"]], ["regressed"], "исправлен и вернулся")

    def test_decisions_file_marks_noise(self):
        self.put("iter-1", snapshot(["Заголовок!", "Описание"]))
        did = self.call("iter-1")["defects"][0]["id"]
        (self.verify / "decisions.json").write_text(json.dumps([{"id": did, "verdict": "false_positive"}]), encoding="utf-8")
        self.put("iter-2", snapshot(["Заголовок!", "Описание"]))
        r = self.call("iter-2")
        self.assertEqual((r["status"], r["counts"]["blocking"], r["counts"]["noise"]), ("clean", 0, 1))
        self.assertEqual(self.read("iter-2", "compare-summary.json")["declaredNoise"], [did])

    def test_hard_gate_writes_empty_defects_and_keeps_findings(self):
        self.put("iter-1", snapshot(["Совсем", "Другое", "Дерево"]))
        r = self.call("iter-1")
        self.assertEqual((r["status"], r["defects"]), ("hard-gate-fail", []))
        self.assertEqual(self.read("iter-1", "defects.json")["defects"], [])
        self.assertTrue(self.read("iter-1", "findings.json")["findings"])

    def test_unready_or_underrendered_capture_asks_for_a_recapture(self):
        self.put("iter-1", snapshot(["Заголовок", "Описание"], ready=False))
        r = self.call("iter-1")
        self.assertEqual(r["status"], "recapture")
        self.assertIn("ещё грузилась", r["reason"])
        s = self.read("iter-1", "compare-summary.json")
        self.assertEqual((s["defects"], s["defectIds"]), ([], []))
        self.put("iter-2", snapshot(["Заголовок", "Описание"], elements=1))
        r = self.call("iter-2")
        self.assertEqual(r["status"], "recapture")
        self.assertIn("недорисована", r["reason"])
        self.assertIn("перезахвати", r["nextStep"])

    def test_overflow_flags_come_from_the_snapshot(self):
        snap = snapshot(["Заголовок", "Описание"])
        snap["root"]["children"][0]["overflowing"] = True
        self.put("iter-1", snap)
        r = self.call("iter-1")
        self.assertEqual([d["code"] for d in r["defects"]], ["OVERFLOW"])

    def test_the_code_gate_reads_the_summary_this_tool_wrote(self):
        self.put("iter-1", snapshot(["Заголовок", "Описание"]))
        self.call("iter-1")
        from daiver.verify import code_compare as CC
        dom = CC._dom_summary(self.verify / "iter-1" / "compare-summary.json")
        self.assertTrue(dom["pairs"] and dom["defectIds"] == [])

    def test_errors_are_named(self):
        with self.assertRaises(T.ToolError) as cm:
            self.call("iter-9")
        self.assertIn("daiver_screenshot", str(cm.exception))
        with self.assertRaises(T.ToolError):
            T.call("daiver_verify_compare", self.root, {"track": self.TB.TRACK, "iterationDir": "src/x"})
        with self.assertRaises(T.ToolError):
            T.call("daiver_verify_compare", self.root, {"track": self.TB.TRACK})
        with self.assertRaises(T.ToolError):
            T.call("daiver_verify_compare", self.root, {"track": "NOPE-1", "iterationDir": ".daiver/tracks/NOPE-1/verify/i"})
        d = self.verify / "iter-1"
        d.mkdir(parents=True)
        (d / "snapshot.json").write_text("не json", encoding="utf-8")
        with self.assertRaises(T.ToolError) as cm:
            self.call("iter-1")
        self.assertIn("snapshot.json не читается", str(cm.exception))

    def test_ds_gate_runs_when_the_build_is_recorded(self):
        TB = self.TB
        TB.write_ds(self.root, ["Button"])
        self.put("iter-1", snapshot(["Заголовок", "Описание"]))
        r = self.call("iter-1")
        self.assertEqual(r["gates"]["ds"], "skipped", "нет build-state: исходников нет — гейт честно не исполнялся")


if __name__ == "__main__":
    unittest.main()
