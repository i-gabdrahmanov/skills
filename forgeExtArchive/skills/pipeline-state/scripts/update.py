#!/usr/bin/env python3
from __future__ import annotations
"""
Update a step's status in the pipeline manifest.

Usage:
    update.py --project <path> --skill <name> --step-id <id> --status <status> \\
        [--artifacts '<json>']        # JSON mapping of artifact keys→paths
        [--output-file <path>]        # path to JSON file with subagent output
        [--output-json <inline>]      # OR inline JSON string
        [--output-stdin]              # OR read JSON from stdin
        [--error <msg>]               # error message (for status=failed)

Statuses: pending | in_progress | completed | failed | skipped

If status=completed and output is provided, saves it to
<project>/ground/statements/<skill>/pipeline/<step-id>.json

--artifacts stores a key→path mapping in the step (e.g.
  '{"tech-design":"docs/feature-pipeline/slug/tech-design.md","task-plan":"..."}'
Paths are normalized to be relative to project root.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import (gate_result_path, judges_dir, load_pipeline_config, origins_dir,
                   overrides_dir, repo_root, safe_load_json)
import forge_events as FE  # журнал evidence (импорт _util уже положил hooks/ в sys.path)

# Соглашение «какие фазы обязаны идти через субагента» — ЕДИНЫЙ источник pipeline_phases
# (co-located feature-pipeline). best-effort импорт + inline-fallback, чтобы переименование
# префикса в одном месте не отключало enforcement молча.
_SUBAGENT_PREFIXES = ("02-sdd", "02-design", "04-test", "04-build", "05-tests", "06-spec",
                      "lite-design", "lite-red", "lite-green", "lite-verify",
                      "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec")
_GATE_RESULT_PREFIXES = ("04-test", "04-build", "05-tests",
                         "lite-jira", "lite-design", "lite-red", "lite-green", "lite-verify",
                         "fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec")
_REQUIRED_STEP_PREFIXES = _SUBAGENT_PREFIXES
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "feature-pipeline" / "scripts"))
    import pipeline_phases as _pp
    _requires_subagent = _pp.requires_subagent
    _SUBAGENT_PREFIXES = _pp.SUBAGENT_PHASE_PREFIXES
    _requires_gate_result = _pp.requires_gate_result
    _GATE_RESULT_PREFIXES = _pp.GATE_RESULT_PREFIXES
    _requires_no_silent_skip = _pp.requires_no_silent_skip
    _REQUIRED_STEP_PREFIXES = _pp.REQUIRED_STEP_PREFIXES
except Exception:
    def _requires_subagent(step_id) -> bool:
        return isinstance(step_id, str) and step_id.startswith(tuple(_SUBAGENT_PREFIXES))

    def _requires_gate_result(step_id) -> bool:
        return isinstance(step_id, str) and step_id.startswith(tuple(_GATE_RESULT_PREFIXES))

    def _requires_no_silent_skip(step_id) -> bool:
        return isinstance(step_id, str) and step_id.startswith(tuple(_REQUIRED_STEP_PREFIXES))


VALID_STATUSES = {"pending", "in_progress", "completed", "failed", "skipped"}


# Абсолютный путь к override_judge.py (тот же каталог) — чтобы подсказка была
# исполняемой как есть, без подстановки <project> рантаймом Qwen.
_OVERRIDE_SCRIPT = Path(__file__).resolve().parent / "override_judge.py"


def _override_hint(judge: str, feature: str, step_id: str, why_ph: str = "<обоснование>") -> str:
    """Подсказка снятия гейта. Снятие — R4-класс: gate-guard пропустит override_judge ТОЛЬКО
    при approval-маркере ground/approvals/gate-override-<judge>.json, который фиксируется
    после ЯВНОГО согласия пользователя (раньше баннеры печатали готовую команду без
    approval-шага — модель снимала гейт молча)."""
    return (
        f"   Снять гейт можно ТОЛЬКО после явного «да» пользователя (R4):\n"
        f"   1) спроси пользователя, показав что не сходится;\n"
        f"   2) зафиксируй согласие: ground/approvals/gate-override-{judge}.json "
        f"{{\"approved_by\": \"user\", \"reason\": \"{why_ph}\"}};\n"
        f"   3) python3 {_OVERRIDE_SCRIPT} --judge {judge} --feature {feature} "
        f"--step-id {step_id} --reason \"{why_ph}\""
    )


# Пути control-plane — из общего резолвера (_util → hooks/_project): писатель evidence
# (state-recorder / record_gate / record_approval) и читатель-верификатор обязаны складывать
# имя одинаково, иначе гейт ищет маркер не там, где он записан.
_judges_dir = judges_dir
_overrides_dir = overrides_dir
_origins_dir = origins_dir


def _has_origin_marker(project: Path, skill: str, feature: str, step_id: str) -> bool:
    """Evidence «шаг закрыл реальный SubagentStop» пишет ТОЛЬКО state-recorder —
    поэтому его наличие доказывает, что шаг выполнен субагентом, а не подделан
    флагом --closed-by. Переоткрытие шага это evidence обнуляет."""
    return FE.origin(project, skill, feature, step_id) is not None


def _load_override(project: Path, skill: str, feature: str, judge_name: str) -> dict | None:
    """Ручное снятие блокировки (override_judge, R4), если оно есть. None — нет."""
    return FE.override(project, skill, feature, judge_name)


def _check_judges(step: dict, project: Path, skill: str, feature: str):
    """
    Детерминированная блокировка: если шаг помечен completed, но не все его
    required_judges пройдены — выкинуть исключение.

    Исключение: если для судьи есть ручной override (запись override с target=<judge>),
    блокировка снимается и факт отклонения фиксируется в manifest-step как предупреждение.
    Создание override — R4-класс: gate-guard пропускает override_judge.py только при
    approval-маркере ground/approvals/gate-override-<judge>.json (после явного «да»
    пользователя). См. _override_hint.
    """
    required = step.get("required_judges", [])
    if not required:
        return

    events = FE.read_events(project, skill, feature)
    blocking = []
    overridden = []

    for judge_name in required:
        # Вердикт — из журнала прогона (kind:"judge", produced_by:"run_judge"); при его
        # отсутствии — из старой раскладки judges/<name>.json (прогон до миграции).
        verdict = FE.judge(project, skill, feature, judge_name, events=events)

        # 1. Вердикта нет: судья не запускался, либо запись/файл нечитаемы, либо провенанс
        # чужой (подделка не отличается от отсутствия — и в обоих случаях шаг не закрыть).
        if verdict is None:
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                overridden.append(
                    f"⚠️  '{judge_name}' не запускался — пропущен вручную. "
                    f"Причина: {ov.get('reason', '?')}"
                )
                continue
            blocking.append(
                f"❌ Вердикт '{judge_name}' не найден — судья не запускался "
                f"(или запись повреждена / без провенанса run_judge).\n"
                + _override_hint(judge_name, feature, step["id"], "<объяснение>")
            )
            continue

        # 2. Схема-санити: настоящий вердикт несёт passed:bool И один из
        # verdict/checks/summary/step_id. Голый {"passed":true} — не вердикт.
        if (not isinstance(verdict.get("passed"), bool)
                or not any(k in verdict for k in ("verdict", "checks", "summary", "step_id"))):
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                overridden.append(f"⚠️  '{judge_name}' схема невалидна — пропущен вручную. "
                                  f"Причина: {ov.get('reason', '?')}")
                continue
            blocking.append(
                f"❌ Вердикт '{judge_name}' не похож на вывод run_judge "
                f"(нужно passed:bool + verdict/checks/summary). "
                f"Перезапусти судью (run_judge.py), не правь состояние руками."
            )
            continue

        # 3. Вердикт есть, но FAIL
        if not verdict.get("passed", False):
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                issues = verdict.get("blocking_issues", [])
                overridden.append(
                    f"⚠️  '{judge_name}' FAIL — пропущен вручную.\n"
                    f"   Причина override: {ov.get('reason', '?')}\n"
                    f"   Заблокированные issues ({len(issues)}): "
                    + (issues[0][:120] if issues else "нет") +
                    (" ..." if len(issues) > 1 else "")
                )
                continue
            issues = verdict.get("blocking_issues", ["не указаны"])
            blocking.append(
                f"❌ Вердикт '{judge_name}.json' — FAIL.\n"
                f"   Blocking issues: {issues}\n"
                + _override_hint(judge_name, feature, step["id"], "<объяснение>")
            )

    # Записываем предупреждения об override в step (для аудита)
    if overridden:
        step.setdefault("override_warnings", [])
        for msg in overridden:
            if msg not in step["override_warnings"]:
                step["override_warnings"].append(msg)
        print("\n".join(f"  {m}" for m in overridden), file=sys.stderr)

    if blocking:
        raise RuntimeError(
            f"Шаг {step['id']} не может быть закрыт: {len(blocking)} блокирующих проблем(ы).\n" +
            "\n".join(blocking)
        )


def _check_subagent_origin(step: dict, closed_by: str, project: Path, skill: str, feature: str):
    """Гарантия «фаза выполнена ЧЕРЕЗ субагента, а не inline» — на EVIDENCE, не на доверии.

    Раньше это пытался форсить subagent-enforcer (PreToolUse), но PreToolUse срабатывает и
    ВНУТРИ субагента → он заблокировал бы сам субагент. Проверку перенесли на закрытие шага, но
    она доверяла флагу --closed-by: оркестратор мог передать --closed-by subagent inline и
    подделать происхождение. Теперь проверяется НАЛИЧИЕ evidence-записи origin в журнале прогона,
    который пишет ТОЛЬКО state-recorder на реальном SubagentStop (рантайм-событие, не тул модели).
    Флаг --closed-by больше не является доказательством.

    Escape-hatch: override subagent-origin (как у судей) — снимает блок с предупреждением
    (для деградации, когда agent() реально недоступен).
    """
    step_id = step.get("id", "")
    if not _requires_subagent(step_id):
        return
    if _has_origin_marker(project, skill, feature, step_id):
        return  # реальный SubagentStop оставил evidence — фаза прошла субагентом
    ov = _load_override(project, skill, feature, "subagent-origin")
    if ov:
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  шаг '{step_id}' закрыт без subagent-evidence — пропущено вручную. "
               f"Причина: {ov.get('reason', '?')}")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return
    # «Ни одной origin-записи вообще» = SubagentStop-хук не срабатывает (харнес не армлен).
    # Считаем по журналу И по старой раскладке: проверка только каталога _origins давала бы
    # ложную диагностику на любом мигрированном проекте — каталога там нет по определению.
    odir = origins_dir(project, skill, feature)
    no_markers_at_all = (
        not any(r.get("kind") == "origin" for r in FE.read_events(project, skill, feature))
        and not (odir.is_dir() and any(odir.glob("*.json")))
    )
    arming_hint = ""
    if no_markers_at_all:
        arming_hint = (
            "\n   ДИАГНОСТИКА: ни одной origin-записи у фичи нет — вероятно харнес не армлен "
            "(SubagentStop-хук не срабатывает). Прогони `python3 .gigacode/hooks/preflight.py "
            "--project .` — он должен вернуть exit 0; если ругается на settings.json/hooks — "
            "сначала deploy. Override уместен только когда agent() реально недоступен."
        )
    raise RuntimeError(
        f"Шаг {step_id} нельзя закрыть: нет evidence, что фаза прошла через субагента "
        f"(записи origin от SubagentStop нет — либо её не было, либо шаг переоткрыт откатом; "
        f"флаг --closed-by "
        f"считается доказательством). Прогони фазу через agent(subagent_type=...) — "
        f"state-recorder запишет evidence и закроет шаг сам. Если agent() реально недоступен:\n"
        + _override_hint("subagent-origin", feature, step_id, "<почему inline допустимо>")
        + f"{arming_hint}"
    )


# Доко-фазы, закрытие которых требует человеческого утверждения дока (Гейт 1 / Гейт SDD).
# Префикс id шага → имя дока. Ключ маркера: <doc>-approved-<feature>.
_DOC_APPROVAL_STEPS = (("00-brd", "brd"), ("02-sdd", "sdd"))


def _approval_marker_valid(project: Path, key: str) -> bool:
    """Согласие человека по ключу засчитывается ТОЛЬКО с провенансом record_approval
    и совпадающим key внутри (переименованная чужая запись не считается).

    Источник — ground/approvals.jsonl, при отсутствии записи — старый файл-маркер
    ground/approvals/<key>.json (прогон, начатый до миграции)."""
    return FE.approval(project, key) is not None


def _check_doc_approval(step: dict, project: Path, skill: str, feature: str):
    """Гарантия «BRD/SDD утверждён человеком» — на EVIDENCE, не на словах модели.

    Закрыть 00-brd/02-sdd можно только при маркере <doc>-approved-<feature> (record_approval
    после явного «да» пользователя). На прогонах модель не задавала вопросов
    вообще — теперь без утверждения фаза не закрывается детерминированно.
    Escape-hatch: override doc-approved-<step_id> (создание — R4 через override_judge)."""
    step_id = step.get("id", "")
    doc = next((d for p, d in _DOC_APPROVAL_STEPS if step_id.startswith(p)), None)
    if doc is None:
        return
    key = f"{doc}-approved-{feature}"
    if _approval_marker_valid(project, key):
        return
    ov = _load_override(project, skill, feature, f"doc-approved-{step_id}")
    if ov:
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  шаг '{step_id}' закрыт без утверждения {doc}.md — пропущено вручную. "
               f"Причина: {ov.get('reason', '?')}")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return
    record_script = Path(__file__).resolve().parent / "record_approval.py"
    raise RuntimeError(
        f"Шаг {step_id} нельзя закрыть: {doc}.md не утверждён пользователем "
        f"(нет валидного маркера ground/approvals/{key}.json).\n"
        f"   Порядок: (1) спроси пользователя («утверждаем {doc.upper()}?»); "
        f"(2) ТОЛЬКО после явного «да»:\n"
        f"   python3 {record_script} --project {project} --key {key} "
        f"--approved-by user --reason \"<кто/почему>\"\n"
        f"   (3) повтори update.py. Молча закрывать доко-фазу нельзя. Если утверждение "
        f"объективно неприменимо:\n"
        + _override_hint(f"doc-approved-{step_id}", feature, step_id,
                         "<почему без утверждения>")
    )


# Фаза grounding: закрыть 01-grounding можно только при СОДЕРЖАТЕЛЬНОЙ выжимке системы.
# Шаги, снимающие инвентарь: полный пайплайн и lite-ветка. forgelite гоняет тот же
# check_taskplan, поэтому пустой инвентарь роняет его гейты ровно так же — прикрывать надо оба.
_GROUNDING_STEP_PREFIX = ("01-grounding", "lite-ground")


def _grounding_excerpt_path(project: Path) -> Path:
    """Путь к срезу инвентаря (ground/inventory/grounding-excerpt.json) через единый резолвер."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "feature-pipeline" / "scripts"))
        import skill_paths  # type: ignore
        return skill_paths.grounding_excerpt_path(project)
    except Exception:
        return project / "ground" / "inventory" / "grounding-excerpt.json"


