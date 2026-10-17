"""lex.py — лексер JS/TS/JSX без зависимостей (сканер дизайн-системы, задача 2.2, спека §5.3).

Зачем свой лексер. Контракт компонентов читается из stories и деклараций библиотеки; настоящий парсер
(typescript) бывает не установлен, а основной путь сканера не должен зависеть от node. Здесь — ровно то, без
чего разбор ненадёжен: строки/шаблоны/регулярки/комментарии лексируются правильно (иначе апостроф в
JSX-тексте «Don't» сбивает кавычки до конца файла), JSX-элемент — ОДИН токен (его внутренности разбирает
`value.parse_jsx`), `/** … */` прикрепляется к следующему токену (JSDoc: описание, `@deprecated`), скобки
сопоставлены.

Решения, не очевидные из текста:
  • `<` и `>` — всегда односимвольные токены: `Array<Array<string>>` иначе не закрыть, а `=>` остаётся одним
    токеном. Сравнения `<=`/`>=` поэтому пара токенов — для разбора объявлений это не важно.
  • JSX начинается там, где может начинаться выражение (после `(`, `,`, `=`, `=>`, `return`, `?`, `:`, `&&`…) и
    только в файлах, где JSX возможен (`.tsx/.jsx/.js/.mjs/.cjs/.mdx`). После идентификатора `<` — обобщение
    или сравнение. Если разбор элемента не удался (обобщённая стрелка `<T,>(x: T) => x` в tsx), `<` остаётся
    обычным токеном — файл не ломается.
  • Регулярка отличается от деления по предыдущему токену (после `)`/`]`/значения — деление).
  • Конец оператора (`stmt_end`) учитывает автоподстановку `;` (ASI): перенос строки после значения, за
    которым не идёт продолжение (`.`, `(`, `[`, оператор), заканчивает оператор. В файлах с `;` (почти все)
    это не требуется, в файлах без них — спасает объявления подряд.
Работает в один проход по строке, глубина вложенных контейнеров (`${}` и `{}` в JSX) — рекурсия лексера.
"""
from __future__ import annotations

import re
from typing import Iterator, List, Optional, Tuple

ID, NUM, STR, TPL, JSX, RE, P = "id", "num", "str", "tpl", "jsx", "re", "p"

_WS = re.compile(r"[ \t\r\n\f\v\u00a0\ufeff\u2028\u2029]+")
_IDENT = re.compile(r"[^\W\d][\w$]*|\$[\w$]*")
_NUM = re.compile(r"0[xX][0-9a-fA-F_]+n?|0[bB][01_]+n?|0[oO][0-7_]+n?"
                  r"|(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9]+)?n?")
_JSX_NAME = re.compile(r"[A-Za-z_$][\w$.:-]*")
_JSX_ATTR = re.compile(r"[A-Za-z_$][\w$:-]*")
_JSX_CLOSE = re.compile(r"</\s*([\w$.:-]*)\s*>")
_JSX_TEXT = re.compile(r"[^<{]+")
_REGEX_FLAGS = re.compile(r"[a-z]*")
_ESC = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])")
_SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_LINE_BREAKS = ("\n", "\r", "\u2028", "\u2029")

_P3 = ("...", "===", "!==", "**=", "&&=", "||=", "??=")
_P2 = ("=>", "==", "!=", "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
       "**")
OPEN = {"(": ")", "[": "]", "{": "}"}
CLOSE = {")": "(", "]": "[", "}": "{"}

# после этих слов выражение продолжается (регулярка и JSX возможны), а значение — нет
_EXPR_KW = frozenset(("return", "typeof", "case", "default", "else", "do", "in", "of", "void", "delete",
                      "throw", "new", "yield", "await", "instanceof", "export"))
_CONT_P = frozenset((".", "?.", "(", "[", ",", "+", "-", "*", "/", "%", "=", "?", ":", "&&", "||", "??", "|",
                     "&", "^", "=>", "<", ">", "==", "===", "!=", "!==", "**", "+=", "-=", "*=", "/=", "%=",
                     "&=", "|=", "^=", "**=", "&&=", "||=", "??=", "..."))
_CONT_ID = frozenset(("in", "instanceof", "as", "satisfies", "extends", "implements", "else", "catch",
                      "finally", "is"))


