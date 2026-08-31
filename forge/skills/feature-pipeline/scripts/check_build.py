#!/usr/bin/env python3
"""check_build.py — артефакты задач существуют на диске + (опц.) сборка проходит.

Gate фазы Build (step 04-build-<id>). Ловит «LLM сказал, что написал код, а файла нет»
и несобирающийся проект. Артефакты в task-plan бывают repo-root-relative (multi-module)
или относительно module/src/main/java — матчим оба варианта суффиксно.

Usage:
    check_build.py <task-plan.json> [--root .] [--task T1] [--build] [--pipeline-config policy.json] [--build-cmd CMD] [--json]
Exit: 0 = pass, 2 = нет артефактов / сборка упала.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Phase 0 v2 refactor: load_project_config — двойной рид policy.json → pipeline.json.
# Скилл изолирован от hooks/_project, но hooks/ лежит рядом в одном репо.
_HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"
if str(_HOOKS_DIR) not in sys.path and _HOOKS_DIR.is_dir():
    sys.path.insert(0, str(_HOOKS_DIR))

_SKIP = {"build", "out", "target", ".gradle", ".idea", "node_modules", ".git"}


def _exists(root: Path, artifact: str) -> bool:
    art = artifact.strip().replace("\\", "/").lstrip("/")
    if (root / art).exists():
        return True
    suffix = "/" + art
    name = Path(art).name
    for p in root.rglob(name):
        if any(s in p.parts for s in _SKIP):
            continue
        if str(p).replace("\\", "/").endswith(suffix):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Build artifacts + compile gate.")
    ap.add_argument("plan")
    ap.add_argument("--root", default=".")
    ap.add_argument("--task", help="check only this task id")
    ap.add_argument("--build", action="store_true", help="run build command and require exit 0")
    ap.add_argument("--build-cmd", help="override build command")
    ap.add_argument("--pipeline-config", help="policy.json (для quality.build_command; legacy: pipeline.json)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    tasks = plan.get("tasks", [])
    if args.task:
        tasks = [t for t in tasks if t.get("id") == args.task]

    missing = []
    for t in tasks:
        for art in t.get("artifacts", []):
            if not _exists(root, art):
                missing.append({"task": t.get("id"), "artifact": art})

    build_ok = None
    build_cmd = ""
    if args.build:
        build_cmd = args.build_cmd or ""
        if not build_cmd and args.pipeline_config:
            # Phase 0 v2 refactor: load_project_config — двойной рид policy.json → pipeline.json.
            # Приоритет: явный --pipeline-config (если задан и существует) > auto-detect от root.
            pcfg: dict = {}
            pcfg_path = Path(args.pipeline_config)
            if pcfg_path.exists():
                try:
                    pcfg = json.loads(pcfg_path.read_text(encoding="utf-8"))
                except Exception:
                    pcfg = {}
            else:
                print(f"Предупреждение: pipeline-config не найден: {pcfg_path}", file=sys.stderr)
            if not pcfg:
                try:
                    from _config_loader import load_project_config  # noqa: E402
                    pcfg = load_project_config(root) or {}
                except Exception:
                    pcfg = {}
            build_cmd = (pcfg.get("quality") or {}).get("build_command", "")
        if build_cmd:
            r = subprocess.run(build_cmd, shell=True, cwd=str(root), capture_output=True, text=True)
            build_ok = r.returncode == 0
        else:
            build_ok = None  # нечего запускать

    status = "pass" if (not missing and build_ok in (None, True)) else "fail"
    verdict = {"status": status, "tasks": len(tasks),
               "missing_artifacts": missing, "build_ran": bool(args.build), "build_ok": build_ok}
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if status == "pass" else "✗ FAIL"
        print(f"Build gate: {mark}  (задач: {len(tasks)}, нет артефактов: {len(missing)})")
        for m in missing:
            print(f"  ✗ {m['task']}: нет {m['artifact']}")
        if args.build:
            print(f"  build: {'✓ exit 0' if build_ok else ('✗ non-zero' if build_ok is False else '· не задан')}  {build_cmd}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
