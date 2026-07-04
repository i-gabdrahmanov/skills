#!/usr/bin/env python3
"""check_brd_doc.py — ДЕТЕРМИНИРОВАННЫЙ gate документа BRD (Thrust 3: судьи → детерминизм).

Зачем. Гейт BRD был только LLM-судьёй (brd-judge, kind=hybrid) — он штамповал «PASS» на
мусорном/кодовом «BRD» («это не БТ, а какая-то фигня»). LLM-вердикт не должен закрывать шаг сам;
хард-гейт — детерминированная структурная проверка, brd-judge остаётся advisory.

BRD = язык БИЗНЕСА («что» и «зачем»), не «как». Проверяет сам `brd.md`:
  1. Файл существует и содержателен (не заглушка/пустышка).
  2. Есть обязательные бизнес-секции (контекст, цели, требования, критерии).
  3. НЕТ утечки реализации: код-блоки java/kotlin/sql/xml, сигнатуры классов/аннотаций,
     SQL DDL, Liquibase — в BRD запрещены (это уровень tech-design). Срабатывание → БЛОК.

Usage:
    check_brd_doc.py <brd.md> [--json]
Exit: 0 = pass, 2 = не БТ / чего-то не хватает / есть код.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Каждая группа = набор синонимов; хотя бы один должен встретиться (иначе секции нет).
REQUIRED_SECTION_GROUPS = [
    ("контекст", "предпосылк", "проблем", "предыстор"),
    ("цел", "задач бизнеса", "ожидаемый результат", "зачем"),
    ("требовани", "объём", "scope", "функци", "сценари"),
    ("критери", "приёмк", "успех", "acceptance", "метрик"),
]

# Утечка реализации в BRD (код/классы/SQL) — БЛОК: это признак «не БТ, а листинг».
_CODE_FENCE = re.compile(r"```(?:java|kotlin|diff|sql|xml|gradle|properties)\b", re.IGNORECASE)
_CODE_SIGNS = re.compile(
    r"(?m)^\s*(?:import\s+[\w.]+;|package\s+[\w.]+;|@(?:RestController|Service|Entity|Repository"
    r"|Component|Autowired|GetMapping|PostMapping|PutMapping|DeleteMapping|Table|Column)\b"
    r"|public\s+(?:class|interface|enum)\s|private\s+\w+\s+\w+\s*[;=])"
)
_SQL_DDL = re.compile(r"(?i)\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX|SEQUENCE)\b|\bchangeSet\b")

_MIN_MEANINGFUL_CHARS = 400  # BRD короче — заглушка/мусор, не документ требований


def check(text_raw: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    text = text_raw.lower()

    meaningful = len(re.sub(r"\s+", "", text_raw))
    if meaningful < _MIN_MEANINGFUL_CHARS:
        errors.append(f"BRD слишком короткий/пустой ({meaningful} знаков без пробелов < "
                      f"{_MIN_MEANINGFUL_CHARS}) — это заглушка, а не документ требований")

    for group in REQUIRED_SECTION_GROUPS:
        if not any(syn in text for syn in group):
            errors.append(f"в brd.md нет обязательной бизнес-секции (одно из: {', '.join(group)})")

    if _CODE_FENCE.search(text_raw):
        errors.append("в brd.md есть код-блок (```java/kotlin/sql/xml) — BRD на языке бизнеса, "
                      "код/листинги = уровень tech-design; убери")
    if _CODE_SIGNS.search(text_raw):
        errors.append("в brd.md есть сигнатуры кода (import/package/@-аннотации/class) — "
                      "это не бизнес-требования; перенеси в tech-design")
    if _SQL_DDL.search(text_raw):
        errors.append("в brd.md есть SQL DDL / Liquibase changeSet — схему БД описывает "
                      "tech-design, BRD — только бизнес-смысл данных")
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="Strict deterministic BRD document gate.")
    ap.add_argument("brd", help="путь к brd.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    brd_path = Path(args.brd)
    if not brd_path.exists():
        errors, warnings = [f"нет BRD-документа: {brd_path}"], []
    else:
        errors, warnings = check(brd_path.read_text(encoding="utf-8", errors="replace"))

    status = "pass" if not errors else "fail"
    verdict = {"status": status, "brd": str(brd_path), "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"BRD doc check: {'✓ PASS' if status == 'pass' else '✗ FAIL'}")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warnings:
            print(f"  · warn: {w}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
