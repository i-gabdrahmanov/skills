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

Поддержка ИНВАРИАНТОВ (KIDPPRB-9254, п.3): для задач с тестами-инвариантами
(проверяют поведение, которое НЕ меняется в этой задаче — напр. константа
TASK_SERVICE_AUTO_CLOSE, оставшаяся неизменной) RED-гейт не должен валиться из-за
зелёных инвариантов. Тест считается инвариантом, если его имя матчит
`invariant_pattern` (по умолчанию 'INVARIANT' — substring/case-insensitive regex).
Включается флагом `allow_invariants=True` (из pipeline.json →
quality.red_gate.allow_invariants или из CLI). Прочие зелёные по-прежнему валят
RED-вердикт как вакуумные.

Производительность (KIDPPRB-9254, п.4): `collect()` рекурсивно обходит дерево
моно-репо (на 30+ модулях Gradle `Path.glob('**/build/test-results/**/TEST-*.xml')`
таймаутил >300 сек). Новая реализация — на `os.scandir` с ранним отсевом по имени
целевых каталогов (`build/test-results`, `target/surefire-reports`,
`target/failsafe-reports`), ограничением глубины (`max_depth=6`) и лимитом
найденных файлов (`max_files=5000` — защита от патологий, логируем warning).
Обратная совместимость: старые сигнатуры `collect(root, since=None)`,
`summarize(root, since=None)` сохранены.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Iterator

_log = logging.getLogger("junit_report")

# Gradle: <module>/build/test-results/<task>/TEST-*.xml; Maven: surefire/failsafe-reports.
# Старые паттерны — для совместимости с публичным API (вдруг кто-то зависит от константы).
REPORT_GLOBS = (
    "**/build/test-results/**/TEST-*.xml",
    "**/target/surefire-reports/TEST-*.xml",
    "**/target/failsafe-reports/TEST-*.xml",
)

# Целевые каталоги (gradle/maven test-results). Как только обходчик ВОШЁЛ в такой
# каталог, он переключается в режим «таргет-зона»: собирает TEST-*.xml в этом каталоге
# и во ВСЕХ его подкаталогах (gradle кладёт в .../test-results/<task>/TEST-*.xml).
_TARGET_DIR_NAMES: frozenset[str] = frozenset({
    "test-results",       # gradle: <module>/build/test-results/<task>/TEST-*.xml
    "surefire-reports",   # maven:  <module>/target/surefire-reports/TEST-*.xml
    "failsafe-reports",   # maven failsafe: <module>/target/failsafe-reports/TEST-*.xml
})

# Каталоги, которые нет смысла рекурсивно обходить (тяжёлые / нерелевантные).
# Эвристика — пропуск на is_dir(follow_symlinks=False): ускоряет обход на порядок.
# ВАЖНО: НЕ включать сюда 'build' и 'target' — внутри них лежат целевые каталоги
# test-results/surefire-reports/failsafe-reports; пропуск build/ целиком сломал бы gradle/maven.
_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git", ".gradle", ".idea", ".vscode", ".mvn", "node_modules",
    "__pycache__", ".pytest_cache",
    "venv", ".venv", "env",
})

# Дефолты для performance-параметров collect(). Защита от регрессий: если кто-то вызовет
# collect() без лимитов на огромном моно-репо, дефолт всё равно отработает за секунды.
# max_depth=8 покрывает base/<group>/<group>/<artifact>/build/test-results/<task>/TEST-*.xml
# (до 8 уровней в самых вложенных gradle/maven layouts). Если у вас глубже — увеличьте.
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_FILES = 5000

# Маркер инварианта по умолчанию. Совпадает с `@DisplayName("INVARIANT: ...")` /
# `// invariant: ...` в исходниках тестов. Через pipeline.json →
# quality.red_gate.invariant_pattern можно переопределить.
DEFAULT_INVARIANT_PATTERN = "INVARIANT"


def _is_target_dir(name: str) -> bool:
    """Является ли имя каталога одним из целевых test-results-каталогов.

    Используется в обходчике как маркер входа в «таргет-зону»: войдя в такой каталог, мы
    рекурсивно собираем TEST-*.xml на любой глубине внутри (gradle раскладка
    <module>/build/test-results/<task>/TEST-*.xml)."""
    return name in _TARGET_DIR_NAMES


