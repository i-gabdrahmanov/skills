#!/usr/bin/env python3
"""budget-meter.py — информационный учёт токен-бюджета (без блокировок).

Никакого circuit-breaker: хук НИКОГДА не блокирует и не предупреждает —
только считает расход и складывает в ОДИН файл <run-dir>/budget.json
(общий расход + разбивка по фазам, наглядно видно, какая фаза сколько потратила).
  • PostToolUse / SubagentStop / PostToolUseFailure — TALLY: прибавить расход
    в total и в текущую фазу (фаза из ground/.../gate.json), один файл под flock.
  • Stop — FINALIZE: проставить finalized_at в budget.json и отдать сводку
    по фазам в контекст (info).

Бюджет — чисто справочная величина (сравнение расхода с ориентиром в сводке),
никаких порогов и стопов. Расход берём из payload
(`usage.total_tokens`/`tokens`/`usage.{input,output}_tokens`), если рантайм их
даёт; иначе оценка: фиксированная стоимость за событие (FALLBACK_PER_EVENT).
Ориентир бюджета — из pipeline.json (`quality.token_budget`), дефолт DEFAULT_BUDGET.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from datetime import datetime

try:
    import fcntl  # POSIX
except ImportError:
    fcntl = None
    import msvcrt  # Windows

DEFAULT_BUDGET = 2_000_000          # токенов-ориентир на прогон, если не задано
FALLBACK_PER_EVENT = 1500           # оценка, если рантайм не отдаёт usage


def _project_root(cwd: str):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=cwd or None, capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return cwd or os.getcwd()


def _run_dir(root: str, data: dict) -> str:
    """Та же логика группировки, что в log-agent (свежий manifest → feature/iter, иначе _adhoc)."""
    base = os.path.join(root, "ground", "ai-logs")
    manifests = [m for m in glob.glob(os.path.join(root, "ground", "statements", "*", "*", "manifest.json"))
                 if os.sep + "archived" + os.sep not in m]
    newest, mt = None, -1.0
    for m in manifests:
        try:
            t = os.path.getmtime(m)
        except OSError:
            continue
        if t > mt:
            newest, mt = m, t
    if newest:
        try:
            man = json.load(open(newest, encoding="utf-8"))
        except Exception:
            man = {}
        ctx = man.get("context") if isinstance(man.get("context"), dict) else {}
        feature = (ctx or {}).get("feature") or man.get("skill") or "pipeline"
        it = (ctx or {}).get("iteration")
        if it is None:
            pid = str(man.get("pipeline_id", "run"))
            it = pid[-6:] if len(pid) > 6 else pid
        safe = lambda s: "".join(c if c.isalnum() or c in "._-" else "-" for c in str(s)) or "x"
        return os.path.join(base, safe(feature), "iter-" + safe(it))
    sess = "".join(c for c in str(data.get("session_id", "nosess")) if c.isalnum())[:8] or "nosess"
    return os.path.join(base, "_adhoc", f"{datetime.now().strftime('%Y%m%d-%H%M')}-{sess}")


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
        from _project import active_feature, gate_file
        gp = gate_file(_Path(root), active_feature(_Path(root)))
        return json.load(open(gp, encoding="utf-8")).get("current_phase", "") or ""
    except Exception:
        return ""


def _bpath(root: str, data: dict) -> str:
    """Единый файл бюджета прогона: <run-dir>/budget.json (total + разбивка по фазам)."""
    d = _run_dir(root, data)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "budget.json")


def _read_state(path: str) -> dict:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def _tally(path: str, tokens: int, budget: int, phase: str) -> dict:
    """Один файл, один flock: прибавить расход в total и в текущую фазу."""
    with open(path, "a+", encoding="utf-8") as f:
        try:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            f.seek(0)
            try:
                state = json.load(f)
            except Exception:
                state = {}
            state["budget"] = budget
            state["total_spent"] = int(state.get("total_spent", 0)) + tokens
            state["total_events"] = int(state.get("total_events", 0)) + 1
            phases = state.setdefault("phases", {})
            key = phase or "(вне фазы)"
            entry = phases.setdefault(key, {"spent": 0, "events": 0})
            entry["spent"] += tokens
            entry["events"] += 1
            state["last_phase"] = phase or state.get("last_phase", "")
            state["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            f.seek(0)
            f.truncate()
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            return state
        finally:
            try:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                else:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass


def _finalize(root: str, data: dict):
    """На Stop: проставить finalized_at в budget.json и отдать сводку в контекст."""
    path = _bpath(root, data)
    state = _read_state(path)
    if not state:
        state = {"budget": _budget(root), "total_spent": 0, "total_events": 0, "phases": {}}
    state["finalized_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    state["per_event_fallback"] = FALLBACK_PER_EVENT
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    phases = state.get("phases", {})
    print(json.dumps({
        "hookSpecificOutput": {
            "additionalContext": (
                f"📊 Бюджет прогона (справочно) → {path}. "
                f"Всего: {state.get('total_spent', 0)} токенов "
                f"({state.get('total_events', 0)} событий). "
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
        root = _project_root(data.get("cwd", ""))

        if ev in ("PostToolUse", "SubagentStop", "PostToolUseFailure"):
            _tally(_bpath(root, data), _tokens(data), _budget(root), _current_phase(root))
            return 0

        if ev == "Stop":
            _finalize(root, data)
            return 0

        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
