#!/usr/bin/env python3
"""
test_no_hardcoded_paths_left.py — Сканирует SKILL.md скиллов пайплайна И
операционные reference-файлы (subagent-prompts.md), которые попадают субагенту
дословно, на оставшиеся хардкодные пути вида ~/.gigacode/...
Если находит — падает со списком.

Канон — ПРОЕКТНАЯ модель: пути вида `<project>/.gigacode/...`, а не домашний
`~/.gigacode/...` (см. _project.py / DEPLOY.md). subagent-prompts.md и
project-grounder/SKILL.md раньше не сканировались — дрейф там накапливался незаметно.

Вторая проверка (supply-chain): ни один версионируемый md/json под skills/ не должен
содержать МАШИННЫЙ абсолютный путь /Users/<имя>/... — прецедент: реальные пути оператора
в minor-defect-fix/config.json уезжали в каждый деплой. Плейсхолдеры `/Users/.../` и
локальный (негитуемый) minor-defect-fix/config.json — легальны.

Exit codes:
  0 — чистый код (нет хардкода)
  1 — найдены хардкодные пути
"""

import re
import sys
from pathlib import Path


# База — каталог skills/ (parents[2] от scripts/). Работает И в source-репо (skills/…),
# И в развёрнутом проекте (<project>/.gigacode/skills/…), т.к. пути относительны skills/.
# Раньше база считалась как parents[4] + ".gigacode/skills/…" — в source-репо такого пути
# нет, все файлы «пропускались», и тест давал ложную зелень. Теперь резолв корректен в обеих
# раскладках, а отсутствие файла из списка — ошибка (а не молчаливый skip).
SKILLS_DIR = Path(__file__).resolve().parents[2]

# Скиллы пайплайна, которые надо проверить (пути относительно skills/)
SKILL_PATHS = [
    "feature-pipeline/SKILL.md",
    "system-analyst/SKILL.md",
    "tech-design/SKILL.md",
    "pipeline-state/SKILL.md",
    "minor-defect-fix/SKILL.md",
    "jira-task-writer/SKILL.md",
    "java-spring-dev/SKILL.md",
    "brd-interview/SKILL.md",
    "business-requirements/SKILL.md",
    "defect-analyzer/SKILL.md",
    "bugfix-developer/SKILL.md",
    "project-grounder/SKILL.md",
    "brd-grounder/SKILL.md",
    # Операционные reference-файлы: уходят субагенту дословно (get_prompt.py не подставляет пути).
    "feature-pipeline/references/subagent-prompts.md",
]


# Паттерн для поиска: ~/.gigacode (но не внутри строчки-заметки о том, что не надо так делать)
HARDCODED_PATTERN = re.compile(r"~\/\.gigacode")

# Машинный путь: /Users/<реальный сегмент>/ — но НЕ плейсхолдер /Users/.../
MACHINE_PATH_PATTERN = re.compile(r"/Users/(?!\.\.\.)[A-Za-z0-9_.-]+/")

# Файлы, где машинные пути легальны: локальный конфиг оператора (не версионируется,
# в деплой-копии содержит реальные маппинги проект→спека — это его работа).
MACHINE_PATH_EXEMPT = {"minor-defect-fix/config.json"}


def line_is_notice(line: str) -> bool:
    """Проверяет, не является ли строка заметкой 'Не используй ~/.gigacode'."""
    return "Не используй" in line and "~/.gigacode" in line


def scan_machine_paths() -> list:
    """Сканирует ВСЕ md/json под skills/ на реальные /Users/<имя>/-пути."""
    errors = []
    for path in sorted(SKILLS_DIR.rglob("*")):
        if path.suffix not in (".md", ".json") or not path.is_file():
            continue
        rel = path.relative_to(SKILLS_DIR).as_posix()
        if "__pycache__" in rel or rel in MACHINE_PATH_EXEMPT:
            continue
        try:
            lines = path.read_text("utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            if MACHINE_PATH_PATTERN.search(line):
                errors.append((rel, i, line.strip()))
    return errors


def main():
    total_errors = []

    for rel_path in SKILL_PATHS:
        full_path = SKILLS_DIR / rel_path
        if not full_path.exists():
            # Не молчаливый skip: отсутствующий файл из списка — это ошибка покрытия.
            print(f"❌ {rel_path}: файл не найден по базе {SKILLS_DIR}")
            total_errors.append((rel_path, 0))
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

    # Supply-chain: реальные машинные пути в версионируемых md/json скиллов
    machine_errors = scan_machine_paths()
    if machine_errors:
        print("❌ Машинные пути /Users/<имя>/ в файлах скиллов (утекут в деплой):")
        for rel, lineno, text in machine_errors:
            print(f"   {rel}:{lineno}: {text[:120]}")
        total_errors.extend([(rel, lineno) for rel, lineno, _ in machine_errors])
    else:
        print("✅ машинные /Users/-пути в skills/ — чисто")

    print()
    if total_errors:
        print(f"❌ НАЙДЕНО {len(total_errors)} ХАРДКОДНЫХ ПУТЕЙ. Замени их на ссылки из skill-paths.json.")
        sys.exit(1)
    else:
        print("🎉 Ни одного хардкодного пути не найдено!")
        sys.exit(0)


if __name__ == "__main__":
    main()