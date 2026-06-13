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

    default_compile_cmd = "./gradlew compileTestJava"
    default_test_cmd = "./gradlew test"

    compile_cmd = args.compile_cmd or cfg.get("quality", {}).get("compile_test_command") or default_compile_cmd
    test_cmd = args.test_cmd or cfg.get("quality", {}).get("test_command") or default_test_cmd

    # test-filter → --tests glob
    test_filter = args.test_filter
    full_test_cmd = test_cmd
    if test_filter and test_filter not in ("*", "*Test.java"):
        full_test_cmd = f"{test_cmd} --tests \"*{test_filter}*\""

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
        test_failed = rc_test != 0
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
                reason=f"тесты компилируются и падают (rc={rc_test}) — корректное RED-состояние.",
            )
        else:
            result.update(
                status="fail",
                verdict="fail: GREEN (tests pass)",
                reason="тесты проходят, но должны быть RED. "
                       "Проверь, что тесты тестируют ещё нереализованный код.",
            )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"RED gate: {verdict}")
        print(f"  compile: {'✓' if compile_ok else '✗'}  {compile_cmd}")
        print(f"  tests: {'✗' if test_failed else '✓'}  {full_test_cmd}")
        print(f"  reason: {reason}")

    return 0 if "pass" in verdict else 2


if __name__ == "__main__":
    raise SystemExit(main())