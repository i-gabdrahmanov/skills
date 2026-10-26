"""test_code_compare.py — код-гейт: исходники модуля против макета без браузера (задача 4.7, спека §14.5, INV-C29, F4, QG-U18, N13).

Пинит: React-адаптер `jsx_tree` на синтетических модулях (инлайн локальных компонентов со сплайсом `{children}`, подстановка
пропсов, таблицы `mocks.ts` — цепочки, индексы, шаблоны, `.map` с привязкой параметра, маркеры динамики вне дерева, компоненты пакетов —
чёрные ящики, защита от циклов, вход секции) и сверку `compare` (MISSING unconfirmed / corroborated / dismissed по парам DOM, маркеры →
unverified, `treeRecognized: false`, дедуп по id, редакторские ссылки, никаких числовых кодов) и `run` на настоящем треке со build-state
(skipped во всех названных случаях с причиной, артефакты, отказ чужой папки). Только разбор текста: ни сборки, ни node.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import canon as C  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver.verify import code_compare as CC  # noqa: E402
from daiver.verify import jsx_tree as JT  # noqa: E402
from test_match import D, T as TXT, box, inst, screen  # noqa: E402


def build(files, outputs=None, sid="s1"):
    return JT.build(files, [{"id": sid, "outputs": outputs or [r for r in files if r.endswith((".tsx", ".jsx"))]}])


def flat(n, out=None):
    out = out if out is not None else []
    out.append(n)
    for k in n.get("children") or []:
        flat(k, out)
    return out


def texts(model):
    return [n["text"] for n in flat(model["root"]) if n.get("text")]


def by_id(model, nid):
    return next(n for n in flat(model["root"]) if n["id"] == nid)


CARD = '''import { Button } from "@corp/ui";
import { MOCKS } from "./mocks";
import { Row } from "./Row";
function Panel({ title, children }) {
  return (<section><h2>{title}</h2>{children}</section>);
}
export default function Card() {
  return (
    <Panel title={MOCKS.title}>
      <p>Описание карточки</p>
      {MOCKS.rows.map((r) => <Row key={r.label} label={r.label} />)}
      {flag && <span>Только при флаге</span>}
      <Button size="md" label="Сохранить">Сохранить</Button>
      {dyn}
      <Ghost />
    </Panel>
  );
}
'''
FILES = {
    "src/modules/Card.tsx": CARD,
    "src/modules/Row.tsx": 'export const Row = ({ label }) => <div className="row"><b>{label}</b></div>;\n',
    "src/modules/mocks.ts": 'export const MOCKS = { title: "Заголовок", rows: [{label: "Раз"}, {label: "Два"}], n: { deep: ["x", "y"] } } as const;\n',
}


class Adapter(unittest.TestCase):
    def setUp(self):
        self.m = build(FILES, ["src/modules/Card.tsx", "src/modules/Row.tsx"])

    def test_ids_are_code_section_path(self):
        ids = [n["id"] for n in flat(self.m["root"])]
        self.assertEqual(ids[0:3], ["code:root", "code:s1", "code:s1/0"])
        self.assertIn("code:s1/2/0", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(C.validate_node(self.m["root"]), [])

    def test_local_component_inlined_with_children_spliced(self):
        top = by_id(self.m, "code:s1")
        self.assertEqual((top["tag"], top["component"]["figmaName"]), ("section", "Panel"))
        self.assertEqual(by_id(self.m, "code:s1/0")["text"], "Заголовок", "проп title разрешён через mocks.ts из другого файла")
        self.assertEqual(by_id(self.m, "code:s1/1")["text"], "Описание карточки", "дети Panel вставлены на {children}")

    def test_map_over_a_resolvable_array_replicates_with_the_bound_parameter(self):
        self.assertEqual(texts(self.m).count("Раз"), 1)
        self.assertIn("Два", texts(self.m))
        row = by_id(self.m, "code:s1/2")
        self.assertEqual(row["component"]["figmaName"], "Row", "локальный компонент из другого файла развёрнут инлайн")

    def test_package_component_is_a_black_box_with_identity_literal_props_and_slot_content(self):
        btn = next(n for n in flat(self.m["root"]) if n["kind"] == "instance" and n["name"] == "Button")
        self.assertEqual(btn["component"], {"figmaName": "Button", "implName": "Button", "textProps": {"label": "Сохранить"}})
        self.assertEqual([k["text"] for k in btn["children"]], ["Сохранить"], "содержимое слота — детьми")
        self.assertNotIn("size", btn["component"]["textProps"], "size — не текст")

    def test_markers_live_outside_the_tree(self):
        kinds = sorted(m["type"] for m in self.m["markers"])
        self.assertEqual(kinds, ["conditional", "dynamic-text", "unresolved-import"])
        self.assertTrue(all("type" not in n and "markers" not in n and "_marks" not in n for n in flat(self.m["root"])))
        self.assertEqual(sorted(k for v in self.m["marks"].values() for k in v), kinds)
        flag = next(n for n in flat(self.m["root"]) if n.get("text") == "Только при флаге")
        self.assertEqual(self.m["marks"][flag["id"]], ["conditional"])
        ghost = next(n for n in flat(self.m["root"]) if n.get("name") == "Ghost")
        self.assertEqual(self.m["marks"][ghost["id"]], ["unresolved-import"])

    def test_locations_are_file_line_column(self):
        flag = next(n for n in flat(self.m["root"]) if n.get("text") == "Только при флаге")
        loc = self.m["locations"][flag["id"]]
        self.assertEqual((loc["file"], loc["line"]), ("src/modules/Card.tsx", 12))
        self.assertGreaterEqual(loc["column"], 1)

    def test_dynamic_text_node_has_no_words(self):
        dyn = next(n for n in flat(self.m["root"]) if "dynamic-text" in self.m["marks"].get(n["id"], []))
        self.assertEqual((dyn["kind"], dyn.get("text")), ("text", None))

    def test_unresolvable_map_is_a_repeated_marker(self):
        src = 'export default function L() { return <ul>{items.map((i) => <li>{i.name}</li>)}</ul>; }\n'
        m = build({"a.tsx": src})
        self.assertIn("repeated", [x["type"] for x in m["markers"]])
        self.assertIn("dynamic-text", [x["type"] for x in m["markers"]])

    def test_expression_forms(self):
        src = ('import { T } from "./t";\nconst L = ["a", "b"];\nexport default function X() {\n return (<div><i>{`n-${L[1]}`}</i><i>{T.rows[1].label}</i>'
               '<i>{L.length}</i><i>{"lit"}</i><i>{3}</i>{null}{false}</div>);\n}\n')
        tt = 'export const T = { rows: [{ label: "A" }, { label: "B" }] };\n'
        m = build({"a.tsx": src, "t.ts": tt}, ["a.tsx"])
        self.assertEqual(texts(m), ["n-b", "B", "lit", "3"])
        self.assertEqual([x["type"] for x in m["markers"]], ["dynamic-text"], "L.length статически неизвестна")

    def test_conditional_forms_and_multiple_returns(self):
        src = ('export default function X({ a }) {\n if (a) { return <p>раз</p>; }\n return (<div>{a ? <b>да</b> : <i>нет</i>}</div>);\n}\n')
        m = build({"a.tsx": src})
        self.assertEqual(sorted(texts(m)), ["да", "нет"], "вход — первый return с JSX; обе ветки тернарника в дереве")
        self.assertEqual(sum(1 for x in m["markers"] if x["type"] == "conditional"), 3, "несколько return + две ветки тернарника")

    def test_cycle_guard(self):
        src = ('function A() { return <div><B /></div>; }\nfunction B() { return <div><A /></div>; }\nexport default function Root() { return <A />; }\n')
        m = build({"a.tsx": src})
        self.assertTrue(m["root"]["children"])
        self.assertTrue(any("сам себя" in w for w in m["warnings"]))

    def test_entry_is_the_unreferenced_component_default_wins(self):
        src = ('function Inner() { return <i>x</i>; }\nexport function Alpha() { return <b>y</b>; }\n'
               'export default function Zeta() { return <div><Inner /></div>; }\n')
        m = build({"a.tsx": src})
        self.assertEqual(m["sections"]["s1"]["entry"], "a.tsx:Zeta", "default export побеждает, хотя Alpha раньше по имени")
        two = build({"a.tsx": 'export function B() { return <b>y</b>; }\nexport function A() { return <i>x</i>; }\n'})
        self.assertEqual(two["sections"]["s1"]["entry"], "a.tsx:A")
        self.assertTrue(any("неоднозначный вход" in w for w in two["warnings"]))

    def test_fragments_svg_img_and_text_leaf(self):
        src = 'export default function X() { return <><svg width="8" /><img src="a.png" /><p>Один</p></>; }\n'
        m = build({"a.tsx": src})
        top = m["root"]["children"][0]
        self.assertEqual([k["kind"] for k in top["children"]], ["vector", "image", "text"])

    def test_no_component_with_jsx_leaves_the_section_out_with_a_warning(self):
        m = build({"a.ts": "export const x = 1;\n"}, ["a.ts"])
        self.assertEqual(m["root"]["children"], [])

    def test_broken_file_is_a_warning_not_a_crash(self):
        m = build({"a.tsx": "export default function ( { return <<<< ;\n", "b.tsx": "export default function B() { return <p>ok</p>; }\n"}, ["a.tsx", "b.tsx"])
        self.assertIn("ok", texts(m))

    def test_sections_hang_under_code_root(self):
        m = JT.build({"a.tsx": "export default function A() { return <p>раз</p>; }\n", "b.tsx": "export default function B() { return <p>два</p>; }\n"},
                     [{"id": "p1", "outputs": ["a.tsx"]}, {"id": "p2", "outputs": ["b.tsx"]}])
        self.assertEqual([k["id"] for k in m["root"]["children"]], ["code:p1", "code:p2"])


def design_of(*nodes):
    return screen(list(nodes))


def model_of(files, outputs=None):
    return build(files, outputs)


PAGE = {"a.tsx": 'export default function P() { return <div><h2>Заголовок</h2><p>Описание</p></div>; }\n'}


class Compare(unittest.TestCase):
    def d(self):
        return design_of(TXT("d:h", "Заголовок"), TXT("d:p", "Описание", y=20))

    def test_clean_module_passes(self):
        r = CC.compare(self.d(), model_of(PAGE), {"pairs": []})
        self.assertEqual((r["status"], r["defects"], r["treeRecognized"], r["matchRate"]), ("pass", [], True, 1.0))
        self.assertEqual({v["status"] for v in r["ledger"].values()}, {"pass"})

    def test_changed_text_is_a_blocking_defect_with_a_location(self):
        files = {"a.tsx": 'export default function P() {\n  return <div><h2>Заголовок</h2><p>Другое описание</p></div>;\n}\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": []}, root=Path("/repo"))
        self.assertEqual(r["status"], "fail")
        [df] = r["defects"]
        self.assertEqual((df["code"], df["severity"], df["design"]), ("TEXT", "critical", "d:p"))
        self.assertEqual((df["location"]["file"], df["location"]["line"]), ("a.tsx", 2))
        self.assertTrue(df["editor"].startswith("vscode://file//repo/a.tsx:2:"))
        self.assertEqual(df["id"], CC.defect_id("TEXT", "d:p", df["impl"], "text"))

    def test_no_numeric_or_style_codes_ever(self):
        d = design_of(D("frame", "d:f", [TXT("d:h", "Заголовок")], box=box(0, 0, 500, 50), style={"background": "rgba(1, 2, 3, 1)", "radius": 8}),
                      inst("d:b", "Button", comp={"origin": "library", "paints": True, "sized": "own", "textProps": {"l": "Ок"}}))
        files = {"a.tsx": 'import { Button } from "@corp/ui";\nexport default function P() { return <div><div><h2>Заголовок</h2></div><Button label="Ок" /></div>; }\n'}
        r = CC.compare(d, model_of(files), {"pairs": []})
        self.assertEqual([f["code"] for f in r["findings"]], [])
        self.assertEqual(r["degraded"], [])

    def test_missing_without_a_dom_summary_is_unconfirmed_not_a_defect(self):
        files = {"a.tsx": 'export default function P() { return <div><h2>Заголовок</h2></div>; }\n'}
        r = CC.compare(self.d(), model_of(files), None)
        self.assertEqual([(f["code"], f["status"]) for f in r["findings"]], [("MISSING", "unconfirmed")])
        self.assertEqual(r["defects"], [])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["ledger"]["code-reconcile"]["unconfirmed"], 1)

    def test_missing_absent_from_both_trees_is_corroborated_and_blocks(self):
        files = {"a.tsx": 'export default function P() { return <div><h2>Заголовок</h2></div>; }\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": [{"design": "d:h", "impl": "dom:1"}]})
        self.assertEqual(r["corroborated"], ["d:p"])
        [df] = r["defects"]
        self.assertEqual((df["code"], df["status"], df["severity"]), ("MISSING", "corroborated", "critical"))
        self.assertEqual(df["id"], CC.defect_id("MISSING", "d:p", None, None))
        self.assertEqual(r["status"], "fail")

    def test_missing_that_the_page_rendered_is_dismissed(self):
        files = {"a.tsx": 'export default function P() { return <div><h2>Заголовок</h2></div>; }\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": [{"design": "d:p", "impl": "dom:2"}]})
        self.assertEqual((r["defects"], r["findings"]), ([], []))
        self.assertTrue(any("вне сканлиста" in x["reason"] for x in r["dismissals"]))

    def test_unresolved_import_next_to_a_missing_becomes_unverified_content(self):
        files = {"a.tsx": 'import { Ghost } from "./Ghost";\nexport default function P() { return <div><h2>Заголовок</h2><Ghost /></div>; }\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": []})
        self.assertEqual((r["defects"], r["findings"]), ([], []))
        self.assertEqual([(u["design"], u["aspect"]) for u in r["unverified"]], [("d:p", "content")])

    def test_dynamic_text_next_to_a_missing_text_becomes_unverified_copy(self):
        files = {"a.tsx": 'export default function P({ x }) { return <div><h2>Заголовок</h2>{x}</div>; }\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": []})
        self.assertEqual(r["defects"], [])
        self.assertEqual([(u["design"], u["aspect"]) for u in r["unverified"]], [("d:p", "copy")])

    def test_dynamic_text_node_demotes_a_text_difference(self):
        files = {"a.tsx": 'export default function P({ x }) { return <div><h2>Заголовок</h2><p>{x}</p></div>; }\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": []})
        self.assertEqual(r["defects"], [])

    def test_mixed_static_and_dynamic_text_is_one_unknown_text(self):
        d = design_of(TXT("d:t", "Итого: 5 позиций"))
        files = {"a.tsx": 'export default function P({ n }) { return <p>Итого: {n} позиции</p>; }\n'}
        m = model_of(files)
        leaf = m["root"]["children"][0]
        self.assertEqual((leaf["kind"], leaf.get("text")), ("text", None), "слова целиком неизвестны")
        self.assertEqual([x["type"] for x in m["markers"]], ["dynamic-text"])
        r = CC.compare(d, m, {"pairs": []})
        self.assertEqual(r["defects"], [])
        self.assertEqual([u["aspect"] for u in r["unverified"]], ["copy"])

    def test_dynamic_prop_of_a_package_component_demotes_its_slots(self):
        d = design_of(inst("d:b", "Button", comp={"origin": "library", "sized": "own", "content": [{"id": "s", "text": "Сохранить", "overridden": True}]}))
        files = {"a.tsx": 'import { Button } from "@corp/ui";\nexport default function P({ t, onClick }) { return <div><Button label={t} onClick={onClick} /></div>; }\n'}
        r = CC.compare(d, model_of(files), {"pairs": []})
        self.assertEqual((r["defects"], r["findings"]), ([], []))
        self.assertTrue(any("динамический" in x["reason"] for x in r["dismissals"]))
        handler_only = {"a.tsx": 'import { Button } from "@corp/ui";\nexport default function P({ onClick }) { return <div><Button onClick={onClick} /></div>; }\n'}
        r = CC.compare(d, model_of(handler_only), {"pairs": []})
        self.assertEqual([x["code"] for x in r["defects"]], ["SLOT_MISSING"], "обработчик слова не скрывает")

    def test_conditional_demotes_a_text_difference(self):
        files = {"a.tsx": 'export default function P({ x }) { return <div><h2>Заголовок</h2>{x && <p>Описани</p>}</div>; }\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": []})
        self.assertEqual([f for f in r["findings"] if f["code"] == "TEXT"], [])
        self.assertTrue(any("условный" in x["reason"] for x in r["dismissals"]))

    def test_unrecognised_tree_emits_no_defects_but_keeps_the_evidence(self):
        files = {"a.tsx": 'export default function P() { return <div><h2>Совсем</h2><p>Другое</p><b>Дерево</b></div>; }\n'}
        r = CC.compare(self.d(), model_of(files), {"pairs": []})
        self.assertFalse(r["treeRecognized"])
        self.assertEqual((r["status"], r["defects"]), ("skipped", []))
        self.assertIn("не доверяет", r["reason"])
        self.assertTrue(r["findings"], "находки остаются уликами")
        self.assertEqual(r["ledger"]["code-tree"]["status"], "skipped")

    def test_dedup_with_the_dom_gate_by_id(self):
        files = {"a.tsx": 'export default function P() { return <div><h2>Заголовок</h2></div>; }\n'}
        did = CC.defect_id("MISSING", "d:p", None, None)
        r = CC.compare(self.d(), model_of(files), {"pairs": [], "defectIds": [did]})
        self.assertEqual(r["defects"], [], "измеренное выигрывает: тот же id уже записан DOM-гейтом")
        self.assertEqual(r["ledger"]["code-reconcile"]["deduped"], 1)

    def test_slot_text_from_literal_props(self):
        d = design_of(inst("d:b", "Button", comp={"origin": "library", "sized": "own", "content": [{"id": "s", "text": "Сохранить", "overridden": True}]}))
        files = {"a.tsx": 'import { Button } from "@corp/ui";\nexport default function P() { return <div><Button label="Отменить" /></div>; }\n'}
        r = CC.compare(d, model_of(files), {"pairs": []})
        self.assertEqual([(x["code"], x["severity"]) for x in r["defects"]], [("SLOT_TEXT", "critical")])
        ok = {"a.tsx": 'import { Button } from "@corp/ui";\nexport default function P() { return <div><Button label="Сохранить" /></div>; }\n'}
        self.assertEqual(CC.compare(d, model_of(ok), {"pairs": []})["defects"], [])

    def test_design_locations_give_dom_defects_the_same_link(self):
        r = CC.compare(self.d(), model_of(PAGE), {"pairs": []}, root=Path("/repo"))
        self.assertEqual(r["designLocations"]["d:h"]["file"], "a.tsx")
        self.assertTrue(r["designLocations"]["d:h"]["editor"].startswith("vscode://file//repo/a.tsx:1:"))

    def test_deterministic(self):
        files = {"a.tsx": 'export default function P() {\n  return <div><h2>Заголовок</h2><p>Другое</p></div>;\n}\n'}
        a = CC.compare(self.d(), model_of(files), {"pairs": []})
        b = CC.compare(self.d(), model_of(files), {"pairs": []})
        self.assertEqual(json.dumps(a, sort_keys=True, ensure_ascii=False), json.dumps(b, sort_keys=True, ensure_ascii=False))


class Links(unittest.TestCase):
    def test_editor_link_encodes_segments_and_uses_one_based_numbers(self):
        link = CC.editor_link("vscode://file/{path}:{line}:{column}", Path("/my repo"), "src/мой модуль/A b.tsx", 12, 3)
        self.assertEqual(link, "vscode://file//my%20repo/src/%D0%BC%D0%BE%D0%B9%20%D0%BC%D0%BE%D0%B4%D1%83%D0%BB%D1%8C/A%20b.tsx:12:3")
        self.assertEqual(CC.editor_link("idea://open?file={path}&line={line}", Path("/r"), "a.tsx", 1, 1), "idea://open?file=/r/a.tsx&line=1")

    def test_defect_id_ignores_impl_for_missing_only(self):
        self.assertEqual(CC.defect_id("MISSING", "d", "dom:1", None), CC.defect_id("MISSING", "d", "code:1", None))
        self.assertNotEqual(CC.defect_id("TEXT", "d", "dom:1", "text"), CC.defect_id("TEXT", "d", "code:1", "text"))
        self.assertEqual(len(CC.defect_id("TEXT", "d", "i", "p")), 12)


class World(unittest.TestCase):
    """Настоящий трек: канон, план, build-state и файлы секций на диске."""

    def setUp(self):
        import test_build_state as TB
        self.TB = TB
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        node = TB.frame("Page", [TB.panel("p1", [TB.leaf("t1", name="t1", text="Заголовок")]),
                                 TB.panel("p2", [TB.leaf("t2", name="t2", text="Описание")]),
                                 TB.panel("p3", [TB.leaf("t3", name="t3", text="Футер")])],
                        name=TB.autoname("Page"), layout={"direction": "column", "gap": 8})
        TB.make_world(self.root, node)
        TB.walk(self.root)                                   # p1 и p2 собраны (outputs), p3 начата
        h = T.call("daiver_section", self.root, {"track": TB.TRACK, "sectionId": "p3", "weightBudget": TB.BUDGET})["section"]["hash"]
        (self.root / "src" / "modules").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "modules" / "p3.tsx").write_text("export {};\n", encoding="utf-8")
        T.call("daiver_build_state_set", self.root, {"track": TB.TRACK, "sectionId": "p3", "status": "done", "outputs": ["src/modules/p3.tsx"], "hash": h})
        self.out = self.root / ".daiver" / "tracks" / TB.TRACK / "verify" / "iter-1"
        (self.root / "src" / "modules" / "p3.tsx").write_text(self.FOOTER, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    FOOTER = "export default function P3() { return <footer>Футер</footer>; }\n"

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def call(self, **kw):
        return T.call("daiver_code_compare", self.root, dict({"track": self.TB.TRACK, "iterationDir": self.out.relative_to(self.root).as_posix()}, **kw))

    def test_full_run_writes_four_artifacts(self):
        self.write("src/modules/p1.tsx", "export default function P1() { return <h2>Заголовок</h2>; }\n")
        self.write("src/modules/p2.tsx", "export default function P2() { return <p>Описание</p>; }\n")
        res = self.call()
        self.assertEqual(res["status"], "pass", res)
        for name in CC.ARTIFACTS:
            self.assertTrue((self.out / name).is_file(), name)
        summary = json.loads((self.out / "code-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(summary["sections"]), ["p1", "p2", "p3"])
        self.assertTrue(summary["treeRecognized"])
        model = json.loads((self.out / "code-model.json").read_text(encoding="utf-8"))
        self.assertEqual(model["daiver"]["sourceFormat"], "code")
        self.assertEqual(model["root"]["id"], "code:root")

    def test_changed_text_fails_with_file_line(self):
        self.write("src/modules/p1.tsx", "export default function P1() {\n  return <h2>Заголовок!</h2>;\n}\n")
        self.write("src/modules/p2.tsx", "export default function P2() { return <p>Описание</p>; }\n")
        res = self.call()
        self.assertEqual(res["status"], "fail")
        d = res["defects"][0]
        self.assertEqual((d["code"], d["location"]["file"], d["location"]["line"]), ("TEXT", "src/modules/p1.tsx", 2))
        self.assertTrue(d["editor"].startswith("vscode://file/"))
        self.assertIn("src/modules/p1.tsx:2:", d["editor"])

    def test_dom_summary_next_to_the_artifacts_is_read(self):
        self.write("src/modules/p1.tsx", "export default function P1() { return <h2>Заголовок</h2>; }\n")
        self.write("src/modules/p2.tsx", "export default function P2() { return <b>Другое</b>; }\n")
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "compare-summary.json").write_text(json.dumps({"pairs": [{"design": "p2", "impl": "dom:7"}]}), encoding="utf-8")
        res = self.call()
        self.assertEqual([d["code"] for d in res["defects"]], [])
        self.assertGreaterEqual(res["ledger"]["code-reconcile"]["dismissed"], 1)

    def test_skipped_with_a_reason_when_the_sources_are_gone(self):
        for rel in ("src/modules/p1.tsx", "src/modules/p2.tsx", "src/modules/p3.tsx"):
            (self.root / rel).unlink()
        res = self.call()
        self.assertEqual(res["status"], "skipped")
        self.assertIn("пропали", res["reason"])
        self.assertTrue(any("файла нет" in w for w in res["warnings"]))
        self.assertTrue((self.out / "code-summary.json").is_file(), "и skipped оставляет сводку с причиной")

    def test_skipped_without_build_state(self):
        (self.root / self.TB.STATE_REL).unlink()
        res = self.call()
        self.assertEqual(res["status"], "skipped")
        self.assertIn("нет build-state", res["reason"])

    def test_vue_outputs_are_a_named_skip(self):
        state = json.loads((self.root / self.TB.STATE_REL).read_text(encoding="utf-8"))
        for s in state["sections"]:
            if s.get("outputs"):
                s["outputs"] = [o.replace(".tsx", ".vue") for o in s["outputs"]]
        (self.root / self.TB.STATE_REL).write_text(json.dumps(state), encoding="utf-8")
        res = self.call()
        self.assertEqual(res["status"], "skipped")
        self.assertIn("Vue/Svelte", res["reason"])

    def test_config_against_files_files_win_with_a_warning(self):
        self.write("src/modules/p1.tsx", "export default function P1() { return <h2>Заголовок</h2>; }\n")
        self.write("src/modules/p2.tsx", "export default function P2() { return <p>Описание</p>; }\n")
        cfg = self.root / ".daiver" / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn('framework: "react"', text)
        cfg.write_text(text.replace('framework: "react"', 'framework: "vue"'), encoding="utf-8")
        res = self.call()
        self.assertEqual(res["status"], "pass")
        self.assertTrue(any("файлы сильнее конфига" in w for w in res["warnings"]))

    def test_iteration_dir_outside_verify_is_refused(self):
        with self.assertRaises(T.ToolError):
            T.call("daiver_code_compare", self.root, {"track": self.TB.TRACK, "iterationDir": "src/modules"})

    def test_tool_arguments_are_checked(self):
        with self.assertRaises(T.ToolError):
            T.call("daiver_code_compare", self.root, {"track": self.TB.TRACK})
        with self.assertRaises(T.ToolError):
            T.call("daiver_code_compare", self.root, {"track": "../x", "iterationDir": "a"})

    def test_mocks_next_to_the_outputs_are_included(self):
        self.write("src/modules/mocks.ts", 'export const MOCKS = { head: "Заголовок" };\n')
        self.write("src/modules/p1.tsx", 'import { MOCKS } from "./mocks";\nexport default function P1() { return <h2>{MOCKS.head}</h2>; }\n')
        self.write("src/modules/p2.tsx", "export default function P2() { return <p>Описание</p>; }\n")
        res = self.call()
        self.assertEqual(res["status"], "pass")
        summary = json.loads((self.out / "code-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["dynamics"], {}, "mocks.ts прочитан: MOCKS.head разрешён, динамики нет")


if __name__ == "__main__":
    unittest.main()
