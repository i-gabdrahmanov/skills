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
        [--allow-invariants | --strict-invariants] [--invariant-pattern REGEX]
Exit: 0 = pass (есть RED-тесты / нет задач с main-слоем)
      2 = fail (тесты не компилируются / есть зелёные / не написаны / нет JUnit-отчётов)

KIDPPRB-9254 п.3: при наличии в задаче тестов-ИНВАРИАНТОВ (проверяют поведение,
которое НЕ меняется — напр. `// invariant:` или `@DisplayName("INVARIANT: ...")`)
RED-гейт не должен валиться из-за их зелёного состояния. Включается флагом
`--allow-invariants` или централизованно через `quality.red_gate.allow_invariants: true`
в pipeline.json. Прочие зелёные (вакуумные) по-прежнему валят RED.
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

# pipeline_phases co-located (тот же каталог) — единый предикат test-exemption
_SELF_DIR = Path(__file__).resolve().parent
if str(_SELF_DIR) not in sys.path:
    sys.path.insert(0, str(_SELF_DIR))
import pipeline_phases

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


def _red_gate_settings(project_root: Path, cli_allow: bool | None, cli_pattern: str | None,
                       cfg: dict | None) -> tuple[bool, str]:
    """Резолв allow_invariants / invariant_pattern: CLI > pipeline.json > дефолт.

    Источник cfg (уже загруженный через --pipeline-config), фолбэк — ground/policy.json
    (legacy: ground/pipeline.json) от project_root. quality.red_gate.{allow_invariants,
    invariant_pattern}."""
    allow = cli_allow if cli_allow is not None else False
    pattern = cli_pattern if cli_pattern else junit_report.DEFAULT_INVARIANT_PATTERN
    rg = ((cfg or {}).get("quality") or {}).get("red_gate") or {}
    if isinstance(rg, dict):
        if cli_allow is None and isinstance(rg.get("allow_invariants"), bool):
            allow = rg["allow_invariants"]
        if not cli_pattern and isinstance(rg.get("invariant_pattern"), str) and rg["invariant_pattern"].strip():
            pattern = rg["invariant_pattern"].strip()
    if cli_allow is not None or cli_pattern:
        return allow, pattern  # CLI задан явно — не лезем в ФС
    # Фолбэк к ground/{policy.json|pipeline.json} от project_root (если --pipeline-config не дан).
    # Phase 0 v2 refactor: двойной рид через hooks/_config_loader.load_project_config.
    try:
        # Прямой импорт канонического reader (не через fragile re-export chain).
        import sys
        from pathlib import Path as _Path
        _HOOKS = _Path(__file__).resolve().parents[3] / "hooks"
        if str(_HOOKS) not in sys.path:
            sys.path.insert(0, str(_HOOKS))
        from _config_loader import load_project_config  # noqa: E402
        cfg2 = load_project_config(project_root) or {}
        rg2 = (cfg2.get("quality") or {}).get("red_gate") or {}
    except Exception:
        return allow, pattern
    if isinstance(rg2, dict):
        if isinstance(rg2.get("allow_invariants"), bool):
            allow = rg2["allow_invariants"]
        if isinstance(rg2.get("invariant_pattern"), str) and rg2["invariant_pattern"].strip():
            pattern = rg2["invariant_pattern"].strip()
    return allow, pattern


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


def _module_to_path(module: str) -> str:
    """Gradle/Maven-имя модуля → относительный файловый путь.

    'service:taskservice' / ':service:taskservice' / 'service-taskservice' → 'service/taskservice'.
    'taskservice' → 'taskservice' (одно-сегментный). Этот путь мы добавляем к project_root
    как корень модуля и передаём в junit_report.collect(roots=…)."""
    m = module.strip().lstrip(":")
    # Сначала ':' (gradle group:artifact), потом '-' (maven artifactId-стиль group-artifact).
    return m.replace(":", "/").replace("-", "/")


