"""group.py — свёртка находок по общей причине в дефекты (спека §14.4.6; задача 4.6).

12 строк таблицы с одним неверным gap — 12 находок, ОДИН дефект с `coverage: 12`. Сливаются только находки слоёв geometry / typography /
color / shape, у которых совпали код, свойство и ПОДПИСЬ вида узла макета (`ids.signature`: вид + имя компонента/слоя): это «одно свойство у
одного вида элемента». Находки, делящие только предка (контейнер и дети, соседние разные компоненты), не сливаются — независимые
наблюдения; structure и component (MISSING, EXTRA, ORDER, TEXT, SLOT_*) и DS-находки — всегда по одной на дефект. Разные пары значений одного
адреса остаются обе: `values: [{expected, actual, count}]`.

Подавленные находки в дефекты не входят (они хранятся отдельно в findings.json); дефект несёт `suppressedCount` — сколько находок заглушили
его находки.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import ids as I


def fold(findings: List[Dict[str, Any]], d_index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Активные находки (без suppressedBy) → группы {id, code, layer, prop, signature, findings[], values[]}; порядок — первое появление."""
    groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    suppressed_by: Dict[str, int] = {}
    for f in findings:
        if f.get("suppressedBy"):
            suppressed_by[f["suppressedBy"]] = suppressed_by.get(f["suppressedBy"], 0) + 1
    for f in findings:
        if f.get("suppressedBy"):
            continue
        sig = I.signature(d_index.get(f.get("design") or ""))
        foldable = f.get("layer") in I.FOLD_LAYERS and bool(sig) and f["code"] != "TEXT"
        key: Tuple[Any, ...] = (f["code"], f.get("prop"), sig) if foldable else ("one", f["id"])
        g = groups.get(key)
        if g is None:
            g = {"id": I.fold_id(f["code"], sig, f.get("prop")) if foldable else f["id"], "code": f["code"], "layer": f["layer"],
                 "prop": f.get("prop"), "signature": sig if foldable else None, "findings": [], "values": []}
            groups[key] = g
            out.append(g)
        g["findings"].append(f)
        if "expected" in f or "actual" in f:
            pair = (f.get("expected"), f.get("actual"))
            for v in g["values"]:
                if (v["expected"], v["actual"]) == pair:
                    v["count"] += 1
                    break
            else:
                g["values"].append({"expected": pair[0], "actual": pair[1], "count": 1})
    for g in out:
        g["coverage"] = len(g["findings"])
        g["suppressedCount"] = sum(suppressed_by.get(f["id"], 0) for f in g["findings"])
    return out
