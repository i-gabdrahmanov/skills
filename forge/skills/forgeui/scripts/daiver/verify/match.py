"""match.py — матчинг «узел макета ↔ элемент страницы» (спека §14.4.2, INV-C26; константы §20.4).

Прежде чем сравнивать свойства, надо знать, какой элемент страницы — это какой узел макета: неверная пара делает все
дальнейшие дефекты вымыслом. Модуль чистый (два дерева канона + необязательный plan.json → пары и находки), без диска
и браузера; одинаков для моделей `dom` и `code` — ему важна только форма канона.

Якоря по убыванию силы (веса — константы SC_*; решающий — только сумма «содержательных» якорей, SUBSTANTIVE):
  текст (нормализованный, без регистра; уникальный на обеих сторонах — +20) · компонент (figmaName ≡ имя компонента
  реализации без регистра и разделителей) · мост плана (имя компонента из resolutions) · перекрытие текстов поддеревьев
  (контейнер/инстанс: доля текстов макета, найденных в поддереве страницы; `textProps`/`content` инстанса — его тексты;
  один неверный символ в составной ячейке прощается) · краска (пустой контрол: тот же фон и размер) · позиция (тот же
  сматченный родитель, тот же индекс — только в добивающем проходе и только при равном числе соседей) · геометрия (+≤5,
  только тай-брейк). Роль блокирует пару: text↔text, media (icon/image/vector)↔media, block (frame/rect/instance)↔block;
  два инстанса спариться могут всегда — имя компонента вопрос score, не вето. Лист-форма страницы с собственным `text`
  (поле ввода) — роль text: «текст со слоем-коробкой не спаривается» относится к пустым коробкам.

Ограничения корректности: сверху вниз (дети — только внутри сматченного родителя: пять одинаковых карточек не
перелинкуются); один-к-одному по убыванию score (ничья — порядок макета, затем документа); консистентность вложенности
(пары одного уровня не вложены друг в друга на странице); узел макета может спариться на 1+ уровней глубже — обёртки
страницы (`body > div#root`) не мешают; несматченный фрейм макета «прозрачен»: его дети ищутся в той же области.
Корневая пара — спуск мимо покрывающей обёртки без краски с единственным ребёнком до рамки/краски/ветвления.

План — мост имён: `read_plan` читает решения из всех носителей (карта по id, запись `{id, component|resolution}`, пары
`[id, имя]`); имя берётся из СВОЕГО поля (`component`/`resolution`), kind-слово («local», «div», «text») отличается от
имени ФОРМОЙ (заглавная первая буква vs строчный тег), а не списком; `ignore` — узел и поддерево выпадают из макета
(кода нет), `local` остаётся; без плана работает порядок соседей.

Выход: пары (`anchor`, `confidence` high|low — у low числовые правила 4.4 пропускаются со счётчиком), MISSING (стабильный
id = id макета; ветка одной строкой; содержимое инстанса, нарисованного одним узлом, не называется; пустая рамка — нет),
EXTRA (только содержимое; анонимная обёртка — нет; тексты слотов сматченного инстанса — его содержимое, не лишние; хром приложения вне региона — не EXTRA, а `stats.outsideRegion`),
ORDER (виноват сдвинувшийся: наибольшая неподвижная подпоследовательность, ничья — больше «на своих местах», затем раньше
в макете). `matchRate` — только по видимому содержимому (text/icon/image/vector/instance/art); < MIN_MATCH_RATE — hard
gate fail; < LOW_MATCH_RATE — MISSING с общим сматченным предком сливаются в один (вместо россыпи).

Добивающий проход спаривает тексты по позиции только при сходстве ≥ MIN_TEXT_SIMILARITY (опечатка — пара, другой текст — нет:
иначе страница с теми же счётами, но чужими текстами набрала бы matchRate 1 и обошла hard gate).

Не проверено данными (боевых пар «макет ↔ страница» пока нет, сверка — на синтетике по каждому правилу): веса якорей,
порог перекрытия 0.5, допуск региона REGION_TOL, «добивающий» проход по позиции при равном числе соседей, слияние MISSING
при LOW_MATCH_RATE (спека говорит «один MISSING на несвязанное поддерево» — понято как «по общему сматченному предку»).
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

MIN_MATCH_RATE = 0.5                    # §20.4
LOW_MATCH_RATE = 0.9                    # §20.4
MIN_OVERLAP = 0.5                       # доля текстов макета в поддереве страницы, с которой контейнер вообще кандидат
MIN_TEXT_SIMILARITY = 0.6               # добивающий проход по позиции спаривает тексты только если они похожи (опечатка, не другой текст)
MIN_FUZZY_LEN = 4                       # «один неверный символ» прощается только строкам от этой длины
REGION_TOL = 4                          # px: хром должен зайти в регион глубже допуска, чтобы репортиться
SC_TEXT, SC_UNIQUE, SC_PLAN, SC_COMP = 100, 20, 70, 60
SC_OVERLAP, SC_PAINT, SC_POS, SC_GEO = 50, 30, 10, 5
SUBSTANTIVE = 25                        # сумма содержательных якорей, с которой пара принимается
LIS_EXACT_MAX = 600                     # до стольки соседей ORDER считается точно (O(n²)), дальше — упрощённо
SHOWN_TEXTS = 3

_WS = re.compile(r"\s+")
_NAME_STRIP = re.compile(r"[^0-9a-zа-яё]+")
_KIND_WORDS = ("local", "ignore")


class MatchError(ValueError):
    """Вход не похож на дерево канона."""


def norm_text(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFKC", s).replace("\xa0", " ")).strip().casefold()


def norm_name(s: Any) -> str:
    return _NAME_STRIP.sub("", s.casefold()) if isinstance(s, str) else ""


def _hamming1(a: str, b: str) -> bool:
    if len(a) != len(b) or a == b:
        return False
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def _painted(style: Any) -> bool:
    if not isinstance(style, dict):
        return False
    if style.get("gradient") or style.get("shadow"):
        return True
    bg = style.get("background")
    if isinstance(bg, str) and bg and not re.match(r"^rgba\(\s*\d+,\s*\d+,\s*\d+,\s*0(\.0+)?\s*\)$", bg):
        return True
    b = style.get("border")
    if isinstance(b, dict):
        w = b.get("width")
        if isinstance(w, dict):
            return any(isinstance(v, (int, float)) and v > 0 for v in w.values())
        return isinstance(w, (int, float)) and w > 0
    return False


# ---------------------------------------------------------------------------------------------------------------- план

def _is_name(v: Any) -> bool:
    """Имя компонента, а не kind-слово: по ФОРМЕ — заглавная первая буква («Button»), тег и слова («div», «local») строчные."""
    return isinstance(v, str) and bool(v) and v[0].isupper()


def read_plan(plan: Any, known_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """plan.json → {id узла макета: {"resolution": "ignore"|"local"|None, "name": имя компонента|None}}.

    Три носителя: карта, ключ — id узла; запись с `id` рядом с полями решения; пары соседей `[id, имя]`. Читаются только
    id, которые есть в макете; чужое и неразобранное молча не считается решением (целостность плана — дело check_frame)."""
    known = set(known_ids)
    out: Dict[str, Dict[str, Any]] = {}

    def take(node_id: str, e: Dict[str, Any]) -> None:
        if node_id not in known:
            return
        rec = out.setdefault(node_id, {"resolution": None, "name": None})
        for field in ("component", "resolution"):
            v = e.get(field)
            if isinstance(v, dict):
                v = v.get("name")
            if _is_name(v):
                rec["name"] = rec["name"] or v
            elif field == "resolution" and v in _KIND_WORDS:
                rec["resolution"] = v

    stack: List[Any] = [plan]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if isinstance(cur.get("id"), str) and ("component" in cur or "resolution" in cur):
                take(cur["id"], cur)
            for k, v in cur.items():
                if isinstance(k, str) and k in known and isinstance(v, dict) and ("component" in v or "resolution" in v):
                    take(k, v)
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            if len(cur) == 2 and isinstance(cur[0], str) and cur[0] in known and isinstance(cur[1], str):
                take(cur[0], {"component": cur[1]})
                continue
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    return {k: out[k] for k in sorted(out)}


# --------------------------------------------------------------------------------------------------------------- деревья

class _Tree:
    """Дерево канона в плоском виде: preorder-индексы, родитель, дети, конец поддерева, поддерево-тексты. Итеративно."""

    def __init__(self, root: Dict[str, Any], side: str, skip: Optional[Set[str]] = None, code: bool = False):
        self.side = side
        self.code = code
        self.nodes: List[Dict[str, Any]] = []
        self.parent: List[int] = []
        self.kids: List[List[int]] = []
        self.depth: List[int] = []
        self.skipped = 0
        stack: List[Tuple[Dict[str, Any], int, int]] = [(root, -1, 0)]
        while stack:
            node, par, dep = stack.pop()
            if side == "design" and (node.get("occluded") is True or (skip and node.get("id") in skip)):
                self.skipped += 1
                continue
            idx = len(self.nodes)
            self.nodes.append(node)
            self.parent.append(par)
            self.kids.append([])
            self.depth.append(dep)
            if par >= 0:
                self.kids[par].append(idx)
            kids = node.get("children")
            if isinstance(kids, list):
                for k in reversed([k for k in kids if isinstance(k, dict)]):
                    stack.append((k, idx, dep + 1))
        n = len(self.nodes)
        size = [1] * n
        for i in range(n - 1, 0, -1):
            size[self.parent[i]] += size[i]
        self.end = [i + size[i] for i in range(n)]
        self.role = [self._role(i) for i in range(n)]
        self.own: List[Set[str]] = [self._own_texts(i) for i in range(n)]
        self.names: List[Tuple[str, ...]] = [self._names(i) for i in range(n)]
        self.paint = [_painted(self.nodes[i].get("style")) for i in range(n)]
        self.content = [self._content(i) for i in range(n)]
        self.sig: List[Set[str]] = [set(s) for s in self.own]
        for i in range(n - 1, 0, -1):
            self.sig[self.parent[i]] |= self.sig[i]

    def inside(self, i: int, anc: int) -> bool:
        """i — потомок anc (строго)."""
        return anc < i < self.end[anc]

    def _role(self, i: int) -> str:
        n = self.nodes[i]
        k = n.get("kind")
        if self.side == "design":
            if k == "text":
                return "text"
            if k in ("icon", "image", "vector") or n.get("art"):
                return "media"
            return "block"
        if k == "text":
            return "text"
        if k in ("image", "vector"):
            return "media"
        if isinstance(n.get("text"), str) and n["text"] and not self.kids[i]:
            return "text"                              # лист-форма (поле ввода): текст и есть содержимое
        return "block"

    def _own_texts(self, i: int) -> Set[str]:
        n = self.nodes[i]
        out: Set[str] = set()
        t = norm_text(n.get("text"))
        if t:
            out.add(t)
        comp = n.get("component")
        if isinstance(comp, dict) and (self.side == "design" or comp.get("textProps")):    # у страницы textProps бывают только в модели кода
            for slot in comp.get("content") or []:
                if isinstance(slot, dict) and norm_text(slot.get("text")):
                    out.add(norm_text(slot["text"]))
            for v in (comp.get("textProps") or {}).values():
                if norm_text(v):
                    out.add(norm_text(v))
        return out

    def _names(self, i: int) -> Tuple[str, ...]:
        comp = self.nodes[i].get("component")
        if not isinstance(comp, dict):
            return ()
        raw = list(comp.get("implNames") or []) if self.side == "impl" else []
        for k in ("implName", "figmaName"):
            if isinstance(comp.get(k), str):
                raw.append(comp[k])
        out: List[str] = []
        for r in raw:
            nm = norm_name(r)
            if nm and nm not in out:
                out.append(nm)
        return tuple(out)

    def _content(self, i: int) -> bool:
        if self.side == "design":
            n = self.nodes[i]
            if self.role[i] == "text":
                return bool(self.own[i])                  # пустой текстовый слой не толкуется: на странице его не увидеть
            return self.role[i] == "media" or n.get("kind") == "instance"
        n = self.nodes[i]
        return self.role[i] in ("text", "media") or n.get("kind") == "instance"      # instance у страницы бывает только в модели кода

    def box(self, i: int) -> Optional[Tuple[float, float, float, float]]:
        b = self.nodes[i].get("box")
        if isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) for k in ("x", "y", "w", "h")):
            return (b["x"], b["y"], b["w"], b["h"])
        return None

    def flat_children(self, i: int) -> List[int]:
        """Дети i, где анонимные обёртки (frame без текста, краски и компонента) развёрнуты в своё содержимое."""
        out: List[int] = []
        stack = list(reversed(self.kids[i]))
        while stack:
            c = stack.pop()
            out.append(c)
            named = bool(self.names[c]) and not (self.code and self.nodes[c].get("kind") != "instance")   # в модели кода инлайн-компонент — обёртка
            if self.role[c] == "block" and self.kids[c] and not self.paint[c] and not named and not self.own[c]:
                stack.extend(reversed(self.kids[c]))
        return out

    def label(self, i: int) -> str:
        n = self.nodes[i]
        fig = (n.get("component") or {}).get("figmaName")
        if n.get("kind") == "instance" and fig:
            return str(fig)                              # имя слоя инстанса в образце — строка вариантов («Variant=Default»)
        return str(n.get("name") or fig or n.get("tag") or n.get("kind") or "?")


# ------------------------------------------------------------------------------------------------------------------ match

def _root_of(x: Any) -> Optional[Dict[str, Any]]:
    if isinstance(x, dict) and "root" in x and "kind" not in x:
        x = x["root"]
    if x is None:
        return None
    if not isinstance(x, dict) or "kind" not in x:
        raise MatchError("ожидается узел канона (или конверт с root)")
    return x


def _descend_root(impl: _Tree) -> int:
    """Корень страницы для корня макета: спуск мимо обёртки без краски с единственным ребёнком, полностью её покрывающим."""
    cur = 0
    while True:
        kids = impl.kids[cur]
        if len(kids) != 1 or impl.paint[cur] or impl.own[cur] or impl.role[cur] != "block" or impl.role[kids[0]] != "block":
            return cur                                    # корень макета — рамка: в одинокий текст/значок страницы не спускаемся
        b, c = impl.box(cur), impl.box(kids[0])
        if b and c and not (c[0] <= b[0] + 1 and c[1] <= b[1] + 1 and c[0] + c[2] >= b[0] + b[2] - 1 and c[1] + c[3] >= b[1] + b[3] - 1):
            return cur
        cur = kids[0]


class _Run:
    def __init__(self, design: Dict[str, Any], impl: Dict[str, Any], plan: Any, descend_root: bool = True):
        self.descend_root = descend_root
        ids = [n for n in self._ids(design)]
        self.bridge = read_plan(plan, ids) if plan is not None else {}
        ignored = {k for k, v in self.bridge.items() if v["resolution"] == "ignore"}
        self.D = _Tree(design, "design", ignored)
        self.I = _Tree(impl, "impl", code=not descend_root)
        self.ignored = len(ignored)
        self.dpair: Dict[int, int] = {}          # индекс макета → индекс страницы
        self.ipair: Dict[int, int] = {}
        self.info: Dict[int, Tuple[str, str, int]] = {}        # d → (anchor, confidence, score)
        self.vocab: Dict[int, Dict[str, List[int]]] = {}       # длина → {текст: [индексы текст-узлов страницы]}
        self.text_at: Dict[str, List[int]] = {}
        self.comp_at: Dict[str, List[int]] = {}
        self.empty_painted: List[int] = []
        for j in range(len(self.I.nodes)):
            for t in self.I.own[j]:
                if self.I.role[j] == "text":
                    self.text_at.setdefault(t, []).append(j)
                    self.vocab.setdefault(len(t), {}).setdefault(t, []).append(j)
            for nm in self.I.names[j]:
                self.comp_at.setdefault(nm, []).append(j)
            if self.I.role[j] == "block" and not self.I.kids[j] and self.I.paint[j]:
                self.empty_painted.append(j)
        self.impl_texts = set(self.text_at)
        self.dmapped: Dict[str, str] = {}

    @staticmethod
    def _ids(root: Dict[str, Any]) -> List[str]:
        out, stack = [], [root]
        while stack:
            n = stack.pop()
            if isinstance(n.get("id"), str):
                out.append(n["id"])
            stack.extend(k for k in (n.get("children") or []) if isinstance(k, dict))
        return out

    # --- текстовые подписи -------------------------------------------------------------------------------------------
    def _mapped(self, t: str) -> str:
        """Текст макета → тот же текст в словаре страницы (с прощением одного неверного символа), иначе он сам."""
        got = self.dmapped.get(t)
        if got is None:
            got = t
            if t not in self.impl_texts and len(t) >= MIN_FUZZY_LEN:
                twins = sorted(c for c in self.vocab.get(len(t), {}) if _hamming1(t, c))
                if len(twins) == 1:
                    got = twins[0]
            self.dmapped[t] = got
        return got

    # --- пары --------------------------------------------------------------------------------------------------------
    def accept(self, d: int, i: int, anchor: str, conf: str, score: int) -> None:
        self.dpair[d], self.ipair[i] = i, d
        self.info[d] = (anchor, conf, score)

    def _consistent(self, i: int, family: List[int]) -> bool:
        I = self.I
        for ia in family:
            if i == ia or I.inside(i, ia) or I.inside(ia, i):
                return False
        return True

    def _geo(self, d: int, i: int, dp: int, ip: int) -> float:
        db, ib, dpb, ipb = self.D.box(d), self.I.box(i), self.D.box(dp), self.I.box(ip)
        if not (db and ib and dpb and ipb) or not (dpb[2] and dpb[3] and ipb[2] and ipb[3]):
            return 0.0
        dx, dy = (db[0] - dpb[0]) / dpb[2], (db[1] - dpb[1]) / dpb[3]
        ix, iy = (ib[0] - ipb[0]) / ipb[2], (ib[1] - ipb[1]) / ipb[3]
        pos = max(0.0, 1 - (abs(dx - ix) + abs(dy - iy)))
        sw = min(db[2], ib[2]) / max(db[2], ib[2]) if max(db[2], ib[2]) else 1.0
        sh = min(db[3], ib[3]) / max(db[3], ib[3]) if max(db[3], ib[3]) else 1.0
        return SC_GEO * (0.5 * pos + 0.25 * sw + 0.25 * sh)

    def _scope_ok(self, j: int, ip: int) -> bool:
        return self.I.inside(j, ip) and j not in self.ipair

    # Роль блокирует пару уже при выдаче кандидатов (текст — только текстовые узлы страницы, блок — только блоки), а в
    # добивающем проходе пары строятся внутри одной роли: отдельной проверки совместимости нет и быть не должно.
    def _candidates(self, d: int, ip: int, dp: int) -> List[Tuple[float, int, str, str]]:
        """Кандидаты страницы для узла макета d внутри поддерева ip: [(score, j, anchor, confidence)]."""
        D, I = self.D, self.I
        role = D.role[d]
        out: List[Tuple[float, int, str, str]] = []
        if role == "text":
            for t in D.own[d]:
                js = [j for j in self.text_at.get(t, []) if self._scope_ok(j, ip)]
                dsame = [x for x in self._siblings_texts(d, dp) if t in D.own[x]]
                for j in js:
                    uniq = len(js) == 1 and len(dsame) == 1
                    out.append((SC_TEXT + (SC_UNIQUE if uniq else 0) + self._geo(d, j, dp, ip) - 0.01 * (I.depth[j] - I.depth[ip]),
                                j, "text", "high"))
            return out
        if role != "block":
            return out
        seen: Dict[int, float] = {}
        plan_name = norm_name((self.bridge.get(D.nodes[d].get("id")) or {}).get("name"))
        names = [(plan_name, SC_PLAN, "plan")] if plan_name else []
        names += [(nm, SC_COMP, "component") for nm in D.names[d]]
        best_name: Dict[int, Tuple[int, str]] = {}
        for nm, w, anchor in names:
            for j in self.comp_at.get(nm, []):
                if self._scope_ok(j, ip) and I.role[j] == "block" and (j not in best_name or best_name[j][0] < w):
                    best_name[j] = (w, anchor)
        sig = {self._mapped(t) for t in D.sig[d]}
        hits: Dict[int, int] = {}
        if sig:
            for t in sorted(sig):
                for jt in self.text_at.get(t, []):
                    if not I.inside(jt, ip):
                        continue
                    a = I.parent[jt]
                    while a > ip:
                        hits[a] = hits.get(a, 0) + 1            # учтёт текст однажды: ниже дублей одного текста нет в sig
                        a = I.parent[a]
        cand = set(best_name) | set(j for j, h in hits.items() if h / len(sig) >= MIN_OVERLAP)
        # пустой контрол: узнаётся по краске
        paint_hits: Dict[int, float] = {}
        if not D.kids[d] and D.paint[d]:
            for j in self.empty_painted:
                if self._scope_ok(j, ip) and self._same_paint(d, j):
                    paint_hits[j] = SC_PAINT
            cand |= set(paint_hits)
        for j in sorted(cand):
            if I.role[j] != "block" or j in self.ipair:
                continue
            score = 0.0
            anchor, conf = "position", "low"
            if j in best_name:
                score += best_name[j][0]
                anchor, conf = best_name[j][1], "high"
            if sig and j in hits:
                f = hits[j] / len(sig)
                p = hits[j] / max(1, len(I.sig[j]))
                if f >= MIN_OVERLAP:
                    score += SC_OVERLAP * (0.5 * f + 0.5 * p)
                    if anchor == "position":
                        anchor, conf = "textOverlap", ("high" if f >= 0.8 else "low")
            if j in paint_hits:
                score += paint_hits[j]
                if anchor == "position":
                    anchor = "paint"
            if score < SUBSTANTIVE:
                continue
            score += self._geo(d, j, dp, ip) - 0.01 * (I.depth[j] - I.depth[ip])
            if D.nodes[d].get("kind") == "instance" and self._sole_inner_same(j, best_name.get(j)):
                score -= 3
            out.append((score, j, anchor, conf))
        return out

    def _sole_inner_same(self, j: int, bn: Optional[Tuple[int, str]]) -> bool:
        """Обёртка компонента, единственный ребёнок которой несёт то же имя: не соперник — внутренний выигрывает ничью."""
        I = self.I
        if bn is None or len(I.kids[j]) != 1:
            return False
        c = I.kids[j][0]
        return bool(set(I.names[j]) & set(I.names[c]))

    def _same_paint(self, d: int, j: int) -> bool:
        ds, js = self.D.nodes[d].get("style") or {}, self.I.nodes[j].get("style") or {}
        if ds.get("background") and ds.get("background") == js.get("background"):
            ok = True
        elif (ds.get("border") or {}).get("color") and (ds.get("border") or {}).get("color") == (js.get("border") or {}).get("color"):
            ok = True
        else:
            return False
        db, ib = self.D.box(d), self.I.box(j)
        if db and ib:
            return all(abs(a - b) <= 0.25 * max(a, b, 1) for a, b in ((db[2], ib[2]), (db[3], ib[3])))
        return ok

    def _siblings_texts(self, d: int, dp: int) -> List[int]:
        return [x for x in range(dp + 1, self.D.end[dp]) if self.D.role[x] == "text"]

    # --- обход -------------------------------------------------------------------------------------------------------
    def run(self) -> None:
        D, I = self.D, self.I
        r = _descend_root(I) if self.descend_root else 0
        self.accept(0, r, "root", "high", 0)
        queue = [(0, r)]
        while queue:
            dp, ip = queue.pop(0)
            for d, i in sorted(self._level(dp, ip)):                 # порядок макета, а не порядок принятия: итог не зависит от score соседей
                if D.kids[d] or D.role[d] == "block":
                    queue.append((d, i))

    def _level(self, dp: int, ip: int) -> List[Tuple[int, int]]:
        """Пары детей (с «прозрачными» несматченными фреймами) внутри пары (dp, ip), в порядке принятия."""
        D, I = self.D, self.I
        made: List[Tuple[int, int]] = []
        family: List[int] = []
        todo = list(D.kids[dp])
        seen_unmatched: List[int] = []
        while todo:
            scored: List[Tuple[float, int, int, str, str]] = []
            for d in todo:
                for score, j, anchor, conf in self._candidates(d, ip, dp):
                    scored.append((score, d, j, anchor, conf))
            scored.sort(key=lambda s: (-s[0], s[1], s[2]))
            for score, d, j, anchor, conf in scored:
                if d in self.dpair or j in self.ipair:
                    continue
                if not self._consistent(j, family):
                    continue
                self.accept(d, j, anchor, conf, int(round(score)))
                family.append(j)
                made.append((d, j))
            lifted: List[int] = []
            for d in todo:
                if d not in self.dpair and D.nodes[d].get("kind") in ("frame", "rect") and D.kids[d] and d not in seen_unmatched:
                    seen_unmatched.append(d)
                    lifted.extend(D.kids[d])
            if not lifted:
                break
            todo = lifted
        for d, j in self._leftover(dp, ip, family):
            made.append((d, j))
        return made

    def _transparent_texts(self, dp: int) -> List[int]:
        """Тексты макета, до которых можно дойти от dp через несматченные фреймы (они «прозрачны»): их дети ищутся в той же области."""
        D = self.D
        out: List[int] = []
        stack = list(reversed(D.kids[dp]))
        while stack:
            d = stack.pop()
            if d in self.dpair:
                continue
            if D.role[d] == "text":
                out.append(d)
            elif D.nodes[d].get("kind") in ("frame", "rect"):
                stack.extend(reversed(D.kids[d]))
        return out

    def _leftover(self, dp: int, ip: int, family: List[int]) -> List[Tuple[int, int]]:
        """Добивающий проход. Тексты: несматченный текст макета (в том числе под прозрачным фреймом) и свободный текст страницы,
        похожие не меньше MIN_TEXT_SIMILARITY, — опечатка, а не другой текст (жадно по убыванию сходства). Значки и блоки: по позиции,
        если свободных детей макета и плоских детей страницы одной роли поровну."""
        D, I = self.D, self.I
        made: List[Tuple[int, int]] = []
        free_i = [j for j in I.flat_children(ip) if j not in self.ipair and self._consistent(j, family)]
        texts_d = [d for d in self._transparent_texts(dp) if D.own[d]]
        texts_i = [j for j in free_i if I.role[j] == "text" and I.own[j]]
        ranked = []
        for d in texts_d:
            for j in texts_i:
                r = self._text_ratio(d, j)
                if r >= MIN_TEXT_SIMILARITY:
                    ranked.append((-r, d, j))
        for _r, d, j in sorted(ranked):
            if d in self.dpair or j in self.ipair or not self._consistent(j, family):
                continue
            self.accept(d, j, "textSimilar", "low", SC_POS)
            family.append(j)
            made.append((d, j))
        free_d = [d for d in D.kids[dp] if d not in self.dpair and self._pairable(d)]
        for role in ("media", "block"):
            ds = [d for d in free_d if D.role[d] == role]
            js = [j for j in free_i if I.role[j] == role and j not in self.ipair]
            if ds and len(ds) == len(js):
                for d, j in zip(ds, js):
                    if j in self.ipair or not self._consistent(j, family):
                        continue
                    self.accept(d, j, "position", "low", SC_POS)
                    family.append(j)
                    made.append((d, j))
        return made

    def _text_ratio(self, d: int, j: int) -> float:
        a, b = sorted(self.D.own[d]), sorted(self.I.own[j])
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a[0], b[0]).ratio()

    def _pairable(self, d: int) -> bool:
        """Узел макета, у которого вообще есть что искать на странице (пустой непокрашенный фрейм — нет)."""
        D = self.D
        if D.role[d] == "text":
            return bool(D.own[d])
        if D.role[d] != "block":
            return True
        return bool(D.kids[d]) or D.paint[d] or D.nodes[d].get("kind") == "instance"


# ---------------------------------------------------------------------------------------------------------------- итоги

def _prefix(flags: List[bool]) -> List[int]:
    out = [0]
    for f in flags:
        out.append(out[-1] + (1 if f else 0))
    return out


def _lis_keep(keys: List[int]) -> Set[int]:
    """Индексы (в порядке макета), которые остаются на месте: наибольшая возрастающая подпоследовательность; ничья —
    больше «на своих местах» (ключ равен ранку), затем лексикографически раньше в макете."""
    n = len(keys)
    rank = {k: r for r, k in enumerate(sorted(keys))}
    if n > LIS_EXACT_MAX:
        tails: List[int] = []
        tail_idx: List[int] = []
        prev = [-1] * n
        for i, k in enumerate(keys):
            lo, hi = 0, len(tails)
            while lo < hi:
                mid = (lo + hi) // 2
                if tails[mid] < k:
                    lo = mid + 1
                else:
                    hi = mid
            if lo == len(tails):
                tails.append(k)
                tail_idx.append(i)
            else:
                tails[lo], tail_idx[lo] = k, i
            prev[i] = tail_idx[lo - 1] if lo else -1
        keep: Set[int] = set()
        cur = tail_idx[-1] if tail_idx else -1
        while cur >= 0:
            keep.add(cur)
            cur = prev[cur]
        return keep
    best: List[Tuple[int, int]] = []                  # (длина, неподвижных на своих местах) для цепочки, заканчивающейся в i
    prev = [-1] * n
    for i in range(n):
        cur = (1, 1 if rank[keys[i]] == i else 0)
        for j in range(i):
            if keys[j] < keys[i]:
                cand = (best[j][0] + 1, best[j][1] + (1 if rank[keys[i]] == i else 0))
                if cand > cur:
                    cur, prev[i] = cand, j
        best.append(cur)
    end = max(range(n), key=lambda i: (best[i], -i)) if n else -1
    keep = set()
    while end >= 0:
        keep.add(end)
        end = prev[end]
    return keep


def _branch(D: _Tree, d: int) -> str:
    n = D.nodes[d]
    kind = n.get("kind")
    if kind == "instance":
        return f"component «{D.label(d)}»"
    if kind == "text":
        t = str(n.get("text") or "")
        return f"text «{t[:40]}»"
    texts: List[str] = []
    media = comps = 0
    stack = [d]
    while stack:
        x = stack.pop()
        if D.nodes[x].get("kind") == "instance":
            comps += 1
            continue                                      # содержимое компонента, нарисованного одним узлом, не называется
        if D.role[x] == "text" and D.nodes[x].get("text"):
            texts.append(str(D.nodes[x]["text"]))
        elif D.role[x] == "media":
            media += 1
        stack.extend(reversed(D.kids[x]))
    parts = []
    if texts:
        shown = ", ".join(f"«{t[:30]}»" for t in texts[:SHOWN_TEXTS]) + (", …" if len(texts) > SHOWN_TEXTS else "")
        parts.append(f"{len(texts)} текст.: {shown}")
    if media:
        parts.append(f"{media} знач./изобр.")
    if comps:
        parts.append(f"{comps} компон.")
    return f"{kind} «{D.label(d)}»" + (" — " + "; ".join(parts) if parts else "")


def _missing(run: _Run, matched_pref: List[int], content_pref: List[int]) -> List[Dict[str, Any]]:
    D = run.D
    out: List[Dict[str, Any]] = []
    stack = [(k, 0) for k in reversed(D.kids[0])]
    while stack:
        d, anc = stack.pop()
        if d in run.dpair:
            stack.extend((k, d) for k in reversed(D.kids[d]))
            continue
        if matched_pref[D.end[d]] - matched_pref[d + 1] > 0:
            stack.extend((k, anc) for k in reversed(D.kids[d]))
            continue
        cnt = content_pref[D.end[d]] - content_pref[d]
        if cnt:
            out.append({"id": D.nodes[d]["id"], "kind": D.nodes[d].get("kind"), "branch": _branch(D, d), "contentCount": cnt,
                        "ancestor": D.nodes[anc]["id"]})
    return out


def _collapse(missing: List[Dict[str, Any]], run: _Run) -> List[Dict[str, Any]]:
    """Ниже LOW_MATCH_RATE: несвязанные ветки под одним сматченным предком — одна запись вместо россыпи."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for m in missing:
        groups.setdefault(m["ancestor"], []).append(m)
    out: List[Dict[str, Any]] = []
    for m in missing:
        g = groups.get(m["ancestor"])
        if g is None:
            continue
        del groups[m["ancestor"]]
        if len(g) == 1:
            out.append(m)
            continue
        shown = "; ".join(x["branch"] for x in g[:SHOWN_TEXTS]) + ("; …" if len(g) > SHOWN_TEXTS else "")
        out.append({"id": g[0]["id"], "ids": [x["id"] for x in g], "kind": "group", "collapsed": True,
                    "branch": f"{len(g)} несвязанных ветки под «{m['ancestor']}»: {shown}",
                    "contentCount": sum(x["contentCount"] for x in g), "ancestor": m["ancestor"]})
    return out


