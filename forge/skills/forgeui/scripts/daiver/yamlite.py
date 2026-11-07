"""yamlite.py — разбор YAML-подмножества конфигов forge-ui (`.daiver/config.yaml`, карта
компонентов, track.yaml). Только stdlib: PyYAML на целевой машине не гарантирован.

Поддержано ровно то, что пишут шаблоны и люди в этих файлах:
  • блочные словари и списки по отступам, элементы-словари (`- match: X`);
  • flow-коллекции `{ width: 1280, height: 900 }`, `[Label, LabelCell]` (с вложенностью);
  • скаляры: строки (в т.ч. в кавычках), целые и дробные, true/false, null/~;
    `on`/`off`/`yes`/`no` остаются СТРОКАМИ (семантика YAML 1.2) — смысл им придаёт читатель
    конфига, а не парсер;
  • комментарии `#` вне кавычек; блочные скаляры `|` и `>`.
Якорей, тегов и многодокументности нет. Неразобранное — `YamlError` с номером строки:
вызывающий решает, падать ли в дефолты (config.py так и делает).

Отличие от парсера в skills/forgetech/scripts/_techlib.py намеренное: тот отдаёт плоский
словарь «a.b.c» → строка ради сравнения значений конфигов до/после; здесь нужно дерево с типами.
"""
from __future__ import annotations

import re
from typing import Any, List, Tuple


class YamlError(ValueError):
    def __init__(self, msg: str, line: int):
        super().__init__(f"строка {line}: {msg}")
        self.line = line


_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")
_KEY = re.compile(r"""^(?:"((?:[^"\\]|\\.)*)"|'((?:[^']|'')*)'|([^\s#'"{}\[\],&*!|>%@`][^:#]*?|[^:#\s'"][^:#]*?))\s*:(?:\s+(.*)|\s*)$""")


def strip_comment(line: str) -> str:
    """Строка без комментария `#` (вне кавычек; # должен идти после пробела или в начале).

    Внутри двойных кавычек `\\"` и `\\\\` — эскейпы: экранированная кавычка строку не закрывает."""
    out, q, skip = [], None, False
    for i, ch in enumerate(line):
        if skip:
            skip = False
        elif q:
            if q == '"' and ch == "\\":
                skip = True
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


_DQ_ESC = re.compile(r"\\(u[0-9a-fA-F]{4}|.)", re.DOTALL)
_DQ_MAP = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "0": "\0", "b": "\b", "f": "\f"}


def _unescape_dq(inner: str) -> str:
    """Один проход по эскейпам двойных кавычек (последовательные replace ломали `\\\\n`)."""
    def rep(m):
        g = m.group(1)
        if g[0] == "u" and len(g) == 5:
            return chr(int(g[1:], 16))
        return _DQ_MAP.get(g, "\\" + g)          # неизвестный эскейп остаётся как написан
    return _DQ_ESC.sub(rep, inner)


def scalar(raw: str) -> Any:
    v = raw.strip()
    if v == "" or v == "~" or v == "null" or v == "Null" or v == "NULL":
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        if v[0] == "'":
            return inner.replace("''", "'")
        return _unescape_dq(inner)
    if v in ("true", "True", "TRUE"):
        return True
    if v in ("false", "False", "FALSE"):
        return False
    if _INT.match(v):
        return int(v)
    if _FLOAT.match(v):
        return float(v)
    return v


