#!/usr/bin/env python3
"""human_override.py — команда forge от имени ЧЕЛОВЕКА: снимает R4-ограничения на один вызов.

    python3 .gigacode/extensions/forge/hooks/human_override.py -- config set quality.tdd false
    python3 .gigacode/extensions/forge/hooks/human_override.py --reason "<почему>" -- config repin --feature KID-1

Зачем. Параметры enforcement (quality.*, security.*, autonomy.mode, repin, ослабление фаз и
risk-политики) меняются только по согласию человека: хуки режут вызов модели, а второй слой
(config.py, rollback.py, archive.py) требует маркер в ground/approvals.jsonl. Слабая модель
этот протокол не проходит — и параметр не меняется вовсе, даже когда человек согласен.

Что делает. Человек подтверждает команду в СВОЁМ терминале кодом, который печатается на
/dev/tty. Дальше скрипт запускает команду; если она просит согласие (`approval_key` в JSON
или `record_approval.py --key …` в подсказке) — выписывает его от имени человека и повторяет
вызов; просит `--confirm` — добавляет. После вызова все выписанные и не потраченные согласия
отзываются: активный маркер gate-guard засчитал бы и модели, и согласие на один вызов стало
бы постоянным разрешением. Выдача и отзыв лежат в журнале согласий с `via: human_override`.

Почему модель его не запустит. Три слоя, каждый закрывает своё:
  1. Окружение агента. Shell-инструмент qwen/GigaCode ставит дочернему процессу QWEN_CODE=1
     (оба режима: pipe и pty; проверено по бандлу qwen-code). Есть маркер агента — отказ,
     в том числе для `!` в сессии: запускать в отдельном терминале.
  2. Терминал. Код читается из /dev/tty, а не из stdin: `echo КОД |` и `yes |` не проходят.
     Без управляющего терминала (pipe-режим агента запускает команду detached, stdin=ignore)
     /dev/tty не открывается — отказ.
  3. Хук. destructive-blocker режет любую shell-команду модели, где упомянут этот скрипт.
Ни один слой сам по себе не абсолютен (модель с shell может снять переменную или собрать pty
через `script`/`expect`), но обход каждого — намеренная подделка, а не ошибка слабой модели.

Exit: код команды · 4 — запуск не человеком / нет терминала · 5 — подтверждение не дано.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import select
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

# Переменные, которые рантаймы агентов ставят своим shell-командам. QWEN_CODE — qwen-code и
# форки на нём (GigaCode); GEMINI_CLI — апстрим; CLAUDECODE — Claude Code.
AGENT_ENV = ("QWEN_CODE", "GIGACODE", "GIGA_CODE", "GEMINI_CLI", "CLAUDECODE")

# Короткие имена скриптов forge, которые человеку приходится звать руками.
ALIASES = {
    "config": "skills/config-helper/scripts/config.py",
    "rollback": "skills/pipeline-state/scripts/rollback.py",
    "archive": "skills/pipeline-state/scripts/archive.py",
}

_KEY_JSON = re.compile(r'"approval_key"\s*:\s*"([^"]+)"')
_KEY_HINT = re.compile(r"record_approval\.py\s+--key\s+([^\s\"'`]+)")
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # без 0/O, 1/I/L — код перепечатывают глазами
MAX_ROUNDS = 6
TIMEOUT_S = 180


def agent_marker(env) -> "str | None":
    for k in AGENT_ENV:
        if (env.get(k) or "").strip():
            return k
    return None


def resolve_command(argv: "list[str]") -> "list[str]":
    """Алиас или путь скрипта → argv для subprocess (.py — тем же интерпретатором)."""
    if not argv:
        raise SystemExit("✗ после -- нужна команда forge, например: -- config set quality.tdd false")
    head, rest = argv[0], argv[1:]
    script = HOOKS.parent / ALIASES[head] if head in ALIASES else Path(head)
    if script.suffix == ".py":
        if not script.is_file():
            raise SystemExit(f"✗ скрипта нет: {script}")
        return [sys.executable, "-X", "utf8", str(script)] + rest
    return [head] + rest


def wanted_keys(output: str) -> "list[str]":
    """Согласия, которые команда просит, — в порядке появления, без повторов."""
    out: "list[str]" = []
    for m in list(_KEY_JSON.finditer(output)) + list(_KEY_HINT.finditer(output)):
        k = m.group(1).strip()
        if k and k not in out:
            out.append(k)
    return out


class Terminal:
    """/dev/tty двумя потоками: текстовый `r+` на терминале падает (поток не seekable)."""

    def __init__(self) -> None:
        self._r = open("/dev/tty", "r", encoding="utf-8", errors="replace")
        try:
            self._w = open("/dev/tty", "w", encoding="utf-8")
        except OSError:
            self._r.close()
            raise

    def write(self, s: str) -> None:
        self._w.write(s)

    def flush(self) -> None:
        self._w.flush()

    def readline(self) -> str:
        return self._r.readline()

    def fileno(self) -> int:
        return self._r.fileno()

    def close(self) -> None:
        self._r.close()
        self._w.close()


def _ask(tty, prompt: str, timeout: "float | None") -> str:
    tty.write(prompt)
    tty.flush()
    if timeout is not None:
        try:
            ready, _, _ = select.select([tty], [], [], timeout)
        except (ValueError, OSError):
            ready = [tty]                    # не файл с дескриптором (тест) — читаем как есть
        if not ready:
            tty.write("\n")
            return ""
    return (tty.readline() or "").strip()


def confirm(tty, cmd: "list[str]", reason: str, project: Path,
            timeout: "float | None" = TIMEOUT_S) -> bool:
    code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    tty.write("\n=== forge: команда от имени человека ===\n"
              f"  проект:   {project}\n"
              f"  команда:  {' '.join(cmd)}\n"
              f"  причина:  {reason}\n"
              "  Согласия, которые команда попросит, будут выписаны от твоего имени на этот\n"
              "  вызов и отозваны после него. Если это предложил агент — проверь команду.\n"
              f"  Код подтверждения: {code}\n")
    got = _ask(tty, "  Введи код (Enter — отмена): ", timeout)
    return got.upper() == code


def _run(cmd: "list[str]", project: Path) -> "tuple[int, str]":
    p = subprocess.run(cmd, cwd=str(project), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


def execute(cmd: "list[str]", project: Path, reason: str, out=None) -> int:
    out = out or sys.stdout
    import forge_events as FE
    from _project import safe_component

    who = f"human:{getpass.getuser()}"
    granted: "list[str]" = []
    rc, text = 0, ""
    try:
        for _ in range(MAX_ROUNDS):
            rc, text = _run(cmd, project)
            if rc == 0:
                break
            new = [k for k in (safe_component(k) for k in wanted_keys(text)) if k not in granted]
            if new:
                for k in new:
                    FE.append_approval(project, k, approved_by=who, reason=reason,
                                       approval_kind="human-override", via="human_override",
                                       evidence="подтверждено человеком кодом в терминале")
                    granted.append(k)
                    out.write(f"  + согласие {k} выписано от имени {who}\n")
                continue
            if rc == 1 and "--confirm" in text and "--confirm" not in cmd:
                cmd = cmd + ["--confirm"]
                out.write("  + команда просит --confirm — добавлен\n")
                continue
            break
    finally:
        # Отзыв — всегда, и при исключении: несъеденное согласие пережило бы вызов.
        for k in granted:
            if FE.approval(project, k) is not None:
                FE.revoke_approval(project, k, reason="human_override: согласие на один вызов")
                out.write(f"  - согласие {k} отозвано (вызов окончен)\n")
    out.write(text if text.endswith("\n") or not text else text + "\n")
    if rc == 0 and re.search(r"\brepin\b", text) and "repin" not in cmd:
        out.write("  ℹ идущий прогон живёт по снимку политики; применить на нём: "
                  "human_override.py -- config repin --feature <F>\n")
    out.write(f"=== итог: exit {rc} ===\n")
    return rc


def main(argv: "list[str] | None" = None, env=None, tty=None, stdin_isatty=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    cmd_argv: "list[str]" = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd_argv = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="Команда forge от имени человека (R4 на один вызов).")
    ap.add_argument("--project", default=".", help="корень проекта (дефолт: текущий каталог)")
    ap.add_argument("--reason", default="не указана",
                    help="почему снимаешь ограничение — уйдёт в журнал (необязательно)")
    args = ap.parse_args(argv)

    marker = agent_marker(env)
    if marker:
        print(f"✗ human_override запускает только человек — в отдельном терминале, не через "
              f"агента и не через `!` в его сессии (в окружении {marker}). Агенту: передай "
              f"команду пользователю и жди его результата.", file=sys.stderr)
        return 4
    own_tty = tty is None
    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()
    if not stdin_isatty:
        print("✗ нет терминала на stdin — human_override запускает только человек в своём "
              "терминале; подтверждение через pipe не принимается.", file=sys.stderr)
        return 4
    if own_tty:
        try:
            tty = Terminal()
        except OSError:
            print("✗ /dev/tty недоступен — запускай в своём терминале.", file=sys.stderr)
            return 4

    project = Path(args.project).resolve()
    cmd = resolve_command(cmd_argv)
    try:
        if not confirm(tty, cmd, args.reason.strip(), project,
                       timeout=TIMEOUT_S if own_tty else None):
            print("✗ подтверждение не дано — ничего не выполнено.", file=sys.stderr)
            return 5
    finally:
        if own_tty:
            tty.close()
    return execute(cmd, project, args.reason.strip())


if __name__ == "__main__":
    sys.exit(main())
