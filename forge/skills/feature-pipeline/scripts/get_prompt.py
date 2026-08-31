#!/usr/bin/env python3
"""
get_prompt.py — печатает ОДНУ секцию `references/subagent-prompts.md` в stdout.

Зачем: оркестратор feature-pipeline берёт контракт субагента (§4.0, §4.1, §7.3 …)
для конкретной фазы. Раньше для этого читался весь файл (~896 строк / ~13K токенов).
Этот скрипт извлекает только нужную секцию (~30-70 строк), экономя контекст оркестратора.

Секция = заголовок `## <id> ...` или `### <id> ...` и всё до следующего заголовка
того же или более высокого уровня (т.е. с тем же или меньшим числом `#`).
Под-под-секции (более глубокий уровень) включаются.

Использование:
  python get_prompt.py 4.0        # секция «## 4.0 BRD-писатель ...»
  python get_prompt.py 7.3        # секция «### 7.3 build-judge ...»
  python get_prompt.py --list     # перечислить доступные id секций

Exit codes:
  0 — секция найдена и напечатана (или --list)
  1 — секция не найдена / файл отсутствует
"""

import argparse
import re
import sys
from pathlib import Path

PROMPTS_MD = Path(__file__).resolve().parent.parent / "references" / "subagent-prompts.md"

# Заголовок markdown: группы — уровень (#...) и первый токен после пробела.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(\S+)")
# id-секции: 4.0, 4.0a, 7.3 … Заголовки вида «## 5. Спецадаптер» дают токен «5.»,
# поэтому хвостовую точку нормализуем (срезаем) с обеих сторон при сравнении.
_ID_RE = re.compile(r"^\d+(\.\d+)?[a-z]?$")


def _norm(token):
    """Нормализует токен заголовка/запроса: срезает хвостовую точку («5.» → «5»)."""
    return token.rstrip(".")


def _iter_headers(lines):
    """Отдаёт (index, level, first_token) для каждой строки-заголовка."""
    for i, line in enumerate(lines):
        m = _HEADER_RE.match(line)
        if m:
            yield i, len(m.group(1)), m.group(2)


def list_sections(lines):
    """id секций, у которых первый токен похож на номер (4.0, 4.0a, 5, 7.3 …)."""
    ids = []
    for _, _, token in _iter_headers(lines):
        norm = _norm(token)
        if _ID_RE.match(norm):
            ids.append(norm)
    return ids


def extract(lines, section_id):
    """Возвращает текст секции (включая заголовок) или None, если не найдено."""
    target = _norm(section_id)
    start = None
    start_level = None
    for i, level, token in _iter_headers(lines):
        if _norm(token) == target:
            start = i
            start_level = level
            break
    if start is None:
        return None

    end = len(lines)
    for i, level, _ in _iter_headers(lines):
        if i > start and level <= start_level:
            end = i
            break
    return "".join(lines[start:end]).rstrip("\n") + "\n"


def main():
    parser = argparse.ArgumentParser(description="Печатает одну секцию subagent-prompts.md")
    parser.add_argument("section_id", nargs="?", help="id секции, напр. 4.0, 4.0a, 7.3")
    parser.add_argument("--list", action="store_true", help="перечислить доступные секции")
    parser.add_argument(
        "--file",
        default=str(PROMPTS_MD),
        help="путь к md-файлу (по умолчанию references/subagent-prompts.md)",
    )
    args = parser.parse_args()

    md_path = Path(args.file)
    if not md_path.exists():
        print(f"❌ Файл не найден: {md_path}", file=sys.stderr)
        sys.exit(1)

    lines = md_path.read_text(encoding="utf-8").splitlines(keepends=True)

    if args.list:
        for sid in list_sections(lines):
            print(sid)
        sys.exit(0)

    if not args.section_id:
        parser.error("укажи id секции или --list")

    text = extract(lines, args.section_id)
    if text is None:
        avail = ", ".join(list_sections(lines))
        print(
            f"❌ Секция '{args.section_id}' не найдена в {md_path.name}.\n"
            f"Доступные: {avail}",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.stdout.write(text)
    sys.exit(0)


if __name__ == "__main__":
    main()
