#!/usr/bin/env python3
"""check_adr.py — детерминированный валидатор ADR (Architecture Decision Records, MADR).

Проверяет каталог `adr/` мастер-репо:
  1. Состав каждого ADR: Status + Context + Decision + Consequences.
  2. Status ∈ {proposed, accepted, rejected, superseded, deprecated}.
  3. При Status = superseded|deprecated — обязателен резолвящийся `Superseded-by: ADR-NNNN`.
  4. Имя файла `NNNN-slug.md`, уникальность ID, резолв ссылок Supersedes/Superseded-by.
  5. (--refs) Референс-целостность: каждый `ADR-NNNN`, упомянутый во внешних артефактах
     (tech-design.md/sdd.md/architecture-policy.json), существует в каталоге.

ADR — «почему так решили» (rationale+статус); из кода не выводится (это не grounding).

Usage:
    check_adr.py <adr_dir> [--refs <file|dir> ...] [--json]
Exit: 0 = ок (в т.ч. пустой/нет каталога), 2 = есть проблемы.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_STATUSES = {"proposed", "accepted", "rejected", "superseded", "deprecated"}
_NEED_SUPERSEDER = {"superseded", "deprecated"}

_FNAME = re.compile(r"^(\d{3,})-[a-z0-9][a-z0-9-]*\.md$")
_STATUS = re.compile(r"(?im)^\*\*Status:\*\*\s*([A-Za-zА-Яа-я-]+)")
_TITLE_ID = re.compile(r"(?im)^#\s*ADR-?0*(\d+)\b")
_SUPERSEDED_BY = re.compile(r"(?im)^\*\*Superseded-by:\*\*\s*ADR-?0*(\d+)")
_SUPERSEDES = re.compile(r"(?im)^\*\*Supersedes:\*\*\s*ADR-?0*(\d+)")
_ADR_REF = re.compile(r"ADR-0*(\d+)")

_SEC = {
    "Context": ["context", "контекст"],
    "Decision": ["decision", "решение"],
    "Consequences": ["consequences", "последствия"],
}
# Разделы — только H2+ (заголовок ADR = H1 «# ADR-NNNN: …», его исключаем, чтобы слова
# из тайтла вроде «решение» не засчитывались как раздел Decision).
_SEC_HEADING = re.compile(r"(?im)^\s{0,3}#{2,6}\s*(.+)$")


def _has_section(text: str, markers: list[str]) -> bool:
    for m in _SEC_HEADING.finditer(text):
        head = m.group(1).lower()
        if any(mk in head for mk in markers):
            return True
    return False


def _adr_files(adr_dir: Path) -> list[Path]:
    if not adr_dir.exists():
        return []
    return sorted(p for p in adr_dir.glob("*.md")
                  if p.name[0].isdigit())  # ADR-файлы начинаются с номера; README и т.п. — нет


def _ref_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            out.extend(sorted(pp.glob("*.md")))
        elif pp.exists():
            out.append(pp)
    return out


def check(adr_dir: Path, refs: list[str]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    files = _adr_files(adr_dir)
    ids: dict[int, Path] = {}

    for f in files:
        fm = _FNAME.match(f.name)
        if not fm:
            errors.append(f"имя ADR не по формату NNNN-slug.md: {f.name}")
            file_id = None
        else:
            file_id = int(fm.group(1))
            if file_id in ids:
                errors.append(f"дублирующийся ID ADR-{file_id:04d}: {f.name} и {ids[file_id].name}")
            else:
                ids[file_id] = f

        text = f.read_text(encoding="utf-8", errors="replace")

        # ID в заголовке должен совпадать с именем файла (если оба есть)
        tid = _TITLE_ID.search(text)
        if fm and tid and int(tid.group(1)) != file_id:
            warnings.append(f"{f.name}: ID в заголовке (ADR-{int(tid.group(1))}) ≠ имени файла")

        m = _STATUS.search(text)
        status = m.group(1).lower() if m else None
        if not status:
            errors.append(f"{f.name}: нет **Status:**")
        elif status not in _STATUSES:
            errors.append(f"{f.name}: недопустимый Status «{status}» (ожидается {sorted(_STATUSES)})")

        for name, markers in _SEC.items():
            if not _has_section(text, markers):
                errors.append(f"{f.name}: нет обязательного раздела «{name}»")

        if status in _NEED_SUPERSEDER:
            sb = _SUPERSEDED_BY.search(text)
            if not sb:
                errors.append(f"{f.name}: Status={status}, но нет `Superseded-by: ADR-NNNN`")

    # Резолв внутренних ссылок Supersedes/Superseded-by
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for rx, label in ((_SUPERSEDED_BY, "Superseded-by"), (_SUPERSEDES, "Supersedes")):
            for mm in rx.finditer(text):
                ref = int(mm.group(1))
                if ref not in ids:
                    errors.append(f"{f.name}: {label} ссылается на несуществующий ADR-{ref:04d}")

    # Референс-целостность из внешних артефактов
    ref_hits = 0
    for rf in _ref_files(refs):
        rtext = rf.read_text(encoding="utf-8", errors="replace")
        for mm in _ADR_REF.finditer(rtext):
            ref_hits += 1
            ref = int(mm.group(1))
            if ref not in ids:
                errors.append(f"{rf.name}: ссылка ADR-{ref:04d} не резолвится в каталоге ADR")

    status = "pass" if not errors else "fail"
    return {"status": status, "adr_dir": str(adr_dir), "adr_count": len(files),
            "refs_scanned": ref_hits, "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="ADR (MADR) composition & reference gate.")
    ap.add_argument("adr_dir", help="каталог adr/")
    ap.add_argument("--refs", action="append", default=[],
                    help="файл/каталог, где искать ссылки ADR-NNNN (повторяемый)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    verdict = check(Path(args.adr_dir), args.refs)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"ADR check: {'✓ PASS' if verdict['status'] == 'pass' else '✗ FAIL'} "
              f"({verdict['adr_count']} ADR, ссылок проверено {verdict['refs_scanned']})")
        for e in verdict["errors"]:
            print(f"  ✗ {e}")
        for w in verdict["warnings"]:
            print(f"  · warn: {w}")
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
