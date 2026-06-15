#!/usr/bin/env python3
"""run_all_tests.py — единый прогон всех test_*.py по всем скиллам (CI-вход).

Раньше тесты жили только в feature-pipeline и запускались вручную. Этот раннер
дискаверит test_*.py во всех skills/*/scripts и гоняет их текущим интерпретатором.

Usage:
    python3 skills/run_all_tests.py [--skill <name>] [-q]
Требует Python 3.10+ (скрипты используют PEP 604).
Exit: 0 — все зелёные, 1 — есть упавшие.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # …/skills


def discover(skill: str | None) -> list[Path]:
    pattern = f"{skill}/**/test_*.py" if skill else "*/**/test_*.py"
    return sorted(ROOT.glob(pattern))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill", default=None, help="ограничить одним скиллом")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    tests = discover(args.skill)
    if not tests:
        print("Тесты не найдены")
        return 0

    passed, failed = [], []
    for t in tests:
        r = subprocess.run([sys.executable, str(t)], capture_output=True, text=True)
        rel = t.relative_to(ROOT)
        if r.returncode == 0:
            passed.append(rel)
            if not args.quiet:
                print(f"  ✅ {rel}")
        else:
            failed.append(rel)
            print(f"  ❌ {rel}")
            tail = (r.stderr or r.stdout).strip().splitlines()[-4:]
            for ln in tail:
                print(f"       {ln}")

    print(f"\n=== PASS={len(passed)} FAIL={len(failed)} (всего {len(tests)}) ===")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
