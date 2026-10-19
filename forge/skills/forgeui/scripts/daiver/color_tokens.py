"""color_tokens.py — daiver_color_tokens: карта «цвет макета → переменная проекта» по `project.styling.colorsFile`
(спека §13.2 п.2, §13.5 п.9; INV-B18, A1; задача 3.2).

Сборка пишет цвет переменной проекта ТОЛЬКО при точном совпадении значения; иначе — сырое значение макета; близкий цвет не
угадывается никогда (INV-B18). Сличать rgba на глаз — арифметика, а не суждение, поэтому её делает код: файл цветов разбирается
здесь, цвета экрана берутся из канона, ответ — короткие списки «цвет → переменная» и «цвет без переменной» (модель канон не читает).

Что разбирается. Формы выведены из текста спеки («less/scss/css-переменные»); боевого файла цветов в образцах нет — допущения
названы в «Результате» задачи и в README → «Нужно от пользователя»:
  • объявления `@имя: значение;` (less), `$имя: значение;` (scss; флаги !default / !global отбрасываются), `--имя: значение;` (css)
    в файлах .less / .scss / .css; комментарии `/* … */` и `// …` (less, scss) вырезаются;
  • значение — литерал цвета: #rgb, #rgba, #rrggbb, #rrggbbaa (регистр не важен), rgb() / rgba() через запятые или пробелы (+ «/ альфа»,
    альфа числом или процентом), white / black / transparent; либо алиас другой переменной (@x, $x, var(--x)) — цепочки алиасов
    разворачиваются. Остальное (darken(), ~"…", списки, карты, hsl()) — не литерал: переменная пропускается с причиной, значение
    не угадывается;
  • вид сравнения — канон макета: `rgba(R, G, B, A)` (`canon.fmt_rgba`): регистр hex и короткая форма (#FFF = #ffffff) совпадению
    не мешают, альфа округляется до 3 знаков, как в каноне; цвет, отличный хотя бы на единицу канала или тысячную альфы, — другой цвет;
  • переменная, объявленная несколько раз с РАЗНЫМИ значениями (темы, переопределения), неоднозначна: в карту не входит, называется
    в ответе (`conflicts`);
  • у одного значения несколько переменных → берётся первая по файлу, причём объявленная литералом идёт раньше объявленной алиасом
    (палитра важнее семантического синонима); число таких значений называется в ответе (`ambiguous`).

Цвета экрана — из канона кадра: `style.background`, `style.gradient`, `style.border.color`, `style.shadow` (цвета внутри CSS-строки
тоже: они пишутся отдельной переменной), `color` узлов, которые рисует сборка, — не occluded и не библиотечные «чёрные ящики»
(их рисует компонент библиотеки). Порядок — порядок документа, без повторов.

Границы. Файл читается только внутри репо (путь из конфига правит человек, но чтение не должно выходить за корень). Формат вне
.less / .scss / .css — не ошибка: ответ называет это, цвета остаются сырыми (спека: «без совпадения — сырое значение»). Не найденный
файл — отказ с лечением: путь в конфиге опечатан, и молчаливо принять «все цвета сырые» значило бы скрыть ошибку настройки.
Модуль ничего не пишет.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import canon as C
from . import config as CFG
from . import outline as OL

FORMATS = {".less": "less", ".scss": "scss", ".css": "css"}
MAX_BYTES = 2 * 1024 * 1024          # файл цветов — список переменных, не гигабайты разметки
MAX_DEPTH = 16                       # длина цепочки алиасов
MAX_VALUE = 200                      # знаков значения: длиннее — не цвет (карта, список); и разбор не квадратичен на мусоре
SHOWN = 10                           # имён в перечнях отказов и пропусков
MAP_SHOWN = 40                       # строк карты в ответе без трека
SCREEN_SHOWN = 60                    # цветов экрана в одном списке ответа
NAMED = {"white": (255, 255, 255, 1), "black": (0, 0, 0, 1), "transparent": (0, 0, 0, 0)}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB = re.compile(r"^rgba?\(\s*([^()]*?)\s*\)$", re.IGNORECASE)
_NUM = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")
_PCT = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))%$")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?<![:\"'])//[^\n]*")
_FLAGS = re.compile(r"!\s*(?:default|global|important)\b", re.IGNORECASE)
_DECL = re.compile(
    r"(?:^|(?<=[;{}]))[ \t]*(?P<name>@[A-Za-z_][\w-]*|\$[A-Za-z_][\w-]*|--[A-Za-z_][\w-]*)[ \t]*:[ \t]*"
    r"(?P<val>[^;{}]{0,%d}?)[ \t]*(?=;|\}|\Z)" % MAX_VALUE, re.MULTILINE)
_ALIAS = re.compile(r"^(?:(@[A-Za-z_][\w-]*)|(\$[A-Za-z_][\w-]*)|var\(\s*(--[A-Za-z_][\w-]*)\s*\))$")
_COLOR_IN_TEXT = re.compile(r"rgba?\([^()]*\)", re.IGNORECASE)


class ColorTokensError(ValueError):
    """Файл цветов или трек не годятся — сообщение называет причину и что делать."""


# ── цвет → канонический вид ──────────────────────────────────────────────────

def _rgb_parts(inner: str) -> Optional[str]:
    if "," in inner:
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) not in (3, 4):
            return None
        chans, alpha = parts[:3], (parts[3] if len(parts) == 4 else None)
    else:
        head, slash, tail = inner.partition("/")
        chans = head.split()
        alpha = tail.strip() if slash else None
        if slash and not alpha:
            return None
    if len(chans) != 3 or not all(_NUM.match(c) for c in chans):
        return None
    vals = [float(c) for c in chans]
    if not all(0 <= v <= 255 for v in vals):
        return None
    a = 1.0
    if alpha is not None:
        pct = _PCT.match(alpha)
        if pct:
            a = float(pct.group(1)) / 100
        elif _NUM.match(alpha):
            a = float(alpha)
        else:
            return None
        if not 0 <= a <= 1:
            return None
    return C.fmt_rgba(vals[0], vals[1], vals[2], a)


def normalize_color(raw: Any) -> Optional[str]:
    """Литерал цвета → канонический `rgba(R, G, B, A)` (как в каноне макета) или None, если это не литерал цвета."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.lower() in NAMED:
        return C.fmt_rgba(*NAMED[s.lower()])
    if _HEX.match(s):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return C.fmt_rgba(r, g, b, int(h[6:8], 16) / 255 if len(h) == 8 else 1)
    m = _RGB.match(s)
    return _rgb_parts(m.group(1)) if m else None


