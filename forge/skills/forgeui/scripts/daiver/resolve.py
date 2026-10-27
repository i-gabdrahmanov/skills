"""resolve.py — firewall резолва: кандидаты, `daiver_resolve_query`, `daiver_resolve_finalize` (спека §10.1, §10.2, §10.4,
§20.4; задача 2.4; INV A7, B23, A2, A3; QG S11).

Резолвер (слабая модель в субагенте) выбирает компонент библиотеки для узла макета. Всё, что ему нельзя видеть или делать,
закрыто КОДОМ этого модуля, а не текстом брифа:
  • он видит не канон, а проекцию кандидата (`project`): имя, figmaName и варианты, КЛЮЧИ текстовых слотов (значений нет),
    figmaName/варианты вложенных инстансов как композиционный сигнал, текст до 40 знаков, отпечаток поддерева по kind.
    Проекция собирается из белого списка полей (геометрии, стилей, шрифтов, цветов, токенов в ней нет и взять их неоткуда),
    дерево ограничено (глубина 2, 48 узлов — §20.4) с флагом `truncated`, чужих узлов (предки, соседи) в ответе нет;
  • его вердикт не верится на слово (`check_verdict` + `finalize_frame`): форма проверяется схемой (все нарушения одним
    сообщением), узел обязан быть кандидатом кадра, при hit в карте вердикт обязан совпасть с правилом (карта сильнее вывода,
    INV-B17), для того же figmaName+variants берётся записанный вердикт (INV-B23), компонент без правила карты обязан
    существовать в библиотеке (INV-A2: ничего не выдумывать); принятое пишется в `resolutions.json` кадра атомарно и под
    замком (параллельные резолверы не теряют записи друг друга).

Кандидаты (§10.1, `scan`): все `kind=instance` и фреймы с осмысленными именами (`header_bf_01` — да; «Frame 123», «Group 5»,
«TEXT», «Rectangle» — нет: предикат `names.is_auto_name`, тот же, что у ingest); простые фреймы — div (раскладку ведёт план).
Не кандидаты: узел с `art` (это файл .svg), закрытое (`occluded`) вместе с поддеревом, внутренности библиотечного инстанса
(`childrenRole: content` — анатомию рисует сам компонент), синтетические узлы движка (`inferred:`), text/rect/vector/image.
Допущение сверх буквы §10.1: `kind=icon` с `component` — тоже кандидат (icon-only инстанс коллапсируется в иконку с сохранённым
component, §7.2; иконки резолвятся по карте/`icons.fallback`, §10.5, §13.5 п.5).

Переиспользование (INV-B23): ключ кандидата — `figmaName` (ключ имени карты: регистр и разделители не важны) + `variants`
(точное равенство). У инстанса/иконки с figmaName на ключ приходится ОДИН вердикт: первый записанный резолвером (`seq`) — эталон,
остальные узлы того же ключа получают его копией (`source: reuse`) — в этом кадре сразу при записи эталона, в других кадрах
трека при вызове `daiver_resolve_candidates`. Фреймы не переиспользуются (в спеке у них нет figmaName+variants).
Вердикт по правилу карты в переиспользование не идёт: карта сверяется с каждым узлом отдельно (узлы с тем же ключом и тем же
вердиктом правила получают запись `map` за один finalize, но эталоном для узлов без правила она не служит).

Хранилище — `resolutions.json` в папке кадра (`track.frame_paths(...)["resolutions"]`; у трека из одного кадра это
`.daiver/tracks/<KEY>/resolutions.json`): машинное хранилище движка, руками не правится; повреждённый файл — отказ с
названной причиной, а не молчаливый сброс. Ни времени, ни случайности: один и тот же порядок вызовов даёт побайтно один файл.
Запись узла проверяется при каждом чтении: она годится, только если узел всё ещё кандидат с тем же ключом, а у узла с hit
в карте её содержимое равно правилу (карту могли дополнить после вердикта).

Что НЕ делает этот модуль (граница с 2.5/7.2): политика подтверждений `workflow.confirmResolutions` («low — ВСЕГДА
спрашивается», INV-C6), `resolution-report.md` и согласие `ui-resolve-<KEY>` — `resolution_report.py` (2.5); `icons.fallback` здесь только
отдаётся в ответе query для иконки без правила; автономия (в ней low → local) — 7.2; state-кадры (§10.1 п.4–5) — 6.2/6.3. Вердикт хранит
`confidence` как есть; поля подтверждения человеком в записи нет: подтверждение привязано к тексту отчёта (контракт вердикта INV-A7 не меняется).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import canon as C
from . import component_map as CM
from . import config as CFG
from . import ds_lookup as DL
from . import outline as OL
from . import track as TR
from .names import is_auto_name

HOOKS = Path(__file__).resolve().parents[4] / "hooks"

# §20.4: «Файрвол resolve-запроса: depth 2, maxNodes 48, maxTextLen 40»
MAX_DEPTH = 2
MAX_NODES = 48
MAX_TEXT_LEN = 40
# остальное — наши решения (в спеке не названо): размеры строк проекции и ответов
MAX_NAME = 64                   # имя слоя и figmaName в проекции
MAX_PAIRS, MAX_PAIR_TEXT = 8, 32        # варианты: пар на узел, знаков в ключе/значении
MAX_SLOTS, MAX_SLOT_TEXT = 8, 32        # ключи текстовых слотов: штук на узел, знаков в ключе
TREE_CHARS = 8000               # потолок строк дерева в знаках: сверх него узлы отбрасываются как сверх MAX_NODES
MAX_RATIONALE = 400
MAX_PROPS = 30
MAX_PROP_VALUE = 200            # как у props правила карты (component_map.MAX_TEXT)
MAX_LIST = 100                  # групп нерешённых кандидатов в ответе daiver_resolve_candidates
MAX_PROPAGATED = 20             # id получивших вердикт копией, названных в ответе finalize

SCHEMA = 1
DECISIONS = ("component", "local", "ignore")
CONFIDENCE = CFG.CONFIDENCE
SOURCES = ("map", "resolver", "reuse")
# контракт вердикта (INV-A7): поля и обязательные
VERDICT_FIELDS = ("nodeId", "decision", "component", "props", "confidence", "rationale", "mapRuleProposal")
VERDICT_REQUIRED = ("nodeId", "decision", "confidence", "rationale")

SYNTHETIC_PREFIX = "inferred:"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")      # управляющие, кроме \t \n \r (их схлопывает _flat)
_CTRL_ANY = re.compile(r"[\x00-\x1f\x7f]")
_PROP_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_:-]*$")


class ResolveError(ValueError):
    """Вызов не годится, вердикт не принят или хранилище/карта не читаются — одним сообщением, с тем, что делать."""


# ── имена ────────────────────────────────────────────────────────────────────

def _flat(s: Any) -> str:
    """Строка в одну линию: управляющие знаки и пробельные последовательности (в том числе переводы строк) → один пробел."""
    return " ".join(_CTRL_ANY.sub(" ", str(s)).split())


def _clip(s: str, limit: int) -> str:
    """Не длиннее limit знаков, обрезка помечена «…» (она входит в limit)."""
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def meaningful_name(name: Any) -> bool:
    """Осмысленное имя слоя: не пустое, с буквой и не автогенерированное («Frame 123», «Group 5», «TEXT», «Rectangle»).

    Автоимена — `names.is_auto_name` (тот же предикат, что у ingest); сверх него — имя без единой буквы («24», «—») тоже
    ничего не называет."""
    if not isinstance(name, str):
        return False
    s = name.strip()
    return bool(s) and any(ch.isalpha() for ch in s) and not is_auto_name(s)


# ── кандидаты (§10.1) ────────────────────────────────────────────────────────

def _kids(n: dict) -> List[dict]:
    k = n.get("children")
    return [c for c in k if isinstance(c, dict)] if isinstance(k, list) else []


def _component(n: dict) -> Dict[str, Any]:
    comp = n.get("component")
    return comp if isinstance(comp, dict) else {}


def _figma_name(n: dict) -> Optional[str]:
    fn = _component(n).get("figmaName")
    return fn if isinstance(fn, str) and fn.strip() else None


def _variants_of(n: dict) -> Dict[str, str]:
    """Варианты узла: `component.variants`, иначе `variants` узла; только строки."""
    raw = _component(n).get("variants")
    if not isinstance(raw, dict):
        raw = n.get("variants")
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and k.strip() and isinstance(v, str)}


def _black_box(n: dict) -> bool:
    """Библиотечный инстанс с контентом: дети — анатомия, которую рисует сам компонент (то же условие, что `outline`)."""
    return n.get("kind") == "instance" and _component(n).get("childrenRole") == "content"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("&", "\\&").replace("=", "\\=")


def reuse_key(figma_name: str, variants: Dict[str, str]) -> str:
    """Ключ переиспользования (INV-B23): ключ имени карты (регистр и разделители не важны) + варианты как есть:
    `modalheader|Steps=OFF&Size=L`. «|», «&», «=» и «\\» внутри вариантов экранируются обратной чертой — ключ однозначен."""
    pairs = "&".join(f"{_esc(k)}={_esc(v)}" for k, v in sorted(variants.items()))
    return f"{CM.name_key(figma_name)}|{pairs}"


class Cand:
    """Кандидат резолва. `lookup` — имя для карты и библиотеки (figmaName инстанса, у фрейма — его имя);
    `reusable` — у ключа есть смысл «тот же компонент с теми же вариантами» (instance/icon с figmaName)."""
    __slots__ = ("id", "kind", "name", "lookup", "variants", "key", "reusable", "node")

    def __init__(self, n: dict):
        self.node = n
        self.id: str = n["id"]
        self.kind: str = n["kind"]
        self.name: str = n.get("name") if isinstance(n.get("name"), str) else ""
        fn = _figma_name(n)
        self.variants = _variants_of(n) if fn else {}
        self.reusable = fn is not None and self.kind in ("instance", "icon")
        self.lookup: str = _clip(_flat(fn if fn else self.name), CM.MAX_NAME)      # одна строка для карты и библиотеки
        self.key = reuse_key(self.lookup, self.variants) if self.reusable else f"{self.kind}:{CM.name_key(self.lookup)}"

    def as_line(self) -> str:
        """Строка списка кандидатов: `<id> <kind> "<имя>" [<figmaName> {варианты}]`."""
        parts = [self.id, self.kind, json.dumps(_clip(_flat(self.name), MAX_NAME), ensure_ascii=False)]
        if self.reusable:
            parts.append("<" + _clip(_flat(self.lookup), MAX_NAME) + ">")
            if self.variants:
                parts.append(_variants_text(self.variants))
        return " ".join(parts)


class Scan:
    """Результат обхода канона: кандидаты в порядке документа и причины, по которым остальные узлы не кандидаты."""
    __slots__ = ("cands", "by_id", "skip", "nodes")

    def __init__(self) -> None:
        self.cands: List[Cand] = []
        self.by_id: Dict[str, Cand] = {}
        self.skip: Dict[str, Tuple[str, str]] = {}          # id → (код причины, id узла-источника)
        self.nodes: Dict[str, dict] = {}


def _classify(n: dict) -> Tuple[Optional[Cand], Optional[Tuple[str, str]]]:
    nid, kind = n["id"], n["kind"]
    if nid.startswith(SYNTHETIC_PREFIX):
        return None, ("synthetic", nid)
    if "art" in n:
        return None, ("art", nid)
    if kind == "instance":
        return Cand(n), None
    if kind == "icon":
        return (Cand(n), None) if _figma_name(n) else (None, ("icon-bare", nid))
    if kind == "frame":
        return (Cand(n), None) if meaningful_name(n.get("name")) else (None, ("autoname", nid))
    return None, ("kind", nid)


def scan(env: dict) -> Scan:
    """Кандидаты канона и причины отказа остальным за один проход (итеративно: глубокие деревья не упираются в рекурсию)."""
    out = Scan()
    stack: List[Tuple[dict, Optional[Tuple[str, str]]]] = [(env["root"], None)]
    while stack:
        n, ctx = stack.pop()
        nid = n["id"]
        out.nodes[nid] = n
        child_ctx = ctx
        if ctx is not None:
            out.skip[nid] = ctx
        elif n.get("occluded") is True:
            out.skip[nid] = ("occluded", nid)
            child_ctx = ("occluded-in", nid)
        else:
            cand, why = _classify(n)
            if cand is not None:
                out.cands.append(cand)
                out.by_id[nid] = cand
            else:
                out.skip[nid] = why  # type: ignore[assignment]
            if _black_box(n):
                child_ctx = ("inside", nid)
        for k in reversed(_kids(n)):
            stack.append((k, child_ctx))
    return out


def candidates(env: dict) -> List[Cand]:
    return scan(env).cands


def why_not(sc: Scan, node_id: str, frame: Optional[str] = None) -> str:
    """Почему узел не кандидат — фраза для отказа (узла нет вовсе — отдельная фраза с подсказкой)."""
    tail = "Кандидаты кадра — daiver_resolve_candidates; id не придумываются"
    where = f" кадра «{frame}»" if frame else " кадра"
    if node_id not in sc.nodes:
        return f"узла «{node_id}» в каноне{where} нет. {tail}"
    code, ref = sc.skip[node_id]
    n = sc.nodes[node_id]
    if code == "occluded":
        return f"узел «{node_id}» закрыт непрозрачным слоем (occluded) — не контент экрана, резолвить нечего"
    if code == "occluded-in":
        return f"узел «{node_id}» лежит внутри закрытого слоя «{ref}» (occluded) — не контент экрана"
    if code == "art":
        return (f"узел «{node_id}» — векторная иллюстрация (art): это файл .svg (daiver_svg_assets), а не компонент "
                f"библиотеки")
    if code == "inside":
        box = sc.nodes[ref]
        return (f"узел «{node_id}» лежит внутри инстанса «{ref}» ({_figma_name(box) or box.get('name', '')}): анатомию "
                f"библиотечного компонента рисует сам компонент — кандидат инстанс «{ref}», не его внутренности")
    if code == "synthetic":
        return f"узел «{node_id}» — синтетическая обёртка движка (inferred:), не слой дизайнера"
    if code == "autoname":
        nm = _flat(n.get("name") or "")
        shown = f"«{_clip(nm, MAX_NAME)}» " if nm else "без имени "
        return (f"фрейм {shown}(узел «{node_id}») с автогенерированным или пустым именем — простой контейнер раскладки "
                f"(div), его строит план, резолвить нечего")
    if code == "icon-bare":
        return f"иконка «{node_id}» без component: по имени нечего сопоставлять"
    return (f"узел «{node_id}» — {n['kind']}: кандидаты — инстансы, иконки-инстансы и фреймы с осмысленными именами "
            f"(текст, прямоугольники, векторы и картинки не резолвятся)")


# ── проекция кандидата (§10.2) ───────────────────────────────────────────────

def _variants_text(v: Dict[str, str]) -> str:
    """`{Steps=OFF, Size=L}` — пары по ключу, не больше MAX_PAIRS; остальное названо числом."""
    items = sorted(v.items())
    shown = [f"{_clip(_flat(k), MAX_PAIR_TEXT)}={_clip(_flat(x), MAX_PAIR_TEXT)}" for k, x in items[:MAX_PAIRS]]
    if len(items) > MAX_PAIRS:
        shown.append(f"…+{len(items) - MAX_PAIRS}")
    return "{" + ", ".join(shown) + "}"


def slot_keys(n: dict) -> Tuple[List[Tuple[str, int]], int]:
    """КЛЮЧИ текстовых слотов узла без значений: [(ключ, сколько слотов с таким ключом)] в порядке появления и число
    слотов без имени. Слоты `component.content` ЗАМЕНЯЮТ textProps там, где присутствуют (§6.4)."""
    comp = _component(n)
    names: List[str] = []
    content = comp.get("content")
    if isinstance(content, list) and content:
        for slot in content:
            if isinstance(slot, dict):
                nm = slot.get("name")
                names.append(_flat(nm) if isinstance(nm, str) else "")
    else:
        for src in (comp.get("textProps"), n.get("textProps")):
            if isinstance(src, dict):
                names.extend(sorted(_flat(k) for k in src if isinstance(k, str)))
    order: List[str] = []
    count: Dict[str, int] = {}
    unnamed = 0
    for nm in names:
        if not nm:
            unnamed += 1
            continue
        if nm not in count:
            order.append(nm)
            count[nm] = 0
        count[nm] += 1
    return [(k, count[k]) for k in order], unnamed


def _slots_text(n: dict) -> str:
    keys, unnamed = slot_keys(n)
    shown = [_clip(k, MAX_SLOT_TEXT) + (f"×{c}" if c > 1 else "") for k, c in keys[:MAX_SLOTS]]
    if len(keys) > MAX_SLOTS:
        shown.append(f"…+{len(keys) - MAX_SLOTS}")
    if unnamed:
        shown.append(f"?×{unnamed}" if unnamed > 1 else "?")
    return ",".join(shown)


def _origin(n: dict) -> Optional[str]:
    o = _component(n).get("origin")
    return o if o in ("library", "local", "undecided") else None


def _text_of(n: dict) -> str:
    t = n.get("text")
    return _flat(t)[:MAX_TEXT_LEN] if isinstance(t, str) else ""


def node_line(n: dict) -> str:
    """Строка дерева проекции: kind, имя, у инстанса — <figmaName>, {варианты}, origin, slots= (ключи без значений),
    у текста — сам текст до MAX_TEXT_LEN знаков. Больше ничего: ни id, ни геометрии, ни стилей (белый список)."""
    kind = n["kind"]
    name = _clip(_flat(n.get("name") or ""), MAX_NAME)
    if kind == "text":
        text = _text_of(n)
        if text and _flat(n.get("name") or "") in ("", _flat(n.get("text") or "")):
            return f"text {json.dumps(text, ensure_ascii=False)}"       # имя слоя = текст (или пусто): один раз и до MAX_TEXT_LEN
        parts = ["text", json.dumps(name, ensure_ascii=False)]
        if text:
            parts.append("text=" + json.dumps(text, ensure_ascii=False))
        return " ".join(parts)
    parts = [kind, json.dumps(name, ensure_ascii=False)]
    fn = _figma_name(n)
    if fn and kind in ("instance", "icon"):
        parts.append("<" + _clip(_flat(fn), MAX_NAME) + ">")
        var = _variants_of(n)
        if var:
            parts.append(_variants_text(var))
        if _origin(n):
            parts.append(f"origin={_origin(n)}")
        slots = _slots_text(n)
        if slots:
            parts.append("slots=" + slots)
    if "art" in n:
        parts.append("art")
    return " ".join(parts)


def candidate_view(c: Cand) -> Dict[str, Any]:
    """Сам кандидат: то, что подставляется в daiver_map_lookup / daiver_ds_lookup (figmaName, variants, textSlotKeys)."""
    n = c.node
    out: Dict[str, Any] = {"id": c.id, "kind": c.kind, "name": _clip(_flat(c.name), MAX_NAME),
                           "figmaName": _clip(_flat(c.lookup), CM.MAX_NAME)}
    if c.variants:
        pairs = sorted(c.variants.items())[:MAX_PAIRS]
        out["variants"] = {_clip(_flat(k), MAX_PAIR_TEXT): _clip(_flat(v), MAX_PAIR_TEXT) for k, v in pairs}
    keys, unnamed = slot_keys(n)
    if keys:
        out["textSlotKeys"] = [_clip(k, MAX_SLOT_TEXT) for k, _ in keys[:MAX_SLOTS]]
    if unnamed:
        out["unnamedSlots"] = unnamed
    if c.reusable and _origin(n):
        out["origin"] = _origin(n)
    return out


def _shown_children(n: dict) -> List[dict]:
    """Видимые дети: закрытые (occluded) поддеревья не контент экрана и в проекцию не входят."""
    return [k for k in _kids(n) if k.get("occluded") is not True]


def fingerprint_counts(node: dict) -> Tuple[Dict[str, int], int]:
    """Отпечаток видимого поддерева по kind (без самого узла) и число узлов в закрытых поддеревьях, которые не вошли.
    Считается по ВСЕМУ поддереву — дерево проекции может быть обрезано, отпечаток от обрезки не зависит."""
    counts: Dict[str, int] = {}
    hidden = 0
    stack = list(_shown_children(node))
    hidden += sum(1 + C.descendants_count(k) for k in _kids(node) if k.get("occluded") is True)
    while stack:
        n = stack.pop()
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
        for k in _kids(n):
            if k.get("occluded") is True:
                hidden += 1 + C.descendants_count(k)
            else:
                stack.append(k)
    return counts, hidden


def _fingerprint_text(counts: Dict[str, int]) -> str:
    ordered = [k for k in C.KINDS if counts.get(k)] + sorted(k for k in counts if k not in C.KINDS)
    return " ".join(f"{k}={counts[k]}" for k in ordered)


def _sizes(root: dict) -> Dict[int, int]:
    """Размер видимого поддерева каждого узла (с ним самим), по id объекта."""
    order: List[dict] = []
    stack = [root]
    while stack:
        n = stack.pop()
        order.append(n)
        stack.extend(_shown_children(n))
    size: Dict[int, int] = {}
    for n in reversed(order):
        size[id(n)] = 1 + sum(size[id(k)] for k in _shown_children(n))
    return size


def project(c: Cand) -> Dict[str, Any]:
    """Проекция кандидата (§10.2): candidate, descendants, fingerprint, tree, truncated (+hidden, occluded).

    Дерево — потомки до глубины MAX_DEPTH, всего не больше MAX_NODES узлов с самим кандидатом и не больше TREE_CHARS знаков
    строк; узлы берутся по уровням (мелкие глубины раньше глубоких), в документе — в порядке документа. Что не вошло, названо:
    `… +N` на месте обрезки, `truncated`, `hidden`."""
    root = c.node
    counts, occluded = fingerprint_counts(root)
    total = sum(counts.values())
    sizes = _sizes(root)
    taken = 1
    budget = 0
    chosen: Dict[int, int] = {}                     # id узла → глубина (узел вошёл в дерево)
    queue = [(root, 0)]
    qi = 0
    stop = False
    while qi < len(queue) and not stop:
        n, d = queue[qi]
        qi += 1
        if d >= MAX_DEPTH:
            continue
        for k in _shown_children(n):
            line = node_line(k)
            if taken >= MAX_NODES or budget + len(line) + 1 > TREE_CHARS:
                stop = True
                break
            taken += 1
            budget += len(line) + 1
            chosen[id(k)] = d + 1
            queue.append((k, d + 1))
    lines: List[str] = []
    stack: List[Tuple[str, Any, int]] = [("node", root, 0)]
    while stack:
        what, item, d = stack.pop()
        if what == "cut":
            lines.append("  " * (d - 1) + f"… +{item}")
            continue
        if d > 0:
            lines.append("  " * (d - 1) + node_line(item))
        kids = _shown_children(item)
        cut = sum(sizes[id(k)] for k in kids if id(k) not in chosen)
        if cut:
            stack.append(("cut", cut, d + 1))                     # маркер — после показанных детей и их поддеревьев
        for k in reversed([k for k in kids if id(k) in chosen]):
            stack.append(("node", k, d + 1))
    hidden = total - (taken - 1)
    out: Dict[str, Any] = {"candidate": candidate_view(c), "descendants": total,
                           "fingerprint": _fingerprint_text(counts), "tree": lines, "truncated": hidden > 0}
    if hidden > 0:
        out["hidden"] = hidden
    if occluded:
        out["occluded"] = occluded
    return out


# ── вердикт (§10.2, INV-A7) ──────────────────────────────────────────────────

def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "булево"
    if isinstance(v, (int, float)):
        return "число"
    if isinstance(v, str):
        return "строка"
    if isinstance(v, list):
        return "список"
    if isinstance(v, dict):
        return "объект"
    return type(v).__name__


def _shown(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (str, int, float, bool)) or v is None else _kind(v)


def check_verdict(raw: Any) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Форма вердикта `{nodeId, decision, component?, props?, confidence, rationale, mapRuleProposal?}` → (чистый вердикт,
    []) либо (None, ВСЕ нарушения списком). Диска не касается. Строго: неизвестное поле — нарушение; `null` у необязательных
    полей читается как «нет»; rationale схлопывается в одну строку (пробелы, переводы строк) и ограничен MAX_RATIONALE."""
    if not isinstance(raw, dict):
        return None, [f"вердикт: ожидается объект {{{', '.join(VERDICT_FIELDS)}}}, получено: {_kind(raw)}"]
    problems: List[str] = []
    for k in raw:
        if k not in VERDICT_FIELDS:
            problems.append(f"неизвестное поле «{k}» (допустимы: {', '.join(VERDICT_FIELDS)})")
    out: Dict[str, Any] = {}

    nid = raw.get("nodeId")
    if not isinstance(nid, str) or not nid:
        problems.append("nodeId: непустая строка — id кандидата из daiver_resolve_candidates")
    else:
        out["nodeId"] = nid

    dec = raw.get("decision")
    dec_ok = isinstance(dec, str) and dec in DECISIONS
    if not dec_ok:
        problems.append(f"decision: {_shown(dec)} — ожидается {' | '.join(DECISIONS)}")
    else:
        out["decision"] = dec

    conf = raw.get("confidence")
    if not (isinstance(conf, str) and conf in CONFIDENCE):
        problems.append(f"confidence: {_shown(conf)} — ожидается {' | '.join(CONFIDENCE)} (честная уверенность, "
                        f"по умолчанию её нет)")
    else:
        out["confidence"] = conf

    comp = raw.get("component")
    if dec_ok and dec == "component":
        if not isinstance(comp, str) or not comp.strip():
            problems.append("component: decision=component требует имя компонента библиотеки")
        else:
            q = CM._component_problem(comp.strip())
            if len(comp.strip()) > CM.MAX_NAME:
                q = f"длиннее {CM.MAX_NAME} знаков"
            if q:
                problems.append(f"component «{_clip(_flat(comp), 60)}»: {q}")
            else:
                out["component"] = comp.strip()
    elif comp is not None and dec_ok:
        problems.append(f"component: при decision={dec} имени компонента быть не должно — оно бывает только у "
                        f"decision=component")

    props = raw.get("props")
    if props is not None:
        clean: Dict[str, Any] = {}
        if not isinstance(props, dict):
            problems.append(f"props: объект {{проп: значение}}, получено: {_kind(props)}")
        else:
            if len(props) > MAX_PROPS:
                problems.append(f"props: {len(props)} пропов, не больше {MAX_PROPS}")
            for k, x in props.items():
                if not isinstance(k, str) or not _PROP_KEY.match(k):
                    problems.append(f"props: «{_clip(_flat(k), 40)}» — не имя пропа")
                elif isinstance(x, bool):
                    clean[k] = x
                elif isinstance(x, (int, float)):
                    if math.isfinite(x):
                        clean[k] = x
                    else:
                        problems.append(f"props.{k}: число должно быть конечным")
                elif isinstance(x, str):
                    if len(x) > MAX_PROP_VALUE or _CTRL.search(x) or "\n" in x or "\r" in x:
                        problems.append(f"props.{k}: строка длиннее {MAX_PROP_VALUE} знаков или с переводом строки")
                    else:
                        clean[k] = x
                else:
                    problems.append(f"props.{k}: значение — строка, число или true/false, получено: {_kind(x)} "
                                    f"(вложенные значения — не уровень вердикта)")
            if clean and dec_ok and dec != "component":
                problems.append("props: пропы бывают только у decision=component")
        if clean and dec_ok and dec == "component":
            out["props"] = clean

    ra = raw.get("rationale")
    if not isinstance(ra, str):
        problems.append("rationale: обязателен — одна-две фразы, почему именно так (по имени, вариантам, составу)")
    elif _CTRL.search(ra):
        problems.append("rationale: управляющие символы недопустимы")
    else:
        text = _flat(ra)
        if not text:
            problems.append("rationale: пустой — одна-две фразы, почему именно так")
        elif len(text) > MAX_RATIONALE:
            problems.append(f"rationale: {len(text)} знаков, не больше {MAX_RATIONALE} (никаких простыней: "
                            f"одна-две фразы)")
        else:
            out["rationale"] = text

    proposal = raw.get("mapRuleProposal")
    if proposal is not None:
        if isinstance(proposal, dict):
            out["mapRuleProposal"] = proposal
        else:
            problems.append(f"mapRuleProposal: правило карты — объект {{match, outcome, …}}, получено: {_kind(proposal)}")

    return (None, problems) if problems else (out, [])


