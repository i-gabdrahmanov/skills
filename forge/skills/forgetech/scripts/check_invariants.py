#!/usr/bin/env python3
"""check_invariants.py — главный гейт tech-ветки: снимок «до» → точный diff «после».

Зачем. У техзадачи нет бизнес-сценария и нет бага, значит нет и RED-теста: проба «в конфиге
стоит новый хост» лишь подтверждает, что в файл записали задуманное. Реальная ценность — в
двух свойствах снимка (tasks/018 §2):
  1. «было» сходится с планом — снимок фиксирует текущее значение, и если оно не равно
     заявленному `from`, план ошибочен (не тот ключ/профиль, задача уже сделана);
  2. «стало» совпадает с заявленным в точности — лишняя правка (соседний ключ) или недоделка
     (профиль, перекрытие в application-dev.yml, `${X:old}`) не проходят.
Плюс инварианты: контракты (API/Kafka/таблицы), конфиг и версии вне заявленного не меняются.

Режимы:
  snapshot  — начало tech-apply, ДО первой правки. targets чисты в git; снимает классы плана;
              «было» каждой цели == текущему; пробы обязаны быть КРАСНЫМИ (проба, проходящая до
              правки, ничего не доказывает). Пишет snapshots/snapshot.json + evidence
              `tech-snapshot` в журнал прогона — без него tech-guard не пустит запись в targets.
  expect    — конец tech-apply (гейт шага). Каждая цель == `to`; «было» плана == снимку (план мог
              дописаться `tech_plan.py amend` — снимок не переснимается); в классах целей нет
              изменений вне целей и `match`-областей; пробы ЗЕЛЁНЫЕ.
  compare   — tech-verify (гейт шага). Инварианты плана == снимку за вычетом заявленного.
              Незаявленное изменение — exit 3 ESCALATE: техзадача перестала быть технической,
              решение за пользователем (дописать в план / откатить / сменить путь).
  show      — печать итогового конфига и объявленных версий (read-only, для tech-diag: брать
              «было» отсюда, а не на глаз из файла).
  deps      — печать резолвнутого дерева зависимостей (read-only, для tech-diag).

Usage:
    check_invariants.py snapshot --project <root> --feature <KEY|slug>
    check_invariants.py expect   --project <root> --feature <KEY|slug>
    check_invariants.py compare  --project <root> --feature <KEY|slug>
    check_invariants.py show     --project <root> [--grep <подстрока>]
    check_invariants.py deps     --project <root> [--grep <подстрока>]

Exit: 0 — ок; 1 — гейт не пройден (причины в stderr); 2 — ошибка входа/окружения;
      3 — ESCALATE (compare: незаявленное изменение).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _techlib as T  # noqa: E402


def _fail(msg: str, code: int = 1) -> int:
    print(f"[check_invariants] FAIL: {msg}", file=sys.stderr)
    return code


def _plan(root: Path, feature: str) -> tuple:
    path = T.plan_path(root, feature)
    plan = T.load_plan(path)
    errs = T.validate_plan(plan)
    if errs:
        raise T.TechError("план невалиден (сначала check_tech_sdd.py):\n  - " + "\n  - ".join(errs))
    return path, plan


def _probes(root: Path, plan: dict, deps) -> list:
    out = []
    for i, pr in enumerate(plan.get("probes") or []):
        ok, detail = T.run_probe(root, pr, deps)
        out.append({"id": pr.get("id") or f"P{i + 1}", "kind": pr.get("kind"), "ok": ok,
                    "detail": detail})
    return out


def cmd_snapshot(root: Path, feature: str) -> int:
    plan_file, plan = _plan(root, feature)
    dirty = T.git_dirty(root, list(plan["targets"]))
    if dirty:
        return _fail("targets уже изменены относительно HEAD — снимок «до» снимается только на "
                     f"нетронутом дереве: {', '.join(dirty)}. Свои незакоммиченные правки "
                     "закоммить или убери в stash ДО прогона (это решение пользователя).")
    classes = T.classes_of_plan(plan)
    snap = T.capture(root, classes)
    problems = T.check_from(plan, snap)
    probes = _probes(root, plan, snap.get("deps"))
    for pr in probes:
        if pr["ok"]:
            problems.append(f"проба {pr['id']} ({pr['kind']}) уже выполнена ДО правки — она ничего "
                            f"не доказывает: {pr['detail']}")
    if problems:
        return _fail("снимок не принят:\n  - " + "\n  - ".join(problems)
                     + "\n  Исправь план (tech-diag) — значения «было» бери из "
                       "`check_invariants.py show|deps`, не из файла на глаз.")
    body = {"schema": T.SCHEMA, "created_at": T.iso_now(), "feature": feature,
            "git_head": T.git_head(root), "plan_sha256": T.sha256_file(plan_file),
            "targets": list(plan["targets"]), "classes": snap, "probes": probes}
    path = T.write_state_json(root, feature, T.SNAPSHOT_FILE, body)
    import forge_events as FE
    FE.append_event(root, T.SKILL, feature, "tech-snapshot", step_id="tech-apply",
                    path=T.rel(root, path), sha256=T.sha256_file(path),
                    plan_sha256=body["plan_sha256"], classes=sorted(snap))
    print(json.dumps({"snapshot": T.rel(root, path), "classes": sorted(snap),
                      "targets": plan["targets"]}, ensure_ascii=False))
    return 0


def cmd_expect(root: Path, feature: str) -> int:
    _, plan = _plan(root, feature)
    snap = T.load_snapshot(root, feature)
    before = snap.get("classes") or {}
    classes = set(before) | T.classes_of_plan(plan)
    now = T.capture(root, classes)
    problems = T.check_from(plan, before)
    problems += T.check_to(plan, now)
    target_classes = {c.get("kind") for c in plan.get("expected_changes") or [] if isinstance(c, dict)}
    extra = T.undeclared(plan, before, now, target_classes & set(T.CHANGE_KINDS))
    for cls, rows in extra.items():
        for k, b, a in rows[:20]:
            problems.append(f"{cls}: незаявленное изменение {k}: {b!r} → {a!r}")
    probes = _probes(root, plan, now.get("deps"))
    for pr in probes:
        if not pr["ok"]:
            problems.append(f"проба {pr['id']} ({pr['kind']}) не выполнена: {pr['detail']}")
    result = {"step": "tech-apply", "at": T.iso_now(), "passed": not problems,
              "problems": problems, "probes": probes,
              "diff": {cls: T.diff_class(before.get(cls), now.get(cls))
                       for cls in sorted(target_classes & set(T.CHANGE_KINDS))}}
    T.write_state_json(root, feature, "expect.json", result)
    if problems:
        return _fail("цель не достигнута в точности:\n  - " + "\n  - ".join(problems)
                     + "\n  Лишнюю правку — убери; недостающую — сделай в пределах targets. "
                       "Нужен файл вне targets — верни status:\"needs_scope\" (tech_plan.py amend).")
    print(json.dumps({"passed": True, "targets": len([c for c in plan["expected_changes"]
                                                      if T.is_target_change(c)]),
                      "probes": len(probes)}, ensure_ascii=False))
    return 0


def cmd_compare(root: Path, feature: str) -> int:
    _, plan = _plan(root, feature)
    snap = T.load_snapshot(root, feature)
    before = snap.get("classes") or {}
    inv_classes = {T.INVARIANT_CLASSES[n] for n in plan.get("invariants") or []
                   if T.INVARIANT_CLASSES.get(n)}
    missing = inv_classes - set(before)
    if missing:
        return _fail(f"в снимке нет классов {sorted(missing)} — снимок снят под другой план; "
                     f"переснять его можно только на нетронутом дереве", 2)
    now = T.capture(root, inv_classes)
    extra = T.undeclared(plan, before, now, inv_classes)
    result = {"step": "tech-verify", "at": T.iso_now(), "passed": not extra,
              "checked": sorted(inv_classes),
              "undeclared": {c: [list(r) for r in rows] for c, rows in extra.items()},
              "diff": {c: [list(r) for r in T.diff_class(before.get(c), now.get(c))]
                       for c in sorted(inv_classes)}}
    T.write_state_json(root, feature, "compare.json", result)
    if extra:
        lines = [f"{c}: {k}: {b!r} → {a!r}" for c, rows in extra.items() for k, b, a in rows[:30]]
        print("=" * 60 + "\n⛔ ESCALATE: изменилось то, что план не заявлял:\n  - "
              + "\n  - ".join(lines)
              + "\nТехзадача перестала быть технической — СТОП, спроси пользователя:\n"
                "  «ожидаемо — дописать в план» (tech_plan.py amend + новое согласие) /\n"
                "  «откатить и сделать иначе» (переоткрыть tech-apply) /\n"
                "  «это фича или баг» (новый прогон full/fix).\n" + "=" * 60, file=sys.stderr)
        return 3
    print(json.dumps({"passed": True, "checked": sorted(inv_classes)}, ensure_ascii=False))
    return 0


def _print_filtered(data: dict, grep) -> None:
    for k in sorted(data):
        if grep and grep not in k:
            continue
        print(f"{k} = {data[k]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["snapshot", "expect", "compare", "show", "deps"])
    ap.add_argument("--project", default=None, help="корень репо кода")
    ap.add_argument("--feature", help="ключ/слаг техзадачи (namespace forgetech)")
    ap.add_argument("--grep", help="show/deps: фильтр по подстроке ключа")
    args = ap.parse_args()
    root = T.project_root(args.project)
    try:
        if args.mode == "show":
            print("# config: <module>|<profile>|<key> = итоговое значение")
            _print_filtered(T.effective_config(root), args.grep)
            print("# build: <файл>:<что> = объявленная версия")
            _print_filtered(T.build_versions(root), args.grep)
            return 0
        if args.mode == "deps":
            _print_filtered(T.resolve_deps(root), args.grep)
            return 0
        if not args.feature:
            return _fail("нужен --feature <KEY|slug>", 2)
        return {"snapshot": cmd_snapshot, "expect": cmd_expect,
                "compare": cmd_compare}[args.mode](root, args.feature)
    except T.TechError as e:
        return _fail(str(e), 2 if args.mode in ("show", "deps") else 1)


if __name__ == "__main__":
    sys.exit(main())
