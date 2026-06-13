#!/usr/bin/env python3
"""eval-guard.py — PreToolUse хук: блокирует запись кода (src/main), пока eval'ы задачи не пройдены.

PDLC v3.5 — Eval-Driven Development: eval'ы пишутся ДО кода (фаза Design).
Этот хук форсирует: файлы в src/main/ не создаются/изменяются, пока eval-plan.json
существует и для соответствующей задачи есть непройденные eval'ы.

Матчится на Write/Edit/WriteFile в src/main/. Блок: exit 2 + stderr.
fail-open: если eval-plan.json нет, eval_enabled=false, или фичи нет — пропускает.

Читает eval-plan.json из папки активной фичи:
  docs/feature-pipeline/<slug>/eval-plan.json

Результаты eval'ов кеширует в ground/statements/feature-pipeline/<slug>/eval-results.json.
Если eval уже однажды прошёл — повторно не гоняет (идемпотентность).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import risk_ladder as R


def _block(reason: str) -> int:
    print(f"[eval-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _load_eval_results(manifest_dir: Path) -> dict:
    path = manifest_dir / "eval-results.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_eval_results(manifest_dir: Path, results: dict) -> None:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / "eval-results.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def _has_passed(results: dict, eval_id: str) -> bool:
    entry = results.get(eval_id)
    return entry is not None and entry.get("status") == "passed"


def _run_eval_command(command: str, cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        passed = result.returncode == 0
        output = result.stdout.strip()
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr.strip()
        return passed, output[:2000]
    except subprocess.TimeoutExpired:
        return False, "Eval command timed out after 300s"
    except Exception as e:
        return False, f"Eval command error: {e}"


def _is_src_main(target_path: str | None) -> bool:
    if not target_path:
        return False
    return "/src/main/" in target_path.replace("\\", "/")


def _target_path(tool_name: str, tool_input: dict) -> str | None:
    if tool_name in ("Write", "WriteFile"):
        return (tool_input.get("file_path") or "").strip()
    if tool_name in ("Edit",):
        return (tool_input.get("file_path") or "").strip()
    return None


def main() -> int:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        return 0

    cwd = data.get("cwd", "")
    root = Path(cwd) if cwd else Path.cwd()
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    target = _target_path(tool_name, tool_input)

    # 1. Проверяем, включён ли eval (fail-open)
    cfg = R.pipeline_cfg(root)
    quality_cfg = cfg.get("quality", {})
    eval_enabled = quality_cfg.get("eval_enabled", True)
    if not eval_enabled:
        return 0

    # 2. Фильтр: только запись в src/main (не src/test)
    if not _is_src_main(target):
        return 0

    # 3. Находим активную фичу
    mp = R.active_manifest(root)
    if not mp or not mp.exists():
        return 0

    manifest = json.loads(mp.read_text(encoding="utf-8"))
    feature_slug = manifest.get("context", {}).get("feature", "")

    # 4. Ищем eval-plan.json
    feature_docs_path = root / cfg.get("docs", {}).get("feature_docs_path", "docs/feature-pipeline")
    eval_plan_path = feature_docs_path / feature_slug / "eval-plan.json"
    if not eval_plan_path.exists():
        return 0

    eval_plan = json.loads(eval_plan_path.read_text(encoding="utf-8"))
    evals = eval_plan.get("evals", [])

    if not evals:
        return 0

    # 5. Определяем текущую задачу по шагам манифеста
    current_task_id = None
    for step in manifest.get("steps", []):
        sid = step.get("id", "")
        if sid.startswith("04-build-") and step.get("status") == "in_progress":
            current_task_id = sid.replace("04-build-", "")
            break

    if not current_task_id:
        return 0

    # 6. Фильтруем eval'ы по текущей задаче
    task_evals = [e for e in evals if e.get("task_id") == current_task_id]
    if not task_evals:
        return 0

    # 7. Загружаем кеш результатов
    manifest_dir = mp.parent
    eval_results = _load_eval_results(manifest_dir)

    # 8. Прогоняем непройденные eval'ы
    failed_evals = []
    for eval_entry in task_evals:
        eval_id = eval_entry["id"]

        if _has_passed(eval_results, eval_id):
            continue

        command = eval_entry.get("command", "")
        if not command:
            eval_results[eval_id] = {"status": "error", "error": "No command specified"}
            failed_evals.append(eval_id)
            continue

        passed, output = _run_eval_command(command, root)
        if passed:
            eval_results[eval_id] = {"status": "passed", "output": output[:500]}
        else:
            eval_results[eval_id] = {"status": "failed", "output": output[:500], "error": f"Eval {eval_id} failed"}
            failed_evals.append(eval_id)

    _save_eval_results(manifest_dir, eval_results)

    # 9. Блокируем, если есть непройденные eval'ы
    if failed_evals:
        details = "; ".join(
            f"{eid}: {eval_results.get(eid, {}).get('output', '')[:120]}"
            for eid in failed_evals
        )
        return _block(
            f"Eval-Driven Development: для задачи {current_task_id} не пройдены eval'ы: "
            f"{failed_evals}. Детали: {details}. "
            f"Сначала добейся прохождения eval'ов, или отключи eval_enabled в pipeline.json."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
