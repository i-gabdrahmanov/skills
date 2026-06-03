#!/usr/bin/env python3
"""context-injector.py — SubagentStart-хук: авто-инъекция контекста субагенту.

Вместо ручной передачи контекста из промпта оркестратора — рантайм сам подкладывает
нужные артефакты субагенту по его типу через additionalContext.

Таблица INJECT: agent_type-regex → список project-relative файлов. Инъектим только те,
что РЕАЛЬНО существуют в проекте (иначе ничего — не шумим). Контент усекаем, чтобы не
раздувать контекст субагента.

Вывод: JSON в stdout `{"additionalContext": "<текст>"}` (пусто/нет вывода → нет инъекции).
Всегда exit 0.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PER_FILE_LIMIT = 6000  # символов на файл

# agent_type (regex, case-insensitive) → файлы для инъекции (project-relative).
INJECT: list[tuple[str, list[str]]] = [
    # тех-дизайн и разработка опираются на компактную выжимку системы
    (r"tech.?design|design", ["docs/system-analysis/grounding-excerpt.json"]),
    (r"java.?spring|build|dev|developer", [
        "docs/system-analysis/grounding-excerpt.json",
        "ground/conventions.md",
    ]),
    (r"jira", ["ground/pipeline.json"]),
    (r"spec|uml|document", ["docs/system-analysis/grounding-excerpt.json"]),
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


def _files_for(agent_type: str) -> list[str]:
    files: list[str] = []
    for pattern, paths in INJECT:
        if re.search(pattern, agent_type, re.I):
            files += paths
    # уникализируем, сохраняя порядок
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        agent_type = str(data.get("agent_type") or "")
        if not agent_type:
            return 0
        root = _project_root(data.get("cwd", ""))

        chunks: list[str] = []
        for rel in _files_for(agent_type):
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
        print(json.dumps({"additionalContext": "\n\n".join(chunks)}, ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
