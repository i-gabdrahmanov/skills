"""canon.py — каноническая модель макета `visual-requirements.canonical.json` (спека §6).

Единственный контракт между фазами: ingest пишет её, outline/section режут, build строит по
секциям, verify сравнивает с ней измеренную страницу (модели `dom` и `code` — ТА ЖЕ схема, поэтому
один матчер и один чекер обслуживают все три мира). Файл большой (130–150 КБ на крупном экране) и
никем не читается целиком — только через инструменты движка.

Что в модуле:
  • схема и СТРОГАЯ валидация (`validate`): неизвестное поле — ошибка (опечатка адаптера не должна
    тихо уходить в файл), все нарушения собираются одним списком с путями вида
    `root.children[2].layout.gap`;
  • нормализация чисел (`normalize`): round(v·100)/100 как в JS `Math.round` (половина вверх),
    альфа и opacity — до 3 знаков, целые без «.0» — иначе 5 и 5.0 дали бы разные хеши;
  • детерминированная сериализация: минифицированный JSON с сортировкой ключей — одинаковое
    дерево всегда даёт одинаковые байты, хеш и вес не зависят от порядка построения;
  • `subtree_hash` (12 hex; меняется при любой правке поддерева — по нему стухают секции сборки),
    `weight` (минифицированные байты поддерева — по нему outline режет секции) и `weights` — все
    веса за O(n);
  • навигация (`iter_nodes`, `find_node`, `ancestors_of`) и `collect_texts` — контракт «текст не
    теряется ни на какой глубине» (§6.5), с дедупликацией «ребёнок = проп» и исключением occluded.

Ширина обхода: деревья обходятся итеративно — глубокий DOM (до ~56 уровней CDP, но не только)
не упирается в лимит рекурсии Python.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import VERSION

SCHEMA = 1
ROLE = "canonical"
SOURCE_FORMATS = ("pixso", "figma-json-a", "figma-json-b", "figma-html", "dom", "code")
KINDS = ("frame", "text", "instance", "icon", "image", "vector", "rect")
ALPHA_KEYS = ("opacity", "a", "alpha")      # эти числа округляются до 3 знаков, остальные — до 2
MAX_ERRORS = 60


class CanonError(ValueError):
    """Файл или дерево не прошли валидацию; `problems` — полный список нарушений."""

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        shown = "; ".join(self.problems[:5])
        more = f" (+ещё {len(self.problems) - 5})" if len(self.problems) > 5 else ""
        super().__init__(f"каноническая модель невалидна: {shown}{more}")


# ── числа и цвета ────────────────────────────────────────────────────────────

def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def round_num(v: Any, decimals: int = 2) -> Any:
    """round(v·10^d)/10^d с половиной вверх (как JS Math.round); целое — int."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if isinstance(v, int):
        return v
    if not math.isfinite(v):
        return v
    k = 10 ** decimals
    f = math.floor(v * k + 0.5) / k
    return int(f) if f == int(f) and abs(f) < 1e15 else f


def fmt_rgba(r: float, g: float, b: float, a: float = 1) -> str:
    """Канонический цвет: rgba(R, G, B, A) — каналы целые 0..255, альфа до 3 знаков."""
    ch = lambda x: int(min(255, max(0, math.floor(x + 0.5))))  # noqa: E731
    al = round_num(min(1, max(0, a)), 3)
    return f"rgba({ch(r)}, {ch(g)}, {ch(b)}, {al})"


def normalize(o: Any, _key: Optional[str] = None) -> Any:
    """Глубокая копия с нормализованными числами (альфа — 3 знака, остальное — 2)."""
    if isinstance(o, dict):
        return {k: normalize(v, k) for k, v in o.items()}
    if isinstance(o, list):
        return [normalize(v, _key) for v in o]
    if isinstance(o, float):
        return round_num(o, 3 if _key in ALPHA_KEYS else 2)
    return o


# ── валидация ────────────────────────────────────────────────────────────────

class _Errs:
    def __init__(self):
        self.items: List[str] = []
        self.dropped = 0

    def add(self, path: str, msg: str):
        if len(self.items) < MAX_ERRORS:
            self.items.append(f"{path}: {msg}")
        else:
            self.dropped += 1

    def result(self) -> List[str]:
        if self.dropped:
            return self.items + [f"… и ещё {self.dropped} нарушений"]
        return self.items


