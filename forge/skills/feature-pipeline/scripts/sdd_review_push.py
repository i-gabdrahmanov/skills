#!/usr/bin/env python3
"""sdd_review_push.py — доставка утверждённого SDD на ветку согласования sdd-review/<slug>.

Единственный легальный способ вынести sdd.md на ревью системным аналитикам на фазе 02-sdd
(Гейт SDD-ревью). Сырые `git commit`/`git push` в spec-фазе блокирует sod-enforcer без
исключений, а запуск ЭТОГО скрипта гейтится gate-guard'ом: нужен approval-маркер
ground/approvals/sdd-review-<slug>.json с провенансом record_approval — он появляется
ТОЛЬКО после явного «да» пользователя (record_approval.py).

Скрипт un-abusable по построению: нет --path/--message/--branch/--remote/--force —
коммитится ТОЛЬКО <feature_docs_dir>/<slug>/sdd.md на refs/heads/sdd-review/<slug> в origin,
без force. Механика — git plumbing через временный индекс: worktree/HEAD/индекс пользователя
не трогаются вообще (dirty tree, detached HEAD и обрыв посреди операции безопасны).
В docs.mode=separate-repo ветка создаётся в репо спеки; approvals/judges читаются из корня
ПРОЕКТА. Повторный запуск идемпотентен (up-to-date / fast-forward поверх remote-tip).

Usage:
    sdd_review_push.py --feature <slug> [--project <root>] [--jira-key <KEY>] [--status] [--json]

Exit: 0 — запушено/актуально (и --status); 2 — гейт/валидация (маркер, судья, аргументы);
1 — операционная git-ошибка (нет remote, push отклонён).
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
BRANCH_PREFIX = "sdd-review"
JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def _err(msg: str) -> None:
    print(f"[sdd-review] {msg}", file=sys.stderr)


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


def _resolve_jira_key(arg_key: str | None, slug: str, sdd_text: str) -> str | None:
    """Jira-ключ: --jira-key → slug (если сам ключ) → строка `**Jira:** KEY` из sdd.md."""
    if arg_key:
        if not JIRA_KEY_RE.match(arg_key):
            raise ValueError(f"невалидный --jira-key: {arg_key!r} (ожидается вида STOR-123)")
        return arg_key
    if JIRA_KEY_RE.match(slug):
        return slug
    m = re.search(r"\*\*Jira:?\*\*:?\s*([A-Z][A-Z0-9]*-\d+)", sdd_text)
    return m.group(1) if m else None


def _default_parent(repo: Path) -> str | None:
    """База новой ветки: origin/HEAD → origin/main|master → HEAD (детач не мешает)."""
    head = _git_out(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    for ref in ([head] if head else []) + ["refs/remotes/origin/main",
                                           "refs/remotes/origin/master", "HEAD"]:
        sha = _git_out(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if sha:
            return sha
    return None  # пустой репозиторий — корневой коммит


def _docs_repo(sdd_path: Path) -> Path | None:
    top = _git_out(sdd_path.parent, "rev-parse", "--show-toplevel")
    return Path(top).resolve() if top else None


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[sdd-review] " + " ".join(f"{k}={v}" for k, v in result.items()))


def do_status(project: Path, slug: str, as_json: bool) -> int:
    """Ридонли-отчёт: состояние ветки согласования (без fetch — только ls-remote)."""
    branch = f"{BRANCH_PREFIX}/{slug}"
    sdd_path = skill_paths.feature_docs_dir(project) / slug / "sdd.md"
    repo = _docs_repo(sdd_path) if sdd_path.parent.exists() else None
    local = remote = None
    if repo:
        local = _git_out(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        ls = _git_out(repo, "ls-remote", "origin", f"refs/heads/{branch}")
        remote = ls.split()[0] if ls else None
    marker = _read_json(project / "ground" / "approvals" / f"sdd-review-{slug}.json")
    _emit({"status": "status", "branch": branch, "sdd_exists": sdd_path.exists(),
           "repo": str(repo) if repo else None, "local": local, "remote": remote,
           "approval": bool(marker and marker.get("produced_by") == "record_approval")},
          as_json)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--feature", required=True, help="Slug/Jira-key фичи")
    p.add_argument("--project", default=None, help="Корень проекта (default: git toplevel cwd)")
    p.add_argument("--jira-key", default=None, help="Jira-ключ для сообщения коммита")
    p.add_argument("--status", action="store_true", help="Ридонли-отчёт о состоянии ветки")
    p.add_argument("--json", action="store_true", help="Машинный вывод")
    args = p.parse_args()

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
        return do_status(project, slug, args.json)

    # V3: скоуп — фича существует именно в namespace feature-pipeline (решение «только full»)
    feat_dir = project / "ground" / "statements" / SKILL_NAME / slug
    if not (feat_dir / "manifest.json").exists():
        _err(f"нет {feat_dir / 'manifest.json'} — гейт SDD-ревью работает только "
             f"для фич feature-pipeline.")
        return 2

    # V4: approval-маркер с провенансом и совпадающим ключом
    key = f"sdd-review-{slug}"
    marker_path = project / "ground" / "approvals" / f"{key}.json"
    marker = _read_json(marker_path)
    if not marker or marker.get("produced_by") != "record_approval" or marker.get("key") != key:
        _err(f"нет валидного approval-маркера {marker_path} (провенанс record_approval, "
             f"key={key}). Порядок: спроси пользователя на Гейте SDD-ревью; после явного «да» — "
             f"pipeline-state/scripts/record_approval.py --key {key} --approved-by user "
             f"--reason \"...\"; затем повтори.")
        return 2

    # V5: SDD прошёл детерминированный гейт (run_judge sdd) — непройденный не выносим
    verdict = _read_json(feat_dir / "judges" / "sdd-judge.json")
    if not verdict or verdict.get("produced_by") != "run_judge" or verdict.get("passed") is not True:
        _err("sdd-judge не PASS (нет judges/sdd-judge.json с produced_by=run_judge и "
             "passed=true) — прогони run_judge.py sdd и доведи SDD до pass.")
        return 2

    # V6: артефакт существует и непуст (без хардкода docs/ — резолвер skill_paths)
    sdd_path = skill_paths.feature_docs_dir(project) / slug / "sdd.md"
    if not sdd_path.exists() or not sdd_path.read_text(encoding="utf-8", errors="replace").strip():
        _err(f"sdd.md не найден или пуст: {sdd_path}")
        return 2
    sdd_text = sdd_path.read_text(encoding="utf-8", errors="replace")

    # V7: secret-scan ДО любого git-действия (паттерны — единый источник check_secrets)
    violations = check_secrets.scan_text(str(sdd_path), sdd_text)
    if violations:
        _err("в sdd.md найден потенциальный секрет — на remote не выносится:\n" +
             "\n".join(f"  строка {v['line']} [{v['kind']}]: {v['detail']}" for v in violations))
        return 2

    # V8: репо доков в git и с remote origin
    repo = _docs_repo(sdd_path)
    if not repo:
        _err(f"каталог доков ({sdd_path.parent}) не в git-репозитории.")
        return 1
    if _git_out(repo, "remote", "get-url", "origin") is None:
        _err(f"в {repo} нет remote origin — настрой remote либо согласуй SDD локальной веткой.")
        return 1

    try:
        jira_key = _resolve_jira_key(args.jira_key, slug, sdd_text)
    except ValueError as e:
        _err(str(e))
        return 2

    # V9: сообщение составляет сам скрипт; пол evidence-enforcer — по построению
    msg = f"docs(sdd): {slug} — SDD на согласование"
    if jira_key and jira_key != slug:
        msg += f"\n\nJira: {jira_key}"
    assert "co-authored-by" not in msg.lower()

    branch = f"{BRANCH_PREFIX}/{slug}"
    ref = f"refs/heads/{branch}"
    try:
        rel = sdd_path.resolve().relative_to(repo).as_posix()
    except ValueError:
        _err(f"sdd.md ({sdd_path}) вне репозитория доков ({repo}).")
        return 1

    # remote-tip первичен (fetch best-effort): повтор после гонки перебазируется сам
    _git(repo, "fetch", "origin", branch)
    remote_tip = _git_out(repo, "rev-parse", "--verify", "--quiet",
                          f"refs/remotes/origin/{branch}")
    local_tip = _git_out(repo, "rev-parse", "--verify", "--quiet", ref)
    parent = remote_tip or local_tip or _default_parent(repo)

    # git plumbing во временном индексе — worktree/HEAD/индекс пользователя не трогаем
    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(td) / "index")}
        r = (_git(repo, "read-tree", parent, env=env) if parent
             else _git(repo, "read-tree", "--empty", env=env))
        if r.returncode != 0:
            _err(f"git read-tree: {r.stderr.strip()}")
            return 1
        blob = _git_out(repo, "hash-object", "-w", "--", str(sdd_path), env=env)
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

    r = _git(repo, "update-ref", ref, commit)
    if r.returncode != 0:
        _err(f"git update-ref: {r.stderr.strip()}")
        return 1

    if remote_tip == commit:
        _emit({"status": "up-to-date", "branch": branch, "commit": commit,
               "repo": str(repo), "sdd": rel, "parent": parent}, args.json)
        return 0

    r = _git(repo, "push", "origin", f"{ref}:{ref}", timeout=120)  # НИКОГДА не force
    if r.returncode != 0:
        _err(f"git push отклонён: {r.stderr.strip()}\n"
             "Если ветка ушла вперёд на remote (аналитик запушил) — просто повтори запуск: "
             "скрипт возьмёт remote-tip родителем и сделает fast-forward.")
        return 1

    _emit({"status": "pushed", "branch": branch, "commit": commit,
           "repo": str(repo), "sdd": rel, "parent": parent}, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