def _check_grounding_substance(step: dict, project: Path, skill: str, feature: str):
    """Гарантия «01-grounding закрывается только при СОДЕРЖАТЕЛЬНОМ инвентаре».

    Инвентарь — топливо детерминированных гейтов дизайна (check_taskplan сверяет по нему
    reuses и модули). Пустышка (0 модулей и 0 entities) означает, что сканировали не тот
    корень или сканировать было нечего; пропустить её дальше — значит остаться без гейтов и
    узнать об этом на фазе дизайна как о «warning: кросс-чек пропущен».
    Enforcement > guidance: гейт в update.py, а не только в брифе.
    Escape-hatch: override grounding-substance-<step_id> (R4, через override_judge)."""
    step_id = step.get("id", "")
    if not step_id.startswith(_GROUNDING_STEP_PREFIX):
        return
    excerpt = _grounding_excerpt_path(project)
    problem = None
    if not excerpt.exists():
        problem = (f"нет инвентаря {excerpt} — ensure_inventory.py не отработал")
    else:
        try:
            data = json.loads(excerpt.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            problem = f"grounding-excerpt.json нечитаем/битый: {e}"
        else:
            if not isinstance(data, dict) or (
                    not (data.get("modules") or []) and not (data.get("entities") or [])):
                problem = ("инвентарь пуст (0 модулей и 0 entities) — сканировали не тот корень "
                           "или сканировать нечего")
    if problem is None:
        return
    ov = _load_override(project, skill, feature, f"grounding-substance-{step_id}")
    if ov:
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  шаг '{step_id}' закрыт без содержательного grounding — пропущено вручную. "
               f"Причина: {ov.get('reason', '?')}")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return
    raise RuntimeError(
        f"Шаг {step_id} нельзя закрыть: {problem}.\n"
        f"   Сними инвентарь: python3 <project>/.gigacode/skills/system-analyst/scripts/"
        f"ensure_inventory.py --root <project> --force, затем повтори update.py.\n"
        + _override_hint(f"grounding-substance-{step_id}", feature, step_id,
                         "<почему grounding объективно недоступен>")
    )


