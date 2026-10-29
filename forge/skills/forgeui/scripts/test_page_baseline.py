"""test_page_baseline.py — правка существующей страницы, написанной не forge-ui (задача 6.4, спека §12.2, §13.7 page, §14.4.6 baseline.standing,
§20.4 proposeSources; INV-C18, C19, F18; QG-U22 (вторая половина: sources-location, standing, scope), P4).

Пинит на синтетической «ручной» странице заказов (React-файл, написанный людьми, и снимок её DOM) и макете с изменением: текст подзаголовка
другой (changed), блока фильтра на странице нет (added), баннера акции в макете нет (removed), цвет заголовка на странице давно не тот
(старое расхождение):
  • `daiver_propose_sources` находит файл страницы по текстам (ранг — число разных текстов), маршрут по адресу кадра, словари текстов;
    каталоги зависимостей не обходит; `daiver_track_manifest` записывает sources и scope, `baselineSkip` — только у страницы;
  • захват базы — ровно `<кадр>/baseline`, только у кадра с sources и только до начала сборки; `daiver_verify_compare` туда пишет
    `baseline {kind: page}`, `daiver_code_compare` — копии файлов; контракт плана читает это (и пропуск — как принятую деградацию);
  • регионы — из разрыва базы против ТЕКУЩЕГО канона: added / changed / removed, location own | inside | ancestor с `file:line…endLine`;
    старые расхождения — preExisting при scope change и регионы при scope page; outline / section / план / build-state идут по регионам,
    у плана нет модуля и пролога; без базы outline отказывает (кроме записанного пропуска — тогда весь кадр);
  • INV-C19: дифф файлов страницы с копией базы вне регионов — отказ гейта сборки с `file:line`; правка в регионе, в import и новые операторы
    верхнего уровня — можно;
  • standing каждого дефекта (DOM и кода): change / pre-existing / regression по ключу (code, designId, prop) / (code, элемент), не по id;
    scope change — pre-existing не блокирует (итерация чистая), scope page — блокирует; регрессия блокирует всегда; три числа в nextStep.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_build_state as TB  # noqa: E402
from daiver import build_gate as BG  # noqa: E402
from daiver import page_base as PB  # noqa: E402
from daiver import plan_digest as PD  # noqa: E402
from daiver import sources as SRC  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver import track as TR  # noqa: E402
from daiver.verify import state as VS  # noqa: E402
from test_verify_compare import snap_node  # noqa: E402

TRACK = TB.TRACK
AUTO = TB.autoname
PAGE = "src/pages/Orders.tsx"


def design():
    """Макет страницы заказов: заголовок, подзаголовок (новый текст), список, фильтр (новый блок)."""
    return TB.frame("root", [
        TB.leaf("title", name="title", text="Мои заказы", color="rgba(17, 17, 17, 1)"),
        TB.leaf("sub", name="sub", text="Все заказы за месяц"),
        TB.frame("list", [TB.leaf("i1", name="i1", text="Заказ номер один"), TB.leaf("i2", name="i2", text="Заказ номер два")],
                 name=AUTO("list"), layout={"direction": "column", "gap": 4}),
        TB.frame("filter", [TB.leaf("ft", name="ft", text="Фильтр по дате")], name=AUTO("filter"), layout={"direction": "row", "gap": 4}),
    ], name=AUTO("root"), layout={"direction": "column", "gap": 8})


def page(sub="Все заказы за год", banner=True, filt=False, item2="Заказ номер два", title_color="rgb(200, 0, 0)"):
    """Снимок DOM страницы заказов (формат daiver_screenshot)."""
    y = [0]

    def t(i, tag, text, **kw):
        y[0] += 20
        return snap_node(i, "text", tag=tag, text=text, box={"x": 0, "y": y[0], "w": 200, "h": 16}, **kw)
    kids = [t("dom:h1", "h1", "Мои заказы", color=title_color), t("dom:p1", "p", sub)]
    if banner:
        kids.append(t("dom:p2", "p", "Старый баннер акции"))
    kids.append(snap_node("dom:ul", "frame", [t("dom:li1", "li", "Заказ номер один"), t("dom:li2", "li", item2)], tag="ul",
                          box={"x": 0, "y": 80, "w": 200, "h": 40}))
    if filt:
        kids.append(snap_node("dom:f", "frame", [t("dom:ft", "span", "Фильтр по дате")], tag="div", box={"x": 0, "y": 130, "w": 200, "h": 20}))
    div = snap_node("dom:div", "frame", kids, tag="div", box={"x": 0, "y": 0, "w": 400, "h": 200})
    root = snap_node("dom:0", "frame", [div], tag="body", box={"x": 0, "y": 0, "w": 400, "h": 300})
    n = json.dumps(root).count('"id"')
    return {"v": 1, "root": root, "elements": n, "domElements": n, "react": {"found": False},
            "viewport": {"width": 400, "height": 300}, "capture": {"ready": True, "readiness": {"ready": True}}}


HAND = """import React from "react";

