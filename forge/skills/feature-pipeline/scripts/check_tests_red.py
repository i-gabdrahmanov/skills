#!/usr/bin/env python3
"""check_tests_red.py — gate проверки, что для задачи есть RED-тесты (PDLC v3.5, §7 шаг 4).

RED-тест = unit-тест, который:
- Компилируется (compileTestJava проходит)
- Падает (test fail, exit code != 0)
- Реально выполняется (не "no tests found")

Это «ворота TDD»: проверяет, что в проекте есть тесты для task-артефактов,
и что они именно RED (падают), а не GREEN (проходят). Только при pass можно
писать реализацию.

Usage:
    check_tests_red.py <task-plan.json> --root . [--pipeline-config pipeline.json]
        [--task <id>] [--test-filter <glob>] [--compile-cmd <cmd>] [--test-cmd <cmd>] [--json]
Exit: 0 = pass (есть RED-тесты / нет задач с main-слоем)
      2 = fail (тесты не компилируются / проходят / не написаны)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _load_json(p: str | Path) -> dict | None:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_cmd(cmd: str, cwd: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except Exception as e:
        return 1, str(e)


# ── Build-система: команды компиляции/тестов и синтаксис фильтра (Gradle/Maven) ─────────
def _build_system(cfg: dict) -> str:
    bs = (cfg.get("project") or {}).get("build_system")
    return bs if bs in ("gradle", "maven") else "gradle"


def _resolve_compile_test_cmd(cfg: dict, override: str | None) -> str:
    """Команда компиляции ТЕСТОВ. override > quality.compile_test_command > дефолт по build-системе."""
    if override:
        return override
    c = (cfg.get("quality") or {}).get("compile_test_command")
    if isinstance(c, str) and c.strip():
        return c.strip()
    return "mvn -q test-compile" if _build_system(cfg) == "maven" else "./gradlew compileTestJava"


def _resolve_test_cmd(cfg: dict, override: str | None) -> str:
    if override:
        return override
    t = (cfg.get("quality") or {}).get("test_command")
    if isinstance(t, str) and t.strip():
        return t.strip()
    return "mvn -q test" if _build_system(cfg) == "maven" else "./gradlew test"


def _apply_test_filter(test_cmd: str, build_system: str, test_filter: str | None) -> str:
    """Добавляет фильтр тест-класса в синтаксисе build-системы (Gradle --tests / Maven -Dtest)."""
    if not test_filter or test_filter in ("*", "*Test.java"):
        return test_cmd
    if build_system == "maven":
        # surefire: не падать в модулях без совпадений
        return f'{test_cmd} -Dtest="*{test_filter}*" -Dsurefire.failIfNoSpecifiedTests=false'
    return f'{test_cmd} --tests "*{test_filter}*"'


def _has_red_tests(output: str) -> tuple[bool, str]:
    """RED-детект по ВЫВОДУ тестов — build-system-агностично (не только exit-код).

    RED = есть провалившиеся тесты / сборка упала / вывод пуст (нет доказательства GREEN).
    GREEN = тесты прошли без провалов. Ловит и Gradle ('BUILD FAILED'), и Maven surefire
    ('Failures: N'), где exit-коды/формат отличаются.
    """
    low = (output or "").lower()
    if not low.strip():
        return True, "пустой вывод — нет доказательства GREEN, считаем RED"
    # явный ноль провалов (Maven surefire success) / успешная сборка Gradle
    zero_fail = re.search(r"failures?:\s*0\b", low) and re.search(r"errors?:\s*0\b", low)
    if (zero_fail or "build successful" in low) and not ("build failed" in low or "tests failed" in low):
        return False, "тесты прошли без провалов (GREEN)"
    if any(m in low for m in ("failed", "failure", "build failed", "failing test", "<<< failure")):
        return True, "вывод содержит признаки провала тестов (RED)"
    if "passed" in low:
        return False, "тесты прошли (GREEN)"
    return True, "неоднозначный вывод — консервативно RED"


def _extract_tasks(plan: dict, task_filter: str | None = None) -> list[dict]:
    tasks = plan.get("tasks", [])
    if task_filter:
        tasks = [t for t in tasks if t.get("id") == task_filter]
    return [t for t in tasks if "main" in str(t.get("layers", "")) or
            any("main/java" in a or "src/main" in a for a in t.get("artifacts", []))]


def main() -> int:
    ap = argparse.ArgumentParser(description="TDD RED-test gate.")
    ap.add_argument("plan", help="task-plan.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--pipeline-config", help="project/ground/pipeline.json")
    ap.add_argument("--task", help="проверить только одну задачу")
    ap.add_argument("--test-filter", help="glob-фильтр тестового класса (например '*T1*')")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--compile-cmd", help="команда компиляции тестов (дефолт: ./gradlew compileTestJava)")
    ap.add_argument("--test-cmd", help="команда запуска тестов (дефолт: ./gradlew test)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    plan = _load_json(args.plan) or {}
    cfg = _load_json(args.pipeline_config or "") or {}

    # Команды компиляции/тестов и синтаксис фильтра — по build-системе (Gradle/Maven)
    build_system = _build_system(cfg)
    compile_cmd = _resolve_compile_test_cmd(cfg, args.compile_cmd)
    test_cmd = _resolve_test_cmd(cfg, args.test_cmd)
    test_filter = args.test_filter
    full_test_cmd = _apply_test_filter(test_cmd, build_system, test_filter)

    tasks = _extract_tasks(plan, args.task)
    if not tasks:
        print(f"RED gate: PASS (нет задач с main-слоем), filter={args.task}")
        return 0

    # Шаг 1: компиляция тестов (обязательно ДО тестов — ранний выход при ошибке)
    rc_compile, compile_out = _run_cmd(compile_cmd, str(root))
    compile_ok = rc_compile == 0

    result = {
        "status": "unknown",
        "compile_ok": compile_ok,
        "test_failed": None,
        "tests_ran": None,
        "tasks_checked": [t.get("id") for t in tasks],
        "compile_cmd": compile_cmd,
        "test_cmd": full_test_cmd,
    }

    if not compile_ok:
        result.update(
            status="fail",
            verdict="fail: compilation error",
            reason=f"compileTestJava error (rc={rc_compile}). "
                   f"RED-тесты должны компилироваться — compile FAIL говорит о неверных "
                   f"сигнатурах или импортах, а не о красном состоянии тестов.",
        )
    else:
        # Шаг 2: запуск тестов (только если compile OK)
        rc_test, test_out = _run_cmd(full_test_cmd, str(root))
        # RED-состояние определяем по exit-коду И по выводу (build-system-агностично):
        # на Maven exit-код/формат отличаются от Gradle.
        red_by_output, red_reason = _has_red_tests(test_out)
        test_failed = (rc_test != 0) or red_by_output
        result["test_failed"] = test_failed

        # Шаг 3: проверка, что тесты действительно выполнялись (не "no tests found")
        tests_ran = True
        no_tests_patterns = ["no tests found", "no test(s) found", "0 tests found", "0 test(s) found"]
        for pat in no_tests_patterns:
            if pat in test_out.lower():
                tests_ran = False
                break
        result["tests_ran"] = tests_ran

        # Вердикт (только после успешной компиляции)
        if not tests_ran:
            result.update(
                status="fail",
                verdict="fail: no tests executed",
                reason=f"no tests matched filter '{test_filter}'. "
                       f"RED-тесты должны запускаться и падать.",
            )
        elif test_failed:
            result.update(
                status="pass",
                verdict="pass: RED (compile OK + tests fail)",
                reason=f"тесты компилируются и падают (rc={rc_test}; {red_reason}) — корректное RED-состояние.",
            )
        else:
            result.update(
                status="fail",
                verdict="fail: GREEN (tests pass)",
                reason="тесты проходят, но должны быть RED. "
                       "Проверь, что тесты тестируют ещё нереализованный код.",
            )

    verdict = result.get("verdict", result.get("status", "fail"))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"RED gate: {verdict}")
        print(f"  compile: {'✓' if compile_ok else '✗'}  {compile_cmd}")
        print(f"  tests: {'✗' if result.get('test_failed') else '✓'}  {full_test_cmd}")
        print(f"  reason: {result.get('reason', '')}")

    return 0 if "pass" in verdict else 2


if __name__ == "__main__":
    raise SystemExit(main())