_gate_result_path = gate_result_path


def _check_gate_result(step: dict, project: Path, skill: str, feature: str):
    """Гарантия «шаг закрыт, потому что детерминированный гейт РЕАЛЬНО прошёл».

    Для build/verify-шагов (04-test/04-build/05-tests, lite-red/green/verify) слово субагента
    («status: completed» в его JSON) — не доказательство: слабая модель возвращает completed
    при упавшей сборке. Требуем evidence с провенансом record_gate и passed:true — его пишет
    record_gate.py по фактическому exit-коду команды гейта.
    Escape-hatch: override gate-result-<step_id>.
    """
    step_id = step.get("id", "")
    if not _requires_gate_result(step_id):
        return
    rec = FE.gate(project, skill, feature, step_id)
    problem = None
    if rec is None:
        problem = ("evidence гейта нет — гейт шага не запускался через record_gate.py "
                   "(либо запись повреждена/без провенанса record_gate)")
    elif not isinstance(rec.get("passed"), bool):
        problem = ("evidence гейта не похож на вывод record_gate.py (нужно passed:bool) — "
                   "не пиши его руками")
    elif rec.get("passed") is not True:
        problem = f"гейт шага НЕ пройден (passed:false): {rec.get('reason', 'exit code != 0')}"
    if problem is None:
        return
    ov = _load_override(project, skill, feature, f"gate-result-{step_id}")
    if ov:
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  шаг '{step_id}' закрыт без валидного gate-result ({problem}) — "
               f"пропущено вручную. Причина: {ov.get('reason', '?')}")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return
    record_script = Path(__file__).resolve().parent / "record_gate.py"
    raise RuntimeError(
        f"Шаг {step_id} нельзя закрыть: {problem}.\n"
        f"   Прогони гейт через раннер (он сам запишет evidence):\n"
        f"   python3 {record_script} --project {project} --skill {skill} --feature {feature} "
        f"--step-id {step_id} --cmd \"<команда гейта>\"  "
        f"(для RED: --expect red --compile-cmd \"<компиляция>\")\n"
        f"   Если гейт объективно неприменим:\n"
        + _override_hint(f"gate-result-{step_id}", feature, step_id, "<почему>")
    )