export default function Orders() {
  return (
    <div className="orders">
      <h1>Мои заказы</h1>
      <p>Все заказы за год</p>
      <p>Старый баннер акции</p>
      <ul>
        <li>Заказ номер один</li>
        <li>Заказ номер два</li>
      </ul>
    </div>
  );
}
"""

BUILT = """import React from "react";

export default function Orders() {
  return (
    <div className="orders">
      <h1>Мои заказы</h1>
      <p>Все заказы за месяц</p>
      <ul>
        <li>Заказ номер один</li>
        <li>Заказ номер два</li>
      </ul>
      <div className="filter">
        <span>Фильтр по дате</span>
      </div>
    </div>
  );
}
"""


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        TB.init_repo(self.root)
        TB.build_repo(self.root, design())
        self.put(PAGE, HAND)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def call(self, name, **args):
        return T.call(name, self.root, dict({"track": TRACK}, **args))

    def refusal(self, name, **args):
        with self.assertRaises(T.ToolError) as cm:
            self.call(name, **args)
        return str(cm.exception)

    @property
    def manifest(self):
        return TR.load(self.root, TRACK)

    @property
    def paths(self):
        return TR.frame_paths(self.root, self.manifest, "main")

    def rel(self, p):
        return p.relative_to(self.root).as_posix()

    def declare(self, scope="change"):
        self.call("daiver_track_manifest", frame="main", set={"sources": [PAGE], "scope": scope})

    def snap(self, folder, snapshot):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
        return self.rel(folder)

    def capture(self, snapshot=None, code=True):
        base = self.snap(self.paths["baseline"], snapshot or page())
        vc = self.call("daiver_verify_compare", iterationDir=base)
        cc = self.call("daiver_code_compare", iterationDir=base) if code else None
        return vc, cc

    def plan(self):
        ol = self.call("daiver_outline")
        self.call("daiver_resolve_report")
        self.call("daiver_plan_write", weightBudget=ol["weightBudget"])
        return ol

    def build(self, text=BUILT):
        bs = self.call("daiver_build_state")
        budget = bs["weightBudget"]
        while bs["next"] is not None:
            sid = bs["next"]["sectionId"]
            self.call("daiver_build_state_set", sectionId=sid, status="in_progress")
            h = self.call("daiver_section", sectionId=sid, weightBudget=budget)["section"]["hash"]
            bs = self.call("daiver_build_state_set", sectionId=sid, status="done", outputs=[PAGE], hash=h)
        self.put(PAGE, text)

    def verify(self, name, snapshot):
        it = self.snap(self.paths["verify"] / name, snapshot)
        return self.call("daiver_verify_compare", iterationDir=it), it

    def ready(self, scope="change"):
        """Страница заявлена, база снята, план написан."""
        self.declare(scope)
        self.capture()
        return self.plan()


# ── поиск файлов страницы ─────────────────────────────────────────────────────

class ProposeSources(World):
    def test_the_page_file_is_found_by_its_texts_and_ranked_first(self):
        self.put("src/pages/Other.tsx", "export const X = () => <p>Мои заказы</p>;\n")
        self.put("node_modules/lib/Orders.tsx", "<p>Мои заказы</p><p>Заказ номер один</p><p>Заказ номер два</p><p>Все заказы за месяц</p>")
        self.put("src/i18n/ru.json", json.dumps({"orders.title": "Фильтр по дате"}, ensure_ascii=False))
        got = self.call("daiver_propose_sources")
        self.assertEqual([c["file"] for c in got["candidates"]], [PAGE, "src/pages/Other.tsx"], "ранг — число разных текстов; node_modules мимо")
        top = got["candidates"][0]
        self.assertEqual(top["texts"], 3)
        self.assertEqual(top["found"][0], {"text": "Мои заказы", "line": 6})
        self.assertEqual([x["file"] for x in got["textFiles"]], ["src/i18n/ru.json"], "словари текстов — отдельно: страница их импортирует")
        self.assertIn("прочитай", got["next"])
        self.assertEqual(got, self.call("daiver_propose_sources"), "один вход — один ответ")

    def test_short_texts_are_not_searched_and_the_router_is_a_witness(self):
        self.assertEqual(SRC.MIN_TEXT_LENGTH, 3)
        self.assertEqual((SRC.LIMIT, SRC.MAX_FILES), (8, 3000))
        self.call("daiver_track_manifest", frame="main", set={"url": "http://localhost:3000/orders/42?tab=all"})
        self.put("src/router.tsx", "const routes = [{ path: '/orders/:id', element: <Orders /> }];\n")
        self.put("src/api.ts", "fetch('/orders')\n")
        got = self.call("daiver_propose_sources")
        self.assertEqual(got["routes"], [{"file": "src/router.tsx", "line": 1, "literal": "'/orders/:id'"}])
        self.assertIsNone(SRC.route_pattern("http://localhost:3000/"), "по корню искать нечего")
        self.assertTrue(SRC.route_pattern("/a/b").search("x = '/a/b'"))
        self.assertEqual(SRC.page_texts(TB.frame("r", [TB.leaf("a", text="ок"), TB.leaf("b", text="Да  да"), TB.leaf("c", text="Да да")])), ["Да да"])

    def test_nothing_found_says_the_page_probably_does_not_exist(self):
        (self.root / PAGE).unlink()
        got = self.call("daiver_propose_sources")
        self.assertEqual(got["candidates"], [])
        self.assertIn("страницы ещё нет", got["next"])

    def test_the_walk_stops_at_max_files_and_says_so(self):
        old = SRC.MAX_FILES
        SRC.MAX_FILES = 1
        try:
            self.put("src/a.ts", "x")
            got = self.call("daiver_propose_sources")
        finally:
            SRC.MAX_FILES = old
        self.assertTrue(got["truncated"])
        self.assertIn("обрезан", got["next"])


class Manifest(unittest.TestCase):
    def frame(self, **kw):
        f = {"name": "main", "role": "screen", "dir": ".", "frameUrl": "u", "requirement": {"kind": "pixso", "path": "r.json", "sha256": "a" * 64, "pulledAt": "t"}}
        f.update(kw)
        return {"schema": 1, "track": "T-1", "frames": [f]}

    def test_page_baseline_and_skip_need_sources(self):
        self.assertEqual(TR.validate(self.frame(sources=["a.tsx"], scope="change", baseline={"kind": "page"})), [])
        self.assertTrue(any("только у кадра с sources" in p for p in TR.validate(self.frame(baseline={"kind": "page"}))))
        self.assertTrue(any("baselineSkip" in p for p in TR.validate(self.frame(baselineSkip="нет стенда"))))
        self.assertTrue(any("уже захвачена" in p for p in TR.validate(self.frame(sources=["a.tsx"], baseline={"kind": "page"}, baselineSkip="x"))))
        self.assertTrue(any("одной строкой" in p for p in TR.validate(self.frame(sources=["a.tsx"], baselineSkip="a\nb"))))
        self.assertTrue(any("kind: page" in p for p in TR.validate(self.frame(sources=["a.tsx"], baseline={"kind": "page", "x": 1}))))

    def test_is_page_excludes_revisions_and_states(self):
        self.assertTrue(TR.is_page({"sources": ["a"]}))
        self.assertFalse(TR.is_page({"sources": ["a"], "baseline": {"kind": "version", "version": 1}}))
        self.assertFalse(TR.is_page({"sources": ["a"], "stateOf": "x"}))
        self.assertFalse(TR.is_page({}))


# ── захват базы ───────────────────────────────────────────────────────────────

class Capture(World):
    def test_only_a_frame_with_sources_captures_and_only_into_the_baseline_folder_itself(self):
        base = self.snap(self.paths["baseline"], page())
        self.assertIn("нет sources", self.refusal("daiver_verify_compare", iterationDir=base))
        self.assertIn("нет sources", self.refusal("daiver_code_compare", iterationDir=base))
        self.declare()
        sub = self.snap(self.paths["baseline"] / "v1", page())
        self.assertIn("iterationDir", self.refusal("daiver_verify_compare", iterationDir=sub))

    def test_the_capture_records_the_page_baseline_and_copies_the_files(self):
        self.declare()
        vc, cc = self.capture()
        self.assertEqual(vc["status"], "defects", "база — разрыв страницы с макетом")
        self.assertEqual(vc["pageBaseline"], {"recorded": True, "written": True, "baseline": {"kind": "page"}})
        self.assertIn("daiver_code_compare", vc["nextStep"])
        self.assertEqual(TR.get_frame(self.manifest, "main")["baseline"], {"kind": "page"})
        copy = self.paths["baseline"] / PB.SOURCES_DIR / PAGE
        self.assertEqual(copy.read_text(encoding="utf-8"), HAND)
        idx = json.loads((self.paths["baseline"] / PB.SOURCES_INDEX).read_text(encoding="utf-8"))
        self.assertEqual([x["path"] for x in idx["files"]], [PAGE])
        self.assertIn("копии", cc["pageBaseline"])
        self.assertNotIn("standing", vc, "у базы standing нет — она сама ключи")

    def test_no_capture_after_the_build_started(self):
        self.ready()
        bs = self.call("daiver_build_state")
        self.call("daiver_build_state_set", sectionId=bs["next"]["sectionId"], status="in_progress")
        base = self.rel(self.paths["baseline"])
        for tool in ("daiver_verify_compare", "daiver_code_compare"):
            self.assertIn("ДО правок", self.refusal(tool, iterationDir=base))
        self.assertEqual((self.paths["baseline"] / PB.SOURCES_DIR / PAGE).read_text(encoding="utf-8"), HAND, "прежняя база остаётся")

    def test_a_hard_gate_failure_records_nothing(self):
        self.declare()
        bad = page()
        bad["root"]["children"][0]["children"] = [snap_node("dom:x", "text", tag="p", text="Совсем другая страница")]
        vc, _cc = self.capture(bad, code=False)
        self.assertEqual(vc["status"], "hard-gate-fail")
        self.assertEqual(vc["pageBaseline"], {"recorded": False})
        self.assertNotIn("baseline", TR.get_frame(self.manifest, "main"))

    def test_the_contract_reads_the_capture_and_the_open_skip(self):
        self.declare()
        it = [i for i in self.call("daiver_plan_contract")["open"] if i["id"] == "baseline"][0]
        self.assertTrue(it["blocking"])
        self.assertIn("baselineSkip", it["record"])
        self.capture(code=False)
        it = [i for i in self.call("daiver_plan_contract")["open"] if i["id"] == "baseline"][0]
        self.assertIn("неполна", it["detail"], "без копий файлов база не целиком")
        self.capture()
        self.assertFalse([i for i in self.call("daiver_plan_contract")["open"] if i["id"] == "baseline"])

    def test_removing_the_sources_drops_the_page_baseline(self):
        self.declare()
        self.capture()
        self.call("daiver_track_manifest", frame="main", set={"sources": None, "scope": None})
        f = TR.get_frame(self.manifest, "main")
        self.assertNotIn("baseline", f)
        self.assertNotIn("sources", f)


# ── регионы, место в коде, план ───────────────────────────────────────────────

class Regions(World):
    def test_regions_come_from_the_gap_with_their_place_in_the_code(self):
        self.declare()
        self.capture()
        fb = self.call("daiver_frame_baseline")
        self.assertTrue(fb["available"])
        got = {r["id"]: (r["kind"], r["location"]["kind"], r["location"]["line"], r["location"]["endLine"]) for r in fb["regions"]}
        self.assertEqual(got, {"changed:sub": ("changed", "own", 7, 7), "removed:dom:p2": ("removed", "own", 8, 8),
                               "added:filter": ("added", "ancestor", 5, 13)})
        self.assertEqual(fb["counts"], {"added": 1, "changed": 1, "removed": 1, "preExisting": 1})
        self.assertEqual(fb["preExisting"]["sample"], [{"code": "COLOR", "prop": "color", "design": "title", "element": "Мои заказы"}])
        self.assertEqual(fb["outsideEdits"], {"checked": True, "count": 0, "edits": []})
        self.assertIn("scope page", fb["next"])
        self.assertEqual(fb, self.call("daiver_frame_baseline"), "один вход — один ответ")

    def test_scope_page_makes_the_old_deviations_regions_too(self):
        self.declare("page")
        self.capture()
        fb = self.call("daiver_frame_baseline")
        self.assertIn("changed:title", [r["id"] for r in fb["regions"]])
        self.assertEqual(fb["counts"]["preExisting"], 0)

    def test_regions_follow_the_current_canon_not_the_one_of_the_capture(self):
        self.declare()
        self.capture()
        TB.set_canon(self.root, TB.frame("root", design()["children"][:3], name=AUTO("root"), layout={"direction": "column", "gap": 8}))
        ids = [r["id"] for r in self.call("daiver_frame_baseline")["regions"]]
        self.assertNotIn("added:filter", ids, "фильтр из макета убрали — регион ушёл без перезахвата")

    def test_outline_refuses_without_a_base_and_builds_the_whole_frame_with_an_open_skip(self):
        self.declare()
        err = self.refusal("daiver_outline")
        self.assertIn("база недоступна", err)
        self.assertIn("baselineSkip", err)
        self.call("daiver_track_manifest", frame="main", set={"baselineSkip": "страница работает только на стенде"})
        ol = self.call("daiver_outline")
        self.assertIn("стенде", ol["page"]["skipped"])
        self.assertNotIn("deltaRoot", json.dumps(ol["sections"]))

    def test_outline_section_plan_and_build_state_go_by_the_regions(self):
        ol = self.ready()
        self.assertEqual(sorted(s["id"] for s in ol["sections"]), ["filter", "sub"])
        self.assertEqual(ol["page"]["removed"][0]["location"]["line"], 8)
        sec = self.call("daiver_section", sectionId="sub", weightBudget=ol["weightBudget"])
        self.assertEqual(sec["page"]["region"]["location"]["kind"], "own")
        self.assertEqual(sec["page"]["region"]["files"], [PAGE])
        self.assertIn("ЧУЖОМ коде", sec["next"])
        plan = json.loads(self.paths["plan"].read_text(encoding="utf-8"))
        self.assertEqual((plan["page"]["scope"], plan["page"]["baseline"], plan["page"]["preExisting"]), ("change", "page", 1))
        self.assertNotIn("module", plan)
        md = self.paths["planMd"].read_text(encoding="utf-8")
        self.assertIn("## Правка страницы", md)
        self.assertIn(f"`{PAGE}:8` (own)", md)
        self.assertIn("Пролога у правки страницы нет", md)
        self.assertNotIn("## Оболочка над секциями", md)
        bs = self.call("daiver_build_state")
        self.assertEqual(bs["page"]["removed"][0]["name"], "Старый баннер акции")
        self.assertIn("ДО правки", bs["page"]["note"])
        rows = PD._pages([{"name": "main", "plan": plan}])
        self.assertIn("| main | `src/pages/Orders.tsx` | change | снята | 1 / 1 / 1 | 1 |", rows)

    def test_a_page_frame_takes_no_placement(self):
        self.declare()
        self.capture()
        from daiver import plan as PL
        from daiver import resolve as RS
        ctx = RS.Ctx(self.root, TRACK, None)
        self.assertEqual(PL.module_of(ctx, None, None), (None, []))


# ── INV-C19: вне регионов дифф нулевой ────────────────────────────────────────

class OutsideRegions(World):
    def setUp(self):
        super().setUp()
        self.ready()
        self.build(BUILT)

    def edits(self):
        return PB.outside_edits(self.root, self.manifest, "main")["edits"]

    def test_the_change_built_inside_the_regions_passes_the_gate(self):
        self.assertEqual(self.edits(), [])
        self.assertEqual(BG.section_problems(self.root, TRACK), [])

    def test_an_edit_outside_the_regions_stops_the_gate_with_file_and_line(self):
        self.put(PAGE, BUILT.replace("<h1>Мои заказы</h1>", '<h1 className="title">Мои заказы</h1>'))
        probs = BG.section_problems(self.root, TRACK)
        self.assertEqual(len(probs), 1)
        self.assertIn(f"{PAGE}:6", probs[0])
        self.assertIn("INV-C19", probs[0])

    def test_imports_and_new_top_level_statements_are_not_outside_edits(self):
        text = BUILT.replace('import React from "react";', 'import React, { useState } from "react";\nimport { Filter } from "./Filter";')
        text += "\nexport function Helper() {\n  return null;\n}\n"
        self.put(PAGE, text)
        self.assertEqual(self.edits(), [])

    def test_reformatting_a_shared_line_and_deleting_the_file_are_caught(self):
        self.put(PAGE, BUILT.replace("        <li>Заказ номер два</li>", "        <li>Заказ  номер два</li>"))
        got = self.edits()
        self.assertEqual([(e["kind"], e["lines"]) for e in got], [("replace", [11, 11])])
        (self.root / PAGE).unlink()
        self.assertEqual(self.edits()[0]["kind"], "delete")

    def test_style_files_and_files_without_a_copy_are_listed_as_unchecked(self):
        self.put("src/pages/orders.less", ".orders { color: red; }\n")
        self.call("daiver_track_manifest", frame="main", set={"sources": [PAGE, "src/pages/orders.less"]})
        got = PB.outside_edits(self.root, self.manifest, "main")
        self.assertEqual(got["edits"], [])
        self.assertEqual(len(got["unchecked"]), 1)
        self.assertIn("копии в базе нет", got["unchecked"][0], "файл заявлен после захвата — сверить не с чем, и это сказано")
        shutil.copyfile(str(self.root / "src/pages/orders.less"), str(self.paths["baseline"] / PB.SOURCES_DIR / "src/pages/orders.less"))
        idx = self.paths["baseline"] / PB.SOURCES_INDEX
        doc = json.loads(idx.read_text(encoding="utf-8"))
        doc["files"].append({"path": "src/pages/orders.less", "sha256": "x", "bytes": 1})
        idx.write_text(json.dumps(doc), encoding="utf-8")
        got = PB.outside_edits(self.root, self.manifest, "main")
        self.assertEqual(len(got["unchecked"]), 1)
        self.assertIn("стили", got["unchecked"][0])

    def test_span_reaches_the_closing_tag(self):
        self.assertEqual(PB._span(BUILT, 12, 7), 14)
        self.assertEqual(PB._span(BUILT, 6, 11), 6, "не начало элемента — та же строка")


# ── standing ──────────────────────────────────────────────────────────────────

class Standing(World):
    def test_scope_change_old_deviations_do_not_block_the_iteration(self):
        self.ready()
        self.build()
        v, _it = self.verify("iter-1", page(sub="Все заказы за месяц", banner=False, filt=True))
        self.assertEqual(v["status"], "clean")
        self.assertEqual(v["page"], {"available": True, "standing": {"scope": "change", "change": 0, "preExisting": 1, "regression": 0}})
        self.assertEqual([(d["code"], d["standing"], d["scope"]) for d in v["defects"]], [("COLOR", "pre-existing", "change")])
        self.assertIn("старых расхождений страницы 1", v["nextStep"])
        self.assertIn("регрессий 0", v["nextStep"])

    def test_unbuilt_change_and_a_regression_block(self):
        self.ready()
        self.build()
        v, _it = self.verify("iter-1", page(sub="Все заказы за месяц", banner=True, filt=False, item2="Заказ номер 2"))
        self.assertEqual(v["status"], "defects")
        got = {(d["code"], d.get("design") or d["element"]): d["standing"] for d in v["defects"]}
        self.assertEqual(got, {("MISSING", "filter"): "change", ("EXTRA", "Старый баннер акции"): "change",
                               ("TEXT", "i2"): "regression", ("COLOR", "title"): "pre-existing"})
        self.assertEqual(v["counts"]["blocking"], 3)

    def test_scope_page_counts_the_old_deviations(self):
        self.ready()
        self.build()
        self.call("daiver_track_manifest", frame="main", set={"scope": "page"})
        v, _it = self.verify("iter-1", page(sub="Все заказы за месяц", banner=False, filt=True))
        self.assertEqual(v["status"], "defects")
        self.assertEqual([(d["code"], d["standing"]) for d in v["defects"]], [("COLOR", "change")], "при scope page цвет — регион")
        self.assertIn("scope page", v["nextStep"])

    def test_the_code_gate_classifies_its_defects_too(self):
        self.ready()
        self.build(BUILT.replace("<span>Фильтр по дате</span>", "<span>Фильтр</span>"))
        v, it = self.verify("iter-1", page(sub="Все заказы за месяц", banner=False, filt=True))
        cc = self.call("daiver_code_compare", iterationDir=it)
        self.assertEqual(cc["standing"]["scope"], "change")
        self.assertTrue(all("standing" in d for d in cc["defects"]))

    def test_classify_keys_never_use_the_defect_id(self):
        res = {"scope": "change", "_regionIds": {"r"}, "_removed": {"старый баннер"}}
        keys = PB.base_keys([{"code": "COLOR", "design": "a", "prop": "color"}], [{"code": "EXTRA", "element": "Лишнее"}])
        ds = [{"id": "x1", "code": "COLOR", "design": "a", "prop": "color"}, {"id": "x1", "code": "SIZE", "design": "a", "prop": "w"},
              {"id": "z", "code": "MISSING", "design": "r"}, {"id": "e1", "code": "EXTRA", "element": "Старый  баннер"},
              {"id": "e2", "code": "EXTRA", "element": "лишнее"}, {"id": "e3", "code": "EXTRA", "element": "Новое"}]
        st = PB.classify(ds, res, keys)
        self.assertEqual([d["standing"] for d in ds], ["pre-existing", "regression", "change", "change", "pre-existing", "regression"])
        self.assertEqual(st, {"scope": "change", "change": 2, "preExisting": 2, "regression": 2})
        self.assertIn("в регионах изменения 2", PB.standing_words(st))

    def test_the_verify_state_rule_reads_the_scope(self):
        base = {"severity": "critical", "zone": "deterministic"}
        self.assertFalse(VS.is_blocking(dict(base, standing="pre-existing", scope="change")))
        self.assertTrue(VS.is_blocking(dict(base, standing="pre-existing", scope="page")))
        self.assertTrue(VS.is_blocking(dict(base, standing="regression", scope="change")))
        self.assertTrue(VS.is_blocking(dict(base)))
        self.assertIn("scope", VS.KEEP)


if __name__ == "__main__":
    unittest.main()
