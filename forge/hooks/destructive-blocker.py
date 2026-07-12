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

# Встроенный fail-closed CORE: проверяется ВСЕГДА (объединяется с risk-policy.json).
# Гарантирует, что при отсутствии/повреждении политики блокировщик не открывается полностью,
# и закрывает обходы (long-form флаги, rm /*, find -delete), мимо которых проходил policy-regex.
_CORE_BLACKLIST = [
    r"\brm\b(?:\s+(?:-\S+|--\w[\w-]*))*\s+(?:(?:/|~|\$HOME|\*)(?:\s|/|\*|$)|\.(?:\s|$))",  # rm <любые флаги> опасная цель (/, /*, ~, $HOME, *, бар. .) — но НЕ ./subdir
    r"\brm\b(?=.*\b(?:-[a-z]*r[a-z]*|--recursive)\b)(?=.*\b(?:-[a-z]*f[a-z]*|--force)\b).*(?:/|~|\$HOME|\*)",
    r"\bfind\s+(?:/|~|\$HOME)\S*\s.*-(?:delete|exec\s+rm)\b",  # find в опасном корне + удаление
    # force-push и в короткой форме `-f` (кластер флагов), кроме --force-with-lease
    r"\bgit\s+push\b(?=.*(?:--force\b|\s-[A-Za-z]*f))(?!.*--force-with-lease)",
    r"\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b",
    r"\bmkfs\b|\bdd\s+if=.*of=/dev/",
    r":\(\)\s*\{.*\};:",
    r"(?:curl|wget)\s+[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh",
    # обфусцированный exec: base64 -d | (ba)sh
    r"\bbase64\s+(?:-d|--decode|-D)\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b",
    # xargs, скармливающий rm (цель подставляет xargs — прямой rm-паттерн её не видит)
    r"\bxargs\b(?:\s+-\S+)*\s+rm\b",
    # python-деструктив без токена rm: shutil.rmtree корня/дома
    r"\brmtree\s*\(\s*['\"]?(?:/|~|\$HOME)",
]


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return 0
        # `git -C <p> push --force`/`git -c k=v push -f` обходили force-push-паттерны
        # (детект по `git\s+push`). Матчим по нормализованной команде (исполняется исходная).
        try:
            cmd = R.normalize_git_command(cmd)
        except Exception:
            pass
        policy = []
        try:
            policy = R.load_policy().get("destructive_blacklist", []) or []
        except Exception:
            policy = []
        for pat in list(policy) + _CORE_BLACKLIST:
            if re.search(pat, cmd, re.I):
                print(f"[destructive-blocker] DENY: команда совпала с запретом /{pat}/. "
                      "Деструктивное действие заблокировано.", file=sys.stderr)
                return 2
    except Exception:
        return 0  # сам блокировщик не должен ронять прогон (но при совпадении — блок выше)
    return 0


if __name__ == "__main__":
    sys.exit(main())
