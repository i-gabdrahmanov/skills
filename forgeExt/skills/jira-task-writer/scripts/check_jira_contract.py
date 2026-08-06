#!/usr/bin/env python3
"""
check_jira_contract.py — проверяет, что SKILL.md jira-task-writer содержит все
необходимые контракты для безопасной работы (субагент без ask_user_question).

Проверки:
1. Запрет на ask_user_question (везде, не только в режиме субагента)
2. Наличие pending_questions механизма
3. Контракт возврата: не подтверждённый черновик (created: false + draft)
4. Контракт возврата: подтверждённое создание (confirmed: true + created)
5. Обработка answers от оркестратора

Usage:
    check_jira_contract.py <skill-path> [--json]

Exit:
    0 — PASS (все проверки пройдены)
    1 — FAIL (контракт нарушен)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def check_skill_contract(skill_md: str, skill_path: str) -> dict:
    """Проверяет SKILL.md на соответствие контракту."""
    checks = []

    # 1. Запрет на ask_user_question (абсолютный)
    has_ask_ban = bool(re.search(
        r"ЗАПРЕЩЕНО использовать.*ask_user_question|НИКОГДА не использовать.*ask_user_question",
        skill_md, re.IGNORECASE | re.DOTALL
    ))

    checks.append({
        "name": "Абсолютный запрет на ask_user_question",
        "status": "PASS" if has_ask_ban else "FAIL",
        "detail": "Есть явный абсолютный запрет ask_user_question"
                  if has_ask_ban else
                  "Нет абсолютного запрета на ask_user_question",
        "severity": "error",
    })

    # 2. pending_questions механизм
    has_pending_questions = bool(re.search(
        r"pending_questions", skill_md, re.IGNORECASE
    ))
    has_pending_contract = bool(re.search(
        r'"pending_questions"\s*:', skill_md
    ))

    checks.append({
        "name": "Механизм pending_questions",
        "status": "PASS" if has_pending_questions and has_pending_contract else "FAIL",
        "detail": f"pending_questions упомянут={'да' if has_pending_questions else 'нет'}, "
                  f"контракт JSON={'есть' if has_pending_contract else 'нет'}",
        "severity": "error",
    })

    # 3. Контракт возврата: не подтверждённый черновик
    has_draft_contract = bool(re.search(
        r"не подтверждённого черновика|created.*false.*draft|draft.*story.*subtasks",
        skill_md, re.IGNORECASE | re.DOTALL
    ))

    checks.append({
        "name": "Контракт возврата (черновик)",
        "status": "PASS" if has_draft_contract else "FAIL",
        "detail": "Контракт черновика (created: false + draft) описан" if has_draft_contract
                  else "Контракт возврата черновика не найден",
        "severity": "error",
    })

    # 4. Контракт возврата: подтверждённое создание
    has_confirm_contract = bool(re.search(
        r"подтверждённого запуска.*confirmed.*true|confirmed.*true.*создания|created.*true.*story.*key",
        skill_md, re.IGNORECASE | re.DOTALL
    ))

    checks.append({
        "name": "Контракт возврата (подтверждённое создание)",
        "status": "PASS" if has_confirm_contract else "FAIL",
        "detail": "Контракт подтверждённого создания (confirmed: true) описан"
                  if has_confirm_contract else
                  "Контракт возврата для confirmed: true не найден",
        "severity": "error",
    })

    # 5. Обработка answers от оркестратора
    has_answers_handling = bool(re.search(
        r"answers\s*:|передаст ответы при повторном|answers.*при повторном|confirmed.*true.*answers",
        skill_md, re.IGNORECASE
    ))

    checks.append({
        "name": "Обработка answers от оркестратора",
        "status": "PASS" if has_answers_handling else "FAIL",
        "detail": "Описана обработка answers при повторном запуске" if has_answers_handling
                  else "Нет упоминания обработки answers от оркестратора",
        "severity": "warning",
    })

    # Вердикт
    blocking_issues = [
        c["detail"] for c in checks
        if c["status"] == "FAIL" and c["severity"] == "error"
    ]
    warnings = [
        c["detail"] for c in checks
        if c["status"] == "FAIL" and c["severity"] == "warning"
    ]

    passed = not any(
        c["status"] == "FAIL" and c["severity"] == "error"
        for c in checks
    )

    return {
        "judge": "check-jira-contract",
        "skill_path": skill_path,
        "verdict": "PASS" if passed else "FAIL",
        "passed": passed,
        "checks": checks,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "summary": f"{sum(1 for c in checks if c['status'] == 'PASS')}/{len(checks)} "
                   f"checks passed. {len(blocking_issues)} blocking issues."
                   + (f" {len(warnings)} warnings." if warnings else ""),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Проверка контракта jira-task-writer SKILL.md"
    )
    ap.add_argument("skill_path", help="Путь к SKILL.md или директории скилла")
    ap.add_argument("--json", action="store_true", help="Вывести JSON в stdout")

    args = ap.parse_args()

    skill_path = Path(args.skill_path)
    if skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"

    if not skill_path.exists():
        print(f"ОШИБКА: файл не найден: {skill_path}", file=sys.stderr)
        sys.exit(2)

    content = skill_path.read_text(encoding="utf-8")
    result = check_skill_contract(content, str(skill_path))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for c in result["checks"]:
            mark = "✅" if c["status"] == "PASS" else "❌"
            print(f"  {mark} {c['name']}: {c['status']}")
            print(f"     {c['detail']}")
        print(f"\nВердикт: {result['verdict']}")
        print(f"  {result['summary']}")
        if result["blocking_issues"]:
            print(f"\nБлокирующие проблемы:")
            for issue in result["blocking_issues"]:
                print(f"  • {issue}")
        if result["warnings"]:
            print(f"\nПредупреждения:")
            for w in result["warnings"]:
                print(f"  • {w}")

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()