"""check.py — правила проверки над парами матчера: находки, dismissals, unverified (спека §14.4.3, §14.4.4; INV-C27, QG-U16).

Каждое правило куплено на реальных прогонах: ложный дефект так же вреден, как пропущенный, поэтому всё, что не стало
дефектом, выводится С ПРИЧИНОЙ (dismissal несёт приговор-предложение), а не молча дропается. Модуль чистый: канон
макета, канон страницы, результат `match.match` и допуски → словарь; диска и браузера нет.

Слои (порядок — LAYERS): structure (MISSING/EXTRA/ORDER из матчера + TEXT) → component (SLOT_MISSING, SLOT_TEXT,
SLOT_TYPOGRAPHY, SLOT_COLOR, INVISIBLE) → token → geometry (SPACING, SIZE, POSITION, OVERFLOW) → typography → color →
shape (SHAPE). Слой token правил не имеет: снимок страницы не несёт имён токенов, а цвет сверяется по значению.

Доктрины (по списку спеки), на данных образца «Modal» и на синтетике:
  • числовые правила — только на уверенных парах (`confidence: high`); у `low` они пропускаются со счётчиком
    (`stats.skippedLowConfidence`), identity-правила (structure, component) работают всегда;
  • «что компонент рендерит — сравнивается»: SLOT_* читают `component.content` (а у инстанса без него — `textProps`);
    сравниваются только overridden-слоты — слот-плейсхолдер библиотеки не дефект и не источник правды (он уходит в
    unverified.copy); SLOT_TYPOGRAPHY+SLOT_COLOR на двух слотах ячейки, совпавшие при обмене, — подсказка «перепутаны»;
  • «чей размер»: SIZE на инстансе только при `sized: own`, иначе dismissal (и unverified.size);
  • внутрикомпонентный padding/gap/radius/border библиотечного инстанса, о котором обе стороны назвали один и тот же
    компонент, — контракт библиотеки (dismissal), но INVISIBLE (мастер рисует поверхность, страница — нет) — дефект;
  • выведенный размер (fill/hug, высота текстового слоя, line box выше, число макета больше комнаты родителя, корень) —
    dismissals; «тот же текстовый бокс короче» и ширина того же текстового бокса — репорт;
  • отступы — по сторонам; margin вместо gap при равных измеренных боксах — dismissal;
  • типографика: первое семейство, пропущенный вес = Regular, align/lineHeight — только где обе стороны объявили;
  • цвет: hex/keyword/rgba — одно значение, нет заливки = transparent, градиент — текстом, обводка у одной стороны — тихо;
  • форма: радиус — то, что рисует браузер (min(r, w/2, h/2)), наличие обводки и тени — в обе стороны;
  • текст: normalizeText; другая копия — critical; только регистр, слот в другом месте страницы — dismissal;
  • «на грани» не срабатывает (строгое «>»); размер в пределах пропорции, но сверх капа `sizeRatioMaxPx`, — репорт;
  • падение правила — `degraded` с названием и текстом ошибки, остальные идут; нет пар — все правила «исполнены».

Допущения, не проверенные данными (боевых прогонов нет): severity (critical/major — выбор этой задачи, спека
уровней не называет); допуск межбуквенного `LETTER_SPACING_TOL`; допуск альфы 0.02 («округлённый токен проходит»);
правило OVERFLOW читает флаги `overflowing`/`scrolls`, которых нет ни в каноне, ни в модели страницы (`impl_model` их
сбрасывает) — их передаёт вызывающий (`flags`), у макета признака нет вовсе; правило POSITION работает только там, где у
макета есть box (в настоящем каноне — у абсолютных узлов); «extent auto-layout = content + padding» не вычисляется.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .. import config as CFG
from . import match as MT

LAYERS = ("structure", "component", "token", "geometry", "typography", "color", "shape")
LAYER_OF = {"MISSING": "structure", "EXTRA": "structure", "ORDER": "structure", "TEXT": "structure",
            "SLOT_MISSING": "component", "SLOT_TEXT": "component", "SLOT_TYPOGRAPHY": "component",
            "SLOT_COLOR": "component", "INVISIBLE": "component",
            "SPACING": "geometry", "SIZE": "geometry", "POSITION": "geometry", "OVERFLOW": "geometry",
            "TYPOGRAPHY": "typography", "COLOR": "color", "SHAPE": "shape"}
CRITICAL = ("MISSING", "TEXT", "SLOT_MISSING", "SLOT_TEXT", "INVISIBLE")
LETTER_SPACING_TOL = 0.1                # px: «чуть шире волоса» — репорт
ALPHA_TOL = 0.02                        # округлённый токен прозрачности проходит
SURFACE_COVER = 0.8                     # доля бокса, которую должна закрывать краска, чтобы считаться поверхностью
SIMILAR_SLOT = 0.6                      # слот с таким сходством текста — SLOT_TEXT (другая копия), иначе SLOT_MISSING
ASPECTS = ("content", "copy", "size")
CORNERS = ("top-left", "top-right", "bottom-right", "bottom-left")
KEYWORDS = {"transparent": (0, 0, 0, 0.0), "black": (0, 0, 0, 1.0), "white": (255, 255, 255, 1.0), "red": (255, 0, 0, 1.0),
            "green": (0, 128, 0, 1.0), "blue": (0, 0, 255, 1.0), "gray": (128, 128, 128, 1.0), "grey": (128, 128, 128, 1.0),
            "yellow": (255, 255, 0, 1.0), "orange": (255, 165, 0, 1.0), "purple": (128, 0, 128, 1.0)}
_HEX = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC = re.compile(r"^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+%?))?\s*\)$", re.IGNORECASE)


class CheckError(ValueError):
    """Вход не похож на результат матчера или дерево канона."""


# --------------------------------------------------------------------------------------------------------------- цвета

def parse_color(s: Any) -> Optional[Tuple[int, int, int, float]]:
    """hex / rgb() / rgba() / ключевое слово → (r, g, b, a); не разобрано (oklch(), currentcolor, var(…)) → None."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    if t.lower() in KEYWORDS:
        return KEYWORDS[t.lower()]
    m = _HEX.match(t)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(a, 4))
    m = _FUNC.match(t)
    if m:
        a = m.group(4)
        alpha = (float(a[:-1]) / 100 if a.endswith("%") else float(a)) if a else 1.0
        return (int(round(float(m.group(1)))), int(round(float(m.group(2)))), int(round(float(m.group(3)))), alpha)
    return None


