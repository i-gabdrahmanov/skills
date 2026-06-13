#!/usr/bin/env python3
"""
_project.py — Единый resolver для всех хуков (ПРОЕКТНАЯ модель).

База кода — каталог, где физически лежат hooks/ и skills/ ЭТОГО проекта, выводится из
расположения самого хук-файла. НИКАКОЙ зависимости от ~/.gigacode: всё живёт в проекте
и управляется git. В развёрнутом проекте база = <project>/.gigacode; в source-репо — корень.

project_root (для ДАННЫХ: ground/, docs/) ищется отдельно по live-файлам
(.git, build.gradle, pipeline.json).

Usage:
    from _project import gigacode_dir, skills_dir, find_project_root
"""

import json
import sys
from pathlib import Path
from typing import Optional


def gigacode_dir() -> Path:
    """База кода: каталог с hooks/ и skills/ этого проекта.

    Хук-файл лежит в <base>/hooks/_project.py → база = parents[1].
    Развёрнутый проект: <project>/.gigacode. Source-репо: корень репо.
    """
    return Path(__file__).resolve().parents[1]


# Обратная совместимость: имя сохранено, но теперь это ПРОЕКТНАЯ база (не ~/.gigacode).
def gigacode_home() -> Path:
    return gigacode_dir()


def skills_dir() -> Path:
    """Путь к скиллам: <project>/.gigacode/skills/<skill>/scripts/..."""
    return gigacode_dir() / "skills"


def hooks_dir() -> Path:
    """Путь к хукам: <project>/.gigacode/hooks/"""
    return gigacode_dir() / "hooks"


def resolve_skill_path(skill_name: str, *subpaths: str) -> Path:
    """Резолвит путь к скиллу: ~/.gigacode/skills/<skill>/<subpaths>

    Пример: resolve_skill_path("pipeline-state", "scripts", "update.py")
    → ~/.gigacode/skills/pipeline-state/scripts/update.py
    """
    return skills_dir().joinpath(skill_name, *subpaths)


def resolve_hook_path(hook_name: str) -> Path:
    """Резолвит путь к хуку: ~/.gigacode/hooks/<hook>.py"""
    return hooks_dir() / f"{hook_name}.py"


def find_project_root(cwd: Optional[Path] = None) -> Path:
    """Ищет корень проекта от cwd вверх.

    Критерии (по убыванию приоритета):
    1. Содержит .git
    2. Содержит build.gradle или settings.gradle или pom.xml
    3. Содержит ground/pipeline.json

    Возвращает первый совпавший, начиная от cwd.
    """
    cwd = cwd or Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent
        if (parent / "build.gradle").exists() or (parent / "settings.gradle").exists():
            return parent
        if (parent / "ground" / "pipeline.json").exists():
            return parent
    return cwd


def active_feature(root: Path, skill: str = "feature-pipeline") -> str:
    """Активная фича = самый свежий manifest.json в ground/statements/<skill>/<feature>/.
    'pipeline' (back-compat), если ни одного манифеста нет. Должна совпадать с
    pipeline_phases.active_feature (проверяется тестом)."""
    base = Path(root) / "ground" / "statements" / skill
    if not base.is_dir():
        return "pipeline"
    best, best_mtime = None, -1.0
    for d in base.iterdir():
        if not d.is_dir() or d.name == "archived":
            continue
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        try:
            mtime = mp.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = d.name, mtime
    return best or "pipeline"


def phases_dir(root: Path, feature: str) -> Path:
    """Каталог фазовой машины фичи: ground/phases/<feature>/."""
    return Path(root) / "ground" / "phases" / feature


def gate_file(root: Path, feature: str) -> Path:
    """gate.json фичи; при отсутствии — legacy ground/phases/gate.json (back-compat чтения)."""
    per = phases_dir(root, feature) / "gate.json"
    if per.exists():
        return per
    legacy = Path(root) / "ground" / "phases" / "gate.json"
    return legacy if legacy.exists() else per


def defs_file(root: Path, feature: str) -> Path:
    per = phases_dir(root, feature) / "phase-defs.json"
    if per.exists():
        return per
    legacy = Path(root) / "ground" / "phases" / "phase-defs.json"
    return legacy if legacy.exists() else per


def evidence_file(root: Path, feature: str) -> Path:
    per = phases_dir(root, feature) / "agent-evidence.jsonl"
    if per.exists():
        return per
    legacy = Path(root) / "ground" / "phases" / "agent-evidence.jsonl"
    return legacy if legacy.exists() else per


def load_pipeline_config(root: Optional[Path] = None) -> dict:
    """Читает pipeline.json из проекта.

    Возвращает dict или {} (с дефолтами). Никогда не бросает.
    """
    root = root or find_project_root()
    cfg_path = root / "ground" / "pipeline.json"
    try:
        if cfg_path.exists():
            return json.loads(cfg_path.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def load_settings_hooks() -> dict:
    """Читает settings.hooks.json — эталонную конфигурацию хуков."""
    path = hooks_dir() / "settings.hooks.json"
    try:
        if path.exists():
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {"hooks": {}}


def verify_environment() -> bool:
    """Проверяет, что код проекта на месте (проектная база, не ~/.gigacode):
    - Есть <project>/.gigacode/skills/
    - Есть <project>/.gigacode/hooks/
    - Есть settings.hooks.json
    """
    base = gigacode_dir()
    return all([
        base.exists(),
        (base / "skills").exists(),
        (base / "hooks").exists(),
        (base / "hooks" / "settings.hooks.json").exists(),
    ])


def verify_project(root: Optional[Path] = None) -> bool:
    """Проверяет, что проект корректен: есть pipeline.json + manifest.json"""
    root = root or find_project_root()
    pip = load_pipeline_config(root)
    if not pip.get("_incomplete"):
        return True
    return False