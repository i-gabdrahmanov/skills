"""csf.py — stories-файлы библиотеки: CSF1/2/3, CSF Factories и `storiesOf` → факты (спека §5.3, QG U8).

Разбираемые формы (по знанию формата Storybook; образца боевых stories у нас нет — это допущение):
  • CSF3: `export default {title, component, args, argTypes, parameters}` (в том числе через
    `const meta = {…} satisfies Meta<typeof X>; export default meta`), истории — объекты:
    `export const Primary: Story = {args, argTypes, name, render, parameters}`.
  • CSF2: `const Template = (args) => <X {...args} />; export const Primary = Template.bind({});
    Primary.args = {…}; Primary.storyName = '…'` — `Name.args/argTypes/storyName/name/parameters = …`.
  • CSF1: `export const Primary = () => <X variant="primary" />` (функция-история; значения пропсов — из JSX).
  • CSF Factories (Storybook 9): `const meta = preview.meta({…}); export const A = meta.story({…})` — объект
    из первого аргумента вызова. Не проверено данными.
  • `storiesOf('Группа/Компонент', module).addParameters({component}).add('имя', () => <X/>, {…})`, в том числе
    через переменную (`const k = storiesOf(…); k.add(…)`).
Из истории берутся: args, argTypes, имя, описание (`parameters.docs.description.story`), текст JSX и
литеральные значения пропсов из него (`usage`). Что не вычислить статически, остаётся маркером opaque/ref.
`includeStories`/`excludeStories` (массив имён или регулярка) и `__namedExportsOrder` отсекают не-истории.
Компонент группы здесь — только то, что названо явно (`component:`/`addParameters`); вывод по JSX и title —
в `merge.py`.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import facts as F
from . import lex as L
from . import value as V
from .lex import ID, JSX, P, RE, STR

Range = Tuple[int, int]

ALWAYS_EXCLUDED = ("__namedExportsOrder",)


# ── импорты ──────────────────────────────────────────────────────────────────

def collect_imports(t: L.Toks) -> Dict[str, Dict[str, Any]]:
    """{локальное имя: {"from", "named", "default", "ns"}}; `import type …` и побочные импорты пропускаются."""
    out: Dict[str, Dict[str, Any]] = {}
    for i, j in t.statements():
        if not t.is_id(i, "import"):
            continue
        k = i + 1
        if k >= j or t.t[k].t == STR:
            continue
        if t.is_id(k, "type") and not t.is_id(k + 1, "from") and not t.is_p(k + 1, ","):
            continue
        spec = None
        for x in range(j - 1, i, -1):
            if t.t[x].t == STR:
                spec = t.t[x].v
                break
        if spec is None:
            continue
        if t.t[k].t == ID and t.t[k].v != "from" and (t.is_p(k + 1, ",") or t.is_id(k + 1, "from")):
            out[t.t[k].v] = {"from": spec, "named": None, "default": True, "ns": False}
            k += 1
            if t.is_p(k, ","):
                k += 1
        if t.is_p(k, "*") and t.is_id(k + 1, "as") and t.is_id(k + 2):
            out[t.t[k + 2].v] = {"from": spec, "named": None, "default": False, "ns": True}
        elif t.is_p(k, "{"):
            for a, b in t.split_top(k + 1, t.close(k), ","):
                if a >= b or t.t[a].t != ID:
                    continue
                if t.is_id(a, "type") and b - a >= 2 and not t.is_id(a + 1, "as"):
                    continue                                  # `{ type Props }`
                imported = t.t[a].v
                local = t.t[a + 2].v if t.is_id(a + 1, "as") and a + 2 < b and t.t[a + 2].t == ID else imported
                if imported == "default":
                    out[local] = {"from": spec, "named": None, "default": True, "ns": False}
                else:
                    out[local] = {"from": spec, "named": imported, "default": False, "ns": False}
    return out


# ── диапазоны выражений ──────────────────────────────────────────────────────

def object_range(env: V.Env, i: int, j: int, depth: int = 0) -> Optional[Range]:
    """Диапазон объектного литерала, на который указывает выражение [i, j): сам литерал, константа с
    литералом, первый аргумент вызова (`preview.meta({…})`, `meta.story({…})`)."""
    t = env.toks
    i, j = env.ev._trim(i, j)
    if j - i >= 2 and t.is_p(i, "{") and t.close(i) == j - 1:
        return i, j
    if depth > 4 or j <= i:
        return None
    if j - i == 1 and t.t[i].t == ID and t.t[i].v in env.consts:
        return object_range(env, *env.consts[t.t[i].v], depth + 1)
    if t.t[i].t == ID:
        k = i
        while k < j and t.t[k].t == ID:
            k += 1
            if k + 1 < j and t.is_p(k, ".") and t.t[k + 1].t == ID:
                k += 1
                continue
            break
        if k < j and t.is_p(k, "(") and t.close(k) == j - 1:
            args = [r for r in t.split_top(k + 1, j - 1, ",") if r[0] < r[1]]
            if args:
                return object_range(env, *args[0], depth + 1)
    return None


def prop_range(t: L.Toks, obj: Range, key: str) -> Optional[Range]:
    """Диапазон значения свойства `key` в объектном литерале obj (метод — весь элемент)."""
    a, b = obj
    for x, y in t.split_top(a + 1, b - 1, ","):
        if x < y and t.t[x].t in (ID, STR) and t.t[x].v == key:
            if t.is_p(x + 1, ":") and x + 2 < y:
                return x + 2, y
            if y - x == 1 or t.is_p(x + 1, "("):
                return x, y
    return None


def chain_text(env: V.Env, a: int, b: int) -> Optional[str]:
    """`Button` / `Form.Item`, если диапазон — чистая цепочка идентификаторов; иначе None."""
    t = env.toks
    a, b = env.ev._trim(a, b)
    names: List[str] = []
    k = a
    while k < b and t.t[k].t == ID:
        names.append(t.t[k].v)
        k += 1
        if k + 1 < b and t.is_p(k, ".") and t.t[k + 1].t == ID:
            k += 1
            continue
        break
    return ".".join(names) if names and k == b else None


def find_jsx(t: L.Toks, a: int, b: int) -> Optional[int]:
    """JSX истории: возвращаемый элемент (`return <X/>`, `return (<X/>)`), иначе первый JSX диапазона."""
    first: Optional[int] = None
    for k in range(a, min(b, t.n)):
        tk = t.t[k]
        if tk.t == JSX:
            if first is None:
                first = k
        elif tk.t == ID and tk.v == "return":
            m = k + 1
            while m < b and t.is_p(m, "("):
                m += 1
            if m < b and t.t[m].t == JSX:
                return m
    return first


def _name_filter(t: L.Toks, env: V.Env, rng: Optional[Range]) -> Optional[Callable[[str], bool]]:
    if rng is None:
        return None
    a, b = rng
    if b - a == 1 and t.t[a].t == RE:
        m = re.match(r"/(.*)/([a-z]*)$", t.t[a].v, re.S)
        if not m:
            return None
        try:
            rx = re.compile(m.group(1), re.I if "i" in m.group(2) else 0)
        except re.error:
            return None
        return lambda n: rx.search(n) is not None
    v = env.ev.ev(a, b)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        names = set(v)
        return lambda n: n in names
    return None


def _docs_text(params: Any, key: str) -> Optional[str]:
    v = params.get("docs") if isinstance(params, dict) else None
    v = v.get("description") if isinstance(v, dict) else None
    v = v.get(key) if isinstance(v, dict) else None
    return L.clean_text(v) if isinstance(v, str) and v.strip() else None


# ── меты и истории ───────────────────────────────────────────────────────────

def _empty_meta() -> Dict[str, Any]:
    return {"title": None, "component": None, "args": None, "argTypes": None, "description": None}


def _meta_from_object(t: L.Toks, env: V.Env, obj: Optional[Range]) -> Dict[str, Any]:
    meta = _empty_meta()
    if obj is None:
        return meta
    val = env.ev.ev(*obj)
    if isinstance(val, dict):
        if isinstance(val.get("title"), str):
            meta["title"] = val["title"]
        for key in ("args", "argTypes"):
            if isinstance(val.get(key), dict):
                meta[key] = F.enc(val[key])
        meta["description"] = _docs_text(val.get("parameters"), "component")
    rng = prop_range(t, obj, "component")
    if rng:
        meta["component"] = chain_text(env, *rng)
    return meta


def _default_export(t: L.Toks) -> Optional[Range]:
    """Выражение `export default <expr>` (или `export { X as default }`) верхнего уровня."""
    for i, j in t.statements():
        if not t.is_id(i, "export"):
            continue
        end = j - 1 if t.is_p(j - 1, ";") else j
        if t.is_id(i + 1, "default"):
            if t.is_id(i + 2, "function") or t.is_id(i + 2, "class") or t.is_id(i + 2, "async"):
                return None
            return (i + 2, end) if i + 2 < end else None
        if t.is_p(i + 1, "{"):
            for a, b in t.split_top(i + 2, t.close(i + 1), ","):
                if b - a == 3 and t.t[a].t == ID and t.is_id(a + 1, "as") and t.is_id(a + 2, "default"):
                    return a, a + 1
    return None


def _collect_assigns(t: L.Toks, env: V.Env) -> None:
    """`Primary.args = {…};` → env.assigns (CSF2)."""
    for i, j in t.statements():
        if t.t[i].t == ID and t.is_p(i + 1, ".") and t.is_id(i + 2) and t.is_p(i + 3, "="):
            end = j - 1 if t.is_p(j - 1, ";") else j
            if i + 4 < end:
                env.assigns.setdefault(t.t[i].v, {})[t.t[i + 2].v] = (i + 4, end)


def _declarators(t: L.Toks, k: int, j: int) -> List[Tuple[str, Range]]:
    """`const A = x, B = y` (k — после `const`) → [(имя, диапазон инициализатора)]."""
    out: List[Tuple[str, Range]] = []
    end = j - 1 if t.is_p(j - 1, ";") else j
    for a, b in t.split_decls(k, end):
        if a < b and t.t[a].t == ID:
            eq = t.find_top(a + 1, b, lambda x: x.t == P and x.v == "=")
            if eq > 0 and eq + 1 < b:
                out.append((t.t[a].v, (eq + 1, b)))
    return out


def _classify(t: L.Toks, env: V.Env, a: int, b: int) -> Tuple[str, Any]:
    """Вид инициализатора истории: ("object", диапазон) | ("bind", шаблон) | ("func", (a, b)) | ("other", None)."""
    a, b = env.ev._trim(a, b)
    if b <= a:
        return "other", None
    if t.is_p(a, "{") and t.close(a) == b - 1:
        return "object", (a, b)
    if t.is_id(a, "function") or (t.is_id(a, "async") and (t.is_id(a + 1, "function")
                                                              or t.is_p(a + 1, "(") or t.is_id(a + 1))):
        return "func", (a, b)
    arrow = t.find_top(a, b, lambda x: x.t == P and x.v == "=>")
    if arrow > 0 and (t.is_p(a, "(") or (t.t[a].t == ID and arrow == a + 1)):
        return "func", (a, b)
    if t.t[a].t == ID:
        names: List[str] = []
        k = a
        while k < b and t.t[k].t == ID:
            names.append(t.t[k].v)
            k += 1
            if k + 1 < b and t.is_p(k, ".") and t.t[k + 1].t == ID:
                k += 1
                continue
            break
        if k < b and t.is_p(k, "(") and t.close(k) == b - 1:
            if len(names) >= 2 and names[-1] == "bind":
                return "bind", ".".join(names[:-1])
            obj = object_range(env, a, b)
            if obj:
                return "object", obj
    return "other", None


def _usage(env: V.Env, jsx_text: str) -> List[List[Any]]:
    return [[tag, F.enc(props)] for tag, props in V.collect_usage(jsx_text, env)]


def _story(t: L.Toks, env: V.Env, export: Optional[str], name: Optional[str], fmt: str,
           args_rng: Optional[Range], argtypes_rng: Optional[Range], jsx_idx: Optional[int],
           params: Any) -> Dict[str, Any]:
    story: Dict[str, Any] = {"export": export, "name": name, "format": fmt, "args": None, "argTypes": None,
                             "jsx": None, "usage": [], "component": None,
                             "description": _docs_text(params, "story")}
    for key, rng in (("args", args_rng), ("argTypes", argtypes_rng)):
        if rng:
            v = env.ev.ev(*rng)
            if isinstance(v, dict):
                story[key] = F.enc(v)
    if jsx_idx is not None:
        text = t.t[jsx_idx].v
        story["jsx"] = text
        story["usage"] = _usage(env, text)
    return story


def _named_story(t: L.Toks, env: V.Env, name: str, a: int, b: int) -> Dict[str, Any]:
    """История из `export const name = <инициализатор>` / `export function name`."""
    kind, info = _classify(t, env, a, b)
    asg = env.assigns.get(name, {})
    args_rng = argtypes_rng = None
    jsx_idx: Optional[int] = None
    sname: Optional[str] = None
    params: Any = None
    if kind == "object":
        obj: Range = info
        val = env.ev.ev(*obj)
        args_rng, argtypes_rng = prop_range(t, obj, "args"), prop_range(t, obj, "argTypes")
        rp = prop_range(t, obj, "render")
        if rp:
            jsx_idx = find_jsx(t, *rp)
        if isinstance(val, dict):
            sname = val.get("name") if isinstance(val.get("name"), str) else None
            params = val.get("parameters")
        fmt = "csf3"
    elif kind == "bind":
        fmt = "csf2"
        tpl = info
        rng = env.consts.get(tpl) or env.funcs.get(tpl)
        if rng:
            jsx_idx = find_jsx(t, *rng)
    elif kind == "func":
        fmt = "csf2" if asg else "csf1"
        jsx_idx = find_jsx(t, *info)
    else:
        fmt = "other"
    if "args" in asg:
        args_rng = asg["args"]
    if "argTypes" in asg:
        argtypes_rng = asg["argTypes"]
    for key in ("storyName", "name"):
        if key in asg:
            v = env.ev.ev(*asg[key])
            if isinstance(v, str):
                sname = v
    if "parameters" in asg:
        params = env.ev.ev(*asg["parameters"])
    return _story(t, env, name, sname, fmt, args_rng, argtypes_rng, jsx_idx, params)


def _named_stories(t: L.Toks, env: V.Env, obj: Optional[Range]) -> List[Dict[str, Any]]:
    include = _name_filter(t, env, prop_range(t, obj, "includeStories")) if obj else None
    exclude = _name_filter(t, env, prop_range(t, obj, "excludeStories")) if obj else None
    out: List[Dict[str, Any]] = []
    seen = set()

    def take(name: str, a: int, b: int) -> None:
        if name in seen or name in ALWAYS_EXCLUDED or (include and not include(name)) \
                or (exclude and exclude(name)):
            return
        seen.add(name)
        out.append(_named_story(t, env, name, a, b))

    for i, j in t.statements():
        if not t.is_id(i, "export") or t.is_id(i + 1, "default"):
            continue
        k = i + 1
        if t.t[k].t == ID and t.t[k].v in ("const", "let", "var"):
            for name, (a, b) in _declarators(t, k + 1, j):
                take(name, a, b)
        elif t.is_id(k, "function") or (t.is_id(k, "async") and t.is_id(k + 1, "function")):
            fn = k + 1 if t.is_id(k, "function") else k + 2
            if t.is_id(fn):
                take(t.t[fn].v, k, j)
        elif t.is_p(k, "{"):
            for a, b in t.split_top(k + 1, t.close(k), ","):
                if a >= b or t.t[a].t != ID:
                    continue
                local = t.t[a].v
                exported = t.t[a + 2].v if t.is_id(a + 1, "as") and a + 2 < b else local
                if exported == "default":
                    continue
                rng = env.consts.get(local) or env.funcs.get(local)
                if rng:
                    take(exported, *rng)
    return out


# ── storiesOf ────────────────────────────────────────────────────────────────

def _stories_of(t: L.Toks, env: V.Env) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    by_var: Dict[str, Dict[str, Any]] = {}
    for k in range(t.n):
        if not (t.is_id(k, "storiesOf") and t.is_p(k + 1, "(")) or (k > 0 and t.is_p(k - 1, ".")):
            continue
        rp = t.close(k + 1)
        args = [r for r in t.split_top(k + 2, rp, ",") if r[0] < r[1]]
        meta = _empty_meta()
        if args:
            title = env.ev.ev(*args[0])
            if isinstance(title, str):
                meta["title"] = title
        group = {"meta": meta, "stories": []}
        _stories_of_chain(t, env, group, rp + 1)
        if k >= 2 and t.is_p(k - 1, "=") and t.t[k - 2].t == ID:
            by_var[t.t[k - 2].v] = group
        groups.append(group)
    if by_var:
        for i, j in t.statements():
            if t.t[i].t == ID and t.t[i].v in by_var and t.is_p(i + 1, "."):
                _stories_of_chain(t, env, by_var[t.t[i].v], i + 1)
    return groups


def _stories_of_chain(t: L.Toks, env: V.Env, group: Dict[str, Any], pos: int) -> None:
    while t.is_p(pos, ".") and t.is_id(pos + 1) and t.is_p(pos + 2, "("):
        method = t.t[pos + 1].v
        lp = pos + 2
        rp = t.close(lp)
        args = [r for r in t.split_top(lp + 1, rp, ",") if r[0] < r[1]]
        if method == "add" and args:
            name = env.ev.ev(*args[0])
            if isinstance(name, str):
                jsx_idx = find_jsx(t, *args[1]) if len(args) > 1 else None
                params = env.ev.ev(*args[2]) if len(args) > 2 else None
                story = _story(t, env, None, name, "storiesOf", None, None, jsx_idx, params)
                group["stories"].append(story)
        elif method == "addParameters" and args:
            obj = object_range(env, *args[0])
            if obj:
                rng = prop_range(t, obj, "component")
                if rng and group["meta"]["component"] is None:
                    group["meta"]["component"] = chain_text(env, *rng)
                val = env.ev.ev(*obj)
                desc = _docs_text(val, "component") if isinstance(val, dict) else None
                if desc and group["meta"]["description"] is None:
                    group["meta"]["description"] = desc
        pos = rp + 1


# ── вход ─────────────────────────────────────────────────────────────────────

def scan(src: str, path: str) -> Dict[str, Any]:
    """Факты stories-файла (`kind: stories`)."""
    t = L.Toks(src, L.is_jsx_file(path))
    env = V.collect_decls(t)
    _collect_assigns(t, env)
    facts = F.empty_facts(path, "stories")
    facts["imports"] = collect_imports(t)
    rng = _default_export(t)
    if rng:
        obj = object_range(env, *rng)
        if obj is None and env.toks.t[rng[0]].t == ID:
            facts["warnings"].append("default export не объектный литерал — мета (title, component) не прочитана")
        facts["groups"].append({"meta": _meta_from_object(t, env, obj), "stories": _named_stories(t, env, obj)})
    facts["groups"].extend(_stories_of(t, env))
    return facts
