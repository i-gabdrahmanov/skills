"""ingest/layout.py — вывод раскладки для макета без auto-layout (спека §8, задача 1.7).

Макет, нарисованный без auto-layout, приходит абсолютными x/y: у каждого ребёнка свой `box`. Скопировать их —
значит получить модуль из абсолютных div, который пиксельный гейт пропустит (геометрия воспроизведена), а жить
с ним нельзя. Поэтому над ГОТОВЫМ каноном (после материализации инстансов 1.4, красок 1.5 и коллапса арта 1.6)
выводится раскладка контейнеров, и принимается она, ТОЛЬКО если реплей ставит каждого ребёнка на его бокс
±LAYOUT_TOLERANCE. Иначе контейнер остаётся с абсолютными детьми и попадает в `stats.layout.unproven` с причиной.

Модуль не знает источника (понадобится и Figma-адаптерам, X.2): ему нужен канон и экранные прямоугольники узлов,
стоящих в потоке чужой раскладки (`rects`: у них `box` нет, а контейнеру без раскладки внутри такого потока без
своего прямоугольника отступы не вычислить). Включается ключом `layout.infer` (daiver/config.py, по умолчанию on;
off/false — модуль не вызывается). Картинка кадра в выводе не участвует: движок детерминирован, значение — только
измерение (INV-F4).

Данные образца. «Modal» целиком на auto-layout (корень и три инстанса с объявленной раскладкой): контейнеров без
`layout` с детьми в нём нет, поэтому модуль не меняет ничего — канон побайтно прежний (пин-тест). Реального
полноэкранного кадра без auto-layout у нас нет: правила взяты из текста §8.1–8.2 и доказаны синтетикой по каждому
правилу и каждому отказу (test_layout_inference.py), а НЕ выведены по данным. Допущения, которых образец не
проверяет, перечислены в конце.

Контейнер — рамка (`kind=frame`) без `layout`, не закрытая поздним слоем (`occluded`), не вложенная в чёрный ящик
библиотечного инстанса (`childrenRole=content`) и не обёртка адаптера (`inferred:…`). Рамка со своей раскладкой
(`stated` или выведенная) не трогается вместе со своими pinned-детьми — и молчит: в unproven её нет.

Правила (§8.1):
  1. Порядок детей — по ПОЗИЦИИ, не по порядку массива. Дети делятся на полосы по вертикали (пересечение полос
     больше допуска = одна строка): одна полоса — row, все полосы из одного ребёнка — column, иначе решётка (п. 6).
  2. Cross-ось. Общая верхняя кромка и одинаковая высота — align не пишется («проще читать»); верхняя кромка
     общая, а высоты разные — явный `start` (иначе умолчание CSS stretch растянуло бы низкого); общая нижняя
     кромка — `end`; `center` — только когда общий центр совпадает с центром контейнера (именно центрирование
     воспроизводит рамку), при совпадении нескольких побеждает простейшее: start, затем end, затем center.
     Ни одно из трёх — отказ `cross`.
  3. Одиночный ребёнок → padding родителя (все четыре стороны — измеренные отступы до кромок контейнера), column
     без align. Отступы справа и снизу (у рядов и колонок — замыкающие) — остаток до кромки, а не намерение
     дизайнера: размер контейнера числом остаётся в его `size`.
  4. Ритм допускает округление: gap — среднее всех зазоров (10 и 11 px — один gap); сколько набежало дрейфа,
     решает реплей. Настоящий разрыв ритм не сводит.
  5. Две величины зазора → синтетические группы. Зазоры кластеризуются по допуску; самый большой кластер режет
     последовательность на отрезки, отрезки из ≥ 2 детей становятся группой-обёрткой (kind=frame, name «Group»,
     size hug×hug, `layout.inferred`, id `inferred:<члены>`). Резать можно, только если разделительный зазор
     БОЛЬШЕ группирующего более чем в GAP_JUMP раз (и больше него на допуск — иначе это шум округления);
     третья величина вкладывается: внутри отрезка та же процедура. Бокс группы — объединение членов, поэтому
     её padding выходит нулевым.
  6. Решётки. Строки решётки с одинаковым зазором в обе стороны → ОДНА wrapping row (`wrap: true`, один gap; все
     строки, кроме последней, из ≥ 2 детей — короткая последняя строка тоже заворачивается; ширина содержимого =
     самая широкая строка, остаток уходит в padding справа); разные зазоры → вложенные ряды: column из строк-групп.
     Колонка полновесных блоков (в каждой строке один ребёнок) — column, не перенос.
  7. Отказы (в unproven при ≥ 2 детей): `overlap` — перекрытия; `outside` — ребёнок начинается вне контейнера;
     `overflow` — контент выпирает за край; `cross` — поперечные позиции не start/center/end; `rhythm` — зазоры не
     сводятся к одному ритму и не делятся на группы; `replay` — реплей не сошёлся; `no-box` — у ребёнка нет бокса;
     `no-geometry` — у контейнера нет прямоугольника. Одиночный ребёнок, который не вписался, — оверлей, а не
     раскладка: в unproven не попадает.

Доказательство (§8.2). `_prove` собирает ПРОДУКТ — layout контейнера и порядок детей с группами, как они лягут в
канон, — и прогоняет мини-flexbox (row/column, gap, padding, align.cross, wrap по ширине; группа — hug): каждый
ребёнок обязан встать на свой бокс ±LAYOUT_TOLERANCE. Не сошлось — ничего не записывается. После принятия дети
теряют `box` (позицию выражает раскладка), а недостающую ось `size` получают числом из бокса — ничего не теряется.

layoutHints — только КАНДИДАТЫ (применяет resolve, 2.5; сборка из канона их не берёт, INV-B8). `w/h: fill` — ребёнок
покрывает контейнер от кромки до кромки (ставится и на детей неразобранных контейнеров: оверлей-фон над контентом
— ровно этот случай); `main: space-between` — дети прижаты к обоим краям (ведущий и замыкающий отступы равны) и
между ними есть зазор; `grid` — ряд ≥ GRID_MIN_ITEMS равновеликих (medium), решётка-перенос равновеликих (high).
Подсказки ставятся на узлы макета, не на синтетические группы.

Допущения, не проверенные данными (образца нет):
  • вся логика §8 — по тексту спеки; направление одиночного ребёнка (column), порог GRID_MIN_ITEMS = 3, условие
    «зазор > 0» для space-between, абсолютная добавка «+ допуск» к GAP_JUMP, id группы длиннее MAX_ID_MEMBERS
    членов — `первый..последний#число` — наши решения там, где спека свободна;
  • «прижаты к обоим краям» читается как равенство ведущего и замыкающего отступов: центрированная группа тоже
    получит кандидата space-between (это подсказка, решает resolve);
  • главная ось всегда start + padding (end/center по главной оси не выводятся — спека называет их только как
    подсказку space-between); двухколоночные раскладки из россыпи детей без обёрток (колонки разной высоты рядом)
    не выводятся — делится только по строкам, затем по зазорам;
  • замыкающие отступы (справа/снизу) — измеренный остаток до кромки рамки; будет ли он нужен контейнеру, который
    сборка сделает hug, макет не говорит;
  • рамки (border) контейнера в реплее не моделируются: в Figma/Pixso дети не сдвигаются на толщину обводки, в CSS
    сдвигаются — учитывает сборка (3.2);
  • инстансы как контейнеры не рассматриваются (их дети — анатомия компонента, `childrenPath`); контейнер, у которого
    нет бокса и адаптер не сообщил прямоугольник, не разбирается (`no-geometry`).
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import canon as C

LAYOUT_TOLERANCE = 1          # px: допуск реплея (спека §20.4)
GAP_JUMP = 1.3                # разделительный зазор обязан превышать группирующий БОЛЕЕ чем в 1.3 раза (§20.4)
GRID_MIN_ITEMS = 3            # допущение: меньше трёх равновеликих — пара, а не решётка (спека порога не называет)
MAX_ID_MEMBERS = 4            # id группы: до 4 членов списком, длиннее — «первый..последний#число»
UNPROVEN_LIST_LIMIT = 20      # длина списка в stats; остальное — честный счётчик dropped
ID_PREFIX = "inferred:"
GROUP_NAME = "Group"          # автоимя (names.is_auto_name): выведенная обёртка — не осмысленный кандидат резолва
EPS = 1e-6
FIT_EPS = 0.005               # ширина строки «влезает» с запасом на округление канона (два знака)

OVERLAP = "overlap"
OUTSIDE = "outside"
OVERFLOW = "overflow"
CROSS = "cross"
RHYTHM = "rhythm"
REPLAY = "replay"
NO_BOX = "no-box"
NO_GEOMETRY = "no-geometry"
REASONS = (OVERLAP, OUTSIDE, OVERFLOW, CROSS, RHYTHM, REPLAY, NO_BOX, NO_GEOMETRY)

NEXT = ("unproven и layoutHints — кандидаты, не решения: px→flex для них принимает только resolve по плану, сборка "
        "из канона их не применяет; список и причины — stats.layout канона")

Rect = Tuple[float, float, float, float]          # x, y, w, h в координатах экрана


class _Refuse(Exception):
    """Раскладку вывести нельзя: код причины и короткая подробность (id детей, числа)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason, detail)
        self.reason = reason
        self.detail = detail


