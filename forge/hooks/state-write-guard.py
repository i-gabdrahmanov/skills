#!/usr/bin/env python3
"""state-write-guard.py — PreToolUse: запрет ПРЯМОЙ записи моделью в control-plane-файлы forge.

Закрывает BLOCKER-1 аудита. Вся пирамида целостности пайплайна — approval-маркеры, manifest,
overrides, gates, _origins, pipeline.json — это обычные JSON внутри `ground/`, а `ground/`
разрешён к записи (pii-boundary/gate-guard его whitelist-ят, классификатор рисков даёт .json в
ground/ уровень R1→auto). Провенанс-проверки в pipeline-state/update.py
(`_check_subagent_origin`/`_check_gate_result`/`_check_judges`, счётчики reopen/failure)
срабатывают ТОЛЬКО если мутация идёт ЧЕРЕЗ update.py. Прямой `Write .../manifest.json` со всеми
`status:"completed"` — или `Write ground/approvals/human-approval.json` — обходит всё это.

Инвариант, который форсит этот хук: **state-файлы меняются только санкционированными скриптами**
(update.py / record_gate.py / override_judge.py / config.py / record_approval.py) и хуком
state-recorder — они пишут через Bash→python→open(), т.е. НЕ инструментом Write/Edit, поэтому
под блок не попадают. Любая прямая запись инструментом (Write/Edit) или shell-редиректом
(`>`/`tee`/`dd of=`/`sed -i`/`cp`/`mv`/`python -c open()`) в эти пути — deny (exit 2).

Матчеры: `^(run_shell_command|Bash)$` и `^(write_file|edit|notebook_edit|...)$` — оба (Bash-вектор
редиректа + Write-вектор). Bash-детект по природе best-effort (в shell тысяча способов записать
файл); ловит частые векторы. Провенанс на approval-маркерах дополнительно форсит gate-guard.

fail-open на не-JSON stdin / отсутствии цели (нечего блокировать). Хук не должен ронять прогон,
но при совпадении control-plane-цели — блок.
"""
from __future__ import annotations

import json
import re
import sys

WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")
BASH_TOOLS = ("Bash", "run_shell_command")

# Пути control-plane. Lookbehind `(?<![\w-])` ловит путь и как bare file_path (Write), и внутри
# shell-команды (после пробела/кавычки/`/`), но не в составе большего слова (myground/…).
_CP_PATTERNS = [
    r"(?<![\w-])ground/pipeline\.json\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/manifest\.json\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/(?:_origins|gates|overrides)(?:/|\b)",
    r"(?<![\w-])ground/approvals(?:/|\b)",
]
_CP_RE = re.compile("|".join(_CP_PATTERNS))

# Токены записи в shell-команде (редирект/копирование/inline-python).
_WRITE_TOKEN_RE = re.compile(
    r">>?|<>|\btee\b|\bdd\b[^|]*\bof=|\bsed\b[^|]*-i|\bcp\b|\bmv\b|\binstall\b"
    r"|\bopen\s*\([^)]*['\"][^'\"]+['\"]\s*,\s*['\"][aw]|\.write(?:_text)?\s*\(|\btruncate\b"
)


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/")


def _hint(target: str) -> str:
    return (
        f"[state-write-guard] DENY: прямая запись в control-plane-файл '{target}' запрещена. "
        f"State меняется ТОЛЬКО санкционированными скриптами (провенанс форсится update.py):\n"
        f"  • шаги/manifest → pipeline-state/scripts/update.py (--feature ...)\n"
        f"  • gate-result → pipeline-state/scripts/record_gate.py\n"
        f"  • снятие судьи → pipeline-state/scripts/override_judge.py\n"
        f"  • параметры pipeline.json → config-helper/scripts/config.py set\n"
        f"  • approval-маркер → pipeline-state/scripts/record_approval.py (ТОЛЬКО после явного "
        f"«да» пользователя; сначала спроси через ask_user_question).\n"
        f"Прямой Write/echo>/tee/python -c open() сюда — обход провенанса, не делай так."
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        tn = data.get("tool_name", "")
        ti = data.get("tool_input") or {}

        if tn in WRITE_TOOLS:
            target = _norm(str(ti.get("file_path") or ti.get("path") or ti.get("filename") or ""))
            if target and _CP_RE.search(target):
                print(_hint(target), file=sys.stderr)
                return 2
            return 0

        if tn in BASH_TOOLS:
            cmd = _norm(str(ti.get("command") or ""))
            if not cmd:
                return 0
            # блок только когда есть И токен записи, И упоминание control-plane-пути в команде
            if _CP_RE.search(cmd) and _WRITE_TOKEN_RE.search(cmd):
                m = _CP_RE.search(cmd)
                print(_hint(m.group(0) if m else "ground/*"), file=sys.stderr)
                return 2
            return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
