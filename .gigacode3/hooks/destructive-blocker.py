#!/usr/bin/env python3
"""destructive-blocker.py — PreToolUse `^Bash$`: блок деструктивных команд (PDLC v3.5, стр. 152).

deny-first: чёрный список из risk-policy.json (`destructive_blacklist`) — `rm -rf /`, force-push
в main, DROP/TRUNCATE, chmod 777, fork-bomb, `curl | sh` и т.п. Совпадение → exit 2.
Не зависит от пайплайна: эти команды опасны всегда. Никогда не пропускает при совпадении.
"""
from __future__ import annotations

import json
import re
import sys

import risk_ladder as R


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return 0
        for pat in R.load_policy().get("destructive_blacklist", []):
            if re.search(pat, cmd, re.I):
                print(f"[destructive-blocker] DENY: команда совпала с запретом /{pat}/. "
                      "Деструктивное действие заблокировано.", file=sys.stderr)
                return 2
    except Exception:
        return 0  # сам блокировщик не должен ронять прогон (но при совпадении — блок выше)
    return 0


if __name__ == "__main__":
    sys.exit(main())
