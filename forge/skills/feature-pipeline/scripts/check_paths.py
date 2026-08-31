#!/usr/bin/env python3
"""
check_paths.py — Preflight-валидатор путей.

Читает references/skill-paths.json и проверяет, что каждый путь
(строковое значение, начинающееся с '.' или относительного паттерна)
существует на диске относительно корня проекта.

Exit codes:
  0 — все пути валидны
  1 — один или более путей не найдены (вывод списка)
  2 — ошибка конфигурации (файл не найден, невалидный JSON)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_paths  # единый локатор реестра путей

# Локатор/резолвер корня — из единого модуля skill_paths (без дублирования).
find_project_root = skill_paths.find_project_root
find_skill_paths_json = skill_paths.find_registry


def collect_path_values(obj, prefix: str = "") -> list[str]:
    """Рекурсивно собирает все строковые значения, похожие на пути."""
    paths = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            sub_prefix = f"{prefix}.{key}" if prefix else key
            if isinstance(value, str):
                if value.startswith(".") and "/" in value:
                    paths.append(value)
            else:
                paths.extend(collect_path_values(value, sub_prefix))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            paths.extend(collect_path_values(item, f"{prefix}[{i}]"))
    return paths


def check_paths(project_root: Path, config_path: Path) -> tuple[list[str], list[str]]:
    """
    Проверяет все пути из конфига.
    Возвращает (valid_paths, invalid_paths).
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"❌ Файл не найден: {config_path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга JSON: {e}", file=sys.stderr)
        sys.exit(2)

    # Игнорируем _comment и ключи-исключения
    excluded_keys = {"_comment"}

    def filter_excluded(obj):
        """Рекурсивно удаляет excluded_keys."""
        if isinstance(obj, dict):
            return {k: filter_excluded(v) for k, v in obj.items() if k not in excluded_keys}
        return obj

    config = filter_excluded(config)
    raw_paths = collect_path_values(config)

    valid = []
    invalid = []
    for path in raw_paths:
        full_path = project_root / path
        if full_path.exists():
            valid.append(path)
        else:
            invalid.append(path)

    return valid, invalid


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Preflight-валидатор путей из references/skill-paths.json"
    )
    parser.add_argument(
        "--project", "-p",
        type=str,
        default=None,
        help="Корень проекта (по умолчанию определяется автоматически)",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Путь к skill-paths.json (по умолчанию ищется автоматически)",
    )
    parser.add_argument(
        "--skill",
        type=str,
        default="feature-pipeline",
        help="Имя скилла для поиска skill-paths.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывод в JSON-формате (для машинного потребления)",
    )

    args = parser.parse_args()

    project_root = Path(args.project).resolve() if args.project else find_project_root()

    if args.config:
        config_path = Path(args.config).resolve()
    else:
        config_path = find_skill_paths_json(project_root, skill=args.skill)

    if not config_path.exists():
        print(json.dumps({
            "status": "error",
            "message": f"skill-paths.json не найден: {config_path}",
        }))
        sys.exit(2)

    valid, invalid = check_paths(project_root, config_path)

    if args.json:
        result = {
            "status": "pass" if not invalid else "fail",
            "project_root": str(project_root),
            "config_path": str(config_path),
            "total": len(valid) + len(invalid),
            "valid": len(valid),
            "invalid": len(invalid),
            "invalid_paths": invalid,
            "valid_paths": valid,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"📁 Проект: {project_root}")
        print(f"📄 Конфиг: {config_path}")
        print(f"✅ Валидных путей: {len(valid)}")
        if invalid:
            print(f"❌ Битых путей: {len(invalid)}")
            for p in invalid:
                print(f"   • {p}")
            sys.exit(1)
        else:
            print("🎉 Все пути валидны!")


if __name__ == "__main__":
    main()