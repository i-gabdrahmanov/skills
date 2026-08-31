#!/usr/bin/env python3
"""Git-чекпойнты рабочего дерева на границах шагов пайплайна.

Снимок ВСЕГО worktree (tracked + untracked, .gitignore уважается) кладётся коммитом
на служебный ref refs/forge/checkpoints/<feature>/<step-id> — ветки, HEAD, пользовательский
индекс и stash не затрагиваются (временный индекс через GIT_INDEX_FILE). Блобы
дедуплицируются git object store, отдельного blob-хранилища не нужно.

Писатели: update.py (закрытие шага), init.py (baseline 00-baseline). Потребитель —
rollback.py (восстановление кода: git restore --source=<ref> по скоупу журнала).
Подделка refs/forge/* инструментами блокируется state-write-guard; легитимные вызовы
идут subprocess-ом из этих скриптов и хуками не перехватываются.

CLI (отладка): checkpoint.py --project <root> --feature <slug> [--list | --create <step-id>]
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

CHECKPOINT_NS = "refs/forge/checkpoints"

# Автор снапшот-коммитов: не зависим от git config пользователя (CI/чистые машины).
_GIT_ENV_IDENT = {
    "GIT_AUTHOR_NAME": "forge-checkpoint",
    "GIT_AUTHOR_EMAIL": "forge@localhost",
    "GIT_COMMITTER_NAME": "forge-checkpoint",
    "GIT_COMMITTER_EMAIL": "forge@localhost",
}


def safe_ref_part(s: str) -> str:
    """Компонент ref-имени: тот же санитайзер, что у update._gate_result_path."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s)).strip("-") or "x"


def checkpoint_ref(feature: str, step_id: str) -> str:
    return f"{CHECKPOINT_NS}/{safe_ref_part(feature)}/{safe_ref_part(step_id)}"


def _git(project: Path, *args: str, env: dict | None = None,
         timeout: int = 60) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **_GIT_ENV_IDENT, **(env or {})}
    return subprocess.run(["git", "-C", str(project), *args],
                          capture_output=True, text=True, timeout=timeout, env=full_env)


def _is_git_repo(project: Path) -> bool:
    try:
        r = _git(project, "rev-parse", "--git-dir", timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def create_checkpoint(project: Path, feature: str, step_id: str) -> str | None:
    """Снапшот worktree → коммит на refs/forge/checkpoints/<feature>/<step-id>.

    Возвращает sha коммита или None (не git-репо / git недоступен / ошибка) — никогда
    не raise: закрытие шага не должно падать из-за чекпойнта (fail-soft, вызывающий
    печатает WARNING). Повторный вызов для того же шага перезаписывает ref.
    """
    project = Path(project)
    try:
        if not _is_git_repo(project):
            print(f"[checkpoint] {project} не git-репо — чекпойнт пропущен", file=sys.stderr)
            return None
        # mkstemp создаёт пустой файл; git считает 0-байтовый индекс повреждённым —
        # удаляем, git создаст сам по этому пути.
        fd, tmp_index = tempfile.mkstemp(prefix="forge-ckpt-index-")
        os.close(fd)
        os.unlink(tmp_index)
        try:
            env = {"GIT_INDEX_FILE": tmp_index}
            # Пустой индекс + add -A = точный снимок worktree (tracked+untracked, без ignored);
            # удалённые из worktree файлы в tree не попадут — это и есть желаемое состояние.
            r = _git(project, "add", "-A", "--", ".", env=env, timeout=300)
            if r.returncode != 0:
                print(f"[checkpoint] git add -A: {r.stderr.strip()}", file=sys.stderr)
                return None
            r = _git(project, "write-tree", env=env)
            if r.returncode != 0:
                print(f"[checkpoint] git write-tree: {r.stderr.strip()}", file=sys.stderr)
                return None
            tree = r.stdout.strip()
        finally:
            if os.path.exists(tmp_index):
                os.unlink(tmp_index)

        commit_args = ["commit-tree", tree, "-m", f"forge checkpoint {feature}/{step_id}"]
        head = _git(project, "rev-parse", "--verify", "-q", "HEAD")
        if head.returncode == 0 and head.stdout.strip():
            commit_args[2:2] = ["-p", head.stdout.strip()]
        r = _git(project, *commit_args)
        if r.returncode != 0:
            print(f"[checkpoint] git commit-tree: {r.stderr.strip()}", file=sys.stderr)
            return None
        commit = r.stdout.strip()

        ref = checkpoint_ref(feature, step_id)
        r = _git(project, "update-ref", ref, commit)
        if r.returncode != 0:
            print(f"[checkpoint] git update-ref {ref}: {r.stderr.strip()}", file=sys.stderr)
            return None
        return commit
    except Exception as e:
        print(f"[checkpoint] ошибка снапшота ({e}) — чекпойнт пропущен", file=sys.stderr)
        return None


def checkpoint_for(project: Path, feature: str, step_id: str) -> str | None:
    """sha чекпойнта шага или None."""
    try:
        r = _git(Path(project), "rev-parse", "--verify", "-q",
                 checkpoint_ref(feature, step_id))
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


def checkpoint_meta(project: Path, feature: str, step_id: str) -> dict | None:
    """{ref, sha, ts} чекпойнта (ts — ISO committer date; нужен журнальному скоупу)."""
    sha = checkpoint_for(project, feature, step_id)
    if not sha:
        return None
    ref = checkpoint_ref(feature, step_id)
    try:
        r = _git(Path(project), "show", "-s", "--format=%cI", sha)
        ts = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        ts = ""
    return {"ref": ref, "sha": sha, "ts": ts}


def list_checkpoints(project: Path, feature: str) -> list[dict]:
    """Чекпойнты фичи: [{ref, step_id, sha, ts}] в порядке возрастания даты коммита."""
    prefix = f"{CHECKPOINT_NS}/{safe_ref_part(feature)}/"
    try:
        r = _git(Path(project), "for-each-ref", "--sort=creatordate",
                 "--format=%(refname)%09%(objectname)%09%(creatordate:iso-strict)", prefix)
        if r.returncode != 0:
            return []
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ref, sha, ts = parts
        out.append({"ref": ref, "step_id": ref[len(prefix):], "sha": sha, "ts": ts})
    return out


def delete_checkpoints(project: Path, feature: str) -> int:
    """Удаляет все refs фичи (уборка при init --force). Возвращает число удалённых."""
    n = 0
    for cp in list_checkpoints(project, feature):
        try:
            if _git(Path(project), "update-ref", "-d", cp["ref"]).returncode == 0:
                n += 1
        except Exception:
            pass
    return n


def read_tree_paths(project: Path, ref: str) -> set[str]:
    """Все пути в дереве чекпойнта (project-relative, posix)."""
    try:
        r = _git(Path(project), "ls-tree", "-r", "--name-only", "-z", ref, timeout=120)
        if r.returncode != 0:
            return set()
        return {p for p in r.stdout.split("\0") if p}
    except Exception:
        return set()


def path_in_ref(project: Path, ref: str, relpath: str) -> bool:
    try:
        return _git(Path(project), "cat-file", "-e", f"{ref}:{relpath}").returncode == 0
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--feature", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--create", metavar="STEP_ID")
    args = p.parse_args()

    project = Path(args.project).resolve()
    if args.list:
        print(json.dumps(list_checkpoints(project, args.feature),
                         indent=2, ensure_ascii=False))
        return 0
    sha = create_checkpoint(project, args.feature, args.create)
    if not sha:
        return 1
    print(json.dumps({"ref": checkpoint_ref(args.feature, args.create), "sha": sha},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