def _flow(text: str, line: int) -> Any:
    """Разбор flow-значения: `{a: 1, b: [x, y]}`, `[a, b]` или скаляр."""
    pos = 0

    def skip_ws():
        nonlocal pos
        while pos < len(text) and text[pos] in " \t":
            pos += 1

    def parse_value(stop: str) -> Any:
        nonlocal pos
        skip_ws()
        if pos < len(text) and text[pos] == "{":
            pos += 1
            out: dict = {}
            while True:
                skip_ws()
                if pos < len(text) and text[pos] == "}":
                    pos += 1
                    return out
                key = parse_scalar_token(":,}")
                skip_ws()
                if pos >= len(text) or text[pos] != ":":
                    raise YamlError(f"во flow-словаре нет ':' после ключа {key!r}", line)
                pos += 1
                out[str(key)] = parse_value(",}")
                skip_ws()
                if pos < len(text) and text[pos] == ",":
                    pos += 1
                    continue
                if pos < len(text) and text[pos] == "}":
                    pos += 1
                    return out
                raise YamlError("незакрытый flow-словарь '{'", line)
        if pos < len(text) and text[pos] == "[":
            pos += 1
            items: list = []
            while True:
                skip_ws()
                if pos < len(text) and text[pos] == "]":
                    pos += 1
                    return items
                items.append(parse_value(",]"))
                skip_ws()
                if pos < len(text) and text[pos] == ",":
                    pos += 1
                    continue
                if pos < len(text) and text[pos] == "]":
                    pos += 1
                    return items
                raise YamlError("незакрытый flow-список '['", line)
        return parse_scalar_token(stop)

    def parse_scalar_token(stop: str) -> Any:
        nonlocal pos
        skip_ws()
        if pos < len(text) and text[pos] in "'\"":
            q = text[pos]
            end = pos + 1
            while end < len(text):
                if text[end] == q:
                    if q == "'" and end + 1 < len(text) and text[end + 1] == "'":
                        end += 2
                        continue
                    if q == '"':
                        back = 0
                        while end - 1 - back > pos and text[end - 1 - back] == "\\":
                            back += 1
                        if back % 2 == 1:                 # нечётное число слэшей — кавычка экранирована
                            end += 1
                            continue
                    break
                end += 1
            if end >= len(text):
                raise YamlError("незакрытая кавычка", line)
            tok = text[pos:end + 1]
            pos = end + 1
            return scalar(tok)
        start = pos
        while pos < len(text) and text[pos] not in stop:
            pos += 1
        return scalar(text[start:pos])

    value = parse_value("")
    skip_ws()
    if pos != len(text):
        raise YamlError(f"лишний текст после значения: {text[pos:]!r}", line)
    return value


def _value(raw: str, line: int) -> Any:
    v = raw.strip()
    if v.startswith(("{", "[")):
        return _flow(v, line)
    return scalar(v)


def _split_key(body: str):
    m = _KEY.match(body)
    if not m:
        return None
    if m.group(1) is not None:
        key = _unescape_dq(m.group(1))
    elif m.group(2) is not None:
        key = m.group(2).replace("''", "'")
    else:
        key = m.group(3).strip()
    return key, (m.group(4) or "")


def loads(text: str) -> Any:
    """YAML-подмножество → dict/list/скаляр. Пустой документ → {}."""
    lines: List[Tuple[int, int, str]] = []          # (номер строки, отступ, тело)
    raw_lines = text.splitlines()
    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i]
        i += 1
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError("табуляция в отступе", i)
        body = strip_comment(raw)
        if not body.strip() or body.strip() in ("---", "..."):
            continue
        indent = len(body) - len(body.lstrip(" "))
        stripped = body.strip()
        # блочный скаляр: «key: |» / «- |» — собираем строки глубже отступа как текст
        m = re.match(r"^(.*?)(?::\s+|^-\s+)([|>])[-+]?$", stripped)
        if m and (stripped.endswith(("|", ">", "|-", ">-", "|+", ">+"))):
            block: List[str] = []
            while i < len(raw_lines):
                nxt = raw_lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip(" "))) <= indent:
                    break
                block.append(nxt.strip())
                i += 1
            joiner = "\n" if m.group(2) == "|" else " "
            text_value = joiner.join(block).strip()
            quoted = '"' + text_value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
            stripped = stripped[: stripped.rfind(m.group(2))].rstrip() + " " + quoted
        lines.append((i, indent, stripped))
    if not lines:
        return {}
    value, pos = _block(lines, 0, lines[0][1])
    if pos != len(lines):
        ln = lines[pos][0]
        raise YamlError("неожиданный отступ (структура не сходится)", ln)
    return value


def _block(lines: List[Tuple[int, int, str]], pos: int, indent: int):
    """Разбор блока, начинающегося на строке pos с отступом indent."""
    ln, ind, body = lines[pos]
    if body.startswith("- ") or body == "-":
        return _seq(lines, pos, indent)
    return _map(lines, pos, indent)


def _map(lines, pos: int, indent: int):
    out: dict = {}
    while pos < len(lines):
        ln, ind, body = lines[pos]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError("неожиданный отступ", ln)
        if body.startswith("- ") or body == "-":
            break
        pair = _split_key(body)
        if pair is None:
            raise YamlError(f"ожидалось «ключ: значение», а не {body!r}", ln)
        key, rest = pair
        if key in out:
            raise YamlError(f"ключ {key!r} повторяется", ln)
        pos += 1
        if rest.strip():
            out[key] = _value(rest, ln)
            continue
        if pos < len(lines) and (lines[pos][1] > indent or
                                 (lines[pos][1] == indent and lines[pos][2].startswith("- "))):
            child_indent = lines[pos][1]
            out[key], pos = _block(lines, pos, child_indent)
        else:
            out[key] = None
    return out, pos