# ── файл цветов ──────────────────────────────────────────────────────────────

def _strip_comments(text: str, fmt: str) -> str:
    text = _BLOCK_COMMENT.sub(" ", text)
    return text if fmt == "css" else _LINE_COMMENT.sub("", text)


def _classify(val: str) -> Tuple[str, str]:
    """('lit', канонический цвет) | ('alias', имя цели) | ('other', причина)."""
    color = normalize_color(val)
    if color is not None:
        return "lit", color
    m = _ALIAS.match(val)
    if m:
        return "alias", m.group(1) or m.group(2) or m.group(3)
    shown = " ".join(val.split())
    return "other", f"не литерал цвета ({shown[:30]}{'…' if len(shown) > 30 else ''})"


def ref(name: str) -> str:
    """Как переменная пишется в коде: @x, $x; у css-свойства — var(--x)."""
    return f"var({name})" if name.startswith("--") else name


def parse_colors(text: str, fmt: str) -> Dict[str, Any]:
    """Разбор файла цветов. → {values: {имя: цвет}, table: {цвет: переменная}, order: [цвет…] (по файлу), conflicts: [имена],
    skipped: [(имя, причина)], ambiguous: число значений с несколькими переменными}. Чистая функция: диска нет."""
    defs: Dict[str, List[Tuple[str, str]]] = {}
    order: List[str] = []
    for m in _DECL.finditer(_strip_comments(text, fmt)):
        name, val = m.group("name"), _FLAGS.sub("", m.group("val")).strip()
        if not val:
            continue
        if name not in defs:
            defs[name] = []
            order.append(name)
        defs[name].append(_classify(val))
    # Алиасы разворачиваются кругами, а не рекурсией: круг k решает имена, чья цепочка до литерала — k-1 переход. Итог зависит от
    # графа, а не от порядка объявлений; цикл и цепочка длиннее MAX_DEPTH остаются нерешёнными.
    state: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}          # имя → (ok, цвет, —) | (conflict|fail, —, причина)

    def decide(name: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
        found: List[str] = []
        aliases = [payload for kind, payload in defs[name] if kind == "alias"]
        for kind, payload in defs[name]:
            if kind == "other":
                return "fail", None, payload
            if kind == "lit":
                found.append(payload)
        undecided = False
        for target in aliases:
            if target not in defs:
                return "fail", None, f"алиас на {target}: не определена в файле"
            got = state.get(target)
            if got is None:
                undecided = True
            elif got[0] != "ok":
                return "fail", None, f"алиас на {target}: {got[2]}"
            else:
                found.append(got[1] or "")
        if undecided:
            return None
        return ("ok", found[0], None) if len(set(found)) == 1 else ("conflict", None, "объявлена с разными значениями")

    pending = list(order)
    for _round in range(MAX_DEPTH + 1):
        left = []
        fresh: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}      # решения круга видны только со следующего
        for name in pending:
            got = decide(name)
            if got is None:
                left.append(name)
            else:
                fresh[name] = got
        if not fresh:
            break
        state.update(fresh)
        pending = left
    for name in pending:
        state[name] = ("fail", None, "цикл алиасов или цепочка длиннее допустимой")
    values: Dict[str, str] = {}
    conflicts: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for name in order:
        kind, color, why = state[name]
        if kind == "ok":
            values[name] = color or ""
        elif kind == "conflict":
            conflicts.append(name)
        else:
            skipped.append((name, why or ""))
    pos = {n: i for i, n in enumerate(order)}
    by_value: Dict[str, List[str]] = {}
    for name in sorted(values, key=lambda n: (0 if any(k == "lit" for k, _p in defs[n]) else 1, pos[n])):
        by_value.setdefault(values[name], []).append(name)
    table = {color: ref(names[0]) for color, names in by_value.items()}
    listed = sorted(by_value, key=lambda c: pos[by_value[c][0]])
    return {"values": values, "table": table, "order": listed, "conflicts": conflicts, "skipped": skipped,
            "ambiguous": sum(1 for names in by_value.values() if len(names) > 1)}