def _num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def colors_differ(a: Any, b: Any, tol: float) -> Optional[bool]:
    """Различаются ли два цвета: True / False; None — сравнить нельзя (формат не разобран). Нет цвета = transparent;
    каналы — строго «> допуска» (на грани тихо), прозрачность — ALPHA_TOL, у полностью прозрачных каналы не сравниваются."""
    pa = parse_color(a) if a else KEYWORDS["transparent"]
    pb = parse_color(b) if b else KEYWORDS["transparent"]
    if pa is None or pb is None:
        return None if (a or b) and (a != b) else False
    if abs(pa[3] - pb[3]) > max(ALPHA_TOL, tol / 255.0):
        return True
    if pa[3] == 0 and pb[3] == 0:
        return False
    return any(abs(x - y) > tol for x, y in zip(pa[:3], pb[:3]))


# --------------------------------------------------------------------------------------------------------------- контекст

def _index(root: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Optional[str]], List[str]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    parent: Dict[str, Optional[str]] = {}
    order: List[str] = []
    stack: List[Tuple[Dict[str, Any], Optional[str]]] = [(root, None)]
    while stack:
        n, par = stack.pop()
        nid = n["id"]
        nodes[nid] = n
        parent[nid] = par
        order.append(nid)
        for k in reversed([k for k in (n.get("children") or []) if isinstance(k, dict)]):
            stack.append((k, nid))
    return nodes, parent, order


def _root_of(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict) and "root" in x and "kind" not in x:
        x = x["root"]
    if not isinstance(x, dict) or "kind" not in x or "id" not in x:
        raise CheckError("ожидается узел канона (или конверт с root)")
    return x


def _norm(s: Any) -> str:
    return MT.norm_text(s)


class _Ctx:
    def __init__(self, design: Dict[str, Any], impl: Optional[Dict[str, Any]], matched: Dict[str, Any],
                 tol: Dict[str, float], flags: Dict[str, Dict[str, Any]]):
        self.D, self.Dp, self.Dorder = _index(design)
        self.I, self.Ip, self.Iorder = _index(impl) if impl else ({}, {}, [])
        self.m = matched
        self.tol = tol
        self.flags = flags
        self.findings: List[Dict[str, Any]] = []
        self.dismissals: List[Dict[str, Any]] = []
        self.root_id = design["id"]
        self.cache: Dict[str, Any] = {}
        self.pairs = []                                  # (design id, impl id, confidence, anchor)
        for p in matched.get("pairs") or []:
            if p.get("design") in self.D and p.get("impl") in self.I:
                self.pairs.append((p["design"], p["impl"], p.get("confidence", "high"), p.get("anchor")))
        self.sure = [p for p in self.pairs if p[2] == "high"]

    # --- выход -------------------------------------------------------------------------------------------------------
    def find(self, code: str, d: Optional[str], i: Optional[str], expected: Any, actual: Any, message: str,
             prop: Optional[str] = None, **extra: Any) -> None:
        f: Dict[str, Any] = {"code": code, "layer": LAYER_OF[code], "severity": "critical" if code in CRITICAL else "major",
                             "design": d, "impl": i, "message": message}
        if prop:
            f["prop"] = prop
        if expected is not None:
            f["expected"] = expected
        if actual is not None:
            f["actual"] = actual
        f.update(extra)
        self.findings.append(f)

    def dismiss(self, code: str, d: str, i: Optional[str], reason: str, prop: Optional[str] = None) -> None:
        r: Dict[str, Any] = {"code": code, "layer": LAYER_OF[code], "design": d, "impl": i, "reason": reason}
        if prop:
            r["prop"] = prop
        self.dismissals.append(r)

    # --- поддерево страницы --------------------------------------------------------------------------------------------
    def impl_texts(self, i: str) -> List[Tuple[str, Dict[str, Any]]]:
        """Текстовые узлы страницы в поддереве i в порядке документа: [(id, узел)]."""
        out: List[Tuple[str, Dict[str, Any]]] = []
        stack = [i]
        while stack:
            x = stack.pop()
            n = self.I[x]
            if isinstance(n.get("text"), str) and n["text"] and not n.get("children"):
                out.append((x, n))
            stack.extend(reversed([k["id"] for k in (n.get("children") or []) if isinstance(k, dict)]))
        return out

    def impl_subtree_ids(self, i: str) -> Set[str]:
        out: Set[str] = set()
        stack = [i]
        while stack:
            x = stack.pop()
            out.add(x)
            stack.extend(k["id"] for k in (self.I[x].get("children") or []) if isinstance(k, dict))
        return out


