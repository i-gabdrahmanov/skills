#!/usr/bin/env python3
"""tech_report.py — итоговая сводка техзадачи (шаг tech-record): report.md.

Сводку пишет СКРИПТ по снимку и результатам гейтов, а не модель пересказом (tasks/018 §3.3):
  • цели: было → стало (по каждой);
  • фактический diff внутри `match`-областей — ПОЛНЫМ списком: `org.springframework*:*`
    разрешает сотни артефактов, и что именно сдвинулось, человек должен видеть, а не верить маске;
  • донастройки скоупа (amend): какие файлы добавлены и почему;
  • инварианты: что сверено и с каким результатом;
  • «вне репозитория» — чек-лист того, что остаётся пользователю.

Режимы:
  write — собрать report.md в каталоге техзадачи;
  check — гейт шага: гейты tech-apply (expect) и tech-verify (compare) пройдены, report.md есть
          и содержит все цели и все пути фактического diff.

Usage:
    tech_report.py write --project <root> --feature <KEY|slug>
    tech_report.py check --project <root> --feature <KEY|slug>
Exit: 0 — ок; 1 — гейт не пройден; 2 — ошибка входа.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _techlib as T  # noqa: E402


def _state(root: Path, feature: str, name: str) -> dict:
    p = T.snapshot_dir(root, feature) / name
    if not p.exists():
        raise T.TechError(f"нет {name} — гейт {'tech-apply' if name == 'expect.json' else 'tech-verify'} "
                          f"не запускался")
    return json.loads(p.read_text(encoding="utf-8"))


def _fmt(v) -> str:
    return "—" if v is None else f"`{v}`"


def build_report(root: Path, feature: str) -> str:
    plan = T.load_plan(T.plan_path(root, feature))
    snap = T.load_snapshot(root, feature)
    expect = _state(root, feature, "expect.json")
    compare = _state(root, feature, "compare.json")
    before = snap.get("classes") or {}
    lines = [f"# Отчёт техзадачи {feature}", "",
             f"**Архетип:** {plan.get('archetype')} · **Снимок «до»:** {snap.get('created_at')} "
             f"(HEAD {str(snap.get('git_head') or '')[:12]})", "",
             "## Цели: было → стало", "",
             "| Цель | Ключ | Было | Стало |", "|---|---|---|---|"]
    diff_rows = {cls: rows for cls, rows in (expect.get("diff") or {}).items()}
    for ch in plan.get("expected_changes") or []:
        if not T.is_target_change(ch):
            continue
        for k in T.target_keys(ch, before) or ["?"]:
            lines.append(f"| {T.change_id(ch)} | {k} | {_fmt(ch.get('from'))} | {_fmt(ch.get('to'))} |")
    lines += ["", "## Фактический diff (все изменения в классах целей)", ""]
    any_diff = False
    for cls, rows in sorted(diff_rows.items()):
        if not rows:
            continue
        any_diff = True
        lines += [f"### {cls}", "", "| Ключ | Было | Стало | Заявлено |", "|---|---|---|---|"]
        for k, b, a in rows:
            how = T.declared(plan, cls, k, before) or "—"
            lines.append(f"| {k} | {_fmt(b)} | {_fmt(a)} | {how} |")
        lines.append("")
    if not any_diff:
        lines += ["Изменений в классах целей нет.", ""]
    lines += ["## Донастройка скоупа (amend)", ""]
    am = plan.get("amendments") or []
    if am:
        for a in am:
            lines.append(f"- {a.get('at')}: +{', '.join(a.get('add_targets') or []) or 'цели'} — "
                         f"{a.get('reason')}")
    else:
        lines.append("Скоуп не менялся после утверждения.")
    lines += ["", "## Инварианты", "",
              f"Сверено: {', '.join(compare.get('checked') or []) or '—'} — "
              f"{'без незаявленных изменений' if compare.get('passed') else 'ЕСТЬ незаявленные изменения'}.",
              "Тесты затронутых модулей — гейт module_tests.py guard шага tech-verify.", ""]
    probes = expect.get("probes") or []
    if probes:
        lines += ["## Пробы", "", "| Проба | Вид | Итог |", "|---|---|---|"]
        for pr in probes:
            lines.append(f"| {pr.get('id')} | {pr.get('kind')} | {'✅' if pr.get('ok') else '❌'} "
                         f"{pr.get('detail')} |")
        lines.append("")
    lines += ["## Вне репозитория — сделать пользователю", "",
              f"- [ ] {plan.get('outside_repo') or 'нет'}", "",
              f"**Откат:** {plan.get('rollback')}", "",
              "_Сводка собрана tech_report.py по снимку и результатам гейтов; коммит, PR и "
              "выкатка — на пользователе._", ""]
    return "\n".join(lines)


def cmd_write(root: Path, feature: str) -> int:
    text = build_report(root, feature)
    out = T.task_dir(root, feature) / T.REPORT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(json.dumps({"report": T.rel(root, out)}, ensure_ascii=False))
    return 0


def cmd_check(root: Path, feature: str) -> int:
    problems = []
    expect = _state(root, feature, "expect.json")
    compare = _state(root, feature, "compare.json")
    if not expect.get("passed"):
        problems.append("гейт tech-apply (check_invariants expect) не пройден")
    if not compare.get("passed"):
        problems.append("гейт tech-verify (check_invariants compare) не пройден")
    out = T.task_dir(root, feature) / T.REPORT_FILE
    if not out.exists():
        problems.append(f"нет {T.rel(root, out)} — собери: tech_report.py write")
    else:
        text = out.read_text(encoding="utf-8")
        plan = T.load_plan(T.plan_path(root, feature))
        for ch in plan.get("expected_changes") or []:
            if T.is_target_change(ch) and T.change_id(ch) not in text:
                problems.append(f"в отчёте нет цели {T.change_id(ch)}")
        for cls, rows in (expect.get("diff") or {}).items():
            for k, _b, _a in rows:
                if str(k) not in text:
                    problems.append(f"в отчёте нет изменения {cls}: {k}")
    if problems:
        print("[tech_report] FAIL:\n  - " + "\n  - ".join(problems[:40]), file=sys.stderr)
        return 1
    print(json.dumps({"passed": True}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["write", "check"])
    ap.add_argument("--project", default=None)
    ap.add_argument("--feature", required=True)
    args = ap.parse_args()
    root = T.project_root(args.project)
    try:
        return (cmd_write if args.mode == "write" else cmd_check)(root, args.feature)
    except T.TechError as e:
        print(f"[tech_report] FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