_REOPEN_DEFAULT_LIMIT = 3


def _max_step_reopens(project: Path) -> int:
    """Лимит переоткрытий шага: quality.max_step_reopens из ground/pipeline.json (дефолт 3)."""
    try:
        v = int(load_pipeline_config(project).get("quality", {}).get(
            "max_step_reopens", _REOPEN_DEFAULT_LIMIT))
        return v if v > 0 else _REOPEN_DEFAULT_LIMIT
    except (TypeError, ValueError):
        return _REOPEN_DEFAULT_LIMIT


def _check_reopen_limit(step: dict, project: Path, skill: str, feature: str):
    """Детерминированный брейк ре-итераций: переоткрытие закрытого шага
    (completed|failed → pending|in_progress) считается в step["reopens"]; при исчерпании
    quality.max_step_reopens транзишен блокируется с exit 3 (ESCALATE — «стоп-и-спроси»),
    а не молча продолжает цикл. Прозаические «лимит 3» в SKILL.md модель не держит —
    держит этот счётчик. Escape-hatch: override step-reopen-<step_id>."""
    step_id = step.get("id", "")
    reopens = step.get("reopens", 0)
    limit = _max_step_reopens(project)
    if reopens < limit:
        step["reopens"] = reopens + 1
        return
    ov = _load_override(project, skill, feature, f"step-reopen-{step_id}")
    if ov:
        step["reopens"] = reopens + 1
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  шаг '{step_id}' переоткрыт сверх лимита ({reopens}/{limit}) — "
               f"пропущено вручную. Причина: {ov.get('reason', '?')}")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return
    print(
        "=" * 60 + "\n"
        f"⛔ STOP: шаг '{step_id}' переоткрывался уже {reopens} раз(а) — лимит "
        f"quality.max_step_reopens={limit} исчерпан.\n"
        f"⛔ ESCALATE: не продолжай цикл правок. Останови работу и спроси пользователя:\n"
        f"   покажи, что не сходится (последние ошибки/вердикты), и предложи варианты.\n"
        + _override_hint(f"step-reopen-{step_id}", feature, step_id,
                         "<почему ещё итерация оправдана>") + "\n"
        + "=" * 60,
        file=sys.stderr,
    )
    sys.exit(3)


