#!/usr/bin/env python3
"""check_coverage.py — детерминированный gate покрытия изменённых файлов (JaCoCo).

Заменяет LLM-«тестраннер, который сам парсит JaCoCo и возвращает JSON». Реализует
алгоритм из minor-defect-fix/references/coverage.md: берёт изменённые .java (git diff,
без тестов), считает line-покрытие класса по JaCoCo XML, сверяет с порогом.

Общий скрипт для minor-defect-fix (фаза тестов) и feature-pipeline (Фаза 4 / step 05-tests).

Usage:
    check_coverage.py [--root .] [--base HEAD] [--threshold 0.80] [--report XML]... [--changed "a.java b.java"] [--strict|--lenient] [--json]

fail-closed по умолчанию (--strict): если JaCoCo-отчёт не найден — покрытие НЕЛЬЗЯ проверить,
поэтому гейт FAIL, а не «тихо пропустить». Раньше отсутствие отчёта давало exit 0 (pass),
из-за чего на проекте без JaCoCo coverage-гейт молча отключался. --lenient восстанавливает
старое поведение (skip=pass) — только для осознанных исключений.

Exit: 0 = pass (или skip в --lenient), 2 = FAIL (LOW/MISSING покрытие, либо в --strict —
      JaCoCo-отчёт не найден).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_DEFAULT_REPORTS = [
    "build/reports/jacoco/test/jacocoTestReport.xml",
    "*/build/reports/jacoco/test/jacocoTestReport.xml",
    "*/*/build/reports/jacoco/test/jacocoTestReport.xml",
    "target/site/jacoco/jacoco.xml",
    "*/target/site/jacoco/jacoco.xml",
]


def _git(root: Path, *args: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines() if out.returncode == 0 else []
    except Exception:
        return []


def _changed_java(root: Path, base: str) -> list[str]:
    files = set()
    files.update(_git(root, "diff", "--name-only", base))          # working tree vs base
    files.update(_git(root, "diff", "--name-only", "--cached"))    # staged
    files.update(_git(root, "ls-files", "--others", "--exclude-standard"))  # new untracked
    return [f for f in sorted(files)
            if f.endswith(".java") and "/test/" not in f and "/src/main/" in f]


def _pkg_file(path: str) -> tuple[str, str]:
    m = re.search(r"src/main/(?:java|kotlin)/(.+)", path)
    rel = Path(m.group(1) if m else path)
    return (str(rel.parent).replace("\\", "/"), rel.name)


def _load_sourcefiles(reports: list[str]) -> dict[tuple[str, str], tuple[int, int]]:
    cov: dict[tuple[str, str], tuple[int, int]] = {}
    for rep in reports:
        try:
            root = ET.parse(rep).getroot()
        except Exception:
            continue
        for pkg in root.findall("package"):
            pkgname = pkg.get("name", "")
            for sf in pkg.findall("sourcefile"):
                line = sf.find("./counter[@type='LINE']")
                if line is None:
                    continue
                c = int(line.get("covered", 0))
                m = int(line.get("missed", 0))
                key = (pkgname, sf.get("name", ""))
                pc, pm = cov.get(key, (0, 0))
                cov[key] = (pc + c, pm + m)
    return cov


def _resolve_reports(root: Path, patterns: list[str]) -> list[str]:
    out: set[str] = set()
    for pat in patterns:
        p = Path(pat)
        if p.is_absolute():
            out.update(str(x) for x in Path(p.anchor).glob(str(p.relative_to(p.anchor))))
        else:
            out.update(str(x) for x in root.glob(pat))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic JaCoCo coverage gate for changed files.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default="HEAD", help="git ref to diff against (branch base or HEAD)")
    ap.add_argument("--threshold", type=float, default=0.80)
    ap.add_argument("--report", action="append", help="JaCoCo XML path/glob (repeatable)")
    ap.add_argument("--changed", help="explicit changed files (comma/space separated) — skips git")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--strict", dest="strict", action="store_true", default=True,
                   help="JaCoCo-отчёт не найден → FAIL (по умолчанию, fail-closed)")
    g.add_argument("--lenient", dest="strict", action="store_false",
                   help="JaCoCo-отчёт не найден → SKIP=pass (старое fail-open поведение)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    reports = _resolve_reports(root, args.report or _DEFAULT_REPORTS)

    if args.changed is not None:
        changed = [c for c in re.split(r"[,\s]+", args.changed.strip())
                   if c.endswith(".java") and "/test/" not in c]
    else:
        changed = _changed_java(root, args.base)

    if not reports:
        if args.strict:
            verdict = {"status": "missing_report",
                       "reason": "JaCoCo XML не найден — покрытие невозможно проверить (strict)",
                       "threshold": args.threshold, "changed": len(changed), "files": []}
            print(json.dumps(verdict, ensure_ascii=False, indent=2) if args.json
                  else (f"Coverage gate: ✗ FAIL (JaCoCo XML не найден, strict). "
                        f"Подключи JaCoCo или прогоняй с --lenient. Изменённых .java: {len(changed)}"))
            return 2
        verdict = {"status": "skipped",
                   "reason": "JaCoCo report not found — coverage can't be checked (lenient)",
                   "threshold": args.threshold, "changed": len(changed), "files": []}
        print(json.dumps(verdict, ensure_ascii=False, indent=2) if args.json
              else f"Coverage gate: SKIPPED (JaCoCo XML не найден, lenient). Изменённых .java: {len(changed)}")
        return 0

    cov = _load_sourcefiles(reports)
    files, status = [], "pass"
    for path in changed:
        key = _pkg_file(path)
        if key not in cov:
            files.append({"path": path, "coverage": None, "status": "MISSING"})
            status = "fail"
            continue
        c, m = cov[key]
        total = c + m
        if total == 0:
            files.append({"path": path, "coverage": None, "status": "EMPTY"})
            continue
        ratio = c / total
        st = "OK" if ratio >= args.threshold else "LOW"
        if st == "LOW":
            status = "fail"
        files.append({"path": path, "coverage": round(ratio, 3), "status": st})

    verdict = {"status": status, "threshold": args.threshold,
               "reports": reports, "changed": len(changed), "files": files}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if status == "pass" else "✗ FAIL"
        print(f"Coverage gate: {mark}  (порог {args.threshold:.0%}, изменённых .java: {len(changed)})")
        for f in files:
            cv = "  n/a" if f["coverage"] is None else f"{f['coverage']:.0%}"
            flag = {"OK": "✓", "LOW": "✗", "MISSING": "✗", "EMPTY": "·"}[f["status"]]
            print(f"  {flag} {f['status']:8} {cv:>5}  {f['path']}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
