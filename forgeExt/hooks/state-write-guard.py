#!/usr/bin/env python3
"""state-write-guard.py — PreToolUse: запрет ПРЯМОЙ записи моделью в control-plane-файлы forge.

Закрывает BLOCKER-1 аудита. Вся пирамида целостности пайплайна — approval-маркеры, manifest,
overrides, gates, _origins, judges (вердикты судей), pipeline.json, ground/phases (фазовая
машина) — это обычные JSON внутри `ground/`, а `ground/` разрешён к записи (pii-boundary/
gate-guard его whitelist-ят, классификатор рисков даёт .json в ground/ уровень R1→auto). Провенанс-проверки в pipeline-state/update.py
(`_check_subagent_origin`/`_check_gate_result`/`_check_judges`, счётчики reopen/failure)
срабатывают ТОЛЬКО если мутация идёт ЧЕРЕЗ update.py. Прямой `Write .../manifest.json` со всеми
`status:"completed"` — или `Write ground/approvals/human-approval.json` — обходит всё это.

Инвариант, который форсит этот хук: **state-файлы меняются только санкционированными скриптами**
(update.py / record_gate.py / override_judge.py / config.py / record_approval.py) и хуком
state-recorder — они пишут через Bash→python→open(), т.е. НЕ инструментом Write/Edit, поэтому
под блок не попадают. Любая прямая запись инструментом (Write/Edit) или shell-редиректом
(`>`/`tee`/`dd of=`/`sed -i`/`cp`/`mv`/`python -c open()`) в эти пути — deny (exit 2).

Матчеры: `^(run_shell_command|Bash)$` и `^(write_file|edit|notebook_edit|...)$` — оба (Bash-вектор
редиректа + Write-вектор). Bash-детект по природе best-effort (в shell тысяча способов записать
файл); ловит частые векторы. Провенанс на approval-маркерах дополнительно форсит gate-guard.

**Bash-детект работает по ЦЕЛЯМ записи, а не по «где-то в команде есть `>`».** Раньше блок давало
совпадение «токен записи в команде» И «путь упомянут в команде» — и легальный вызов
`python3 <harness>/skills/pipeline-state/scripts/update.py … 2>&1` попадал под харнес-гейт, потому
что `2>&1` считался записью, а путь к самому скрипту — «записью в харнес». Так гард глушил ровно
те санкционированные скрипты, ради которых он существует. Теперь из команды извлекаются реальные
цели записи (`_write_targets`: редиректы, `tee`, `dd of=`, `sed -i`, `cp`/`mv`/`install`,
`truncate`, литералы inline-python при `open(...,'w')`/`.write(`), и проверяются только они:
путь исполняемого скрипта и `2>&1` целями не являются.

fail-open на не-JSON stdin / отсутствии цели (нечего блокировать). Хук не должен ронять прогон,
но при совпадении control-plane-цели — блок.
"""
from __future__ import annotations

import json
import posixpath
import re
import shlex
import sys
from pathlib import Path

WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")
BASH_TOOLS = ("Bash", "run_shell_command")

# Пути control-plane. Lookbehind `(?<![\w-])` ловит путь и как bare file_path (Write), и внутри
# shell-команды (после пробела/кавычки/`/`), но не в составе большего слова (myground/…).
# judges/ — вердикты судей: подделанный Write с produced_by:"run_judge" проходил провенанс-
# проверку update._check_judges (легитимный путь — только run_judge.py). ground/phases/ —
# фазовая машина ПРОШЛЫХ прогонов: сейчас состояние выводится из манифеста и на диск не
# пишется, но старую раскладку читатели ещё дочитывают — значит, подделывать её тоже нельзя.
# evals.json — кэш результатов EDD (eval-guard читает status:"passed" по нему): без защиты
# прямой Write этого файла со всеми passed снимал eval-гейт целиком (тот же класс BLOCKER-1,
# что judges/gates). Легитимный писатель — run_pending_evals.py (Bash→python, не тул Write).
# events.jsonl / approvals.jsonl — журналы evidence (origin/gate/judge/override/approval),
# пришедшие на смену россыпи маркеров. Это ГЛАВНАЯ цель гарда: одна дописанная строка с
# нужным produced_by сняла бы гейт так же, как раньше подделанный judges/<name>.json.
# Легитимные писатели дописывают их из скриптов и хуков (Bash→python), а не тул-вызовом.
# Каталоги старой раскладки остаются в списке: прогоны, начатые до миграции, читаются с них.
_CP_PATTERNS = [
    r"(?<![\w-])ground/pipeline\.json\b",
    r"(?<![\w-])ground/approvals\.jsonl\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/manifest\.json\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/evals\.json\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/events\.jsonl\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/(?:_origins|gates|overrides|judges|journal|rollbacks)(?:/|\b)",
    r"(?<![\w-])ground/approvals(?:/|\b)",
    r"(?<![\w-])ground/phases(?:/|\b)",
]
_CP_RE = re.compile("|".join(_CP_PATTERNS))

