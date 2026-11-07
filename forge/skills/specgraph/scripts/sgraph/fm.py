"""fm.py — frontmatter и маркеры тела заметок vault без потерь (задача 0.3).

Frontmatter — это рёбра графа. Прототип терял их на разборе: некавычный список `[[[a]], [b]]` YAML
читает как вложенные списки, парсер склейки брал из него не всё и перезаписывал файл усечённым набором
(96 файлов откатаны из бэкапа). PyYAML нет (только stdlib), поэтому здесь строгий кодек на ПОДМНОЖЕСТВО
YAML, которое пишем мы и пишет Properties Obsidian:

    скаляры: строка, число, true/false, null (пустое значение); строки в "…" и '…'
    списки: flow `["[[a]]", "[[b]]"]`, `[]` и block (`  - "[[a]]"`, как пишет Obsidian)

Всё прочее — вложенные словари, якоря, многострочные скаляры, теги — ошибка с номером строки, а не тихое
чтение. Дубль ключа — ошибка. Некавычный `[[x]]` где угодно — ошибка с подсказкой «возьми в кавычки»:
по YAML это вложенный список, а не ссылка.

Запись: порядок ключей задаёт вызывающий (онтология, `fm_keys`), чужие ключи человека идут следом в
исходном порядке и не теряются. Стиль — как у Properties Obsidian: списки блоком с отступом 2, пустой
список `[]`, пустое значение — `key:`, строка в двойных кавычках только когда без них YAML прочтёт иное
(ссылки `[[…]]` — всегда в кавычках). Сверено по описанию поведения Obsidian, а не по файлу из живого
Obsidian — «не проверено данными» (README задачи).

Маркеры тела: `<!-- graph:<имя>:start {json-атрибуты} -->` … `<!-- graph:<имя>:end -->`. Пустая строка до
и после каждого маркера обязательна: без неё Obsidian держит HTML-блок открытым и рендерит таблицу сырым
текстом (инвариант 12 прототипа). Запись маркеров её ставит сама, проверка (`marker_errors`) — ругается.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*|[^\s:#\-\[\]{}][^:#]*?):(?:\s+(.*))?$")
_ITEM_RE = re.compile(r"^(\s+)-(?:\s+(.*))?$")
_INT_RE = re.compile(r"^[-+]?(0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(r"^[-+]?([0-9]+\.[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$")
_SPECIAL_START = set("{&*!|>%@`")
_MARK_RE = re.compile(r"^<!-- graph:([a-z][a-z0-9-]*):(start|end)(?: (\{.*\}))? -->$")


class FMError(Exception):
    """Ошибка разбора frontmatter: `line` — номер строки ФАЙЛА (с 1), сообщение — что исправить."""

    def __init__(self, line: int, message: str):
        super().__init__(f"строка {line}: {message}")
        self.line = line
        self.message = message


# ─── разбор ────────────────────────────────────────────────────────────────

def split(text: str) -> Tuple[Optional[str], str, int]:
    """(frontmatter без разделителей | None, тело, номер строки файла, с которой начинается FM-текст)."""
    t = text[1:] if text.startswith("﻿") else text
    lines = t.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        return None, text, 0
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") in ("---", "..."):
            fm = "\n".join(l.rstrip("\r") for l in lines[1:i])
            body = "\n".join(lines[i + 1:])
            return fm, body, 2
    raise FMError(1, "frontmatter открыт «---», но не закрыт")


def _unquote_double(s: str, line: int) -> str:
    try:
        return json.loads(s)
    except ValueError:
        raise FMError(line, f"строка в двойных кавычках не разобралась: {s[:40]}")


def _scalar(raw: str, line: int) -> Any:
    s = raw.strip()
    if s == "" or s in ("null", "~", "Null", "NULL"):
        return None
    if s.startswith('"'):
        if not s.endswith('"') or len(s) < 2:
            raise FMError(line, "незакрытая двойная кавычка")
        return _unquote_double(s, line)
    if s.startswith("'"):
        if not s.endswith("'") or len(s) < 2:
            raise FMError(line, "незакрытая одинарная кавычка")
        inner = s[1:-1]
        if re.search(r"(?<!')'(?!')", inner):
            raise FMError(line, "одинарная кавычка внутри '…' — удвой её ('')")
        return inner.replace("''", "'")
    if s.startswith("[[") or (s.startswith("[") and "[[" in s and not s.startswith('["')):
        raise FMError(line, f"ссылка без кавычек ({s[:40]}): YAML прочтёт её как вложенный список — "
                            f'пиши "[[имя]]" в двойных кавычках')
    if s[0] in _SPECIAL_START:
        raise FMError(line, f"неподдерживаемая конструкция YAML «{s[0]}» — значение возьми в двойные кавычки")
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    if ": " in s or s.endswith(":"):
        raise FMError(line, "двоеточие с пробелом в значении без кавычек — возьми значение в кавычки")
    return s


def _strip_comment(raw: str) -> str:
    """YAML: ` #` вне кавычек начинает комментарий."""
    quote = None
    for i, ch in enumerate(raw):
        if quote:
            if ch == "\\" and quote == '"':
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'" and (i == 0 or raw[i - 1] in " [,"):
            quote = ch
        elif ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            return raw[:i].rstrip()
    return raw


def _flow_list(s: str, line: int) -> List[Any]:
    inner = s.strip()[1:-1]
    items: List[Any] = []
    buf, quote, depth, i = "", None, 0, 0
    while i < len(inner):
        ch = inner[i]
        if quote:
            buf += ch
            if ch == "\\" and quote == '"' and i + 1 < len(inner):
                buf += inner[i + 1]
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch == "[":
            depth += 1
            buf += ch
        elif ch == "]":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            items.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    if quote:
        raise FMError(line, "незакрытая кавычка в списке")
    if buf.strip() or items:
        items.append(buf)
    out = []
    for it in items:
        it = it.strip()
        if it == "":
            raise FMError(line, "пустой элемент списка (лишняя запятая)")
        if it.startswith("[") and not it.startswith("[["):
            raise FMError(line, "вложенный список не поддерживается")
        out.append(_scalar(it, line))
    return out


def parse(fm_text: str, first_line: int = 2) -> "OrderedDict[str, Any]":
    """Разбор текста frontmatter (без «---»). `first_line` — номер строки файла первой строки FM."""
    out: "OrderedDict[str, Any]" = OrderedDict()
    lines = fm_text.split("\n") if fm_text else []
    i = 0
    while i < len(lines):
        no = first_line + i
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw[0] in " \t":
            raise FMError(no, "строка с отступом вне списка: вложенные структуры не поддерживаются")
        if raw.startswith("- "):
            raise FMError(no, "элемент списка без ключа")
        m = _KEY_RE.match(raw.rstrip())
        if not m:
            raise FMError(no, f"ожидается «ключ: значение»: {raw[:40]}")
        key = m.group(1).strip()
        if key in out:
            raise FMError(no, f"ключ «{key}» повторяется")
        value = _strip_comment(m.group(2) or "")
        if value.strip().startswith("[[") and not value.strip().startswith("[[]"):
            raise FMError(no, f"ссылка без кавычек ({value.strip()[:40]}): YAML прочтёт её как вложенный "
                              f'список — пиши "[[имя]]" в двойных кавычках')
        if value.strip().startswith("["):
            v = value.strip()
            if not v.endswith("]"):
                raise FMError(no, "flow-список не закрыт «]» в той же строке")
            out[key] = _flow_list(v, no)
            i += 1
            continue
        if value.strip() == "":
            items = []
            j = i + 1
            while j < len(lines):
                mi = _ITEM_RE.match(lines[j].rstrip())
                if not mi:
                    if lines[j].strip() == "":
                        j += 1
                        continue
                    if lines[j][:1] in " \t":
                        raise FMError(first_line + j, "вложенная структура под ключом не поддерживается")
                    break
                item = _strip_comment(mi.group(2) or "")
                if item.strip().startswith("[") and not item.strip().startswith('["'):
                    raise FMError(first_line + j, f"ссылка без кавычек ({item.strip()[:40]}): по YAML это "
                                                  f'вложенный список — пиши "[[имя]]" в двойных кавычках')
                items.append(_scalar(item, first_line + j))
                j += 1
            if j > i + 1 and items:
                out[key] = items
                i = j
                continue
            out[key] = None
            i += 1
            continue
        out[key] = _scalar(value, no)
        i += 1
    return out


def read(text: str) -> Tuple["OrderedDict[str, Any]", str]:
    """(frontmatter, тело). Нет frontmatter — пустой словарь и весь текст телом."""
    fm, body, first = split(text)
    if fm is None:
        return OrderedDict(), body
    return parse(fm, first), body


# ─── запись ────────────────────────────────────────────────────────────────

_PLAIN_OK = re.compile(r"^[^\s\"'#\[\]{},&*!|>%@`:-][^#\n]*$")


def _needs_quotes(s: str) -> bool:
    if s == "" or s != s.strip() or "[[" in s or "\n" in s or "\t" in s:
        return True
    if not _PLAIN_OK.match(s) or ": " in s or s.endswith(":") or " #" in s:
        return True
    if s in ("null", "~", "Null", "NULL", "true", "True", "TRUE", "false", "False", "FALSE"):
        return True
    return bool(_INT_RE.match(s) or _FLOAT_RE.match(s))


def _dump_scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if not isinstance(v, str):
        raise TypeError(f"frontmatter: неподдерживаемый тип {type(v).__name__}")
    return json.dumps(v, ensure_ascii=False) if _needs_quotes(v) else v


def dump(data: Dict[str, Any], order: Optional[List[str]] = None) -> str:
    """Текст frontmatter c «---». Ключи `order` — первыми (если есть в data), остальные — в своём порядке."""
    keys = [k for k in (order or []) if k in data] + [k for k in data if k not in (order or [])]
    lines = ["---"]
    for k in keys:
        v = data[k]
        if isinstance(v, (list, tuple)):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for it in v:
                    if isinstance(it, (list, tuple, dict)):
                        raise TypeError(f"frontmatter «{k}»: вложенные структуры не поддерживаются")
                    lines.append(f"  - {_dump_scalar(it)}".rstrip())
        elif isinstance(v, dict):
            raise TypeError(f"frontmatter «{k}»: вложенные словари не поддерживаются")
        else:
            s = _dump_scalar(v)
            lines.append(f"{k}: {s}" if s != "" else f"{k}:")
    lines.append("---")
    return "\n".join(lines) + "\n"


def write(data: Dict[str, Any], body: str, order: Optional[List[str]] = None) -> str:
    return dump(data, order) + body


def link(name: str) -> str:
    return f"[[{name}]]"


def link_target(value: Any) -> Optional[str]:
    """Имя заметки из «[[имя]]», «[[имя|алиас]]», «[[имя#заголовок]]»; не ссылка — None."""
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]", value.strip())
    return m.group(1).strip() if m else None


# ─── маркеры тела ──────────────────────────────────────────────────────────

def _marker(name: str, end: bool, attrs: Optional[Dict[str, Any]] = None) -> str:
    if end:
        return f"<!-- graph:{name}:end -->"
    if attrs:
        js = json.dumps(attrs, ensure_ascii=False, sort_keys=True).replace(">", "\\u003e").replace("--", "-\\u002d")
        return f"<!-- graph:{name}:start {js} -->"
    return f"<!-- graph:{name}:start -->"


def blocks(body: str) -> List[Dict[str, Any]]:
    """Маркерные блоки тела: [{name, attrs, start, end (индексы строк маркеров), content}]."""
    lines = body.split("\n")
    out, open_ = [], None
    for i, l in enumerate(lines):
        m = _MARK_RE.match(l.strip())
        if not m:
            continue
        name, kind, js = m.groups()
        if kind == "start":
            if open_ is not None:
                raise ValueError(f"маркер graph:{name} открыт внутри graph:{open_['name']} (строка тела {i + 1})")
            attrs = json.loads(js) if js else {}
            open_ = {"name": name, "attrs": attrs, "start": i}
        else:
            if open_ is None or open_["name"] != name:
                raise ValueError(f"закрывающий graph:{name} без открывающего (строка тела {i + 1})")
            open_["end"] = i
            open_["content"] = "\n".join(lines[open_["start"] + 1:i]).strip("\n")
            out.append(open_)
            open_ = None
    if open_ is not None:
        raise ValueError(f"маркер graph:{open_['name']} не закрыт")
    return out


def marker_errors(body: str) -> List[str]:
    """Непарные/вложенные маркеры и маркеры без пустой строки до и после."""
    try:
        found = blocks(body)
    except ValueError as e:
        return [str(e)]
    lines = body.split("\n")
    errs = []
    for b in found:
        for idx, where in ((b["start"], "start"), (b["end"], "end")):
            before = lines[idx - 1] if idx > 0 else ""
            after = lines[idx + 1] if idx + 1 < len(lines) else ""
            if before.strip() or after.strip():
                errs.append(f"graph:{b['name']}:{where}: нужна пустая строка до и после маркера (строка тела {idx + 1})")
    return errs


def set_block(body: str, name: str, content: str, attrs: Optional[Dict[str, Any]] = None) -> str:
    """Заменить содержимое блока (или добавить блок в конец тела); воздух вокруг маркеров — сам."""
    block = "\n".join(["", _marker(name, False, attrs), "", content.strip("\n"), "", _marker(name, True), ""])
    lines = body.split("\n")
    for b in blocks(body):
        if b["name"] == name:
            head = "\n".join(lines[:b["start"]]).rstrip("\n")
            tail = "\n".join(lines[b["end"] + 1:]).lstrip("\n")
            out = head + "\n" + block + ("\n" + tail if tail else "")
            return out.lstrip("\n") if not head else out
    base = body.rstrip("\n")
    return (base + "\n" + block) if base else block.lstrip("\n")


def get_block(body: str, name: str) -> Optional[Dict[str, Any]]:
    for b in blocks(body):
        if b["name"] == name:
            return b
    return None
