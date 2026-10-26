"""suppress.py — каскад подавления находок (спека §14.4.6; задача 4.6; INV-C25, QG-U17, F4).

Слои: structure → component → token → geometry → typography → color → shape. Находка раннего слоя глушит находку позднего ТОЛЬКО
когда содержит её И объясняет. Список объяснений короток (три правила):
  • целый неверный элемент — MISSING / EXTRA — глушит находки поздних слоёв во всём своём поддереве (макета и страницы);
  • другая копия (TEXT) на узле глушит геометрию ЭТОГО узла (SIZE, POSITION): бокс текста меняется вместе с текстом;
  • токен (DS_TOKEN_MISSED / DS_OFF_SCALE) на узле глушит COLOR того же свойства (fill → background, text → color, stroke → border).
Всё остальное — независимые наблюдения общего предка: «высота корня» НЕ глушит 16 цветовых находок детей (история: контейнерность без
объяснения хуже отсутствия каскада). Не глушится: находки одного слоя друг другом; уже подавленная не становится подавителем; ORDER ничего не
объясняет о покраске; DS_DEPRECATED не объясняет ничего; DS-находки (идентичность, пропы) в каскаде независимы, кроме токенов.
Ближайшая причина побеждает (глубже узел макета у подавителя); аннотации пересчитываются с нуля каждую итерацию — функция чистая.

НИЧЕГО не дропается: подавленная находка хранится со `suppressedBy` (id находки-подавителя) и `suppressedReason`; счётчики по слоям уходят
в отчёт — «цвет не смотрели» не то же, что «цвета нет».
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import ids as I

LAYER_RANK = {"structure": 0, "component": 1, "token": 2, "geometry": 3, "typography": 4, "color": 5, "shape": 6, "pixel": 7}
TOKEN_CODES = ("DS_TOKEN_MISSED", "DS_OFF_SCALE")
SLOT_PROP = {"fill": "background", "text": "color", "stroke": "border"}
TEXT_GEOMETRY = ("SIZE", "POSITION")


def rank(f: Dict[str, Any]) -> Optional[int]:
    """Место находки в каскаде; None — независима (DS-идентичность и проп-контракты)."""
    if f["code"] in TOKEN_CODES:
        return LAYER_RANK["token"]
    return LAYER_RANK.get(f.get("layer"))


def _subtree(index: Dict[str, Dict[str, Any]], root_id: Optional[str]) -> set:
    out: set = set()
    if root_id not in index:
        return out
    stack = [index[root_id]]
    while stack:
        n = stack.pop()
        out.add(n["id"])
        stack.extend(k for k in (n.get("children") or []) if isinstance(k, dict))
    return out


def _depth(parent: Dict[str, Optional[str]], nid: Optional[str]) -> int:
    d = 0
    while nid is not None and parent.get(nid) is not None:
        nid = parent[nid]
        d += 1
    return d


def apply(findings: List[Dict[str, Any]], d_index: Dict[str, Dict[str, Any]], d_parent: Dict[str, Optional[str]],
          i_index: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Размечает находки на месте (`id`, `suppressedBy`, `suppressedReason`); возвращает {layers: {слой: {total, suppressed}}, suppressed}."""
    i_index = i_index or {}
    for f in findings:
        f["id"] = I.finding_id(f)
        f.pop("suppressedBy", None)
        f.pop("suppressedReason", None)
    ranked = sorted((k for k in range(len(findings)) if rank(findings[k]) is not None), key=lambda k: (rank(findings[k]), k))
    for k in ranked:                                                     # слой за слоем: подавленная раньше подавителем не становится
        f = findings[k]
        if f.get("_cause"):
            continue
        r = rank(f)
        targets: List[Dict[str, Any]] = []
        kind = ""
        if f["code"] in ("MISSING", "EXTRA"):
            kind = "wholesale"
            d_sub, i_sub = _subtree(d_index, f.get("design")), _subtree(i_index, f.get("impl"))
            targets = [g for g in findings if g is not f and rank(g) is not None and rank(g) > r
                       and ((f.get("design") and g.get("design") in d_sub) or (f.get("impl") and g.get("impl") in i_sub))]
        elif f["code"] == "TEXT":
            kind = "text"
            targets = [g for g in findings if g.get("design") == f.get("design") and g["code"] in TEXT_GEOMETRY]
        elif f["code"] in TOKEN_CODES and f.get("slot") in SLOT_PROP:
            kind = "token"
            targets = [g for g in findings if g["code"] == "COLOR" and g.get("design") == f.get("design")
                       and g.get("prop") == SLOT_PROP[f["slot"]]]
        depth = _depth(d_parent, f.get("design"))
        for g in targets:
            prev = g.get("_cause")
            if prev is None or depth > prev[0]:                          # ближайшая причина побеждает
                g["_cause"] = (depth, f, kind)
    reasons = {"wholesale": "целиком неверный элемент: поддерево объяснено им",
               "text": "другая копия: бокс этого текста меняется вместе с текстом",
               "token": "токен объясняет значение того же свойства того же узла"}
    for g in findings:
        cause = g.pop("_cause", None)
        if cause:
            g["suppressedBy"] = cause[1]["id"]
            g["suppressedReason"] = reasons[cause[2]]
    layers: Dict[str, Dict[str, int]] = {}
    total = 0
    for g in findings:
        row = layers.setdefault(g.get("layer", "?"), {"total": 0, "suppressed": 0})
        row["total"] += 1
        if g.get("suppressedBy"):
            row["suppressed"] += 1
            total += 1
    return {"layers": dict(sorted(layers.items())), "suppressed": total}
