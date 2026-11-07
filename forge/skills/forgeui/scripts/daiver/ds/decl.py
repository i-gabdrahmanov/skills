"""decl.py — декларации типов и компоненты: `.d.ts` и исходники `.ts/.tsx` → факты (спека §5.3, QG U8).

Что собирается (по знанию TS/React; образца боевых деклараций у нас нет — это допущение):
  • `interface X extends A, B<C> {…}` (повторные объявления сливаются), `type X = …`, `enum X {…}` — в таблицу
    типов файла (`types`); члены с JSDoc: описание, `@deprecated`, `@default`;
  • компоненты (`components`): `const X: React.FC<P>`, `ForwardRefExoticComponent<P & RefAttributes<T>>`,
    `MemoExoticComponent<…>`, `ComponentType<P>`, функциональный тип `(props: P) => JSX.Element`;
    `function X(props: P): JSX.Element`; `class X extends React.Component<P>`; в исходниках — ещё
    `const X = ({…}: P) => …`, `React.forwardRef<T, P>(…)`, `memo(…)`;
  • `declare module "pkg" { … }` — имя модуля становится import-путём вложенных компонентов (это единственный
    честный источник import-пути в деклараций: пакет из `package.json` без знания, что он экспортирует, не
    выводится).
Компонент — только имя с заглавной буквы и распознанный вид: в `.d.ts` это тип-обёртка/функция с элементом
в результате; в исходниках функция должна ещё показать себя компонентом (элемент в возвращаемом типе, тип
пропсов `…Props` или JSX в теле). В `.d.ts` всё объявленное считается экспортированным, в исходниках — только
с `export` (в том числе `export { X }` и `export default X` ниже по файлу).
Тела функций и классов не разбираются — оператор пропускается по скобкам.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from . import facts as F
from . import lex as L
from . import typeast as TA
from . import value as V
from .lex import ID, JSX, P

COMPONENT_WRAPPERS = frozenset((
    "FC", "FunctionComponent", "VFC", "VoidFunctionComponent", "SFC", "StatelessComponent", "ComponentType",
    "ComponentClass", "ForwardRefExoticComponent", "ExoticComponent", "NamedExoticComponent",
    "MemoExoticComponent", "LazyExoticComponent", "JSXElementConstructor"))
ELEMENT_NAMES = frozenset(("Element", "ReactElement", "ReactNode", "ReactChild", "ReactPortal"))
CLASS_BASES = frozenset(("Component", "PureComponent"))
HOC_CALLS = frozenset(("memo", "forwardRef", "observer"))
_MODIFIERS = ("export", "default", "declare", "abstract")


def _last(name: str) -> str:
    return name.split(".")[-1]


def is_element(ast: Optional[Dict[str, Any]]) -> bool:
    """Тип результата, похожий на элемент React: JSX.Element, ReactElement, ReactNode… (в том числе в union)."""
    if not ast:
        return False
    if ast["k"] == "ref":
        return _last(ast["name"]) in ELEMENT_NAMES
    if ast["k"] == "union":
        return any(is_element(x) for x in ast["items"])
    return False


def component_type(ast: Optional[Dict[str, Any]], depth: int = 0) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Тип-аннотация — тип компонента? (да/нет, тип пропсов или None)."""
    if not ast or depth > 4:
        return False, None
    k = ast["k"]
    if k == "ref":
        last = _last(ast["name"])
        if last not in COMPONENT_WRAPPERS:
            return False, None
        if not ast["args"]:
            return True, None
        inner = ast["args"][0]
        if last in ("MemoExoticComponent", "LazyExoticComponent"):
            ok, props = component_type(inner, depth + 1)
            if ok:
                return True, props
        return True, inner
    if k == "fn":
        params = ast.get("params") or []
        if len(params) <= 1 and is_element(ast.get("ret")):
            return True, params[0] if params else None
    if k == "union":
        for it in ast["items"]:
            ok, props = component_type(it, depth + 1)
            if ok:
                return True, props
    return False, None


def _ref_name(ast: Optional[Dict[str, Any]]) -> str:
    return ast["name"] if ast and ast["k"] == "ref" else ""


