"""impl_model.py — снимок страницы (`dom-snapshot.js`) → модель реализации в форме канона (спека §14.4.1).

Модель `sourceFormat: dom` — ТА ЖЕ схема, что у макета, поэтому один матчер и один чекер обслуживают оба
мира. Здесь только перевод формы, без суждений:
  • цвета снимка (`#RRGGBB[AA]`) → канонические `rgba(R, G, B, A)`; нераспознанное (oklch(), currentcolor…)
    идёт как есть — сравнивать его будет тот, кто умеет, а не мы;
  • тень — структурой в снимке, одной CSS-строкой `box-shadow` в модели (как её пишет ingest макета);
  • box есть у КАЖДОГО узла (измерен браузером), size — числа;
  • stamps (`data-daiver-id`) в модель не попадают НИКОГДА: оракул-тесты доказывают что-то, только пока
    сопоставитель не может их прочесть;
  • имена компонентов из fiber → `component {figmaName, implName, implNames}`: `implNames` — вся цепочка
    от внешнего к внутреннему (гейт дизайн-системы ищет среди неё опубликованное имя), `implName`/`figmaName` —
    внешнее; `figmaName` обязателен схемой, у dom-модели своего «имени в макете» нет;
  • то, чему в каноне нет места (`attrs`, `textBox`, `bgImage`, `scrolls`, `overflowing`, `cut`), остаётся в
    `snapshot.json` — модель их не несёт, а считаем мы их в `stats`, чтобы потеря была видна.
Пустой документ (`root: null`) — пустой корень без детей и `stats.empty: true`, не исключение.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import canon as C

DROPPED = ("attrs", "textBox", "bgImage", "scrolls", "overflowing", "cut")     # поля снимка без места в каноне
_HEX_RE = re.compile(r"^#([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})?$")
ROOT_ID = "dom:0.1"


class ImplModelError(ValueError):
    """Снимок непригоден (не словарь, чужая версия, дерево не по схеме)."""


def alpha_from_byte(byte: int) -> float:
    """Альфа по байту. Снимок хранит её байтом (`#RRGGBB80`), а автор писал `0.5` или `0.1`: отдаём САМОЕ КОРОТКОЕ
    десятичное число (2 знака, иначе 3), которое даёт тот же байт — иначе `0.1` вернулось бы как `0.102`."""
    for digits in (2, 3):
        cand = round(byte / 255.0, digits)
        if int(cand * 255 + 0.5) == byte:
            return cand
    return round(byte / 255.0, 3)


def color_to_canon(value: Any) -> Optional[str]:
    """`#RRGGBB[AA]` → `rgba(R, G, B, A)`; всё остальное непустое — как есть; пусто/не строка → None."""
    if not isinstance(value, str) or not value.strip():
        return None
    m = _HEX_RE.match(value.strip())
    if not m:
        return value.strip()
    r, g, b = (int(m.group(i), 16) for i in (1, 2, 3))
    a = alpha_from_byte(int(m.group(4), 16)) if m.group(4) else 1
    return C.fmt_rgba(r, g, b, a)


def _px(v: Any) -> str:
    return str(C.round_num(float(v), 2)) + "px"


def shadow_to_css(layers: Any) -> Optional[str]:
    """[{x,y,blur,spread,color,inset}] → `[inset ]Xpx Ypx Bpx Spx rgba(…)`, слои через запятую (форма ingest/style.py)."""
    if not isinstance(layers, list):
        return None
    parts: List[str] = []
    for s in layers:
        if not isinstance(s, dict):
            continue
        col = color_to_canon(s.get("color"))
        nums = " ".join(_px(s.get(k) or 0) for k in ("x", "y", "blur", "spread"))
        parts.append(("inset " if s.get("inset") else "") + nums + (" " + col if col else ""))
    return ", ".join(parts) or None


def _style(src: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    bg = color_to_canon(src.get("background"))
    if bg:
        out["background"] = bg
    if isinstance(src.get("gradient"), str) and src["gradient"]:
        out["gradient"] = src["gradient"]
    if "radius" in src:
        out["radius"] = src["radius"]
    b = src.get("border")
    if isinstance(b, dict):
        nb: Dict[str, Any] = {"width": b.get("width", 0)}
        col = color_to_canon(b.get("color"))
        if col:
            nb["color"] = col
        if b.get("style"):
            nb["style"] = b["style"]
        out["border"] = nb
    sh = shadow_to_css(src.get("shadow"))
    if sh:
        out["shadow"] = sh
    if "opacity" in src:
        out["opacity"] = src["opacity"]
    return out


def _node(src: Dict[str, Any], counts: Dict[str, int]) -> Dict[str, Any]:
    n: Dict[str, Any] = {"id": src["id"], "kind": src["kind"], "box": dict(src["box"])}
    if src.get("name"):
        n["name"] = src["name"]
    if src.get("tag") and src["tag"] != "#text":
        n["tag"] = src["tag"]
    if isinstance(src.get("size"), dict):
        n["size"] = {"w": src["size"].get("w", n["box"]["w"]), "h": src["size"].get("h", n["box"]["h"])}
    else:
        n["size"] = {"w": n["box"]["w"], "h": n["box"]["h"]}
    if isinstance(src.get("layout"), dict):
        n["layout"] = {k: v for k, v in src["layout"].items() if not k.startswith("_")}
    if isinstance(src.get("layoutHints"), dict):
        n["layoutHints"] = src["layoutHints"]
    if isinstance(src.get("style"), dict):
        st = _style(src["style"])
        if st:
            n["style"] = st
    if "text" in src:
        n["text"] = src["text"]
    if isinstance(src.get("font"), dict):
        n["font"] = dict(src["font"])
    col = color_to_canon(src.get("color"))
    if col:
        n["color"] = col
    names = [x for x in (src.get("components") or []) if isinstance(x, str) and x]
    if names:
        n["component"] = {"figmaName": names[0], "implName": names[0], "implNames": names}
    for k in DROPPED:
        if k in src:
            counts[k] = counts.get(k, 0) + 1
    return n


def convert(root: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Дерево снимка → дерево канона (итеративно: DOM бывает глубже лимита рекурсии). Возвращает {root, dropped}."""
    counts: Dict[str, int] = {}
    if not isinstance(root, dict):
        return {"root": None, "dropped": counts}
    out_root = _node(root, counts)
    stack = [(root, out_root)]
    while stack:
        src, dst = stack.pop()
        kids = src.get("children")
        if not isinstance(kids, list) or not kids:
            continue
        dst["children"] = []
        for k in kids:
            if not isinstance(k, dict):
                continue
            nk = _node(k, counts)
            dst["children"].append(nk)
            stack.append((k, nk))
    return {"root": out_root, "dropped": counts}