def _region(run: _Run) -> Optional[Tuple[float, float, float, float]]:
    """Регион страницы: охват боксов слов и виджетов сматченных пар (окрашенная карточка — фон, в охват не входит)."""
    D, I = run.D, run.I
    xs0, ys0, xs1, ys1 = [], [], [], []
    for d, j in run.dpair.items():
        if d == 0:
            continue
        if D.role[d] in ("text", "media") or D.nodes[d].get("kind") == "instance":
            b = I.box(j)
            if b:
                xs0.append(b[0]); ys0.append(b[1]); xs1.append(b[0] + b[2]); ys1.append(b[1] + b[3])
    return (min(xs0), min(ys0), max(xs1), max(ys1)) if xs0 else None


def _covered(run: _Run) -> Set[int]:
    """Тексты страницы внутри сматченного инстанса, которые макет называет слотами этого инстанса (`component.content`,
    `textProps`): у инстанса в макете детей-текстов нет, поэтому такой текст — не лишний, а его же содержимое."""
    D, I = run.D, run.I
    out: Set[int] = set()
    for d, i in run.dpair.items():
        if D.nodes[d].get("kind") != "instance" or not D.own[d]:
            continue
        mine = {run._mapped(t) for t in D.own[d]} | D.own[d]
        for j in range(i + 1, I.end[i]):
            if I.role[j] == "text" and j not in run.ipair and I.own[j] and I.own[j] <= mine:
                out.add(j)
    return out


