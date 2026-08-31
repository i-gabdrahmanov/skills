#!/usr/bin/env python3
"""_root.py — резолв корня проекта для данных (ground/).

Тот же инвариант, что hooks/_project.find_project_root: корень ищется вверх от
запускающего каталога по live-маркерам .git → build.gradle/settings.gradle/pom.xml →
ground/<policy.json|pipeline.json>. Явный --project имеет приоритет над маркерами.

Локальная ко-локейд копия: скилл jira-task-writer изолирован от hooks/_project и
skill_paths (без кросс-скилловых импортов), как _root.py в skills/sdd/scripts.

Phase 0 v2 consolidation: the marker-priority algorithm now lives in
:func:`_config_loader.find_project_root`; this module keeps the legacy signature
(``find_project_root(cwd) -> Path``, never returns ``None``) by delegating and
falling back to ``start`` on miss.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Маркеры ground/ — единый источник hooks/_config_loader.PROJECT_ROOT_MARKERS.
# Скилл jira-task-writer изолирован от hooks/_project и skill_paths, но hooks/ лежит рядом
# с skills/ в одном репо — дотягиваемся через sys.path (как в system-analyst/scripts/common.py).
_HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"
if str(_HOOKS_DIR) not in sys.path and _HOOKS_DIR.is_dir():
    sys.path.append(str(_HOOKS_DIR))
from _config_loader import find_project_root as _canonical_find_project_root  # noqa: E402


def find_project_root(cwd: Optional[Path] = None) -> Path:
    """Корень проекта для ДАННЫХ: вверх от cwd по маркерам. Ничего не найдено — cwd.

    Backwards-compat wrapper over :func:`_config_loader.find_project_root`.
    Behaviour identical to the pre-refactor inline implementation.
    """
    start = Path(cwd) if cwd is not None else Path.cwd()
    if not start.is_absolute():
        start = start.resolve()
    return _canonical_find_project_root(start) or start