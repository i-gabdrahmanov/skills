#!/usr/bin/env python3
"""story_branch_push.py — создание интеграционной ветки фичи feature/<slug> на origin.

Интеграционная ветка фичи собирается ТОЛЬКО мерджем PR сабветок задач — прямые
commit/merge/push в неё блокирует gate-guard (branch_protection, deny-first). Единственный
легальный способ её ЗАВЕСТИ — этот скрипт: он пушит СУЩЕСТВУЮЩИЙ default-tip origin на
новое имя refs/heads/feature/<slug>. Коммитов не создаёт, force не использует, worktree/
HEAD/локальные ветки не трогает, СУЩЕСТВУЮЩУЮ ветку никогда не двигает (идемпотентен:
повторный запуск — status=exists). По построению не может ни опубликовать новый код, ни
переписать историю — поэтому approval-маркером не гейтится (запускается на Гейте 5 после
«да» на push+PR, перед пушами сабветок).

Usage:
    story_branch_push.py --feature <slug> [--project <root>] [--status] [--json]

Exit: 0 — создана/уже существует (и --status); 2 — валидация (слаг, скоуп, аргументы);
1 — операционная git-ошибка (нет remote, пустой remote, push отклонён).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import skill_paths
from doc_review_push import (_default_branch, _git, _git_out, _remote_is_empty)

SKILL_NAME = "feature-pipeline"
BRANCH_PREFIX = "feature"


def _err(msg: str) -> None:
    print(f"[story-branch] {msg}", file=sys.stderr)


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[story-branch] " + " ".join(f"{k}={v}" for k, v in result.items()))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--feature", required=True, help="Slug/Jira-key фичи (Story)")
    p.add_argument("--project", default=None, help="Корень проекта (default: git toplevel cwd)")
    p.add_argument("--status", action="store_true", help="Ридонли-отчёт о состоянии ветки")
    p.add_argument("--json", action="store_true", help="Машинный вывод")
    args = p.parse_args()

    # V1: слаг — один безопасный компонент пути
    try:
        slug = skill_paths.safe_slug(args.feature)
    except ValueError as e:
        _err(str(e))
        return 2
    branch = f"{BRANCH_PREFIX}/{slug}"

    # V2: --project обязан совпадать с toplevel(cwd) — интеграционная ветка заводится
    # в КОДОВОМ репо проекта
    top = _git_out(Path.cwd(), "rev-parse", "--show-toplevel")
    default_root = Path(top) if top else Path.cwd()
    project = Path(args.project).resolve() if args.project else default_root.resolve()
    if args.project and top and project != Path(top).resolve():
        _err(f"--project ({project}) не совпадает с рабочим репо ({top}) — "
             "запускай скрипт из проекта.")
        return 2

    if args.status:
        ls = _git_out(project, "ls-remote", "origin", f"refs/heads/{branch}")
        _emit({"status": "status", "branch": branch,
               "remote": ls.split()[0] if ls else None,
               "base": _default_branch(project)}, args.json)
        return 0

    # V3: скоуп — фича существует именно в namespace feature-pipeline
    feat_dir = project / "ground" / "statements" / SKILL_NAME / slug
    if not (feat_dir / "manifest.json").exists():
        _err(f"нет {feat_dir / 'manifest.json'} — интеграционная ветка заводится только "
             f"для фич feature-pipeline.")
        return 2

    # V4: проект в git и с remote origin
    if not _git_out(project, "rev-parse", "--show-toplevel"):
        _err(f"{project} не в git-репозитории.")
        return 1
    if _git_out(project, "remote", "get-url", "origin") is None:
        _err(f"в {project} нет remote origin — настрой remote и повтори.")
        return 1

    # Существующую ветку НИКОГДА не двигаем (идемпотентность)
    ls = _git_out(project, "ls-remote", "origin", f"refs/heads/{branch}")
    if ls:
        _emit({"status": "exists", "branch": branch, "commit": ls.split()[0],
               "repo": str(project)}, args.json)
        return 0

    base = _default_branch(project)
    if base is None:
        if _remote_is_empty(project):
            _err("remote origin пуст — интеграционной ветке не от чего ветвиться; "
                 "сначала запушь default-ветку проекта.")
        else:
            _err("не удалось определить default-ветку origin — выполни "
                 "`git remote set-head origin -a` и повтори.")
        return 1
    _git(project, "fetch", "origin", base)
    tip = _git_out(project, "rev-parse", "--verify", "--quiet",
                   f"refs/remotes/origin/{base}^{{commit}}")
    if not tip:
        _err(f"remote-tip default-ветки origin/{base} недоступен (fetch не удался?).")
        return 1

    # Пуш существующего sha на новое имя: НИКАКИХ коммитов, локальных ref и force.
    r = _git(project, "push", "origin", f"{tip}:refs/heads/{branch}", timeout=120)
    if r.returncode != 0:
        _err(f"git push отклонён: {r.stderr.strip()}")
        return 1

    _emit({"status": "created", "branch": branch, "base": base, "commit": tip,
           "repo": str(project)}, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
