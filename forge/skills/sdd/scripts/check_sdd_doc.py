#!/usr/bin/env python3
"""check_sdd_doc.py — gate документа SDD (PDLC v3.5, стр. 66).

Проверяет ТОЛЬКО сам `sdd.md` (фаза 02-sdd, task-plan ещё нет):
  1. Файл существует.
  2. Есть все обязательные секции.
  3. Есть хотя бы один сценарий Given-When-Then.

Линковку task-plan ↔ sdd.md (acceptance + sdd_ref у каждой задачи) проверяет
`tech-design/scripts/check_sdd.py` уже на фазе 02-design.

Usage:
    check_sdd_doc.py <sdd.md> [--json]
Exit: 0 = pass, 2 = чего-то не хватает.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REQUIRED_SECTIONS = [
    "бизнес-контекст", "функциональные требования", "нефункциональные",
    "api", "модель данных", "критерии приёмки",
]
_GWT = re.compile(r"(?i)given.*when.*then")

# Признаки утечки реализации в SDD (спека = «что», а не «как»). Срабатывание → warning:
# SDD должен описывать поведение словами, а не листингами кода/миграций.
_CODE_FENCE = re.compile(r"```(?:java|diff|kotlin|sql|xml)\b", re.IGNORECASE)
_CODE_SIGNS = re.compile(
    r"(?m)^\s*(?:import\s+[\w.]+;|@(?:RestController|Service|Entity|Repository|Component"
    r"|GetMapping|PostMapping|PutMapping|DeleteMapping)\b|public\s+(?:class|interface|enum)\s)"
)
_LIQUIBASE = re.compile(r"(?i)\b(?:changeSet|databaseChangeLog|liquibase)\b")


def main() -> int:
    ap = argparse.ArgumentParser(description="Strict SDD document gate.")
    ap.add_argument("sdd", help="путь к sdd.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sdd_path = Path(args.sdd)
    errors: list[str] = []
    warnings: list[str] = []

    if not sdd_path.exists():
        errors.append(f"нет SDD-документа: {sdd_path}")
    else:
        raw = sdd_path.read_text(encoding="utf-8", errors="replace")
        text = raw.lower()
        for sec in REQUIRED_SECTIONS:
            if sec not in text:
                errors.append(f"в sdd.md нет обязательной секции: «{sec}»")
        if not _GWT.search(raw):
            errors.append("в sdd.md не найден ни один сценарий Given-When-Then")
        # SDD — «что», не «как»: код в спеке = утечка реализации (обычно из фазы Document).
        # Однозначный код (fenced-блок java/diff/sql, сигнатуры) — БЛОК (errors), чтобы
        # загрязнённый SDD не проходил гейт зелёным. Мягкое упоминание Liquibase — warning.
        if _CODE_FENCE.search(raw):
            errors.append("в sdd.md есть код-блок (```java/diff/sql/...) — спека описывает "
                          "поведение словами, а не листингом; убери код (он уровень tech-design)")
        if _CODE_SIGNS.search(raw):
            errors.append("в sdd.md есть сигнатуры кода (import/@RestController/public class) — "
                          "это уровень tech-design, убери из спеки")
        if _LIQUIBASE.search(raw):
            warnings.append("в sdd.md упомянут Liquibase changeset — миграции описывай на уровне "
                            "«какие таблицы/поля», детали changeset — в tech-design")

    status = "pass" if not errors else "fail"
    verdict = {"status": status, "sdd": str(sdd_path),
               "errors": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"SDD doc check: {'✓ PASS' if status == 'pass' else '✗ FAIL'}")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warnings:
            print(f"  · warn: {w}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
