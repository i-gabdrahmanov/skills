#!/usr/bin/env python3
"""log-agent.py — единый лог тул-активности агента и субагентов GigaCode/Qwen.

Вызывается рантаймом как command-хук (SYNC — без async:true, чтобы лог не «флапал» и
ошибки не глотались) на событиях PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart,
SubagentStop, SessionStart, Stop. Читает hook-JSON из stdin и дописывает событие в общий лог
прогона + в пер-агентный файл. Лёгкий: только файловый append под flock, без сети/тяжёлых
операций — безопасно держать sync на каждом тул-вызове.

Группировка: ОДИН каталог на сессию — «один прогон = одна папка / один таймлайн»:
  <root>/ground/ai-logs/run-<session8>/
(раньше ключевались по новейшему манифесту + iter-<N> и логи одного прогона разъезжались по
 <feature>/iter-* и _adhoc/* — теперь каталог даёт общий _project.run_dir, тот же, что у budget-meter).

В каждом каталоге прогона ровно два файла на весь прогон:
  agents.log    — человекочитаемый лог: главный агент + все субагенты + ошибки,
                  каждая строка с меткой [<label>] (main / <agent_type>-<id8>)
  agents.jsonl  — те же события машиночитаемо (для agentops-отчётов)
Отдельных по-агентных файлов нет — фильтр по агенту делается grep'ом по метке.

Никогда не падает с ненулевым кодом — логирование не должно ронять прогон (хук async,
вывод/exit всё равно игнорируются, но на всякий случай возвращаем 0).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

# Каталог прогона, git-корень и конкурентно-безопасный append — единый источник в _project,
# чтобы log-agent и budget-meter НЕ разъезжались (раньше держали свои копии _run_dir и
# budget.json улетал в другой каталог, чем agents.log/.jsonl).
from _project import append_locked, git_toplevel, run_dir

TRUNC = int(os.environ.get("GIGACODE_LOG_TRUNC", "4000"))  # длина payload; для анализа крупнее (env-override)


def _safe(s) -> str:
    """Имя файла/каталога без сюрпризов."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s)).strip("-")
    return s or "x"


def _trunc(obj) -> str:
    """payload как строка, обрезанная до TRUNC символов (с маркером усечения)."""
    if obj is None:
        return ""
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    s = s.replace("\n", "\\n")
    if len(s) > TRUNC:
        return s[:TRUNC] + f"…(+{len(s) - TRUNC} chars)"
    return s


def _agent_label(data: dict) -> str:
    at = data.get("agent_type")
    if not at:
        return "main"
    aid = _safe(data.get("agent_id", ""))[:8]
    return _safe(at) + (f"-{aid}" if aid else "")


def _human(data: dict, label: str) -> str:
    ev = data.get("hook_event_name", "?")
    tool = data.get("tool_name", "")
    bits = [datetime.now().strftime("%H:%M:%S"), f"[{label}]", ev]
    if tool:
        bits.append(f"tool={tool}")
    if ev == "PreToolUse":
        bits.append(_trunc(data.get("tool_input")))
    elif ev == "PostToolUse":
        bits.append(_trunc(data.get("tool_response")))
    elif ev == "PostToolUseFailure":
        bits.append("ERR " + _trunc(data.get("error")))
    elif ev in ("SubagentStart", "SubagentStop"):
        bits.append("agent=" + str(data.get("agent_type", "")))
        if ev == "SubagentStop" and data.get("last_assistant_message"):
            bits.append(_trunc(data.get("last_assistant_message")))
    elif ev == "UserPromptSubmit":
        bits.append("PROMPT " + _trunc(data.get("prompt")))
    elif ev in ("Stop", "SessionEnd") and data.get("last_assistant_message"):
        bits.append("FINAL " + _trunc(data.get("last_assistant_message")))
    return "  ".join(b for b in bits if b) + "\n"


