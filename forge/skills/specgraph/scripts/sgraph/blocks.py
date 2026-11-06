"""blocks.py — Markdown исходника → неделимые блоки (задача 1.1).

Блок — единица, которой оперирует раскладка (1.5): модель назначает блоки узлам, а не режет текст.
Поэтому разрезать фенс, таблицу или пункт алгоритма нельзя по построению — такой операции нет
(прототип восстанавливал 347 разрезанных строгих код-блоков, PROBLEMS.md §A).

Виды блоков: frontmatter, heading (ATX и setext), paragraph, list_item (пункт ВЕРХНЕГО уровня вместе с
вложенными пунктами и строками продолжения), table (целиком), fence (целиком), html (до пустой строки),
hr. Пустые строки приклеиваются к предыдущему блоку (в начале файла — к первому), поэтому
СКЛЕЙКА ТЕКСТОВ ВСЕХ БЛОКОВ = ФАЙЛ ПОБАЙТНО, включая BOM и CRLF. Из этого факта исходит проверка
полноты нарезки (1.6).

Незакрытый фенс по CommonMark тянется до конца файла и проглотил бы всё дальше (реальный случай —
task-service.md#1748 прототипа). Правило: фенс закрывается перед ближайшим заголовком того же или
более высокого уровня, чем заголовок его раздела (нет раздела — перед любым заголовком), плюс
предупреждение `W-fence-unclosed` с номером строки. Текст при этом не меняется — меняется только
граница блока.

У блока: id `<файл>#<порядковый с 1>`, sha1 текста, вид, строки начала и конца (с 1), путь
заголовков (хлебные крошки) и id заголовка своего раздела.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BLOCKS_REL = ".spec-graph/blocks"
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
_TABLE = re.compile(r"^ {0,3}\|")
_HTML = re.compile(r"^ {0,3}<(?:[A-Za-z/!?])")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_HR = re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")


def _strip_eol(line: str) -> str:
    return line.rstrip("\r\n")


def _blank(line: str) -> bool:
    return not _strip_eol(line).strip()


def _indented(line: str) -> bool:
    s = _strip_eol(line)
    return bool(s) and s[0] in " \t" and bool(s.strip())


def _starts_block(line: str) -> bool:
    s = _strip_eol(line)
    return bool(_ATX.match(s) or _FENCE.match(s) or _LIST.match(s) or _TABLE.match(s) or _HTML.match(s)
                or _HR.match(s))


def parse(text: str, file: str = "") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(блоки, предупреждения). Склейка `block["text"]` по порядку == text."""
    lines = text.splitlines(keepends=True)
    raw: List[Tuple[str, int, int, Dict[str, Any]]] = []      # (вид, первая строка, последняя+1, доп.)
    warnings: List[Dict[str, Any]] = []
    section_level = 0
    i, n = 0, len(lines)
    first = lines[0].lstrip("﻿") if lines else ""
    if lines and _strip_eol(first) == "---":
        j = 1
        while j < n and _strip_eol(lines[j]) not in ("---", "..."):
            j += 1
        if j < n:
            raw.append(("frontmatter", 0, j + 1, {}))
            i = j + 1
    while i < n:
        line = lines[i]
        s = _strip_eol(line).lstrip("﻿") if i == 0 else _strip_eol(line)
        if not s.strip():
            i += 1
            continue
        m = _ATX.match(s)
        if m:
            level = len(m.group(1))
            raw.append(("heading", i, i + 1, {"level": level, "title": (m.group(2) or "").strip()}))
            section_level = level
            i += 1
            continue
        fm = _FENCE.match(s)
        if fm:
            mark = fm.group(1)
            j = i + 1
            closed = False
            while j < n:
                t = _strip_eol(lines[j])
                if re.match(r"^ {0,3}" + re.escape(mark[0]) + "{%d,}[ \t]*$" % len(mark), t):
                    closed = True
                    j += 1
                    break
                h = _ATX.match(t)
                if h and (section_level == 0 or len(h.group(1)) <= section_level):
                    break
                j += 1
            if not closed:
                warnings.append({"code": "W-fence-unclosed", "line": i + 1, "file": file})
            raw.append(("fence", i, j, {"lang": s.strip()[len(mark):].strip().split(" ")[0]}))
            i = j
            continue
        if _HR.match(s) and not _LIST.match(s):
            raw.append(("hr", i, i + 1, {}))
            i += 1
            continue
        if _LIST.match(s):
            j = i + 1
            while j < n:
                t = lines[j]
                if _blank(t):
                    k = j
                    while k < n and _blank(lines[k]):
                        k += 1
                    if k < n and _indented(lines[k]) and not _LIST.match(_strip_eol(lines[k])):
                        j = k
                        continue
                    break
                if _indented(t):
                    j += 1
                    continue
                if _starts_block(t):
                    break
                j += 1                                    # ленивое продолжение пункта
            raw.append(("list_item", i, j, {}))
            i = j
            continue
        if _TABLE.match(s):
            j = i + 1
            while j < n and _TABLE.match(_strip_eol(lines[j])):
                j += 1
            raw.append(("table", i, j, {}))
            i = j
            continue
        if _HTML.match(s):
            j = i + 1
            while j < n and not _blank(lines[j]):
                j += 1
            raw.append(("html", i, j, {}))
            i = j
            continue
        j = i + 1
        while j < n and not _blank(lines[j]) and not _starts_block(lines[j]) and not _SETEXT.match(_strip_eol(lines[j])):
            j += 1
        if j < n and _SETEXT.match(_strip_eol(lines[j])) and not _blank(lines[j]):
            under = _strip_eol(lines[j]).strip()
            level = 1 if under.startswith("=") else 2
            title = " ".join(_strip_eol(l).strip() for l in lines[i:j]).lstrip("﻿")
            raw.append(("heading", i, j + 1, {"level": level, "title": title, "setext": True}))
            section_level = level
            i = j + 1
            continue
        raw.append(("paragraph", i, j, {}))
        i = j
    return _finish(raw, lines, file), warnings


