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
    brd        — проверка БТ на язык бизнеса (нет код-токенов); поддерживает --brd <path>
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
import re
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
BRD_OVERRIDE: Path | None = None  # явный путь к brd.md (--brd) для standalone-проверки
REUSE_SCAN_OVERRIDE: Path | None = None  # явный путь к scan/reuse.json (--reuse-scan)
DIFF_BASE: str = "HEAD"  # база git diff для фазы reuse (--diff-base)


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


_VALID_STATUSES = {"PASS", "FAIL", "WARN", "SKIP"}


def validate_verdict(obj) -> tuple[bool, list]:
    """Строгая проверка JSON-вердикта по схеме judge-verdict@1 (fail-closed для Qwen).

    На слабой модели субагент часто отдаёт битый/неполный JSON. Раньше проверяли только
    наличие ключа 'passed' — теперь валидируем структуру и при невалидности блокируем.
    Возвращает (ok, errors).
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return False, ["вердикт должен быть JSON-объектом"]
    if "passed" not in obj:
        errors.append("нет обязательного поля 'passed'")
    elif not isinstance(obj["passed"], bool):
        errors.append("'passed' должно быть boolean (true/false)")
    checks = obj.get("checks")
    if checks is not None:
        if not isinstance(checks, list):
            errors.append("'checks' должно быть массивом")
        else:
            for i, c in enumerate(checks):
                if not isinstance(c, dict):
                    errors.append(f"checks[{i}] должен быть объектом")
                    continue
                if "name" not in c:
                    errors.append(f"checks[{i}] без 'name'")
                st = c.get("status")
                if st is not None and st not in _VALID_STATUSES:
                    errors.append(f"checks[{i}].status='{st}' не из {sorted(_VALID_STATUSES)}")
    for key in ("blocking_issues", "warnings"):
        if key in obj and not isinstance(obj[key], list):
            errors.append(f"'{key}' должно быть массивом")
    if "summary" in obj and not isinstance(obj["summary"], str):
        errors.append("'summary' должно быть строкой")
    return (len(errors) == 0), errors


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

    # 1. Пытаемся вытащить модули из task-plan.json.
    #    tech-design пишет модули в tasks[].modules (массив; см. check_taskplan.py),
    #    но допускаем и единичное поле module (str) для совместимости.
    if feature_dir:
        taskplan_path = feature_dir / "task-plan.json"
        if taskplan_path.exists():
            try:
                with open(taskplan_path) as f:
                    tp = json.load(f)
                for task in tp.get("tasks", []):
                    mods = task.get("modules")
                    if isinstance(mods, list):
                        for m in mods:
                            if m and m not in modules:
                                modules.append(m)
                    elif isinstance(task.get("module"), str) and task["module"]:
                        if task["module"] not in modules:
                            modules.append(task["module"])
            except (json.JSONDecodeError, OSError):
                pass

    # 2. Fallback — из pipeline.json. Модули лежат в project.modules
    #    (init_pipeline_config.py); top-level modules — на случай старого формата.
    if not modules and pipeline_json_path.exists():
        try:
            with open(pipeline_json_path) as f:
                cfg = json.load(f)
            modules = list(cfg.get("project", {}).get("modules") or cfg.get("modules", []))
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Пусто → одномодульный проект (нет settings.gradle с include): тесты гоняем
    #    в корне (./gradlew test), а не падаем «нет модулей». None — сентинел корня.
    single_module = not modules
    if single_module:
        modules = [None]

    # Читаем test_layer из pipeline.json (default=service-unit для multimodule)
    test_layer = "service-unit"
    if pipeline_json_path.exists():
        try:
            with open(pipeline_json_path) as f:
                _pcfg = json.load(f)
            test_layer = _pcfg.get("quality", {}).get("test_layer", test_layer)
        except (json.JSONDecodeError, OSError):
            pass

    checks = []
    blocking_issues = []
    warnings: list[str] = []
    all_passed = True

    # Детерминированный флор: запрещённые аннотации в тест-файлах.
    # Блокирует ДО запуска gradle — быстрее и точнее.
    ann_checks, ann_blocking, ann_warnings = _check_forbidden_test_annotations(
        feature_dir, test_layer
    )
    checks.extend(ann_checks)
    blocking_issues.extend(ann_blocking)
    warnings.extend(ann_warnings)
    if ann_blocking:
        all_passed = False

    for module in modules:
        if module is None:
            # Одномодульный проект — тесты в корне (без :module: префикса).
            label = "root"
            gradle_cmd = ["./gradlew", "test", "--tests", "*Test", "--no-daemon"]
        else:
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
                # Разделяем по последнему дефису: service-taskservice → :service:taskservice
                parts = gradle_path.rsplit("-", 1)
                if len(parts) == 2:
                    # Проверяем, что это не пакет с дефисом (например my-lib)
                    gradle_path = f":{parts[0]}:{parts[1]}"
                else:
                    gradle_path = f":{gradle_path}"
            label = module
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
                blocking_issues.append(f"RED-judge {label}: tests FAILED ({detail})")
                all_passed = False

            checks.append({
                "name": f"test:{label}",
                "status": "PASS" if passed else "FAIL",
                "detail": detail[:200],
                "severity": "error",
            })
        except subprocess.TimeoutExpired:
            checks.append({
                "name": f"test:{label}",
                "status": "FAIL",
                "detail": "timeout (300s)",
                "severity": "error",
            })
            blocking_issues.append(f"RED-judge {label}: timeout")
            all_passed = False
        except FileNotFoundError:
            checks.append({
                "name": f"test:{label}",
                "status": "FAIL",
                "detail": "gradlew not found",
                "severity": "error",
            })
            blocking_issues.append(f"RED-judge {label}: gradlew not found")
            all_passed = False

    passed_count = sum(1 for c in checks if c["status"] == "PASS")
    summary = f"{passed_count}/{len(checks)} modules passed"
    if blocking_issues:
        summary += f", {len(blocking_issues)} blocking"

    return _make_verdict("red-judge", slug, all_passed, checks, blocking_issues, warnings, summary)


def _changed_src_files(base: str = "HEAD", main_only: bool = True) -> list:
    """Изменённые *.java файлы (vs base). main_only — только src/main."""
    import subprocess
    try:
        r = subprocess.run(["git", "diff", "--name-only", base, "--", "*.java"],
                           cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if r.returncode != 0:
        return []
    files = []
    for f in r.stdout.splitlines():
        if main_only and "src/main" not in f:
            continue
        p = PROJECT_ROOT / f
        if p.exists():
            files.append(p)
    return files


_STUB_RE = re.compile(r"UnsupportedOperationException|not\s+implemented", re.IGNORECASE)
_TODO_RE = re.compile(r"\b(TODO|FIXME)\b")

# Аннотации, запрещённые при test_layer=service-unit.
# @DataJpaTest поднимает ApplicationContext → initializationError в multimodule.
# @SpringBootTest — то же плюс требует запущенного контейнера.
_FORBIDDEN_TEST_ANNOTATIONS = re.compile(
    r"@(?:DataJpaTest|SpringBootTest)\b"
)


def _check_forbidden_test_annotations(
    feature_dir: Path | None,
    test_layer: str,
) -> tuple:
    """Детерминированный флор red-judge: запрещённые аннотации в тест-файлах.

    Сканирует только файлы из task-plan artifacts с src/test в пути.
    При test_layer=service-unit → blocking для @DataJpaTest/@SpringBootTest.
    При других значениях → warning (в проекте может быть настроен контекст).
    """
    checks, blocking, warnings = [], [], []
    if not feature_dir:
        return checks, blocking, warnings

    taskplan_path = feature_dir / "task-plan.json"
    if not taskplan_path.exists():
        return checks, blocking, warnings

    try:
        tp = json.loads(taskplan_path.read_text())
    except (json.JSONDecodeError, OSError):
        return checks, blocking, warnings

    # Собираем пути тест-файлов из artifacts
    test_artifacts: list[Path] = []
    project_root = PROJECT_ROOT or Path.cwd()
    for task in tp.get("tasks", []):
        for artifact in task.get("artifacts", []):
            if "src/test" in artifact and artifact.endswith(".java"):
                p = project_root / artifact
                if p.exists():
                    test_artifacts.append(p)

    if not test_artifacts:
        return checks, blocking, warnings

    hits = []
    for p in test_artifacts:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in txt.splitlines():
            m = _FORBIDDEN_TEST_ANNOTATIONS.search(ln)
            if m:
                hits.append(f"{p.name}: {ln.strip()[:100]}")

    check_name = "Нет @DataJpaTest/@SpringBootTest (test_layer=service-unit)"
    if hits:
        severity = "blocking" if test_layer == "service-unit" else "warning"
        detail = "; ".join(hits[:5])
        if test_layer == "service-unit":
            blocking.extend(
                f"Запрещённая аннотация (test_layer=service-unit): {h}" for h in hits[:5]
            )
            checks.append({"name": check_name, "status": "FAIL",
                           "detail": detail, "severity": "error"})
        else:
            warnings.append(f"@DataJpaTest/@SpringBootTest: {detail}")
            checks.append({"name": check_name, "status": "WARN",
                           "detail": detail, "severity": "warning"})
    else:
        checks.append({"name": check_name, "status": "PASS",
                       "detail": f"Проверено {len(test_artifacts)} тест-файлов",
                       "severity": "error"})

    return checks, blocking, warnings


def _build_floor() -> tuple:
    """Детерминированный пол build-judge: ловит stubs даже без/при битом LLM-вердикте."""
    checks, blocking, warnings = [], [], []
    files = _changed_src_files()
    stubs, todos = [], []
    for p in files:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in txt.splitlines():
            if _STUB_RE.search(ln):
                stubs.append(f"{p.name}: {ln.strip()[:80]}")
            elif _TODO_RE.search(ln):
                todos.append(f"{p.name}: {ln.strip()[:80]}")
    if stubs:
        checks.append({"name": "Нет stubs (детерминированно)", "status": "FAIL",
                       "detail": "; ".join(stubs[:5]), "severity": "error"})
        blocking.extend(f"stub в production: {s}" for s in stubs[:5])
    else:
        checks.append({"name": "Нет stubs (детерминированно)", "status": "PASS",
                       "detail": f"{len(files)} изменённых файлов src/main", "severity": "error"})
    if todos:
        warnings.extend(f"TODO/FIXME: {t}" for t in todos[:5])
    return checks, blocking, warnings


def check_build(slug: str, feature_dir: Path | None) -> dict:
    """build-judge — гибрид: вердикт LLM-субагента + детерминированный пол (stubs).

    Пол работает даже если LLM-вердикт отсутствует/невалиден — LLM не может «протащить»
    stubs, объявив passed=true (важно на слабой модели Qwen)."""
    floor_checks, floor_block, floor_warn = _build_floor()
    if feature_dir is None:
        return _make_verdict(
            "build-judge", slug, False,
            floor_checks + [{"name": "Feature directory exists", "status": "FAIL",
                             "detail": "Папка фичи не найдена", "severity": "error"}],
            floor_block + ["Папка фичи не найдена"], floor_warn, "Папка фичи не найдена"
        )
    verdict = _load_json(_find_judge_verdict(slug, "build-judge"))

    if not verdict:
        return _make_verdict(
            "build-judge", slug, False,
            floor_checks + [{"name": "Build-judge verdict from subagent", "status": "FAIL",
                             "detail": "Вердикт build-judge не найден. Запусти субагента.",
                             "severity": "error"}],
            floor_block + ["build-judge вердикт отсутствует — реализация не проверена"],
            floor_warn, "BUILD-judge не запущен: вердикт не найден"
        )

    ok, verr = validate_verdict(verdict)
    if not ok:
        return _make_verdict(
            "build-judge", slug, False,
            floor_checks + [{"name": "Вердикт build-judge валиден", "status": "FAIL",
                             "detail": "; ".join(verr), "severity": "error"}],
            floor_block + [f"вердикт build-judge невалиден: {'; '.join(verr)}"],
            floor_warn, "BUILD-judge: невалидный вердикт субагента (fail-closed)"
        )

    checks = list(verdict.get("checks", [])) + floor_checks
    blocking = list(verdict.get("blocking_issues", [])) + floor_block
    warnings = list(verdict.get("warnings", [])) + floor_warn
    passed = bool(verdict.get("passed", False)) and not floor_block
    summary = (verdict.get("summary", "BUILD-judge: см. вердикт субагента")
               + f" | пол: {'OK' if not floor_block else str(len(floor_block)) + ' blocking'}")
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


_SECRET_RE = re.compile(
    r"(?:password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token)\s*[=:]\s*"
    r"['\"][^'\"\n]{6,}['\"]"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE)


def _delivery_floor() -> tuple:
    """Детерминированный пол delivery-judge: ловит секреты в изменённых файлах."""
    checks, blocking, warnings = [], [], []
    files = _changed_src_files(main_only=False)
    secrets = []
    for p in files:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in txt.splitlines():
            if _SECRET_RE.search(ln):
                secrets.append(f"{p.name}: {ln.strip()[:60]}")
    if secrets:
        checks.append({"name": "Нет секретов (детерминированно)", "status": "FAIL",
                       "detail": "; ".join(secrets[:5]), "severity": "error"})
        blocking.extend(f"возможный секрет в коде: {s}" for s in secrets[:5])
    else:
        checks.append({"name": "Нет секретов (детерминированно)", "status": "PASS",
                       "detail": f"{len(files)} изменённых файлов", "severity": "error"})
    return checks, blocking, warnings


def check_delivery(slug: str, feature_dir: Path | None) -> dict:
    """delivery-judge — гибрид: вердикт LLM-субагента + детерминированный пол (секреты).

    Секреты блокируют доставку даже если LLM-вердикт отсутствует/невалиден."""
    floor_checks, floor_block, floor_warn = _delivery_floor()
    verdict = _load_json(_find_judge_verdict(slug, "delivery-judge"))

    if not verdict:
        return _make_verdict(
            "delivery-judge", slug, False,
            floor_checks + [{"name": "Delivery-judge verdict from subagent", "status": "FAIL",
                             "detail": "Вердикт delivery-judge не найден. Запусти субагента.",
                             "severity": "error"}],
            floor_block + ["delivery-judge вердикт отсутствует — доставка не проверена"],
            floor_warn, "DELIVERY-judge не запущен: вердикт не найден"
        )

    ok, verr = validate_verdict(verdict)
    if not ok:
        return _make_verdict(
            "delivery-judge", slug, False,
            floor_checks + [{"name": "Вердикт delivery-judge валиден", "status": "FAIL",
                             "detail": "; ".join(verr), "severity": "error"}],
            floor_block + [f"вердикт delivery-judge невалиден: {'; '.join(verr)}"],
            floor_warn, "DELIVERY-judge: невалидный вердикт субагента (fail-closed)"
        )

    checks = list(verdict.get("checks", [])) + floor_checks
    blocking = list(verdict.get("blocking_issues", [])) + floor_block
    warnings = list(verdict.get("warnings", [])) + floor_warn
    passed = bool(verdict.get("passed", False)) and not floor_block
    summary = (verdict.get("summary", "DELIVERY-judge: см. вердикт субагента")
               + f" | пол: {'OK' if not floor_block else str(len(floor_block)) + ' blocking'}")
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


# Детерминированный слой brd-judge: код-токены, которым не место в БТ.
# (имя проверки, regex, re-флаги, severity). severity=error → blocking, warning → не блокирует.
# SQL и ALL_CAPS — регистрозависимы намеренно: чтобы не ловить обычную бизнес-прозу
# («from», «where») и акронимы без подчёркивания (REST, API, SLA, BRD).
BRD_CODE_PATTERNS = [
    ("Java-аннотации и типы",
     r"@[A-Z][a-zA-Z]+\b"
     r"|\b(SQLException|RuntimeException|IllegalStateException|Throwable|Optional|"
     r"HashMap|ArrayList|LinkedList|UUID|BigDecimal)\b"
     r"|\b\w+\.java\b", 0, "error"),
    ("SQL/JPQL",
     r"\b(SELECT|INSERT|UPDATE|DELETE|JOIN|WHERE|FROM|GROUP BY|ORDER BY)\b"
     r"|\b\w+\.sql\b|\bJPQL\b", 0, "error"),
    ("Сигнатуры методов",
     r"\b\w*[a-z][A-Z]\w*\([^)\n]*\)|\b[a-z]+_[a-z][\w]*\([^)\n]*\)", 0, "error"),
    ("Пакеты/неймспейсы",
     r"\b[a-z][a-z0-9]+(?:\.[a-z][a-z0-9]+){2,}\b", 0, "error"),
    ("Топики/очереди/метрики (ALL_CAPS)",
     r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", 0, "error"),
    ("Имена классов (CamelCase)",
     r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", 0, "warning"),
]


def _resolve_brd_path(slug: str, feature_dir: Path | None) -> Path | None:
    """Резолвит путь к документу БТ: --brd → feature_dir/brd.md → <slug>-brd.md."""
    if BRD_OVERRIDE is not None:
        p = BRD_OVERRIDE if BRD_OVERRIDE.is_absolute() else (PROJECT_ROOT / BRD_OVERRIDE)
        return p if p.exists() else None
    if feature_dir is not None:
        cand = feature_dir / "brd.md"
        if cand.exists():
            return cand
    fallback = FEATURE_DOCS_DIR / f"{slug}-brd.md"
    if fallback.exists():
        return fallback
    return None


def check_brd(slug: str, feature_dir: Path | None) -> dict:
    """Детерминированный слой судьи БТ: ловит литеральные код-токены в документе БТ.

    Семантику («написано как спецификация, а не как БТ») проверяет LLM-судья brd-judge —
    его вердикт ингестится через --from-output. Здесь — дешёвый regex-гейт, который
    работает и standalone (вне feature-pipeline) через флаг --brd.
    """
    brd_path = _resolve_brd_path(slug, feature_dir)
    if brd_path is None:
        return _make_verdict(
            "brd-judge", slug, False,
            [{"name": "BRD exists", "status": "FAIL",
              "detail": "brd.md не найден (искал: --brd / feature_dir/brd.md / <slug>-brd.md)",
              "severity": "error"}],
            ["Документ БТ не найден — нечего проверять"],
            [], "BRD отсутствует"
        )

    text = brd_path.read_text(encoding="utf-8", errors="replace")
    checks = []
    blocking_issues = []
    warnings = []

    # 1. Fenced-блоки кода (```) — в БТ их быть не должно
    fenced = re.findall(r"```.*?```", text, re.DOTALL)
    if fenced:
        checks.append({"name": "Нет блоков кода (```)", "status": "FAIL",
                       "detail": f"Найдено {len(fenced)} fenced-блок(ов) кода",
                       "severity": "error"})
        blocking_issues.append(
            f"БТ содержит {len(fenced)} блок(ов) кода (```) — удали, опиши бизнес-эффект")
    else:
        checks.append({"name": "Нет блоков кода (```)", "status": "PASS",
                       "detail": "fenced-блоков нет", "severity": "error"})

    # Сканируем текст без fenced-блоков (чтобы не дублировать их содержимое в токенах)
    scan = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)

    for name, pattern, flags, severity in BRD_CODE_PATTERNS:
        found: list[str] = []
        for m in re.finditer(pattern, scan, flags):
            tok = m.group(0).strip()
            if tok and tok not in found:
                found.append(tok)
            if len(found) >= 8:
                break
        if found:
            sample = ", ".join(f"`{t}`" for t in found)
            if severity == "error":
                checks.append({"name": name, "status": "FAIL",
                               "detail": f"Найдено: {sample}", "severity": "error"})
                blocking_issues.append(
                    f"{name}: {sample} — это реализация, опиши бизнес-эффект и правило")
            else:
                checks.append({"name": name, "status": "WARN",
                               "detail": f"Найдено: {sample}", "severity": "warning"})
                warnings.append(
                    f"{name}: {sample} — проверь, не код ли это (спорное решает LLM-судья)")
        else:
            checks.append({"name": name, "status": "PASS",
                           "detail": "не найдено", "severity": severity})

    passed = len(blocking_issues) == 0
    summary = f"{sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} checks passed."
    if blocking_issues:
        summary += f" {len(blocking_issues)} blocking issue(s)."
    if warnings:
        summary += f" {len(warnings)} warning(s)."

    return _make_verdict("brd-judge", slug, passed, checks, blocking_issues, warnings, summary)


# Детерминированный слой reuse-judge: «велосипеды» — код, дублирующий доступные библиотеки/stdlib.
# (имя, regex, замена, libs|None). libs=None → замена из stdlib (всегда доступна) → severity warning
# (эвристика, спорное добивает LLM); libs=(...) → blocking, если хоть одна есть в каталоге зависимостей.
REUSE_PATTERNS = [
    ("Ручная проверка пустоты/null коллекции",
     r"\w+\s*[!=]=\s*null\s*(?:&&|\|\|)\s*!?\s*\w+\.isEmpty\(\)",
     "CollectionUtils.isEmpty()/isNotEmpty()",
     ("commons-collections", "commons-collections4", "spring-core", "guava")),
    ("Ручная проверка пустой/blank строки",
     r"\.trim\(\)\s*\.isEmpty\(\)|\.equals\(\"\"\)|\"\"\.equals\(",
     "StringUtils.isBlank()/isEmpty()",
     ("commons-lang3", "spring-core", "guava")),
    ("Ручной null-default (тернарник)",
     r"\w+\s*!=\s*null\s*\?\s*\w+\s*:\s*\w+",
     "Objects.requireNonNullElse() / Optional.ofNullable()",
     None),
    ("Ручной null-check с throw",
     r"if\s*\(\s*\w+\s*==\s*null\s*\)\s*\{?\s*throw",
     "Objects.requireNonNull()",
     None),
]


def _git_diff_added(base: str) -> list:
    """Список (file, added_line) добавленных строк по `git diff <base>` для *.java."""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "diff", "--unified=0", base, "--", "*.java"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if r.returncode != 0:
        return []
    out = []
    cur = None
    for line in r.stdout.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((cur or "?", line[1:]))
    return out


def _load_reuse_deps() -> set:
    """Множество имён зависимостей (artifact и group) из scan/reuse.json."""
    p = REUSE_SCAN_OVERRIDE if REUSE_SCAN_OVERRIDE else (
        PROJECT_ROOT / "docs" / "system-analysis" / "scan" / "reuse.json")
    data = _load_json(Path(p))
    deps: set = set()
    if data:
        for it in data.get("dependencies", []):
            if it.get("artifact"):
                deps.add(it["artifact"])
            if it.get("group"):
                deps.add(it["group"])
    return deps


def check_reuse(slug: str, feature_dir: Path | None) -> dict:
    """Детерминированный слой судьи качества: ловит велосипеды в добавленном production-коде.

    Семантику («новый helper дублирует существующий util проекта/библиотеки») добивает LLM
    reuse-judge через --from-output. Здесь — дешёвый regex по git diff, работает и standalone
    (minor-defect-fix) через --diff-base.
    """
    added = [(f, t) for (f, t) in _git_diff_added(DIFF_BASE) if "src/main" in (f or "")]
    checks: list = []
    blocking_issues: list = []
    warnings: list = []

    if not added:
        checks.append({"name": "Изменённый production-код", "status": "SKIP",
                       "detail": f"нет добавленных строк в src/main (base={DIFF_BASE})",
                       "severity": "info"})
        return _make_verdict("reuse-judge", slug, True, checks, [], [],
                             "Нет production-изменений для проверки")

    deps = _load_reuse_deps()
    for name, pattern, replacement, libs in REUSE_PATTERNS:
        hits = []
        for f, t in added:
            if re.search(pattern, t):
                hits.append((f, t.strip()[:120]))
            if len(hits) >= 6:
                break
        if not hits:
            checks.append({"name": name, "status": "PASS", "detail": "не найдено",
                           "severity": "info"})
            continue
        sample = "; ".join(f"{f}: `{txt}`" for f, txt in hits)
        lib_available = any(any(l in d for d in deps) for l in libs) if libs else False
        if libs is not None and lib_available:
            checks.append({"name": name, "status": "FAIL",
                           "detail": f"{replacement} доступен в зависимостях; {sample}",
                           "severity": "error"})
            blocking_issues.append(f"{name}: используй {replacement} вместо велосипеда ({sample})")
        else:
            checks.append({"name": name, "status": "WARN",
                           "detail": f"рассмотри {replacement}; {sample}",
                           "severity": "warning"})
            warnings.append(f"{name}: рассмотри {replacement} ({sample})")

    passed = len(blocking_issues) == 0
    summary = f"{sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} checks passed."
    if blocking_issues:
        summary += f" {len(blocking_issues)} blocking issue(s)."
    if warnings:
        summary += f" {len(warnings)} warning(s)."
    return _make_verdict("reuse-judge", slug, passed, checks, blocking_issues, warnings, summary)


# ====== MAIN ======

PHASE_MAP = {
    "brd": check_brd,
    "reuse": check_reuse,
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
    parser.add_argument("--brd", default=None,
                        help="Явный путь к документу БТ для фазы brd (standalone-проверка, "
                             "напр. business-requirements/<slug>.md). Относительный путь — от --project-root.")
    parser.add_argument("--diff-base", default="HEAD",
                        help="База git diff для фазы reuse (по умолчанию HEAD).")
    parser.add_argument("--reuse-scan", default=None,
                        help="Путь к scan/reuse.json (каталог зависимостей) для фазы reuse "
                             "(по умолчанию docs/system-analysis/scan/reuse.json).")
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

    if args.brd:
        global BRD_OVERRIDE
        BRD_OVERRIDE = Path(args.brd)

    global DIFF_BASE, REUSE_SCAN_OVERRIDE
    DIFF_BASE = args.diff_base
    if args.reuse_scan:
        REUSE_SCAN_OVERRIDE = Path(args.reuse_scan)

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
        ok, verr = validate_verdict(subagent)
        if not ok:
            print("ERROR: вердикт субагента не прошёл валидацию схемы judge-verdict@1 "
                  "(fail-closed). Верни валидный JSON: обязателен boolean 'passed', "
                  "'checks' — массив {name,status∈PASS/FAIL/WARN/SKIP}, "
                  "'blocking_issues'/'warnings' — массивы.", file=sys.stderr)
            for e in verr:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(1)
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
        # Для детерминированных и гибридных фаз — полная проверка заново (не кэш).
        # build/delivery — гибрид: check_* загружает сохранённый LLM-вердикт и применяет
        # детерминированный пол (stubs/секреты), поэтому recheck обязан их пересчитывать.
        if args.phase in ("brd", "reuse", "eval", "spec", "red", "coverage", "build", "delivery"):
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

    # Подсказка: как вручную пропустить заблокированный гейт (последнее средство).
    # Однострочная команда — Qwen-рантайм надёжнее выполняет команды без переносов.
    if not verdict["passed"] and verdict.get("blocking_issues"):
        _ovr = Path(__file__).resolve().parents[2] / "pipeline-state" / "scripts" / "override_judge.py"
        print()
        print("  ℹ️  Гейт можно пропустить вручную (последнее средство, после 3 ре-итераций):")
        print(f"     python3 {_ovr} --judge {judge_name} "
              f"--feature {args.slug} --step-id <step-id> --reason \"<обоснование>\"")

    sys.exit(0 if verdict["passed"] else 1)


if __name__ == "__main__":
    main()