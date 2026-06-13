#!/usr/bin/env python3
"""
gate-resolver.py — runtime feature gate resolver (аналог GrowthBook из Claude Code).

Хук на SubagentStart: читает ground/feature-gates.json, резолвит активные gates
и внедряет результат как hookSpecificOutput.additionalContext.

Поддерживает трёхуровневую стратегию (как в Claude Code):
  1. Environment overrides (GATE_OVERRIDE_<NAME>=true/false)
  2. Дисковый кэш (ground/feature-gates.json)
  3. Default (вшитые в код значения)

Использование как PreToolUse хук:
  Читает feature-gates.json, сохраняет gates в hookSpecificOutput
  для использования другими хуками (tdd-guard, eval-guard, evidence-enforcer).

Событие: PreToolUse (Write|Edit|Bash) или SubagentStart
"""
import json, os, sys, re

FEATURE_GATES_PATH = os.path.join(
    os.environ.get('PROJECT_ROOT', ''),
    'ground', 'feature-gates.json'
)

# Default values (аналог DEFAULTS в Claude Code gates.ts)
DEFAULT_GATES = {
    "eval_driven_dev": True,
    "security_review": False,
    "tdd_enforced": True,
    "parallel_build": False,
    "evidence_bundle": True,
    "background_consolidation": False,
    "migration_impact_check": True,
    "module_deps_validation": True,
    "single_feature_branch": False,
}


def load_gates():
    """Читает feature-gates.json с диска, накладывает env-оверрайды.
    
    Стратегия (как GrowthBook CACHED_MAY_BE_STALE):
    1. Env override  → GATE_OVERRIDE_EVAL_DRIVEN_DEV=true
    2. Disk cache    → ground/feature-gates.json
    3. Default       → DEFAULT_GATES
    """
    gates = dict(DEFAULT_GATES)

    # Уровень 2: disk cache
    try:
        with open(FEATURE_GATES_PATH) as f:
            data = json.load(f)
            for name, cfg in data.get("gates", {}).items():
                if isinstance(cfg, dict) and "enabled" in cfg:
                    gates[name] = cfg["enabled"]
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Уровень 1: env overrides (GATE_OVERRIDE_<UPPER_NAME>)
    env_prefix = "GATE_OVERRIDE_"
    for key, val in os.environ.items():
        if key.startswith(env_prefix):
            name = key[len(env_prefix):].lower()
            if val.lower() in ("true", "1", "yes"):
                gates[name] = True
            elif val.lower() in ("false", "0", "no"):
                gates[name] = False

    return gates


def is_gate_enabled(name, gates=None):
    """Проверить конкретный gate (аналог feature() из Claude Code, но runtime)."""
    if gates is None:
        gates = load_gates()
    return gates.get(name, DEFAULT_GATES.get(name, False))


def main():
    project_root = os.environ.get('PROJECT_ROOT', '')
    feature_gates_path = os.path.join(project_root, 'ground', 'feature-gates.json')

    gates = load_gates()

    # Если файла нет — создаём с дефолтами (как init_pipeline_config.py)
    if not os.path.exists(feature_gates_path):
        os.makedirs(os.path.dirname(feature_gates_path), exist_ok=True)
        data = {
            "_meta": {
                "version": 1,
                "updated_at": "2026-06-13T00:00:00Z",
                "cache_ttl_hours": 6,
                "source": "auto-initialized"
            },
            "gates": {k: {"enabled": v, "description": ""} for k, v in DEFAULT_GATES.items()}
        }
        with open(feature_gates_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # Вывод: JSON для hookSpecificOutput
    output = {
        "hookEventName": "PreToolUse",
        "gates": gates,
        "enabled_gates": [k for k, v in gates.items() if v],
        "disabled_gates": [k for k, v in gates.items() if not v],
    }

    # Также добавляем additionalContext для модели (аналог context-injector)
    enabled_list = ", ".join(output["enabled_gates"])
    output["additionalContext"] = (
        f"[Feature Gates] Активные gates: {enabled_list}. "
        f"Отключены: {', '.join(output['disabled_gates'])}."
    ) if output["enabled_gates"] else "[Feature Gates] Все gates отключены."

    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()