#!/usr/bin/env python3
"""sod-enforcer.py — Separation of Duties: PreToolUse хук.

Блокирует действия, не соответствующие роли текущей фазы пайплайна, на основе ROLE_POLICY.
Если активна фаза тестов (04-test), а цель — src/main/ — BLOCK. Если активна фаза дизайна, а
команда — build — BLOCK. Git-команды (commit/push) хук НЕ гейтит: доставку делает пользователь
сам (промптом или руками), пайплайн заканчивается верифицированным артефактом.

**Роль определяется по АКТИВНОМУ шагу манифеста** (in_progress), а не по tool_input: на Write/Edit
в tool_input нет ни role, ни prompt субагента (все субагенты — general-purpose), поэтому прежняя
эвристика по tool_input всегда давала None и хук молчал. Теперь роль детерминированно выводится из
id активного шага, который ведёт state-recorder/update.

В отличие от update._check_subagent_origin (который проверяет, что фаза ЗАКРЫТА субагентом),
sod-enforcer проверяет, что действие внутри фазы НЕ выходит за границы её роли.

Маппинг: (Write|Edit|Bash) на пути src/main/ + src/test/ + git/build.
Блок: exit 2 + stderr. fail-open: если активной фичи/шага нет — пропускает.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# risk_ladder (co-located) даёт project_root + active_manifest — те же резолверы, что у gate-guard.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import risk_ladder as _R
except Exception:  # pragma: no cover
    _R = None

# Команда сборки/тестов/линта — Gradle ИЛИ Maven ИЛИ standalone-линтер. Роль build/test закрыта
# для spec/design/jira-фаз; раньше распознавался только `./gradlew` (на Maven `mvn` не срабатывал,
# P1-16), + standalone checkstyle/ktlint/detekt/spotless (checkstyle inline в дизайн-фазе).
BUILD_CMD_RE = r"(?:\./gradlew\s+|\bmvn\b|\b(?:checkstyle|ktlint|detekt|spotless)\b)"

# Роли и их разрешённые действия
# role → { allowed_path_prefixes, blocked_commands, blocked_path_patterns }
ROLE_POLICY = {
    "test": {
        "allowed_paths": ["src/test/"],
        "blocked_paths": ["src/main/"],
        "blocked_commands": [],
        "blocked_content_patterns": [
            r"(?i)throw\s+new\s+(UnsupportedOperationException|RuntimeException)\s*\(\s*\"(not\s+implemented|stub)",
        ],
    },
    "spec": {
        "allowed_paths": ["docs/", "ground/"],
        "blocked_paths": ["src/"],
        "blocked_commands": [
            BUILD_CMD_RE,
        ],
        "blocked_content_patterns": [],
    },
    "design": {
        "allowed_paths": [],  # все пути
        "blocked_paths": [],  # нет ограничений по путям
        "blocked_commands": [
            BUILD_CMD_RE,
            r"\bjira\b.*\bcreate\b",
            r"\bacli\b.*\bcreate\b",
        ],
        "blocked_content_patterns": [],
    },
    "dev": {
        "allowed_paths": [],  # все пути
        "blocked_paths": [],
        "blocked_commands": [
            r"\bjira\b.*\bcreate\b",
        ],
        "blocked_content_patterns": [],
    },
    "jira": {
        "allowed_paths": [],
        "blocked_paths": ["src/"],
        "blocked_commands": [
            BUILD_CMD_RE,
        ],
        "blocked_content_patterns": [],
    },
}

# Префикс id шага → роль фазы. Длинный префикс выигрывает. Фазы без ограничений SoD
# (00-brd / 01-grounding) сюда не входят → роль None → fail-open.
STEP_ROLE = {
    "02-sdd": "spec",
    "02-eval-plan": "design",
    "02-design": "design",
    "02-": "design",
    "03-jira": "jira",
    "03-": "jira",
    "04-test": "test",
    "04-build": "dev",
    "05-tests": "test",
    "05-": "test",
    "06-spec": "spec",
    "06-": "spec",
    # Lite-ветка (forgelite): плоские шаги lite-*.
    "lite-design": "design",
    "lite-red": "test",
    "lite-green": "dev",
    "lite-verify": "test",
}


def _active_step_id(root: Path) -> str | None:
    """id активного (in_progress) шага самого свежего манифеста активной фичи."""
    if _R is None:
        return None
    try:
        mp = _R.active_manifest(root)
        if not mp or not mp.exists():
            return None
        manifest = json.loads(mp.read_text(encoding="utf-8"))
        for step in manifest.get("steps", []):
            if step.get("status") == "in_progress":
                return step.get("id") or None
    except Exception:
        return None
    return None


def _detect_role(root: Path) -> str | None:
    """Роль текущей фазы по id активного шага манифеста (детерминированно)."""
    step_id = _active_step_id(root)
    if not isinstance(step_id, str) or not step_id:
        return None
    for prefix, role in sorted(STEP_ROLE.items(), key=lambda kv: -len(kv[0])):
        if step_id.startswith(prefix):
            return role
    return None


def _target_path(tool_name: str, tool_input: dict) -> str:
    """Извлекает целевой путь из tool_input. Канон-имена рантайма (write_file/edit/notebook_edit,
    run_shell_command) + Claude-алиасы — иначе роль-блок на записи молча не срабатывал."""
    if tool_name in ("Write", "WriteFile", "Edit", "edit", "write_file",
                     "NotebookEdit", "notebook_edit"):
        return tool_input.get("file_path") or tool_input.get("path") or ""
    if tool_name in ("Bash", "run_shell_command"):
        return tool_input.get("command", "")
    return ""


def _detect_command_from_bash(command: str) -> str | None:
    """Извлекает команду уровня gradle/jira из shell-команды (git не гейтим)."""
    if re.search(BUILD_CMD_RE, command):
        return "build"
    if re.search(r"\bjira\b", command):
        return "jira"
    return None


def _block(reason: str) -> int:
    print(f"[sod-enforcer] DENY: {reason}", file=sys.stderr)
    return 2


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0  # не-JSON stdin — fail-open, не роняем инструмент
    if not isinstance(data, dict):
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    target = _target_path(tool_name, tool_input)

    # 1. Определяем роль по активному шагу манифеста
    root = Path(_R.project_root(data.get("cwd", ""))) if _R else Path(data.get("cwd") or ".")
    role = _detect_role(root)
    if not role:
        return 0  # fail-open: вне фазы с SoD — не блокируем

    policy = ROLE_POLICY.get(role)
    if not policy:
        return 0

    # 2. Проверка по blocked_commands (только для Bash)
    if tool_name in ("Bash", "run_shell_command"):
        cmd = tool_input.get("command", "")
        detected_cmd = _detect_command_from_bash(cmd)
        if detected_cmd:
            for pattern in policy.get("blocked_commands", []):
                if re.search(pattern, cmd):
                    return _block(
                        f"роль '{role}' (фаза активного шага) не может выполнять '{detected_cmd}' "
                        f"(blocked_commands: {pattern})"
                    )
        return 0  # для Bash больше проверок нет

    # 3. Проверка по blocked_paths (для Write/Edit)
    if not target:
        return 0

    norm = target.replace("\\", "/")
    for blocked_pattern in policy.get("blocked_paths", []):
        if blocked_pattern in norm:
            return _block(
                f"роль '{role}' (фаза активного шага) не может писать в '{target}' "
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
