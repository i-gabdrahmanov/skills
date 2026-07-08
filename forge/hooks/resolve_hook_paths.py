#!/usr/bin/env python3
"""
resolve_hook_paths.py — подстановка ${PROJECT_ROOT} в settings.hooks.json.

Читает .gigacode/hooks/settings.hooks.json (эталон с плейсхолдером),
заменяет ${PROJECT_ROOT} на реальный путь к корню проекта,
и обновляет ТОЛЬКО блок "hooks" в существующем .gigacode/settings.json.
Все остальные секции (mcpServers, permissions, $version, ...) НЕ трогает.

Usage:
    python3 resolve_hook_paths.py                           # авто: git toplevel
    python3 resolve_hook_paths.py --project /path/to/proj   # явный путь
    python3 resolve_hook_paths.py --dry-run                 # только вывод, без записи
    python3 resolve_hook_paths.py --check                   # exit 0 если всё ок, 1 если проблемы
"""
from __future__ import annotations  # PEP604 (X | None) под Python 3.9

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLACEHOLDER = "${PROJECT_ROOT}"
PYTHON_PLACEHOLDER = "${PYTHON}"


def find_python_cmd() -> str:
    """Абсолютный путь к интерпретатору, которым запущен сам resolver.

    Это тот же python, что deploy-local.sh уже нашёл в PATH (python3/python/py) —
    подставляем его абсолютным путём, чтобы хуки на рантайме не зависели от PATH
    (на Windows часто нет python3, только python.exe/py.exe).
    """
    exe = sys.executable
    if not exe:
        return "python3"
    return f'"{exe}"' if " " in exe else exe


def find_project_root(cwd: str | None = None) -> str:
    """Определяет корень проекта через git, gradle или pipeline.json."""
    start = Path(cwd or os.getcwd()).resolve()
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists():
            return str(parent)
    for parent in [start] + list(start.parents):
        if (parent / "build.gradle").exists() or (parent / "settings.gradle").exists():
            return str(parent)
    for parent in [start] + list(start.parents):
        if (parent / "ground" / "pipeline.json").exists():
            return str(parent)
    return str(start)


def resolve_string(value: str, project_root: str, python_cmd: str) -> str:
    """Заменяет ${PROJECT_ROOT} и ${PYTHON} в строке."""
    return value.replace(PLACEHOLDER, project_root).replace(PYTHON_PLACEHOLDER, python_cmd)


def resolve_hooks_block(hooks: dict, project_root: str, python_cmd: str) -> dict:
    """Рекурсивно заменяет ${PROJECT_ROOT}/${PYTHON} во всех строковых значениях блока hooks."""

    def _walk(node):
        if isinstance(node, str):
            return resolve_string(node, project_root, python_cmd)
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(hooks)


