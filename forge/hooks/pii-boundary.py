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
import os
import re
import sys

# Импорт форж-модулей — fail-CLOSED. Крэш на импорте отдаёт exit 1, а блокировка —
# exit 2; рантайм читает exit 1 как «хук не возражает» и ВЫПОЛНЯЕТ вызов. Так уже молча
# выключался весь enforcement (PEP 604 без future-импорта в _project.py под python 3.9 —
# на КАЖДОМ tool-call, без единой строки пользователю). Несобранный бандл обязан быть
# громким отказом, а не тишиной. Инвариант «пол интерпретатора» держит test_python_floor.py.
try:
    import risk_ladder as R
    import _project
except Exception as _e:  # pragma: no cover — сломанный бандл/интерпретатор
    # Форточка на команды ВОССТАНОВЛЕНИЯ: сплошной deny запирал и починку бандла
    # (баннер советовал `bash .gigacode/deploy-local.sh`, а матчер ^Bash$ её же и резал).
    # _failclosed — stdlib-only; если не грузится и он, поведение прежнее (exit 2).
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[pii-boundary] DENY: бандл forge не грузится ({_e}). Проверь интерпретатор в "
              f".gigacode/settings.json (нужен python 3.9+ с рабочим expat), затем "
              f"перезапусти: bash .gigacode/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("pii-boundary", _e))


def _allowed_scope(target: str) -> bool:
    """PII можно писать в тесты/фикстуры и в рабочие данные ground/."""
    if _project.is_test_path(target):
        return True
    p = (target or "").replace("\\", "/")
    segs = {s for s in p.split("/") if s}
    # Скобки обязательны: `A or B and C` связывает `and` сильнее, и ветка resources была
    # мертва (is_test_path выше уже вернул False, поэтому второй операнд всегда False).
    return "ground" in segs or ("resources" in segs and _project.is_test_path(target))


def _content(tool_name: str, ti: dict) -> str:
    if tool_name in ("Edit", "edit", "NotebookEdit"):
        return str(ti.get("new_string") or ti.get("new_source") or ti.get("content") or "")
    if tool_name in ("Bash", "run_shell_command"):
        return str(ti.get("command") or "")
    # Write/WriteFile/write_file И любой нераспознанный write-подобный инструмент:
    # сканируем самые частые поля контента (fail-closed на неизвестный tool_name).
    return str(ti.get("content") or ti.get("new_string") or ti.get("text") or "")


# Пути в командной строке. Класс символов включает `@` и `+`: без них цель обрезалась на
# первом же `@` — а корень проекта у пользователя вида `/home/work/<таб-номер>@<домен>/code/…`
# вполне обычен. Обрезанный путь ломал ОБЕ проверки: scope считался по огрызку
# ('/home/work/22269498'), и запись в docs/ репо выглядела записью «вне scope».
_PATH_CHARS = r"[\w./~@+=-]+"

# Сток, в который писать безопасно по определению: содержимое никуда не попадает.
# `2>/dev/null` — вообще не запись контента, а глушилка stderr, и она стояла в КАЖДОЙ
# второй команде разведки.
_NULL_SINKS = {"/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "nul", "NUL"}


def _targets(tool_name: str, ti: dict) -> list[str]:
    """Все цели записи. Список, а не первое совпадение: в `cmd 2>/dev/null > out.txt`
    первым шёл /dev/null, и настоящая цель (out.txt) не проверялась вовсе."""
    if tool_name in ("Bash", "run_shell_command"):
        cmd = str(ti.get("command") or "")
        # перенаправление/запись в файл: > >> , tee [-a], dd of=, а также inline-python
        # (open('path','w'|'a'), Path('path').write_text(...)) — иначе PII писали мимо редиректа.
        pats = (rf"(?<![0-9<>])>>?\s*({_PATH_CHARS})",
                rf"\btee\s+(?:-a\s+)?({_PATH_CHARS})",
                rf"\bdd\b[^|]*\bof=({_PATH_CHARS})",
                r"\bopen\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][aw]",
                r"\bPath\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\.write_text")
        out = []
        for pat in pats:
            out.extend(m.group(1) for m in re.finditer(pat, cmd))
        return [t for t in out if t not in _NULL_SINKS]
    one = str(ti.get("file_path") or ti.get("path") or ti.get("filename") or "")
    return [one] if one and one not in _NULL_SINKS else []


def _strip_infra_paths(content: str, data: dict) -> str:
    """Убрать из сканируемого текста путь к корню проекта и cwd.

    Корень проекта — инфраструктура, а не полезная нагрузка: он и так есть на диске у всех,
    кто работает в репо, «утечь» им нельзя. Но выглядеть он может как угодно — у пользователя
    это `/home/work/<таб-номер>@<домен>/code/<repo>`, и email-паттерн `pii_patterns` матчился
    на КАЖДУЮ команду с абсолютным путём: разведочный grep, mkdir, printf в docs/. Хук
    превращался в сплошной deny, не имеющий отношения к ПДн."""
    roots = []
    try:
        roots.append(str(R.project_root(data.get("cwd", ""))))
    except Exception:
        pass
    roots.append(str(data.get("cwd") or ""))
    # ТОЛЬКО абсолютные и неоднобуквенные пути. Иначе cwd="." вырезает из содержимого ВСЕ
    # точки, и `user@example.com` перестаёт совпадать с email-паттерном — то есть слепое
    # вырезание само становится дырой в детекторе.
    real = {r for r in roots if r and os.path.isabs(r) and len(r) > 3}
    for root in sorted(real, key=len, reverse=True):
        content = content.replace(root, "").replace(root.replace("\\", "/"), "")
    return content


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
        targets = _targets(tn, ti)
        # для Bash без редиректа в файл — нечего охранять
        if tn in ("Bash", "run_shell_command") and not targets:
            return 0
        guarded = [t for t in targets if not _allowed_scope(t)]
        if targets and not guarded:
            return 0  # все цели в разрешённом scope (тесты/фикстуры/ground)

        content = _strip_infra_paths(content, data)
        for pat in R.load_policy().get("pii_patterns", []):
            if re.search(pat, content):
                print(f"[pii-boundary] DENY: запись PII/секрета (паттерн /{pat[:32]}…/) в "
                      f"'{guarded[0] if guarded else '?'}' вне разрешённого scope. "
                      f"Убери ПДн или пиши в test/fixtures.",
                      file=sys.stderr)
                return 2
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
