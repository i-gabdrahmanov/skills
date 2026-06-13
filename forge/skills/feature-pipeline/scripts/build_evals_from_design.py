#!/usr/bin/env python3
"""
build_evals_from_design.py — генерирует eval-plan.json из task-plan.json.

Evals пишутся ДО кода (фаза Design) и форсят Eval-Driven Development:
агент не может закрыть build-шаг задачи, пока все её eval'ы не пройдены.

Для каждой задачи в task-plan генерируются типовые evals:
  - compile:  полная компиляция проекта (./gradlew compileJava)
  - coverage: проверка JaCoCo покрытия (check_coverage.py)
  - test_pass: % зелёных тестов >= порога (gradle test)

Пороги берутся из pipeline.json quality.*, либо из параметров командной строки.

Usage:
    build_evals_from_design.py <task-plan.json> \\
        --coverage-script <path/to/check_coverage.py> \\
        [--pipeline-config pipeline.json] \\
        [--out eval-plan.json] \\
        [--coverage-threshold 0.80] \\
        [--test-pass-threshold 0.95] \\
        [--build-cmd "./gradlew compileJava"] \\
        [--json]

Exit: 0 (всегда — генерация не блокируется; блокирует использование eval-guard).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = "feature-pipeline/eval-plan@1"

DEFAULT_COVERAGE_THRESHOLD = 0.80
DEFAULT_TEST_PASS_THRESHOLD = 0.95
DEFAULT_BUILD_CMD = "./gradlew compileJava"
DEFAULT_TEST_CMD = "./gradlew test"


def _load_json(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)


def build_evals(
    task_plan: dict,
    pipeline_config: dict | None = None,
    coverage_script: str = "check_coverage.py",
    coverage_helper: str | None = None,
    coverage_threshold: float | None = None,
    test_pass_threshold: float | None = None,
    build_cmd: str | None = None,
    test_cmd: str | None = None,
) -> dict:
    """Генерирует eval-plan из task-plan.

    Args:
        task_plan: загруженный task-plan.json.
        pipeline_config: загруженный pipeline.json (опц.).
        coverage_script: путь к check_coverage.py.
        coverage_threshold: порог покрытия (переопределяет pipeline.json).
        test_pass_threshold: порог прохождения тестов.
        build_cmd: команда сборки (переопределяет pipeline.json).

    Returns:
        eval-plan dict.
    """
    # Извлекаем пороги (приоритет: аргументы > pipeline.json > дефолты)
    cov_threshold = coverage_threshold
    if cov_threshold is None and pipeline_config:
        cov_threshold = pipeline_config.get("quality", {}).get("coverage_threshold")
    if cov_threshold is None:
        cov_threshold = DEFAULT_COVERAGE_THRESHOLD

    test_threshold = test_pass_threshold
    if test_threshold is None:
        test_threshold = DEFAULT_TEST_PASS_THRESHOLD

    build = build_cmd or DEFAULT_BUILD_CMD

    test = test_cmd or DEFAULT_TEST_CMD

    cov_script = coverage_script

    feature_slug = task_plan.get("feature_slug", "unknown")
    tasks = task_plan.get("tasks", [])
    threshold_from_task = task_plan.get("coverage_threshold")

    evals = []
    for task in tasks:
        tid = task["id"]
        task_cov = task.get("coverage_threshold") or threshold_from_task or cov_threshold

        # 1. Compile eval — проверка, что проект компилируется
        evals.append({
            "id": f"compile-{tid.lower()}",
            "type": "compile",
            "task_id": tid,
            "command": build,
            "threshold": 0,
            "description": f"Полная компиляция проекта ({build})",
        })

        # 2. Coverage eval — проверка JaCoCo покрытия
        cov_prefix = ""
        if coverage_helper:
            cov_prefix = f"python3 {coverage_helper} --base HEAD && "
        evals.append({
            "id": f"coverage-{tid.lower()}",
            "type": "coverage",
            "task_id": tid,
            "command": (
                f"{cov_prefix}python3 {cov_script} "
                f"--base HEAD~1 "
                f"--threshold {task_cov}"
            ),
            "threshold": task_cov,
            "description": f"Покрытие кода задачи {tid} >= {task_cov:.0%}",
        })

        # 3. Test pass eval — % зелёных тестов задачи
        evals.append({
            "id": f"test_pass-{tid.lower()}",
            "type": "test_pass",
            "task_id": tid,
            "command": test,
            "threshold": test_threshold,
            "description": f"Тесты задачи {tid} проходят (>= {test_threshold:.0%})",
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
    parser.add_argument("--test-pass-threshold", type=float, help="Порог зелёных тестов")
    parser.add_argument("--build-cmd", help="Команда сборки")
    parser.add_argument("--test-cmd", default=DEFAULT_TEST_CMD, help="Команда для тестов")
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
        test_pass_threshold=args.test_pass_threshold,
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