Check = Callable[[Any, str, _Errs], None]


def _typename(v: Any) -> str:
    return {dict: "объект", list: "список", str: "строка", bool: "булево", int: "число",
            float: "число", type(None): "null"}.get(type(v), type(v).__name__)


def c_num(lo: Optional[float] = None, hi: Optional[float] = None) -> Check:
    def check(v, path, errs):
        if not _is_num(v):
            errs.add(path, f"ожидается число, получено {_typename(v)}")
        elif lo is not None and v < lo:
            errs.add(path, f"{v} меньше допустимого {lo}")
        elif hi is not None and v > hi:
            errs.add(path, f"{v} больше допустимого {hi}")
    return check


def c_int(lo: Optional[int] = None) -> Check:
    def check(v, path, errs):
        whole = isinstance(v, int) or (isinstance(v, float) and math.isfinite(v) and v.is_integer())
        if isinstance(v, bool) or not whole:
            errs.add(path, f"ожидается целое, получено {_typename(v) if not _is_num(v) else v}")
        elif lo is not None and v < lo:
            errs.add(path, f"{v} меньше допустимого {lo}")
    return check


def c_str(nonempty: bool = False) -> Check:
    def check(v, path, errs):
        if not isinstance(v, str):
            errs.add(path, f"ожидается строка, получено {_typename(v)}")
        elif nonempty and not v:
            errs.add(path, "пустая строка")
    return check


def c_bool() -> Check:
    def check(v, path, errs):
        if not isinstance(v, bool):
            errs.add(path, f"ожидается true/false, получено {_typename(v)}")
    return check


def c_enum(*values: str) -> Check:
    def check(v, path, errs):
        if not (isinstance(v, str) and v in values):
            errs.add(path, f"недопустимое значение {v!r}; ожидается одно из: {', '.join(values)}")
    return check


def c_any_of(label: str, *checks: Check) -> Check:
    def check(v, path, errs):
        for c in checks:
            probe = _Errs()
            c(v, path, probe)
            if not probe.items:
                return
        errs.add(path, f"ожидается {label}, получено {v!r}" if not isinstance(v, (dict, list))
                 else f"ожидается {label}, получено {_typename(v)}")
    return check


def c_list(item: Check, exact: Optional[int] = None) -> Check:
    def check(v, path, errs):
        if not isinstance(v, list):
            errs.add(path, f"ожидается список, получено {_typename(v)}")
            return
        if exact is not None and len(v) != exact:
            errs.add(path, f"ожидается ровно {exact} элементов, получено {len(v)}")
        for i, x in enumerate(v):
            item(x, f"{path}[{i}]", errs)
    return check


def c_map(value: Check) -> Check:
    def check(v, path, errs):
        if not isinstance(v, dict):
            errs.add(path, f"ожидается объект, получено {_typename(v)}")
            return
        for k, x in v.items():
            if not isinstance(k, str):
                errs.add(path, f"ключ {k!r} не строка")
                continue
            value(x, f"{path}.{k}", errs)
    return check


def c_obj(fields: Dict[str, Tuple[Check, bool]]) -> Check:
    """Строгий объект: неизвестные ключи — ошибка, обязательные — должны быть."""
    def check(v, path, errs):
        if not isinstance(v, dict):
            errs.add(path, f"ожидается объект, получено {_typename(v)}")
            return
        for k in v:
            if k not in fields:
                errs.add(f"{path}.{k}", "неизвестное поле")
        for k, (chk, required) in fields.items():
            if k in v:
                chk(v[k], f"{path}.{k}", errs)
            elif required:
                errs.add(path, f"нет обязательного поля «{k}»")
    return check


_SIZE_VAL = c_any_of("число ≥ 0 или fill/hug", c_num(0), c_enum("fill", "hug"))
_COLOR = c_str(nonempty=True)
_PADDING = c_obj({k: (c_num(), True) for k in ("top", "right", "bottom", "left")})