# ── Каталог САМОГО ХАРНЕСА (код форжа) — тоже control-plane ───────────────────────────
# Артефакты фазы (sdd.md, task-plan.json, fix-plan.md) должны идти в docs-каталог ПРОЕКТА
# (`docs.*` → skill_paths.feature_docs_dir). Но брифы подставляют путь через плейсхолдер, и
# нерезолвнутый плейсхолдер уводит запись «рядом со SKILL.md» — т.е. в skills/ харнеса. В
# extension-раскладке это вообще ОБЩИЙ каталог на все проекты: артефакт одной задачи оседает
# в коде форжа, едет в следующий проект и подменяет бриф фазы. Поэтому во время прогона любая
# запись внутрь корня харнеса — deny.
#
# Корень определяем от самого хука (hooks/state-write-guard.py → parents[1]), поэтому правило
# не зависит от раскладки: legacy `<project>/.gigacode`, установленный extension или слинкованный
# каталог-исходник. Гейт активен ТОЛЬКО при активном пайплайне (есть манифест): разработка
# самого форжа (правка skills/ руками вне прогона) не блокируется.
_HARNESS_ROOT = Path(__file__).resolve().parent.parent
_HARNESS_HINT_DIRS = ("skills", "hooks", "commands", "references")


def _in_harness(target: str, cwd: str = "") -> bool:
    """Указывает ли путь внутрь корня харнеса (кода форжа).

    Относительный путь резолвим от cwd СЕССИИ (payload), а не от cwd процесса-хука: рантайм
    запускает хук с произвольным рабочим каталогом, и от `Path.cwd()` относительный `docs/x.md`
    мог «приземлиться» внутрь харнеса и дать ложный deny."""
    if not target:
        return False
    try:
        p = Path(target)
        if not p.is_absolute():
            p = Path(cwd or ".") / p
        p = Path(posixpath.normpath(str(p).replace("\\", "/")))
        return p == _HARNESS_ROOT or _HARNESS_ROOT in p.parents
    except (OSError, ValueError):
        return False


