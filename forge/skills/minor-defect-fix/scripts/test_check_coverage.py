#!/usr/bin/env python3
"""Тесты check_coverage.py — фокус на fail-closed (P0-1): нет JaCoCo-отчёта = FAIL в
strict (по умолчанию) и skip=pass в --lenient. Плюс OK/LOW/MISSING/EMPTY на фикстуре XML.

Git не нужен — изменённые файлы передаём через --changed, отчёт через --report.
Запуск: python3 test_check_coverage.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_coverage.py"
CHANGED = "src/main/java/com/foo/Bar.java"  # _pkg_file → ('com/foo', 'Bar.java')
PASSED = 0
FAILED = 0


def write_report(path: Path, covered: int, missed: int, *, include: bool = True):
    """JaCoCo XML с пакетом com/foo и sourcefile Bar.java (или без него, если include=False)."""
    sf = (f'<sourcefile name="Bar.java"><counter type="LINE" covered="{covered}" '
          f'missed="{missed}"/></sourcefile>') if include else \
        '<sourcefile name="Other.java"><counter type="LINE" covered="5" missed="0"/></sourcefile>'
    path.write_text(
        f'<?xml version="1.0"?><report name="t"><package name="com/foo">{sf}</package></report>')


def run(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        parsed = {}
    return r.returncode, parsed, out


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        missing = root / "nope.xml"  # несуществующий отчёт

        # 1. Нет отчёта + strict (дефолт) → FAIL exit 2, status missing_report
        rc, j, raw = run("--root", str(root), "--changed", CHANGED,
                         "--report", str(missing), "--json")
        check("нет отчёта + strict(дефолт) → exit 2", rc == 2, f"rc={rc} {raw}")
        check("status = missing_report", j.get("status") == "missing_report", raw)

        # 2. Нет отчёта + явный --strict → FAIL
        rc, j, raw = run("--root", str(root), "--changed", CHANGED,
                         "--report", str(missing), "--strict", "--json")
        check("нет отчёта + --strict → exit 2", rc == 2, raw)

        # 3. Нет отчёта + --lenient → skip=pass exit 0
        rc, j, raw = run("--root", str(root), "--changed", CHANGED,
                         "--report", str(missing), "--lenient", "--json")
        check("нет отчёта + --lenient → exit 0", rc == 0, raw)
        check("lenient status = skipped", j.get("status") == "skipped", raw)

        # 4. Отчёт есть, покрытие >= порога → pass
        rep = root / "jacoco.xml"
        write_report(rep, covered=8, missed=2)  # 0.8
        rc, j, raw = run("--root", str(root), "--changed", CHANGED,
                         "--report", str(rep), "--threshold", "0.8", "--json")
        check("покрытие 0.8 >= 0.8 → exit 0", rc == 0 and j.get("status") == "pass", raw)

        # 5. Отчёт есть, покрытие ниже порога → FAIL
        write_report(rep, covered=5, missed=5)  # 0.5
        rc, j, raw = run("--root", str(root), "--changed", CHANGED,
                         "--report", str(rep), "--threshold", "0.8", "--json")
        check("покрытие 0.5 < 0.8 → exit 2", rc == 2 and j["files"][0]["status"] == "LOW", raw)

        # 6. Отчёт есть, но изменённый файл в нём отсутствует → MISSING → FAIL
        write_report(rep, covered=5, missed=0, include=False)
        rc, j, raw = run("--root", str(root), "--changed", CHANGED,
                         "--report", str(rep), "--threshold", "0.8", "--json")
        check("файл не в отчёте → MISSING exit 2", rc == 2 and j["files"][0]["status"] == "MISSING", raw)

        # 7. EMPTY (0 строк, marker interface) → pass (намеренно, см. coverage.md)
        write_report(rep, covered=0, missed=0)
        rc, j, raw = run("--root", str(root), "--changed", CHANGED,
                         "--report", str(rep), "--threshold", "0.8", "--json")
        check("0 строк (EMPTY) → exit 0", rc == 0 and j["files"][0]["status"] == "EMPTY", raw)

        # 8. Нет изменённых файлов → pass (даже без отчёта это про reports, но тут отчёт есть)
        write_report(rep, covered=8, missed=2)
        rc, j, raw = run("--root", str(root), "--changed", "",
                         "--report", str(rep), "--json")
        check("нет изменённых .java → exit 0", rc == 0, raw)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
