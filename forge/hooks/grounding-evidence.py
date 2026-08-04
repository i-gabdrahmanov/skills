#!/usr/bin/env python3
"""grounding-evidence.py — фиксирует чтение grounding-index в agent-evidence.jsonl.

НЕ логгер. Единственная задача: когда агент читает `grounding-index.json`, дописать одну
evidence-запись `read_grounding` в `ground/phases/<feature>/agent-evidence.jsonl`. Её читает
`gate-guard`, чтобы снять блок фазы `01-grounding` (пока агент не сверился с grounding-index,
чтение `src/` заблокировано).

Вынесено из удалённого `log-agent` (там эта запись ехала попутно с логами тул-активности).
В отличие от прежней версии пишет ТОЛЬКО факт чтения grounding-index — никакого пер-файлового
«лога чтений» (read_code/search_code/…): их потребитель (agentops) удалён, а гейту нужен лишь
`read_grounding`. Хук лёгкий, sync, НИКОГДА не падает с ненулевым кодом — фиксация evidence не
должна ронять прогон.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _project import active_feature, git_toplevel, phases_dir


def _agent_label(data: dict) -> str:
    at = data.get("agent_type")
    aid = str(data.get("agent_id") or "")[:8]
    if at:
        return f"{at}-{aid}" if aid else at
    return "main"


def _record_grounding_read(data: dict, root: str) -> None:
    """Пишет evidence, только если агент читает grounding-index."""
    if data.get("tool_name", "") not in {"Read", "ReadFile"}:
        return
    tool_input = data.get("tool_input") or {}
    file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if "grounding-index" not in file_path:
        return

    ev_path = phases_dir(Path(root), active_feature(Path(root))) / "agent-evidence.jsonl"
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "read_grounding",
        "agent": _agent_label(data),
        "agent_type": data.get("agent_type"),
        "path": file_path,
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
        _record_grounding_read(data, root)
    except Exception:
        return 0  # фиксация evidence не должна ронять прогон
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
