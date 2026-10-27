"""outline.py — daiver_outline и daiver_section: карта макета и тело секции (спека §9, §16 nodeUrl, задача 1.8).

Канон большого экрана — 35–45 тыс. токенов, и целиком его не читает никто (INV-B1, N3): resolve планирует по
ДАЙДЖЕСТУ (`daiver_outline`), build получает поддеревья, ограниченные по байтам (`daiver_section`). Модуль чистый:
канон на входе — ответ на выходе, ни файлов, ни времени (один вход → побайтно один выход, F7).

Секции (§9.2, descend-and-partition). Границы выбирает КОД по весу — минифицированные байты поддерева из
`canon.weights` (веса и хеши не пересчитываются здесь заново): узел, чей вес ≤ бюджета, — секция целиком; тяжелее —
спускаемся к детям в порядке документа; лист тяжелее бюджета — `oversize`, секция из него одного, посередине не
режется. Поэтому корень при большом бюджете — вся секция, а меньший бюджет всегда даёт разрез не крупнее большего
(тест пинит «разрез мельче, но тот же»). Секция — узел: `hash` — `subtree_hash` его поддерева (не зависит от
бюджета). Бюджет: аргумент > `workflow.weightBudget` конфига > 24000 (дефолт живёт в config.py).

Что выдаёт outline: stats, sections[] (`{id, name, kind, figmaName?, weight, nDescendants, depth, path, hash}` +
`oversize`/`occluded` там, где они верны), дерево, unproven[], goalImage с подсказкой, nodeUrlTemplate.

Дерево отдаётся СТРОКАМИ, а не вложенными объектами — отклонение от буквы §9.1 (README → «Отклонения»). Причина
измерена: MCP печатает ответ `json.dumps(indent=1)`, и вложенный объект узла (layout с padding и align, size) раскрывается
в десяток строк — дерево из объектов оказалось по транспорту всего в 1.1–1.2 раза легче канона, строка на узел — в 2.6–3.8
раза (на тех же синтетических экранах и с теми же полями). Одна строка = один узел, отступ = глубина (2 пробела):
    <id> <kind> "<name>" [<figmaName>] <вес>B [n=<детей>] [art=<slug>] [occluded] [text="…"] [box=x,y,w,h]
    [row|column gap= crossGap= pad= main= cross= wrap inferred stated] [w= h=]
Поля дерева — те же, что в спеке: kind/name/weight/nChildren; текст — первые 80 символов БЕЗ эллипсиса; box — только
у узлов, у которых он есть в каноне (то есть у абсолютных); layout/size — где есть (значения как в каноне: отсутствие
`align.cross` не читается как start). Сверх спеки: figmaName у инстанса (имя слоя у инстанса — строка вариантов
«Steps=OFF», по ней компонент не опознать), art-slug и occluded (иллюстрацию не строят руками, скрытое не строят).
Структурное «лёгкое» дерево (`light_tree`) — для кода следующих задач; модель видит строки.

Потолок дерева (допущение, в спеке его нет). Дайджест большого экрана обязан быть заметно легче канона (контракт
«≥ 4× легче», спека обещает 5–6×) — иначе это не дайджест. Дерево получает остаток четверти канона после sections/
unproven/подсказок, но не меньше одного бюджета секции: карта маленького экрана не должна ужиматься из-за постоянных
частей ответа, и канон, влезающий в одну секцию, получает дерево целиком (исключение — патологическая глубина: отступ
строки растёт с глубиной, цепочка из сотен вложенных рамок тяжелее своего канона). Не помещается — свёртка по ценности
для планировщика: сначала простые листья (text/rect/vector/icon/image: кандидатами резолва и контейнерами раскладки они
не бывают, а их id названы в unproven там, где они важны), потом нижние уровни целиком. Узлы с непоказанными детьми несут
`n=` и пометку `folded`, ответ называет, что скрыто (`folded`: leaves, depth, hiddenNodes). Без потолка текстовый экран
из сотен узлов давал дайджест вдвое легче канона. Детали свёрнутого — `daiver_section` её секции.

unproven[] строится по КАНОНУ, а не по `stats.layout` (там список обрезан до 20 и его нет при `layout.infer: off`):
  • `absolute-children` — рамка без `layout` и с ≥ 2 детьми: дети остались абсолютными. Называются ВСЕ её дети (до 20 id
    и честное число `count`) — декларации constraint'ов живут на детях, их берёт `daiver_source_nodes`. Рамка с одним
    ребёнком — оверлей, не раскладка, молчит; как и рамка со своей раскладкой (объявленной или доказанной, в том числе с
    pinned-детьми); как и всё под `occluded` и внутри библиотечного инстанса (анатомия компонента). Это те же условия, что у
    `ingest/layout.py` (тест сверяет множества на прогоне вывода раскладки);
  • `layout-hints` — узел с кандидатом `layoutHints` (w/h fill, main space-between, grid): решает resolve по плану.
Узел с обеими причинами идёт ДВАЖДЫ (сначала дети, потом подсказки). Список — первые 60 в порядке документа, остальное —
`unprovenDropped`. Полностью объявленный макет («Modal») — пусто.

goalImage — путь картинки кадра от корня репо или null и подсказка «что делать дальше» (картинка нужна ТОЛЬКО для
группировки контейнеров из unproven; сначала декларации, `verify.vision: off` — не открывать). nodeUrlTemplate строится
из `frameUrl` кадра (его записал pull): Pixso — подмена `item-id`, Figma — `node-id` (в id двоеточие → дефис); параметра
нет — добавляется; путь внутри инстанса (`A;B/C`) ссылается на инстанс A; синтетический `inferred:`-узел и модели
dom/code ссылки не имеют; нет URL — шаблон null и голый id. Адрес не выдумывается.

daiver_section понимает ровно id секций outline при том же бюджете (необязательный `weightBudget` — тот же, что был у
outline: секции зависят от бюджета). Чужой id — отказ с названием того, что брать вместо него; корень, тяжелее бюджета,
целиком не отдаётся (обходного пути вокруг outline нет, N3). `ancestors` — цепочка root→parent с раскладкой и поверхностью
(style/tokens): оболочка над секциями ни в одну секцию не входит, и без её стиля её не из чего собрать.

Секция — это узел, и соседей в одну секцию не склеить (у склейки не было бы ни id, ни хеша поддерева): 60 панелей по 2.8 КБ
при бюджете 24000 дают 60 секций, а не восемь по 22 КБ. Спека упаковки соседей не описывает; план (2.6) должен это знать.

Данные образца. «Modal» (4 узла, 3101 байт) целиком влезает в бюджет: одна секция — корень, unproven пуст (экран
полностью объявлен, подсказок нет), при бюджете 1500 корень разрезается на три инстанса. Полноэкранного кадра в образце
нет — границы секций, лёгкость дайджеста и правила unproven доказаны синтетикой, собранной из настоящих узлов образца.

Допущения, которых образец не проверяет: форма строки дайджеста и потолок дерева — наши решения (спека формата не
называет); вес дайджеста на реальном экране — на синтетике из узлов образца ответ в 4.5 раза легче канона у экрана на
дизайн-системе (169 КБ, 60 панелей) и в 6–7 раз у текстового, у канона в 34 КБ (чуть больше бюджета) — всего в 2.9 раза:
«≥ 4×» держится для больших экранов; набор полей `ancestors`; грубость свёртки по уровням (шаг глубины может скрыть
много); ссылки Figma (`node-id` с дефисом) — по тексту спеки, образца Figma нет; Pixso-параметр `item-id` — по спеке,
URL кадра образца обезличен.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import canon as C
from . import config as CFG
from . import states as STS
from . import track as TR

MAX_TEXT_LEN = 80                 # спека §20.4: maxTextLen
MAX_UNPROVEN = 60                 # спека §20.4: MAX_UNPROVEN
MAX_ABSOLUTE_CHILD_IDS = 20       # спека §20.4: MAX_ABSOLUTE_CHILD_IDS
DEFAULT_BUDGET = CFG.DEFAULTS["workflow"]["weightBudget"]
DIGEST_RATIO = 4                  # ответ outline по возможности не тяжелее 1/4 большого канона (задача 1.8; спека — 5–6×)
SECTION_ERROR_IDS = 10            # сколько id секций называет отказ «узел разрезан»
NO_LINK_PREFIXES = ("inferred:", "dom:", "code:")
PLAIN_LEAF_KINDS = ("text", "rect", "vector", "icon", "image")

ABSOLUTE = "absolute-children"
HINTS = "layout-hints"

LEGEND = ("outline: строка = узел, отступ = глубина: id kind \"имя\" [<компонент>] <вес>B [n=<детей>] [art=<slug>] "
          "[occluded] [text=\"…\"] [box=x,y,w,h] [раскладка] [w= h=]. Вес — байты минифицированного поддерева. box есть "
          "только у абсолютных узлов, остальным позицию задаёт раскладка родителя. Раскладка: row|column gap= crossGap= "
          "pad= (число или top/right/bottom/left) main= cross= wrap inferred (выведена нами) stated (объявлена "
          "дизайнером); w=/h=: px, fill или hug. n=… folded — часть детей не показана: daiver_section.")

NEXT_OUTLINE = ("секции берутся только из sections[].id: тело — daiver_section(sectionId); дерево — карта, а не канон: "
                "значения раскладки и стили берутся из секции")
NEXT_UNPROVEN = ("unproven: сначала daiver_source_nodes по id из children — декларации дизайнера сильнее впечатления; "
                 "layoutHints — кандидаты, а не решения: принимает их только план")
NEXT_FOLDED = ("дерево свёрнуто (простые листья text/rect/vector/icon/image и/или глубокие уровни не показаны): "
               "детали — daiver_section нужной секции (id в sections[])")

GOAL_NOT_NEEDED = "группировать нечего (контейнеров с абсолютными детьми нет) — картинка для раскладки не нужна"
GOAL_OPEN = ("открой картинку (Read): она нужна ТОЛЬКО чтобы сгруппировать контейнеры из unproven; числа (размеры, цвета, "
             "отступы) с неё не брать; сначала daiver_source_nodes по id детей — декларация сильнее впечатления")
GOAL_VISION_OFF = ("картинка есть, но verify.vision: off — не открывай её; группировку контейнеров из unproven решает "
                   "вопрос пользователю (сначала daiver_source_nodes по id детей)")
GOAL_MISSING = ("картинки кадра нет: группировку контейнеров из unproven не угадывай — сначала daiver_source_nodes по id "
                "детей, остальное спроси у пользователя или прикрепи картинку (daiver_track_manifest, rasterPath)")

FIGMA_NOTE = "в {id} двоеточие заменяется дефисом (12:34 → 12-34)"
URL_NOTE = "для id вида A;B/C подставляйте A — ссылка ведёт на инстанс"


class OutlineError(ValueError):
    """Нельзя построить карту/секцию: нет трека, канона, неверный бюджет или чужой id секции."""


# ── бюджет ───────────────────────────────────────────────────────────────────

def check_budget(v: Any) -> Any:
    """Бюджет-аргумент: положительное конечное число (как `workflow.weightBudget` в конфиге)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise OutlineError(f"weightBudget: положительное число — байты минифицированного поддерева секции, "
                           f"получено {v!r}. Без аргумента берётся workflow.weightBudget из конфига, иначе {DEFAULT_BUDGET}")
    return int(v) if v == int(v) else v


