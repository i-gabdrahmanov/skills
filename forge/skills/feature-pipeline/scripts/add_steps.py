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

# Формат task-id: латиница, старт с буквы, дальше буквы/цифры/-/_; длина 2..64.
# Покрывает шаблоны: T1 / T-12 / task-foo / KIDPPRB-9254-1. Точка как разделитель (1.2.3)
# отсечена намеренно — task-id не иерархический, и точка ломает glob/grep по манифесту.
# Нижняя граница была 3, и это отбивало РОВНО тот id, который во всех доках служит
# каноническим примером: `T1` (task-plan-schema.md, 🚨-баннер 02-design.md, докстринг этого
# файла). Любая фича с задачами T1/T2 падала на add_steps с exit 2 «недопустимая длина».
# Граница нужна не против коротких id, а против вырожденных — а вырожденные («task»,
# пустой суффикс) ловит _RESERVED_TASK_SUFFIXES отдельно и по смыслу, а не по длине.
_TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_TASK_ID_MIN_LEN = 2
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


def _validate_depends_on(steps: list, manifest: dict) -> str | None:
    """Зависимость на НЕСУЩЕСТВУЮЩИЙ шаг — отказ на записи. Причина или None.

    Ловим в момент объявления, а не при чтении. Висячая зависимость не падает и не
    диагностируется сама: шаг просто никогда не становится готовым, и пайплайн встаёт молча
    (`next_runnable` пуст, а причина ниоткуда не видна). Штатный источник таких ссылок —
    выключённые конфигом шаги: при `quality.tdd:false` не заводится `04-test-<taskId>`,
    при `quality.eval_enabled:false` — `02-eval-plan`, а зависящий от них `04-build-<taskId>`
    по контракту манифеста их перечисляет. Режим `--from-task-plan` строит зависимости сам и
    такого не делает; проверка нужна для вызовов с ручным `--steps`.

    Известные id = уже лежащие в манифесте ∪ добавляемые этим вызовом (шаги одной пачки
    могут ссылаться друг на друга).
    """
    existing = {s.get("id") for s in (manifest.get("steps") or []) if isinstance(s, dict)}
    bad = [f"'{sid}' → '{dep}'" for sid, dep in pp.unknown_deps(steps, existing)]
    if bad:
        return (f"depends_on ссылается на несуществующие шаги: {', '.join(bad)}. "
                f"Такой шаг не станет готовым никогда — пайплайн встанет молча. "
                f"Либо заведи недостающий шаг в этом же вызове, либо убери зависимость "
                f"(шаг выключен конфигом — напр. 04-test-* при quality.tdd:false, "
                f"02-eval-plan при quality.eval_enabled:false).")
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


EVAL_STEP = "02-eval-plan"
VERIFY_STEP = "05-tests"
DESIGN_STEP = "02-design"


def _load_quality(project_root: Path) -> dict:
    """quality-секция ЭФФЕКТИВНОГО конфига прогона (снимок политики поверх policy.json).

    Именно эффективного: набор шагов обязан соответствовать политике, под которой прогон
    стартовал, иначе правка policy.json посреди прогона завела бы RED-шаги, которых гейты
    этого прогона не ждут.
    """
    try:
        sys.path.append(str(Path(__file__).resolve().parents[3] / "hooks"))
        from _config_loader import load_project_config
        cfg = load_project_config(project_root) or {}
    except Exception:  # noqa: BLE001 — без конфига берём дефолты реестра
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def derive_steps_from_plan(plan: dict, cfg: dict) -> list:
    """task-plan.json + конфиг → шаги фаз Eval/Build. Это ЕДИНСТВЕННЫЙ способ их заводить.

    ЗАЧЕМ. Раньше набор шагов диктовался прозой брифа: «добавь 02-eval-plan, 04-test-<taskId>
    (при quality.tdd:true) и 04-build-<taskId> (depends_on 04-test-<taskId> и 02-eval-plan)».
    Две ручки конфига (quality.tdd, quality.eval_enabled) при этом влияли на НАЛИЧИЕ шага, но
    не на зависимость от него — то есть документированный путь `quality.tdd:false` предписывал
    не заводить `04-test-<taskId>` и одновременно ссылаться на него из `04-build-<taskId>`.
    Получался шаг, который не станет готовым никогда. Здесь ручки читаются один раз и влияют
    на обе стороны сразу.

    Правила (ровно контракт манифеста SKILL.md, только исполняемый):
      • 02-eval-plan  — при quality.eval_enabled;
      • 04-test-<id>  — при quality.tdd, если задача пишет код и не освобождена от тестов
                        (pipeline_phases.task_is_test_exempt: миграции/DTO/энтити);
      • 04-build-<id> — всегда; зависит от СУЩЕСТВУЮЩИХ шагов: своего RED (если заведён) и
                        02-eval-plan (если заведён); иначе — от 02-design.

    Порядок между задачами (task-plan depends_on) в шаги НЕ переносится: контракт манифеста
    этого не делает, а перенос изменил бы, какие задачи могут идти параллельно. Порядок
    реализации остаётся в task-plan, где он и описан.

    Регистр task-id сохраняется как в плане — гейты сопоставляют шаг с задачей по суффиксу.
    """
    quality = cfg.get("quality") if isinstance(cfg.get("quality"), dict) else {}
    tdd = quality.get("tdd", True)
    eval_enabled = quality.get("eval_enabled", True)

    steps: list = []
    if eval_enabled:
        steps.append({"id": EVAL_STEP, "title": "Eval-plan generated",
                      "depends_on": [DESIGN_STEP]})

    for task in (plan.get("tasks") or []):
        tid = str(task.get("id") or "").strip()
        if not tid:
            continue
        title = str(task.get("title") or "").strip()
        suffix = f" — {title}" if title else ""
        needs_red = bool(tdd) and pp.task_touches_code(task) and not pp.task_is_test_exempt(task, cfg)
        if needs_red:
            steps.append({"id": f"04-test-{tid}",
                          "title": f"TDD RED: {tid}{suffix}",
                          "depends_on": [DESIGN_STEP]})
        build_deps = ([f"04-test-{tid}"] if needs_red else []) + ([EVAL_STEP] if eval_enabled else [])
        steps.append({"id": f"04-build-{tid}",
                      "title": f"TDD GREEN: {tid}{suffix}" if needs_red else f"Build: {tid}{suffix}",
                      "depends_on": build_deps or [DESIGN_STEP]})
    return steps