def _extract_tasks(plan: dict, task_filter: str | None = None, cfg: dict | None = None) -> list[dict]:
    """Задачи, для которых нужен RED: пишут реальный код (src/main/java) И не освобождены.

    Освобождение — единый предикат pipeline_phases (task.no_test / quality.no_test_layers). Раньше
    триггер был по подстроке 'src/main' в артефакте, из-за чего задачи-миграции (changeset в
    src/main/resources) ложно затягивались в RED-гейт.
    """
    tasks = plan.get("tasks", [])
    if task_filter:
        tasks = [t for t in tasks if t.get("id") == task_filter]
    cfg = cfg or {}
    return [t for t in tasks
            if pipeline_phases.task_touches_code(t)
            and not pipeline_phases.task_is_test_exempt(t, cfg)]


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
    ap.add_argument("--module-roots", help="скоуп JUnit-сканирования на конкретные модули "
                                           "(запятая-раздельно; масштабирование моно-репо, п.4)")
    ap.add_argument("--scope-module",
                    help="имя модуля из task-plan (например 'service:taskservice' или "
                         "'service-taskservice'); собирает JUnit ТОЛЬКО в этом модуле "
                         "(резолвится через tasks[].modules → файловый путь, см. "
                         "_module_to_path). Если задан — перекрывает --module-roots. "
                         "KIDPPRB-9254 п.4: лечит таймаут scan на моно-репо.")
    ap.add_argument("--tolerate-green", action="store_true",
                    help="не валить RED из-за зелёных, если есть ≥1 красный (инварианты чужих модулей, "
                         "п.3; ГРУБЫЙ режим — пропускает ЛЮБЫЕ зелёные; для точечного см. "
                         "--allow-invariants)")
    ap.add_argument("--allow-invariants", dest="allow_invariants", action="store_true",
                    default=None,
                    help="пропустить зелёные тесты с маркером 'INVARIANT' в имени (точечный "
                         "режим, KIDPPRB-9254 п.3). Дефолт берётся из "
                         "quality.red_gate.allow_invariants в policy.json (legacy: pipeline.json), "
                         "при отсутствии — False")
    ap.add_argument("--strict-invariants", dest="allow_invariants", action="store_false",
                    help="явно ЗАПРЕТИТЬ allow_invariants (override policy.json|pipeline.json)")
    ap.add_argument("--invariant-pattern", default=None,
                    help="паттерн имени для инвариантов (case-insensitive regex). "
                         "Дефолт: 'INVARIANT' либо quality.red_gate.invariant_pattern "
                         "из policy.json (legacy: pipeline.json)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    plan = _load_json(args.plan) or {}
    cfg = _load_json(args.pipeline_config or "") or {}

    # allow_invariants / invariant_pattern: CLI > policy.json|pipeline.json > дефолт.
    # Источник для дефолта — quality.red_gate в переданном config (или ground/{policy.json|pipeline.json}
    # от --project, если --pipeline-config не задан).
    allow_inv, inv_pattern = _red_gate_settings(
        root, args.allow_invariants, args.invariant_pattern, cfg)

    # Команды компиляции/тестов и синтаксис фильтра — по build-системе (Gradle/Maven)
    build_system = _build_system(cfg)
    compile_cmd = _resolve_compile_test_cmd(cfg, args.compile_cmd)
    test_cmd = _resolve_test_cmd(cfg, args.test_cmd)
    test_filter = args.test_filter
    full_test_cmd = _apply_test_filter(test_cmd, build_system, test_filter)

    # Скоуп JUnit-сканирования на конкретные модули (моно-репо): если задан, не обходим весь
    # корень, а сканируем только корни модулей. Побочный плюс — чистый RED-вердикт (инварианты
    # чужих модулей не попадают в зелёные).
    module_roots: list[Path] | None = None
    if args.scope_module:
        # --scope-module: имя из task-plan.tasks[].modules (например 'service:taskservice').
        # Резолвим: 1) ищем точное совпадение в plan.tasks[].modules; 2) если нет — берём
        # как есть и нормализуем _module_to_path; 3) если путь не существует — fallback к
        # имени как относительному пути (warning, не fatal — лучше сканировать что-то,
        # чем весь репо).
        plan_mods: set[str] = set()
        for t in plan.get("tasks", []):
            mods = t.get("modules") or []
            if isinstance(mods, list):
                plan_mods.update(m for m in mods if isinstance(m, str) and m)
            elif isinstance(mods, str) and mods:
                plan_mods.add(mods)
        scope = args.scope_module.strip()
        if scope in plan_mods or any(_module_to_path(m) == _module_to_path(scope)
                                     for m in plan_mods):
            rel = _module_to_path(scope)
            module_roots = [root / rel]
        else:
            # Не нашли в plan.tasks[].modules: всё равно пробуем как путь (CLI-override).
            rel = _module_to_path(scope)
            module_roots = [root / rel]
            print(f"RED gate: warning — --scope-module '{scope}' не найден в "
                  f"task-plan.tasks[].modules; используем как путь '{rel}'.")
    elif args.module_roots:
        module_roots = [root / part for part in args.module_roots.split(",") if part.strip()]

    tasks = _extract_tasks(plan, args.task, cfg)
    if not tasks:
        print(f"RED gate: PASS (нет задач, требующих тестов — не код/освобождены), filter={args.task}")
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
        "allow_invariants": allow_inv,
        "invariant_pattern": inv_pattern,
        "scope_module": args.scope_module,
        "module_roots": [str(p) for p in module_roots] if module_roots else None,
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
        t = junit_report.summarize(root, since=started - 2, roots=module_roots)
        executed = len(t["red"]) + len(t["green"])
        result["tests_ran"] = executed > 0
        result["tests_total"] = executed
        result["tests_red"] = len(t["red"])
        result["tests_green"] = len(t["green"])
        if t["green"]:
            result["green_tests"] = t["green"][:10]
        result["test_failed"] = executed > 0 and not t["red"]

        hint = ("Gradle: --tests 'FooTest'; Maven: -Dtest=FooTest "
                f"(сюда — через --test-filter, сейчас '{test_filter}')")
        red_fail = junit_report.red_reason(t, hint_scope=hint,
                                           allow_invariants=allow_inv,
                                           invariant_pattern=inv_pattern,
                                           tolerate_green=args.tolerate_green)
        if red_fail is None:
            result.update(
                status="pass",
                verdict="pass: RED (compile OK + тесты прогона падают)",
                reason=f"компиляция OK; {len(t['red'])}/{executed} выполненных тестов "
                       f"красные (rc={rc_test}) — корректное RED-состояние.",
            )
        else:
            result.update(
                status="fail",
                verdict=("fail: no tests executed" if executed == 0 and t["reports"]
                         else "fail: GREEN only (нет красных)" if t["green"] and not t["red"]
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