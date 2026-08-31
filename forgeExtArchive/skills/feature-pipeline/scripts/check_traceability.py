#!/usr/bin/env python3
"""check_traceability.py — сквозной детерминированный judge трассируемости (P2-11).

Матрица «требование → раздел SDD → задача → eval → тест». check_taskplan/check_sdd проверяют
поля по отдельности (acceptance непуст, sdd_ref присутствует), но НЕ замыкают цепочку:
  • sdd_ref проверялся на наличие строки, но НЕ на то, что якорь реально резолвится в sdd.md
    (битая ссылка проходила);
  • НИКТО не проверял, что у каждой задачи есть eval (задача без eval = EDD её не верифицирует).

Этот гейт замыкает цепочку детерминированно (без LLM) и выдаёт матрицу трассируемости:
  task → {sdd_ref резолвится?} → {N eval'ов} → {N acceptance}.

Жёстко (error): задача без eval (при наличии eval-plan); битый sdd_ref-якорь; задача без
acceptance. Мягко (warning): eval-сирота (task_id не из плана); sdd_ref без якоря (не проверить).

Usage:
    check_traceability.py <task-plan.json> [--sdd sdd.md] [--eval-plan eval-plan.json]
        [--strict] [--json]
Exit: 0 = pass (или только warnings без --strict), 2 = разрыв трассировки (error / --strict).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _slug(heading: str) -> str:
    """GitHub-подобный slug заголовка (\\w в py3 включает кириллицу)."""
    h = heading.strip().lower()
    h = re.sub(r"[^\w\s-]", "", h, flags=re.UNICODE)
    h = re.sub(r"\s+", "-", h)
    return h.strip("-")


def md_anchors(text: str) -> set:
    """Все якоря markdown: явные (<a name>, id=, {#a}) + slug заголовков."""
    anchors = set()
    for m in re.finditer(r'<a\s+(?:name|id)\s*=\s*"([^"]+)"', text, re.I):
        anchors.add(m.group(1).lower())
    for m in re.finditer(r"\{#([^}]+)\}", text):
        anchors.add(m.group(1).lower())
    for m in re.finditer(r'\bid\s*=\s*"([^"]+)"', text):
        anchors.add(m.group(1).lower())
    for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        anchors.add(_slug(m.group(1)))
    return anchors


def _ref_anchor(sdd_ref: str) -> str | None:
    """Якорь из sdd_ref ('docs/.../sdd.md#t1' → 't1'); None если якоря нет."""
    if not isinstance(sdd_ref, str) or "#" not in sdd_ref:
        return None
    return sdd_ref.rsplit("#", 1)[1].strip().lower() or None


def analyze(plan: dict, sdd_text: str | None, eval_plan: dict | None) -> dict:
    """Матрица трассируемости + нарушения. eval_plan=None → eval-цепочка не проверяется."""
    tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
    task_ids = {t.get("id") for t in tasks if t.get("id")}
    anchors = md_anchors(sdd_text) if sdd_text else None

    evals_by_task: dict = {}
    orphan_evals = []
    if eval_plan is not None:
        for e in eval_plan.get("evals", []):
            tid = e.get("task_id")
            if tid in task_ids:
                evals_by_task[tid] = evals_by_task.get(tid, 0) + 1
            elif tid:
                orphan_evals.append(e.get("id", tid))

    matrix, errors, warnings = [], [], []
    for t in tasks:
        tid = t.get("id", "?")
        acc = [a for a in (t.get("acceptance") or []) if str(a).strip()]
        sdd_ref = t.get("sdd_ref")
        anchor = _ref_anchor(sdd_ref or "")

        # sdd_ref резолвится?
        if anchors is None:
            sdd_resolved = None  # нет sdd.md — резолв не проверяем здесь (это забота check_sdd)
        elif anchor is None:
            sdd_resolved = None
            if sdd_ref:
                warnings.append(f"task {tid}: sdd_ref '{sdd_ref}' без якоря (#...) — резолв не проверить")
        else:
            sdd_resolved = anchor in anchors
            if not sdd_resolved:
                errors.append(f"task {tid}: sdd_ref якорь '#{anchor}' не найден в sdd.md (битая ссылка)")

        # eval-покрытие
        n_evals = evals_by_task.get(tid, 0) if eval_plan is not None else None
        if eval_plan is not None and n_evals == 0:
            errors.append(f"task {tid}: нет ни одного eval (EDD не верифицирует задачу)")

        # acceptance
        if not acc:
            errors.append(f"task {tid}: пустой acceptance (нечем проверить требование)")

        matrix.append({"task_id": tid, "sdd_ref": sdd_ref, "sdd_resolved": sdd_resolved,
                       "evals": n_evals, "acceptance": len(acc)})

    for oe in orphan_evals:
        warnings.append(f"eval '{oe}': task_id не существует в task-plan (eval-сирота)")

    return {"status": "fail" if errors else "pass",
            "tasks": len(tasks), "matrix": matrix,
            "errors": errors, "warnings": warnings,
            "counts": {"error": len(errors), "warning": len(warnings)}}


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic end-to-end traceability judge.")
    ap.add_argument("plan")
    ap.add_argument("--sdd", help="sdd.md (по умолчанию <папка task-plan>/sdd.md)")
    ap.add_argument("--eval-plan", help="eval-plan.json (по умолчанию <папка task-plan>/eval-plan.json)")
    ap.add_argument("--strict", action="store_true", help="warnings тоже валят (exit 2)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plan_path = Path(args.plan)
    plan = _load(plan_path)
    if plan is None:
        print(json.dumps({"status": "fail", "errors": [f"invalid task-plan: {plan_path}"]},
                         ensure_ascii=False))
        return 2

    sdd_path = Path(args.sdd) if args.sdd else plan_path.parent / "sdd.md"
    sdd_text = sdd_path.read_text(encoding="utf-8", errors="replace") if sdd_path.exists() else None

    ep_path = Path(args.eval_plan) if args.eval_plan else plan_path.parent / "eval-plan.json"
    eval_plan = _load(ep_path) if ep_path.exists() else None

    verdict = analyze(plan, sdd_text, eval_plan)
    failed = verdict["counts"]["error"] > 0 or (args.strict and verdict["counts"]["warning"] > 0)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✗ FAIL" if failed else ("✓ PASS" if not verdict["errors"] and not verdict["warnings"]
                                        else "✓ PASS (warnings)")
        print(f"Traceability gate: {mark}  (задач {verdict['tasks']}, "
              f"ошибок {verdict['counts']['error']}, предупр. {verdict['counts']['warning']})")
        for row in verdict["matrix"]:
            sr = {True: "✓", False: "✗", None: "·"}[row["sdd_resolved"]]
            ev = "n/a" if row["evals"] is None else row["evals"]
            print(f"  {row['task_id']:6} sdd:{sr} evals:{ev} acc:{row['acceptance']}")
        for e in verdict["errors"]:
            print(f"  ✗ {e}")
        for w in verdict["warnings"]:
            print(f"  ⚠ {w}")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