def _walk_reports(base: Path, *, max_depth: int, max_files: int) -> Iterator[Path]:
    """Рекурсивный обход base на os.scandir с ранним отсевом.

    Алгоритм:
    1. На каждом уровне пропускаем «тяжёлые» каталоги (_SKIP_DIR_NAMES: .git, .gradle,
       node_modules, __pycache__, …).
    2. Как только обходчик ВХОДИТ в один из целевых каталогов (test-results /
       surefire-reports / failsafe-reports), включается режим «таргет-зона»:
       собираем TEST-*.xml в этом каталоге и ВСЕХ его подкаталогах.
    3. max_depth ограничивает обход ВНЕ таргет-зоны (защита от рекурсии в src/, docs/).
       Внутри таргет-зоны дополнительный запас +2 уровня (gradle <task>/каталоги внутри
       test-results). Итоговый жёсткий лимит: depth > max_depth + (2 if in_target else 0).
    4. Останавливаемся после max_files (защита от патологий).

    Выигрыш vs Path.glob('**/.../**/TEST-*.xml'): на моно-репо с 30+ модулями и 492 XML
    мы НЕ обходим src/, docs/, scripts/ и прочие «пустые» ветки (KIDPPRB-9254 п.4:
    был таймаут >300 сек, теперь <1 сек).
    """
    base = Path(base)
    if not base.is_dir():
        return

    found = 0

    def _walk(dir_path: Path, depth: int, in_target: bool) -> Iterator[Path]:
        nonlocal found
        # Жёсткий лимит обхода: depth > max_depth → return. Файлы и каталоги проверяются
        # одинаково (если мы зашли слишком глубоко — это уже не наш test-results).
        if depth > max_depth or found >= max_files:
            return
        try:
            with os.scandir(dir_path) as it:
                entries = list(it)
        except OSError:
            return

        for entry in entries:
            if found >= max_files:
                return
            try:
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_file:
                # В таргет-зоне собираем ТОЛЬКО TEST-*.xml. Снаружи — пропускаем файлы
                # (мы ищем только XML-отчёты, не исходники build/ или target/).
                if in_target:
                    name = entry.name
                    if name.startswith("TEST-") and name.endswith(".xml"):
                        found += 1
                        yield Path(dir_path) / name
                continue

            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not is_dir:
                continue
            name = entry.name
            if not in_target and name in _SKIP_DIR_NAMES:
                continue
            sub_in_target = in_target or _is_target_dir(name)
            yield from _walk(Path(dir_path) / name, depth + 1, sub_in_target)

    yield from _walk(base, 0, False)


def _matches_scope(p: Path, root: Path, scope_glob: str) -> bool:
    """Проверить, попадает ли путь p под scope_glob относительно root.

    scope_glob — glob-паттерн в стиле fnmatch ('service/taskservice', 'service/*').
    Проверяется, что КАКОЙ-ЛИБО префикс относительного пути матчит паттерн (т.е.
    scope_glob матчит один из ancestor-ов файла). Это отличается от PurePath.match(),
    который матчит справа (suffix-style), и нужно для префиксной семантики scope."""
    import fnmatch
    try:
        rel = p.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    # Проверяем все префиксы: от первого компонента до всего пути.
    for i in range(1, len(parts) + 1):
        sub = "/".join(parts[:i])
        if fnmatch.fnmatch(sub, scope_glob):
            return True
    return False


