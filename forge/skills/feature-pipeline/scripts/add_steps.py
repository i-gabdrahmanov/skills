#!/usr/bin/env python3
from __future__ import annotations
"""
Добавляет шаги в manifest.json пайплайна.
Идемпотентно: если шаг с таким id уже есть — не перезаписывает (кроме status).

Синхронизировать нечего: фазовое состояние ВЫЧИСЛЯЕТСЯ из манифеста
(pipeline_phases.live_state), поэтому новые шаги (02-eval-plan, 04-test-*)
видны фазовой машине сразу. Прежние gate.json/phase-defs.json были кэшем этой
же деривации — их приходилось перестраивать здесь, и пропуск ребилда делал
новые шаги невидимыми.

Usage:
    python3 add_steps.py --skill feature-pipeline --feature <slug> --steps '<json_array>'
                  [--task-plan <path>]

Пример steps:
    [{"id":"04-test-T1","title":"TDD RED: T1","depends_on":["02-design"]}]

Коды возврата:
    0 — шаги добавлены / идемпотентно пропущены
    1 — ошибка инфраструктуры (нет манифеста, битый JSON, I/O)
    2 — ошибка валидации task-id (неизвестный / малформедный; см. п.9 KIDPPRB-9254)
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_paths  # find_project_root — ре-экспорт из hooks/_project


def _resolve_root(project_root: Path | None) -> Path:
    """Корень проекта: явный --project > live-маркеры (.git/build.gradle/policy.json|pipeline.json).
    Без подъёма по маркерам вызов из постороннего каталога ломал резолв ground/."""
    return project_root or skill_paths.find_project_root()


def get_manifest_path(skill: str, feature: str, project_root: Path | None = None) -> Path:
    root = _resolve_root(project_root)
    return (
        root
        / "ground"
        / "statements"
        / skill
        / feature
        / "manifest.json"
    )


# Единый источник истины фаз/судей — pipeline_phases.
import pipeline_phases as pp

PREFIX_PHASE = pp.PREFIX_PHASE
MAIN_PHASES = pp.MAIN_PHASES
REQUIRED_JUDGES_MASK = pp.REQUIRED_JUDGES_MASK
_match_required_judges = pp.match_required_judges
_guess_phase = pp.guess_phase

# Зарезервированные/шаблонные суффиксы id динамических шагов TDD/GREEN, которые НЕ являются
# реальным task-id из task-plan. Прецедент (KIDPPRB-9254): оркестратор подставил «task» в id
# 04-test-task / 04-build-task вместо 04-test-T1 / 04-build-T1 — фазовый префикс не дал visible,
# и в манифесте повисли дубликаты.
_TASK_STEP_PREFIXES = ("04-test-", "04-build-")
_RESERVED_TASK_SUFFIXES = {"task", "tasks", "taskplan", "task-plan", ""}

# Формат task-id: латиница, старт с буквы, дальше буквы/цифры/-/_; длина 3..64.
# Покрывает шаблоны: T1 / T-12 / task-foo / KIDPPRB-9254-1. Точка как разделитель (1.2.3)
# отсечена намеренно — task-id не иерархический, и точка ломает glob/grep по манифесту.
_TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_TASK_ID_MIN_LEN = 3
_TASK_ID_MAX_LEN = 64


def _task_from_id(step_id: str) -> str | None:
    """Если id — динамический шаг задачи (04-test-<task> / 04-build-<task>), вернуть суффикс
    (task-id); иначе None (не задача-шаг — контейнер/документ, валидация не нужна)."""
    for prefix in _TASK_STEP_PREFIXES:
        if isinstance(step_id, str) and step_id.startswith(prefix):
            return step_id[len(prefix):]
    return None


def _validate_task_id_format(suf: str) -> str | None:
    """Формат-чек task-id-суффикса: regex ^[A-Za-z][A-Za-z0-9_-]*$, длина 3..64.
    Возвращает причину отказа или None."""
    if not suf:
        return None  # пустой суффикс ловится reserved-suffix чеком отдельно
    if len(suf) < _TASK_ID_MIN_LEN or len(suf) > _TASK_ID_MAX_LEN:
        return (f"task-id '{suf}' имеет недопустимую длину {len(suf)} "
                f"(требуется {_TASK_ID_MIN_LEN}-{_TASK_ID_MAX_LEN})")
    if not _TASK_ID_RE.match(suf):
        return (f"task-id '{suf}' имеет недопустимый формат "
                f"(ожидается ^[A-Za-z][A-Za-z0-9_-]*$, {_TASK_ID_MIN_LEN}-"
                f"{_TASK_ID_MAX_LEN} символов)")
    return None


def _validate_step_task_ids(steps: list) -> str | None:
    """Базовая проверка суффиксов задачи в id шага. Возвращает причину отказа или None.

    Ловит зарезервированные/шаблонные суффиксы (дают самопальные id вместо настоящих
    task-id) и формат-нарушения (слишком короткий/длинный, спецсимволы). Регистр task-id
    не нормализуем — task-plan хранит как есть (п.9 KIDPPRB-9254)."""
    for step in steps:
        suf = _task_from_id(step.get("id", ""))
        if suf is None:
            continue
        if suf.strip().lower() in _RESERVED_TASK_SUFFIXES:
            return (f"id шага '{step.get('id')}' использует зарезервированный суффикс "
                    f"'{suf}' — должен быть реальным task-id из task-plan (например 04-test-T1, "
                    f"04-build-T1). Уточни id от task-plan.json.")
        fmt_err = _validate_task_id_format(suf)
        if fmt_err:
            return f"id шага '{step.get('id')}': {fmt_err}"
    return None


def _validate_step_task_plan(steps: list, plan: dict, feature: str = "") -> str | None:
    """Сверка суффиксов id задачи с task-plan (если передан). Возвращает причину или None.

    Сообщение об ошибке — формат 'Task X not found in task-plan for feature Y; available: ...',
    чтобы оператор сразу видел, какой task-id валиден (раньше просто перечислялся список)."""
    task_ids = {str(t.get("id", "")) for t in plan.get("tasks", [])} if isinstance(plan, dict) else set()
    if not task_ids:
        return None
    for step in steps:
        suf = _task_from_id(step.get("id", ""))
        if suf is not None and suf not in task_ids:
            avail = ", ".join(sorted(task_ids))
            feat_suffix = f" for feature {feature}" if feature else ""
            return (f"Task '{suf}' not found in task-plan{feat_suffix}; "
                    f"available: {avail}")
    return None


def _find_task_plan(skill: str, feature: str, project_root: Path) -> tuple[dict | None, bool]:
    """Авто-резолв task-plan.json по фиче. Возвращает (plan, found).
    found=False — план не найден ни в одном из канонических путей; вызывающий решает,
    warn-ить и пропустить валидацию. found=True при найденном (в т.ч. нечитаемом) плане."""
    candidates = [
        project_root / "docs" / skill / feature / "task-plan.json",
        project_root / "ground" / "statements" / skill / feature / "task-plan.json",
        project_root / ".gigacode" / "pipeline-state" / feature / "task-plan.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                return json.loads(c.read_text(encoding="utf-8")), True
            except (json.JSONDecodeError, OSError) as e:
                print(f"[add_steps] WARNING: task-plan {c} повреждён ({e}); "
                      f"строгая сверка task-id пропущена.", file=sys.stderr)
                return None, True
    return None, False


def add_steps(skill: str, feature: str, steps: list, task_plan: dict | None = None) -> dict:
    """Добавить шаги в manifest.json. Возвращает dict с ключами status/error/...

    error_class (если status='error'):
      - 'validation' — task-id неизвестный / малформедный → CLI exit 2
      - 'infra'      — manifest не найден / битый JSON / I/O → CLI exit 1
    """
    manifest_path = get_manifest_path(skill, feature)

    if not manifest_path.exists():
        return {"status": "error", "error_class": "infra",
                "error": f"Manifest not found: {manifest_path}"}

    # Валидация id (п.9): формат-чек + reserved-суффиксы — всегда; при наличии task-plan —
    # строгая сверка реальных task-id.
    base_err = _validate_step_task_ids(steps)
    if base_err:
        return {"status": "error", "error_class": "validation", "error": base_err}
    plan_err = _validate_step_task_plan(steps, task_plan, feature=feature)
    if plan_err:
        return {"status": "error", "error_class": "validation", "error": plan_err}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"status": "error", "error_class": "infra",
                "error": f"Manifest повреждён ({manifest_path}): {e}"}
    existing_ids = {s["id"] for s in manifest.get("steps", [])}

    added = 0
    skipped = 0

    for step in steps:
        if step["id"] in existing_ids:
            skipped += 1
            continue
        step["status"] = "pending"
        step["attempts"] = 0
        # Применяем required_judges по той же маске, что и init.py
        req = _match_required_judges(step["id"])
        if req:
            step["required_judges"] = req
        manifest["steps"].append(step)
        added += 1

    if added > 0:
        manifest["last_update"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # Фазовое состояние никуда не синхронизируется: оно ВЫЧИСЛЯЕТСЯ из манифеста
        # (pipeline_phases.live_state). Прежние gate.json/phase-defs.json были кэшем
        # этой же деривации и требовали ребилда на каждое изменение шагов.
        decision = pp.live_phase_decision(manifest)

        return {
            "status": "ok",
            "manifest": str(manifest_path),
            "added": added,
            "skipped": skipped,
            "total": len(manifest["steps"]),
            "current_phase": decision["current_phase"],
            "phase_count": len(decision["phases"]),
        }

    return {
        "status": "ok",
        "manifest": str(manifest_path),
        "added": added,
        "skipped": skipped,
        "total": len(manifest["steps"]),
        "gate_synced": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--project-root", default=None, help="Корень проекта (по умолчанию cwd)")
    parser.add_argument("--steps", required=True, help="JSON array string")
    parser.add_argument("--task-plan", default=None,
                        help="Путь к task-plan.json — строгая сверка task-id в id шагов (п.9). "
                             "Если не указан, ищем план в docs/<skill>/<feature>/task-plan.json, "
                             "ground/statements/<skill>/<feature>/task-plan.json, "
                             ".gigacode/pipeline-state/<feature>/task-plan.json.")
    parser.add_argument("--strict-task-plan", action="store_true",
                        help="Если план не найден — exit 2 вместо warning+продолжения (для CI/гейтов).")
    args = parser.parse_args()

    try:
        steps = json.loads(args.steps)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error_class": "infra",
                          "error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    # Резолв task-plan: явный --task-plan > авто-резолв от --feature.
    plan = None
    if args.task_plan:
        p = Path(args.task_plan)
        try:
            if p.exists():
                plan = json.loads(p.read_text(encoding="utf-8"))
            else:
                p2 = _resolve_root(args.project_root and Path(args.project_root)) / args.task_plan
                if p2.exists():
                    plan = json.loads(p2.read_text(encoding="utf-8"))
                else:
                    print(f"[add_steps] WARNING: --task-plan {p} не найден; "
                          f"строгая сверка task-id пропущена.", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[add_steps] WARNING: --task-plan {p} повреждён ({e}); "
                  f"строгая сверка task-id пропущена.", file=sys.stderr)
    else:
        # Авто-резолв: ищем план для текущей фичи. Не нашли → warning в stderr,
        # продолжаем без строгой сверки (п.5: task-plan может генерироваться позже).
        # --strict-task-plan заставляет валиться с exit 2 — для CI/гейта.
        root = _resolve_root(args.project_root and Path(args.project_root))
        plan, plan_found = _find_task_plan(args.skill, args.feature, root)
        if not plan_found:
            msg = (f"[add_steps] WARNING: task-plan.json для feature '{args.feature}' не найден "
                   f"(искали в docs/{args.skill}/{args.feature}/, "
                   f"ground/statements/{args.skill}/{args.feature}/, "
                   f".gigacode/pipeline-state/{args.feature}/). "
                   f"Строгая сверка task-id пропущена.")
            if args.strict_task_plan:
                print(json.dumps({"status": "error", "error_class": "validation",
                                  "error": msg}))
                sys.exit(2)
            print(msg, file=sys.stderr)
        elif plan is None:
            # Найден, но нечитаемый — _find_task_plan уже напечатал WARNING.
            pass

    result = add_steps(args.skill, args.feature, steps, task_plan=plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "ok":
        sys.exit(0)
    # error_class определяет exit code: validation=2, infra=1
    sys.exit(2 if result.get("error_class") == "validation" else 1)
