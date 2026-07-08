#!/usr/bin/env python3
"""
build_evals_from_design.py — генерирует eval-plan.json из task-plan.json.

Evals пишутся ДО кода (фаза Design) и форсят Eval-Driven Development:
агент не может закрыть build-шаг задачи, пока все её eval'ы не пройдены.

Для каждой задачи в task-plan генерируются типовые evals:
  - compile:  компиляция проекта (бинарный gate, exit-код)
  - coverage: проверка JaCoCo покрытия (check_coverage.py --strict: нет отчёта = FAIL)
  - test_pass: вся тест-сюита зелёная — регресс-чекпоинт (бинарный, exit-код, НЕ «% задачи»)

Команды compile/test берутся из pipeline.json (project.build_system, quality.test_command) —
поэтому одинаково работает на Gradle и Maven. Порог покрытия — из quality.coverage_threshold.

Usage:
    build_evals_from_design.py <task-plan.json> \\
        --coverage-script <path/to/check_coverage.py> \\
        [--pipeline-config pipeline.json] \\
        [--out eval-plan.json] \\
        [--coverage-threshold 0.80] \\
        [--build-cmd "<команда компиляции>"] \\
        [--test-cmd "<команда тестов>"] \\
        [--json]

Exit: 0 (всегда — генерация не блокируется; блокирует использование eval-guard).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = "feature-pipeline/eval-plan@1"

# Абсолютный путь к интерпретатору, которым запущен сам генератор (кроссплатформенно —
# на Windows часто нет python3 в PATH, только python.exe/py.exe; run_pending_evals.py
# исполняет "command" через shell=True, поэтому литеральный "python3" там ненадёжен).
PYTHON_CMD = f'"{sys.executable}"' if sys.executable and " " in sys.executable else (sys.executable or "python3")

DEFAULT_COVERAGE_THRESHOLD = 0.80

# Обёртка Gradle платформенно разная: "./gradlew" — POSIX-скрипт с shebang (bash его
# исполняет через "./"), на Windows нужен gradlew.bat — cmd.exe (куда уходит shell=True
# в run_pending_evals.py/check_build.py вне зависимости от того, из какой оболочки
# запущен сам python) не умеет ни в shebang, ни в "./" без расширения.
_GRADLEW = "gradlew.bat" if sys.platform == "win32" else "./gradlew"

# Команды компиляции/тестов резолвятся по build-системе из pipeline.json (Maven-корректно).
# compile — намеренно лёгкая (только компиляция, не полный build), чтобы не дублировать test_pass.
GRADLE_COMPILE = f"{_GRADLEW} compileJava"
GRADLE_TEST = f"{_GRADLEW} test"
MAVEN_COMPILE = "mvn -q compile"
MAVEN_TEST = "mvn -q test"
# Дефолты для обратной совместимости (используются как fallback, если pipeline.json не задан).
DEFAULT_BUILD_CMD = GRADLE_COMPILE
DEFAULT_TEST_CMD = GRADLE_TEST


def _load_json(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _build_system(pipeline_config: dict | None) -> str:
    """gradle | maven из pipeline.json project.build_system (дефолт gradle)."""
    if pipeline_config:
        bs = pipeline_config.get("project", {}).get("build_system")
        if bs in ("gradle", "maven"):
            return bs
    return "gradle"


def _resolve_compile_cmd(pipeline_config: dict | None, override: str | None) -> str:
    """Лёгкая команда компиляции по build-системе (НЕ полный build — чтобы compile-eval
    был быстрым и не дублировал test_pass). quality.build_command тут не берём: там
    `clean build`/`clean verify`, который гоняет и тесты."""
    if override:
        return override
    return MAVEN_COMPILE if _build_system(pipeline_config) == "maven" else GRADLE_COMPILE


def _resolve_test_cmd(pipeline_config: dict | None, override: str | None) -> str:
    """Команда прогона тестов. Приоритет: override > quality.test_command (его init
    пишет уже build-system-корректным, с генерацией JaCoCo) > дефолт по build-системе."""
    if override:
        return override
    if pipeline_config:
        tc = pipeline_config.get("quality", {}).get("test_command")
        if isinstance(tc, str) and tc.strip():
            return tc.strip()
    return MAVEN_TEST if _build_system(pipeline_config) == "maven" else GRADLE_TEST


def build_evals(
    task_plan: dict,
    pipeline_config: dict | None = None,
    coverage_script: str = "check_coverage.py",
    coverage_helper: str | None = None,
    coverage_threshold: float | None = None,
    build_cmd: str | None = None,
    test_cmd: str | None = None,
) -> dict:
    """Генерирует eval-plan из task-plan.

    Args:
        task_plan: загруженный task-plan.json.
        pipeline_config: загруженный pipeline.json (опц.) — отсюда build-система и команды.
        coverage_script: путь к check_coverage.py.
        coverage_threshold: порог покрытия (переопределяет pipeline.json).
        build_cmd: команда компиляции (override; иначе по build-системе).
        test_cmd: команда тестов (override; иначе quality.test_command / build-система).

    Returns:
        eval-plan dict.
    """
    # Порог покрытия (приоритет: аргумент > pipeline.json > дефолт)
    cov_threshold = coverage_threshold
    if cov_threshold is None and pipeline_config:
        cov_threshold = pipeline_config.get("quality", {}).get("coverage_threshold")
    if cov_threshold is None:
        cov_threshold = DEFAULT_COVERAGE_THRESHOLD

    # Команды compile/test — Maven-корректно из pipeline.json (или override)
    build = _resolve_compile_cmd(pipeline_config, build_cmd)
    test = _resolve_test_cmd(pipeline_config, test_cmd)

    cov_script = coverage_script

    feature_slug = task_plan.get("feature_slug", "unknown")
    tasks = task_plan.get("tasks", [])
    threshold_from_task = task_plan.get("coverage_threshold")

    evals = []
    for task in tasks:
        tid = task["id"]
        task_cov = task.get("coverage_threshold") or threshold_from_task or cov_threshold

        # 1. Compile eval — проект компилируется (бинарный gate по exit-коду)
        evals.append({
            "id": f"compile-{tid.lower()}",
            "type": "compile",
            "task_id": tid,
            "command": build,
            "threshold": 0,
            "binary": True,
            "description": f"Проект компилируется ({build})",
        })

        # 2. Coverage eval — проверка JaCoCo покрытия
        cov_prefix = ""
        if coverage_helper:
            cov_prefix = f"{PYTHON_CMD} {coverage_helper} --base HEAD && "
        evals.append({
            "id": f"coverage-{tid.lower()}",
            "type": "coverage",
            "task_id": tid,
            "command": (
                f"{cov_prefix}{PYTHON_CMD} {cov_script} "
                f"--base HEAD~1 "
                f"--threshold {task_cov} "
                f"--strict"
            ),
            "threshold": task_cov,
            "description": f"Покрытие кода задачи {tid} >= {task_cov:.0%}",
        })

        # 3. Test pass eval — вся тест-сюита зелёная (бинарный регресс-чекпоинт: задача
        #    не должна ломать ранее написанные тесты). exit-код, не «% задачи» — рантайм
        #    (eval-guard/run_pending_evals) и так смотрит только returncode.
        evals.append({
            "id": f"test_pass-{tid.lower()}",
            "type": "test_pass",
            "task_id": tid,
            "command": test,
            "threshold": 0,
            "binary": True,
            "description": f"Вся тест-сюита зелёная после задачи {tid} (регрессия, {test})",
        })

    result = {
        "$schema": SCHEMA_VERSION,
        "feature_slug": feature_slug,
        "evaluated_at": None,  # заполняется при прогоне eval-guard
        "evals": evals,
        "summary": {
            "total": len(evals),
            "by_type": {},
            "by_task": {},
        },
    }

    # Собираем сводку
    by_type = {}
    by_task = {}
    for e in evals:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        by_task[e["task_id"]] = by_task.get(e["task_id"], 0) + 1
    result["summary"]["by_type"] = by_type
    result["summary"]["by_task"] = by_task

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Генерация eval-plan.json из task-plan.json",
    )
    parser.add_argument("task_plan", help="Путь к task-plan.json")
    parser.add_argument("--pipeline-config", help="Путь к pipeline.json (опционально)")
    parser.add_argument("--out", default=None, help="Куда писать результат (по умолчанию рядом с task-plan)")
    parser.add_argument("--coverage-threshold", type=float, help="Порог покрытия (переопределяет pipeline.json)")
    parser.add_argument("--build-cmd", help="Команда компиляции (override; иначе по build-системе)")
    parser.add_argument("--test-cmd", default=None, help="Команда тестов (override; иначе из pipeline.json)")
    parser.add_argument("--coverage-script", required=True, help="Путь к check_coverage.py")
    parser.add_argument("--coverage-helper", help="Путь к coverage-helper.py (инкрементальный запуск JaCoCo)")
    parser.add_argument("--json", action="store_true", help="Вывести JSON в stdout")

    args = parser.parse_args()

    task_plan_path = Path(args.task_plan)
    if not task_plan_path.exists():
        print(f"ОШИБКА: task-plan не найден: {task_plan_path}", file=sys.stderr)
        sys.exit(1)

    if not Path(args.coverage_script).exists():
        print(f"ОШИБКА: coverage_script не найден: {args.coverage_script}", file=sys.stderr)
        sys.exit(1)

    task_plan = _load_json(task_plan_path)

    pipeline_config = None
    if args.pipeline_config:
        pcfg_path = Path(args.pipeline_config)
        if pcfg_path.exists():
            pipeline_config = _load_json(pcfg_path)
        else:
            print(f"Предупреждение: pipeline_config не найден: {pcfg_path}", file=sys.stderr)

    eval_plan = build_evals(
        task_plan,
        pipeline_config=pipeline_config,
        coverage_threshold=args.coverage_threshold,
        test_cmd=args.test_cmd,
        build_cmd=args.build_cmd,
        coverage_script=args.coverage_script,
        coverage_helper=args.coverage_helper,
    )

    # Определяем путь вывода
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = task_plan_path.parent / "eval-plan.json"

    with open(out_path, "w") as f:
        json.dump(eval_plan, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"eval-plan.json создан: {out_path}")
    print(f"  evals: {eval_plan['summary']['total']}")
    print(f"  by_type: {eval_plan['summary']['by_type']}")
    print(f"  by_task: {eval_plan['summary']['by_task']}")

    if args.json:
        print()
        print(json.dumps(eval_plan, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()