def collect(root: Path,
            since: float | None = None,
            roots: Iterable[Path] | None = None,
            *,
            max_depth: int = DEFAULT_MAX_DEPTH,
            max_files: int = DEFAULT_MAX_FILES,
            scope_glob: str | None = None) -> list[Path]:
    """JUnit XML-отчёты под root, изменённые не раньше since (None — все).

    roots — если задан, сканировать ТОЛЬКО эти каталоги (обычно модули из task-plan), а не
    весь моно-репозиторий. Means: на 50-модульном репозитории glob **/... по всему корню
    медленно и тянет чужие модули; скоуп модулей решает и производительность, и чистоту
    RED-вердикта (инварианты других модулей не попадают в зелёные).

    Новые параметры (KIDPPRB-9254, п.4) — обратная совместимость: все дефолты безопасны.

    max_depth — максимальная глубина рекурсии от base (default 6 — хватает для
        `<root>/<module>/build/test-results/<task>/TEST-*.xml`).
    max_files — потолок числа собранных файлов (default 5000). При превышении — warning
        в stderr и остановка обхода; результат всё равно полезен для RED-вердикта
        (один модуль никогда не даст 5000 файлов).
    scope_glob — fnmatch-паттерн относительно root; пропускает файлы, чей путь не
        матчит (например 'service/taskservice' → только этот модуль).
    """
    bases: list[Path] = []
    if roots:
        for r in roots:
            base = Path(r)
            if base.is_dir():
                bases.append(base)
    else:
        rb = Path(root)
        if rb.is_dir():
            bases.append(rb)

    if not bases:
        return []

    out: list[Path] = []
    seen: set[Path] = set()
    over_limit = False

    for base in bases:
        # Сначала пройдём с мягким лимитом max_files — если сработает, пометим и остановим.
        for f in _walk_reports(base, max_depth=max_depth, max_files=max_files):
            if f in seen:
                continue
            try:
                if not f.is_file():
                    continue
            except OSError:
                continue
            if scope_glob is not None:
                root_for_scope = root if isinstance(root, Path) else Path(root)
                if not _matches_scope(f, root_for_scope, scope_glob):
                    continue
            try:
                if since is not None and f.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            seen.add(f)
            out.append(f)
            if len(out) >= max_files:
                over_limit = True
                break
        if over_limit:
            break

    if over_limit:
        # Предупреждение в stderr — не падаем, RED-вердикт всё равно осмысленный
        # (один прогон не даёт 5000 XML). Дополнительно — sys.stderr.write (без
        # logging — может не быть конфигурирован).
        try:
            sys.stderr.write(
                f"junit_report.collect: превышен лимит max_files={max_files} — "
                f"обход остановлен, возможно стоит увеличить или сузить scope_glob.\n"
            )
        except Exception:
            pass
        _log.warning("junit_report.collect: max_files=%d exceeded", max_files)

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


def summarize(root: Path,
              since: float | None = None,
              roots: Iterable[Path] | None = None,
              *,
              max_depth: int = DEFAULT_MAX_DEPTH,
              max_files: int = DEFAULT_MAX_FILES,
              scope_glob: str | None = None) -> dict:
    """collect + tally одним вызовом. roots — скоуп сканирования на конкретные модули."""
    return tally(collect(root, since, roots,
                         max_depth=max_depth, max_files=max_files, scope_glob=scope_glob))


def classify_greens(t: dict, invariant_pattern: str = DEFAULT_INVARIANT_PATTERN
                    ) -> tuple[list[str], list[str], str | None]:
    """Разбить t['green'] на инварианты и вакуумные по invariant_pattern (case-insensitive regex).

    Возвращает (invariants, vacuum, error): error — описание re.error при битом паттерне,
    иначе None. Используется и red_reason(), и тестами — единая семантика
    «что считается инвариантом»."""
    try:
        inv_re = re.compile(invariant_pattern, re.IGNORECASE)
    except re.error as e:
        return [], list(t.get("green", [])), (
            f"quality.red_gate.invariant_pattern='{invariant_pattern}' — "
            f"некорректное регулярное выражение: {e}"
        )
    greens = list(t.get("green", []))
    invariants = [g for g in greens if inv_re.search(g)]
    vacuum = [g for g in greens if not inv_re.search(g)]
    return invariants, vacuum, None


