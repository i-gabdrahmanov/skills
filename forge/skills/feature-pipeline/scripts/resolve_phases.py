#!/usr/bin/env python3
"""
resolve_phases.py — динамический резолвер фаз feature-pipeline.

Аналог GrowthBook runtime feature gating из Claude Code, но в виде
детерминированного Python-скрипта.

Читает:
  - pipeline.json — основная конфигурация
  - ground/feature-gates.json — runtime gate flags (дисковый кэш)
  - (опционально) --feature <slug> — контекст фичи для skip_if

Возвращает JSON-массив активных фаз в порядке выполнения.

Использование:
    python resolve_phases.py --project <root>
    python resolve_phases.py --project <root> --feature my-feature --gates ground/feature-gates.json
    python resolve_phases.py --project <root> --list    # показать все фазы с причинами включения

Exit code:
    0 — OK (stdout = JSON)
    1 — ошибка конфига
"""
import argparse, json, os, sys, re


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"JSON error in {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _resolve_jpath(obj, path, default=None):
    """Разрешить jq-подобный путь 'quality.tdd' в словаре."""
    if not path:
        return default
    parts = path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return default
        if current is None:
            return default
    return current


def _evaluate_skip_if(skip_expr, pipeline, gates, feature_ctx):
    """Проверить условие skip_if.
    
    Поддерживаемые выражения:
      - "grounding.exists" — grounding уже есть (всегда False, резолвится внешне)
      - "!quality.eval_enabled" — отрицание поля из pipeline.json
      - "gates.parallel_build" — gate-флаг из feature-gates.json
    """
    negate = False
    expr = skip_expr.strip()
    if expr.startswith("!"):
        negate = True
        expr = expr[1:]

    if expr == "grounding.exists":
        # Всегда False — grounding-check делается отдельно через check_grounding.py
        result = False
    elif expr.startswith("gates."):
        gate_name = expr.split(".", 1)[1]
        gates_data = gates or {}
        gate = gates_data.get("gates", {}).get(gate_name, {})
        result = gate.get("enabled", False) if isinstance(gate, dict) else False
    else:
        result = bool(_resolve_jpath(pipeline, expr, False))

    return result if not negate else not result


# Дефолты для enabled_by-полей, ОТСУТСТВУЮЩИХ в pipeline.json. Должны совпадать с
# дефолтами читателей того же поля: config.py (eval_enabled→True) и eval-guard/tdd-guard.
# Иначе на конфиге без ключа фаза молча выпадала (02-eval-plan скипался при default=False,
# хотя доки/судьи считают EDD включённым по умолчанию).
ENABLED_BY_DEFAULTS = {
    "quality.eval_enabled": True,
    "quality.tdd": True,
}


def _evaluate_enabled_by(expr, pipeline, gates):
    """Проверить условие enabled_by (аналог compile-time feature() из Bun).

    Если enabled_by нет или None — фаза включена по умолчанию.
    Если это строка — проверяем как путь в pipeline.json или gates.
    Отсутствующий ключ берёт дефолт из ENABLED_BY_DEFAULTS (по умолчанию False).
    """
    if expr is None:
        return True
    if expr.startswith("gates."):
        gate_name = expr.split(".", 1)[1]
        gates_data = gates or {}
        gate = gates_data.get("gates", {}).get(gate_name, {})
        return gate.get("enabled", False) if isinstance(gate, dict) else False
    return bool(_resolve_jpath(pipeline, expr, ENABLED_BY_DEFAULTS.get(expr, False)))


