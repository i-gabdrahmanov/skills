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
    sdd        — проверка sdd.md (секции + Given-When-Then); закрывает шаг 02-sdd
    eval       — проверка eval-plan.json
    red        — проверка RED-тестов (только если есть файл вердикта от субагента)
    build      — проверка build-артефактов
    spec       — проверка spec-документов
    delivery   — проверка готовности к доставке (перед коммитом)
    coverage   — проверка JaCoCo-покрытия (закрывает шаг 05-tests)
    regression — тесты затронутых модулей не регрессировали vs baseline (module_tests compare)
    design     — check_taskplan + check_sdd (закрывает шаг 02-design)

Если --recheck указан, скрипт проверяет, что вердикт судьи на диске есть и passed=true.
Если вердикта нет или passed=false — exit 1 (блокировка).

Exit:
    0 — PASS (все проверки пройдены)
    1 — FAIL (блокирующие проблемы — чини и перезапусти, в пределах лимита ре-итераций)
    2 — ERROR (скрипт не может выполнить проверку — нет контекста, нет файлов)
    3 — ESCALATE (исчерпан лимит ре-итераций судьи: quality.max_judge_iterations, дефолт 3) —
        ОСТАНОВИСЬ и спроси пользователя (§0.6); НЕ гоняй судью снова и не правь прод/тесты ради GREEN
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

# Слои, непокрываемые юнит-тестами при test_layer=service-unit (data-holders / framework-generated):
# repository (Spring Data — тело генерит фреймворк), entity (data-класс), dto, config. Coverage-гейт
# не должен требовать их покрытия, иначе он конфликтует с tdd-guard (тот блокирует @DataJpaTest/
# @SpringBootTest — единственный способ покрыть репозиторий). Переопределяется в pipeline.json
# quality.coverage_exclude_globs (список или [] чтобы отключить исключения).
DEFAULT_SERVICE_UNIT_COVERAGE_EXCLUDES = [
    "*/repository/*", "*Repository.java",
    "*/entity/*", "*/entities/*", "*/domain/model/*", "*Entity.java",
    "*/dto/*", "*/config/*", "*/configuration/*",
]


def _set_paths(project_root: Path, skill: str = "feature-pipeline") -> None:
    """Устанавливает глобальные пути для скрипта (вызывается из main())."""
    global PROJECT_ROOT, GROUND_DIR, FEATURE_DOCS_DIR, SYSTEM_ANALYSIS_DIR, SKILL_NAME
    PROJECT_ROOT = project_root
    GROUND_DIR = project_root / "ground"
    # docs-расположение резолвится по ground/pipeline.json (in-repo / separate-repo)
    FEATURE_DOCS_DIR = skill_paths.feature_docs_dir(project_root)
    SYSTEM_ANALYSIS_DIR = skill_paths.system_analysis_dir(project_root)
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


def _max_iterations(project_root: Path) -> int:
    """Потолок ре-итераций судьи (pipeline.json quality.max_judge_iterations, дефолт 3)."""
    cfg = _load_json(project_root / "ground" / "pipeline.json") or {}
    try:
        n = int(cfg.get("quality", {}).get("max_judge_iterations", 3))
        return n if n > 0 else 3
    except (TypeError, ValueError):
        return 3


def _judge_iteration_count(slug: str, judge_name: str) -> int:
    """Сколько раз ЭТОТ судья уже падал (по errors.json) — для лимита ре-итераций."""
    store = _load_json(_find_errors_store(slug)) or {}
    return sum(1 for it in store.get("iterations", []) if it.get("judge") == judge_name)


