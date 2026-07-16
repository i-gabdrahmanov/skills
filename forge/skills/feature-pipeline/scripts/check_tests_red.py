#!/usr/bin/env python3
"""check_tests_red.py — gate проверки, что для задачи есть RED-тесты (PDLC v3.5, §7 шаг 4).

RED-состояние = ПО-ТЕСТОВО (JUnit XML текущего прогона, junit_report):
- Тесты компилируются (compileTestJava проходит)
- Выполнился ≥1 тест И ВСЕ выполненные тесты падают. Exit-кода раннера недостаточно:
  один красный тест валит весь прогон, и N зелёных ВАКУУМНЫХ тестов (проходят без
  реализации) раньше проходили гейт как «RED». Поэтому прогон обязан быть заскоуплен
  на новые тест-классы (--test-filter → Gradle --tests / Maven -Dtest).

Это «ворота TDD»: проверяет, что в проекте есть тесты для task-артефактов,
и что они именно RED (падают), а не GREEN (проходят). Только при pass можно
писать реализацию.

Usage:
    check_tests_red.py <task-plan.json> --root . [--pipeline-config pipeline.json]
        [--task <id>] [--test-filter <glob>] [--compile-cmd <cmd>] [--test-cmd <cmd>] [--json]
Exit: 0 = pass (есть RED-тесты / нет задач с main-слоем)
      2 = fail (тесты не компилируются / есть зелёные / не написаны / нет JUnit-отчётов)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# junit_report co-located в pipeline-state (общий с record_gate) — тот же паттерн, что
# импорт judges_registry в pipeline_phases
_PSTATE_SCRIPTS = Path(__file__).resolve().parents[2] / "pipeline-state" / "scripts"
if str(_PSTATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PSTATE_SCRIPTS))
import junit_report

# cmd.exe (куда на Windows всегда уходит shell=True, вне зависимости от оболочки, из
# которой запущен сам python) не умеет ни в shebang, ни в "./" без расширения.
_GRADLEW = "gradlew.bat" if sys.platform == "win32" else "./gradlew"


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
    return "mvn -q test-compile" if _build_system(cfg) == "maven" else f"{_GRADLEW} compileTestJava"


def _resolve_test_cmd(cfg: dict, override: str | None) -> str:
    if override:
        return override
    t = (cfg.get("quality") or {}).get("test_command")
    if isinstance(t, str) and t.strip():
        return t.strip()
    return "mvn -q test" if _build_system(cfg) == "maven" else f"{_GRADLEW} test"


def _apply_test_filter(test_cmd: str, build_system: str, test_filter: str | None) -> str:
    """Добавляет фильтр тест-класса в синтаксисе build-системы (Gradle --tests / Maven -Dtest)."""
    if not test_filter or test_filter in ("*", "*Test.java"):
        return test_cmd
    if build_system == "maven":
        # surefire: не падать в модулях без совпадений
        return f'{test_cmd} -Dtest="*{test_filter}*" -Dsurefire.failIfNoSpecifiedTests=false'
    return f'{test_cmd} --tests "*{test_filter}*"'


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
        started = time.time()
        rc_test, test_out = _run_cmd(full_test_cmd, str(root))

        # Шаг 3: ПО-ТЕСТОВЫЙ вердикт по JUnit XML текущего прогона (-2s — гранулярность
        # mtime). Exit-код/грепы вывода недостаточны: 1 red + N green проходил как «RED»,
        # а зелёные новые тесты — вакуумные (проходят без реализации).
        t = junit_report.summarize(root, since=started - 2)
        executed = len(t["red"]) + len(t["green"])
        result["tests_ran"] = executed > 0
        result["tests_total"] = executed
        result["tests_red"] = len(t["red"])
        result["tests_green"] = len(t["green"])
        if t["green"]:
            result["green_tests"] = t["green"][:10]
        result["test_failed"] = executed > 0 and not t["green"]

        hint = ("Gradle: --tests 'FooTest'; Maven: -Dtest=FooTest "
                f"(сюда — через --test-filter, сейчас '{test_filter}')")
        red_fail = junit_report.red_reason(t, hint_scope=hint)
        if red_fail is None:
            result.update(
                status="pass",
                verdict="pass: RED (compile OK + все тесты прогона падают)",
                reason=f"компиляция OK; {len(t['red'])}/{executed} выполненных тестов "
                       f"красные, зелёных нет (rc={rc_test}) — корректное RED-состояние.",
            )
        else:
            result.update(
                status="fail",
                verdict=("fail: no tests executed" if executed == 0 and t["reports"]
                         else "fail: GREEN tests present" if t["green"]
                         else "fail: no junit reports"),
                reason=red_fail,
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