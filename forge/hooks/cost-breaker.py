#!/usr/bin/env python3
"""cost-breaker.py — token/cost circuit breaker (PDLC v3.5, стр. 156/194).

Один скрипт на три события (по hook_event_name):
  • PostToolUse / SubagentStop — TALLY: прибавить расход в budget.json (sync, лёгкий).
    Дополнительно аккумулирует расход по фазам проекта (читает ground/phases/gate.json
    для определения current_phase).
  • PreToolUse                 — ENFORCE: выводит warn при ≥80% (без блокировки).
  • Stop                       — ENFORCE: пишет budget-final.json с постейджной
    статистикой и общим расходом. Не блокирует.

Бюджет безлимитный: блокировка (exit 2 / decision:block) отключена.
Расход берём из payload (`usage.total_tokens`/`tokens`/`usage.{input,output}_tokens`),
если рантайм их даёт; иначе оценка: фиксированная стоимость за событие (FALLBACK_PER_EVENT).
Бюджет — из pipeline.json (`quality.token_budget`), дефолт DEFAULT_BUDGET.
Состояние — <run-dir>/budget.json рядом с логом (та же группировка, что в log-agent).
Итоговая статистика — <run-dir>/budget-final.json на Stop.
"""
from __future__ import annotations

import fcntl
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

DEFAULT_BUDGET = 2_000_000          # токенов на прогон, если не задано
FALLBACK_PER_EVENT = 1500           # оценка, если рантайм не отдаёт usage
WARN_RATIO = 0.80                   # только info, без блокировки


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
    d = _run_dir(root, data)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "budget.json")


def _phase_state_path(root: str, data: dict) -> str:
    """Файл аккумуляции расхода по фазам: <run-dir>/budget-phases.json."""
    d = _run_dir(root, data)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "budget-phases.json")


def _read_state(path: str) -> dict:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def _add(path: str, tokens: int, budget: int) -> dict:
    with open(path, "a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            try:
                state = json.load(f)
            except Exception:
                state = {"spent": 0, "events": 0}
            state["spent"] = int(state.get("spent", 0)) + tokens
            state["events"] = int(state.get("events", 0)) + 1
            state["budget"] = budget
            f.seek(0)
            f.truncate()
            json.dump(state, f, ensure_ascii=False)
            f.flush()
            return state
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _add_phase_tally(root: str, data: dict, tokens: int):
    """Аккумулировать расход по текущей фазе в budget-phases.json."""
    phase = _current_phase(root)
    if not phase:
        return
    ppath = _phase_state_path(root, data)
    with open(ppath, "a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            try:
                state = json.load(f)
            except Exception:
                state = {"phases": {}, "total": 0, "events": 0}
            phases = state.setdefault("phases", {})
            phase_entry = phases.setdefault(phase, {"spent": 0, "events": 0})
            phase_entry["spent"] += tokens
            phase_entry["events"] += 1
            state["total"] = state.get("total", 0) + tokens
            state["events"] = state.get("events", 0) + 1
            state["last_phase"] = phase
            f.seek(0)
            f.truncate()
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _write_final(root: str, data: dict):
    """На Stop: записать budget-final.json с постейджной статистикой и общим расходом."""
    rund = _run_dir(root, data)
    os.makedirs(rund, exist_ok=True)

    # Общий расход
    budget_path = os.path.join(rund, "budget.json")
    total_state = _read_state(budget_path)

    # Постейджный расход
    phase_path = os.path.join(rund, "budget-phases.json")
    phase_state = _read_state(phase_path)

    final = {
        "type": "budget-final",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "budget": total_state.get("budget", _budget(root)),
        "total_spent": total_state.get("spent", 0),
        "total_events": total_state.get("events", 0),
        "phases": phase_state.get("phases", {}),
        "per_event_fallback_used": FALLBACK_PER_EVENT,
    }

    final_path = os.path.join(rund, "budget-final.json")
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, ensure_ascii=False)

    print(json.dumps({
        "hookSpecificOutput": {
            "additionalContext": (
                f"📊 Бюджет: итоговый файл → {final_path}. "
                f"Всего потрачено: {final['total_spent']} токенов "
                f"({final['total_events']} событий). "
                f"Расход по фазам:\n" + "\n".join(
                    f"  • {ph}: {p['spent']} токенов ({p['events']} событий)"
                    for ph, p in sorted(final["phases"].items())
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
        budget = _budget(root)
        path = _bpath(root, data)

        if ev in ("PostToolUse", "SubagentStop", "PostToolUseFailure"):
            tok = _tokens(data)
            _add(path, tok, budget)
            _add_phase_tally(root, data, tok)
            return 0

        # ENFORCE — только info, без блокировки
        spent = int(_read_state(path).get("spent", 0))
        ratio = spent / budget if budget else 0.0

        if ev == "Stop":
            _write_final(root, data)
            return 0

        if ev in ("PreToolUse", "UserPromptSubmit"):
            if ratio >= WARN_RATIO:
                print(json.dumps({"hookSpecificOutput": {"additionalContext":
                    f"ℹ️ cost-breaker: израсходовано {ratio:.0%} токен-бюджета "
                    f"({spent}/{budget}). Без блокировки (бюджет безлимитный)."}},
                    ensure_ascii=False))
            return 0
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
