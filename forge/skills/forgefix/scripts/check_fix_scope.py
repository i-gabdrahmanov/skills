#!/usr/bin/env python3
"""check_fix_scope.py — детерминированный скоуп-чек fix-пути (шаг fix-intake).

Зачем: главный симптом, ради которого заведён fix-путь, — «пришёл багфикс, а харнес завёл
новую фичу». Решение «это дефект или фича» нельзя оставлять прозе SKILL.md: issuetype,
приоритет и текст приходят детерминированно, значит и классифицирует их скрипт.

Проверяет ДВЕ стороны:
  • это вообще дефект? (issuetype=Bug либо признаки поломки в тексте) — иначе не fix-путь;
  • он МЕЛКИЙ? (не Epic/Story, не Blocker, без migration/refactor/redesign, один сценарий).

Usage:
    check_fix_scope.py --issue-json <file>   # JSON ответа Jira MCP (модель сохраняет в файл)
    check_fix_scope.py --issue-json -        # или из stdin
    check_fix_scope.py --text-file <file>    # свободное описание бага (прогон без Jira)

Понимает и сырой формат Jira REST ({"fields": {...}}), и плоский ({"issuetype": "...",
"summary": "...", "description": "...", "priority": "..."}).

Exit: 0 — минорный дефект, fix-путь подходит;
      3 — ESCALATE: скоуп не для fix (это фича / крупная задача / нет описания) —
          СТОП, спроси пользователя: «взять fix, lite или full?»;
      2 — ошибка входа (нечитаемый JSON/файл).
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# Типы задач, которые fix не берёт (это не дефект, а работа full/lite-пути)
_NOT_BUG_ISSUETYPES = {"epic", "story", "new feature", "эпик", "история", "improvement",
                       "улучшение", "task", "задача"}
# Типы, однозначно говорящие «дефект»
_BUG_ISSUETYPES = {"bug", "defect", "баг", "дефект", "ошибка", "incident", "инцидент"}

# Приоритеты «это не минорный фикс — подтверди у пользователя»
_HIGH_PRIORITIES = {"blocker", "critical", "highest", "блокер", "критический", "наивысший"}

# Признаки поломки в тексте (когда issuetype не сказал прямо)
_BUG_MARKERS = re.compile(
    r"(?:не\s+работает|не\s+отображ\w+|не\s+сохран\w+|не\s+приход\w+|падае\w+|упал\w*|"
    r"ошибк\w*|некорректн\w*|неверн\w*|неправильн\w*|воспроизв\w+|регресс\w*|"
    r"\bбаг\b|\bдефект\w*|\bbug\b|\bdefect\b|\bregression\b|\bNPE\b|\bexception\b|"
    r"stack\s?trace|500\s|internal\s+server\s+error|\bfix\b|\bпочин\w+|\bисправ\w+)", re.I)

# Признаки «это новая функциональность», а не починка существующей
_FEATURE_MARKERS = re.compile(
    r"(?:нужно\s+добавить|добавить\s+возможност\w+|реализовать\s+нов\w+|новый\s+эндпойнт|"
    r"новая\s+функциональност\w+|новая\s+фича|\bnew\s+feature\b|\bimplement\s+new\b)", re.I)

# Слова, указывающие на скоуп больше минорного фикса
_BIG_SCOPE_RE = re.compile(
    r"(?:\bbreaking\s+change\b|\bmigration\b|\bмиграци\w+|\brefactor\w*|\w*рефактор\w*|"
    r"\bredesign\b|\bпереписать\b|\bперепроектир\w+|\bархитектурн\w+)", re.I)

_LIST_MARKERS = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\[ ?\])\s+\S", re.M)

# Больше стольких пунктов списка — похоже на набор независимых сценариев, а не один дефект
_MAX_LIST_ITEMS = 8


def _extract(issue: dict) -> tuple[str, str, str, str]:
    """(issuetype, priority, summary, description) из сырого Jira REST или плоского формата."""
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else issue
    it = fields.get("issuetype")
    issuetype = (it.get("name") if isinstance(it, dict) else it) or ""
    pr = fields.get("priority")
    priority = (pr.get("name") if isinstance(pr, dict) else pr) or ""
    summary = fields.get("summary") or ""
    desc = fields.get("description")
    if isinstance(desc, dict):  # ADF (Atlassian Document Format) — плоский текст из нод
        desc = json.dumps(desc, ensure_ascii=False)
        desc = " ".join(re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', desc))
    return str(issuetype), str(priority), str(summary), str(desc or "")


def check_scope(issue: dict) -> list[str]:
    """Список причин эскалации (пусто — скоуп ок для fix-пути)."""
    issuetype, priority, summary, desc = _extract(issue)
    text = f"{summary}\n{desc}"
    itype = issuetype.strip().lower()
    reasons: list[str] = []

    # 1. Это вообще дефект? Тип Bug — достаточное свидетельство; иначе ищем признаки поломки.
    is_bug_type = itype in _BUG_ISSUETYPES
    if not is_bug_type:
        if itype in _NOT_BUG_ISSUETYPES:
            reasons.append(f"issuetype '{issuetype}' — это не дефект "
                           f"(fix-путь только для багов; фичу веди через full/lite)")
        elif not _BUG_MARKERS.search(text):
            reasons.append("в задаче не распознан дефект: нет типа Bug и нет признаков поломки "
                           "(«не работает», «ошибка», «падает», exception, регресс)")
        if _FEATURE_MARKERS.search(text):
            reasons.append("текст описывает НОВУЮ функциональность "
                           "(«нужно добавить», «реализовать новый»), а не починку существующей")

    # 2. Он минорный?
    if priority.strip().lower() in _HIGH_PRIORITIES:
        reasons.append(f"приоритет '{priority}' — это не минорный фикс, нужно подтверждение")
    m = _BIG_SCOPE_RE.search(text)
    if m:
        reasons.append(f"слово '{m.group(0)}' указывает на скоуп больше минорного фикса")
    if len(desc.strip()) < 40:
        reasons.append("описание пустое/слишком короткое — нет симптома и шагов воспроизведения, "
                       "чинить нечего")
    items = len(_LIST_MARKERS.findall(desc))
    if items > _MAX_LIST_ITEMS:
        reasons.append(f"в описании {items} пунктов списка — похоже на несколько независимых "
                       f"сценариев, а не один дефект")
    return reasons


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue-json", help="файл с JSON issue или '-' (stdin)")
    g.add_argument("--text-file", help="файл со свободным описанием бага (прогон без Jira)")
    args = p.parse_args()

    try:
        if args.text_file:
            text = open(args.text_file, encoding="utf-8").read()
            issue = {"issuetype": "", "summary": text.strip().splitlines()[0] if text.strip() else "",
                     "description": text}
        else:
            raw = sys.stdin.read() if args.issue_json == "-" else \
                open(args.issue_json, encoding="utf-8").read()
            issue = json.loads(raw)
            if not isinstance(issue, dict):
                raise ValueError("ожидался JSON-объект issue")
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"[check_fix_scope] ERROR: не смог прочитать вход: {e}", file=sys.stderr)
        return 2

    reasons = check_scope(issue)
    if not reasons:
        print("[check_fix_scope] OK: минорный дефект — продолжай fix-путь")
        return 0
    print("⛔ ESCALATE: задача не похожа на минорный дефект:", file=sys.stderr)
    for r in reasons:
        print(f"   - {r}", file=sys.stderr)
    print("   СТОП: спроси пользователя — «это фикс (forgefix), готовая подзадача (forgelite) "
          "или фича с нуля (feature-pipeline)?». Не решай молча.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
