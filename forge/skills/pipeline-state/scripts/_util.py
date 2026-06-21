"""Мелкие общие хелперы скриптов pipeline-state."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def repo_root() -> str:
    """Корень репо: git toplevel или cwd. Чтобы оркестратору не нужен $(pwd)/$(git ...)
    в shell-команде — рантайм Qwen/GigaCode жёстко режет command substitution ($(), backticks),
    и вызов скрипта с такой подстановкой блокируется ещё до запуска python."""
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


# ── Резолв базы docs (ОБЩИЙ контракт с skill_paths.py / _project.py) ──────────
# pipeline-state деплоится глобально (отдельно от feature-pipeline), поэтому держит
# собственную копию резолвера. Синхронность пинится test_docs_resolver_consistency.py.

def load_pipeline_config(project_root: Path) -> dict:
    p = Path(project_root) / "ground" / "pipeline.json"
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _docs_cfg(cfg: Optional[dict], project_root: Path) -> dict:
    cfg = cfg if cfg is not None else load_pipeline_config(project_root)
    docs = cfg.get("docs") if isinstance(cfg, dict) else None
    return docs if isinstance(docs, dict) else {}


def _is_safe_segment(name) -> bool:
    return (isinstance(name, str) and name not in ("", ".", "..")
            and "/" not in name and "\\" not in name and ".." not in name
            and not name.startswith(("~", "/")))


def _clean_subdir(val, default: str) -> str:
    if _is_safe_segment(val):
        return val
    if val is not None and val != default:
        print(f"[_util] docs: небезопасное имя подпапки {val!r} → '{default}'", file=sys.stderr)
    return default


def _clean_rel(val, project_root: Path, default: str) -> Path:
    if isinstance(val, str) and val.strip():
        s = val.strip()
        if not s.startswith(("/", "~")) and ".." not in Path(s).parts:
            return Path(project_root) / s
        print(f"[_util] docs: путь {val!r} выходит за проект → '{default}'", file=sys.stderr)
    elif val is not None:
        print(f"[_util] docs: путь не строка ({val!r}) → '{default}'", file=sys.stderr)
    return Path(project_root) / default


def safe_slug(slug) -> str:
    """Валидный слаг фичи (один компонент пути). ValueError на traversal/разделителях."""
    if not _is_safe_segment(slug):
        raise ValueError(f"небезопасный feature-slug: {slug!r} (запрещены '/', '..', '~', абсолютный, пустой)")
    return slug


def docs_base(project_root: Path, cfg: Optional[dict] = None) -> Path:
    project_root = Path(project_root)
    docs = _docs_cfg(cfg, project_root)
    if docs.get("mode") == "separate-repo":
        rp = docs.get("repo_path")
        if isinstance(rp, str) and rp.strip():
            p = Path(rp.strip()).expanduser()
            return p if p.is_absolute() else (project_root / p)
    return _clean_rel(docs.get("docs_path"), project_root, "docs")


def feature_docs_dir(project_root: Path, cfg: Optional[dict] = None) -> Path:
    project_root = Path(project_root)
    docs = _docs_cfg(cfg, project_root)
    legacy = docs.get("feature_docs_path")
    if (isinstance(legacy, str) and legacy and docs.get("mode") != "separate-repo"
            and not legacy.startswith(("/", "~")) and ".." not in Path(legacy).parts):
        return project_root / legacy
    return docs_base(project_root, cfg) / _clean_subdir(docs.get("feature_subdir"), "feature-pipeline")
