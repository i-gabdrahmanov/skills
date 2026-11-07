"""specs.py — структурный разбор спек бизнес-логики экрана (daiver_spec, спека §11.2).

Парсер режет, атрибутирует и считает — смысл спеки он не толкует (INV-B19):
  • пять канонических разделов — Data/States/Events/Validations/E2E и русские алиасы
    (Данные/Состояния/События/Валидации/Сценарии), на ЛЮБОМ уровне заголовка, регистр и
    двоеточие в конце не важны;
  • диалекты хостов: `### Requirement: …` / `#### Scenario: …` на любом уровне; строки
    Given…When…Then (и Дано…Когда…Тогда) где угодно — одной строкой или блоком;
  • блок закрывается заголовком того же или более высокого уровня (семантика markdown);
    обёртка вроде `## ADDED Requirements` становится контекстом сценария;
  • сценарий, чей контекст читается как REMOVED, описывает УДАЛЯЕМОЕ поведение — в e2e не идёт;
  • id сценария — `<stem>#<n>`; при совпадении стемов двух файлов к стему добавляется ближайший
    различающий родительский каталог (`promo-spec#1`), при повторном — числовой суффикс (-2, -3);
  • незнакомый формат — не ошибка: текст уходит в prose с атрибуцией. Ноль спек — нормальный
    исход (презентационный модуль), зарегистрированный и отсутствующий файл — предупреждение.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Канонические разделы → нормализованные имена заголовков (регистр/двоеточие снимаются).
SECTION_ALIASES: Dict[str, Tuple[str, ...]] = {
    "data": ("data", "данные"),
    "states": ("states", "состояния"),
    "events": ("events", "события"),
    "validations": ("validations", "валидации"),
    "e2e": ("e2e", "e2e scenarios", "scenarios", "сценарии", "сценарии e2e"),
}
_ALIAS_TO_SECTION = {alias: sec for sec, aliases in SECTION_ALIASES.items() for alias in aliases}

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_REQUIREMENT = re.compile(r"^requirement\s*:\s*(.+)$", re.IGNORECASE)
_SCENARIO = re.compile(r"^scenario\s*:\s*(.+)$", re.IGNORECASE)
_REMOVED = re.compile(r"\bremoved\b|удаля|удалён|удален", re.IGNORECASE)
# маркер пункта списка или номер в начале строки
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_EMPH = re.compile(r"[*_`]+")
# Ключевые слова GWT. Одна строка: Given … When … Then (в любом регистре, с выделением).
# После каждого ключевого слова обязано идти содержание, а не сразу следующее слово: упоминание
# формата («пиши Given/When/Then») — не сценарий.
_GWT_LINE = re.compile(
    r"\b(given|дано)\b\W+(?!(when|когда)\b)\w.*?"
    r"\b(when|когда)\b\W+(?!(then|тогда)\b)\w.*?"
    r"\b(then|тогда)\b\W+\w", re.IGNORECASE)
_GWT_START = re.compile(r"^(given|дано|допустим)\b", re.IGNORECASE)
_GWT_CONT = re.compile(r"^(when|then|and|but|когда|тогда|и|но|а)\b", re.IGNORECASE)
_DATA_REF = re.compile(r"\b(types|client)\s*:\s*`([^`]+)`", re.IGNORECASE)
_MOCKS = re.compile(r"\bмок(и|ов|ах|ами)?\b|\bmocks?\b", re.IGNORECASE)


def norm_heading(text: str) -> str:
    """Заголовок без выделения, хвостового двоеточия и лишних пробелов, в нижнем регистре."""
    t = _EMPH.sub("", text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t.rstrip(":").strip().lower()


def section_of(heading: str) -> Optional[str]:
    """Канонический раздел по тексту заголовка; None — не канонический."""
    return _ALIAS_TO_SECTION.get(norm_heading(heading))


def _plain(line: str) -> str:
    """Строка без маркера списка и markdown-выделения — для поиска ключевых слов GWT."""
    return _EMPH.sub("", _ITEM.sub("", line)).strip()


class _Heading:
    __slots__ = ("level", "title", "line")

    def __init__(self, level: int, title: str, line: int):
        self.level, self.title, self.line = level, title, line


def strip_comments(text: str) -> str:
    """Убирает HTML-комментарии `<!-- … -->`, сохраняя число строк (номера строк не плывут):
    в шаблонах там живут инструкции автору, а не содержание спеки."""
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL)


def _headings(lines: Sequence[str]) -> List[_Heading]:
    """Заголовки markdown вне fenced-блоков кода."""
    out: List[_Heading] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            out.append(_Heading(len(m.group(1)), m.group(2).strip(), i))
    return out


def _block_end(heads: List[_Heading], idx: int, total: int) -> int:
    """Строка, на которой кончается блок заголовка idx: следующий заголовок того же или
    более высокого уровня (markdown-семантика)."""
    level = heads[idx].level
    for h in heads[idx + 1:]:
        if h.level <= level:
            return h.line
    return total


def _context(heads: List[_Heading], line: int) -> List[str]:
    """Цепочка заголовков, охватывающих строку (от внешнего к внутреннему)."""
    chain: List[_Heading] = []
    for h in heads:
        if h.line >= line:
            break
        while chain and chain[-1].level >= h.level:
            chain.pop()
        chain.append(h)
    return [h.title for h in chain]


def _body(lines: Sequence[str], start: int, end: int) -> str:
    return "\n".join(lines[start:end]).strip()


def _is_empty_marker(text: str) -> bool:
    """Раздел явно помечен как пустой: «нет», «нет — презентационный экран», «-», «none»."""
    t = _plain(text).lower()
    return bool(re.match(r"^(нет|none|n/a|—|-)(\b|$|[\s.,—–-])", t))


def stem_of(path: Path) -> str:
    """Стем файла спеки: `promo.spec.md` → `promo`, `spec.md` → `spec`, `ui-spec.md` → `ui-spec`."""
    name = path.name
    for suf in (".md", ".markdown"):
        if name.lower().endswith(suf):
            name = name[: -len(suf)]
            break
    if name.lower().endswith(".spec"):
        name = name[: -len(".spec")]
    return name


def assign_stems(paths: Sequence[Path]) -> Dict[Path, str]:
    """Стабильные стемы для id сценариев: совпавшие стемы различаются ближайшим родительским
    каталогом, который их различает (`promo-spec`), затем числовым суффиксом."""
    base = {p: stem_of(p) for p in paths}
    out: Dict[Path, str] = {}
    by_stem: Dict[str, List[Path]] = {}
    for p, s in base.items():
        by_stem.setdefault(s, []).append(p)
    for stem, group in by_stem.items():
        if len(group) == 1:
            out[group[0]] = stem
            continue
        for p in group:
            others = [q for q in group if q != p]
            chosen = None
            for parent in p.parents:
                if parent.name and all(parent.name not in [a.name for a in q.parents] for q in others):
                    chosen = f"{parent.name}-{stem}"
                    break
            out[p] = chosen or stem
    # повторные совпадения (одинаковые каталоги на разной глубине) → числовой суффикс
    seen: Dict[str, int] = {}
    for p in sorted(out, key=lambda q: str(q)):
        s = out[p]
        if s in seen:
            seen[s] += 1
            out[p] = f"{s}-{seen[s]}"
        else:
            seen[s] = 1
    return out


def _scenarios(lines: Sequence[str], heads: List[_Heading]) -> List[dict]:
    """Сценарии файла в порядке документа: блоки `Scenario:` и строки/блоки Given-When-Then."""
    found: List[Tuple[int, dict]] = []
    covered = set()
    for i, h in enumerate(heads):
        m = _SCENARIO.match(_EMPH.sub("", h.title).strip())
        if not m:
            continue
        end = _block_end(heads, i, len(lines))
        covered.update(range(h.line, end))
        ctx = _context(heads, h.line)
        found.append((h.line, {"title": _EMPH.sub("", m.group(1)).strip(),
                                "body": _body(lines, h.line + 1, end),
                                "context": ctx, "line": h.line + 1}))
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or i in covered or _HEADING.match(line):
            i += 1
            continue
        text = _plain(line)
        if _GWT_LINE.search(text):
            found.append((i, {"title": text[:120], "body": text,
                              "context": _context(heads, i), "line": i + 1}))
            i += 1
            continue
        if _GWT_START.match(text):
            j = i + 1
            block = [text]
            while j < len(lines) and _plain(lines[j]) and _GWT_CONT.match(_plain(lines[j])) \
                    and not _HEADING.match(lines[j]):
                block.append(_plain(lines[j]))
                j += 1
            if len(block) > 1 and any(re.match(r"^(then|тогда)\b", b, re.IGNORECASE) for b in block):
                found.append((i, {"title": block[0][:120], "body": "\n".join(block),
                                  "context": _context(heads, i), "line": i + 1}))
                i = j
                continue
        i += 1
    found.sort(key=lambda t: t[0])
    return [d for _, d in found]


def parse_text(text: str, rel: str, stem: str) -> dict:
    """Разбор одного файла спеки. rel — путь для атрибуции, stem — стем для id сценариев."""
    lines = strip_comments(text).splitlines()
    heads = _headings(lines)
    sections: Dict[str, List[dict]] = {k: [] for k in SECTION_ALIASES}
    requirements: List[dict] = []
    prose: List[dict] = []
    canonical_lines = set()
    for i, h in enumerate(heads):
        sec = section_of(h.title)
        end = _block_end(heads, i, len(lines))
        if sec:
            body = _body(lines, h.line + 1, end)
            sections[sec].append({"file": rel, "heading": h.title, "line": h.line + 1,
                                  "body": body, "empty": not body or _is_empty_marker(body)})
            canonical_lines.update(range(h.line, end))
            continue
        m = _REQUIREMENT.match(_EMPH.sub("", h.title).strip())
        if m:
            requirements.append({"file": rel, "title": m.group(1).strip(), "line": h.line + 1,
                                 "context": _context(heads, h.line),
                                 "body": _body(lines, h.line + 1, end)})
    for i, h in enumerate(heads):
        # заголовок 1-го уровня — имя документа; внутри канонических разделов — их тело
        if h.level == 1 or h.line in canonical_lines:
            continue
        nxt = heads[i + 1].line if i + 1 < len(heads) else len(lines)
        body = _body(lines, h.line + 1, nxt)
        if body:
            prose.append({"file": rel, "heading": h.title, "line": h.line + 1, "chars": len(body)})
    scenarios = _scenarios(lines, heads)
    e2e = []
    for n, sc in enumerate(scenarios, start=1):
        sc["id"] = f"{stem}#{n}"
        sc["file"] = rel
        sc["removed"] = any(_REMOVED.search(c) for c in sc["context"])
        e2e.append(sc)
    data_refs = []
    mocks = False
    for blk in sections["data"]:
        for m in _DATA_REF.finditer(blk["body"]):
            data_refs.append({"kind": m.group(1).lower(), "path": m.group(2).strip(), "file": rel})
        mocks = mocks or bool(_MOCKS.search(blk["body"]))
    recognized = any(sections[k] for k in sections) or bool(requirements) or bool(e2e)
    return {"sections": sections, "requirements": requirements, "e2e": e2e, "prose": prose,
            "dataRefs": data_refs, "mocks": mocks, "recognized": recognized}


def parse_files(paths: Sequence[Path], registered: Sequence[Path] = (), root: Optional[Path] = None) -> dict:
    """daiver_spec: все спеки трека одним ответом. paths — найденные по конвенции,
    registered — зарегистрированные в track.yaml (могут лежать вне трека и отсутствовать)."""
    root = root or Path.cwd()
    reg = [Path(p) for p in registered]
    allp: List[Path] = []
    for p in list(reg) + [Path(x) for x in paths]:
        if p not in allp:
            allp.append(p)
    existing = [p for p in allp if p.is_file()]
    stems = assign_stems(existing)
    files: List[dict] = []
    warnings: List[str] = []
    out = {k: [] for k in ("data", "states", "events", "validations")}
    requirements: List[dict] = []
    e2e: List[dict] = []
    removed: List[dict] = []
    prose: List[dict] = []
    unrecognized: List[str] = []
    data_refs: List[dict] = []
    mocks = False
    for p in allp:
        try:
            rel = str(p.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = str(p)
        entry = {"path": rel, "registered": p in reg, "exists": p.is_file(), "parsed": False,
                 "bytes": 0, "sha256": None}
        if not p.is_file():
            if p in reg:
                warnings.append(f"спека зарегистрирована, но файла нет: {rel} "
                                f"(легитимно, если хост уже переложил её в архив)")
            files.append(entry)
            continue
        try:
            raw = p.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            warnings.append(f"не читается {rel}: {e}")
            files.append(entry)
            continue
        entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), parsed=True)
        files.append(entry)
        res = parse_text(text, rel, stems[p])
        for k in out:
            out[k].extend(res["sections"][k])
        requirements.extend(res["requirements"])
        for sc in res["e2e"]:
            (removed if sc["removed"] else e2e).append(sc)
        prose.extend(res["prose"])
        data_refs.extend(res["dataRefs"])
        mocks = mocks or res["mocks"]
        if not res["recognized"]:
            unrecognized.append(rel)
    has_specs = bool(e2e or removed or requirements or any(out[k] for k in out))
    return {
        "files": files,
        "hasSpecs": has_specs,
        **out,
        "requirements": requirements,
        "e2e": e2e,
        "removedScenarios": removed,
        "hasE2e": bool(e2e),
        "dataRefs": data_refs,
        "mocks": mocks,
        "prose": prose,
        "unrecognized": unrecognized,
        "counts": {"files": len(files), "parsed": sum(1 for f in files if f["parsed"]),
                   "requirements": len(requirements), "e2e": len(e2e),
                   "removed": len(removed), "prose": len(prose)},
        "warnings": warnings,
    }