def _empty_root() -> Dict[str, Any]:
    return {"id": ROOT_ID, "kind": "frame", "name": "body", "tag": "body",
            "box": {"x": 0, "y": 0, "w": 0, "h": 0}, "size": {"w": 0, "h": 0}, "children": []}


def to_model(snapshot: Any) -> Dict[str, Any]:
    """Снимок (разобранный JSON) → нормализованная валидная модель `sourceFormat: dom`. ImplModelError — снимок не тот;
    C.CanonError — после перевода дерево нарушает схему (баг перевода, а не страницы: молча не чиним)."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("v"), int):
        raise ImplModelError("снимок: ожидается объект с полем v (версия формата)")
    if snapshot["v"] != 1:
        raise ImplModelError(f"снимок: версия формата {snapshot['v']} не поддерживается (умею 1)")
    got = convert(snapshot.get("root"))
    empty = got["root"] is None
    stats: Dict[str, Any] = {
        "elements": snapshot.get("elements", 0),
        "domElements": snapshot.get("domElements", 0),
        "truncated": snapshot.get("truncated") or {},
        "react": snapshot.get("react") or {"found": False},
        "viewport": snapshot.get("viewport") or {},
        "page": snapshot.get("page") or {},
        "dropped": got["dropped"],
    }
    if empty:
        stats["empty"] = True
    env = C.new_envelope(_empty_root() if empty else got["root"], "dom", stats)
    env = C.normalize(env)
    problems = C.validate(env)
    if problems:
        raise C.CanonError(problems)
    return env