def _is_painted(n: Dict[str, Any]) -> bool:
    return MT._painted(n.get("style"))


def _box(n: Dict[str, Any]) -> Optional[Dict[str, float]]:
    b = n.get("box")
    return b if isinstance(b, dict) and all(_num(b.get(k)) is not None for k in ("x", "y", "w", "h")) else None


# ---------------------------------------------------------------------------------------------------------------- правила

def _claims(c: _Ctx) -> Set[str]:
    """Тексты страницы, которые уже объясняет слот инстанса, — другая копия слота (SLOT_TEXT) и копия слота-плейсхолдера
    (unverified.copy): отдельным EXTRA они не идут, иначе одна причина попадает в отчёт дважды."""
    if "claims" in c.cache:
        return c.cache["claims"]
    claimed: Set[str] = set()
    for d, i, _conf, _a in c.pairs:
        dn = c.D[d]
        if dn.get("kind") != "instance":
            continue
        slots = _slots(dn)
        if not slots:
            continue
        mine = {_norm(s["text"]) for s in slots}
        spare = [(x, n) for x, n in c.impl_texts(i) if _norm(n.get("text")) not in mine]
        real = [s for s in slots if s.get("overridden") is not False]
        holders = len(slots) - len(real)
        # другая копия слота-оверрайда: каждому непарному слоту — один непарный текст (порядок документа)
        unpaired = [s for s in real if _norm(s["text"]) not in {_norm(n.get("text")) for _x, n in c.impl_texts(i)}]
        for x, _n in spare[:len(unpaired) + holders]:
            claimed.add(x)
    c.cache["claims"] = claimed
    return claimed


def rule_structure(c: _Ctx) -> None:
    """MISSING / EXTRA / ORDER — готовые находки матчера, в том же порядке и с теми же id."""
    claimed = _claims(c)
    for m in c.m.get("missing") or []:
        c.find("MISSING", m["id"], None, None, None, f"нет на странице: {m.get('branch', '')}", branch=m.get("branch"),
               contentCount=m.get("contentCount"), **({"ids": m["ids"]} if m.get("ids") else {}))
    for e in c.m.get("extra") or []:
        if e["id"] in claimed:
            continue
        what = e.get("text") or e.get("component") or e.get("tag") or e.get("kind")
        c.find("EXTRA", None, e["id"], None, None, f"лишнее на странице: {what}", contentCount=e.get("contentCount"))
    for o in c.m.get("order") or []:
        c.find("ORDER", o["id"], o.get("impl"), o.get("designIndex"), o.get("implIndex"),
               "порядок соседей нарушен: сдвинулся этот элемент", parent=o.get("parent"))


def rule_text(c: _Ctx) -> None:
    for d, i, _conf, _a in c.pairs:
        dn, inn = c.D[d], c.I[i]
        if dn.get("kind") != "text" or not isinstance(inn.get("text"), str):
            continue
        want, got = _norm_copy(dn.get("text")), _norm_copy(inn.get("text"))
        if not want or want == got:
            continue                                     # пустой слой не толкуется; одинаковая копия — норма
        if want.casefold() == got.casefold():
            c.dismiss("TEXT", d, i, "копия отличается только регистром: text-transform на странице рисует то, что нужно — "
                                    "не чинить", prop="text")
        else:
            c.find("TEXT", d, i, dn["text"], inn["text"], "другая копия: текст страницы не совпадает с макетом", prop="text")


def _norm_copy(s: Any) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip() if isinstance(s, str) else ""


def _slots(dn: Dict[str, Any]) -> List[Dict[str, Any]]:
    comp = dn.get("component") or {}
    if comp.get("content"):
        return [dict(s) for s in comp["content"] if isinstance(s, dict) and s.get("text")]
    return [{"id": k, "text": v, "overridden": True} for k, v in sorted((comp.get("textProps") or {}).items())
            if isinstance(v, str) and v]