def _pipeline_active(cwd: str) -> bool:
    """Идёт ли прогон (есть манифест). Вне прогона харнес-гейт не вмешивается."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import risk_ladder as _R
        return bool(_R.manifest_exists(_R.project_root(cwd or ".")))
    except Exception:  # noqa: BLE001 — резолвер недоступен: не блокируем (fail-open)
        return False


def _harness_hint(target: str) -> str:
    return (
        f"[state-write-guard] DENY: запись в каталог ХАРНЕСА '{target}' запрещена во время "
        f"прогона. Это код форжа ({'/'.join(_HARNESS_HINT_DIRS)}), а в extension-раскладке — "
        f"общий каталог на все проекты, а не место для артефактов задачи.\n"
        f"  Артефакты фазы (sdd.md, tech-design.md, task-plan.json, fix-plan.md) пишутся в "
        f"docs-каталог ПРОЕКТА. Узнай его точный путь одной командой (не подставляй плейсхолдер "
        f"и не пиши рядом со SKILL.md):\n"
        f"  python3 {_HARNESS_ROOT}/skills/feature-pipeline/scripts/skill_paths.py "
        f"feature-docs --project <toplevel> --feature <slug>\n"
        f"  Правка самого форжа — отдельная задача вне прогона пайплайна."
    )

# ── Извлечение ЦЕЛЕЙ записи из shell-команды ──────────────────────────────────────────
# Гард обязан отличать «файл, в который пишут» от «файл, который читают/исполняют». Иначе
# `python3 <harness>/…/update.py … 2>&1` выглядит как запись в харнес (см. докстринг модуля).
_CMD_SEP_RE = re.compile(r"\|\||&&|[;|&\n]")
_REDIR_TOK_RE = re.compile(r"^[0-9]*&?(>>?|<>)(.*)$")
_COPY_CMDS = ("cp", "mv", "install", "rsync")
_MULTI_TARGET_CMDS = ("tee", "truncate")   # пишут во все свои файлы всегда
_INPLACE_CMDS = ("sed", "perl", "ruby")    # пишут в файл ТОЛЬКО с -i (иначе поток на stdout)
# inline-python: пишущий вызов в тексте команды. Есть такой — целями считаем ВСЕ строковые
# литералы команды (какой из них путь, из shell не разобрать; лучше перебдеть).
_PY_WRITE_RE = re.compile(
    r"open\s*\([^)]*['\"]\s*,\s*['\"][aw]|\.write(?:_text|_bytes)?\s*\(|\bshutil\.(copy|move)\b"
)
_STR_LIT_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")


def _tokens(seg: str) -> list[str]:
    try:
        return shlex.split(seg, posix=True)
    except ValueError:  # незакрытая кавычка — грубая токенизация
        return re.findall(r"[^\s'\"]+", seg)


def _write_targets(cmd: str) -> list[str]:
    """Пути, в которые команда ПИШЕТ (best-effort). Путь исполняемого скрипта, аргументы-входы
    и `2>&1` целями не считаются."""
    out: list[str] = []
    if _PY_WRITE_RE.search(cmd):
        out += [a or b for a, b in _STR_LIT_RE.findall(cmd)]
    for seg in _CMD_SEP_RE.split(cmd.replace(">|", ">")):
        if not seg.strip():
            continue
        toks = _tokens(seg)
        if not toks:
            continue
        # редиректы: `> f`, `>>f`, `1> f`, `&> f` (но не `2>&1` и не fd-номер)
        redirect_idx = set()
        for i, t in enumerate(toks):
            m = _REDIR_TOK_RE.match(t)
            if not m:
                continue
            redirect_idx.add(i)
            rest = m.group(2)
            if not rest and i + 1 < len(toks):
                rest = toks[i + 1]
                # цель редиректа — НЕ аргумент команды: иначе у `cp src <cp-файл> > /dev/null`
                # последним аргументом cp оказывался /dev/null, и настоящее назначение копии
                # (control-plane) не проверялось вовсе — дыра в гарде.
                redirect_idx.add(i + 1)
            if rest and not rest.startswith("&") and not rest.isdigit():
                out.append(rest)
        argv = [t for i, t in enumerate(toks) if i not in redirect_idx]
        if not argv:
            continue
        name = posixpath.basename(argv[0])
        files = [a for a in argv[1:] if not a.startswith("-")]
        if name in _COPY_CMDS and files:
            out.append(files[-1])          # назначение copy/move — последний аргумент
        elif name in _MULTI_TARGET_CMDS:
            out += files
        elif name in _INPLACE_CMDS and any(a.startswith("-i") for a in argv[1:]):
            out += files
        for a in argv:
            if a.startswith("of="):        # dd of=<file>
                out.append(a[3:])
    return [t for t in out if t]

# Чекпойнт-refs (refs/forge/*) — control-plane в git: точки восстановления rollback.py.
# `git update-ref` на них — подделка чекпойнта (перенаправить откат на выгодный коммит),
# deny безусловно (update-ref сам и есть запись, write-токен не нужен). Легитимный писатель —
# checkpoint.py subprocess-ом из update.py/init.py (не тул-вызов, хуками не перехватывается).
_FORGE_REF_RE = re.compile(r"\bgit\b[^|;&]*\bupdate-ref\b[^|;&]*\brefs/forge/")


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/")


def _collapse(p: str) -> str:
    """Схлопнуть `//`, `/./` и разрешить `..` в пути-цели Write, иначе эквивалентные записи
    `ground//pipeline.json` / `ground/./pipeline.json` / `.../feat/../feat/manifest.json`
    писали бы в тот же control-plane-файл мимо CP-regex. posixpath.normpath не трогает
    разделитель (всегда '/'), поэтому Windows-пути уже приведены _norm к прямым слэшам."""
    p = _norm(p)
    if not p:
        return p
    return posixpath.normpath(p)


def _hint(target: str) -> str:
    return (
        f"[state-write-guard] DENY: прямая запись в control-plane-файл '{target}' запрещена. "
        f"State меняется ТОЛЬКО санкционированными скриптами (провенанс форсится update.py):\n"
        f"  • шаги/manifest → pipeline-state/scripts/update.py (--feature ...)\n"
        f"  • gate-result → pipeline-state/scripts/record_gate.py\n"
        f"  • вердикт судьи → feature-pipeline/scripts/run_judge.py (--from-output / --recheck)\n"
        f"  • фазовая машина — не файл: выводится из manifest.json (шаги закрывает update.py)\n"
        f"  • снятие судьи → pipeline-state/scripts/override_judge.py\n"
        f"  • параметры pipeline.json → config-helper/scripts/config.py set\n"
        f"  • approval-маркер → pipeline-state/scripts/record_approval.py (ТОЛЬКО после явного "
        f"«да» пользователя; сначала спроси через ask_user_question).\n"
        f"Прямой Write/echo>/tee/python -c open() сюда — обход провенанса, не делай так."
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        tn = data.get("tool_name", "")
        ti = data.get("tool_input") or {}

        cwd = str(data.get("cwd") or "")

        if tn in WRITE_TOOLS:
            target = _collapse(str(ti.get("file_path") or ti.get("path") or ti.get("filename") or ""))
            if target and _CP_RE.search(target):
                print(_hint(target), file=sys.stderr)
                return 2
            if target and _in_harness(target, cwd) and _pipeline_active(cwd):
                print(_harness_hint(target), file=sys.stderr)
                return 2
            return 0

        if tn in BASH_TOOLS:
            cmd = _norm(str(ti.get("command") or ""))
            if not cmd:
                return 0
            if _FORGE_REF_RE.search(cmd):
                print("[state-write-guard] DENY: git update-ref на refs/forge/* запрещён — "
                      "чекпойнт-refs пишет только checkpoint.py (из update.py/init.py). "
                      "Ручная правка refs подделывает точку восстановления rollback.",
                      file=sys.stderr)
                return 2
            targets = [_collapse(t) for t in _write_targets(cmd)]
            for t in targets:
                if _CP_RE.search(t):
                    print(_hint(t), file=sys.stderr)
                    return 2
            for t in targets:
                if _in_harness(t, cwd) and _pipeline_active(cwd):
                    print(_harness_hint(t), file=sys.stderr)
                    return 2
            return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