def _later_exports(t: L.Toks) -> Set[str]:
    """Имена, экспортированные отдельным оператором: `export { A, B as C }`, `export default A`."""
    names: Set[str] = set()
    for i, j in t.statements():
        if not t.is_id(i, "export"):
            continue
        if t.is_p(i + 1, "{") and not any(t.is_id(x, "from") for x in range(t.close(i + 1), j)):
            for a, b in t.split_top(i + 2, t.close(i + 1), ","):
                if a < b and t.t[a].t == ID:
                    names.add(t.t[a].v)
        elif t.is_id(i + 1, "default") and t.is_id(i + 2) and (j - i <= 4):
            names.add(t.t[i + 2].v)
    return names


# ── типы ─────────────────────────────────────────────────────────────────────

def _skip_generics(t: L.Toks, p: int) -> int:
    if t.is_p(p, "<"):
        e = t.skip_angle(p)
        return e if e > 0 else p + 1
    return p


def _interface(t: L.Toks, k: int, j: int, facts: Dict[str, Any], doc_tok: int) -> None:
    name = t.t[k + 1].v
    p = _skip_generics(t, k + 2)
    extends: List[Dict[str, Any]] = []
    if t.is_id(p, "extends"):
        lb = t.find_top(p + 1, j, lambda x: x.t == P and x.v == "{")
        if lb < 0:
            return
        extends = [TA.parse_type(t, a, b) for a, b in TA.split_type(t, p + 1, lb, ",")]
        p = lb
    if not t.is_p(p, "{"):
        return
    m = TA.members(t, p)
    decl = {"kind": "interface", "extends": extends, "members": m["members"], "open": m["open"], "type": None,
            "enum": None, "doc": F.doc_fact(t.doc(doc_tok))}
    old = facts["types"].get(name)
    if old and old["kind"] == "interface":                     # слияние объявлений интерфейса
        old["members"].extend(decl["members"])
        old["extends"].extend(decl["extends"])
        old["open"] = old["open"] or decl["open"]
        old["doc"] = old["doc"] or decl["doc"]
    else:
        facts["types"][name] = decl


def _alias(t: L.Toks, k: int, j: int, facts: Dict[str, Any], doc_tok: int) -> None:
    name = t.t[k + 1].v
    p = _skip_generics(t, k + 2)
    if not t.is_p(p, "="):
        return
    end = j - 1 if t.is_p(j - 1, ";") else j
    facts["types"][name] = {"kind": "alias", "extends": [], "members": [], "open": False,
                            "type": TA.parse_type(t, p + 1, end), "enum": None,
                            "doc": F.doc_fact(t.doc(doc_tok))}


def _enum(t: L.Toks, env: V.Env, k: int, facts: Dict[str, Any], doc_tok: int) -> None:
    if not (t.is_id(k + 1) and t.is_p(k + 2, "{")):
        return
    facts["types"][t.t[k + 1].v] = {"kind": "enum", "extends": [], "members": [], "open": False, "type": None,
                                    "enum": F.enc(V.enum_members(t, env, k + 2)),
                                    "doc": F.doc_fact(t.doc(doc_tok))}


# ── компоненты ───────────────────────────────────────────────────────────────

def _binding(name: str, props: Optional[Dict[str, Any]], doc: Any, module: Optional[str]) -> Dict[str, Any]:
    return {"name": name, "props": props, "doc": F.doc_fact(doc), "module": module}


def _param_type(t: L.Toks, a: int, b: int) -> Optional[Dict[str, Any]]:
    """Тип первого параметра `props: P` / `{a, b}: P` / `props: P = {}` (диапазон параметра [a, b))."""
    colon = t.find_top(a, b, lambda x: x.t == P and x.v == ":")
    if colon < 0:
        return None
    eq = t.find_top(colon + 1, b, lambda x: x.t == P and x.v == "=")
    return TA.parse_type(t, colon + 1, eq if eq > 0 else b)