def effective_budget(root: Path, arg: Any = None) -> Tuple[Any, List[str]]:
    """(бюджет, предупреждения): аргумент > workflow.weightBudget конфига > 24000 (дефолт — в config.py)."""
    if arg is not None:
        return check_budget(arg), []
    cfg = CFG.load(Path(root))
    warns = [w for w in cfg["warnings"] if w.startswith("workflow.weightBudget")]
    return cfg["config"]["workflow"]["weightBudget"], warns


# ── индекс канона ────────────────────────────────────────────────────────────

def _kids(n: dict) -> List[dict]:
    k = n.get("children")
    return [c for c in k if isinstance(c, dict)] if isinstance(k, list) else []


class _Map:
    """Одноразовый индекс канона: веса, глубины, родители, числа потомков — по одному проходу."""

    def __init__(self, root: dict):
        self.root = root
        self.order = list(C.iter_nodes(root))
        self.W = C.weights(root)
        self.parent: Dict[str, dict] = {}
        self.depth: Dict[str, int] = {root["id"]: 0}
        for n in self.order:                              # прямой порядок: родитель раньше детей
            for k in _kids(n):
                self.parent[k["id"]] = n
                self.depth[k["id"]] = self.depth[n["id"]] + 1
        self.cnt: Dict[str, int] = {}
        for n in reversed(self.order):                    # потомки раньше предков
            self.cnt[n["id"]] = sum(1 + self.cnt[k["id"]] for k in _kids(n))
        self.by_id = {n["id"]: n for n in self.order}

    def chain(self, n: dict) -> List[dict]:
        """Цепочка root → … → родитель узла (без самого узла)."""
        out: List[dict] = []
        cur = self.parent.get(n["id"])
        while cur is not None:
            out.append(cur)
            cur = self.parent.get(cur["id"])
        out.reverse()
        return out