def _canon_value(v: Any) -> Any:
    """Значение для сравнения пропов: целое число с точкой = целое (JSON 1.0 и 1 — одно и то же)."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, dict):
        return {k: _canon_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_canon_value(x) for x in v]
    return v


def same_props(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    """Пропы равны строго (false ≠ 0, "1" ≠ 1); отсутствие и {} — одно и то же."""
    return json.dumps(_canon_value(a or {}), sort_keys=True, ensure_ascii=False) == \
        json.dumps(_canon_value(b or {}), sort_keys=True, ensure_ascii=False)


def same_verdict(a: Dict[str, Any], b: Dict[str, Any], confidence: bool = True) -> bool:
    """Решение совпало: decision, component, props (и confidence)."""
    if a.get("decision") != b.get("decision") or (a.get("component") or None) != (b.get("component") or None):
        return False
    if not same_props(a.get("props"), b.get("props")):
        return False
    return not confidence or a.get("confidence") == b.get("confidence")


def verdict_text(v: Dict[str, Any]) -> str:
    """Вердикт одной фразой для отказов: decision=component component=X props={…} confidence=high."""
    parts = [f"decision={v.get('decision')}"]
    if v.get("component"):
        parts.append(f"component={v['component']}")
    if v.get("props"):
        parts.append("props=" + json.dumps(v["props"], ensure_ascii=False, sort_keys=True))
    if v.get("confidence"):
        parts.append(f"confidence={v['confidence']}")
    return " ".join(parts)


# ── карта: один разбор на вызов ──────────────────────────────────────────────

class MapView:
    """Карта компонентов, разобранная один раз. Битая карта — отказ с её перечнем проблем: резолв без карты по догадке не
    идёт (файл правит человек). Нет файла — hit'ов нет."""

    def __init__(self, root: Path):
        try:
            mf = CM.load(root)
            self.parsed = mf.require_valid() if mf.exists else None
        except CM.MapError as e:
            raise ResolveError(str(e))
        self.wrapper = bool(self.parsed is not None and self.parsed.wrapper)
        self.icons_fallback: str = self.parsed.icons_fallback if self.parsed is not None else "ask"

    def hit(self, name: str) -> Optional[Dict[str, Any]]:
        if self.parsed is None or not CM.name_key(name):
            return None
        try:
            res = self.parsed.find(name)
        except CM.MapError as e:
            raise ResolveError(str(e))
        return res if res.get("hit") else None


