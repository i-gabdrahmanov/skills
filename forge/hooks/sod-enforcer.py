#!/usr/bin/env python3
"""sod-enforcer.py — Separation of Duties: PreToolUse хук.

Блокирует действия, не соответствующие роли субагента, на основе
risk-policy.json agent_caps. Если роль = test, а цель — src/main/ — BLOCK.
Если роль = design, а команда — git push — BLOCK.

В отличие от subagent-enforcer (который проверяет, что фаза выполняется
через субагента), sod-enforcer проверяет, что субагент НЕ выходит за
границы своей роли.

Эвристика роли извлекается из поля role в tool_input, а если его нет —
из prompt субагента (контекст SubagentStart).

Маппинг: (Write|Edit|Bash) на пути src/main/ + src/test/ + git.
Блок: exit 2 + stderr. fail-open: если риск-политика не загружена — пропускает.

PDLC v3.5, risk-policy.json §agent_caps.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Роли и их разрешённые действия
# role → { allowed_path_prefixes, blocked_commands, blocked_path_patterns }
ROLE_POLICY = {
    "test": {
        "allowed_paths": ["src/test/"],
        "blocked_paths": ["src/main/"],
        "blocked_commands": [r"\bgit\s+push\b", r"\bgit\s+commit\b"],
        "blocked_content_patterns": [
            r"(?i)throw\s+new\s+(UnsupportedOperationException|RuntimeException)\s*\(\s*\"(not\s+implemented|stub)",
        ],
    },
    "spec": {
        "allowed_paths": ["docs/", "ground/"],
        "blocked_paths": ["src/"],
        "blocked_commands": [
            r"\bgit\s+push\b",
            r"\bgit\s+commit\b",
            r"\./gradlew\s+",
        ],
        "blocked_content_patterns": [],
    },
    "design": {
        "allowed_paths": [],  # все пути
        "blocked_paths": [],  # нет ограничений по путям
        "blocked_commands": [
            r"\bgit\s+push\b",
            r"\bgit\s+commit\b",
            r"\./gradlew\s+",
            r"\bjira\b.*\bcreate\b",
            r"\bacli\b.*\bcreate\b",
        ],
        "blocked_content_patterns": [],
    },
    "dev": {
        "allowed_paths": [],  # все пути
        "blocked_paths": [],
        "blocked_commands": [
            r"\bgit\s+push\b",
            r"\bjira\b.*\bcreate\b",
        ],
        "blocked_content_patterns": [],
    },
    "jira": {
        "allowed_paths": [],
        "blocked_paths": ["src/"],
        "blocked_commands": [
            r"\./gradlew\s+",
            r"\bgit\s+push\b",
            r"\bgit\s+commit\b",
        ],
        "blocked_content_patterns": [],
    },
}


def _detect_role(tool_input: dict, cwd: str | None = None) -> str | None:
    """Определяет роль субагента.

    Порядок:
    1. Явное поле role в tool_input (если передано оркестратором)
    2. Эвристика по prompt (описание/description)
    3. По tool_name (Bash с jira → role=jira)
    """
    # 1. Явная роль
    role = tool_input.get("role")
    if role and role in ROLE_POLICY:
        return role

    # 2. Эвристика по description
    desc = (tool_input.get("description") or tool_input.get("prompt") or "").lower()

    if any(kw in desc for kw in ["red", "test writer", "напиши тесты", "write failing tests"]):
        return "test"
    if any(kw in desc for kw in ["spec", "update spec", "documentation", "обнови спецификац"]):
        return "spec"
    if any(kw in desc for kw in ["tech design", "design", "проектирован", "архитектур"]):
        return "design"
    if any(kw in desc for kw in ["green", "implement", "реализуй", "java-spring-dev", "code for"]):
        return "dev"
    if any(kw in desc for kw in ["jira", "task writer", "заведи задач"]):
        return "jira"

    return None


def _target_path(tool_name: str, tool_input: dict) -> str:
    """Извлекает целевой путь из tool_input."""
    if tool_name in ("Write", "WriteFile"):
        return tool_input.get("file_path", "")
    if tool_name == "Edit":
        return tool_input.get("file_path", "")
    if tool_name == "Bash":
        return tool_input.get("command", "")
    return ""


def _detect_command_from_bash(command: str) -> str | None:
    """Извлекает команду уровня git/gradle/jira из shell-команды."""
    if re.search(r"\bgit\s+push\b", command):
        return "git push"
    if re.search(r"\bgit\s+commit\b", command):
        return "git commit"
    if re.search(r"\./gradlew\s+", command):
        return "gradle"
    if re.search(r"\bjira\b", command):
        return "jira"
    return None


def _block(reason: str) -> int:
    print(f"[sod-enforcer] DENY: {reason}", file=sys.stderr)
    return 2


def main() -> int:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    target = _target_path(tool_name, tool_input)

    # 1. Определяем роль
    role = _detect_role(tool_input)
    if not role:
        return 0  # fail-open: если роль не определена, не блокируем

    policy = ROLE_POLICY.get(role)
    if not policy:
        return 0

    # 2. Проверка по blocked_commands (только для Bash)
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        detected_cmd = _detect_command_from_bash(cmd)
        if detected_cmd:
            for pattern in policy.get("blocked_commands", []):
                if re.search(pattern, cmd):
                    return _block(
                        f"роль '{role}' не может выполнять '{detected_cmd}' "
                        f"(blocked_commands: {pattern})"
                    )
        return 0  # для Bash больше проверок нет

    # 3. Проверка по blocked_paths (для Write/Edit)
    if not target:
        return 0

    for blocked_pattern in policy.get("blocked_paths", []):
        if blocked_pattern in target.replace("\\", "/"):
            return _block(
                f"роль '{role}' не может писать в '{target}' "
                f"(blocked_paths: {blocked_pattern})"
            )

    # 4. Проверка blocked_content_patterns для content, который пишется
    if policy.get("blocked_content_patterns"):
        content = tool_input.get("content", "")
        for pattern in policy["blocked_content_patterns"]:
            if re.search(pattern, content):
                return _block(
                    f"роль '{role}' не может содержать паттерн '{pattern}' в контенте"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())