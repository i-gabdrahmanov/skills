"""profile.py — профайлер входа без содержимого (задача 4.2, D17, D22).

Реальную спеку разработка не увидит, но может увидеть её ФОРМУ: глубину и вид заголовков, нумерацию,
какие слова-маркеры встречаются, как записан API, сколько фенсов и каких языков. По форме видно,
почему правило промахнулось, и случай воспроизводится синтетикой (стресс-корпус 0.5). Тот же
профайлер описывает незнакомые XML и CSV — на нём строятся адаптеры ARIS и клиентских путей (6.2, 6.3).

Правило: в профиле нет текста. Слова показываются как есть, только если они в общем словаре
(`references/generic-words.txt`, доменно-нейтральная лексика); остальные — солёным хешем `w#xxxxxx`.
Соль — на проект (`.spec-graph/salt`, вне git): одно слово в разных прогонах даёт один хеш, динамику
видно между диагностиками, а словарь чужого проекта по хешам не восстановить. Пути файлов — тоже
хешами. Раскрыть хеш (`reveal`) можно только локально, в окружении; в диагностику раскрытое не идёт.
XML: пути элементов с частотами, имена атрибутов, ТИП значений — без значений. CSV: колонки и типы.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import blocks as B

SALT_REL = ".spec-graph/salt"
GENERIC = Path(__file__).resolve().parents[2] / "references" / "generic-words.txt"
_WORD = re.compile(r"[^\W\d_]{2,}")
_HTTP = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+/[^\s`)]*")
_TOPIC = re.compile(r"\b[a-z0-9]+(?:\.[a-z0-9_-]+){2,}\.v\d+\b")
_CRON = re.compile(r"`?(?:[0-9*/?,-]+\s+){4,6}[0-9*/?,-]+`?")
_LANGS = {"json", "json5", "sql", "yaml", "yml", "xml", "java", "kotlin", "plantuml", "puml", "mermaid", "text",
          "bash", "sh", "http", "graphql", "roomsql", "postgresql", ""}


def generic_words() -> set:
    return {w.strip().lower() for w in GENERIC.read_text(encoding="utf-8").splitlines()
            if w.strip() and not w.startswith("#")}


def salt(root: Path) -> str:
    p = Path(root) / SALT_REL
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    s = os.urandom(8).hex()
    p.write_text(s + "\n", encoding="utf-8")
    return s


class Masker:
    """Слово → как есть (общий словарь) или w#хеш; путь → f#хеш."""

    def __init__(self, root: Path):
        self.salt = salt(root)
        self.generic = generic_words()

    def h(self, kind: str, value: str) -> str:
        return f"{kind}#" + hashlib.sha1(f"{self.salt}:{value}".encode("utf-8")).hexdigest()[:6]

    def word(self, w: str) -> str:
        w = w.lower()
        return w if w in self.generic else self.h("w", w)

    def words(self, text: str) -> List[str]:
        return [self.word(w) for w in _WORD.findall(text)]


def _heading_shape(title: str) -> str:
    t = title.strip()
    if re.match(r"^\[[^\]]+\]", t):
        return "code"
    if re.match(r"^(раздел|глава|section|chapter)\s+\d", t, re.I):
        return "razdel"
    m = re.match(r"^(\d+(?:\.\d+)*)(\.?)\s", t)
    if m:
        return ".".join("N" for _ in m.group(1).split(".")) + m.group(2)
    if re.match(r"^(GET|POST|PUT|PATCH|DELETE)\s+/", t):
        return "http"
    return "none"


def _top(counter: Counter, n: int = 15) -> List[List[Any]]:
    return [[k, v] for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def profile_md(root: Path, rel: str, masker: Masker) -> Dict[str, Any]:
    raw = (Path(root) / rel).read_bytes()
    text = raw.decode("utf-8")
    blocks, warnings = B.parse(text, rel)
    types = Counter(b["type"] for b in blocks)
    levels = Counter(str(b["level"]) for b in blocks if b["type"] == "heading")
    shapes = Counter(_heading_shape(b["title"]) for b in blocks if b["type"] == "heading")
    shapes["bold_line"] = sum(1 for b in blocks if b["type"] == "paragraph"
                              and re.fullmatch(r"\*\*[^*\n]+\*\*\s*", b["text"].strip() + "\n"))
    shapes["setext"] = sum(1 for b in blocks if b.get("setext"))
    hwords = Counter(w for b in blocks if b["type"] == "heading" for w in masker.words(b["title"]))
    cols, hdr = Counter(), Counter()
    for b in blocks:
        if b["type"] == "table":
            first = b["text"].splitlines()[0]
            cells = [c for c in first.strip().strip("|").split("|")]
            cols[str(len(cells))] += 1
            for c in cells:
                hdr.update(masker.words(c))
    fences = Counter((b.get("lang") or "").lower() if (b.get("lang") or "").lower() in _LANGS else masker.h("lang", b.get("lang") or "")
                     for b in blocks if b["type"] == "fence")
    prose = "\n".join(b["text"] for b in blocks if b["type"] != "fence")
    return {
        "f": masker.h("f", rel), "depth": rel.count("/"), "bytes": len(raw), "lines": text.count("\n"),
        "crlf": "\r\n" in text, "bom": text.startswith("﻿"),
        "blocks": dict(sorted(types.items())), "heading_levels": dict(sorted(levels.items())),
        "heading_shapes": {k: v for k, v in sorted(shapes.items()) if v},
        "heading_words": _top(hwords, 30),
        "tables": {"cols": dict(sorted(cols.items())), "header_words": _top(hdr, 10)},
        "fences": dict(sorted(fences.items())),
        "links": {"anchor": len(re.findall(r"\]\(#", prose)), "web": len(re.findall(r"\]\(https?:", prose)),
                  "relative": len(re.findall(r"\]\((?!#|https?:)", prose)), "wiki": prose.count("[[")},
        "hits": {"http": len(_HTTP.findall(prose)), "topic_like": len(_TOPIC.findall(text)), "cron": len(_CRON.findall(prose))},
        "warnings": dict(Counter(w["code"] for w in warnings)),
    }


def _value_type(v: str) -> str:
    v = v.strip()
    if not v:
        return "empty"
    if re.fullmatch(r"(true|false)", v, re.I):
        return "bool"
    if re.fullmatch(r"[-+]?\d+([.,]\d+)?", v):
        return "number"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ][\d:.]+Z?)?|\d{2}\.\d{2}\.\d{4}", v):
        return "date"
    if re.fullmatch(r"[0-9a-fA-F-]{8,}|[A-Za-z]{1,6}[-_]?\d+|#?\d+", v):
        return "id"
    return "text" + ("<20" if len(v) < 20 else "<200" if len(v) < 200 else "200+")