C_BOX = c_obj({"x": (c_num(), True), "y": (c_num(), True), "w": (c_num(0), True), "h": (c_num(0), True)})
C_SIZE = c_obj({"w": (_SIZE_VAL, False), "h": (_SIZE_VAL, False)})
C_LAYOUT = c_obj({
    "direction": (c_enum("row", "column"), True),
    "gap": (c_num(), False),
    "crossGap": (c_num(), False),
    "padding": (_PADDING, False),
    "align": (c_obj({"main": (c_enum("start", "center", "end", "space-between"), False),
                     "cross": (c_enum("start", "center", "end", "stretch", "baseline"), False)}), False),
    "wrap": (c_bool(), False),
    "inferred": (c_bool(), False),
    "stated": (c_bool(), False),
})
_BORDER_WIDTH = c_any_of("число ≥ 0 или {top,right,bottom,left}", c_num(0),
                         c_obj({k: (c_num(0), True) for k in ("top", "right", "bottom", "left")}))
C_STYLE = c_obj({
    "background": (_COLOR, False),
    "gradient": (_COLOR, False),
    "radius": (c_any_of("число ≥ 0 или 4 значения углов", c_num(0), c_list(c_num(0), exact=4)), False),
    "border": (c_obj({"width": (_BORDER_WIDTH, True), "color": (_COLOR, False),
                      "style": (c_str(nonempty=True), False),
                      "align": (c_enum("inside", "center", "outside"), False)}), False),
    "shadow": (c_str(nonempty=True), False),
    "opacity": (c_num(0, 1), False),
})
C_FONT = c_obj({
    "family": (c_str(nonempty=True), False),
    "size": (c_num(0), False),
    "weight": (c_int(1), False),
    "lineHeight": (c_any_of("число px или auto", c_num(0), c_enum("auto")), False),
    "align": (c_enum("left", "center", "right", "justify"), False),
    "letterSpacing": (c_num(), False),
    "italic": (c_bool(), False),
    "token": (c_str(nonempty=True), False),
})
C_SLOT = c_obj({
    "id": (c_str(nonempty=True), True),
    "name": (c_str(), False),
    "text": (c_str(), True),
    "overridden": (c_bool(), True),
    "font": (C_FONT, False),
    "color": (_COLOR, False),
})
C_COMPONENT = c_obj({
    "figmaName": (c_str(nonempty=True), True),
    "implName": (c_str(nonempty=True), False),     # имя компонента реализации (fiber / JSX) — для dom/code
    "variants": (c_map(c_str()), False),
    "content": (c_list(C_SLOT), False),
    "textProps": (c_map(c_str()), False),
    "childrenRole": (c_enum("content", "fallback"), False),
    "origin": (c_enum("library", "local", "undecided"), False),
    "paints": (c_bool(), False),
    "sized": (c_enum("own", "master"), False),
    "childrenPath": (c_list(c_str(nonempty=True)), False),
})
C_HINTS = c_obj({
    "w": (c_enum("fill"), False),
    "h": (c_enum("fill"), False),
    "main": (c_enum("space-between"), False),
    "grid": (c_obj({"confidence": (c_enum("medium", "high"), True),
                    "columns": (c_int(1), False)}), False),
})
# Поля узла, кроме `children` (его обходит сам валидатор — итеративно).
NODE_FIELDS: Dict[str, Tuple[Check, bool]] = {
    "id": (c_str(nonempty=True), True),
    "kind": (c_enum(*KINDS), True),
    "name": (c_str(), False),
    "box": (C_BOX, False),
    "layout": (C_LAYOUT, False),
    "size": (C_SIZE, False),
    "style": (C_STYLE, False),
    "text": (c_str(), False),
    "font": (C_FONT, False),
    "color": (_COLOR, False),
    "variants": (c_map(c_str()), False),
    "textProps": (c_map(c_str()), False),
    "tokens": (c_map(c_str(nonempty=True)), False),   # привязанные стили: fill/text/stroke/effect → имя токена
    "component": (C_COMPONENT, False),
    "art": (c_obj({"slug": (c_str(nonempty=True), True), "nodes": (c_int(0), True)}), False),
    "occluded": (c_bool(), False),
    "tag": (c_str(nonempty=True), False),
    "layoutHints": (C_HINTS, False),
}
_NODE_CHECK = c_obj(dict(NODE_FIELDS, children=(lambda v, p, e: None, False)))
C_ENVELOPE_DAIVER = c_obj({
    "version": (c_str(nonempty=True), True),
    "role": (c_enum(ROLE), True),
    "sourceFormat": (c_enum(*SOURCE_FORMATS), True),
    "schema": (c_int(1), False),
})