class Tok:
    """Токен: t — вид, v — значение (у строки — раскодированное; у шаблона с `${}` — None), s/e — смещения в
    исходнике, doc — предшествующий `/** */` целиком (или None), nl — между предыдущим токеном и этим был
    перевод строки."""
    __slots__ = ("t", "v", "s", "e", "doc", "nl")

    def __init__(self, t: str, v, s: int, e: int, doc: Optional[str], nl: bool):
        self.t, self.v, self.s, self.e, self.doc, self.nl = t, v, s, e, doc, nl

    def __repr__(self) -> str:
        return f"Tok({self.t},{self.v!r})"


def _unescape(raw: str) -> str:
    if "\\" not in raw:
        return raw

    def rep(m) -> str:
        g = m.group(1)
        if g[0] == "u" and len(g) > 1:
            h = g[2:-1] if g[1] == "{" else g[1:]
            try:
                return chr(int(h, 16))
            except (ValueError, OverflowError):
                return g
        if g[0] == "x" and len(g) == 3:
            return chr(int(g[1:], 16))
        if g in ("\n", "\r\n", "\r", "\u2028", "\u2029"):
            return ""
        return _SIMPLE_ESC.get(g, g)

    out = _ESC.sub(rep, raw)
    if any("\ud800" <= ch <= "\udfff" for ch in out):          # суррогатные пары \ud83d\ude00
        try:
            out = out.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeError:
            out = "".join("\ufffd" if "\ud800" <= ch <= "\udfff" else ch for ch in out)
    return out


def _expr_start(prev: Optional[Tok]) -> bool:
    """Здесь может начинаться выражение (значит, `/` — регулярка, а `<` — возможно JSX)."""
    if prev is None:
        return True
    if prev.t == P:
        return prev.v not in (")", "]")
    if prev.t == ID:
        return prev.v in _EXPR_KW
    return False


def _value_end(t: Tok) -> bool:
    if t.t == ID:
        return t.v not in _EXPR_KW
    if t.t in (NUM, STR, TPL, JSX, RE):
        return True
    return t.v in (")", "]", "}", ">")


