#!/usr/bin/env python3
"""
run_judge.py — Детерминированный runner для судей (judges) feature-pipeline.

Запускает проверки фазы и сохраняет вердикт. Используется когда:
1) Судья реализован как Python-логика (детерминированные проверки без LLM)
2) Нужно сохранить вердикт из LLM-судьи (агента) в единый стор
3) Нужно перепроверить вердикт перед закрытием шага

Usage:
    run_judge.py <phase> <slug> [--recheck] [--out <path>]

Phases:
    eval       — проверка eval-plan.json
    red        — проверка RED-тестов (только если есть файл вердикта от субагента)
    build      — проверка build-артефактов
    spec       — проверка spec-документов
    delivery   — проверка готовности к доставке (перед коммитом)
    coverage   — проверка JaCoCo-покрытия (закрывает шаг 05-tests)
    design     — check_taskplan + check_sdd (закрывает шаг 02-design)

Если --recheck указан, скрипт проверяет, что вердикт судьи на диске есть и passed=true.
Если вердикта нет или passed=false — exit 1 (блокировка).

Exit:
    0 — PASS (все проверки пройдены)
    1 — FAIL (блокирующие проблемы)
    2 — ERROR (скрипт не может выполнить проверку — нет контекста, нет файлов)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_paths  # единый реестр путей (references/skill-paths.json)

SCHEMA_VERSION = "feature-pipeline/judge-verdict@1"

# Стандартные пути — больше не глобальные константы.
# Все пути передаются через аргументы командной строки.
PROJECT_ROOT: Path | None = None
GROUND_DIR: Path | None = None
FEATURE_DOCS_DIR: Path | None = None
SYSTEM_ANALYSIS_DIR: Path | None = None
SKILL_NAME: str = "feature-pipeline"


def _set_paths(project_root: Path, skill: str = "feature-pipeline") -> None:
    """Устанавливает глобальные пути для скрипта (вызывается из main())."""
    global PROJECT_ROOT, GROUND_DIR, FEATURE_DOCS_DIR, SYSTEM_ANALYSIS_DIR, SKILL_NAME
    PROJECT_ROOT = project_root
    GROUND_DIR = project_root / "ground"
    FEATURE_DOCS_DIR = project_root / "docs" / "feature-pipeline"
    SYSTEM_ANALYSIS_DIR = project_root / "docs" / "system-analysis"
    SKILL_NAME = skill


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _find_feature_dir(slug: str) -> Path | None:
    """Ищет папку фичи: точное совпадение или первый с таким slug в имени."""
    if not FEATURE_DOCS_DIR.exists():
        return None
    exact = FEATURE_DOCS_DIR / slug
    if exact.exists() and exact.is_dir():
        return exact
    for d in FEATURE_DOCS_DIR.iterdir():
        if d.is_dir() and slug in d.name:
            return d
    return None


def _find_judge_verdict(slug: str, judge_name: str) -> Path:
    """Путь к файлу вердикта судьи."""
    return GROUND_DIR / "statements" / SKILL_NAME / slug / "judges" / f"{judge_name}.json"


def _save_verdict(slug: str, judge_name: str, verdict: dict) -> Path:
    """Сохраняет вердикт судьи в ground/statements."""
    path = _find_judge_verdict(slug, judge_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def _find_errors_store(slug: str) -> Path:
    """Путь к errors.json — perpetual error store для ре-итераций."""
    return _find_judge_verdict(slug, "errors").with_name("errors.json")


def _save_errors(slug: str, judge_name: str, new_errors: list) -> Path:
    """Сохраняет ошибки в errors.json с дедупликацией и инкрементом iteration.

    При каждом FAIL: добавляет новую запись в iterations, пополняет accumulated_errors
    (только новыми, не дублируя существующие).
    """
    path = _find_errors_store(slug)
    store = _load_json(path) or {
        "$schema": "feature-pipeline/errors-store@1",
        "feature_slug": slug,
        "iterations": [],
        "accumulated_errors": [],
    }

    prev_accumulated = set(store["accumulated_errors"])
    for err in new_errors:
        if err not in prev_accumulated:
            store["accumulated_errors"].append(err)
            prev_accumulated.add(err)

    iteration_num = len(store["iterations"])
    store["iterations"].append({
        "iteration": iteration_num,
        "judge": judge_name,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "errors": new_errors,
    })

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def _clear_errors(slug: str) -> None:
    """Удаляет errors.json при PASS — ошибки исправлены."""
    path = _find_errors_store(slug)
    if path.exists():
        path.unlink()


def _make_verdict(
    judge_name: str,
    slug: str,
    passed: bool,
    checks: list,
    blocking_issues: list,
    warnings: list,
    summary: str,
) -> dict:
    return {
        "$schema": SCHEMA_VERSION,
        "judge": judge_name,
        "feature_slug": slug,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if passed else "FAIL",
        "passed": passed,
        "checks": checks,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "summary": summary,
    }


# ====== PHASE CHECKS ======


def check_eval(slug: str, feature_dir: Path | None) -> dict:
    """Проверка eval-plan.json (детерминированные проверки)."""
    if feature_dir is None:
        return _make_verdict(
            "eval-judge", slug, False,
            [{"name": "Feature directory exists", "status": "FAIL",
              "detail": f"Папка фичи не найдена в {FEATURE_DOCS_DIR}", "severity": "error"}],
            [f"Папка фичи не найдена: docs/feature-pipeline/{slug}/"],
            [], "Папка фичи отсутствует"
        )
    eval_plan_path = feature_dir / "eval-plan.json"
    task_plan_path = feature_dir / "task-plan.json"

    eval_plan = _load_json(eval_plan_path)
    task_plan = _load_json(task_plan_path)

    checks = []
    blocking_issues = []
    warnings = []

    if not eval_plan:
        return _make_verdict(
            "eval-judge", slug, False,
            [{"name": "eval-plan.json exists", "status": "FAIL",
              "detail": "Файл не найден", "severity": "error"}],
            ["eval-plan.json не найден — eval'ы не сгенерированы"],
            [], "eval-plan.json отсутствует"
        )

    if not task_plan:
        return _make_verdict(
            "eval-judge", slug, False,
            [{"name": "task-plan.json exists", "status": "FAIL",
              "detail": "Файл не найден", "severity": "error"}],
            ["task-plan.json не найден — не с чем сверить"],
            [], "task-plan.json отсутствует"
        )

    evals = eval_plan.get("evals", [])
    tasks = task_plan.get("tasks", [])

    # 1. compile eval для каждой задачи
    task_ids = {t["id"] for t in tasks}
    eval_task_ids = {e["task_id"] for e in evals}
    missing_compile = []
    for tid in task_ids:
        has_compile = any(e["task_id"] == tid and e["type"] == "compile" for e in evals)
        if not has_compile:
            missing_compile.append(tid)

    if missing_compile:
        checks.append({
            "name": "Compile eval exists for all tasks",
            "status": "FAIL",
            "detail": f"Нет compile eval для задач: {', '.join(missing_compile)}",
            "severity": "error",
        })
        blocking_issues.append(f"Задачи без compile eval: {', '.join(missing_compile)}")
    else:
        checks.append({
            "name": "Compile eval exists for all tasks",
            "status": "PASS",
            "detail": f"{len(task_ids)}/{len(task_ids)} tasks have compile eval",
            "severity": "error",
        })

    # 2. coverage eval для каждой задачи
    missing_coverage = []
    for tid in task_ids:
        has_coverage = any(e["task_id"] == tid and e["type"] == "coverage" for e in evals)
        if not has_coverage:
            missing_coverage.append(tid)

    if missing_coverage:
        checks.append({
            "name": "Coverage eval exists for all tasks",
            "status": "FAIL",
            "detail": f"Нет coverage eval для задач: {', '.join(missing_coverage)}",
            "severity": "error",
        })
        blocking_issues.append(f"Задачи без coverage eval: {', '.join(missing_coverage)}")
    else:
        checks.append({
            "name": "Coverage eval exists for all tasks",
            "status": "PASS",
            "detail": f"{len(task_ids)}/{len(task_ids)} tasks have coverage eval",
            "severity": "error",
        })

    # 3. test_pass eval для каждой задачи
    missing_test_pass = []
    for tid in task_ids:
        has_tp = any(e["task_id"] == tid and e["type"] == "test_pass" for e in evals)
        if not has_tp:
            missing_test_pass.append(tid)

    if missing_test_pass:
        checks.append({
            "name": "Test_pass eval exists for all tasks",
            "status": "FAIL",
            "detail": f"Нет test_pass eval для задач: {', '.join(missing_test_pass)}",
            "severity": "error",
        })
        blocking_issues.append(f"Задачи без test_pass eval: {', '.join(missing_test_pass)}")
    else:
        checks.append({
            "name": "Test_pass eval exists for all tasks",
            "status": "PASS",
            "detail": f"{len(task_ids)}/{len(task_ids)} tasks have test_pass eval",
            "severity": "error",
        })

    # 4. Проверка порогов
    cov_thresholds = [
        e.get("threshold", 0) for e in evals
        if e["type"] == "coverage" and e.get("threshold") is not None
    ]
    low_thresholds = [t for t in cov_thresholds if t < 0.5]
    if low_thresholds:
        warnings.append(f"Низкие пороги coverage: {low_thresholds} (минимальный: {min(low_thresholds)})")
        checks.append({
            "name": "Coverage thresholds reasonable",
            "status": "WARN",
            "detail": f"Есть пороги ниже 0.5: {low_thresholds}",
            "severity": "warning",
        })
    else:
        checks.append({
            "name": "Coverage thresholds reasonable",
            "status": "PASS",
            "detail": f"Все пороги >= 0.5",
            "severity": "warning",
        })

    tp_thresholds = [
        e.get("threshold", 0) for e in evals
        if e["type"] == "test_pass" and e.get("threshold") is not None
    ]
    low_tp = [t for t in tp_thresholds if t < 0.8]
    if low_tp:
        blocking_issues.append(f"Низкие пороги test_pass: {low_tp} (должны быть >= 0.8)")
        checks.append({
            "name": "Test_pass thresholds reasonable",
            "status": "FAIL",
            "detail": f"Есть пороги ниже 0.8: {low_tp}",
            "severity": "error",
        })
    else:
        checks.append({
            "name": "Test_pass thresholds reasonable",
            "status": "PASS",
            "detail": f"Все пороги >= 0.8",
            "severity": "warning",
        })

    # 5. Дубликаты eval'ов
    eval_ids = [e["id"] for e in evals]
    duplicates = set(eid for eid in eval_ids if eval_ids.count(eid) > 1)
    if duplicates:
        warnings.append(f"Найдены дубликаты eval'ов: {duplicates}")
        checks.append({
            "name": "No duplicate evals",
            "status": "WARN",
            "detail": f"Дубликаты: {duplicates}",
            "severity": "warning",
        })
    else:
        checks.append({
            "name": "No duplicate evals",
            "status": "PASS",
            "detail": "Все eval'ы уникальны",
            "severity": "warning",
        })

    # 6. Eval'ы ссылаются на существующие task_id
    unknown_tasks = [e["task_id"] for e in evals if e["task_id"] not in task_ids]
    if unknown_tasks:
        blocking_issues.append(f"Eval'ы ссылаются на несуществующие задачи: {set(unknown_tasks)}")
        checks.append({
            "name": "All eval task_ids exist in task-plan",
            "status": "FAIL",
            "detail": f"Неизвестные task_id: {set(unknown_tasks)}",
            "severity": "error",
        })
    else:
        checks.append({
            "name": "All eval task_ids exist in task-plan",
            "status": "PASS",
            "detail": f"Все {len(evals)} eval'ов ссылаются на существующие задачи",
            "severity": "error",
        })

    passed = len(blocking_issues) == 0
    summary = f"{sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} checks passed. "
    if blocking_issues:
        summary += f"{len(blocking_issues)} blocking issue(s)."
    if warnings:
        summary += f" {len(warnings)} warning(s)."

    return _make_verdict("eval-judge", slug, passed, checks, blocking_issues, warnings, summary)


def check_red(slug: str, feature_dir: Path | None) -> dict:
    """Проверка RED-тестов: запускает gradle test с фильтром и проверяет exit code."""
    import subprocess

    # Определяем модули из pipeline.json и task-plan.json
    project_root = PROJECT_ROOT
    pipeline_json_path = project_root / "ground" / "pipeline.json"
    modules = []

    # 1. Пытаемся вытащить модули из task-plan.json
    if feature_dir:
        taskplan_path = feature_dir / "task-plan.json"
        if taskplan_path.exists():
            try:
                with open(taskplan_path) as f:
                    tp = json.load(f)
                for task in tp.get("tasks", []):
                    m = task.get("module")
                    if m and m not in modules:
                        modules.append(m)
            except (json.JSONDecodeError, OSError):
                pass

    # 2. Fallback — из pipeline.json
    if not modules and pipeline_json_path.exists():
        try:
            with open(pipeline_json_path) as f:
                cfg = json.load(f)
            modules = cfg.get("modules", [])
        except (json.JSONDecodeError, OSError):
            pass

    if not modules:
        return _make_verdict(
            "red-judge", slug, False,
            [{"name": "module discovery", "status": "FAIL",
              "detail": "Не удалось определить модули — нет task-plan.json и pipeline.json",
              "severity": "error"}],
            ["Нет модулей для запуска тестов"],
            [], "RED-judge: не найдены модули"
        )

    checks = []
    blocking_issues = []
    all_passed = True

    for module in modules:
        # Нормализация имени модуля:
        #   service-taskservice → :service:taskservice (из pipeline.json modules)
        #   service:taskservice → :service:taskservice (из task-plan.json, уже с :)
        #   :service:taskservice → :service:taskservice (уже нормализован)
        gradle_path = module
        if gradle_path.startswith(":"):
            pass  # уже нормализован
        elif ":" in gradle_path:
            # service:taskservice → :service:taskservice (добавить ведущее :)
            gradle_path = f":{gradle_path}"
        else:
            # Разделяем по последнему дефису, пример: service-taskservice → :service:taskservice
            parts = gradle_path.rsplit("-", 1)
            if len(parts) == 2:
                # Проверяем, что это не пакет с дефисом (например my-lib)
                gradle_path = f":{parts[0]}:{parts[1]}"
            else:
                gradle_path = f":{gradle_path}"

        # Запускаем gradle test с фильтром на классы, содержащие 'Test'
        gradle_cmd = ["./gradlew", f"{gradle_path}:test", "--tests", "*Test", "--no-daemon"]
        try:
            r = subprocess.run(
                gradle_cmd, cwd=str(project_root),
                capture_output=True, text=True, timeout=300,
            )
            passed = r.returncode == 0
            detail = f"exit {r.returncode}"
            if not passed:
                # Ищем число упавших тестов в stdout
                for line in r.stdout.splitlines():
                    if "test" in line.lower() and "fail" in line.lower():
                        detail = line.strip()
                        break
                blocking_issues.append(f"RED-judge {module}: tests FAILED ({detail})")
                all_passed = False

            checks.append({
                "name": f"test:{module}",
                "status": "PASS" if passed else "FAIL",
                "detail": detail[:200],
                "severity": "error",
            })
        except subprocess.TimeoutExpired:
            checks.append({
                "name": f"test:{module}",
                "status": "FAIL",
                "detail": "timeout (300s)",
                "severity": "error",
            })
            blocking_issues.append(f"RED-judge {module}: timeout")
            all_passed = False
        except FileNotFoundError:
            checks.append({
                "name": f"test:{module}",
                "status": "FAIL",
                "detail": "gradlew not found",
                "severity": "error",
            })
            blocking_issues.append(f"RED-judge {module}: gradlew not found")
            all_passed = False

    passed_count = sum(1 for c in checks if c["status"] == "PASS")
    summary = f"{passed_count}/{len(checks)} modules passed"
    if blocking_issues:
        summary += f", {len(blocking_issues)} blocking"

    return _make_verdict("red-judge", slug, all_passed, checks, blocking_issues, [], summary)


def check_build(slug: str, feature_dir: Path | None) -> dict:
    """Проверка build: проверяет вердикт от build-judge субагента."""
    if feature_dir is None:
        return _make_verdict(
            "build-judge", slug, False,
            [{"name": "Feature directory exists", "status": "FAIL",
              "detail": "Папка фичи не найдена", "severity": "error"}],
            ["Папка фичи не найдена"], [], "Папка фичи не найдена"
        )
    verdict_path = _find_judge_verdict(slug, "build-judge")
    verdict = _load_json(verdict_path)

    if not verdict:
        return _make_verdict(
            "build-judge", slug, False,
            [{"name": "Build-judge verdict from subagent", "status": "FAIL",
              "detail": "Вердикт build-judge не найден. Запусти субагента build-judge.",
              "severity": "error"}],
            ["build-judge вердикт отсутствует — реализация не проверена"],
            [], "BUILD-judge не запущен: вердикт не найден"
        )

    passed = verdict.get("passed", False)
    checks = verdict.get("checks", [])
    blocking = verdict.get("blocking_issues", [])
    warnings = verdict.get("warnings", [])
    summary = verdict.get("summary", "BUILD-judge: см. вердикт субагента")

    return _make_verdict("build-judge", slug, passed, checks, blocking, warnings, summary)


def check_spec(slug: str, feature_dir: Path | None) -> dict:
    """Проверка spec-документов (детерминированные проверки)."""
    checks = []
    blocking_issues = []
    warnings = []

    if not feature_dir:
        blocking_issues.append("Папка фичи не найдена — docs не проверены")
        checks.append({"name": "Feature directory exists", "status": "FAIL",
                        "detail": f"Папка фичи не найдена в {FEATURE_DOCS_DIR}",
                        "severity": "error"})
        # Всё остальное проверить не можем — возвращаем FAIL
        passed = False
        summary = "0/1 checks passed. Feature directory missing."
        return _make_verdict("spec-judge", slug, passed, checks, blocking_issues,
                             warnings, summary)

    # 1. Проверка наличия обязательных документов
    # brd.md может лежать в папке фичи или как <slug>-brd.md в корне docs/feature-pipeline/
    brd_path = feature_dir / "brd.md"
    brd_fallback = FEATURE_DOCS_DIR / f"{slug}-brd.md"
    tech_path = feature_dir / "tech-design.md"
    task_plan_path = feature_dir / "task-plan.json"
    if brd_path.exists():
        checks.append({"name": "BRD exists", "status": "PASS",
                        "detail": f"brd.md found in feature dir", "severity": "error"})
    elif brd_fallback.exists():
        checks.append({"name": "BRD exists", "status": "PASS",
                        "detail": f"brd.md found as {brd_fallback.name}", "severity": "error"})
    else:
        checks.append({"name": "BRD exists", "status": "FAIL",
                        "detail": "brd.md not found", "severity": "error"})
        blocking_issues.append("BRD (brd.md) не найден")

    if tech_path.exists():
        checks.append({"name": "Tech design exists", "status": "PASS",
                        "detail": "tech-design.md found", "severity": "error"})
    else:
        checks.append({"name": "Tech design exists", "status": "FAIL",
                        "detail": "tech-design.md not found", "severity": "error"})
        blocking_issues.append("Tech design (tech-design.md) не найден")

    if task_plan_path.exists():
        checks.append({"name": "Task-plan exists", "status": "PASS",
                        "detail": "task-plan.json found", "severity": "error"})
    else:
        checks.append({"name": "Task-plan exists", "status": "FAIL",
                        "detail": "task-plan.json not found", "severity": "error"})
        blocking_issues.append("Task-plan (task-plan.json) не найден")

    # 2. Проверка ground
    manifest_path = GROUND_DIR / "statements" / SKILL_NAME / slug / "manifest.json"
    if manifest_path.exists():
        checks.append({"name": "Ground manifest exists", "status": "PASS",
                        "detail": "manifest.json found", "severity": "warning"})
    else:
        checks.append({"name": "Ground manifest exists", "status": "FAIL",
                        "detail": "manifest.json not found in ground", "severity": "error"})
        blocking_issues.append("manifest.json в ground отсутствует")

    # grounding-excerpt.json — должен быть актуален (содержать записи, а не пустой)
    excerpt_path = SYSTEM_ANALYSIS_DIR / "grounding-excerpt.json"
    if excerpt_path.exists():
        try:
            excerpt_data = json.loads(excerpt_path.read_text())
            excerpt_age = excerpt_data.get("updated_at", "")
            excerpt_gate = excerpt_data.get("gate_total", 0)

            # Проверка: enrich_grounding запускался после task-plan
            excerpt_mtime = excerpt_path.stat().st_mtime if excerpt_path.exists() else 0
            tp_mtime = task_plan_path.stat().st_mtime if task_plan_path.exists() else 0
            enrich_fresh = excerpt_mtime >= tp_mtime if (excerpt_mtime and tp_mtime) else False

            if excerpt_gate > 0 and enrich_fresh:
                checks.append({"name": "Grounding excerpt exists and non-empty", "status": "PASS",
                               "detail": f"grounding-excerpt.json found, gate_total={excerpt_gate}, enrich_grounding запущен",
                               "severity": "warning"})
            elif excerpt_gate > 0 and not enrich_fresh:
                warnings.append("grounding-excerpt.json старше task-plan.json — enrich_grounding не запускался после изменений")
                checks.append({"name": "Enrich grounding fresh", "status": "WARN",
                               "detail": "excerpt старше task-plan, enrich_grounding не обновлён", "severity": "warning"})
            else:
                warnings.append("grounding-excerpt.json пуст (gate_total=0) — enrich_grounding не дал данных")
                checks.append({"name": "Grounding excerpt non-empty", "status": "WARN",
                               "detail": "grounding-excerpt.json пуст", "severity": "warning"})
        except (json.JSONDecodeError, KeyError, AttributeError):
            warnings.append("grounding-excerpt.json повреждён")
            checks.append({"name": "Grounding excerpt valid", "status": "WARN",
                           "detail": "grounding-excerpt.json повреждён", "severity": "warning"})
    else:
        warnings.append("grounding-excerpt.json не найден — enrich_grounding не запускался")
        checks.append({"name": "Grounding excerpt exists", "status": "WARN",
                        "detail": "grounding-excerpt.json not found", "severity": "warning"})

    passed = len(blocking_issues) == 0
    summary = f"{sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} checks passed. "
    if blocking_issues:
        summary += f"{len(blocking_issues)} blocking issue(s)."

    return _make_verdict("spec-judge", slug, passed, checks, blocking_issues, warnings, summary)


def check_delivery(slug: str, feature_dir: Path | None) -> dict:
    """Проверка готовности к доставке: проверяет вердикт от delivery-judge субагента."""
    verdict_path = _find_judge_verdict(slug, "delivery-judge")
    verdict = _load_json(verdict_path)

    if not verdict:
        return _make_verdict(
            "delivery-judge", slug, False,
            [{"name": "Delivery-judge verdict from subagent", "status": "FAIL",
              "detail": "Вердикт delivery-judge не найден. Запусти субагента delivery-judge.",
              "severity": "error"}],
            ["delivery-judge вердикт отсутствует — доставка не проверена"],
            [], "DELIVERY-judge не запущен: вердикт не найден"
        )

    passed = verdict.get("passed", False)
    checks = verdict.get("checks", [])
    blocking = verdict.get("blocking_issues", [])
    warnings = verdict.get("warnings", [])
    summary = verdict.get("summary", "DELIVERY-judge: см. вердикт субагента")

    return _make_verdict("delivery-judge", slug, passed, checks, blocking, warnings, summary)


def check_design(slug: str, feature_dir: Path | None) -> dict:
    """Запускает check_taskplan.py + check_sdd.py и собирает единый вердикт."""
    project_root = PROJECT_ROOT

    # Пути — из единого реестра skill-paths.json (skill_paths loader)
    check_taskplan_script = skill_paths.script(project_root, "tech-design", "check_taskplan")
    check_sdd_script = skill_paths.script(project_root, "tech-design", "check_sdd")

    import subprocess

    taskplan_path = feature_dir / "task-plan.json" if feature_dir else None
    sdd_path = feature_dir / "sdd.md" if feature_dir else None

    checks = []
    blocking_issues = []
    warnings = []

    # 1. check_taskplan
    if taskplan_path and taskplan_path.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(check_taskplan_script), str(taskplan_path), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                checks.append({"name": "check_taskplan", "status": "PASS",
                               "detail": taskplan_path.name, "severity": "error"})
            else:
                detail = r.stderr.strip() or r.stdout.strip() or "exit code {}".format(r.returncode)
                checks.append({"name": "check_taskplan", "status": "FAIL",
                               "detail": detail[:200], "severity": "error"})
                blocking_issues.append(f"check_taskplan FAIL: {detail[:200]}")
        except subprocess.TimeoutExpired:
            checks.append({"name": "check_taskplan", "status": "FAIL",
                           "detail": "timeout (60s)", "severity": "error"})
            blocking_issues.append("check_taskplan: timeout")
    else:
        warnings.append(f"task-plan.json not found at {taskplan_path}")
        checks.append({"name": "check_taskplan", "status": "SKIP",
                       "detail": "file not found", "severity": "info"})

    # 2. check_sdd
    if taskplan_path and taskplan_path.exists():
        try:
            cmd = [sys.executable, str(check_sdd_script), str(taskplan_path), "--json"]
            if sdd_path and sdd_path.exists():
                cmd.extend(["--sdd", str(sdd_path)])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                checks.append({"name": "check_sdd", "status": "PASS",
                               "detail": taskplan_path.name, "severity": "error"})
            else:
                detail = r.stderr.strip() or r.stdout.strip() or "exit code {}".format(r.returncode)
                checks.append({"name": "check_sdd", "status": "FAIL",
                               "detail": detail[:200], "severity": "error"})
                blocking_issues.append(f"check_sdd FAIL: {detail[:200]}")
        except subprocess.TimeoutExpired:
            checks.append({"name": "check_sdd", "status": "FAIL",
                           "detail": "timeout (60s)", "severity": "error"})
            blocking_issues.append("check_sdd: timeout")
    else:
        warnings.append(f"task-plan.json not found at {taskplan_path}")
        checks.append({"name": "check_sdd", "status": "SKIP",
                       "detail": "task-plan.json missing", "severity": "info"})

    passed = len(blocking_issues) == 0
    summary = f"{sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} checks passed"
    if blocking_issues:
        summary += f", {len(blocking_issues)} blocking"

    return _make_verdict("design-judge", slug, passed, checks, blocking_issues, warnings, summary)


def check_coverage(slug: str, feature_dir: Path | None) -> dict:
    """Запускает check_coverage.py (JaCoCo gate) и собирает вердикт coverage-judge.

    Имя вердикта (coverage-judge.json) совпадает с required_judges['05-tests'].
    """
    project_root = PROJECT_ROOT

    # Путь к check_coverage.py — из единого реестра skill-paths.json
    check_cov_script = skill_paths.script(project_root, "minor-defect-fix", "check_coverage")
    check_cov_rel = str(check_cov_script.relative_to(project_root)) \
        if check_cov_script and check_cov_script.is_relative_to(project_root) \
        else str(check_cov_script)

    # Порог из pipeline.json quality.coverage_threshold (fallback 0.80)
    threshold = 0.80
    pipeline_cfg = _load_json(project_root / "ground" / "pipeline.json") or {}
    try:
        threshold = float(pipeline_cfg.get("quality", {}).get("coverage_threshold", threshold))
    except (TypeError, ValueError):
        pass

    if not check_cov_script.exists():
        return _make_verdict(
            "coverage-judge", slug, False,
            [{"name": "check_coverage.py available", "status": "FAIL",
              "detail": f"Скрипт не найден: {check_cov_rel}", "severity": "error"}],
            [f"check_coverage.py не найден ({check_cov_rel}) — покрытие не проверено"],
            [], "COVERAGE-judge: скрипт покрытия отсутствует"
        )

    import subprocess
    cmd = [sys.executable, str(check_cov_script), "--root", str(project_root),
           "--threshold", str(threshold), "--json"]
    try:
        r = subprocess.run(cmd, cwd=str(project_root),
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return _make_verdict(
            "coverage-judge", slug, False,
            [{"name": "check_coverage", "status": "FAIL",
              "detail": "timeout (120s)", "severity": "error"}],
            ["check_coverage: timeout"], [], "COVERAGE-judge: timeout"
        )

    # check_coverage.py: exit 0 = pass/skip, 2 = LOW/MISSING
    passed = r.returncode == 0
    detail = (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}")[:300]
    checks = [{
        "name": "check_coverage",
        "status": "PASS" if passed else "FAIL",
        "detail": detail,
        "severity": "error",
    }]
    blocking = [] if passed else [f"Покрытие ниже порога {threshold}: {detail}"]
    summary = f"check_coverage exit {r.returncode} (порог {threshold})"
    return _make_verdict("coverage-judge", slug, passed, checks, blocking, [], summary)


# ====== MAIN ======

PHASE_MAP = {
    "eval": check_eval,
    "red": check_red,
    "build": check_build,
    "spec": check_spec,
    "delivery": check_delivery,
    "design": check_design,
    "coverage": check_coverage,
}


def main():
    parser = argparse.ArgumentParser(
        description="Run judge checks for a feature-pipeline phase",
    )
    parser.add_argument("phase", choices=list(PHASE_MAP.keys()),
                        help="Фаза для проверки")
    parser.add_argument("slug", help="Slug фичи (или Jira-ключ)")
    parser.add_argument("--project-root", default=".",
                        help="Корень проекта (по умолчанию cwd)")
    parser.add_argument("--skill", default="feature-pipeline",
                        help="Имя скилла для резолвинга ground/statements/<skill>/")
    parser.add_argument("--feature-docs", default=None,
                        help="Путь к docs/feature-pipeline (по умолчанию <project-root>/docs/feature-pipeline)")
    parser.add_argument("--system-analysis-dir", default=None,
                        help="Путь к docs/system-analysis (по умолчанию <project-root>/docs/system-analysis)")
    parser.add_argument("--recheck", action="store_true",
                        help="Перепроверить существующий вердикт (exit 1 если нет или failed)")
    parser.add_argument("--from-output", default=None,
                        help="Файл с JSON-вердиктом субагента (или '-' для stdin) — "
                             "сохранить как judges/<phase>-judge.json. Для pass-through "
                             "судей (build, delivery), которые считает не run_judge, а субагент.")
    parser.add_argument("--out", default=None,
                        help="Куда писать вердикт (по умолчанию ground/statements/.../judges/)")

    args = parser.parse_args()

    # Инициализируем пути
    project_root = Path(args.project_root).resolve()
    _set_paths(project_root, skill=args.skill)

    # Переопределяем docs-пути, если явно переданы
    if args.feature_docs:
        global FEATURE_DOCS_DIR
        FEATURE_DOCS_DIR = Path(args.feature_docs).resolve()
    else:
        FEATURE_DOCS_DIR = project_root / "docs" / "feature-pipeline"

    if args.system_analysis_dir:
        global SYSTEM_ANALYSIS_DIR
        SYSTEM_ANALYSIS_DIR = Path(args.system_analysis_dir).resolve()
    else:
        SYSTEM_ANALYSIS_DIR = project_root / "docs" / "system-analysis"

    feature_dir = _find_feature_dir(args.slug)
    if not feature_dir:
        print(f"WARN: Папка фичи не найдена в {FEATURE_DOCS_DIR}", file=sys.stderr)
        print(f"WARN: slug={args.slug}, ищу в {FEATURE_DOCS_DIR}", file=sys.stderr)
        # Продолжаем — некоторые проверки могут работать без папки фичи

    # --from-output: ingest вердикта субагента (pass-through судьи build/delivery).
    # run_judge сам их не считает — он только сохраняет валидированный вердикт в judges/,
    # чтобы update.py смог закрыть шаг, а --recheck — подтвердить.
    if args.from_output:
        judge_name = f"{args.phase}-judge"
        try:
            raw = sys.stdin.read() if args.from_output == "-" else Path(args.from_output).read_text()
            subagent = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: не удалось прочитать --from-output: {e}", file=sys.stderr)
            sys.exit(2)
        if not isinstance(subagent, dict) or "passed" not in subagent:
            print("ERROR: вердикт субагента должен быть JSON-объектом с полем 'passed'",
                  file=sys.stderr)
            sys.exit(2)
        verdict = _make_verdict(
            judge_name, args.slug, bool(subagent.get("passed")),
            subagent.get("checks", []),
            subagent.get("blocking_issues", []),
            subagent.get("warnings", []),
            subagent.get("summary", f"{judge_name}: вердикт принят от субагента"),
        )
        out_path = _save_verdict(args.slug, judge_name, verdict)
        if verdict["passed"]:
            _clear_errors(args.slug)
        elif verdict.get("blocking_issues"):
            _save_errors(args.slug, judge_name, verdict["blocking_issues"])
        print(f"{'✅' if verdict['passed'] else '❌'} {judge_name} (ingest): {verdict['verdict']}")
        print(f"  Verdict saved: {out_path}")
        for issue in verdict.get("blocking_issues", []):
            print(f"  BLOCKING: {issue}")
        sys.exit(0 if verdict["passed"] else 1)

    # --recheck: перепроверяем реально (для eval/spec/red с детерминированными проверками)
    if args.recheck:
        # Для eval и spec — запускаем полную проверку заново (не кэш)
        if args.phase in ("eval", "spec", "red", "coverage"):
            try:
                verdict = PHASE_MAP[args.phase](args.slug, feature_dir)
            except Exception as e:
                print(f"ERROR: Ошибка при перепроверке {args.phase}: {e}", file=sys.stderr)
                sys.exit(2)

            # Сохраняем обновлённый вердикт
            out_path = _save_verdict(args.slug, f"{args.phase}-judge", verdict)

            # Perpetual error store: FAIL → сохранить, PASS → очистить
            if verdict["passed"]:
                _clear_errors(args.slug)
            else:
                blocking = verdict.get("blocking_issues", [])
                if blocking:
                    errors_path = _save_errors(args.slug, f"{args.phase}-judge", blocking)
                    print(f"  Errors saved: {errors_path}")
                    print(f"  Accumulated: {len(_load_json(errors_path).get('accumulated_errors', []))}")

            status_emoji = "✅" if verdict["passed"] else "❌"
            print(f"{status_emoji} {args.phase}-judge (recheck): {verdict['verdict']}")
            print(f"  Summary: {verdict['summary']}")
            for issue in verdict.get("blocking_issues", []):
                print(f"  BLOCKING: {issue}")
            sys.exit(0 if verdict["passed"] else 1)

        # Для build/delivery — проверяем, что вердикт уже есть и passed=true
        verdict_path = _find_judge_verdict(args.slug, f"{args.phase}-judge")
        verdict = _load_json(verdict_path)
        if not verdict:
            print(f"FAIL: Вердикт не найден: {verdict_path}", file=sys.stderr)
            sys.exit(1)
        if not verdict.get("passed", False):
            print(f"FAIL: Вердикт не пройден ({verdict.get('summary', '')})", file=sys.stderr)
            for issue in verdict.get("blocking_issues", []):
                print(f"  BLOCKING: {issue}", file=sys.stderr)
            sys.exit(1)
        print(f"PASS: Вердикт {args.phase}-judge подтверждён")
        sys.exit(0)

    # Запускаем проверку
    check_fn = PHASE_MAP[args.phase]
    try:
        verdict = check_fn(args.slug, feature_dir)
    except Exception as e:
        print(f"ERROR: Ошибка при проверке {args.phase}: {e}", file=sys.stderr)
        sys.exit(2)

    # Сохраняем вердикт
    out_path = args.out
    if not out_path:
        out_path = _save_verdict(args.slug, f"{args.phase}-judge", verdict)
    else:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(verdict, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # Выводим результат
    status_emoji = "✅" if verdict["passed"] else "❌"
    print(f"{status_emoji} {args.phase}-judge: {verdict['verdict']}")
    print(f"  Summary: {verdict['summary']}")
    print(f"  Verdict saved: {out_path}")

    # Perpetual error store: FAIL → сохранить, PASS → очистить
    judge_name = f"{args.phase}-judge"
    if verdict["passed"]:
        _clear_errors(args.slug)
    else:
        blocking = verdict.get("blocking_issues", [])
        if blocking:
            errors_path = _save_errors(args.slug, judge_name, blocking)
            print(f"  Errors saved: {errors_path}")
            print(f"  Accumulated: {len(_load_json(errors_path).get('accumulated_errors', []))}")

    for issue in verdict.get("blocking_issues", []):
        print(f"  BLOCKING: {issue}")
    for warn in verdict.get("warnings", []):
        print(f"  WARN: {warn}")
    for check in verdict.get("checks", []):
        if check["status"] != "PASS":
            print(f"  [{check['status']}] {check['name']}: {check['detail']}")

    sys.exit(0 if verdict["passed"] else 1)


if __name__ == "__main__":
    main()