def _extra(run: _Run, i_matched: List[int], i_content: List[int], covered: Set[int]) -> Tuple[List[Dict[str, Any]], int]:
    I = run.I
    r = run.dpair[0]
    region = _region(run)
    out: List[Dict[str, Any]] = []
    chrome = 0
    stack = list(reversed(I.kids[r]))
    while stack:
        j = stack.pop()
        if j in run.ipair or j in covered:
            stack.extend(reversed(I.kids[j]))
            continue
        if i_matched[I.end[j]] - i_matched[j + 1] > 0:
            stack.extend(reversed(I.kids[j]))
            continue
        cnt = i_content[I.end[j]] - i_content[j]
        if not cnt:
            continue
        b = I.box(j)
        if region and b:
            dx = min(b[0] + b[2], region[2]) - max(b[0], region[0])
            dy = min(b[1] + b[3], region[3]) - max(b[1], region[1])
            if dx <= REGION_TOL or dy <= REGION_TOL:
                chrome += 1
                continue
        n = I.nodes[j]
        rec: Dict[str, Any] = {"id": n["id"], "kind": n.get("kind"), "contentCount": cnt}
        if n.get("tag"):
            rec["tag"] = n["tag"]
        if I.role[j] == "text" and n.get("text"):
            rec["text"] = str(n["text"])[:80]
        elif I.names[j]:
            rec["component"] = (n.get("component") or {}).get("implName")
        out.append(rec)
    return out, chrome


