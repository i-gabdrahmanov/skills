#!/usr/bin/env python3
"""budget-meter.py — информационный учёт токен-бюджета (без блокировок).

Никакого circuit-breaker: хук НИКОГДА не блокирует и не предупреждает — только считает
расход. Раньше держал ОТДЕЛЬНЫЙ stateful `budget.json` в СВОЁМ каталоге (по старой схеме
`<feature>/iter-N/` или `_adhoc/<ts>-<sess>/`), из-за чего budget.json улетал не туда, где
log-agent писал agents.log/.jsonl → «помойка из трёх файлов по разным папкам».

Теперь расход сворачивается в ЕДИНЫЙ лог прогона:
  • PostToolUse / SubagentStop / PostToolUseFailure — TALLY: дописать одно событие
    {"event":"budget", "phase":…, "tokens":N} в <run-dir>/agents.jsonl (+ человекочитаемую
    строку в <run-dir>/agents.log). Тот же каталог и тот же flock-append, что у log-agent
    (общий _project.run_dir / _project.append_locked) — «один прогон = одна папка / один лог».
  • Stop — FINALIZE: просканировать agents.jsonl, просуммировать budget-события (всего +
    разбивка по фазам), дописать итоговое {"event":"budget_summary", …} в тот же лог и отдать
    сводку по фазам в контекст (info).

Отдельного budget.json больше нет. Бюджет — чисто справочная величина (сравнение расхода с
ориентиром в сводке), никаких порогов и стопов. Расход берём из payload
(`usage.total_tokens`/`tokens`/`usage.{input,output}_tokens`), если рантайм их даёт; иначе
оценка: фиксированная стоимость за событие (FALLBACK_PER_EVENT). Ориентир бюджета — из
pipeline.json (`quality.token_budget`), дефолт DEFAULT_BUDGET.

Никогда не падает с ненулевым кодом — учёт не должен ронять прогон.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# Единый каталог прогона, git-корень и flock-append — из _project (тот же источник, что у
# log-agent), чтобы budget-события легли в ТОТ ЖЕ agents.jsonl, а не в отдельный файл/каталог.
from _project import active_feature, append_locked, gate_file, git_toplevel, run_dir

DEFAULT_BUDGET = 2_000_000          # токенов-ориентир на прогон, если не задано
FALLBACK_PER_EVENT = 1500           # оценка, если рантайм не отдаёт usage


def _budget(root: str) -> int:
    try:
        cfg = json.load(open(os.path.join(root, "ground", "pipeline.json"), encoding="utf-8"))
        b = (cfg.get("quality") or {}).get("token_budget")
        if isinstance(b, (int, float)) and b > 0:
            return int(b)
    except Exception:
        pass
    return DEFAULT_BUDGET


def _tokens(data: dict) -> int:
    u = data.get("usage")
    if isinstance(u, dict):
        if isinstance(u.get("total_tokens"), (int, float)):
            return int(u["total_tokens"])
        s = sum(int(u.get(k, 0)) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"))
        if s:
            return s
    if isinstance(data.get("tokens"), (int, float)):
        return int(data["tokens"])
    return FALLBACK_PER_EVENT


def _current_phase(root: str) -> str:
    """Прочитать gate.json активной фичи и вернуть current_phase, или пустую строку."""
    try:
        from pathlib import Path as _Path
        gp = gate_file(_Path(root), active_feature(_Path(root)))
        return json.load(open(gp, encoding="utf-8")).get("current_phase", "") or ""
    except Exception:
        return ""


def _agent_label(data: dict) -> str:
    at = data.get("agent_type")
    if not at:
        return "main"
    aid = "".join(c for c in str(data.get("agent_id", "")) if c.isalnum())[:8]
    safe_at = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(at)) or "x"
    return safe_at + (f"-{aid}" if aid else "")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(root: str, data: dict, tokens: int, phase: str) -> None:
    """TALLY: дописать одно budget-событие в agents.jsonl + строку в agents.log прогона."""
    run = run_dir(root, data.get("session_id", ""))
    label = _agent_label(data)
    rec = {
        "ts": _iso(),
        "event": "budget",
        "session_id": data.get("session_id"),
        "agent": label,
        "agent_type": data.get("agent_type"),
        "phase": phase or "",
        "tokens": int(tokens),
    }
    append_locked(os.path.join(run, "agents.jsonl"), json.dumps(rec, ensure_ascii=False) + "\n")
    human = "  ".join([
        datetime.now().strftime("%H:%M:%S"),
        f"[{label}]",
        "budget",
        f"phase={phase or '(вне фазы)'}",
        f"+{int(tokens)} tok",
    ]) + "\n"
    append_locked(os.path.join(run, "agents.log"), human)


def _scan_budget(jsonl_path: str) -> tuple[int, int, dict]:
    """Просуммировать все budget-события лога: (total_tokens, events, phases{ph:{spent,events}})."""
    total, events, phases = 0, 0, {}
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("event") != "budget":
                    continue
                t = int(rec.get("tokens", 0) or 0)
                total += t
                events += 1
                key = rec.get("phase") or "(вне фазы)"
                entry = phases.setdefault(key, {"spent": 0, "events": 0})
                entry["spent"] += t
                entry["events"] += 1
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return total, events, phases


def _finalize(root: str, data: dict) -> None:
    """На Stop: просуммировать budget-события лога, дописать budget_summary и отдать сводку."""
    run = run_dir(root, data.get("session_id", ""))
    jsonl = os.path.join(run, "agents.jsonl")
    total, events, phases = _scan_budget(jsonl)
    budget = _budget(root)

    summary = {
        "ts": _iso(),
        "event": "budget_summary",
        "session_id": data.get("session_id"),
        "total_tokens": total,
        "events": events,
        "budget": budget,
        "phases": phases,
        "per_event_fallback": FALLBACK_PER_EVENT,
        "finalized_at": _iso(),
    }
    append_locked(jsonl, json.dumps(summary, ensure_ascii=False) + "\n")

    print(json.dumps({
        "hookSpecificOutput": {
            "additionalContext": (
                f"📊 Бюджет прогона (справочно) → {jsonl}. "
                f"Всего: {total} токенов ({events} событий). "
                f"Ориентир: {budget}. "
                f"По фазам:\n" + "\n".join(
                    f"  • {ph}: {p['spent']} токенов ({p['events']} событий)"
                    for ph, p in sorted(phases.items())
                )
            )
        }
    }, ensure_ascii=False))


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        ev = data.get("hook_event_name", "")
        root = git_toplevel(data.get("cwd", ""))

        if ev in ("PostToolUse", "SubagentStop", "PostToolUseFailure"):
            _emit(root, data, _tokens(data), _current_phase(root))
            return 0

        if ev == "Stop":
            _finalize(root, data)
            return 0

        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
