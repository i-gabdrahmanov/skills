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
# Рекурсивный `rm` по абсолютному пути/дому здесь не регуляркой, а разбором argv — см.
# _rm_recursive_dangerous_targets.
_TOK = r"(?:(?<=\s)|^)"

_CORE_BLACKLIST = [
    # find в опасном корне + удаление; -execdir/-ok/-okdir — те же rm (боевой прогон v0.4.6)
    r"\bfind\s+(?:/|~|\$HOME)\S*\s.*-(?:delete\b|(?:exec|execdir|ok|okdir)\s+(?:\S*/)?rm\b)",
    # force-push и в короткой форме `-f` (кластер флагов), кроме --force-with-lease
    r"\bgit\s+push\b(?=.*(?:--force\b|\s-[A-Za-z]*f))(?!.*--force-with-lease)",
    # SQL-деструктив — только в КОНТЕКСТЕ выполнения: вызов БД-клиента либо начало сегмента
    # команды. Голый матч по подстроке блокировал текст: `echo "-- DROP TABLE users" >> notes.md`.
    r"(?:\b(?:psql|mysql|mariadb|sqlite3|sqlplus|clickhouse-client|mongosh?|cqlsh|liquibase|flyway)\b[^;|&]*"
    r"|(?:^|[;&|]\s*))(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b",
    # dd на блочное устройство — в любом порядке операндов (`dd of=/dev/sda if=x.iso` проходил,
    # L-3); безобидные приёмники /dev/null|zero|stdout|stderr — не цель.
    r"\bmkfs\b|\bdd\b[^|;&]*\bof=/dev/(?!(?:null|zero|stdout|stderr|fd/)\b)",
    r":\(\)\s*\{.*\};:",
    r"(?:curl|wget)\s+[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh",
    # обфусцированный exec: base64 -d | (ba)sh
    r"\bbase64\s+(?:-d|--decode|-D)\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b",
    # python-деструктив без токена rm: shutil.rmtree корня/дома
    r"\brmtree\s*\(\s*['\"]?(?:/|~|\$HOME)",
    # chmod с правами «всем всё»: 777/0777/7777 и a+rwx/+rwx, флаги в любой форме. Policy-строка
    # `chmod\s+-R?\s*777` требовала дефис — голый `chmod 777 /etc` проходил (боевой прогон, A2),
    # а в ядре chmod не было вовсе, хотя докстринг его обещал.
    r"\bchmod\b(?:\s+--?[A-Za-z][\w-]*)*\s+(?:[0-7]?777\b|(?:a|ugo)?[+=]rwx\b)",
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


_SHELLS = ("sh", "bash", "zsh", "dash", "ksh")


def _segment_tokens(cmd: str, depth: int = 0) -> "list[list[str]]":
    """argv по сегментам оболочки (;, &&, ||, |, &, перевод строки); кавычки сняты shlex'ом.

    `bash -c '<скрипт>'` / `sh -c` / `eval` раскрываются на один уровень: скрипт приходил одним
    токеном, и `bash -c 'rm -rf /etc/services'`, `sudo bash -c …` проходили блокировщик
    (боевой прогон v0.4.6, E-08)."""
    out = []
    for seg in _CMD_SEP_RE.split(cmd):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:                 # незакрытая кавычка — грубая токенизация
            toks = re.findall(r"[^\s'\"]+", seg)
        if not toks:
            continue
        out.append(toks)
        if depth:
            continue
        i = next((k for k, t in enumerate(toks) if os.path.basename(t) in _SHELLS), None)
        if i is not None and "-c" in toks[i + 1:]:
            j = toks.index("-c", i + 1)
            if j + 1 < len(toks):
                out += _segment_tokens(toks[j + 1], depth + 1)
        elif os.path.basename(toks[0]) == "eval" and len(toks) > 1:
            out += _segment_tokens(" ".join(toks[1:]), depth + 1)
    return out


def _rm_bare_dangerous_target(cmd: str) -> bool:
    """`rm` с ГОЛОЙ опасной целью: корень, дом, звезда, текущий каталог.

    Абсолютные пути вида `/etc/passwd` ловит _rm_recursive_dangerous_targets (у неё есть
    законное исключение «внутри своего проекта»), здесь — только цели без содержательного пути."""
    for toks in _segment_tokens(cmd):
        i = next((k for k, t in enumerate(toks) if os.path.basename(t) == "rm"), None)
        if i is None:
            continue                       # rm — и за sudo/env/nice, как в рекурсивной проверке
        for a in toks[i + 1:]:
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


# Рекурсивный `rm` по абсолютному пути / дому — разбором argv, как и голые цели выше.
# Прежняя регулярка требовала ОДНОВРЕМЕННО флаг рекурсии и флаг force и видела цель только
# без кавычек: `rm -r /etc`, `rm --recursive /etc`, `rm -rf "/etc/passwd"`, `rm -rf ${HOME}/x`,
# `rm -rf \<перевод строки> /etc/passwd` проходили насквозь (боевой прогон, A1/A7). shlex
# снимает кавычки, флаги читаются как флаги, а не подстроки.
_HOME_FORMS = ("~", "$HOME", "${HOME}")


def _is_dangerous_root(target: str) -> bool:
    return target.startswith("/") or any(target == h or target.startswith(h + "/")
                                         for h in _HOME_FORMS)


def _rm_recursive_dangerous_targets(cmd: str) -> "list[str]":
    """Цели РЕКУРСИВНОГО `rm` с опасным корнем (абсолютный путь, ~, $HOME, ${HOME}).

    `rm` ищется в любой позиции сегмента — за `sudo`, `env`, `nice` и прочими обёртками,
    как прежний `\brm\b`. Нерекурсивный `rm` файла сюда не попадает: удаление своего
    временного файла в /tmp — штатная работа."""
    out = []
    for toks in _segment_tokens(cmd):
        i = next((k for k, t in enumerate(toks) if os.path.basename(t) == "rm"), None)
        if i is None:
            continue
        recursive, targets, opts = False, [], True
        for a in toks[i + 1:]:
            if opts and a == "--":
                opts = False
            elif opts and a.startswith("--"):
                recursive = recursive or a == "--recursive"
            elif opts and a.startswith("-") and len(a) > 1:
                recursive = recursive or "r" in a or "R" in a
            else:
                targets.append(a)
        if recursive:
            out += [t for t in targets if _is_dangerous_root(t)]
    # `rsync --delete SRC/ DST/` стирает в DST всё, чего нет в SRC: перепутанные операнды —
    # классика «пустой каталог поверх /etc» (E-15). Цель — DST, правила те же, что у rm -r.
    for toks in _segment_tokens(cmd):
        i = next((k for k, t in enumerate(toks) if os.path.basename(t) == "rsync"), None)
        if i is None or not any(t.startswith("--delete") or t == "--del" for t in toks[i + 1:]):
            continue
        files = [t for t in toks[i + 1:] if not t.startswith("-")]
        if len(files) >= 2 and _is_dangerous_root(files[-1]):
            out.append(files[-1])
    return out


# Рекурсивный `rm` ВНУТРИ своего проекта — штатная уборка, а не деструктив.
# tasks/011 расширил опасную цель с «ровно / или ~» до любого абсолютного пути, чтобы ловить
# `rm -rf /etc/passwd`; побочно под блок попал `rm -rf /путь/к/проекту/build` — то, что
# gradle-разработчик набирает каждый день. Разводим по смыслу: снаружи проекта — деструктив,
# внутри — уборка.
# Корень, внутри которого рекурсивный rm — «уборка», обязан быть ПРОЕКТОМ. Резолвер без
# маркеров возвращает сам cwd, и сессия, запущенная из `/` (headless `gigacode -p` из корня),
# получала корень `/`: `rm -rf /usr` выглядел уборкой внутри проекта и проходил все хуки
# (боевой прогон v0.4.6, E-CWD-ROOT). Так же — дом пользователя и каталог без маркеров.
_PROJECT_MARKERS = (".git", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
                    "settings.gradle.kts", "ground")


def _is_real_project(root: Path) -> bool:
    if root == Path(root.anchor):
        return False
    try:
        if root == Path.home().resolve():
            return False
    except (OSError, RuntimeError):
        pass
    return any((root / m).exists() for m in _PROJECT_MARKERS)


def _targets_inside_project(targets: "list[str]", root) -> bool:
    """Все цели лежат СТРОГО внутри проекта (сам корень — не цель)."""
    if root is None or not targets:
        return False                      # корень не резолвится → исключение не выдаём
    try:
        root = Path(os.path.normpath(str(Path(root).expanduser()))).resolve()
    except (OSError, ValueError):
        return False
    if not _is_real_project(root):
        return False                      # «/», дом, каталог без маркеров — не проект
    home = os.path.expanduser("~")
    for t in targets:
        if "*" in t or "?" in t:          # глоб внутри проекта — цель неизвестна до раскрытия
            return False
        for h in ("${HOME}", "$HOME"):
            if t == h or t.startswith(h + "/"):
                t = home + t[len(h):]
                break
        try:
            # resolve с ОБЕИХ сторон: иначе /var vs /private/var (симлинк macOS) разводит
            # корень и цель по разным деревьям, и уборка своего же build выглядит внешней.
            # Заодно симлинк изнутри проекта наружу честно резолвится наружу и блокируется.
            p = Path(os.path.normpath(os.path.expanduser(t))).resolve()
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
        # Перенос строки через `\` для оболочки — пробел; без склейки цель на второй строке
        # уходила в отдельный сегмент, и `rm -rf \<NL> /etc` выглядел как `rm -rf` без цели.
        cmd = cmd.replace("\\\n", " ")
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
        rm_targets = _rm_recursive_dangerous_targets(cmd)
        if rm_targets:
            try:
                # R.project_root — тот же резолвер, что у остальных хуков (_project.find_project_root
                # с git-фолбэком): «внутри проекта» обязано значить то же самое везде.
                inside = _targets_inside_project(rm_targets, R.project_root(data.get("cwd") or ""))
            except Exception:  # noqa: BLE001 — резолвер корня не ответил: остаёмся строгими
                inside = False
            if not inside:
                print(f"[destructive-blocker] DENY: рекурсивный `rm` вне проекта: "
                      f"{' '.join(rm_targets)[:200]}. Уборку делай внутри проекта "
                      f"(`rm -rf build/`); чужие каталоги не трогай.", file=sys.stderr)
                return 2
        for pat in list(policy) + _CORE_BLACKLIST:
            if re.search(pat, cmd, re.I):
                print(f"[destructive-blocker] DENY: команда совпала с запретом /{pat}/. "
                      "Деструктивное действие заблокировано.", file=sys.stderr)
                return 2
    except Exception:
        return 0  # сам блокировщик не должен ронять прогон (но при совпадении — блок выше)
    return 0


if __name__ == "__main__":
    sys.exit(main())