def _check_failure_limit(step: dict, project: Path, skill: str, feature: str) -> bool:
    """Вторая половина брейка: считает повторные провалы шага (транзишены в failed).
    Возвращает True, когда лимит исчерпан — вызывающий код ДОПИСЫВАЕТ манифест (провал
    фиксируется) и завершает процесс exit 3. Тот же лимит и тот же override, что у reopens."""
    step_id = step.get("id", "")
    step["failures"] = step.get("failures", 0) + 1
    limit = _max_step_reopens(project)
    if step["failures"] < limit:
        return False
    ov = _load_override(project, skill, feature, f"step-reopen-{step_id}")
    if ov:
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  шаг '{step_id}' провален {step['failures']} раз(а) (лимит {limit}) — "
               f"эскалация снята вручную. Причина: {ov.get('reason', '?')}")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return False
    print(
        "=" * 60 + "\n"
        f"⛔ STOP: шаг '{step_id}' провален уже {step['failures']} раз(а) — лимит "
        f"quality.max_step_reopens={limit} исчерпан.\n"
        f"⛔ ESCALATE: не перезапускай фазу ещё раз. Останови работу и спроси пользователя:\n"
        f"   покажи последние ошибки и предложи варианты (сменить подход / сузить задачу / отложить).\n"
        + _override_hint(f"step-reopen-{step_id}", feature, step_id,
                         "<почему ещё попытка оправдана>") + "\n"
        + "=" * 60,
        file=sys.stderr,
    )
    return True


_RED_STEP_PREFIXES = ("lite-red", "fix-red", "04-test")


def _red_step_test_exempt(step_id: str, project: Path, skill: str, feature: str) -> bool:
    """Можно ли ЛЕГАЛЬНО пропустить RED-шаг: задача(и) не пишут тестируемый код.

    Брифы lite/fix предлагали для такой задачи «не заводи шаг RED» — но манифест этих ветвей
    СТАТИЧЕСКИЙ (init.py берёт весь список из references/manifest-steps.json), шаг всегда есть,
    а `skipped` для него блокировался как для обязательного. Эскейп был невыполним: шаг висел в
    `pending` до конца прогона либо снимался R4-override'ом. Теперь решает тот же детерминированный
    предикат, что и у tdd-guard (task_is_test_exempt / no_test_layers), — по task-plan, а не на слово.
    """
    if not step_id.startswith(_RED_STEP_PREFIXES):
        return False
    try:
        import pipeline_phases as pp
        import risk_ladder as R
        plan_path = _docs_dir_for(project, skill, feature) / "task-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        cfg = R.pipeline_cfg(project)
        tid = pp.test_task_id(step_id)          # 04-test-<taskId> → per-task освобождение
        if tid:
            task = next((t for t in plan.get("tasks", []) if t.get("id") == tid), None)
            return bool(task) and pp.task_is_test_exempt(task, cfg)
        return pp.all_tasks_test_exempt(plan, cfg)
    except Exception:  # noqa: BLE001 — плана нет/нечитаем: fail-closed, пропуск не разрешаем
        return False


def _check_required_skip(step: dict, project: Path, skill: str, feature: str):
    """Обязательный шаг (REQUIRED_STEP_PREFIXES) нельзя тихо пропустить (status=skipped) — иначе
    fallback «не смог спросить → пропущу фазу» молча выкидывает качество-гейты. Для обязательной
    фазы пропуск = ОСТАНОВКА (Thrust 1: fallback=STOP). Escape: override step-skip-<step_id>
    (создание — R4 через override_judge + approval-маркер). Иначе exit 3 ESCALATE."""
    step_id = step.get("id", "")
    if not _requires_no_silent_skip(step_id):
        return
    if _red_step_test_exempt(step_id, project, skill, feature):
        step.setdefault("override_warnings", [])
        msg = (f"RED-шаг '{step_id}' пропущен: задача(и) прогона test-exempt "
               f"(no_test/quality.no_test_layers) — падающий unit-тест для них не пишется")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return
    ov = _load_override(project, skill, feature, f"step-skip-{step_id}")
    if ov:
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  обязательный шаг '{step_id}' пропущен по override "
               f"(reason: {ov.get('reason', '?')})")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        return
    sys.stderr.write(
        "\n" + "=" * 60 + "\n"
        f"⛔ STOP: шаг '{step_id}' — обязательный, его нельзя пропустить (skipped) молча.\n"
        f"   Пустой ответ вопроса / «не смог спросить» для обязательной фазы = ОСТАНОВКА,\n"
        f"   а не тихий пропуск. Доведи фазу или спроси пользователя. Если пропуск реально\n"
        f"   согласован пользователем:\n"
        + _override_hint(f"step-skip-{step_id}", feature, step_id, "<почему пропуск согласован>")
        + "\n" + "=" * 60 + "\n"
    )
    sys.exit(3)


