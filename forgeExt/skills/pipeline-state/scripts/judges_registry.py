#!/usr/bin/env python3
"""judges_registry.py — единый загрузчик масок required_judges и метаданных судей.

ЕДИНЫЙ источник истины — references/judges-registry.json. Раньше маска
REQUIRED_JUDGES_MASK дублировалась в init.py и patch_manifest_judges.py; теперь
оба читают её отсюда, чтобы не рассинхронизироваться.

Использование:
    import judges_registry
    judges_registry.match_step("04-build-T1")  # -> ["build-judge", "reuse-judge"]
    judges_registry.step_masks()               # -> dict masks
    judges_registry.judges()                   # -> dict metadata
"""
from __future__ import annotations

import json
from pathlib import Path

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "references" / "judges-registry.json"
_CACHE: dict | None = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _CACHE = {}
    return _CACHE


def step_masks() -> dict:
    """{step_mask: [judge, ...]} — точные id и wildcard-маски (заканчиваются на *)."""
    return dict(_load().get("step_masks", {}))


def judges() -> dict:
    """{judge_name: {phase, kind, contract}} — метаданные судей."""
    return dict(_load().get("judges", {}))


def match_step(step_id: str) -> list:
    """required_judges для step_id: точное совпадение → wildcard (mask кончается на *)."""
    masks = step_masks()
    if step_id in masks:
        return list(masks[step_id])
    for mask, js in masks.items():
        if mask.endswith("*") and step_id.startswith(mask[:-1]):
            return list(js)
    return []
