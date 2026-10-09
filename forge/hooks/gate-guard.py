#!/usr/bin/env python3
"""gate-guard.py — PreToolUse permission gateway с risk-adaptive ladder R0–R5 (PDLC v3.5).

Заменяет фиксированную политику на risk-adaptive (см. risk_ladder.py + risk-policy.json).
Принцип **deny-first**: рисковое (R3+) действие блокируется, пока не выполнено требование
уровня (manifest-шаги / approval-маркер / evidence). На R3+ при внутренней ошибке/неясности —
тоже блок (fail-CLOSED). R0/R1 и любые читающие команды — проходят мгновенно (fail-open).

Матчеры: вешать на `^Bash$` и `(Write|Edit|WriteFile|NotebookEdit)`. Блок: exit 2 + stderr.
Separation of duties: если действие выше cap роли (agent_type) — deny.

Дополнительно: **phase-lock state machine** — проверка последовательности фаз пайплайна.
Фазовое состояние ВЫЧИСЛЯЕТСЯ из manifest.json (pipeline_phases.live_state), а не читается
из ground/phases/gate.json: тот был персистентным кэшем этого же расчёта и умел устаревать.
Три проверки:
  1. Скилл соответствует allowed_skills текущей фазы
  2. Read/Grep/Glob в src/ заблокированы, пока grounding не завершён
  3. depends_on фазы выполнены
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
    from _project import active_feature, safe_component
    import forge_events as FE
except Exception as _e:  # pragma: no cover — сломанный бандл/интерпретатор
    # Форточка на команды ВОССТАНОВЛЕНИЯ: сплошной deny запирал и починку бандла
    # (баннер советовал `bash .gigacode/deploy-local.sh`, а матчер ^Bash$ её же и резал).
    # _failclosed — stdlib-only; если не грузится и он, поведение прежнее (exit 2).
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[gate-guard] DENY: бандл forge не грузится ({_e}). Проверь интерпретатор в "
              f".gigacode/settings.json (нужен python 3.9+ с рабочим expat), затем "
              f"перезапусти: bash .gigacode/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("gate-guard", _e))

# Фазовая машина считается из манифеста (pipeline_phases — единственная реализация
# деривации «шаги → фазы»). Импорт мягкий: он и раньше был мягким у tdd-guard/eval-guard,
# а отсутствие фазового состояния и раньше означало fail-open (нет gate.json → не блокируем).
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "feature-pipeline" / "scripts"))
    import pipeline_phases as _pp
except Exception:  # pragma: no cover — бандл повреждён
    _pp = None


def _live_state(root: Path):
    """(gate, defs_map) активного прогона. ({}, {}) — пайплайна нет либо деривация недоступна."""
    if _pp is None:
        return {}, {}
    mp = R.active_manifest(root)
    if mp is None:
        return {}, {}
    manifest = _read_json(mp)
    if not manifest:
        return {}, {}
    # project_root обязателен: без него live_state не применяет enabled_by/skip_if, и фаза,
    # выключенная конфигом, навсегда остаётся current_phase (её никто не закроет) — см. tasks/012.
    try:
        return _pp.live_state(manifest, project_root=root)
    except TypeError:  # старый бандл pipeline_phases без параметра
        return _pp.live_state(manifest)


def _block(reason: str) -> int:
    print(f"[gate-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _deny() -> bool:
    """Обёртка: блокировка в check_phase_gate. Возвращает False (bool для branch)."""
    return False


def _read_json(path: Path) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


_WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")

# Generic-типы субагента: РАНТАЙМОВОЕ имя механизма, а не заявка на скилл. Форк зовёт всех
# субагентов пайплайна как `agent(subagent_type="general-purpose")` (FORGE.md «Известные
# ограничения», docs/v2/01-runtime-config-surface.md), поэтому сравнение с allowed_skills фазы
# (там имена скиллов: tech-design, jira-task-writer, …) давало DENY на ЛЮБОЙ тул-вызов
# субагента — вплоть до `ls -la` в фазе 02-design. Вместе с inline-phase-guard (тот блокирует
# ПУСТОЙ agent_type как «оркестратор пишет inline») получался пинцет: при любом значении поля
# работу рубил один из двух хуков, и ни одна субагентная фаза full-ветки не проходила.
# Гейт сохраняется ровно для своего случая — НАЗВАННЫЙ чужой скилл в фазе (jira-task-writer
# в дизайне). Роль внутри фазы форсит sod-enforcer, происхождение шага — update._check_subagent_origin.
# Читающие инструменты (Claude-нотация + канон рантайма) — см. main().
_READ_TOOLS = ("Read", "ReadFile", "read_file",
               "Grep", "GrepSearch", "grep", "grep_search", "search_file_content", "Glob", "glob")
# Поиск: цель — каталог и шаблон, а не файл. Без `path` он идёт от корня проекта, то есть и
# по src/ (см. check_phase_gate).
_SEARCH_TOOLS = ("Grep", "GrepSearch", "grep", "grep_search", "search_file_content",
                 "Glob", "glob")

_GENERIC_AGENT_TYPES = frozenset({
    "general-purpose", "general_purpose", "generalpurpose",
    "agent", "task", "subagent", "default", "none",
})


def _is_generic_agent_type(agent_type: str) -> bool:
    return str(agent_type or "").strip().lower() in _GENERIC_AGENT_TYPES


def _required_decisions_missing(root: Path) -> str | None:
    """Первый не-записанный required-ключ для активной фазы (fail-closed решения), иначе None.

    Карта required_decisions в risk-policy.json: префикс id шага → [dot-path ключей].

    v2: ключи указывают на inputs.* / decisions.* (например, "inputs.spec", "inputs.story").
    R.config_get автоматически резолвит их из manifest.json активной фичи, с fallback
    на legacy sources.* в pipeline.json (для старых проектов без миграции)."""
    try:
        policy = R.load_policy().get("required_decisions") or {}
        if not policy:
            return None
        step = R.current_step_id(root)
        if not step:
            return None
        for prefix, keys in policy.items():
            if prefix.startswith("_"):
                continue
            if step.startswith(prefix):
                for k in keys:
                    if not R.config_get(root, k):
                        return k
                return None
    except Exception:
        return None
    return None


def _approval_valid(root: Path, key: str) -> bool:
    """approval-маркер засчитывается ТОЛЬКО с провенансом produced_by:"record_approval"
    (BLOCKER-1): рукописный/самовыписанный маркер без провенанса не снимает гейт, даже если
    он как-то просочился мимо state-write-guard.

    KIDPPRB-9254: переписано на чтение JSONL-журнала через FE.approval() вместо legacy-файла
    ground/approvals/<key>.json — record_approval.py пишет в JSONL, и прежний путь не видел
    свежих маркеров. Legacy-файл остаётся fallback (см. forge_events.approval)."""
    rec = FE.approval(root, key)
    return isinstance(rec, dict) and rec.get("produced_by") == "record_approval"


# Нормализация ключа approval — общий санитайзер с record_approval (иначе гейт искал бы
# маркер под именем, которого скрипт никогда не создаст).
_safe_key = safe_component


def _phase_approval_missing(root: Path) -> tuple[str, str] | None:
    """(approval-key, step_id), если активная фаза требует ПОДТВЕРЖДЕНИЯ ЧЕЛОВЕКОМ, а валидного
    маркера нет; иначе None. Карта phase_approvals в risk-policy.json: префикс id шага → шаблон
    ключа ({feature} = слаг активной фичи).

    Зачем. Точки «покажи план и спроси» жили только прозой в брифах — на fix-ветке это давало
    прогон, где модель после диагностики молча уходила писать код. Здесь тот же класс гейта,
    что у R4-оверрайдов: без ground/approvals/<key>.json с провенансом record_approval
    продуктивная запись фазы не проходит."""
    try:
        policy = R.load_policy().get("phase_approvals") or {}
        if not policy:
            return None
        step = R.current_step_id(root)
        if not step:
            return None
        for prefix, template in policy.items():
            if prefix.startswith("_") or not isinstance(template, str):
                continue
            if step.startswith(prefix):
                feature = R.active_feature_name(root) or "pipeline"
                key = _safe_key(template.replace("{feature}", feature))
                return None if _approval_valid(root, key) else (key, step)
    except Exception:
        return None
    return None


def check_phase_gate(tool_name: str, tool_input: dict, agent_type: str | None,
                     root: Path) -> bool:
    """Трёхуровневая проверка последовательности фаз пайплайна.
    Возвращает True (пропустить) или False (блокировать — сообщение уже в stderr)."""
    # Фазовое состояние считается из манифеста на месте. Раньше читались gate.json и
    # phase-defs.json — персистентные копии этого же расчёта, которые умели устаревать
    # (sync пропущен/упал → гейт решал по протухшему снимку).
    gate, defs_map = _live_state(root)
    if not gate:
        return True  # вне пайплайна — не блокируем

    current_phase_id = gate.get("current_phase", "")
    phase = next((p for p in gate.get("phases", []) if p["id"] == current_phase_id), None)
    if not phase:
        return True  # некорректное состояние — пропускаем (не наша ошибка)

    pd = defs_map.get(current_phase_id, {})
    phase_status = phase.get("status", "pending")

    # ════════════════════════════════════════════════════════════════════
    # Проверка 1: скилл соответствует фазе (только для НАЗВАННЫХ agent-вызовов)
    # ════════════════════════════════════════════════════════════════════
    if agent_type and not _is_generic_agent_type(agent_type):
        allowed = pd.get("allowed_skills", [])
        if allowed and agent_type not in allowed:
            _block(
                f"phase gate: фаза '{current_phase_id}' (status={phase_status}) "
                f"не разрешает скилл '{agent_type}'. "
                f"Разрешены: {allowed}. "
                f"Пропусти фазу или заверши сначала."
            )
            return _deny()

    # ════════════════════════════════════════════════════════════════════
    # Проверка 2: блокировка инструментов до завершения фазы
    # ════════════════════════════════════════════════════════════════════
    blocked_tools = pd.get("blocked_tools_until_complete", [])
    blocked_paths = pd.get("blocked_paths", [])

    if phase_status != "completed" and blocked_tools and tool_name in blocked_tools:
        file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        command = str(tool_input.get("command") or "")

        # Пропускаем системные пути и конфиги. "ground/policy" — v2-имя проектного
        # конфига; до его добавления в списке жило только легаси "ground/pipeline",
        # и на v2-native проекте (policy.json, pipeline.json уже нет) чтение
        # собственного конфига ложно упиралось в фазовый гейт.
        if any(safe in file_path for safe in
               ("ground/phases", "grounding-excerpt", "ground/policy", "ground/pipeline",
                ".gigacode/")):
            return True

        # Поиск без `path` идёт от корня проекта — по src/ тоже. Пустая цель была fail-open:
        # канонический вызов `glob {"pattern": "src/**/*.java"}` и `grep_search {"pattern":
        # "class"}` проходили блок чтения src/ до grounding'а (боевой прогон v0.4.6, B-F1).
        # Целью считаем шаблон glob'а, а у поиска без каталога — весь проект.
        if not file_path and tool_name in _SEARCH_TOOLS and blocked_paths:
            pattern = re.sub(r"^(?:\./)+", "", str(tool_input.get("pattern") or "")
                             .replace("\\", "/"))
            is_glob = tool_name in ("Glob", "glob")
            if is_glob and pattern and not pattern.startswith(("*", "{")):
                if not any(bp in pattern + "/" for bp in blocked_paths):
                    return True            # glob по docs/**, ground/** и т.п.
                file_path = pattern
            else:
                file_path = (f"{pattern or '*'} (поиск без path — по всему проекту, включая "
                             f"{blocked_paths[0]}; укажи path вне него)")

        # Пропускаем чтение README, .md, .json, .yml — если не в src/
        if not any(bp in file_path for bp in blocked_paths):
            return True

        # Пропускаем тесты — по сегментам пути, не по подстроке (иначе src/main/Testimonials
        # ложно считался тестом → обход гейта)
        try:
            import _project
            if _project.is_test_path(file_path):
                return True
        except Exception:
            if "/test/" in file_path or "/Test" in file_path:
                return True

        # Прочитал ли агент grounding-excerpt — из журнала прогона (kind:"grounding");
        # прогон до миграции дочитывается со старого agent-evidence.jsonl.
        if current_phase_id == "01-grounding" and phase_status != "completed":
            mp = R.active_manifest(root)
            if mp is not None and FE.grounding_read(root, mp.parent.parent.name, mp.parent.name):
                return True  # grounding прочитан — снимаем блокировку

        _block(
            f"phase gate: фаза '{current_phase_id}' не завершена (status={phase_status}). "
            f"Инструмент '{tool_name}' заблокирован до её завершения. "
            f"Агент '{agent_type or '?'}' пытается читать: {file_path or command[:80]}. "
            f"Требуется завершить текущую фазу или прочитать grounding-excerpt.json."
        )
        return _deny()

    # ════════════════════════════════════════════════════════════════════
    # Проверка 3: зависимости фазы выполнены
    # ════════════════════════════════════════════════════════════════════
    for dep_id in phase.get("depends_on", []):
        dep = next((p for p in gate.get("phases", []) if p["id"] == dep_id), None)
        if dep and dep.get("status") not in ("completed", "skipped"):
            _block(
                f"phase gate: фаза '{current_phase_id}' зависит от '{dep_id}', "
                f"но та не завершена (status={dep.get('status')}). "
                f"Сначала заверши зависимую фазу."
            )
            return _deny()

    return True


# ── Разбор команды: argv ГЕЙТЯЩЕГОСЯ сегмента, а не токены всей строки ────────────────
# Все четыре R4-проверки ниже искали readonly-флаг (`--list`/`--dry-run`) среди токенов
# ВСЕЙ командной строки: `shlex.split` не срезает комментарии и не разделяет операторы
# оболочки. Флаг можно было дописать где угодно — гейт снимался, ничего не запуская:
#   `override_judge.py --judge coverage-judge  # не использую --list`  → rc 0 (было)
#   `rollback.py --to-step 02-sdd && echo done --dry-run`              → rc 0 (было)
#   `config.py repin # --dry-run`                                      → rc 0 (было)
# Устоял только check_skip_judges — и то случайно (он требует флаг, а не отсутствие).
# Флаг обязан читаться из argv ТОГО сегмента, который запускает гейтящийся скрипт.
_SHELL_OPS = frozenset(("&&", "||", ";", "|", "&", ">", ">>", "<", "<<", "(", ")", "\n"))


def _command_segments(command: str) -> "list[list[str]]":
    """Команда → argv по сегментам оболочки; комментарии отсечены, пустые сегменты убраны."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = "#"
        toks = list(lex)
    except ValueError:  # незакрытая кавычка — грубый разбор, но комментарий всё равно режем
        toks = []
        for t in command.split():
            if t.startswith("#"):
                break
            toks.append(t)
    segments: "list[list[str]]" = [[]]
    for t in toks:
        if t in _SHELL_OPS:
            segments.append([])
        else:
            segments[-1].append(t)
    return [s for s in segments if s]


