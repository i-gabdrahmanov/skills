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
import sys
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


# ── Резолв базы ДОКУМЕНТНЫХ артефактов (docs) ─────────────────────────
# Артефакты (brd/sdd/tech-design/task-plan, system-analysis/grounding) могут жить либо
# в самом репо кода (in-repo), либо в отдельном репозитории спеки (separate-repo).
# ЕДИНЫЙ источник правды — ground/pipeline.json, секция `docs`:
#   {"mode":"in-repo|separate-repo", "docs_path":"docs", "repo_path":"/abs/spec-repo",
#    "feature_subdir":"feature-pipeline", "system_analysis_subdir":"system-analysis"}
# Контракт ОБЩИЙ со стороной хуков (hooks/_project.py: docs_base/feature_docs_dir/
# system_analysis_dir) — синхронность пинится test_docs_resolver_consistency.py.

def load_pipeline_config(project_root: Path) -> dict:
    """ground/pipeline.json проекта (или {}). Никогда не бросает."""
    p = Path(project_root) / "ground" / "pipeline.json"
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


# ── Хелперы устойчивости (типы конфига + анти-traversal) ──────────────
def _docs_cfg(cfg: Optional[dict], project_root: Path) -> dict:
    cfg = cfg if cfg is not None else load_pipeline_config(project_root)
    docs = cfg.get("docs") if isinstance(cfg, dict) else None
    return docs if isinstance(docs, dict) else {}


def _is_safe_segment(name) -> bool:
    """Простое имя подпапки/слага: строка, без разделителей/traversal/абсолюта."""
    return (isinstance(name, str) and name not in ("", ".", "..")
            and "/" not in name and "\\" not in name and ".." not in name
            and not name.startswith(("~", "/")))


def _clean_subdir(val, default: str) -> str:
    """Имя подпапки docs. Не-строка/traversal/абсолют → default (с предупреждением)."""
    if _is_safe_segment(val):
        return val
    if val is not None and val != default:
        print(f"[skill_paths] docs: небезопасное имя подпапки {val!r} → '{default}'", file=sys.stderr)
    return default


def _clean_rel(val, project_root: Path, default: str) -> Path:
    """Относительный путь под project_root. Не-строка/абсолют/traversal → project_root/default."""
    if isinstance(val, str) and val.strip():
        s = val.strip()
        if not s.startswith(("/", "~")) and ".." not in Path(s).parts:
            return project_root / s
        print(f"[skill_paths] docs: путь {val!r} выходит за проект → '{default}'", file=sys.stderr)
    elif val is not None:
        print(f"[skill_paths] docs: путь не строка ({val!r}) → '{default}'", file=sys.stderr)
    return project_root / default


def safe_slug(slug) -> str:
    """Валидный слаг фичи (один компонент пути). ValueError на traversal/разделителях."""
    if not _is_safe_segment(slug):
        raise ValueError(f"небезопасный feature-slug: {slug!r} (запрещены '/', '..', '~', абсолютный, пустой)")
    return slug


def docs_base(project_root: Path, cfg: Optional[dict] = None) -> Path:
    """База, под которой лежат `feature-pipeline/` и `system-analysis/`.

    in-repo       → project_root / docs.docs_path (дефолт 'docs', только под проектом).
    separate-repo → docs.repo_path (внешний репо спеки); относительный — от project_root.
    """
    project_root = Path(project_root)
    docs = _docs_cfg(cfg, project_root)
    if docs.get("mode") == "separate-repo":
        rp = docs.get("repo_path")
        if isinstance(rp, str) and rp.strip():
            p = Path(rp.strip()).expanduser()
            return p if p.is_absolute() else (project_root / p)
        # mode=separate-repo, но repo_path нет/битый → безопасный откат в in-repo
    return _clean_rel(docs.get("docs_path"), project_root, "docs")


def feature_docs_dir(project_root: Path, cfg: Optional[dict] = None) -> Path:
    """Каталог документов фич: <docs_base>/feature-pipeline (или legacy docs.feature_docs_path)."""
    project_root = Path(project_root)
    docs = _docs_cfg(cfg, project_root)
    legacy = docs.get("feature_docs_path")
    if (isinstance(legacy, str) and legacy and docs.get("mode") != "separate-repo"
            and not legacy.startswith(("/", "~")) and ".." not in Path(legacy).parts):
        return project_root / legacy
    return docs_base(project_root, cfg) / _clean_subdir(docs.get("feature_subdir"), "feature-pipeline")


def system_analysis_dir(project_root: Path, cfg: Optional[dict] = None) -> Path:
    """Каталог системного обзора: <docs_base>/system-analysis (или legacy docs.system_analysis_path)."""
    project_root = Path(project_root)
    docs = _docs_cfg(cfg, project_root)
    legacy = docs.get("system_analysis_path")
    if (isinstance(legacy, str) and legacy and docs.get("mode") != "separate-repo"
            and not legacy.startswith(("/", "~")) and ".." not in Path(legacy).parts):
        return project_root / legacy
    return docs_base(project_root, cfg) / _clean_subdir(docs.get("system_analysis_subdir"), "system-analysis")


def scan_dir(project_root: Path, cfg: Optional[dict] = None) -> Path:
    """Каталог детерминированного скана: <system_analysis>/scan."""
    return system_analysis_dir(project_root, cfg) / "scan"


def grounding_excerpt_path(project_root: Path, cfg: Optional[dict] = None) -> Path:
    """Путь к компактной выжимке grounding: <system_analysis>/grounding-excerpt.json."""
    return system_analysis_dir(project_root, cfg) / "grounding-excerpt.json"