class Lexer:
    def __init__(self, src: str, jsx: bool):
        self.src = src
        self.n = len(src)
        self.jsx = jsx

    # ── строки, шаблоны, регулярки ───────────────────────────────────────────

    def _scan_string(self, i: int) -> Tuple[int, str]:
        src, n = self.src, self.n
        q = src[i]
        j = i + 1
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == q:
                return j + 1, _unescape(src[i + 1:j])
            if c == "\n":
                break                    # незакрытая строка: JS-ошибка, останавливаемся на конце строки
            j += 1
        j = min(j, n)
        return j, _unescape(src[i + 1:j])

    def _scan_template(self, i: int) -> Tuple[int, Optional[str]]:
        src, n = self.src, self.n
        j = i + 1
        has_subst = False
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "`":
                return j + 1, (None if has_subst else _unescape(src[i + 1:j]))
            if c == "$" and src[j + 1:j + 2] == "{":
                has_subst = True
                _, pos = self.lex(j + 2, stop_on_close=True, collect=False)
                if pos < 0:
                    return n, None
                j = pos + 1
                continue
            j += 1
        return n, None

    def _scan_regex(self, i: int) -> int:
        """Конец регулярки (после флагов) или -1, если это не регулярка."""
        src, n = self.src, self.n
        j = i + 1
        in_class = False
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                return -1
            if in_class:
                if c == "]":
                    in_class = False
            elif c == "[":
                in_class = True
            elif c == "/":
                return _REGEX_FLAGS.match(src, j + 1).end()
            j += 1
        return -1

    # ── JSX ──────────────────────────────────────────────────────────────────

    def _skip_braced(self, j: int) -> int:
        """src[j] == '{': индекс после парной '}' или -1."""
        _, pos = self.lex(j + 1, stop_on_close=True, collect=False)
        return pos + 1 if pos >= 0 else -1

    def _jsx_ws(self, j: int) -> int:
        """Пробелы и комментарии между атрибутами."""
        src, n = self.src, self.n
        while j < n:
            m = _WS.match(src, j)
            if m:
                j = m.end()
                continue
            if src.startswith("/*", j):
                k = src.find("*/", j + 2)
                if k < 0:
                    return n
                j = k + 2
                continue
            if src.startswith("//", j):
                k = src.find("\n", j)
                if k < 0:
                    return n
                j = k
                continue
            break
        return j

    def scan_jsx(self, i: int) -> int:
        """src[i] == '<': индекс после элемента/фрагмента или -1, если это не JSX."""
        src, n = self.src, self.n
        j = i + 1
        if src[j:j + 1] == ">":
            return self._jsx_children(j + 1, "")
        m = _JSX_NAME.match(src, j)
        if not m:
            return -1
        name = m.group(0)
        j = m.end()
        while True:
            j = self._jsx_ws(j)
            if j >= n:
                return -1
            ch = src[j]
            if ch == "/":
                return j + 2 if src[j + 1:j + 2] == ">" else -1
            if ch == ">":
                return self._jsx_children(j + 1, name)
            if ch == "{":                                   # {...spread}
                k = self._skip_braced(j)
                if k < 0:
                    return -1
                j = k
                continue
            m = _JSX_ATTR.match(src, j)
            if not m:
                return -1
            j = self._jsx_ws(m.end())
            if src[j:j + 1] != "=":
                continue
            j = self._jsx_ws(j + 1)
            q = src[j:j + 1]
            if q in ('"', "'"):
                k = src.find(q, j + 1)
                if k < 0:
                    return -1
                j = k + 1
            elif q == "{":
                k = self._skip_braced(j)
                if k < 0:
                    return -1
                j = k
            elif q == "<":
                k = self.scan_jsx(j)
                if k < 0:
                    return -1
                j = k
            else:
                return -1

    def _jsx_children(self, j: int, name: str) -> int:
        src, n = self.src, self.n
        while j < n:
            ch = src[j]
            if ch == "{":
                k = self._skip_braced(j)
                if k < 0:
                    return -1
                j = k
            elif ch == "<":
                if src[j + 1:j + 2] == "/":
                    m = _JSX_CLOSE.match(src, j)
                    if not m or m.group(1) != name:
                        return -1
                    return m.end()
                k = self.scan_jsx(j)
                if k < 0:
                    return -1
                j = k
            else:
                j = _JSX_TEXT.match(src, j).end()
        return -1

    # ── основной цикл ────────────────────────────────────────────────────────

    def lex(self, i: int, stop_on_close: bool = False, collect: bool = True) -> Tuple[List[Tok], int]:
        """Токены от i. stop_on_close — остановиться на несбалансированной `}` и вернуть её индекс (контейнеры
        `${}` и JSX `{}`); -1, если файл кончился раньше."""
        src, n = self.src, self.n
        out: List[Tok] = []
        depth = 0
        nl = False
        doc: Optional[str] = None
        prev: Optional[Tok] = None
        while i < n:
            c = src[i]
            if c in " \t\r\n\f\v\u00a0\ufeff\u2028\u2029":
                m = _WS.match(src, i)
                if any(b in m.group(0) for b in _LINE_BREAKS):
                    nl = True
                i = m.end()
                continue
            if c == "/":
                c2 = src[i + 1:i + 2]
                if c2 == "/":
                    j = src.find("\n", i)
                    i = n if j < 0 else j
                    continue
                if c2 == "*":
                    j = src.find("*/", i + 2)
                    end = n if j < 0 else j + 2
                    body = src[i:end]
                    if body.startswith("/**") and body != "/**/":
                        doc = body
                    if "\n" in body:
                        nl = True
                    i = end
                    continue
            s = i
            if c in "\"'":
                i, val = self._scan_string(i)
                tok = Tok(STR, val, s, i, doc, nl)
            elif c == "`":
                i, val = self._scan_template(i)
                tok = Tok(TPL, val, s, i, doc, nl)
            elif "0" <= c <= "9" or (c == "." and "0" <= src[i + 1:i + 2] <= "9" and src[i + 1:i + 2]):
                m = _NUM.match(src, i)
                i = m.end()
                tok = Tok(NUM, m.group(0), s, i, doc, nl)
            elif c.isalpha() or c in "_$" or ord(c) > 127:
                m = _IDENT.match(src, i)
                if m:
                    i = m.end()
                    tok = Tok(ID, m.group(0), s, i, doc, nl)
                else:
                    i += 1
                    tok = Tok(P, c, s, i, doc, nl)
            elif c == "/" and _expr_start(prev) and self._scan_regex(i) > 0:
                i = self._scan_regex(i)
                tok = Tok(RE, src[s:i], s, i, doc, nl)
            elif (c == "<" and self.jsx and _expr_start(prev) and re.match(r"[A-Za-z_$>]", src[i + 1:i + 2] or " ")
                  and self.scan_jsx(i) > 0):
                i = self.scan_jsx(i)
                tok = Tok(JSX, src[s:i], s, i, doc, nl)
            else:
                three, two = src[i:i + 3], src[i:i + 2]
                if three in _P3:
                    v = three
                elif two in _P2 and not (two == "?." and "0" <= src[i + 2:i + 3] <= "9" and src[i + 2:i + 3]):
                    v = two
                else:
                    v = c
                i += len(v)
                tok = Tok(P, v, s, i, doc, nl)
                if stop_on_close:
                    if v == "{":
                        depth += 1
                    elif v == "}":
                        if depth == 0:
                            return out, s
                        depth -= 1
            if collect:
                out.append(tok)
            prev = tok
            doc = None
            nl = False
        return out, -1


