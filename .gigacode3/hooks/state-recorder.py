#!/usr/bin/env python3
"""state-recorder.py — SubagentStop-хук: авто-запись результата субагента в pipeline-state.

Снимает зависимость пайплайна от того, что МОДЕЛЬ сама вызовет update.py после субагента.

Логика (детерминированная, без угадывания):
  1. Берём финальный JSON субагента — из last_assistant_message, иначе из хвоста
     agent_transcript_path (последний валидный ```json``` блок или {…}).
  2. Если в нём есть поле "step_id" (контракт субагентов пайплайна) — вызываем
     pipeline-state/update.py --skill feature-pipeline --step-id <id> --status <completed|failed>
     с этим JSON как output. Статус: "failed", если в JSON status/result == fail/error/blocked.
  3. Если step_id нет — НИЧЕГО не метим (не угадываем), но складываем вывод в
     <root>/ground/ai-logs/_subagent-outputs/<agent_type>-<id8>.json для трассировки.

Никогда не роняет прогон: всегда exit 0 (это пост-событие, блокировать смысла нет).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKILL = "feature-pipeline"
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
UPDATE = SKILLS_DIR / "pipeline-state" / "scripts" / "update.py"

_FAIL_WORDS = {"fail", "failed", "error", "blocked", "false"}


def _project_root(cwd: str) -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        )
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return Path(top)
    except Exception:
        pass
    return Path(cwd or os.getcwd())


def _extract_json(text: str) -> dict | None:
    """Последний валидный JSON-объект из текста: сперва ```json```, потом голые {…}."""
    if not text:
        return None
    candidates: list[str] = []
    candidates += re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    # голые объекты (грубо, но обрабатываем по убыванию длины — берём самый «полный»)
    candidates += re.findall(r"(\{(?:[^{}]|\{[^{}]*\})*\})", text, re.S)
    for cand in sorted(set(candidates), key=len, reverse=True):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _read_transcript_tail(path: str, limit: int = 20000) -> str:
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace")
        return data[-limit:]
    except Exception:
        return ""


def _agent_label(data: dict) -> str:
    at = re.sub(r"[^A-Za-z0-9._-]+", "-", str(data.get("agent_type") or "agent")).strip("-")
    aid = re.sub(r"[^A-Za-z0-9]+", "", str(data.get("agent_id") or ""))[:8]
    return at + (f"-{aid}" if aid else "")


def _status_from(obj: dict) -> str:
    for key in ("status", "result", "ok", "passed"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip().lower() in _FAIL_WORDS:
            return "failed"
        if isinstance(v, bool) and v is False:
            return "failed"
    return "completed"


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        root = _project_root(data.get("cwd", ""))

        text = data.get("last_assistant_message") or ""
        obj = _extract_json(text)
        if obj is None:
            obj = _extract_json(_read_transcript_tail(data.get("agent_transcript_path", "")))

        if not isinstance(obj, dict):
            return 0

        step_id = obj.get("step_id")
        if step_id:
            status = _status_from(obj)
            if UPDATE.exists():
                try:
                    subprocess.run(
                        [sys.executable, str(UPDATE),
                         "--project", str(root), "--skill", SKILL,
                         "--step-id", str(step_id), "--status", status,
                         "--output-json", json.dumps(obj, ensure_ascii=False)],
                        capture_output=True, text=True, timeout=20,
                    )
                except Exception:
                    pass
            return 0

        # нет step_id — не угадываем, просто сохраняем вывод для трассировки
        drop = root / "ground" / "ai-logs" / "_subagent-outputs"
        drop.mkdir(parents=True, exist_ok=True)
        (drop / f"{_agent_label(data)}.json").write_text(
            json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