def _policy() -> dict:
    """risk-policy.json (co-located с хуками). Пусто при любой проблеме — проверки ниже
    fail-open по политике: их пол дублируют gate-guard и гейты фаз."""
    try:
        import risk_ladder as R
        return R.load_policy() or {}
    except Exception:  # noqa: BLE001
        return {}


def _config_get(project: Path, dotpath: str):
    try:
        import risk_ladder as R
        return R.config_get(project, dotpath)
    except Exception:  # noqa: BLE001
        return None


def _check_skip_judges(project: Path, feature: str) -> None:
    """`--skip-judges` снимает ВСЕ гейты закрытия шага — это R4, а не служебный флаг.

    Флаг задумывался под один случай (восстановление статусов после `init.py --force`), но по
    факту был готовым bypass'ом в одну опцию: судьи, gate-result, subagent-origin, обязательные
    решения и артефакты — всё разом. Теперь нужен approval-маркер `skip-judges-<feature>` с
    провенансом `record_approval`, т.е. явное «да» пользователя. Второй слой — gate-guard
    блокирует саму команду с флагом; здесь проверка держится и при запуске мимо харнеса."""
    prefix = "skip-judges"
    try:
        policy = _policy().get("skip_judges") or {}
        prefix = policy.get("approval_prefix") or prefix
    except Exception:  # noqa: BLE001
        pass
    key = f"{prefix}-{feature}"
    if _approval_marker_valid(project, key):
        return
    rec_script = Path(__file__).resolve().parent / "record_approval.py"
    sys.stderr.write(
        "\n" + "=" * 60 + "\n"
        "⛔ STOP: --skip-judges снимает ВСЕ гейты закрытия шага (судьи, gate-result,\n"
        "   subagent-origin, обязательные решения, артефакты) — это R4-класс.\n"
        f"   Нужен approval-маркер ground/approvals/{key}.json с провенансом record_approval.\n"
        "   Порядок: (1) объясни пользователю, ЗАЧЕМ обходить гейты (легитимный случай —\n"
        "   восстановление статусов после init.py --force) и спроси; (2) после явного «да»:\n"
        f"   python3 {rec_script} --project {project} --key {key} --approved-by user "
        f"--reason \"<зачем обход>\"\n"
        "   (3) повтори команду. Штатное закрытие шага флага НЕ требует.\n"
        + "=" * 60 + "\n"
    )
    sys.exit(3)


def _check_required_decisions(step: dict, project: Path, skill: str, feature: str):
    """Шаг, на котором задаётся ОБЯЗАТЕЛЬНЫЙ вопрос, нельзя закрыть с незаписанным ответом.

    Прогон: агент спросил «к какой стори относится баг?», получил ответ — и закрыл `fix-intake`,
    не выполнив `config.py set sources.story`. Решение потерялось молча (типовая механика: на
    свежем проекте `ground/pipeline.json` ещё не создан, `set` возвращает exit 3, и ошибка
    проходит незамеченной). Дальше `<fixdir>` резолвится плоским, а `find_spec_anchor` работает
    без самого сильного признака — и якорь спеки уезжает.

    `required_decisions` в risk-policy.json закрывает ЗАПИСЬ следующей фазы (gate-guard);
    `required_decisions_on_close` форсит тот же ключ в точке, где ответ уже получен.
    """
    policy = _policy().get("required_decisions_on_close") or {}
    step_id = step.get("id", "")
    keys = next((v for k, v in policy.items()
                 if not k.startswith("_") and step_id.startswith(k)), None)
    if not keys:
        return
    missing = [k for k in keys if not _config_get(project, k)]
    if not missing:
        return
    cfg_script = (Path(__file__).resolve().parents[1].parent
                  / "config-helper" / "scripts" / "config.py")
    raise RuntimeError(
        f"Шаг {step_id} нельзя закрыть: решение(я) {', '.join(missing)} не записаны в "
        f"ground/pipeline.json.\n"
        f"   Это ответ ПОЛЬЗОВАТЕЛЯ, а не догадка: спроси его (для sources.story — «к какой "
        f"стори/фиче относится баг? ключ вида STOR-100; не знаешь — так и скажи»), затем "
        f"запиши ответ (осознанное «не знаю» пишется как none):\n"
        + "".join(f"   python3 {cfg_script} --project {project} set {k} <значение|none>\n"
                  for k in missing)
        + f"   ПРОВЕРЬ exit-код set: 0 = записано. exit 3 «pipeline.json не найден» — сначала "
        f"init_pipeline_config.py --project {project}, потом повтори set."
    )


