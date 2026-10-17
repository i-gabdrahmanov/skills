#!/usr/bin/env python3
"""test_ds_requirements.py — требования дизайн-системы из stories/исходников (задача 2.2; QG U8, S4; INV A5, A6).

Пинит: лексер JS/TS/JSX (апостроф в JSX-тексте, регулярка против деления, шаблоны с `${}`, JSDoc к токену, ASI);
статическое вычисление выражений (константы, enum, spread, `as const`, невычислимое → opaque); разбор stories —
CSF3, CSF2 (`Template.bind` + `Name.args =`), CSF1, storiesOf, CSF Factories, include/excludeStories; деклараций —
interface/type/enum, extends/Omit/Pick, внешние `*Attributes` (набор открыт), привязки компонентов (FC, forwardRef,
функция, класс, `declare module`); MDX (stories.mdx, документация `Meta of`, правила привязки прозы); слияние
(types > argTypes > usage, узкие options поверх `string`, usedValues, import, deprecation + замена, примеры);
документы (формат, валидатор: неизвестное поле/options/import/хеш пары — отказ), инструмент: storybook-mcp → отказ без
записи (S4), ноль компонентов → ошибка без записи, потолки MAX_FILES / MAX_FILE_BYTES, пути только внутри репо, симлинк
наружу пропускается, детерминизм (побайтно), идемпотентность, `check` (ok / missing / invalid / stale по хешу).

Данные: фикстура `fixtures/fake-ds/` — СИНТЕТИЧЕСКАЯ библиотека (нейтральные имена, форматы Storybook 6/7: CSF3,
CSF2, CSF1, storiesOf, MDX, `.d.ts`), боевой библиотеки у нас нет. Что НЕ проверено данными: формы stories, MDX и
деклараций реальной библиотеки выведены из знания Storybook/TypeScript (допущения названы в докстрингах
`daiver/ds/*` и в «Результате» задачи).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))

from daiver import ds_node  # noqa: E402
from daiver import ds_requirements as DR  # noqa: E402
from daiver import init as I  # noqa: E402
from daiver import tools as T  # noqa: E402
from daiver.ds import csf, decl, interp, lex, mdx, merge, typeast, value as V  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "fake-ds"
MCP_SERVER = HERE.parents[2] / "hooks" / "daiver_mcp.py"


# ── построители ──────────────────────────────────────────────────────────────

def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def init_repo(root, source="stories", scan_paths=("lib/src", "lib/types")) -> Path:
    root = Path(root)
    write(root / "package.json", "{}")
    (root / "src" / "modules").mkdir(parents=True, exist_ok=True)
    answers = {"framework": "react", "language": "ts", "styling": "less", "outputDir": "src/modules",
               "buildCommand": "npm run build", "librarySource": source}
    if source == "storybook-mcp":
        answers["storybookMcp"] = "sb"
    else:
        answers["scanPaths"] = list(scan_paths)
    res = I.init(root, answers)
    assert res["ok"], res
    return root


def fixture_repo(tmp, source="stories") -> Path:
    root = init_repo(tmp, source)
    shutil.copytree(FIXTURE, root / "lib")
    return root


def docs(root: Path):
    c = json.loads((Path(root) / DR.CONTRACT_REL).read_text(encoding="utf-8"))
    d = json.loads((Path(root) / DR.DETAILS_REL).read_text(encoding="utf-8"))
    return c, d


def toks(src: str, jsx: bool = True) -> lex.Toks:
    return lex.Toks(src, jsx)


def kinds(src: str, jsx: bool = True) -> List[str]:
    return [t.t for t in toks(src, jsx).t]


def values(src: str, jsx: bool = True) -> List[Any]:
    return [t.v for t in toks(src, jsx).t]


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ── лексер ───────────────────────────────────────────────────────────────────

class LexerTests(unittest.TestCase):
    def test_jsx_text_with_apostrophe_does_not_break_quotes(self):
        # Апостроф в тексте JSX — главная причина самодельного лексера: простой лексер принял бы его за строку.
        src = "export const A = () => <p>Don't panic</p>;\nexport const B = 1;\n"
        t = toks(src)
        self.assertEqual([x.t for x in t.t].count("jsx"), 1)
        self.assertEqual(t.t[-2].v, "1")
        self.assertEqual(len(list(t.statements())), 2)

    def test_jsx_is_one_token_with_nested_expressions(self):
        src = "const x = <A b={c ? <B /> : 'z'} d=\"e\">{f(`t${g}`)} text</A>;"
        t = toks(src)
        jsx = [x for x in t.t if x.t == "jsx"]
        self.assertEqual(len(jsx), 1)
        self.assertTrue(jsx[0].v.startswith("<A ") and jsx[0].v.endswith("</A>"))

    def test_less_than_after_identifier_is_not_jsx(self):
        self.assertNotIn("jsx", kinds("const a = useState<string>('x'); if (a < b) {}"))
        self.assertNotIn("jsx", kinds("type A = Array<Array<string>>;", jsx=False))

    def test_generic_arrow_in_tsx_falls_back_to_punct(self):
        # `<T,>(x: T) => x` в tsx не JSX: разбор элемента не удаётся — файл не ломается.
        t = toks("const id = <T,>(x: T) => x;\nexport const Y = 2;")
        self.assertNotIn("jsx", [x.t for x in t.t])
        self.assertEqual(t.t[-2].v, "2")

    def test_ts_file_never_has_jsx(self):
        self.assertNotIn("jsx", kinds("const a = <b>c</b>;", jsx=False))

    def test_regex_versus_division(self):
        self.assertEqual(kinds("x = /ab+c[/]/g.test(s);")[2], "re")
        self.assertEqual(kinds("x = (a) / 2 / 3;").count("re"), 0)
        self.assertEqual(kinds("x = a / b;").count("re"), 0)

    def test_strings_escapes_and_templates(self):
        self.assertEqual(values(r"'it\'s A \x42 \n'")[0], "it's A B \n")
        self.assertEqual(values("`a${b + `c${d}`}e`")[0], None, "шаблон с подстановкой не вычисляется")
        self.assertEqual(values("`plain \\` tick`")[0], "plain ` tick")
        self.assertEqual(values(r"'😀'")[0], "\U0001F600", "суррогатная пара склеивается")

    def test_unterminated_string_stops_at_line_end(self):
        self.assertEqual(values("a = 'oops\nb = 2;"), ["a", "=", "oops", "b", "=", "2", ";"])

    def test_jsdoc_attaches_to_next_token_only(self):
        t = toks("/** doc */\nexport const A = 1;\nexport const B = 2;")
        self.assertIsNotNone(t.t[0].doc)
        self.assertIsNone(t.t[5].doc)
        self.assertEqual(lex.parse_jsdoc(t.t[0].doc).text, "doc")

    def test_jsdoc_tags_and_links(self):
        d = lex.parse_jsdoc("/**\n * Text {@link Foo|bar} here.\n * @deprecated Use `X`\n *   instead.\n * @default 'md'\n */")
        self.assertEqual(d.text, "Text bar here.")
        self.assertEqual(d.tag("deprecated"), "Use `X` instead.")
        self.assertEqual(d.tag("default"), "'md'")
        self.assertTrue(lex.parse_jsdoc("/** @deprecated */").has("deprecated"))
        self.assertEqual(lex.parse_jsdoc("/** @deprecated */").tag("deprecated"), "")

    def test_angle_brackets_are_single_tokens(self):
        self.assertEqual(values("A<B<C>>", jsx=False), ["A", "<", "B", "<", "C", ">", ">"])
        t = toks("A<B<C>> x", jsx=False)
        self.assertEqual(t.skip_angle(1), 7)

    def test_brackets_are_paired_and_tolerate_garbage(self):
        t = toks("f(a, [b, {c}]) ) {", jsx=False)
        self.assertEqual(t.close(1), 11)
        self.assertEqual(t.close(len(t.t) - 1), t.n, "незакрытая скобка не ломает разбор")

    def test_statements_split_by_semicolon_and_asi(self):
        src = "const a = {\n x: 1\n}\nconst b = foo\n  .bar()\nexport default b"
        t = toks(src, jsx=False)
        parts = [t.text(i, j) for i, j in t.statements()]
        self.assertEqual(len(parts), 3)
        self.assertIn(".bar()", parts[1], "перенос перед `.` — продолжение выражения")

    def test_split_decls_ignores_commas_in_generics(self):
        t = toks("const A = forwardRef<El, Props>(f), B = 1;", jsx=False)
        i, j = next(t.statements())
        self.assertEqual(len(t.split_decls(1, j - 1)), 2)

    def test_is_jsx_file(self):
        for name, want in (("a.tsx", True), ("a.jsx", True), ("a.js", True), ("a.mdx", True), ("a.ts", False),
                           ("a.d.ts", False), ("a.mts", False)):
            self.assertEqual(lex.is_jsx_file(name), want, name)


# ── вычисление значений ──────────────────────────────────────────────────────

def ev(src: str):
    t = toks("const __x = " + src + ";")
    env = V.collect_decls(t)
    return env.const_value("__x")


class ValueTests(unittest.TestCase):
    def test_literals_objects_arrays(self):
        self.assertEqual(ev("{ a: 1, 'b-c': 'x', [`k`]: true, d: null, e: -2, f: 0x10, g: 1_000 }"),
                         {"a": 1, "b-c": "x", "k": True, "d": None, "e": -2, "f": 16, "g": 1000})
        self.assertEqual(ev("['a', 1, ...['b']]"), ["a", 1, "b"])
        self.assertEqual(ev("1.5"), 1.5)
        self.assertEqual(ev("2.0"), 2)

    def test_type_suffixes_are_dropped(self):
        self.assertEqual(ev("['a', 'b'] as const"), ["a", "b"])
        self.assertEqual(ev("{ x: 1 } satisfies Foo<Bar>"), {"x": 1})
        self.assertEqual(ev("('v')"), "v")
        self.assertEqual(ev("'v'!"), "v")

    def test_constants_enums_and_spread(self):
        t = toks("enum Size { Sm = 'sm', Md = 'md' }\nconst base = { a: 1 };\nconst VARS = ['x', 'y'];\n"
                 "const o = { ...base, size: Size.Md, vars: VARS, all: Object.values(Size), keys: Object.keys(base) };")
        o = V.collect_decls(t).const_value("o")
        self.assertEqual(o, {"a": 1, "size": "md", "vars": ["x", "y"], "all": ["sm", "md"], "keys": ["a"]})

    def test_not_computable_is_opaque_and_unresolved_is_ref(self):
        o = ev("{ fn: () => 1, call: f(), cond: a ? 1 : 2, tpl: `x${y}`, comp: Button, member: Form.Item }")
        for k in ("fn", "call", "cond", "tpl"):
            self.assertIs(o[k], V.OPAQUE, k)
        self.assertEqual(o["comp"], V.Ref("Button"))
        self.assertEqual(o["member"], V.Ref("Form.Item"))
        self.assertTrue(V.has_opaque(o))

    def test_jsx_value_and_methods(self):
        o = ev("{ icon: <Icon name=\"x\" />, onClick() { return 1; }, get g() { return 2; } }")
        self.assertIsInstance(o["icon"], V.Jsx)
        self.assertIs(o["onClick"], V.OPAQUE)

    def test_cycles_and_deep_nesting_do_not_hang(self):
        t = toks("const a = b;\nconst b = a;\nconst c = " + "[" * 60 + "1" + "]" * 60 + ";")
        env = V.collect_decls(t)
        self.assertIs(env.const_value("a"), V.OPAQUE)
        env.const_value("c")                                   # не падает и не зависает

    def test_usage_collects_literal_attributes_in_preorder(self):
        env = V.collect_decls(toks("const VARIANT = 'ghost';"))
        usage = V.collect_usage('<Card elevated n={3} variant={VARIANT} icon={<Icon name="plus" />}>'
                                "Don't <Button size=\"lg\" {...rest} onClick={() => 1} /></Card>", env)
        self.assertEqual([u[0] for u in usage], ["Card", "Icon", "Button"])
        self.assertEqual(usage[0][1], {"elevated": [True], "n": [3], "variant": ["ghost"]})
        self.assertEqual(usage[2][1], {"size": ["lg"]})

    def test_usage_ignores_dom_tags_and_normalize_jsx(self):
        env = V.collect_decls(toks(""))
        self.assertEqual(V.collect_usage("<div className=\"a\"><span /></div>", env), [])
        self.assertEqual(V.normalize_jsx("<Card\n  elevated\n>\n  <p>a\n   b</p>\n</Card>"),
                         "<Card elevated><p>a b</p></Card>")



# ── stories ──────────────────────────────────────────────────────────────────

def group(src: str, path: str = "A.stories.tsx", i: int = 0) -> Dict[str, Any]:
    return csf.scan(src, path)["groups"][i]


def story(g: Dict[str, Any], name: str) -> Dict[str, Any]:
    return next(s for s in g["stories"] if s["export"] == name or s["name"] == name)


CSF3 = """
import { Button } from '@fakeui/core';
const meta = {
  title: 'Components/Button', component: Button, args: { label: 'L' },
  parameters: { docs: { description: { component: 'About button.' } } },
  argTypes: { variant: { options: ['a', 'b'], description: 'Look' } },
} satisfies Meta<typeof Button>;
export default meta;
export const Primary: Story = { args: { variant: 'a' }, name: 'Main' };
export const Plain = { render: (args) => <Button {...args} size="lg">Go</Button> };
export const __namedExportsOrder = ['Primary'];
"""


class StoriesTests(unittest.TestCase):
    def test_csf3_meta_and_stories(self):
        g = group(CSF3)
        self.assertEqual(g["meta"]["title"], "Components/Button")
        self.assertEqual(g["meta"]["component"], "Button")
        self.assertEqual(g["meta"]["description"], "About button.")
        self.assertEqual(g["meta"]["args"], {"label": "L"})
        self.assertEqual(g["meta"]["argTypes"]["variant"]["options"], ["a", "b"])
        self.assertEqual([s["export"] for s in g["stories"]], ["Primary", "Plain"], "__namedExportsOrder — не история")
        self.assertEqual(story(g, "Primary")["name"], "Main")
        self.assertEqual(story(g, "Primary")["format"], "csf3")
        self.assertEqual(story(g, "Plain")["usage"], [["Button", {"size": ["lg"]}]])

    def test_default_export_inline_and_through_identifier(self):
        g = group("export default { title: 'T/X', component: Foo.Bar };\nexport const A = {};")
        self.assertEqual(g["meta"]["component"], "Foo.Bar")
        g = group("const m = { title: 'T/Y' };\nexport default m;\nexport const A = {};")
        self.assertEqual(g["meta"]["title"], "T/Y")

    def test_csf2_template_bind_and_assigned_fields(self):
        g = group("export default { title: 'T/B', component: Badge };\n"
                  "const Template = (args) => <Badge {...args} />;\n"
                  "export const P = Template.bind({});\nP.args = { tone: 'info' };\nP.storyName = 'Primary tone';\n"
                  "export const S = Template.bind({});\nS.args = { ...P.args, tone: 'ok' };\n")
        p, s = story(g, "P"), story(g, "S")
        self.assertEqual((p["format"], p["name"], p["args"]), ("csf2", "Primary tone", {"tone": "info"}))
        self.assertEqual(s["args"], {"tone": "ok"}, "spread `P.args` читается из присваивания")
        self.assertEqual(p["jsx"], "<Badge {...args} />")

    def test_csf1_function_stories_take_values_from_jsx(self):
        g = group("export default { title: 'L/Card' };\n"
                  "export const A = () => <Card elevated title=\"x\">Don't</Card>;\n"
                  "export function B() { return (<Card title=\"y\" />); }\n")
        self.assertEqual([s["format"] for s in g["stories"]], ["csf1", "csf1"])
        self.assertEqual(story(g, "A")["usage"], [["Card", {"elevated": [True], "title": ["x"]}]])
        self.assertEqual(story(g, "B")["usage"], [["Card", {"title": ["y"]}]])

    def test_stories_of_chain_and_variable_form(self):
        g = group("storiesOf('O/Tip', module).addParameters({ component: Tip })\n"
                  "  .add('one', () => <Tip placement=\"top\" />)\n"
                  "  .add('two', () => <Tip placement=\"left\" />, { docs: { description: { story: 'Left.' } } });\n",
                  "T.stories.js")
        self.assertEqual(g["meta"]["title"], "O/Tip")
        self.assertEqual(g["meta"]["component"], "Tip")
        self.assertEqual([s["name"] for s in g["stories"]], ["one", "two"])
        self.assertEqual(story(g, "two")["description"], "Left.")
        g = group("const k = storiesOf('V/X', module);\nk.add('a', () => <X v=\"1\" />);\nk.add('b', () => <X />);\n",
                  "V.stories.js")
        self.assertEqual(len(g["stories"]), 2)

    def test_csf_factories_take_the_first_object_argument(self):
        g = group("const meta = preview.meta({ title: 'F/Z', component: Z });\nexport default meta;\n"
                  "export const A = meta.story({ args: { k: 1 } });\n")
        self.assertEqual(g["meta"]["component"], "Z")
        self.assertEqual(story(g, "A")["args"], {"k": 1})

    def test_include_and_exclude_stories(self):
        g = group("export default { title: 'I/X', component: X, excludeStories: /.*Data$/ };\n"
                  "export const A = {};\nexport const fooData = {};\n")
        self.assertEqual([s["export"] for s in g["stories"]], ["A"])
        g = group("export default { title: 'I/X', component: X, includeStories: ['B'] };\n"
                  "export const A = {};\nexport const B = {};\n")
        self.assertEqual([s["export"] for s in g["stories"]], ["B"])

    def test_export_list_and_function_exports(self):
        g = group("export default { title: 'E/X', component: X };\nconst A = { args: { a: 1 } };\n"
                  "function B() { return <X />; }\nexport { A, B as Renamed };\n")
        self.assertEqual({s["export"] for s in g["stories"]}, {"A", "Renamed"})

    def test_unresolvable_values_survive_as_markers(self):
        g = group("export default { title: 'M/X', component: X, argTypes: { ...shared, v: { options: opts } } };\n"
                  "export const A = { args: { onClick: fn(), icon: <I /> } };\n")
        a = story(g, "A")["args"]
        self.assertEqual(a["onClick"], {"$": "opaque"})
        self.assertEqual(a["icon"], {"$": "jsx", "text": "<I />"})
        self.assertEqual(g["meta"]["argTypes"]["v"]["options"], {"$": "ref", "name": "opts"})

    def test_imports_of_every_shape(self):
        f = csf.scan("import A, { B, C as D, type E } from '@x/ui';\nimport * as N from '@y/ns';\n"
                     "import type { F } from 'z';\nimport './side.css';\n", "i.stories.tsx")
        self.assertEqual(f["imports"]["A"], {"from": "@x/ui", "named": None, "default": True, "ns": False})
        self.assertEqual(f["imports"]["D"], {"from": "@x/ui", "named": "C", "default": False, "ns": False})
        self.assertEqual(f["imports"]["N"]["ns"], True)
        self.assertNotIn("E", f["imports"])
        self.assertNotIn("F", f["imports"])

    def test_file_without_stories_gives_no_groups(self):
        self.assertEqual(csf.scan("export const helper = 1;\n", "h.stories.ts")["groups"], [])


# ── типы и декларации ────────────────────────────────────────────────────────

def types_of(src: str, path: str = "a.d.ts", kind: str = "types") -> Dict[str, Any]:
    return decl.scan(src, path, kind)


def members(f: Dict[str, Any], name: str) -> Dict[str, Dict[str, Any]]:
    return {m["name"]: m for m in f["types"][name]["members"]}


class DeclTests(unittest.TestCase):
    def test_interface_members_with_docs_and_asi(self):
        f = types_of("""
