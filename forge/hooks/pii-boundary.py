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

Мок-данные макета (forge-ui, фронт-репо; задача 3.4). Макет несёт реалистичные тексты (имена, телефоны, почты), а
бриф `ui-build` велит класть их в `mocks.ts` модуля. Без исключения хук резал бы ровно ту запись, которую требует
пайплайн. Исключение узкое: запись только в `<project.outputDir>/**/mocks.ts` и `<project.outputDir>/**/__mocks__/`,
активный прогон — `forgeui` (трек = ключ стори), `.daiver/config.yaml` на месте и проходит проверку, и КАЖДАЯ PII-подобная строка записи
дословно есть в текстах канона трека (`daiver.texts.mockup_texts`). Любое «нет» — движка нет (бэк-профиль), трека, конфига
или канона, мок-файл с чужой строкой, цель не мок-файл — хук ведёт себя ровно как раньше. FAIL-CLOSED на самом исключении:
сбой сверки (ошибка движка, нечитаемый канон) исключения не даёт — запись блокируется, а не пропускается молча
(общий `except` хука fail-OPEN, поэтому вся сверка спрятана в `_mockup_verdict`, который сбой превращает в отказ).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

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


def _luhn_ok(digits: str) -> bool:
    """Контрольная сумма Луна — её сходится у каждого настоящего номера карты."""
    if not digits:
        return False
    total, double = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if double:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        double = not double
    return total % 10 == 0


# ── мок-данные макета (forge-ui; задача 3.4) ──────────────────────────────────────────────────────
# Исключение для записи в мок-файлы модуля — см. докстринг модуля. Всё, что может упасть (движок, конфиг, канон), живёт
# внутри `_mockup_verdict`; вызывающий код дополнительно ловит и сам вызов: потерянное имя функции — тоже отказ.
_ENGINE_DIR = Path(__file__).resolve().parents[1] / "skills" / "forgeui" / "scripts"
_UI_SKILL = "forgeui"
_MOCK_FILE = "mocks.ts"
_MOCK_DIR = "__mocks__"
_MOCK_ONLY_HINT = ("Тексты макета (в том числе похожие на ПДн) в прогоне forge-ui допустимы ТОЛЬКО в mocks.ts или __mocks__/ "
                   "модуля и только дословно из макета: перенеси их туда и импортируй.")


def _pii_hits(content: str, policy: dict) -> list:
    """ВСЕ PII-подобные строки текста — а не первое совпавшее правило, как в проверке `main`: исключению для мок-данных нужен
    весь список. Паттерны секретов и контактов; номера карт — только с сошедшейся суммой Луна."""
    hits = []
    for pat in policy.get("pii_patterns", []):
        hits.extend(m.group(0) for m in re.finditer(pat, content))
    for pat in policy.get("pii_luhn_patterns", []):
        hits.extend(m.group(0) for m in re.finditer(pat, content) if _luhn_ok(re.sub(r"\D", "", m.group(0))))
    return hits


def _out_dirs(root: Path, out_dir: str) -> list:
    """outputDir конфига и его раскрытый вид (каталог может быть ссылкой): posix от корня репо, без регистра (macOS и Windows
    регистр не различают). outputDir «.» (весь репо) не совпадёт ни с одним путём — исключению там делать нечего."""
    names = {out_dir.replace("\\", "/").strip("/")}
    try:
        names.add((root / out_dir).resolve().relative_to(root.resolve()).as_posix())
    except (ValueError, OSError, RuntimeError):
        pass
    return sorted(n.casefold() for n in names if n)


def _under_out(root: Path, cwd: str, target: str, outs: list):
    """Сегменты пути цели (без регистра) относительно корня репо, если цель внутри outputDir; иначе None."""
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = Path(cwd or root) / p
    try:
        rel = p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError, RuntimeError):
        return None
    low = rel.casefold()
    return low.split("/") if any(low.startswith(o + "/") for o in outs) else None