def _rewire_verify_step(manifest: dict, derived: list) -> list:
    """05-tests зависит от ВСЕХ 04-build-*. Возвращает список добавленных зависимостей.

    Единственная правка существующего шага, которую делает этот скрипт, и она вынужденная:
    контракт манифеста требует такой зависимости, но на init.py задач ещё нет — id build-шагов
    становятся известны ровно здесь. Без неё 05-tests формально готов сразу после 02-design,
    то есть полный прогон тестов мог стартовать до того, как код вообще написан.
    """
    verify = next((s for s in (manifest.get("steps") or [])
                   if isinstance(s, dict) and s.get("id") == VERIFY_STEP), None)
    if verify is None:
        return []
    have = list(verify.get("depends_on") or [])
    added = [s["id"] for s in derived
             if s["id"].startswith("04-build-") and s["id"] not in have]
    if added:
        verify["depends_on"] = have + added
    return added


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


def add_steps(skill: str, feature: str, steps: list, task_plan: dict | None = None,
              project_root: Path | None = None, rewire_verify: bool = False) -> dict:
    """Добавить шаги в manifest.json. Возвращает dict с ключами status/error/...

    error_class (если status='error'):
      - 'validation' — task-id неизвестный / малформедный → CLI exit 2
      - 'infra'      — manifest не найден / битый JSON / I/O → CLI exit 1
    """
    # project_root до манифеста доезжает обязательно: раньше --project-root уважался только
    # при поиске task-plan, а манифест всё равно искался от cwd — флаг работал наполовину.
    manifest_path = get_manifest_path(skill, feature, project_root)

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

    # Проверка depends_on — ПОСЛЕ чтения манифеста: известные id складываются из уже
    # лежащих шагов и добавляемых сейчас.
    dep_err = _validate_depends_on(steps, manifest)
    if dep_err:
        return {"status": "error", "error_class": "validation", "error": dep_err}

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

    rewired = _rewire_verify_step(manifest, steps) if rewire_verify else []

    if added > 0 or rewired:
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
            "verify_depends_on_added": rewired or None,
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
    parser.add_argument("--steps", default=None,
                        help="JSON array string (взаимоисключающе с --from-task-plan)")
    parser.add_argument("--from-task-plan", action="store_true",
                        help="Вывести шаги 02-eval-plan/04-test-*/04-build-* из task-plan.json "
                             "по quality.tdd и quality.eval_enabled (вместо ручного --steps). "
                             "Зависимости строятся только на РЕАЛЬНО заводимые шаги, поэтому "
                             "висячих ссылок не возникает; 05-tests довязывается к build-шагам.")
    parser.add_argument("--task-plan", default=None,
                        help="Путь к task-plan.json — строгая сверка task-id в id шагов (п.9). "
                             "Если не указан, ищем план в docs/<skill>/<feature>/task-plan.json, "
                             "ground/statements/<skill>/<feature>/task-plan.json, "
                             ".gigacode/pipeline-state/<feature>/task-plan.json.")
    parser.add_argument("--strict-task-plan", action="store_true",
                        help="Если план не найден — exit 2 вместо warning+продолжения (для CI/гейтов).")
    args = parser.parse_args()

    if bool(args.steps) == bool(args.from_task_plan):
        print(json.dumps({"status": "error", "error_class": "infra",
                          "error": "нужен ровно один из --steps / --from-task-plan"},
                         ensure_ascii=False))
        sys.exit(1)

    root = _resolve_root(args.project_root and Path(args.project_root))

    if args.steps:
        try:
            steps = json.loads(args.steps)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "error_class": "infra",
                              "error": f"Invalid JSON: {e}"}))
            sys.exit(1)
    else:
        steps = None  # соберём ниже, когда резолвнётся task-plan

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

    if args.from_task_plan:
        if not isinstance(plan, dict) or not plan.get("tasks"):
            print(json.dumps({"status": "error", "error_class": "validation",
                              "error": "--from-task-plan: task-plan.json не найден или без "
                                       "'tasks'. Сначала фаза 02-design (tech-design пишет "
                                       "task-plan.json), потом заводи шаги."},
                             ensure_ascii=False))
            sys.exit(2)
        steps = derive_steps_from_plan(plan, _load_quality(root))
        if not steps:
            print(json.dumps({"status": "error", "error_class": "validation",
                              "error": "--from-task-plan: из плана не вывелось ни одного шага "
                                       "(у задач нет id?)"}, ensure_ascii=False))
            sys.exit(2)

    result = add_steps(args.skill, args.feature, steps, task_plan=plan,
                       project_root=root, rewire_verify=bool(args.from_task_plan))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "ok":
        sys.exit(0)
    # error_class определяет exit code: validation=2, infra=1
    sys.exit(2 if result.get("error_class") == "validation" else 1)
