#!/usr/bin/env python3
"""context-injector.py — SubagentStart-хук: авто-инъекция контекста субагенту.

Рантайм подкладывает субагенту grounding-выжимку и конвенции, чтобы он проектировал/кодил по
актуальному срезу системы, не перечитывая код.

ВАЖНО (по исходникам Qwen): рантайм читает контекст ТОЛЬКО из `hookSpecificOutput.additionalContext`
(`getAdditionalContext()` в core/hooks/types.ts), а на SubagentStart кладёт его в контекст субагента
(`agent.ts`: contextState 'hook_context'). Поэтому печатаем именно `hookSpecificOutput.additionalContext`.

НЕ зависим от `agent_type`: в пайплайне все субагенты дёргаются как `subagent_type=general-purpose`,
поэтому матчинг по типу не работал бы. Инъектим то, что есть в проекте, всем субагентам (выжимка дёшева;
роль субагента и так задаётся его промптом от оркестратора).

Вывод: `{"hookSpecificOutput": {"additionalContext": "<текст>"}}` (пусто → нет вывода). Всегда exit 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PER_FILE_LIMIT = 6000  # символов на файл

# Файлы для инъекции (project-relative), в порядке важности. Берём те, что реально есть.
INJECT_FILES = [
    "docs/system-analysis/grounding-excerpt.json",  # компактный срез системы (главное для дизайна/кода)
    "ground/conventions.md",                         # раскладка слоёв проекта (приоритет над generic)
]


def _project_root(cwd: str) -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        )
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return Path(top)
    except Exception:
        pass
    return Path(cwd or os.getcwd())


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        root = _project_root(data.get("cwd", ""))

        chunks: list[str] = []
        for rel in INJECT_FILES:
            p = root / rel
            if not p.exists():
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(txt) > PER_FILE_LIMIT:
                txt = txt[:PER_FILE_LIMIT] + f"\n…(усечено, всего {len(txt)} символов)"
            chunks.append(f"### Контекст пайплайна: `{rel}`\n```\n{txt}\n```")

        if not chunks:
            return 0
        print(json.dumps({"hookSpecificOutput": {"additionalContext": "\n\n".join(chunks)}},
                         ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
