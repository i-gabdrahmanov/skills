"""value.py — статическое вычисление выражений JS/TS и разбор JSX-элемента (сканер ДС, задача 2.2).

Что вычисляется (всё, что люди кладут в `args`/`argTypes`/`parameters` сторей):
  • строки, числа (в том числе `-3`, `0x10`, `1_000`), `true/false/null`, шаблоны без `${}`;
  • объекты (`{a: 1, 'b-c': 2, [k]: 3, short, ...spread}`; методы/стрелки → OPAQUE) и массивы (со spread);
  • ссылки на константы файла: `const sizes = ['sm', 'md']; … options: sizes`, на `enum`, на поле
    константы-объекта (`Size.Large`), `Object.keys/values/entries(X)`, `Object.freeze(X)`;
  • хвосты `as const`, `as T`, `satisfies T`, `!` отбрасываются;
  • JSX-элемент как значение → `Jsx(текст)` (проп-слот: `icon: <Icon name="plus" />`).
Всё остальное (вызовы, стрелки, условия, шаблоны с `${}`) — `OPAQUE`: «значение есть, но статически не
известно». Неразрешённый идентификатор (`component: Button`) — `Ref("Button")`: имя нужно, значения нет.
Ничего не выполняется и не импортируется, исключения наружу не выходят.

Второе назначение — `parse_jsx`: JSX-текст (токен лексера) → дерево `El`; `collect_usage` обходит его и
собирает, какие компоненты с какими литеральными значениями пропсов использованы в сторях (usedValues).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import lex as L
from .lex import ID, NUM, P, STR, TPL, JSX

MAX_DEPTH = 40
MAX_USAGE_ELEMENTS = 400


class _Opaque:
    __slots__ = ()

    def __repr__(self) -> str:
        return "OPAQUE"


OPAQUE = _Opaque()


class Ref:
    """Неразрешённое имя (`Button`, `Form.Item`): значение неизвестно, имя — данные."""
    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"Ref({self.name})"

    def __eq__(self, o: object) -> bool:
        return isinstance(o, Ref) and o.name == self.name

    def __hash__(self) -> int:
        return hash(("Ref", self.name))


class Jsx:
    """JSX-элемент как значение; text — исходный текст элемента."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    def __repr__(self) -> str:
        return f"Jsx({self.text[:30]!r})"


class Obj(dict):
    """Объектный литерал; partial — часть ключей вычислить не удалось (spread неизвестного, computed)."""
    partial = False


def is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) and not isinstance(v, (Ref, Jsx))


def _num(text: str) -> Any:
    s = text.replace("_", "")
    if s.endswith("n"):
        return OPAQUE
    try:
        if s[:2] in ("0x", "0X"):
            return int(s, 16)
        if s[:2] in ("0b", "0B"):
            return int(s[2:], 2)
        if s[:2] in ("0o", "0O"):
            return int(s[2:], 8)
        if re.fullmatch(r"[0-9]+", s):
            return int(s)
        f = float(s)
    except ValueError:
        return OPAQUE
    return int(f) if f == int(f) and abs(f) < 1e15 else f


class Env:
    """Объявления верхнего уровня файла: константы (диапазоны инициализаторов, значения — лениво), enum'ы,
    функции. Через них вычислитель разворачивает `options: sizes` и `Size.Large`."""

    def __init__(self, toks: L.Toks):
        self.toks = toks
        self.consts: Dict[str, Tuple[int, int]] = {}
        self.enums: Dict[str, Obj] = {}
        self.funcs: Dict[str, Tuple[int, int]] = {}
        # присваивания полей `Primary.args = {...}` (CSF2): имя → поле → диапазон значения
        self.assigns: Dict[str, Dict[str, Tuple[int, int]]] = {}
        self._memo: Dict[str, Any] = {}
        self._busy: List[str] = []
        self.ev = Evaluator(toks, self)

    def const_value(self, name: str) -> Any:
        if name in self._memo:
            return self._memo[name]
        if name in self._busy or len(self._busy) > 6:
            return OPAQUE
        self._busy.append(name)
        try:
            v = self.ev.ev(*self.consts[name])
        finally:
            self._busy.pop()
        self._memo[name] = v
        return v


_MODS = ("export", "declare", "default")


