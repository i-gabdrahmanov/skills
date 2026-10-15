"""ingest/materialize.py — что РЕНДЕРИТ инстанс: тексты мастера × оверрайды инстанса (спека §7.3, задача 1.4).

В Pixso-экспорте дерево экрана содержит инстансы БЕЗ детей: вся анатомия и все тексты живут в
определениях компонентов, а то, что человек написал на экране, — в `props[]` инстанса. Без
материализации канон пуст, и сравнивать с макетом нечего («картинка против разметки», §6.4).

Модель оверрайдов (выведена по образцу «Modal» и сверена с его картинкой, а не угадана):
  • запись `props[]` адресуется `pathString` — цепочка guid через «/»: первый узел лежит в мастере
    инстанса (прямой ребёнок варианта), каждый следующий — в мастере предыдущего звена (вложенного
    инстанса); последнее звено — сам узел-цель. Одна цепочка — один отрисованный узел: две кнопки
    одного компонента различаются префиксом вложенного инстанса;
  • слои оверрайдов: записи инстанса на экране (ранг 0 — «копия этого экрана», overridden=true)
    сильнее записей вложенных инстансов внутри определений (ранг = глубина — настройка самой
    библиотеки, overridden=false: «Отмена» у вторичной кнопки Modal Action — дефолт компонента, а не
    текст экрана); конфликт полей решает ранг, поля разных слоёв складываются;
  • видимость узла = его собственное `visible`, поверх — свойство-видимость (`propRefMap.visible.key`
    → присвоение ближайшего инстанса `propAssignMap`, иначе умолчание `propDefMap` определения-
    владельца), поверх — запись `props[]` с `visible`; скрытый узел скрывает поддерево;
  • запись корневого пути (`pathString` == guid варианта) несёт НАСТОЯЩУЮ геометрию инстанса: у шапки
    узел говорит 600×64 (размер мастера), а отрисованно 560×44 — отсюда `sized` и числа размера;
  • подмена компонента (свойство типа instance): `propAssignMap[key].value` — guid подставленного
    компонента; если его определения в экспорте нет, остаётся умолчание мастера, случай считается.
Происхождение: определение с `publishFile` — library (чёрный ящик: рендерим библиотечный компонент и
наполняем из `content`, анатомию детьми не раскрываем); без него — local (композиция дизайнера:
анатомия идёт детьми, вложенные library-инстансы остаются чёрными ящиками); если в экспорте не
опубликовано ничего — undecided. Идентификаторы слотов и анатомии — `<инстанс>;<цепочка>`: две ячейки
одного компонента не делят id, ссылка на узел ведёт на инстанс (часть до первой «;»).

Не реализовано, потому что образец этого не содержит (см. «Результат» задачи): INFERRED_NESTED_INSTANCE_SWAP,
VIRTUAL_HIERARCHY_CYCLE, текстовые свойства компонента, привязанные к текстовому узлу.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..pixso import dsl as D
from . import paint as PT

TEXT_TYPES = ("PARAGRAPH", "TEXT")
ART_TYPES = ("VECTOR", "BOOLEAN_OPERATION", "STAR", "POLYGON")
SKIP_ENTRY_KEYS = ("componentId", "pathString", "type")
MAX_DEPTH = 24
UNMATCHED_LIST_LIMIT = 20
EXAMPLES = 5

Chain = Tuple[str, ...]
Entries = Dict[Chain, Dict[str, Tuple[int, Any]]]


def _num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Event:
    """Узел мастера в развёртке инстанса: цепочка, родитель в цепочке, видим ли он в итоге."""
    __slots__ = ("node", "chain", "parent", "visible")

    def __init__(self, node: dict, chain: Chain, parent: Optional[Chain], visible: bool):
        self.node, self.chain, self.parent, self.visible = node, chain, parent, visible


class Materialized:
    """Итог материализации одного инстанса."""

    def __init__(self) -> None:
        self.resolved = False
        self.origin: Optional[str] = None
        self.content: List[dict] = []
        self.text_props: Dict[str, str] = {}
        self.children: Optional[List[dict]] = None      # виртуальная анатомия local-инстанса
        self.sized: Optional[str] = None
        self.paints = False
        self.has_art = False                            # в видимой анатомии есть векторные фигуры
        self.geometry: Dict[str, float] = {}            # настоящие размеры из записи корня


class Materializer:
    def __init__(self, conv: Any):
        self.conv = conv
        self.nodes: Dict[str, dict] = {n["guid"]: n for n in D.definition_nodes(conv.body)
                                       if isinstance(n.get("guid"), str)}
        self.by_publish: Dict[str, dict] = {n["publishID"]: n for n in self.nodes.values()
                                            if isinstance(n.get("publishID"), str) and n["publishID"]}
        self.published_any = any(isinstance(t.get("publishFile"), str) and t["publishFile"]
                                 for t in conv.tops.values())
        self.counts: Counter = Counter()
        self.unmatched: List[dict] = []
        self.diagnostics: Dict[str, dict] = {}
        self._kids: Dict[Tuple[int, bool], List[dict]] = {}

    # ── служебное ────────────────────────────────────────────────────────────
    def diag(self, code: str, example: Optional[dict] = None) -> None:
        e = self.diagnostics.setdefault(code, {"count": 0, "examples": []})
        e["count"] += 1
        if example and example not in e["examples"] and len(e["examples"]) < EXAMPLES:
            e["examples"].append(example)

    def report(self) -> dict:
        out: Dict[str, Any] = {"counts": dict(sorted(self.counts.items()))}
        if self.diagnostics:
            out["diagnostics"] = {k: self.diagnostics[k] for k in sorted(self.diagnostics)}
        if self.unmatched:
            out["overridesUnmatched"] = self.unmatched
        return out

    @staticmethod
    def _variant_of(node: Optional[dict]) -> Optional[dict]:
        """Группа состояний → её первый вариант; вариант и обычный компонент — как есть."""
        if node is not None and node.get("layerType") == "COMPONENT_SET":
            kids = [c for c in node.get("childNode") or [] if isinstance(c, dict) and c.get("type") == "SYMBOL"]
            return kids[0] if kids else node
        return node

    def resolve(self, inst: dict) -> Tuple[Optional[dict], Optional[dict]]:
        """(верхнеуровневое определение, вариант-мастер) инстанса; (None, None) — определения нет."""
        top = self.conv._definition(inst)
        if top is None:
            return None, None
        mc = inst.get("mainComponent")
        variant = self.nodes.get(mc) if isinstance(mc, str) else None
        if variant is None or self.conv.owner.get(variant["guid"]) != top.get("guid"):
            variant = self._variant_of(top)
        return top, variant

    def _swap_target(self, value: Any) -> Optional[dict]:
        node = self.nodes.get(value) if isinstance(value, str) else None
        if node is None and isinstance(value, str):
            node = self.by_publish.get(value)
        return self._variant_of(node)

    def _master_of(self, nested: dict, scopes: Tuple[dict, ...], record: bool = True) -> Optional[dict]:
        """Мастер вложенного инстанса с учётом подмены компонента (свойство типа instance).
        record=False — узел скрыт: подмена применяется, но счётчики качества экспорта не растут."""
        refs = nested.get("propRefMap")
        ref = refs.get("instance") if isinstance(refs, dict) else None
        if isinstance(ref, dict) and isinstance(ref.get("key"), str) and scopes:
            a = scopes[0].get(ref["key"])                       # присвоение у родителя этого инстанса
            if isinstance(a, dict) and isinstance(a.get("value"), str):
                target = self._swap_target(a["value"])
                if target is not None:
                    if record:
                        self.counts["swapsApplied"] += 1
                    return target
                if record:
                    self.counts["swapsUnresolved"] += 1
                    self.diag("MISSING_COMPONENT_DEFINITION", {"id": nested.get("guid"), "ref": a["value"]})
        mc = nested.get("mainComponent")
        return self._variant_of(self.nodes.get(mc)) if isinstance(mc, str) else None

    def _kids_of(self, node: dict, ordered: bool) -> List[dict]:
        """Дети мастера: по позиции (для текстов) либо в порядке массива (для анатомии); с кэшем."""
        key = (id(node), ordered)
        got = self._kids.get(key)
        if got is None:
            if ordered:
                got = [k for k, _ in self.conv._ordered(node, self.conv._direction(node), drop_hidden=False)]
            else:
                got = [k for k in node.get("childNode") or [] if isinstance(k, dict)]
            self._kids[key] = got
        return got

    # ── слои оверрайдов и видимость ──────────────────────────────────────────
    @staticmethod
    def _add_entries(entries: Entries, props: Any, prefix: Chain, rank: int) -> None:
        for p in props or []:
            if not isinstance(p, dict) or not isinstance(p.get("pathString"), str):
                continue
            chain = prefix + tuple(x for x in p["pathString"].split("/") if x)
            slot = entries.setdefault(chain, {})
            for k, v in p.items():
                if k in SKIP_ENTRY_KEYS:
                    continue
                cur = slot.get(k)
                if cur is None or rank < cur[0]:
                    slot[k] = (rank, v)

    def _default(self, node: dict, key: str) -> Any:
        """Умолчание свойства-видимости — из определения, которому принадлежит узел."""
        top = self.conv.tops.get(self.conv.owner.get(node.get("guid")))
        pd = (top or {}).get("propDefMap")
        d = pd.get(key) if isinstance(pd, dict) else None
        return d.get("defaultValue") if isinstance(d, dict) else None

    def _visible(self, node: dict, scopes: Tuple[dict, ...], ent: Optional[Dict[str, Tuple[int, Any]]]) -> bool:
        vis = node.get("visible") is not False
        refs = node.get("propRefMap")
        ref = refs.get("visible") if isinstance(refs, dict) else None
        if isinstance(ref, dict) and isinstance(ref.get("key"), str):
            key, val = ref["key"], None
            for sc in scopes:                                    # ближайший инстанс сильнее
                a = sc.get(key)
                if isinstance(a, dict) and isinstance(a.get("value"), bool):
                    val = a["value"]
                    break
            if val is None:
                val = self._default(node, key)
            if isinstance(val, bool):
                vis = val
        if ent and isinstance(ent.get("visible", (0, None))[1], bool):
            vis = ent["visible"][1]
        return vis

    # ── развёртка мастера ────────────────────────────────────────────────────
    def walk(self, inst: dict, variant: dict, descend: bool, ordered: bool) -> Tuple[List[Event], Entries]:
        """Узлы мастера инстанса с вычисленной видимостью. descend — раскрывать вложенные инстансы
        (для текстов library-инстанса); ordered — по позиции, иначе в порядке массива."""
        entries: Entries = {}
        self._add_entries(entries, inst.get("props"), (), 0)
        events: List[Event] = []
        base: Tuple[dict, ...] = ((inst.get("propAssignMap") or {}),)
        stack: List[tuple] = [(k, (), base, True, (variant["guid"],), None)
                              for k in reversed(self._kids_of(variant, ordered))]
        while stack:
            node, prefix, scopes, parent_vis, masters, parent = stack.pop()
            chain = prefix + (node["guid"],)
            vis = parent_vis and self._visible(node, scopes, entries.get(chain))
            events.append(Event(node, chain, parent, vis))
            if node.get("type") == "INSTANCE" and descend:
                master = self._master_of(node, scopes, vis)
                if master is None:
                    if vis:
                        self.counts["nestedUnresolved"] += 1
                        self.diag("MISSING_MAIN_COMPONENT", {"id": node.get("guid"), "name": node.get("name")})
                    continue
                if master["guid"] in masters or len(masters) >= MAX_DEPTH:
                    self.counts["expansionCycles"] += 1
                    self.diag("COMPONENT_EXPANSION_CYCLE", {"id": node.get("guid"), "master": master["guid"]})
                    continue
                self._add_entries(entries, node.get("props"), chain, len(chain))
                nested = ((node.get("propAssignMap") or {}),) + scopes
                for k in reversed(self._kids_of(master, ordered)):
                    stack.append((k, chain, nested, vis, masters + (master["guid"],), chain))
            else:
                for k in reversed(self._kids_of(node, ordered)):
                    stack.append((k, prefix, scopes, vis, masters, chain))
        return events, entries

    # ── материализация ───────────────────────────────────────────────────────
    def materialize(self, inst: dict, force_local: bool = False) -> Materialized:
        """force_local — раскрыть анатомию детьми независимо от публикации (корень экрана с автоименем:
        он становится фреймом, и тексты мастера не должны пропасть)."""
        out = Materialized()
        top, variant = self.resolve(inst)
        if top is None or variant is None:
            return out
        out.resolved = True
        self.counts["instancesResolved"] += 1
        published = bool(top.get("publishFile") or variant.get("publishFile"))
        out.origin = ("library" if published else "local") if self.published_any else "undecided"
        self.counts["origin:" + out.origin] += 1
        local = force_local or out.origin == "local"
        masters = tuple(inst.get("_masters") or ())          # мастера, анатомию которых уже раскрывали выше
        if local and (variant["guid"] in masters or len(masters) >= MAX_DEPTH):
            self.counts["expansionCycles"] += 1
            self.diag("COMPONENT_EXPANSION_CYCLE", {"id": inst.get("guid"), "master": variant["guid"]})
            out.children = []                                 # цикл local-композиций: дальше не раскрываем
            return out
        events, entries = self.walk(inst, variant, descend=not local, ordered=not local)

        # настоящий размер инстанса — из записи корневого пути
        root = entries.get((variant["guid"],), {})
        for dim in ("width", "height"):
            v = _num(root.get(dim, (0, None))[1])
            if v is not None:
                out.geometry[dim] = v
        w = out.geometry.get("width", _num(inst.get("width")))
        h = out.geometry.get("height", _num(inst.get("height")))
        mw, mh = _num(variant.get("width")), _num(variant.get("height"))
        if None not in (w, h, mw, mh):
            out.sized = "own" if abs(w - mw) > 0.5 or abs(h - mh) > 0.5 else "master"
            self.counts["sized:" + out.sized] += 1
        # поверхность: заливка/обводка мастера с учётом оверрайда корня
        surface = dict(variant)
        for k in ("fillPaints", "strokePaints", "strokeWeight"):
            if k in root:
                surface[k] = root[k][1]
        out.paints = PT.has_fill(surface) or PT.has_stroke(surface)
        out.has_art = any(ev.visible and ev.node.get("type") in ART_TYPES for ev in events)

        text_chains = set()
        inst_chains = set()
        for ev in events:
            if ev.node.get("type") == "INSTANCE":
                inst_chains.add(ev.chain)
            if ev.node.get("type") not in TEXT_TYPES:
                continue
            text_chains.add(ev.chain)
            if local:
                continue                                    # анатомия local несёт текст в узлах, не в content
            ent = entries.get(ev.chain, {})
            text, overridden = ev.node.get("nodeText"), False
            if "nodeText" in ent:
                text, overridden = ent["nodeText"][1], ent["nodeText"][0] == 0
            if not ev.visible:
                self.counts["slotsHidden"] += 1
                continue
            if isinstance(text, str) and text:
                out.content.append({"id": f"{inst['guid']};{'/'.join(ev.chain)}", "name": ev.node.get("name") or "",
                                    "text": text, "overridden": bool(overridden)})
                self.counts["slots"] += 1
                self.counts["slotsOverridden"] += bool(overridden)
        for chain, ent in entries.items():
            if "nodeText" not in ent or ent["nodeText"][0] != 0 or chain in text_chains:
                continue
            if local and any(chain[:i] in inst_chains for i in range(1, len(chain))):
                continue                                    # ляжет на текст вложенного инстанса при его разборе
            self.counts["overridesUnmatched"] += 1
            if len(self.unmatched) < UNMATCHED_LIST_LIMIT:
                self.unmatched.append({"instance": inst.get("name"), "id": inst.get("guid"),
                                       "path": "/".join(chain), "text": ent["nodeText"][1]})
        for pm in (inst.get("propAssignMap") or {}).values():
            if isinstance(pm, dict) and str(pm.get("type")).lower() == "text" and isinstance(pm.get("name"), str) \
                    and isinstance(pm.get("value"), str):
                out.text_props[pm["name"]] = pm["value"]
        if local:
            out.children = self._virtual_children(inst, variant, events, entries)
            self.counts["virtualNodes"] += sum(1 for _ in _count(out.children))
        return out

    # ── анатомия local-композиции ────────────────────────────────────────────
    @staticmethod
    def _virtual_children(inst: dict, variant: dict, events: List[Event], entries: Entries) -> List[dict]:
        """Копия анатомии мастера как узлов-источников: видимые, с применёнными оверрайдами, id
        `<инстанс>;<цепочка>`. Вложенные инстансы остаются инстансами и разбираются своим чередом —
        с пере-базированными записями экрана; дети — в порядке массива (тай-брейк рисования).
        Вложенный инстанс помнит цепочку раскрытых мастеров (`_masters`) — по ней ловится цикл."""
        masters = tuple(inst.get("_masters") or ()) + (variant["guid"],)
        made: Dict[Chain, dict] = {}
        top: List[dict] = []
        for ev in events:
            if not ev.visible:
                continue
            v = {k: val for k, val in ev.node.items() if k != "childNode"}
            for key, (_, val) in entries.get(ev.chain, {}).items():
                if key == "visible":
                    continue
                if key == "autoLayout" and isinstance(val, dict) and isinstance(v.get("autoLayout"), dict):
                    v["autoLayout"] = dict(v["autoLayout"], **val)
                else:
                    v[key] = val
            v["guid"] = f"{inst['guid']};{'/'.join(ev.chain)}"
            if ev.node.get("type") == "INSTANCE":
                rebased = []
                n = len(ev.chain)
                for chain, ent in entries.items():
                    if len(chain) > n and chain[:n] == ev.chain:
                        entry: Dict[str, Any] = {"pathString": "/".join(chain[n:])}
                        entry.update({k: val for k, (rank, val) in ent.items() if rank == 0})
                        rebased.append(entry)
                v["props"] = rebased + list(ev.node.get("props") or [])   # записи экрана сильнее настроек мастера
                v["_masters"] = masters
            parent = made.get(ev.parent) if ev.parent is not None else None
            (top if parent is None else parent.setdefault("childNode", [])).append(v)
            made[ev.chain] = v
        return top


def _count(nodes: List[dict]) -> Iterator[dict]:
    stack = list(nodes)
    while stack:
        n = stack.pop()
        yield n
        stack.extend(k for k in n.get("childNode") or [] if isinstance(k, dict))