def _signature(t: L.Toks, a: int, b: int) -> Optional[Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]]:
    """Стрелка или function-выражение в [a, b): (тип первого параметра, тип результата) или None."""
    k = a
    if t.is_id(k, "async"):
        k += 1
    if t.is_id(k, "function"):
        k += 1
        if t.is_id(k):
            k += 1
        k = _skip_generics(t, k)
        if not t.is_p(k, "("):
            return None
        rp = t.close(k)
        ret = None
        if t.is_p(rp + 1, ":"):
            body = t.find_top(rp + 2, b, lambda x: x.t == P and x.v == "{")
            ret = TA.parse_type(t, rp + 2, body if body > 0 else b)
        first = [r for r in t.split_top(k + 1, rp, ",") if r[0] < r[1]]
        return (_param_type(t, *first[0]) if first else None), ret
    k = _skip_generics(t, k)
    if t.is_p(k, "("):
        rp = t.close(k)
        after = rp + 1
        ret = None
        if t.is_p(after, ":"):
            arrow = t.find_top(after + 1, b, lambda x: x.t == P and x.v == "=>")
            if arrow < 0:
                return None
            ret = TA.parse_type(t, after + 1, arrow)
            after = arrow
        if not t.is_p(after, "=>"):
            return None
        first = [r for r in t.split_top(k + 1, rp, ",") if r[0] < r[1]]
        return (_param_type(t, *first[0]) if first else None), ret
    if t.t[k].t == ID and t.is_p(k + 1, "=>"):
        return None, None
    return None


def _has_jsx(t: L.Toks, a: int, b: int) -> bool:
    return any(t.t[x].t == JSX for x in range(a, min(b, t.n)))


def _looks_like_component(props: Optional[Dict[str, Any]], ret: Optional[Dict[str, Any]], kind: str,
                          t: L.Toks, a: int, b: int) -> bool:
    if is_element(ret):
        return True
    if _last(_ref_name(props)).endswith("Props"):
        return True
    return kind == "source" and _has_jsx(t, a, b)


