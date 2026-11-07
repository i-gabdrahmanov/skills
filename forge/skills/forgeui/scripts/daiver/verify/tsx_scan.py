"""tsx_scan.py — урезанный скан исходников модуля: импорты, JSX-использование, литеральные значения пропсов, цветовые литералы
(спека §14.4.5 «reduced code scan по записанным build-state outputs»; задача 4.5).

Зачем отдельно от DS-гейта. Скан не знает про дизайн-систему и макет — он отвечает на вопрос «что написано в файле»: какие имена
импортированы и откуда, какие компоненты и с какими пропсами использованы (с номером строки), какие цветовые литералы лежат в
коде. Решать, хорошо ли это, будет гейт; код-гейт 4.7 берёт тот же скан и строит поверх него разбор JSX-дерева.

Никакого node: лексер и выражения — готовые блоки сканера ДС (`ds/lex.py`, `ds/value.py`, `ds/csf.collect_imports`): строки,
шаблоны, комментарии лексируются правильно, JSX-элемент — один токен, константы файла (`const size = "sm"`) разрешаются. Ничего не
выполняется, исключения наружу не выходят: файл, который не разобрался, даёт `errors` и пустые списки, а не падение гейта.

Значение пропа — одно из трёх: литерал (строка, число, булево; атрибут без значения = true), `dynamic` (выражение, статически не
известное) или не названо вовсе (spread `{...props}` — отмечен флагом `spread` у использования, значения он не даёт).

Цвета: `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb()/rgba()` в строках, шаблонах и JSX (ts/tsx/js/jsx) и во всём тексте
стилевых файлов (.less/.scss/.css) без комментариев; в канонической форме `rgba(R, G, B, A)`. `fallback: true` — литерал стоит
запасным значением внутри `var(--x, …)`: ссылка на токен есть, литерал — её страховка.

Эвристика: `#abc` в значении `href`/`id`/`to`/`for`/`name`/`src`/`key` — якорь или имя, не цвет (иначе `href="#add"` читался бы цветом).

Не проверено данными (боевых модулей нет, разбор выверен на синтетике и текстах из stories): настоящие `.tsx` с дженериками,
декораторами и сложными выражениями — лексер ds/lex осторожен (не удалось разобрать элемент — токен остаётся обычным), но число
пропущенных использований на реальных файлах неизвестно; `styled`-шаблоны и CSS-in-JS объекты дают цвета строками, а не
структурой.
"""
from __future__ import annotations

import bisect
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import color_tokens as CT
from ..ds import csf, lex as L, value as V

JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
STYLE_SUFFIXES = (".less", ".scss", ".css")
MAX_USAGES = 5000
PARSE_ERRORS = (RecursionError, IndexError, KeyError, TypeError, AttributeError, ValueError)
_HEX = re.compile(r"(?<![\w#&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
_RGB = re.compile(r"\brgba?\([^()]*\)", re.IGNORECASE)
_VAR_FALLBACK = re.compile(r"var\(\s*[^(),]*,\s*$")
_LINK_CTX = re.compile(r"(?:href|id|to|htmlFor|for|name|src|key)\s*=\s*\{?[\"'`]$")   # `href="#add"` — якорь, а не цвет #aadd


def is_js(rel: str) -> bool:
    return rel.lower().endswith(JS_SUFFIXES)


def is_style(rel: str) -> bool:
    return rel.lower().endswith(STYLE_SUFFIXES)


class _Lines:
    def __init__(self, text: str):
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def at(self, off: int) -> Tuple[int, int]:
        """Смещение → (строка, колонка), обе с 1."""
        i = max(0, bisect.bisect_right(self.starts, off) - 1)
        return i + 1, off - self.starts[i] + 1


def _color_hits(text: str, base: int, lines: _Lines, out: List[Dict[str, Any]]) -> None:
    for rx in (_HEX, _RGB):
        for m in rx.finditer(text):
            val = CT.normalize_color(m.group(0))
            if val is None or _LINK_CTX.search(text[max(0, m.start() - 16):m.start()]):
                continue
            line, col = lines.at(base + m.start())
            out.append({"value": val, "raw": m.group(0), "line": line, "col": col,
                        "fallback": bool(_VAR_FALLBACK.search(text[max(0, m.start() - 60):m.start()]))})


def scan_style(rel: str, text: str) -> Dict[str, Any]:
    """Стилевой файл: только цвета (комментарии вырезаются с сохранением строк)."""
    fmt = "css" if rel.lower().endswith(".css") else "less"
    clean = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.DOTALL)
    if fmt != "css":
        clean = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), clean)
    colors: List[Dict[str, Any]] = []
    _color_hits(clean, 0, _Lines(text), colors)
    return {"file": rel, "imports": {}, "usages": [], "colors": colors, "errors": [], "spread": 0}