export interface P extends Base, Omit<X, 'a'> {
  /** Look. */
  variant?: 'a' | 'b'
  /** @deprecated Use `variant="b"`. */
  flat?: boolean
  size?:
    | 'sm'
    | 'md'
  readonly id: string;
  onClick?: (e: Event) => void;
  render(item: Item): Node;
  [key: string]: unknown;
}""")
        m = members(f, "P")
        self.assertEqual(list(m), ["variant", "flat", "size", "id", "onClick", "render"])
        self.assertEqual(m["size"]["type"]["k"], "union", "многострочный union без `;` — один член")
        self.assertEqual(m["variant"]["doc"]["text"], "Look.")
        self.assertEqual(m["flat"]["doc"]["deprecated"], 'Use `variant="b"`.')
        self.assertTrue(m["render"]["method"])
        self.assertTrue(f["types"]["P"]["open"], "индексная сигнатура открывает набор")
        self.assertEqual(len(f["types"]["P"]["extends"]), 2)

    def test_interface_declarations_merge(self):
        f = types_of("interface A { a: 1 }\ninterface A { b: 2 }\n")
        self.assertEqual(list(members(f, "A")), ["a", "b"])

    def test_alias_and_enum(self):
        f = types_of("type S = 'x' | 'y';\ntype N = A extends B ? C : D;\nenum E { A = 'a', B = 'b' }\n"
                     "declare const enum K { One = 1, Two }\n")
        self.assertEqual(f["types"]["S"]["type"]["k"], "union")
        self.assertEqual(f["types"]["N"]["type"]["k"], "other", "условный тип честно other")
        self.assertEqual(f["types"]["E"]["enum"], {"A": "a", "B": "b"})
        self.assertEqual(f["types"]["K"]["enum"], {"One": 1, "Two": 2})

    def test_component_bindings_in_declarations(self):
        f = types_of("""
