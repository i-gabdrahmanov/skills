"""Детерминированный сканер JPA-домена: @Entity / @Embeddable / @MappedSuperclass.

Recall здесь критичен (раньше LLM находил 14 из 55). Считаем КАЖДУЮ аннотацию-сущность;
поля/связи — обогащение по телу класса (best-effort).
"""
from __future__ import annotations

import re
from pathlib import Path

from common import iter_java, read_text
from endpoints import _balanced, _strip_comments

_KIND = {"Entity": "entity", "Embeddable": "embeddable", "MappedSuperclass": "mapped_superclass"}
_REL_ANNOS = ("OneToMany", "ManyToOne", "OneToOne", "ManyToMany", "ElementCollection")

_CLASS_DECL_RE = re.compile(
    r"\b(?:public\s+|abstract\s+|final\s+)*class\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?"
    r"\s*(?:extends\s+([A-Za-z_][\w.]*)(?:<[^>]*>)?)?"
)
_FIELD_RE = re.compile(
    r"(?:private|protected|public)\s+(?:final\s+|transient\s+|static\s+|volatile\s+)*"
    r"([A-Za-z_][\w.]*(?:\s*<[^;={]+>)?)\s+([a-zA-Z_]\w*)\s*[;=]"
)
_TABLE_NAME_RE = re.compile(r'@Table\s*\([^)]*name\s*=\s*"([^"]+)"')


def _relations(body: str) -> list[dict]:
    rels: list[dict] = []
    for m in re.finditer(r"@(" + "|".join(_REL_ANNOS) + r")\b", body):
        seg = body[m.end():m.end() + 500]
        fm = _FIELD_RE.search(seg)
        target, field_name = "", ""
        if fm:
            raw_type = fm.group(1)
            field_name = fm.group(2)
            gm = re.search(r"<\s*([A-Za-z_][\w.]*)", raw_type)
            target = (gm.group(1) if gm else raw_type).split(".")[-1]
        rels.append({"kind": m.group(1), "target": target, "field": field_name})
    return rels


def parse_file(path: Path) -> list[dict]:
    raw = read_text(path)
    if not any(f"@{a}" in raw for a in _KIND):
        return []
    text = _strip_comments(raw)
    out: list[dict] = []
    for m in re.finditer(r"@(Entity|Embeddable|MappedSuperclass)\b", text):
        cm = _CLASS_DECL_RE.search(text, m.start())
        if not cm:
            continue
        window = text[max(0, m.start() - 200): cm.start()]
        tm = _TABLE_NAME_RE.search(window)
        fields: list[dict] = []
        relations: list[dict] = []
        brace = text.find("{", cm.end())
        if brace != -1:
            close = _balanced(text, brace, "{", "}")
            body = text[brace + 1: close - 1] if close > 0 else text[brace + 1: brace + 6000]
            for fm in _FIELD_RE.finditer(body):
                fields.append({"name": fm.group(2), "type": re.sub(r"\s+", "", fm.group(1))})
            relations = _relations(body)
        out.append({
            "name": cm.group(1),
            "kind": _KIND[m.group(1)],
            "table": tm.group(1) if tm else None,
            "extends": cm.group(2).split(".")[-1] if cm.group(2) else None,
            "fields": fields,
            "relations": relations,
            "file": str(path),
        })
    return out


def scan(root: Path) -> list[dict]:
    items: list[dict] = []
    for p in iter_java(Path(root)):
        items.extend(parse_file(p))
    items.sort(key=lambda d: d["name"])
    return items