def _attr_offset(chunk: str, name: str) -> int:
    m = re.search(r"(?<![\w$-])" + re.escape(name) + r"(?![\w$-])\s*(?:=|/|>|\s|$)", chunk)
    return m.start() if m else 0


class _Walker:
    def __init__(self, text: str, env: Any, lines: _Lines, out: List[Dict[str, Any]]):
        self.text, self.env, self.lines, self.out = text, env, lines, out

    def element(self, el: Any, src: str, base: int) -> None:
        """el — разобранный элемент, src — текст, из которого он разобран, base — смещение src в файле."""
        if len(self.out) >= MAX_USAGES:
            return
        later: List[Any] = []
        props: List[Dict[str, Any]] = []
        spread = False
        chunk = src[el.s:el.e]
        for name, val in el.attrs:
            if name == "...":
                spread = True
                continue
            line, _col = self.lines.at(base + el.s + _attr_offset(chunk, name))
            if val is True:
                props.append({"name": name, "kind": "literal", "value": True, "line": line})
            elif val[0] == "str":
                props.append({"name": name, "kind": "literal", "value": val[1], "line": line})
            elif val[0] == "expr":
                v = V.eval_snippet(val[1], self.env)
                if V.is_scalar(v):
                    props.append({"name": name, "kind": "literal", "value": v, "line": line})
                else:
                    props.append({"name": name, "kind": "dynamic", "line": line})
                    if isinstance(v, V.Jsx):
                        nested = V.parse_jsx(v.text)
                        if nested is not None:
                            later.append((nested, v.text, base + el.s + max(0, chunk.find(v.text))))
                    else:
                        later.append(val[1])
            elif val[0] == "el":
                props.append({"name": name, "kind": "dynamic", "line": line})
                later.append((val[1], src, base))
        if V.is_component_tag(el.tag):
            line, col = self.lines.at(base + el.s)
            self.out.append({"tag": el.tag, "line": line, "col": col, "props": props, "spread": spread})
        for x in later:
            if isinstance(x, tuple):
                self.element(x[0], x[1], x[2])
            else:
                self.expr(x, src, base + max(0, chunk.find(x)) + el.s)
        for ch in el.children:
            if isinstance(ch, V.El):
                self.element(ch, src, base)
            elif ch[0] == "expr":
                self.expr(ch[1], src, base + el.s + max(0, chunk.find(ch[1])))

    def expr(self, expr_src: str, _parent: str, base: int) -> None:
        for tk in L.Lexer(expr_src, True).lex(0)[0]:
            if tk.t == L.JSX:
                el = V.parse_jsx(tk.v)
                if el is not None:
                    self.element(el, tk.v, base + tk.s)


def scan_source(rel: str, text: str) -> Dict[str, Any]:
    """Файл ts/tsx/js/jsx → {file, imports, usages, colors, errors, spread}; стилевой файл — только цвета."""
    if is_style(rel):
        return scan_style(rel, text)
    errors: List[str] = []
    imports: Dict[str, Dict[str, Any]] = {}
    usages: List[Dict[str, Any]] = []
    colors: List[Dict[str, Any]] = []
    lines = _Lines(text)
    try:
        toks = L.Toks(text, L.is_jsx_file(rel))
        imports = csf.collect_imports(toks)
        env = V.collect_decls(toks)
        walker = _Walker(text, env, lines, usages)
        for tk in toks.t:
            if tk.t == L.JSX:
                el = V.parse_jsx(tk.v)
                if el is not None:
                    walker.element(el, tk.v, tk.s)
            if tk.t in (L.STR, L.TPL, L.JSX):
                _color_hits(text[tk.s:tk.e], tk.s, lines, colors)
    except PARSE_ERRORS as e:
        errors.append(f"{type(e).__name__}: {e}")
    return {"file": rel, "imports": imports, "usages": usages, "colors": colors, "errors": errors,
            "spread": sum(1 for u in usages if u["spread"])}


def resolve_tag(tag: str, imports: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Тег использования → {from, kind: named|default|namespace, imported, rest}: откуда пришёл корень тега; не импортирован — None
    (определён в файле или глобален: не библиотека). `rest` — хвост после корня («.Item» у `Form.Item`)."""
    root, dot, tail = tag.partition(".")
    imp = imports.get(root)
    if imp is None:
        return None
    rest = dot + tail
    if imp.get("ns"):
        return {"from": imp["from"], "kind": "namespace", "imported": None, "rest": rest}
    if imp.get("default"):
        return {"from": imp["from"], "kind": "default", "imported": None, "rest": rest}
    return {"from": imp["from"], "kind": "named", "imported": imp.get("named"), "rest": rest}
