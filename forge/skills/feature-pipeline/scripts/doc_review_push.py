#!/usr/bin/env python3
"""doc_review_push.py — доставка дока (brd.md|sdd.md) на ветку задачи docs/<slug>.

У КАЖДОЙ Jira-задачи (фичи) — СВОЯ ветка согласования docs/<slug> (slug почти всегда
Jira-ключ): brd.md приезжает на неё в фазе 00-brd, sdd.md — в фазе 02-sdd, оба дока живут
на одной ветке задачи. База новой ветки — default-ветка origin (origin/HEAD → main|master),
так что ветка мерджится в основную без сюрпризов.

Единственный легальный способ вынести док на согласование аналитикам (Гейт доставки в фазах
00-brd / 02-sdd: «нужен мердж и пуш?» ДО утверждения). Сырые `git commit`/`git push` в
доко-фазах блокирует sod-enforcer без исключений, а запуск ЭТОГО скрипта гейтится
gate-guard'ом: нужен approval-маркер ground/approvals/<doc>-review-<slug>.json с провенансом
record_approval — он появляется ТОЛЬКО после явного «да» пользователя (record_approval.py).
После успешного пуша пайплайн берёт ПАУЗУ: закрыть шаг фазы update.py не даст, пока после
итогов ревью не зафиксировано утверждение (маркер <doc>-approved-<slug>).

Скрипт un-abusable по построению: нет --path/--message/--branch/--remote/--force —
коммитится ТОЛЬКО <feature_docs_dir>/<slug>/<doc>.md на refs/heads/docs/<slug> в
origin, без force. Механика — git plumbing через временный индекс: worktree/HEAD/индекс/
ЛОКАЛЬНЫЕ ветки пользователя не трогаются вообще (обновляется только remote: push
<sha>:refs/heads/docs/<slug>). В docs.mode=separate-repo ветка создаётся в репо
спеки; approvals/judges читаются из корня ПРОЕКТА. Повторный запуск идемпотентен
(up-to-date / новый коммит поверх remote-tip ветки — правки аналитиков не теряются).

Usage:
    doc_review_push.py --doc <brd|sdd> --feature <slug> [--project <root>]
                       [--jira-key <KEY>] [--status] [--json]

Exit: 0 — запушено/актуально (и --status); 2 — гейт/валидация (маркер, судья, аргументы);
1 — операционная git-ошибка (нет remote/default-ветки, push отклонён).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import check_secrets
import skill_paths

SKILL_NAME = "feature-pipeline"
DOCS = ("brd", "sdd")
BRANCH_PREFIX = "docs"  # одна ветка задачи на фичу — для ОБОИХ доков: docs/<slug>
JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def _err(msg: str) -> None:
    print(f"[doc-review] {msg}", file=sys.stderr)


def _git(repo: Path, *args: str, env: dict | None = None,
         timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)


def _git_out(repo: Path, *args: str, env: dict | None = None) -> str | None:
    r = _git(repo, *args, env=env)
    return r.stdout.strip() if r.returncode == 0 else None


def _read_json(path: Path) -> dict | None:
    try:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _resolve_jira_key(arg_key: str | None, slug: str, doc_text: str) -> str | None:
    """Jira-ключ: --jira-key → slug (если сам ключ) → строка `**Jira:** KEY` из дока."""
    if arg_key:
        if not JIRA_KEY_RE.match(arg_key):
            raise ValueError(f"невалидный --jira-key: {arg_key!r} (ожидается вида STOR-123)")
        return arg_key
    if JIRA_KEY_RE.match(slug):
        return slug
    m = re.search(r"\*\*Jira:?\*\*:?\s*([A-Z][A-Z0-9]*-\d+)", doc_text)
    return m.group(1) if m else None


def _default_branch(repo: Path) -> str | None:
    """Имя default-ветки origin: origin/HEAD → origin/main|master → symref с remote.
    Локальные ветки НЕ рассматриваются — мерджим только в то, что есть на remote."""
    head = _git_out(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if head and head.startswith("refs/remotes/origin/"):
        return head[len("refs/remotes/origin/"):]
    for name in ("main", "master"):
        if _git_out(repo, "rev-parse", "--verify", "--quiet",
                    f"refs/remotes/origin/{name}^{{commit}}"):
            return name
    ls = _git_out(repo, "ls-remote", "--symref", "origin", "HEAD")
    if ls:
        m = re.search(r"^ref:\s+refs/heads/(\S+)\s+HEAD", ls, re.M)
        if m:
            return m.group(1)
    return None


def _remote_is_empty(repo: Path) -> bool:
    ls = _git_out(repo, "ls-remote", "--heads", "origin")
    return ls is not None and not ls.strip()


def _docs_repo(doc_path: Path) -> Path | None:
    top = _git_out(doc_path.parent, "rev-parse", "--show-toplevel")
    return Path(top).resolve() if top else None


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[doc-review] " + " ".join(f"{k}={v}" for k, v in result.items()))


def do_status(project: Path, doc: str, slug: str, as_json: bool) -> int:
    """Ридонли-отчёт: ветка задачи, её remote-tip, маркеры и судья (без git-действий)."""
    doc_path = skill_paths.feature_docs_dir(project) / slug / f"{doc}.md"
    repo = _docs_repo(doc_path) if doc_path.parent.exists() else None
    branch = f"{BRANCH_PREFIX}/{slug}"
    remote = None
    if repo:
        ls = _git_out(repo, "ls-remote", "origin", f"refs/heads/{branch}")
        remote = ls.split()[0] if ls else None
    marker = _read_json(project / "ground" / "approvals" / f"{doc}-review-{slug}.json")
    verdict = _read_json(project / "ground" / "statements" / SKILL_NAME / slug /
                         "judges" / f"{doc}-judge.json")
    _emit({"status": "status", "doc": doc, "branch": branch,
           "doc_exists": doc_path.exists(), "repo": str(repo) if repo else None,
           "remote": remote,
           "approval": bool(marker and marker.get("produced_by") == "record_approval"),
           "judge_pass": bool(verdict and verdict.get("produced_by") == "run_judge"
                              and verdict.get("passed") is True)},
          as_json)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--doc", required=True, choices=DOCS, help="Какой док доставляем: brd|sdd")
    p.add_argument("--feature", required=True, help="Slug/Jira-key фичи")
    p.add_argument("--project", default=None, help="Корень проекта (default: git toplevel cwd)")
    p.add_argument("--jira-key", default=None, help="Jira-ключ для сообщения коммита")
    p.add_argument("--status", action="store_true", help="Ридонли-отчёт о состоянии доставки")
    p.add_argument("--json", action="store_true", help="Машинный вывод")
    args = p.parse_args()
    doc = args.doc

    # V1: слаг — один безопасный компонент пути
    try:
        slug = skill_paths.safe_slug(args.feature)
    except ValueError as e:
        _err(str(e))
        return 2

    # V2: --project обязан совпадать с toplevel(cwd) — иначе gate-guard проверил маркер
    # в одном корне, а действие пошло бы в другом
    top = _git_out(Path.cwd(), "rev-parse", "--show-toplevel")
    default_root = Path(top) if top else Path.cwd()
    project = Path(args.project).resolve() if args.project else default_root.resolve()
    if args.project and top and project != Path(top).resolve():
        _err(f"--project ({project}) не совпадает с рабочим репо ({top}) — "
             "маркер проверяется в корне cwd, запускай скрипт из проекта.")
        return 2

    if args.status:
        return do_status(project, doc, slug, args.json)

    # V3: скоуп — фича существует именно в namespace feature-pipeline (решение «только full»)
    feat_dir = project / "ground" / "statements" / SKILL_NAME / slug
    if not (feat_dir / "manifest.json").exists():
        _err(f"нет {feat_dir / 'manifest.json'} — гейт доставки доков работает только "
             f"для фич feature-pipeline.")
        return 2

    # V4: approval-маркер с провенансом и совпадающим ключом
    key = f"{doc}-review-{slug}"
    marker_path = project / "ground" / "approvals" / f"{key}.json"
    marker = _read_json(marker_path)
    if not marker or marker.get("produced_by") != "record_approval" or marker.get("key") != key:
        _err(f"нет валидного approval-маркера {marker_path} (провенанс record_approval, "
             f"key={key}). Порядок: спроси пользователя на Гейте доставки («нужен мердж и пуш "
             f"{doc}.md?»); после явного «да» — pipeline-state/scripts/record_approval.py "
             f"--key {key} --approved-by user --reason \"...\"; затем повтори.")
        return 2

    # V5: док прошёл детерминированный гейт (run_judge) — непройденный не выносим
    verdict = _read_json(feat_dir / "judges" / f"{doc}-judge.json")
    if not verdict or verdict.get("produced_by") != "run_judge" or verdict.get("passed") is not True:
        _err(f"{doc}-judge не PASS (нет judges/{doc}-judge.json с produced_by=run_judge и "
             f"passed=true) — прогони run_judge.py {doc} и доведи док до pass.")
        return 2

    # V6: артефакт существует и непуст (без хардкода docs/ — резолвер skill_paths)
    doc_path = skill_paths.feature_docs_dir(project) / slug / f"{doc}.md"
    if not doc_path.exists() or not doc_path.read_text(encoding="utf-8", errors="replace").strip():
        _err(f"{doc}.md не найден или пуст: {doc_path}")
        return 2
    doc_text = doc_path.read_text(encoding="utf-8", errors="replace")

    # V7: secret-scan ДО любого git-действия (паттерны — единый источник check_secrets)
    violations = check_secrets.scan_text(str(doc_path), doc_text)
    if violations:
        _err(f"в {doc}.md найден потенциальный секрет — на remote не выносится:\n" +
             "\n".join(f"  строка {v['line']} [{v['kind']}]: {v['detail']}" for v in violations))
        return 2

    # V8: репо доков в git и с remote origin
    repo = _docs_repo(doc_path)
    if not repo:
        _err(f"каталог доков ({doc_path.parent}) не в git-репозитории.")
        return 1
    if _git_out(repo, "remote", "get-url", "origin") is None:
        _err(f"в {repo} нет remote origin — настрой remote либо согласуй {doc}.md локально.")
        return 1

    try:
        jira_key = _resolve_jira_key(args.jira_key, slug, doc_text)
    except ValueError as e:
        _err(str(e))
        return 2

    # V9: сообщение составляет сам скрипт; пол evidence-enforcer — по построению
    msg = f"docs({doc}): {slug} — {doc.upper()} на согласование"
    if jira_key and jira_key != slug:
        msg += f"\n\nJira: {jira_key}"
    assert "co-authored-by" not in msg.lower()

    # Куда пушим: ветка задачи docs/<slug> — общая для brd.md и sdd.md этой фичи.
    # База новой ветки — default-ветка origin; существующая ветка первична (правки
    # аналитиков не теряются). Пустой remote (свежий спек-репо) — корневой коммит.
    branch = f"{BRANCH_PREFIX}/{slug}"

    try:
        rel = doc_path.resolve().relative_to(repo).as_posix()
    except ValueError:
        _err(f"{doc}.md ({doc_path}) вне репозитория доков ({repo}).")
        return 1

    # remote-tip ветки задачи первичен (fetch best-effort): повтор после гонки
    # перебазируется сам
    _git(repo, "fetch", "origin", branch)
    branch_tip = _git_out(repo, "rev-parse", "--verify", "--quiet",
                          f"refs/remotes/origin/{branch}^{{commit}}")
    parent = branch_tip
    if parent is None:
        base = _default_branch(repo)
        if base is not None:
            _git(repo, "fetch", "origin", base)
            parent = _git_out(repo, "rev-parse", "--verify", "--quiet",
                              f"refs/remotes/origin/{base}^{{commit}}")
        elif not _remote_is_empty(repo):
            _err("не удалось определить default-ветку origin (база ветки задачи) — "
                 "выполни `git remote set-head origin -a` в репо доков и повтори.")
            return 1

    # git plumbing во временном индексе — worktree/HEAD/индекс/локальные ветки не трогаем
    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(td) / "index")}
        r = (_git(repo, "read-tree", parent, env=env) if parent
             else _git(repo, "read-tree", "--empty", env=env))
        if r.returncode != 0:
            _err(f"git read-tree: {r.stderr.strip()}")
            return 1
        blob = _git_out(repo, "hash-object", "-w", "--", str(doc_path), env=env)
        if not blob:
            _err("git hash-object не удался.")
            return 1
        r = _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)
        if r.returncode != 0:
            _err(f"git update-index: {r.stderr.strip()}")
            return 1
        tree = _git_out(repo, "write-tree", env=env)
    if not tree:
        _err("git write-tree не удался.")
        return 1

    parent_tree = _git_out(repo, "rev-parse", f"{parent}^{{tree}}") if parent else None
    if parent and tree == parent_tree:
        commit = parent  # содержимое не изменилось — новый коммит не нужен
    else:
        cmd = ["commit-tree", tree] + (["-p", parent] if parent else []) + ["-m", msg]
        commit = _git_out(repo, *cmd)
        if not commit:
            _err("git commit-tree не удался.")
            return 1

    if branch_tip == commit:
        _emit({"status": "up-to-date", "doc": doc, "branch": branch, "commit": commit,
               "repo": str(repo), "file": rel, "parent": parent}, args.json)
        return 0

    # ЛОКАЛЬНЫЙ ref не обновляем (никаких веток в клоне пользователя) — пушим sha напрямую.
    r = _git(repo, "push", "origin", f"{commit}:refs/heads/{branch}", timeout=120)  # НИКОГДА не force
    if r.returncode != 0:
        _err(f"git push отклонён: {r.stderr.strip()}\n"
             "Если ветка задачи ушла вперёд на remote (аналитик запушил) — просто повтори "
             "запуск: скрипт возьмёт свежий remote-tip родителем и соберёт коммит поверх.")
        return 1

    _emit({"status": "pushed", "doc": doc, "branch": branch, "commit": commit,
           "repo": str(repo), "file": rel, "parent": parent}, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