def _mockup_verdict(data: dict, guarded: list, content: str, policy: dict):
    """Исключение для мок-данных макета. True — действует (запись пропускается); False — не применимо (хук ведёт себя как
    раньше, без пояснений); строка — применимо, но не действует (отказ с этой причиной в тексте).

    Действует только если: есть движок forge-ui; активный прогон — `forgeui` (трек = ключ стори); `.daiver/config.yaml` на
    месте; КАЖДАЯ охраняемая цель — `mocks.ts` или файл в `__mocks__/` под `project.outputDir`; КАЖДАЯ PII-подобная строка
    записи дословно есть в текстах канона трека. Любой сбой (движок, конфиг, нечитаемый канон) — не пропуск: False или
    причина, и запись блокируется."""
    try:
        if not guarded or not _ENGINE_DIR.is_dir():
            return False
        cwd = str(data.get("cwd") or "")
        root = Path(R.project_root(cwd))
        manifest = R.active_manifest(root)
        if not manifest or manifest.parent.parent.name != _UI_SKILL:
            return False
        track = manifest.parent.name
        if str(_ENGINE_DIR) not in sys.path:
            sys.path.insert(0, str(_ENGINE_DIR))
        import importlib
        CFG = importlib.import_module("daiver.config")
        TX = importlib.import_module("daiver.texts")
        cfg = CFG.load(root)
        if not cfg.get("initialized"):
            return False                      # конфига нет или он не проходит проверку: outputDir в нём — дефолт, а не слово команды
        outs = _out_dirs(root, str(cfg["config"]["project"]["outputDir"]))
        parts = [_under_out(root, cwd, t, outs) for t in guarded]
        if any(p is None for p in parts):
            return False                      # цель вне модуля: исключения нет, хук как раньше
        if not all(p[-1] == _MOCK_FILE or _MOCK_DIR in p[:-1] for p in parts):
            return _MOCK_ONLY_HINT            # код модуля с ПДн-подобным текстом: подсказка, куда его положить
        hits = _pii_hits(content, policy)
        if not hits:
            return False
        try:
            texts = TX.mockup_texts(root, track)
        except TX.TextsError as e:
            return f"тексты макета не получены ({str(e)[:160]})"
        missing = TX.unmatched(texts, hits)
        if missing:
            return (f"{len(missing)} из {len(set(hits))} PII-подобных строк нет дословно в текстах макета "
                    f"(мок-данные берутся из макета как есть, не выдумываются)")
        return True
    except Exception:  # noqa: BLE001 — сбой сверки = исключения нет (fail-closed), а не молчаливый пропуск
        return False


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
        policy = R.load_policy()
        # Заведомо не ПДн (UUID, git@host:, зарезервированные example-домены) — вырезаем до
        # сканирования, иначе пример в спеке выглядит утечкой (см. _pii_safe_note в политике).
        for pat in policy.get("pii_safe_patterns", []):
            content = re.sub(pat, " ", content)
        hit = next((pat for pat in policy.get("pii_patterns", []) if re.search(pat, content)),
                   None)
        # Номер карты — только с сошедшейся контрольной суммой Луна: голое 13–19-значное число
        # (epoch-millis в JSON/yml) иначе было DENY с диагнозом «карта».
        hit = hit or next((pat for pat in policy.get("pii_luhn_patterns", [])
                           if any(_luhn_ok(re.sub(r"\D", "", m.group(0)))
                                  for m in re.finditer(pat, content))), None)
        if hit:
            verdict = False
            if guarded:
                try:
                    verdict = _mockup_verdict(data, guarded, content, policy)
                except Exception:  # noqa: BLE001 — и потерянное имя функции: исключения нет, запись блокируется
                    verdict = False
                if verdict is True:
                    return 0  # мок-данные макета: каждая строка дословно из канона трека
            note = f" {verdict}" if isinstance(verdict, str) else ""
            print(f"[pii-boundary] DENY: запись PII/секрета (паттерн /{hit[:32]}…/) в "
                  f"'{guarded[0] if guarded else '?'}' вне разрешённого scope. "
                  f"Убери ПДн или пиши в test/fixtures. Пример в спеке/доке — на "
                  f"зарезервированном домене (user@example.com), телефон — маской "
                  f"(+7 (XXX) XXX-XX-XX), секрет — плейсхолдером (${{DB_PASSWORD}}).{note}",
                  file=sys.stderr)
            return 2
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
