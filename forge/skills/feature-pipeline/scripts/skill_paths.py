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


# find_project_root — общий с хуками (ре-экспорт ниже, вместе с остальным резолвером).
# Здесь была своя версия: .git → ground/pipeline.json, без gradle/maven. Хуки при этом
# смотрели ещё и build.gradle, причём поуровнево — на мульти-модульном репо стороны
# отвечали РАЗНОЕ про то, где корень ДАННЫХ. Теперь ответ один (см. _project._ROOT_MARKERS).


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


# ── Резолв базы ДОКУМЕНТНЫХ артефактов (docs) — ЕДИНАЯ реализация в hooks/_project ────
# Артефакты (brd/sdd/tech-design/task-plan, system-analysis/grounding) могут жить либо
# в самом репо кода (in-repo), либо в отдельном репозитории спеки (separate-repo).
# ЕДИНЫЙ источник правды — ground/pipeline.json, секция `docs`:
#   {"mode":"in-repo|separate-repo", "docs_path":"docs", "repo_path":"/abs/spec-repo",
#    "feature_subdir":"feature-pipeline", "system_analysis_subdir":"system-analysis"}
#
# Здесь лежала вторая копия этого резолвера (третья — в pipeline-state/_util.py): стороны
# скриптов и хуков деплоились раздельно, co-located импорт был невозможен, и синхронность
# держалась property-based тестом. В extension-модели бандл едет целиком, поэтому копии
# сняты — контракт теперь один объект, а не три совпадающие реализации.
#
# Резолв путей ground/ (statements/judges/gates/_origins/approvals) — оттуда же.

def _hooks_dir() -> Path:
    """Каталог hooks/ бандла: forge/ (source) или <project>/.gigacode (deploy)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "hooks" / "_project.py").is_file():
            return parent / "hooks"
    raise ImportError("forge: hooks/_project.py не найден — бандл повреждён")


# append (не insert): sys.path[0] — каталог вызывающего скрипта, он должен побеждать.
_H = str(_hooks_dir())
if _H not in sys.path:
    sys.path.append(_H)

from _project import (  # noqa: E402
    GROUND,
    _clean_rel,
    _clean_subdir,
    _docs_cfg,
    _is_safe_segment,
    _master_base,
    approval_path,
    approvals_dir,
    archived_dir,
    docs_base,
    feature_docs_dir,
    find_project_root,
    gate_result_path,
    gates_dir,
    grounding_excerpt_path,
    ground_dir,
    judge_path,
    judges_dir,
    load_pipeline_config,
    manifest_path,
    master_adr_dir,
    master_adr_path,
    master_capability,
    master_spec_path,
    master_specs_dir,
    origin_path,
    origins_dir,
    override_path,
    overrides_dir,
    pipeline_config_path,
    safe_component,
    safe_slug,
    scan_dir,
    state_dir,
    statements_dir,
    step_output_path,
    system_analysis_dir,
)


def is_story_slug(story) -> bool:
    """Назван ли реальный слаг стори. 'none'/'-'/пусто = «стори неизвестна» (осознанный ответ
    пользователя, а не пропуск вопроса)."""
    return isinstance(story, str) and story.strip().lower() not in ("", "none", "-", "нет")


def fix_docs_dir(project_root: Path, bug: str, story=None, cfg: Optional[dict] = None) -> Path:
    """Каталог артефактов ФИКСА: <feature-docs>/<story>/fixes/<bug>.

    Фикс не заводит собственную «фичу»: он чинит поведение, которое уже описано стори, и живёт
    ВНУТРИ её папки. Раньше артефакты фикса ложились в <feature-docs>/<bug> — рядом с фичами, и
    в docs/feature-pipeline баги стояли в одном ряду со стори, а связь «этот фикс к этой фиче»
    нигде не выражалась. Стори неизвестна (`none`) — фолбэк на плоскую папку <feature-docs>/<bug>.
    """
    base = feature_docs_dir(project_root, cfg)
    bug_slug = safe_slug(bug)
    if is_story_slug(story):
        return base / safe_slug(story.strip()) / "fixes" / bug_slug
    return base / bug_slug


def fix_delta_slug(bug: str, story=None) -> str:
    """Слаг дельты фикса для /forge-spec (merge/diff): 'STOR-100/fixes/BUG-512' либо 'BUG-512'."""
    bug_slug = safe_slug(bug)
    return f"{safe_slug(story.strip())}/fixes/{bug_slug}" if is_story_slug(story) else bug_slug


# ── CLI: печать резолвнутых путей ─────────────────────────────────────────────
# Зачем: брифы скиллов раньше писали плейсхолдер `<docs>` и полагались на то, что модель
# сама прочитает docs.* из pipeline.json и подставит. Нерезолвнутый плейсхолдер — прямая
# дорога записать артефакты рядом со SKILL.md, т.е. В КАТАЛОГ ХАРНЕСА (skills/), а не в
# docs/ проекта. Один вызов вместо догадки.
_CLI_TARGETS = {
    "docs-base": docs_base,
    "feature-docs": feature_docs_dir,
    "fix-docs": None,          # особый: нужны --feature (баг) и --story
    "master-base": _master_base,
    "master-specs": master_specs_dir,
    "master-spec": master_spec_path,
    "master-adr": master_adr_dir,
}


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Печатает резолвнутый путь из docs.* (ground/pipeline.json).")
    ap.add_argument("target", choices=sorted(_CLI_TARGETS), help="какой путь напечатать")
    ap.add_argument("--project", default=".", help="корень репо кода (по умолчанию cwd)")
    ap.add_argument("--feature", help="слаг/Jira-ключ: для feature-docs/fix-docs — подкаталог")
    ap.add_argument("--story", help="fix-docs: слаг стори, к которой относится баг "
                                    "('none' — стори неизвестна, папка будет плоской)")
    ap.add_argument("--print-slug", action="store_true",
                    help="fix-docs: вместо пути напечатать слаг дельты для /forge-spec merge")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    if args.target == "fix-docs":
        if not args.feature:
            print("fix-docs: нужен --feature <ключ бага>", file=sys.stderr)
            return 2
        if args.print_slug:
            print(fix_delta_slug(args.feature, args.story))
        else:
            print(fix_docs_dir(root, args.feature, args.story))
        return 0
    path = _CLI_TARGETS[args.target](root)
    if args.feature and args.target == "feature-docs":
        path = path / safe_slug(args.feature)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
