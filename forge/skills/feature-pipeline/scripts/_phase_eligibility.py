#!/usr/bin/env python3
"""_phase_eligibility.py — ЕДИНЫЙ предикат фазы на enabled_by / skip_if.

DRY-извлечение из resolve_phases.py. Раньше resolve_phases.resolve_phases() корректно
учитывал enabled_by (truthy env/feature_flag/jpath) и skip_if (eval expression), а
pipeline_phases.live_phase_decision() их игнорировал. Из-за этого live-снимок фаз
застревал на опорожнённой фазе (п.8 KIDPPRB-9254) — current_phase указывал на фазу,
которая уже отключена конфигом. Чинка: оба пути теперь используют phase_eligibility(),
а ShouldExecute фиксирует skip_reason ("disabled_by: ..." / "skip_if: ...") для
диагностики.

Поддерживаемые формы enabled_by / skip_if:
  - None                  — нет условия (фаза активна по умолчанию)
  - bool                  — литерал True/False
  - "gates.X.Y"           — флаг из feature-gates.json (gates["gates"][X]["enabled"])
  - "a.b.c"               — jpath в pipeline.json (с дефолтом из ENABLED_BY_DEFAULTS)
  - "env:VAR"             — переменная окружения (truthy = active; absent/empty = off)

Импортируется из:
  - resolve_phases.py     — резолвер фаз (поведение сохранено 1:1 для существующих тестов)
  - pipeline_phases.py    — live-снимок (новая функциональность через project_root/pipeline/gates)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Импорт из hooks/_config_loader (Phase 0 v2 refactor) — load_project_config: двойной рид
# policy.json → pipeline.json. Скилл изолирован от hooks/_project, но _config_loader — точечный
# helper для ридов, и каталог hooks/ всегда рядом в одном репо, поэтому дотягиваемся явно.
# __file__ = .../skills/feature-pipeline/scripts/_phase_eligibility.py → parents[3] = <repo>
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hooks"))


# ── Результат проверки фазы ──────────────────────────────────────────
@dataclass(frozen=True)
class ShouldExecute:
    """Решение о выполнении фазы.

    should_execute=False → фаза отключена конфигурацией или скипнута runtime-условием.
    skip_reason — короткий префикс + источник: "disabled_by: <expr>" / "skip_if: <expr>".
    Пустая строка, если should_execute=True.
    """
    should_execute: bool = True
    skip_reason: str = ""

    @classmethod
    def execute(cls) -> "ShouldExecute":
        return cls(True, "")

    @classmethod
    def disabled_by(cls, cond) -> "ShouldExecute":
        return cls(False, f"disabled_by: {cond}")

    @classmethod
    def skip_if_triggered(cls, expr) -> "ShouldExecute":
        return cls(False, f"skip_if: {expr}")


# ── jpath-резолв (jq-подобный) ──────────────────────────────────────
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


# Дефолты enabled_by-полей, ОТСУТСТВУЮЩИХ в pipeline.json. Должны совпадать с
# дефолтами читателей того же поля: config.py (eval_enabled→True) и eval-guard/tdd-guard.
# Иначе на конфиге без ключа фаза молча выпадала (02-eval-plan скипался при default=False,
# хотя доки/судьи считают EDD включённым по умолчанию).
ENABLED_BY_DEFAULTS = {
    "quality.eval_enabled": True,
    "quality.tdd": True,
}


# ── enabled_by ──────────────────────────────────────────────────────
def _env_bool(name: str) -> bool:
    """Парсить env-переменную как bool: "1"/"true"/"yes"/"on" (case-insens.) → True.

    Иначе Python bool("0")=True и FEATURE_X=0 молча включал бы фазу — это классическая
    ловушка. Используется и в enabled_by, и в skip_if для согласованной семантики.
    """
    val = os.environ.get(name, "")
    return val.strip().lower() in ("1", "true", "yes", "on")


def _evaluate_enabled_by(expr, pipeline, gates):
    """Проверить условие enabled_by (аналог compile-time feature() из Bun).

    None  → True (фаза активна).
    bool  → литерал.
    "gates.X" → флаг из feature-gates.json.
    "env:VAR" → os.environ["VAR"] truthy как bool ("1"/"true"/"yes"/"on").
    "a.b.c"  → jpath в pipeline.json (дефолт из ENABLED_BY_DEFAULTS).
    """
    if expr is None:
        return True
    if isinstance(expr, bool):
        return expr
    if isinstance(expr, str):
        if expr.startswith("gates."):
            gate_name = expr.split(".", 1)[1]
            gates_data = gates or {}
            gate = gates_data.get("gates", {}).get(gate_name, {})
            return gate.get("enabled", False) if isinstance(gate, dict) else False
        if expr.startswith("env:"):
            var = expr.split(":", 1)[1].strip()
            return _env_bool(var)
    return bool(_resolve_jpath(pipeline, expr, ENABLED_BY_DEFAULTS.get(expr, False)))


# ── skip_if ─────────────────────────────────────────────────────────
def _evaluate_skip_if(skip_expr, pipeline, gates, feature_ctx):
    """Проверить условие skip_if.

    Поддерживаемые выражения:
      - "grounding.exists" — grounding уже есть (всегда False, резолвится внешне)
      - "!quality.eval_enabled" — отрицание поля из pipeline.json
      - "gates.X" — gate-флаг из feature-gates.json
      - "env:VAR" — env-переменная как bool (truthy = skip)
    """
    if isinstance(skip_expr, bool):
        return skip_expr
    negate = False
    expr = skip_expr.strip()
    if expr.startswith("!"):
        negate = True
        expr = expr[1:]

    if expr == "grounding.exists":
        result = False
    elif expr.startswith("gates."):
        gate_name = expr.split(".", 1)[1]
        gates_data = gates or {}
        gate = gates_data.get("gates", {}).get(gate_name, {})
        result = gate.get("enabled", False) if isinstance(gate, dict) else False
    elif expr.startswith("env:"):
        var = expr.split(":", 1)[1].strip()
        result = _env_bool(var)
    else:
        result = bool(_resolve_jpath(pipeline, expr, False))

    return result if not negate else not result


# ── ЕДИНЫЙ предикат (используют обе функции) ────────────────────────
def phase_eligibility(phase: dict, pipeline: dict, gates: dict,
                      feature_ctx: Optional[dict] = None) -> ShouldExecute:
    """Решение «фазу выполнять?» по enabled_by + skip_if.

    Args:
        phase:       dict c полями enabled_by, skip_if (или без них — обе None).
        pipeline:    содержимое ground/pipeline.json (или {} если не загружен).
        gates:       содержимое ground/feature-gates.json (или {} если не загружен).
        feature_ctx: контекст фичи для skip_if (резерв; сейчас не используется, но
                     оставлен для будущих выражений типа "ctx.brd_present").

    Returns:
        ShouldExecute c should_execute и skip_reason.
    """
    enabled = _evaluate_enabled_by(phase.get("enabled_by"), pipeline, gates)
    if not enabled:
        return ShouldExecute.disabled_by(phase.get("enabled_by"))

    skip_expr = phase.get("skip_if")
    if skip_expr:
        should_skip = _evaluate_skip_if(skip_expr, pipeline, gates, feature_ctx or {})
        if should_skip:
            return ShouldExecute.skip_if_triggered(skip_expr)

    return ShouldExecute.execute()


# ── Безопасная загрузка конфигов (для live_phase_decision) ──────────
def load_pipeline_json(root) -> dict:
    """ground/policy.json (или ground/pipeline.json для legacy) от корня проекта.

    Мягкий фолбэк — пустой dict. None/пустой root / нет файла / битый JSON / OSError
    → {} (без exception). live_phase_decision работает best-effort: без config gating
    пропускается.

    Имя функции сохранено для обратной совместимости с импортами вызывающих (resolve_phases
    и т.п.), но реализация теперь — load_project_config из hooks/_config_loader (Phase 0 v2
    refactor): двойной рид policy.json → pipeline.json, легаси-проекты продолжают работать."""
    from _config_loader import load_project_config
    if not root:
        return {}
    return load_project_config(Path(root))


def load_gates_json(path) -> dict:
    """feature-gates.json. None / нет файла / битый JSON → {}.

    Используется и live_phase_decision, и (опционально) тестами для проверки
    gate-флагов. Поиск пути — на стороне вызывающего (обычно <root>/ground/feature-gates.json).
    """
    if not path:
        return {}
    gp = Path(path)
    if not gp.is_file():
        return {}
    try:
        return json.loads(gp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