def _maybe_escalate(slug: str, judge_name: str, project_root: Path) -> bool:
    """После сохранения FAIL: исчерпан ли лимит ре-итераций? Если да — печатает STOP-баннер.

    Возвращает True, если оркестратор обязан ОСТАНОВИТЬСЯ и спросить пользователя (exit 3),
    а не молча гонять судью снова (на прогоне #3 модель крутила Verify 1.5 часа без тормоза).
    """
    limit = _max_iterations(project_root)
    count = _judge_iteration_count(slug, judge_name)
    if count < limit:
        return False
    print()
    print(f"⛔ STOP: {judge_name} провалился {count} раз (лимит {limit}). "
          "Авто-ре-итерации исчерпаны.")
    print("   НЕ запускай судью снова и НЕ правь прод-код/существующие тесты ради зелёного.")
    print("   Остановись и спроси пользователя (§0.6): (a) сброс errors.json и заново; "
          "(b) отмена шага; (c) ручной override — только если причина внешняя и неустранима.")
    print(f"⛔ ESCALATE: {judge_name} {count}/{limit} iterations exhausted — stop and ask user",
          file=sys.stderr)
    return True


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
        # Провенанс: update._check_judges требует это поле — отсекает рукописные/поддельные
        # вердикты (правило «перезапусти судью, не правь файл руками»).
        "produced_by": "run_judge",
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

    # test_pass — бинарный gate (exit-код, «вся сюита зелёная»), а не ratio-порог.
    # Рантайм (eval-guard/run_pending_evals) смотрит только returncode, поэтому валидируем
    # не порог, а наличие непустой команды у каждого test_pass eval.
    tp_evals = [e for e in evals if e["type"] == "test_pass"]
    tp_no_cmd = [e.get("id", "?") for e in tp_evals if not str(e.get("command", "")).strip()]
    if tp_no_cmd:
        blocking_issues.append(f"test_pass eval'ы без команды: {tp_no_cmd}")
        checks.append({
            "name": "Test_pass evals have command",
            "status": "FAIL",
            "detail": f"Без команды: {tp_no_cmd}",
            "severity": "error",
        })
    else:
        checks.append({
            "name": "Test_pass evals have command",
            "status": "PASS",
            "detail": f"У всех {len(tp_evals)} test_pass есть команда (бинарный gate)",
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


def _canon_module(m: "str | None") -> "str | None":
    """Канонический ключ модуля: 'service:taskservice'/'service-taskservice' → 'service-taskservice'."""
    if m is None:
        return None
    return m.strip().lower().replace(":", "-")


def _module_from_test_path(path: str) -> "str | None":
    """Выводит модуль из пути тест-артефакта.

    'service/taskservice/src/test/java/...' → 'service-taskservice'.
    Если 'src' в корне (одномодульный проект) → None (корневой gradle).
    """
    parts = path.replace("\\", "/").split("/")
    if "src" in parts:
        i = parts.index("src")
        if i >= 2:
            return f"{parts[i - 2]}-{parts[i - 1]}".lower()
        if i >= 1:
            return parts[i - 1].lower()
    return None


def _class_glob_from_path(path: str) -> str:
    """Путь тест-файла → gradle --tests glob: '.../FooTest.java' → '*FooTest'."""
    base = path.replace("\\", "/").split("/")[-1]
    if base.endswith(".java"):
        base = base[:-5]
    return f"*{base}"


def _feature_test_classes(feature_dir: Path | None, slug: str) -> dict:
    """Тест-классы ИМЕННО этой фичи, сгруппированные по модулю.

    Возвращает {canonical-module-key|None: sorted ['*ClassA', ...]}.
    Источники: task-plan tasks[].artifacts (src/test *.java) + output шагов 04-test-*
    (поле test_files тестописателя). Нужно, чтобы red-judge гонял только тесты фичи,
    а не все тесты модуля (иначе чужой упавший тест роняет судью всей фичи).
    """
    result: dict = {}

    def add(modkey, glob):
        result.setdefault(modkey, set()).add(glob)

    def ingest_artifact(art, modkeys):
        if not isinstance(art, str):
            return
        norm = art.replace("\\", "/")
        if "src/test" not in norm or not norm.endswith(".java"):
            return
        glob = _class_glob_from_path(norm)
        keys = modkeys if modkeys else [_module_from_test_path(norm)]
        for k in keys:
            add(k, glob)

    # 1. task-plan artifacts
    if feature_dir:
        tp_path = feature_dir / "task-plan.json"
        if tp_path.exists():
            try:
                tp = json.loads(tp_path.read_text())
            except (json.JSONDecodeError, OSError):
                tp = {}
            for task in tp.get("tasks", []):
                modkeys = []
                mods = task.get("modules")
                if isinstance(mods, list):
                    modkeys = [_canon_module(m) for m in mods if m]
                elif isinstance(task.get("module"), str) and task["module"]:
                    modkeys = [_canon_module(task["module"])]
                for art in task.get("artifacts", []):
                    ingest_artifact(art, modkeys)

    # 2. output шагов 04-test-* (test_files от тестописателя) — best-effort
    if PROJECT_ROOT:
        stmts = PROJECT_ROOT / "ground" / "statements" / SKILL_NAME / slug
        if stmts.is_dir():
            for outp in stmts.glob("04-test-*.json"):
                try:
                    data = json.loads(outp.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                for tf in data.get("test_files", []):
                    ingest_artifact(tf, [])

    return {k: sorted(v) for k, v in result.items()}


def check_red(slug: str, feature_dir: Path | None) -> dict:
    """Проверка RED-тестов: запускает тесты фичи (Gradle --tests / Maven -Dtest по build-системе
    из pipeline.json) с фильтром по тест-классам фичи и проверяет exit code."""
    import subprocess

    project_root = PROJECT_ROOT
    pipeline_json_path = project_root / "ground" / "pipeline.json"

    # Тест-классы ИМЕННО этой фичи, сгруппированные по модулю. red-judge гонит ТОЛЬКО их,
    # а не все тесты модуля — иначе чужой упавший тест роняет судью всей фичи
    # (DEBAG-ORDERS P1/P2: KafkaSendEventListenerTest валил фичу). Ключ — канонический
    # модуль ('service-taskservice') или None (корень одномодульного проекта).
    feat_classes = _feature_test_classes(feature_dir, slug)

    # Читаем test_layer + build-систему из pipeline.json (default=service-unit / gradle)
    test_layer = "service-unit"
    build_system = "gradle"
    if pipeline_json_path.exists():
        try:
            with open(pipeline_json_path) as f:
                _pcfg = json.load(f)
            test_layer = _pcfg.get("quality", {}).get("test_layer", test_layer)
            _bs = _pcfg.get("project", {}).get("build_system")
            if _bs in ("gradle", "maven"):
                build_system = _bs
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

    # Нет тест-классов фичи → НЕ гнать все тесты модуля (fallback: WARN + пропуск прогона).
    # Иначе несвязанный упавший тест модуля заблокировал бы фичу.
    if not feat_classes:
        warnings.append(
            "red-judge: не удалось определить тест-классы фичи из task-plan/04-test-* — "
            "прогон gradle пропущен (чтобы не валить фичу на несвязанных тестах модуля)"
        )
        checks.append({
            "name": "test:scope",
            "status": "WARN",
            "detail": "нет тест-классов фичи; прогон gradle пропущен",
            "severity": "warning",
        })

    # Собираем «работы» по build-системе: Gradle — по модулю (--tests glob), Maven — один
    # прогон surefire по всем тест-классам фичи (-Dtest=...). Раньше тут был жёсткий ./gradlew,
    # из-за чего на Maven RED-judge падал «gradlew not found» (P1-16).
    jobs: list[tuple[str, list]] = []
    if build_system == "maven":
        all_globs = sorted({g for globs in feat_classes.values() for g in globs})
        if all_globs:
            jobs.append(("maven", [
                "mvn", "-q", "test",
                f"-Dtest={','.join(all_globs)}",
                "-Dsurefire.failIfNoSpecifiedTests=false",
            ]))
    else:
        for module in feat_classes:
            test_flags = []
            for glob in feat_classes[module]:
                test_flags += ["--tests", glob]
            if module is None:
                # Одномодульный проект — тесты в корне (без :module: префикса).
                jobs.append(("root", ["./gradlew", "test", *test_flags, "--no-daemon"]))
            else:
                # Нормализация имени модуля: 'service-taskservice' → ':service:taskservice'.
                gradle_path = module
                if gradle_path.startswith(":"):
                    pass  # уже нормализован
                elif ":" in gradle_path:
                    gradle_path = f":{gradle_path}"
                else:
                    parts = gradle_path.rsplit("-", 1)
                    if len(parts) == 2:
                        gradle_path = f":{parts[0]}:{parts[1]}"
                    else:
                        gradle_path = f":{gradle_path}"
                jobs.append((module, ["./gradlew", f"{gradle_path}:test", *test_flags, "--no-daemon"]))

    runner_name = "mvn" if build_system == "maven" else "gradlew"
    for label, cmd in jobs:
        try:
            r = subprocess.run(
                cmd, cwd=str(project_root),
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
                "detail": f"{runner_name} not found",
                "severity": "error",
            })
            blocking_issues.append(f"RED-judge {label}: {runner_name} not found")
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

# Наследование тест-класса от базового и эвристика «интеграционной» базы.
# Тест из DEBAG-ORDERS P3 наследовался от BaseTest (с @SpringBootTest) — аннотации в самом
# файле нет, поэтому прямой греп её не ловил. Проверяем базовый класс транзитивно.
_EXTENDS_RE = re.compile(r"\bclass\s+\w+[^{]*\bextends\s+([A-Za-z_]\w*)")
_INTEGRATION_BASE_MARKERS = ("base", "abstract", "integration")


def _looks_integration_base(name: str) -> bool:
    """Имя базового класса намекает на интеграционный тест (Spring-контекст)?"""
    low = name.lower()
    if any(mark in low for mark in _INTEGRATION_BASE_MARKERS):
        return True
    return name.endswith("IT")  # *IT — типичный суффикс интеграционного теста


def _find_test_class_file(project_root: Path, simple_name: str) -> "Path | None":
    """Ищет <SimpleName>.java в src/test проекта (для транзитивной проверки базы)."""
    try:
        for p in project_root.rglob(f"{simple_name}.java"):
            if "src/test" in str(p).replace("\\", "/"):
                return p
    except OSError:
        return None
    return None


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

    hits = []            # определённые нарушения (прямая аннотация ИЛИ база с ней)
    base_warnings = []   # наследование от подозрительной базы, аннотацию не подтвердили
    base_cache: dict = {}  # simple_name → (annotated: bool|None)

    for p in test_artifacts:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # 1. Прямая аннотация в самом тест-файле
        direct = False
        for ln in txt.splitlines():
            if _FORBIDDEN_TEST_ANNOTATIONS.search(ln):
                hits.append(f"{p.name}: {ln.strip()[:100]}")
                direct = True
        if direct:
            continue  # уже зафиксировали — транзитивно не дублируем

        # 2. Транзитивно: наследование от интеграционной базы (с @SpringBootTest и т.п.)
        m = _EXTENDS_RE.search(txt)
        if not m:
            continue
        base = m.group(1)
        if not _looks_integration_base(base):
            continue
        if base not in base_cache:
            base_file = _find_test_class_file(project_root, base)
            annotated = None
            if base_file:
                try:
                    btxt = base_file.read_text(encoding="utf-8", errors="replace")
                    annotated = bool(_FORBIDDEN_TEST_ANNOTATIONS.search(btxt))
                except OSError:
                    annotated = None
            base_cache[base] = annotated
        annotated = base_cache[base]
        if annotated is True:
            hits.append(f"{p.name}: extends {base} (в базе @DataJpaTest/@SpringBootTest)")
        else:
            # База не найдена или без аннотации — только предупреждаем (без false-positive)
            base_warnings.append(
                f"{p.name}: extends {base} — проверь, не поднимает ли Spring-контекст; "
                f"для service-unit перепиши на Mockito"
            )

    check_name = "Нет @DataJpaTest/@SpringBootTest (прямо/через базу; test_layer=service-unit)"
    if hits:
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

    # Наследование от подозрительной базы — всегда advisory-warning (не блокируем по имени)
    for w in base_warnings[:5]:
        warnings.append(w)

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


def _delivery_floor() -> tuple:
    """Детерминированный пол delivery-judge: ловит секреты в изменённых файлах.

    Правила поиска — ЕДИНЫЙ источник `check_secrets.scan_text` (тот же сканер, что standalone-гейт
    P2-12), чтобы regex не двоился. best-effort импорт (co-located), inline-fallback на простой паттерн.
    """
    checks, blocking, warnings = [], [], []
    files = _changed_src_files(main_only=False)
    try:
        import check_secrets as _cs
        scan = _cs.scan_text
    except Exception:
        _fallback = re.compile(
            r"(?:password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token)\s*[=:]\s*"
            r"['\"][^'\"\n]{6,}['\"]|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I)

        def scan(path, text):
            return [{"file": path, "line": i, "kind": "secret", "detail": ln.strip()[:80]}
                    for i, ln in enumerate(text.splitlines(), 1) if _fallback.search(ln)]

    secrets = []
    for p in files:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for hit in scan(p.name, txt):
            secrets.append(f"{hit['file']}: {hit['detail']}")
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


def check_sdd_doc(slug: str, feature_dir: Path | None) -> dict:
    """Запускает sdd/scripts/check_sdd_doc.py (gate документа SDD) и собирает вердикт sdd-judge.

    Проверяет сам sdd.md (обязательные секции + Given-When-Then), без task-plan —
    он ещё не создан на фазе 02-sdd.
    """
    project_root = PROJECT_ROOT
    check_sdd_doc_script = skill_paths.script(project_root, "sdd", "check_sdd_doc")

    import subprocess

    sdd_path = feature_dir / "sdd.md" if feature_dir else None

    checks = []
    blocking_issues = []
    warnings = []

    if sdd_path and sdd_path.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(check_sdd_doc_script), str(sdd_path), "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                checks.append({"name": "check_sdd_doc", "status": "PASS",
                               "detail": sdd_path.name, "severity": "error"})
            else:
                detail = r.stderr.strip() or r.stdout.strip() or "exit code {}".format(r.returncode)
                checks.append({"name": "check_sdd_doc", "status": "FAIL",
                               "detail": detail[:200], "severity": "error"})
                blocking_issues.append(f"check_sdd_doc FAIL: {detail[:200]}")
        except subprocess.TimeoutExpired:
            checks.append({"name": "check_sdd_doc", "status": "FAIL",
                           "detail": "timeout (60s)", "severity": "error"})
            blocking_issues.append("check_sdd_doc: timeout")
    else:
        checks.append({"name": "check_sdd_doc", "status": "FAIL",
                       "detail": f"sdd.md not found at {sdd_path}", "severity": "error"})
        blocking_issues.append(f"SDD (sdd.md) не найден: {sdd_path}")

    passed = len(blocking_issues) == 0
    summary = f"{sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} checks passed"
    if blocking_issues:
        summary += f", {len(blocking_issues)} blocking"

    return _make_verdict("sdd-judge", slug, passed, checks, blocking_issues, warnings, summary)


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


# Floor «GREEN любой ценой» (C2): паттерны ослабления СУЩЕСТВУЮЩИХ тестов.
_TEST_DISABLED_RE = re.compile(r"@(?:Disabled|Ignore)\b")
_VERIFY_TIMES_RE = re.compile(r"\btimes\s*\(\s*(\d+)\s*\)")
_ASSERT_VERIFY_RE = re.compile(
    r"\b(?:assert\w*|assertThat|verify|verifyNoInteractions|verifyNoMoreInteractions|"
    r"thenThrow|expectThrows)\b"
)


def _git_diff_test_changes(base: str) -> dict:
    """Added/removed строки по `git diff --diff-filter=M` для МОДИФИЦИРОВАННЫХ тест-файлов.

    Только filter=M (изменения СУЩЕСТВУЮЩИХ тестов) — новые тест-файлы (A) трогать ок, риск
    «прогнул тест под зелёное» именно в правке уже написанного теста. Возвращает {path: {added, removed}}.
    """
    import subprocess
    try:
        r = subprocess.run(
            ["git", "diff", "--diff-filter=M", "--unified=0", base, "--",
             "*Test.java", "*Tests.java", "*IT.java", "*/src/test/*.java"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}
    if r.returncode != 0:
        return {}
    per_file: dict = {}
    cur = None
    for line in r.stdout.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            per_file.setdefault(cur, {"added": [], "removed": []})
        elif cur and line.startswith("+") and not line.startswith("+++"):
            per_file[cur]["added"].append(line[1:])
        elif cur and line.startswith("-") and not line.startswith("---"):
            per_file[cur]["removed"].append(line[1:])
    return per_file


def _test_integrity_floor(base: str = "HEAD") -> tuple:
    """Детерминированный floor фазы Verify: ослабление существующих тестов ради GREEN.

    BLOCK: добавлен @Disabled/@Ignore на ранее активный тест; рост times(N)→times(M) (ослаблена
    проверка числа вызовов под новое поведение). WARN: нетто-удаление assert/verify (возможная
    потеря проверок). Консервативно (только filter=M), чтобы не ловить легитимный рефактор тестов.
    """
    checks, blocking, warnings = [], [], []
    per_file = _git_diff_test_changes(base)
    if not per_file:
        checks.append({"name": "Целостность существующих тестов", "status": "PASS",
                       "detail": "нет правок существующих тест-файлов", "severity": "error"})
        return checks, blocking, warnings

    disabled_hits, weakened_verify, assertion_loss = [], [], []
    for path, ch in per_file.items():
        added, removed = ch["added"], ch["removed"]
        # 1. Добавлен @Disabled/@Ignore (и это не просто перенос существующей аннотации)
        if any(_TEST_DISABLED_RE.search(a) for a in added) and \
           not any(_TEST_DISABLED_RE.search(rm) for rm in removed):
            disabled_hits.append(path)
        # 2. Рост times(): max добавленный счётчик > max удалённый
        add_times = [int(m.group(1)) for a in added for m in _VERIFY_TIMES_RE.finditer(a)]
        rem_times = [int(m.group(1)) for rm in removed for m in _VERIFY_TIMES_RE.finditer(rm)]
        if add_times and rem_times and max(add_times) > max(rem_times):
            weakened_verify.append(f"{path}: times {max(rem_times)}→{max(add_times)}")
        # 3. Нетто-потеря assert/verify
        a_add = sum(1 for a in added if _ASSERT_VERIFY_RE.search(a))
        a_rem = sum(1 for rm in removed if _ASSERT_VERIFY_RE.search(rm))
        if a_rem - a_add >= 2:
            assertion_loss.append(f"{path}: -{a_rem - a_add} assert/verify")

    name = "Существующие тесты не ослаблены ради GREEN"
    blocking.extend(f"существующий тест отключён (@Disabled/@Ignore): {p}" for p in disabled_hits[:5])
    blocking.extend(f"ослаблена проверка числа вызовов: {w}" for w in weakened_verify[:5])
    if disabled_hits or weakened_verify:
        checks.append({"name": name, "status": "FAIL",
                       "detail": "; ".join(disabled_hits + weakened_verify)[:200],
                       "severity": "error"})
    else:
        checks.append({"name": name, "status": "PASS",
                       "detail": f"{len(per_file)} изменённых тест-файлов без ослабления",
                       "severity": "error"})
    for a in assertion_loss[:5]:
        warnings.append(f"возможная потеря проверок в существующем тесте: {a}")
    return checks, blocking, warnings


def check_coverage(slug: str, feature_dir: Path | None) -> dict:
    """Запускает check_coverage.py (JaCoCo gate) и собирает вердикт coverage-judge.

    Имя вердикта (coverage-judge.json) совпадает с required_judges['05-tests'].
    Плюс floor целостности тестов (C2): блокирует ослабление существующих тестов.
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
    quality_cfg = pipeline_cfg.get("quality", {}) if isinstance(pipeline_cfg, dict) else {}
    try:
        threshold = float(quality_cfg.get("coverage_threshold", threshold))
    except (TypeError, ValueError):
        pass

    # Исключения покрытия: явный список из pipeline.json, иначе дефолт для service-unit
    # (репозитории/энтити/dto/config не покрываемы юнит-тестом — см. DEFAULT_..._EXCLUDES).
    test_layer = quality_cfg.get("test_layer", "service-unit")
    cov_excludes = quality_cfg.get("coverage_exclude_globs")
    if cov_excludes is None:
        cov_excludes = DEFAULT_SERVICE_UNIT_COVERAGE_EXCLUDES if test_layer == "service-unit" else []

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
           "--threshold", str(threshold), "--strict", "--json"]
    for g in cov_excludes:
        cmd += ["--exclude", g]
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

    # check_coverage.py (--strict): exit 0 = pass, 2 = LOW/MISSING/JaCoCo-отчёт не найден
    cov_passed = r.returncode == 0
    detail = (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}")[:300]
    checks = [{
        "name": "check_coverage",
        "status": "PASS" if cov_passed else "FAIL",
        "detail": detail,
        "severity": "error",
    }]
    cov_block = [] if cov_passed else [f"Покрытие ниже порога {threshold}: {detail}"]

    # Floor «GREEN любой ценой»: ловит ослабление СУЩЕСТВУЮЩИХ тестов (фаза Verify — типичное
    # место, где модель гнёт тест/прод-код под зелёное). Блокирует, даже если покрытие прошло.
    floor_checks, floor_block, floor_warn = _test_integrity_floor(DIFF_BASE)
    checks += floor_checks
    blocking = cov_block + floor_block
    passed = cov_passed and not floor_block
    summary = (f"check_coverage exit {r.returncode} (порог {threshold})"
               + (f" | integrity: {len(floor_block)} blocking" if floor_block else " | integrity OK"))
    return _make_verdict("coverage-judge", slug, passed, checks, blocking, floor_warn, summary)


def check_regression(slug: str, feature_dir: Path | None) -> dict:
    """regression-judge (D): тесты ЗАТРОНУТЫХ модулей не должны регрессировать vs baseline.

    Гоняет `module_tests.py compare` (baseline снят на старте Build — SKILL §7). РЕГРЕССИЯ
    (ранее зелёный тест теперь падает) или невозможность прогнать модуль baseline → FAIL.
    Пре-существующие/infra-падения (красные и в baseline) НЕ блокируют. «Агент сломал тест → поймали».
    """
    project_root = PROJECT_ROOT
    mt_script = Path(__file__).resolve().parent / "module_tests.py"
    baseline = GROUND_DIR / "statements" / SKILL_NAME / slug / "test-baseline.json"

    if not mt_script.exists():
        return _make_verdict(
            "regression-judge", slug, False,
            [{"name": "module_tests.py доступен", "status": "FAIL",
              "detail": f"не найден {mt_script}", "severity": "error"}],
            ["module_tests.py не найден — регресс не проверить"], [],
            "REGRESSION-judge: скрипт отсутствует")

    import subprocess
    cmd = [sys.executable, str(mt_script), "compare", "--root", str(project_root),
           "--baseline", str(baseline), "--from-diff", DIFF_BASE, "--json"]
    try:
        r = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return _make_verdict(
            "regression-judge", slug, False,
            [{"name": "module_tests compare", "status": "FAIL",
              "detail": "timeout (1200s)", "severity": "error"}],
            ["regression: timeout прогона тестов затронутых модулей"], [],
            "REGRESSION-judge: timeout")

    data = {}
    if r.stdout.strip():
        try:
            data = json.loads(r.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            data = {}
    regressions = data.get("regressions", [])
    no_run = data.get("modules_without_results", [])
    passed = r.returncode == 0

    detail = (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}")[:300]
    checks = [{"name": "Нет регрессий тестов затронутых модулей",
               "status": "PASS" if passed else "FAIL", "detail": detail, "severity": "error"}]
    blocking = []
    blocking += [f"регрессия теста (был зелёным, теперь падает): {t}" for t in regressions[:10]]
    if no_run:
        blocking.append(f"не удалось прогнать тесты модулей: {', '.join(no_run)} — "
                        "нельзя подтвердить зелёное (fail-closed)")
    if not passed and not blocking:
        # exit 2 без распарсенного JSON (напр. baseline не снят — SKILL §7)
        blocking.append(f"regression-gate FAIL: {(r.stderr.strip() or r.stdout.strip() or 'exit 2')[:200]}")
    summary = f"module_tests compare exit {r.returncode}: регрессий {len(regressions)}"
    return _make_verdict("regression-judge", slug, passed, checks, blocking, [], summary)


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
        SYSTEM_ANALYSIS_DIR / "scan" / "reuse.json")
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
    "sdd": check_sdd_doc,
    "reuse": check_reuse,
    "eval": check_eval,
    "red": check_red,
    "build": check_build,
    "spec": check_spec,
    "delivery": check_delivery,
    "design": check_design,
    "coverage": check_coverage,
    "regression": check_regression,
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

    # docs-пути уже резолвлены в _set_paths по docs-конфигу; CLI-флаги переопределяют точечно
    if args.feature_docs:
        global FEATURE_DOCS_DIR
        FEATURE_DOCS_DIR = Path(args.feature_docs).resolve()

    if args.system_analysis_dir:
        global SYSTEM_ANALYSIS_DIR
        SYSTEM_ANALYSIS_DIR = Path(args.system_analysis_dir).resolve()

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
        if args.phase in ("brd", "reuse", "eval", "spec", "red", "coverage", "regression", "build", "delivery"):
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
            if not verdict["passed"] and _maybe_escalate(args.slug, f"{args.phase}-judge", project_root):
                sys.exit(3)
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

    if not verdict["passed"] and _maybe_escalate(args.slug, judge_name, project_root):
        sys.exit(3)
    sys.exit(0 if verdict["passed"] else 1)


if __name__ == "__main__":
    main()