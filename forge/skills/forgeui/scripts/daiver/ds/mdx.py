"""mdx.py — MDX: `*.stories.mdx` (Storybook 6: `<Meta>` + `<Story>`) и MDX-документация (Storybook 7+:
`<Meta of={ButtonStories} />` + проза) → факты (спека §5.3, QG U8: «*.stories.mdx, mdx-docs, слияние CSF+MDX»).

Что берётся:
  • ESM-строки (`import …`/`export …` в начале строки; многострочные импорты — до закрытия скобок);
  • JSX-блоки верхнего уровня (строка начинается с `<Имя`): `<Meta title component of args argTypes>`,
    `<Story name args argTypes>` (внутри `<Canvas>`/`<Preview>` тоже), остальное (`ArgsTable`, `Source`,
    `Controls`) — декорации, текста в них нет;
  • проза: markdown без ESM, JSX-блоков и HTML-комментариев; блоки кода остаются (это примеры применения).
Историй из `<Story of={…}>` (ссылка на CSF) здесь нет — их хозяин CSF-файл; MDX с `<Meta of=…>` даёт только
документацию (`docOf`) — к какому компоненту она относится, решает `merge.py` (по `of` → CSF-файл, по
`component`, по `title`, по соседству «одно имя в одном каталоге»).
Не проверено данными: формат боевой MDX-документации библиотеки не виден — разобраны канонические формы
Storybook 6/7.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import csf
from . import facts as F
from . import lex as L
from . import value as V

MAX_PROSE = 20000
_ESM = re.compile(r"(?:import|export)\s")
_FENCE = re.compile(r"\s{0,3}(```|~~~)")
_JSX_LINE = re.compile(r"\s{0,3}<[A-Za-z]")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _balanced(chunk: str) -> bool:
    """ESM-оператор закончен: скобки сбалансированы (строки/комментарии лексер учитывает)."""
    t = L.Toks(chunk, True)
    depth = 0
    for tk in t.t:
        if tk.t == L.P:
            if tk.v in L.OPEN:
                depth += 1
            elif tk.v in L.CLOSE:
                depth -= 1
    return depth <= 0 and bool(t.t) and not (t.t[-1].t == L.P and t.t[-1].v in (",", "=", "{", "(", "["))


def split(text: str) -> Tuple[str, List[Tuple[int, int]], str]:
    """(ESM-текст, [(начало, конец) JSX-блоков верхнего уровня], проза)."""
    lx = L.Lexer(text, True)
    n = len(text)
    esm: List[str] = []
    blocks: List[Tuple[int, int]] = []
    prose: List[str] = []
    in_fence = False
    i = 0
    while i < n:
        j = text.find("\n", i)
        line_end = n if j < 0 else j
        line = text[i:line_end]
        if _FENCE.match(line):
            in_fence = not in_fence
            prose.append(line)
        elif in_fence:
            prose.append(line)
        elif _ESM.match(line):
            end = line_end
            chunk = line
            while not _balanced(chunk) and end < n:
                nxt = text.find("\n", end + 1)
                nxt = n if nxt < 0 else nxt
                chunk = text[i:nxt]
                end = nxt
            esm.append(chunk)
            i = end + 1
            continue
        elif _JSX_LINE.match(line):
            pos = i + (len(line) - len(line.lstrip()))
            end = lx.scan_jsx(pos)
            if end > 0:
                blocks.append((pos, end))
                nl = text.find("\n", end)
                rest = text[end:(n if nl < 0 else nl)].strip()
                if rest:
                    prose.append(rest)
                i = (n if nl < 0 else nl) + 1
                continue
            prose.append(line)
        else:
            prose.append(line)
        i = line_end + 1
    return "\n".join(esm), blocks, "\n".join(prose)


def clean_prose(prose: str) -> Optional[str]:
    s = _COMMENT.sub("", prose)
    s = re.sub(r"\A---\n.*?\n---\n", "", s, flags=re.S)       # frontmatter
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if not s:
        return None
    return s if len(s) <= MAX_PROSE else s[:MAX_PROSE - 1] + "…"


def _walk(el: V.El):
    yield el
    for ch in el.children:
        if isinstance(ch, V.El):
            yield from _walk(ch)
    for _, val in el.attrs:
        if isinstance(val, tuple) and val[0] == "el":
            yield from _walk(val[1])


def _attr(el: V.El, name: str) -> Any:
    for n, v in el.attrs:
        if n == name:
            return v
    return None


def _attr_str(el: V.El, name: str, env: V.Env) -> Optional[str]:
    v = _attr(el, name)
    if isinstance(v, tuple) and v[0] == "str":
        return v[1]
    if isinstance(v, tuple) and v[0] == "expr":
        r = V.eval_snippet(v[1], env)
        return r if isinstance(r, str) else None
    return None


def _attr_chain(el: V.El, name: str, env: V.Env) -> Optional[str]:
    v = _attr(el, name)
    if isinstance(v, tuple) and v[0] == "expr":
        toks = L.Toks(v[1], True)
        return csf.chain_text(V.Env(toks), 0, toks.n)
    return None


def _attr_obj(el: V.El, name: str, env: V.Env) -> Optional[Dict[str, Any]]:
    v = _attr(el, name)
    if isinstance(v, tuple) and v[0] == "expr":
        r = V.eval_snippet(v[1], env)
        if isinstance(r, dict):
            return F.enc(r)
    return None


def _story_jsx(el: V.El, text: str) -> Optional[str]:
    for ch in el.children:
        if isinstance(ch, V.El):
            return text[ch.s:ch.e]
        if ch[0] == "expr" and ch[1]:
            toks = L.Toks(ch[1], True)
            idx = csf.find_jsx(toks, 0, toks.n)
            if idx is not None:
                return toks.t[idx].v
    return None


def scan(src: str, path: str) -> Dict[str, Any]:
    """Факты MDX-файла (`kind: mdx`)."""
    facts = F.empty_facts(path, "mdx")
    esm, blocks, prose = split(src)
    esm_toks = L.Toks(esm, True)
    facts["imports"] = csf.collect_imports(esm_toks)
    env = V.collect_decls(esm_toks)
    facts["prose"] = clean_prose(prose)

    meta_el: Optional[V.El] = None
    stories: List[Dict[str, Any]] = []
    for a, b in blocks:
        text = src[a:b]
        root = V.parse_jsx(text)
        if root is None:
            facts["warnings"].append("JSX-блок MDX не разобран — пропущен")
            continue
        for el in _walk(root):
            if el.tag == "Meta" and meta_el is None:
                meta_el = el
            elif el.tag == "Story" and _attr(el, "of") is None:
                name = _attr_str(el, "name", env)
                if not name:
                    continue
                jsx = _story_jsx(el, text)
                usage = [[tag, F.enc(props)] for tag, props in V.collect_usage(jsx, env)] if jsx else []
                stories.append({"export": None, "name": name, "format": "mdx", "args": _attr_obj(el, "args", env),
                                "argTypes": _attr_obj(el, "argTypes", env), "jsx": jsx, "usage": usage,
                                "component": None, "description": None})
    if meta_el is not None:
        title = _attr_str(meta_el, "title", env)
        comp = _attr_chain(meta_el, "component", env)
        facts["docOf"] = {"of": _attr_chain(meta_el, "of", env), "title": title, "component": comp}
        if title and (comp or stories):
            facts["groups"].append({
                "meta": {"title": title, "component": comp, "args": _attr_obj(meta_el, "args", env),
                         "argTypes": _attr_obj(meta_el, "argTypes", env), "description": None},
                "stories": stories})
    elif stories:
        facts["warnings"].append("<Story> без <Meta> — истории не привязать к компоненту")
    return facts
