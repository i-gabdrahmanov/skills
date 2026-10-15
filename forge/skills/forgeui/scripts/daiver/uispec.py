"""uispec.py — фронт-спека стори forge-ui (`ui-spec.md`): шапка, таблица кадров, проверка формы.

Разделы логики (Данные/Состояния/События/Валидации/Сценарии) разбирает общий парсер спек
(specs.py) — тот же, что потом читает движок; здесь — то, что добавляет forge-ui поверх него:
шапка с полем «Бэк-часть» (связь с бэк-спекой стори) и таблица «Кадры» (единственный вход шага
ui-design: кадры и URL макетов не выдумываются, они берутся отсюда).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from . import specs

SPEC_NAME = "ui-spec.md"

# Обязательные разделы: (ключ, алиасы нормализованного заголовка).
REQUIRED_SECTIONS = (
    ("purpose", ("назначение и границы", "назначение", "purpose and scope", "purpose")),
    ("frames", ("кадры", "frames")),
    ("acceptance", ("критерии приёмки", "критерии приемки", "acceptance criteria", "acceptance")),
)
# Разделы логики — канонические разделы парсера спек; пустой раздел — ошибка, явное «нет» — ок.
LOGIC_SECTIONS = ("data", "states", "events", "validations", "e2e")
LOGIC_TITLES = {"data": "Данные", "states": "Состояния", "events": "События",
                "validations": "Валидации", "e2e": "Сценарии"}

ROLES = {"screen": "screen", "экран": "screen", "dialog": "dialog", "диалог": "dialog",
         "модалка": "dialog", "state": "state", "состояние": "state",
         "breakpoint": "breakpoint", "брейкпоинт": "breakpoint"}

_FIELD = re.compile(r"\*\*\s*(Jira|Статус|Бэк-часть)\s*:?\s*\*\*\s*:?\s*([^*\n]*)", re.IGNORECASE)
_URL = re.compile(r"https?://\S+")
_EXPORT_FILE = re.compile(r"`?[\w./-]+\.(json|html?)`?", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"^(tbd|todo|\?+|—|-|нет|n/a|<.*>)$", re.IGNORECASE)
_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def header_fields(text: str) -> Dict[str, str]:
    """Поля шапки `**Jira:** … **Статус:** … **Бэк-часть:** …` (в одной строке или в разных)."""
    out: Dict[str, str] = {}
    head = "\n".join(specs.strip_comments(text).splitlines()[:15])
    for m in _FIELD.finditer(head):
        key = m.group(1).lower()
        out[{"jira": "jira", "статус": "status", "бэк-часть": "back"}[key]] = m.group(2).strip()
    return out


def _cells(row: str) -> List[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


_COLUMNS = {
    "name": ("имя", "кадр", "name", "frame"),
    "role": ("роль", "role"),
    "mockup": ("макет", "макет (url)", "url макета", "mockup", "design"),
    "route": ("маршрут", "route", "url"),
    "open": ("как открыть", "открытие", "open", "recipe"),
}


def frames_table(text: str) -> dict:
    """Таблица раздела «Кадры»: {rows: [{name, role, mockup, route, open}], columns, problems}."""
    lines = specs.strip_comments(text).splitlines()
    heads = specs._headings(lines)
    idx = next((i for i, h in enumerate(heads)
                if specs.norm_heading(h.title) in ("кадры", "frames")), None)
    if idx is None:
        return {"rows": [], "columns": {}, "problems": ["нет раздела «Кадры»"], "found": False}
    end = specs._block_end(heads, idx, len(lines))
    table = [l for l in lines[heads[idx].line + 1:end] if l.strip().startswith("|")]
    if len(table) < 2:
        return {"rows": [], "columns": {}, "found": True,
                "problems": ["в «Кадрах» нет таблицы (имя | роль | макет | маршрут | как открыть)"]}
    header = [specs.norm_heading(c) for c in _cells(table[0])]
    cols: Dict[str, int] = {}
    for key, names in _COLUMNS.items():
        for i, h in enumerate(header):
            if h in names and key not in cols and i not in cols.values():
                cols[key] = i
    problems = [f"в таблице «Кадров» нет колонки «{names[0]}»"
                for key, names in _COLUMNS.items() if key in ("name", "role", "mockup") and key not in cols]
    rows = []
    for raw in table[1:]:
        cells = _cells(raw)
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue                                    # строка-разделитель |---|---|
        row = {k: (cells[i] if i < len(cells) else "") for k, i in cols.items()}
        rows.append(row)
    return {"rows": rows, "columns": cols, "problems": problems, "found": True}


def _mockup_ok(cell: str) -> bool:
    c = (cell or "").strip()
    if not c or _PLACEHOLDER.match(c):
        return False
    return bool(_URL.search(c) or _EXPORT_FILE.search(c))


def check(text: str, rel: str = SPEC_NAME, project: Optional[Path] = None) -> dict:
    """Проверка формы ui-spec.md. errors — гейт не пройден; warnings — сказать, не блокировать."""
    errors: List[str] = []
    warnings: List[str] = []
    fields = header_fields(text)
    if not fields.get("jira"):
        errors.append("шапка: нет поля **Jira:** (ключ стори — связь с бэк-частью)")
    status = (fields.get("status") or "").lower()
    if status not in ("draft", "approved"):
        errors.append("шапка: **Статус:** должен быть draft или approved")
    back = (fields.get("back") or "").strip()
    if not back:
        errors.append("шапка: нет поля **Бэк-часть:** — напиши «нет» или «есть: <KEY>»; "
                      "отсутствие поля не значит «нет»")
    elif not re.match(r"^(нет|есть)\b", back.lower()):
        errors.append(f"шапка: **Бэк-часть:** «{back}» — ожидается «нет» или «есть: <KEY>»")

    lines = specs.strip_comments(text).splitlines()
    heads = specs._headings(lines)
    present = {specs.norm_heading(h.title) for h in heads}
    for key, names in REQUIRED_SECTIONS:
        hit = next((i for i, h in enumerate(heads) if specs.norm_heading(h.title) in names), None)
        if hit is None:
            errors.append(f"нет раздела «{names[0].capitalize()}»")
        elif key != "frames":
            body = specs._body(lines, heads[hit].line + 1, specs._block_end(heads, hit, len(lines)))
            if not body:
                errors.append(f"раздел «{heads[hit].title}» пуст")

    parsed = specs.parse_text(text, rel, specs.stem_of(Path(rel)))
    for sec in LOGIC_SECTIONS:
        blocks = parsed["sections"][sec]
        title = LOGIC_TITLES[sec]
        if not blocks:
            errors.append(f"нет раздела «{title}» — нет логики? напиши в нём «нет»")
            continue
        if all(not b["body"] for b in blocks):
            errors.append(f"раздел «{title}» пуст — заполни или явно напиши «нет»")

    e2e_blocks = parsed["sections"]["e2e"]
    scenarios = [s for s in parsed["e2e"] if not s["removed"]]
    if e2e_blocks and not all(b["empty"] for b in e2e_blocks) and not scenarios:
        errors.append("«Сценарии» не пусты, но ни один сценарий не разобран: пиши "
                      "Given … When … Then (или Дано … Когда … Тогда), строкой или блоком")

    data_blocks = parsed["sections"]["data"]
    if data_blocks and not all(b["empty"] for b in data_blocks):
        if not parsed["dataRefs"] and not parsed["mocks"]:
            errors.append("«Данные» не называют источник: `types:`/`client:` с путём в бэктиках "
                          "или явно «моки» — тип и клиент не изобретаются")
        if project is not None:
            for ref in parsed["dataRefs"]:
                if not (project / ref["path"]).exists():
                    warnings.append(f"«Данные»: {ref['kind']}: `{ref['path']}` пока нет в репо — "
                                    f"если не появится к сборке, упадёт компиляция (так и задумано)")

    ft = frames_table(text)
    if ft["found"]:
        errors.extend(ft["problems"])
        if ft["found"] and not ft["problems"] and not ft["rows"]:
            errors.append("в «Кадрах» нет ни одной строки — без кадра нечего строить")
        names = set()
        for n, row in enumerate(ft["rows"], start=1):
            name = row.get("name", "").strip()
            label = name or f"строка {n}"
            if not name:
                errors.append(f"«Кадры», строка {n}: нет имени кадра")
            elif name in names:
                errors.append(f"«Кадры»: имя «{name}» повторяется")
            names.add(name)
            if "role" in row and ROLES.get(row["role"].strip().lower()) is None:
                errors.append(f"«Кадры», {label}: роль «{row['role']}» — ожидается "
                              f"screen | dialog | state | breakpoint")
            if "mockup" in row and not _mockup_ok(row["mockup"]):
                errors.append(f"«Кадры», {label}: нет ссылки на макет (URL Pixso/Figma или файл "
                              f"экспорта) — кадры и URL не выдумываются, спроси пользователя")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "fields": fields,
        "frames": ft["rows"],
        "scenarios": [{"id": s["id"], "title": s["title"]} for s in scenarios],
        "dataRefs": parsed["dataRefs"],
        "mocks": parsed["mocks"],
        "sectionsPresent": sorted(present),
    }
