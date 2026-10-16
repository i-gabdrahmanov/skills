"""ingest/pixso.py — Pixso DSL → каноническая модель: структура и геометрия (спека §7.1–7.2, задача 1.3).

Вход — сырой или слимнутый DSL (формат один). Здесь строится ДЕРЕВО ЭКРАНА; содержимое инстансов
(тексты слотов, анатомия local-композиций, origin/sized/paints) даёт материализация (`materialize.py`,
1.4), краски и типографику — 1.5, векторный арт — 1.6, вывод раскладки для макетов без auto-layout — 1.7.

Правила, проверенные по образцу «Modal» (docs/daiver/dsl.json), а не угаданные:
  • узел в потоке раскладки — ребёнок контейнера (`autoLayout.stackMode` HORIZONTAL|VERTICAL), у
    которого нет `autoLayoutItemAbsolutePos`; контейнер даёт `layout` (direction из stackMode,
    gap — autoLayoutItemSpacing, padding — autoLayoutPadding*, align.main — autoLayoutPrimaryAlign
    (flex-start|center|flex-end|space-between), align.cross — autoLayoutCounterAlign); поле
    autoLayoutAlignType выравниванием по главной оси НЕ является — проверено на геометрии;
  • намерение размера по оси — коды autoLayoutWidthResize/HeightResize: 0 фиксированный,
    1 по содержимому (hug), 2 на всю доступную (fill, только в потоке); у контейнера hug также
    следует из stackPrimarySizing/stackCounterSizing = RESIZE_TO_FIT; у текста «по содержимому»
    задаёт textAutoResize (WIDTH_AND_HEIGHT — обе оси, HEIGHT — высота). Число DSL у hug и fill —
    размер мастера или вычисленный, ему верить нельзя, поэтому намерение пишется строкой;
  • `box` — только у узлов ВНЕ потока раскладки, в координатах экрана (корень в (0,0)); внутри
    потока позицию выражает раскладка;
  • дети упорядочены по позиции вдоль главной оси потока (затем по другой), равные — в порядке
    рисования (дерево Pixso «topmost first»: последний ребёнок рисуется первым); абсолютные дети
    контейнера — после потока;
  • `visible: false` выбрасывается вместе с поддеревом; корни экрана мельче 5 % крупнейшего (иконка
    рядом с кадром) выбрасываются с сообщением, несколько настоящих корней оборачиваются в
    синтетический фрейм `inferred:roots:<id>`;
  • окклюзия: узел закрыт, если выше по порядку рисования у того же родителя лежит FRAME/RECTANGLE с
    СПЛОШНОЙ непрозрачной заливкой (альфа 1, opacity 1, обычный режим), чей бокс целиком накрывает бокс
    узла; полупрозрачный фон (backdrop) ничего не доказывает. Узел остаётся в дереве с `occluded`;
  • размеры инстанса — настоящие (запись корневого пути оверрайдов), а не размер мастера из узла;
  • icon-only инстанс (не local, без видимого текста, с векторной анатомией, без поверхности, ≤ 64×64)
    становится kind=icon с сохранённым `component`;
  • краски, типографика и токены — `style.py` (цвет `rgba(…)`, радиус/обводка/тень в px и CSS, вес шрифта
    числом, привязанные стили → `tokens`/`font.token`); image-fill делает узел kind=image;
  • векторный арт — `art.py`: над готовым деревом (после иконного коллапса инстансов) группа шейпов
    склеивается в один узел `art:{slug, nodes}`; `stats.nodesOut` считает узлы ПОСЛЕ склейки;
  • вывод раскладки — `layout.py`, последним проходом над готовым деревом и только по `infer_layout=True`
    (`normalize_frame` берёт его из `layout.infer` конфига); адаптер отдаёт ему экранные прямоугольники узлов в
    потоке (`Converter.rects`: у них `box` нет), `stats.nodesOut` включает синтетические группы.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .. import canon as C
from ..names import is_auto_name, parse_variants
from ..pixso import dsl as D
from . import layout as LY
from . import paint as PT
from .art import ICON_MAX, collapse_vector_art
from .materialize import Materialized, Materializer
from .style import Styler

STRAY_ROOT_SHARE = 0.05
COVER_EPS = 0.5
OCCLUDER_TYPES = ("FRAME", "RECTANGLE")
LAYOUT_MODES = {"HORIZONTAL": "row", "VERTICAL": "column"}
DIRECTION_CODES = {1: "row", 2: "column"}
ALIGN_MAIN = {"flex-start": "start", "center": "center", "flex-end": "end", "space-between": "space-between"}
ALIGN_CROSS = {"flex-start": "start", "center": "center", "flex-end": "end", "stretch": "stretch",
               "baseline": "baseline"}
KIND_BY_TYPE = {
    "FRAME": "frame", "GROUP": "frame", "SECTION": "frame", "SYMBOL": "frame", "COMPONENT": "frame",
    "COMPONENT_SET": "frame", "INSTANCE": "instance", "PARAGRAPH": "text", "TEXT": "text",
    "RECTANGLE": "rect", "ELLIPSE": "rect", "LINE": "rect", "VECTOR": "vector",
    "BOOLEAN_OPERATION": "vector", "STAR": "vector", "POLYGON": "vector",
}
TEXT_TYPES = ("PARAGRAPH", "TEXT")


def _num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Converter:
    def __init__(self, body: dict):
        self.body = body
        self.owner = D.owner_index(body)
        self.tops = {t.get("guid"): t for t in body.get(D.DEFS_KEY) or [] if isinstance(t, dict)}
        self.stats: Counter = Counter()
        self.warnings: List[str] = []
        self.rects: Dict[str, Tuple[float, float, float, float]] = {}   # узлы в потоке: у них `box` нет (для 1.7)
        self._warned: set = set()
        self.styler = Styler(body, self.warn)
        self.mat = Materializer(self)

    # ── служебное ────────────────────────────────────────────────────────────
    def warn(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.warnings.append(msg)

    def _definition(self, inst: dict) -> Optional[dict]:
        """Верхнеуровневое определение инстанса: по группе состояний, иначе по варианту."""
        for k in ("mainStateGroup", "mainComponent"):
            ref = inst.get(k)
            top = self.owner.get(ref) if isinstance(ref, str) else None
            if top in self.tops:
                return self.tops[top]
        return None

    # ── раскладка и размеры ──────────────────────────────────────────────────
    @staticmethod
    def _direction(src: dict) -> Optional[str]:
        """Направление раскладки контейнера: stackMode, а при его отсутствии — код направления."""
        al = src.get("autoLayout")
        if not isinstance(al, dict):
            return None
        direction = LAYOUT_MODES.get(al.get("stackMode"))
        if direction is None and src.get("childNode"):
            direction = DIRECTION_CODES.get(al.get("autoLayoutDirection"))
        return direction

    def _layout(self, src: dict) -> Optional[dict]:
        al = src.get("autoLayout")
        direction = self._direction(src)
        if direction is None:
            return None
        if LAYOUT_MODES.get(al.get("stackMode")) is None:
            self.stats["layoutFromDirectionCode"] += 1
        lay: Dict[str, Any] = {"direction": direction, "stated": True}
        gap = _num(al.get("autoLayoutItemSpacing"))
        if gap is not None:
            lay["gap"] = gap
        pad = {side.lower(): _num(al.get("autoLayoutPadding" + side)) or 0
               for side in ("Top", "Right", "Bottom", "Left")}
        if any(pad.values()):
            lay["padding"] = pad
        align: Dict[str, str] = {}
        for field, table, axis in (("autoLayoutPrimaryAlign", ALIGN_MAIN, "main"),
                                   ("autoLayoutCounterAlign", ALIGN_CROSS, "cross")):
            raw = al.get(field)
            if raw is None:
                continue
            if raw in table:
                align[axis] = table[raw]
            else:
                self.warn(f"align:{field}:{raw}", f"{field}: значение «{raw}» незнакомо — выравнивание не "
                                                  f"выведено (в образце встречались {', '.join(sorted(table))})")
        if align:
            lay["align"] = align
        return lay

    def _sizes(self, n: dict, in_flow: bool) -> Dict[str, Any]:
        al = n.get("autoLayout") if isinstance(n.get("autoLayout"), dict) else {}
        mode = al.get("stackMode")
        container = mode in LAYOUT_MODES
        sizing: Dict[str, Optional[str]] = {}
        if container:
            prim, counter = al.get("stackPrimarySizing"), al.get("stackCounterSizing")
            sizing = {"w": prim, "h": counter} if mode == "HORIZONTAL" else {"w": counter, "h": prim}
        auto = n.get("textAutoResize")
        out: Dict[str, Any] = {}
        for axis, dim, code_key in (("w", "width", "autoLayoutWidthResize"), ("h", "height", "autoLayoutHeightResize")):
            code = al.get(code_key)
            if n.get("type") in TEXT_TYPES:
                hug = auto == "WIDTH_AND_HEIGHT" or (axis == "h" and auto == "HEIGHT")
                if code == 2 and in_flow:
                    value: Any = "fill"
                elif hug:
                    value = "hug"
                else:
                    value = _num(n.get(dim))
            elif code == 2 and in_flow:
                value = "fill"
            elif code == 1 or sizing.get(axis) == "RESIZE_TO_FIT":
                value = "hug"
            else:
                value = _num(n.get(dim))
            if value is not None:
                out[axis] = value
        return out

    # ── порядок детей ────────────────────────────────────────────────────────
    def _ordered(self, src: dict, mode: Optional[str], drop_hidden: bool = True) -> List[Tuple[dict, bool]]:
        """Дети узла как (узел, в потоке раскладки), в порядке канона. drop_hidden=False оставляет
        скрытых (их видимость решает материализатор) и не трогает счётчики."""
        kids = [k for k in src.get("childNode") or [] if isinstance(k, dict)]
        draw = {id(k): len(kids) - 1 - i for i, k in enumerate(kids)}   # последний рисуется первым
        keep: List[dict] = []
        for k in kids:
            if drop_hidden and k.get("visible") is False:
                self.stats["hiddenDropped"] += 1
                self.stats["hiddenNodesDropped"] += sum(1 for _ in D.walk(k))
            else:
                keep.append(k)

        def absolute(k: dict) -> bool:
            al = k.get("autoLayout")
            return bool(isinstance(al, dict) and al.get("autoLayoutItemAbsolutePos"))

        left = lambda k: _num(k.get("left")) or 0   # noqa: E731
        top = lambda k: _num(k.get("top")) or 0     # noqa: E731
        if mode is None:
            flow: List[dict] = []
            free = keep
        else:
            flow = [k for k in keep if not absolute(k)]
            free = [k for k in keep if absolute(k)]
        if mode == "row":
            flow.sort(key=lambda k: (left(k), top(k), draw[id(k)]))
        elif mode == "column":
            flow.sort(key=lambda k: (top(k), left(k), draw[id(k)]))
        free = sorted(free, key=lambda k: (top(k), left(k), draw[id(k)]))
        return [(k, True) for k in flow] + [(k, False) for k in free]

    @staticmethod
    def _rect(k: dict) -> Optional[Tuple[float, float, float, float]]:
        l, t, w, h = (_num(k.get(x)) for x in ("left", "top", "width", "height"))
        if None in (l, t, w, h) or w <= 0 or h <= 0:
            return None
        return l, t, l + w, t + h

    def _occluded(self, src: dict) -> set:
        """id() детей, закрытых более поздним (рисуемым выше) непрозрачным сиблингом. Дерево Pixso
        «topmost first»: ребёнок с меньшим индексом в массиве рисуется позже и лежит выше."""
        arr = [k for k in src.get("childNode") or [] if isinstance(k, dict) and k.get("visible") is not False]
        covers = []
        for i, k in enumerate(arr):
            r = self._rect(k)
            if r is not None and k.get("type") in OCCLUDER_TYPES and PT.is_opaque_fill(k):
                covers.append((i, r))
        out: set = set()
        if not covers:
            return out
        for i, k in enumerate(arr):
            r = self._rect(k)
            if r is None:
                continue
            for j, c in covers:
                if j < i and c[0] <= r[0] + COVER_EPS and c[1] <= r[1] + COVER_EPS \
                        and c[2] >= r[2] - COVER_EPS and c[3] >= r[3] - COVER_EPS:
                    out.add(id(k))
                    break
        return out

    # ── узел ─────────────────────────────────────────────────────────────────
    def _node(self, src: dict, in_flow: bool, ax: float, ay: float,
              as_frame: bool = False) -> Tuple[dict, Optional[str], dict]:
        """(узел канона, направление его раскладки, источник детей). Источник детей — сам src, а у
        local-инстанса без собственных детей — его виртуальная анатомия."""
        t = src.get("type")
        kind = "frame" if as_frame else KIND_BY_TYPE.get(t)
        if kind is None:
            kind = "frame"
            self.warn(f"type:{t}", f"тип узла «{t}» незнаком — принят за frame (дети сохранены)")
        if kind in ("frame", "rect", "vector") and self.styler.is_image(src):
            kind = "image"                              # image-fill — картинка, а не background-color
            self.stats["imageFills"] += 1
        comp: Optional[dict] = None
        mat: Optional[Materialized] = None
        kids_src = src
        if kind == "instance":
            comp, mat = self._component(src)
            if self._is_icon(comp, mat, src):
                kind = "icon"
                self.stats["iconsCollapsed"] += 1
        elif as_frame and t == "INSTANCE":
            # корень-инстанс с автоименем: фрейм, а анатомию (и тексты) берём из мастера как у local
            mat = self.mat.materialize(src, force_local=True)
        if mat is not None and mat.children is not None and not src.get("childNode"):
            kids_src = dict(src, childNode=mat.children)
        node: Dict[str, Any] = {"id": src["guid"], "kind": kind}
        name = src.get("name")
        if isinstance(name, str):
            node["name"] = name
            if is_auto_name(name):
                self.stats["autoNamed"] += 1
        if t in TEXT_TYPES and isinstance(src.get("nodeText"), str) and src["nodeText"]:
            node["text"] = src["nodeText"]
        geom = mat.geometry if mat is not None else {}
        size = self._sizes(src, in_flow)
        for axis, dim in (("w", "width"), ("h", "height")):
            if dim in geom and isinstance(size.get(axis), (int, float)):
                size[axis] = geom[dim]                 # настоящий размер инстанса, а не размер мастера
        if size:
            node["size"] = size
        w, h = geom.get("width", _num(src.get("width"))), geom.get("height", _num(src.get("height")))
        if not in_flow and w is not None and h is not None:
            node["box"] = {"x": ax, "y": ay, "w": w, "h": h}
        elif in_flow and w is not None and h is not None:
            self.rects[src["guid"]] = (ax, ay, w, h)    # позицию в канон не пишем, но вывод раскладки её знает
        lay = self._layout(src) if kind in ("frame", "instance") else None
        if lay:
            node["layout"] = lay
        if comp is not None:
            node["component"] = comp
        if kind != "icon":                              # у иконки — только component (вид рисует сама иконка)
            look = self.styler.look(src, kind)
            for field, value in (("style", look.style), ("font", look.font), ("color", look.color),
                                 ("tokens", look.tokens)):
                if value:
                    node[field] = value
        self.stats["nodesOut"] += 1
        return node, (lay["direction"] if lay else None), kids_src

    def _component(self, src: dict) -> Tuple[dict, Optional[Materialized]]:
        top = self._definition(src)
        variants: Optional[Dict[str, str]] = None
        refs = (src.get("propRefMap") or {}).get("variant") if isinstance(src.get("propRefMap"), dict) else None
        if isinstance(refs, list):
            pairs = {r["name"]: r["value"] for r in refs
                     if isinstance(r, dict) and isinstance(r.get("name"), str) and isinstance(r.get("value"), str)}
            variants = pairs or None
        if variants is None:
            variants = parse_variants(src.get("name"))
        self.stats["instances"] += 1
        if top is None:
            self.stats["instancesUnresolved"] += 1
            self.warn("instance-unresolved", "инстансы без определения в экспорте идут как fallback-разметка "
                                             "(их внутренности экспорт не донёс)")
            has_main = isinstance(src.get("mainComponent"), str) and bool(src.get("mainComponent"))
            self.mat.diag("MISSING_COMPONENT_DEFINITION" if has_main else "MISSING_MAIN_COMPONENT",
                          {"id": src.get("guid"), "name": src.get("name")})
            comp: Dict[str, Any] = {"figmaName": src.get("name") or "unknown", "childrenRole": "fallback"}
            if variants:
                comp["variants"] = variants
            return comp, None
        sg = src.get("mainStateGroup")
        if isinstance(sg, str) and sg and sg not in self.owner:
            # ключ группы состояний есть, а группы в экспорте нет (у обычного компонента ключа нет вовсе)
            self.mat.diag("MISSING_COMPONENT_BASE", {"id": src.get("guid"), "name": src.get("name")})
        mat = self.mat.materialize(src)
        comp = {"figmaName": top.get("name") or src.get("name") or "unknown"}
        if variants:
            comp["variants"] = variants
        comp["origin"] = mat.origin
        comp["childrenRole"] = "fallback" if mat.origin == "local" else "content"
        if mat.content:
            comp["content"] = mat.content
        elif mat.text_props:
            comp["textProps"] = mat.text_props
        if mat.paints:
            comp["paints"] = True
        if mat.sized:
            comp["sized"] = mat.sized
        if mat.children:
            comp["childrenPath"] = [c["guid"].split(";", 1)[1] for c in mat.children]
        if mat.origin != "local" and not (mat.content or mat.text_props or mat.has_art or mat.paints):
            self.stats["emptyInstances"] += 1
        return comp, mat

    def _is_icon(self, comp: Optional[dict], mat: Optional[Materialized], src: dict) -> bool:
        """Инстанс без подписи, с векторной анатомией и без поверхности, не крупнее ICON_MAX."""
        if comp is None or mat is None or not mat.resolved or mat.origin == "local":
            return False
        if comp.get("content") or comp.get("textProps") or comp.get("paints") or not mat.has_art:
            return False
        w = mat.geometry.get("width", _num(src.get("width")))
        h = mat.geometry.get("height", _num(src.get("height")))
        return w is not None and h is not None and w <= ICON_MAX and h <= ICON_MAX

    def _tree(self, root: dict, ox: float, oy: float, as_frame: bool = False) -> dict:
        out: Optional[dict] = None
        stack: List[tuple] = [(root, None, False, ox, oy, as_frame, False)]
        while stack:
            src, sink, in_flow, ax, ay, af, covered = stack.pop()
            node, mode, kids_src = self._node(src, in_flow, ax, ay, af)
            if covered:
                node["occluded"] = True
                self.stats["occluded"] += 1
            if sink is None:
                out = node
            else:
                sink.append(node)
            kids = self._ordered(kids_src, mode) if node["kind"] != "icon" else []
            if kids:
                bucket = node.setdefault("children", [])
                hidden_by = self._occluded(kids_src)
                for child, flow in reversed(kids):
                    stack.append((child, bucket, flow, ax + (_num(child.get("left")) or 0),
                                  ay + (_num(child.get("top")) or 0), False, id(child) in hidden_by))
        assert out is not None
        return out

    # ── экран ────────────────────────────────────────────────────────────────
    def convert(self) -> dict:
        roots = [r for r in self.body.get(D.SCREEN_KEY) or [] if isinstance(r, dict) and "guid" in r]
        self.stats["nodesIn"] = sum(1 for r in roots for _ in D.walk(r))
        visible = [r for r in roots if r.get("visible") is not False]
        for r in roots:
            if r.get("visible") is False:
                self.stats["hiddenDropped"] += 1
                self.stats["hiddenNodesDropped"] += sum(1 for _ in D.walk(r))
        area = lambda r: (_num(r.get("width")) or 0) * (_num(r.get("height")) or 0)  # noqa: E731
        biggest = max((area(r) for r in visible), default=0)
        kept = [r for r in visible if not (biggest > 0 and area(r) < STRAY_ROOT_SHARE * biggest)]
        for r in visible:
            if r not in kept:
                self.stats["strayRootsDropped"] += 1
                self.warnings.append(f"корень «{r.get('name')}» ({r.get('guid')}) мельче {int(STRAY_ROOT_SHARE * 100)} % "
                                     f"крупнейшего — посторонний объект рядом с кадром, выброшен")
        self.stats["screenRoots"] = len(kept)
        if not kept:
            raise ValueError("в экспорте нет ни одного видимого корня экрана")
        if len(kept) == 1:
            root = kept[0]
            as_frame = root.get("type") == "INSTANCE" and is_auto_name(root.get("name"))
            if as_frame:
                self.stats["genericRootFallback"] += 1
            return self._tree(root, 0, 0, as_frame)
        # несколько настоящих корней: синтетический фрейм-обёртка, позиции — внутри него
        min_x = min(_num(r.get("left")) or 0 for r in kept)
        min_y = min(_num(r.get("top")) or 0 for r in kept)
        max_x = max((_num(r.get("left")) or 0) + (_num(r.get("width")) or 0) for r in kept)
        max_y = max((_num(r.get("top")) or 0) + (_num(r.get("height")) or 0) for r in kept)
        children = [self._tree(r, (_num(r.get("left")) or 0) - min_x, (_num(r.get("top")) or 0) - min_y)
                    for r in sorted(kept, key=lambda r: (_num(r.get("top")) or 0, _num(r.get("left")) or 0))]
        self.warnings.append(f"в экспорте {len(kept)} корня экрана — это {len(kept)} отдельных кадра; тяните "
                             f"каждый отдельным pull'ом: один канон на несколько экранов разбирать неудобно")
        self.stats["nodesOut"] += 1
        return {"id": f"inferred:roots:{kept[0]['guid']}", "kind": "frame", "name": "roots",
                "box": {"x": 0, "y": 0, "w": max_x - min_x, "h": max_y - min_y}, "children": children}


def normalize_doc(doc: Any, source_sha256: Optional[str] = None, infer_layout: bool = False) -> dict:
    """Канон (конверт) по Pixso-документу. D.DslError — не Pixso DSL; ValueError — пустой экран.

    infer_layout — вывод раскладки для макета без auto-layout (1.7). По умолчанию выключен: включает вызывающий,
    `normalize_frame` — по ключу `layout.infer` конфига (там он on по умолчанию)."""
    body, _ = D.unwrap(doc)
    conv = Converter(body)
    root = conv.convert()
    art = collapse_vector_art(root)                  # после иконного коллапса инстансов (1.4), до раскладки (1.7)
    conv.stats["nodesOut"] -= art.get("collapsedNodes", 0)
    layout = LY.infer_layout(root, conv.rects) if infer_layout else {}      # последним: над готовым деревом
    conv.stats["nodesOut"] += layout.get("groups", 0)
    if art.get("slugFallback"):
        conv.warnings.append(f"общий slug «art» у рисунков: {art['slugFallback']} — у узла и его предков нет "
                             f"осмысленного ASCII-имени (не-ASCII не транслитерируется); список с id и именами даёт "
                             f"daiver_svg_assets, файлы кладутся под эти slug")
    stats: Dict[str, Any] = dict(sorted(conv.stats.items()))
    if art:
        stats["art"] = art
    if layout:
        stats["layout"] = layout
    stats["materialization"] = conv.mat.report()
    stats["styles"] = conv.styler.report()
    stats["warnings"] = conv.warnings
    if source_sha256:
        stats["sourceSha256"] = source_sha256
    return C.new_envelope(root, "pixso", stats)
