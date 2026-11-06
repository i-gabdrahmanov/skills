#!/usr/bin/env python3
"""graph_diag.py — пакет диагностики из shell: `graph_diag.py --project <repo> [--level excerpt --block <id> …]`.

То же, что инструмент graph_diag (задача 4.3): пакет в `<vault>/_graph/diag/`, без текста спеки.

Коды: 0 — пакет записан, 2 — неверный вызов или нет конфига, 3 — самопроверка утечки нашла текст окружения:
пакет НЕ записан, в выводе — номера строк.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv=None) -> int:
    from sgraph import config as C
    from sgraph import diag as D
    ap = argparse.ArgumentParser(description="Пакет диагностики spec-graph.")
    ap.add_argument("--project", default=".")
    ap.add_argument("--level", default="form", choices=["form", "excerpt"])
    ap.add_argument("--block", action="append", default=[], help="id блока для level excerpt (решение пользователя)")
    args = ap.parse_args(argv)
    root = Path(args.project).resolve()
    cfg, errors = C.load(root)
    if cfg is None:
        print(f"✗ конфиг: {'; '.join(errors) or 'нет .spec-graph/config.json'}", file=sys.stderr)
        return 2
    try:
        res = D.build(root, cfg, level=args.level, excerpt_blocks=args.block)
    except D.DiagError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 3 if e.code == "E-diag-leak" else 2
    print(f"✓ {res['file']} — строк {res['lines']}, сокращено: {res['cut']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
