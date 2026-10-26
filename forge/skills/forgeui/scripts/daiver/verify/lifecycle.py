"""lifecycle.py — жизненный цикл дефектов между итерациями (спека §14.4.6; задача 4.6; INV-C28).

Итерации лежат в папках verify/ кадра (`iter-1`, `iter-2`, …) и оставляют `defects.json`. История — предыдущие папки (имя меньше текущего
по «естественному» порядку, сама текущая не входит: повторный compare в ту же папку не сдвигает цикл).

  new         id не встречался ни в одной прошлой итерации;
  persisting  id был в ПРЕДЫДУЩЕЙ итерации (в том числе частичный фикс: coverage уменьшился — это не регресс, `coverageDelta` < 0);
  regressed   id встречался раньше, пропал в предыдущей итерации (был исправлен) и вернулся;
  fixed       id был в предыдущей, а в текущей нет — не дефект, а запись в `fixed[]`.

declaredNoise: каждый `noise: true` любой прошлой итерации и каждый `false_positive` из файла решений человека (`decisions.apply` ставит
`noise` на дефект до жизненного цикла) помечаются в текущем compare — остаются в дефектах, не блокируют, уходят из `counts.blocking` и из
знаменателя fidelity.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

_NUM = re.compile(r"(\d+)")


def natural(name: str) -> Tuple[Any, ...]:
    return tuple(int(p) if p.isdigit() else p for p in _NUM.split(name))


def load_history(verify_dir: Path, current: str) -> List[Dict[str, Any]]:
    """[{name, defects: [{id, code, element?, coverage?, noise?}]}] прошлых итераций по порядку; битый/пустой defects.json — пустая итерация."""
    out: List[Dict[str, Any]] = []
    if not verify_dir.is_dir():
        return out
    for d in sorted((p for p in verify_dir.iterdir() if p.is_dir() and p.name != current and natural(p.name) < natural(current)),
                    key=lambda p: natural(p.name)):
        f = d / "defects.json"
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            data = {}
        items = data.get("defects") if isinstance(data, dict) else None
        out.append({"name": d.name, "defects": [x for x in (items or []) if isinstance(x, dict) and isinstance(x.get("id"), str)]})
    return out


def declared_noise(history: List[Dict[str, Any]]) -> Set[str]:
    """id, помеченные `noise: true` в любой прошлой итерации."""
    return {x["id"] for it in history for x in it["defects"] if x.get("noise") is True}


def apply(defects: List[Dict[str, Any]], history: List[Dict[str, Any]], noise: Set[str]) -> Dict[str, Any]:
    """Проставляет `lifecycle`, `coverageDelta`, `noise` на месте; возвращает {counts, fixed[]}."""
    prev = {x["id"]: x for x in (history[-1]["defects"] if history else [])}
    earlier: Set[str] = {x["id"] for it in history[:-1] for x in it["defects"]}
    counts = {"new": 0, "persisting": 0, "regressed": 0, "fixed": 0}
    now = set()
    for d in defects:
        now.add(d["id"])
        if d["id"] in prev:
            d["lifecycle"] = "persisting"
            delta = (d.get("coverage") or 1) - (prev[d["id"]].get("coverage") or 1)
            if delta:
                d["coverageDelta"] = delta
        elif d["id"] in earlier:
            d["lifecycle"] = "regressed"
        else:
            d["lifecycle"] = "new"
        counts[d["lifecycle"]] += 1
        d["noise"] = bool(d.get("noise")) or d["id"] in noise            # шум решения человека (decisions.apply) уже на дефекте
    fixed = [{k: x[k] for k in ("id", "code", "element") if k in x} for i, x in sorted(prev.items()) if i not in now]
    counts["fixed"] = len(fixed)
    return {"counts": counts, "fixed": fixed}