# База фаз пайплайна (id/порядок — стабильны; phases_override в pipeline.json может дополнить).
# id фаз ДОЛЖНЫ быть подмножеством pipeline_phases.MAIN_PHASES и идти в каноническом порядке —
# это пинит test_phase_consistency (раньше resolve_phases был вторым нескоординированным
# источником списка фаз).
DEFAULT_PHASES = [
    {"id": "00-brd",          "skill": "business-requirements", "enabled_by": None,              "skip_if": None,           "gates": ["brd"],        "description": "Discovery / BRD"},
    {"id": "01-grounding",    "skill": "system-analyst",        "enabled_by": None,              "skip_if": "grounding.exists", "gates": None,       "description": "System overview ensured"},
    {"id": "02-sdd",          "skill": "sdd",                   "enabled_by": None,              "skip_if": None,           "gates": ["sdd"],        "description": "SDD specification"},
    {"id": "02-design",       "skill": "tech-design",           "enabled_by": None,              "skip_if": None,           "gates": ["design"],     "description": "Tech design + task plan"},
    {"id": "02-eval-plan",    "skill": None,                    "enabled_by": "quality.eval_enabled", "skip_if": None,     "gates": None,           "description": "Eval-plan generated"},
    {"id": "03-jira",         "skill": "jira-task-writer",      "enabled_by": "jira.enabled",     "skip_if": None,           "gates": ["jira"],       "description": "Jira issues created"},
    {"id": "04-tdd",          "skill": "java-spring-dev",       "enabled_by": "quality.tdd",      "skip_if": None,           "gates": None,           "description": "TDD RED→GREEN per task"},
    {"id": "05-verify",       "skill": None,                    "enabled_by": None,              "skip_if": None,           "gates": None,           "description": "Full test run + coverage"},
    {"id": "06-document",     "skill": None,                    "enabled_by": None,              "skip_if": None,           "gates": None,           "description": "Spec updated"},
    {"id": "07-deliver",      "skill": None,                    "enabled_by": None,              "skip_if": None,           "gates": ["commit","pr","report"], "description": "Stacked PR delivery"},
]


def resolve_phases(project_root, feature_slug=None, gates_path=None):
    """Основная функция: возвращает список активных фаз."""
    pipeline = load_json(os.path.join(project_root, "ground", "pipeline.json"))
    if not pipeline:
        print("pipeline.json not found", file=sys.stderr)
        sys.exit(1)

    gates = load_json(gates_path) if gates_path else None
    if gates_path and gates is None:
        print(f"feature-gates.json not found at {gates_path}, using defaults", file=sys.stderr)
        gates = {"gates": {}}

    feature_ctx = {}
    if feature_slug:
        state_dir = os.path.join(project_root, "ground", "statements", "feature-pipeline", feature_slug)
        manifest_path = os.path.join(state_dir, "manifest.json")
        manifest = load_json(manifest_path)
        if manifest:
            feature_ctx = manifest.get("context", {})

    # База фаз — модульная константа DEFAULT_PHASES (порядок/id пинятся test_phase_consistency
    # против pipeline_phases.MAIN_PHASES). Копируем, чтобы phases_override не мутировал константу.
    phases_definitions = [dict(p) for p in DEFAULT_PHASES]

    # Позволяем pipeline.json переопределить фазы через phases_override
    override = pipeline.get("phases_override")
    if override:
        override_index = {p["id"]: p for p in override}
        for i, phase in enumerate(phases_definitions):
            if phase["id"] in override_index:
                phases_definitions[i] = {**phase, **override_index[phase["id"]]}

    result = []
    skipped = []
    for phase in phases_definitions:
        # enabled_by
        enabled = _evaluate_enabled_by(phase.get("enabled_by"), pipeline, gates)
        if not enabled:
            skipped.append({"id": phase["id"], "reason": f"enabled_by({phase['enabled_by']}) = false"})
            continue

        # skip_if
        skip_expr = phase.get("skip_if")
        if skip_expr:
            should_skip = _evaluate_skip_if(skip_expr, pipeline, gates, feature_ctx)
            if should_skip:
                skipped.append({"id": phase["id"], "reason": f"skip_if({skip_expr}) = true"})
                continue

        result.append({
            "id": phase["id"],
            "skill": phase["skill"],
            "gates": phase.get("gates", []),
            "description": phase.get("description", ""),
        })

    return {"phases": result, "skipped": skipped, "total": len(result), "skipped_count": len(skipped)}


def main():
    parser = argparse.ArgumentParser(description="Resolve active feature-pipeline phases")
    parser.add_argument("--project", default=os.getcwd(), help="Project root directory")
    parser.add_argument("--feature", help="Feature slug (for skip_if context)")
    parser.add_argument("--gates", help="Path to feature-gates.json")
    parser.add_argument("--list", action="store_true", help="Show all phases with reasons")
    args = parser.parse_args()

    result = resolve_phases(args.project, args.feature, args.gates)

    if args.list:
        print(f"Total active phases: {result['total']}, skipped: {result['skipped_count']}")
        print()
        for p in result["phases"]:
            gates_str = f" gates=[{','.join(p['gates'])}]" if p["gates"] else ""
            skill_str = f" skill={p['skill']}" if p["skill"] else ""
            print(f"  ✅ {p['id']}: {p['description']}{skill_str}{gates_str}")
        for s in result["skipped"]:
            print(f"  ⏭️  {s['id']}: {s['reason']}")
        return

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()