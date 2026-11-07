"""jsx_tree.py — React-адаптер код-гейта: исходники секций → дерево формы канона (спека §14.5; задача 4.7; INV-C29).

Читает ТОЛЬКО то, что ему дали (файлы outputs build-state + `mocks.ts` рядом), и строит дерево, которое матчер сверит с макетом так
же, как сверяет DOM: `code:root` → `code:<sectionId>` → `code:<sectionId>/<путь детей>`. Никакого node: лексер и выражения — блоки
сканера ДС (`ds/lex`, `ds/value`), разбор JSX — `value.parse_jsx`. Три прохода, как в спеке:

  1. scan — импорты, определения компонентов верхнего уровня (`function X`, `const X = (…) =>`, `export default`), таблицы
     литералов `mocks.ts` (`as const`, цепочки `MOCKS.x.y`, индексация `rows[1].label`, шаблоны, которые разрешаются целиком);
  2. вход секции — компонент, на который нет ссылок среди файлов секции, считается смонтированным; побеждает default export;
     неоднозначность — предупреждение, берётся первый по имени;
  3. обход JSX — intrinsic-теги → frame (svg → vector, img → image); единственный статический текст → text-лист; локальные
     компоненты (в просканированных файлах) разворачиваются ИНЛАЙН с подстановкой литеральных пропсов и сплайсом детей на
     `{children}` (защита от циклов, потолок глубины); компоненты пакетов — чёрные ящики `instance` (идентичность, литеральные
     строковые пропсы как `component.textProps`, содержимое слотов — детьми); `.map` над разрешимым массивом размножается по
     элементам с привязкой параметра колбэка.

Маркеры динамики лежат ВНЕ дерева (матчер их не читает): `repeated` (длина массива известна только в рантайме),
`conditional` (макет рисует одно состояние: `a && <X/>`, `a ? <X/> : <Y/>`, несколько `return`), `dynamic-text` (слова
неизвестны: текст не оценивается), `unresolved-import` (относительный импорт ведёт мимо просканированных файлов). Доктрина:
«значение, которого скан не видит, — значение, о котором он не судит» — не пропуск, а метка с приговором.

Не проверено данными (боевых модулей нет, разбор выверен на синтетике): настоящие `.tsx` с хуками, `React.memo`/`forwardRef`,
дженериками, `styled`-компонентами и условным рендером через вызовы; слот `{props.children}` в деструктурированных пропсах
по умолчанию; импорты по алиасам (`@/…`) считаются пакетом, а не локальным модулем.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..ds import csf, lex as L, value as V
from . import tsx_scan as TS

MAX_DEPTH = 12                          # вложенность разворачиваемых компонентов
MAX_NODES = 5000
MAX_REPEAT = 50
FRAGMENTS = ("", "Fragment", "React.Fragment")
LEAF_KIND = {"svg": "vector", "img": "image"}
NOT_TEXT_PROPS = frozenset(("className", "style", "key", "ref", "id", "type", "size", "variant", "color", "kind", "as", "role", "href",
                            "to", "target", "rel", "name", "htmlFor", "src", "alt", "tone", "shape", "mode"))
RESOLVE_SUFFIXES = ("", ".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts", "/index.jsx", "/index.js")
_ACCESS = re.compile(r"\.\s*([A-Za-z_$][\w$]*)|\[\s*(\d+)\s*\]|\[\s*(\"[^\"]*\"|'[^']*')\s*\]")
_HEAD = re.compile(r"^([A-Za-z_$][\w$]*)")
_NUM_OR_STR = re.compile(r"^(?:\"([^\"\\]*)\"|'([^'\\]*)'|(-?\d+(?:\.\d+)?))$")
_TPL_PART = re.compile(r"\$\{([^{}]*)\}")


class _Dyn:
    """Значение, статически не известное."""
    __slots__ = ()

    def __repr__(self) -> str:
        return "DYN"


DYN = _Dyn()
MISSING = object()


class CompDef:
    __slots__ = ("name", "module", "params", "props_name", "jsx", "multi", "default", "exported", "line")

    def __init__(self, name: str, module: "Module", params: List[str], props_name: Optional[str], jsx: Optional[Tuple[Any, str, int]],
                 multi: bool, default: bool, exported: bool, line: int):
        self.name, self.module, self.params, self.props_name, self.jsx = name, module, params, props_name, jsx
        self.multi, self.default, self.exported, self.line = multi, default, exported, line


class Module:
    def __init__(self, rel: str, text: str):
        self.rel, self.text = rel, text
        self.lines = TS._Lines(text)
        self.toks = L.Toks(text, L.is_jsx_file(rel))
        self.env = V.collect_decls(self.toks)
        self.imports = csf.collect_imports(self.toks)
        self.comps: Dict[str, CompDef] = {}
        self._defs()

    # --- определения компонентов ---------------------------------------------------------------------------------
    def _defs(self) -> None:
        t = self.toks
        for i, j in t.statements():
            k, exported, default = i, False, False
            while k < j and t.t[k].t == L.ID and t.t[k].v in ("export", "default", "declare", "async"):
                exported = exported or t.t[k].v == "export"
                default = default or t.t[k].v == "default"
                k += 1
            if k >= j or t.t[k].t != L.ID:
                continue
            name: Optional[str] = None
            params: Tuple[int, int] = (0, 0)
            body: Optional[Tuple[int, int]] = None
            if t.t[k].v == "function":
                n = k + 1
                if t.is_p(n, "*"):
                    n += 1
                if t.is_id(n) and t.is_p(n + 1, "("):
                    name = t.t[n].v
                    pc = t.close(n + 1)
                elif t.is_p(n, "("):
                    name, pc = "default", t.close(n)
                    n -= 1
                else:
                    continue
                if pc < 0:
                    continue
                params = (n + 2, pc)
                b = pc + 1
                while b < j and not t.is_p(b, "{"):
                    b += 1
                if b < j and t.close(b) > 0:
                    body = (b + 1, t.close(b))
            elif t.t[k].v in ("const", "let", "var") and t.is_id(k + 1):
                name = t.t[k + 1].v
                eq = t.find_top(k + 2, j, lambda x: x.t == L.P and x.v == "=")
                if eq < 0:
                    continue
                params, body = self._arrow(eq + 1, j)
                if body is None:
                    continue
            elif default and t.is_p(k, "("):
                name = "default"
                params, body = self._arrow(k, j)
                if body is None:
                    continue
            else:
                continue
            jsx, multi = self._find_jsx(*body) if body else (None, False)
            pnames, props_name = self._params(*params)
            line = self.lines.at(t.t[i].s)[0]
            self.comps[name] = CompDef(name, self, pnames, props_name, jsx, multi, default or name == "default", exported or default, line)

    def _arrow(self, a: int, b: int) -> Tuple[Tuple[int, int], Optional[Tuple[int, int]]]:
        """Выражение [a, b) — стрелка/function-выражение/обёртка (`memo(...)`, `forwardRef(...)`) → ((параметры), (тело))."""
        t = self.toks
        a0 = a
        while a < b:
            if t.is_p(a, "(") and t.close(a) > 0:
                c = t.close(a)
                if t.is_p(c + 1, "=>"):
                    body_start = c + 2
                    if t.is_p(body_start, "{") and t.close(body_start) > 0:
                        return (a + 1, c), (body_start + 1, t.close(body_start))
                    return (a + 1, c), (body_start, b)
                if a > a0 and t.is_id(a - 1) and t.t[a - 1].v in ("memo", "forwardRef", "observer"):
                    return self._arrow(a + 1, c)
                a = c + 1
                continue
            if t.is_id(a, "function"):
                n = a + 1
                if t.is_id(n):
                    n += 1
                if t.is_p(n, "(") and t.close(n) > 0:
                    c = t.close(n)
                    bs = c + 1
                    while bs < b and not t.is_p(bs, "{"):
                        bs += 1
                    if bs < b and t.close(bs) > 0:
                        return (n + 1, c), (bs + 1, t.close(bs))
            if t.t[a].t == L.ID and t.is_p(a + 1, "=>"):                       # x => …
                return (a, a + 1), (a + 2, b)
            a += 1
        return (0, 0), None

    def _find_jsx(self, a: int, b: int) -> Tuple[Optional[Tuple[Any, str, int]], bool]:
        t = self.toks
        found: List[Tuple[Any, str, int]] = []
        for x in range(a, b):
            if t.t[x].t == L.JSX:
                el = V.parse_jsx(t.t[x].v)
                if el is not None:
                    found.append((el, t.t[x].v, t.t[x].s))
        main = max(found, key=lambda f: len(f[1])) if found else None      # охранные return'ы (загрузка, пусто) короче основной разметки
        return main, len(found) > 1

    def _params(self, a: int, b: int) -> Tuple[List[str], Optional[str]]:
        t = self.toks
        if a >= b:
            return [], None
        if t.is_p(a, "{") and t.close(a) > 0:
            names: List[str] = []
            for x, y in t.split_top(a + 1, t.close(a), ","):
                if x < y and t.t[x].t == L.ID:
                    names.append(t.t[x].v)
            return names, None
        if t.t[a].t == L.ID:
            return [], t.t[a].v
        return [], None


# ----------------------------------------------------------------------------------------------------------------- обход

class _Build:
    def __init__(self, modules: Dict[str, Module]):
        self.modules = modules
        self.markers: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.count = 0

    def mark(self, kind: str, node: Optional[Dict[str, Any]], mod: Module, off: int, **kw: Any) -> None:
        line, col = mod.lines.at(off)
        m = {"type": kind, "file": mod.rel, "line": line, "column": col, **kw}
        self.markers.append(m)
        if node is not None:
            node.setdefault("_marks", []).append(kind)

    # --- значения ----------------------------------------------------------------------------------------------------
    def head_value(self, name: str, scope: Dict[str, Any], mod: Module, seen: Optional[Set[Tuple[str, str]]] = None) -> Any:
        if name in scope["vars"]:
            return scope["vars"][name]
        if name in scope["props"]:
            return scope["props"][name]
        if name == scope.get("props_name"):
            return scope["props"]
        if name in mod.env.consts or name in mod.env.enums:
            v = V.eval_snippet(name, mod.env)
            return DYN if v is V.OPAQUE else v
        imp = mod.imports.get(name)
        if imp and imp["from"].startswith("."):
            target = self.resolve_module(mod, imp["from"])
            if target is not None:
                key = (target.rel, imp["named"] or "default")
                if seen and key in seen:
                    return DYN
                if imp["named"] and (imp["named"] in target.env.consts):
                    v = V.eval_snippet(imp["named"], target.env)
                    return DYN if v is V.OPAQUE else v
        return MISSING

    def ev(self, src: str, scope: Dict[str, Any], mod: Module) -> Any:
        """Значение выражения: литерал, цепочка членов/индексов, шаблон, разрешимый целиком; иначе DYN."""
        s = src.strip()
        while s.startswith("(") and s.endswith(")") and L.Toks(s, True).close(0) == L.Toks(s, True).n - 1:
            s = s[1:-1].strip()
        if s in ("null", "undefined", "true", "false"):
            return {"null": None, "undefined": None, "true": True, "false": False}[s]
        m = _NUM_OR_STR.match(s)
        if m:
            if m.group(3) is not None:
                return float(m.group(3)) if "." in m.group(3) else int(m.group(3))
            return m.group(1) if m.group(1) is not None else m.group(2)
        if s.startswith("`") and s.endswith("`") and len(s) >= 2:
            body, bad = s[1:-1], False

            def sub(mm: "re.Match[str]") -> str:
                nonlocal bad
                v = self.ev(mm.group(1), scope, mod)
                if V.is_scalar(v) and not isinstance(v, bool) and v is not None:
                    return str(v)
                bad = True
                return ""
            out = _TPL_PART.sub(sub, body)
            return DYN if bad or "${" in out else out
        h = _HEAD.match(s)
        if not h:
            return DYN
        v = self.head_value(h.group(1), scope, mod)
        if v is MISSING:
            return DYN
        rest = s[h.end():]
        pos = 0
        while pos < len(rest):
            am = _ACCESS.match(rest, pos)
            if not am:
                return DYN
            pos = am.end()
            key: Any = am.group(1) if am.group(1) is not None else (int(am.group(2)) if am.group(2) is not None else am.group(3)[1:-1])
            if isinstance(v, dict) and key in v:
                v = v[key]
            elif isinstance(v, list) and isinstance(key, int) and 0 <= key < len(v):
                v = v[key]
            else:
                return DYN
        return DYN if v is V.OPAQUE or isinstance(v, (V.Ref, V.Jsx)) else v

    # --- модули -------------------------------------------------------------------------------------------------------
    def resolve_module(self, mod: Module, spec: str) -> Optional[Module]:
        base = mod.rel.rsplit("/", 1)[0] if "/" in mod.rel else ""
        parts = [p for p in (base + "/" + spec).split("/") if p]
        out: List[str] = []
        for p in parts:
            if p == ".":
                continue
            if p == "..":
                if out:
                    out.pop()
                continue
            out.append(p)
        path = "/".join(out)
        for suf in RESOLVE_SUFFIXES:
            if path + suf in self.modules:
                return self.modules[path + suf]
        return None

    def find_component(self, mod: Module, tag: str) -> Tuple[str, Optional[CompDef]]:
        """Тег → ("local", определение) | ("package", None) | ("unresolved", None) | ("intrinsic", None)."""
        root = tag.split(".")[0]
        if tag[:1].islower() and "." not in tag:
            return "intrinsic", None
        if root in mod.comps and "." not in tag:
            return "local", mod.comps[root]
        imp = mod.imports.get(root)
        if imp is None:
            return "unresolved", None
        if imp["from"].startswith("."):
            target = self.resolve_module(mod, imp["from"])
            if target is None:
                return "unresolved", None
            if "." in tag:
                return "unresolved", None
            if imp["default"]:
                d = next((c for c in target.comps.values() if c.default), None)
                if d is None and imp["named"] is None:
                    d = target.comps.get(root)
                return ("local", d) if d else ("unresolved", None)
            c = target.comps.get(imp["named"] or root)
            return ("local", c) if c else ("unresolved", None)
        return "package", None

    # --- элементы -----------------------------------------------------------------------------------------------------
    def element(self, el: Any, src: str, base: int, scope: Dict[str, Any], mod: Module, stack: Tuple[str, ...]) -> List[Dict[str, Any]]:
        self.count += 1
        if self.count > MAX_NODES:
            return []
        tag = el.tag
        loc = base + el.s
        if tag in FRAGMENTS:
            return self.children(el, src, base, scope, mod, stack)
        kind, cdef = self.find_component(mod, tag)
        props = self._props(el, scope, mod)
        if kind == "intrinsic":
            node: Dict[str, Any] = {"kind": LEAF_KIND.get(tag, "frame"), "tag": tag, "name": tag, "_loc": (mod, loc)}
            if node["kind"] == "frame":
                kids = self.children(el, src, base, scope, mod, stack)
                if len(kids) > 1 and all(k["kind"] == "text" and k.get("tag") == "#text" for k in kids) and any(k.get("text") is None for k in kids):
                    marks = [m for k in kids for m in (k.get("_marks") or [])]       # «Итого: {n} позиции» — слова целиком неизвестны
                    kids = [{"kind": "text", "tag": "#text", "_loc": kids[0]["_loc"], "_marks": sorted(set(marks))}]
                if len(kids) == 1 and kids[0]["kind"] == "text" and kids[0].get("tag") == "#text":
                    node["kind"] = "text"                       # единственный статический текст — лист; метка динамики переезжает на него
                    if kids[0].get("text") is not None:
                        node["text"] = kids[0]["text"]
                    if kids[0].get("_marks"):
                        node["_marks"] = list(kids[0]["_marks"])
                elif kids:
                    node["children"] = kids
                else:
                    node["kind"] = "rect"
            return [node]
        if kind == "local" and cdef is not None and cdef.jsx is not None:
            nodes = self.children(el, src, base, scope, mod, stack)
            if cdef.name in stack or len(stack) >= MAX_DEPTH:
                self.warnings.append(f"{mod.rel}: {tag} вызывает сам себя (или слишком глубоко) — развёрнут как чёрный ящик")
                return [self._black_box(tag, props, el, src, base, scope, mod, stack, loc)]
            child_scope = {"vars": {}, "props": dict(props), "props_name": cdef.props_name}
            child_scope["props"]["children"] = nodes
            for n_ in cdef.params:
                child_scope["props"].setdefault(n_, DYN)
            out = self.element(cdef.jsx[0], cdef.jsx[1], cdef.jsx[2], child_scope, cdef.module, stack + (cdef.name,))
            if cdef.multi:
                self.mark("conditional", out[0] if out else None, cdef.module, cdef.jsx[2], component=cdef.name)
            if len(out) == 1:
                node = out[0]
                node.setdefault("component", {"figmaName": cdef.name, "implName": cdef.name})
                return [node]
            return [{"kind": "frame", "tag": "div", "name": cdef.name, "component": {"figmaName": cdef.name, "implName": cdef.name},
                     "children": out, "_loc": (mod, loc)}]
        node = self._black_box(tag, props, el, src, base, scope, mod, stack, loc)
        if kind == "unresolved" or (kind == "local" and cdef is not None and cdef.jsx is None):
            self.mark("unresolved-import", node, mod, loc, component=tag)
        return [node]

    def _black_box(self, tag: str, props: Dict[str, Any], el: Any, src: str, base: int, scope: Dict[str, Any], mod: Module,
                   stack: Tuple[str, ...], loc: int) -> Dict[str, Any]:
        name = tag.split(".")[-1] if "." in tag else tag
        comp: Dict[str, Any] = {"figmaName": name, "implName": name}
        tp = {k: v for k, v in sorted(props.items()) if isinstance(v, str) and v.strip() and k not in NOT_TEXT_PROPS
              and not k.startswith(("data-", "aria-")) and k != "children" and re.search(r"\w", v)}
        if tp:
            comp["textProps"] = tp
        node: Dict[str, Any] = {"kind": "instance", "tag": tag, "name": name, "component": comp, "_loc": (mod, loc)}
        kids = self.children(el, src, base, scope, mod, stack)
        if kids:
            node["children"] = kids
        dyn = [k for k, v in props.items() if v is DYN and k not in NOT_TEXT_PROPS and k != "children"
               and not k.startswith(("data-", "aria-")) and not (k.startswith("on") and k[2:3].isupper())]
        if dyn:                                         # label={t}: слова слота неизвестны — слоты этого экземпляра не судятся
            self.mark("dynamic-text", node, mod, loc, props=dyn)
        return node

    def _props(self, el: Any, scope: Dict[str, Any], mod: Module) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        for name, val in el.attrs:
            if name == "...":
                continue
            if val is True:
                props[name] = True
            elif val[0] == "str":
                props[name] = val[1]
            elif val[0] == "expr":
                v = self.ev(val[1], scope, mod)
                props[name] = v
            else:
                props[name] = DYN
        return props

    def children(self, el: Any, src: str, base: int, scope: Dict[str, Any], mod: Module, stack: Tuple[str, ...]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        chunk = src[el.s:el.e]
        for ch in el.children:
            if isinstance(ch, V.El):
                out.extend(self.element(ch, src, base, scope, mod, stack))
                continue
            if ch[0] == "text":
                text = re.sub(r"\s+", " ", ch[1]).strip()
                if text:
                    out.append({"kind": "text", "tag": "#text", "text": text, "_loc": (mod, base + el.s + max(0, chunk.find(ch[1].strip()[:20] or ch[1])))})
                continue
            if ch[0] == "expr":
                out.extend(self.expr_child(ch[1], src, base + el.s + max(0, chunk.find(ch[1])), scope, mod, stack))
        return out

    def expr_child(self, expr: str, src: str, off: int, scope: Dict[str, Any], mod: Module, stack: Tuple[str, ...]) -> List[Dict[str, Any]]:
        s = expr.strip()
        if not s:
            return []
        if s in ("children", "props.children") or (scope.get("props_name") and s == f"{scope['props_name']}.children"):
            kids = scope["props"].get("children")
            return [dict(k) for k in kids] if isinstance(kids, list) else []
        m = re.match(r"^(.*?)\.map\(\s*\(?\s*([^)=]*?)\s*\)?\s*=>\s*(.*)\)\s*$", s, re.DOTALL)
        if m and "<" in m.group(3):
            return self._map(m.group(1), m.group(2), m.group(3), off, scope, mod, stack)
        toks = L.Lexer(s, True).lex(0)[0]
        jsx_toks = [tk for tk in toks if tk.t == L.JSX]
        if jsx_toks:
            conditional = any(tk.t == L.P and tk.v in ("&&", "||", "??", "?") for tk in toks)
            out: List[Dict[str, Any]] = []
            for tk in jsx_toks:
                el = V.parse_jsx(tk.v)
                if el is not None:
                    out.extend(self.element(el, tk.v, off + tk.s, scope, mod, stack))
            if conditional:
                for n in out:
                    self.mark("conditional", n, mod, off)
            return out
        v = self.ev(s, scope, mod)
        if isinstance(v, str):
            text = re.sub(r"\s+", " ", v).strip()
            return [{"kind": "text", "tag": "#text", "text": text, "_loc": (mod, off)}] if text else []
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return [{"kind": "text", "tag": "#text", "text": str(v), "_loc": (mod, off)}]
        if v is None or isinstance(v, bool):
            return []
        node = {"kind": "text", "tag": "#text", "_loc": (mod, off)}                     # слова неизвестны: узел без текста + метка
        self.mark("dynamic-text", node, mod, off, expr=s[:60])
        return [node]

    def _map(self, arr_src: str, params: str, body: str, off: int, scope: Dict[str, Any], mod: Module, stack: Tuple[str, ...]) -> List[Dict[str, Any]]:
        arr = self.ev(arr_src, scope, mod)
        names = [p.strip() for p in re.split(r",(?![^{]*\})", params.strip().strip("()")) if p.strip()]
        items = arr if isinstance(arr, list) else None
        toks = [tk for tk in L.Lexer(body, True).lex(0)[0] if tk.t == L.JSX]
        out: List[Dict[str, Any]] = []
        runs = items[:MAX_REPEAT] if items is not None else [DYN]
        if items is None:
            self.mark("repeated", None, mod, off, source=arr_src.strip()[:60])
        for idx, item in enumerate(runs):
            vars_ = dict(scope["vars"])
            if names:
                first = names[0]
                if first.startswith("{") and first.endswith("}"):
                    for part in [x.strip().split(":")[0].strip().split("=")[0].strip() for x in first[1:-1].split(",")]:
                        if part:
                            vars_[part] = item[part] if isinstance(item, dict) and part in item else DYN
                else:
                    vars_[first] = item
                if len(names) > 1:
                    vars_[names[1]] = idx
            sc = {"vars": vars_, "props": scope["props"], "props_name": scope.get("props_name")}
            for tk in toks:
                el = V.parse_jsx(tk.v)
                if el is not None:
                    out.extend(self.element(el, tk.v, off + tk.s, sc, mod, stack))
        return out


# ------------------------------------------------------------------------------------------------------------------ модель

def _entries(modules: Dict[str, Module], files: List[str]) -> Tuple[Optional[CompDef], List[str]]:
    defs = [c for rel in files if rel in modules for c in modules[rel].comps.values() if c.jsx is not None]
    referenced: Set[str] = set()
    for rel in files:
        if rel not in modules:
            continue
        for tk in modules[rel].toks.t:
            if tk.t == L.JSX:
                for u in re.finditer(r"<\s*([A-Z][\w$]*)", tk.v):
                    referenced.add(u.group(1))
    free = [c for c in defs if c.name not in referenced or c.default]
    warns: List[str] = []
    if not free:
        free = defs
    if not free:
        return None, warns
    free.sort(key=lambda c: (0 if c.default else 1, c.module.rel, c.name))
    if len(free) > 1 and sum(1 for c in free if c.default) != 1:
        warns.append(f"неоднозначный вход секции: {', '.join(c.name for c in free[:4])} — взят {free[0].name}")
    return free[0], warns


def _assign(node: Dict[str, Any], node_id: str, locations: Dict[str, Dict[str, Any]], marks: Dict[str, List[str]]) -> Dict[str, Any]:
    """Дерево сборки → узлы канона с id `code:<секция>/<путь>`; приватные поля уходят в locations / marks (итеративно)."""
    root_out: Dict[str, Any] = {}
    stack: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = [(node, root_out, node_id)]
    while stack:
        src, dst, nid = stack.pop()
        dst["id"], dst["kind"] = nid, src["kind"]
        for k in ("name", "tag", "text", "component"):
            if src.get(k) is not None and k != "tag":
                dst[k] = src[k]
        if src.get("tag") and src["tag"] != "#text":
            dst["tag"] = src["tag"]
        loc = src.get("_loc")
        if loc:
            mod, off = loc
            line, col = mod.lines.at(off)
            locations[nid] = {"file": mod.rel, "line": line, "column": col}
        if src.get("_marks"):
            marks[nid] = sorted(set(src["_marks"]))
        kids = [k for k in (src.get("children") or [])]
        if kids:
            dst["children"] = [{} for _ in kids]
            for i in range(len(kids) - 1, -1, -1):
                stack.append((kids[i], dst["children"][i], f"{nid}/{i}" if "/" in nid else f"{nid}/{i}"))
    return root_out


def build(files: Dict[str, str], sections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Исходники ({путь: текст}) и секции ([{id, outputs}]) → {root, locations, markers, marks, warnings, sections, stats}."""
    warnings: List[str] = []
    modules: Dict[str, Module] = {}
    for rel in sorted(files):
        if not TS.is_js(rel):
            continue
        try:
            modules[rel] = Module(rel, files[rel])
        except (RecursionError, IndexError, KeyError, TypeError, AttributeError, ValueError) as e:
            warnings.append(f"{rel}: не разобран ({type(e).__name__}: {e})")
    b = _Build(modules)
    locations: Dict[str, Dict[str, Any]] = {}
    marks: Dict[str, List[str]] = {}
    section_info: Dict[str, Any] = {}
    kids: List[Dict[str, Any]] = []
    for sec in sections:
        sid = sec["id"]
        entry, w = _entries(modules, [o for o in sec.get("outputs") or []])
        warnings.extend(f"секция {sid}: {x}" for x in w)
        if entry is None:
            warnings.append(f"секция {sid}: нет компонента с JSX — в дерево не вошла")
            section_info[sid] = {"entry": None, "files": list(sec.get("outputs") or [])}
            continue
        scope = {"vars": {}, "props": {p: DYN for p in entry.params}, "props_name": entry.props_name}
        nodes = b.element(entry.jsx[0], entry.jsx[1], entry.jsx[2], scope, entry.module, (entry.name,))
        if entry.multi:
            b.mark("conditional", nodes[0] if nodes else None, entry.module, entry.jsx[2], component=entry.name)
        if len(nodes) == 1:
            top = nodes[0]
            top.setdefault("component", {"figmaName": entry.name, "implName": entry.name})
        else:
            top = {"kind": "frame", "tag": "div", "name": entry.name, "children": nodes,
                   "component": {"figmaName": entry.name, "implName": entry.name}, "_loc": (entry.module, entry.jsx[2])}
        kids.append(_assign(top, f"code:{sid}", locations, marks))
        section_info[sid] = {"entry": f"{entry.module.rel}:{entry.name}", "files": list(sec.get("outputs") or [])}
    root = {"id": "code:root", "kind": "frame", "name": "code", "children": kids}
    census: Dict[str, int] = {}
    for m in b.markers:
        census[m["type"]] = census.get(m["type"], 0) + 1
    return {"root": root, "locations": locations, "markers": b.markers, "marks": marks, "warnings": warnings + b.warnings,
            "sections": section_info, "stats": {"nodes": len(locations) + 1, "dynamics": dict(sorted(census.items())),
                                                "modules": len(modules)}}
