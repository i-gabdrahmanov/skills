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
граница блока. Незакрытым фенс считается, только если закрывающей строки нет до конца файла, или до неё
открывается другой фенс с языком, или у фенса указан язык без `#`-комментариев (json, sql, xml…), а внутри
стоит заголовок разметки с пустыми строками вокруг. Иначе строка `# …` внутри — комментарий кода (bash, YAML, Python): до 2026-11-08
правило срабатывало на ней, и закрытый фенс резался на три блока с ложным H1 (ревью 2026-11-08).

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

from . import fsutil as FS

BLOCKS_REL = ".spec-graph/blocks"
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
_TABLE = re.compile(r"^ {0,3}\|")
_HTML = re.compile(r"^ {0,3}<(?:[A-Za-z/!?])")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_HR = re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
# языки, где строка `# …` — комментарий кода, а не заголовок
_HASH_LANGS = {"bash", "sh", "shell", "zsh", "console", "yaml", "yml", "python", "py", "toml", "ini", "properties",
               "conf", "cfg", "dockerfile", "docker", "make", "makefile", "cmake", "r", "perl", "ruby", "rb",
               "powershell", "ps1", "nginx", "env", "dotenv", "gitignore", "tcl", "hcl", "terraform"}


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


def _fence_end(lines: List[str], i: int, mark: str, lang: str, section_level: int) -> Tuple[int, bool]:
    """(индекс строки после фенса, закрыт ли). Закрывающая строка есть и внутри нет признаков обрыва —
    фенс закрыт ею; иначе он кончается перед ближайшим заголовком раздела (или следующим фенсом с языком)."""
    n = len(lines)
    ch = re.escape(mark[0])
    close = re.compile(r"^ {0,3}" + ch + "{%d,}[ \t]*$" % len(mark))
    opener = re.compile(r"^ {0,3}" + ch + "{%d,}[ \t]*[^\\s%s]" % (len(mark), ch))

    def heading(j: int) -> bool:
        h = _ATX.match(_strip_eol(lines[j]))
        return bool(h) and (section_level == 0 or len(h.group(1)) <= section_level)

    def markup_heading(j: int, end: int) -> bool:
        """Заголовок разметки, а не комментарий кода: пустые строки до и после."""
        return heading(j) and j > 0 and _blank(lines[j - 1]) and (j + 1 >= end or _blank(lines[j + 1]))

    k = next((j for j in range(i + 1, n) if close.match(_strip_eol(lines[j]))), None)
    if k is not None and not any(opener.match(_strip_eol(lines[j])) for j in range(i + 1, k)):
        if not lang or lang.lower() in _HASH_LANGS or not any(markup_heading(j, k) for j in range(i + 1, k)):
            return k + 1, True
    j = i + 1
    while j < n and not heading(j) and not opener.match(_strip_eol(lines[j])):
        j += 1
    return j, False


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
            lang = s.strip()[len(mark):].strip().split(" ")[0]
            j, closed = _fence_end(lines, i, mark, lang, section_level)
            if not closed:
                warnings.append({"code": "W-fence-unclosed", "line": i + 1, "file": file})
            raw.append(("fence", i, j, {"lang": lang}))
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


def full_id(file: str, bid: Any) -> Any:
    """Короткий id блока единицы одного файла (`#12`, `12`) — в полный `<файл>#12`; прочее как есть.
    Субагент GLM на боевом прогоне писал короткие id по шаблону оркестратора, и валидатор отбивал
    целую пачку: файл единицы известен, гадать модели не о чем."""
    if isinstance(bid, int) and not isinstance(bid, bool):
        return f"{file}#{bid}"
    if isinstance(bid, str) and re.fullmatch(r"#?\d+", bid.strip()):
        return f"{file}#{bid.strip().lstrip('#')}"
    return bid


# ─── скан исходников проекта ───────────────────────────────────────────────

def md_files(base: Path) -> List[Path]:
    """MD-файлы корня исходников: расширение без учёта регистра (`Экран.MD` молча выпадал — rglob("*.md")
    регистрозависим), без скрытых каталогов (`.trash/` Obsidian с удалёнными заметками, `.obsidian/`)."""
    if base.is_file():
        return [base]
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() == ".md"
                  and not any(part.startswith(".") for part in p.relative_to(base).parts[:-1]))


def source_files(root: Path, cfg: Dict[str, Any]) -> List[str]:
    """Относительные пути MD-исходников по конфигу, без исключённых; порядок детерминирован."""
    root = Path(root)
    excl = cfg.get("exclude") or []
    out = []
    for s in cfg.get("sources") or []:
        if s.get("adapter", "md") != "md":
            continue
        base = root / s["root"]
        cands = md_files(base)
        for p in cands:
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in excl):
                continue
            if rel not in out:
                out.append(rel)
    return out


def _out_path(root: Path, rel: str) -> Path:
    return Path(root) / BLOCKS_REL / (rel + ".json")


class SourceError(Exception):
    """Исходник не читается движком — отказ с именем файла, а не трейсбек без него."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def decode(raw: bytes, rel: str) -> str:
    """UTF-8 или отказ E-source-encoding: один файл в cp1251 ронял разведку всего корпуса голым
    UnicodeDecodeError без имени файла (ревью 2026-11-08)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SourceError(f"исходник {rel} не в UTF-8 (байт 0x{raw[e.start]:02x} на позиции {e.start}): пусть "
                          f"пользователь пересохранит его в UTF-8 или исключит (exclude в .spec-graph/config.json)",
                          "E-source-encoding")


def changed_since_scan(root: Path, rel: str) -> bool:
    """Файл изменился после скана: sha1 его байтов ≠ sha1 в кэше блоков. Нарезка и проверка дословности
    читают кэш, поэтому без этой сверки правка спеки после разведки (или после приёмки) не видна: старый
    текст в vault, «перенесён полностью», гейты проходят. Нет файла или кэша — не «изменился»."""
    p = Path(root) / rel
    data = load(root, rel)
    if data is None or not p.is_file():
        return False
    return hashlib.sha1(p.read_bytes()).hexdigest() != data.get("sha1")


def scan_file(root: Path, rel: str) -> Dict[str, Any]:
    raw = (Path(root) / rel).read_bytes()
    text = decode(raw, rel)
    blocks, warnings = parse(text, rel)
    data = {"file": rel, "sha1": hashlib.sha1(raw).hexdigest(), "bytes": len(raw), "blocks": blocks,
            "warnings": warnings}
    p = _out_path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
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
