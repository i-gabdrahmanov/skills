#!/usr/bin/env python3
"""rollback.py — санкционированный откат пайплайна к шагу X («X переделывается»).

Семантика: X и всё после него (по порядку манифеста + транзитивное замыкание depends_on)
→ pending; evidence этих шагов (_origins/gates/judges/step-overrides/doc-approvals)
архивируется в rollbacks/<ts>/ — повторное закрытие не пройдёт по старым доказательствам;
код восстанавливается на git-чекпойнт последнего ОСТАЮЩЕГОСЯ completed-шага
(fallback-цепочка назад до 00-baseline), точечным `git restore --source=<ref>` по скоупу
журнала file-journal (ручные правки человека вне пайплайна не затираются). Динамические
шаги (04-test-*/04-build-*/07-deliver-*) при откате фазы дизайна удаляются из манифеста
(add_steps.py пересоздаст по новому task-plan). Jira-задачи и запушенные ветки/PR НЕ
трогаются — печатается список сирот и черновик комментария в Story (политика
stacked-pr-delivery: уборку решает человек).

Откат — R4-класс (уничтожает рабочие результаты): gate-guard пропускает запуск только при
approval-маркере ground/approvals/rollback-<feature>-<to-step>.json (record_approval после
явного «да» пользователя на план --dry-run). Второй слой: скрипт САМ валидирует маркер и
потребляет его (одно согласие = один откат). --dry-run/--list — readonly, не гейтятся.

Usage:
    rollback.py --project <root> --skill <name> --feature <slug> --to-step <id>
                [--dry-run] [--no-code] [--unscoped]
    rollback.py --project <root> --skill <name> --feature <slug> --to-phase <phase-id>
    rollback.py --project <root> --skill <name> --feature <slug> --list

Exit: 0 ok; 2 ошибка аргументов/состояния; 3 ESCALATE (нет валидного approval-маркера).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import repo_root, safe_load_json, safe_slug
from phase_sync import sync_gate_from_manifest
import checkpoint

# Динамические шаги / фазы — единый источник pipeline_phases (best-effort, как в update.py).
_DYNAMIC_PREFIXES = ("04-test-", "04-build-", "07-deliver-")
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "feature-pipeline" / "scripts"))
    import pipeline_phases as _pp
    _DYNAMIC_PREFIXES = (_pp.TEST_STEP_PREFIX, _pp.BUILD_STEP_PREFIX, _pp.DELIVER_STEP_PREFIX)
    _guess_phase = _pp.guess_phase
except Exception:
    _pp = None

    def _guess_phase(step_id: str) -> str:
        prefix_phase = {
            "00-": "00-brd", "01-": "01-grounding", "02-sdd": "02-sdd",
            "02-eval-plan": "02-eval-plan", "02-": "02-design", "03-": "03-jira",
            "04-": "04-tdd", "05-": "05-verify", "06-": "06-document",
            "07-deliver-": "07-deliver", "07-report": "07-report", "07-": "07-deliver",
        }
        for prefix, phase in sorted(prefix_phase.items(), key=lambda x: -len(x[0])):
            if isinstance(step_id, str) and step_id.startswith(prefix):
                return phase
        return step_id


DATA_DIR = "ground"
BASELINE_STEP = "00-baseline"
# Каталоги/пути, которые git-restore отката НИКОГДА не трогает: ground/ откатывается
# манифест-хирургией этого же скрипта, служебные каталоги — не код фичи.
_RESTORE_SKIP_RE = re.compile(r"^(?:ground|\.gigacode|\.git|\.qwen|\.claude)(?:/|$)")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s)).strip("-") or "x"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _feature_dir(project: Path, skill: str, feature: str) -> Path:
    return project / DATA_DIR / "statements" / skill / feature


def _approval_key(feature: str, target: str) -> str:
    return _safe(f"rollback-{feature}-{target}")


def _approval_marker_valid(project: Path, key: str) -> bool:
    """Как update._approval_marker_valid: засчитывается ТОЛЬКО провенанс record_approval."""
    path = project / DATA_DIR / "approvals" / f"{key}.json"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(d, dict) and d.get("produced_by") == "record_approval" \
        and d.get("key") == key


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _git(project: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project), *args],
                          capture_output=True, text=True, timeout=timeout)


# ── план отката ────────────────────────────────────────────────────────────────────────

def compute_reset_set(steps: list[dict], to_step: str) -> list[str]:
    """X ∪ всё после X по порядку манифеста ∪ транзитивное замыкание по depends_on."""
    ids = [s.get("id") for s in steps]
    idx = ids.index(to_step)
    reset = set(ids[idx:])
    # замыкание: кто зависит от reset-шага — тоже reset (порядок в манифесте не гарантия)
    changed = True
    while changed:
        changed = False
        for s in steps:
            sid = s.get("id")
            if sid in reset:
                continue
            if any(d in reset for d in (s.get("depends_on") or [])):
                reset.add(sid)
                changed = True
    return [i for i in ids if i in reset]  # в порядке манифеста


def _restore_ref_for(project: Path, feature: str, steps: list[dict],
                     reset_ids: set[str]) -> tuple[str | None, str | None]:
    """(step_id, ref) чекпойнта последнего остающегося completed-шага перед X;
    fallback назад по цепочке, затем 00-baseline. (None, None) — чекпойнтов нет вообще."""
    survivors = [s for s in steps
                 if s.get("id") not in reset_ids and s.get("status") == "completed"]
    for s in reversed(survivors):
        sid = s.get("id")
        if checkpoint.checkpoint_for(project, feature, sid):
            return sid, checkpoint.checkpoint_ref(feature, sid)
    if checkpoint.checkpoint_for(project, feature, BASELINE_STEP):
        return BASELINE_STEP, checkpoint.checkpoint_ref(feature, BASELINE_STEP)
    return None, None


def _journal_scope(project: Path, skill: str, feature: str,
                   after_ts: datetime | None) -> tuple[set[str], list[str]]:
    """(пути журнала после чекпойнта, warnings). Абсолютные пути (вне project root,
    например docs-репо) не восстанавливаются — уходят в warnings."""
    jpath = _feature_dir(project, skill, feature) / "journal" / "files.jsonl"
    paths: set[str] = set()
    warnings: list[str] = []
    if not jpath.exists():
        return paths, warnings
    for line in jpath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(rec.get("ts", ""))
        if after_ts is not None and (ts is None or ts <= after_ts):
            continue
        if rec.get("op") == "bash-opaque":
            warnings.append(
                f"журнал: команда меняла файлы неизвестно где — проверь руками: "
                f"{rec.get('command', '?')[:160]}")
            continue
        for p in rec.get("paths") or []:
            if not isinstance(p, str) or not p:
                continue
            if p.startswith("/") or re.match(r"^[A-Za-z]:/", p):
                warnings.append(f"журнал: путь вне репо проекта (не восстанавливается): {p}")
                continue
            paths.add(p)
    return paths, warnings


def _worktree_changes(project: Path, ref: str) -> tuple[set[str], set[str]]:
    """(изменённые/удалённые vs ref, untracked). Полнота отката — от git, журнал лишь скоупит."""
    r = _git(project, "diff", "--name-only", "-z", ref)
    diff = {p for p in r.stdout.split("\0") if p} if r.returncode == 0 else set()
    r = _git(project, "ls-files", "--others", "--exclude-standard", "-z")
    untracked = {p for p in r.stdout.split("\0") if p} if r.returncode == 0 else set()
    return diff, untracked


def build_code_plan(project: Path, skill: str, feature: str, steps: list[dict],
                    reset_ids: set[str], unscoped: bool) -> dict:
    """План отката кода: {ref, restore:[], delete:[], skipped:int, warnings:[]}."""
    anchor_step, ref = _restore_ref_for(project, feature, steps, reset_ids)
    if ref is None:
        return {"ref": None, "anchor_step": None, "restore": [], "delete": [], "skipped": 0,
                "warnings": ["чекпойнтов нет (фича старше механизма?) — код не трогаю; "
                             "откати state с --no-code и разбери код руками"]}
    warnings: list[str] = []
    meta = checkpoint.checkpoint_meta(project, feature, anchor_step) or {}
    after_ts = _parse_ts(meta.get("ts", ""))

    diff, untracked = _worktree_changes(project, ref)
    changed_all = {p for p in (diff | untracked) if not _RESTORE_SKIP_RE.match(p)}

    if unscoped:
        scope = changed_all
    else:
        journal_paths, jwarn = _journal_scope(project, skill, feature, after_ts)
        warnings.extend(jwarn)
        if not journal_paths and changed_all:
            warnings.append(
                "журнал изменённых файлов пуст (file-journal не был активен?) — restore-set "
                "пуст; для полного отката по git-diff используй --unscoped")
        scope = changed_all & journal_paths

    ref_paths = checkpoint.read_tree_paths(project, ref)
    restore = sorted(p for p in scope if p in ref_paths)
    delete = sorted(p for p in scope if p not in ref_paths)
    skipped = len(changed_all - scope)
    return {"ref": ref, "anchor_step": anchor_step, "restore": restore, "delete": delete,
            "skipped": skipped, "warnings": warnings}


def apply_code_plan(project: Path, plan: dict) -> list[str]:
    """Точечный restore/delete по плану (НЕ reset --hard/checkout .). Возвращает ошибки."""
    errors: list[str] = []
    ref = plan["ref"]
    restore = plan["restore"]
    for i in range(0, len(restore), 100):
        chunk = restore[i:i + 100]
        r = _git(project, "restore", "--source", ref, "--worktree", "--staged", "--", *chunk,
                 timeout=300)
        if r.returncode != 0:
            errors.append(f"git restore: {r.stderr.strip()[:300]}")
    for p in plan["delete"]:
        try:
            (project / p).unlink(missing_ok=True)
        except OSError as e:
            errors.append(f"unlink {p}: {e}")
    return errors


# ── evidence / сироты ──────────────────────────────────────────────────────────────────

def _archive_move(src: Path, dest_root: Path, rel: str, moved: list[str]) -> None:
    if not src.exists():
        return
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    moved.append(rel)


def archive_evidence(project: Path, skill: str, feature: str, reset_steps: list[dict],
                     archive_ts: str, approval_key: str) -> list[str]:
    """Перемещает (не удаляет) evidence reset-шагов в rollbacks/<ts>/ — иначе повторное
    закрытие прошло бы по старым доказательствам. Возвращает список перемещённого."""
    fdir = _feature_dir(project, skill, feature)
    dest = fdir / "rollbacks" / archive_ts
    moved: list[str] = []

    judges: set[str] = set()
    reset_has_eval_plan = False
    for step in reset_steps:
        sid = step.get("id", "")
        safe = _safe(sid)
        judges.update(step.get("required_judges") or [])
        if sid.startswith("02-eval-plan"):
            reset_has_eval_plan = True
        _archive_move(fdir / "_origins" / f"{safe}.json", dest, f"_origins/{safe}.json", moved)
        _archive_move(fdir / "gates" / f"{safe}.json", dest, f"gates/{safe}.json", moved)
        out_name = step.get("output_file") or f"{sid}.json"
        _archive_move(fdir / out_name, dest, f"outputs/{out_name}", moved)
        for kind in ("gate-result", "step-reopen", "step-skip", "doc-approved"):
            name = f"{kind}-{sid}.json"
            _archive_move(fdir / "overrides" / name, dest, f"overrides/{name}", moved)
        # doc-approvals: утверждение дока снимается вместе с откатом его фазы
        for prefix, doc in (("00-brd", "brd"), ("02-sdd", "sdd")):
            if sid.startswith(prefix):
                for marker in (f"{doc}-approved-{feature}", f"{doc}-review-{feature}"):
                    _archive_move(project / DATA_DIR / "approvals" / f"{marker}.json",
                                  dest, f"approvals/{marker}.json", moved)

    for judge in sorted(judges):
        jn = _safe(judge)
        _archive_move(fdir / "judges" / f"{jn}.json", dest, f"judges/{jn}.json", moved)
    # perpetual error store судей — заводится с чистого листа
    _archive_move(fdir / "judges" / "errors.json", dest, "judges/errors.json", moved)
    if reset_has_eval_plan:
        _archive_move(fdir / "evals.json", dest, "evals.json", moved)
    # потребить approval-маркер отката: одно согласие = один откат
    _archive_move(project / DATA_DIR / "approvals" / f"{approval_key}.json",
                  dest, f"approvals/{approval_key}.json", moved)
    return moved


def orphan_report(project: Path, reset_steps: list[dict], feature: str,
                  to_step: str) -> list[str]:
    """Сироты глубокого отката: Jira-задачи / ветки / PR. Только сообщения — НИЧЕГО не
    удаляем и не постим (политика stacked-pr-delivery: решает человек)."""
    lines: list[str] = []
    story_key = None
    for step in reset_steps:
        sid = step.get("id", "")
        arts = step.get("artifacts") or {}
        if sid.startswith("03-jira"):
            jr = arts.get("jira-result")
            data = _read_json_relaxed(project, jr)
            if data:
                story_key = (data.get("story") or {}).get("key") or data.get("story_key")
                tasks = data.get("tasks") or data.get("subtasks") or []
                keys = [t.get("key") for t in tasks if isinstance(t, dict) and t.get("key")]
                lines.append(f"⚠️  СИРОТЫ Jira: Story {story_key or '?'}, сабтаски "
                             f"{', '.join(keys) or '?'} — созданы до отката, НЕ удаляю. "
                             f"Реши сам: закрыть/переиспользовать.")
            else:
                lines.append("⚠️  Шаг 03-jira откатывается: созданные Story/сабтаски "
                             "становятся сиротами (jira-tasks-result.json не прочитан) — "
                             "проверь Jira руками.")
        elif sid.startswith("07-deliver-"):
            pr = _read_json_relaxed(project, arts.get("pr-info"))
            if pr:
                lines.append(f"⚠️  СИРОТЫ доставки {sid}: ветка {pr.get('branch', '?')}, "
                             f"PR {pr.get('pr_url') or pr.get('url') or '?'} — запушены, "
                             f"НЕ удаляю. Закрой PR/ветку руками, если не нужны.")
            else:
                lines.append(f"⚠️  Шаг {sid} откатывается: запушенные ветки/PR становятся "
                             f"сиротами — проверь удалённый репозиторий руками.")
    if story_key:
        lines.append(
            f"   Черновик комментария в Story {story_key} (постить или нет — решаешь ты):\n"
            f"   «Фича {feature} откачена до шага {to_step}: план работ будет пересобран, "
            f"текущие сабтаски могут потерять актуальность.»")
    return lines


def _read_json_relaxed(project: Path, rel: str | None) -> dict | None:
    if not rel or not isinstance(rel, str):
        return None
    p = Path(rel)
    if not p.is_absolute():
        p = project / rel
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


# ── main ───────────────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None)
    p.add_argument("--skill", required=True)
    p.add_argument("--feature", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--to-step", help="Шаг X: переделывается (X и всё после → pending)")
    g.add_argument("--to-phase", help="Сахар: первый шаг фазы (по guess_phase)")
    g.add_argument("--list", action="store_true",
                   help="Чекпойнты + история откатов (readonly)")
    p.add_argument("--dry-run", action="store_true", help="Полный план без изменений (readonly)")
    p.add_argument("--no-code", action="store_true", help="Только state, код не трогать")
    p.add_argument("--unscoped", action="store_true",
                   help="Restore-set = полный git-diff vs checkpoint (без пересечения с журналом)")
    args = p.parse_args()

    try:
        safe_slug(args.feature)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    project = Path(args.project or repo_root()).resolve()
    fdir = _feature_dir(project, args.skill, args.feature)
    manifest_path = fdir / "manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    manifest = safe_load_json(manifest_path, what="manifest")
    steps = manifest.get("steps", [])

    if args.list:
        print(json.dumps({
            "checkpoints": checkpoint.list_checkpoints(project, args.feature),
            "rollback_history": manifest.get("rollback_history", []),
        }, indent=2, ensure_ascii=False))
        return 0

    # --to-phase → первый шаг фазы в порядке манифеста
    to_step = args.to_step
    if args.to_phase:
        to_step = next((s.get("id") for s in steps
                        if _guess_phase(s.get("id", "")) == args.to_phase), None)
        if not to_step:
            print(f"ERROR: в манифесте нет шагов фазы '{args.to_phase}'", file=sys.stderr)
            return 2
    if not any(s.get("id") == to_step for s in steps):
        print(f"ERROR: шаг '{to_step}' не найден в манифесте", file=sys.stderr)
        return 2

    # Гонка с работающим субагентом: откат только между шагами
    in_prog = [s.get("id") for s in steps if s.get("status") == "in_progress"]
    if in_prog:
        print(f"ERROR: есть in_progress-шаги: {in_prog}. Откат делается только между шагами — "
              f"сначала пометь их failed через update.py (или дождись завершения).",
              file=sys.stderr)
        return 2

    reset_ids = compute_reset_set(steps, to_step)
    reset_set = set(reset_ids)
    reset_steps = [s for s in steps if s.get("id") in reset_set]

    # Динамические шаги удаляются, если откатывается сама фаза дизайна (task-plan пересоберётся)
    design_reset = any(sid.startswith("02-design") for sid in reset_ids)
    dynamic_removed = [s.get("id") for s in reset_steps
                       if design_reset and str(s.get("id", "")).startswith(_DYNAMIC_PREFIXES)]

    code_plan = ({"ref": None, "anchor_step": None, "restore": [], "delete": [],
                  "skipped": 0, "warnings": ["--no-code: код не трогаю"]}
                 if args.no_code else
                 build_code_plan(project, args.skill, args.feature, steps, reset_set,
                                 args.unscoped))

    approval_key = _approval_key(args.feature, args.to_step or args.to_phase)
    plan_view = {
        "feature": args.feature,
        "to_step": to_step,
        "reset_steps": reset_ids,
        "dynamic_removed": dynamic_removed,
        "code": {
            "checkpoint": code_plan["ref"],
            "anchor_step": code_plan["anchor_step"],
            "restore_count": len(code_plan["restore"]),
            "restore": code_plan["restore"][:50],
            "delete_count": len(code_plan["delete"]),
            "delete": code_plan["delete"][:50],
            "skipped_out_of_scope": code_plan["skipped"],
        },
        "warnings": code_plan["warnings"],
        "approval_key": approval_key,
    }

    if args.dry_run:
        print(json.dumps({"dry_run": True, **plan_view}, indent=2, ensure_ascii=False))
        return 0

    # Второй слой enforcement (первый — gate-guard): без валидного маркера не пишем,
    # даже при запуске мимо харнеса.
    if not _approval_marker_valid(project, approval_key):
        print(
            f"⛔ ESCALATE: откат — R4-класс, нужен approval-маркер "
            f"ground/approvals/{approval_key}.json с провенансом record_approval.\n"
            f"   Порядок: (1) покажи пользователю план: rollback.py ... --dry-run;\n"
            f"   (2) ТОЛЬКО после явного «да»: python3 "
            f"{Path(__file__).resolve().parent / 'record_approval.py'} --project {project} "
            f"--key {approval_key} --approved-by user --reason \"<кто/почему>\";\n"
            f"   (3) повтори команду. Маркер одноразовый — потребляется этим откатом.",
            file=sys.stderr,
        )
        return 3

    now = iso_now()
    archive_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Сироты считаются ДО хирургии: она выпотрошит artifacts из этих же step-словарей
    orphan_lines = orphan_report(project, reset_steps, args.feature, to_step)

    # 1. Evidence-архив (перемещение, не удаление) + потребление approval-маркера
    moved = archive_evidence(project, args.skill, args.feature, reset_steps,
                             archive_ts, approval_key)

    # 2. Манифест-хирургия
    prev_counters: dict[str, dict] = {}
    removed_copies: list[dict] = []
    kept_steps: list[dict] = []
    for step in steps:
        sid = step.get("id")
        if sid not in reset_set:
            kept_steps.append(step)
            continue
        if sid in dynamic_removed:
            removed_copies.append(dict(step))
            continue
        if step.get("reopens") or step.get("failures"):
            prev_counters[sid] = {"reopens": step.get("reopens", 0),
                                  "failures": step.get("failures", 0)}
        step["status"] = "pending"
        for f in ("completed_at", "started_at", "duration_ms", "closed_by", "output_file",
                  "artifacts", "error", "override_warnings", "checkpoint",
                  "reopens", "failures"):
            step.pop(f, None)
        kept_steps.append(step)
    manifest["steps"] = kept_steps
    manifest["last_update"] = now
    manifest.setdefault("rollback_history", []).append({
        "ts": now,
        "to_step": to_step,
        "reset_steps": reset_ids,
        "dynamic_removed": removed_copies,
        "restore_ref": code_plan["ref"],
        "restore_planned": len(code_plan["restore"]),
        "delete_planned": len(code_plan["delete"]),
        "evidence_archived": f"rollbacks/{archive_ts}" if moved else None,
        "prev_counters": prev_counters,
        "approval_key": approval_key,
        "produced_by": "rollback",
    })

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    # 3. gate.json пересобирается из манифеста
    try:
        sync_gate_from_manifest(str(project), args.feature, args.skill)
    except Exception as e:
        print(f"WARNING: phase_sync failed: {e}", file=sys.stderr)

    # 4. Откат кода
    code_errors: list[str] = []
    if not args.no_code and code_plan["ref"]:
        code_errors = apply_code_plan(project, code_plan)

    # 5. Сироты
    for line in orphan_lines:
        print(line, file=sys.stderr)
    for w in code_plan["warnings"]:
        print(f"⚠️  {w}", file=sys.stderr)
    for e in code_errors:
        print(f"❌ {e}", file=sys.stderr)

    print(json.dumps({
        "status": "rolled_back",
        **plan_view,
        "evidence_archived": moved,
        "code_restored": 0 if args.no_code else len(code_plan["restore"]),
        "code_deleted": 0 if args.no_code else len(code_plan["delete"]),
        "code_errors": len(code_errors),
    }, indent=2, ensure_ascii=False))
    return 0 if not code_errors else 2


if __name__ == "__main__":
    sys.exit(main())