def _figma_name(n: dict) -> Optional[str]:
    comp = n.get("component")
    if n.get("kind") == "instance" and isinstance(comp, dict) and comp.get("figmaName"):
        return comp["figmaName"]
    return None


def _black_box(n: dict) -> bool:
    """Библиотечный инстанс с контентом: его дети — анатомия, которую рендерит компонент (как в ingest/layout.py)."""
    comp = n.get("component")
    return n.get("kind") == "instance" and isinstance(comp, dict) and comp.get("childrenRole") == "content"


# ── секции ───────────────────────────────────────────────────────────────────

def partition(m: _Map, budget: Any) -> List[Tuple[dict, int, bool, bool]]:
    """Секции в порядке документа: [(узел, глубина, oversize, occluded)] — descend-and-partition (§9.2).

    Узел с весом ≤ бюджета — секция целиком; тяжелее и с детьми — спуск; тяжелее и без детей — oversize-лист.
    occluded наследуется: секция под непрозрачным слоем — не контент экрана, даже если флаг стоит на предке."""
    out: List[Tuple[dict, int, bool, bool]] = []
    stack: List[Tuple[dict, int, bool]] = [(m.root, 0, False)]
    if m.root.get("deltaRoot") is True:                       # кадр-состояние (6.2): секции — регионы дельты, сам синтетический корень секцией не бывает
        stack = [(k, 1, False) for k in reversed(_kids(m.root))]
    while stack:
        n, depth, occ = stack.pop()
        occ = occ or n.get("occluded") is True
        kids = _kids(n)
        if m.W[n["id"]] <= budget:
            out.append((n, depth, False, occ))
        elif not kids:
            out.append((n, depth, True, occ))
        else:
            for k in reversed(kids):                          # порядок документа: первый ребёнок обрабатывается первым
                stack.append((k, depth + 1, occ))
    return out


