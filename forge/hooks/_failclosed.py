#!/usr/bin/env python3
"""_failclosed.py — общая развязка «бандл forge не грузится» для блокирующих хуков.

Хуки форжа на неудачном импорте форж-модулей отдают exit 2 (DENY) — это сознательный
fail-CLOSED: exit 1 рантайм читает как «хук не возражает» и ВЫПОЛНЯЕТ вызов, так уже молча
выключался весь enforcement. Но у сплошного deny было следствие, которого никто не хотел:
матчер стоит на ^(run_shell_command|Bash)$, значит заблокированы ВСЕ shell-вызовы, включая
ту самую команду восстановления, которую печатает сам баннер:

    [destructive-blocker] DENY: бандл forge не грузится (No module named 'risk_ladder').
      ... перезапусти: bash .gigacode/deploy-local.sh      ← и она же не проходит

Починить бандл из сессии было нельзя — только руками в терминале. Здесь узкая форточка:
команды ВОССТАНОВЛЕНИЯ (deploy-local.sh / deploy.sh / preflight.py / doctor.py / проверка
версии интерпретатора) пропускаются с предупреждением, всё остальное по-прежнему exit 2.

Маржинальный риск нулевой: при сломанном бандле enforcement и так не работает — вопрос лишь
в том, может ли агент его починить. Форточка узкая по построению: команда должна СОСТОЯТЬ из
одного восстановительного вызова — любой разделитель (`;`, `&&`, `|`, `&`, подстановка) её
закрывает, чтобы `bash deploy-local.sh; rm -rf ~` не проехал под видом починки.

Модуль намеренно stdlib-only и без синтаксиса новее 3.9: он грузится ровно тогда, когда
остальной бандл уже не грузится. Если не загрузится и он — вызывающий хук остаётся при
прежнем поведении (exit 2), регрессии нет.
"""
from __future__ import annotations

import json
import re
import sys

# Разделители/подстановки: их наличие снимает статус «одна восстановительная команда».
_COMPOUND_RE = re.compile(r"[;&|\n]|\$\(|`|\|\|")

# Что считаем восстановлением. Только то, что чинит или ДИАГНОСТИРУЕТ бандл.
_RECOVERY_RE = re.compile(
    r"(?:^|[\s/\\])(?:deploy-local\.sh|deploy\.sh|preflight\.py|doctor\.py)\b"
    r"|(?:^|\s)(?:python3?|py)(?:\s+-[A-Za-z]+)*\s+(?:-V|--version)\s*$"
    r"|(?:^|\s)(?:python3?|py)\s+-c\s+['\"]?import\s+(?:sys|risk_ladder|_project)\b"
)

_RESTART_HINT = (
    "Проверь интерпретатор в .gigacode/settings.json (нужен python 3.9+ с рабочим expat), "
    "затем перезапусти: bash .gigacode/deploy-local.sh"
)


def _command_from_stdin() -> str:
    """Команда из payload'а, если это Bash-вызов. Пусто — не Bash либо payload не читается."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return ""
    if not raw or not raw.strip():
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("tool_name") not in ("Bash", "run_shell_command"):
        return ""
    cmd = (data.get("tool_input") or {}).get("command")
    return cmd if isinstance(cmd, str) else ""


def is_recovery_command(command: str) -> bool:
    """Одна-единственная команда восстановления бандла (без цепочек и подстановок)."""
    if not command or not command.strip():
        return False
    if _COMPOUND_RE.search(command):
        return False
    return bool(_RECOVERY_RE.search(command))


def bundle_denied(hook: str, err: object) -> int:
    """Код возврата для except-блока импорта: 0 для команд восстановления, иначе 2.

    Печатает причину в stderr в обоих случаях — тишина при выключенном enforcement'е
    недопустима (это и был исходный дефект, ради которого fail-CLOSED вводился)."""
    if is_recovery_command(_command_from_stdin()):
        print(f"[{hook}] WARN: бандл forge не грузится ({err}) — enforcement ВЫКЛЮЧЕН. "
              f"Пропускаю команду восстановления. После починки повтори действие.",
              file=sys.stderr)
        return 0
    print(f"[{hook}] DENY: бандл forge не грузится ({err}). {_RESTART_HINT}", file=sys.stderr)
    return 2
