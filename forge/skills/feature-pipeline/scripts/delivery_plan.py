#!/usr/bin/env python3
"""delivery_plan.py — детерминированный ИДЕМПОТЕНТНЫЙ план доставки (P1-7).

Доставка (push веток + создание PR) — необратима, а Bitbucket-PR НЕ дедуплицируется: если
фаза упала после доставки T1, перезапуск вслепую создаст дубль ветки/PR. Этот скрипт считает
из ДЕТЕРМИНИРОВАННОГО состояния (git-ветки + manifest), что уже сделано, и выдаёт план
«create / resume / skip» на задачу — его оркестратор смотрит ПЕРЕД необратимым гейтом push/PR.

Ключ идемпотентности — имя ветки задачи (стабильно из jira-key или slug+taskId). Сигналы:
  • deliver-шаг `07-deliver-<id>` completed в manifest → задача уже доставлена (skip);
  • ветка существует (локально/в origin), но шаг не закрыт → resume (НЕ пересоздавать ветку,
    довести push/PR, проверив существующий PR);
  • ничего нет → create.

Usage:
    delivery_plan.py <task-plan.json> --manifest <manifest.json> [--root .]
        [--pipeline-config pipeline.json] [--jira-result jira-tasks-result.json]
        [--prefix 07-deliver-] [--branches "a,b,c"] [--no-remote] [--json]

`--branches` (через запятую/пробел) подменяет git-список локальных веток — для тестов/offline.
Exit: 0 всегда (это план, не гейт). Поле summary.all_done=true, если доставлять нечего.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _load(p) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _git(root: Path, *args: str, timeout: int = 10) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=timeout)
        return out.stdout.splitlines() if out.returncode == 0 else []
    except Exception:
        return []


def _local_branches(root: Path) -> set[str]:
    return {b.strip() for b in _git(root, "branch", "--format=%(refname:short)") if b.strip()}


def _remote_branches(root: Path) -> tuple[set[str], bool]:
    """(имена веток origin, удалось_ли_проверить). ls-remote ходит в сеть — на offline
    возвращаем (∅, False), чтобы remote не трактовался как «ветки нет»."""
    lines = _git(root, "ls-remote", "--heads", "origin", timeout=15)
    if not lines:
        return set(), False
    names = set()
    for ln in lines:
        m = re.search(r"refs/heads/(.+)$", ln.strip())
        if m:
            names.add(m.group(1))
    return names, True


def _default_branch(cfg: dict, root: Path) -> str:
    db = (cfg.get("project") or {}).get("default_branch")
    if isinstance(db, str) and db.strip():
        return db.strip()
    for ln in _git(root, "symbolic-ref", "refs/remotes/origin/HEAD"):
        m = re.search(r"refs/remotes/origin/(.+)$", ln.strip())
        if m:
            return m.group(1)
    return "main"


def _jira_keys(jira_result: dict) -> dict:
    """task_id → jira key из ledger jira-task-writer (несколько форматов, как в check_jira)."""
    if not jira_result or jira_result.get("skipped"):
        return {}
    if isinstance(jira_result.get("tasks"), dict):
        return {k: v for k, v in jira_result["tasks"].items() if isinstance(v, str)}
    if isinstance(jira_result.get("subtasks"), list):
        return {s.get("task_id"): s.get("key")
                for s in jira_result["subtasks"] if s.get("task_id") and s.get("key")}
    return {}


def _branch_name(tid: str, jira_keys: dict, slug: str, branch_prefix: str) -> str:
    key = jira_keys.get(tid)
    if isinstance(key, str) and key.strip():
        return f"{branch_prefix}{key.strip()}"
    return f"{branch_prefix}{slug}-{tid}"


def _topo_order(tasks: list) -> list:
    """Задачи по зависимостям (Kahn); циклы/висячие deps не роняют — добавляем как есть."""
    ids = [t.get("id") for t in tasks if t.get("id")]
    idset = set(ids)
    deps = {t["id"]: [d for d in (t.get("depends_on") or []) if d in idset]
            for t in tasks if t.get("id")}
    ordered, seen = [], set()
    # стабильный Kahn: пока есть задачи, чьи зависимости уже выложены
    remaining = list(ids)
    progress = True
    while remaining and progress:
        progress = False
        for tid in list(remaining):
            if all(d in seen for d in deps.get(tid, [])):
                ordered.append(tid); seen.add(tid); remaining.remove(tid)
                progress = True
    ordered.extend(remaining)  # цикл/висячая зависимость — в конец, как есть
    return ordered


def build_plan(plan: dict, manifest: dict, cfg: dict, jira_result: dict, root: Path,
               prefix: str, local_branches: set[str], remote_branches: set[str],
               remote_checked: bool) -> dict:
    tasks = plan.get("tasks", [])
    by_tid = {t.get("id"): t for t in tasks if t.get("id")}
    order = _topo_order(tasks)

    jira_keys = _jira_keys(jira_result)
    slug = plan.get("feature_slug") or manifest.get("context", {}).get("feature", "feature")
    branch_prefix = (cfg.get("delivery") or {}).get("branch_prefix", "feature/")
    default_branch = _default_branch(cfg, root)

    step_status = {(s.get("id") or "").lower(): s.get("status")
                   for s in manifest.get("steps", [])}
    branch_of = {tid: _branch_name(tid, jira_keys, slug, branch_prefix) for tid in order}

    rows = []
    for tid in order:
        branch = branch_of[tid]
        deps = [d for d in (by_tid[tid].get("depends_on") or []) if d in by_tid]
        target = branch_of[deps[0]] if deps else default_branch

        delivered = step_status.get(f"{prefix}{tid}".lower()) == "completed"
        local = branch in local_branches
        remote = branch in remote_branches

        if delivered:
            action = "skip"      # уже доставлено — не пушить/не создавать PR заново
        elif local or remote:
            action = "resume"    # ветка есть, шаг не закрыт — довести, не пересоздавать
        else:
            action = "create"

        rows.append({
            "task_id": tid, "branch": branch, "target": target, "action": action,
            "delivered": delivered, "branch_local": local,
            "branch_remote": (remote if remote_checked else None),
        })

    by_action = {"create": 0, "resume": 0, "skip": 0}
    for r in rows:
        by_action[r["action"]] += 1
    return {
        "feature_slug": slug,
        "default_branch": default_branch,
        "remote_checked": remote_checked,
        "tasks": rows,
        "summary": {
            "total": len(rows), "by_action": by_action,
            "all_done": by_action["create"] == 0 and by_action["resume"] == 0,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Идемпотентный план доставки (resume-aware).")
    ap.add_argument("plan")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--pipeline-config")
    ap.add_argument("--jira-result")
    ap.add_argument("--prefix", default="07-deliver-")
    ap.add_argument("--branches", help="подмена локальных веток (через ,/пробел) — тест/offline")
    ap.add_argument("--no-remote", action="store_true", help="не ходить в origin (ls-remote)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    plan = _load(args.plan)
    manifest = _load(args.manifest)
    cfg = _load(args.pipeline_config) if args.pipeline_config else {}
    jira_result = _load(args.jira_result) if args.jira_result else {}

    if args.branches is not None:
        local = {b for b in re.split(r"[,\s]+", args.branches.strip()) if b}
        remote, remote_checked = set(), False
    else:
        local = _local_branches(root)
        if args.no_remote:
            remote, remote_checked = set(), False
        else:
            remote, remote_checked = _remote_branches(root)

    result = build_plan(plan, manifest, cfg, jira_result, root, args.prefix,
                        local, remote, remote_checked)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        s = result["summary"]
        print(f"Delivery plan: {s['total']} задач — "
              f"create={s['by_action']['create']}, resume={s['by_action']['resume']}, "
              f"skip={s['by_action']['skip']}"
              + ("  ✓ всё уже доставлено" if s["all_done"] else ""))
        if not result["remote_checked"]:
            print("  (origin не проверялся — remote-ветки неизвестны; resume по локальным/manifest)")
        for r in result["tasks"]:
            tag = {"create": "＋", "resume": "↻", "skip": "✓"}[r["action"]]
            print(f"  {tag} {r['action']:6} {r['task_id']:6} {r['branch']} → {r['target']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