def lookup(table: Dict[str, str], color: Any) -> Optional[str]:
    """Переменная для цвета — только при точном совпадении канонического значения; иначе None (сырое значение)."""
    key = normalize_color(color)
    return table.get(key) if key is not None else None


def read_colors_file(root: Path, rel: str) -> Tuple[str, Optional[str]]:
    """(текст файла, формат). Формат вне .less / .scss / .css — (пустой текст, None): файл не читается. Нет файла, путь вне репо,
    слишком большой файл, не UTF-8 — отказ с лечением."""
    root = Path(root)
    fix = "поправь project.styling.colorsFile в .daiver/config.yaml (файл правит человек) или убери ключ — тогда цвета пойдут сырыми"
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or rel.startswith("~"):
        raise ColorTokensError(f"project.styling.colorsFile «{rel}»: нужен относительный путь внутри репо, без «..»; {fix}")
    full = (root / p)
    try:
        full.resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        raise ColorTokensError(f"project.styling.colorsFile «{rel}» выходит за корень репо; {fix}")
    if not full.is_file():
        raise ColorTokensError(f"project.styling.colorsFile «{rel}»: файла нет; {fix}")
    fmt = FORMATS.get(full.suffix.lower())
    if fmt is None:
        return "", None
    try:
        data = full.read_bytes()
    except OSError as e:
        raise ColorTokensError(f"project.styling.colorsFile «{rel}» не читается ({e.strerror or e}); {fix}")
    if len(data) > MAX_BYTES:
        raise ColorTokensError(f"project.styling.colorsFile «{rel}»: {len(data)} байт, больше {MAX_BYTES} — это не список "
                               f"переменных цветов; {fix}")
    try:
        return data.decode("utf-8-sig"), fmt
    except UnicodeDecodeError:
        raise ColorTokensError(f"project.styling.colorsFile «{rel}»: файл не в UTF-8; {fix}")


