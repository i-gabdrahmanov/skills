#!/usr/bin/env python3
"""graph_gate.py — гейт шагов ветки specgraph: `graph_gate.py map|link|check --project <repo>`.

Шаг закрывается только этим гейтом (record_gate с командой из risk-policy.gate_cmd_expect), а не
словами модели «готово».

map (задача 1.7): очередь draft-file закрыта; каталог утверждён — согласие graph-catalog-<KEY> привязано
к снимку catalog.approved.json; очередь assign-section построена из него и закрыта; нарезка сделана по
снимку (заметка на каждый узел); полнота и дословность — 0 нарушений (E-coverage, E-verbatim);
frontmatter всех заметок читается. Брошенные единицы очередей перечисляются в ответе.
link, check — пока «не реализовано» (exit 3): шаг не закрывается. Проверки — задачи 2.6, 3.5.

Коды: 0 — гейт пройден, 1 — проверки не прошли, 2 — неверный вызов, 3 — гейт шага не реализован.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

STEPS = {"map": "graph-map", "link": "graph-link", "check": "graph-check"}
_PLANNED = {"link": "2.6", "check": "3.5"}


def check_map(root: Path) -> Tuple[List[str], List[str]]:
    """(нарушения, заметки): нарушений нет — шаг можно закрывать."""
    from sgraph import catalog as CT
    from sgraph import config as C
    from sgraph import fm as F
    from sgraph import ontology as O
    from sgraph import queue as Q
    from sgraph import split as SP
    bad, notes = [], []
    cfg, errors = C.load(root)
    if cfg is None:
        return [f"конфиг: {'; '.join(errors) or 'нет .spec-graph/config.json — graph_init'}"], notes
    for name in ("draft-file", "assign-section"):
        if not (root / Q.QUEUE_REL / f"{name}.json").is_file():
            bad.append(f"очередь {name} не построена")
            continue
        closed, abandoned = Q.closed(root, name)
        if not closed:
            st = Q.status(root, name)
            bad.append(f"очередь {name} не закрыта: pending {st['pending']}, в аренде {st['leased']}")
        if abandoned:
            notes.append(f"очередь {name}: брошено единиц {len(abandoned)} — в отчёт человеку")
    ok, why = CT.approved(root)
    if not ok:
        bad.append(why)
        return bad, notes
    ont = O.load(root)
    vault = root / cfg["vault"]
    approved = CT.load_approved(root)
    missing = [n["name"] for n in approved["nodes"] if n.get("in_scope", True)
               and not (vault / ont.path_for(n["kind"], n["name"], n.get("service"), n.get("subkind"))).is_file()]
    if missing:
        bad.append(f"нарезка не сделана по снимку: нет заметок {len(missing)} (например «{missing[0]}») — graph_split")
    for code, msg in SP.verify(root, cfg)[:10]:
        bad.append(f"[{code}] {msg}")
    for p in sorted(vault.rglob("*.md")):
        if "_graph" in p.relative_to(vault).parts:
            continue
        try:
            F.read(SP.read_note(p))
        except F.FMError as e:
            bad.append(f"{p.relative_to(vault)}: frontmatter {e}")
    delta = json.loads((root / ".spec-graph/catalog.delta.json").read_text(encoding="utf-8")) \
        if (root / ".spec-graph/catalog.delta.json").is_file() else []
    if delta:
        notes.append(f"узлов, добавленных раскладкой: {len(delta)} — решаются в отчёте связей (graph-link)")
    return bad, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Гейт шага ветки specgraph.")
    ap.add_argument("step", choices=sorted(STEPS))
    ap.add_argument("--project", default=".", help="корень репо спеки")
    args = ap.parse_args(argv)
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"✗ нет каталога проекта: {args.project}", file=sys.stderr)
        return 2
    if args.step in _PLANNED:
        print(f"✗ {STEPS[args.step]}: гейт шага не реализован (spec-graph, задача {_PLANNED[args.step]}) — "
              f"шаг не закрывается. Скажи пользователю, что этот шаг конвейера ещё не готов.")
        return 3
    bad, notes = check_map(root)
    for n in notes:
        print(f"• {n}")
    if bad:
        print(f"✗ {STEPS[args.step]}: шаг не закрывается:")
        for b in bad[:15]:
            print(f"  - {b}")
        return 1
    print(f"✓ {STEPS[args.step]}: каталог утверждён, нарезка полная и дословная")
    return 0


if __name__ == "__main__":
    sys.exit(main())