def _record(data: dict, label: str, root: str = "") -> str:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": data.get("hook_event_name"),
        "session_id": data.get("session_id"),
        "project": os.path.basename(root) if root else None,
        "cwd": data.get("cwd"),
        "agent": label,
        "agent_type": data.get("agent_type"),
        "agent_id": data.get("agent_id"),
        "tool_name": data.get("tool_name"),
        "tool_use_id": data.get("tool_use_id"),
    }
    if "tool_input" in data:
        rec["tool_input"] = _trunc(data.get("tool_input"))
    if "tool_response" in data:
        rec["tool_response"] = _trunc(data.get("tool_response"))
    if "error" in data:
        rec["error"] = _trunc(data.get("error"))
    if data.get("hook_event_name") == "SubagentStop":
        rec["agent_transcript_path"] = data.get("agent_transcript_path")
    if data.get("last_assistant_message"):
        rec["last_assistant_message"] = _trunc(data.get("last_assistant_message"))
    if "prompt" in data:
        rec["prompt"] = _trunc(data.get("prompt"))
    if "permission_mode" in data:
        rec["permission_mode"] = data.get("permission_mode")
    return json.dumps(rec, ensure_ascii=False) + "\n"


def _record_code_access(data: dict, root: str, label: str) -> None:
    """Если агент читает код — записать evidence в ground/phases/agent-evidence.jsonl."""
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    command = str(tool_input.get("command") or "")

    # Определяем событие: read, search, glob
    read_events = {"Read", "ReadFile"}
    search_events = {"GrepSearch", "Grep"}
    glob_events = {"Glob"}

    event = None
    if tool_name in read_events and file_path:
        event = "read_code"
    elif tool_name in search_events and ("src/" in command or "src/" in file_path):
        event = "search_code"
    elif tool_name in glob_events and ("src/" in command or "src/" in file_path):
        event = "glob_code"

    if not event:
        return

    # Если это чтение grounding-index — записываем отдельно
    if "grounding-index" in file_path:
        event = "read_grounding"

    # Если это чтение gate.json или phase-defs — не пишем (циклично)
    if "ground/phases" in file_path or ".gigacode/" in file_path:
        return

    # evidence пишем в namespace активной фичи (per-feature)
    from pathlib import Path as _Path
    from _project import active_feature, phases_dir
    ev_path = str(phases_dir(_Path(root), active_feature(_Path(root))) / "agent-evidence.jsonl")
    os.makedirs(os.path.dirname(ev_path), exist_ok=True)

    ev = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "agent": label,
        "agent_type": data.get("agent_type"),
        "path": file_path or command[:120],
    }

    with open(ev_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        root = git_toplevel(data.get("cwd", ""))
        run = run_dir(root, data.get("session_id", ""))
        label = _agent_label(data)

        # ── Evidence: запись доступа к коду / grounding ─────────────
        _record_code_access(data, root, label)

        jline, hline = _record(data, label, root), _human(data, label)
        # Единый лог прогона: один человекочитаемый .log + один машинный .jsonl.
        # Метка [<agent>] в каждой строке отделяет главного агента от субагентов,
        # поэтому отдельные by-agent/* файлы не нужны (фильтровать можно grep'ом по метке).
        append_locked(os.path.join(run, "agents.log"), hline)
        append_locked(os.path.join(run, "agents.jsonl"), jline)
        # единый кросс-прогонный/кросс-проектный архив для анализа (монтажная ротация)
        append_locked(_archive_path(), jline)
    except Exception:
        return 0  # никогда не ломаем прогон из-за логирования
    return 0


def _archive_path() -> str:
    """Единый лог всех агентов/субагентов по всем прогонам и проектам — для анализа.
    GIGACODE_AILOG_ARCHIVE (env) переопределяет каталог; иначе <home>/ai-logs-archive рядом с hooks."""
    base = os.environ.get("GIGACODE_AILOG_ARCHIVE") or \
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-logs-archive")
    return os.path.join(base, "agents-" + datetime.now().strftime("%Y%m") + ".jsonl")


if __name__ == "__main__":
    sys.exit(main())
