#!/usr/bin/env python3
"""fork-syntax-guard.py — PreToolUse `^Bash$`: инструктивный блок синтаксиса, который режет
нативный сейфти форка GigaCode (Qwen).

Проблема: рантайм молча отклоняет command substitution (`$(...)`, backticks) и filesystem-
enumeration (`find … -exec`, `ls -R`) с невнятным `Tool run_shell_command is denied` — слабая
модель не понимает причину и тратит итерации на ретраи того же самого. Этот хук перехватывает
паттерн РАНЬШЕ нативного deny и объясняет в stderr, чем заменить. Эргономика, не enforcement:
не входит в essential_hooks preflight.
"""
from __future__ import annotations

import json
import re
import sys

_RULES = [
    (re.compile(r"\$\("),
     "command substitution `$(...)` режется рантаймом форка. Убери подстановку: путь к репо "
     "скрипты forge берут сами (repo_root()), текущий каталог передавай как `.` или явным путём."),
    (re.compile(r"`[^`]+`"),
     "backticks (`...`) режутся рантаймом форка. Убери подстановку: передай значение явно "
     "или используй отдельный вызов и подставь результат вручную."),
    (re.compile(r"\bfind\b.*\s-exec\b"),
     "`find … -exec` режет нативный сейфти форка. Для перечисления/чтения файлов используй "
     "тулы Glob/Grep/Read, а не shell-обход файловой системы."),
    (re.compile(r"\bls\s+(?:-[a-zA-Z]*R[a-zA-Z]*)\b"),
     "`ls -R` (рекурсивный обход) режет нативный сейфти форка. Используй Glob для списка "
     "файлов по маске."),
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
        for pat, hint in _RULES:
            if pat.search(cmd):
                print(f"[fork-syntax-guard] DENY: {hint}", file=sys.stderr)
                return 2
    except Exception:
        return 0  # страховочный хук не должен ронять прогон
    return 0


if __name__ == "__main__":
    sys.exit(main())
