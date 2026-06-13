#!/usr/bin/env python3
"""
test_skill_paths_exist.py — Детерминированная проверка: каждый путь из
skill-paths.json существует на диске относительно корня проекта.

Exit codes:
  0 — все пути валидны
  1 — один или более путей не найдены
"""

import json
import os
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_DIR.parents[3]  # .gigacode/skills/feature-pipeline -> .gigacode -> project root
CONFIG_PATH = SKILL_DIR / "references" / "skill-paths.json"


def collect_path_values(obj):
    """Рекурсивно собирает все строковые значения, похожие на пути."""
    paths = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "_comment":
                continue
            if isinstance(value, str):
                if value.startswith(".") and "/" in value:
                    paths.append(value)
            else:
                paths.extend(collect_path_values(value))
    elif isinstance(obj, list):
        for item in obj:
            paths.extend(collect_path_values(item))
    return paths


def main():
    errors = []

    if not CONFIG_PATH.exists():
        print(f"❌ CONFIG NOT FOUND: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    all_paths = collect_path_values(config)
    print(f"📄 Проверяю {len(all_paths)} путей из {CONFIG_PATH.name}...")

    for path in all_paths:
        full = PROJECT_ROOT / path
        status = "✅" if full.exists() else "❌"
        if not full.exists():
            errors.append(path)
        print(f"  {status} {path}")

    print()
    if errors:
        print(f"❌ НАЙДЕНО {len(errors)} БИТЫХ ПУТЕЙ:")
        for p in errors:
            print(f"   • {p}")
        sys.exit(1)
    else:
        print("🎉 Все пути валидны!")
        sys.exit(0)


if __name__ == "__main__":
    main()