def _seq(lines, pos: int, indent: int):
    out: list = []
    while pos < len(lines):
        ln, ind, body = lines[pos]
        if ind < indent or not (body.startswith("- ") or body == "-"):
            if ind > indent:
                raise YamlError("неожиданный отступ в списке", ln)
            break
        if ind > indent:
            raise YamlError("неожиданный отступ в списке", ln)
        rest = body[1:].strip()
        pos += 1
        if not rest:
            if pos < len(lines) and lines[pos][1] > indent:
                val, pos = _block(lines, pos, lines[pos][1])
                out.append(val)
            else:
                out.append(None)
            continue
        pair = None if rest.startswith(("{", "[")) else _split_key(rest)
        if pair is None:
            out.append(_value(rest, ln))
            continue
        # элемент-словарь: первая пара на строке «- », остальные — с отступом indent+2
        item_indent = ind + (len(body) - len(rest))
        sub = [(ln, item_indent, rest)]
        while pos < len(lines) and lines[pos][1] >= item_indent and not (
                lines[pos][1] == indent and lines[pos][2].startswith("- ")):
            if lines[pos][1] <= indent:
                break
            sub.append(lines[pos])
            pos += 1
        val, used = _map(sub, 0, item_indent)
        if used != len(sub):
            raise YamlError("структура элемента списка не сходится", sub[used][0])
        out.append(val)
    return out, pos


# ── эмиттер ──────────────────────────────────────────────────────────────────

_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_RESERVED = re.compile(r"^(true|false|null|~|yes|no|on|off|y|n)$", re.IGNORECASE)
_NUMERIC = re.compile(r"^[-+]?(\d[\d_]*\.?\d*|\.\d+)([eE][-+]?\d+)?$|^0[xob][0-9a-fA-F]+$")
_DATEISH = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _needs_quotes(s: str) -> bool:
    if s == "" or s != s.strip():
        return True
    if _RESERVED.match(s) or _NUMERIC.match(s) or _DATEISH.match(s):
        return True
    if s[0] in "{}[]&*!|>'\"%@`#,?-:" and not (s[0] == "-" and len(s) > 1 and s[1] not in " -"):
        return True
    if ": " in s or " #" in s or s.endswith(":"):
        return True
    return any(ord(c) < 32 or c == "\x7f" for c in s)


def dump_scalar(v: Any) -> str:
    """Скаляр в YAML так, чтобы `loads` вернул то же самое значение."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    return s if not _needs_quotes(s) else _quote(s)


def _quote(s: str) -> str:
    """Строка в двойных кавычках с эскейпами — читается `loads` обратно без потерь."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ch == "\x7f":
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _dump_key(k: Any) -> str:
    s = str(k)
    return s if _PLAIN_KEY.match(s) and not _RESERVED.match(s) else _quote(s)


def _inline(v: Any) -> Optional[str]:
    """Однострочная форма значения или None, если нужен блок."""
    if isinstance(v, dict):
        return "{}" if not v else None
    if isinstance(v, (list, tuple)):
        return "[]" if not v else None
    return dump_scalar(v)


def _emit(v: Any, level: int, lines: List[str], pad: str = "  ") -> None:
    ind = pad * level
    if isinstance(v, dict):
        for k, x in v.items():
            inline = _inline(x)
            if inline is not None:
                lines.append(f"{ind}{_dump_key(k)}: {inline}")
            else:
                lines.append(f"{ind}{_dump_key(k)}:")
                _emit(x, level + 1, lines, pad)
    elif isinstance(v, (list, tuple)):
        for x in v:
            inline = _inline(x)
            if inline is not None:
                lines.append(f"{ind}- {inline}")
            elif isinstance(x, dict):
                sub: List[str] = []
                _emit(x, level + 1, sub, pad)
                lines.append(f"{ind}- {sub[0].lstrip()}")
                lines.extend(sub[1:])
            else:
                lines.append(f"{ind}-")
                _emit(x, level + 1, lines, pad)


def split_header(text: str) -> Tuple[List[str], str]:
    """(строки шапки-комментария, остаток): шапка — лидирующие `#`-строки и пустые строки."""
    lines = text.splitlines()
    n = 0
    while n < len(lines) and (lines[n].startswith("#") or not lines[n].strip()):
        n += 1
    return lines[:n], "\n".join(lines[n:])


def dumps(obj: Any, header: Optional[List[str]] = None) -> str:
    """YAML-подмножество: то, что читает `loads`. header — строки комментариев в начале файла."""
    lines: List[str] = list(header or [])
    inline = _inline(obj)
    if inline is not None:
        lines.append(inline)
    else:
        _emit(obj, 0, lines)
    return "\n".join(lines) + "\n"