# ── числа и геометрия ────────────────────────────────────────────────────────

def _isnum(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r2(v: Any) -> Any:
    return C.round_num(v, 2)


def _fmt(v: float) -> str:
    return f"{v:g}"


def _rect_of(n: dict, rects: Dict[str, Sequence[float]]) -> Optional[Rect]:
    """Прямоугольник узла в координатах экрана: `box`, а у узла в потоке — от адаптера. Числа — как в каноне
    (два знака): реплей доказывает то, что ляжет в файл."""
    b = n.get("box")
    vals: Optional[Sequence[float]] = None
    if isinstance(b, dict) and all(_isnum(b.get(k)) for k in ("x", "y", "w", "h")):
        vals = (b["x"], b["y"], b["w"], b["h"])
    else:
        r = rects.get(n.get("id"))
        if isinstance(r, (list, tuple)) and len(r) == 4 and all(_isnum(v) for v in r):
            vals = r
    if vals is None:
        return None
    return (_r2(vals[0]), _r2(vals[1]), _r2(vals[2]), _r2(vals[3]))


def _span(r: Rect, d: str) -> Tuple[float, float, float, float]:
    """(начало, конец) по главной оси направления d и (начало, конец) по поперечной."""
    x, y, w, h = r
    return (x, x + w, y, y + h) if d == "row" else (y, y + h, x, x + w)


def _union(rects: Sequence[Rect]) -> Rect:
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (_r2(x0), _r2(y0), _r2(x1 - x0), _r2(y1 - y0))


def _clusters(gaps: Sequence[float], tol: float) -> List[List[int]]:
    """Индексы зазоров по возрастанию величины; кластер — зазоры в пределах допуска от своего наименьшего
    (10 и 11 — одна величина, 10 и 24 — две)."""
    order = sorted(range(len(gaps)), key=lambda i: (gaps[i], i))
    out: List[List[int]] = []
    for i in order:
        if out and gaps[i] - gaps[out[-1][0]] <= tol + EPS:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def _cross_pos(align: Optional[str], start: float, room: float, size: float) -> float:
    if align == "end":
        return start + room - size
    if align == "center":
        return start + (room - size) / 2
    return start


def _padding(d: str, lead: Any, trail: Any, lo: Any, hi: Any) -> Dict[str, Any]:
    """lead/trail — отступы по главной оси, lo/hi — по поперечной; для row главная — горизонталь."""
    if d == "row":
        return {"top": lo, "right": trail, "bottom": hi, "left": lead}
    return {"top": lead, "right": hi, "bottom": trail, "left": lo}


def _black_box(n: dict) -> bool:
    """Библиотечный инстанс с контентом: его дети — анатомия, которую рендерит компонент, а не разметка."""
    comp = n.get("component")
    return n.get("kind") == "instance" and isinstance(comp, dict) and comp.get("childrenRole") == "content"


# ── участники раскладки ──────────────────────────────────────────────────────

class _Item:
    """Участник раскладки: узел макета (members=None) или синтетическая группа (members и layout)."""
    __slots__ = ("node", "rect", "leaves", "order", "members", "layout")

    def __init__(self, node: dict, rect: Rect, leaves: List[str], order: int,
                 members: Optional[List["_Item"]] = None, layout: Optional[dict] = None):
        self.node = node
        self.rect = rect
        self.leaves = leaves
        self.order = order
        self.members = members
        self.layout = layout


class _Solved:
    __slots__ = ("layout", "items", "columns")

    def __init__(self, layout: dict, items: List[_Item], columns: Optional[int]):
        self.layout = layout
        self.items = items
        self.columns = columns


def _lines(items: List[_Item], tol: float) -> List[List[_Item]]:
    """Полосы по вертикали: ребёнок входит в текущую, если перекрывает её более чем на допуск. Сверху вниз,
    внутри полосы — слева направо (равные — в порядке исходного списка)."""
    ordered = sorted(items, key=lambda it: (it.rect[1], it.rect[0], it.order))
    lines: List[List[_Item]] = []
    cur: List[_Item] = []
    band_end = 0.0
    for it in ordered:
        top, bottom = it.rect[1], it.rect[1] + it.rect[3]
        if cur and top < band_end - tol - EPS:
            cur.append(it)
            band_end = max(band_end, bottom)
        else:
            if cur:
                lines.append(cur)
            cur, band_end = [it], bottom
    if cur:
        lines.append(cur)
    return [sorted(line, key=lambda it: (it.rect[0], it.rect[1], it.order)) for line in lines]


def _first_overlap(items: List[_Item], tol: float) -> Optional[Tuple[_Item, _Item]]:
    """Первая пара детей, перекрывающихся по обеим осям более чем на допуск (свип по x)."""
    order = sorted(items, key=lambda it: (it.rect[0], it.rect[1], it.order))
    t = tol + EPS
    for i in range(len(order)):
        a = order[i]
        a_end = a.rect[0] + a.rect[2]
        for j in range(i + 1, len(order)):
            b = order[j]
            if b.rect[0] >= a_end - t:
                break
            ox = min(a_end, b.rect[0] + b.rect[2]) - max(a.rect[0], b.rect[0])
            oy = min(a.rect[1] + a.rect[3], b.rect[1] + b.rect[3]) - max(a.rect[1], b.rect[1])
            if ox > t and oy > t:
                return a, b
    return None


# ── прогон по дереву ─────────────────────────────────────────────────────────

class _Run:
    def __init__(self, root: dict, rects: Dict[str, Sequence[float]], tol: float, jump: float):
        self.root = root
        self.tol = tol
        self.jump = jump
        self.geo: Dict[str, Rect] = {}          # снимок ДО правок: решения не зависят от порядка обхода
        self.ids: set = set()
        for n in C.iter_nodes(root):
            nid = n.get("id")
            if isinstance(nid, str):
                self.ids.add(nid)
                r = _rect_of(n, rects)
                if r is not None:
                    self.geo[nid] = r
        self.counts: Counter = Counter()
        self.unproven: List[Dict[str, str]] = []

    # ── обход ────────────────────────────────────────────────────────────────
    def walk(self) -> None:
        stack = [self.root]
        while stack:
            n = stack.pop()
            if n.get("occluded") is True or _black_box(n):
                continue                         # закрытое слоем и чёрные ящики — не контент экрана
            kids = [k for k in (n.get("children") or []) if isinstance(k, dict)]
            if self._candidate(n, kids):
                self._visit(n, kids)
            stack.extend(reversed(kids))         # дети ДО правки: обход идёт по исходному дереву

    @staticmethod
    def _candidate(n: dict, kids: List[dict]) -> bool:
        return (n.get("kind") == "frame" and "layout" not in n and "art" not in n and bool(kids)
                and not str(n.get("id", "")).startswith(ID_PREFIX))

    def _visit(self, node: dict, kids: List[dict]) -> None:
        many = len(kids) >= 2
        cont = self.geo.get(node["id"])
        rects = [self.geo.get(k.get("id")) for k in kids]
        if cont is None:
            if many:
                self._fail(node, _Refuse(NO_GEOMETRY, "у контейнера нет бокса: он стоит в потоке чужой раскладки, "
                                                      "а адаптер не сообщил его прямоугольник"))
            return
        if any(r is None for r in rects):
            if many:
                bad = next(k for k, r in zip(kids, rects) if r is None)
                self._fail(node, _Refuse(NO_BOX, str(bad.get("id"))))
            return
        items = [_Item(k, r, [k["id"]], i) for i, (k, r) in enumerate(zip(kids, rects))]
        self._fill_hints(items, cont)
        try:
            self._refusals(items, cont)
            solved = self._lone(items[0], cont) if len(items) == 1 else self._solve(items, cont)
        except _Refuse as err:
            if many:
                self._fail(node, err)
            return
        self._commit(node, solved)

    def _fail(self, node: dict, err: _Refuse) -> None:
        rec = {"id": node["id"], "reason": err.reason}
        if err.detail:
            rec["detail"] = err.detail
        self.unproven.append(rec)

    # ── отказы геометрии (п. 7) ──────────────────────────────────────────────
    def _refusals(self, items: List[_Item], cont: Rect) -> None:
        X, Y, W, H = cont
        t = self.tol + EPS
        for it in items:
            x, y = it.rect[0], it.rect[1]
            if x < X - t or y < Y - t or x > X + W + t or y > Y + H + t:
                raise _Refuse(OUTSIDE, it.node["id"])
        for it in items:
            x, y, w, h = it.rect
            if x + w > X + W + t or y + h > Y + H + t:
                raise _Refuse(OVERFLOW, it.node["id"])
        pair = _first_overlap(items, self.tol)
        if pair is not None:
            raise _Refuse(OVERLAP, f"{pair[0].node['id']} × {pair[1].node['id']}")

    # ── подсказки-кандидаты ──────────────────────────────────────────────────
    def _hint(self, node: dict, key: str, value: Any) -> None:
        hints = node.get("layoutHints")
        if not isinstance(hints, dict):
            hints = {}
            node["layoutHints"] = hints
        if hints.get(key) != value:
            hints[key] = value
            self.counts["hint:" + key] += 1

    def _fill_hints(self, items: List[_Item], cont: Rect) -> None:
        """`w/h: fill` — ребёнок покрывает контейнер от кромки до кромки. Ставится независимо от исхода доказательства:
        у неразобранного контейнера это единственное, что геометрия подсказывает."""
        X, Y, W, H = cont
        t = self.tol + EPS
        for it in items:
            x, y, w, h = it.rect
            if W > 0 and abs(x - X) <= t and abs(x + w - (X + W)) <= t:
                self._hint(it.node, "w", "fill")
            if H > 0 and abs(y - Y) <= t and abs(y + h - (Y + H)) <= t:
                self._hint(it.node, "h", "fill")

    def _container_hints(self, node: dict, solved: _Solved) -> None:
        lay, items = solved.layout, solved.items
        d = lay["direction"]
        t = self.tol + EPS
        if len(items) >= 2 and not lay.get("wrap") and lay.get("gap", 0) > t:
            pad = lay.get("padding") or {}
            lead, trail = ((pad.get("left", 0), pad.get("right", 0)) if d == "row"
                           else (pad.get("top", 0), pad.get("bottom", 0)))
            if abs(lead - trail) <= t:
                self._hint(node, "main", "space-between")
        if d == "row" and len(items) >= GRID_MIN_ITEMS and all(it.members is None for it in items):
            ws = [it.rect[2] for it in items]
            hs = [it.rect[3] for it in items]
            if max(ws) - min(ws) <= t and max(hs) - min(hs) <= t:
                self._hint(node, "grid", {"confidence": "high" if lay.get("wrap") else "medium",
                                          "columns": solved.columns or len(items)})

    # ── решение ──────────────────────────────────────────────────────────────
    def _lone(self, it: _Item, cont: Rect) -> _Solved:
        """Одиночный ребёнок → padding родителя (п. 3)."""
        x, y, w, h = it.rect
        X, Y, W, H = cont
        pad = {"top": _r2(max(0.0, y - Y)), "right": _r2(max(0.0, X + W - (x + w))),
               "bottom": _r2(max(0.0, Y + H - (y + h))), "left": _r2(max(0.0, x - X))}
        layout: Dict[str, Any] = {"direction": "column", "inferred": True}
        if any(pad.values()):
            layout["padding"] = pad
        self._prove(cont, layout, [it])
        return _Solved(layout, [it], None)

    def _solve(self, items: List[_Item], cont: Rect) -> _Solved:
        lines = _lines(items, self.tol)
        if len(lines) == 1:
            return self._sequence(items, "row", cont)
        if all(len(line) == 1 for line in lines):
            return self._sequence(items, "column", cont)
        return self._lattice(lines, cont)

    def _lattice(self, lines: List[List[_Item]], cont: Rect) -> _Solved:
        """Решётка (п. 6): одна wrapping row при равном зазоре в обе стороны, иначе вложенные ряды."""
        try:
            return self._wrap(lines, cont)
        except _Refuse:
            pass
        units = [line[0] if len(line) == 1 else self._group(line, "row") for line in lines]
        return self._sequence(units, "column", cont)

    def _wrap(self, lines: List[List[_Item]], cont: Rect) -> _Solved:
        t = self.tol + EPS
        if any(len(line) < 2 for line in lines[:-1]):
            raise _Refuse(RHYTHM, "строка из одного блока не в конце — это колонка, не перенос")
        bands = [(min(it.rect[1] for it in line), max(it.rect[1] + it.rect[3] for it in line)) for line in lines]
        col_gaps = [max(0.0, line[i + 1].rect[0] - (line[i].rect[0] + line[i].rect[2]))
                    for line in lines for i in range(len(line) - 1)]
        row_gaps = [max(0.0, bands[k + 1][0] - bands[k][1]) for k in range(len(lines) - 1)]
        gaps = col_gaps + row_gaps
        if max(gaps) - min(gaps) > t:
            raise _Refuse(RHYTHM, "зазоры по горизонтали и вертикали разные")
        lefts = [line[0].rect[0] for line in lines]          # ровные начала строк проверяет реплей: все с padding.left
        X, Y, W, H = cont
        raw = [self._cross(line, "row", (X, top, W, bottom - top)) for line, (top, bottom) in zip(lines, bands)]
        picked = set(a or "start" for a in raw)
        if len(picked) > 1:
            raise _Refuse(CROSS, "строки выровнены по-разному")
        pick = next(iter(picked))
        align = ("start" if "start" in raw else None) if pick == "start" else pick
        gap = _r2(sum(gaps) / len(gaps))
        left = max(0.0, min(lefts) - X)
        top = max(0.0, bands[0][0] - Y)
        used = [sum(it.rect[2] for it in line) + (len(line) - 1) * gap for line in lines]
        right = max(0.0, W - left - max(used))             # ширина содержимого = самая широкая строка
        content_h = sum(b - a for a, b in bands) + (len(lines) - 1) * gap
        bottom_pad = max(0.0, H - top - content_h)
        layout: Dict[str, Any] = {"direction": "row", "wrap": True, "inferred": True}
        if gap:
            layout["gap"] = gap
        pad = {"top": _r2(top), "right": _r2(right), "bottom": _r2(bottom_pad), "left": _r2(left)}
        if any(pad.values()):
            layout["padding"] = pad
        if align:
            layout["align"] = {"cross": align}
        ordered = [it for line in lines for it in line]
        self._prove(cont, layout, ordered)
        return _Solved(layout, ordered, len(lines[0]))

    @staticmethod
    def _key(it: _Item, d: str) -> Tuple[float, float, int]:
        s = _span(it.rect, d)
        return s[0], s[2], it.order

    def _gaps(self, items: List[_Item], d: str) -> List[float]:
        spans = [_span(it.rect, d) for it in items]
        return [max(0.0, spans[i + 1][0] - spans[i][1]) for i in range(len(items) - 1)]

    def _sequence(self, items: List[_Item], d: str, cont: Rect) -> _Solved:
        """Дети на одной оси: плоский ритм, а если он не сходится — деление на группы по величинам зазора (п. 5)."""
        items = sorted(items, key=lambda it: self._key(it, d))
        try:
            return self._flat(items, d, cont)
        except _Refuse as err:
            first = err
        segments = self._segments(items, d)
        if segments is None:
            gaps = self._gaps(items, d)
            if first.reason == REPLAY and gaps and max(gaps) - min(gaps) > self.tol + EPS:
                raise _Refuse(RHYTHM, f"зазоры {_fmt(min(gaps))}…{_fmt(max(gaps))} px не сводятся к одному ритму")
            raise first
        units = [seg[0] if len(seg) == 1 else self._group(seg, d) for seg in segments]
        return self._flat(units, d, cont)

    def _segments(self, items: List[_Item], d: str) -> Optional[List[List[_Item]]]:
        """Отрезки по самой большой величине зазора; None — делить нечего (одна величина, или разрыв не больше
        GAP_JUMP, или он не больше группирующего зазора на допуск — шум округления, а не разделитель)."""
        gaps = self._gaps(items, d)
        if len(gaps) < 2:
            return None
        groups = _clusters(gaps, self.tol)
        if len(groups) < 2:
            return None
        sep_min = min(gaps[i] for i in groups[-1])
        grp_max = max(gaps[i] for i in groups[-2])
        if not (sep_min > self.jump * grp_max and sep_min - grp_max > self.tol + EPS):
            return None
        cut = set(groups[-1])
        out: List[List[_Item]] = []
        start = 0
        for i in range(len(items) - 1):
            if i in cut:
                out.append(items[start:i + 1])
                start = i + 1
        out.append(items[start:])
        return out

    def _group(self, members: List[_Item], d: str) -> _Item:
        """Синтетическая тугая обёртка: id выдаётся при записи, чтобы отвергнутые попытки не занимали имена."""
        rect = _union([m.rect for m in members])
        solved = self._sequence(members, d, rect)
        node: Dict[str, Any] = {"kind": "frame", "name": GROUP_NAME, "layout": solved.layout,
                                "size": {"w": "hug", "h": "hug"}, "children": [m.node for m in solved.items]}
        leaves = [leaf for m in solved.items for leaf in m.leaves]
        return _Item(node, rect, leaves, min(m.order for m in members), solved.items, solved.layout)

    def _cross(self, items: List[_Item], d: str, cont: Rect) -> Optional[str]:
        """Выравнивание по поперечной оси (п. 2): None — align не пишется; иначе start/end/center; нет — отказ."""
        t = self.tol + EPS
        spans = [_span(it.rect, d) for it in items]
        c0 = [s[2] for s in spans]
        c1 = [s[3] for s in spans]
        sizes = [b - a for a, b in zip(c0, c1)]
        if max(c0) - min(c0) <= t:
            return None if max(sizes) - min(sizes) <= t else "start"
        if max(c1) - min(c1) <= t:
            return "end"
        mid = [(a + b) / 2 for a, b in zip(c0, c1)]
        cs, ce = _span(cont, d)[2], _span(cont, d)[3]
        if max(mid) - min(mid) <= t and abs(sum(mid) / len(mid) - (cs + ce) / 2) <= t:
            return "center"
        raise _Refuse(CROSS, "поперечные позиции детей не start/center/end")

    def _flat(self, items: List[_Item], d: str, cont: Rect) -> _Solved:
        items = sorted(items, key=lambda it: self._key(it, d))
        spans = [_span(it.rect, d) for it in items]
        align = self._cross(items, d, cont)
        gaps = self._gaps(items, d)
        gap = _r2(sum(gaps) / len(gaps)) if gaps else 0
        layout: Dict[str, Any] = {"direction": d, "inferred": True}
        if gap:
            layout["gap"] = gap
        ms, me, cs, ce = _span(cont, d)               # у группы cont — объединение членов: отступы выходят нулевыми
        c0 = [s[2] for s in spans]
        c1 = [s[3] for s in spans]
        lead = max(0.0, spans[0][0] - ms)
        trail = max(0.0, me - max(s[1] for s in spans))
        if align == "center":
            lo = hi = max(0.0, ((ce - cs) - max(b - a for a, b in zip(c0, c1))) / 2)
        else:
            lo, hi = max(0.0, min(c0) - cs), max(0.0, ce - max(c1))
        pad = _padding(d, _r2(lead), _r2(trail), _r2(lo), _r2(hi))
        if any(pad.values()):
            layout["padding"] = pad
        if align:
            layout["align"] = {"cross": align}
        self._prove(cont, layout, items)
        return _Solved(layout, items, None)

    # ── доказательство: реплей продукта ──────────────────────────────────────
    def _measure(self, it: _Item) -> Tuple[float, float]:
        """Размер участника: узел макета — его бокс, группа — hug по содержимому."""
        if it.members is None:
            return it.rect[2], it.rect[3]
        lay = it.layout or {}
        sizes = [self._measure(m) for m in it.members]
        gap = lay.get("gap", 0)
        pad = lay.get("padding") or {}
        pw = pad.get("left", 0) + pad.get("right", 0)
        ph = pad.get("top", 0) + pad.get("bottom", 0)
        n = len(sizes)
        if lay.get("direction") == "row":
            return sum(s[0] for s in sizes) + gap * (n - 1) + pw, max(s[1] for s in sizes) + ph
        return max(s[0] for s in sizes) + pw, sum(s[1] for s in sizes) + gap * (n - 1) + ph

    def _place(self, layout: dict, items: List[_Item], cont: Rect) -> List[Rect]:
        """Мини-flexbox: куда раскладка поставит участников в контейнере cont."""
        X, Y, W, H = cont
        d = layout["direction"]
        pad = layout.get("padding") or {}
        pl, pr = pad.get("left", 0), pad.get("right", 0)
        pt, pb = pad.get("top", 0), pad.get("bottom", 0)
        gap = layout.get("gap", 0)
        cross = (layout.get("align") or {}).get("cross")
        sizes = [self._measure(it) for it in items]
        out: List[Any] = [None] * len(items)
        if layout.get("wrap"):
            if d != "row":
                raise _Refuse(REPLAY, "перенос поддерживается только для row")
            inner = W - pl - pr
            row_gap = layout.get("crossGap", gap)
            lines: List[List[int]] = []
            cur: List[int] = []
            used = 0.0
            for i, (w, _h) in enumerate(sizes):
                need = w if not cur else used + gap + w
                if cur and need > inner + FIT_EPS:
                    lines.append(cur)
                    cur, used = [i], w
                else:
                    cur.append(i)
                    used = need
            if cur:
                lines.append(cur)
            y = Y + pt
            for line in lines:
                line_h = max(sizes[i][1] for i in line)
                x = X + pl
                for i in line:
                    w, h = sizes[i]
                    out[i] = (x, _cross_pos(cross, y, line_h, h), w, h)
                    x += w + gap
                y += line_h + row_gap
            return out
        if d == "row":
            x = X + pl
            for i, (w, h) in enumerate(sizes):
                out[i] = (x, _cross_pos(cross, Y + pt, H - pt - pb, h), w, h)
                x += w + gap
        else:
            y = Y + pt
            for i, (w, h) in enumerate(sizes):
                out[i] = (_cross_pos(cross, X + pl, W - pl - pr, w), y, w, h)
                y += h + gap
        return out

    def _worst(self, items: List[_Item], placed: List[Rect]) -> Tuple[float, str]:
        """Наибольшее смещение узла макета от своего бокса (с заходом в группы) и id этого узла."""
        best: Tuple[float, str] = (0.0, "")
        for it, r in zip(items, placed):
            if it.members is None:
                t = self.geo[it.node["id"]]
                cand = (max(abs(r[0] - t[0]), abs(r[1] - t[1])), it.node["id"])
            else:
                cand = self._worst(it.members, self._place(it.layout or {}, it.members, r))
            if cand[0] > best[0]:
                best = cand
        return best

    def _prove(self, cont: Rect, layout: dict, items: List[_Item]) -> None:
        dev, who = self._worst(items, self._place(layout, items, cont))
        if dev > self.tol + EPS:
            raise _Refuse(REPLAY, f"{who}: сдвиг {dev:.2f} px")

    # ── запись принятой раскладки ────────────────────────────────────────────
    def _gid(self, leaves: List[str]) -> str:
        """id группы из её членов (узлов макета, вложенные группы раскрыты): тот же макет — тот же id."""
        if len(leaves) <= MAX_ID_MEMBERS:
            body = "+".join(leaves)
        else:
            body = f"{leaves[0]}..{leaves[-1]}#{len(leaves)}"
        base = ID_PREFIX + body
        gid, i = base, 2
        while gid in self.ids:                   # не пересекаться с id источника
            gid = f"{base}~{i}"
            i += 1
        self.ids.add(gid)
        return gid

    def _enter_flow(self, it: _Item) -> None:
        """Участник встал в поток: позицию выражает раскладка — `box` уходит, размер по оси не теряется."""
        if it.members is not None:
            it.node["id"] = self._gid(it.leaves)
            self.counts["groups"] += 1
            for m in it.members:
                self._enter_flow(m)
            return
        n = it.node
        n.pop("box", None)
        size = n.get("size")
        if not isinstance(size, dict):
            size = {}
            n["size"] = size
        if "w" not in size:
            size["w"] = it.rect[2]
        if "h" not in size:
            size["h"] = it.rect[3]

    def _commit(self, node: dict, solved: _Solved) -> None:
        node["layout"] = solved.layout
        node["children"] = [it.node for it in solved.items]
        for it in solved.items:
            self._enter_flow(it)
        self.counts["inferred"] += 1
        if solved.layout.get("wrap"):
            self.counts["wraps"] += 1
        self._container_hints(node, solved)

    def report(self) -> Dict[str, Any]:
        """Счётчики и неразобранные контейнеры; пусто, если делать было нечего (канон остаётся побайтно прежним)."""
        out: Dict[str, Any] = {}
        for key in ("inferred", "groups", "wraps"):
            if self.counts[key]:
                out[key] = self.counts[key]
        if self.unproven:
            lst: Dict[str, Any] = {"count": len(self.unproven), "items": self.unproven[:UNPROVEN_LIST_LIMIT]}
            if len(self.unproven) > UNPROVEN_LIST_LIMIT:
                lst["dropped"] = len(self.unproven) - UNPROVEN_LIST_LIMIT
            out["unproven"] = lst
        hints = {k[len("hint:"):]: v for k, v in sorted(self.counts.items()) if k.startswith("hint:") and v}
        if hints:
            out["hints"] = hints
        return out


# ── вход ─────────────────────────────────────────────────────────────────────

def infer_layout(root: dict, rects: Optional[Dict[str, Sequence[float]]] = None,
                 tolerance: float = LAYOUT_TOLERANCE, gap_jump: float = GAP_JUMP) -> Dict[str, Any]:
    """Выводит раскладку контейнеров без `layout` НА МЕСТЕ; корень остаётся тем же объектом.

    rects — экранные прямоугольники {id: (x, y, w, h)} узлов, у которых нет `box` (стоят в потоке чужой раскладки):
    без них контейнер в таком потоке не разобрать. Возвращает отчёт для `stats.layout` (только ненулевое):
    inferred, groups, wraps, hints {w,h,main,grid}, unproven {count, items[{id, reason, detail?}], dropped?}."""
    run = _Run(root, rects or {}, tolerance, gap_jump)
    run.walk()
    return run.report()


def summary(report: Dict[str, Any]) -> Dict[str, Any]:
    """Короткая сводка для ответа daiver_normalize (списки остаются в каноне); нечего сказать — {}."""
    if not report:
        return {}
    out: Dict[str, Any] = {k: report[k] for k in ("inferred", "groups", "wraps") if k in report}
    if "unproven" in report:
        out["unproven"] = report["unproven"]["count"]
    if "hints" in report:
        out["hints"] = dict(report["hints"])
    if "unproven" in report or "hints" in report:
        out["next"] = NEXT
    return out
