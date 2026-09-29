#!/usr/bin/env python3
"""destructive-blocker.py — PreToolUse `^Bash$`: блок деструктивных команд (PDLC v3.5, стр. 152).

deny-first: чёрный список из risk-policy.json (`destructive_blacklist`) — `rm -rf /`, force-push
в main, DROP/TRUNCATE, chmod 777, fork-bomb, `curl | sh` и т.п. Совпадение → exit 2.
Не зависит от пайплайна: эти команды опасны всегда. Никогда не пропускает при совпадении.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

# Импорт форж-модулей — fail-CLOSED. Крэш на импорте отдаёт exit 1, а блокировка —
# exit 2; рантайм читает exit 1 как «хук не возражает» и ВЫПОЛНЯЕТ вызов. Так уже молча
# выключался весь enforcement (PEP 604 без future-импорта в _project.py под python 3.9 —
# на КАЖДОМ tool-call, без единой строки пользователю). Несобранный бандл обязан быть
# громким отказом, а не тишиной. Инвариант «пол интерпретатора» держит test_python_floor.py.
try:
    import risk_ladder as R
except Exception as _e:  # pragma: no cover — сломанный бандл/интерпретатор
    # Форточка на команды ВОССТАНОВЛЕНИЯ: сплошной deny запирал и починку бандла
    # (баннер советовал `bash .gigacode/deploy-local.sh`, а матчер ^Bash$ её же и резал).
    # _failclosed — stdlib-only; если не грузится и он, поведение прежнее (exit 2).
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[destructive-blocker] DENY: бандл forge не грузится ({_e}). Проверь интерпретатор в "
              f".gigacode/settings.json (нужен python 3.9+ с рабочим expat), затем "
              f"перезапусти: bash .gigacode/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("destructive-blocker", _e))

# Встроенный fail-closed CORE: проверяется ВСЕГДА (объединяется с risk-policy.json).
# Гарантирует, что при отсутствии/повреждении политики блокировщик не открывается полностью,
# и закрывает обходы (long-form флаги, rm /*, find -delete), мимо которых проходил policy-regex.
# Флаги rm через явную границу «начало строки или пробел»: у `\b-` границы НЕТ (перед '-'
# стоит пробел, оба символа не-словесные), поэтому прежние лукахеды `\b(?:-[a-z]*r[a-z]*)\b`
# не срабатывали НИКОГДА — паттерн был мёртв, и `rm -rf /etc/passwd` проходил насквозь
# (ловились только `rm -rf /` и `rm -rf ~/…`, где цель ровно корень/дом). tasks/011.
_TOK = r"(?:(?<=\s)|^)"
_RM_RECURSIVE = _TOK + r"(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)(?=\s|$)"
_RM_FORCE = _TOK + r"(?:-[a-zA-Z]*f[a-zA-Z]*|--force)(?=\s|$)"
# Опасная цель: АБСОЛЮТНЫЙ путь, домашний каталог или голая звезда. Относительная цель
# (`build/tmp`, `./target`) под рекурсивное удаление не подпадает — это штатная уборка.
_DANGEROUS_TARGET = _TOK + r"(?:/\S*|~\S*|\$HOME\S*|\*)(?=\s|$)"

# Паттерн «rm -rf по абсолютному пути» держим отдельной ссылкой: у него ЕСТЬ законное
# исключение (уборка внутри своего же проекта), у остальных — нет. См. _rm_abs_inside_project.
_RM_ABS_PATTERN = (r"\brm\b(?=.*" + _RM_RECURSIVE + r")(?=.*" + _RM_FORCE + r").*"
                   + _DANGEROUS_TARGET)

_CORE_BLACKLIST = [
    _RM_ABS_PATTERN,
    r"\bfind\s+(?:/|~|\$HOME)\S*\s.*-(?:delete|exec\s+rm)\b",  # find в опасном корне + удаление
    # force-push и в короткой форме `-f` (кластер флагов), кроме --force-with-lease
    r"\bgit\s+push\b(?=.*(?:--force\b|\s-[A-Za-z]*f))(?!.*--force-with-lease)",
    # SQL-деструктив — только в КОНТЕКСТЕ выполнения: вызов БД-клиента либо начало сегмента
    # команды. Голый матч по подстроке блокировал текст: `echo "-- DROP TABLE users" >> notes.md`.
    r"(?:\b(?:psql|mysql|mariadb|sqlite3|sqlplus|clickhouse-client|mongosh?|cqlsh|liquibase|flyway)\b[^;|&]*"
    r"|(?:^|[;&|]\s*))(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b",
    r"\bmkfs\b|\bdd\s+if=.*of=/dev/",
    r":\(\)\s*\{.*\};:",
    r"(?:curl|wget)\s+[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh",
    # обфусцированный exec: base64 -d | (ba)sh
    r"\bbase64\s+(?:-d|--decode|-D)\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b",
    # python-деструктив без токена rm: shutil.rmtree корня/дома
    r"\brmtree\s*\(\s*['\"]?(?:/|~|\$HOME)",
]

# ── Контекстные проверки (одним regex не выражаются) ─────────────────────────────────
# `rm <любые флаги> <опасная цель>` — разбором argv, не регуляркой. Прежний паттерн
#   \brm\b(?:\s+(?:-\S+|--\w[\w-]*))*\s+(?:(?:/|~|\$HOME|\*)…)
# имел двусмысленную альтернативу под `*`: `--recursive` подходит ОБЕИМ ветвям, и на не
# подошедшем хвосте движок перебирал разбиения экспоненциально. Замер на этом дереве:
# `rm --recursive`×28 → 131.8 с при таймауте хука 40 с. Таймаут рантайм читает как
# «возражений нет», то есть одна длинная строка флагов ГАСИЛА блокировщик целиком —
# ReDoS здесь не про «медленно», а про снятие enforcement. Токенизация линейна и точнее:
# `rm -rf ./build` (относительная цель) под запрет не подпадает и раньше.
_CMD_SEP_RE = re.compile(r"\|\||&&|[;|&\n]")
_BARE_DANGEROUS = frozenset((
    "/", "/*", "~", "~/*", "*", ".", "$HOME", "$HOME/*", "${HOME}", "${HOME}/*",
))


def _rm_bare_dangerous_target(cmd: str) -> bool:
    """`rm` с ГОЛОЙ опасной целью: корень, дом, звезда, текущий каталог.

    Абсолютные пути вида `/etc/passwd` ловит _RM_ABS_PATTERN (у него есть законное
    исключение «внутри своего проекта»), здесь — только цели без содержательного пути."""
    for seg in _CMD_SEP_RE.split(cmd):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:                 # незакрытая кавычка — грубая токенизация
            toks = re.findall(r"[^\s'\"]+", seg)
        if not toks or os.path.basename(toks[0]) != "rm":
            continue
        for a in toks[1:]:
            if a.startswith("-"):
                continue                   # флаг в любой форме (-rf, --recursive, --)
            if a.rstrip("/") in _BARE_DANGEROUS or a in _BARE_DANGEROUS:
                return True
    return False


# `xargs rm` цель получает из stdin, поэтому опасность определяет ПРОИЗВОДИТЕЛЬ списка.
# Прежний безусловный блок резал штатную уборку (`find . -name '*.tmp' | xargs rm`).
_XARGS_RM_RE = re.compile(r"\bxargs\b(?:\s+-\S+)*\s+rm\b")
_DANGEROUS_ROOT_RE = re.compile(_TOK + r"(?:/|~|\$HOME)\S*")


def _xargs_rm_from_dangerous_root(cmd: str) -> bool:
    if not _XARGS_RM_RE.search(cmd):
        return False
    return bool(_DANGEROUS_ROOT_RE.search(cmd.split("|")[0]))


# `rm -rf <абсолютный путь>` ВНУТРИ своего проекта — штатная уборка, а не деструктив.
# tasks/011 расширил опасную цель с «ровно / или ~» до любого абсолютного пути, чтобы ловить
# `rm -rf /etc/passwd`; побочно под блок попал `rm -rf /путь/к/проекту/build` — то, что
# gradle-разработчик набирает каждый день. Eval пинил именно это ожидание и с тех пор был
# красным. Разводим по смыслу: снаружи проекта — деструктив, внутри — уборка.
_ABS_TOKEN_RE = re.compile(_TOK + r"(/\S*)(?=\s|$)")


def _rm_abs_inside_project(cmd: str, root) -> bool:
    """Все абсолютные цели команды лежат СТРОГО внутри проекта (сам корень — не цель)."""
    if root is None:
        return False                      # корень не резолвится → исключение не выдаём
    try:
        root = Path(os.path.normpath(str(Path(root).expanduser()))).resolve()
    except (OSError, ValueError):
        return False
    targets = _ABS_TOKEN_RE.findall(cmd)
    if not targets:
        return False
    for t in targets:
        if "*" in t or "?" in t:          # глоб внутри проекта — цель неизвестна до раскрытия
            return False
        try:
            # resolve с ОБЕИХ сторон: иначе /var vs /private/var (симлинк macOS) разводит
            # корень и цель по разным деревьям, и уборка своего же build выглядит внешней.
            # Заодно симлинк изнутри проекта наружу честно резолвится наружу и блокируется.
            p = Path(os.path.normpath(t)).resolve()
        except (OSError, ValueError):
            return False
        if p == root or root not in p.parents:
            return False
    return True


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return 0
        # `git -C <p> push --force`/`git -c k=v push -f` обходили force-push-паттерны
        # (детект по `git\s+push`). Матчим по нормализованной команде (исполняется исходная).
        try:
            cmd = R.normalize_git_command(cmd)
        except Exception:
            pass
        policy = []
        try:
            policy = R.load_policy().get("destructive_blacklist", []) or []
        except Exception:
            policy = []
        if _rm_bare_dangerous_target(cmd):
            print("[destructive-blocker] DENY: `rm` с целью «корень/дом/звезда/текущий "
                  "каталог». Уборку делай по конкретному относительному пути "
                  "(`rm -rf build/`), а не по `/`, `~`, `*` или `.`.", file=sys.stderr)
            return 2
        if _xargs_rm_from_dangerous_root(cmd):
            print("[destructive-blocker] DENY: `xargs rm` со списком из опасного корня "
                  "(/, ~, $HOME). Уборку делай в пределах рабочего каталога.", file=sys.stderr)
            return 2
        exempt_rm_abs = False
        if re.search(_RM_ABS_PATTERN, cmd, re.I):
            try:
                # R.project_root — тот же резолвер, что у остальных хуков (_project.find_project_root
                # с git-фолбэком): «внутри проекта» обязано значить то же самое везде.
                exempt_rm_abs = _rm_abs_inside_project(cmd, R.project_root(data.get("cwd") or ""))
            except Exception:  # noqa: BLE001 — резолвер корня не ответил: остаёмся строгими
                exempt_rm_abs = False
        for pat in list(policy) + _CORE_BLACKLIST:
            if pat is _RM_ABS_PATTERN and exempt_rm_abs:
                continue
            if re.search(pat, cmd, re.I):
                print(f"[destructive-blocker] DENY: команда совпала с запретом /{pat}/. "
                      "Деструктивное действие заблокировано.", file=sys.stderr)
                return 2
    except Exception:
        return 0  # сам блокировщик не должен ронять прогон (но при совпадении — блок выше)
    return 0


if __name__ == "__main__":
    sys.exit(main())