def _path(m: _Map, n: dict) -> str:
    return " > ".join((x.get("name") or x["id"]) for x in m.chain(n) + [n])


def _section_meta(m: _Map, n: dict, depth: int, oversize: bool, occluded: bool) -> dict:
    meta: Dict[str, Any] = {"id": n["id"], "name": n.get("name", ""), "kind": n["kind"]}
    fn = _figma_name(n)
    if fn:
        meta["figmaName"] = fn
    meta.update({"weight": m.W[n["id"]], "nDescendants": m.cnt[n["id"]], "depth": depth, "path": _path(m, n),
                 "hash": C.subtree_hash(n)})
    if oversize:
        meta["oversize"] = True
    if occluded:
        meta["occluded"] = True
    return meta


def section_list(env: dict, budget: Any) -> List[dict]:
    """Мета всех секций кадра в порядке документа — то, что выдаёт outline (для кода 2.6/3.1/1.10)."""
    m = _Map(env["root"])
    return [_section_meta(m, n, d, o, occ) for n, d, o, occ in partition(m, budget)]


# ── дайджест: лёгкое дерево и его строки ─────────────────────────────────────

def _light(m: _Map, n: dict) -> dict:
    """Лёгкие поля узла (значения как в каноне): kind/name/weight/nChildren + то, что нужно планировщику."""
    o: Dict[str, Any] = {"id": n["id"], "kind": n["kind"]}
    if n.get("name"):
        o["name"] = n["name"]
    fn = _figma_name(n)
    if fn:
        o["figmaName"] = fn
    o["weight"] = m.W[n["id"]]
    kids = _kids(n)
    if kids:
        o["nChildren"] = len(kids)
    art = n.get("art")
    if isinstance(art, dict) and art.get("slug"):
        o["art"] = art["slug"]
    if n.get("occluded") is True:
        o["occluded"] = True
    text = n.get("text")
    if isinstance(text, str) and text:
        o["text"] = text[:MAX_TEXT_LEN]                       # без эллипсиса: он съел бы место, а не сообщил бы ничего
    for key in ("box", "layout", "size"):
        if key in n:
            o[key] = n[key]
    return o


def light_tree(env: dict) -> dict:
    """Структурное лёгкое дерево (вложенные объекты) — для кода следующих задач; модель видит строки `outline`."""
    m = _Map(env["root"])
    lights = {n["id"]: _light(m, n) for n in m.order}
    top = dict(lights[env["root"]["id"]])
    stack = [(env["root"], top)]
    while stack:
        n, out = stack.pop()
        kids = _kids(n)
        if kids:
            out["children"] = [dict(lights[k["id"]]) for k in kids]
            stack.extend(zip(kids, out["children"]))
    return top


def _num(v: Any) -> str:
    """Число канона без хвостов («600», «12.5»); fill/hug и прочие слова — как есть."""
    return v if isinstance(v, str) else str(v) if isinstance(v, int) else repr(v)


def _layout_tokens(lay: dict) -> List[str]:
    out = [lay["direction"]]
    if lay.get("gap"):
        out.append(f"gap={_num(lay['gap'])}")
    if lay.get("crossGap"):
        out.append(f"crossGap={_num(lay['crossGap'])}")
    pad = lay.get("padding")
    if isinstance(pad, dict):
        sides = [pad.get(k, 0) for k in ("top", "right", "bottom", "left")]
        if any(sides):
            out.append(f"pad={_num(sides[0])}" if len(set(sides)) == 1 else "pad=" + "/".join(_num(s) for s in sides))
    align = lay.get("align") or {}
    for key in ("main", "cross"):
        if align.get(key):
            out.append(f"{key}={align[key]}")
    for flag in ("wrap", "inferred", "stated"):
        if lay.get(flag):
            out.append(flag)
    return out


