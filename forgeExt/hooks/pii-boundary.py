#!/usr/bin/env python3
"""pii-boundary.py — PreToolUse: не дать записать PII/секреты за пределы разрешённого scope.

PDLC v3.5: pii-boundary-check (стр. 153). Матчер: `(Write|Edit|WriteFile|NotebookEdit)` и `^Bash$`
(перехват редиректов в файл). Если в записываемом содержимом есть PII/секреты (паттерны из
risk-policy.json `pii_patterns`) И цель — вне разрешённого scope (по умолчанию репо-дерево, кроме
логов/тестов-фикстур) → deny (R3). Внутри scope — пропуск (лог оставляем хуку-логгеру).
NB: при внутренней ошибке хук fail-OPEN (не может определить цель/контент → не блокирует) —
это вторичный слой; первичную защиту рисковых путей форсит gate-guard.

«Scope»: разрешено писать PII только под `**/test*/`, `**/fixtures/`, `ground/` (рабочие данные).
Запись PII в `src/main`, конфиги, docs — блок (утечка персональных данных в код/спеку).
"""
from __future__ import annotations

import json
import re
import sys

import risk_ladder as R
import _project


def _allowed_scope(target: str) -> bool:
    """PII можно писать в тесты/фикстуры и в рабочие данные ground/."""
    if _project.is_test_path(target):
        return True
    p = (target or "").replace("\\", "/")
    segs = {s for s in p.split("/") if s}
    return "ground" in segs or "resources" in segs and _project.is_test_path(target)


def _content(tool_name: str, ti: dict) -> str:
    if tool_name in ("Edit", "edit", "NotebookEdit"):
        return str(ti.get("new_string") or ti.get("new_source") or ti.get("content") or "")
    if tool_name in ("Bash", "run_shell_command"):
        return str(ti.get("command") or "")
    # Write/WriteFile/write_file И любой нераспознанный write-подобный инструмент:
    # сканируем самые частые поля контента (fail-closed на неизвестный tool_name).
    return str(ti.get("content") or ti.get("new_string") or ti.get("text") or "")


def _target(tool_name: str, ti: dict) -> str:
    if tool_name in ("Bash", "run_shell_command"):
        cmd = str(ti.get("command") or "")
        # перенаправление/запись в файл: > >> , tee [-a], dd of=, а также inline-python
        # (open('path','w'|'a'), Path('path').write_text(...)) — иначе PII писали мимо редиректа.
        m = (re.search(r">>?\s*([\w./~-]+)", cmd)
             or re.search(r"\btee\s+(?:-a\s+)?([\w./~-]+)", cmd)
             or re.search(r"\bdd\b[^|]*\bof=([\w./~-]+)", cmd)
             or re.search(r"\bopen\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][aw]", cmd)
             or re.search(r"\bPath\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\.write_text", cmd))
        return m.group(1) if m else ""
    return str(ti.get("file_path") or ti.get("path") or ti.get("filename") or "")


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        tn = data.get("tool_name", "")
        ti = data.get("tool_input") or {}
        content = _content(tn, ti)
        if not content:
            return 0
        target = _target(tn, ti)
        # для Bash без редиректа в файл — нечего охранять
        if tn in ("Bash", "run_shell_command") and not target:
            return 0
        if target and _allowed_scope(target):
            return 0  # разрешённый scope (тесты/фикстуры/ground)

        for pat in R.load_policy().get("pii_patterns", []):
            if re.search(pat, content):
                print(f"[pii-boundary] DENY: запись PII/секрета (паттерн /{pat[:32]}…/) в "
                      f"'{target or '?'}' вне разрешённого scope. Убери ПДн или пиши в test/fixtures.",
                      file=sys.stderr)
                return 2
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
