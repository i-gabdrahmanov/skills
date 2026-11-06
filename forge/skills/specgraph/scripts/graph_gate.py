#!/usr/bin/env python3
"""graph_gate.py — гейт шагов ветки specgraph: `graph_gate.py map|link|check --project <repo>`.

Шаг закрывается только этим гейтом (record_gate с командой из risk-policy.gate_cmd_expect), а не
словами модели «готово». Пока проверки шагов не реализованы (spec-graph 0.1), каждый шаг отвечает
exit 3 «не реализовано» — честная остановка: шаг не закрывается, конвейер не делает вид, что
граф построен. Проверки появляются задачами 1.7 (map), 2.6 (link), 3.5 (check).

Коды: 0 — гейт пройден, 1 — проверки не прошли, 2 — неверный вызов, 3 — гейт шага не реализован.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

STEPS = {"map": "graph-map", "link": "graph-link", "check": "graph-check"}
_PLANNED = {"map": "1.7", "link": "2.6", "check": "3.5"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Гейт шага ветки specgraph.")
    ap.add_argument("step", choices=sorted(STEPS))
    ap.add_argument("--project", default=".", help="корень репо спеки")
    args = ap.parse_args(argv)
    if not Path(args.project).is_dir():
        print(f"✗ нет каталога проекта: {args.project}", file=sys.stderr)
        return 2
    print(f"✗ {STEPS[args.step]}: гейт шага не реализован (spec-graph, задача {_PLANNED[args.step]}) — "
          f"шаг не закрывается. Скажи пользователю, что этот шаг конвейера ещё не готов.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
