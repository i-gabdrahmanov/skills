"""reference.py — эталонный рендер: HTML из канона макета и его снимок (спека §14.6; задача 5.1; INV-F17, QG-N15).

Зачем. Картинки дизайна у кадра может не быть, а диффу нужен «макет» в тех же координатах, что и реализация. `render_html` строит HTML из КАНОНА (не из
картинки и не из исходного DSL): раскладка auto-layout → flex, абсолютные узлы → `position:absolute` по боксам, стили, тексты и шрифты слотов инстансов.
`run` пишет `reference.html` в папку итерации и снимает его тем же захватом, что реализацию (`capture.run(target="reference")`, тот же viewport):
`reference.png` + `reference-snapshot.json` — стампы `data-daiver-id` дают отчёту маркеры панели «макет».

Что рендер НЕ выдумывает (эталон — реконструкция, и всё, чего в каноне нет, видно глазом, а не прячется серым боксом):
  • арт — из `.svg` трека (`assets/<slug>.svg`, инлайн data-URI); файла нет — помеченный блок с подписью `art: slug`, а не серый прямоугольник;
  • картинка и значок без арта — помеченный блок с подписью вида узла;
  • инстанс, чей размер канон не атрибутировал (`sized` не own, чисел нет) — ограничен (`data-daiver-bounded`), а не прибит к размеру мастера;
  • слот-плейсхолдер библиотеки (`overridden: false`) — помечен (`data-daiver-placeholder`, пунктирный контур): настоящая копия экрана неизвестна;
  • поверхность библиотечного инстанса (фон, обводка мастера) в каноне не названа — не рисуется; это одна из причин, по которым дифф — сигнал, а не вердикт.
Имя шрифта очищается от кавычек, обратных слэшей и `;`: кавычка внутри кавычек раньше роняла хвост атрибута `style` и уносила весь текст страницы.

Не проверено данными (боевых экранов нет; проверено на синтетике и каноне «Modal»): сходство с настоящим рендером на сложных экранах (grid, wrap, text-transform);
шрифты макета, которых нет в системе, рисуются запасным семейством `sans-serif`; градиенты берутся из канона строкой как есть.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import canon as C
from .. import track as TR
from . import capture as CP

REFERENCE_FILE = "reference.html"
MARK = "outline:1px dashed #d0f"
JUSTIFY = {"start": "flex-start", "center": "center", "end": "flex-end", "space-between": "space-between"}
ALIGN = {"start": "flex-start", "center": "center", "end": "flex-end", "stretch": "stretch", "baseline": "baseline"}
_FAMILY_BAD = re.compile(r"[\"'\\;<>{}]")
_SLUG = re.compile(r"^[a-z0-9-]+$")
READY_WAIT_MS = 5000


class ReferenceError_(ValueError):
    """Эталон построить нельзя — сообщение называет причину."""


def px(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v}px"


def family(name: Any) -> str:
    """Первое семейство без кавычек и разделителей; запасное — sans-serif. Кавычек в значении нет вовсе."""
    first = str(name).split(",")[0] if isinstance(name, str) else ""
    clean = _FAMILY_BAD.sub("", first).strip()
    return f'font-family:"{clean}",sans-serif;' if clean else ""


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def font_css(font: Any, color: Any) -> str:
    out: List[str] = []
    if isinstance(font, dict):
        out.append(family(font.get("family")))
        if _is_num(font.get("size")):
            out.append(f"font-size:{px(font['size'])};")
        if _is_num(font.get("weight")):
            out.append(f"font-weight:{int(font['weight'])};")
        lh = font.get("lineHeight")
        if _is_num(lh):
            out.append(f"line-height:{px(lh)};")
        if _is_num(font.get("letterSpacing")):
            out.append(f"letter-spacing:{px(font['letterSpacing'])};")
        if font.get("align") in ("left", "center", "right", "justify"):
            out.append(f"text-align:{font['align']};")
        if font.get("italic"):
            out.append("font-style:italic;")
        if font.get("textTransform") in ("uppercase", "lowercase", "capitalize"):
            out.append(f"text-transform:{font['textTransform']};")
    if isinstance(color, str) and color:
        out.append(f"color:{_css_value(color)};")
    return "".join(out)


def _css_value(v: str) -> str:
    """Значение цвета/тени/градиента из канона без символов, способных закрыть объявление или атрибут."""
    return re.sub(r"[;{}<>\"\\]", "", str(v))


def style_css(style: Any) -> str:
    if not isinstance(style, dict):
        return ""
    out: List[str] = []
    grad = style.get("gradient")
    if isinstance(grad, str) and "gradient(" in grad:
        out.append(f"background:{_css_value(grad)};")
    elif isinstance(style.get("background"), str):
        out.append(f"background:{_css_value(style['background'])};")
    r = style.get("radius")
    if _is_num(r):
        out.append(f"border-radius:{px(r)};")
    elif isinstance(r, list) and len(r) == 4 and all(_is_num(x) for x in r):
        out.append("border-radius:" + " ".join(px(x) for x in r) + ";")
    b = style.get("border")
    if isinstance(b, dict):
        w = b.get("width")
        col = _css_value(b.get("color") or "currentColor")
        st = _css_value(b.get("style") or "solid")
        if _is_num(w):
            out.append(f"border:{px(w)} {st} {col};")
        elif isinstance(w, dict):
            for side in ("top", "right", "bottom", "left"):
                if _is_num(w.get(side)):
                    out.append(f"border-{side}:{px(w[side])} {st} {col};")
    if isinstance(style.get("shadow"), str):
        out.append(f"box-shadow:{_css_value(style['shadow'])};")
    if _is_num(style.get("opacity")):
        out.append(f"opacity:{style['opacity']};")
    return "".join(out)


def layout_css(layout: Any) -> str:
    if not isinstance(layout, dict):
        return ""
    out = ["display:flex;", f"flex-direction:{layout.get('direction', 'row')};"]
    if _is_num(layout.get("gap")):
        out.append(f"gap:{px(layout['gap'])};")
    if _is_num(layout.get("crossGap")):
        out.append(f"row-gap:{px(layout['crossGap'])};" if layout.get("direction") == "row" else f"column-gap:{px(layout['crossGap'])};")
    pad = layout.get("padding")
    if isinstance(pad, dict):
        out.append("padding:" + " ".join(px(pad.get(k, 0)) for k in ("top", "right", "bottom", "left")) + ";")
    al = layout.get("align") or {}
    if al.get("main") in JUSTIFY:
        out.append(f"justify-content:{JUSTIFY[al['main']]};")
    if al.get("cross") in ALIGN:
        out.append(f"align-items:{ALIGN[al['cross']]};")
    if layout.get("wrap"):
        out.append("flex-wrap:wrap;")
    return "".join(out)


def _size_css(n: Dict[str, Any], parent_dir: Optional[str], absolute: bool, bounded: bool) -> str:
    size = n.get("size") or {}
    box = n.get("box") if isinstance(n.get("box"), dict) else {}
    out: List[str] = []
    for axis, key, along in (("width", "w", "row"), ("height", "h", "column")):
        v = size.get(key)
        if _is_num(v) and not bounded:
            out.append(f"{axis}:{px(v)};")
        elif v == "fill" and not absolute:
            out.append("flex:1 1 0;min-width:0;" if parent_dir == along else "align-self:stretch;")
        elif absolute and _is_num(box.get(key)) and not bounded:
            out.append(f"{axis}:{px(box[key])};")
    if bounded:
        out.append("max-width:100%;min-height:1px;")
    return "".join(out)


def _attr(v: Any) -> str:
    return html.escape(str(v), quote=True)


def _mark_block(label: str, n: Dict[str, Any], extra: str = "") -> str:
    size = n.get("size") or {}
    w = f"width:{px(size['w'])};" if _is_num(size.get("w")) else "width:24px;"
    h = f"height:{px(size['h'])};" if _is_num(size.get("h")) else "height:24px;"
    return (f'<div data-daiver-id="{_attr(n["id"])}" data-daiver-marked="{_attr(label)}" style="box-sizing:border-box;{w}{h}{MARK};'
            f'font:9px/1 sans-serif;color:#d0f;overflow:hidden;{extra}">{html.escape(label)}</div>')


AssetReader = Callable[[str], Optional[bytes]]


def render_html(env: Dict[str, Any], assets: Optional[AssetReader] = None) -> Tuple[str, Dict[str, Any]]:
    """Канон → (HTML, сведения о том, что рендер не мог нарисовать честно: missingArt, markedBlocks, placeholders, bounded). Итеративно."""
    root = env["root"]
    info: Dict[str, Any] = {"missingArt": [], "markedBlocks": 0, "placeholders": 0, "bounded": 0, "nodes": 0}
    rb = root.get("box") if isinstance(root.get("box"), dict) else {}
    rsize = root.get("size") or {}
    rw = rsize.get("w") if _is_num(rsize.get("w")) else rb.get("w")
    rh = rsize.get("h") if _is_num(rsize.get("h")) else rb.get("h")
    parts: List[str] = []
    stack: List[Tuple[str, Any, Any]] = [("open", root, None)]
    while stack:
        what, n, par = stack.pop()
        if what == "close":
            parts.append(n)
            continue
        if n.get("occluded") is True:
            continue
        info["nodes"] += 1
        kind = n.get("kind")
        pdir = (par.get("layout") or {}).get("direction") if isinstance(par, dict) and isinstance(par.get("layout"), dict) else None
        absolute = isinstance(par, dict) and not isinstance(par.get("layout"), dict) and isinstance(n.get("box"), dict) and isinstance(par.get("box"), dict)
        comp = n.get("component") if isinstance(n.get("component"), dict) else None
        art = n.get("art") if isinstance(n.get("art"), dict) else None
        if art is not None or kind in ("icon", "vector"):
            slug = (art or {}).get("slug")
            data = assets(slug) if (assets and isinstance(slug, str) and _SLUG.match(slug)) else None
            if data:
                size = n.get("size") or {}
                dims = (f"width:{px(size['w'])};" if _is_num(size.get("w")) else "") + (f"height:{px(size['h'])};" if _is_num(size.get("h")) else "")
                uri = "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")
                parts.append(f'<img data-daiver-id="{_attr(n["id"])}" src="{uri}" alt="" style="display:block;{dims}{_pos(n, par, absolute)}">')
            else:
                label = f"art: {slug}" if slug else str(kind)
                if slug:
                    info["missingArt"].append(slug)
                info["markedBlocks"] += 1
                parts.append(_mark_block(label, n, _pos(n, par, absolute)))
            continue
        if kind == "image":
            info["markedBlocks"] += 1
            parts.append(_mark_block("image", n, _pos(n, par, absolute)))
            continue
        if kind == "text":
            css = "box-sizing:border-box;white-space:pre-wrap;overflow-wrap:anywhere;" + font_css(n.get("font"), n.get("color")) + \
                _size_css(n, pdir, absolute, False) + _pos(n, par, absolute) + style_css(n.get("style"))
            parts.append(f'<div data-daiver-id="{_attr(n["id"])}" style="{_attr(css)}">{html.escape(str(n.get("text") or ""))}</div>')
            continue
        bounded = bool(comp) and comp.get("sized") != "own" and not (_is_num((n.get("size") or {}).get("w")) and _is_num((n.get("size") or {}).get("h")))
        if bounded:
            info["bounded"] += 1
        css = ["box-sizing:border-box;"]
        css.append(layout_css(n.get("layout")) if isinstance(n.get("layout"), dict) else ("position:relative;" if n.get("children") else ""))
        if n is root:
            css.append("position:relative;")
            if _is_num(rw):
                css.append(f"width:{px(rw)};")
            if _is_num(rh) and not (isinstance(rsize.get("h"), str)):
                css.append(f"min-height:{px(rh)};")
        else:
            css.append(_size_css(n, pdir, absolute, bounded))
            css.append(_pos(n, par, absolute))
        css.append(style_css(n.get("style")))
        slots = _slot_children(n, info) if comp and not n.get("children") else []
        if slots and not isinstance(n.get("layout"), dict):
            css.append("display:flex;flex-direction:column;")
        marks = ' data-daiver-bounded="1"' if bounded else ""
        parts.append(f'<div data-daiver-id="{_attr(n["id"])}"{marks} style="{_attr("".join(css))}">')
        stack.append(("close", "</div>", None))
        for s in reversed(slots):
            stack.append(("close", s, None))
        for k in reversed([k for k in (n.get("children") or []) if isinstance(k, dict)]):
            stack.append(("open", k, n))
    body = "".join(parts)
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>reference</title>'
           '<style>html,body{margin:0;padding:0;background:#fff}</style></head><body>' + body + "</body></html>")
    info["missingArt"] = sorted(set(info["missingArt"]))
    return doc, info


def _pos(n: Dict[str, Any], par: Any, absolute: bool) -> str:
    if not absolute:
        return ""
    b, pb = n["box"], par["box"]
    if not all(_is_num(b.get(k)) for k in ("x", "y")) or not all(_is_num(pb.get(k)) for k in ("x", "y")):
        return ""
    return f"position:absolute;left:{px(b['x'] - pb['x'])};top:{px(b['y'] - pb['y'])};"


def _slot_children(n: Dict[str, Any], info: Dict[str, Any]) -> List[str]:
    """Тексты слотов инстанса (`component.content`, иначе `textProps`) как готовые блоки; плейсхолдеры библиотеки помечены."""
    comp = n["component"]
    out: List[str] = []
    slots = [s for s in (comp.get("content") or []) if isinstance(s, dict) and s.get("text")]
    if not slots:
        slots = [{"id": k, "text": v, "overridden": True} for k, v in sorted((comp.get("textProps") or {}).items()) if isinstance(v, str) and v]
    for s in slots:
        css = "white-space:pre-wrap;overflow-wrap:anywhere;" + font_css(s.get("font"), s.get("color"))
        mark = ""
        if s.get("overridden") is False:
            info["placeholders"] += 1
            mark = ' data-daiver-placeholder="1"'
            css += MARK + ";"
        out.append(f'<div data-daiver-id="{_attr(s.get("id") or n["id"])}"{mark} style="{_attr(css)}">{html.escape(str(s["text"]))}</div>')
    return out


def file_url(path: Path) -> str:
    return Path(path).resolve().as_uri()


def run(root: Any, track: str, frame: Optional[str], iteration_dir: Path) -> Dict[str, Any]:
    """`daiver_render_reference`: пишет `reference.html` и снимает его (reference.png + reference-snapshot.json) в том же viewport."""
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise ReferenceError_(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    f = TR.get_frame(manifest, frame)
    paths = TR.frame_paths(root, manifest, f["name"])
    if not CP.iteration_dir_ok(paths, iteration_dir):
        raise ReferenceError_(f"iterationDir: папка итерации лежит под «verify» (или «baseline») кадра, а не «{iteration_dir}»")
    if not paths["canonical"].is_file():
        raise ReferenceError_("у кадра нет канона: эталон строить не из чего (его строит daiver_normalize)")
    try:
        env = C.read_canonical(paths["canonical"])
    except C.CanonError as e:
        raise ReferenceError_(f"канон кадра не читается: {e}")

    def read_art(slug: str) -> Optional[bytes]:
        p = paths["assets"] / f"{slug}.svg"
        try:
            return p.read_bytes() if p.is_file() else None
        except OSError:
            return None

    doc, info = render_html(env, read_art)
    iteration_dir.mkdir(parents=True, exist_ok=True)
    CP._atomic_write(iteration_dir / REFERENCE_FILE, doc.encode("utf-8"))
    selector = f'[data-daiver-id="{env["root"]["id"].replace(chr(34), "")}"]'
    res = CP.run(root, track, f["name"], file_url(iteration_dir / REFERENCE_FILE), iteration_dir, READY_WAIT_MS, None, selector, None, None, "reference")
    if res.get("blocked"):
        return res
    res["reference"] = {"html": (iteration_dir / REFERENCE_FILE).relative_to(root).as_posix(), **{k: info[k] for k in ("missingArt", "markedBlocks", "placeholders", "bounded")}}
    notes: List[str] = []
    if info["missingArt"]:
        notes.append(f"арты без .svg ({', '.join(info['missingArt'][:5])}) нарисованы помеченными блоками: файл — daiver_svg_assets")
    if info["placeholders"]:
        notes.append(f"слотов-плейсхолдеров библиотеки: {info['placeholders']} — на эталоне они помечены пунктиром, настоящая копия неизвестна")
    if notes:
        res.setdefault("warnings", []).extend(notes)
    res["nextStep"] = ("эталон — реконструкция из канона, а не картинка дизайна: поверхность библиотечных компонентов в нём не нарисована. Если у кадра есть "
                       "картинка дизайна, daiver_visual_diff возьмёт её (она правдивее); иначе — against: reference")
    return res
