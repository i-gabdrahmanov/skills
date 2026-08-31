#!/usr/bin/env python3
"""fork-syntax-guard.py — PreToolUse `^Bash$`: инструктивный блок синтаксиса, который режет
нативный сейфти форка GigaCode (Qwen).

Проблема: рантайм молча отклоняет command substitution (`$(...)`, backticks) и filesystem-
enumeration (`find … -exec`, `ls -R`) с невнятным `Tool run_shell_command is denied` — слабая
модель не понимает причину и тратит итерации на ретраи того же самого. Этот хук перехватывает
паттерн РАНЬШЕ нативного deny и объясняет в stderr, чем заменить. Эргономика, не enforcement:
не входит в essential_hooks preflight.

Точность матчинга (tasks/011). Паттерн искался в СЫРОЙ строке команды, поэтому под блок
попадало то, что никакой подстановкой не является:
  • `cat > docs/x.md <<'EOF' … \\`update.py\\` … EOF` — backtick в ТЕЛЕ дока (а тела heredoc
    рантайм и не исполняет). Запись почти любого дока форжа блокировалась;
  • `grep -rn "find . -exec" docs/` — паттерн внутри аргумента поиска;
  • `awk '{print $(NF)}' f` — подстановка внутри ОДИНАРНЫХ кавычек, где shell её не делает.
Поэтому перед матчингом вырезаются тела heredoc и одинарные кавычки, а для правил про
исполняемые команды (`find -exec`, `ls -R`) — и двойные. Двойные кавычки для `$(...)`
сохраняются намеренно: там подстановка выполняется по-настоящему.

Выключатель: `harness.fork_syntax_guard: false` в ground/policy.json — для рантаймов, где
`$(...)` полностью легален и блок был бы чистым ложняком.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_RULES = [
    (re.compile(r"\$\("), "double",
     "command substitution `$(...)` режется рантаймом форка. Убери подстановку: путь к репо "
     "скрипты forge берут сами (repo_root()), текущий каталог передавай как `.` или явным путём."),
    (re.compile(r"`[^`]+`"), "double",
     "backticks (`...`) режутся рантаймом форка. Убери подстановку: передай значение явно "
     "или используй отдельный вызов и подставь результат вручную."),
    (re.compile(r"\bfind\b.*\s-exec\b"), "all",
     "`find … -exec` режет нативный сейфти форка. Для перечисления/чтения файлов используй "
     "тулы Glob/Grep/Read, а не shell-обход файловой системы."),
    (re.compile(r"\bls\s+(?:-[a-zA-Z]*R[a-zA-Z]*)\b"), "all",
     "`ls -R` (рекурсивный обход) режет нативный сейфти форка. Используй Glob для списка "
     "файлов по маске."),
]

# <<EOF / <<-'EOF' / <<"EOF" … до строки-терминатора. Тело heredoc — это ДАННЫЕ (текст дока,
# JSON, SQL), рантайм его не исполняет, и искать в нём shell-синтаксис бессмысленно.
_HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1.*?^\s*\2\s*$",
    re.S | re.M,
)
_SQ_RE = re.compile(r"'[^']*'")
_DQ_RE = re.compile(r'"[^"]*"')


def _strip(command: str, mode: str) -> str:
    """Убрать из команды то, что рантайм не исполняет.

    mode="double" — тела heredoc и одинарные кавычки (в двойных подстановка РАБОТАЕТ);
    mode="all"    — плюс двойные кавычки (правила про исполняемые команды)."""
    out = _HEREDOC_RE.sub(" ", command)
    out = _SQ_RE.sub("''", out)
    if mode == "all":
        out = _DQ_RE.sub('""', out)
    return out


def _enabled(cwd: str) -> bool:
    """Хук включён, если harness.fork_syntax_guard не выставлен в false.

    Резолв мягкий: любая проблема с конфигом — считаем включённым (прежнее поведение)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _config_loader import load_project_config, find_project_root
        root = find_project_root(Path(cwd or ".").resolve()) or Path(cwd or ".")
        cfg = load_project_config(root) or {}
        val = (cfg.get("harness") or {}).get("fork_syntax_guard")
        return val is not False
    except Exception:
        return True


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return 0
        if not _enabled(str(data.get("cwd") or "")):
            return 0
        for pat, mode, hint in _RULES:
            if pat.search(_strip(cmd, mode)):
                print(f"[fork-syntax-guard] DENY: {hint}", file=sys.stderr)
                return 2
    except Exception:
        return 0  # страховочный хук не должен ронять прогон
    return 0


if __name__ == "__main__":
    sys.exit(main())
