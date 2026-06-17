#!/usr/bin/env python3
"""log-agent.py — единый лог тул-активности агента и субагентов GigaCode/Qwen.

Вызывается рантаймом как command-хук (SYNC — без async:true, чтобы лог не «флапал» и
ошибки не глотались) на событиях PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart,
SubagentStop, SessionStart, Stop. Читает hook-JSON из stdin и дописывает событие в общий лог
прогона + в пер-агентный файл. Лёгкий: только файловый append под flock, без сети/тяжёлых
операций — безопасно держать sync на каждом тул-вызове.

Группировка (гибрид):
  есть pipeline-state манифест → <root>/ground/ai-logs/<feature>/iter-<iter>/
  иначе                         → <root>/ground/ai-logs/_adhoc/<YYYYMMDD-HHMM>-<session8>/

В каждом каталоге прогона ровно два файла на весь прогон:
  agents.log    — человекочитаемый лог: главный агент + все субагенты + ошибки,
                  каждая строка с меткой [<label>] (main / <agent_type>-<id8>)
  agents.jsonl  — те же события машиночитаемо (для agentops-отчётов)
Отдельных по-агентных файлов нет — фильтр по агенту делается grep'ом по метке.

Никогда не падает с ненулевым кодом — логирование не должно ронять прогон (хук async,
вывод/exit всё равно игнорируются, но на всякий случай возвращаем 0).
"""
from __future__ import annotations

import fcntl
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import os as _os
TRUNC = int(_os.environ.get("GIGACODE_LOG_TRUNC", "4000"))  # длина payload; для анализа крупнее (env-override)


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


def _project_root(cwd: str) -> str:
    """Корень репо (как в скиллах: git toplevel, иначе cwd)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        )
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return top
    except Exception:
        pass
    return cwd or os.getcwd()


def _run_dir(root: str, data: dict) -> str:
    base = os.path.join(root, "ground", "ai-logs")
    # активный пайплайн = самый свежий манифест pipeline-state
    manifests = [m for m in glob.glob(os.path.join(root, "ground", "statements", "*", "*", "manifest.json"))
                 if os.sep + "archived" + os.sep not in m]
    newest, newest_mtime = None, -1.0
    for m in manifests:
        try:
            mt = os.path.getmtime(m)
        except OSError:
            continue
        if mt > newest_mtime:
            newest, newest_mtime = m, mt
    if newest:
        try:
            with open(newest, encoding="utf-8") as f:
                man = json.load(f)
        except Exception:
            man = {}
        ctx = man.get("context") if isinstance(man.get("context"), dict) else {}
        ctx = ctx or {}
        feature = ctx.get("feature") or man.get("skill") or "pipeline"
        it = ctx.get("iteration")
        if it is None:
            pid = str(man.get("pipeline_id", "run"))
            it = pid[-6:] if len(pid) > 6 else pid  # хвост таймстампа pipeline_id
        return os.path.join(base, _safe(feature), "iter-" + _safe(it))
    # фоллбэк: ad-hoc по сессии
    sess = _safe(data.get("session_id", "nosess"))[:8] or "nosess"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return os.path.join(base, "_adhoc", f"{stamp}-{sess}")


def _agent_label(data: dict) -> str:
    at = data.get("agent_type")
    if not at:
        return "main"
    aid = _safe(data.get("agent_id", ""))[:8]
    return _safe(at) + (f"-{aid}" if aid else "")


def _append(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # безопасный конкурентный append
            f.write(text)
            f.flush()
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


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
        root = _project_root(data.get("cwd", ""))
        run = _run_dir(root, data)
        label = _agent_label(data)

        # ── Evidence: запись доступа к коду / grounding ─────────────
        _record_code_access(data, root, label)

        jline, hline = _record(data, label, root), _human(data, label)
        # Единый лог прогона: один человекочитаемый .log + один машинный .jsonl.
        # Метка [<agent>] в каждой строке отделяет главного агента от субагентов,
        # поэтому отдельные by-agent/* файлы не нужны (фильтровать можно grep'ом по метке).
        _append(os.path.join(run, "agents.log"), hline)
        _append(os.path.join(run, "agents.jsonl"), jline)
        # единый кросс-прогонный/кросс-проектный архив для анализа (монтажная ротация)
        _append(_archive_path(), jline)
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
