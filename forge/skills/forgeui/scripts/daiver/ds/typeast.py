"""typeast.py — упрощённое представление типов TS: разбор из токенов в JSON-совместимое дерево.

Нужно ровно то, что читает слияние (`merge.py`): литеральные объединения → допустимые значения пропа, примитивы
→ вид пропа, ссылки на объявленные типы → разворот, объектные типы → набор пропсов, функции → «обработчик».
Всё остальное (условные и отображённые типы, `keyof`, индексный доступ, `typeof`) честно становится
`{"k": "other", "text": …}` — сканер не притворяется компилятором.

Узлы  {"k": …}
  lit    {"v": строка|число|булево}           'primary', 3, true
  kw     {"v": string|number|boolean|any|unknown|never|void|null|undefined|object|symbol|bigint}
  ref    {"name": "A.B", "args": [тип…]}
  union / inter  {"items": [тип…]}
  obj    {"members": [Member…], "open": bool}   open — есть индексная сигнатура
  fn     {"params": [тип|None…], "ret": тип|None}
  arr    {"item": тип}
  tuple  {}        tpl  {}  (шаблонный литеральный тип — строка)       other  {"text": …}
Member {"name", "optional", "type", "method", "doc"} — `doc` как в facts.doc_fact.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import facts as F
from . import lex as L
from . import value as V
from .lex import ID, NUM, P, STR, TPL

Ast = Dict[str, Any]

KEYWORDS = ("string", "number", "boolean", "any", "unknown", "never", "void", "null", "undefined", "object",
            "symbol", "bigint")
MAX_TEXT = 120
MAX_DEPTH = 14
_MEMBER_MODS = ("readonly", "public", "private", "protected", "static", "declare", "abstract", "override")


def other(t: L.Toks, i: int, j: int) -> Ast:
    text = " ".join(t.text(i, j).split())
    return {"k": "other", "text": text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"}


def split_angle(t: L.Toks, lt: int, gt: int) -> List[Tuple[int, int]]:
    """Диапазоны аргументов внутри `<…>`: lt — индекс `<`, gt — индекс после парной `>`."""
    out: List[Tuple[int, int]] = []
    a = lt + 1
    k = lt + 1
    end = gt - 1
    while k < end:
        tk = t.t[k]
        if tk.t == P and tk.v == "<":
            e = t.skip_angle(k)
            k = e if e > 0 else k + 1
            continue
        if tk.t == P and tk.v == ",":
            out.append((a, k))
            a = k + 1
        k = t.after(k)
    if a < end:
        out.append((a, end))
    return out


def split_type(t: L.Toks, i: int, j: int, sep: str) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    a = i
    k = i
    while k < j:
        tk = t.t[k]
        if tk.t == P and tk.v == "<":
            e = t.skip_angle(k)
            if 0 < e <= j:
                k = e
                continue
        if tk.t == P and tk.v == sep:
            out.append((a, k))
            a = k + 1
        k = t.after(k)
    out.append((a, j))
    return [(x, y) for x, y in out if x < y]


def _top(t: L.Toks, i: int, j: int, pred) -> int:
    """Первый токен диапазона на верхнем уровне (скобки и угловые группы целиком), удовлетворяющий pred."""
    k = i
    while k < j:
        tk = t.t[k]
        if pred(tk):
            return k
        if tk.t == P and tk.v == "<":
            e = t.skip_angle(k)
            if 0 < e <= j:
                k = e
                continue
        k = t.after(k)
    return -1


def parse_type(t: L.Toks, i: int, j: int, depth: int = 0) -> Ast:
    if depth > MAX_DEPTH or j <= i:
        return other(t, i, j)
    if _top(t, i, j, lambda x: x.t == P and x.v == "=>") > 0:
        return _function(t, i, j, depth)
    if t.t[i].t == ID and t.t[i].v in ("new", "abstract"):
        return {"k": "fn", "params": [], "ret": None}
    if _top(t, i, j, lambda x: x.t == ID and x.v == "extends") > i or \
            _top(t, i, j, lambda x: x.t == P and x.v == "?") >= 0:
        return other(t, i, j)                                  # условный тип
    for sep, kind in (("|", "union"), ("&", "inter")):
        parts = split_type(t, i, j, sep)
        if len(parts) > 1:
            return {"k": kind, "items": [parse_type(t, a, b, depth + 1) for a, b in parts]}
        if len(parts) == 1 and parts[0] != (i, j):
            return parse_type(t, parts[0][0], parts[0][1], depth + 1)       # ведущий `|`
    ast, end = _primary(t, i, j, depth)
    while ast is not None and end < j:
        if t.is_p(end, "[") and t.is_p(end + 1, "]"):
            ast, end = {"k": "arr", "item": ast}, end + 2
        else:
            return other(t, i, j)                              # индексный доступ и прочее
    return ast if ast is not None else other(t, i, j)


def _function(t: L.Toks, i: int, j: int, depth: int) -> Ast:
    """`(a: A, b: B) => R` / `<T>(x: T) => R`: параметры и результат."""
    k = i
    if t.is_p(k, "<"):
        e = t.skip_angle(k)
        k = e if e > 0 else k + 1
    params: List[Optional[Ast]] = []
    if t.is_p(k, "("):
        for a, b in t.split_top(k + 1, t.close(k), ","):
            colon = _top(t, a, b, lambda x: x.t == P and x.v == ":")
            params.append(parse_type(t, colon + 1, b, depth + 1) if colon > 0 else None)
    arrow = _top(t, i, j, lambda x: x.t == P and x.v == "=>")
    ret = parse_type(t, arrow + 1, j, depth + 1) if 0 < arrow + 1 < j else None
    return {"k": "fn", "params": params, "ret": ret}


def _primary(t: L.Toks, i: int, j: int, depth: int) -> Tuple[Optional[Ast], int]:
    a = t.t[i]
    if a.t == STR:
        return {"k": "lit", "v": a.v}, i + 1
    if a.t == NUM:
        v = V._num(a.v)
        return ({"k": "lit", "v": v} if isinstance(v, (int, float)) else other(t, i, i + 1)), i + 1
    if a.t == P and a.v == "-" and i + 1 < j and t.t[i + 1].t == NUM:
        v = V._num(t.t[i + 1].v)
        return ({"k": "lit", "v": -v} if isinstance(v, (int, float)) else other(t, i, i + 2)), i + 2
    if a.t == TPL:
        return {"k": "tpl"}, i + 1
    if a.t == P and a.v == "(":
        c = t.close(i)
        return parse_type(t, i + 1, c, depth + 1), c + 1
    if a.t == P and a.v == "{":
        c = t.close(i)
        if c >= j:
            return None, i
        if t.is_p(i + 1, "[") and t.is_id(i + 3, "in") or (t.is_id(i + 1, "readonly") and t.is_p(i + 2, "[")
                                                            and t.is_id(i + 4, "in")):
            return other(t, i, c + 1), c + 1                  # отображённый тип
        return dict(k="obj", **members(t, i)), c + 1
    if a.t == P and a.v == "[":
        c = t.close(i)
        return {"k": "tuple"}, c + 1
    if a.t == ID:
        v = a.v
        if v in ("true", "false"):
            return {"k": "lit", "v": v == "true"}, i + 1
        if v in KEYWORDS and not t.is_p(i + 1, "."):
            return {"k": "kw", "v": v}, i + 1
        if v == "readonly" and i + 1 < j:
            return _primary(t, i + 1, j, depth)
        if v in ("keyof", "typeof", "infer", "unique", "asserts", "import", "this"):
            return other(t, i, j), j
        names = [v]
        k = i + 1
        while k + 1 < j and t.is_p(k, ".") and t.t[k + 1].t == ID:
            names.append(t.t[k + 1].v)
            k += 2
        args: List[Ast] = []
        if k < j and t.is_p(k, "<"):
            e = t.skip_angle(k)
            if e < 0 or e > j:
                return other(t, i, j), j
            args = [parse_type(t, x, y, depth + 1) for x, y in split_angle(t, k, e)]
            k = e
        return {"k": "ref", "name": ".".join(names), "args": args}, k
    return other(t, i, j), j


# ── члены объектного типа ────────────────────────────────────────────────────

def _member_end(t: L.Toks, k: int, end: int) -> int:
    """Конец члена, начатого в k: `;`/`,` на верхнем уровне либо перенос строки после значения (ASI)."""
    prev = None
    m = k
    while m < end:
        tk = t.t[m]
        if prev is not None and tk.nl and L._value_end(prev) and \
                (not t._continues(tk) or (tk.t == P and tk.v == "[")):
            return m                                           # TS: `[` после переноса строки не постфикс
        if tk.t == P:
            if tk.v in (";", ","):
                return m
            if tk.v == "<":
                e = t.skip_angle(m)
                if e > 0 and e <= end:
                    prev = t.t[e - 1]
                    m = e
                    continue
            if tk.v in L.OPEN:
                m = min(t.close(m) + 1, end)
                prev = t.t[m - 1]
                continue
        prev = tk
        m += 1
    return end


def members(t: L.Toks, lb: int) -> Dict[str, Any]:
    """Члены `{ … }` (lb — индекс `{`): {"members": [...], "open": bool}. Индексная сигнатура → open."""
    end = t.close(lb)
    out: List[Dict[str, Any]] = []
    is_open = False
    k = lb + 1
    while k < end:
        if t.is_p(k, ";") or t.is_p(k, ","):
            k += 1
            continue
        e = max(_member_end(t, k, end), k + 1)
        m = _member(t, k, e)
        if m == "open":
            is_open = True
        elif m:
            out.append(m)
        k = e
    return {"members": out, "open": is_open}


def _member(t: L.Toks, a: int, b: int) -> Any:
    doc = F.doc_fact(t.doc(a))
    k = a
    while k + 1 < b and t.t[k].t == ID and t.t[k].v in _MEMBER_MODS and \
            (t.t[k + 1].t in (ID, STR) or t.is_p(k + 1, "[")):
        k += 1
    if k + 1 < b and t.t[k].t == ID and t.t[k].v in ("get", "set") and t.t[k + 1].t == ID and t.is_p(k + 2, "("):
        k += 1
    if t.is_p(k, "["):
        if t.is_id(k + 1) and t.is_p(k + 2, ":"):
            return "open"                                      # [key: string]: T
        return None                                            # [Symbol.iterator]
    if k >= b or t.t[k].t not in (ID, STR, NUM):
        return None                                            # call/construct-сигнатура
    name = str(t.t[k].v)
    k += 1
    optional = False
    if t.is_p(k, "?"):
        optional = True
        k += 1
    elif t.is_p(k, "!"):
        k += 1
    if t.is_p(k, "(") or t.is_p(k, "<"):
        return {"name": name, "optional": optional, "type": {"k": "fn", "params": [], "ret": None},
                "method": True, "doc": doc}
    if t.is_p(k, ":"):
        return {"name": name, "optional": optional, "type": parse_type(t, k + 1, b), "method": False, "doc": doc}
    return {"name": name, "optional": optional, "type": None, "method": False, "doc": doc}