export declare const A: React.FC<AP>;
export declare const B: React.ForwardRefExoticComponent<BP & React.RefAttributes<HTMLElement>>;
export declare const M: React.MemoExoticComponent<React.ComponentType<MP>>;
export declare function C(props: CP): JSX.Element;
export declare class D extends React.Component<DP, State> {}
export declare const NotAComponent: { a: 1 };
export declare const lower: React.FC<LP>;
export declare const F: (props: FP) => React.ReactElement;
""")
        names = {c["name"]: c["props"] for c in f["components"]}
        self.assertEqual(list(names), ["A", "B", "M", "C", "D", "F"])
        self.assertEqual(names["A"]["name"], "AP")
        self.assertEqual(names["B"]["k"], "inter")
        self.assertEqual(names["M"]["name"], "MP")
        self.assertEqual(names["F"]["name"], "FP")

    def test_ambient_module_names_the_import_path(self):
        f = types_of('declare module "@fakeui/icons" {\n  export const Icon: React.FC<IP>;\n}\n')
        self.assertEqual(f["components"][0]["module"], "@fakeui/icons")

    def test_sources_need_export_and_look_like_components(self):
        f = types_of("""
export interface CardProps { title: string }
export const Card = ({ title }: CardProps) => <div>{title}</div>;
export const Fwd = React.forwardRef<HTMLDivElement, CardProps>((p, r) => <div ref={r} />);
export function Panel(props: PanelProps) { return <section />; }
const Hidden = (p: HP) => <i />;
export { Later };
const Later = (p: LP) => <b />;
export const util = (a: number) => a;
export const Util = (a: number) => a + 1;
""", "src/Card.tsx", "source")
        self.assertEqual([c["name"] for c in f["components"]], ["Card", "Fwd", "Panel", "Later"])

    def test_declarations_without_export_are_visible_in_dts(self):
        f = types_of("declare const G: React.FC<GP>;\n")
        self.assertEqual([c["name"] for c in f["components"]], ["G"])

    def test_deprecated_class_component_keeps_its_doc(self):
        f = types_of("/** @deprecated Use `Button` instead. */\nexport declare class Old extends React.Component<OP> {}\n")
        self.assertEqual(f["components"][0]["doc"]["deprecated"], "Use `Button` instead.")


class TypeAstTests(unittest.TestCase):
    def ast(self, text: str) -> Dict[str, Any]:
        t = toks("type X = " + text + ";", jsx=False)
        return typeast.parse_type(t, 3, t.n - 1)

    def test_shapes(self):
        self.assertEqual(self.ast("'a'"), {"k": "lit", "v": "a"})
        self.assertEqual(self.ast("-1"), {"k": "lit", "v": -1})
        self.assertEqual(self.ast("string[]")["k"], "arr")
        self.assertEqual(self.ast("(A | B)[]")["item"]["k"], "union")
        self.assertEqual(self.ast("[A, B]")["k"], "tuple")
        self.assertEqual(self.ast("`x-${string}`")["k"], "tpl")
        self.assertEqual(self.ast("A.B<C, D<E>>")["args"][1]["args"][0]["name"], "E")
        self.assertEqual(self.ast("() => void | string")["ret"]["k"], "union")
        self.assertEqual(self.ast("{ [K in keyof T]: T[K] }")["k"], "other")
        self.assertEqual(self.ast("keyof typeof x")["k"], "other")
        self.assertEqual(self.ast("{ a?: 1 | 2; b: string }")["members"][0]["optional"], True)


# ── MDX ──────────────────────────────────────────────────────────────────────

TABS_MDX = """import { Meta, Story, Canvas } from '@storybook/addon-docs';
import {
  Tabs,
  Tab,
} from '@fakeui/core';
export const note = 1;

<Meta title="Nav/Tabs" component={Tabs} argTypes={{ o: { options: ['h', 'v'] } }} />

# Tabs

Switch views. Don't nest.

<!-- hidden -->
<Canvas>
  <Story name="Default" args={{ o: 'h' }}>
    {(args) => <Tabs {...args} value="a"><Tab id="a">A</Tab></Tabs>}
  </Story>
  <Story name="Plain"><Tabs value="b" /></Story>
  <Story of={Other.Primary} />
</Canvas>

