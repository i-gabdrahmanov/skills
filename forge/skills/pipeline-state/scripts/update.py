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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import load_pipeline_config, repo_root, safe_load_json
from phase_sync import sync_gate_from_manifest

# Соглашение «какие фазы обязаны идти через субагента» — ЕДИНЫЙ источник pipeline_phases
# (co-located feature-pipeline). best-effort импорт + inline-fallback, чтобы переименование
# префикса в одном месте не отключало enforcement молча.
_SUBAGENT_PREFIXES = ("02-sdd", "02-design", "04-test", "04-build", "05-tests", "06-spec",
                      "lite-red", "lite-green", "lite-verify")
_GATE_RESULT_PREFIXES = ("04-test", "04-build", "05-tests", "lite-red", "lite-green", "lite-verify")
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "feature-pipeline" / "scripts"))
    import pipeline_phases as _pp
    _requires_subagent = _pp.requires_subagent
    _SUBAGENT_PREFIXES = _pp.SUBAGENT_PHASE_PREFIXES
    _requires_gate_result = _pp.requires_gate_result
    _GATE_RESULT_PREFIXES = _pp.GATE_RESULT_PREFIXES
except Exception:
    def _requires_subagent(step_id) -> bool:
        return isinstance(step_id, str) and step_id.startswith(tuple(_SUBAGENT_PREFIXES))

    def _requires_gate_result(step_id) -> bool:
        return isinstance(step_id, str) and step_id.startswith(tuple(_GATE_RESULT_PREFIXES))


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


def _judges_dir(project: Path, skill: str, feature: str) -> Path:
    """Путь к каталогу вердиктов судей."""
    return project / "ground" / "statements" / skill / feature / "judges"


def _overrides_dir(project: Path, skill: str, feature: str) -> Path:
    """Путь к каталогу ручных override-файлов."""
    return project / "ground" / "statements" / skill / feature / "overrides"


def _origins_dir(project: Path, skill: str, feature: str) -> Path:
    """Каталог evidence-маркеров происхождения шага. Маркер <step_id>.json пишет ТОЛЬКО
    state-recorder на реальном SubagentStop — поэтому его наличие доказывает, что шаг
    выполнен субагентом, а не подделан флагом --closed-by."""
    return project / "ground" / "statements" / skill / feature / "_origins"


def _has_origin_marker(project: Path, skill: str, feature: str, step_id: str) -> bool:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(step_id)).strip("-") or "x"
    return (_origins_dir(project, skill, feature) / f"{safe}.json").exists()