def collect_decls(toks: L.Toks) -> Env:
    """Константы, enum'ы и функции верхнего уровня (и внутри `export`/`declare`)."""
    env = Env(toks)
    t = toks
    for i, j in t.statements():
        k = i
        while k < j and t.t[k].t == ID and t.t[k].v in _MODS:
            k += 1
        if k >= j:
            continue
        if t.is_id(k, "const") and t.is_id(k + 1, "enum"):
            k += 1
        if t.t[k].t != ID:
            continue
        kw = t.t[k].v
        if kw in ("const", "let", "var"):
            for a, b in t.split_decls(k + 1, j):
                if b > a and t.t[b - 1].t == P and t.t[b - 1].v == ";":
                    b -= 1
                if a < b and t.t[a].t == ID:
                    eq = t.find_top(a + 1, b, lambda x: x.t == P and x.v == "=")
                    if eq > 0 and eq + 1 < b:
                        env.consts[t.t[a].v] = (eq + 1, b)
        elif kw == "enum" and t.is_id(k + 1) and t.is_p(k + 2, "{"):
            env.enums[t.t[k + 1].v] = enum_members(t, env, k + 2)
        elif kw == "function" and t.is_id(k + 1):
            env.funcs[t.t[k + 1].v] = (i, j)
        elif kw == "async" and t.is_id(k + 1, "function") and t.is_id(k + 2):
            env.funcs[t.t[k + 2].v] = (i, j)
    return env


def enum_members(t: L.Toks, env: Env, lb: int) -> Obj:
    """Члены `enum X {…}` (lb — индекс `{`): {имя: значение}; без инициализатора — автонумерация."""
    out = Obj()
    nxt = 0
    for a, b in t.split_top(lb + 1, t.close(lb), ","):
        if a >= b or t.t[a].t not in (ID, STR):
            continue
        name = t.t[a].v
        if t.is_p(a + 1, "=") and a + 2 < b:
            v = env.ev.ev(a + 2, b)
        else:
            v = nxt
        out[name] = v
        nxt = v + 1 if isinstance(v, int) and not isinstance(v, bool) else 0
    return out


class Evaluator:
    def __init__(self, toks: L.Toks, env: Env):
        self.t = toks
        self.env = env
        self._depth = 0

    def ev(self, i: int, j: int) -> Any:
        if self._depth > MAX_DEPTH:
            return OPAQUE
        self._depth += 1
        try:
            return self._ev(i, j)
        except (IndexError, KeyError, RecursionError, ValueError, TypeError):
            return OPAQUE
        finally:
            self._depth -= 1

    # ── разбор диапазона ─────────────────────────────────────────────────────

    def _trim(self, i: int, j: int) -> Tuple[int, int]:
        t = self.t
        while True:
            if j - i > 1 and t.is_p(j - 1, "!"):
                j -= 1
            cut = t.find_top(i + 1, j, lambda x: x.t == ID and x.v in ("as", "satisfies"))
            if cut > 0:
                j = cut
                continue
            if j - i >= 2 and t.is_p(i, "(") and t.close(i) == j - 1:
                i, j = i + 1, j - 1
                continue
            return i, j

    def _ev(self, i: int, j: int) -> Any:
        t = self.t
        i, j = self._trim(i, j)
        n = j - i
        if n <= 0:
            return OPAQUE
        a = t.t[i]
        if n == 1:
            if a.t == STR:
                return a.v
            if a.t == TPL:
                return a.v if a.v is not None else OPAQUE
            if a.t == NUM:
                return _num(a.v)
            if a.t == JSX:
                return Jsx(a.v)
            if a.t == ID:
                return self._chain([a.v])
            return OPAQUE
        if a.t == P and a.v in ("-", "+") and n == 2 and t.t[i + 1].t == NUM:
            v = _num(t.t[i + 1].v)
            return -v if a.v == "-" and isinstance(v, (int, float)) else v
        if a.t == P and a.v == "{" and t.close(i) == j - 1:
            return self._obj(i, j)
        if a.t == P and a.v == "[" and t.close(i) == j - 1:
            return self._arr(i, j)
        if a.t == ID:
            names: List[str] = []
            k = i
            while k < j and t.t[k].t == ID:
                names.append(t.t[k].v)
                k += 1
                if k + 1 < j and (t.is_p(k, ".") or t.is_p(k, "?.")) and t.t[k + 1].t == ID:
                    k += 1
                    continue
                break
            if k == j:
                return self._chain(names)
            if k < j and t.is_p(k, "(") and t.close(k) == j - 1:
                return self._call(names, k, j)
        return OPAQUE

    # ── узлы ─────────────────────────────────────────────────────────────────

    def _chain(self, names: List[str]) -> Any:
        root = names[0]
        if len(names) == 1:
            if root == "true":
                return True
            if root == "false":
                return False
            if root == "null":
                return None
            if root in ("undefined", "NaN", "Infinity"):
                return OPAQUE
        if len(names) == 2 and names[1] in self.env.assigns.get(root, {}):
            return self.ev(*self.env.assigns[root][names[1]])      # `...Default.args`
        if root in self.env.consts:
            v: Any = self.env.const_value(root)
        elif root in self.env.enums:
            v = self.env.enums[root]
        else:
            return Ref(".".join(names))
        for idx, key in enumerate(names[1:], 1):
            if isinstance(v, Ref):
                return Ref(".".join([v.name] + names[idx:]))
            if isinstance(v, Obj) and key in v:
                v = v[key]
            else:
                return OPAQUE
        return v

    def _call(self, names: List[str], lp: int, j: int) -> Any:
        t = self.t
        full = ".".join(names)
        args = [r for r in t.split_top(lp + 1, j - 1, ",") if r[0] < r[1]]
        if full in ("Object.freeze", "Object.seal") and len(args) == 1:
            return self.ev(*args[0])
        if full in ("Object.keys", "Object.values", "Object.entries") and len(args) == 1:
            v = self.ev(*args[0])
            if isinstance(v, Obj):
                if full == "Object.keys":
                    return list(v.keys())
                if full == "Object.values":
                    return list(v.values())
                return [[k, x] for k, x in v.items()]
        return OPAQUE

    def _obj(self, i: int, j: int) -> Obj:
        t = self.t
        out = Obj()
        for a, b in t.split_top(i + 1, j - 1, ","):
            if a >= b:
                continue
            tk = t.t[a]
            if tk.t == P and tk.v == "...":
                v = self.ev(a + 1, b)
                if isinstance(v, dict):
                    out.update(v)
                    out.partial = out.partial or getattr(v, "partial", False)
                else:
                    out.partial = True
                continue
            if tk.t in (ID, STR, NUM) and t.is_p(a + 1, ":"):
                key = tk.v if tk.t != NUM else str(_num(tk.v))
                out[key] = self.ev(a + 2, b)
            elif tk.t == P and tk.v == "[":
                c = t.close(a)
                if t.is_p(c + 1, ":"):
                    kv = self.ev(a + 1, c)
                    if isinstance(kv, (str, int, float)) and not isinstance(kv, bool):
                        out[str(kv)] = self.ev(c + 2, b)
                        continue
                out.partial = True
            elif tk.t == ID and b - a == 1:
                out[tk.v] = self._chain([tk.v])
            else:                                  # метод, getter, async-метод
                k = a
                while k < b and t.t[k].t == ID and t.t[k].v in ("async", "get", "set") and \
                        not t.is_p(k + 1, "("):
                    k += 1
                if k < b and t.t[k].t in (ID, STR) and t.is_p(k + 1, "("):
                    out[t.t[k].v] = OPAQUE
                else:
                    out.partial = True
        return out

    def _arr(self, i: int, j: int) -> List[Any]:
        t = self.t
        out: List[Any] = []
        for a, b in t.split_top(i + 1, j - 1, ","):
            if a >= b:
                continue
            if t.is_p(a, "..."):
                v = self.ev(a + 1, b)
                if isinstance(v, list):
                    out.extend(v)
                else:
                    out.append(OPAQUE)
                continue
            out.append(self.ev(a, b))
        return out