# ── цвета экрана ─────────────────────────────────────────────────────────────

def _color_strings(n: dict) -> List[str]:
    style = n.get("style") if isinstance(n.get("style"), dict) else {}
    border = style.get("border") if isinstance(style.get("border"), dict) else {}
    cand = [style.get("background"), style.get("gradient"), border.get("color"), style.get("shadow"), n.get("color")]
    return [c for c in cand if isinstance(c, str)]


def screen_colors(root_node: dict) -> List[str]:
    """Канонические цвета, которые пишет сборка, в порядке документа и без повторов (определение — в докстринге модуля)."""
    seen: Dict[str, None] = {}
    stack = [root_node]
    while stack:
        n = stack.pop()
        if n.get("occluded") is True or OL._black_box(n):
            continue
        for text in _color_strings(n):
            for found in _COLOR_IN_TEXT.findall(text):
                color = normalize_color(found)
                if color is not None:
                    seen.setdefault(color)
        stack.extend(reversed(OL._kids(n)))
    return list(seen)


# ── ответ инструмента ────────────────────────────────────────────────────────

def _capped(out: Dict[str, Any], key: str, items: List[str], limit: int) -> None:
    out[key] = items[:limit]
    if len(items) > limit:
        out[key + "More"] = len(items) - limit


def run(root: Any, track: Optional[str] = None, frame: Optional[str] = None) -> Dict[str, Any]:
    """`daiver_color_tokens`: карта файла цветов; с `track` — цвета этого кадра: переменная проекта или «сырой». Только читает."""
    root = Path(root)
    rel = CFG.load(root)["config"]["project"]["styling"]["colorsFile"]
    out: Dict[str, Any] = {"colorsFile": rel}
    parsed: Dict[str, Any] = {"table": {}, "order": [], "conflicts": [], "skipped": [], "ambiguous": 0, "values": {}}
    note = None
    if rel is None:
        note = "project.styling.colorsFile не задан — все цвета пишутся сырыми значениями макета (rgba(...) как есть)"
    else:
        text, fmt = read_colors_file(root, rel)
        out["format"] = fmt
        if fmt is None:
            note = (f"формат «{Path(rel).suffix or rel}» не разбирается (разбираются .less, .scss, .css) — все цвета пишутся "
                    f"сырыми значениями макета; скажи об этом в ответе пользователю")
        else:
            parsed = parse_colors(text, fmt)
            out["variables"] = len(parsed["values"])
            if parsed["conflicts"]:
                _capped(out, "conflicts", parsed["conflicts"], SHOWN)
            if parsed["skipped"]:
                _capped(out, "skipped", [f"{n}: {why}"[:120] for n, why in parsed["skipped"]], SHOWN)
            if parsed["ambiguous"]:
                out["ambiguous"] = parsed["ambiguous"]
    table = parsed["table"]
    if track is None:
        _capped(out, "map", [f"{c} → {table[c]}" for c in parsed["order"]], MAP_SHOWN)
    else:
        try:
            _manifest, f, _paths, env, warnings = OL._load(root, track, frame)
        except OL.OutlineError as e:
            raise ColorTokensError(str(e))
        colors = screen_colors(env["root"])
        out["frame"] = f["name"]
        out["screenColors"] = len(colors)
        _capped(out, "matched", [f"{c} → {table[c]}" for c in colors if c in table], SCREEN_SHOWN)
        _capped(out, "raw", [c for c in colors if c not in table], SCREEN_SHOWN)
        if warnings:
            out["warnings"] = list(warnings)
    out["nextStep"] = note or ("цвет из списка matched пиши переменной проекта; цвет без переменной (raw) — сырым значением "
                               "макета; ближний цвет не подбирай и переменную не угадывай (точное совпадение — единственное правило)")
    return out