def has_placeholder(value) -> bool:
    """Проверяет, есть ли хоть один ${PROJECT_ROOT} в JSON-значении."""
    if isinstance(value, str):
        return PLACEHOLDER in value
    if isinstance(value, dict):
        return any(has_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(has_placeholder(item) for item in value)
    return False


def has_absolute_hook_paths(settings: dict) -> list[str]:
    """Ищет оставшиеся абсолютные пути в command-полях блока hooks.

    Возвращает список путей, которые НЕ принадлежат текущему проекту.
    """
    hooks = settings.get("hooks", {})
    found = []

    def _walk(node, path=""):
        if isinstance(node, str) and path.endswith("command"):
            # Ищем путь к .py-хуку, а не к интерпретатору — тот может быть
            # "python3", "python" или абсолютным путём (sys.executable, в т.ч. в кавычках).
            m = re.search(r"(/\S+\.py)\b", node)
            if m:
                found.append(m.group(1))
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(hooks)
    return found


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    check_mode = "--check" in args

    # Определяем корень проекта
    project_root = None
    if "--project" in args:
        idx = args.index("--project")
        if idx + 1 < len(args):
            project_root = args[idx + 1]
    if not project_root:
        try:
            project_root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            project_root = find_project_root()

    project_root = str(Path(project_root).resolve())
    project_gigacode = Path(project_root) / ".gigacode"
    hooks_template_path = project_gigacode / "hooks" / "settings.hooks.json"
    target_settings_path = project_gigacode / "settings.json"

    # --- check mode ---
    if check_mode:
        issues = []

        if not hooks_template_path.exists():
            issues.append(f"MISSING: {hooks_template_path}")
        else:
            try:
                tmpl = json.loads(hooks_template_path.read_text())
                if not has_placeholder(tmpl.get("hooks", {})):
                    issues.append(
                        f"ETALON HAS NO {PLACEHOLDER} in hooks block: {hooks_template_path}"
                    )
            except (json.JSONDecodeError, OSError) as e:
                issues.append(f"PARSE ERROR: {hooks_template_path}: {e}")

        if target_settings_path.exists():
            try:
                stg = json.loads(target_settings_path.read_text())
                abs_paths = has_absolute_hook_paths(stg)
                expected_prefix = f"{project_root}/.gigacode/hooks/"
                foreign = [p for p in abs_paths if not p.startswith(expected_prefix)]
                if foreign:
                    issues.append(
                        f"FOREIGN ABSOLUTE PATHS in settings.json hooks: {foreign}"
                    )
            except (json.JSONDecodeError, OSError) as e:
                issues.append(f"PARSE ERROR: {target_settings_path}: {e}")

        if issues:
            print(json.dumps({"passed": False, "issues": issues}, ensure_ascii=False, indent=2))
            sys.exit(1)
        else:
            print(
                json.dumps(
                    {"passed": True, "project_root": project_root},
                    ensure_ascii=False,
                )
            )
            sys.exit(0)

    # --- resolve mode ---
    if not hooks_template_path.exists():
        print(
            json.dumps(
                {
                    "error": f"settings.hooks.json not found: {hooks_template_path}",
                    "passed": False,
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    template = json.loads(hooks_template_path.read_text())
    template_hooks = template.get("hooks", {})

    if not has_placeholder(template_hooks):
        print(
            json.dumps(
                {
                    "warning": f"Эталон {hooks_template_path} не содержит {PLACEHOLDER} в hooks. "
                    f"Копируем блок как есть.",
                    "passed": True,
                },
                ensure_ascii=False,
            )
        )
        resolved_hooks = template_hooks
    else:
        resolved_hooks = resolve_hooks_block(template_hooks, project_root, find_python_cmd())

    # Читаем существующий settings.json или создаём новый
    if target_settings_path.exists():
        existing = json.loads(target_settings_path.read_text())
    else:
        existing = {}

    # Обновляем ТОЛЬКО блок hooks
    existing["hooks"] = resolved_hooks

    # Гарантируем minimal обязательные поля
    existing.setdefault("disableAllHooks", False)
    existing.setdefault("$version", 3)

    output = json.dumps(existing, ensure_ascii=False, indent=2)

    if dry_run:
        print(output)
        return

    target_settings_path.write_text(output)

    # Считаем количество замен
    count = count_occurrences_in_hooks(template_hooks)

    print(
        json.dumps(
            {
                "passed": True,
                "project_root": project_root,
                "source": str(hooks_template_path),
                "target": str(target_settings_path),
                "hooks_updated": True,
                "other_sections_preserved": [
                    k for k in existing if k != "hooks"
                ],
                "replacements_made": count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def count_occurrences_in_hooks(node) -> int:
    """Считает количество ${PROJECT_ROOT} в JSON-значении."""
    count = 0
    if isinstance(node, str):
        count += node.count(PLACEHOLDER)
    elif isinstance(node, dict):
        for v in node.values():
            count += count_occurrences_in_hooks(v)
    elif isinstance(node, list):
        for item in node:
            count += count_occurrences_in_hooks(item)
    return count


if __name__ == "__main__":
    main()