# ── JSDoc ────────────────────────────────────────────────────────────────────

_LINK = re.compile(r"\{@(?:link|linkcode|linkplain)\s+([^}|\s]+)(?:[\s|]+([^}]*))?\}")


def clean_text(s: str) -> str:
    """Свёртка пробелов и {@link X|текст} → текст."""
    s = _LINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), s)
    return re.sub(r"\s+", " ", s).strip()


class Doc:
    """Разобранный JSDoc: text — описание (одна строка), tags — [(имя, текст)] в порядке записи."""
    __slots__ = ("text", "tags")

    def __init__(self, text: str, tags: List[Tuple[str, str]]):
        self.text, self.tags = text, tags

    def tag(self, name: str) -> Optional[str]:
        for n, v in self.tags:
            if n == name:
                return v
        return None

    def has(self, name: str) -> bool:
        return any(n == name for n, _ in self.tags)


def parse_jsdoc(raw: Optional[str]) -> Optional[Doc]:
    if not raw:
        return None
    body = raw[3:-2] if raw.endswith("*/") else raw[3:]
    text_lines: List[str] = []
    tags: List[List] = []
    cur: Optional[List] = None
    for ln in body.split("\n"):
        s = ln.strip()
        if s.startswith("*"):
            s = s[1:].strip()
        m = re.match(r"@([A-Za-z]+)\s*(.*)$", s)
        if m:
            cur = [m.group(1), [m.group(2)]]
            tags.append(cur)
        elif cur is not None:
            cur[1].append(s)
        else:
            text_lines.append(s)
    return Doc(clean_text(" ".join(text_lines)), [(n, clean_text(" ".join(p))) for n, p in tags])


# ── поток токенов ────────────────────────────────────────────────────────────

