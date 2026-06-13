#!/usr/bin/env python3
"""skill_paths.py — единый загрузчик путей из references/skill-paths.json.

ЕДИНЫЙ источник истины для путей на стороне скриптов (skills/*/scripts).
Скрипты больше не должны хардкодить `.gigacode/skills/...` литералы и не должны
сами искать skill-paths.json — всё резолвится здесь.

(Хуки используют свой резолвер `hooks/_project` — он выводит ту же проектную базу
`<project>/.gigacode` из расположения хук-файла. Обе стороны резолвят код ВНУТРИ проекта.)

Использование:
    import skill_paths
    root = skill_paths.find_project_root()
    p = skill_paths.script(root, "tech-design", "check_taskplan")   # абсолютный Path
    p = skill_paths.resolve(root, "docs", "feature_pipeline_dir")    # любой ключ

Если skill-paths.json не найден или ключ отсутствует — используется `default`
(относительный путь), приклеенный к корню проекта. Так поведение остаётся рабочим
даже без реестра, но реестр всегда имеет приоритет.

ИНВАРИАНТ БАЗ ПУТЕЙ (ПРОЕКТНАЯ модель):
  • ВСЁ живёт в проекте и управляется git. Никакой зависимости от ~/.gigacode.
  • КОД (скрипты скиллов, хуки) — в <project>/.gigacode/{skills,hooks}/…
  • ДАННЫЕ (ground/, docs/) — в корне проекта.
  • skill_paths резолвит относительно project_root (реестровые пути вида
    ".gigacode/skills/…" и "ground/…" приклеиваются к project_root).
  • Хуки используют hooks/_project (база выводится из расположения хука —
    тот же <project>/.gigacode). Обе стороны указывают на код ВНУТРИ проекта.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_CACHE: dict[str, dict] = {}


def find_project_root(start: Optional[Path] = None) -> Path:
    """Корень проекта по .git или ground/pipeline.json (вверх от start/cwd)."""
    start = (start or Path.cwd()).resolve()
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists():
            return parent
        if (parent / "ground" / "pipeline.json").exists():
            return parent
    return start


def find_registry(project_root: Path, skill: str = "feature-pipeline") -> Path:
    """Ищет skill-paths.json в стандартных местах; возвращает первый существующий
    либо канонический путь по умолчанию."""
    candidates = [
        project_root / ".gigacode" / "skills" / skill / "references" / "skill-paths.json",
        project_root / "references" / "skill-paths.json",
        project_root / ".gigacode" / "references" / "skill-paths.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def load(project_root: Path, skill: str = "feature-pipeline") -> dict:
    """Загружает реестр (с кэшем). {} если файл отсутствует/битый."""
    reg_path = find_registry(project_root, skill)
    key = str(reg_path)
    if key in _CACHE:
        return _CACHE[key]
    data: dict = {}
    try:
        if reg_path.exists():
            data = json.loads(reg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    _CACHE[key] = data
    return data


def _dig(data: dict, keys: tuple[str, ...]):
    node = data
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node if isinstance(node, str) else None


def resolve(project_root: Path, *keys: str, default: Optional[str] = None,
            skill: str = "feature-pipeline") -> Optional[Path]:
    """Резолвит вложенный ключ реестра в абсолютный Path относительно корня проекта.

    `keys` — путь по дереву JSON, напр. resolve(root, "skills", "tech-design",
    "scripts", "check_taskplan"). Если ключ не найден — используется `default`
    (относительный путь). Всё резолвится ВНУТРИ проекта (project_root); зависимости
    от ~/.gigacode нет. Возвращает None, если нет ни ключа, ни default.
    """
    rel = _dig(load(project_root, skill), keys)
    if rel is None:
        rel = default
    if rel is None:
        return None
    return project_root / rel


def script(project_root: Path, skill_name: str, script_name: str,
           default: Optional[str] = None, skill: str = "feature-pipeline") -> Optional[Path]:
    """Удобный резолв скрипта: skills.<skill_name>.scripts.<script_name>.

    Если в реестре нет — собирает канонический default
    `.gigacode/skills/<skill_name>/scripts/<script_name>.py`.
    """
    if default is None:
        default = f".gigacode/skills/{skill_name}/scripts/{script_name}.py"
    return resolve(project_root, "skills", skill_name, "scripts", script_name,
                   default=default, skill=skill)
