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

fail-open на не-JSON stdin / отсутствии цели (нечего блокировать). Хук не должен ронять прогон,
но при совпадении control-plane-цели — блок.
"""
from __future__ import annotations

import json
import posixpath
import re
import sys
from pathlib import Path

WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")
BASH_TOOLS = ("Bash", "run_shell_command")

# Пути control-plane. Lookbehind `(?<![\w-])` ловит путь и как bare file_path (Write), и внутри
# shell-команды (после пробела/кавычки/`/`), но не в составе большего слова (myground/…).
# judges/ — вердикты судей: подделанный Write с produced_by:"run_judge" проходил провенанс-
# проверку update._check_judges (легитимный путь — только run_judge.py). ground/phases/ —
# фазовая машина (gate.json/phase-defs.json/agent-evidence.jsonl), её читает phase-lock
# gate-guard; пишут только init_phase_gate.py/phase_sync.py и хук grounding-evidence (не тул-вызовы).
# evals.json — кэш результатов EDD (eval-guard читает status:"passed" по нему): без защиты
# прямой Write этого файла со всеми passed снимал eval-гейт целиком (тот же класс BLOCKER-1,
# что judges/gates). Легитимный писатель — run_pending_evals.py (Bash→python, не тул Write).
_CP_PATTERNS = [
    r"(?<![\w-])ground/pipeline\.json\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/manifest\.json\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/evals\.json\b",
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


def _in_harness(target: str) -> bool:
    """Указывает ли путь внутрь корня харнеса (кода форжа)."""
    if not target:
        return False
    try:
        p = Path(target)
        if not p.is_absolute():
            p = Path.cwd() / p
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

# Токены записи в shell-команде (редирект/копирование/inline-python).
_WRITE_TOKEN_RE = re.compile(
    r">>?|<>|\btee\b|\bdd\b[^|]*\bof=|\bsed\b[^|]*-i|\bcp\b|\bmv\b|\binstall\b"
    r"|\bopen\s*\([^)]*['\"][^'\"]+['\"]\s*,\s*['\"][aw]|\.write(?:_text)?\s*\(|\btruncate\b"
)

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
        f"  • фазовая машина (ground/phases) → init_phase_gate.py / phase_sync.py\n"
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

        if tn in WRITE_TOOLS:
            target = _collapse(str(ti.get("file_path") or ti.get("path") or ti.get("filename") or ""))
            if target and _CP_RE.search(target):
                print(_hint(target), file=sys.stderr)
                return 2
            if target and _in_harness(target) and _pipeline_active(str(data.get("cwd") or "")):
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
            # схлопываем `//` и `/./` в команде (best-effort: `..` в тексте команды не резолвим),
            # чтобы редирект в `ground//pipeline.json` совпал с CP-паттерном.
            cmd_cp = re.sub(r"/\./", "/", re.sub(r"/{2,}", "/", cmd))
            # блок только когда есть И токен записи, И упоминание control-plane-пути в команде
            if _CP_RE.search(cmd_cp) and _WRITE_TOKEN_RE.search(cmd):
                m = _CP_RE.search(cmd_cp)
                print(_hint(m.group(0) if m else "ground/*"), file=sys.stderr)
                return 2
            if _WRITE_TOKEN_RE.search(cmd):
                harness = str(_HARNESS_ROOT).replace("\\", "/")
                for tok in re.findall(r"[^\s'\"|;&><]+", cmd_cp):
                    if tok.startswith(harness) and _pipeline_active(str(data.get("cwd") or "")):
                        print(_harness_hint(tok), file=sys.stderr)
                        return 2
            return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