def _gated_argv(command: str, pattern: str) -> "list[str]":
    """argv сегмента, запускающего гейтящийся скрипт (`pattern` по тексту сегмента).

    Пустой список = сегмент не опознан, ХОТЯ по всей строке совпадение было. Вызывающие
    обязаны трактовать это как «флага нет» (то есть идти к deny), а не как readonly:
    нераспознанный разбор не должен снимать R4-гейт."""
    for argv in _command_segments(command):
        if re.search(pattern, " ".join(argv)):
            return argv
    return []


def check_gate_override(command: str, root: Path) -> str | None:
    """R4-класс: снятие детерминированного гейта через override_judge.py требует
    approval-маркера ground/approvals/gate-override-<judge>.json (кладётся ТОЛЬКО после
    явного согласия пользователя). Возвращает причину блокировки или None (пропустить).

    --list/--remove свободны: чтение и ВОССТАНОВЛЕНИЕ enforcement'а не гейтятся.
    Держится всегда (и вне пайплайна) — как deny-first для R4+. Ошибка разбора →
    fail-CLOSED (снятие гейта без ясности опаснее ложного блока)."""
    try:
        policy = R.load_policy().get("gate_override") or {}
        pat = policy.get("command_pattern", r"override_judge\.py")
        if not command or not re.search(pat, command):
            return None
        # readonly (--list/--remove) свободны — но проверяем по РЕАЛЬНЫМ токенам-аргументам,
        # а не подстрокой: иначе `--reason "cleanup --list"` ложно трактуется как readonly (обход).
        argv = _gated_argv(command, pat)
        ro_flags = policy.get("readonly_arg_flags") or ["--list", "--remove"]
        if any(f in argv for f in ro_flags):
            return None
        if "--batch" in argv or any(a.startswith("--batch=") for a in argv):
            # Судьи лежат в YAML/JSON, командной строки хук не хватает. Маркер на КАЖДУЮ
            # запись батча сверяет сам override_judge (второй слой) — ровно как record_approval
            # держит цитату для своих батчей.
            return None
        prefix = policy.get("approval_prefix", "gate-override")
        # `--judge a,b,c` снимает три гейта одним вызовом, поэтому и маркер нужен на каждого.
        # Прежний регэксп брал имя до первой запятой: согласие на одного судью открывало
        # список любой длины.
        judges = [j.strip() for j in _opt_value(argv, "--judge").split(",") if j.strip()]
        keys = [_safe_key(f"{prefix}-{j}") for j in judges]
        missing = [k for k in keys if not _approval_valid(root, k)]
        if keys and not missing:
            return None
        if len(missing) > 1:
            key = "<ключ>"
            need = f"approval-маркер на КАЖДОГО судью — не хватает: {', '.join(missing)}"
        else:
            key = missing[0] if missing else f"{prefix}-<judge>"
            need = f"approval-маркер '{key}'"
        exists_no_prov = any(R.approval_exists(root, k) for k in missing)
        prov_note = (
            " Маркер есть, но БЕЗ провенанса record_approval — рукописный маркер не считается "
            "(его мог выписать сам агент). " if exists_no_prov else " "
        )
        if not judges:
            prov_note = " В команде нет --judge — ключ маркера не резолвится. "
        return (
            f"снятие гейта (override_judge) — R4-класс, нужен {need}"
            f" (журнал ground/approvals.jsonl, пишет ТОЛЬКО record_approval.py).{prov_note}Порядок: (1) останови работу и спроси "
            f"пользователя (покажи, что не сходится); (2) ТОЛЬКО после явного «да» зафиксируй "
            f"согласие СКРИПТОМ pipeline-state/scripts/record_approval.py --key {key} "
            f"--approved-by user --reason \"<кто/почему>\" --evidence \"<дословная цитата пользователя>\" (он штампует провенанс; прямой Write "
            f"в approvals/ заблокирован state-write-guard); (3) повтори команду. Маркер "
            f"одноразовый — потребляется созданием override. "
            f"Молча снимать гейт нельзя. --list/--remove не гейтятся."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки gate-override ({e})."


# ── R4-класс: запись САМОГО согласия человека ────────────────────────────────────────
# Самый опасный отказ харнеса: согласие было самомаркируемым. `record_approval.py` не гейтил
# НИ ОДИН хук (проверено прогоном: gate-guard / state-write-guard / destructive-blocker /
# sod-enforcer — все exit 0), а DENY-баннеры соседних R4-гейтов печатали готовую к вставке
# команду. Цепочка «прочитал баннер → выписал себе маркер → снял гейт» проходила целиком
# внутри модели, человека в ней не было ни на одном шаге.
#
# Чинится тем, что согласие обязано ПРИЙТИ ИЗВНЕ: `--evidence` с дословной цитатой
# пользователя, и цитата сверяется с репликами ПОЛЬЗОВАТЕЛЯ в транскрипте сессии
# (на PreToolUse поле `transcript_path` есть; замерено e2e на qwen-code 0.21.14,
# docs/v2/01-runtime-config-surface.md).
#
# Именно реплики пользователя, а не весь транскрипт. qwen пишет запись `assistant` вместе с
# `functionCall` и его аргументами в chats/<session>.jsonl в конце стрима — ДО запуска
# инструмента (recordAssistantTurn). То есть к PreToolUse в транскрипте уже лежит сама
# команда `record_approval … --evidence "<цитата>"`, и подстрока по всему файлу находила
# цитату в ней же: проходила любая выдумка без кавычек (не проходили только цитаты с `"` —
# в JSONL они экранированы). Туда же попадали собственные реплики модели и вывод её команд
# (`echo "<фраза>"` в tool_result) — согласие можно было «процитировать» у самого себя.
#
# Гейт стоит на классах, которые СНИМАЮТ enforcement (FE.CONSENT_PREFIXES). Approval'ы
# плана (fix-plan-*, jira-plan-*, <doc>-approved-*) не гейтятся: они двигают прогон вперёд,
# а не убирают защиту, и их брифы и так проводят через явный вопрос.
_EVIDENCE_MIN_CHARS = 12          # «да», «ок», «+» цитатой пользователя не являются
# Потолок чтения транскрипта: файл читается целиком (реплика с согласием может быть далеко
# от хвоста — после неё модель успевает прочитать десятки файлов), но не бесконечно.
_TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024


# Ответ на `ask_user_question` — тоже слова пользователя, хотя приходит результатом
# инструмента: рантайм пишет туда выбранный вариант либо введённый текст. Брифы и баннеры
# велят спрашивать именно им, а сверка брала одни записи `type:"user"` — и согласие, данное
# по правилам, отбивалось как «пользователь этого не говорил». Берём ТОЛЬКО значения ответов:
# заголовок вопроса (`**<header>**:`) пишет модель. Остальные tool_result — вывод команд
# модели (`echo "<фраза>"`), их не берём по-прежнему.
_ASK_TOOLS = ("ask_user_question", "AskUserQuestion")
_ASK_ANSWER_RE = re.compile(r"^\s*\*\*.+?\*\*:\s*(.+?)\s*$")


def _user_texts(rec) -> "list[str]":
    """Тексты, которые в этой записи транскрипта написал ПОЛЬЗОВАТЕЛЬ; [] — запись не его.

    qwen/gigacode: `{"type":"user","message":{"parts":[{"text":…}]}}` (и плоская форма
    `{"type":"user","parts":[…]}`); результаты инструментов там — отдельный тип
    `tool_result`, из них берутся только ответы `ask_user_question`. Claude Code:
    `{"type":"user","message":{"content":"…" | [{"type":"text","text":…}]}}`; результаты
    инструментов — блоки `tool_result` в той же user-записи, их не берём."""
    if not isinstance(rec, dict):
        return []
    msg = rec.get("message")
    if rec.get("type") == "tool_result":
        parts = msg.get("parts") if isinstance(msg, dict) else None
        out = []
        for p in parts if isinstance(parts, list) else []:
            fr = p.get("functionResponse") if isinstance(p, dict) else None
            if not isinstance(fr, dict) or fr.get("name") not in _ASK_TOOLS:
                continue
            resp = fr.get("response")
            text = resp.get("output") if isinstance(resp, dict) else resp
            if isinstance(text, str):
                answers = [m.group(1) for m in map(_ASK_ANSWER_RE.match, text.splitlines()) if m]
                if answers:
                    out.append("\n".join(answers))
        return out
    if rec.get("type") != "user":
        return []
    if not isinstance(msg, dict):
        msg = rec if isinstance(rec.get("parts"), list) else None
        if msg is None:
            return []
    out = []
    parts = msg.get("parts")
    if isinstance(parts, list):
        out += [p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
    content = msg.get("content")
    if isinstance(content, str):
        out.append(content)
    elif isinstance(content, list):
        out += [b["text"] for b in content
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return out


def _main_transcript(transcript_path: str) -> str:
    """Транскрипт ОСНОВНОЙ сессии. У субагента qwen ведёт свой файл
    `<проект>/subagents/<сессия>/agent-*.jsonl`, и реплика `user` в нём — постановка задачи,
    которую написал оркестратор, то есть модель. Согласием она быть не может: если рантайм
    отдаст хуку путь субагента, сверяемся с `<проект>/chats/<сессия>.jsonl`."""
    p = Path(transcript_path)
    if len(p.parents) >= 3 and p.parents[1].name == "subagents":
        return str(p.parents[2] / "chats" / f"{p.parent.name}.jsonl")
    return transcript_path


def _transcript_utterances(transcript_path: str) -> "list[str] | None":
    """Реплики пользователя в этой сессии по порядку (одна запись — одна реплика); None —
    сверить не с чем (файла нет либо в нём не распознано ни одной реплики — формат рантайма
    не тот)."""
    if not transcript_path:
        return None
    try:
        with open(_main_transcript(transcript_path), "rb") as f:
            raw = f.read(_TRANSCRIPT_MAX_BYTES)
    except Exception:  # noqa: BLE001 — рантайм не отдал путь/файл недоступен
        return None
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if '"user"' not in line and '"tool_result"' not in line:   # дешёвый префильтр
            continue
        try:
            texts = _user_texts(json.loads(line))
        except ValueError:
            continue
        if texts:
            out.append("\n".join(texts))
    return out or None


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _quote_in(evidence: str, utterance: str) -> bool:
    """Цитата — целые слова реплики (по нормализованным пробелам, регистронезависимо): из
    «когда-нибудь потом» не вырезается «да-нибудь потом»."""
    q, u = _norm_ws(evidence), _norm_ws(utterance)
    if not q:
        return False
    start = u.find(q)
    while start >= 0:
        end = start + len(q)
        if ((start == 0 or not (u[start - 1].isalnum() or u[start - 1] == "_"))
                and (end == len(u) or not (u[end].isalnum() or u[end] == "_"))):
            return True
        start = u.find(q, start + 1)
    return False


def _evidence_problem(evidence: str, utterances: "list[str]") -> str | None:
    """Почему цитата не засчитывается согласием; None — засчитывается.

    Согласие — ответ на вопрос о ЭТОМ действии, поэтому цитата берётся из ПОСЛЕДНЕЙ реплики
    пользователя: модель спросила — пользователь ответил — модель фиксирует. Раньше годилась
    любая реплика сессии, и согласие собиралось из старых: из постановки задачи, из ответа на
    вопрос BRD-интервью «Да, нужна интеграция с Kafka», из отказа. Реплика, начатая
    отрицанием («Нет. Да, понимаю, но…»), согласием не бывает, какой бы кусок из неё ни
    процитировать."""
    last = utterances[-1]
    if not _quote_in(evidence, last):
        if any(_quote_in(evidence, u) for u in utterances[:-1]):
            return ("цитата из СТАРОЙ реплики пользователя. Согласие — ответ на вопрос об ЭТОМ "
                    "действии: спроси сейчас и процитируй его ответ.")
        return ("переданной цитаты нет ни в одной реплике пользователя — значит, он этого не "
                "говорил.")
    first = FE.consent_words(last)[:1]
    if first and first[0] in FE.NEGATION_WORDS:
        return (f"последняя реплика пользователя начинается с отрицания («{first[0]}») — это "
                f"отказ, а не согласие.")
    return FE.consent_quote_problem(evidence)


# Батч согласий: ключи лежат в файле, которого хук в командной строке не видит, а второй слой
# (record_approval) транскрипта не видит вовсе — цитату там сверять не с чем. Через батч
# согласие выписывалось с мусорной цитатой `zzzzzzzzzzzzzzzzzz`, и `git reset --hard` после
# него проходил (боевой прогон v0.4.6, E-CONSENT-BATCH). Согласие — по одному вызову на ключ.
_BATCH_KEY_RE = re.compile(r"""["']?\bkey["']?\s*:\s*["']?([^"'\s,}\]#]+)""")


def _batch_keys(path: str, cwd: str) -> "list[str]":
    """Ключи approval'ов из batch-файла: разбор YAML/JSON, плюс текстовый поиск `key:` — на
    случай, если хуку файл не разобрать (нет PyYAML), а скрипту — разобрать."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd or ".") / p
    try:
        with open(p, "rb") as f:
            text = f.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — файла нет: record_approval сам откажет
        return []
    keys = [m.group(1) for m in _BATCH_KEY_RE.finditer(text)]
    data = None
    try:
        data = json.loads(text)
    except ValueError:
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(text)
        except Exception:  # noqa: BLE001
            data = None
    if isinstance(data, dict):
        data = data.get("approvals")
    if isinstance(data, list):
        keys += [str(i["key"]) for i in data if isinstance(i, dict) and i.get("key")]
    return keys


def _opt_value(argv: "list[str]", name: str) -> str:
    """Значение опции из argv: `--opt value` и `--opt=value`. Пусто — опции нет.

    Читать значение регуляркой по склеенной строке нельзя: shlex уже снял кавычки, и
    `--evidence "цитата из нескольких слов"` обрезалось бы по первому пробелу.
    """
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1].strip()
        if a.startswith(name + "="):
            return a[len(name) + 1:].strip()
    return ""


def check_record_approval(command: str, root: Path, transcript_path: str,
                          cwd: str = "") -> str | None:
    """R4-класс: `record_approval.py` для ключей, снимающих enforcement, требует `--evidence`
    — согласие из ПОСЛЕДНЕЙ реплики пользователя (см. _evidence_problem). Возвращает причину
    блокировки или None.

    Второй слой — сам record_approval.py проверяет, что цитата выражает согласие (гейт
    держится и мимо харнеса). Ошибка разбора → fail-CLOSED."""
    try:
        if not command or not re.search(r"record_approval\.py", command):
            return None
        argv = _gated_argv(command, r"record_approval\.py")
        if not argv:
            return None
        tail = ("\n  Порядок: (1) покажи пользователю, ЧТО снимается и почему, и спроси прямо — "
                "ask_user_question с вариантом-фразой «Да, <действие>» либо вопросом в чате; "
                "(2) повтори команду с --evidence \"<его ответ дословно>\". Цитата сверяется с "
                "ПОСЛЕДНЕЙ репликой пользователя в транскрипте и обязана быть согласием "
                "(«да, …», «согласен …», «подтверждаю …»): старые реплики, постановка задачи, "
                "свои реплики и вывод команд не считаются, пересказ своими словами тоже.")
        batch = _opt_value(argv, "--batch")
        if batch or "--batch" in argv:
            consent = sorted({k for k in map(_safe_key, _batch_keys(batch, cwd))
                              if k and FE.consent_required(k)})
            if not consent:
                return None                # согласия плана/документов батчем — можно
            return (f"согласия, снимающие enforcement ({', '.join(consent)}), батчем не "
                    f"записываются: цитату в файле сверить не с чем. По одному вызову на ключ: "
                    f"record_approval.py --key <ключ> … --evidence \"<ответ пользователя>\"."
                    + tail)
        key = _safe_key(_opt_value(argv, "--key"))
        if not key or not FE.consent_required(key):
            return None                    # approval плана/документа — не этот класс

        evidence = _opt_value(argv, "--evidence")
        head = (f"запись согласия по ключу '{key}' снимает enforcement — цитата пользователя "
                f"обязательна. ")
        if len(evidence) < _EVIDENCE_MIN_CHARS:
            return head + (f"В команде нет --evidence (или он короче {_EVIDENCE_MIN_CHARS} "
                           f"символов: «да»/«ок» цитатой не считаются).") + tail

        utterances = _transcript_utterances(transcript_path)
        if utterances is None:
            # Сверить не с чем. Не запираем единственный аварийный выход наглухо:
            # --evidence уже обязателен, record_approval проверит, что это согласие, и
            # цитата уйдёт в журнал под аудит.
            print(f"[gate-guard] WARN: реплики пользователя в транскрипте не найдены (файла нет "
                  f"либо формат рантайма не распознан) — цитата --evidence для '{key}' "
                  f"записана БЕЗ сверки. Проверяется постфактум по ground/approvals.jsonl.",
                  file=sys.stderr)
            return None
        problem = _evidence_problem(evidence, utterances)
        if problem:
            return head + problem[0].upper() + problem[1:] + tail
        return None
    except Exception as e:
        return f"deny-first: ошибка проверки record_approval ({e})."


def check_policy_repin(command: str, root: Path) -> str | None:
    """R4-класс: `config.py repin` переснимает политику ИДУЩЕГО прогона — approval обязателен.

    Прогон фиксирует политику снимком на init.py ровно для того, чтобы его шаги нельзя было
    закрыть под разными правилами. `repin` эту фиксацию снимает, а правит он пороги, которыми
    харнес меряет САМ СЕБЯ: coverage_threshold и max_judge_iterations (лимит ре-итераций судьи —
    тот самый «⛔ STOP: остановись и спроси пользователя»). Без гейта связка `set` + `repin` —
    две команды уровня R1 — позволяла модели поднять себе лимит и снять эскалацию.

    Правка policy.json БЕЗ repin не гейтится намеренно: идущий прогон её не видит (он читает
    свой снимок), она применится со следующего — это штатный путь, а не обход.

    `--dry-run` (показать расхождение) свободен. Ошибка разбора → fail-CLOSED.
    """
    try:
        policy = R.load_policy().get("policy_repin") or {}
        pat = policy.get("command_pattern", r"config\.py[^\n]*\brepin\b")
        if not command or not re.search(pat, command):
            return None
        ro_flags = policy.get("readonly_arg_flags") or ["--dry-run"]
        if any(f in _gated_argv(command, pat) for f in ro_flags):
            return None
        m = re.search(r"--feature[\s=]+[\"']?([\w.-]+)", command)
        feat = m.group(1) if m else ""
        prefix = policy.get("approval_prefix", "policy-repin")
        key = f"{prefix}-{feat}" if feat else prefix
        if feat and _approval_valid(root, key):
            return None
        exists_no_prov = feat and R.approval_exists(root, key) and not _approval_valid(root, key)
        prov_note = (
            " Маркер есть, но БЕЗ провенанса record_approval — рукописный маркер не считается "
            "(его мог выписать сам агент). " if exists_no_prov else " "
        )
        args_note = "" if feat else " В команде нет --feature — ключ маркера не резолвится."
        return (
            f"переснятие политики прогона (config.py repin) — R4-класс, нужен approval-маркер "
            f"'{key}' (журнал ground/approvals.jsonl, пишет ТОЛЬКО record_approval.py)."
            f"{prov_note}{args_note} Прогон идёт по политике, зафиксированной на init.py — это "
            f"защита от «шаги 1-5 закрылись под одним порогом, шаги 6-10 под другим». Порядок: "
            f"(1) останови работу и покажи пользователю, ЧТО меняется (`config.py repin "
            f"--dry-run` печатает расхождение дайджестов); (2) ТОЛЬКО после явного «да» "
            f"зафиксируй согласие СКРИПТОМ pipeline-state/scripts/record_approval.py "
            f"--key {key} --approved-by user --reason \"<почему>\" --evidence \"<дословная цитата пользователя>\"; (3) повтори команду. "
            f"Маркер одноразовый. Правка policy.json БЕЗ repin не гейтится — она применится "
            f"со следующего прогона, и это штатный путь."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки policy-repin ({e})."


# Переключатели enforcement, правка которых требует approval. Дублируют
# risk-policy.json:quality_downgrade.params — удаление секции из политики не должно молча
# открывать обход. В том же namespace quality.* живут ФАКТЫ о проекте (build_command,
# test_command, jacoco_configured, test_layer): их настройка — штатная работа config-helper,
# и под гейт они НЕ попадают. Обходные ослабления (coverage_exclude_globs, no_test_layers)
# попадают: вывести код из-под гейта можно и не выключая флаг.
_QUALITY_GATED_PARAMS = frozenset((
    "quality.tdd", "quality.eval_enabled", "quality.coverage_threshold",
    "quality.eval_threshold", "quality.max_judge_iterations", "quality.max_step_reopens",
    "quality.architecture_check", "quality.tautology_check", "quality.traceability_check",
    "quality.module_dep_policy", "quality.coverage_exclude_globs", "quality.no_test_layers",
    "sdd.security_gate", "autonomy.mode",
    "risk.autonomy_auto_max", "risk.default_level", "security_review",
))


def _positional_param(argv: "list[str]") -> str:
    """Первый позиционный аргумент после подкоманды `set` — id параметра.

    Именно позиционный: `--reason "выключаю quality.tdd"` не должен считаться правкой."""
    try:
        i = argv.index("set")
    except ValueError:
        return ""
    skip_next = False
    for a in argv[i + 1:]:
        if skip_next:
            skip_next = False
            continue
        if a.startswith("--"):
            if "=" not in a and a not in ("--confirm", "--dry-run", "--json"):
                skip_next = True      # опция со значением: `--skill S`
            continue
        return a
    return ""


def check_quality_downgrade(command: str, root: Path) -> str | None:
    """R4-класс: `config.py set quality.*|security.*` — правка собственных порогов харнеса.

    Прямая запись в ground/policy.json режется state-write-guard, а санкционный скрипт не
    гейтился ничем: `set quality.tdd false` / `set quality.eval_enabled false` проходили все
    хуки с exit 0. Снимок политики тут не защита — он фиксируется на init.py, а окно ДО него
    открыто, и policy.json переживает прогон, то есть отключение едет и в соседние.

    Чтение (`get`/`list`) под pattern не подпадает, `--dry-run` свободен.
    Ошибка разбора → fail-CLOSED."""
    try:
        policy = R.load_policy().get("quality_downgrade") or {}
        pat = policy.get("command_pattern", r"config\.py[^\n]*\bset\b")
        if not command or not re.search(pat, command):
            return None
        ro_flags = policy.get("readonly_arg_flags") or ["--dry-run"]
        argv = _gated_argv(command, pat)
        if any(f in argv for f in ro_flags):
            return None
        # Гейтится КОНКРЕТНЫЙ параметр, а не namespace: в quality.* живут и факты о проекте
        # (build_command, test_command, jacoco_configured) — их настройка не должна требовать
        # approval. Дефолты продублированы в коде: удаление секции из risk-policy.json не
        # должно молча снимать enforcement.
        gated = set(policy.get("params") or _QUALITY_GATED_PARAMS)
        prefixes = tuple(policy.get("param_prefixes") or ("security.",))
        param = _positional_param(argv)
        if not param or not (param in gated or param.startswith(prefixes)):
            return None
        prefix = policy.get("approval_prefix", "policy-downgrade")
        key = _safe_key(f"{prefix}-{param}")
        if _approval_valid(root, key):
            return None
        exists_no_prov = R.approval_exists(root, key) and not _approval_valid(root, key)
        prov_note = (
            " Маркер есть, но БЕЗ провенанса record_approval — рукописный маркер не считается "
            "(его мог выписать сам агент). " if exists_no_prov else " "
        )
        return (
            f"правка '{param}' — переключатель enforcement, R4-класс: нужен approval-маркер '{key}' (журнал "
            f"ground/approvals.jsonl, пишет ТОЛЬКО record_approval.py).{prov_note}"
            f"quality.*/security.* — это пороги, которыми харнес меряет СОБСТВЕННУЮ работу: "
            f"понижение снимает enforcement с себя же, и не на прогон, а на весь проект "
            f"(policy.json действует и на соседние прогоны). Порядок: (1) прогони гейт, "
            f"а не снимай его; (2) гейт объективно неприменим — покажи пользователю, ЧТО "
            f"меняется, и спроси; (3) после явного «да» — pipeline-state/scripts/"
            f"record_approval.py --key {key} --approved-by user --reason \"<почему>\" "
            f"--evidence \"<дословная цитата пользователя>\"; (4) повтори команду. "
            f"Чтение (`config.py get`) и `--dry-run` не гейтятся."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки quality/security-downgrade ({e})."


def _subcommand_args(argv: "list[str]", sub: str) -> "list[str]":
    """Позиционные аргументы после подкоманды `sub` config.py; опции со значением пропущены."""
    try:
        i = argv.index(sub)
    except ValueError:
        return []
    out, skip_next = [], False
    for a in argv[i + 1:]:
        if skip_next:
            skip_next = False
            continue
        if a.startswith("--"):
            if "=" not in a and a not in ("--confirm", "--dry-run", "--json"):
                skip_next = True
            continue
        out.append(a)
    return out


def _mandatory_phase(phase_id: str) -> bool:
    """Обязательная ли фаза (resolve_phases.mandatory_phase_ids). Список не загрузился —
    обязательной считается любая: снять фазу без ясности опаснее лишнего вопроса."""
    try:
        import resolve_phases as _rp            # каталог скриптов уже в sys.path (pipeline_phases)
        return phase_id in _rp.mandatory_phase_ids()
    except Exception:  # noqa: BLE001
        return True


def check_policy_structure_edit(command: str, root: Path) -> str | None:
    """R4-класс: ослабить политику санкционным скриптом мимо `set` — убрать паттерн из
    risk-policy (`config.py risk list-remove`), поднять cap роли (`risk cap-set`), выключить
    обязательную фазу (`config.py phase disable 04-tdd`, либо `enable/add --enabled-by …`,
    делающий её условной). Требует approval-маркера policy-downgrade-risk.<ключ> /
    policy-downgrade-phase.<фаза> с цитатой пользователя. Возвращает причину блокировки или None.

    Регрессия: этим командам хватало `--confirm`, а его модель ставит сама — gate-guard ловил
    только `config.py set`, и `risk list-remove pii_patterns …` снимал детектор ПДн с exit 0.
    Добавить паттерн или включить фазу — свободно: это усиление. Второй слой — config.py.
    Ошибка разбора → fail-CLOSED."""
    try:
        if not command or not re.search(r"config\.py", command):
            return None
        argv = _gated_argv(command, r"config\.py")
        if "--dry-run" in argv:
            return None
        key = what = ""
        risk = _subcommand_args(argv, "risk")
        phase = _subcommand_args(argv, "phase")
        if risk and risk[0] in ("list-remove", "cap-set"):
            target = "agent_caps" if risk[0] == "cap-set" else (risk[1] if len(risk) > 1 else "")
            key = _safe_key(f"policy-downgrade-risk.{target or '<ключ>'}")
            what = (f"`config.py risk {risk[0]}` ослабляет risk-policy.json ({target or '?'}): "
                    f"детекторы и лестница рисков — это защита харнеса, а не настройка проекта")
        elif len(phase) >= 2 and _mandatory_phase(phase[1]) and (
                phase[0] == "disable"
                or any(a == "--enabled-by" or a.startswith("--enabled-by=") for a in argv)):
            key = _safe_key(f"policy-downgrade-phase.{phase[1]}")
            what = (f"`config.py phase {phase[0]} {phase[1]}` выключает (или делает условной) "
                    f"обязательную фазу — её проверки не пойдут ни в одном следующем прогоне")
        if not key or _approval_valid(root, key):
            return None
        return (
            f"{what}. R4-класс: нужен approval-маркер '{key}' (журнал ground/approvals.jsonl, "
            f"пишет ТОЛЬКО record_approval.py). `--confirm` согласием не считается — его ставит "
            f"сама модель. Порядок: (1) покажи пользователю, ЧТО ослабляется и почему; (2) после "
            f"явного «да» — pipeline-state/scripts/record_approval.py --key {key} --approved-by "
            f"user --reason \"<почему>\" --evidence \"<дословная цитата пользователя>\"; "
            f"(3) повтори команду. Маркер одноразовый. Добавить паттерн (`risk list-add`) или "
            f"включить фазу — не гейтится."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки правки политики ({e})."


def check_skip_judges(command: str, root: Path) -> str | None:
    """R4-класс: `update.py --skip-judges` снимает ВСЕ гейты закрытия шага (судьи, gate-result,
    subagent-origin, обязательные решения, артефакты) — bypass в одну опцию. Требует
    approval-маркера `skip-judges-<feature>`. Возвращает причину блокировки или None.

    Второй слой — сам `update.py` валидирует маркер (гейт держится и мимо харнеса). Ошибка
    разбора → fail-CLOSED: снятие всех гейтов без ясности опаснее ложного блока."""
    try:
        policy = R.load_policy().get("skip_judges") or {}
        pat = policy.get("command_pattern", r"update\.py")
        flag = policy.get("arg_flag", "--skip-judges")
        if not command or flag not in command or not re.search(pat, command):
            return None
        argv = _gated_argv(command, pat)
        if argv and flag not in argv:
            return None                      # флаг лишь упомянут в строке-аргументе
        # argv пуст = сегмент не опознан при совпадении по строке → к deny (fail-CLOSED)
        m = re.search(r"--feature[\s=]+[\"']?([\w.-]+)", command)
        feat = m.group(1) if m else ""
        prefix = policy.get("approval_prefix", "skip-judges")
        key = f"{prefix}-{feat}" if feat else f"{prefix}-<feature>"
        if feat and _approval_valid(root, key):
            return None
        prov_note = (" Маркер есть, но БЕЗ провенанса record_approval — рукописный маркер не "
                     "считается. " if feat and R.approval_exists(root, key) else " ")
        return (
            f"`--skip-judges` снимает ВСЕ гейты закрытия шага — R4-класс, нужен approval-маркер "
            f"'{key}' (журнал ground/approvals.jsonl, пишет ТОЛЬКО record_approval.py).{prov_note}Легитимный случай один: восстановление "
            f"статусов после init.py --force. Порядок: (1) объясни пользователю, зачем обходить "
            f"гейты, и спроси; (2) после явного «да» — pipeline-state/scripts/record_approval.py "
            f"--key {key} --approved-by user --reason \"<зачем обход>\" --evidence \"<дословная цитата пользователя>\"; (3) повтори команду. "
            f"Маркер одноразовый — тратится на одно закрытие; утверждение BRD/SDD флаг не снимает. "
            f"Штатное закрытие шага этого флага НЕ требует — прогони гейт фазы."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки --skip-judges ({e})."


def check_rollback(command: str, root: Path) -> str | None:
    """R4-класс: откат пайплайна к шагу (rollback.py) уничтожает рабочие результаты — код,
    evidence закрытых шагов — и порождает сирот в Jira/PR. Запуск требует approval-маркера
    ground/approvals/rollback-<feature>-<to-step>.json (record_approval ТОЛЬКО после явного
    «да» пользователя на план --dry-run; маркер одноразовый — rollback.py потребляет его).
    Возвращает причину блокировки или None.

    --dry-run/--list (план и история — readonly) свободны. Дефолты зашиты в код — удаление
    секции rollback из risk-policy.json не выключает enforcement молча. Ошибка разбора →
    fail-CLOSED (откат без ясности опаснее ложного блока)."""
    try:
        policy = R.load_policy().get("rollback") or {}
        pat = policy.get("command_pattern", r"rollback\.py")
        if not command or not re.search(pat, command):
            return None
        # readonly по РЕАЛЬНЫМ токенам-аргументам, не подстрокой (обход через
        # `--feature "x --dry-run"` не должен трактоваться как readonly)
        ro_flags = policy.get("readonly_arg_flags") or ["--dry-run", "--list"]
        if any(f in _gated_argv(command, pat) for f in ro_flags):
            return None
        m = re.search(r"--feature[\s=]+[\"']?([\w.-]+)", command)
        feat = m.group(1) if m else ""
        mt = re.search(r"--to-(?:step|phase)[\s=]+[\"']?([\w.-]+)", command)
        target = mt.group(1) if mt else ""
        prefix = policy.get("approval_prefix", "rollback")
        key = f"{prefix}-{feat}-{target}" if feat and target else f"{prefix}-<feature>-<to-step>"
        if feat and target and _approval_valid(root, key):
            return None
        exists_no_prov = (feat and target and R.approval_exists(root, key)
                          and not _approval_valid(root, key))
        prov_note = (
            " Маркер есть, но БЕЗ провенанса record_approval — рукописный маркер не считается "
            "(его мог выписать сам агент). " if exists_no_prov else " "
        )
        args_note = "" if (feat and target) else (
            " В команде нет --feature/--to-step — ключ маркера не резолвится."
        )
        return (
            f"откат пайплайна (rollback.py) — R4-класс, нужен approval-маркер '{key}'"
            f" (журнал ground/approvals.jsonl, пишет ТОЛЬКО record_approval.py).{prov_note}Порядок: (1) покажи пользователю план "
            f"отката (rollback.py ... --dry-run: какие шаги сбросятся, какой код "
            f"восстановится, какие сироты останутся); (2) ТОЛЬКО после явного «да» зафиксируй "
            f"согласие СКРИПТОМ pipeline-state/scripts/record_approval.py --key {key} "
            f"--approved-by user --reason \"<кто/почему>\" --evidence \"<дословная цитата пользователя>\" (он штампует провенанс; прямой Write "
            f"в approvals/ заблокирован state-write-guard); (3) повтори команду. Маркер "
            f"одноразовый — потребляется откатом.{args_note} --dry-run/--list не гейтятся."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки rollback ({e})."


def _archive_drop_target(argv: "list[str]", put_force_prefix: str,
                         abandon_prefix: str) -> "tuple[str, str] | None":
    """(префикс ключа, слаг) для команды archive.py, СНИМАЮЩЕЙ прогон с активных; иначе None.

    Снимают двое: `abandon <feature>` и `put <slug> --force` (гейты готовности обойдены —
    уезжает незавершённый прогон). Остальные подкоманды и --dry-run ничего не уносят."""
    idx = next((i for i, a in enumerate(argv) if re.search(r"archive\.py$", a)), None)
    if idx is None:
        return None
    rest = argv[idx + 1:]
    if "--dry-run" in rest:
        return None
    pos, i = [], 0
    while i < len(rest):
        if rest[i] in ("--project", "--skill", "--reason"):
            i += 2                       # опция со значением: значение — не подкоманда
            continue
        if not rest[i].startswith("-"):
            pos.append(rest[i])
        i += 1
    if not pos:
        return None
    slug = pos[1] if len(pos) > 1 else ""
    if pos[0] == "abandon":
        return abandon_prefix, slug
    if pos[0] == "put" and "--force" in rest:
        return put_force_prefix, slug
    return None


def check_archive_drop(command: str, root: Path) -> str | None:
    """R4-класс: снять прогон с активных — `archive.py abandon` либо `put --force`. Прогон
    перестаёт числиться живым и выпадает из резолва активной фичи; у `put --force` ещё и
    удаляются git-чекпойнты (у abandon — откладываются, вернёт `restore`). Требует
    approval-маркера abandon-<feature> /
    archive-force-<slug> с цитатой пользователя. Возвращает причину блокировки или None.

    Инцидент: preflight на двух живых прогонах подсказывал команду abandon, и модель сама
    сняла прогон, который сочла брошенным, — вместе с чекпойнтами. Какой прогон лишний, знает
    только пользователь. Второй слой — archive.py сверяет маркер сам. Ошибка разбора →
    fail-CLOSED."""
    try:
        policy = R.load_policy().get("archive_drop") or {}
        pat = policy.get("command_pattern", r"archive\.py")
        if not command or not re.search(pat, command):
            return None
        argv = _gated_argv(command, pat)
        target = _archive_drop_target(argv, policy.get("put_force_prefix", "archive-force"),
                                      policy.get("abandon_prefix", "abandon"))
        if target is None:
            return None
        prefix, slug = target
        key = _safe_key(f"{prefix}-{slug}") if slug else f"{prefix}-<feature>"
        if slug and _approval_valid(root, key):
            return None
        prov_note = (" Маркер есть, но БЕЗ провенанса record_approval — рукописный маркер не "
                     "считается. " if slug and R.approval_exists(root, key) else " ")
        return (
            f"снять прогон с активных (archive.py abandon / put --force) — R4-класс: прогон "
            f"перестаёт числиться живым, гейты поедут по другому (у put --force ещё и удалятся "
            f"git-чекпойнты). Нужен "
            f"approval-маркер '{key}' (журнал ground/approvals.jsonl, пишет ТОЛЬКО "
            f"record_approval.py).{prov_note}Какой прогон брошен, знает только пользователь — "
            f"в списке может быть тот, что идёт прямо сейчас. Порядок: (1) покажи `archive.py "
            f"status` и спроси, какой прогон снимать; (2) ТОЛЬКО после явного ответа — "
            f"pipeline-state/scripts/record_approval.py --key {key} --approved-by user "
            f"--reason \"<почему брошен>\" --evidence \"<дословная цитата пользователя>\"; "
            f"(3) повтори команду. Маркер одноразовый. --dry-run и status не гейтятся."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки archive.py ({e})."


# Pathspec «всё дерево»: точечный откат своего файла (`git checkout -- src/A.java`) — штатная
# работа и не гейтится; гейтится сброс ВСЕГО незакоммиченного.
_WHOLE_TREE = frozenset((".", "./", ":/", ":/*", "*"))


def _git_discard(argv: "list[str]") -> str:
    """Описание git-команды, стирающей незакоммиченную работу целиком; '' — не такая.

    reset --hard, clean -f, checkout/restore всего дерева (или checkout -f), switch
    --discard-changes|-f, stash drop|clear. Точечные формы и безопасные (`restore --staged`,
    `clean -n`, `reset` без --hard, `stash`/`stash pop`) — ''."""
    if not argv or os.path.basename(argv[0]) != "git" or len(argv) < 2:
        return ""
    sub, args = argv[1], argv[2:]
    forced = any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a)
                 for a in args)
    whole = any(a in _WHOLE_TREE for a in args)
    if sub == "reset" and "--hard" in args:
        return "git reset --hard"
    if sub == "clean" and forced and "-n" not in args and "--dry-run" not in args:
        return "git clean -f"
    if sub == "checkout" and (whole or "-f" in args or "--force" in args):
        return "git checkout всего дерева / -f"
    if sub == "restore" and whole and not ("--staged" in args and not
                                           ({"--worktree", "-W"} & set(args))):
        return "git restore всего дерева"
    if sub == "switch" and ("--discard-changes" in args or "-f" in args or "--force" in args):
        return "git switch --discard-changes"
    if sub == "stash" and args[:1] in (["drop"], ["clear"]):
        return f"git stash {args[0]}"
    return ""


def check_git_discard(command: str, root: Path) -> str | None:
    """R4-класс: git-команда стирает незакоммиченную работу целиком (reset --hard, clean -f,
    checkout/restore всего дерева, stash drop|clear). Требует approval-маркера `git-discard` с
    цитатой пользователя; маркер ОДНОРАЗОВЫЙ — тратится здесь же, на пропуске команды.
    Возвращает причину блокировки или None.

    Доставку (commit/push) форж не гейтит сознательно — это работа пользователя. Но эти
    команды уничтожают не доставку, а рабочее дерево: «вернуть в чистое состояние» сносит и
    несохранённую работу самого пользователя, а вернуть её нечем (tasks/015 п.5). Ошибка
    разбора → fail-CLOSED."""
    try:
        if not command or "git" not in command:
            return None
        policy = R.load_policy().get("git_discard") or {}
        key = _safe_key(policy.get("approval_key", "git-discard"))
        what = ""
        for argv in _command_segments(R.normalize_git_command(command)):
            what = _git_discard(argv)
            if what:
                break
        if not what:
            return None
        if _approval_valid(root, key):
            FE.revoke_approval(root, key, reason=f"согласие потрачено: {command[:120]}")
            return None
        return (
            f"`{what}` стирает незакоммиченную работу целиком — и твою, и пользователя; вернуть "
            f"её нечем. R4-класс: нужен approval-маркер '{key}' (журнал ground/approvals.jsonl, "
            f"пишет ТОЛЬКО record_approval.py). Порядок: (1) покажи пользователю `git status`, что "
            f"пропадёт, и спроси; (2) после явного «да» — pipeline-state/scripts/record_approval.py "
            f"--key {key} --approved-by user --reason \"<почему>\" --evidence \"<дословная цитата "
            f"пользователя>\"; (3) повтори команду. Маркер одноразовый — тратится на неё. "
            f"Откатить СВОЙ файл — `git checkout -- <файл>` / `git restore <файл>` — не гейтится."
        )
    except Exception as e:
        return f"deny-first: ошибка проверки git-команды ({e})."


def _kind(tool_name: str, command: str) -> str:
    """git commit/push НЕ классифицируем: доставку делает пользователь сам (промптом/руками),
    пайплайн заканчивается верифицированным артефактом и git-команды не гейтит."""
    if tool_name in ("Bash", "run_shell_command"):
        if re.search(r"\bacli\b.*\bcreate\b|\bjira\b.*\bcreate\b|rest/api/\d+/issue\b", command, re.I):
            return "jira"
        return "other"
    return "write"


def main() -> int:
    level = "R0"
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        agent_type = data.get("agent_type")
        root = Path(R.project_root(data.get("cwd", "")))

        # Читающие инструменты: единственная применимая к ним проверка — фазовая (блок чтения
        # src/ до завершения grounding'а). Ladder к чтению неприменим (blast-radius ноль), а
        # deny-first по битой политике на чтении только расширил бы локаут: хук проведён на
        # read-матчер ради ОДНОГО этого гейта, который до сих пор был недостижим (tasks/012).
        if tool_name in _READ_TOOLS:
            return 0 if check_phase_gate(tool_name, tool_input, agent_type, root) else 2

        # Без загруженной risk-policy ladder не может классифицировать → в пайплайне deny-first
        # (иначе всё прошло бы как R1-auto). preflight ловит это ДО старта; здесь — backstop.
        if not R.policy_loaded() and R.manifest_exists(root):
            return _block("risk-policy.json не загружена/битая — risk ladder неактивен; "
                          "почини .gigacode/hooks/risk-policy.json (см. preflight).")

        info = R.classify(tool_name, tool_input, str(root))
        level = info["level"]
        command = info["command"]
        kind = _kind(tool_name, command)

        # M6: policy битая ВНЕ пайплайна — классификатор слеп (command_risk пуст → jira
        # упала бы в R1-auto). Не пропускаем создание Jira-задач по слепому auto.
        if not R.policy_loaded() and not R.manifest_exists(root) and kind == "jira":
            return _block(
                "risk-policy.json не загружена/битая — классификатор рисков неактивен; "
                f"действие ({kind}) заблокировано вне пайплайна до починки policy."
            )

        # ── R4-класс: запись самого согласия (record_approval) без цитаты пользователя ──
        # Первым в ряду R4: остальные гейты ссылаются на approval-маркер как на доказательство
        # человеческого «да», и если выписать маркер может сама модель, они все декоративны.
        deny = check_record_approval(command, root, str(data.get("transcript_path") or ""),
                                     str(data.get("cwd") or ""))
        if deny:
            return _block(deny)

        # ── R4-класс: снятие детерминированного гейта (override_judge) без approval ──
        # ДО auto-early-return: classify даёт таким командам default-R1 → иначе прошли бы авто.
        deny = check_gate_override(command, root)
        if deny:
            return _block(deny)

        # ── R4-класс: переснятие политики прогона (config.py repin) без approval ──
        # Тоже ДО auto-early-return: classify даёт config.py default-R1 → прошёл бы авто.
        deny = check_policy_repin(command, root)
        if deny:
            return _block(deny)

        # ── R4-класс: понижение собственных порогов (config.py set quality/security) ──
        # Тоже ДО auto-early-return: classify даёт config.py default-R1 → прошёл бы авто.
        deny = check_quality_downgrade(command, root)
        if deny:
            return _block(deny)

        # ── R4-класс: ослабить risk-policy / выключить обязательную фазу (config.py risk|phase) ──
        deny = check_policy_structure_edit(command, root)
        if deny:
            return _block(deny)

        # ── R4-класс: откат пайплайна (rollback.py) без approval ──
        # Тоже ДО auto-early-return: classify даёт скрипту default-R1 → прошёл бы авто.
        deny = check_rollback(command, root)
        if deny:
            return _block(deny)

        # ── R4-класс: снять прогон с активных (archive.py abandon / put --force) ──
        deny = check_archive_drop(command, root)
        if deny:
            return _block(deny)

        # ── R4-класс: git стирает незакоммиченную работу (reset --hard, clean -f, …) ──
        deny = check_git_discard(command, root)
        if deny:
            return _block(deny)

        # ── R4-класс: обход всех гейтов закрытия (update.py --skip-judges) без approval ──
        deny = check_skip_judges(command, root)
        if deny:
            return _block(deny)

        # ── Phase gate: проверка последовательности фаз пайплайна ──────────
        if not check_phase_gate(tool_name, tool_input, agent_type, root):
            return 2  # блокировка уже выдана в check_phase_gate

        # ── Fail-closed решения: продуктивная запись фазы блокируется, пока требуемое
        #    решение не записано (напр. inputs.story для fix-diag). Только write-инструменты,
        #    чтобы не заблокировать config.py set / ask, которыми решение и записывается.
        if tool_name in _WRITE_TOOLS:
            miss = _required_decisions_missing(root)
            if miss:
                return _block(
                    f"фаза требует решения '{miss}', которого нет в pipeline.json (fail-closed). "
                    f"Запиши: config.py set {miss} <value> (интерактивно — ответь на вопрос "
                    f"оркестратора; headless — предзапись ДО прогона), затем повтори."
                )

            # ── Гейт подтверждения плана человеком (phase_approvals) ──────────
            need = _phase_approval_missing(root)
            if need:
                key, step = need
                prov_note = (" Маркер есть, но БЕЗ провенанса record_approval — рукописный "
                             "маркер не считается согласием. "
                             if R.approval_exists(root, key) else " ")
                return _block(
                    f"фаза '{step}' требует ЯВНОГО подтверждения плана пользователем; нет "
                    f"approval-маркера '{key}' (журнал ground/approvals.jsonl, пишет ТОЛЬКО "
                    f"record_approval.py).{prov_note}"
                    f"Порядок: (1) покажи план (что сломано → как чиним, где правим, риск "
                    f"регресса, какое требование спеки затронуто) и спроси «делаем так или "
                    f"правки?»; (2) ТОЛЬКО после явного «да» — pipeline-state/scripts/"
                    f"record_approval.py --key {key} --approved-by user --reason \"<кто/почему>\" --evidence \"<дословная цитата пользователя>\" "
                    f"(прямая запись в approvals/ заблокирована state-write-guard); (3) повтори "
                    f"действие. Правки — верни фазу плана на доработку, маркер не выписывай."
                )

        # Читающая shell-команда — blast-radius ноль, ladder к ней неприменим. Классификатор
        # оценивает риск по ПУТИ из команды и чтения от записи не отличал: `cat
        # src/main/resources/application.yml` уходил в R4 (нужен approval человека, причём и
        # ВНЕ пайплайна), `grep … /auth/Foo.java` — в R3. Проверка стоит ПОСЛЕ фазового гейта
        # и R4-классов (override/rollback/skip-judges), поэтому их не ослабляет.
        if tool_name in ("Bash", "run_shell_command") and R.is_read_only_command(command):
            return 0

        # R0/R1 (или ниже порога критичности фичи) — авто. Не вмешиваемся.
        if R.level_order(level) <= R.level_order(R.auto_max_risk(root)):
            return 0

        in_pipeline = R.manifest_exists(root)

        # вне пайплайна (нет manifest) — gateway не форсит пайплайн-требования, но
        # deny-first для R4+ всё равно держим (необратимое без контекста — опасно).
        if not in_pipeline and R.level_order(level) < R.level_order("R4"):
            return 0

        # В пайплайне рисковое действие (R2+) нельзя делать, пока НЕ выбрана критичность фичи.
        # Это форсит шаг «выбор критичности» — он не выполнялся на прогонах.
        if in_pipeline and not R.criticality_set(root):
            return _block(
                "не выбрана критичность фичи (v2: decisions.criticality + decisions.auto_max_risk "
                "в manifest.json активной фичи). Вызови скрипт атомарной записи — "
                "feature-pipeline/scripts/set_criticality.py (он деривит auto_max_risk из критичности):\n"
                "  python3 <project>/.gigacode/skills/feature-pipeline/scripts/set_criticality.py "
                "--criticality <low|medium|high> --skill <skill> --feature <feature> "
                "--project-root <root>\n"
                "Ручная правка ground/policy.json / manifest.json заблокирована "
                "state-write-guard (Гейт критичности после BRD). "
                f"Действие risk={level} ({info['reason']})."
            )

        # separation of duties: действие выше cap роли субагента → deny
        cap = R.agent_cap(agent_type)
        if cap and R.level_order(level) > R.level_order(cap):
            return _block(
                f"separation of duties: роль '{agent_type}' ограничена {cap}, "
                f"а действие классифицировано как {level} ({info['reason']})."
            )

        req = R.requirement(level)
        allowed, why = R.check_requirement(level, req, root, kind, agent_type)
        if not allowed:
            return _block(f"{why}. Действие={kind} target='{info['target']}' risk={level}.")
        return 0

    except Exception as e:
        # fail-CLOSED на рисковых, fail-open на низких
        if R.level_order(level) >= R.level_order("R3"):
            return _block(f"deny-first: ошибка оценки риска на {level} ({e}).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
