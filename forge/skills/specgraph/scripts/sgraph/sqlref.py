"""sqlref.py — таблицы из SQL в спеке: детерминированно, без модели и без внешних зависимостей.

Спека пишет запросы фрагментами (```sql …``` или `UPDATE task SET …` в тексте), а таблиц как разделов в
ней обычно нет — модель ставила `uses_data` к «task», цели не было, и 606 связей боевого прогона ушли
вопросами человеку. Здесь — то, что делают парсеры линеджа (sqlglot/sqllineage в DataHub, OpenMetadata):
таблицы запроса и режим доступа, имена CTE (`WITH x AS (…)`) вычитаются — иначе они тоже «таблицы».
Полный разбор диалектов не нужен: нужен список таблиц и чтение/запись.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

SQL_LANGS = {"sql", "postgresql", "postgres", "psql", "plsql", "pgsql", "mysql", "tsql", "oracle", "sqlite", "hql"}
_START = re.compile(r"^\s*(?:select|update|insert|delete|with|merge|create\s+table|alter\s+table|truncate)\b", re.I)
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:[^']|'')*'")
_IDENT = r"(?:[A-Za-z_][A-Za-z0-9_$]*|\"[^\"]+\")"
_NAME = rf"({_IDENT}(?:\.{_IDENT})?)"
# порядок альтернатив важен: «delete from x» — запись, а не «from x»
_REF = re.compile(r"\b(delete\s+from|merge\s+into|insert\s+into|update|truncate(?:\s+table)?|alter\s+table|"
                  r"create\s+table(?:\s+if\s+not\s+exists)?|from|join|into)\s+(?:only\s+)?" + _NAME + r"(\s*\()?", re.I)
_CTE = re.compile(rf"(?:\bwith(?:\s+recursive)?|,)\s*({_IDENT})\s*(?:\([^)]*\)\s*)?as\s*(?:not\s+)?(?:materialized\s+)?\(",
                  re.I)
_NOT_TABLE = {"select", "lateral", "unnest", "dual", "values", "table", "only", "set", "where", "generate_series"}
_CLAUSE_END = re.compile(r"\b(?:where|join|inner|left|right|full|cross|group|order|limit|union|having|on|"
                         r"returning|window|offset|fetch|for)\b|[;()]", re.I)
_INLINE = re.compile(r"`([^`\n]{12,})`")
_FENCE = re.compile(r"^(```+|~~~+)[ \t]*([\w+-]*)[^\n]*\n(.*?)^\1[ \t]*\r?$", re.S | re.M)


def _clean(sql: str) -> str:
    return _STRING.sub("''", _COMMENT.sub(" ", sql))


def is_sql(text: str, lang: str = "") -> bool:
    """Фрагмент — SQL: язык ограды из списка, либо первая значимая строка начинается с оператора SQL."""
    if (lang or "").lower() in SQL_LANGS:
        return True
    first = next((l for l in _clean(text).splitlines() if l.strip()), "")
    return bool(_START.match(first)) and bool(re.search(r"\b(?:from|into|set|table)\b", text, re.I))


def statements(sql: str) -> List[str]:
    """Операторы фрагмента — куски ИСХОДНОГО текста (для цитат), разделитель «;» вне строк."""
    out, cur, quoted = [], [], False
    for ch in sql:
        if ch == "'":
            quoted = not quoted
        if ch == ";" and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [s for s in out if s.strip()]


_SUBQUERY = re.compile(r"\s*(?:select|with|values)\b", re.I)
_INSERT_FIRST = {"insert", "merge", "replace"}


def _in_call(text: str, pos: int) -> bool:
    """FROM/JOIN внутри скобок, которые не подзапрос: аргументы функции — `extract(epoch FROM col)`,
    `substring(x FROM 2)`, `trim(both FROM name)`; таблицы там нет."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            if depth == 0:
                return not _SUBQUERY.match(text, i + 1)
            depth -= 1
    return False


def tables(stmt: str) -> List[Tuple[str, str]]:
    """[(таблица, «read»|«write»)] оператора в порядке появления, без CTE и функций.

    Не таблицы (ревью 2026-11-08, правило auto+stub ставило их узлами): `SELECT … INTO v_x` (переменная
    PL/SQL — голый INTO пишет в таблицу только у INSERT/MERGE), FROM в аргументах функции, `IS [NOT]
    DISTINCT FROM b`."""
    text = _clean(stmt)
    ctes = {m.group(1).strip('"').lower() for m in _CTE.finditer(text)}
    lead = re.match(r"\s*(\w+)", text)
    first = lead.group(1).lower() if lead else ""
    out: List[Tuple[str, str]] = []
    for m in _REF.finditer(text):
        verb, name, call = m.group(1).lower(), m.group(2), m.group(3)
        if verb == "into" and first not in _INSERT_FIRST:
            continue
        if verb in ("from", "join") and (_in_call(text, m.start()) or
                                         re.search(r"\bdistinct\s*$", text[:m.start()], re.I)):
            continue
        if call:                                  # from generate_series(…), insert into t (cols) — отдельно
            if not verb.startswith(("insert", "into")):
                continue
        mode = "read" if verb in ("from", "join") else "write"
        names = [name.strip('"')]
        if verb == "from":                        # FROM a x, b y — список через запятую
            tail = _CLAUSE_END.split(text[m.end(2):], 1)[0]
            for item in tail.split(",")[1:]:
                im = re.match(r"\s*" + _NAME, item)
                if im:
                    names.append(im.group(1).strip('"'))
        for t in names:
            if t.lower() in ctes or t.lower() in _NOT_TABLE:
                continue
            if (t, mode) not in out:
                out.append((t, mode))
    return out


def fragments(markdown: str) -> List[str]:
    """SQL-фрагменты markdown-текста: ограды SQL и инлайн-код, похожий на запрос."""
    out = []
    for m in _FENCE.finditer(markdown):
        if is_sql(m.group(3), m.group(2)):
            out.append(m.group(3))
    rest = _FENCE.sub("", markdown)
    out += [m.group(1) for m in _INLINE.finditer(rest) if is_sql(m.group(1))]
    return out


def refs(markdown: str) -> List[Dict[str, str]]:
    """[{table, mode, statement}] по всем фрагментам: statement — исходный текст оператора (цитата)."""
    out = []
    for frag in fragments(markdown):
        for st in statements(frag):
            for t, mode in tables(st):
                out.append({"table": t, "mode": mode, "statement": st})
    return out