```jsx
<Tabs o="v" />
```
"""


class MdxTests(unittest.TestCase):
    def test_stories_mdx_gives_group_prose_and_imports(self):
        f = mdx.scan(TABS_MDX, "Tabs.stories.mdx")
        self.assertEqual(f["imports"]["Tab"]["from"], "@fakeui/core", "многострочный import собран")
        g = f["groups"][0]
        self.assertEqual((g["meta"]["title"], g["meta"]["component"]), ("Nav/Tabs", "Tabs"))
        self.assertEqual(g["meta"]["argTypes"]["o"]["options"], ["h", "v"])
        self.assertEqual([s["name"] for s in g["stories"]], ["Default", "Plain"], "<Story of> — не новая история")
        self.assertEqual(g["stories"][0]["args"], {"o": "h"})
        self.assertEqual(g["stories"][0]["usage"][0], ["Tabs", {"value": ["a"]}])
        self.assertTrue(g["stories"][1]["jsx"].startswith("<Tabs"))
        self.assertEqual(f["prose"], "# Tabs\n\nSwitch views. Don't nest.\n\n```jsx\n<Tabs o=\"v\" />\n```")

    def test_docs_mdx_has_doc_of_and_no_groups(self):
        f = mdx.scan("import * as BS from './B.stories';\n<Meta of={BS} />\n\n# B\n\nText.\n", "B.mdx")
        self.assertEqual(f["groups"], [])
        self.assertEqual(f["docOf"], {"of": "BS", "title": None, "component": None})
        self.assertEqual(f["prose"], "# B\n\nText.")
        self.assertTrue(f["imports"]["BS"]["ns"])

    def test_story_without_meta_warns(self):
        f = mdx.scan('<Story name="X"><A /></Story>\n', "x.mdx")
        self.assertEqual(f["groups"], [])
        self.assertIn("без <Meta>", f["warnings"][0])

    def test_frontmatter_and_comments_are_dropped(self):
        self.assertEqual(mdx.clean_prose("---\ntitle: x\n---\n\n<!-- c -->\nText\n\n\n\nMore\n"), "Text\n\nMore")
        self.assertIsNone(mdx.clean_prose("<!-- only -->\n"))


def scan_all(files: Dict[str, str]) -> List[Dict[str, Any]]:
    out = []
    for path, text in sorted(files.items()):
        if path.endswith(".mdx"):
            out.append(mdx.scan(text, path))
        elif path.endswith(".d.ts"):
            out.append(decl.scan(text, path, "types"))
        elif ".stories." in path:
            out.append(csf.scan(text, path))
        else:
            out.append(decl.scan(text, path, "source"))
    return out


def merged(files: Dict[str, str]) -> Dict[str, Any]:
    return merge.merge(scan_all(files))


def comp(res: Dict[str, Any], name: str) -> Dict[str, Any]:
    return res["components"][name]["contract"]


class DocsAttachTests(unittest.TestCase):
    STORIES = "export default { title: 'C/Btn', component: Btn };\nexport const A = {};\n"

    def test_attach_by_of_import(self):
        r = merged({"d/Btn.stories.tsx": self.STORIES,
                    "d/Btn.mdx": "import * as S from './Btn.stories';\n<Meta of={S} />\n\n# Btn\n\nPress it.\n"})
        self.assertEqual(r["components"]["Btn"]["details"]["description"], "Press it.")
        self.assertEqual(r["warnings"], [])

    def test_attach_by_component_by_title_and_by_neighbour(self):
        r = merged({"d/Btn.stories.tsx": self.STORIES, "e/c.mdx": "<Meta component={Btn} />\n\nBy component.\n"})
        self.assertIn("By component.", r["components"]["Btn"]["details"]["docs"])
        r = merged({"d/Btn.stories.tsx": self.STORIES, "e/t.mdx": '<Meta title="C/Btn" />\n\nBy title.\n'})
        self.assertIn("By title.", r["components"]["Btn"]["details"]["docs"])
        r = merged({"d/Btn.stories.tsx": self.STORIES, "d/Btn.mdx": "Neighbour text.\n"})
        self.assertIn("Neighbour text.", r["components"]["Btn"]["details"]["docs"])

    def test_attach_by_of_import_across_directories(self):
        # `of` — единственная связь: другой каталог, другое имя файла, нет component и title
        r = merged({"d/Btn.stories.tsx": self.STORIES,
                    "guides/Intro.mdx": "import * as S from '../d/Btn.stories';\n<Meta of={S} />\n\nGuide text.\n"})
        self.assertIn("Guide text.", r["components"]["Btn"]["details"]["docs"])
        self.assertEqual(r["warnings"], [])

    def test_unattached_docs_warn(self):
        r = merged({"d/Btn.stories.tsx": self.STORIES, "z/Other.mdx": "Orphan.\n"})
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("z/Other.mdx", r["warnings"][0])
        self.assertIsNone(r["components"]["Btn"]["details"], "ни прозы, ни примеров — подробностей нет")


# ── слияние ──────────────────────────────────────────────────────────────────

BTN_TYPES = """export interface BtnProps { variant?: 'a' | 'b'; size?: string; mode: number; label?: string; flag?: boolean }
export declare const Btn: React.FC<BtnProps>;
"""


class MergeTests(unittest.TestCase):
    def test_types_beat_argtypes_and_narrow_options_beat_plain_string(self):
        r = merged({"t.d.ts": BTN_TYPES, "s.stories.tsx": (
            "export default { title: 'X/Btn', component: Btn, argTypes: {"
            " variant: { options: ['a'] }, size: { options: ['s', 'm'] }, label: { control: 'text' },"
            " extra: { control: 'boolean' } } };\nexport const A = {};")})
        p = comp(r, "Btn")["props"]
        self.assertEqual(p["variant"], {"kind": "enum", "from": "types", "options": ["a", "b"]})
        self.assertEqual(p["size"], {"kind": "enum", "from": "argTypes", "options": ["s", "m"]})
        self.assertEqual(p["label"], {"kind": "string", "from": "types"})
        self.assertEqual(p["mode"], {"kind": "number", "from": "types", "required": True})
        self.assertEqual(p["extra"], {"kind": "boolean", "from": "argTypes"})
        self.assertTrue(comp(r, "Btn")["propsClosed"])

    def test_usage_infers_kind_when_nothing_else_is_known(self):
        r = merged({"s.stories.tsx": ("export default { title: 'X/W', component: W };\n"
                                      "export const A = { args: { n: 1, f: true, t: 'x', m: 1 } };\n"
                                      "export const B = { args: { m: 'y' } };")})
        p = comp(r, "W")["props"]
        self.assertEqual((p["n"]["kind"], p["f"]["kind"], p["t"]["kind"], p["m"]["kind"]),
                         ("number", "boolean", "string", "unknown"))
        self.assertEqual(p["n"]["from"], "usage")
        self.assertFalse(comp(r, "W")["propsClosed"], "истории без типов набор не закрывают")

    def test_used_values_are_clean_sorted_and_capped(self):
        many = ", ".join(f"{{ args: {{ v: 'v{i:02d}', children: 'copy' }} }}" for i in range(30))
        src = "export default { title: 'X/W', component: W };\n" + "".join(
            f"export const S{i} = {{ args: {{ v: 'v{i:02d}', children: 'copy', t: 'x'.repeat }} }};\n" for i in range(30))
        src += "export const L = { args: { v: 'a very long string value that is not a vocabulary word', n: 2, b: false } };\n"
        src += "export const M = { args: { n: 1, b: true } };\n"
        c = comp(merged({"s.stories.tsx": src}), "W")
        self.assertNotIn("children", c["usedValues"])
        self.assertEqual(len(c["usedValues"]["v"]), merge.MAX_USED_VALUES)
        self.assertEqual(c["usedCapped"], ["v"])
        self.assertEqual(c["usedValues"]["n"], [1, 2])
        self.assertEqual(c["usedValues"]["b"], [False, True], "порядок: булевы, числа, строки")
        self.assertNotIn("a very long", json.dumps(c["usedValues"]))
        self.assertIsNotNone(many)

    def test_import_is_bare_most_frequent_and_never_relative(self):
        r = merged({"a.stories.tsx": "import { X } from '@p/ui';\nexport default { title: 'A/X', component: X };\nexport const S = {};",
                    "b.stories.tsx": "import { X } from '@p/ui/x';\nexport default { title: 'B/X', component: X };\nexport const S = {};",
                    "c.stories.tsx": "import { X } from '@p/ui';\nexport default { title: 'C/Y', component: Y };\nexport const S = {};",
                    "d.stories.tsx": "import { Z } from './Z';\nexport default { title: 'D/Z', component: Z };\nexport const S = {};",
                    "e.stories.tsx": "import { Q } from '@/Q';\nexport default { title: 'E/Q', component: Q };\nexport const S = {};",
                    "f.stories.tsx": "import R from '@p/ui/R';\nexport default { title: 'F/R', component: R };\nexport const S = {};"})
        self.assertEqual(comp(r, "X")["import"], {"path": "@p/ui", "named": "X"}, "2 против 1: чаще")
        self.assertIsNone(comp(r, "Z")["import"])
        self.assertIsNone(comp(r, "Q")["import"], "алиас `@/` — не пакет")
        self.assertEqual(comp(r, "R")["import"], {"path": "@p/ui/R", "default": True})

    def test_ambient_module_name_is_the_only_import_evidence_in_declarations(self):
        r = merged({"t.d.ts": 'declare module "@x/ui" {\n  export const A: React.FC<AP>;\n}\nexport declare const B: React.FC<BP>;\n'})
        self.assertEqual(comp(r, "A")["import"], {"path": "@x/ui", "named": "A"})
        self.assertIsNone(comp(r, "B")["import"], "обычный экспорт .d.ts пути пакета не называет")

    def test_namespace_import_strips_the_prefix(self):
        r = merged({"a.stories.tsx": "import * as Ui from '@p/ui';\nexport default { title: 'A/B', component: Ui.Btn };\nexport const S = {};"})
        self.assertEqual(comp(r, "Btn")["import"], {"path": "@p/ui", "named": "Btn"})

    def test_component_inferred_from_jsx_roots(self):
        r = merged({"a.stories.jsx": "export default { title: 'L/Card' };\nexport const A = () => <Card x=\"1\" />;\n",
                    "b.stories.jsx": "export default { title: 'L/Mixed' };\nexport const A = () => <P />;\n"
                                     "export const B = () => <Q />;\n"})
        self.assertIn("Card", r["components"])
        self.assertNotIn("Q", r["components"])
        self.assertTrue(any("b.stories.jsx" in w and "без компонента" in w for w in r["warnings"]))

    def test_other_components_stories_feed_used_values_of_known_components(self):
        r = merged({"a.stories.jsx": "export default { title: 'A/Btn', component: Btn };\nexport const S = {};",
                    "b.stories.jsx": "export default { title: 'B/Modal', component: Modal };\n"
                                     "export const S = () => <Modal><Btn variant=\"ghost\" /></Modal>;"})
        self.assertEqual(comp(r, "Btn")["usedValues"], {"variant": ["ghost"]})

    def test_conflicting_titles_are_merged_with_warning(self):
        r = merged({"a.stories.jsx": "export default { title: 'A/Btn', component: Btn };\nexport const S = {};",
                    "b.stories.jsx": "export default { title: 'B/Btn', component: Btn };\nexport const T = {};"})
        self.assertEqual(comp(r, "Btn")["stories"], 2)
        self.assertEqual(comp(r, "Btn")["title"], "A/Btn")
        self.assertTrue(any("разными title" in w for w in r["warnings"]))

    def test_deprecations_and_replacement(self):
        r = merged({"t.d.ts": ("export interface P { /** @deprecated Use `variant=\"ghost\"` instead. */ flat?: boolean;"
                               " /** @deprecated */ old?: string }\n"
                               "/** @deprecated Replaced by NewBtn. */\nexport declare const Btn: React.FC<P>;")})
        deps = r["components"]["Btn"]["details"]["deprecations"]
        self.assertEqual([(d["target"], d["name"], d["replacement"]) for d in deps],
                         [("component", "Btn", "NewBtn"), ("prop", "flat", 'variant="ghost"'), ("prop", "old", None)])
        self.assertEqual(deps[2]["message"], "")

    def test_examples_inline_args_synthesize_and_cap(self):
        src = ("export default { title: 'X/Btn', component: Btn, args: { label: 'L' } };\n"
               "export const A = { args: { variant: 'a', disabled: true, n: 3, off: false, cb: fn(), "
               "items: ['x', 'y'], children: 'Go' } };\n"
               "export const B = { render: (args) => <Btn {...args} size=\"lg\" /> };\n"
               "export const C = { render: () => <Btn x=\"1\">hi</Btn> };\n")
        ex = merged({"s.stories.tsx": src})["components"]["Btn"]["details"]["examples"]
        self.assertEqual(ex[0]["jsx"], "<Btn label=\"L\" variant=\"a\" disabled n={3} off={false} items={['x', 'y']}>Go</Btn>")
        self.assertEqual(ex[1]["jsx"], '<Btn label="L" size="lg" />')
        self.assertEqual(ex[2]["jsx"], '<Btn x="1">hi</Btn>')
        many = "export default { title: 'X/Btn', component: Btn };\n" + "".join(
            f"export const S{i} = {{ args: {{ k: {i} }} }};\n" for i in range(20))
        self.assertEqual(len(merged({"s.stories.tsx": many})["components"]["Btn"]["details"]["examples"]),
                         merge.MAX_EXAMPLES)


# ── интерпретация типов и argTypes ───────────────────────────────────────────

def tt_of(src: str, path: str = "t.d.ts") -> interp.TypeTable:
    return interp.TypeTable([decl.scan(src, path, "types")])


def type_ast(text: str) -> Dict[str, Any]:
    t = toks("type X = " + text + ";", jsx=False)
    return typeast.parse_type(t, 3, t.n - 1)


class InterpTests(unittest.TestCase):
    def kind(self, text: str, table: Optional[interp.TypeTable] = None):
        return interp.classify(type_ast(text), "t.d.ts", table or interp.TypeTable([]))

    def test_classify_primitives_slots_and_functions(self):
        self.assertEqual(self.kind("string"), ("string", None))
        self.assertEqual(self.kind("number | undefined"), ("number", None))
        self.assertEqual(self.kind("React.ReactNode"), ("node", None))
        self.assertEqual(self.kind("JSX.Element | null"), ("node", None))
        self.assertEqual(self.kind("(e: Event) => void"), ("function", None))
        self.assertEqual(self.kind("React.MouseEventHandler<HTMLButtonElement>"), ("function", None))
        self.assertEqual(self.kind("Array<string>"), ("array", None))
        self.assertEqual(self.kind("string[]"), ("array", None))
        self.assertEqual(self.kind("{ a: 1 }"), ("object", None))
        self.assertEqual(self.kind("`a-${string}`"), ("string", None))
        self.assertEqual(self.kind("any"), ("unknown", None))
        self.assertEqual(self.kind("keyof typeof X"), ("other", None))

    def test_classify_unions(self):
        self.assertEqual(self.kind("'a' | 'b' | undefined"), ("enum", ["a", "b"]))
        self.assertEqual(self.kind("1 | 2"), ("enum", [1, 2]))
        self.assertEqual(self.kind("true | false"), ("boolean", None))
        self.assertEqual(self.kind("boolean | 'auto'"), ("enum", [True, False, "auto"]))
        self.assertEqual(self.kind("'a' | string"), ("string", None), "литералы + string — не закрытый набор")
        self.assertEqual(self.kind("'a' | (string & {})"), ("string", None))
        self.assertEqual(self.kind("string | React.ReactNode"), ("node", None))

    def test_classify_follows_aliases_and_enums(self):
        t = tt_of("type S = 'x' | 'y';\ntype Z = S;\nenum E { A = 'a', B = 'b' }\nenum N { A, B }\ninterface I { a: 1 }\n")
        self.assertEqual(self.kind("Z", t), ("enum", ["x", "y"]))
        self.assertEqual(self.kind("E", t), ("enum", ["a", "b"]))
        self.assertEqual(self.kind("N", t), ("enum", [0, 1]))
        self.assertEqual(self.kind("I", t), ("object", None))
        self.assertEqual(self.kind("Unknown", t), ("other", None))

    def props(self, text: str, src: str = ""):
        t = tt_of(src)
        return interp.resolve_props(type_ast(text), "t.d.ts", t)

    def test_resolve_props_utilities(self):
        src = "interface B { a: 1; b?: 2; c: 3 }\ninterface C extends B { d: 4 }\n"
        p, closed = self.props("Omit<B, 'a' | 'b'>", src)
        self.assertEqual((list(p), closed), (["c"], True))
        p, closed = self.props("Pick<B, 'a'>", src)
        self.assertEqual(list(p), ["a"])
        p, closed = self.props("Partial<B>", src)
        self.assertTrue(all(m["optional"] for m in p.values()))
        p, closed = self.props("React.PropsWithChildren<B>", src)
        self.assertEqual(list(p), ["a", "b", "c", "children"])
        p, closed = self.props("C", src)
        self.assertEqual((list(p), closed), (["d", "a", "b", "c"], True))
        p, closed = self.props("B & { e: 5 } & React.RefAttributes<X>", src)
        self.assertEqual((list(p), closed), (["a", "b", "c", "e"], True))

    def test_external_unresolved_and_index_signature_open_the_set(self):
        for text in ("React.HTMLAttributes<HTMLDivElement>", "Unknown", "Omit<React.ButtonHTMLAttributes<X>, 'a'>",
                     "{ a: 1; [k: string]: unknown }", "Record<string, number>"):
            self.assertFalse(self.props(text)[1], text)
        _, closed = self.props("B & React.HTMLAttributes<X>", "interface B { a: 1 }")
        self.assertFalse(closed, "одна открытая часть открывает пересечение")
        p, closed = self.props("A", "interface A extends A { a: 1 }")
        self.assertEqual(list(p), ["a"], "цикл extends не зависает")

    def test_argtype_info(self):
        a = interp.argtype_info
        self.assertEqual(a({"control": "select", "options": ["x", "y"], "description": " Look\n here "}),
                         {"options": ["x", "y"], "kind": "enum", "description": "Look here"})
        self.assertEqual(a({"control": {"type": "radio", "options": [1, 2]}})["options"], [1, 2])
        self.assertEqual(a({"control": "boolean"}), {"kind": "boolean"})
        self.assertEqual(a({"control": "range"})["kind"], "number")
        self.assertEqual(a({"control": False}), {})
        self.assertEqual(a({"action": "clicked"})["kind"], "function")
        self.assertEqual(a({"type": {"name": "enum", "value": ["'a'", "'b'"]}})["options"], ["a", "b"])
        self.assertEqual(a({"type": {"name": "string", "required": True}}), {"kind": "string", "required": True})
        t = a({"table": {"type": {"summary": "'a' | 'b'"}, "defaultValue": {"summary": "'a'"}}})
        self.assertEqual((t["options"], t["default"]), (["a", "b"], "a"))
        self.assertEqual(a({"table": {"defaultValue": {"summary": "true"}}})["default"], True)
        self.assertEqual(a({"defaultValue": 3})["default"], 3)
        self.assertNotIn("options", a({"options": [{"$": "opaque"}, "x"]}), "невычислимое — не контракт")
        self.assertNotIn("options", a({"options": {"$": "ref", "name": "opts"}}))

    def test_parse_union_text(self):
        p = interp.parse_union_text
        self.assertEqual(p("'a' | \"b\" | 3"), ("enum", ["a", "b", 3]))
        self.assertEqual(p("boolean"), ("boolean", None))
        self.assertEqual(p("() => void"), ("function", None))
        self.assertEqual(p("string"), ("string", None))
        self.assertEqual(p("Foo<Bar>"), ("other", None))

    def test_replacement_of(self):
        r = interp.replacement_of
        self.assertEqual(r('Use `variant="ghost"` instead.'), 'variant="ghost"')
        self.assertEqual(r("Use NewButton instead"), "NewButton")
        self.assertEqual(r("Replaced by `Chip`."), "Chip")
        self.assertEqual(r("Instead, use Chip."), "Chip")
        self.assertEqual(r("Prefer `Link`"), "Link")
        self.assertIsNone(r("Will be removed."))
        self.assertIsNone(r(""))

    def test_synth_example_quotes_and_markers(self):
        e = interp.synth_example("B", {"a": 'say "hi"', "b": {"$": "jsx", "text": "<I\n  x=\"1\"\n/>"},
                                       "c": {"$": "opaque"}, "d": None, "children": {"$": "jsx", "text": "<b>x</b>"}})
        self.assertEqual(e, "<B a={'say \"hi\"'} b={<I x=\"1\" />}><b>x</b></B>")


# ── фикстура-библиотека: контракт и детали по составу ───────────────────────

ALL_NAMES = ["Avatar", "Badge", "Button", "Card", "Divider", "Icon", "Legacy", "Tabs", "Tag", "Tooltip"]


class FixtureTests(Tmp):
    def built(self, source: str):
        root = fixture_repo(self.root, source)
        res = T.call("daiver_ds_requirements", root, {})
        c, d = docs(root)
        return res, c["components"], d["components"]

    def test_stories_mode_composition(self):
        res, c, d = self.built("stories")
        self.assertEqual(sorted(c), ALL_NAMES)
        self.assertEqual((res["files"], res["components"], res["stories"], res["parseErrors"], res["warnings"]),
                         (8, 10, 12, 0, []))
        self.assertEqual(sorted(d), ["Avatar", "Badge", "Button", "Card", "Legacy", "Tabs", "Tag", "Tooltip"])
        self.assertEqual({n: x["stories"] for n, x in c.items() if x["stories"]},
                         {"Badge": 2, "Button": 4, "Card": 2, "Tabs": 2, "Tooltip": 2})
        self.assertEqual(res["propsClosed"], 4, "закрыты только компоненты с полностью разобранным TS-типом")
        self.assertEqual(sorted(n for n, x in c.items() if x["propsClosed"]), ["Avatar", "Divider", "Icon", "Legacy"])

    def test_csf3_component_with_mdx_docs(self):
        _, c, d = self.built("stories")
        b = c["Button"]
        self.assertEqual((b["title"], b["taxonomy"], b["import"]),
                         ("Components/Inputs/Button", ["Components", "Inputs"], {"path": "@fakeui/core", "named": "Button"}))
        self.assertEqual(b["props"], {
            "disabled": {"kind": "boolean", "from": "argTypes"},
            "label": {"kind": "string", "from": "usage"},
            "size": {"kind": "enum", "from": "argTypes", "options": ["sm", "md", "lg"]},
            "variant": {"kind": "enum", "from": "argTypes", "options": ["primary", "secondary", "ghost"]}})
        self.assertFalse(b["propsClosed"], "без типов набор пропсов неполон")
        self.assertEqual(b["usedValues"], {"disabled": [True], "label": ["Button", "Don't click"], "size": ["lg"],
                                           "variant": ["ghost", "primary", "secondary"]})
        x = d["Button"]
        self.assertEqual(x["description"], "Buttons trigger actions.", "parameters.docs.description.component")
        self.assertIn("Use one primary button per view.", x["docs"], "проза MDX привязана по `Meta of`")
        self.assertEqual(x["props"], {"size": {"default": "md"}, "variant": {"description": "Visual style"}})
        self.assertEqual([(e["name"], e["jsx"]) for e in x["examples"]], [
            ("Primary", '<Button label="Don\'t click" variant="primary" />'),
            ("Big one", '<Button label="Button" size="lg" />'),
            ("With Icon", '<Button label="Button" variant="ghost" icon={<Icon name="plus" />}>Add</Button>'),
            ("Disabled", '<Button label="Button" disabled variant="secondary" />')])

    def test_csf2_csf1_stories_of_and_mdx_stories(self):
        _, c, d = self.built("stories")
        self.assertEqual(c["Badge"]["props"], {"tone": {"kind": "enum", "from": "argTypes",
                                                        "options": ["info", "success", "warning", "danger"]}})
        self.assertEqual(c["Badge"]["usedValues"], {"tone": ["info", "success"]})
        self.assertEqual([(e["name"], e["jsx"]) for e in d["Badge"]["examples"]],
                         [("Primary", '<Badge tone="info">New</Badge>'),
                          ("Success tone", '<Badge tone="success">New</Badge>')])
        card = c["Card"]
        self.assertEqual((card["title"], card["import"]), ("Layout/Card", None), "относительный импорт — не пакет")
        self.assertEqual(card["props"], {"elevated": {"kind": "boolean", "from": "usage"},
                                         "title": {"kind": "string", "from": "usage"}})
        self.assertEqual(c["Tooltip"]["usedValues"], {"content": ["Another hint", "Hint"], "placement": ["bottom", "top"]})
        self.assertEqual([e["name"] for e in d["Tooltip"]["examples"]], ["top", "bottom"])
        tabs = c["Tabs"]
        self.assertEqual(tabs["props"]["orientation"], {"kind": "enum", "from": "argTypes",
                                                        "options": ["horizontal", "vertical"]})
        self.assertEqual(tabs["usedValues"]["value"], ["a", "b"])
        self.assertIn("Don't nest them.", d["Tabs"]["docs"])

    def test_declarations_without_stories(self):
        _, c, d = self.built("stories")
        av = c["Avatar"]
        self.assertEqual(av["props"], {
            "initials": {"kind": "string", "from": "types"},
            "shape": {"kind": "enum", "from": "types", "options": ["circle", "square"]},
            "size": {"kind": "enum", "from": "types", "options": ["xs", "sm", "md", "lg"]},
            "src": {"kind": "string", "from": "types"}})
        self.assertTrue(av["propsClosed"])
        self.assertEqual(d["Avatar"]["description"], "Round user picture.")
        tag = c["Tag"]
        self.assertEqual(sorted(tag["props"]), ["className", "color", "removable"], "extends BaseProps разворачивается")
        self.assertFalse(tag["propsClosed"], "Omit<React.HTMLAttributes> — внешний тип, набор открыт")
        self.assertEqual(c["Divider"]["props"]["orientation"]["options"], ["horizontal", "vertical"])
        icon = c["Icon"]
        self.assertEqual(icon["import"], {"path": "@fakeui/icons", "named": "Icon"}, "имя `declare module` — import-путь")
        self.assertEqual(icon["props"]["name"], {"kind": "enum", "from": "types", "required": True,
                                                 "options": ["plus", "minus", "close"]})
        self.assertEqual(icon["props"]["size"]["options"], [16, 24])
        self.assertEqual(icon["usedValues"], {"name": ["plus"]}, "значение из JSX чужой истории (Button/WithIcon)")
        self.assertEqual(d["Legacy"]["deprecations"], [{"target": "component", "name": "Legacy",
                         "message": "Use `Button` instead.", "replacement": "Button"}])

    def test_sources_mode_adds_typed_contract(self):
        res, c, d = self.built("sources")
        self.assertEqual((res["files"], res["propsClosed"], res["withDeprecations"]), (9, 5, 2))
        b = c["Button"]
        self.assertTrue(b["propsClosed"])
        self.assertEqual({n: p["kind"] for n, p in b["props"].items()}, {
            "disabled": "boolean", "flat": "boolean", "icon": "node", "label": "string", "loading": "boolean",
            "onClick": "function", "size": "enum", "variant": "enum"})
        self.assertEqual(b["props"]["variant"], {"kind": "enum", "from": "types",
                                                 "options": ["primary", "secondary", "ghost"]}, "alias ButtonVariant")
        self.assertEqual(d["Button"]["deprecations"], [{"target": "prop", "name": "flat",
                         "message": 'Use `variant="ghost"` instead.', "replacement": 'variant="ghost"'}])
        self.assertEqual(d["Button"]["props"]["label"], {"description": "Text shown inside the button."})
        self.assertEqual(d["Button"]["props"]["size"], {"default": "md", "description": "Control size."})
        self.assertEqual(d["Button"]["props"]["variant"], {"description": "Visual style"}, "argTypes сильнее JSDoc")

    def test_stories_mode_does_not_read_plain_sources(self):
        res, c, _ = self.built("stories")
        self.assertEqual(res["files"], 8)
        self.assertNotIn("flat", c["Button"]["props"])


# ── инструмент: условия, потолки, безопасность пути ─────────────────────────

def call(root, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return T.call("daiver_ds_requirements", Path(root), args or {})


def tree(root: Path) -> Dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


class ToolTests(Tmp):
    def test_storybook_mcp_is_refused_and_nothing_is_written(self):
        root = init_repo(self.root, "storybook-mcp")
        with self.assertRaises(T.ToolError) as cm:
            call(root)
        self.assertIn("storybook-mcp", str(cm.exception))
        self.assertIn("INV-A6", str(cm.exception))
        self.assertFalse((root / DR.CONTRACT_REL).exists() or (root / DR.DETAILS_REL).exists())
        self.assertEqual(call(root, {"check": True})["state"], "not-needed")

    def test_stories_and_sources_build_both_documents(self):
        for source in ("stories", "sources"):
            with self.subTest(source), tempfile.TemporaryDirectory() as tmp:
                root = fixture_repo(tmp, source)
                res = call(root)
                self.assertEqual((res["ok"], res["librarySource"], res["scanner"]), (True, source, "py/1"))
                self.assertTrue((root / DR.CONTRACT_REL).is_file() and (root / DR.DETAILS_REL).is_file())

    def test_not_initialized_project_is_refused(self):
        write(self.root / "package.json", "{}")
        with self.assertRaises(T.ToolError) as cm:
            call(self.root)
        self.assertIn("daiver_init", str(cm.exception))
        self.assertEqual(call(self.root, {"check": True})["state"], "unconfigured")

    def test_zero_components_is_an_error_and_keeps_existing_documents(self):
        root = init_repo(self.root, "stories", ("lib",))
        write(root / "lib" / "dist" / "index.js", "export const a = 1;\n")
        write(root / "lib" / "helpers.ts", "export const b = 2;\n")
        write(root / DR.CONTRACT_REL, "KEEP")
        with self.assertRaises(T.ToolError) as cm:
            call(root)
        msg = str(cm.exception)
        self.assertIn("не найдено ни одного компонента", msg)
        self.assertIn("Сканпути неверны", msg)
        self.assertEqual((root / DR.CONTRACT_REL).read_text(encoding="utf-8"), "KEEP", "пустой контракт не пишется")
        self.assertFalse((root / DR.DETAILS_REL).exists())

    def test_scan_paths_must_exist_and_stay_inside_the_repo(self):
        for sp, want in (("nope", "пути нет на диске"), ("../outside", "без «..»"),
                         (str(Path(tempfile.gettempdir()).resolve()), "выходит за корень")):
            with self.subTest(sp), tempfile.TemporaryDirectory() as tmp:
                root = init_repo(tmp, "stories", (sp,))
                with self.assertRaises(T.ToolError) as cm:
                    call(root)
                self.assertIn(want, str(cm.exception))

    def test_too_many_files_is_refused_not_truncated(self):
        root = fixture_repo(self.root)
        with mock.patch.object(DR, "MAX_FILES", 3):
            with self.assertRaises(T.ToolError) as cm:
                call(root)
        self.assertIn("8 > 3", str(cm.exception))
        self.assertFalse((root / DR.CONTRACT_REL).exists())

    def test_oversize_and_non_utf8_files_are_skipped_with_reason(self):
        root = fixture_repo(self.root)
        write(root / "lib/src/Big/Big.stories.tsx", "export default { title: 'B/Big', component: Big };\n"
              + "export const A = {};\n" * 100)
        (root / "lib/types/bad.d.ts").write_bytes(b"export declare const X: \xff\xfe;")
        with mock.patch.object(DR, "MAX_FILE_BYTES", 1500):
            res = call(root)
        reasons = {s["path"]: s["reason"] for s in res["skipped"]}
        self.assertEqual(reasons["lib/src/Big/Big.stories.tsx"], "too-large")
        self.assertEqual(reasons["lib/types/bad.d.ts"], "not-utf8")
        self.assertNotIn("Big", docs(root)[0]["components"])
        self.assertEqual(docs(root)[0]["source"]["skipped"], res["skipped"])

    def test_symlink_leading_outside_the_repo_is_not_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            write(Path(outside) / "Evil.stories.tsx", "export default { title: 'E/Evil', component: Evil };\nexport const A = {};\n")
            root = fixture_repo(self.root)
            os.symlink(outside, root / "lib" / "src" / "linked")
            res = call(root)
        self.assertNotIn("Evil", docs(root)[0]["components"])
        self.assertEqual([s for s in res["skipped"] if s["reason"] == "outside-repo"],
                         [{"path": "lib/src/linked", "reason": "outside-repo"}], "в документ не попадают пути машины")

    def test_nested_node_modules_and_dot_dirs_are_skipped_unless_named(self):
        root = fixture_repo(self.root)
        write(root / "lib/src/node_modules/x/Bad.stories.tsx", "export default { title: 'N/Bad', component: Bad };\nexport const A = {};\n")
        write(root / "lib/src/.hidden/H.stories.tsx", "export default { title: 'N/H', component: H };\nexport const A = {};\n")
        call(root)
        self.assertEqual(sorted(docs(root)[0]["components"]), ALL_NAMES)
        # библиотека, поставленная пакетом: scanPaths указывает прямо в node_modules — это разрешено
        pkg = init_repo(Path(tempfile.mkdtemp(dir=str(self.root))), "sources", ("node_modules/@fakeui/core/dist",))
        write(pkg / "node_modules/@fakeui/core/dist/index.d.ts", "export interface AP { a?: 1 | 2 }\nexport declare const A: React.FC<AP>;\n")
        self.assertEqual(call(pkg)["names"], ["A"])

    def test_one_broken_file_does_not_kill_the_scan_but_bugs_are_loud(self):
        root = fixture_repo(self.root)
        real = csf.scan

        def flaky(src, path):
            if path.endswith("Card.stories.jsx"):
                raise IndexError("boom")
            return real(src, path)
        with mock.patch.object(csf, "scan", flaky):
            res = call(root)
        self.assertEqual(res["parseErrors"], 1)
        self.assertTrue(any("Card.stories.jsx" in w and "не разобран" in w for w in res["warnings"]))
        self.assertNotIn("Card", docs(root)[0]["components"])

        def buggy(src, path):
            raise NameError("lost_function")
        with mock.patch.object(csf, "scan", buggy):
            with self.assertRaises(NameError):
                call(root)

    def test_arguments_are_validated(self):
        root = fixture_repo(self.root)
        for args, want in (({"outPath": "x.json"}, "неизвестный аргумент"), ({"check": "yes"}, "ожидается boolean"),
                           ({"engine": "ruby"}, "python | node")):
            with self.subTest(args), self.assertRaises(T.ToolError) as cm:
                call(root, args)
            self.assertIn(want, str(cm.exception))
        schema = T.TOOLS["daiver_ds_requirements"]["schema"]
        self.assertEqual(sorted(schema["properties"]), ["check", "engine"])
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse((root / DR.CONTRACT_REL).exists(), "отказ по аргументам ничего не пишет")


# ── детерминизм, хеш источников, check ──────────────────────────────────────

class StateTests(Tmp):
    def test_same_input_gives_same_bytes_in_another_root_and_creation_order(self):
        a = fixture_repo(self.root / "a")
        call(a)
        b = init_repo(self.root / "b")
        files = sorted(p for p in FIXTURE.rglob("*") if p.is_file())
        for p in reversed(files):                              # другой порядок создания файлов
            write(b / "lib" / p.relative_to(FIXTURE), p.read_text(encoding="utf-8"))
        call(b)
        for rel in (DR.CONTRACT_REL, DR.DETAILS_REL):
            self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(), rel.as_posix())

    def test_crlf_checkout_gives_the_same_documents_and_hash(self):
        lf = fixture_repo(self.root / "lf")
        crlf = init_repo(self.root / "crlf")
        for p in sorted(FIXTURE.rglob("*")):
            if p.is_file():
                dest = crlf / "lib" / p.relative_to(FIXTURE)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(p.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
        call(lf)
        call(crlf)
        for rel in (DR.CONTRACT_REL, DR.DETAILS_REL):
            self.assertEqual((lf / rel).read_bytes(), (crlf / rel).read_bytes(), rel.as_posix())

    def test_second_run_is_idempotent(self):
        root = fixture_repo(self.root)
        self.assertTrue(call(root)["changed"])
        before = tree(root)
        res = call(root)
        self.assertFalse(res["changed"])
        self.assertEqual(tree(root), before)
        self.assertFalse(list((root / ".daiver").glob("*.tmp")), "временные файлы не остаются")

    def test_documents_have_stable_readable_format(self):
        root = fixture_repo(self.root)
        call(root)
        text = (root / DR.CONTRACT_REL).read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        self.assertIn('"options": ["primary", "secondary", "ghost"]', text, "массив скаляров — в одну строку")
        self.assertEqual(list(json.loads(text)), ["components", "daiver", "source"], "ключи по алфавиту")
        self.assertNotIn(str(self.root), text, "в документ не попадают пути машины")
        self.assertEqual(json.loads(text)["daiver"], {"doc": "contract", "role": "ds-requirements", "schema": 1,
                                                      "version": json.loads(text)["daiver"]["version"]})

    def test_sources_hash_reacts_to_edit_add_remove_and_rename(self):
        root = fixture_repo(self.root)
        call(root)

        def sha():
            return docs(root)[0]["source"]["sha256"]
        base = sha()
        story = root / "lib/src/Badge/Badge.stories.jsx"
        text = story.read_text(encoding="utf-8")
        story.write_text(text + "// edit\n", encoding="utf-8")
        call(root)
        edited = sha()
        self.assertNotEqual(edited, base)
        story.write_text(text, encoding="utf-8")
        call(root)
        self.assertEqual(sha(), base, "возврат содержимого возвращает хеш")
        story.rename(story.with_name("Badge2.stories.jsx"))
        call(root)
        self.assertNotEqual(sha(), base, "переименование меняет хеш (путь входит в него)")
        story.with_name("Badge2.stories.jsx").unlink()
        call(root)
        self.assertNotEqual(sha(), base)
        self.assertNotIn("Badge", docs(root)[0]["components"])
        write(root / "lib/src/Extra/Extra.stories.jsx", "export default { title: 'X/Extra', component: Extra };\nexport const A = {};\n")
        call(root)
        self.assertIn("Extra", docs(root)[0]["components"])

    def test_check_ok_stale_missing_invalid(self):
        root = fixture_repo(self.root)
        call(root)
        ok = call(root, {"check": True})
        self.assertEqual((ok["state"], ok["components"], ok["scanner"]), ("ok", 10, "py/1"))
        before = tree(root)
        call(root, {"check": True})
        self.assertEqual(tree(root), before, "check только читает")

        story = root / "lib/src/Tabs/Tabs.stories.mdx"
        story.write_text(story.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
        st = call(root, {"check": True})
        self.assertEqual(st["state"], "stale")
        self.assertIn("хеш источников не совпал", st["reasons"][0])
        self.assertEqual(call(root, {"check": True})["state"], "stale")
        call(root)
        self.assertEqual(call(root, {"check": True})["state"], "ok")

        (root / DR.DETAILS_REL).unlink()
        self.assertEqual(call(root, {"check": True})["state"], "missing")
        call(root)
        (root / DR.CONTRACT_REL).write_text("{not json", encoding="utf-8")
        st = call(root, {"check": True})
        self.assertEqual(st["state"], "invalid")
        self.assertIn("не читается как JSON", st["problems"][0])

    def test_check_detects_mixed_pair_and_config_changes(self):
        root = fixture_repo(self.root)
        call(root)
        details = json.loads((root / DR.DETAILS_REL).read_text(encoding="utf-8"))
        details["source"]["sha256"] = "0" * 64
        (root / DR.DETAILS_REL).write_text(DR.dumps(details), encoding="utf-8")
        st = call(root, {"check": True})
        self.assertEqual(st["state"], "invalid")
        self.assertIn("пара собрана не за один прогон", st["problems"][0])
        call(root)
        cfg = root / ".daiver/config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("source: \"stories\"", "source: \"sources\""), encoding="utf-8")
        st = call(root, {"check": True})
        self.assertEqual(st["state"], "stale")
        self.assertIn("library.source изменился", st["reasons"][0])

    def test_check_vanished_scan_path_is_stale_not_a_crash(self):
        root = fixture_repo(self.root)
        call(root)
        shutil.rmtree(root / "lib" / "types")
        st = call(root, {"check": True})
        self.assertEqual(st["state"], "stale")
        self.assertIn("пути нет на диске", st["reasons"][0])


# ── валидаторы ───────────────────────────────────────────────────────────────

class ValidatorTests(Tmp):
    def setUp(self):
        super().setUp()
        root = fixture_repo(self.root, "sources")
        call(root)
        self.contract, self.details = docs(root)

    def mutate(self, doc: str, fn) -> List[str]:
        import copy
        c, d = copy.deepcopy(self.contract), copy.deepcopy(self.details)
        fn(c if doc == "c" else d)
        if doc == "c":
            return DR.validate_contract(c)
        if doc == "d":
            return DR.validate_details(d)
        return DR.validate_pair(c, d)

    def test_built_documents_are_valid(self):
        self.assertEqual(DR.validate_contract(self.contract), [])
        self.assertEqual(DR.validate_details(self.details), [])
        self.assertEqual(DR.validate_pair(self.contract, self.details), [])

    def test_contract_rejects_unknown_fields_and_wrong_versions(self):
        self.assertIn("неизвестное поле", self.mutate("c", lambda c: c["components"]["Button"].update(extra=1))[0])
        self.assertIn("неизвестное поле", self.mutate("c", lambda c: c["components"]["Button"]["props"]["label"].update(x=1))[0])
        self.assertTrue(self.mutate("c", lambda c: c["daiver"].update(schema=2)))
        self.assertTrue(self.mutate("c", lambda c: c["daiver"].update(role="canonical")))
        self.assertTrue(self.mutate("c", lambda c: c["source"].update(sha256="abc")))
        self.assertTrue(self.mutate("c", lambda c: c["source"].update(scanner="regex/1")))
        self.assertTrue(self.mutate("c", lambda c: c["components"]["Button"]["props"]["label"].update(kind="widget")))

    def test_contract_options_exactly_for_enum(self):
        p = lambda c: c["components"]["Button"]["props"]                      # noqa: E731
        self.assertIn("options обязательны у kind=enum", self.mutate("c", lambda c: p(c)["variant"].pop("options"))[0])
        self.assertIn("запрещены у остальных", self.mutate("c", lambda c: p(c)["label"].update(options=["a"]))[0])
        self.assertIn("пусты или с повторами", self.mutate("c", lambda c: p(c)["variant"].update(options=["a", "a"]))[0])
        self.assertTrue(self.mutate("c", lambda c: p(c)["variant"].update(options=[])))

    def test_contract_import_must_be_a_bare_specifier(self):
        imp = lambda c, v: c["components"]["Button"].update(**{"import": v})  # noqa: E731
        self.assertIn("не bare specifier", self.mutate("c", lambda c: imp(c, {"path": "./Button", "named": "Button"}))[0])
        self.assertIn("не bare specifier", self.mutate("c", lambda c: imp(c, {"path": "@/Button", "named": "Button"}))[0])
        self.assertIn("ровно одно", self.mutate("c", lambda c: imp(c, {"path": "@x/ui", "named": "B", "default": True}))[0])
        self.assertIn("ровно одно", self.mutate("c", lambda c: imp(c, {"path": "@x/ui"}))[0])
        self.assertEqual(self.mutate("c", lambda c: imp(c, None)), [])

    def test_zero_components_is_not_a_contract(self):
        self.assertIn("ноль компонентов", self.mutate("c", lambda c: c["components"].clear())[0])

    def test_details_schema(self):
        self.assertIn("неизвестное поле", self.mutate("d", lambda d: d["components"]["Button"].update(x=1))[0])
        self.assertTrue(self.mutate("d", lambda d: d["components"]["Button"]["deprecations"][0].update(target="enum")))
        self.assertTrue(self.mutate("d", lambda d: d["components"]["Button"]["examples"][0].pop("jsx")))

    def test_pair_consistency(self):
        self.assertIn("пара собрана не за один прогон", self.mutate("p", lambda d: d["source"].update(sha256="1" * 64))[0])
        self.assertIn("нет в контракте", self.mutate("p", lambda d: d["components"].update(Ghost=d["components"]["Button"]))[0])
        self.assertIn("вне контракта", self.mutate("p", lambda d: d["components"]["Button"]["deprecations"][0].update(name="nope"))[0])
        self.assertIn("пропа нет в контракте", self.mutate("p", lambda d: d["components"]["Button"]["props"].update(nope={"description": "x"}))[0])


# ── MCP ──────────────────────────────────────────────────────────────────────

def mcp_session(root: Path, calls: List[Any]) -> List[Dict[str, Any]]:
    msgs: List[Dict[str, Any]] = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2024-11-05"}}]
    for i, (name, args) in enumerate(calls, 2):
        msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
    data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs)
    r = subprocess.run([sys.executable, "-X", "utf8", str(MCP_SERVER), "--project-root", str(root)], input=data,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


class McpTests(Tmp):
    def test_build_check_and_refusal_over_json_rpc(self):
        root = fixture_repo(self.root)
        out = mcp_session(root, [("daiver_ds_requirements", {}), ("daiver_ds_requirements", {"check": True}),
                                 ("daiver_ds_requirements", {"engine": "ruby"})])
        built = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual((out[1]["result"]["isError"], built["components"], built["stories"]), (False, 10, 12))
        self.assertEqual(json.loads(out[2]["result"]["content"][0]["text"])["state"], "ok")
        self.assertTrue(out[3]["result"]["isError"])
        self.assertIn("python | node", out[3]["result"]["content"][0]["text"])
        self.assertLess(len(json.dumps(built, indent=1).splitlines()), 60, "ответ короткий для слабой модели")

    def test_status_names_the_tool_when_documents_are_missing(self):
        root = fixture_repo(self.root)
        out = mcp_session(root, [("daiver_status", {})])
        warns = json.loads(out[1]["result"]["content"][0]["text"])["warnings"]
        self.assertTrue(any(w.startswith("ds-requirements-missing") and "daiver_ds_requirements" in w for w in warns), warns)
        mcp_session(root, [("daiver_ds_requirements", {})])
        warns = json.loads(mcp_session(root, [("daiver_status", {})])[1]["result"]["content"][0]["text"])["warnings"]
        self.assertFalse(any(w.startswith("ds-requirements-missing") for w in warns))



class LoadPairTests(Tmp):
    """`load_pair` — единственный способ для 2.3 / 4.5 прочитать документы: либо валидная согласованная пара,
    либо названные проблемы и ни одного документа."""

    def test_valid_pair_is_returned_without_scanning(self):
        root = fixture_repo(self.root)
        call(root)
        shutil.rmtree(root / "lib")                           # библиотеки уже нет — читается только закоммиченное
        contract, details, problems = DR.load_pair(root)
        self.assertEqual(problems, [])
        self.assertEqual(sorted(contract["components"]), ALL_NAMES)
        self.assertIn("Button", details["components"])
        self.assertEqual(DR.validate_pair(contract, details), [])

    def test_missing_or_broken_documents_give_problems_and_no_documents(self):
        root = fixture_repo(self.root)
        self.assertEqual(DR.load_pair(root), (None, None, [f"нет {DR.CONTRACT_REL.as_posix()}", f"нет {DR.DETAILS_REL.as_posix()}"]))
        call(root)
        (root / DR.DETAILS_REL).write_text("[]", encoding="utf-8")
        contract, details, problems = DR.load_pair(root)
        self.assertEqual((contract, details), (None, None))
        self.assertTrue(problems and problems[0].startswith("details:"), problems)

    def test_load_pair_does_not_write(self):
        root = fixture_repo(self.root)
        call(root)
        before = tree(root)
        DR.load_pair(root)
        self.assertEqual(tree(root), before)


# ── необязательный node-движок ──────────────────────────────────────────────

TS_ENV = "FORGEUI_TEST_TYPESCRIPT"            # каталог пакета typescript — для паритета двух движков


def skip_reason_no_node() -> Optional[str]:
    return None if shutil.which("node") else "на машине нет node — проверяется только отказ (тест ниже)"


def skip_reason_no_typescript() -> Optional[str]:
    reason = skip_reason_no_node()
    if reason:
        return reason
    ts = os.environ.get(TS_ENV)
    if not ts or not (Path(ts) / "package.json").is_file():
        return f"нет typescript для паритета: задайте {TS_ENV}=<каталог пакета typescript>"
    return None


def link_typescript(root: Path) -> None:
    (root / "node_modules").mkdir(exist_ok=True)
    os.symlink(os.environ[TS_ENV], root / "node_modules" / "typescript")


EXTRA_CASES = {
    "e1.stories.tsx": ("stories", "const meta = preview.meta({ title: 'F/Z', component: Z });\nexport default meta;\n"
                       "export const A = meta.story({ args: { k: 1 } });\nconst Q = { args: { a: 1 } };\n"
                       "export { Q, A as Renamed };\n"),
    "e2.stories.js": ("stories", "storiesOf('O/Tip', module).addParameters({ component: Tip })\n"
                      "  .add('one', () => <Tip placement=\"top\" />);\nconst k = storiesOf('V/X', module);\n"
                      "k.add('a', () => <X v=\"1\" />);\n"),
    "e3.stories.jsx": ("stories", "enum Size { Sm = 'sm', Md = 'md', Lg }\nconst base = { label: 'Hi', n: 1_000 };\n"
                       "export default { title: 'M/X', component: X, args: { ...base, size: Size.Md, f: () => {} },"
                       " argTypes: { v: { options: Object.values(Size) }, w: { options: opts } } };\n"
                       "export const A = { args: { n: -2, t: `tpl`, u: undefined } };\n"),
    "e4.d.ts": ("types", "export interface P extends Base, Omit<X, 'a'> {\n  /** @deprecated Use `v`. */\n"
                "  flat?: boolean\n  size?:\n    | 'sm'\n    | 'md'\n  render(item: Item): Node;\n"
                "  [key: string]: unknown;\n  cond: A extends B ? C : D;\n  tpl: `x-${string}`;\n}\n"
                "interface A { a: 1 }\ninterface A { b: 2 }\ndeclare const enum K { One = 1, Two }\n"
                "export declare const B: React.ForwardRefExoticComponent<BP & React.RefAttributes<HTMLElement>>;\n"
                "/** @deprecated Use `X`. */\nexport declare class D extends React.Component<DP, State> {}\n"
                "declare module \"@fakeui/icons\" {\n  export const Icon: React.FC<IP>;\n}\n"),
    "e5.tsx": ("source", "export const Fwd = React.forwardRef<HTMLDivElement, CardProps>((p, r) => <div ref={r} />);\n"
               "export function Panel(props: PanelProps) { return <section />; }\nexport { Later };\n"
               "const Later = (p: LP) => <b />;\nexport const Util = (a: number) => a + 1;\n"),
}


class NodeEngineTests(Tmp):
    def test_node_engine_without_node_is_a_named_refusal_not_a_fallback(self):
        root = fixture_repo(self.root)
        with mock.patch.object(ds_node.shutil, "which", return_value=None):
            with self.assertRaises(T.ToolError) as cm:
                call(root, {"engine": "node"})
        self.assertIn("нет node", str(cm.exception))
        self.assertFalse((root / DR.CONTRACT_REL).exists(), "молчаливого отката на python нет")

    def test_node_engine_without_typescript_in_repo_is_a_named_refusal(self):
        reason = skip_reason_no_node()
        if reason:
            self.skipTest(reason)
        root = fixture_repo(self.root)
        with self.assertRaises(T.ToolError) as cm:
            call(root, {"engine": "node"})
        self.assertIn("typescript", str(cm.exception))
        self.assertFalse((root / DR.CONTRACT_REL).exists())

    def test_helper_rejects_bad_input_with_a_code(self):
        reason = skip_reason_no_node()
        if reason:
            self.skipTest(reason)
        r = subprocess.run(["node", str(ds_node.HELPER)], input="not json", capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(json.loads(r.stdout)["error"], "bad-input")

    def test_documents_are_identical_across_engines(self):
        reason = skip_reason_no_typescript()
        if reason:
            self.skipTest(reason)
        for source in ("stories", "sources"):
            with self.subTest(source):
                py_root = fixture_repo(self.root / f"py-{source}", source)
                nd_root = fixture_repo(self.root / f"nd-{source}", source)
                link_typescript(nd_root)
                call(py_root)
                res = call(nd_root, {"engine": "node"})
                self.assertEqual((res["scanner"], bool(res["typescript"])), ("node/1", True))
                pc, pd = docs(py_root)
                nc, nd = docs(nd_root)
                self.assertEqual(nc["source"]["scanner"], "node/1")
                nc["source"]["scanner"] = pc["source"]["scanner"]
                self.assertEqual(nc, pc)
                self.assertEqual(nd, pd)
                self.assertEqual(call(nd_root, {"check": True})["state"], "ok")

    def test_facts_are_identical_on_extra_constructs(self):
        reason = skip_reason_no_typescript()
        if reason:
            self.skipTest(reason)
        import hashlib
        root = init_repo(self.root)
        link_typescript(root)
        entries = []
        for path, (kind, text) in sorted(EXTRA_CASES.items()):
            write(root / path, text)
            entries.append(DR.Entry(path, kind, text, hashlib.sha256(text.encode("utf-8")).hexdigest()))
        py = [DR.scan_py(e)[0] for e in entries]
        facts, parse_errors, _ = ds_node.scan(root, entries)
        self.assertEqual(parse_errors, 0)
        for a, b in zip(py, facts):
            self.assertEqual(a, b, a["file"])

    def test_file_changed_between_hash_and_parse_is_refused(self):
        reason = skip_reason_no_typescript()
        if reason:
            self.skipTest(reason)
        root = fixture_repo(self.root)
        link_typescript(root)
        cands, skipped = DR.discover(root, ["lib/src", "lib/types"], "stories")
        entries = DR.read_entries(root, cands, skipped)
        victim = next(e for e in entries if e.kind == "stories")
        victim.sha = "0" * 64
        with self.assertRaises(DR.DsError) as cm:
            ds_node.scan(root, entries)
        self.assertIn("изменился во время скана", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
