#!/usr/bin/env python3
"""check_tautological_tests.py — статический детектор тавтологичных/пустых тестов (P2-10).

RED→GREEN-гейты (check_tests_red / red-judge) ловят `assertTrue(true)` исполнением — но только
если есть рабочий build (gradlew/mvn), а tdd-guard (всегда-он) смотрит лишь статус шага, не
содержимое теста. Этот гейт — детерминированный СТАТИЧЕСКИЙ флор: разбирает @Test-методы и ловит
тесты, которые не доказывают ничего: пустое тело, тавтологичные ассерты (`assertTrue(true)`,
`assertEquals(x, x)`), отсутствие ассертов/verify вовсе.

Консервативен (низкий false-positive): пустое тело и тавтология — error; «есть код, но не видно
ассерта/verify» — warning (может быть делегирование в хелпер). `--strict` ужесточает warnings.

Usage:
    check_tautological_tests.py [--root .] [--base HEAD] [--changed "a.java b.java"]
        [--strict] [--json]
Exit: 0 = pass (или только warnings без --strict), 2 = fail (тавтология/пустой тест / --strict).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_TEST_ANNO = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory)\b")
# сигнатура метода после аннотации: ... name(...) {
_METHOD_SIG = re.compile(r"(?:public|protected|private|\s)*\s*[\w<>\[\],\s.?]+\s+(\w+)\s*\([^)]*\)\s*(?:throws[\w\s,.]+)?\{")

# Словарь «доказательных» вызовов (ассерты/verify/исключения/AssertJ/Hamcrest/Mockito).
_ASSERTION_VOCAB = re.compile(
    r"\bassert\w*\s*\(|\bassertThat\s*\(|\bverify\w*\s*\(|\bfail\s*\(|\bexpect\w*\s*\(|"
    r"\.is(?:Equal|True|False|Null|NotNull|Not|Same|Present|Empty|GreaterThan|LessThan)|"
    r"\.contains|\.hasSize|\.has\w+|\bthen\s*\(|\bshould\w*\s*\(|\bassertj\b|"
    r"\bverifyNo(?:More)?Interactions\s*\(|\bawait\s*\(\)|\.satisfies\s*\(",
    re.I,
)
# Вызов хелпера-проверки (имя содержит assert/verify/check/expect/ensure) — не FP'им как «нет ассерта».
_HELPER_CALL = re.compile(r"\b\w*(?:assert|verify|check|expect|ensure)\w*\s*\(", re.I)

# Тавтологии (тело нормализовано по пробелам).
_TAUTOLOGIES = [
    (re.compile(r"assertTrue\(\s*true\s*\)"), "assertTrue(true)"),
    (re.compile(r"assertFalse\(\s*false\s*\)"), "assertFalse(false)"),
    (re.compile(r"assertThat\(\s*true\s*\)\.isTrue\(\)"), "assertThat(true).isTrue()"),
    (re.compile(r"assertThat\(\s*false\s*\)\.isFalse\(\)"), "assertThat(false).isFalse()"),
    (re.compile(r"assertEquals\(\s*(true|false|null|-?\d+|\"[^\"]*\")\s*,\s*\1\s*\)"),
     "assertEquals(x, x) — одинаковые литералы"),
    (re.compile(r"assertSame\(\s*(\w+)\s*,\s*\1\s*\)"), "assertSame(x, x)"),
    (re.compile(r"\bassert\s+true\s*;"), "assert true;"),
]


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    return src


def _extract_test_methods(content: str) -> list:
    """[(name, body)] для @Test-методов. Тело — по балансу скобок от сигнатуры."""
    src = _strip_comments(content or "")
    out = []
    for m in _TEST_ANNO.finditer(src):
        sig = _METHOD_SIG.search(src, m.end())
        if not sig:
            continue
        # тело от '{' по балансу
        i = sig.end() - 1  # позиция '{'
        depth, j = 0, i
        while j < len(src):
            c = src[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = src[i + 1:j]
        out.append((sig.group(1), body))
    return out


def analyze_test_content(path: str, content: str) -> list:
    """Нарушения тавтологичности в одном тест-файле: {file, method, rule, severity, detail}."""
    norm = path.replace("\\", "/")
    if not norm.endswith(".java") or "/test/" not in norm:
        return []
    v = []
    for name, body in _extract_test_methods(content):
        flat = re.sub(r"\s+", " ", body).strip()
        if not flat or flat in ("{}",):
            v.append({"file": norm, "method": name, "rule": "empty-test",
                      "severity": "error", "detail": "пустое тело @Test — ничего не проверяет"})
            continue
        taut = next((label for rx, label in _TAUTOLOGIES if rx.search(flat)), None)
        if taut:
            v.append({"file": norm, "method": name, "rule": "tautological-assert",
                      "severity": "error", "detail": f"тавтологичный ассерт: {taut}"})
            continue
        if not _ASSERTION_VOCAB.search(body) and not _HELPER_CALL.search(body):
            v.append({"file": norm, "method": name, "rule": "no-assertion",
                      "severity": "warning",
                      "detail": "в тесте не видно ассерта/verify (возможно, ничего не проверяет)"})
    return v


def analyze(files: dict) -> dict:
    violations = []
    for path, content in files.items():
        violations.extend(analyze_test_content(path, content))
    errors = [x for x in violations if x["severity"] == "error"]
    warnings = [x for x in violations if x["severity"] == "warning"]
    return {"status": "fail" if errors else "pass",
            "checked": len(files), "violations": violations,
            "counts": {"error": len(errors), "warning": len(warnings)}}


def _git(root: Path, *args: str) -> list:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines() if out.returncode == 0 else []
    except Exception:
        return []


def _changed_tests(root: Path, base: str) -> list:
    files = set()
    files.update(_git(root, "diff", "--name-only", base))
    files.update(_git(root, "diff", "--name-only", "--cached"))
    files.update(_git(root, "ls-files", "--others", "--exclude-standard"))
    return [f for f in sorted(files) if f.endswith(".java") and "/test/" in f]


def main() -> int:
    ap = argparse.ArgumentParser(description="Static tautological/empty-test detector.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--changed", help="явный список тест-.java (через ,/пробел) — минует git")
    ap.add_argument("--strict", action="store_true", help="warnings тоже валят (exit 2)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if args.changed is not None:
        paths = [c for c in re.split(r"[,\s]+", args.changed.strip())
                 if c.endswith(".java") and "/test/" in c]
    else:
        paths = _changed_tests(root, args.base)

    files = {}
    for p in paths:
        fp = (root / p) if not Path(p).is_absolute() else Path(p)
        try:
            files[p] = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            files[p] = ""

    verdict = analyze(files)
    failed = verdict["counts"]["error"] > 0 or (args.strict and verdict["counts"]["warning"] > 0)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✗ FAIL" if failed else ("✓ PASS" if not verdict["violations"] else "✓ PASS (warnings)")
        c = verdict["counts"]
        print(f"Tautology gate: {mark}  (тест-файлов {verdict['checked']}, "
              f"ошибок {c['error']}, предупр. {c['warning']})")
        for x in verdict["violations"]:
            flag = "✗" if x["severity"] == "error" else "⚠"
            print(f"  {flag} [{x['rule']}] {x['file']}#{x['method']}: {x['detail']}")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
