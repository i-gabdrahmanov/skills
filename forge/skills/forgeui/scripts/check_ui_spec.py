#!/usr/bin/env python3
"""check_ui_spec.py — гейт шага ui-spec (forge-ui): форма фронт-спеки стори `ui-spec.md`.

Проверяет то, без чего следующие шаги вынуждены были бы догадываться:
  • шапка: **Jira:**, **Статус:** draft|approved, **Бэк-часть:** «нет» | «есть: <KEY>»
    (отсутствие поля ≠ «нет» — связь с бэк-спекой стори записывается явно);
  • «Кадры» — таблица имя | роль | макет | маршрут | как открыть; у каждого кадра ссылка на
    макет (URL Pixso/Figma или файл экспорта) — кадры не выдумываются;
  • Данные / Состояния / События / Валидации / Сценарии — заполнены или явно «нет»
    (презентационный экран без логики — нормальный исход);
  • «Сценарии» разбираются парсером движка (Given/When/Then или Дано/Когда/Тогда);
  • «Данные» называют `types:`/`client:` путями в бэктиках или явно «моки».
Путь из «Данных», которого пока нет в репо, — предупреждение, не отказ: если к сборке не
появится, упадёт компиляция — там этой ошибке и место.

exit 0 — спека годна; 1 — нет (причины в выводе); 2 — нет файла / неверные аргументы.

Usage:
    python3 check_ui_spec.py <ui-spec.md | каталог стори> [--project <корень репо>] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import uispec  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Гейт ui-spec: форма фронт-спеки стори (ui-spec.md).")
    ap.add_argument("path", help="ui-spec.md или каталог стори, где он лежит")
    ap.add_argument("--project", default=None, help="корень репо (для проверки путей из «Данных»)")
    ap.add_argument("--json", action="store_true", help="вывод JSON")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if p.is_dir():
        p = p / uispec.SPEC_NAME
    if not p.is_file():
        print(f"✗ нет файла {p} — спека стори пишется под именем {uispec.SPEC_NAME}", file=sys.stderr)
        return 2
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"✗ не читается {p}: {e}", file=sys.stderr)
        return 2
    project = Path(args.project).resolve() if args.project else None
    res = uispec.check(text, p.name, project)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"{'✅' if res['ok'] else '❌'} {p}")
        for e in res["errors"]:
            print(f"  ✗ {e}")
        for w in res["warnings"]:
            print(f"  ⚠ {w}")
        if res["ok"]:
            print(f"  кадров: {len(res['frames'])}, сценариев: {len(res['scenarios'])}, "
                  f"бэк-часть: {res['fields'].get('back', '?')}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