def validate(env: Any) -> List[str]:
    """Список нарушений конверта и всего дерева; пустой — модель валидна."""
    errs = _Errs()
    if not isinstance(env, dict):
        return ["конверт: ожидается объект"]
    for k in env:
        if k not in ("daiver", "stats", "root"):
            errs.add(k, "неизвестное поле конверта")
    if "daiver" not in env:
        errs.add("конверт", "нет обязательного поля «daiver»")
    else:
        C_ENVELOPE_DAIVER(env["daiver"], "daiver", errs)
    if "stats" in env and not isinstance(env["stats"], dict):
        errs.add("stats", f"ожидается объект, получено {_typename(env['stats'])}")
    if "root" not in env:
        errs.add("конверт", "нет обязательного поля «root»")
        return errs.result()
    _validate_tree(env["root"], "root", errs)
    return errs.result()


def validate_node(node: Any, path: str = "root") -> List[str]:
    errs = _Errs()
    _validate_tree(node, path, errs)
    return errs.result()


def _validate_tree(root: Any, root_path: str, errs: _Errs) -> None:
    ids: Dict[str, str] = {}
    stack: List[Tuple[Any, str]] = [(root, root_path)]
    while stack:
        node, path = stack.pop()
        if not isinstance(node, dict):
            errs.add(path, f"узел: ожидается объект, получено {_typename(node)}")
            continue
        _NODE_CHECK(node, path, errs)
        nid = node.get("id")
        if isinstance(nid, str) and nid:
            if nid in ids:
                errs.add(path, f"id «{nid}» повторяется (первый раз — {ids[nid]}): по id ищут узлы и секции")
            else:
                ids[nid] = path
        if "art" in node and node.get("children"):
            errs.add(path, "узел-арт не может иметь детей: иллюстрация строится из .svg целиком")
        kids = node.get("children")
        if kids is None:
            continue
        if not isinstance(kids, list):
            errs.add(f"{path}.children", f"ожидается список, получено {_typename(kids)}")
            continue
        for i in range(len(kids) - 1, -1, -1):          # в стек в обратном порядке: сообщения — в порядке документа
            stack.append((kids[i], f"{path}.children[{i}]"))


# ── конверт, сериализация, файлы ─────────────────────────────────────────────

def new_envelope(root: dict, source_format: str, stats: Optional[dict] = None) -> dict:
    return {"daiver": {"version": VERSION, "role": ROLE, "sourceFormat": source_format, "schema": SCHEMA},
            "stats": dict(stats or {}), "root": root}


def dumps(o: Any) -> str:
    """Минифицированный JSON с сортировкой ключей: одинаковое содержимое — одинаковые байты."""
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_bytes(node: dict) -> bytes:
    return dumps(node).encode("utf-8")


def write_canonical(path: Path, env: dict) -> dict:
    """Нормализует числа, валидирует и атомарно пишет. Возвращает записанный (нормализованный) конверт."""
    norm = normalize(env)
    problems = validate(norm)
    if problems:
        raise CanonError(problems)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(norm), encoding="utf-8")
    os.replace(str(tmp), str(path))
    return norm


def read_canonical(path: Path) -> dict:
    try:
        env = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise CanonError([f"файл не читается: {e}"])
    except json.JSONDecodeError as e:
        raise CanonError([f"не JSON: {e}"])
    problems = validate(env)
    if problems:
        raise CanonError(problems)
    return env


# ── навигация ────────────────────────────────────────────────────────────────

