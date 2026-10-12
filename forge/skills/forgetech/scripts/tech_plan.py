#!/usr/bin/env python3
"""tech_plan.py — донастройка скоупа утверждённого плана техзадачи (amend).

Типовой случай апгрейда: tech-diag по release notes нашёл ClientConfig.java, а после подъёма
версии компиляция падает ещё в трёх файлах, о которых release notes молчат. tech-guard не
пускает правку вне targets — и это правильно: скоуп утвердил человек. Выход — короткий цикл,
а не откат на tech-diag (tasks/018 §5.1):

  1. tech-apply возвращает status:"needs_scope" со списком {path, reason} — причина из вывода
     компилятора/тестов, не пересказ;
  2. оркестратор: `tech_plan.py amend --add-target <path> --reason "..."` — дописывает targets
     (и, если нужно, expected_changes), пишет запись в amendments и ГАСИТ согласие
     tech-plan-<F>: оно было дано на другой скоуп;
  3. вопрос пользователю «нужно поправить ещё N файлов: … — ок?» → новое согласие с привязкой
     к новому плану (record_approval --bind-file);
  4. tech-apply продолжает с того же места. Снимок «до» НЕ переснимается: добавленные файлы ещё
     не правились — поэтому amend отказывает, если путь уже изменён относительно HEAD.

Ограничения: только добавление; R5-путь — отказ; число amend-циклов — quality.max_step_reopens.

`revoke` — погасить согласие, не трогая план. Нужен, когда план поправили НЕ через amend
(вернули на tech-diag, правки пользователя): согласие привязано к sha256 старого плана и уже
не действует, но остаётся «активным» — и новое согласие record_approval пропустил бы как дубль.
Гашение согласия только сужает права (ничего не разрешает), поэтому отдельного «да» не требует.

Usage:
    tech_plan.py amend --project <root> --feature <KEY|slug> --add-target <path> [--add-target …]
        --reason "<почему, из вывода сборки>" [--add-change '<json-объект цели>' …]
    tech_plan.py revoke --project <root> --feature <KEY|slug> --reason "<почему>"
Exit: 0 — план дописан / согласие погашено; 1 — отказ (причина в stderr); 2 — ошибка входа.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _techlib as T  # noqa: E402

_DEFAULT_LIMIT = 3


def _limit(root: Path) -> int:
    try:
        from _config_loader import load_project_config
        v = int((load_project_config(root).get("quality") or {}).get("max_step_reopens", _DEFAULT_LIMIT))
        return v if v > 0 else _DEFAULT_LIMIT
    except Exception:  # noqa: BLE001
        return _DEFAULT_LIMIT


def cmd_amend(args) -> int:
    root = T.project_root(args.project)
    path = T.plan_path(root, args.feature)
    plan = T.load_plan(path)
    errs = T.validate_plan(plan)
    if errs:
        raise T.TechError("исходный план невалиден — правь его фазой tech-diag:\n  - " + "\n  - ".join(errs))
    if not (args.reason or "").strip():
        raise T.TechError("--reason обязателен: почему нужен файл (строка из вывода сборки)")
    amendments = plan.get("amendments") or []
    limit = _limit(root)
    if len(amendments) >= limit:
        raise T.TechError(f"лимит донастроек скоупа исчерпан ({len(amendments)}/{limit}, "
                          f"quality.max_step_reopens) — ESCALATE: скоуп оценён неверно, верни "
                          f"задачу на tech-diag и спроси пользователя")
    new_targets = [t for t in args.add_target or [] if t not in plan["targets"]]
    new_changes = []
    for raw in args.add_change or []:
        try:
            ch = json.loads(raw)
        except ValueError as e:
            raise T.TechError(f"--add-change не JSON: {e}")
        new_changes.append(ch)
    if not new_targets and not new_changes:
        raise T.TechError("нечего добавлять: пути уже в targets, целей нет")
    try:
        import risk_ladder as R
    except Exception:  # noqa: BLE001
        R = None
    for t in new_targets:
        if R is not None and R.classify("Write", {"file_path": t}, str(root))["level"] == "R5":
            raise T.TechError(f"'{t}' — R5-путь (change-advisory): техзадачей не правится")
    if new_targets:
        dirty = T.git_dirty(root, new_targets)
        if dirty:
            raise T.TechError(f"пути уже изменены относительно HEAD: {', '.join(dirty)} — снимок "
                              f"«до» для них недействителен. Откати правку этих файлов (git "
                              f"checkout -- <файл>) и повтори amend")
    candidate = dict(plan)
    candidate["targets"] = list(plan["targets"]) + new_targets
    candidate["expected_changes"] = list(plan.get("expected_changes") or []) + new_changes
    candidate["amendments"] = amendments + [{
        "at": T.iso_now(), "add_targets": new_targets, "add_changes": new_changes,
        "reason": args.reason.strip()}]
    errs = T.validate_plan(candidate)
    if errs:
        raise T.TechError("после дополнения план невалиден:\n  - " + "\n  - ".join(errs))
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)

    import forge_events as FE
    from _project import safe_component
    key = safe_component(f"tech-plan-{args.feature}")
    if FE.approval(root, key) is not None:
        FE.revoke_approval(root, key, reason=f"скоуп дополнен tech_plan.py amend: "
                                             f"{', '.join(new_targets) or 'цели'} — {args.reason.strip()}")
    rec = Path(__file__).resolve().parents[2] / "pipeline-state" / "scripts" / "record_approval.py"
    print(json.dumps({"amended": True, "added_targets": new_targets,
                      "added_changes": len(new_changes), "approval_revoked": key},
                     ensure_ascii=False))
    print(f"[tech_plan] Согласие '{key}' погашено: оно давалось на прежний скоуп. Спроси "
          f"пользователя: «Нужно поправить ещё {len(new_targets)} файл(а): "
          f"{'; '.join(new_targets)} — {args.reason.strip()}. Ок?» и ТОЛЬКО после явного «да»:\n"
          f"  python3 {rec} --project {root} --key {key} --approved-by user --reason \"скоуп "
          f"дополнен\" --bind-file {T.rel(root, path)} --evidence \"<ответ пользователя дословно>\"",
          file=sys.stderr)
    return 0


def cmd_revoke(args) -> int:
    root = T.project_root(args.project)
    import forge_events as FE
    from _project import safe_component
    key = safe_component(f"tech-plan-{args.feature}")
    if FE.approval(root, key) is None:
        print(json.dumps({"revoked": False, "key": key, "note": "активного согласия нет"},
                         ensure_ascii=False))
        return 0
    FE.revoke_approval(root, key, reason=f"tech_plan.py revoke: {args.reason.strip()}")
    print(json.dumps({"revoked": True, "key": key}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("amend")
    a.add_argument("--project", default=None)
    a.add_argument("--feature", required=True)
    a.add_argument("--add-target", action="append")
    a.add_argument("--add-change", action="append", help="JSON-объект цели (как в expected_changes)")
    a.add_argument("--reason", required=True)
    r = sub.add_parser("revoke")
    r.add_argument("--project", default=None)
    r.add_argument("--feature", required=True)
    r.add_argument("--reason", required=True)
    args = ap.parse_args()
    try:
        return cmd_amend(args) if args.cmd == "amend" else cmd_revoke(args)
    except T.TechError as e:
        print(f"[tech_plan] DENY: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
