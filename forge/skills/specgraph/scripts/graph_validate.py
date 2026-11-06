#!/usr/bin/env python3
"""graph_validate.py — валидатор графа из shell: `graph_validate.py --project <repo> [--scope <префикс>]`.

То же, что инструмент graph_validate (задача 3.1): счётчики по кодам и первые нарушения в stdout, полностью —
`<vault>/_graph/report.md` и `.spec-graph/validate.json`.

Коды: 0 — ошибок E-* нет (предупреждения W-* допустимы), 1 — есть ошибки, 2 — неверный вызов или нет конфига.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv=None) -> int:
    from sgraph import config as C
    from sgraph import validate as V
    ap = argparse.ArgumentParser(description="Проверка графа spec-graph.")
    ap.add_argument("--project", default=".")
    ap.add_argument("--scope", default=None, help="префикс пути внутри vault")
    args = ap.parse_args(argv)
    root = Path(args.project).resolve()
    cfg, errors = C.load(root)
    if cfg is None:
        print(f"✗ конфиг: {'; '.join(errors) or 'нет .spec-graph/config.json — graph_init'}", file=sys.stderr)
        return 2
    res = V.validate(root, cfg, args.scope)
    print(f"{'✓' if not res['errors'] else '✗'} заметок {res['notes']}: ошибок {res['errors']}, "
          f"предупреждений {res['warnings']}" + "".join(f"\n  {k}: {v}" for k, v in res["counts"].items()))
    for line in res["top"]:
        print(f"  - {line}")
    print(f"  отчёт: {res['report']}")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
