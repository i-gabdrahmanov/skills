#!/usr/bin/env python3
"""run_all_tests.py — единый прогон всех test_*.py по всем скиллам (CI-вход).

Раньше тесты жили только в feature-pipeline и запускались вручную. Этот раннер
дискаверит test_*.py во всех skills/*/scripts И в hooks/ (hooks/test_*.py +
hooks/tests/test_*.py) и гоняет их текущим интерпретатором. Юнит-тесты хуков
раньше не гонялись ни одним раннером — каталог hooks/tests/ молча гнил.

Usage:
    python3 skills/run_all_tests.py [--skill <name>] [-q]
    python3 skills/run_all_tests.py --skill hooks   # только тесты хуков
Требует Python 3.10+ (скрипты используют PEP 604).
Exit: 0 — все зелёные, 1 — есть упавшие.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # …/skills
REPO = ROOT.parent                       # корень репо (родитель skills/ и hooks/)
HOOKS = REPO / "hooks"                    # тесты хуков живут вне skills/


def _hooks_tests() -> list[Path]:
    """Юнит-тесты хуков: hooks/test_*.py + hooks/tests/test_*.py.
    Раньше они не гонялись ни одним раннером — каталог hooks/tests/ молча гнил."""
    if not HOOKS.is_dir():
        return []
    found = list(HOOKS.glob("test_*.py")) + list(HOOKS.glob("tests/test_*.py"))
    return sorted(found)


def discover(skill: str | None) -> list[Path]:
    if skill == "hooks":
        return _hooks_tests()
    skills_tests = sorted(ROOT.glob(f"{skill}/**/test_*.py" if skill else "*/**/test_*.py"))
    if skill:
        return skills_tests
    return skills_tests + _hooks_tests()


def _pollution_snapshot() -> dict[str, int]:
    """Гард изоляции тестов: runtime-каталоги репо (ai-logs-archive/, ground/) не должны
    меняться прогоном тестов. Прецедент: смоук log-agent с пустым stdin без tmp-cwd и
    GIGACODE_AILOG_ARCHIVE дописывал all-null записи в боевой кросс-прогонный архив."""
    snap: dict[str, int] = {}
    for base in (REPO / "ai-logs-archive", REPO / "ground"):
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    snap[str(p)] = p.stat().st_size
    return snap


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill", default=None, help="ограничить одним скиллом")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    tests = discover(args.skill)
    if not tests:
        print("Тесты не найдены")
        return 0

    pollution_before = _pollution_snapshot()
    passed, failed = [], []
    for t in tests:
        r = subprocess.run([sys.executable, str(t)], capture_output=True, text=True)
        rel = t.relative_to(REPO)
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

    pollution_after = _pollution_snapshot()
    if pollution_after != pollution_before:
        changed = sorted(set(pollution_after.items()) ^ set(pollution_before.items()))
        print("\n❌ ИЗОЛЯЦИЯ НАРУШЕНА: тесты изменили runtime-каталоги репо "
              "(ai-logs-archive/ или ground/):")
        for path, size in changed:
            print(f"   {path} ({size}b)")
        print("   Тест обязан писать в tmp (cwd=tmp, GIGACODE_AILOG_ARCHIVE=tmp).")
        failed.append(Path("pollution-guard"))

    print(f"\n=== PASS={len(passed)} FAIL={len(failed)} (всего {len(tests)}) ===")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
