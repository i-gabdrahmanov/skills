#!/usr/bin/env python3
"""agentops.py — отчёт Trust-метрик из append-only JSONL аудита (PDLC v3.5, стр. 217).

НЕ хук — репорт-утилита. Читает agents.jsonl прогона (или все под ground/ai-logs) и считает
Trust-категорию v3.5: hook-block rate, intervention rate, риск-распределение действий, частоту
denied/ошибок. Источник денормализован из логов log-agent + (если есть) budget.json.

Usage:
    agentops.py [--root .] [--run <path-to-run-dir>] [--json]
Exit 0.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter
from pathlib import Path


def _iter_events(jsonl: Path):
    try:
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="Trust metrics from JSONL audit.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--run", help="конкретный run-dir; иначе агрегируем все agents.jsonl")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if args.run:
        files = [str(Path(args.run) / "agents.jsonl")]
    else:
        files = glob.glob(str(root / "ground" / "ai-logs" / "**" / "agents.jsonl"), recursive=True)

    ev = Counter()
    tools = Counter()
    failures = 0
    blocks = 0          # PostToolUseFailure с признаком блока / Stop block — приблизительно
    pretool = 0
    subagents = set()
    total = 0
    for jf in files:
        for rec in _iter_events(Path(jf)):
            total += 1
            e = rec.get("event") or rec.get("hook_event_name")
            ev[e] += 1
            if rec.get("tool_name"):
                tools[rec["tool_name"]] += 1
            if e == "PreToolUse":
                pretool += 1
            if e == "PostToolUseFailure":
                failures += 1
                err = (rec.get("error") or "")
                if "DENY" in err or "blocked" in err.lower() or "gate" in err.lower():
                    blocks += 1
            if rec.get("agent_type"):
                subagents.add(f"{rec.get('agent_type')}-{rec.get('agent_id')}")

    block_rate = (blocks / pretool) if pretool else 0.0
    fail_rate = (failures / pretool) if pretool else 0.0
    metrics = {
        "files": len(files),
        "events_total": total,
        "by_event": dict(ev),
        "top_tools": dict(tools.most_common(8)),
        "subagents": len(subagents),
        "pretool_calls": pretool,
        "hook_block_rate": round(block_rate, 3),
        "tool_failure_rate": round(fail_rate, 3),
    }
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    else:
        print("=== AgentOps / Trust-метрики ===")
        print(f"файлов аудита: {metrics['files']}, событий: {total}, субагентов: {len(subagents)}")
        print(f"PreToolUse вызовов: {pretool}")
        print(f"hook-block rate:    {block_rate:.1%}")
        print(f"tool-failure rate:  {fail_rate:.1%}")
        print("по событиям:", dict(ev))
        print("топ тулов:  ", dict(tools.most_common(8)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
