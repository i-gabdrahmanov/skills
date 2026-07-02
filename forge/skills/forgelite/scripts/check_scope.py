#!/usr/bin/env python3
"""check_scope.py — детерминированный скоуп-чек lite-пути (шаг lite-jira).

Зачем: правило «останови, если Epic/Story/нет AC» в SKILL.md — проза, которую слабая модель
не держит. Issuetype и описание приходят из Jira MCP детерминированно — их проверяет скрипт.

Usage:
    check_scope.py --issue-json <file>     # JSON ответа Jira MCP (модель сохраняет в файл)
    check_scope.py --issue-json -          # или из stdin

Понимает и сырой формат Jira REST ({"fields": {...}}), и плоский ({"issuetype": "...",
"summary": "...", "description": "..."}).

Exit: 0 — задача похожа на готовую подзадачу, продолжай lite;
      3 — ESCALATE: скоуп не для lite (Epic/Story/нет AC/несколько сценариев) —
          СТОП, спроси пользователя: «продолжить в lite или взять full (feature-pipeline)?»;
      2 — ошибка входа (нечитаемый JSON).
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# Типы задач, которые lite не берёт (нужен full-путь с анализом/декомпозицией)
_BIG_ISSUETYPES = {"epic", "story", "new feature", "эпик", "история"}

# Маркеры acceptance criteria в описании
_AC_MARKERS = re.compile(
    r"(?:acceptance\s+criteria|критерии\s+приёмки|критерии\s+готовности|\bAC\b|\bКП\b|"
    r"given\s+.+when\s+.+then|дано\s+.+когда\s+.+тогда)",
    re.I | re.S,
)
_LIST_MARKERS = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\[ ?\])\s+\S", re.M)

# Слова, указывающие на скоуп больше подзадачи
_BIG_SCOPE_RE = re.compile(
    r"(?:\bbreaking\s+change\b|\bmigration\b|\bмиграци\w+|\brefactor\w*|\w*рефактор\w*|"
    r"\bredesign\b|\bпереписать\b|\bперепроектир\w+|\bновая\s+фича\b|\bnew\s+feature\b)", re.I)


def _extract(issue: dict) -> tuple[str, str, str]:
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


def check_scope(issue: dict) -> list[str]:
    """Список причин эскалации (пусто — скоуп ок)."""
    issuetype, summary, desc = _extract(issue)
    reasons = []
    if issuetype.strip().lower() in _BIG_ISSUETYPES:
        reasons.append(f"issuetype '{issuetype}' — это не готовая подзадача (нужен full-путь)")
    text = f"{summary}\n{desc}"
    if len(desc.strip()) < 40:
        reasons.append("описание пустое/слишком короткое — нет материала для acceptance criteria")
    elif not (_AC_MARKERS.search(text) or _LIST_MARKERS.search(desc)):
        reasons.append("в описании не распознаны acceptance criteria "
                       "(нет маркеров AC/критериев/списка/given-when-then)")
    m = _BIG_SCOPE_RE.search(text)
    if m:
        reasons.append(f"слово '{m.group(0)}' указывает на скоуп больше подзадачи")
    return reasons


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--issue-json", required=True, help="файл с JSON issue или '-' (stdin)")
    args = p.parse_args()

    try:
        raw = sys.stdin.read() if args.issue_json == "-" else \
            open(args.issue_json, encoding="utf-8").read()
        issue = json.loads(raw)
        if not isinstance(issue, dict):
            raise ValueError("ожидался JSON-объект issue")
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"[check_scope] ERROR: не смог прочитать issue: {e}", file=sys.stderr)
        return 2

    reasons = check_scope(issue)
    if not reasons:
        print("[check_scope] OK: задача похожа на готовую подзадачу — продолжай lite")
        return 0
    print("⛔ ESCALATE: задача не похожа на готовую подзадачу для lite:", file=sys.stderr)
    for r in reasons:
        print(f"   - {r}", file=sys.stderr)
    print("   СТОП: спроси пользователя — «продолжить в lite или взять full "
          "(feature-pipeline)?». Не решай молча.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
