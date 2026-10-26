"""test_ds_gate.py — DS-гейт: компоненты, пропсы, токены (задача 4.5, спека §14.4.5, INV-C13, B17, A2, QG-U16).

Пинит на синтетике (боевых ДС, планов и модулей нет): каждый код и его dismissal-двойник; три честных `ran: false`;
сверку «до обвинения» для отсутствующего fiber (разметка, исходники, ничего); fiber вне опубликованных — молчание;
неуверенная пара — dismissal; план другого кадра — degraded; namespace-импорт и самодельный одноимённый; значения
enum/boolean и «неназываемое значение»; deprecated своего компонента; токен-литерал, ссылка, fallback, мимо шкалы;
`rule` у каждой находки; чтение с диска (`gather`) на настоящем треке с планом и build-state. Скан исходников
(`tsx_scan`) пинится здесь же: строки, колонки, импорты, spread, цвета без комментариев.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import ds_lookup as DL  # noqa: E402
from daiver.verify import ds_gate as G  # noqa: E402
from daiver.verify import match as MT  # noqa: E402
from daiver.verify import tsx_scan as TS  # noqa: E402
from test_match import D, P, T, box, comp, inst, page, screen  # noqa: E402


def prop(kind, options=None):
    d = {"kind": kind, "from": "types"}
    if options is not None:
        d["options"] = options
    return d


def library(**extra):
    comps = {
        "Button": {"title": None, "taxonomy": [], "stories": 1, "propsClosed": True,
                   "props": {"size": prop("enum", ["sm", "md"]), "disabled": prop("boolean"), "label": prop("string")},
                   "usedValues": {"label": ["Ок"]}, "import": {"path": "@corp/ui", "named": "Button"}},
        "Card": {"title": None, "taxonomy": [], "stories": 1, "propsClosed": False, "props": {}, "usedValues": {},
                 "import": {"path": "@corp/ui", "named": "Card"}},
        "Label": {"title": None, "taxonomy": [], "stories": 1, "propsClosed": False, "props": {}, "usedValues": {},
                  "import": {"path": "@corp/ui", "named": "Label"}},
        "Form.Item": {"title": None, "taxonomy": [], "stories": 1, "propsClosed": False, "props": {}, "usedValues": {},
                      "import": {"path": "@corp/ui", "named": "Form"}},
        "OldTag": {"title": None, "taxonomy": [], "stories": 1, "propsClosed": False,
                   "props": {"tone": prop("enum", ["a", "b"])}, "usedValues": {}, "import": {"path": "@corp/ui", "named": "OldTag"}},
    }
    det = {n: {"description": None, "docs": None, "props": {}, "deprecations": [], "examples": []} for n in comps}
    det["OldTag"]["deprecations"] = [{"target": "component", "name": "OldTag", "message": "use Tag", "replacement": "Tag"},
                                     {"target": "prop", "name": "tone", "message": "use kind", "replacement": "kind"}]
    det["Card"]["docs"] = "OldTag is deprecated, do not use"            # чужая проза: Card от этого не deprecated
    for k, v in extra.items():
        comps[k] = v
    return DL.Library("stories", {"components": comps}, {"components": det})


def plan_of(*entries):
    return {"sections": [{"id": "s1", "nodes": list(entries)}]}


def pair_for(d, i, anchor="component", confidence="high"):
    return {"pairs": [{"design": "d:root", "impl": "p:wrap2", "anchor": "root", "confidence": "high", "score": 0},
                      {"design": d, "impl": i, "anchor": anchor, "confidence": confidence, "score": 1}],
            "missing": [], "extra": [], "order": []}


def run(design, impl, plan, sources, lib=None, matched=None, **kw):
    m = matched if matched is not None else MT.match(design, impl, plan)
    return G.run(design, impl, m, plan, library() if lib is None else lib, sources, **kw)


APP = 'import { Button } from "@corp/ui";\nexport const A = () => <Button size="md">Ок</Button>;\n'


def btn_design():
    return screen([inst("d:b", "Button", comp={"origin": "library", "sized": "own", "textProps": {"l": "Ок"}}, box=box(0, 0, 80, 30))])


def codes(res):
    return sorted((f["code"], f.get("design") or f.get("prop")) for f in res["findings"])


def dcodes(res):
    return sorted((f["code"], f.get("design") or f.get("prop")) for f in res["dismissals"])


class HonestRuns(unittest.TestCase):
    def test_no_requirements_document(self):
        res = G.run(btn_design(), page([]), MT.match(btn_design(), page([])), plan_of({"id": "d:b", "component": "Button"}), None, {"a.tsx": APP})
        self.assertFalse(res["ran"])
        self.assertIn("нет документа требований", res["reason"])
        self.assertEqual(res["findings"], [])

    def test_no_js_among_outputs(self):
        res = run(btn_design(), page([]), plan_of({"id": "d:b", "component": "Button"}), {"a.less": "@c: #fff;"})
        self.assertFalse(res["ran"])
        self.assertIn("нет JS/TS", res["reason"])

    def test_nothing_to_hold_against(self):
        res = run(screen([T("d:a", "Привет")]), page([P("p:a", "Привет")]), plan_of({"id": "d:a", "resolution": "local"}), {"a.tsx": APP})
        self.assertFalse(res["ran"])
        self.assertIn("нечего держать против", res["reason"])

    def test_ran_true_when_something_to_check(self):
        res = run(btn_design(), page([comp("p:b", ["Button"], [P("p:t", "Ок")])]), plan_of({"id": "d:b", "component": "Button"}), {"a.tsx": APP})
        self.assertTrue(res["ran"])
        self.assertEqual(res["findings"], [])


class Identity(unittest.TestCase):
    def _go(self, impl_children, plan=None, matched=None, sources=None, react=None):
        plan = plan or plan_of({"id": "d:b", "component": "Button"})
        return run(btn_design(), page(impl_children), plan, {"a.tsx": APP} if sources is None else sources, matched=matched, react_found=react)

    def test_matching_fiber_is_silent(self):
        self.assertEqual(self._go([comp("p:b", ["Button"], [P("p:t", "Ок")])])["findings"], [])

    def test_name_ignores_case_separators_and_compound(self):
        d = screen([inst("d:f", "Form Item", comp={"origin": "library", "sized": "own", "textProps": {"l": "Поле"}}, box=box(0, 0, 80, 30))])
        res = run(d, page([comp("p:f", ["FormItem"], [P("p:t", "Поле")])]), plan_of({"id": "d:f", "component": "Form.Item"}), {"a.tsx": APP})
        self.assertEqual(res["findings"], [])

    def test_another_published_component_is_wrong(self):
        res = self._go([comp("p:b", ["Card"], [P("p:t", "Ок")])])
        self.assertEqual(codes(res), [("DS_COMPONENT_WRONG", "d:b")])
        f = res["findings"][0]
        self.assertEqual((f["expected"], f["actual"]), ("Button", "Card"))
        self.assertIn("plan.sections[].nodes[d:b].component = Button", f["rule"])

    def test_fiber_outside_the_published_names_is_silent(self):
        res = self._go([comp("p:b", ["MyOwnThing"], [P("p:t", "Ок")])])
        self.assertEqual((res["findings"], res["dismissals"]), ([], []))

    def test_any_name_in_the_chain_matching_the_plan_is_enough(self):
        self.assertEqual(self._go([comp("p:b", ["Wrapper", "Button"], [P("p:t", "Ок")])])["findings"], [])

    def test_absent_fiber_stands_when_sources_do_not_render_it(self):
        res = self._go([D("frame", "p:b", [P("p:t", "Ок")], box=box(0, 0, 80, 30), tag="div")], sources={"a.tsx": "export const A = () => <div/>;\n"}, react=True)
        self.assertEqual(codes(res), [("DS_COMPONENT_MISSED", "d:b")])
        self.assertEqual(res["findings"][0]["expected"], "Button")

    def test_absent_fiber_is_dismissed_when_the_markup_agrees_with_the_plan(self):
        d = screen([inst("d:l", "Label", comp={"origin": "library", "sized": "own", "textProps": {"t": "Имя"}}, box=box(0, 0, 80, 30))])
        i = page([D("frame", "p:l", [P("p:t", "Имя")], box=box(0, 0, 80, 30), tag="label")])
        res = run(d, i, plan_of({"id": "d:l", "component": "Label"}), {"a.tsx": "export const A = () => <label>Имя</label>;\n"}, react_found=True)
        self.assertEqual(res["findings"], [])
        self.assertEqual(dcodes(res), [("DS_COMPONENT_MISSED", "d:l")])
        self.assertIn("<label>", res["dismissals"][0]["reason"])

    def test_absent_fiber_is_withdrawn_when_sources_render_the_component(self):
        res = self._go([D("frame", "p:b", [P("p:t", "Ок")], box=box(0, 0, 80, 30), tag="div")], react=True)
        self.assertEqual(res["findings"], [])
        self.assertEqual(dcodes(res), [("DS_COMPONENT_MISSED", "d:b")])
        self.assertIn("остаточный риск", res["dismissals"][0]["reason"])
        self.assertIn("a.tsx:2", res["dismissals"][0]["reason"])

    def test_same_named_homemade_component_does_not_count_as_the_library(self):
        own = 'import { Button } from "./Button";\nexport const A = () => <Button size="md">Ок</Button>;\n'
        res = self._go([D("frame", "p:b", [P("p:t", "Ок")], box=box(0, 0, 80, 30), tag="div")], sources={"a.tsx": own}, react=True)
        self.assertEqual(codes(res), [("DS_COMPONENT_MISSED", "d:b")], "импорт из ./Button — не библиотека")

    def test_namespace_import_counts_as_the_library(self):
        ns = 'import * as Ui from "@corp/ui";\nexport const A = () => <Ui.Button size="md">Ок</Ui.Button>;\n'
        res = self._go([D("frame", "p:b", [P("p:t", "Ок")], box=box(0, 0, 80, 30), tag="div")], sources={"a.tsx": ns}, react=True)
        self.assertEqual(dcodes(res), [("DS_COMPONENT_MISSED", "d:b")])

    def test_unsure_pair_turns_both_codes_into_dismissals(self):
        low = lambda ch: pair_for("d:b", "p:b", "position", "low")  # noqa: E731
        res = self._go([comp("p:b", ["Card"], [P("p:t", "Ок")])], matched=low(0))
        self.assertEqual((res["findings"], dcodes(res)), ([], [("DS_COMPONENT_WRONG", "d:b")]))
        res = self._go([D("frame", "p:b", [P("p:t", "Ок")], box=box(0, 0, 80, 30), tag="div")], matched=low(0), sources={"a.tsx": "export {};\n"}, react=True)
        self.assertEqual((res["findings"], dcodes(res)), ([], [("DS_COMPONENT_MISSED", "d:b")]))

    def test_no_resolution_map_means_identity_did_not_run(self):
        d = screen([btn_design()["children"][0], D("rect", "d:r", box=box(0, 40, 10, 10), style={"background": "rgba(1, 1, 1, 1)"})])
        res = run(d, page([comp("p:b", ["Card"], [P("p:t", "Ок")])]), None, {"a.tsx": APP})
        self.assertFalse(res["ran"], "без плана нечего держать против: ни компонентов, ни токенов")
        d["children"][0]["tokens"] = {"fill": "T"}
        d["children"][0]["style"] = {"background": "rgba(1, 2, 3, 1)"}
        res = run(d, page([comp("p:b", ["Card"], [P("p:t", "Ок")], style={"background": "rgba(1, 2, 3, 1)"})]), None, {"a.tsx": APP})
        self.assertTrue(res["ran"])
        self.assertFalse(res["parts"]["identity"]["ran"])
        self.assertIn("карты резолюций нет", res["parts"]["identity"]["reason"])
        self.assertNotIn("DS_COMPONENT_WRONG", [f["code"] for f in res["findings"]], "идентичность не угадывается")

    def test_prod_build_without_fibers_means_identity_did_not_run(self):
        res = self._go([D("frame", "p:b", [P("p:t", "Ок")], box=box(0, 0, 80, 30), tag="div")], react=False,
                       sources={"a.tsx": "export {};\n"})
        self.assertFalse(res["parts"]["identity"]["ran"])
        self.assertIn("react.found: false", res["parts"]["identity"]["reason"])
        self.assertEqual(res["findings"], [])

    def test_plan_of_another_frame_degrades(self):
        plan = plan_of({"id": "d:b", "component": "Button"}, {"id": "other:frame:9", "component": "Card"}, {"id": "d:x"}, "мусор")
        res = self._go([comp("p:b", ["Button"], [P("p:t", "Ок")])], plan=plan)
        self.assertEqual(len(res["degraded"]), 1)
        self.assertIn("другого кадра", res["degraded"][0]["reason"])
        self.assertTrue(res["parts"]["identity"]["ran"], "известные записи всё равно работают")

    def test_local_and_ignore_decisions_are_not_checked(self):
        plan = plan_of({"id": "d:b", "resolution": "local"})
        res = self._go([comp("p:b", ["Card"], [P("p:t", "Ок")])], plan=plan)
        self.assertEqual(res["ran"], False)

    def test_node_missing_on_the_page_is_the_matcher_business(self):
        res = self._go([P("p:x", "Совсем другое")])
        self.assertEqual([c for c in codes(res) if c[0].startswith("DS_COMPONENT")], [])


def code_run(source, lib=None, plan=None, extra=None):
    d = btn_design()
    plan = plan if plan is not None else plan_of({"id": "d:b", "component": "Button"})
    return run(d, page([comp("p:b", ["Button"], [P("p:t", "Ок")])]), plan, {"a.tsx": source, **(extra or {})}, lib=lib)


HDR = 'import { Button, OldTag, Card } from "@corp/ui";\n'


class CodeScan(unittest.TestCase):
    def test_value_outside_enum_options_cites_the_requirement(self):
        res = code_run(HDR + 'export const A = () => <Button size="xl">Ок</Button>;\n')
        self.assertEqual(codes(res), [("DS_PROP_WRONG", "size")])
        f = res["findings"][0]
        self.assertEqual((f["file"], f["line"], f["expected"], f["actual"]), ("a.tsx", 2, ["sm", "md"], "xl"))
        self.assertIn("contract.components.Button.props.size", f["rule"])

    def test_accepted_values_are_silent(self):
        res = code_run(HDR + 'export const A = () => <Button size="sm" disabled label="что угодно">Ок</Button>;\n')
        self.assertEqual(res["findings"], [])

    def test_boolean_prop_wants_a_boolean(self):
        res = code_run(HDR + 'export const A = () => <Button disabled="yes">Ок</Button>;\n')
        self.assertEqual(codes(res), [("DS_PROP_WRONG", "disabled")])
        self.assertEqual(code_run(HDR + 'export const A = () => <Button disabled={false}>Ок</Button>;\n')["findings"], [])

    def test_prop_outside_closed_contract_is_wrong_open_contract_is_a_dismissal(self):
        res = code_run(HDR + 'export const A = () => <Button kind="primary">Ок</Button>;\n')
        self.assertEqual(codes(res), [("DS_PROP_WRONG", "kind")])
        self.assertIn("propsClosed", res["findings"][0]["rule"])
        res = code_run(HDR + 'export const A = () => <Card kind="primary">Ок</Card>;\n')
        self.assertEqual((res["findings"], dcodes(res)), ([], [("DS_PROP_WRONG", "kind")]))
        self.assertIn("propsClosed: false", res["dismissals"][0]["reason"])

    def test_handlers_aria_data_and_service_props_are_not_outside_the_contract(self):
        src = HDR + 'export const A = () => <Button onClick={f} aria-label="x" data-id="1" key="k" className="c" style={s}>Ок</Button>;\n'
        self.assertEqual(code_run(src)["findings"], [])

    def test_dynamic_enum_value_is_not_judged(self):
        res = code_run(HDR + 'export const A = () => <Button size={sz}>Ок</Button>;\n')
        self.assertEqual((res["findings"], dcodes(res)), ([], [("DS_PROP_WRONG", "size")]))
        self.assertIn("статически", res["dismissals"][0]["reason"])

    def test_constant_resolved_in_the_file_is_judged(self):
        res = code_run(HDR + 'const S = "xl";\nexport const A = () => <Button size={S}>Ок</Button>;\n')
        self.assertEqual(codes(res), [("DS_PROP_WRONG", "size")])

    def test_non_enum_kinds_are_never_judged(self):
        self.assertEqual(code_run(HDR + 'export const A = () => <Button label="совсем новая">Ок</Button>;\n')["findings"], [])

    def test_spread_gives_no_values(self):
        self.assertEqual(code_run(HDR + 'export const A = () => <Button {...rest}>Ок</Button>;\n')["findings"], [])

    def test_namespace_import_is_checked_homemade_is_not(self):
        ns = 'import * as Ui from "@corp/ui";\nexport const A = () => <Ui.Button size="xl">Ок</Ui.Button>;\n'
        self.assertEqual(codes(code_run(ns)), [("DS_PROP_WRONG", "size")])
        own = 'import { Button } from "./my/Button";\nexport const A = () => <Button size="xl">Ок</Button>;\n'
        self.assertEqual(code_run(own)["findings"], [], "одноимённый самодельный контракту не подчиняется")

    def test_deprecated_component_and_prop(self):
        res = code_run(HDR + 'export const A = () => <OldTag tone="a">x</OldTag>;\n')
        self.assertEqual(sorted((f["code"], f["prop"]) for f in res["findings"]), [("DS_DEPRECATED", "OldTag"), ("DS_DEPRECATED", "tone")])
        comp_f = [f for f in res["findings"] if f["prop"] == "OldTag"][0]
        self.assertIn("замена Tag", comp_f["message"])
        self.assertIn("deprecations[component]", comp_f["rule"])

    def test_foreign_deprecation_prose_does_not_taint_the_component(self):
        res = code_run(HDR + 'export const A = () => <Card>x</Card>;\n')
        self.assertEqual(res["findings"], [], "в прозе Card упомянут чужой deprecated — сам Card не deprecated")

    def test_plan_chose_the_deprecated_component_is_a_dismissal(self):
        d = screen([inst("d:o", "OldTag", comp={"origin": "library", "sized": "own", "textProps": {"l": "x"}}, box=box(0, 0, 80, 30))])
        res = run(d, page([comp("p:o", ["OldTag"], [P("p:t", "x")])]), plan_of({"id": "d:o", "component": "OldTag"}),
                  {"a.tsx": HDR + 'export const A = () => <OldTag>x</OldTag>;\n'})
        self.assertEqual([c for c in codes(res) if c[0] == "DS_DEPRECATED" and c[1] == "OldTag"], [])
        self.assertIn(("DS_DEPRECATED", "OldTag"), dcodes(res))

    def test_every_finding_carries_its_rule(self):
        res = code_run(HDR + 'export const A = () => <><Button size="xl" kind="x">Ок</Button><OldTag tone="a" /></>;\n')
        self.assertGreaterEqual(len(res["findings"]), 4)
        self.assertTrue(all(f.get("rule") for f in res["findings"]))
        self.assertEqual({f["layer"] for f in res["findings"]}, {"ds"})


def token_design(color="rgba(0, 120, 255, 1)", tokens=None):
    n = D("frame", "d:f", [T("d:t", "Привет")], box=box(0, 0, 100, 40), style={"background": color}, tokens=tokens if tokens is not None else {"fill": "Brand/Primary"})
    return screen([n])


def token_page(color="rgba(0, 120, 255, 1)"):
    return page([D("frame", "p:f", [P("p:t", "Привет")], box=box(0, 0, 100, 40), tag="div", style={"background": color})])


TOK_PLAN = plan_of({"id": "d:f", "resolution": "local"})
TSX = 'export const A = () => <div style={{ background: "#0078ff" }} />;\n'


class Tokens(unittest.TestCase):
    def _go(self, src, files=None, colors=None, colors_file=None, design=None):
        return run(design or token_design(), token_page(), TOK_PLAN, {"a.tsx": src, **(files or {})}, colors=colors, colors_file=colors_file)

    def test_literal_where_the_mockup_binds_a_token(self):
        res = self._go(TSX, colors={"rgba(0, 120, 255, 1)": "@brand"})
        self.assertEqual(codes(res), [("DS_TOKEN_MISSED", "d:f")])
        f = res["findings"][0]
        self.assertEqual((f["file"], f["line"], f["expected"], f["actual"]), ("a.tsx", 1, "Brand/Primary", "#0078ff"))
        self.assertIn("@brand", f["message"])
        self.assertIn("design token Brand/Primary (fill)", f["rule"])

    def test_reference_to_a_token_is_silent(self):
        res = self._go('export const A = () => <div style={{ background: "var(--brand)" }} />;\n', files={"a.less": ".x { color: @brand; }\n"})
        self.assertEqual((res["findings"], res["dismissals"]), ([], []))

    def test_fallback_inside_var_is_a_dismissal(self):
        res = self._go('export const A = () => <div style={{ background: "var(--brand, #0078ff)" }} />;\n')
        self.assertEqual((res["findings"], dcodes(res)), ([], [("DS_TOKEN_MISSED", "d:f")]))
        self.assertIn("var(--", res["dismissals"][0]["reason"])

    def test_near_but_not_equal_is_off_scale(self):
        res = self._go('export const A = () => <div style={{ background: "#0078fa" }} />;\n')
        self.assertEqual(codes(res), [("DS_OFF_SCALE", "d:f")])
        far = self._go('export const A = () => <div style={{ background: "#ff0000" }} />;\n')
        self.assertEqual(far["findings"], [], "далёкий цвет — не «мимо шкалы», это вопрос правила COLOR")
        edge = self._go('export const A = () => <div style={{ background: "#0080ff" }} />;\n')           # зелёный канал +8: на грани допуска
        self.assertEqual(codes(edge), [("DS_OFF_SCALE", "d:f")])
        beyond = self._go('export const A = () => <div style={{ background: "#0081ff" }} />;\n')         # +9: уже не «рядом»
        self.assertEqual(beyond["findings"], [])

    def test_style_files_are_scanned_but_the_colors_file_itself_is_not(self):
        res = self._go("export {};\n", files={"src/ui.less": ".x { background: #0078ff; }\n", "src/colors.less": "@brand: #0078ff;\n"},
                       colors_file="src/colors.less")
        self.assertEqual([(f["file"], f["line"]) for f in res["findings"]], [("src/ui.less", 1)])

    def test_text_and_stroke_slots(self):
        n = D("text", "d:t", text="Привет", box=box(0, 0, 60, 14), color="rgba(10, 20, 30, 1)", tokens={"text": "Text/Main"})
        res = run(screen([n]), page([P("p:t", "Привет")]), plan_of({"id": "d:t", "resolution": "local"}),
                  {"a.tsx": 'export const A = () => <p style={{ color: "#0a141e" }}>x</p>;\n'})
        self.assertEqual(codes(res), [("DS_TOKEN_MISSED", "d:t")])
        self.assertIn("(text)", res["findings"][0]["rule"])

    def test_node_missing_on_the_page_is_not_judged(self):
        res = run(token_design(), page([P("p:x", "Совсем другое")]), TOK_PLAN, {"a.tsx": TSX})
        self.assertEqual(res["findings"], [])

    def test_no_tokens_in_the_mockup_means_the_part_did_not_run(self):
        res = run(btn_design(), page([comp("p:b", ["Button"], [P("p:t", "Ок")])]), plan_of({"id": "d:b", "component": "Button"}), {"a.tsx": APP})
        self.assertFalse(res["parts"]["tokens"]["ran"])


class Scanner(unittest.TestCase):
    SRC = ('import React from "react";\nimport { Button, Modal as Dlg } from "@corp/ui";\nimport * as Ui from "@corp/ui";\n'
           'import Label from "./Label";\nconst SIZE = "lg";\n// color: #123456 in a comment\n'
           'export const A = () => (\n  <div style={{ color: "#fff" }}>\n'
           '    <Button size="md" disabled onClick={f} variant={SIZE} {...rest}>Ok</Button>\n    <Ui.Tag kind={k}>x</Ui.Tag>\n'
           '    {cond && <Dlg title="t" />}\n    <svg fill="rgba(1, 2, 3, 0.5)" stroke="var(--x, #abc)" />\n  </div>\n);\n')

    def setUp(self):
        self.sc = TS.scan_source("src/A.tsx", self.SRC)

    def test_imports(self):
        imp = self.sc["imports"]
        self.assertEqual((imp["Button"]["from"], imp["Button"]["named"]), ("@corp/ui", "Button"))
        self.assertEqual(imp["Dlg"]["named"], "Modal")
        self.assertTrue(imp["Ui"]["ns"])
        self.assertTrue(imp["Label"]["default"])

    def test_usages_with_lines_literals_and_dynamic(self):
        u = {x["tag"]: x for x in self.sc["usages"]}
        self.assertEqual(sorted(u), ["Button", "Dlg", "Ui.Tag"])
        b = u["Button"]
        self.assertEqual((b["line"], b["spread"]), (9, True))
        self.assertEqual({p["name"]: (p["kind"], p.get("value")) for p in b["props"]},
                         {"size": ("literal", "md"), "disabled": ("literal", True), "onClick": ("dynamic", None), "variant": ("literal", "lg")})
        self.assertEqual(u["Dlg"]["line"], 11, "JSX внутри {cond && …} найден")
        self.assertEqual(u["Ui.Tag"]["props"][0]["kind"], "dynamic")

    def test_colors_skip_comments_and_mark_the_var_fallback(self):
        got = [(c["raw"], c["line"], c["fallback"]) for c in sorted(self.sc["colors"], key=lambda c: (c["line"], c["col"]))]
        self.assertEqual(got, [("#fff", 8, False), ("rgba(1, 2, 3, 0.5)", 12, False), ("#abc", 12, True)])
        self.assertEqual(self.sc["colors"][0]["value"], "rgba(255, 255, 255, 1)")

    def test_style_files_give_colors_without_comments(self):
        got = TS.scan_style("a.less", "@a: #FFF; /* #000 */ .x{ color: rgba(1,2,3,.5); }\n// #111\n")["colors"]
        self.assertEqual([(c["raw"], c["line"]) for c in got], [("#FFF", 1), ("rgba(1,2,3,.5)", 1)])

    def test_resolve_tag(self):
        imp = self.sc["imports"]
        self.assertEqual(TS.resolve_tag("Ui.Tag", imp)["kind"], "namespace")
        self.assertEqual(TS.resolve_tag("Label", imp)["from"], "./Label")
        self.assertIsNone(TS.resolve_tag("Local", imp))

    def test_broken_file_does_not_raise(self):
        sc = TS.scan_source("b.tsx", "export const A = () => <div <<<< \n const = ;\n")
        self.assertIsInstance(sc["usages"], list)

    def test_ids_in_markup_are_not_colors(self):
        sc = TS.scan_source("c.tsx", 'export const A = () => <a href="#add" id="#abc1234" title="&#123;">x</a>;\n')
        self.assertEqual(sc["colors"], [])


class Gather(unittest.TestCase):
    """Чтение с диска на настоящем треке: план, build-state, outputs, требования ДС, карта цветов."""

    def test_gather_reads_the_real_files(self):
        import test_build_state as TB
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            TB.make_world(root)
            TB.write_ds(root, ["Button"])
            TB.walk(root)
            (root / "src" / "modules" / "p1.tsx").write_text(HDR + 'export const A = () => <Button size="xl" />;\n', encoding="utf-8")
            got = G.gather(root, TB.TRACK)
            self.assertIsNotNone(got["lib"])
            self.assertIsNone(got["libProblem"])
            self.assertIsInstance(got["plan"], dict)
            self.assertEqual(sorted(got["sources"]), ["src/modules/p1.tsx", "src/modules/p2.tsx"])
            self.assertIn("Button", got["sources"]["src/modules/p1.tsx"])
            self.assertIn("sections", got["plan"])

    def test_missing_requirements_are_named_not_raised(self):
        import test_build_state as TB
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            TB.make_world(root)
            got = G.gather(root, TB.TRACK)
            self.assertIsNone(got["lib"])
            self.assertIn("требований ДС нет", got["libProblem"])
            self.assertTrue(any("нет build-state" in n for n in got["notes"]))

    def test_unknown_track_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                G.gather(Path(tmp), "NOPE-1")


class Misc(unittest.TestCase):
    def test_bad_input(self):
        with self.assertRaises(G.DsGateError):
            G.run(btn_design(), page([]), {"nope": 1}, None, library(), {"a.tsx": APP})
        with self.assertRaises(G.DsGateError):
            G.run({"x": 1}, page([]), {"pairs": []}, None, library(), {"a.tsx": APP})

    def test_plan_decisions_reader(self):
        plan = plan_of({"id": "a", "component": " Button "}, {"id": "b", "resolution": "ignore"}, {"id": "c", "resolution": "weird"}, {"id": "zz", "component": "X"}, {})
        got, foreign = G.plan_decisions(plan, {"a", "b", "c"})
        self.assertEqual(got, {"a": {"decision": "component", "component": "Button"}, "b": {"decision": "ignore", "component": None}})
        self.assertEqual(foreign, ["zz"])
        self.assertEqual(G.plan_decisions("мусор", {"a"}), ({}, []))

    def test_deterministic(self):
        a = code_run(HDR + 'export const A = () => <><Button size="xl" kind="x">Ок</Button><OldTag tone="a" /></>;\n')
        b = code_run(HDR + 'export const A = () => <><Button size="xl" kind="x">Ок</Button><OldTag tone="a" /></>;\n')
        self.assertEqual(json.dumps(a, sort_keys=True, ensure_ascii=False), json.dumps(b, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
