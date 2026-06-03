#!/usr/bin/env python3
"""gate-guard.py — PreToolUse permission gateway с risk-adaptive ladder R0–R5 (PDLC v3.5).

Заменяет фиксированную политику на risk-adaptive (см. risk_ladder.py + risk-policy.json).
Принцип **deny-first**: рисковое (R3+) действие блокируется, пока не выполнено требование
уровня (manifest-шаги / approval-маркер / evidence). На R3+ при внутренней ошибке/неясности —
тоже блок (fail-CLOSED). R0/R1 и любые читающие команды — проходят мгновенно (fail-open).

Матчеры: вешать на `^Bash$` и `(Write|Edit|WriteFile|NotebookEdit)`. Блок: exit 2 + stderr.
Separation of duties: если действие выше cap роли (agent_type) — deny.
"""
from __future__ import annotations

import json
import re
import sys

import risk_ladder as R


def _kind(tool_name: str, command: str) -> str:
    if tool_name in ("Bash", "run_shell_command"):
        if re.search(r"\bgit\s+commit\b", command):
            return "commit"
        if re.search(r"\bgit\s+push\b|pull[-_ ]?request|pullrequests|\bacli\b.*\bpr\b", command, re.I):
            return "push"
        if re.search(r"\bacli\b.*\bcreate\b|\bjira\b.*\bcreate\b|rest/api/\d+/issue\b", command, re.I):
            return "jira"
        return "other"
    return "write"


def _block(reason: str) -> int:
    print(f"[gate-guard] DENY: {reason}", file=sys.stderr)
    return 2


def main() -> int:
    level = "R0"
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        agent_type = data.get("agent_type")
        root = R.project_root(data.get("cwd", ""))

        info = R.classify(tool_name, tool_input, root)
        level = info["level"]
        command = info["command"]
        kind = _kind(tool_name, command)

        # R0/R1 — авто (читающие команды, docs, тесты). Не вмешиваемся.
        if R.level_order(level) <= R.level_order(R.load_policy().get("autonomy_auto_max", "R1")):
            return 0

        # вне пайплайна (нет manifest) — gateway не форсит пайплайн-требования, но
        # deny-first для R4+ всё равно держим (необратимое без контекста — опасно).
        if not R.manifest_exists(root) and R.level_order(level) < R.level_order("R4"):
            return 0

        # separation of duties: действие выше cap роли субагента → deny
        cap = R.agent_cap(agent_type)
        if cap and R.level_order(level) > R.level_order(cap):
            return _block(
                f"separation of duties: роль '{agent_type}' ограничена {cap}, "
                f"а действие классифицировано как {level} ({info['reason']})."
            )

        req = R.requirement(level)
        allowed, why = R.check_requirement(level, req, root, kind, agent_type)
        if not allowed:
            return _block(f"{why}. Действие={kind} target='{info['target']}' risk={level}.")
        return 0

    except Exception as e:
        # fail-CLOSED на рисковых, fail-open на низких
        if R.level_order(level) >= R.level_order("R3"):
            return _block(f"deny-first: ошибка оценки риска на {level} ({e}).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
