#!/usr/bin/env python3
"""impl_check.py — что из плана задач фичи реально есть в коде. Вход /forge-merge.

Дельта (sdd.md) — это НАМЕРЕНИЕ: что фича собиралась сделать. Слить её в мастер как есть —
записать в мастер обещание, а не факт. Поэтому merge сначала сверяет task-plan.json фичи с
кодом проекта и пишет итог сверки в журнал мастера рядом с записью о слиянии.

Что сверяется по каждой задаче плана (детерминированно, без LLM):
  • artifacts — новые файлы задачи лежат в коде (тот же суффиксный матч, что у гейта сборки
    check_build.py: пути в плане бывают от корня репо и от module/src/main/java);
  • reuses    — изменяемые существующие файлы на месте и ТРОНУТЫ с начала прогона (git: коммит
    после started_at манифеста либо незакоммиченная правка). Нетронутый reuse — пометка, не
    отказ: правка могла оказаться не нужна, но мастер должен это знать;
  • шаг сборки задачи в манифесте прогона (04-build-<id>; у forgefix — fix-green) закрыт;
  • КРИТЕРИИ ПРИЁМКИ: у каждого (кроме задач, освобождённых от тестов) в коде есть тест с
    маркером `@acceptance <фича>:<задача>.<n>` (check_acceptance.py), и последний гейт шага
    сборки в журнале прогона — `check_acceptance.py --expect green`, passed. Журнал пишет только
    record_gate (state-write-guard), так что «тесты критериев прошли» — факт прогона, а не
    слово модели.

  • РУЧНЫЕ критерии (`verify:"manual"` в task-plan — не всё проверяется тестом): в журнале
    approvals есть согласие человека `acceptance-<ID>` (record_approval), и в мастер уходит,
    кто и как проверил.

Статус задачи: done | missing (нет артефакта) | not-built (шаг сборки не закрыт) |
               no-test (у критерия нет теста) | unverified (тесты критериев есть, но гейт
               приёмки на шаге сборки не проходил) | unconfirmed (ручной критерий человек
               не подтвердил).
Статус фичи:   implemented | partial | not-implemented | no-plan.

Usage:
    impl_check.py --project-root <root> --docs <каталог доков фичи> [--feature F] [--json]
Exit: 0 = implemented/no-plan, 3 = partial/not-implemented (решение человека), 2 = ошибка.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_FP = SCRIPT_DIR.parents[1] / "feature-pipeline" / "scripts"
_HOOKS = SCRIPT_DIR.parents[2] / "hooks"
for _p in (_FP, _HOOKS):
    if str(_p) not in sys.path and _p.is_dir():
        sys.path.insert(0, str(_p))

PLAN_NAME = "task-plan.json"
# Плоская ветка фикса: одна задача, её сборка — шаг fix-green (references/manifest-steps.json).
_FLAT_BUILD_STEP = {"forgefix": "fix-green"}


def _manifest(root: Path, feature: str) -> "tuple[str | None, dict | None]":
    """(skill, manifest) прогона фичи из ground/statements/*/<feature>/; неоднозначно — (None, None)."""
    hits = sorted((root / "ground" / "statements").glob(f"*/{feature}/manifest.json"))
    if len(hits) != 1:
        return None, None
    try:
        return hits[0].parent.parent.name, json.loads(hits[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return hits[0].parent.parent.name, None


def _acceptance_gate(root: Path, skill: "str | None", feature: str, step_id: str) -> "dict | None":
    """Последний гейт шага сборки, если это прошедший check_acceptance --expect green."""
    if not skill:
        return None
    try:
        import forge_events as FE
        rec = FE.gate(root, skill, feature, step_id)
    except Exception:  # noqa: BLE001 — журнала нет/не читается: подтверждения нет
        return None
    if not rec or rec.get("passed") is not True:
        return None
    cmd = str(rec.get("cmd") or "")
    if "check_acceptance.py" not in cmd or "--expect green" not in cmd:
        return None
    return rec


def _git(root: Path, *args: str) -> "str | None":
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _touched(root: Path, path: Path, since: "str | None") -> "bool | None":
    """Файл менялся с начала прогона: коммит после since либо правка в рабочем дереве.
    None — проверить нечем (нет git / нет started_at)."""
    try:
        rel = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return None
    dirty = _git(root, "status", "--porcelain", "--", rel)
    if dirty is None:
        return None
    if dirty.strip():
        return True
    if not since:
        return None
    log = _git(root, "log", f"--since={since}", "--format=%H", "-1", "--", rel)
    return None if log is None else bool(log.strip())


def check(root, docs_dir, feature: "str | None" = None) -> dict:
    """Сверка task-plan.json из docs_dir с кодом под root. Ничего не пишет."""
    import check_build
    root, docs_dir = Path(root), Path(docs_dir)
    feature = feature or docs_dir.name
    plan_path = docs_dir / PLAN_NAME
    out = {"status": "no-plan", "plan": str(plan_path), "tasks": [], "done": 0, "total": 0,
           "error": None}
    if not plan_path.is_file():
        out["error"] = f"нет {PLAN_NAME} в {docs_dir} — сверять с кодом нечего"
        return out
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        out["error"] = f"{PLAN_NAME} нечитаем: {e}"
        return out
    tasks = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
    if not tasks:
        out["error"] = f"в {PLAN_NAME} нет задач"
        return out

    skill, man = _manifest(root, feature)
    steps = {s.get("id"): s.get("status") for s in (man or {}).get("steps", [])
             if isinstance(s, dict)}
    since = (man or {}).get("started_at")

    import check_acceptance as CA
    cfg = CA._cfg(root)
    found_tests = CA.scan(root, feature)

    rows = []
    for t in tasks:
        tid = str(t.get("id") or "?")
        missing, found = [], []
        for art in t.get("artifacts") or []:
            p = check_build.find_artifact(root, str(art))
            (found if p else missing).append(str(art))
        untouched, absent_reuse = [], []
        for ref in t.get("reuses") or []:
            ref = str(ref)
            if "/" not in ref and not ref.endswith((".java", ".kt", ".xml", ".sql", ".yml",
                                                    ".yaml", ".json")):
                continue          # FQN/голое имя класса: путь не восстановить — не судим
            p = check_build.find_artifact(root, ref)
            if p is None:
                absent_reuse.append(ref)
            elif _touched(root, p, since) is False:
                untouched.append(ref)
        step_id = f"04-build-{tid}"
        if step_id not in steps and skill in _FLAT_BUILD_STEP and len(tasks) == 1:
            step_id = _FLAT_BUILD_STEP[skill]
        step = steps.get(step_id)          # None — манифеста нет или шага в нём нет
        acc = CA.analyze(plan, feature, found_tests, cfg, tid)["rows"]
        need_tests = [a for a in acc if a["status"] in ("covered", "no-test")]
        gate = _acceptance_gate(root, skill, feature, step_id) if need_tests else None
        for a in acc:
            if a["status"] == "manual":
                ok = CA.manual_approval(root, a["id"])
                a["approval"] = ({k: ok.get(k) for k in ("approved_by", "evidence", "reason", "ts")}
                                 if ok else None)
                a["confirmed"] = bool(ok)
            else:
                a["confirmed"] = bool(gate) and a["status"] == "covered"
        if missing or absent_reuse:
            status = "missing"
        elif step is not None and step != "completed":
            status = "not-built"
        elif any(a["status"] == "no-test" for a in acc):
            status = "no-test"
        elif need_tests and not gate:
            status = "unverified"
        elif any(a["status"] == "manual" and not a["confirmed"] for a in acc):
            status = "unconfirmed"
        else:
            status = "done"
        rows.append({"id": tid, "title": str(t.get("title") or ""), "status": status,
                     "found": found, "missing": missing + absent_reuse,
                     "untouched": untouched, "step": step_id,
                     "step_status": step, "acceptance": acc,
                     "gate_ts": (gate or {}).get("ts")})

    done = sum(1 for r in rows if r["status"] == "done")
    out.update(tasks=rows, done=done, total=len(rows), manifest=bool(man),
               status="implemented" if done == len(rows)
               else ("not-implemented" if done == 0 else "partial"))
    return out


def summary(res: dict) -> str:
    if res["status"] == "no-plan":
        return f"с кодом не сверено ({res['error']})"
    return f"код сверен с task-plan: реализовано {res['done']}/{res['total']} задач"


def task_line(r: dict) -> str:
    head = f"{r['id']} «{r['title']}»" if r["title"] else r["id"]
    acc = r.get("acceptance") or []
    tested = [a for a in acc if a["status"] in ("covered", "no-test")]
    manual = [a for a in acc if a["status"] == "manual"]
    if r["status"] == "done":
        tail = "реализована"
        if tested:
            when = f" {r['gate_ts']}" if r.get("gate_ts") else ""
            tail += (f"; критерии приёмки {len(tested)}/{len(tested)} подтверждены тестами "
                     f"(гейт {r['step']}{when})")
        if manual:
            tail += f"; {len(manual)} — вручную"
        elif acc and not tested:
            tail += "; критерии приёмки тестами не проверяются (задача освобождена от тестов)"
        if r["untouched"]:
            tail += f"; не тронуты с начала прогона: {', '.join(r['untouched'])}"
        return f"{head} — {tail}"
    if r["status"] == "missing":
        return f"{head} — НЕ реализована: нет в коде {', '.join(r['missing'])}"
    if r["status"] == "no-test":
        n = sum(1 for a in acc if a["status"] == "no-test")
        return f"{head} — НЕ подтверждена: у {n} из {len(tested)} критериев приёмки нет теста"
    if r["status"] == "unconfirmed":
        n = sum(1 for a in manual if not a["confirmed"])
        return (f"{head} — НЕ подтверждена: {n} критери{'й' if n == 1 else 'ев'} ручной "
                f"проверки человек не подтвердил")
    if r["status"] == "unverified":
        return (f"{head} — НЕ подтверждена: тесты критериев есть, но гейт приёмки "
                f"(check_acceptance --expect green) на шаге {r['step']} не проходил")
    return f"{head} — НЕ подтверждена: шаг {r['step']} не закрыт ({r['step_status']})"


def acceptance_line(a: dict) -> str:
    if a["status"] == "manual":
        ap = a.get("approval")
        if not ap:
            return f"{a['id']} «{a['text']}» — ручная проверка НЕ подтверждена ({a['reason']})"
        how = ap.get("evidence") or ap.get("reason") or ""
        return (f"{a['id']} «{a['text']}» — проверено вручную: {ap.get('approved_by') or '?'}"
                + (f", {how}" if how else "") + (f" ({ap['ts']})" if ap.get("ts") else ""))
    if a["status"] == "exempt":
        return f"{a['id']} «{a['text']}» — без теста (задача освобождена)"
    if a["status"] == "no-test":
        return f"{a['id']} «{a['text']}» — НЕТ теста"
    mark = "✓" if a.get("confirmed") else "не подтверждён прогоном"
    return f"{a['id']} «{a['text']}» — {', '.join(a['tests'])} {mark}"


def journal_lines(res: dict) -> "list[str]":
    """Подпункты к записи журнала мастера: строка на задачу и вложенная — на критерий."""
    out = []
    for r in res.get("tasks") or []:
        out.append(f"  - {task_line(r)}")
        out += [f"    - {acceptance_line(a)}" for a in r.get("acceptance") or []]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сверка task-plan фичи с кодом проекта.")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--docs", required=True, help="каталог доков фичи (где task-plan.json)")
    ap.add_argument("--feature", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        res = check(Path(args.project_root).resolve(), Path(args.docs), args.feature)
    except Exception as e:  # noqa: BLE001
        print(f"✗ {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(summary(res))
        for r in res["tasks"]:
            print(f"   {'✓' if r['status'] == 'done' else '✗'} {task_line(r)}")
    return 3 if res["status"] in ("partial", "not-implemented") else 0


if __name__ == "__main__":
    raise SystemExit(main())