def _load_override(project: Path, skill: str, feature: str, judge_name: str) -> dict | None:
    """Читает override-файл судьи, если существует. None — нет override."""
    path = _overrides_dir(project, skill, feature) / f"{judge_name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _check_judges(step: dict, project: Path, skill: str, feature: str):
    """
    Детерминированная блокировка: если шаг помечен completed, но не все его
    required_judges пройдены — выкинуть исключение.

    Исключение: если для судьи есть ручной override-файл (overrides/<judge>.json),
    блокировка снимается и факт отклонения фиксируется в manifest-step как предупреждение.
    Создание override — R4-класс: gate-guard пропускает override_judge.py только при
    approval-маркере ground/approvals/gate-override-<judge>.json (после явного «да»
    пользователя). См. _override_hint.
    """
    required = step.get("required_judges", [])
    if not required:
        return

    judges_dir = _judges_dir(project, skill, feature)
    blocking = []
    overridden = []

    for judge_name in required:
        verdict_path = judges_dir / f"{judge_name}.json"

        # 1. Нет вердикта вообще
        if not verdict_path.exists():
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                overridden.append(
                    f"⚠️  '{judge_name}' не запускался — пропущен вручную. "
                    f"Причина: {ov.get('reason', '?')}"
                )
                continue
            blocking.append(
                f"❌ Вердикт '{judge_name}.json' не найден — судья не запускался.\n"
                + _override_hint(judge_name, feature, step["id"], "<объяснение>")
            )
            continue

        # 2. Вердикт есть, но повреждён
        try:
            verdict = json.loads(verdict_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                overridden.append(
                    f"⚠️  '{judge_name}' повреждён — пропущен вручную. "
                    f"Причина: {ov.get('reason', '?')}"
                )
                continue
            blocking.append(f"❌ Вердикт '{judge_name}.json' повреждён: {e}")
            continue

        # 2b. Схема-санити + ПРОВЕНАНС: настоящий вердикт run_judge несёт produced_by:"run_judge",
        # passed:bool И один из verdict/checks/summary/step_id. Рукописный/поддельный (в т.ч. голый
        # {"passed":true} или дописанный руками) → блок: «перезапусти судью, не правь файл руками».
        if (not isinstance(verdict, dict) or verdict.get("produced_by") != "run_judge"
                or not isinstance(verdict.get("passed"), bool)
                or not any(k in verdict for k in ("verdict", "checks", "summary", "step_id"))):
            ov = _load_override(project, skill, feature, judge_name)
            if ov:
                overridden.append(f"⚠️  '{judge_name}' схема/провенанс невалидны — пропущен вручную. "
                                  f"Причина: {ov.get('reason', '?')}")
                continue
            blocking.append(
                f"❌ Вердикт '{judge_name}.json' не похож на вывод run_judge "
                f"(нужно produced_by:'run_judge' + passed:bool + verdict/checks/summary). "
                f"Перезапусти судью (run_judge.py), не правь файл руками."
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
    подделать происхождение. Теперь проверяется НАЛИЧИЕ evidence-маркера _origins/<step_id>.json,
    который пишет ТОЛЬКО state-recorder на реальном SubagentStop (рантайм-событие, не тул модели).
    Флаг --closed-by больше не является доказательством.

    Escape-hatch: overrides/subagent-origin.json (как у судей) — снимает блок с предупреждением
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
    origins_dir = project / DATA_DIR / "statements" / skill / feature / "_origins"
    no_markers_at_all = not origins_dir.exists() or not any(origins_dir.glob("*.json"))
    arming_hint = ""
    if no_markers_at_all:
        arming_hint = (
            "\n   ДИАГНОСТИКА: ни одного _origins-маркера у фичи нет — вероятно харнес не армлен "
            "(SubagentStop-хук не срабатывает). Прогони `python3 .gigacode/hooks/preflight.py "
            "--project .` — он должен вернуть exit 0; если ругается на settings.json/hooks — "
            "сначала deploy. Override уместен только когда agent() реально недоступен."
        )
    raise RuntimeError(
        f"Шаг {step_id} нельзя закрыть: нет evidence, что фаза прошла через субагента "
        f"(_origins/{step_id}.json от SubagentStop отсутствует; флаг --closed-by теперь не "
        f"считается доказательством). Прогони фазу через agent(subagent_type=...) — "
        f"state-recorder запишет evidence и закроет шаг сам. Если agent() реально недоступен:\n"
        + _override_hint("subagent-origin", feature, step_id, "<почему inline допустимо>")
        + f"{arming_hint}"
    )


def _gate_result_path(project: Path, skill: str, feature: str, step_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(step_id)).strip("-") or "x"
    return project / "ground" / "statements" / skill / feature / "gates" / f"{safe}.json"


def _check_gate_result(step: dict, project: Path, skill: str, feature: str):
    """Гарантия «шаг закрыт, потому что детерминированный гейт РЕАЛЬНО прошёл».

    Для build/verify-шагов (04-test/04-build/05-tests, lite-red/green/verify) слово субагента
    («status: completed» в его JSON) — не доказательство: слабая модель возвращает completed
    при упавшей сборке. Требуем gates/<step_id>.json с провенансом produced_by:"record_gate"
    и passed:true — его пишет record_gate.py по фактическому exit-коду команды гейта.
    Escape-hatch: overrides/gate-result-<step_id>.json.
    """
    step_id = step.get("id", "")
    if not _requires_gate_result(step_id):
        return
    gp = _gate_result_path(project, skill, feature, step_id)
    problem = None
    if not gp.exists():
        problem = f"артефакт гейта gates/{gp.name} не найден — гейт шага не запускался через record_gate.py"
    else:
        try:
            rec = json.loads(gp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            rec = None
            problem = f"артефакт гейта повреждён: {e}"
        if rec is not None:
            if not isinstance(rec, dict) or rec.get("produced_by") != "record_gate" \
                    or not isinstance(rec.get("passed"), bool):
                problem = ("артефакт гейта не похож на вывод record_gate.py "
                           "(нужно produced_by:'record_gate' + passed:bool) — не пиши его руками")
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
    держит этот счётчик. Escape-hatch: overrides/step-reopen-<step_id>.json."""
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

    # Брейк ре-итераций: переоткрытие закрытого шага лимитируется quality.max_step_reopens
    if (not args.skip_judges and args.status in ("pending", "in_progress")
            and prev_status in ("completed", "failed")):
        _check_reopen_limit(step, project, args.skill, args.feature)

    # Детерминированная блокировка: не даём закрыть шаг без судей и без субагентного происхождения
    if not args.skip_judges and args.status == "completed" and prev_status != "completed":
        _check_subagent_origin(step, args.closed_by, project, args.skill, args.feature)
        _check_gate_result(step, project, args.skill, args.feature)
        _check_judges(step, project, args.skill, args.feature)

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
        with open(args.output_file) as f:
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
        with open(tmp, "w") as f:
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

    manifest["last_update"] = now

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    # Синхронизация gate.json из manifest.json
    try:
        sync_gate_from_manifest(str(project), args.feature, args.skill)
    except Exception as e:
        print(f"WARNING: phase_sync failed: {e}", file=sys.stderr)

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