def render_line(light: dict, depth: int, folded: bool = False) -> str:
    """Строка дайджеста: одна строка = один узел, отступ = глубина (формат — в docstring модуля и LEGEND)."""
    parts = [light["id"], light["kind"], json.dumps(light.get("name", ""), ensure_ascii=False)]
    if "figmaName" in light:
        parts.append(f"<{light['figmaName']}>")
    parts.append(f"{light['weight']}B")
    if "nChildren" in light:
        parts.append(f"n={light['nChildren']}")
        if folded:
            parts.append("folded")
    if "art" in light:
        parts.append(f"art={light['art']}")
    if light.get("occluded"):
        parts.append("occluded")
    if "text" in light:
        parts.append("text=" + json.dumps(light["text"], ensure_ascii=False))
    b = light.get("box")
    if isinstance(b, dict):
        parts.append("box=" + ",".join(_num(b[k]) for k in ("x", "y", "w", "h")))
    if isinstance(light.get("layout"), dict):
        parts.extend(_layout_tokens(light["layout"]))
    sz = light.get("size")
    if isinstance(sz, dict):
        parts.extend(f"{k}={_num(sz[k])}" for k in ("w", "h") if k in sz)
    return "  " * depth + " ".join(parts)


def _plain_leaf(n: dict) -> bool:
    """Лист без детей вида text/rect/vector/icon/image: сам по себе не кандидат резолва и не контейнер раскладки."""
    return n.get("kind") in PLAIN_LEAF_KINDS and not _kids(n)


def _render(m: _Map, lights: Dict[str, dict], max_depth: Optional[int], drop_leaves: bool) -> List[str]:
    """Строки дерева в порядке документа. drop_leaves — простые листья не перечисляются; глубже max_depth детей нет.
    Узел, у которого часть детей не показана, помечен folded."""
    out: List[str] = []
    stack: List[Tuple[dict, int]] = [(m.root, 0)]
    while stack:
        n, d = stack.pop()
        kids = _kids(n)
        shown = [k for k in kids if not (drop_leaves and _plain_leaf(k))]
        cut = max_depth is not None and d >= max_depth and bool(shown)
        out.append(render_line(lights[n["id"]], d, folded=cut or len(shown) < len(kids)))
        if not cut:
            for k in reversed(shown):
                stack.append((k, d + 1))
    return out


def _size(lines: List[str]) -> int:
    return len(C.dumps(lines).encode("utf-8"))                # минифицированные байты массива: от способа печати не зависят


def _fit_tree(m: _Map, lights: Dict[str, dict], cap: Optional[Any]) -> Tuple[List[str], Optional[dict]]:
    """Дерево строками; не влезает в cap — свёртка по ценности для планировщика: сначала простые листья
    (text/rect/vector/icon/image — кандидатами резолва и контейнерами раскладки они не бывают), затем целые нижние уровни.

    cap=None — без потолка. Не помещается и корень с детьми — остаётся глубина 0: карта не бывает пустой."""
    full = _render(m, lights, None, False)
    if cap is None or _size(full) <= cap:
        return full, None
    lines = _render(m, lights, None, True)
    info: Dict[str, Any] = {"leaves": True} if len(lines) < len(full) else {}
    if _size(lines) > cap:
        lo, hi, best = 0, max(m.depth.values()) - 1, 0
        while lo <= hi:                                       # размер дерева растёт с глубиной — бинарный поиск
            mid = (lo + hi) // 2
            if _size(_render(m, lights, mid, True)) <= cap:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        lines = _render(m, lights, best, True)
        info["depth"] = best
    info["hiddenNodes"] = len(m.order) - len(lines)
    return lines, (info if info["hiddenNodes"] else None)


# ── unproven ─────────────────────────────────────────────────────────────────

def _hint_text(h: dict) -> str:
    parts = []
    for key in ("w", "h", "main"):
        if key in h:
            parts.append(f"{key}={h[key]}")
    g = h.get("grid")
    if isinstance(g, dict):
        parts.append(f"grid={g.get('confidence')}" + (f" columns={g['columns']}" if "columns" in g else ""))
    return " ".join(parts)