def _finish(raw, lines, file):
    """Пустые строки — к предыдущему блоку (в начале файла — к первому); пути заголовков; id."""
    if not raw:
        return [{"id": f"{file}#1", "type": "blank", "text": "".join(lines), "start": 1, "end": max(1, len(lines)),
                 "sha1": hashlib.sha1("".join(lines).encode("utf-8")).hexdigest(), "path": [], "section": None}] \
            if lines else []
    spans = []
    for idx, (kind, a, b, extra) in enumerate(raw):
        start = 0 if idx == 0 else a
        end = raw[idx + 1][1] if idx + 1 < len(raw) else len(lines)
        spans.append((kind, start, end, a, b, extra))
    out = []
    stack: List[Tuple[int, str, str]] = []                   # (уровень, заголовок, id блока)
    for k, (kind, start, end, a, b, extra) in enumerate(spans, 1):
        bid = f"{file}#{k}"
        if kind == "heading":
            while stack and stack[-1][0] >= extra["level"]:
                stack.pop()
            stack.append((extra["level"], extra["title"], bid))
        text = "".join(lines[start:end])
        blk = {"id": bid, "type": kind, "text": text, "start": a + 1, "end": b,
               "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
               "path": [t for _, t, _ in stack], "section": stack[-1][2] if stack else None}
        blk.update({k2: v for k2, v in extra.items() if k2 in ("level", "title", "lang", "setext")})
        out.append(blk)
    return out


# ─── скан исходников проекта ───────────────────────────────────────────────

def source_files(root: Path, cfg: Dict[str, Any]) -> List[str]:
    """Относительные пути MD-исходников по конфигу, без исключённых; порядок детерминирован."""
    root = Path(root)
    excl = cfg.get("exclude") or []
    out = []
    for s in cfg.get("sources") or []:
        if s.get("adapter", "md") != "md":
            continue
        base = root / s["root"]
        cands = [base] if base.is_file() else sorted(base.rglob("*.md")) if base.is_dir() else []
        for p in cands:
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in excl):
                continue
            if rel not in out:
                out.append(rel)
    return out


def _out_path(root: Path, rel: str) -> Path:
    return Path(root) / BLOCKS_REL / (rel + ".json")


def scan_file(root: Path, rel: str) -> Dict[str, Any]:
    raw = (Path(root) / rel).read_bytes()
    text = raw.decode("utf-8")
    blocks, warnings = parse(text, rel)
    data = {"file": rel, "sha1": hashlib.sha1(raw).hexdigest(), "bytes": len(raw), "blocks": blocks,
            "warnings": warnings}
    p = _out_path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return data


def load(root: Path, rel: str) -> Optional[Dict[str, Any]]:
    p = _out_path(root, rel)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def outline(blocks: List[Dict[str, Any]], depth: int = 2) -> List[str]:
    top = min((b["level"] for b in blocks if b["type"] == "heading"), default=1)
    return [("  " * (b["level"] - top)) + b["title"] for b in blocks
            if b["type"] == "heading" and b["level"] < top + depth]


def scan(root: Path, cfg: Dict[str, Any], scope: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
    """Скан исходников в `.spec-graph/blocks/`; короткая сводка для модели (не больше `limit` файлов)."""
    files = source_files(root, cfg)
    if scope and scope != "all":
        files = [f for f in files if f == scope or f.startswith(scope.rstrip("/") + "/")
                 or Path(f).parts[-2:-1] == (scope,)]
    summary, warnings, total = [], [], 0
    for rel in files:
        data = scan_file(root, rel)
        total += len(data["blocks"])
        warnings += data["warnings"]
        if len(summary) < limit:
            summary.append({"file": rel, "blocks": len(data["blocks"]), "kb": round(data["bytes"] / 1024, 1),
                            "outline": outline(data["blocks"])[:12]})
    return {"files": len(files), "blocks": total, "summary": summary,
            "more_files": max(0, len(files) - limit), "warnings": warnings[:20]}
