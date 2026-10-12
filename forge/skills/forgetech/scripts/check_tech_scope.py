#!/usr/bin/env python3
"""check_tech_scope.py — детерминированный скоуп-чек tech-пути (шаг tech-intake).

Зачем: tech-путь держится на одном допущении — наблюдаемое поведение системы НЕ меняется
(меняется «как устроено»: версии, конфиг). Пропусти сюда фичу — и гейты пути (снимок + точный
diff) будут честно сторожить не то; пропусти баг — фикс уйдёт без RED-теста. Решение
«это техзадача, баг или фича» нельзя оставлять прозе SKILL.md: issuetype и текст приходят
детерминированно, значит и классифицирует их скрипт.

Проверяет ДВЕ стороны:
  • это техзадача? (issuetype Task/Technical debt/Chore… либо технические признаки в тексте:
    «подними версию», CVE, EOL, «поменять хост/таймаут/конфиг», dependabot/renovate);
  • это НЕ фича и НЕ баг? (Story/Epic/Bug, «добавить возможность», «не работает» — не сюда).

Usage:
    check_tech_scope.py --issue-json <file>   # JSON ответа Jira MCP (модель сохраняет в файл)
    check_tech_scope.py --issue-json -        # или из stdin
    check_tech_scope.py --text-file <file>    # свободное описание задачи (прогон без Jira)

Exit: 0 — техзадача, tech-путь подходит;
      3 — ESCALATE: не похоже на техзадачу (фича / баг / нет описания) — СТОП, спроси
          пользователя: «взять tech, fix или full?»;
      2 — ошибка входа (нечитаемый JSON/файл).
"""
from __future__ import annotations

import argparse
import json
import re
import sys

_FEATURE_ISSUETYPES = {"epic", "story", "new feature", "эпик", "история"}
_BUG_ISSUETYPES = {"bug", "defect", "баг", "дефект", "ошибка", "incident", "инцидент"}
_TECH_ISSUETYPES = {"task", "задача", "sub-task", "subtask", "подзадача", "technical debt",
                    "tech debt", "технический долг", "техдолг", "chore", "improvement",
                    "улучшение", "maintenance"}

_TECH_MARKERS = re.compile(
    r"(?:подн\w+\s+верси\w*|обнов\w+\s+(?:верси\w*|библиотек\w*|зависимост\w*|spring|java)|"
    r"\bверси\w*\s+\S+\s*(?:→|->|до|на)\s*\d|\bupgrade\b|\bbump\b|\bupdate\s+(?:to|dependenc\w+)|"
    r"\bCVE-\d{4}-\d+|\bуязвимост\w*|\bEOL\b|end\s+of\s+life|\bdependabot\b|\brenovate\b|"
    r"библиотек\w*|зависимост\w*|\bdependenc\w+|\bspring\s*boot\b|\bBOM\b|"
    r"\bхост\w*|\bhost\w*|\burl\b|\bэндпойнт\s+(?:сервиса|интеграции)|"
    r"\bтаймаут\w*|\btimeout\w*|\bконфиг\w*|\bconfig\w*|application[\w-]*\.(?:ya?ml|properties)|"
    r"\bпрофил\w+\s+(?:dev|ift|test|prod|uat|psi)|\bсвойств\w+|\bproperty\b)", re.I)

_FEATURE_MARKERS = re.compile(
    r"(?:нужно\s+добавить|добавить\s+возможност\w+|реализовать\s+нов\w+|новый\s+эндпойнт|"
    r"новая\s+функциональност\w+|новая\s+фича|новое\s+поле|новую\s+таблиц\w*|новый\s+топик|"
    r"\bnew\s+feature\b|\bimplement\s+new\b|\bas\s+a\s+user\b|\bкак\s+пользователь\b)", re.I)

_BUG_MARKERS = re.compile(
    r"(?:не\s+работает|не\s+отображ\w+|не\s+сохран\w+|падае\w+|упал\w*|некорректн\w*|"
    r"воспроизв\w+|регресс\w*|\bNPE\b|stack\s?trace|internal\s+server\s+error|\bбаг\b)", re.I)


def _extract(issue: dict) -> tuple:
    """(issuetype, summary, description) из сырого Jira REST или плоского формата."""
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else issue
    it = fields.get("issuetype")
    issuetype = (it.get("name") if isinstance(it, dict) else it) or ""
    summary = fields.get("summary") or ""
    desc = fields.get("description")
    if isinstance(desc, dict):  # ADF (Atlassian Document Format) — плоский текст из нод
        desc = json.dumps(desc, ensure_ascii=False)
        desc = " ".join(re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', desc))
    return str(issuetype), str(summary), str(desc or "")


def check_scope(issue: dict) -> list:
    """Список причин эскалации (пусто — скоуп ок для tech-пути)."""
    issuetype, summary, desc = _extract(issue)
    text = f"{summary}\n{desc}"
    it = issuetype.strip().lower()
    reasons: list = []
    if not text.strip():
        return ["нет ни summary, ни описания — нечего классифицировать"]
    if it in _FEATURE_ISSUETYPES:
        reasons.append(f"тип задачи '{issuetype}' — это фича (full-путь), а не техзадача")
    if it in _BUG_ISSUETYPES:
        reasons.append(f"тип задачи '{issuetype}' — это дефект (fix-путь), а не техзадача")
    fm = _FEATURE_MARKERS.search(text)
    if fm:
        reasons.append(f"в тексте признак новой функциональности: «{fm.group(0)}» — поведение "
                       f"меняется, это full-путь")
    bm = _BUG_MARKERS.search(text)
    if bm and it not in _TECH_ISSUETYPES:
        reasons.append(f"в тексте признак поломки: «{bm.group(0)}» — это fix-путь")
    if not _TECH_MARKERS.search(text) and it not in _TECH_ISSUETYPES:
        reasons.append("нет признаков техзадачи (версии/зависимости/CVE/конфиг/хост/таймаут) и "
                       "тип задачи не технический")
    return reasons


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue-json", help="файл с JSON issue ('-' — stdin)")
    g.add_argument("--text-file", help="файл со свободным описанием задачи")
    args = ap.parse_args()
    try:
        if args.issue_json:
            raw = sys.stdin.read() if args.issue_json == "-" else open(args.issue_json, encoding="utf-8").read()
            issue = json.loads(raw)
            if not isinstance(issue, dict):
                raise ValueError("ожидается JSON-объект issue")
        else:
            issue = {"summary": "", "description": open(args.text_file, encoding="utf-8").read()}
    except (OSError, ValueError) as e:
        print(f"[check_tech_scope] ERROR: вход нечитаем: {e}", file=sys.stderr)
        return 2
    reasons = check_scope(issue)
    if reasons:
        print("[check_tech_scope] ESCALATE: не похоже на техзадачу:\n  - " + "\n  - ".join(reasons)
              + "\n  СТОП — спроси пользователя: «взять tech, fix (баг) или full (фича)?»",
              file=sys.stderr)
        return 3
    print(json.dumps({"scope": "tech", "ok": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