def map_verdict(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Вердикт, который диктует правило карты (high, библиотека не читалась). Иконка — компонент (обёртка или компонент
    набора иконок); имя компонента у `local` карта хранит для матчера verify — к вердикту оно не относится."""
    if hit["outcome"] in ("component", "icon"):
        out: Dict[str, Any] = {"decision": "component", "component": hit["component"], "confidence": "high"}
        if hit.get("props"):
            out["props"] = dict(hit["props"])
        return out
    return {"decision": hit["outcome"], "confidence": "high"}


def _rule_text(hit: Dict[str, Any]) -> str:
    rule = hit.get("rule") or {}
    m = rule.get("match")
    shown = ", ".join(m) if isinstance(m, list) else str(m)
    return f"правило карты #{rule.get('index')} «{_clip(_flat(shown), 60)}»"


# ── хранилище вердиктов ──────────────────────────────────────────────────────

RECORD_EXTRA = ("source", "key", "kind", "name", "figmaName", "variants", "seq", "from", "fromFrame")
STORE_KEYS = ("schema", "seq", "verdicts")


def empty_store() -> Dict[str, Any]:
    return {"schema": SCHEMA, "seq": 0, "verdicts": {}}


def check_store(data: Any) -> List[str]:
    """Нарушения хранилища (пусто — годится). Запись обязана быть тем, что мог записать finalize."""
    if not isinstance(data, dict):
        return ["корень: ожидается объект {schema, seq, verdicts}"]
    problems = [f"неизвестное поле «{k}»" for k in data if k not in STORE_KEYS]
    if data.get("schema") != SCHEMA:
        problems.append(f"schema: ожидается {SCHEMA}, получено {_shown(data.get('schema'))}")
    seq = data.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        problems.append("seq: целое число ≥ 0")
        seq = 0
    verdicts = data.get("verdicts")
    if not isinstance(verdicts, dict):
        problems.append("verdicts: ожидается объект {nodeId: запись}")
        return problems
    for nid, rec in verdicts.items():
        where = f"verdicts[{nid}]"
        if not isinstance(rec, dict):
            problems.append(f"{where}: ожидается объект")
            continue
        extra = [k for k in rec if k not in VERDICT_FIELDS and k not in RECORD_EXTRA]
        problems += [f"{where}: неизвестное поле «{k}»" for k in extra]
        _, bad = check_verdict({k: rec[k] for k in VERDICT_FIELDS if k in rec})
        problems += [f"{where}: {b}" for b in bad]
        if rec.get("nodeId") != nid:
            problems.append(f"{where}: nodeId записи не совпадает с ключом")
        if rec.get("source") not in SOURCES:
            problems.append(f"{where}: source — {' | '.join(SOURCES)}")
        if not isinstance(rec.get("key"), str) or not rec.get("key"):
            problems.append(f"{where}: key — непустая строка")
        s = rec.get("seq")
        if isinstance(s, bool) or not isinstance(s, int) or s < 1 or s > seq:
            problems.append(f"{where}: seq — целое от 1 до seq хранилища")
        if rec.get("source") == "reuse" and not isinstance(rec.get("from"), str):
            problems.append(f"{where}: у reuse нет from (узел-эталон)")
        if len(problems) > 12:
            break
    return problems


def store_path(root: Path, manifest: dict, frame_name: str) -> Path:
    return TR.frame_paths(root, manifest, frame_name)["resolutions"]


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_store(root: Path, path: Path) -> Dict[str, Any]:
    """Хранилище кадра; нет файла — пустое. Повреждённый файл — отказ (молчаливый сброс потерял бы вердикты)."""
    if not path.is_file():
        return empty_store()
    rel = _rel(root, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ResolveError(f"{rel} не читается ({e}). Это машинное хранилище вердиктов: руками не правится")
    except ValueError as e:
        raise ResolveError(f"{rel} не JSON ({e}). Это машинное хранилище вердиктов: руками не правится; "
                           f"повреждённый файл можно удалить — вердикты будут приняты заново")
    problems = check_store(data)
    if problems:
        tail = f"; …и ещё {len(problems) - 4}" if len(problems) > 4 else ""
        raise ResolveError(f"{rel} не проходит проверку ({len(problems)}): " + "; ".join(problems[:4]) + tail
                           + ". Файл руками не правится; повреждённый можно удалить — вердикты будут приняты заново")
    return data


def write_store(root: Path, path: Path, store: Dict[str, Any]) -> None:
    """Атомарная запись (tmp + replace), ключи отсортированы, времени нет: тот же порядок вызовов — те же байты."""
    text = json.dumps(store, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))                  # байтами: без перевода строк платформы
        os.replace(str(tmp), str(path))
    except OSError as e:
        raise ResolveError(f"запись {_rel(root, path)} не удалась ({e}); вердикт не записан")
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _lock(root: Path):
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    import _project as P       # noqa: WPS433 — замки control-plane forge (hooks/ рядом со skills/)
    return P.exclusive_lock(P.ground_dir(root) / "resolve.lock")


def make_record(c: Cand, verdict: Dict[str, Any], source: str, seq: int, origin: Optional[Tuple[str, Optional[str]]] = None
                ) -> Dict[str, Any]:
    """Запись хранилища: вердикт + откуда он (source), по какому ключу и о каком узле (для отчёта резолва 2.5)."""
    rec: Dict[str, Any] = {k: v for k, v in verdict.items() if k in VERDICT_FIELDS}
    if origin is not None:
        rec.pop("mapRuleProposal", None)           # предложение правила принадлежит узлу-эталону, копия его не несёт
    rec["nodeId"] = c.id
    rec.update({"source": source, "key": c.key, "kind": c.kind, "seq": seq})
    if c.name:
        rec["name"] = _clip(_flat(c.name), CM.MAX_NAME)
    if c.reusable:
        rec["figmaName"] = _clip(_flat(c.lookup), CM.MAX_NAME)
        if c.variants:
            rec["variants"] = dict(c.variants)
    if origin is not None:
        rec["from"] = origin[0]
        if origin[1]:
            rec["fromFrame"] = origin[1]
    return rec


# ── состояние кадра: что решено, что устарело, что осталось ───────────────────

class State:
    """Разбор хранилища против канона и карты. `valid` — годные записи по id; `stale` — id → почему запись не годится;
    `todo` — кандидаты без годной записи; `hits` — hit карты по id кандидата."""
    __slots__ = ("sc", "store", "hits", "valid", "stale", "todo", "local", "foreign", "index")

    def authority(self, key: str) -> Optional[Tuple[Dict[str, Any], Optional[str]]]:
        """Эталон ключа: самая ранняя годная запись резолвера (кадр по порядку манифеста, затем seq). → (запись, имя кадра,
        если эталон из другого кадра)."""
        best: Optional[Tuple[Tuple[int, int], Dict[str, Any], Optional[str]]] = None
        mine = self.local.get(key)
        if mine is not None:
            best = ((self.index, mine["seq"]), mine, None)
        other = self.foreign.get(key)
        if other is not None:
            order = (other[0], other[1]["seq"])
            if best is None or order < best[0]:
                best = (order, other[1], other[2])
        return (best[1], best[2]) if best else None


def analyze(sc: Scan, store: Dict[str, Any], mv: MapView, foreign: Dict[str, Tuple[int, Dict[str, Any], str]],
            index: int) -> State:
    st = State()
    st.sc, st.store, st.foreign, st.index = sc, store, foreign, index
    st.hits, st.valid, st.stale, st.local = {}, {}, {}, {}
    deferred: List[Tuple[Cand, Dict[str, Any]]] = []
    for c in sc.cands:
        hit = mv.hit(c.lookup)
        st.hits[c.id] = hit
        rec = store["verdicts"].get(c.id)
        if rec is None:
            continue
        if rec["key"] != c.key:
            st.stale[c.id] = "узел изменился после вердикта (другое имя или варианты)"
        elif hit is not None:
            if same_verdict(rec, map_verdict(hit)):
                st.valid[c.id] = rec
            else:
                st.stale[c.id] = f"{_rule_text(hit)} расходится с записанным вердиктом"
        elif rec["source"] == "map":
            st.stale[c.id] = "правила карты, по которому записан вердикт, больше нет"
        elif rec["source"] == "reuse":
            deferred.append((c, rec))
        else:
            st.valid[c.id] = rec
            if c.reusable:
                best = st.local.get(c.key)
                if best is None or rec["seq"] < best["seq"]:
                    st.local[c.key] = rec
    for c, rec in deferred:
        auth = st.authority(c.key)
        if auth is not None and same_verdict(rec, auth[0]):
            st.valid[c.id] = rec
        else:
            st.stale[c.id] = "вердикт-эталон для этого figmaName и вариантов изменился или исчез"
    st.todo = [c for c in sc.cands if c.id not in st.valid]
    return st


def foreign_authorities(root: Path, manifest: dict, frame_name: str) -> Tuple[Dict[str, Tuple[int, Dict[str, Any], str]], int]:
    """Эталоны ключей из ДРУГИХ кадров трека (INV-B23: библиотека между кадрами не менялась): ключ → (номер кадра в манифесте,
    запись резолвера, имя кадра), самый ранний кадр и seq. Записи чужих кадров не перепроверяются по их канону —
    ключ сам и есть свидетельство. Возвращает и номер текущего кадра."""
    frames = manifest.get("frames") or []
    here = 0
    out: Dict[str, Tuple[int, Dict[str, Any], str]] = {}
    for i, f in enumerate(frames):
        if f["name"] == frame_name:
            here = i
            continue
        path = store_path(root, manifest, f["name"])
        if not path.is_file():
            continue
        other = read_store(root, path)
        for rec in other["verdicts"].values():
            if rec["source"] != "resolver" or "|" not in rec["key"]:
                continue                        # переиспользуются только вердикты резолвера по ключу инстанса (`имя|варианты`)
            cur = out.get(rec["key"])
            if cur is None or (i, rec["seq"]) < (cur[0], cur[1]["seq"]):
                out[rec["key"]] = (i, rec, f["name"])
    return out, here


# ── вход: трек → канон, карта, хранилище ─────────────────────────────────────

class Ctx:
    """Всё, что нужно инструменту про кадр: канон, обход, карта, путь хранилища."""
    __slots__ = ("root", "track", "manifest", "frame", "fname", "env", "sc", "mv", "spath", "warnings")

    def __init__(self, root: Any, track: str, frame: Optional[str]):
        self.root = Path(root)
        self.track = track
        try:
            self.manifest, self.frame, paths, self.env, warns = OL._load(self.root, track, frame)
        except OL.OutlineError as e:
            raise ResolveError(str(e))
        self.fname: str = self.frame["name"]
        self.sc = scan(self.env)
        self.mv = MapView(self.root)
        self.spath = paths["resolutions"]
        self.warnings: List[str] = list(warns)

    def state(self, store: Dict[str, Any]) -> State:
        foreign, idx = foreign_authorities(self.root, self.manifest, self.fname)
        return analyze(self.sc, store, self.mv, foreign, idx)


def _view(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Запись для ответа: решение + откуда оно (без служебных ключей хранилища)."""
    out = {k: rec[k] for k in ("decision", "component", "props", "confidence", "rationale") if rec.get(k) not in (None, {})}
    out["source"] = rec["source"]
    if rec.get("from"):
        out["from"] = rec["from"]
        if rec.get("fromFrame"):
            out["fromFrame"] = rec["fromFrame"]
    return out


def _group_key(c: Cand, hit: Optional[Dict[str, Any]]) -> Optional[str]:
    """Кандидаты одной группы решаются одним вердиктом: тот же ключ (figmaName+variants) и, если есть правило карты, тот же
    диктуемый им вердикт. Фреймы и инстансы без figmaName в группы не входят."""
    if not c.reusable:
        return None
    if hit is None:
        return c.key
    return c.key + "|map|" + json.dumps(map_verdict(hit), sort_keys=True, ensure_ascii=False)


def _propagate_reuse(st: State, store: Dict[str, Any], key: str) -> List[str]:
    """Копии эталона ключа узлам кадра, у которых нет годной записи и нет правила карты (INV-B23). → id получивших."""
    auth = st.authority(key)
    if auth is None:
        return []
    rec, from_frame = auth
    got: List[str] = []
    for c in st.todo:
        if not c.reusable or c.key != key or st.hits[c.id] is not None:
            continue
        if from_frame is None and rec["nodeId"] == c.id:
            continue
        store["seq"] += 1
        store["verdicts"][c.id] = make_record(c, rec, "reuse", store["seq"], (rec["nodeId"], from_frame))
        got.append(c.id)
    return got


def _propagate_map(st: State, store: Dict[str, Any], src: Cand, rec: Dict[str, Any]) -> List[str]:
    """Узлам кадра с тем же ключом и тем же вердиктом карты — запись `map` (правило одно, перерешать нечего)."""
    want = map_verdict(st.hits[src.id])
    got: List[str] = []
    for c in st.todo:
        hit = st.hits.get(c.id)
        if c is src or not c.reusable or c.key != src.key or hit is None or not same_verdict(map_verdict(hit), want):
            continue
        store["seq"] += 1
        store["verdicts"][c.id] = make_record(c, rec, "map", store["seq"], (src.id, None))
        got.append(c.id)
    return got


# ── daiver_resolve_candidates ────────────────────────────────────────────────

def _shareable(st: State) -> List[str]:
    """Ключи нерешённых узлов, у которых уже есть эталон (и нет правила карты, которое сильнее)."""
    keys = set()
    for c in st.todo:
        if c.reusable and st.hits[c.id] is None:
            auth = st.authority(c.key)
            if auth is not None and not (auth[1] is None and auth[0]["nodeId"] == c.id):
                keys.add(c.key)
    return sorted(keys)


def _sync(ctx: Ctx) -> State:
    """Состояние кадра после раздачи эталонов: узлы, чей figmaName+variants уже решён (в этом или раннем кадре), получают
    вердикт копией. Замок берётся и файл пишется только когда есть что раздать (читающий вызов следов не оставляет)."""
    st = ctx.state(read_store(ctx.root, ctx.spath))
    if not _shareable(st):
        return st
    with _lock(ctx.root):
        store = read_store(ctx.root, ctx.spath)             # свежие данные под замком
        st = ctx.state(store)
        got: List[str] = []
        for key in _shareable(st):
            got += _propagate_reuse(st, store, key)
        if got:
            write_store(ctx.root, ctx.spath, store)
            st = ctx.state(store)
        return st


def candidates_frame(root: Any, track: str, frame: Optional[str] = None) -> Dict[str, Any]:
    """Кандидаты кадра, которые ещё надо решить (§10.1). Одинаковые figmaName+variants — одной строкой `×N`."""
    ctx = Ctx(root, track, frame)
    st = _sync(ctx)
    groups: List[List[Any]] = []
    where: Dict[str, int] = {}
    for c in st.todo:
        gk = _group_key(c, st.hits[c.id])
        if gk is not None and gk in where:
            groups[where[gk]][1] += 1
            continue
        if gk is not None:
            where[gk] = len(groups)
        groups.append([c, 1])
    lines = [g[0].as_line() + (f" ×{g[1]}" if g[1] > 1 else "") + (" [stale]" if g[0].id in st.stale else "")
             for g in groups[:MAX_LIST]]
    total = len(ctx.sc.cands)
    stats = {"candidates": total, "resolved": total - len(st.todo), "todo": len(st.todo), "groups": len(groups)}
    out: Dict[str, Any] = {"track": track, "frame": ctx.fname, "stats": stats, "candidates": lines}
    if len(groups) > MAX_LIST:
        out["more"] = len(groups) - MAX_LIST
    stale = [c.id for c in st.todo if c.id in st.stale]
    if stale:
        out["stale"] = {i: st.stale[i] for i in stale[:5]}
    if ctx.warnings:
        out["warnings"] = ctx.warnings
    if not total:
        out["next"] = "кандидатов в кадре нет (ни инстансов, ни фреймов с осмысленными именами) — резолвить нечего"
    elif not st.todo:
        out["next"] = ("все кандидаты кадра решены — резолв закончен; вердикты ушли в хранилище кадра (resolutions.json); "
                       "дальше daiver_resolve_report (в треке из нескольких кадров — когда решены все)")
    else:
        out["next"] = ("строка → daiver_resolve_query(nodeId), затем daiver_resolve_finalize. «×N» — N узлов с тем же figmaName "
                       "и вариантами: решай один, остальные получат вердикт сами. «[stale]» — прежний вердикт не годится "
                       "(причина в stale), решай заново"
                       + ("; список обрезан (more) — остальное появится по мере решения" if len(groups) > MAX_LIST else ""))
    return out


# ── daiver_resolve_query ─────────────────────────────────────────────────────

def _icon_next(fallback: str) -> str:
    """Иконка без правила карты (§10.5, §13.5 п.5): угаданная библиотечная иконка и рукописный `<svg>` запрещены, поэтому
    библиотеку по ней не листают; применяется `icons.fallback` карты."""
    how = ("плейсхолдер с именем иконки без вопроса" if fallback == "placeholder"
           else "плейсхолдер с именем иконки, человек увидит его в списке подтверждений (daiver_resolve_report)")
    return (f"иконка без правила в карте: библиотеку не угадывай. icons.fallback={fallback}: decision=local ({how}); в rationale "
            f"назови иконку. Компонент иконки из набора называет только пользователь (правка к списку подтверждений): это правило "
            f"карты (outcome icon), а не вердикт. Затем daiver_resolve_finalize")


def _next_for(ctx: Ctx, hit: Optional[Dict[str, Any]], rec: Optional[Dict[str, Any]], has_reuse: bool,
              icon: bool = False) -> str:
    if rec is not None:
        if rec["source"] == "reuse":
            return (f"вердикт этого узла взят у узла «{rec['from']}» (recorded, INV-B23); новый резолв не нужен — менять "
                    f"решение надо на узле-эталоне")
        return ("вердикт этого узла уже записан (recorded); новый резолв не нужен. Если пользователь просит иное — "
                "daiver_resolve_finalize перезапишет его")
    if has_reuse:
        return ("для того же figmaName и вариантов вердикт уже записан (reuse): заново не решай — "
                "daiver_resolve_finalize с этим вердиктом (nodeId — этого узла), другой код отклонит (INV-B23)")
    if hit is not None:
        return ("правило карты сработало (map): вердикт high по нему, библиотеку не читай — decision/component/props как в map; "
                "затем daiver_resolve_finalize (расхождение с картой код отклонит)")
    if icon:
        return _icon_next(ctx.mv.icons_fallback)
    lib = CFG.load(ctx.root)["config"]["library"]
    if lib.get("source") == "storybook-mcp":
        how = f"решай по MCP-серверу сторибука «{lib.get('storybookMcp')}»"
    else:
        how = ("daiver_ds_lookup(figmaName, variants, textSlotKeys из candidate), финалистов — daiver_ds_details; "
               "пусто — это local")
    return f"правила в карте нет: {how}; затем daiver_resolve_finalize. Геометрии и стилей здесь нет — для выбора они не нужны"


def _screen_verdict(ctx: "Ctx", cand: Cand) -> Optional[Dict[str, Any]]:
    """Кадр-состояние (6.2): changed-регион — тот же узел, что на экране, но с другим вариантом/текстом. Вердикт экрана по базовому узлу — подсказка: тот же компонент остаётся
    (меняется проп под новый вариант), а не решается заново. Решает по-прежнему резолвер: подсказка вердикта не пишет."""
    delta = cand.node.get("delta") if isinstance(cand.node.get("delta"), dict) else None
    screen = ctx.frame.get("stateOf")
    if not delta or delta.get("kind") != "changed" or not screen or not delta.get("baseId"):
        return None
    try:
        path = store_path(ctx.root, ctx.manifest, screen)
        store = read_store(ctx.root, path) if path.is_file() else None
    except (ResolveError, TR.TrackError):
        return None
    rec = (store or {}).get("verdicts", {}).get(delta["baseId"])
    if not rec:
        return None
    return {"frame": screen, "nodeId": delta["baseId"], "changes": delta.get("changes"), **_view(rec),
            "hint": "узел — тот же, что на экране, но изменился: компонент остаётся тем же (decision component, тот же component), уточни props под новый вариант/текст из changes"}


def query_frame(root: Any, track: str, frame: Optional[str], node_id: Any) -> Dict[str, Any]:
    """Проекция ОДНОГО кандидата + результат карты (§10.2). Читает, не пишет."""
    if not isinstance(node_id, str) or not node_id:
        raise ResolveError("nodeId: непустая строка — id кандидата из daiver_resolve_candidates")
    ctx = Ctx(root, track, frame)
    cand = ctx.sc.by_id.get(node_id)
    if cand is None:
        raise ResolveError(why_not(ctx.sc, node_id, ctx.fname))
    proj = project(cand)
    if CM.name_key(cand.lookup):
        try:
            map_res = CM.lookup(ctx.root, cand.lookup, proj["candidate"].get("variants"))
        except CM.MapError as e:                      # карта сказала, что не может ответить, — резолв стоит, а не идёт «как без карты»
            raise ResolveError(str(e))
    else:
        map_res = {"hit": False, "note": "в имени нет ни букв, ни цифр — по карте такое не ищется; решай по библиотеке"}
    st = ctx.state(read_store(ctx.root, ctx.spath))
    out: Dict[str, Any] = {"track": track, "frame": ctx.fname}
    out.update(proj)
    out["map"] = map_res
    rec = st.valid.get(cand.id)
    reuse = None
    if rec is not None:
        out["recorded"] = _view(rec)
    else:
        if cand.id in st.stale:
            out["recordStale"] = st.stale[cand.id]
        if cand.reusable and st.hits[cand.id] is None:
            auth = st.authority(cand.key)
            if auth is not None and not (auth[1] is None and auth[0]["nodeId"] == cand.id):
                reuse = {"from": auth[0]["nodeId"], "verdict": _view(auth[0])}
                if auth[1]:
                    reuse["fromFrame"] = auth[1]
                out["reuse"] = reuse
    screen_verdict = _screen_verdict(ctx, cand)
    if screen_verdict is not None:
        out["screenVerdict"] = screen_verdict
    icon = cand.kind == "icon" and st.hits[cand.id] is None
    if icon and rec is None and reuse is None:
        out["iconFallback"] = ctx.mv.icons_fallback
    if ctx.warnings:
        out["warnings"] = ctx.warnings
    out["next"] = _next_for(ctx, st.hits[cand.id], rec, reuse is not None, icon)
    return out


# ── daiver_resolve_finalize ──────────────────────────────────────────────────

def _refusal(problems: List[str]) -> str:
    body = "; ".join(f"{i}) {t}" for i, t in enumerate(problems[:8], 1))
    more = f"; …и ещё {len(problems) - 8}" if len(problems) > 8 else ""
    return (f"вердикт не принят ({len(problems)}): {body}{more}. Запись не изменена — исправь всё перечисленное сразу "
            f"и вызови снова")


def _near_names(root: Path, name: str) -> str:
    try:
        found = DL.lookup(root, name, None, None, 3)["candidates"]
    except DL.DsLookupError:
        return ""
    return ", ".join(c["name"] for c in found)


def _library_problems(root: Path, v: Dict[str, Any]) -> List[str]:
    """Компонент эвристического вердикта обязан существовать в библиотеке (INV-A2: ничего не выдумывать). Проверка идёт по
    требованиям ДС (stories | sources); при storybook-mcp документов нет — имя проверяет MCP-сервер в процедуре резолвера."""
    if v["decision"] != "component":
        return []
    cfg = CFG.load(root)
    if not cfg["initialized"]:
        return ["проект не инициализирован (сначала daiver_init): без конфига компонент не с чем сверить с библиотекой"]
    if cfg["config"]["library"]["source"] == "storybook-mcp":
        return []
    try:
        lib = DL.load(root)
    except DL.DsLookupError as e:
        return [str(e)]
    comp = v["component"]
    if comp in lib.comps or comp.split(".")[0] in lib.comps:
        return []
    near = _near_names(root, comp.split(".")[0])
    return [f"компонента «{comp}» нет в библиотеке (требования ДС): имя берётся только из daiver_ds_lookup / "
            f"daiver_ds_inventory" + (f"; похожие по имени: {near}" if near else "")
            + ". Подходящего нет — decision=local"]


def _proposal_problems(ctx: Ctx, v: Dict[str, Any], c: Cand) -> List[str]:
    """mapRuleProposal обязан пройти ту же проверку, что и daiver_map_append (иначе человек согласится на правило, которое
    не запишется), не противоречить вердикту и покрывать сам узел."""
    p = v.get("mapRuleProposal")
    if p is None:
        return []
    try:
        CM.append(ctx.root, ctx.track, p, True)
    except CM.MapError as e:
        return ["mapRuleProposal не проходит проверку daiver_map_append: " + str(e).split(". Файл не тронут")[0]]
    rule, _bad = CM.check_rule(p, 0, wrapper=ctx.mv.wrapper)
    if rule is None:
        return []
    out: List[str] = []
    dec = {"component": "component", "icon": "component", "local": "local", "ignore": "ignore"}[rule.outcome]
    if dec != v["decision"]:
        out.append(f"mapRuleProposal: outcome «{rule.outcome}» противоречит decision={v['decision']}")
    elif rule.outcome == "component" and "$" not in (rule.component or "") and rule.component != v.get("component"):
        out.append(f"mapRuleProposal: component «{rule.component}» ≠ component вердикта «{v.get('component')}»")
    if rule.kind == "regex":
        covered = bool(rule.regex.search(c.lookup))
    else:
        covered = CM.name_key(c.lookup) in rule.keys
    if not covered:
        out.append(f"mapRuleProposal: правило не покрывает этот узел (его имя для карты — «{c.lookup}»)")
    return out


def _semantic(ctx: Ctx, v: Dict[str, Any], c: Cand, st: State) -> Tuple[List[str], str, Optional[Tuple[Dict[str, Any], Optional[str]]]]:
    """Вердикт против того, что сильнее модели: правило карты, записанный эталон ключа, библиотека. → (нарушения, source,
    эталон для копии)."""
    problems: List[str] = []
    hit = st.hits[c.id]
    if hit is not None:
        want = map_verdict(hit)
        if not same_verdict(v, want):
            problems.append(f"{_rule_text(hit)} диктует вердикт «{verdict_text(want)}», а у тебя «{verdict_text(v)}»: карта "
                            f"сильнее вывода (INV-B17), расходиться с ней может только человек правкой карты")
        problems += _proposal_problems(ctx, v, c)
        return problems, "map", None
    auth = st.authority(c.key) if c.reusable else None
    if auth is not None and not (auth[1] is None and auth[0]["nodeId"] == c.id):
        rec, from_frame = auth
        if not same_verdict(v, rec, confidence=False):
            where = f"узел «{rec['nodeId']}»" + (f", кадр «{from_frame}»" if from_frame else "")
            problems.append(f"для figmaName «{c.lookup}» с теми же variants вердикт уже записан ({where}): "
                            f"{verdict_text(rec)}; у тебя «{verdict_text(v)}». INV-B23: тот же компонент с теми же вариантами "
                            f"решается один раз — принимается только он; изменить решение можно, перерешив узел-эталон")
        problems += _proposal_problems(ctx, v, c)
        return problems, "reuse", auth
    problems += _library_problems(ctx.root, v)
    problems += _proposal_problems(ctx, v, c)
    return problems, "resolver", None


def finalize_frame(root: Any, track: str, frame: Optional[str], raw: Any) -> Dict[str, Any]:
    """Принять вердикт по ОДНОМУ кандидату и записать его (§10.2). Любое нарушение — отказ целиком, одним сообщением."""
    ctx = Ctx(root, track, frame)
    clean, problems = check_verdict(raw)
    nid = raw.get("nodeId") if isinstance(raw, dict) else None
    cand = ctx.sc.by_id.get(nid) if isinstance(nid, str) else None
    if isinstance(nid, str) and nid and cand is None:
        problems.append(why_not(ctx.sc, nid, ctx.fname))
    with _lock(ctx.root):
        store = read_store(ctx.root, ctx.spath)
        st = ctx.state(store)
        source, auth = "resolver", None
        if clean is not None and cand is not None:
            more, source, auth = _semantic(ctx, clean, cand, st)
            problems += more
        if problems:
            raise ResolveError(_refusal(problems))
        assert clean is not None and cand is not None
        old = store["verdicts"].get(cand.id)
        keep_seq = old is not None and old["key"] == cand.key and old["source"] == source      # повтор вызова — те же байты
        if not keep_seq:
            store["seq"] += 1
        seq = old["seq"] if keep_seq else store["seq"]
        if source == "reuse":
            assert auth is not None
            rec = make_record(cand, auth[0], "reuse", seq, (auth[0]["nodeId"], auth[1]))
        else:
            rec = make_record(cand, clean, source, seq)
        store["verdicts"][cand.id] = rec
        st2 = ctx.state(store)
        got: List[str] = []
        if source == "resolver":
            got = _propagate_reuse(st2, store, cand.key)
        elif source == "map":
            got = _propagate_map(st2, store, cand, rec)
        write_store(ctx.root, ctx.spath, store)
        st3 = ctx.state(store) if got else st2
    out: Dict[str, Any] = {"ok": True, "track": track, "frame": ctx.fname, "nodeId": cand.id, "recorded": _view(rec)}
    if source == "reuse":
        out["note"] = (f"вердикт взят у узла «{rec['from']}» (INV-B23): тот же figmaName и варианты решаются один раз; "
                       f"твои confidence и rationale в запись не вошли")
    if got:
        out["reused"] = got[:MAX_PROPAGATED]
        if len(got) > MAX_PROPAGATED:
            out["reusedCount"] = len(got)
    out["remaining"] = len(st3.todo)
    if ctx.warnings:
        out["warnings"] = ctx.warnings
    out["next"] = ("все кандидаты кадра решены — дальше daiver_resolve_report (в треке из нескольких кадров — когда решены все)"
                   if not st3.todo else
                   f"осталось нерешённых кандидатов: {len(st3.todo)} — daiver_resolve_candidates покажет их")
    return out


# ── для следующих задач (2.5 отчёт, 2.6 план) ────────────────────────────────

def verdicts(root: Any, track: str, frame: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Годные вердикты кадра по id узла (полные записи хранилища). Только чтение: вердикты других кадров по INV-B23 раздаёт
    `daiver_resolve_candidates` (он же показывает, что осталось решить)."""
    ctx = Ctx(root, track, frame)
    st = ctx.state(read_store(ctx.root, ctx.spath))
    return {c.id: st.valid[c.id] for c in ctx.sc.cands if c.id in st.valid}
