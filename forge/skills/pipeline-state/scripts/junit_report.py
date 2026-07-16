#!/usr/bin/env python3
"""junit_report.py — детерминированный ПО-ТЕСТОВЫЙ разбор JUnit XML-отчётов.

Зачем: RED-гейты (record_gate --expect red, check_tests_red) раньше мерили «красноту»
exit-кодом прогона: ОДИН упавший тест валит весь раннер → «RED пройден», даже если
остальные новые тесты зелёные (вакуумные — проходят без реализации). Судья засчитывал
1 red + N green как успех. Правильный инвариант RED: ВСЕ выполненные тесты прогона
красные. Это можно проверить только по-тестово — из JUnit XML (Gradle test-results,
Maven surefire/failsafe), которые пишутся независимо от exit-кода и формата stdout.

Отчёты фильтруются по mtime (`since`) — берём только написанные ТЕКУЩИМ прогоном,
а не залежавшиеся от прошлых.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

# Gradle: <module>/build/test-results/<task>/TEST-*.xml; Maven: surefire/failsafe-reports.
REPORT_GLOBS = (
    "**/build/test-results/**/TEST-*.xml",
    "**/target/surefire-reports/TEST-*.xml",
    "**/target/failsafe-reports/TEST-*.xml",
)


def collect(root: Path, since: float | None = None) -> list[Path]:
    """JUnit XML-отчёты под root, изменённые не раньше since (None — все)."""
    out: list[Path] = []
    seen: set[Path] = set()
    for pat in REPORT_GLOBS:
        for f in Path(root).glob(pat):
            if f in seen or not f.is_file():
                continue
            seen.add(f)
            try:
                if since is not None and f.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            out.append(f)
    return sorted(out)


def tally(files: Iterable[Path]) -> dict:
    """Пофайловый разбор: {'reports': N, 'red': [имена], 'green': [имена], 'skipped': N}.
    Битый XML пропускается (не наш файл / оборванная запись) — консервативно не считается."""
    red: list[str] = []
    green: list[str] = []
    skipped = 0
    reports = 0
    for f in files:
        try:
            root = ET.parse(f).getroot()
        except (ET.ParseError, OSError):
            continue
        reports += 1
        for tc in root.iter("testcase"):
            name = f"{tc.get('classname', '?')}.{tc.get('name', '?')}"
            if tc.find("skipped") is not None:
                skipped += 1
            elif tc.find("failure") is not None or tc.find("error") is not None:
                red.append(name)
            else:
                green.append(name)
    return {"reports": reports, "red": red, "green": green, "skipped": skipped}


def summarize(root: Path, since: float | None = None) -> dict:
    """collect + tally одним вызовом."""
    return tally(collect(root, since))


def red_reason(t: dict, hint_scope: str) -> str | None:
    """Причина провала по-тестовой RED-проверки (None — RED чистый).

    Требования: отчёты есть; ≥1 выполненный тест; НИ ОДНОГО зелёного.
    hint_scope — как заскоупить прогон (синтаксис build-системы) для сообщения."""
    if t["reports"] == 0:
        return ("прогон не оставил JUnit-отчётов (build/test-results, surefire-reports) — "
                "по-тестовая проверка RED невозможна. Команда гейта должна быть реальным "
                "тест-раннером; для не-JUnit стека — override gate-result.")
    executed = len(t["red"]) + len(t["green"])
    if executed == 0:
        return ("ни один тест не выполнился (0 testcase в свежих отчётах) — это не RED; "
                "проверь фильтр тестов.")
    if t["green"]:
        names = ", ".join(t["green"][:5]) + (" …" if len(t["green"]) > 5 else "")
        return (f"RED не чистый: {len(t['green'])} из {executed} выполненных тестов "
                f"ЗЕЛЁНЫЕ — вакуумные (проходят БЕЗ реализации): {names}. ВСЕ тесты "
                f"RED-прогона обязаны падать: перепиши зелёные так, чтобы они падали на "
                f"assert'ах ещё нереализованного поведения, и скоупь прогон на новые "
                f"тест-классы ({hint_scope}).")
    return None
