#!/usr/bin/env python3
from __future__ import annotations
"""
override_judge.py — ручное подтверждение пропуска гейта судьи.

Когда судья заблокировал шаг по причине, которую нельзя устранить автоматически
(нет тестовой БД, внешний сервис недоступен, acceptance намеренно ослаблен и т.д.),
пользователь создаёт override-файл с объяснением. update.py учитывает его и пропускает
блокировку этого судьи — но фиксирует факт отклонения в manifest.

Использование:
    python3 override_judge.py \\
        --judge <judge-name>         # напр. red-judge, coverage-judge
        --feature <slug>             # фича (slug / Jira-key)
        --step-id <id>               # напр. 04-test-T1
        --reason "<объяснение>"      # ОБЯЗАТЕЛЬНО — почему пропуск допустим
        [--project <root>]           # корень проекта (по умолчанию — cwd/git)
        [--skill feature-pipeline]   # скилл (по умолчанию feature-pipeline)
        [--list]                     # показать существующие overrides
        [--remove]                   # удалить override
        [--json]                     # JSON-вывод

Exit:
    0 — override создан / показан / удалён
    1 — ошибка (не указана причина, файл не найден и т.д.)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import repo_root


SCHEMA = "pipeline/judge-override@1"


def overrides_dir(project: Path, skill: str, feature: str) -> Path:
    return project / "ground" / "statements" / skill / feature / "overrides"


def override_path(project: Path, skill: str, feature: str, judge: str) -> Path:
    return overrides_dir(project, skill, feature) / f"{judge}.json"


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_create(args, project: Path) -> int:
    if not args.reason:
        print("ERROR: --reason обязателен при создании override", file=sys.stderr)
        return 1

    odir = overrides_dir(project, args.skill, args.feature)
    odir.mkdir(parents=True, exist_ok=True)

    record = {
        "$schema": SCHEMA,
        "judge": args.judge,
        "feature_slug": args.feature,
        "step_id": args.step_id or "unknown",
        "override_at": iso_now(),
        "reason": args.reason,
        "approved_by": "user",
    }

    path = override_path(project, args.skill, args.feature, args.judge)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps({"status": "created", "path": str(path), "record": record},
                         ensure_ascii=False))
    else:
        print(f"✅ Override создан: {path.relative_to(project)}")
        print(f"   Судья:  {args.judge}")
        print(f"   Шаг:    {record['step_id']}")
        print(f"   Причина: {args.reason}")
        print()
        print("Теперь можно закрыть шаг:")
        print(f"  python3 update.py --skill {args.skill} --feature {args.feature} "
              f"--step-id {record['step_id']} --status completed")
    return 0


def cmd_list(args, project: Path) -> int:
    odir = overrides_dir(project, args.skill, args.feature)
    files = sorted(odir.glob("*.json")) if odir.exists() else []

    if args.json:
        records = []
        for f in files:
            try:
                records.append(json.loads(f.read_text()))
            except Exception:
                records.append({"path": str(f), "error": "invalid JSON"})
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        if not files:
            print(f"Нет активных overrides для {args.feature}")
        else:
            print(f"Активные overrides ({args.feature}):")
            for f in files:
                try:
                    r = json.loads(f.read_text())
                    print(f"  • {r['judge']:25s} шаг={r.get('step_id','?'):20s} "
                          f"дата={r.get('override_at','?')[:10]}")
                    print(f"    причина: {r.get('reason','')[:100]}")
                except Exception:
                    print(f"  • {f.name} (повреждён)")
    return 0


def cmd_remove(args, project: Path) -> int:
    path = override_path(project, args.skill, args.feature, args.judge)
    if not path.exists():
        print(f"Override не найден: {path.relative_to(project)}", file=sys.stderr)
        return 1
    path.unlink()
    if args.json:
        print(json.dumps({"status": "removed", "judge": args.judge}))
    else:
        print(f"🗑  Override удалён: {args.judge}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge", help="Имя судьи (напр. red-judge, coverage-judge)")
    ap.add_argument("--feature", required=True, help="Slug фичи / Jira-key")
    ap.add_argument("--step-id", help="ID шага (для справки, не влияет на механику)")
    ap.add_argument("--reason", help="Почему пропуск допустим (обязательно при создании)")
    ap.add_argument("--project", default=None)
    ap.add_argument("--skill", default="feature-pipeline")
    ap.add_argument("--list", action="store_true", help="Показать существующие overrides")
    ap.add_argument("--remove", action="store_true", help="Удалить override")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    project = Path(args.project or repo_root()).resolve()

    if args.list:
        return cmd_list(args, project)

    if not args.judge:
        ap.error("--judge обязателен (кроме --list)")

    if args.remove:
        return cmd_remove(args, project)

    return cmd_create(args, project)


if __name__ == "__main__":
    sys.exit(main())
