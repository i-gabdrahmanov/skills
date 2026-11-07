#!/usr/bin/env python3
"""gen_variants.py — стресс-корпус: варианты СТИЛЯ фикстуры ТЗ при неизменном смысле (задача 0.5, D22).

Реальной спеки разработка не увидит никогда. Единственная защита от «на синтетике работает, в окружении
нет» — прогонять правила на многих стилях записи одного содержания. Генератор берёт `fixtures/tz-mini`
и детерминированно переписывает ФОРМУ: нумерацию и вид заголовков, способ записи API, таблицы, списки,
переводы строк, кодировочные мелочи. Смысл не трогается, поэтому эталонный граф у всех вариантов один —
с картой имён, если трансформация меняет текст имени (ё → е).

Варианты не лежат в git копиями: они строятся из базы на лету (тесты — во временный каталог). Правка
базы сама доходит до всех вариантов, копии не разъезжаются. Посмотреть глазами:

    python3 -X utf8 gen_variants.py --out /tmp/variants [--variant english-groups]

Каждая находка из окружения (4.4) добавляет сюда трансформацию той же формы — корпус растёт из реальных
находок, не видя реальных данных.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1] / "fixtures" / "tz-mini" / "sources"

_HEAD = re.compile(r"^(#{1,6}) (.*)$", re.M)
_NUM = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")
_API_HEAD = re.compile(r"^(#{3,4}) ((?:\d+(?:\.\d+)*)\s+)?(GET|POST|PUT|PATCH|DELETE) (/\S+) — (.+)$", re.M)


def _heading_titles(text: str, fn: Callable[[int, str], str]) -> str:
    """Применить fn(уровень, заголовок) к заголовкам вне фенсов."""
    out, fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
        m = _HEAD.match(line) if not fence else None
        out.append(fn(len(m.group(1)), m.group(2)) if m else line)
    return "\n".join(out)


def _outside_fences(text: str, fn: Callable[[str], str]) -> str:
    parts = re.split(r"(^```[^\n]*\n.*?^```[ \t]*$)", text, flags=re.M | re.S)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


# ─── трансформации: текст файла → текст файла ──────────────────────────────

def num_trailing_dot(t: str) -> str:
    def f(lvl, title):
        m = _NUM.match(title)
        return "#" * lvl + " " + (f"{m.group(1)}. {m.group(2)}" if m else title)
    return _heading_titles(t, f)


def num_none(t: str) -> str:
    def f(lvl, title):
        m = _NUM.match(title)
        return "#" * lvl + " " + (m.group(2) if m else title)
    return _heading_titles(t, f)


def num_razdel(t: str) -> str:
    def f(lvl, title):
        m = _NUM.match(title)
        return "#" * lvl + " " + (f"Раздел {m.group(1)}. {m.group(2)}" if m and lvl == 2 else title)
    return _heading_titles(t, f)


def setext(t: str) -> str:
    def f(lvl, title):
        if lvl in (1, 2):
            # «5. Заголовок» над подчёркиванием — по CommonMark пункт списка: автор экранирует точку
            title = re.sub(r"^(\d+)([.)])(\s)", r"\1\\\2\3", title)
            return title + "\n" + ("=" if lvl == 1 else "-") * max(3, len(title))
        return "#" * lvl + " " + title
    return _heading_titles(t, f)


def bold_h3(t: str) -> str:
    return _heading_titles(t, lambda lvl, title: f"**{title}**" if lvl == 3 else "#" * lvl + " " + title)


def code_prefix(t: str) -> str:
    return _heading_titles(t, lambda lvl, title: "#" * lvl + " " + (f"[kid_task] {title}" if lvl == 3 else title))


_EN = {"Общее описание": "Overview", "Шедулеры": "Schedulers", "Динамические атрибуты": "Dynamic attributes",
       "См. также": "See also", "REST API": "Endpoints", "Kafka": "Kafka consumers and producers"}


def english_groups(t: str) -> str:
    def f(lvl, title):
        if lvl != 2:
            return "#" * lvl + " " + title
        m = _NUM.match(title)
        num, rest = (m.group(1) + " ", m.group(2)) if m else ("", title)
        return "#" * lvl + " " + num + _EN.get(rest, rest)
    return _heading_titles(t, f)


def api_in_body(t: str) -> str:
    """`### 4.1 GET /path — заголовок` → `### 4.1 Заголовок` + строка с методом и путём в теле."""
    return _API_HEAD.sub(lambda m: f"{m.group(1)} {m.group(2) or ''}{m.group(5)[0].upper()}{m.group(5)[1:]}\n\n"
                                   f"Метод: `{m.group(3)} {m.group(4)}`", t)


def api_openapi(t: str) -> str:
    """Метод и путь — YAML-фрагментом OpenAPI в теле раздела."""
    def sub(m):
        title = m.group(5)
        return (f"{m.group(1)} {m.group(2) or ''}{title[0].upper()}{title[1:]}\n\n```yaml\npaths:\n  {m.group(4)}:\n"
                f"    {m.group(3).lower()}:\n      summary: {title}\n```")
    return _API_HEAD.sub(sub, t)


def html_tables(t: str) -> str:
    def conv(chunk: str) -> str:
        lines, out, i = chunk.split("\n"), [], 0
        while i < len(lines):
            if lines[i].startswith("|") and i + 1 < len(lines) and re.match(r"^\|[-| :]+\|$", lines[i + 1]):
                rows = [lines[i]]
                j = i + 2
                while j < len(lines) and lines[j].startswith("|"):
                    rows.append(lines[j])
                    j += 1
                cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
                html = ["<table>", "<tr>" + "".join(f"<th>{c}</th>" for c in cells[0]) + "</tr>"]
                html += ["<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in cells[1:]]
                out += html + ["</table>"]
                i = j
            else:
                out.append(lines[i])
                i += 1
        return "\n".join(out)
    return _outside_fences(t, conv)


def paren_lists(t: str) -> str:
    return _outside_fences(t, lambda c: re.sub(r"^(\d+)\. ", r"\1) ", c, flags=re.M))


def tab_indent(t: str) -> str:
    return _outside_fences(t, lambda c: re.sub(r"^   (?=\S)", "\t", c, flags=re.M))


def crlf(t: str) -> str:
    return t.replace("\n", "\r\n")


def bom(t: str) -> str:
    return "﻿" + t


def nbsp(t: str) -> str:
    return _outside_fences(t, lambda c: re.sub(r"(?<=[а-яё]) (?=[а-яё])", " ", c))


def yo_to_e(t: str) -> str:
    return t.replace("ё", "е").replace("Ё", "Е")


def sched_reglament(t: str) -> str:
    """Шедулер называется «регламентным заданием» (частое в банковских спеках; пробный цикл 4.4)."""
    for a, b in (("Шедулеры", "Регламентные задания"), ("шедулеры", "регламентные задания"),
                 ("Шедулер", "Регламентное задание"), ("шедулер", "регламентное задание")):
        t = t.replace(a, b)
    return t


def unclosed_fence(t: str) -> str:
    """Первый json-фенс раздела «Kafka» теряет закрывающую строку (как task-service.md#1748 прототипа)."""
    head = t.find("### 3.1")
    if head < 0:
        return t
    start = t.find("```json", head)
    end = t.find("\n```\n", start + 7)
    return t if start < 0 or end < 0 else t[:end] + "\n" + t[end + 5:]


def prose_in_fence(t: str) -> str:
    """Строка прозы внутри json-фенса (PROBLEMS.md §F: «Находим tn_aod по id…» внутри кода)."""
    return t.replace('  "upzTaskId": 98765,\n', '  "upzTaskId": 98765,\nНаходим задачу по requestId и сохраняем upzTaskId.\n', 1)


TRANSFORMS: Dict[str, Callable[[str], str]] = {f.__name__: f for f in (
    num_trailing_dot, num_none, num_razdel, setext, bold_h3, code_prefix, english_groups, api_in_body,
    api_openapi, html_tables, paren_lists, tab_indent, crlf, bom, nbsp, yo_to_e, unclosed_fence, prose_in_fence,
    sched_reglament)}

# Трансформации, меняющие текст ИМЁН и цитат: та же функция применяется к именам и цитатам эталона.
TEXT_TRANSFORMS = {"yo_to_e", "nbsp", "sched_reglament"}

VARIANTS: Dict[str, List[str]] = {
    "num-trailing-dot": ["num_trailing_dot"],
    "num-none": ["num_none"],
    "num-razdel": ["num_razdel"],
    "setext": ["setext"],
    "bold-h3": ["bold_h3"],
    "code-prefix": ["code_prefix"],
    "english-groups": ["english_groups"],
    "api-in-body": ["api_in_body"],
    "api-openapi": ["api_openapi"],
    "html-tables": ["html_tables"],
    "paren-lists": ["paren_lists", "tab_indent"],
    "crlf-bom": ["crlf", "bom"],
    "nbsp-yo": ["nbsp", "yo_to_e"],
    "unclosed-fence": ["unclosed_fence"],
    "prose-in-fence": ["prose_in_fence"],
    "sched-reglament": ["sched_reglament"],
    "mixed": ["num_none", "bold_h3", "api_in_body", "html_tables", "crlf"],
}


def apply(text: str, transforms: List[str]) -> str:
    for name in transforms:
        text = TRANSFORMS[name](text)
    return text


def text_fn(variant: str) -> Callable[[str], str]:
    """Как вариант меняет имена и цитаты эталона (для сверки)."""
    names = [t for t in VARIANTS[variant] if t in TEXT_TRANSFORMS]
    return lambda s: apply(s, names)


def materialize(out: Path, variant: str, base: Path = BASE) -> Path:
    dst = Path(out) / variant
    if dst.exists():
        shutil.rmtree(dst)
    for src in sorted(base.rglob("*")):
        rel = src.relative_to(base)
        target = dst / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".md":
            text = src.read_text(encoding="utf-8")
            target.write_bytes(apply(text, VARIANTS[variant]).encode("utf-8"))
        else:
            shutil.copyfile(src, target)
    return dst


def bloated(out: Path, services: int = 50, sections: int = 40) -> Path:
    """«Раздутый» корпус: services сервисов × sections шедулеров-секций из шаблона task-service (бюджет)."""
    dst = Path(out) / "bloated"
    if dst.exists():
        shutil.rmtree(dst)
    tmpl = (BASE / "task-service" / "task-service.md").read_text(encoding="utf-8")
    section = tmpl[tmpl.index("### 2.1"):tmpl.index("## 3. Kafka")]
    for s in range(1, services + 1):
        svc = f"svc-{s:02d}"
        parts = [f"# {svc}\n\n## 1. Общее описание\n\nСервис {svc} — синтетика для замера бюджета.\n\n## 2. Шедулеры\n"]
        for k in range(1, sections + 1):
            parts.append(re.sub(r"^### 2\.(\d) (.+)$", lambda m: f"### 2.{k}.{m.group(1)} {m.group(2)} ({svc}, {k})",
                                section, flags=re.M))
        f = dst / svc / f"{svc}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(parts), encoding="utf-8")
    return dst


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Стресс-корпус: варианты стиля фикстуры tz-mini.")
    ap.add_argument("--out", required=True, help="куда положить варианты")
    ap.add_argument("--variant", choices=sorted(VARIANTS), help="один вариант (по умолчанию все)")
    ap.add_argument("--bloated", action="store_true", help="плюс «раздутый» корпус 50×40")
    a = ap.parse_args(argv)
    for v in ([a.variant] if a.variant else sorted(VARIANTS)):
        print(materialize(Path(a.out), v))
    if a.bloated:
        print(bloated(Path(a.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
