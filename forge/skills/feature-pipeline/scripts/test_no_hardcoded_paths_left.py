#!/usr/bin/env python3
"""
test_no_hardcoded_paths_left.py — Сканирует все SKILL.md скиллов пайплайна
на оставшиеся хардкодные пути вида ~/.gigacode/...
Если находит — падает со списком.

Exit codes:
  0 — чистый код (нет хардкода)
  1 — найдены хардкодные пути
"""

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Скиллы пайплайна, которые надо проверить
SKILL_PATHS = [
    ".gigacode/skills/feature-pipeline/SKILL.md",
    ".gigacode/skills/system-analyst/SKILL.md",
    ".gigacode/skills/tech-design/SKILL.md",
    ".gigacode/skills/pipeline-state/SKILL.md",
    ".gigacode/skills/minor-defect-fix/SKILL.md",
    ".gigacode/skills/jira-task-writer/SKILL.md",
    ".gigacode/skills/java-spring-dev/SKILL.md",
    ".gigacode/skills/brd-interview/SKILL.md",
    ".gigacode/skills/business-requirements/SKILL.md",
    ".gigacode/skills/defect-analyzer/SKILL.md",
    ".gigacode/skills/bugfix-developer/SKILL.md",
]


# Паттерн для поиска: ~/.gigacode (но не внутри строчки-заметки о том, что не надо так делать)
HARDCODED_PATTERN = re.compile(r"~\/\.gigacode")


def line_is_notice(line: str) -> bool:
    """Проверяет, не является ли строка заметкой 'Не используй ~/.gigacode'."""
    return "Не используй" in line and "~/.gigacode" in line


def main():
    total_errors = []

    for rel_path in SKILL_PATHS:
        full_path = PROJECT_ROOT / rel_path
        if not full_path.exists():
            print(f"⚠️  Файл не найден (пропускаю): {rel_path}")
            continue

        lines = full_path.read_text("utf-8").splitlines()
        file_errors = []

        for i, line in enumerate(lines, 1):
            if HARDCODED_PATTERN.search(line):
                if line_is_notice(line):
                    continue  # intentional notice about NOT using hardcoded paths
                file_errors.append((i, line.strip()))

        if file_errors:
            print(f"❌ {rel_path}:")
            for lineno, text in file_errors:
                print(f"   строка {lineno}: {text}")
            total_errors.extend([(rel_path, lineno) for lineno, _ in file_errors])
        else:
            print(f"✅ {rel_path} — чисто")

    print()
    if total_errors:
        print(f"❌ НАЙДЕНО {len(total_errors)} ХАРДКОДНЫХ ПУТЕЙ. Замени их на ссылки из skill-paths.json.")
        sys.exit(1)
    else:
        print("🎉 Ни одного хардкодного пути не найдено!")
        sys.exit(0)


if __name__ == "__main__":
    main()