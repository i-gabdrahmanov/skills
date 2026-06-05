#!/usr/bin/env python3
"""cost-breaker.py — token/cost circuit breaker (PDLC v3.5, стр. 156/194).

Один скрипт на три события (по hook_event_name):
  • PostToolUse / SubagentStop — TALLY: прибавить расход в budget.json (sync, лёгкий).
  • PreToolUse                 — ENFORCE: ≥120% → exit 2 (stop); ≥80% → варн в additionalContext.
  • Stop                       — ENFORCE: ≥120% → decision:block с reason.

Расход берём из payload (`usage.total_tokens`/`tokens`/`usage.{input,output}_tokens`), если рантайм
их даёт; иначе оценка: фиксированная стоимость за событие (FALLBACK_PER_EVENT). Бюджет — из
pipeline.json (`quality.token_budget`), дефолт DEFAULT_BUDGET. Состояние — <run-dir>/budget.json
рядом с логом (та же группировка, что в log-agent: ground/ai-logs/<feature>/iter-NN | _adhoc/...).

Никогда не роняет прогон, кроме намеренного exit 2 / decision:block.
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
WARN_RATIO = 0.80
STOP_RATIO = 1.20


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


def _bpath(root: str, data: dict) -> str:
    d = _run_dir(root, data)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "budget.json")


def _read(path: str) -> dict:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"spent": 0, "events": 0}


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
            f.seek(0); f.truncate()
            json.dump(state, f, ensure_ascii=False)
            f.flush()
            return state
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


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
            _add(path, _tokens(data), budget)
            return 0

        # ENFORCE
        spent = int(_read(path).get("spent", 0))
        ratio = spent / budget if budget else 0.0

        if ev == "Stop":
            if ratio >= STOP_RATIO:
                print(json.dumps({"decision": "block",
                    "reason": f"Token budget исчерпан: {spent}/{budget} ({ratio:.0%}). "
                              "Заверши задачу/сократи объём, не начинай новые шаги."},
                    ensure_ascii=False))
            return 0

        if ev in ("PreToolUse", "UserPromptSubmit"):
            if ratio >= STOP_RATIO:
                print(f"[cost-breaker] STOP: бюджет {spent}/{budget} ({ratio:.0%}) превышен.",
                      file=sys.stderr)
                return 2
            if ratio >= WARN_RATIO:
                print(json.dumps({"hookSpecificOutput": {"additionalContext":
                    f"⚠️ cost-breaker: израсходовано {ratio:.0%} токен-бюджета "
                    f"({spent}/{budget}). Будь экономнее, избегай лишних шагов."}},
                    ensure_ascii=False))
            return 0
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