_CANONICAL_ARTIFACTS = {
    # step-id → файлы, которые фаза ОБЯЗАНА положить в docs-каталог задачи под этими именами.
    # Имя — часть контракта, а не оформление: `/forge-spec` ищет дельту строго как `sdd.md`,
    # а следующие фазы (RED/GREEN) читают `tech-design.md`/`fix-plan.md` по имени. На прогоне
    # lite-документ уехал под именем слага задачи — гейты фазы этого не заметили (им путь
    # передаёт сама модель), и дельта выпала из `merge`/`diff`. Full-путь здесь не перечислен:
    # там те же имена держат судьи фаз (sdd-judge/design-judge) и `check_traceability`.
    "lite-design": ("tech-design.md", "task-plan.json"),
    "fix-diag": ("fix-plan.md", "task-plan.json"),
    "fix-spec": ("sdd.md",),
}


def _docs_dir_for(project: Path, skill: str, feature: str):
    """Каталог артефактов задачи: у фикса — <docs>/<стори>/fixes/<баг>, иначе <docs>/<feature>."""
    import skill_paths as SP
    if skill == "forgefix":
        return SP.fix_docs_dir(project, feature, _config_get(project, "sources.story"))
    return SP.feature_docs_dir(project) / feature


def _check_step_artifacts(step: dict, project: Path, skill: str, feature: str):
    """Фаза не закрывается, пока её артефакты не лежат под КАНОНИЧЕСКИМИ именами."""
    names = _CANONICAL_ARTIFACTS.get(step.get("id", ""))
    if not names:
        return
    try:
        ddir = _docs_dir_for(project, skill, feature)
    except Exception:  # noqa: BLE001 — не резолвится docs-конфиг: не наш гейт (fail-open)
        return
    missing = [n for n in names if not (ddir / n).exists()]
    if not missing:
        return
    step_id = step.get("id", "")
    ov = _load_override(project, skill, feature, f"artifacts-{step_id}")
    if ov:
        step.setdefault("override_warnings", [])
        msg = (f"⚠️  шаг '{step_id}' закрыт без канонических артефактов "
               f"({', '.join(missing)}) — пропущено вручную. Причина: {ov.get('reason', '?')}")
        if msg not in step["override_warnings"]:
            step["override_warnings"].append(msg)
        print(f"  {msg}", file=sys.stderr)
        return
    try:
        found = sorted(p.name for p in ddir.iterdir() if p.is_file())[:10]
    except OSError:
        found = []
    raise RuntimeError(
        f"Шаг {step.get('id')} нельзя закрыть: в {ddir} нет {', '.join(missing)}.\n"
        f"   Найдено в каталоге: {', '.join(found) or '(пусто/каталога нет)'}\n"
        f"   Имя файла — часть контракта, а не оформление: по нему артефакт находят следующие "
        f"фазы и /forge-spec (дельта ищется строго как sdd.md). Файл написан под другим именем "
        f"(напр. по слагу задачи) — переименуй в канонический, не заводи второй. Каталог задачи "
        f"изменился (стори узнали позже, и папка фикса переехала в <стори>/fixes/<баг>) — "
        f"перенеси туда уже записанные артефакты, а не пиши их заново.\n"
        f"   Каталог задачи резолвится одной командой: skill_paths.py "
        f"{'fix-docs --feature <баг> --story <стори|none>' if skill == 'forgefix' else 'feature-docs --feature <слаг>'}"
        f" --project {project}\n"
        f"   Если артефакт объективно лежит в другом месте (нестандартная раскладка docs):\n"
        + _override_hint(f"artifacts-{step_id}", feature, step_id, "<где артефакт и почему там>")
    )


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# База данных скиллов внутри проекта (НЕ dot-папка — иначе рантайм режет доступ).
DATA_DIR = "ground"