def eval_snippet(src: str, env: Env) -> Any:
    """Значение выражения из куска исходника (атрибут JSX `{…}`), имена — из объявлений файла."""
    toks = L.Toks(src, True)
    return Evaluator(toks, env).ev(0, toks.n)


def has_opaque(v: Any) -> bool:
    """В значении (список/объект) есть невычисленные места."""
    if v is OPAQUE or isinstance(v, Ref):
        return True
    if isinstance(v, list):
        return any(has_opaque(x) for x in v)
    if isinstance(v, dict):
        return bool(getattr(v, "partial", False)) or any(has_opaque(x) for x in v.values())
    return False


# ── JSX ──────────────────────────────────────────────────────────────────────

class El:
    """JSX-элемент. attrs — [(имя, значение)]: True | ("str", s) | ("expr", src) | ("el", El) |
    ("spread", src) с именем "..."; children — El | ("text", s) | ("expr", src). s/e — границы элемента в
    разобранном тексте (чтобы взять исходник потомка)."""
    __slots__ = ("tag", "attrs", "children", "s", "e")

    def __init__(self, tag: str, attrs: list, children: list, s: int = 0, e: int = 0):
        self.tag, self.attrs, self.children, self.s, self.e = tag, attrs, children, s, e


def parse_jsx(text: str) -> Optional[El]:
    lx = L.Lexer(text, True)
    try:
        el, _ = _parse_el(lx, 0)
    except (IndexError, AttributeError):
        return None
    return el


