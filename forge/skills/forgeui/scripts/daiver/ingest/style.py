"""ingest/style.py — краски, типографика и токены Pixso DSL → поля канона (спека §7.2, задача 1.5).

Verify сравнивает цвета, шрифты, радиусы и тени с допусками в единицы, поэтому канон несёт их точно и
в одной форме для любого источника: цвет — строка `rgba(R, G, B, A)` (каналы 0..255 целые, альфа до
3 знаков), числа — px, тень — одна CSS-строка `box-shadow`.

Правила, проверенные по образцу «Modal» (docs/daiver/dsl.json), а не угаданные:
  • краска `{type, color:{r,g,b: 0..255, a: 0..1}, opacity?, visible?, blendMode}`; поле `opacity` краски
    дублирует `color.a` (0.12 и 0.12) — эффективная альфа это меньшая из двух (paint.alpha);
  • масштаб каналов определяется ОДИН раз на файл (в образце 0..255; 0..1 — если ни один канал нигде не
    превышает 1), смешивать масштабы внутри файла нельзя;
  • привязанный стиль — `inherit{Fill,Text,Stroke,Effect}StyleID` → запись `localStyleMap` (FILL несёт
    `fillPaints`, TEXT — `text{fontFamily,fontSize,fontStyle,lineHeight*,letterSpacing*}`; стиль обводки —
    тоже FILL). Значение, которое узел несёт сам, сильнее стиля; чего нет — берётся из стиля. Стиль, чьей
    записи в экспорте нет (библиотечный), имени не даёт — токен не пишется, случай считается;
  • пустой список краски в ОРЕДЕ оверрайда (`fillPaints: []`) — «не переопределено», а не «заливки нет»
    (у заголовка шапки так, а на картинке цвет есть); у самого узла пустой список — настоящее «нет»;
  • радиус: `cornerRadius`, а при `rectangle{TopLeft,…}CornerRadius` — по углам в порядке CSS
    (tl, tr, br, bl; отсутствующий угол = 0), массив только если углы различаются; обводка — по сторонам
    (`border*Weight`) либо `strokeWeight`; DROP_SHADOW/INNER_SHADOW → `box-shadow` (inner — `inset`),
    размытие слоя и фона — не тень, отбрасывается со счётчиком.
Не проверено образцом (принято по соглашению Figma, названо в «Результате» задачи): порядок нескольких
заливок (берётся первая видимая — как у детей «topmost first»; число таких узлов считается),
направление линейного градиента (из `startPt/endPt`, иначе без угла), процентные единицы шрифта.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import canon as C
from . import paint as PT

WEIGHTS = {
    "thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300, "regular": 400,
    "normal": 400, "book": 400, "medium": 500, "semibold": 600, "demibold": 600, "bold": 700,
    "extrabold": 800, "ultrabold": 800, "heavy": 900, "black": 900,
}
TEXT_ALIGN = {"left": "left", "center": "center", "right": "right", "justified": "justify", "justify": "justify"}
STROKE_ALIGN = ("inside", "center", "outside")
CORNERS = ("TopLeft", "TopRight", "BottomRight", "BottomLeft")        # порядок CSS border-radius
STYLE_KEYS = {"fill": "inheritFillStyleID", "text": "inheritTextStyleID", "stroke": "inheritStrokeStyleID",
              "effect": "inheritEffectStyleID"}
LIST_KEYS = ("fillPaints", "strokePaints", "effects")
UNRESOLVED_LIMIT = 20


def _num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def fmt(v: float) -> str:
    return str(C.round_num(v, 2))


def px(v: float) -> str:
    return fmt(v) + "px"


def detect_color_scale(body: Any) -> int:
    """255 — каналы 0..255 (образец), 1 — 0..1. Решается один раз по ВСЕМ цветам файла."""
    peak = 0.0
    stack: List[Any] = [body]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            c = o.get("color")
            if isinstance(c, dict):
                chans = [_num(c.get(k)) for k in ("r", "g", "b")]
                if None not in chans:
                    peak = max(peak, *chans)
            stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
        elif isinstance(o, list):
            stack.extend(v for v in o if isinstance(v, (dict, list)))
    return 1 if 0 < peak <= 1 else 255


def overlay(node: dict, ent: Dict[str, Tuple[int, Any]]) -> dict:
    """Узел мастера с наложенными записями оверрайда: пустой список краски — «не переопределено»; стиль
    без собственной краски в записи отбрасывает устаревшую краску мастера (её даст новый стиль)."""
    out = dict(node)
    for key, (_, val) in ent.items():
        if key in LIST_KEYS and isinstance(val, list) and not val:
            continue
        if key == "autoLayout" and isinstance(val, dict) and isinstance(out.get("autoLayout"), dict):
            out["autoLayout"] = dict(out["autoLayout"], **val)
        else:
            out[key] = val
    for list_key, style_key in (("fillPaints", STYLE_KEYS["fill"]), ("strokePaints", STYLE_KEYS["stroke"])):
        if style_key in ent and not (isinstance(ent.get(list_key, (0, None))[1], list) and ent[list_key][1]):
            out.pop(list_key, None)
    return out


class Look:
    """Внешний вид узла для канона: style/tokens у не-текста, font/color/tokens у текста."""
    __slots__ = ("style", "font", "color", "tokens")

    def __init__(self) -> None:
        self.style: Dict[str, Any] = {}
        self.font: Dict[str, Any] = {}
        self.color: Optional[str] = None
        self.tokens: Dict[str, str] = {}


class Styler:
    def __init__(self, body: dict, warn: Callable[[str, str], None]):
        ls = body.get("localStyleMap")
        self.styles: Dict[str, dict] = {k: v for k, v in ls.items() if isinstance(v, dict)} if isinstance(ls, dict) else {}
        self.scale = detect_color_scale(body)
        self.warn = warn
        self.counts: Counter = Counter()
        self.unresolved: Dict[str, set] = {}
        self.unknown_fonts: List[str] = []

    # ── отчёт ────────────────────────────────────────────────────────────────
    def report(self) -> dict:
        out: Dict[str, Any] = {"colorScale": self.scale, "counts": dict(sorted(self.counts.items()))}
        if self.unresolved:
            out["unresolvedStyles"] = {k: {"count": len(v), "ids": sorted(v)[:UNRESOLVED_LIMIT]}
                                       for k, v in sorted(self.unresolved.items())}
        if self.unknown_fonts:
            out["unknownFontStyles"] = sorted(self.unknown_fonts)
        return out

    def _unresolved(self, kind: str, sid: str) -> None:
        self.unresolved.setdefault(kind, set()).add(sid)

    def style(self, node: dict, kind: str) -> Optional[dict]:
        """Запись привязанного стиля узла; None — не привязан или записи в экспорте нет (считается)."""
        sid = node.get(STYLE_KEYS[kind])
        if not isinstance(sid, str) or not sid:
            return None
        rec = self.styles.get(sid)
        if rec is None:
            self._unresolved(kind, sid)
        return rec

    def token(self, node: dict, kind: str) -> Optional[str]:
        rec = self.style(node, kind)
        name = rec.get("name") if rec else None
        if isinstance(name, str) and name:
            self.counts["styleRefsResolved"] += 1
            return name
        return None

    # ── цвет и краски ────────────────────────────────────────────────────────
    def rgba(self, color: Any, alpha: Optional[float] = None) -> Optional[str]:
        if not isinstance(color, dict):
            return None
        chans = [_num(color.get(k)) for k in ("r", "g", "b")]
        if None in chans:
            return None
        k = 255 if self.scale == 1 else 1
        a = _num(color.get("a"))
        a = 1.0 if a is None else a
        return C.fmt_rgba(chans[0] * k, chans[1] * k, chans[2] * k, a if alpha is None else min(a, alpha))

    def paints(self, node: dict, kind: str) -> List[dict]:
        """Краски узла: свои (даже пустой список — это «нет»), иначе из привязанного стиля."""
        key = "fillPaints" if kind == "fill" else "strokePaints"
        own = node.get(key)
        if isinstance(own, list):
            return [p for p in own if isinstance(p, dict)]
        rec = self.style(node, kind)
        got = rec.get("fillPaints") if rec else None
        return [p for p in got if isinstance(p, dict)] if isinstance(got, list) else []

    def top(self, paints: List[dict], count: bool = True) -> Optional[dict]:
        vis = [p for p in paints if PT.paint_is_visible(p)]
        if count and len(vis) > 1:
            self.counts["multiFill"] += 1
            self.warn("multi-fill", "у узлов несколько видимых заливок — порядок слоёв в образце не проверен, "
                                    "взята первая видимая (см. stats.styles.counts.multiFill)")
        return vis[0] if vis else None

    def gradient(self, p: dict) -> Optional[str]:
        stops = p.get("stops")
        if not isinstance(stops, list) or not stops:
            return None
        op = _num(p.get("opacity"))
        parts = []
        for s in sorted((s for s in stops if isinstance(s, dict)), key=lambda s: _num(s.get("position")) or 0):
            col = self.rgba(s.get("color"), 1.0 if op is None else op)
            pos = _num(s.get("position"))
            if col is None or pos is None:
                return None
            parts.append(f"{col} {fmt(pos * 100)}%")
        body = ", ".join(parts)
        t = str(p.get("type"))
        self.counts["gradients"] += 1
        if t == "GRADIENT_LINEAR":
            a, b = p.get("startPt"), p.get("endPt")
            if isinstance(a, dict) and isinstance(b, dict) and None not in (_num(a.get("x")), _num(a.get("y")),
                                                                              _num(b.get("x")), _num(b.get("y"))):
                deg = math.degrees(math.atan2(b["x"] - a["x"], -(b["y"] - a["y"]))) % 360
                return f"linear-gradient({fmt(deg)}deg, {body})"
            self.counts["gradientAngleUnknown"] += 1
            return f"linear-gradient({body})"
        if t == "GRADIENT_ANGULAR":
            return f"conic-gradient({body})"
        return f"radial-gradient({body})"          # RADIAL и DIAMOND (ромб приближён кругом)

    def css(self, p: dict) -> Optional[Tuple[str, str]]:
        """('solid', rgba) | ('gradient', css) | ('image', '') для верхней краски."""
        t = str(p.get("type"))
        if t == "SOLID":
            col = self.rgba(p.get("color"), PT.alpha(p))
            return ("solid", col) if col else None
        if t.startswith("GRADIENT"):
            g = self.gradient(p)
            return ("gradient", g) if g else None
        if t == "IMAGE":
            return ("image", "")
        self.counts["paintTypesUnknown"] += 1
        return None

    def is_image(self, node: dict) -> bool:
        p = self.top(self.paints(node, "fill"), count=False)
        return bool(p and str(p.get("type")) == "IMAGE")

    # ── геометрия стиля ──────────────────────────────────────────────────────
    @staticmethod
    def radius(node: dict) -> Any:
        corners = [_num(node.get(f"rectangle{c}CornerRadius")) for c in CORNERS]
        if any(c is not None for c in corners):
            vals = [c or 0 for c in corners]
        else:
            r = _num(node.get("cornerRadius"))
            vals = [r or 0] * 4
        if not any(vals):
            if node.get("type") == "ELLIPSE":
                w, h = _num(node.get("width")), _num(node.get("height"))
                if w and h:
                    return C.round_num(min(w, h) / 2, 2)
            return None
        return C.round_num(vals[0], 2) if len(set(vals)) == 1 else [C.round_num(v, 2) for v in vals]

    def border(self, node: dict) -> Optional[dict]:
        top = self.top(self.paints(node, "stroke"))
        if top is None:
            return None
        sides = {s.lower(): _num(node.get(f"border{s}Weight")) for s in ("Top", "Right", "Bottom", "Left")}
        if any(v is not None for v in sides.values()):
            width: Any = {k: (v or 0) for k, v in sides.items()}
            if len(set(width.values())) == 1:
                width = next(iter(width.values()))
        else:
            width = _num(node.get("strokeWeight"))
        if not width or (isinstance(width, dict) and not any(width.values())):
            return None
        shown = self.css(top)
        out: Dict[str, Any] = {"width": C.round_num(width, 2) if not isinstance(width, dict)
                               else {k: C.round_num(v, 2) for k, v in width.items()}}
        if shown and shown[0] != "image":
            out["color"] = shown[1]
        align = str(node.get("strokeAlign") or "").lower()
        if align in STROKE_ALIGN:
            out["align"] = align
        return out

    def shadow(self, node: dict) -> Optional[str]:
        effects = node.get("effects")
        if not isinstance(effects, list):
            rec = self.style(node, "effect")
            effects = rec.get("effects") if rec else None
        parts = []
        for e in effects if isinstance(effects, list) else []:
            if not isinstance(e, dict) or e.get("visible") is False:
                continue
            t = str(e.get("type"))
            if t not in ("DROP_SHADOW", "INNER_SHADOW"):
                self.counts["effectsDropped"] += 1             # размытие слоя/фона — не тень
                continue
            col = self.rgba(e.get("color"))
            if col is None:
                continue
            off = e.get("offset") if isinstance(e.get("offset"), dict) else {}
            parts.append(("inset " if t == "INNER_SHADOW" else "") + " ".join(
                px(_num(v) or 0) for v in (off.get("x"), off.get("y"), e.get("radius"), e.get("spread"))) + " " + col)
        return ", ".join(parts) or None

    # ── типографика ──────────────────────────────────────────────────────────
    def weight(self, name: Any) -> Tuple[Optional[int], bool]:
        """('Semibold Italic') → (600, True). Неизвестное имя — (None, italic) и счётчик."""
        if isinstance(name, (int, float)) and not isinstance(name, bool):
            return int(name), False
        raw = str(name or "")
        low = raw.lower().replace("-", " ").replace("_", " ")
        italic = "italic" in low or "oblique" in low
        key = low.replace("italic", "").replace("oblique", "").replace(" ", "")
        if not key:
            return 400, italic
        if key.isdigit():
            return int(key), italic
        got = WEIGHTS.get(key)
        if got is None and raw and raw not in self.unknown_fonts:
            self.unknown_fonts.append(raw)
            self.warn(f"fontStyle:{raw}", f"начертание «{raw}» незнакомо — вес не выведен")
        return got, italic

    def font(self, node: dict) -> Dict[str, Any]:
        rec = self.style(node, "text")
        base = rec.get("text") if rec and isinstance(rec.get("text"), dict) else {}

        def get(k: str) -> Any:
            v = node.get(k)
            return v if v is not None else base.get(k)

        out: Dict[str, Any] = {}
        if isinstance(get("fontFamily"), str) and get("fontFamily"):
            out["family"] = get("fontFamily")
        size = _num(get("fontSize"))
        if size is not None:
            out["size"] = C.round_num(size, 2)
        weight, italic = self.weight(get("fontStyle"))
        if weight is not None:
            out["weight"] = weight
        if italic:
            out["italic"] = True
        lh = self._metric(get("lineHeightNumber"), get("lineHeightUnit"), size, auto_ok=True)
        if lh is not None:
            out["lineHeight"] = lh
        ls = self._metric(get("letterSpacingNumber"), get("letterSpacingUnit"), size, auto_ok=False)
        if ls:
            out["letterSpacing"] = ls                          # ноль — значение по умолчанию, не пишется
        align = TEXT_ALIGN.get(str(get("textAlignHorizontal") or "").lower())
        if align:
            out["align"] = align
        name = rec.get("name") if rec else None
        if isinstance(name, str) and name:
            out["token"] = name
        return out

    def _metric(self, number: Any, unit: Any, size: Optional[float], auto_ok: bool) -> Any:
        u = str(unit or "").upper()
        n = _num(number)
        if auto_ok and u in ("AUTO", "INTRINSIC", "INTRINSIC_%"):
            return "auto"
        if n is None:
            return None
        if u in ("", "PIXELS", "PIXEL", "PX"):
            return C.round_num(n, 2)
        if u in ("PERCENT", "%"):
            if size is None:
                return None
            return C.round_num(size * n / 100, 2)             # процент — доля размера шрифта
        self.warn(f"metric-unit:{u}", f"единица «{unit}» у межстрочного/трекинга незнакома — значение не выведено")
        return None

    # ── сводка по узлу ───────────────────────────────────────────────────────
    def look(self, node: dict, kind: str) -> Look:
        """kind — вид узла канона. Текст: font/color; вектор: color; остальное: style; токены — у всех."""
        out = Look()
        fill = self.top(self.paints(node, "fill"))
        shown = self.css(fill) if fill else None
        if kind == "text":
            out.font = self.font(node)
            if shown and shown[0] == "solid":
                out.color = shown[1]
            elif shown and shown[0] == "gradient":
                self.counts["textGradientDropped"] += 1
        elif kind == "vector":
            if shown and shown[0] == "solid":
                out.color = shown[1]
        elif kind != "image":
            if shown and shown[0] == "solid":
                out.style["background"] = shown[1]
            elif shown and shown[0] == "gradient":
                out.style["gradient"] = shown[1]
            rad = self.radius(node)
            if rad is not None:
                out.style["radius"] = rad
            border = self.border(node)
            if border:
                out.style["border"] = border
            shadow = self.shadow(node)
            if shadow:
                out.style["shadow"] = shadow
        op = _num(node.get("opacity"))
        if op is not None and op < 1:
            out.style["opacity"] = C.round_num(op, 3)
        # токены: только на то, что узел реально показывает
        if fill is not None and node.get(STYLE_KEYS["fill"]):
            name = self.token(node, "fill")
            if name:
                out.tokens["fill"] = name
        if kind == "text" and out.font.get("token"):
            out.tokens["text"] = out.font["token"]
        if "border" in out.style and node.get(STYLE_KEYS["stroke"]):
            name = self.token(node, "stroke")
            if name:
                out.tokens["stroke"] = name
        if "shadow" in out.style and node.get(STYLE_KEYS["effect"]):
            name = self.token(node, "effect")
            if name:
                out.tokens["effect"] = name
        return out