def iter_nodes(node: dict) -> Iterator[dict]:
    """Узел и все потомки в порядке документа."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        kids = n.get("children")
        if isinstance(kids, list):
            stack.extend(reversed([k for k in kids if isinstance(k, dict)]))


def find_node(root: dict, node_id: str) -> Optional[dict]:
    for n in iter_nodes(root):
        if n.get("id") == node_id:
            return n
    return None


def ancestors_of(root: dict, node_id: str) -> Optional[List[dict]]:
    """Цепочка root → родитель узла (без самого узла); None — узла нет; [] — это корень."""
    stack: List[Tuple[dict, List[dict]]] = [(root, [])]
    while stack:
        n, chain = stack.pop()
        if n.get("id") == node_id:
            return chain
        kids = n.get("children")
        if isinstance(kids, list):
            nxt = chain + [n]
            for k in reversed([k for k in kids if isinstance(k, dict)]):
                stack.append((k, nxt))
    return None


def index_nodes(root: dict) -> Dict[str, dict]:
    return {n["id"]: n for n in iter_nodes(root) if isinstance(n.get("id"), str)}


# ── хеш и вес ────────────────────────────────────────────────────────────────

def subtree_hash(node: dict) -> str:
    """12 hex от минифицированного поддерева (ключи отсортированы). Дерево обязано быть
    нормализованным (так хранится файл): 5 и 5.0 дали бы разные байты."""
    return hashlib.sha256(canonical_bytes(node)).hexdigest()[:12]


def weight(node: dict) -> int:
    """Минифицированные байты поддерева (UTF-8) — мера, по которой outline режет секции."""
    return len(canonical_bytes(node))


def weights(root: dict) -> Dict[str, int]:
    """Вес каждого узла за один проход: id → байты минифицированного поддерева.

    Точная арифметика сериализации, а не оценка: len(own без children) + длина массива детей;
    тест сверяет с `weight()` для каждого узла."""
    order = list(iter_nodes(root))
    out: Dict[int, int] = {}
    for n in reversed(order):                       # потомки раньше предков
        own = {k: v for k, v in n.items() if k != "children"}
        base = len(dumps(own).encode("utf-8"))
        kids = [k for k in (n.get("children") or []) if isinstance(k, dict)]
        if "children" not in n:
            out[id(n)] = base
            continue
        arr = 2 + sum(out[id(k)] for k in kids) + max(0, len(kids) - 1)      # [ … , … ]
        comma = 1 if own else 0
        out[id(n)] = base - 1 + comma + len('"children":') + arr + 1        # убрать «}», добавить поле, вернуть «}»
    return {n["id"]: out[id(n)] for n in order if isinstance(n.get("id"), str)}


def descendants_count(node: dict) -> int:
    return sum(1 for _ in iter_nodes(node)) - 1


# ── тексты ───────────────────────────────────────────────────────────────────

def collect_texts(node: dict) -> List[dict]:
    """Все видимые тексты поддерева в порядке документа — контракт «текст не теряется» (§6.5).

    Запись: {text, id, via: node|content|textProp, overridden: bool|None}.
      • occluded-узел вместе с поддеревом — не контент экрана, исключается;
      • у инстанса `component.content` ЗАМЕНЯЕТ textProps; слоты-плейсхолдеры библиотеки
        (overridden=false) тоже в списке — с флагом, решать, что с ними делать, будет потребитель;
      • дедупликация: текстовый потомок инстанса, равный одному из его собственных текстов, не
        считается дважды — по одному потомку на каждый собственный текст (так distinct ребёнок и
        distinct проп остаются оба, а два одинаковых ребёнка при одном пропе — оба минус один)."""
    out: List[dict] = []
    stack: List[Tuple[dict, Counter]] = [(node, Counter())]
    while stack:
        n, pool = stack.pop()
        if n.get("occluded") is True:
            continue
        comp = n.get("component")
        if isinstance(comp, dict):
            own: List[Tuple[str, str, Optional[bool]]] = []
            if comp.get("content"):
                for slot in comp["content"]:
                    if isinstance(slot, dict) and slot.get("text"):
                        own.append((slot["text"], "content", bool(slot.get("overridden"))))
            elif comp.get("textProps"):
                for v in comp["textProps"].values():
                    if isinstance(v, str) and v:
                        own.append((v, "textProp", None))
            for text, via, ov in own:
                out.append({"text": text, "id": n.get("id"), "via": via, "overridden": ov})
            if own:
                pool = Counter(t for t, _, _ in own)       # новая область дедупликации
                # вложенный инстанс БЕЗ собственных текстов область не закрывает: его анатомия
                # по-прежнему дублирует тексты внешнего
        text = n.get("text")
        if isinstance(text, str) and text:
            if pool.get(text, 0) > 0:
                pool[text] -= 1                            # это дубль собственного текста инстанса
            else:
                out.append({"text": text, "id": n.get("id"), "via": "node", "overridden": None})
        kids = n.get("children")
        if isinstance(kids, list):
            for k in reversed([k for k in kids if isinstance(k, dict)]):
                stack.append((k, pool))
    return out


def texts(node: dict) -> List[str]:
    return [t["text"] for t in collect_texts(node)]
