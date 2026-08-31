#!/usr/bin/env python3
"""grounding-evidence.py — фиксирует чтение grounding-excerpt в журнале прогона.

НЕ логгер. Единственная задача: когда агент читает `grounding-excerpt.json`, дописать одну
evidence-запись `kind:"grounding"` в журнал прогона фичи (events.jsonl). Её читает
`gate-guard`, чтобы снять блок фазы `01-grounding` (пока агент не сверился с grounding-excerpt,
чтение `src/` заблокировано).

Вынесено из удалённого `log-agent` (там эта запись ехала попутно с логами тул-активности).
В отличие от прежней версии пишет ТОЛЬКО факт чтения grounding-excerpt — никакого пер-файлового
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

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import git_toplevel  # noqa: E402
import forge_events as FE  # noqa: E402

try:
    import risk_ladder as R  # noqa: E402
except Exception:  # pragma: no cover — бандл повреждён
    R = None


def _agent_label(data: dict) -> str:
    at = data.get("agent_type")
    aid = str(data.get("agent_id") or "")[:8]
    if at:
        return f"{at}-{aid}" if aid else at
    return "main"


def _record_grounding_read(data: dict, root: str) -> None:
    """Пишет evidence, только если агент читает grounding-excerpt."""
    if data.get("tool_name", "") not in {"Read", "ReadFile"}:
        return
    tool_input = data.get("tool_input") or {}
    file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if "grounding-excerpt" not in file_path:
        return

    # Пишем в журнал прогона активной фичи. Раньше — в ground/phases/<feature>/
    # agent-evidence.jsonl, рядом с производным кэшем фазовой машины; кэш снят.
    # Фичу резолвим по самому свежему манифесту ПО ВСЕМ веткам (fix/lite/full), как
    # это делает file-journal: гейт 01-grounding есть и у них.
    mp = R.active_manifest(Path(root)) if R is not None else None
    if mp is None:
        return  # вне пайплайна — evidence не нужно
    FE.append_event(Path(root), mp.parent.parent.name, mp.parent.name, "grounding",
                    agent=_agent_label(data),
                    agent_type=data.get("agent_type"),
                    path=file_path)


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