class Toks:
    """Токены файла + парные скобки + операции над диапазонами. Индексы — позиции в `t`."""

    def __init__(self, src: str, jsx: bool):
        self.src = src
        self.jsx = jsx
        self.t: List[Tok] = Lexer(src, jsx).lex(0)[0]
        self.n = len(self.t)
        self.m = self._pairs()

    def _pairs(self) -> List[int]:
        m = [-1] * self.n
        stack: List[int] = []
        for idx, tk in enumerate(self.t):
            if tk.t != P:
                continue
            if tk.v in OPEN:
                stack.append(idx)
            elif tk.v in CLOSE:
                want = CLOSE[tk.v]
                k = len(stack) - 1
                while k >= 0 and self.t[stack[k]].v != want:
                    k -= 1
                if k < 0:
                    continue                    # лишняя закрывающая — игнорируем
                o = stack[k]
                del stack[k:]                   # незакрытые внутри остаются -1
                m[o] = idx
                m[idx] = o
        return m

    # проверки
    def is_p(self, i: int, v: str) -> bool:
        return 0 <= i < self.n and self.t[i].t == P and self.t[i].v == v

    def is_id(self, i: int, v: Optional[str] = None) -> bool:
        return 0 <= i < self.n and self.t[i].t == ID and (v is None or self.t[i].v == v)

    def kind(self, i: int) -> Optional[str]:
        return self.t[i].t if 0 <= i < self.n else None

    def close(self, i: int) -> int:
        """Индекс парной закрывающей скобки (или n, если не закрыта)."""
        j = self.m[i] if 0 <= i < self.n else -1
        return j if j >= 0 else self.n

    def after(self, i: int) -> int:
        """Индекс после группы, начатой в i (скобка — вся группа; иначе i + 1)."""
        if 0 <= i < self.n and self.t[i].t == P and self.t[i].v in OPEN:
            return self.close(i) + 1
        return i + 1

    def text(self, i: int, j: int) -> str:
        """Исходный текст токенов [i, j)."""
        if j <= i or i >= self.n:
            return ""
        return self.src[self.t[i].s:self.t[min(j, self.n) - 1].e]

    def doc(self, i: int) -> Optional[Doc]:
        return parse_jsdoc(self.t[i].doc) if 0 <= i < self.n else None

    # разбиение
    def split_top(self, i: int, j: int, sep: str = ",") -> List[Tuple[int, int]]:
        """Диапазоны [a, b) между разделителями sep на верхнем уровне [i, j) (скобки целиком)."""
        out: List[Tuple[int, int]] = []
        a = i
        k = i
        while k < j:
            if self.t[k].t == P and self.t[k].v == sep:
                out.append((a, k))
                a = k + 1
                k += 1
                continue
            k = self.after(k)
        if a < j:
            out.append((a, j))
        return out

    def split_decls(self, i: int, j: int) -> List[Tuple[int, int]]:
        """Как split_top по запятой, но запятые внутри обобщений `Foo<A, B>` не делят (объявления переменных:
        `const X = forwardRef<El, Props>(…), Y = 1`)."""
        out: List[Tuple[int, int]] = []
        a = i
        k = i
        while k < j:
            tk = self.t[k]
            if tk.t == P and tk.v == "<" and k > i and self.t[k - 1].t == ID:
                e = self.skip_angle(k)
                if 0 < e <= j:
                    k = e
                    continue
            if tk.t == P and tk.v == ",":
                out.append((a, k))
                a = k + 1
                k += 1
                continue
            k = self.after(k)
        if a < j:
            out.append((a, j))
        return out

    def find_top(self, i: int, j: int, pred) -> int:
        """Первый индекс k в [i, j) на верхнем уровне, для которого pred(token) истинно; иначе -1."""
        k = i
        while k < j:
            if pred(self.t[k]):
                return k
            k = self.after(k)
        return -1

    def skip_angle(self, i: int) -> int:
        """t[i] == '<': индекс после парной '>' (обобщения TS) или -1."""
        depth = 0
        k = i
        while k < self.n:
            tk = self.t[k]
            if tk.t == P:
                if tk.v == "<":
                    depth += 1
                elif tk.v == ">":
                    depth -= 1
                    if depth == 0:
                        return k + 1
                elif tk.v == ";":
                    return -1
                elif tk.v in OPEN:
                    k = self.close(k) + 1
                    continue
                elif tk.v in CLOSE:
                    return -1
            k += 1
        return -1

    # границы операторов
    def stmt_end(self, i: int, limit: Optional[int] = None) -> int:
        """Индекс после оператора, начатого в i: `;` на верхнем уровне либо ASI по переносу строки."""
        n = self.n if limit is None else limit
        k = i
        prev: Optional[Tok] = None
        while k < n:
            tk = self.t[k]
            if prev is not None and tk.nl and _value_end(prev) and not self._continues(tk):
                return k
            if tk.t == P:
                if tk.v == ";":
                    return k + 1
                if tk.v in OPEN:
                    c = self.close(k)
                    k = min(c + 1, n)
                    prev = self.t[k - 1]
                    continue
                if tk.v in CLOSE:
                    return k                    # чужая закрывающая: оператор кончился раньше
            prev = tk
            k += 1
        return n

    @staticmethod
    def _continues(tk: Tok) -> bool:
        if tk.t == P:
            return tk.v in _CONT_P
        return tk.t == ID and tk.v in _CONT_ID

    def statements(self, i: int = 0, limit: Optional[int] = None) -> Iterator[Tuple[int, int]]:
        n = self.n if limit is None else limit
        while i < n:
            if self.t[i].t == P and self.t[i].v == ";":
                i += 1
                continue
            j = max(self.stmt_end(i, n), i + 1)
            yield i, j
            i = j


def is_jsx_file(name: str) -> bool:
    """JSX возможен везде, кроме .ts/.mts/.cts/.d.ts."""
    low = name.lower()
    return not low.endswith((".ts", ".mts", ".cts"))