def rule_slots(c: _Ctx) -> None:
    all_texts = [(x, _norm(n.get("text"))) for x, n in c.I.items() if isinstance(n.get("text"), str) and not n.get("children")]
    for d, i, conf, _a in c.pairs:
        dn = c.D[d]
        if dn.get("kind") != "instance":
            continue
        slots = [s for s in _slots(dn) if s.get("overridden") is not False]          # слот-плейсхолдер библиотеки не сравнивается
        if not slots:
            continue
        inside = c.impl_texts(i)
        inside_ids = {x for x, _ in inside}
        free = list(inside)
        found: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        for s in slots:                                   # точные совпадения (с регистром) идут первыми — иначе близкий слот займёт чужой
            want = _norm_copy(s["text"])
            for k, (x, n) in enumerate(free):
                if _norm_copy(n.get("text")) == want:
                    found[s["id"]] = free.pop(k)
                    break
        for s in slots:
            if s["id"] in found:
                continue
            want = _norm(s["text"])
            hit = next(((k, x, n) for k, (x, n) in enumerate(free) if _norm(n.get("text")) == want), None)
            if hit:
                c.dismiss("SLOT_TEXT", d, i, "слот отличается только регистром: text-transform рисует то, что нужно — не чинить",
                          prop=s["id"])
                found[s["id"]] = free.pop(hit[0])
                continue
            elsewhere = [x for x, t in all_texts if t == want and x not in inside_ids]
            if elsewhere:
                c.dismiss("SLOT_MISSING", d, i, f"текст слота «{s['text'][:40]}» нарисован в другом месте страницы "
                                                  f"({elsewhere[0]}): слот на месте не обязателен — не чинить", prop=s["id"])
                continue
            best = max(((difflib.SequenceMatcher(None, want, _norm(n.get("text"))).ratio(), k) for k, (x, n) in enumerate(free)),
                       default=(0.0, -1))
            if best[0] >= SIMILAR_SLOT:
                x, n = free.pop(best[1])
                c.find("SLOT_TEXT", d, i, s["text"], n["text"], f"слот «{s.get('name') or s['id']}»: другая копия", prop=s["id"])
            else:
                c.find("SLOT_MISSING", d, i, s["text"], None, f"слот «{s.get('name') or s['id']}» не нарисован в компоненте",
                       prop=s["id"])
        if conf != "high":
            continue
        bad: List[Tuple[Dict[str, Any], bool, bool]] = []
        for s in slots:
            if s["id"] not in found:
                continue
            x, n = found[s["id"]]
            typo = _font_diffs(s.get("font"), n.get("font"), c.tol)
            col = bool(s.get("color") and n.get("color") and colors_differ(s["color"], n["color"], c.tol["color"]))
            for what, exp, act in typo:
                c.find("SLOT_TYPOGRAPHY", d, i, exp, act, f"слот «{s.get('name') or s['id']}»: {what}", prop=f"{s['id']}:{what}")
            if col:
                c.find("SLOT_COLOR", d, i, s["color"], n["color"], f"слот «{s.get('name') or s['id']}»: цвет текста",
                       prop=f"{s['id']}:color")
            bad.append((s, bool(typo), col))
        _swap_hint(c, d, i, slots, found, bad)


def _swap_hint(c: _Ctx, d: str, i: str, slots: List[Dict[str, Any]], found: Dict[str, Tuple[str, Dict[str, Any]]],
               bad: List[Tuple[Dict[str, Any], bool, bool]]) -> None:
    """Два слота ячейки, у обоих и типографика, и цвет не сошлись, а при обмене сходятся, — перепутаны: чинить назначение слотов."""
    both = [s for s, t, k in bad if t and k]
    for a in range(len(both)):
        for b in range(a + 1, len(both)):
            sa, sb = both[a], both[b]
            na, nb = found[sa["id"]][1], found[sb["id"]][1]
            if (not _font_diffs(sa.get("font"), nb.get("font"), c.tol) and not _font_diffs(sb.get("font"), na.get("font"), c.tol)
                    and not colors_differ(sa.get("color"), nb.get("color"), c.tol["color"])
                    and not colors_differ(sb.get("color"), na.get("color"), c.tol["color"])):
                for f in c.findings:
                    if f["code"] in ("SLOT_TYPOGRAPHY", "SLOT_COLOR") and f["design"] == d and f["prop"].split(":")[0] in (sa["id"], sb["id"]):
                        f["hint"] = (f"слоты «{sa.get('name') or sa['id']}» и «{sb.get('name') or sb['id']}» перепутаны местами: "
                                     f"чинить назначение слотов, а не тексты")


def rule_invisible(c: _Ctx) -> None:
    for d, i, _conf, _a in c.pairs:
        dn, inn = c.D[d], c.I[i]
        comp = dn.get("component") or {}
        if dn.get("kind") != "instance" or comp.get("paints") is not True:
            continue
        if inn.get("kind") in ("image", "vector"):
            continue                                      # картинка — не «бокс без краски»
        if _has_surface(c, i):
            continue
        c.find("INVISIBLE", d, i, "поверхность компонента (фон/обводка/тень)", "элемент без краски",
               "мастер рисует поверхность, а страница — элемент без краски: не «удовлетворять размер» прозрачным боксом")


