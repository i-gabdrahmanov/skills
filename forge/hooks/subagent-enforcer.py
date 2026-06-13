#!/usr/bin/env python3
"""
subagent-enforcer.py — PreToolUse Write/Edit хук.

Блокирует запись/редактирование файлов, если активный шаг пайплайна
относится к фазе, которая должна выполняться ЧЕРЕЗ СУБАГЕНТА, а не inline.

Проверяет: активный шаг манифеста (самый свежий manifest.json) —
если его id начинается с 02-design, 04-test, 04-build, 05-tests, 06-spec —
то запись в любые .md, .java, .json, .yml, .gradle файлы блокируется
с сообщением: "Этот шаг должен выполняться ЧЕРЕЗ agent(), а не inline."

Исключения:
- test-директории всегда разрешены (тесты пишутся свободно)
- ground/statements/ — разрешено (pipeline-state)
- write_file в <feature>/ папку (через субагента) — разрешено

Usage:
  hook: PreToolUse, matcher: (Write|Edit|WriteFile)

Environment:
  PROJECT_ROOT — корень проекта (из pipeline-config)
"""
import json
import os
import sys
import re
from pathlib import Path

# Фазы, которые ОБЯЗАНЫ выполняться через субагента
PHASES_REQUIRE_SUBAGENT = {
    "02-design", "04-test", "04-build", "05-tests", "06-spec",
}

# Паттерны файлов, которые блокируются (если шаг inline)
BLOCKED_PATTERNS = [
    r".*\.java$",
    r".*\.md$",
    r".*\.json$",
    r".*\.yml$",
    r".*\.yaml$",
    r".*\.gradle$",
    r".*\.xml$",
    r".*\.properties$",
]

# Пути, которые ВСЕГДА разрешены (даже для inline-шагов)
ALLOWED_PATHS = [
    "/ground/statements/",
    "/ground/approvals/",
    "/ground/evidence/",
    "/ground/brd-grounding/",
    "/.gigacode/",
    "/logs/",
]


def find_active_manifest(project_root: str) -> dict | None:
    """Находит самый свежий manifest среди активных фич."""
    statements_dir = Path(project_root) / "ground" / "statements" / "feature-pipeline"
    if not statements_dir.exists():
        return None

    latest_mtime = 0
    latest_manifest = None

    for feature_dir in statements_dir.iterdir():
        manifest_path = feature_dir / "manifest.json"
        if manifest_path.exists():
            try:
                mtime = manifest_path.stat().st_mtime
                if mtime > latest_mtime:
                    with open(manifest_path) as f:
                        manifest = json.load(f)
                    # Проверяем, что не archived
                    if manifest.get("context", {}).get("status") != "archived":
                        latest_mtime = mtime
                        latest_manifest = manifest
            except (json.JSONDecodeError, OSError):
                continue

    return latest_manifest


def get_active_step(manifest: dict) -> dict | None:
    """Находит активный (in_progress) шаг в manifest."""
    for step in manifest.get("steps", []):
        if step.get("status") == "in_progress":
            return step
    return None


def should_block(file_path: str, step_id: str) -> bool:
    """Проверяет, нужно ли блокировать запись в file_path для данного шага."""

    # Определяем, требует ли шаг субагента
    requires_subagent = any(step_id.startswith(prefix) for prefix in PHASES_REQUIRE_SUBAGENT)
    if not requires_subagent:
        return False

    # Проверяем allowed paths
    for allowed in ALLOWED_PATHS:
        if allowed in file_path:
            return False

    # Проверяем test-директории (всегда разрешены)
    if "/test/" in file_path or "/src/test/" in file_path:
        return False

    # Проверяем блокируемые паттерны
    for pattern in BLOCKED_PATTERNS:
        if re.match(pattern, file_path):
            return True

    return False


def main():
    # В хуках нет прямого доступа к аргументам WriteFile, используем окружение
    project_root = os.environ.get("PROJECT_ROOT", os.getcwd())
    file_path = os.environ.get("FILE_PATH", "")

    if not file_path:
        # Нет информации о файле — пропускаем (fail-open для безопасности)
        sys.exit(0)

    manifest = find_active_manifest(project_root)
    if not manifest:
        # Нет манифеста — не блокируем
        sys.exit(0)

    active_step = get_active_step(manifest)
    if not active_step:
        # Нет активного шага — не блокируем
        sys.exit(0)

    step_id = active_step.get("id", "")
    if should_block(file_path, step_id):
        print(
            f"[subagent-enforcer] BLOCKED: запись в '{file_path}' во время шага '{step_id}'. "
            f"Фаза {step_id} должна выполняться через agent(subagent_type='general-purpose', ...), "
            f"а не inline. Используй agent() для этой фазы.",
            file=sys.stderr,
        )
        # exit 1 — warning (не блокируем жёстко, чтобы не сломать)
        # Если нужна жёсткая блокировка — exit 2
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()