def unproven_entries(root: dict) -> List[dict]:
    """ВСЕ записи unproven в порядке документа (кап и счётчик отброшенного — у вызывающего); правила — в docstring."""
    out: List[dict] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.get("occluded") is True:
            continue                                          # скрытое не контент экрана: ни детей, ни подсказок
        kids = [k for k in _kids(n) if not k.get("stub")]            # стабы кадра-состояния (6.2) — не дети, а ссылки на уже собранное экраном
        if n.get("kind") == "frame" and "layout" not in n and "art" not in n and len(kids) >= 2 and not n.get("deltaRoot"):
            ids = [k["id"] for k in kids]
            out.append({"id": n["id"], "name": n.get("name", ""), "reason": ABSOLUTE, "count": len(ids),
                        "children": ",".join(ids[:MAX_ABSOLUTE_CHILD_IDS])})
        hints = n.get("layoutHints")
        if isinstance(hints, dict) and hints:
            out.append({"id": n["id"], "name": n.get("name", ""), "reason": HINTS, "hints": _hint_text(hints)})
        if not _black_box(n):
            stack.extend(reversed(kids))                      # анатомия библиотечного инстанса — не раскладка экрана
    return out


# ── ссылки на узлы (§16) ─────────────────────────────────────────────────────

_URL = re.compile(r"^(https?://[^\s?#]+)(\?[^\s#]*)?(#\S*)?$")


def node_url_template(frame_url: Any, source_format: Any) -> Optional[str]:
    """Шаблон ссылки с `{id}` из frameUrl кадра; None — адреса нет (тогда в тексте голый id, адрес не выдумывается).

    Pixso — подмена значения `item-id`, Figma — `node-id`; параметра нет — добавляется. Остальное в адресе не трогаем."""
    fmt = str(source_format or "")
    param = "item-id" if fmt == "pixso" else "node-id" if fmt.startswith("figma") else None
    m = _URL.match(frame_url.strip()) if isinstance(frame_url, str) else None
    if param is None or m is None:
        return None
    base, query, frag = m.group(1), (m.group(2) or "")[1:], m.group(3) or ""
    pairs = [p for p in query.split("&") if p]
    found = False
    for i, p in enumerate(pairs):
        if p.partition("=")[0] == param:
            pairs[i], found = f"{param}={{id}}", True
    if not found:
        pairs.append(f"{param}={{id}}")
    return base + "?" + "&".join(pairs) + frag


def node_url(template: Optional[str], node_id: Any) -> Optional[str]:
    """Ссылка на узел по шаблону; None — ссылки нет. Путь внутри инстанса ведёт на инстанс; Figma пишет id с дефисом."""
    if not template or not isinstance(node_id, str) or not node_id or node_id.startswith(NO_LINK_PREFIXES):
        return None
    real = node_id.split(";", 1)[0]
    if "node-id={id}" in template:
        real = real.replace(":", "-")
    return template.replace("{id}", real)


# ── ответы инструментов ──────────────────────────────────────────────────────

def _goal(unproven: List[dict], goal_image: Optional[str], vision: bool) -> str:
    if not any(u["reason"] == ABSOLUTE for u in unproven):
        return GOAL_NOT_NEEDED
    if goal_image is None:
        return GOAL_MISSING
    return GOAL_OPEN if vision else GOAL_VISION_OFF


def state_view(st: dict) -> dict:
    """Кадр-состояние в ответе outline: чей это экран, схожесть, счётчики регионов, что убрано, переключатели-кандидаты. Секции ниже — регионы added и changed."""
    return {"screen": st["screen"], "similarity": st["similarity"], "counts": st["counts"],
            "removed": [{"baseId": r["baseId"], "name": r["name"], "parentId": r["parentId"]} for r in st["regions"] if r["kind"] == "removed"],
            "shared": [{"stateId": r["nodeId"], "baseId": r["baseId"], "name": r["name"]} for r in st["regions"] if r["kind"] == "shared"],
            "triggers": st["triggers"],
            "note": "кадр-состояние: sections — регионы added и changed (дельта к экрану), остальное уже собрано экраном; строится ВНУТРИ его модуля"}


