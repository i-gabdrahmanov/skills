"""paint.py — вопросы к краскам узла Pixso DSL, нужные структуре: есть ли поверхность, непрозрачна ли.

Полное преобразование красок в стиль канона (rgba, градиенты, радиусы, тени, токены) — задача 1.5;
здесь только предикаты, на которых стоят материализация (`component.paints`) и окклюзия.

Формат краски образца: `{"type": "SOLID", "color": {"r","g","b": 0..255, "a": 0..1}, "visible"?, "opacity"?,
"blendMode"}`. Поле `opacity` у краски дублирует `color.a` (0.12 и 0.12 в образце), поэтому эффективная
альфа — меньшая из двух, а не произведение.
"""
from __future__ import annotations

from typing import Any, Iterable

OPAQUE = 0.999


def alpha(p: Any) -> float:
    if not isinstance(p, dict):
        return 0.0
    vals = []
    col = p.get("color")
    if isinstance(col, dict) and isinstance(col.get("a"), (int, float)):
        vals.append(float(col["a"]))
    if isinstance(p.get("opacity"), (int, float)) and not isinstance(p.get("opacity"), bool):
        vals.append(float(p["opacity"]))
    return min(vals) if vals else 1.0


def paint_is_visible(p: Any) -> bool:
    return isinstance(p, dict) and p.get("visible") is not False and alpha(p) > 0


def _paints(node: dict, key: str) -> Iterable[dict]:
    v = node.get(key)
    return [p for p in v if isinstance(p, dict)] if isinstance(v, list) else []


def has_fill(node: dict) -> bool:
    return any(paint_is_visible(p) for p in _paints(node, "fillPaints"))


def has_stroke(node: dict) -> bool:
    weights = [node.get("strokeWeight")] + [node.get("border" + s + "Weight") for s in ("Top", "Right", "Bottom", "Left")]
    thick = any(isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 for w in weights)
    return thick and any(paint_is_visible(p) for p in _paints(node, "strokePaints"))


def is_opaque_fill(node: dict) -> bool:
    """Закрывает всё под собой: видимая сплошная заливка с альфой 1, обычный режим наложения, opacity узла 1."""
    if node.get("visible") is False:
        return False
    op = node.get("opacity")
    if isinstance(op, (int, float)) and not isinstance(op, bool) and op < OPAQUE:
        return False
    return any(p.get("type") == "SOLID" and p.get("visible") is not False and alpha(p) >= OPAQUE
               and p.get("blendMode", "NORMAL") in ("NORMAL", "PASS_THROUGH") for p in _paints(node, "fillPaints"))
