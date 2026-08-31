#!/usr/bin/env python3
"""destructive-blocker.py — PreToolUse `^Bash$`: блок деструктивных команд (PDLC v3.5, стр. 152).

deny-first: чёрный список из risk-policy.json (`destructive_blacklist`) — `rm -rf /`, force-push
в main, DROP/TRUNCATE, chmod 777, fork-bomb, `curl | sh` и т.п. Совпадение → exit 2.
Не зависит от пайплайна: эти команды опасны всегда. Никогда не пропускает при совпадении.
"""
from __future__ import annotations

import json
import re
import sys

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

_CORE_BLACKLIST = [
    r"\brm\b(?:\s+(?:-\S+|--\w[\w-]*))*\s+(?:(?:/|~|\$HOME|\*)(?:\s|/|\*|$)|\.(?:\s|$))",  # rm <любые флаги> опасная цель (/, /*, ~, $HOME, *, бар. .) — но НЕ ./subdir
    r"\brm\b(?=.*" + _RM_RECURSIVE + r")(?=.*" + _RM_FORCE + r").*" + _DANGEROUS_TARGET,
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
# `xargs rm` цель получает из stdin, поэтому опасность определяет ПРОИЗВОДИТЕЛЬ списка.
# Прежний безусловный блок резал штатную уборку (`find . -name '*.tmp' | xargs rm`).
_XARGS_RM_RE = re.compile(r"\bxargs\b(?:\s+-\S+)*\s+rm\b")
_DANGEROUS_ROOT_RE = re.compile(_TOK + r"(?:/|~|\$HOME)\S*")


def _xargs_rm_from_dangerous_root(cmd: str) -> bool:
    if not _XARGS_RM_RE.search(cmd):
        return False
    return bool(_DANGEROUS_ROOT_RE.search(cmd.split("|")[0]))


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
        if _xargs_rm_from_dangerous_root(cmd):
            print("[destructive-blocker] DENY: `xargs rm` со списком из опасного корня "
                  "(/, ~, $HOME). Уборку делай в пределах рабочего каталога.", file=sys.stderr)
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