def build_outline(env: dict, budget: Any, *, track: Optional[str] = None, frame: Optional[str] = None,
                  frame_url: Optional[str] = None, goal_image: Optional[str] = None, vision: bool = True,
                  warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Ответ daiver_outline по канону (§9.1). Чистая функция: файлы читает `outline_frame`."""
    root = env["root"]
    m = _Map(root)
    sections = [_section_meta(m, n, d, o, occ) for n, d, o, occ in partition(m, budget)]
    lights = {n["id"]: _light(m, n) for n in m.order}
    entries = unproven_entries(root)
    shown = entries[:MAX_UNPROVEN]
    oversize = [s for s in sections if s.get("oversize")]
    stats: Dict[str, Any] = {"nodes": len(m.order), "instances": sum(1 for n in m.order if n["kind"] == "instance"),
                             "weight": m.W[root["id"]], "sections": len(sections)}
    if oversize:
        stats["oversize"] = len(oversize)
    stats["unproven"] = len(entries)
    warns = list(warnings or [])
    if oversize:
        names = ", ".join(s["id"] for s in oversize[:5]) + (" …" if len(oversize) > 5 else "")
        warns.append(f"секции-листья тяжелее бюджета {budget} (oversize: {names}) — лист не режется, строится целиком; "
                     f"поднимите workflow.weightBudget или упростите макет")
    tpl = node_url_template(frame_url, (env.get("daiver") or {}).get("sourceFormat"))

    def assemble(lines: List[str], folded: Optional[dict]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if track is not None:
            out["track"] = track
        if frame is not None:
            out["frame"] = frame
        out.update({"weightBudget": budget, "stats": stats, "sections": sections, "legend": LEGEND, "outline": lines})
        if folded:
            out["folded"] = folded
        out["unproven"] = shown
        if len(entries) > len(shown):
            out["unprovenDropped"] = len(entries) - len(shown)
        if env.get("state"):
            out["state"] = state_view(env["state"])
        out["goalImage"] = goal_image
        out["goalImageNext"] = _goal(entries, goal_image, vision)
        out["nodeUrlTemplate"] = tpl
        if tpl:
            out["nodeUrlNote"] = URL_NOTE + ("; " + FIGMA_NOTE if "node-id={id}" in tpl else "")
        if warns:
            out["warnings"] = warns
        nxt = [NEXT_OUTLINE]
        if folded:
            nxt.append(NEXT_FOLDED)
        if any(u["reason"] == ABSOLUTE for u in entries):
            nxt.append(NEXT_UNPROVEN)
        out["next"] = "; ".join(nxt)
        return out

    # потолок дерева = четверть канона минус всё остальное в ответе (меряем с запасом: дерево «свёрнуто» по максимуму),
    # но не меньше бюджета секции: карта маленького экрана не должна ужиматься из-за постоянных частей ответа
    worst = {"leaves": True, "depth": len(m.order), "hiddenNodes": len(m.order)}
    rest = len(C.dumps(assemble([], worst)).encode("utf-8"))
    cap = max(m.W[root["id"]] // DIGEST_RATIO - rest, budget)
    lines, folded = _fit_tree(m, lights, cap)
    return assemble(lines, folded)


def _ancestor(n: dict) -> dict:
    """Контекст предка: раскладка и поверхность (то, что нужно, чтобы положить секцию и собрать оболочку над ней)."""
    o: Dict[str, Any] = {"id": n["id"], "kind": n["kind"]}
    if n.get("name"):
        o["name"] = n["name"]
    fn = _figma_name(n)
    if fn:
        o["figmaName"] = fn
    for key in ("tag", "box", "layout", "size", "style", "tokens"):
        if key in n:
            o[key] = n[key]
    return o


STATE_NEXT = ("секция кадра-состояния — РЕГИОН дельты, а не страница: state.region.kind added — рендерится в состоянии (поддерево node целиком); changed — правка на месте "
              "узла экрана baseId (changes: что изменилось; consequence-изменения размера и позиции не пинятся), дети node — стабы inferred:ref:…, их уже построил экран; "
              "код идёт ВНУТРЬ модуля экрана: переменная состояния из плана, условие на регион, переключатель; отдельной копии экрана и маршрута нет")


def _region_of(m: _Map, n: dict) -> dict:
    """Регион внутри крупного добавленного поддерева (секция глубже корня региона): к какому региону она относится."""
    for a in reversed(m.chain(n)):
        if isinstance(a.get("delta"), dict):
            return dict(a["delta"], regionRoot=a["id"])
    return {}


def _refuse_unknown(m: _Map, parts: List[Tuple[dict, int, bool, bool]], section_id: str, budget: Any) -> OutlineError:
    """Почему id не секция: узла нет / он внутри секции / он тяжелее бюджета и разрезан. Всегда с выходом — какой id брать."""
    n = m.by_id.get(section_id)
    if n is None:
        return OutlineError(f"узла «{section_id}» в каноне кадра нет: sectionId берётся только из sections[].id "
                            f"daiver_outline (id не придумываются и не берутся из памяти)")
    above = {a["id"] for a in m.chain(n)}
    for sec, _d, _o, _occ in parts:                                   # внутри секции: одна из секций — предок узла
        if sec["id"] in above:
            return OutlineError(f"«{section_id}» не граница секции при weightBudget={budget}: узел лежит внутри секции "
                                f"«{sec['id']}» ({sec.get('name', '')}) — запросите её id")
    inside = [sec["id"] for sec, _d, _o, _occ in parts if section_id in {a["id"] for a in m.chain(sec)}]
    shown = ", ".join(inside[:SECTION_ERROR_IDS]) + (" …" if len(inside) > SECTION_ERROR_IDS else "")
    return OutlineError(f"«{section_id}» тяжелее бюджета ({m.W[section_id]} > {budget}) и разрезан на секции "
                        f"(всего {len(inside)}); канон целиком не отдаётся — запросите одну из них: {shown}")


def build_section(env: dict, section_id: str, budget: Any, *, track: Optional[str] = None, frame: Optional[str] = None,
                  warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Ответ daiver_section (§9.3): section (мета), ancestors (root→parent), node (полное поддерево ≤ бюджета)."""
    m = _Map(env["root"])
    parts = partition(m, budget)
    hit = next(((n, d, o, occ) for n, d, o, occ in parts if n["id"] == section_id), None)
    if hit is None:
        raise _refuse_unknown(m, parts, section_id, budget)
    n, depth, oversize, occluded = hit
    out: Dict[str, Any] = {}
    if track is not None:
        out["track"] = track
    if frame is not None:
        out["frame"] = frame
    out.update({"weightBudget": budget, "section": _section_meta(m, n, depth, oversize, occluded),
                "ancestors": [_ancestor(a) for a in m.chain(n) if not a.get("deltaRoot")], "node": n})
    if env.get("state"):
        out["state"] = {"screen": env["state"]["screen"], "region": n.get("delta") or _region_of(m, n)}
    if warnings:
        out["warnings"] = list(warnings)
    nxt = ["строй ТОЛЬКО поддерево node; ancestors — контекст (раскладка и поверхность предков root→parent), "
           "в секцию они не входят; box есть только у абсолютных узлов"]
    if env.get("state"):
        nxt = [STATE_NEXT]
    if oversize:
        nxt.append("лист тяжелее бюджета: не режется, строится целиком")
    if occluded:
        nxt.append("секция под непрозрачным слоем (occluded) — не контент экрана, не строится")
    out["next"] = "; ".join(nxt)
    return out


# ── вход: трек → канон ───────────────────────────────────────────────────────

def _load(root: Path, track: str, frame: Optional[str]) -> Tuple[dict, dict, Dict[str, Path], dict, List[str]]:
    try:
        manifest = TR.load(root, track)
    except TR.TrackError as e:
        raise OutlineError(str(e))
    if manifest is None:
        raise OutlineError(f"трека «{track}» нет — сначала daiver_pixso_pull")
    try:
        f = TR.get_frame(manifest, frame)
    except TR.TrackError as e:
        raise OutlineError(str(e))
    paths = TR.frame_paths(root, manifest, f["name"])
    if not paths["canonical"].is_file():
        raise OutlineError(f"канона кадра «{f['name']}» нет — сначала daiver_normalize")
    try:
        env = C.read_canonical(paths["canonical"])
    except C.CanonError as e:
        raise OutlineError(f"канон кадра не читается ({e}) — запустите daiver_normalize заново")
    warnings: List[str] = []
    if f.get("stateOf"):
        res = STS.analyze(root, manifest, f["name"])
        if not res["available"]:
            raise OutlineError(f"кадр «{f['name']}» объявлен состоянием экрана «{res['screen']}», но состояние недоступно: {res['reason']}")
        env = dict(env, root=res["_delta"], state=STS.public(res))
    req = f.get("requirement") or {}
    src = (env.get("stats") or {}).get("sourceSha256")
    if src and req.get("sha256") and src != req["sha256"]:
        warnings.append("канон построен по другому экспорту, чем записан в манифесте (требование перетянуто) — "
                        "секции могли измениться: запустите daiver_normalize")
    return manifest, f, paths, env, warnings


def outline_frame(root: Path, track: str, frame: Optional[str] = None, weight_budget: Any = None) -> Dict[str, Any]:
    root = Path(root)
    _manifest, f, paths, env, warnings = _load(root, track, frame)
    budget, budget_warnings = effective_budget(root, weight_budget)
    goal = None
    if f.get("raster") and paths["raster"].is_file():
        goal = paths["raster"].resolve().relative_to(root.resolve()).as_posix()
    vision = bool(CFG.load(root)["config"]["verify"]["vision"])
    return build_outline(env, budget, track=track, frame=f["name"], frame_url=f.get("frameUrl"), goal_image=goal,
                         vision=vision, warnings=warnings + budget_warnings)


def section_frame(root: Path, track: str, frame: Optional[str], section_id: str,
                  weight_budget: Any = None) -> Dict[str, Any]:
    root = Path(root)
    if not isinstance(section_id, str) or not section_id:
        raise OutlineError("sectionId: непустая строка — id секции из sections[] daiver_outline")
    _manifest, f, _paths, env, warnings = _load(root, track, frame)
    budget, budget_warnings = effective_budget(root, weight_budget)
    return build_section(env, section_id, budget, track=track, frame=f["name"], warnings=warnings + budget_warnings)
