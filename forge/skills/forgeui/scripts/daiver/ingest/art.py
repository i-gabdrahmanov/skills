"""ingest/art.py — векторный арт: группа шейпов → ОДИН узел `art:{slug, nodes}` (спека §7.4, задача 1.6).

Экспорт не несёт геометрию путей: иллюстрация приходит десятками шейпов (векторы, булевы операции,
фигуры в группах). Рисовать её руками из этих шейпов нельзя (INV-B7), поэтому дерево сворачивается:
верхушка рисунка остаётся ОДНИМ узлом с `art`, а строится он потом из `.svg`-файла (`svg_assets.py`,
инструмент daiver_svg_assets). Сворачивание идёт над готовым каноном — после иконного коллапса
инстансов (1.4), до вывода раскладки (1.7): выведенная раскладка не должна «читать» шейпы рисунка.

Образец «Modal» векторных групп на экране не содержит (экран — фрейм и три инстанса; векторы живут в
определениях: четыре иконки — SYMBOL 24×24 с ОДНИМ составным VECTOR «Outline» 11–20 px, плюс скрытая
булева операция). Поэтому правила взяты из текста §7.4 и опираются на виды узлов канона; доказаны
синтетикой и анатомией local-композиций (виртуальные узлы материализации), а реальные векторы образца
проверены на обратное — они остаются иконками-кандидатами. Что образец не проверяет, названо ниже.

Правила:
  • рисунок — поддерево, где ВСЕ узлы frame/vector/rect, нет auto-layout (`layout`) и есть хотя бы один
    vector. Текст, инстанс, иконка (kind=icon) и bitmap (kind=image) внутри — не рисунок. Поддерево из
    одних rect — «контейнер простых прямоугольников» (фон, строится CSS) — не рисунок;
  • коллапс — на ВЕРХНЕЙ арт-ноде: самый высокий узел, чьё поддерево целиком рисунок. Корень никогда
    не склеивается (экран остаётся фреймом), id верхушки сохраняется, дети исчезают, а `art.nodes` —
    сколько узлов канона схлопнуто (с верхушкой);
  • одиночный vector (сама фигура, булева операция или цепочка обёрток-фреймов вокруг ОДНОЙ фигуры):
    не крупнее ICON_MAX по обеим сторонам — иконка-кандидат, остаётся как есть; крупнее хоть по одной
    стороне — рисунок (вордмарк, лого). Размер неизвестен (fill/hug) — не доказано, что это рисунок:
    остаётся как есть, случай считается. Группа из НЕСКОЛЬКИХ шейпов склеивается при любом размере
    (спека порог ICON_MAX отдаёт только одиночному вектору — не проверено данными);
  • FRAME с собственной заливкой, обводкой или тенью — коробка UI, а не группа шейпов: она (и всё
    выше неё) не склеивается, рисунок внутри склеивается сам по себе — уточнение к §7.4 (иначе кнопка-
    иконка «фрейм с фоном + глиф» стала бы картинкой), названо в README задачи; не проверено данными;
  • россыпь шейпов рядом с текстом без общей группы не склеивается: нет узла, который можно назвать
    верхушкой (синтетических групп коллапс не создаёт) — каждый вектор судится сам, как одиночный;
  • `art`-узел минимален: kind=vector, id, name, box/size, occluded, `style.opacity` и `art`. Заливка,
    обводка, тень, цвет и токены верхушки уже «запечены» в .svg: останься они в каноне, verify потребовал
    бы их же от <img> и нашёл бы ложные дефекты. Попадает ли opacity верхушки в экспорт .svg — не
    проверено данными;
  • slug: осмысленное имя узла (не автоимя вроде «Group 12»), иначе имя ближайшего осмысленного
    предка; у инстанса-предка — имя компонента, если собственное имя — автоимя или строка вариантов
    («Steps=OFF»). Имя в slug: латиница и цифры через «-» (camelCase режется), НЕ-ASCII не транслитери-
    руется — символы отбрасываются, а имя без единой латинской буквы или цифры падает в фолбэк `art`.
    Коллизии разводятся суффиксом в порядке документа: `logo`, `logo-2`, `logo-3`. Slug — чистая
    функция дерева (без времени, случайности и порядка словарей): два прогона normalize дают те же
    slug (F5); дерево, уже содержащее art, не меняется при повторном коллапсе.

`collect_vector_art` читает ассеты обратно из схлопнутого дерева (для svg_assets, статуса и verify).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .. import canon as C
from ..names import is_auto_name, parse_variants

ICON_MAX = 64                         # одиночный vector крупнее — рисунок (art), мельче — кандидат в иконки
FALLBACK_SLUG = "art"
SLUG_MAX = 48
SHAPE_KINDS = ("frame", "vector", "rect")
SURFACE_KEYS = ("background", "gradient", "border", "shadow")
KEEP_KEYS = ("id", "name", "box", "size", "occluded")
RESERVED = frozenset(["con", "prn", "aux", "nul"] + [f"com{i}" for i in range(1, 10)]
                     + [f"lpt{i}" for i in range(1, 10)])       # имена устройств Windows: `con.svg` не создать

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]+")


# ── имя → slug ───────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """«Hero Banner», «HeroBanner», «hero_banner» → `hero-banner`; не-ASCII отбрасывается (не транслитерируется);
    пусто — когда в имени нет ни латинской буквы, ни цифры."""
    s = _NOT_ALNUM.sub("-", _CAMEL.sub(r"\1-\2", name)).strip("-").lower()[:SLUG_MAX].strip("-")
    return s + "-art" if s in RESERVED else s


def _label(n: dict) -> Optional[str]:
    """Имя узла для slug: у инстанса с автоимёнем или именем-вариантом («Steps=OFF») — имя компонента."""
    name = n.get("name") if isinstance(n.get("name"), str) else None
    comp = n.get("component")
    if isinstance(comp, dict):
        own_ok = bool(name) and not is_auto_name(name) and parse_variants(name) is None
        fig = comp.get("figmaName")
        if not own_ok and isinstance(fig, str) and fig and fig != "unknown":
            return fig
    return name


def _meaningful(label: Optional[str]) -> bool:
    return isinstance(label, str) and bool(label.strip()) and not is_auto_name(label)


def _base_slug(n: dict, parents: Any) -> Tuple[str, bool]:
    """(slug, фолбэк?). parents — связный список (предок, ссылка на следующего) от ближайшего предка."""
    cur, link = n, parents
    while True:
        label = _label(cur)
        if _meaningful(label):
            s = slugify(label)
            return (s, False) if s else (FALLBACK_SLUG, True)     # осмысленное, но не ASCII — фолбэк, не предок
        if link is None:
            return FALLBACK_SLUG, True
        cur, link = link


def _unique(base: str, used: set) -> Tuple[str, bool]:
    slug, i = base, 2
    while slug in used:
        slug = f"{base}-{i}"
        i += 1
    used.add(slug)
    return slug, slug != base


# ── геометрия ────────────────────────────────────────────────────────────────

def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def node_dims(n: dict) -> Tuple[Optional[float], Optional[float]]:
    """(w, h) узла: числа из `box` (вне потока), иначе из `size`; fill/hug и отсутствие — None. Числа — как
    в каноне (два знака): 64.004 в файле будет 64, и порог ICON_MAX решается по тому, что лежит в файле."""
    out: List[Optional[float]] = []
    for axis in ("w", "h"):
        v = None
        for key in ("box", "size"):
            src = n.get(key)
            if isinstance(src, dict) and _is_num(src.get(axis)):
                v = C.round_num(src[axis], 2)
                break
        out.append(v)
    return out[0], out[1]


# ── анализ дерева ────────────────────────────────────────────────────────────

def _has_surface(n: dict) -> bool:
    st = n.get("style")
    return isinstance(st, dict) and any(k in st for k in SURFACE_KEYS)


def _children(n: dict) -> List[dict]:
    kids = n.get("children")
    return [k for k in kids if isinstance(k, dict)] if isinstance(kids, list) else []


def _analyze(root: dict) -> Dict[int, Tuple[bool, bool, bool]]:
    """id(узла) → (pure, has_vector, single) снизу вверх за O(n).
    pure — всё поддерево может быть частью рисунка; has_vector — в нём есть vector; single — это одна фигура."""
    order = list(C.iter_nodes(root))
    info: Dict[int, Tuple[bool, bool, bool]] = {}
    for n in reversed(order):                                   # потомки раньше предков
        kids = _children(n)
        kind = n.get("kind")
        pure = (kind in SHAPE_KINDS and "layout" not in n and not (kind == "frame" and _has_surface(n))
                and all(info[id(k)][0] for k in kids))
        has_vec = kind == "vector" or any(info[id(k)][1] for k in kids)
        single = kind == "vector" or (kind == "frame" and len(kids) == 1 and info[id(kids[0])][2])
        info[id(n)] = (pure, has_vec, single)
    return info


def _to_art(n: dict, slug: str, nodes: int) -> None:
    keep = {k: n[k] for k in KEEP_KEYS if k in n}
    st = n.get("style")
    opacity = st.get("opacity") if isinstance(st, dict) else None
    n.clear()
    n.update(keep)
    n["kind"] = "vector"
    n["art"] = {"slug": slug, "nodes": nodes}
    if opacity is not None:
        n["style"] = {"opacity": opacity}


def collapse_vector_art(root: dict, icon_max: float = ICON_MAX) -> Dict[str, Any]:
    """Склеивает рисунки дерева НА МЕСТЕ; корень остаётся тем же объектом. Возвращает счётчики (только
    ненулевые): arts, collapsedNodes (убрано узлов канона), iconCandidates (одиночные вектора ≤ icon_max),
    sizeUnknown (одиночный вектор без числового размера), slugFallback, slugSuffixed."""
    report: Counter = Counter()
    used = {n["art"]["slug"] for n in C.iter_nodes(root)
            if isinstance(n.get("art"), dict) and isinstance(n["art"].get("slug"), str)}
    info = _analyze(root)
    stack: List[Tuple[dict, Any]] = [(k, (root, None)) for k in reversed(_children(root))]   # корень не склеивается
    while stack:
        n, parents = stack.pop()
        if isinstance(n.get("art"), dict):
            continue                                            # уже арт: не трогаем (повторный коллапс — тождество)
        pure, has_vec, single = info[id(n)]
        if pure and has_vec:
            w, h = node_dims(n)
            big = (w is not None and w > icon_max) or (h is not None and h > icon_max)
            if single and not big:
                report["iconCandidates" if w is not None and h is not None else "sizeUnknown"] += 1
                continue
            base, fallback = _base_slug(n, parents)
            slug, suffixed = _unique(base, used)
            nodes = sum(1 for _ in C.iter_nodes(n))
            _to_art(n, slug, nodes)
            report["arts"] += 1
            report["collapsedNodes"] += nodes - 1
            report["slugFallback"] += fallback
            report["slugSuffixed"] += suffixed
            continue
        for k in reversed(_children(n)):
            stack.append((k, (n, parents)))
    return {k: v for k, v in sorted(report.items()) if v}


def collect_vector_art(root: dict) -> List[dict]:
    """Ассеты схлопнутого дерева в порядке документа: {slug, id, nodes, name?, w?, h?, occluded?}.
    occluded — арт закрыт поздним непрозрачным слоем (не контент экрана: файл для него не требуется)."""
    out: List[dict] = []
    stack: List[Tuple[dict, bool]] = [(root, False)]
    while stack:
        n, covered = stack.pop()
        covered = covered or n.get("occluded") is True
        art = n.get("art")
        if isinstance(art, dict):
            rec: Dict[str, Any] = {"slug": art["slug"], "id": n.get("id"), "nodes": art.get("nodes")}
            if isinstance(n.get("name"), str):
                rec["name"] = n["name"]
            w, h = node_dims(n)
            if w is not None:
                rec["w"] = w
            if h is not None:
                rec["h"] = h
            if covered:
                rec["occluded"] = True
            out.append(rec)
            continue
        for k in reversed(_children(n)):
            stack.append((k, covered))
    return out
