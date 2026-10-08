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

Легитимные пути записи approval-маркеров
=========================================
Прямая запись моделью в `ground/approvals/<key>.json` (старая раскладка) И в
`ground/approvals.jsonl` (журнал) ЗАБЛОКИРОВАНА этим хуком: deny на любой Write/Edit в
`ground/approvals*` и на Bash-цели записи в эти пути. Провенанс на чтении дополнительно
форсит `gate-guard._approval_valid` (читает через `FE.approval`): даже если файл как-то
просочился, без штампа `produced_by:"record_approval"` в записи он не снимет гейт.

  • Одиночное согласие (R4 «человек сказал да» для override-гейта / human-approval /
    security-review / change-advisory / fix-plan / doc-approved):
      python3 <harness>/skills/pipeline-state/scripts/record_approval.py \\
          --project <repo-root> --key <phase-or-judge-key> \\
          [--kind approval|gate-override-<judge>|human-approval|doc-approved|...] \\
          --approver <name> --reason "<объяснение>" [--evidence "<ref>"]

  • Batch (атомарная запись многих маркеров одним вызовом; идемпотентно по ключу;
    атомарный flock в `ground/approvals.jsonl`, всё-или-ничего):
      python3 <harness>/skills/pipeline-state/scripts/record_approval.py \\
          --project <repo-root> --batch approvals.yaml
      Файл — YAML/JSON: список объектов с полями `project`/`key`/`kind`/`evidence`/
      `approver`/`reason` (либо обёртка `{approvals: [...]}`). Подробный формат и пример
      для прогона на 30+ модулях (KIDPPRB-9254 п.6) — в `docs/approval-markers.md`.

  • Override гейта судьи (R4-класс; требует approval-маркер `gate-override-<judge>`,
    сначала записанный через `record_approval.py`, иначе `update._check_gate_override_approval`
    блокирует создание override'а):
      python3 <harness>/skills/pipeline-state/scripts/override_judge.py \\
          --project <repo-root> --judge <judge-name> --feature <slug> \\
          --reason "<объяснение>" [--evidence "<ref>"] [--approver <name>]
      batch:
      python3 <harness>/skills/pipeline-state/scripts/override_judge.py \\
          --project <repo-root> --batch overrides.yaml

  • Legacy `ground/approvals/<key>.json` — файл СТАРОЙ раскладки (record_approval до
    миграции на .jsonl писал туда). Легитимный путь существования такого файла сегодня —
    ТОЛЬКО как продукт старого `record_approval.py` или импорт из старой системы, у которой
    в теле маркера уже стоит `produced_by:"record_approval"` (это требование
    `FE._legacy_marker`, иначе gate-guard не засчитает). Создавать legacy-файл руками
    (Write/Edit/echo>/tee) — ОБХОД провенанса: этот хук его блокирует (`ground/approvals/`
    под CP-regex), а `gate-guard._approval_valid` дополнительно фильтрует по `produced_by`.
    Если нужно перенести approvals из внешней системы — переиздайте их батчем через
    `record_approval.py --batch` (атомарно, с теми же reason/evidence; см.
    `docs/approval-markers.md`, раздел «Миграция legacy»).

Если задача требует approval-маркер — модель ОБЯЗАНА сначала спросить пользователя через
`ask_user_question` («утверждаем X?»), получить явное «да» и только потом вызвать
`record_approval.py`. Молча выписать маркер ради само-разблокировки — нарушение инварианта
BLOCKER-1.
"""
from __future__ import annotations

import json
import os
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
# policy.json — канонический v2-конфиг проекта (глобальные параметры: jira/build_system/
# conventions и т.п.). Прямой Write со скомплектованным «правильным» JSON обходит провенанс:
# config.py читает его и применяет, поэтому легитимный путь записи — config.py (Bash→python,
# тул Write не используется). Тот же класс BLOCKER-1, что и pipeline.json, на смену которому
# policy.json и пришёл.
# manifest.json в statements/<skill>/<feature>/ уже защищён — добавлен в v1-итерации гарда
# для защиты update.py от прямой подделки шагов. Присутствие явное (см. test_block_manifest),
# чтобы регрессия в regex-движке (например, исчезновение альтернативы) не открыла обход.
_CP_PATTERNS = [
    r"(?<![\w-])ground/pipeline\.json\b",
    r"(?<![\w-])ground/policy\.json\b",
    # feature-gates.json — live gate-флаги (gates.X.Y), читаются pipeline_phases.py и
    # _phase_eligibility.py; прямой Write со скомплектованным JSON обходит провенанс config.py
    # (тот же класс BLOCKER-1, что policy.json): легитимный путь записи —
    # config-helper/scripts/config.py set gates.* (Bash→python, тул Write не используется).
    r"(?<![\w-])ground/feature-gates\.json\b",
    r"(?<![\w-])ground/approvals\.jsonl\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/manifest\.json\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/evals\.json\b",
    # task-plan.json в statements/ — КАНОНИЧЕСКАЯ копия плана, по которой add_steps.py
    # разворачивает шаги и tdd/eval-guard резолвят задачу по артефакту (_find_task_plan).
    # Подделка плана = подделка состава шагов и привязки «файл → задача». Копия в docs/
    # НЕ гейтится: это артефакт фазы дизайна, её пишет субагент тулом Write.
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/task-plan\.json\b",
    # ground/inventory/** — инвентарь проекта (scan/*.json + grounding-excerpt.json):
    # топливо детерминированных гейтов дизайна и источник, по которому check_taskplan
    # сверяет reuses. Единственный легитимный писатель — system-analyst/ensure_inventory.py
    # (Bash→python), тул Write тут не используется: прямая запись подделывает grounding.
    r"(?<![\w-])ground/inventory(?:/|\b)",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/events\.jsonl\b",
    r"(?<![\w-])ground/statements/[^/]+/[^/]+/(?:_origins|gates|overrides|judges|journal|rollbacks)(?:/|\b)",
    r"(?<![\w-])ground/approvals(?:/|\b)",
    r"(?<![\w-])ground/phases(?:/|\b)",
    # ground/archive/ — стейт ЗАВЕРШЁННЫХ прогонов (archive.py). Данные те же, что в
    # statements/: манифест, evidence, вердикты. Санкционированный писатель — archive.py
    # (пишет файловым API изнутри процесса, тул Write не использует); голый `mv`/`cp` сюда
    # означает, что кто-то раскладывает стейт руками в обход гейтов готовности.
    r"(?<![\w-])ground/archive(?:/|\b)",
    # risk-policy.json — control-plane файл ХАРНЕСА (co-located с хуками), не project config.
    # Защищаем ВСЕГДА (не только при активном ране) — это кодовая константа, не derived state.
    # Легитимная запись — Edit в PR с явным обоснованием, не в проде.
    r"(?<![\w-])(?:hooks/|\.gigacode/hooks/)risk-policy\.json\b",
]
# IGNORECASE обязателен: ФС macOS/Windows регистронезависимы, а regex — нет. `write_file
# GROUND/statements/<s>/<f>/manifest.json` попадал в ТОТ ЖЕ файл, но мимо гарда — это был
# мастер-ключ к состоянию (без манифеста все хуки становятся noop).
_CP_RE = re.compile("|".join(_CP_PATTERNS), re.IGNORECASE)
# Живой control-plane: то, по чему хуки принимают решение ПРЯМО СЕЙЧАС. Удаление такой цели
# гасит enforcement, поэтому rm/mv/touch по ней — deny. Архив завершённых прогонов сюда не
# входит: его чистка — уборка, а не подделка (пин test_pass_archive_script_and_reads).
_CP_LIVE_RE = re.compile(
    "|".join(p for p in _CP_PATTERNS if "ground/archive" not in p), re.IGNORECASE)

# Каталог, ВНУТРИ которого живой control-plane: снести его целиком — то же, что снести
# манифест или хуки. Паттерны выше держат имена файлов, поэтому `rm -rf ground/`,
# `rm -rf ground/statements/<skill>/<feature>` и `rm -rf .gigacode/hooks` проходили (боевой
# прогон, PATH-6/7) — «начать заново» модель может и без умысла. Только для удаления/переноса,
# не для записи: файлы внутри этих каталогов разбирают паттерны выше.
_CP_CONTAINER_RE = re.compile(
    r"(?<![\w-])(?:ground(?:/statements(?:/[^/]+){0,2})?|\.gigacode(?:/(?:hooks|skills))?)"
    r"(?:/\*)?/?$", re.IGNORECASE)


def _container_hint(target: str) -> str:
    return (
        f"[state-write-guard] DENY: удаление/перенос каталога '{target}' целиком снимает живой "
        f"control-plane (стейт прогонов или сами хуки) — после этого все гейты становятся noop.\n"
        f"  Начать прогон заново — pipeline-state/scripts/init.py --force (архивирует, а не "
        f"теряет); убрать брошенный прогон — archive.py abandon (R4, по согласию пользователя); "
        f"снять форж с проекта — uninstall.sh, его запускает пользователь. ground/archive/ "
        f"чистить можно."
    )


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
        if p == _HARNESS_ROOT or _HARNESS_ROOT in p.parents:
            return True
        # Корень резолвнут (Path(__file__).resolve()), значит и цель резолвим: путь через
        # симлинк — `/tmp/proj/.gigacode/hooks/x.py` при корне `/private/tmp/…` на macOS —
        # лексически расходился с корнем, и запись в хуки проходила (боевой прогон, A3).
        rp = Path(os.path.realpath(str(p)))
        return rp == _HARNESS_ROOT or _HARNESS_ROOT in rp.parents
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


def _unlink_hint(target: str) -> str:
    return (
        f"[state-write-guard] DENY: удаление/перенос control-plane-цели '{target}' запрещено.\n"
        f"  Без manifest.json фазовая машина не резолвится и ВСЕ хуки становятся noop — снести "
        f"состояние дешевле, чем его подделать, поэтому это тот же класс запрета, что и запись.\n"
        f"  Нужно начать прогон заново — pipeline-state/scripts/init.py --force (архивирует "
        f"текущий стейт, а не теряет его). Нужно откатить шаги — rollback.py (R4, по approval). "
        f"Уборка завершённых прогонов — archive.py; ground/archive/ чистить можно."
    )


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
# Разбор целей записи/удаления — общий с tdd-guard/eval-guard/sod-enforcer. Модуль stdlib-only;
# не грузится — бандл битый, и молчать нельзя: exit 1 рантайм читает как «возражений нет».
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _shell_targets as ST
except Exception as _e:  # pragma: no cover — сломанный бандл
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[state-write-guard] DENY: бандл forge не грузится ({_e}). Перезапусти: "
              f"bash .gigacode/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("state-write-guard", _e))

_CMD_SEP_RE = ST._CMD_SEP_RE
_SHELLS = ST._SHELLS
_segments = ST._segments
_write_targets = ST.write_targets
_unlink_targets = ST.unlink_targets
_rm_targets = ST.rm_targets

# ── find, который УДАЛЯЕТ найденное ──────────────────────────────────────────────────
# `find . -name manifest.json -delete` проходил все семь Bash-хуков: state-write-guard знал
# только rm/unlink/shred/touch/mv, а ядро destructive-blocker ловит find лишь от `/`/`~`.
# На жертвенном клоне манифест исчез, и запись в src/main, до того DENY, стала rc 0 (боевой
# прогон v0.4.6, E-FIND-DELETE). Разбор: корни поиска + фильтры имён → задевает ли удаление
# живой control-plane. Уборка своего (`find . -name '*.class' -delete`, `find build -delete`)
# остаётся свободной.
_FIND_DELETE_EXEC = ("rm", "unlink", "shred", "srm", "mv", "truncate")
_FIND_NAME_OPTS = ("-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
                   "-regex", "-iregex")
# Что живёт внутри ground/ и hooks/: имена, по которым фильтр find задевает control-plane.
_CP_SAMPLES = (
    "ground/statements/s/f/manifest.json", "ground/statements/s/f/events.jsonl",
    "ground/statements/s/f/evals.json", "ground/statements/s/f/task-plan.json",
    "ground/policy.json", "ground/pipeline.json", "ground/feature-gates.json",
    "ground/approvals.jsonl", "ground/inventory/grounding-excerpt.json",
    ".gigacode/hooks/gate-guard.py", ".gigacode/hooks/risk-policy.json",
)


def _find_is_deleting(argv: list[str], cmd: str) -> bool:
    if "-delete" in argv:
        return True
    for i, a in enumerate(argv):
        if a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(argv):
            target = posixpath.basename(argv[i + 1])
            if target in _FIND_DELETE_EXEC:
                return True
            if target in _SHELLS and re.search(r"\b(?:rm|unlink|shred|mv|truncate)\b|>",
                                               " ".join(argv[i + 2:])):
                return True
    return bool(re.search(r"\|\s*xargs\b[^;&|]*\b(?:rm|unlink|shred|srm|mv)\b", cmd))


def _find_roots_and_filters(argv: list[str]) -> "tuple[list[str], list[tuple[str, str]]]":
    roots, i = [], 1
    while i < len(argv) and argv[i] in ("-H", "-L", "-P", "-O0", "-O1", "-O2", "-O3"):
        i += 1
    while i < len(argv) and not argv[i].startswith(("-", "(", "!", "\\(")):
        roots.append(argv[i])
        i += 1
    filters = [(argv[k], argv[k + 1]) for k in range(i, len(argv) - 1)
               if argv[k] in _FIND_NAME_OPTS]
    return roots or ["."], filters


def _filter_hits_cp(opt: str, value: str) -> bool:
    """Задевает ли фильтр find хоть один файл control-plane (по образцам _CP_SAMPLES)."""
    import fnmatch
    for sample in _CP_SAMPLES:
        if opt in ("-name", "-iname"):
            hit = fnmatch.fnmatch(posixpath.basename(sample).lower(), value.lower())
        elif opt in ("-regex", "-iregex"):
            try:
                hit = re.search(value, "./" + sample, re.I) is not None
            except re.error:
                hit = True
        else:
            hit = fnmatch.fnmatch(("./" + sample).lower(), value.lower())
        if hit:
            return True
    return False


def _covers(root_dir: str, path: str) -> bool:
    root_dir = root_dir.rstrip("/") or "/"
    return path == root_dir or path.startswith(root_dir + "/") or root_dir == "/"


def _find_delete_hit(cmd: str, cwd: str) -> str:
    """Корень find, удаление под которым задевает живой control-plane; '' — не задевает."""
    for argv in _segments(cmd):
        if posixpath.basename(argv[0]) != "find" or not _find_is_deleting(argv, cmd):
            continue
        roots, filters = _find_roots_and_filters(argv)
        base = os.path.abspath(cwd or ".")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import risk_ladder as _R
            proj = str(_R.project_root(base))
        except Exception:  # noqa: BLE001 — корень не резолвится: меряем от cwd
            proj = base
        cp_dirs = [posixpath.normpath(posixpath.join(proj, d).replace("\\", "/"))
                   for d in ("ground", ".gigacode")]
        for r in roots:
            rr = _collapse(r)
            if _CP_LIVE_RE.search(rr) or _CP_CONTAINER_RE.search(rr):
                return r                    # корень поиска — сам control-plane
            absr = posixpath.normpath(posixpath.join(base.replace("\\", "/"),
                                                     os.path.expanduser(r).replace("\\", "/")))
            if not any(_covers(absr, d) for d in cp_dirs):
                continue                    # под этим корнем control-plane нет
            if not filters or any(_filter_hits_cp(o, v) for o, v in filters):
                return r
    return ""


# Доки фич и их архив — история стройки: BRD, sdd, tech-design, task-plan. Не control-plane
# (фазы пишут туда тулом Write, а forgefix штатно переносит свои доки `mv`), но УДАЛЯТЬ их
# моделью нельзя: на отказе архивации доки остаются на месте, и «прибраться руками» значило
# потерять историю фичи безвозвратно — архив её больше не увидит, restore вернуть нечего.
# Уборка — только /forge-archive put (переносит доки вместе со стейтом прогона).
_DOCS_RE = re.compile(r"(?<![\w-])feature-pipeline(?:/|$)|(?<![\w-])docs/archive(?:/|$)",
                      re.IGNORECASE)
def _docs_hint(target: str) -> str:
    return (
        f"[state-write-guard] DENY: удаление доков фичи '{target}' запрещено — это история "
        f"стройки (BRD, sdd, tech-design, task-plan), и после удаления её не вернёт ничто.\n"
        f"  Убрать доки завершённой фичи с рабочего стола — /forge-archive put <слаг> (доки "
        f"переезжают в <docs>/archive/ вместе со стейтом прогона, restore возвращает обратно).\n"
        f"  Архивация отказала — покажи пользователю её причину как есть и не обходи её руками."
    )


def _resolved(target: str, cwd: str) -> str:
    """Цель через симлинки: `ln -s ground l; echo x > l/policy.json` писал в policy.json мимо
    CP-паттерна — в строке цели нет `ground/` (track C F-3). Для харнеса realpath уже был."""
    try:
        p = target if os.path.isabs(target) else os.path.join(cwd or ".", target)
        return os.path.realpath(p).replace("\\", "/")
    except (OSError, ValueError):
        return ""


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
            hit = _find_delete_hit(cmd, cwd)
            if hit:
                print(_unlink_hint(f"find {hit} … (удаление найденного)"), file=sys.stderr)
                return 2
            for t in (_collapse(x) for x in _unlink_targets(cmd)):
                for v in (t, _resolved(t, cwd)):
                    if v and _CP_LIVE_RE.search(v):
                        print(_unlink_hint(t), file=sys.stderr)
                        return 2
                    if v and _CP_CONTAINER_RE.search(v):
                        print(_container_hint(t), file=sys.stderr)
                        return 2
            for t in (_collapse(x) for x in _rm_targets(cmd)):
                if _DOCS_RE.search(t):
                    print(_docs_hint(t), file=sys.stderr)
                    return 2
            targets = [_collapse(t) for t in _write_targets(cmd)]
            for t in targets:
                if _CP_RE.search(t) or _CP_RE.search(_resolved(t, cwd)):
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