def _order(run: _Run) -> List[Dict[str, Any]]:
    D = run.D
    out: List[Dict[str, Any]] = []
    for p in range(len(D.nodes)):
        kids = [k for k in D.kids[p] if k in run.dpair]
        if len(kids) < 2:
            continue
        keys = [run.dpair[k] for k in kids]
        keep = _lis_keep(keys)
        ranks = {k: r for r, k in enumerate(sorted(keys))}
        for pos, k in enumerate(kids):
            if pos not in keep:
                out.append({"id": D.nodes[k]["id"], "impl": run.I.nodes[run.dpair[k]]["id"], "parent": D.nodes[p]["id"],
                            "designIndex": pos, "implIndex": ranks[keys[pos]]})
    return out


def match(design: Any, impl: Any, plan: Any = None, descend_root: bool = True) -> Dict[str, Any]:
    """Макет и страница (конверты канона или корни) [+ plan.json] → пары и находки (см. докстринг модуля).

    `descend_root=False` — режим модели кода: корень макета парой с корнем дерева как есть (`code:root` — синтетика) и инлайн-компонент
    (frame с именем) — прозрачная обёртка, а не соперник; чёрные ящики пакетов (`instance`) остаются компонентами.
    Выход: pairs, missing, extra, order, stats, hardGate, lowMatchRate. Один вход — побайтно один выход."""
    d_root, i_root = _root_of(design), _root_of(impl)
    if d_root is None:
        raise MatchError("макет пуст: сравнивать не с чем")
    if i_root is None:
        i_root = {"id": "dom:empty", "kind": "frame", "box": {"x": 0, "y": 0, "w": 0, "h": 0}}
    run = _Run(d_root, i_root, plan, descend_root)
    run.run()
    D, I = run.D, run.I
    content_total = sum(D.content)
    matched_content = sum(1 for d in range(len(D.nodes)) if D.content[d] and d in run.dpair)
    matched_pref = _prefix([d in run.dpair for d in range(len(D.nodes))])
    content_pref = _prefix(D.content)
    covered = _covered(run)
    i_matched = _prefix([j in run.ipair or j in covered for j in range(len(I.nodes))])
    i_content = _prefix(I.content)

    rate = round(matched_content / content_total, 4) if content_total else 1.0
    gate_ok = rate >= MIN_MATCH_RATE and not (content_total and not sum(I.content))
    low = gate_ok and rate < LOW_MATCH_RATE
    missing = _missing(run, matched_pref, content_pref)
    if low:
        missing = _collapse(missing, run)
    extra, chrome = _extra(run, i_matched, i_content, covered)
    order = _order(run)
    r = run.dpair[0]
    pairs = [{"design": D.nodes[d]["id"], "impl": I.nodes[j]["id"], "anchor": run.info[d][0], "confidence": run.info[d][1],
              "score": run.info[d][2]} for d, j in sorted(run.dpair.items())]
    low_pairs = sum(1 for p in pairs if p["confidence"] == "low")
    reason = None
    if not gate_ok:
        reason = (f"matchRate {rate} < {MIN_MATCH_RATE}: страница — не этот кадр (или не загрузилась); дефекты сравнения не издаются"
                  if sum(I.content) else "на странице нет содержимого: сравнивать нечего")
    return {
        "pairs": pairs, "missing": missing, "extra": extra, "order": order,
        "hardGate": {"pass": gate_ok, "matchRate": rate, "min": MIN_MATCH_RATE, **({"reason": reason} if reason else {})},
        "lowMatchRate": low,
        "stats": {"designContent": content_total, "matchedContent": matched_content, "matchRate": rate,
                  "unmatched": content_total - matched_content, "pairs": len(pairs), "lowConfidence": low_pairs,
                  "missing": len(missing), "extra": len(extra), "order": len(order),
                  "outsideRegion": chrome, "ignored": run.ignored,
                  "implContent": sum(I.content), "rootImpl": I.nodes[r]["id"]},
    }