def _tag(name: str, masker: Masker) -> str:
    local = name.split("}", 1)[-1]
    return local if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", local) else masker.h("t", local)


def profile_xml(path: Path, masker: Masker, limit: int = 60) -> Dict[str, Any]:
    paths, attrs = Counter(), Counter()
    vtypes: Dict[str, Counter] = {}
    stack: List[str] = []
    for event, el in ET.iterparse(str(path), events=("start", "end")):
        if event == "start":
            stack.append(_tag(el.tag, masker))
            key = "/".join(stack[-3:])
            paths[key] += 1
            for a, v in el.attrib.items():
                an = f"{stack[-1]}@{_tag(a, masker)}"
                attrs[an] += 1
                vtypes.setdefault(an, Counter())[_value_type(v)] += 1
            if el.text and el.text.strip():
                vtypes.setdefault(f"{stack[-1]}#text", Counter())[_value_type(el.text)] += 1
        else:
            stack.pop()
            el.clear()
    return {"kind": "xml", "elements": _top(paths, limit), "attributes": _top(attrs, limit),
            "value_types": {k: dict(v) for k, v in sorted(vtypes.items())[:limit]}}


def profile_csv(path: Path, masker: Masker, sample: int = 500) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    header = rows[0] if rows else []
    cols = []
    for i, h in enumerate(header):
        types = Counter(_value_type(r[i]) for r in rows[1:sample + 1] if i < len(r))
        name = h.strip().lower()
        cols.append({"col": i + 1, "name": " ".join(masker.word(w) for w in _WORD.findall(name)) or masker.h("c", name),
                     "types": dict(types)})
    return {"kind": "csv", "rows": max(0, len(rows) - 1), "delimiter": repr(getattr(dialect, "delimiter", ",")),
            "columns": cols}


def profile(root: Path, cfg: Dict[str, Any], scope: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
    masker = Masker(root)
    files = B.source_files(root, cfg)
    if scope and scope != "all":
        files = [f for f in files if f == scope or f.startswith(scope.rstrip("/") + "/")]
    out = [profile_md(root, f, masker) for f in files]
    tot_shapes, tot_words, tot_types = Counter(), Counter(), Counter()
    for p in out:
        tot_shapes.update(p["heading_shapes"])
        tot_types.update(p["blocks"])
        tot_words.update({w: c for w, c in p["heading_words"]})
    return {"files": len(out), "totals": {"blocks": dict(sorted(tot_types.items())),
                                          "heading_shapes": dict(sorted(tot_shapes.items())),
                                          "heading_words": _top(tot_words, 25)},
            "per_file": out[:limit], "more_files": max(0, len(out) - limit)}


def reveal(root: Path, cfg: Dict[str, Any], token: str) -> Dict[str, Any]:
    """Только локально: какие слова исходников стоят за хешем `w#…` (или какой файл за `f#…`)."""
    masker = Masker(root)
    found = set()
    for rel in B.source_files(root, cfg):
        if token.startswith("f#") and masker.h("f", rel) == token:
            found.add(rel)
        if token.startswith("w#"):
            text = (Path(root) / rel).read_text(encoding="utf-8")
            for w in _WORD.findall(text):
                if masker.h("w", w.lower()) == token:
                    found.add(w.lower())
    return {"token": token, "matches": sorted(found),
            "note": "раскрытие — только для этого окружения; в пакет диагностики не попадает"}


_HASH = re.compile(r"\b[a-z]{1,4}#[0-9a-f]{6}\b")
# Перечислимые значения схемы профиля (виды заголовков, типы значений): разрешены, это не текст спеки.
ENUM_VALUES = {"none", "code", "razdel", "http", "empty", "bool", "number", "date", "id", "text", "xml", "csv"}


def _strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def leaked_words(obj: Any, sources: Iterable[str], generic: Optional[set] = None) -> List[str]:
    """Слова исходников (вне общего словаря), встретившиеся в СТРОКОВЫХ значениях профиля/диагностики.

    Ключи схемы и перечислимые значения — не текст спеки; хеши `w#…`/`f#…` вычищаются до сверки."""
    generic = generic if generic is not None else generic_words()
    vocab = {w.lower() for s in sources for w in _WORD.findall(s) if len(w) >= 3} - generic - ENUM_VALUES
    seen = set()
    for s in _strings(obj):
        for w in _WORD.findall(_HASH.sub(" ", s)):
            seen.add(w.lower())
    return sorted(vocab & seen)