def _parse_el(lx: L.Lexer, i: int) -> Tuple[El, int]:
    src = lx.src
    j = i + 1
    attrs: list = []
    if src[j:j + 1] == ">":
        tag = ""
        j += 1
    else:
        m = L._JSX_NAME.match(src, j)
        tag = m.group(0)
        j = m.end()
        while True:
            j = lx._jsx_ws(j)
            ch = src[j]
            if ch == "/":
                return El(tag, attrs, [], i, j + 2), j + 2
            if ch == ">":
                j += 1
                break
            if ch == "{":
                k = lx._skip_braced(j)
                inner = src[j + 1:k - 1].strip()
                attrs.append(("...", ("spread", inner[3:].strip() if inner.startswith("...") else inner)))
                j = k
                continue
            m = L._JSX_ATTR.match(src, j)
            name = m.group(0)
            j = lx._jsx_ws(m.end())
            if src[j:j + 1] != "=":
                attrs.append((name, True))
                continue
            j = lx._jsx_ws(j + 1)
            q = src[j]
            if q in ("\"", "'"):
                k = src.find(q, j + 1)
                attrs.append((name, ("str", src[j + 1:k])))
                j = k + 1
            elif q == "{":
                k = lx._skip_braced(j)
                attrs.append((name, ("expr", src[j + 1:k - 1].strip())))
                j = k
            else:
                child, j = _parse_el(lx, j)
                attrs.append((name, ("el", child)))
    children: list = []
    while True:
        ch = src[j]
        if ch == "{":
            k = lx._skip_braced(j)
            children.append(("expr", src[j + 1:k - 1].strip()))
            j = k
        elif ch == "<":
            if src[j + 1:j + 2] == "/":
                end = L._JSX_CLOSE.match(src, j).end()
                return El(tag, attrs, children, i, end), end
            child, j = _parse_el(lx, j)
            children.append(child)
        else:
            m = L._JSX_TEXT.match(src, j)
            children.append(("text", m.group(0)))
            j = m.end()


def is_component_tag(tag: str) -> bool:
    """Компонент, а не DOM-тег: `Button`, `Form.Item`, `Ui.Button` (с заглавной первой буквой корня)."""
    return bool(tag) and tag[0].isupper()


def _jsx_tokens(src: str) -> List[str]:
    return [tk.v for tk in L.Lexer(src, True).lex(0)[0] if tk.t == JSX]


def collect_usage(text: str, env: Env) -> List[Tuple[str, Dict[str, List[Any]]]]:
    """Использование компонентов в JSX-тексте: [(тег, {проп: [скалярные значения]})] в порядке обхода, включая
    вложенные элементы, элементы в значениях атрибутов и в `{…}`-контейнерах. Значения — только литералы
    (строки, числа, булевы; атрибут без значения = true); вычисляемое пропускается."""
    out: List[Tuple[str, Dict[str, List[Any]]]] = []
    budget = [MAX_USAGE_ELEMENTS]

    def walk_expr(src: str) -> None:
        for jx in _jsx_tokens(src):
            el = parse_jsx(jx)
            if el is not None:
                walk(el)

    def walk(el: El) -> None:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        props: Dict[str, List[Any]] = {}
        later: List[Any] = []                  # вложенное обходим ПОСЛЕ записи самого элемента (прямой порядок)
        for name, val in el.attrs:
            if name == "...":
                continue
            if val is True:
                props.setdefault(name, []).append(True)
            elif val[0] == "str":
                props.setdefault(name, []).append(val[1])
            elif val[0] == "expr":
                v = eval_snippet(val[1], env)
                if is_scalar(v):
                    props.setdefault(name, []).append(v)
                elif isinstance(v, Jsx):
                    nested = parse_jsx(v.text)
                    if nested is not None:
                        later.append(nested)
                elif v is OPAQUE or isinstance(v, (Ref, list, dict)):
                    later.append(val[1])
            elif val[0] == "el":
                later.append(val[1])
        if is_component_tag(el.tag):
            out.append((el.tag, props))
        for x in later:
            walk(x) if isinstance(x, El) else walk_expr(x)
        for ch in el.children:
            if isinstance(ch, El):
                walk(ch)
            elif ch[0] == "expr":
                walk_expr(ch[1])

    root = parse_jsx(text)
    if root is not None:
        walk(root)
    return out


_NL_AFTER_OPEN = re.compile(r"(?<=[>{])\s*[\r\n]\s*")
_NL_BEFORE_CLOSE = re.compile(r"\s*[\r\n]\s*(?=[<}])")
_NL = re.compile(r"\s*[\r\n]\s*")
_SP_BEFORE_CLOSE = re.compile(r"\s+(/?>)")


def normalize_jsx(text: str) -> str:
    """Однострочная форма JSX для примеров: переносы у границ тегов и скобок убираются (JSX их и так обрезает),
    остальные — в пробел. Пример иллюстративен: пробелы между тегами на одной строке не трогаются."""
    t = _NL_BEFORE_CLOSE.sub("", _NL_AFTER_OPEN.sub("", text.strip()))     # JSX обрезает строки у границ тегов
    t = _NL.sub(" ", t)
    t = _SP_BEFORE_CLOSE.sub(r"\1", t)
    return re.sub(r"(?<=\S)/>", " />", t)                    # единая форма самозакрытого тега: `<X a="1" />`