def pipeline_dir(project: Path, skill: str, feature: str = "pipeline") -> Path:
    return project / DATA_DIR / "statements" / skill / feature


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    p.add_argument("--skill", required=True)
    p.add_argument("--feature", default="pipeline", help="Namespace стейта на фичу (как в init.py)")
    p.add_argument("--step-id", required=True)
    p.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    p.add_argument("--artifacts", help='JSON mapping of artifact keys to file paths, e.g. \'{"tech-design":"docs/.../tech-design.md","task-plan":"..."}\'')
    g = p.add_mutually_exclusive_group()
    g.add_argument("--output-file", help="Path to JSON file with subagent's output")
    g.add_argument("--output-json", help="Inline JSON string of subagent's output")
    g.add_argument("--output-stdin", action="store_true", help="Read JSON output from stdin")
    p.add_argument("--error", help="Error message (use with status=failed)")
    p.add_argument("--skip-judges", action="store_true", help="Skip judge check (use when restoring state after init --force)")
    p.add_argument("--closed-by", default="inline", choices=["inline", "subagent"],
                   help="Кто закрывает шаг: subagent (от SubagentStop/state-recorder) или inline (оркестратор). "
                        "Фазы из SUBAGENT_PHASE_PREFIXES требуют subagent.")
    args = p.parse_args()

    project = Path(args.project or repo_root()).resolve()
    pdir = pipeline_dir(project, args.skill, args.feature)
    manifest_path = pdir / "manifest.json"

    if not manifest_path.exists():
        print(f"ERROR: manifest not found at {manifest_path}. Run init.py first.", file=sys.stderr)
        sys.exit(3)

    manifest = safe_load_json(manifest_path, what="manifest")

    step = next((s for s in manifest["steps"] if s["id"] == args.step_id), None)
    if step is None:
        print(f"ERROR: step '{args.step_id}' not found in manifest", file=sys.stderr)
        sys.exit(2)

    now = iso_now()
    prev_status = step.get("status")

    # Обход всех гейтов закрытия — только с явным согласием пользователя (R4)
    if args.skip_judges:
        _check_skip_judges(project, args.feature)

    # Брейк ре-итераций: переоткрытие закрытого шага лимитируется quality.max_step_reopens
    if (not args.skip_judges and args.status in ("pending", "in_progress")
            and prev_status in ("completed", "failed")):
        _check_reopen_limit(step, project, args.skill, args.feature)

    # Детерминированная блокировка: не даём закрыть шаг без судей и без субагентного происхождения
    if not args.skip_judges and args.status == "completed" and prev_status != "completed":
        _check_subagent_origin(step, args.closed_by, project, args.skill, args.feature)
        _check_gate_result(step, project, args.skill, args.feature)
        _check_judges(step, project, args.skill, args.feature)
        _check_doc_approval(step, project, args.skill, args.feature)
        _check_grounding_substance(step, project, args.skill, args.feature)
        _check_required_decisions(step, project, args.skill, args.feature)
        _check_step_artifacts(step, project, args.skill, args.feature)

    # Fallback=STOP: обязательный шаг нельзя тихо пропустить (skipped) без override
    if not args.skip_judges and args.status == "skipped":
        _check_required_skip(step, project, args.skill, args.feature)

    step["status"] = args.status
    if args.status == "completed":
        step["closed_by"] = args.closed_by

    # Счётчик провалов: повторный failed сверх лимита → зафиксировать провал и exit 3
    escalate_failed = False
    if args.status == "failed" and not args.skip_judges:
        escalate_failed = _check_failure_limit(step, project, args.skill, args.feature)

    # Track timestamps
    if args.status == "in_progress" and prev_status != "in_progress":
        step["started_at"] = now
        step["attempts"] = step.get("attempts", 0) + 1
    elif args.status in ("completed", "failed", "skipped"):
        step["completed_at"] = now
        if "started_at" in step:
            try:
                started = datetime.strptime(step["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                ended = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                step["duration_ms"] = int((ended - started).total_seconds() * 1000)
            except Exception:
                pass

    # Handle output
    output_data = None
    if args.output_file:
        with open(args.output_file, encoding="utf-8") as f:
            output_data = json.load(f)
    elif args.output_json:
        output_data = json.loads(args.output_json)
    elif args.output_stdin:
        raw = sys.stdin.read().strip()
        if raw:
            output_data = json.loads(raw)

    if output_data is not None and args.status == "completed":
        out_file = pdir / f"{args.step_id}.json"
        tmp = out_file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, out_file)
        step["output_file"] = out_file.name

    # Handle artifacts mapping
    if args.artifacts and args.status == "completed":
        try:
            artifacts = json.loads(args.artifacts)
            if not isinstance(artifacts, dict):
                print("WARNING: --artifacts must be a JSON object (dict), ignoring", file=sys.stderr)
            else:
                # Normalize paths to be relative to project root
                project_str = str(project)
                normalized = {}
                for key, path in artifacts.items():
                    if not isinstance(path, str):
                        continue
                    p_abs = Path(path)
                    if p_abs.is_absolute():
                        try:
                            rel = p_abs.relative_to(project)
                            normalized[key] = str(rel)
                        except ValueError:
                            normalized[key] = path
                    else:
                        normalized[key] = path
                step["artifacts"] = normalized
        except json.JSONDecodeError as e:
            print(f"WARNING: --artifacts invalid JSON: {e}, ignoring", file=sys.stderr)

    if args.error:
        step["error"] = args.error
    elif args.status != "failed" and "error" in step:
        del step["error"]

    # Git-чекпойнт worktree на закрытии шага — точка восстановления кода для rollback.py
    # (служебный ref, ветки/HEAD/индекс не трогаются). Fail-soft: без git закрытие шага
    # не падает, но rollback откажется трогать код без чекпойнта. Блок ДО сериализации,
    # чтобы step["checkpoint"] ушёл в манифест одной записью.
    if args.status == "completed" and prev_status != "completed":
        try:
            from checkpoint import create_checkpoint
            _ckpt_sha = create_checkpoint(project, args.feature, args.step_id)
            if _ckpt_sha:
                step["checkpoint"] = _ckpt_sha[:12]
        except Exception as e:
            print(f"WARNING: checkpoint failed: {e}", file=sys.stderr)

    manifest["last_update"] = now

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    print(json.dumps({
        "status": "updated",
        "step_id": args.step_id,
        "new_status": args.status,
        "output_saved": step.get("output_file") is not None and args.status == "completed",
    }, ensure_ascii=False))

    if escalate_failed:
        sys.exit(3)


if __name__ == "__main__":
    main()