def _init_component(t: L.Toks, a: int, b: int, kind: str, depth: int = 0
                    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Инициализатор переменной — компонент? Стрелка/функция, `forwardRef<T, P>(…)`, `memo(…)`."""
    if depth > 3 or b <= a:
        return False, None
    if t.t[a].t == ID and t.t[a].v not in ("async", "function"):
        names: List[str] = []
        k = a
        while k < b and t.t[k].t == ID:
            names.append(t.t[k].v)
            k += 1
            if k + 1 < b and t.is_p(k, ".") and t.t[k + 1].t == ID:
                k += 1
                continue
            break
        targs: List[Dict[str, Any]] = []
        if k < b and t.is_p(k, "<"):
            e = t.skip_angle(k)
            if e > 0:
                targs = [TA.parse_type(t, x, y) for x, y in TA.split_angle(t, k, e)]
                k = e
        if names and _last(".".join(names)) in HOC_CALLS and k < b and t.is_p(k, "(") and t.close(k) == b - 1:
            last = names[-1]
            if last == "forwardRef" and len(targs) >= 2:
                return True, targs[1]
            if last in ("memo", "observer") and targs:
                return True, targs[0]
            args = [r for r in t.split_top(k + 1, b - 1, ",") if r[0] < r[1]]
            if args:
                ok, props = _init_component(t, args[0][0], args[0][1], kind, depth + 1)
                if ok:
                    return True, props
                if kind == "source" and _has_jsx(t, a, b):
                    return True, None
            return False, None
    sig = _signature(t, a, b)
    if sig is None:
        return False, None
    props, ret = sig
    if _looks_like_component(props, ret, kind, t, a, b):
        return True, props
    return False, None


def _declare_variable(t: L.Toks, k: int, j: int, i: int, facts: Dict[str, Any], module: Optional[str],
                      visible, kind: str) -> None:
    end = j - 1 if t.is_p(j - 1, ";") else j
    first = True
    for a, b in t.split_decls(k + 1, end):
        if a >= b or t.t[a].t != ID:
            continue
        name = t.t[a].v
        eq = t.find_top(a + 1, b, lambda x: x.t == P and x.v == "=")
        stop = eq if eq > 0 else b
        colon = t.find_top(a + 1, stop, lambda x: x.t == P and x.v == ":")
        doc = t.doc(i) if first else t.doc(a)
        first = False
        if not name[:1].isupper() or not visible(name):
            continue
        ok, props = False, None
        if colon > 0:
            ok, props = component_type(TA.parse_type(t, colon + 1, stop))
        if not ok and eq > 0:
            ok, props = _init_component(t, eq + 1, b, kind)
        if ok:
            facts["components"].append(_binding(name, props, doc, module))


def _declare_function(t: L.Toks, k: int, j: int, i: int, facts: Dict[str, Any], module: Optional[str],
                      visible, kind: str) -> None:
    fn = k + 1
    if not t.is_id(fn):
        return
    name = t.t[fn].v
    if not name[:1].isupper() or not visible(name):
        return
    p = _skip_generics(t, fn + 1)
    if not t.is_p(p, "("):
        return
    rp = t.close(p)
    ret = None
    if t.is_p(rp + 1, ":"):
        body = t.find_top(rp + 2, j, lambda x: x.t == P and x.v in ("{", ";"))
        ret = TA.parse_type(t, rp + 2, body if body > 0 else j)
    first = [r for r in t.split_top(p + 1, rp, ",") if r[0] < r[1]]
    props = _param_type(t, *first[0]) if first else None
    if _looks_like_component(props, ret, kind, t, k, j):
        facts["components"].append(_binding(name, props, t.doc(i), module))


def _declare_class(t: L.Toks, k: int, j: int, i: int, facts: Dict[str, Any], module: Optional[str],
                   visible) -> None:
    if not t.is_id(k + 1):
        return
    name = t.t[k + 1].v
    p = _skip_generics(t, k + 2)
    if not (name[:1].isupper() and visible(name) and t.is_id(p, "extends")):
        return
    lb = t.find_top(p + 1, j, lambda x: x.t == P and x.v == "{")
    stop = t.find_top(p + 1, lb if lb > 0 else j, lambda x: x.t == ID and x.v == "implements")
    end = stop if stop > 0 else (lb if lb > 0 else j)
    ast = TA.parse_type(t, p + 1, end)
    if ast["k"] == "ref" and _last(ast["name"]) in CLASS_BASES:
        facts["components"].append(_binding(name, ast["args"][0] if ast["args"] else None, t.doc(i), module))


# ── обход ────────────────────────────────────────────────────────────────────

def _walk(t: L.Toks, env: V.Env, facts: Dict[str, Any], a: int, b: int, module: Optional[str],
          ambient: bool, later: Set[str], kind: str) -> None:
    for i, j in t.statements(a, b):
        k = i
        exported = False
        declared = False
        while k < j and t.t[k].t == ID and t.t[k].v in _MODIFIERS:
            exported = exported or t.t[k].v == "export"
            declared = declared or t.t[k].v == "declare"
            k += 1
        if k >= j or t.t[k].t != ID:
            continue

        def visible(name: str, exported=exported, declared=declared) -> bool:
            return ambient or module is not None or exported or declared or name in later

        kw = t.t[k].v
        if kw == "async" and t.is_id(k + 1, "function"):
            k += 1
            kw = "function"
        if kw == "interface" and t.is_id(k + 1):
            _interface(t, k, j, facts, i)
        elif kw == "type" and t.is_id(k + 1) and (t.is_p(k + 2, "=") or t.is_p(k + 2, "<")):
            _alias(t, k, j, facts, i)
        elif kw == "enum" or (kw == "const" and t.is_id(k + 1, "enum")):
            _enum(t, env, k if kw == "enum" else k + 1, facts, i)
        elif kw in ("const", "let", "var"):
            _declare_variable(t, k, j, i, facts, module, visible, kind)
        elif kw == "function":
            _declare_function(t, k, j, i, facts, module, visible, kind)
        elif kw == "class":
            _declare_class(t, k, j, i, facts, module, visible)
        elif kw in ("module", "namespace", "global"):
            lb = t.find_top(k + 1, j, lambda x: x.t == P and x.v == "{")
            if lb > 0:
                name_tok = t.t[k + 1] if k + 1 < j else None
                mod = name_tok.v if name_tok is not None and name_tok.t == L.STR else module
                _walk(t, env, facts, lb + 1, t.close(lb), mod, ambient, later, kind)


def scan(src: str, path: str, kind: str) -> Dict[str, Any]:
    """Факты деклараций/исходников (kind: types — `.d.ts`; source — `.ts/.tsx/.js/.jsx` исходники)."""
    t = L.Toks(src, L.is_jsx_file(path))
    env = V.collect_decls(t)
    facts = F.empty_facts(path, kind)
    _walk(t, env, facts, 0, t.n, None, kind == "types", _later_exports(t), kind)
    return facts