def red_reason(t: dict, hint_scope: str, *,
               allow_invariants: bool = False,
               invariant_pattern: str = DEFAULT_INVARIANT_PATTERN,
               tolerate_green: bool = False) -> str | None:
    """Причина провала по-тестовой RED-проверки (None — RED чистый).

    Требования: отчёты есть; ≥1 выполненный тест; НИ ОДНОГО зелёного (вакуумного).
    hint_scope — как заскоупить прогон (синтаксис build-системы) для сообщения.

    Два режима смягчения (используются согласованно: allow_invariants — точечный,
    tolerate_green — грубый «проталкиватель override»):

    allow_invariants=True — точечное смягчение: зелёные тесты, чьё имя матчит
        invariant_pattern (case-insensitive regex), считаются ИНВАРИАНТАМИ
        (проверяют поведение, которое НЕ меняется в этой задаче — напр. KIDPPRB-9254:
        2 из 4 тестов T1 проверяли TASK_SERVICE_AUTO_CLOSE, который остался неизменным,
        и они зелёные по дизайну). ИНВАРИАНТЫ не валят RED-гейт. Все прочие зелёные
        (вакуумные — проходят без реализации) по-прежнему валят вердикт. Требуется
        ≥1 красный тест (RED). Маркер по умолчанию 'INVARIANT' — соответствует
        `@DisplayName("INVARIANT: ...")` или `// invariant: ...` в исходниках тестов.
        Конфиг: policy.json (legacy: pipeline.json) → quality.red_gate.{allow_invariants, invariant_pattern}.

    tolerate_green=True — грубое смягчение для обратной совместимости: любой зелёный
        тест пропускается, если есть ≥1 красный (использовалось, когда инварианты
        чужих модулей проталкивали override). Сейчас предпочтительнее allow_invariants:
        точнее и не «съедает» настоящие вакуумные зелёные. Если оба флага True —
        побеждает allow_invariants (точечный приоритет)."""
    if t["reports"] == 0:
        return ("прогон не оставил JUnit-отчётов (build/test-results, surefire-reports) — "
                "по-тестовая проверка RED невозможна. Команда гейта должна быть реальным "
                "тест-раннером; для не-JUnit стека — override gate-result.")
    executed = len(t["red"]) + len(t["green"])
    if executed == 0:
        return ("ни один тест не выполнился (0 testcase в свежих отчётах) — это не RED; "
                "проверь фильтр тестов.")
    if not t["green"]:
        return None

    if allow_invariants:
        # Точечный режим: отфильтровать из «вакуумных» только те зелёные, чьё имя
        # матчит invariant_pattern. Остаток по-прежнему валит.
        invariants, vacuum, perr = classify_greens(t, invariant_pattern)
        if perr:
            return perr
        if vacuum:
            names = ", ".join(vacuum[:5]) + (" …" if len(vacuum) > 5 else "")
            return (f"RED не чистый: {len(vacuum)} из {executed} выполненных тестов "
                    f"ЗЕЛЁНЫЕ — вакуумные (проходят БЕЗ реализации): {names}. "
                    f"allow_invariants=True пропускает зелёные с маркером "
                    f"'{invariant_pattern}' в имени ({len(invariants)} шт.), но эти "
                    f"зелёные НЕ помечены как инварианты. ВСЕ остальные зелёные валят "
                    f"RED: перепиши их, чтобы падали на assert'ах ещё нереализованного "
                    f"поведения, или скоупь прогон на новые тест-классы ({hint_scope}).")
        # все зелёные — инварианты; требуем ≥1 красный (RED)
        if not t["red"]:
            names = ", ".join(t["green"][:5]) + (" …" if len(t["green"]) > 5 else "")
            return (f"все тесты прогона зелёные ({len(t['green'])}, из них "
                    f"{len(invariants)} инвариантов) — нет ни одного красного; это не "
                    f"RED. Нужен хотя бы один падающий тест нового поведения "
                    f"({hint_scope}).")
        return None

    if tolerate_green:
        # есть ≥1 красный и ≥1 зелёный — но зелёные — инварианты/чужие модули: RED «чист» для
        # новых тестов (есть падающие). Проверяем, что есть хоть один красный.
        if not t["red"]:
            names = ", ".join(t["green"][:5]) + (" …" if len(t["green"]) > 5 else "")
            return (f"все тесты прогона зелёные ({len(t['green'])}) — нет ни одного красного; "
                    f"это не RED. Нужен хотя бы один падающий тест нового поведения "
                    f"({hint_scope}).")
        return None

    names = ", ".join(t["green"][:5]) + (" …" if len(t["green"]) > 5 else "")
    return (f"RED не чистый: {len(t['green'])} из {executed} выполненных тестов "
            f"ЗЕЛЁНЫЕ — вакуумные (проходят БЕЗ реализации): {names}. ВСЕ тесты "
            f"RED-прогона обязаны падать: перепиши зелёные так, чтобы они падали на "
            f"assert'ах ещё нереализованного поведения, и скоупь прогон на новые "
            f"тест-классы ({hint_scope}).")