def _has_surface(c: _Ctx, i: str) -> bool:
    inn = c.I[i]
    if _is_painted(inn):
        return True
    ib = _box(inn)
    for x in c.impl_subtree_ids(i):
        n = c.I[x]
        if x == i or not _is_painted(n) or n.get("kind") not in ("frame", "rect"):
            continue
        nb = _box(n)
        if not (ib and nb) or (ib["w"] and nb["w"] >= SURFACE_COVER * ib["w"] and nb["h"] >= SURFACE_COVER * ib["h"]):
            return True
    return False


def _first_family(f: Any) -> str:
    return str(f).split(",")[0].strip().strip("'\"").casefold() if isinstance(f, str) and f.strip() else ""


def _font_diffs(a: Any, b: Any, tol: Dict[str, float]) -> List[Tuple[str, Any, Any]]:
    """Расхождения шрифта макета `a` и страницы `b`: [(что, ожидалось, получено)]. Сравнивается то, что объявили ОБЕ стороны."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return []
    out: List[Tuple[str, Any, Any]] = []
    fa, fb = _first_family(a.get("family")), _first_family(b.get("family"))
    if fa and fb and fa != fb:
        out.append(("шрифт", a.get("family"), b.get("family")))
    sa, sb = _num(a.get("size")), _num(b.get("size"))
    if sa is not None and sb is not None and abs(sa - sb) > tol["fontSize"]:
        out.append(("размер шрифта", sa, sb))
    if "weight" in a or "weight" in b:
        wa, wb = a.get("weight") or 400, b.get("weight") or 400
        if wa != wb:
            out.append(("насыщенность", wa, wb))
    la, lb = _num(a.get("lineHeight")), _num(b.get("lineHeight"))
    if la is not None and lb is not None and abs(la - lb) > tol["lineHeight"]:
        out.append(("межстрочный", la, lb))
    ka, kb = _num(a.get("letterSpacing")), _num(b.get("letterSpacing"))
    if ka is not None and kb is not None and abs(ka - kb) > LETTER_SPACING_TOL:
        out.append(("межбуквенный", ka, kb))
    if a.get("align") and b.get("align") and a["align"] != b["align"]:
        out.append(("выравнивание", a["align"], b["align"]))
    if a.get("italic") is not None and b.get("italic") is not None and bool(a["italic"]) != bool(b["italic"]):
        out.append(("курсив", bool(a["italic"]), bool(b["italic"])))
    return out


def rule_typography(c: _Ctx) -> None:
    for d, i, _conf, _a in c.sure:
        dn, inn = c.D[d], c.I[i]
        if dn.get("kind") != "text":
            continue
        for what, exp, act in _font_diffs(dn.get("font"), inn.get("font"), c.tol):
            c.find("TYPOGRAPHY", d, i, exp, act, f"типографика: {what}", prop=what)


def rule_color(c: _Ctx) -> None:
    for d, i, _conf, _a in c.sure:
        dn, inn = c.D[d], c.I[i]
        if dn.get("kind") == "text":
            if dn.get("color") and inn.get("color"):
                diff = colors_differ(dn["color"], inn["color"], c.tol["color"])
                if diff:
                    c.find("COLOR", d, i, dn["color"], inn["color"], "цвет текста", prop="color")
                elif diff is None:
                    c.dismiss("COLOR", d, i, "формат цвета страницы не разобран: сравнить нельзя — проверить глазами", prop="color")
            continue
        ds, ist = dn.get("style") or {}, inn.get("style") or {}
        if ds.get("gradient") or ist.get("gradient"):
            if ds.get("gradient") and not ist.get("gradient"):
                c.find("COLOR", d, i, ds["gradient"], ist.get("background"), "градиент макета не нарисован", prop="gradient")
            elif ds.get("gradient") and ist.get("gradient") and _norm_grad(ds["gradient"]) != _norm_grad(ist["gradient"]):
                c.find("COLOR", d, i, ds["gradient"], ist["gradient"], "градиент отличается (сравнение текстом)", prop="gradient")
        else:
            diff = False if (_master_draws(dn) and not ds.get("background")) else \
                colors_differ(ds.get("background"), ist.get("background"), c.tol["color"])
            if diff:
                c.find("COLOR", d, i, ds.get("background") or "transparent", ist.get("background") or "transparent",
                       "цвет фона", prop="background")
            elif diff is None:
                c.dismiss("COLOR", d, i, "формат цвета страницы не разобран: сравнить нельзя — проверить глазами", prop="background")
        db, ib = ds.get("border"), ist.get("border")
        if isinstance(db, dict) and isinstance(ib, dict) and db.get("color") and ib.get("color") and _has_width(db) and _has_width(ib):
            if colors_differ(db["color"], ib["color"], c.tol["color"]):
                c.find("COLOR", d, i, db["color"], ib["color"], "цвет обводки", prop="border")


def _norm_grad(s: str) -> str:
    return re.sub(r"\s+", "", s).casefold()


def _has_width(b: Any) -> bool:
    if not isinstance(b, dict):
        return False
    w = b.get("width")
    if isinstance(w, dict):
        return any((_num(v) or 0) > 0 for v in w.values())
    return (_num(w) or 0) > 0


def _eff_radius(r: Any, w: Optional[float], h: Optional[float]) -> Optional[List[float]]:
    """Радиус, который нарисует браузер: каждый угол не больше половины меньшей стороны (50 % и 9999px — один круг)."""
    if r is None:
        vals = [0.0] * 4
    elif _num(r) is not None:
        vals = [float(r)] * 4
    elif isinstance(r, list) and len(r) == 4 and all(_num(x) is not None for x in r):
        vals = [float(x) for x in r]
    else:
        return None
    if w is not None and h is not None:
        cap = min(w, h) / 2
        vals = [min(v, cap) for v in vals]
    return vals


def _dims(n: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    b = _box(n)
    if b:
        return b["w"], b["h"]
    s = n.get("size") or {}
    return _num(s.get("w")), _num(s.get("h"))


def _master_draws(dn: Dict[str, Any]) -> bool:
    """Инстанс библиотеки: поверхность (фон, обводка, тень, радиус), которую макет у него не назвал, рисует мастер компонента —
    «нет заливки» здесь не transparent, а «не знаю»: сравнивать нечего."""
    comp = dn.get("component") or {}
    return dn.get("kind") == "instance" and (comp.get("origin") == "library" or comp.get("paints") is True)


def _library_contract(c: _Ctx, d: str, i: str, anchor: Optional[str]) -> bool:
    """Обе стороны назвали один компонент библиотеки: его внутренности — контракт, правкой экрана не меняются."""
    comp = c.D[d].get("component") or {}
    return c.D[d].get("kind") == "instance" and comp.get("origin") == "library" and anchor in ("component", "plan")


def rule_shape(c: _Ctx) -> None:
    for d, i, _conf, anchor in c.sure:
        dn, inn = c.D[d], c.I[i]
        if dn.get("kind") == "text":
            continue
        ds, ist = dn.get("style") or {}, inn.get("style") or {}
        contract = _library_contract(c, d, i, anchor)
        rad_d = _eff_radius(ds.get("radius"), *_dims(dn))
        rad_i = _eff_radius(ist.get("radius"), *_dims(inn))
        draws = _master_draws(dn)
        if rad_d is not None and rad_i is not None and not (draws and ds.get("radius") is None):
            bad = [k for k in range(4) if abs(rad_d[k] - rad_i[k]) > c.tol["px"]]
            if bad:
                names = ", ".join(CORNERS[k] for k in bad)
                if contract:
                    c.dismiss("SHAPE", d, i, "радиус внутри компонента библиотеки — контракт библиотеки: не чинить обёрткой", prop="radius")
                else:
                    c.find("SHAPE", d, i, rad_d, rad_i, f"радиус: {names}", prop="radius", corners=[CORNERS[k] for k in bad])
        bd, bi = _has_width(ds.get("border")), _has_width(ist.get("border"))
        if bd != bi and not (draws and not bd):
            if contract:
                c.dismiss("SHAPE", d, i, "обводка внутри компонента библиотеки — контракт библиотеки", prop="border")
            else:
                c.find("SHAPE", d, i, "есть" if bd else "нет", "есть" if bi else "нет", "обводка: наличие не совпадает", prop="border")
        sd, si = bool(ds.get("shadow")), bool(ist.get("shadow"))
        if sd != si and not (draws and not sd):
            c.find("SHAPE", d, i, "есть" if sd else "нет", "есть" if si else "нет", "тень: наличие не совпадает", prop="shadow")


def _sides(layout: Any) -> Optional[Dict[str, float]]:
    p = (layout or {}).get("padding") if isinstance(layout, dict) else None
    if p is None:
        return {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0} if isinstance(layout, dict) else None
    return {k: float(_num(p.get(k)) or 0) for k in ("top", "right", "bottom", "left")} if isinstance(p, dict) else None


def rule_spacing(c: _Ctx) -> None:
    for d, i, _conf, anchor in c.sure:
        dn, inn = c.D[d], c.I[i]
        if dn.get("kind") == "text" or not isinstance(dn.get("layout"), dict) or not isinstance(inn.get("layout"), dict):
            continue
        contract = _library_contract(c, d, i, anchor)
        pd, pi = _sides(dn["layout"]), _sides(inn["layout"])
        for side in ("top", "right", "bottom", "left"):
            if pd and pi and abs(pd[side] - pi[side]) > c.tol["px"]:
                if contract:
                    c.dismiss("SPACING", d, i, f"внутренний отступ {side} компонента библиотеки — контракт библиотеки", prop=f"padding.{side}")
                else:
                    c.find("SPACING", d, i, pd[side], pi[side], f"внутренний отступ {side}", prop=f"padding.{side}")
        gd, gi = _num(dn["layout"].get("gap")), _num(inn["layout"].get("gap"))
        if gd is not None and dn["layout"].get("direction") == inn["layout"].get("direction"):
            gi = gi if gi is not None else 0.0
            if abs(gd - gi) > c.tol["px"]:
                if contract:
                    c.dismiss("SPACING", d, i, "промежуток внутри компонента библиотеки — контракт библиотеки", prop="gap")
                elif _measured_gaps_match(c, i, inn["layout"]["direction"], gd):
                    c.dismiss("SPACING", d, i, "промежуток держится margin, а не gap: измеренные боксы равны макету — "
                                                "браузер уже посчитал, не чинить", prop="gap")
                else:
                    c.find("SPACING", d, i, gd, gi, "промежуток между детьми", prop="gap")


def _measured_gaps_match(c: _Ctx, i: str, direction: str, want: float) -> bool:
    kids = [k for k in (c.I[i].get("children") or []) if isinstance(k, dict) and _box(k)]
    if len(kids) < 2:
        return False
    gaps = []
    for a, b in zip(kids, kids[1:]):
        ba, bb = _box(a), _box(b)
        gaps.append(bb["x"] - (ba["x"] + ba["w"]) if direction == "row" else bb["y"] - (ba["y"] + ba["h"]))
    return all(abs(g - want) <= c.tol["px"] for g in gaps)


def _allowed(value: float, tol: Dict[str, float]) -> float:
    """Допуск размера: пропорция от числа макета, не ниже px и не выше потолка sizeRatioMaxPx (но потолок не ниже px)."""
    return max(tol["px"], min(abs(value) * tol["sizeRatio"], tol["sizeRatioMaxPx"]))


def _room(c: _Ctx, nid: str, tree: str) -> Tuple[Optional[float], Optional[float]]:
    """Комната родителя (внутри padding) в одной из сторон: (ширина, высота) или None."""
    nodes, parents = (c.D, c.Dp) if tree == "D" else (c.I, c.Ip)
    par = parents.get(nid)
    if par is None:
        return None, None
    pn = nodes[par]
    w, h = _dims(pn)
    pad = _sides(pn.get("layout")) or {"top": 0, "right": 0, "bottom": 0, "left": 0}
    return (w - pad["left"] - pad["right"] if w is not None else None, h - pad["top"] - pad["bottom"] if h is not None else None)


def rule_size(c: _Ctx) -> None:
    for d, i, _conf, _a in c.sure:
        dn, inn = c.D[d], c.I[i]
        if d == c.root_id:
            c.dismiss("SIZE", d, i, "корневой фрейм: размер задаёт окно, а не страница — не чинить")
            continue
        comp = dn.get("component") or {}
        is_text = dn.get("kind") == "text"
        if dn.get("kind") == "instance" and comp.get("sized") != "own":
            c.dismiss("SIZE", d, i, "размер инстанса — размер мастера, а не заявка экрана (sized не own): свойство не проверяется")
            continue
        size = dn.get("size") or {}
        isz = _dims(inn)
        droom, iroom = _room(c, d, "D"), _room(c, i, "I")
        for axis, k in (("w", 0), ("h", 1)):
            want = size.get(axis)
            got = isz[k]
            if want in ("fill", "hug"):
                c.dismiss("SIZE", d, i, f"{'ширина' if axis == 'w' else 'высота'} выведена, а не заявлена ({want}): не чинить",
                          prop=axis)
                continue
            want = _num(want)
            if want is None or got is None:
                continue
            delta = got - want
            if abs(delta) <= _allowed(want, c.tol):
                continue
            if droom[k] is not None and want > droom[k] + c.tol["px"]:
                c.dismiss("SIZE", d, i, "число макета больше комнаты родителя: макет противоречит себе — не чинить", prop=axis)
                continue
            if is_text and axis == "h":
                if delta < 0:
                    c.find("SIZE", d, i, want, got, "текстовый бокс короче макета", prop=axis)
                else:
                    c.dismiss("SIZE", d, i, "высота текстового слоя выведена по содержимому (line box выше в загруженном шрифте)",
                              prop=axis)
                continue
            if droom[k] is not None and abs(want - droom[k]) <= c.tol["px"] and iroom[k] is not None and abs(got - iroom[k]) <= c.tol["px"]:
                c.dismiss("SIZE", d, i, "ширина равна комнате родителя, и страница тоже занимает её целиком", prop=axis)
                continue
            c.find("SIZE", d, i, want, got, f"{'ширина' if axis == 'w' else 'высота'}", prop=axis)


def rule_position(c: _Ctx) -> None:
    for d, i, _conf, _a in c.sure:
        dn, inn = c.D[d], c.I[i]
        db, ib = _box(dn), _box(inn)
        dp, ip = c.Dp.get(d), c.Ip.get(i)
        if not (db and ib) or dp is None or ip is None or d == c.root_id:
            continue
        pdb, pib = _box(c.D[dp]), _box(c.I[ip])
        if not (pdb and pib):
            continue
        for axis in ("x", "y"):
            want, got = db[axis] - pdb[axis], ib[axis] - pib[axis]
            if abs(want - got) > c.tol["px"]:
                c.find("POSITION", d, i, round(want, 2), round(got, 2), f"позиция по {axis} внутри родителя", prop=axis)


def rule_overflow(c: _Ctx) -> None:
    for d, i, _conf, _a in c.sure:
        fl = c.flags.get(i) or {}
        if fl.get("overflowing") or fl.get("scrolls"):
            if (c.D[d].get("overflow") or "visible") != "visible":
                c.dismiss("OVERFLOW", d, i, "переполнение рисует сам макет — не чинить")
            else:
                c.find("OVERFLOW", d, i, "в размер макета", "переполняет" if fl.get("overflowing") else "прокручивается",
                       "содержимое уехало за рамку, а макет собран в размер")


RULES: List[Tuple[str, Callable[[_Ctx], None]]] = [
    ("structure", rule_structure), ("text", rule_text), ("slots", rule_slots), ("invisible", rule_invisible),
    ("size", rule_size), ("spacing", rule_spacing), ("position", rule_position), ("overflow", rule_overflow),
    ("typography", rule_typography), ("color", rule_color), ("shape", rule_shape),
]


# -------------------------------------------------------------------------------------------------------------- unverified

def unverified(c: _Ctx) -> Dict[str, Any]:
    """Третий выход: что нечем было сравнивать. Читается вслух ВСЕГДА, рядом со счётчиком дефектов."""
    items: List[Dict[str, Any]] = []
    counts = {a: 0 for a in ASPECTS}
    for nid in c.Dorder:
        n = c.D[nid]
        if n.get("kind") != "instance" or n.get("occluded") is True:
            continue
        comp = n.get("component") or {}
        aspects: List[str] = []
        if not comp.get("origin"):                       # определения компонента экспорт не донёс: внутренности невидимы
            aspects.append("content")
        if any(s.get("overridden") is False for s in (comp.get("content") or []) if isinstance(s, dict)):
            aspects.append("copy")
        if comp.get("origin") and comp.get("sized") not in ("own", "master"):
            aspects.append("size")
        if aspects:
            items.append({"id": nid, "name": comp.get("figmaName") or n.get("name"), "aspects": aspects})
            for a in aspects:
                counts[a] += 1
    summary = None
    if items:
        parts = ", ".join(f"{a} {counts[a]}" for a in ASPECTS if counts[a])
        summary = (f"не проверено: {len(items)} элем. ({parts}) — у экспорта нет их определений или слоты стоят на плейсхолдере; "
                   f"дефектов под чистым отчётом может быть больше, чем найдено")
    return {"items": items, "counts": counts, "summary": summary}


# ------------------------------------------------------------------------------------------------------------------ вход

def check(design: Any, impl: Any, matched: Dict[str, Any], tolerances: Optional[Dict[str, Any]] = None,
          flags: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Макет, страница, результат `match.match`, допуски (по умолчанию config.DEFAULT_TOLERANCES), флаги страницы по id
    (`overflowing`/`scrolls`) → {findings, dismissals, unverified, degraded, executed, stats}."""
    d_root = _root_of(design)
    i_root = _root_of(impl) if impl is not None else None
    if not isinstance(matched, dict) or not isinstance(matched.get("pairs"), list):
        raise CheckError("ожидается результат match.match (с полем pairs)")
    tol = dict(CFG.DEFAULT_TOLERANCES)
    for k, v in (tolerances or {}).items():
        if k in tol and _num(v) is not None and v >= 0:
            tol[k] = v
    c = _Ctx(d_root, i_root, matched, tol, flags or {})
    degraded: List[Dict[str, str]] = []
    executed: List[str] = []
    for name, fn in RULES:
        try:
            fn(c)
            executed.append(name)
        except Exception as e:                           # noqa: BLE001 — падение правила не тишина: гейт degraded, остальные идут
            degraded.append({"rule": name, "error": f"{type(e).__name__}: {e}"})
    findings = sorted(c.findings, key=lambda f: (LAYERS.index(f["layer"]), f.get("design") or "", f.get("impl") or "", f["code"],
                                                 f.get("prop") or ""))
    dismissals = sorted(c.dismissals, key=lambda f: (LAYERS.index(f["layer"]), f["design"], f.get("impl") or "", f["code"],
                                                     f.get("prop") or ""))
    by_code: Dict[str, int] = {}
    for f in findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    low = sum(1 for p in c.pairs if p[2] != "high")
    return {
        "findings": findings, "dismissals": dismissals, "unverified": unverified(c), "degraded": degraded,
        "executed": executed, "tolerances": tol,
        "stats": {"pairs": len(c.pairs), "skippedLowConfidence": low, "findings": len(findings), "byCode": dict(sorted(by_code.items())),
                  "dismissals": len(dismissals), "degraded": len(degraded), "nothingToCompare": len(c.pairs) == 0},
    }
