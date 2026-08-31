#!/usr/bin/env python3
"""sync_master.py — синхронизация КЛОНА мастер-репо перед grounding.

Только для docs.master.mode=separate-repo (мастер в отдельном/удалённом репо). Политика
forge-no-delivery: делаем лишь READ-освежение (`git pull --ff-only`); коммит/push мастера —
на пользователе. Если мастер co-located (не separate-repo) — no-op.

Usage:
    sync_master.py --project <root> [--pull]
Exit:
    0 — ок / нечего делать / pull не удался (мягко, продолжаем на локальной версии)
    2 — клон мастер-репо отсутствует (СТОП: попроси пользователя склонировать)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _load_cfg(project: Path) -> dict:
    p = project / "ground" / "pipeline.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def master_repo(project: Path, cfg: dict) -> "Path | None":
    """Корень КЛОНА мастер-репо, если мастер в separate-repo; иначе None (co-located)."""
    docs = (cfg.get("docs") or {}) if isinstance(cfg, dict) else {}
    m = docs.get("master")
    if not isinstance(m, dict):
        return None
    mode = m.get("mode", docs.get("mode"))
    if mode != "separate-repo":
        return None
    rp = m.get("repo_path") or docs.get("repo_path")
    if not (isinstance(rp, str) and rp.strip()):
        return None
    p = Path(rp.strip()).expanduser()
    return p if p.is_absolute() else (project / p)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync master spec clone before grounding.")
    ap.add_argument("--project", default=".")
    ap.add_argument("--pull", action="store_true", help="git pull --ff-only клона мастера")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    cfg = _load_cfg(project)
    repo = master_repo(project, cfg)

    if repo is None:
        print("sync_master: мастер co-located (не separate-repo) — синхронизация не нужна")
        return 0

    if not repo.exists() or not (repo / ".git").is_dir():
        url = ((cfg.get("docs") or {}).get("master") or {}).get("repo_url") or "<docs.master.repo_url>"
        print(f"⛔ STOP: клон мастер-репо не найден: {repo}", file=sys.stderr)
        print(f"   склонируй репо мастер-спеки:  git clone {url} {repo}", file=sys.stderr)
        return 2

    if not args.pull:
        print(f"sync_master: клон найден: {repo} (pull не запрошен)")
        return 0

    # Сетевой pull — только при явном opt-in sdd.pull_before_grounding (проверку клона выше
    # делаем всегда, чтобы отсутствие клона ловилось рано, до grounding).
    if not bool((cfg.get("sdd") or {}).get("pull_before_grounding", False)):
        print(f"sync_master: клон найден: {repo} (sdd.pull_before_grounding=false — pull пропущен)")
        return 0

    try:
        r = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            print(f"✅ sync_master: мастер обновлён (git pull --ff-only): {repo}")
        else:
            print(f"· warn: git pull --ff-only не удался "
                  f"({(r.stderr or r.stdout).strip()[:200]}) — продолжаю на локальной версии мастера",
                  file=sys.stderr)
        return 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"· warn: git pull не выполнен ({e}) — продолжаю на локальной версии", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
