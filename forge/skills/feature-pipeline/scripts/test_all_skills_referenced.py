#!/usr/bin/env python3
"""
test_all_skills_referenced.py — Проверяет, что каждый скилл из таблицы §1
и вызовов feature-pipeline/SKILL.md присутствует в skill-paths.json.
И наоборот — нет ли в конфиге скиллов, которые не используются.

Exit codes:
  0 — все скиллы согласованы
  1 — расхождение
"""

import json
import re
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"
CONFIG_PATH = SKILL_DIR / "references" / "skill-paths.json"


def extract_skill_refs_from_skill_md(path: Path) -> set[str]:
    """
    Извлекает имена скиллов из SKILL.md:
    - Из таблицы §1 (колонка Исполнитель)
    - Из read_file("<project>/.gigacode/skills/<skill-name>/SKILL.md")
    - Из упоминаний в стиле `skills.<skill-name>.*`
    """
    text = path.read_text("utf-8")
    refs = set()

    # 1. read_file("<project>/.gigacode/skills/<name>/SKILL.md")
    for m in re.finditer(
        r'read_file\s*\(\s*["\'].*?/skills/([^/]+)/SKILL\.md["\']\s*\)', text
    ):
        refs.add(m.group(1))

    # 2. `skills.<skill-name>.`
    for m in re.finditer(r"skills\.([a-z][a-z0-9_-]*)\.", text):
        refs.add(m.group(1))

    # 3. Упоминания в блоках кода (python <project>/.gigacode/skills/<name>/...)
    for m in re.finditer(r"/skills/([a-z][a-z0-9_-]*)/", text):
        refs.add(m.group(1))

    return refs


def get_skills_from_config(path: Path) -> set[str]:
    """Извлекает имена скиллов из skill-paths.json -> skills.*"""
    config = json.loads(path.read_text("utf-8"))
    return set(config.get("skills", {}).keys())


def main():
    errors = []

    if not SKILL_MD.exists():
        print(f"❌ SKILL.md не найден: {SKILL_MD}")
        sys.exit(1)
    if not CONFIG_PATH.exists():
        print(f"❌ skill-paths.json не найден: {CONFIG_PATH}")
        sys.exit(1)

    skill_md_refs = extract_skill_refs_from_skill_md(SKILL_MD)
    config_skills = get_skills_from_config(CONFIG_PATH)

    print("📚 Скиллы из SKILL.md:", sorted(skill_md_refs))
    print("📚 Скиллы из skill-paths.json:", sorted(config_skills))
    print()

    # Скиллы, которые есть в SKILL.md, но нет в конфиге
    missing_in_config = skill_md_refs - config_skills
    if missing_in_config:
        errors.append(
            f"❌ Скиллы из SKILL.md отсутствуют в skill-paths.json: {sorted(missing_in_config)}"
        )

    # Скиллы, которые есть в конфиге, но не упомянуты в SKILL.md
    # (не обязательно ошибка — могут быть вспомогательными, просто warn)
    extra_in_config = config_skills - skill_md_refs
    if extra_in_config:
        print(f"⚠️  Скиллы в skill-paths.json, не упомянутые в SKILL.md: {sorted(extra_in_config)}")
        print("   (это нормально, если они используются другими скиллами)")

    if errors:
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print("🎉 Все скиллы согласованы!")
        sys.exit(0)


if __name__ == "__main__":
    main()