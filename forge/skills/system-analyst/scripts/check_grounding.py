#!/usr/bin/env python3
"""
Проверяет, собран ли grounding (системный обзор) для проекта.
Вызывается в фазе 1 feature-pipeline.

Exit 0 (есть) — переиспользовать найденный обзор.
Exit 1 (нет) — нужно запустить полный system-analyst.

Usage:
    python3 check_grounding.py --root . [--json]
"""

import argparse
import json
import sys
from pathlib import Path


def check_grounding(project_root: str) -> dict:
    root = Path(project_root)

    # 1. Проверка grounding-excerpt.json (полный обзор с выжимкой)
    excerpt_paths = [
        root / "docs" / "system-analysis" / "grounding-excerpt.json",
        root / "docs" / "system-analysis" / "grounding" / "grounding-excerpt.json",
    ]
    for p in excerpt_paths:
        if p.exists():
            try:
                data = json.loads(p.read_text())
                return {
                    "status": "found",
                    "path": str(p),
                    "kind": "excerpt",
                    "modules": data.get("modules", []),
                    "entities_count": len(data.get("entities", [])),
                    "gate_total": data.get("gate_total", 0),
                }
            except (json.JSONDecodeError, KeyError):
                continue

    # 2. Проверка README.md (краткий обзор)
    readme_path = root / "docs" / "system-analysis" / "README.md"
    if readme_path.exists():
        return {
            "status": "found",
            "path": str(readme_path),
            "kind": "overview",
            "modules": [],
            "entities_count": 0,
            "gate_total": 0,
        }

    # 3. Проверка scan-директории
    scan_dir = root / "docs" / "system-analysis" / "scan"
    if scan_dir.is_dir() and any(scan_dir.iterdir()):
        return {
            "status": "found",
            "path": str(scan_dir),
            "kind": "scan",
            "modules": [],
            "entities_count": 0,
            "gate_total": 0,
        }

    return {"status": "not_found", "path": None, "kind": None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Корень проекта")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывод в JSON (машиночитаемый)",
    )
    args = parser.parse_args()

    result = check_grounding(args.root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["status"] == "found":
            print(f"Grounding found: {result['path']} (kind={result['kind']})")
        else:
            print("Grounding not found")

    sys.exit(0 if result["status"] == "found" else 1)