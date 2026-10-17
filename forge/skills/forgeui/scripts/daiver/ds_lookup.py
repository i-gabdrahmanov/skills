"""ds_lookup.py — daiver_ds_inventory / daiver_ds_lookup / daiver_ds_details: ЧТЕНИЕ готовых требований дизайн-системы
(спека §10.3, §11.1 п.1, §20.4; INV-A1, A2, A4, A5; QG U9).

Что делает (документы строит задача 2.2, `daiver_ds_requirements`; здесь они только читаются)
  • `inventory(root)` — список компонентов ДС: имя, группа, короткое описание, пометка deprecated, счётчики. Читает
    КОММИТНУТЫЙ контракт и никогда не пересканирует библиотеку (INV-A4: один раз на трек, до резолва).
  • `lookup(root, figmaName, variants?, textSlotKeys?, limit?)` — шортлист кандидатов по имени узла макета. Ранжирует
    КОД (INV-A1), ответ объясняет баллы; пустой список — настоящий ответ «в библиотеке такого нет» → local.
  • `details(root, components)` — факты о названных компонентах (до 10 за вызов): пропы с допустимыми значениями,
    дефолты, deprecations, примеры историй (JSX). Длинное сокращается кодом под потолок размера ответа.
Пару документов читает только `ds_requirements.load_pair` (схема, смысл и один хеш источников проверены); пары нет,
она битая или построена для другой библиотеки — отказ `blocked: library-source-unreachable` с названной причиной и
указанием, какой инструмент строит требования. Мнение «на глаз» о библиотеке эти инструменты не заменяют (INV-A2).

Ранжирование — правило, выведенное из текста спеки §10.3 (образца боевой ДС нет: допущения помечены «не проверено
данными»). Имя — тот же ключ, что у карты компонентов (`component_map.name_key`: NFKC → casefold → буквы и цифры),
поэтому «Modal Header», «modal-header» и «ModalHeader» — одно имя. Кандидат — только компонент со связью по имени с
запросом; вариант, слот или истории без связи по имени кандидата не создают (иначе список никогда не бывал бы пустым).
Уровни имени (основа балла; разрыв между уровнями больше любых кредитов, поэтому точное имя — максимум):
    exact   1000  ключи равны (регистр и разделители не важны); 900, если равно только имя из `title` (имя в коде — главное)
    word     600  одно имя целиком входит в другое как слово(а): Button в PrimaryButton   (+ доля длины 0..99)
    part     300  одно имя входит в другое внутри слова: Tab в Tabs                       (+ доля длины 0..99)
    shared   100  только общие слова ≥ 3 знаков: ModalHeader ~ TableHeader               (+ доля общих слов 0..100)
Слова — по разделителям, camelCase (aB, ABc) и границе буква/цифра. Часть короче MIN_PART знаков в `word`/`part` не
участвует. Синонимов, опечаток и расстояния Левенштейна нет намеренно: «Notification» не найдёт «Alert» — это решение
модели по inventory, а не угадывание кода. Имя в `title` (последний сегмент «Группа/Компонент») — второе имя того же
компонента (данные самой ДС, сверх спеки); лучшее из двух решает, при равенстве — имя из кода; точное совпадение только по
`title` — TITLE_EXACT: ниже точного имени в коде (у другого компонента), выше любого `word`.
Кредиты (только упорядочивают внутри уровня, сумма ≤ CREDIT_CAP):
    +10  вариант узла объявлен пропом компонента (ключи равны по name_key)
    +15  значение этого варианта компонент принимает: enum — значение среди options; boolean — true/false; иначе —
         среди usedValues (реальные значения историй); без догадок («Large» не станет «lg»)
    +10  ключ текстового слота узла есть среди пропов вида string | node
    +1…5 истории (число документированных конфигураций, не больше DOCS_MAX): небольшой бонус за документированность
Равные баллы — по ключу имени, затем по имени (порядок вставки в документ роли не играет).

`propsClosed` и `usedValues` читаются как в 2.2: пропа вне списка нет только при `propsClosed: true`; usedValues —
подтверждённый словарь, не запрет. Хеш-устаревание (файлы библиотеки менялись после сборки) эти инструменты НЕ
проверяют — оно требует чтения библиотеки; его говорит `daiver_ds_requirements(check:true)`. Дёшево проверяемое
устаревание (library.source и scanPaths конфига против документа) — отказ, как у отсутствующей пары.

Потолки ответов (слабая модель-читатель: выдача короткая и однозначная; свойство держит код, а не надежда)
  • lookup — не больше MAX_LIMIT кандидатов, `why` ≤ WHY_MAX знаков, `deprecated` ≤ DEPRECATION_TEXT; худший случай (20 кандидатов,
    длинные пути импорта, 30 вариантов и 30 слотов на входе) — около 12 КБ в indent=1, ответ по умолчанию (5) — около 3 КБ;
  • details — цель DETAILS_TOTAL на вызов делится на число компонентов, не больше DETAILS_PER_COMPONENT на одного; пять ступеней
    сокращения (LEVELS): сначала проза `docs`, затем примеры, описания пропов, хвост пропов (его имена остаются в `moreProps`); поле
    `cut` называет убранное; deprecations ступенями не сокращаются (не больше MAX_DEPRECATIONS записей);
  • inventory — INVENTORY_BUDGET: сначала отбрасываются описания, затем остаются одни имена; список имён не режется никогда.

Не проверено данными (боевой ДС нет; всё выведено из текста спеки §10.3 и проверено на синтетической fake-ds): пороги и веса
(MIN_PART, W_*, DOCS_MAX, разрывы уровней), поведение на реальных парах «имя макета ↔ имя кода» (префиксы библиотеки, множественное
число, синонимы), бюджеты размера ответов, смысл «значение принимается» для kind=string/number (usedValues как словарь).
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import component_map as CM
from . import config as CFG
from . import ds_requirements as DR

DEFAULT_LIMIT = 5                       # спека §20.4
MIN_LIMIT, MAX_LIMIT = 1, 20            # §20.4: «схема клампит 1..20»
MAX_DETAILS = 10                        # §20.4: details ≤ 10 компонентов за вызов

# уровни имени (основа балла) и кредиты — см. докстринг
EXACT, TITLE_EXACT, WORD, PART, SHARED = 1000, 900, 600, 300, 100
MIN_PART = 3
W_DECLARED, W_ACCEPTED, W_SLOT, DOCS_MAX = 10, 15, 10, 5
CREDIT_CAP = 99

MAX_NAME = CM.MAX_NAME                  # длина имени запроса — как у карты
MAX_VARIANTS = CM.MAX_VARIANTS
MAX_VALUE = CM.MAX_ICON
MAX_SLOTS = 30
SHOWN_KEYS = 4                          # сколько ключей вариантов/слотов называет `why`
WHY_MAX = 220

INVENTORY_DESC = 64                     # короткое описание в строке inventory
INVENTORY_BUDGET = 32000                # потолок ответа inventory (знаков в indent=1), иначе описания отбрасываются

DETAILS_TOTAL = 26000                   # цель для всего ответа details (знаков в indent=1), делится на число компонентов
DETAILS_PER_COMPONENT = 6000            # …и потолок одного компонента (один-два финалиста — свободно)
MAX_DEPRECATIONS = 12
DEPRECATION_TEXT = 140

BLOCKED = "library-source-unreachable"  # спека §20.3
BUILD_HINT = ("Требования ДС строит daiver_ds_requirements (после daiver_init; проверка — check:true; при пустом "
              "результате поправь library.scanPaths в .daiver/config.yaml). Пока их нет — резолв не начинать и "
              "компоненты библиотеки «на глаз» не подбирать")

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_PLAIN = re.compile(r"^[\w.\-]+$")
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


class DsLookupError(ValueError):
    """Вызов не годится или библиотека не читается — сообщение называет причину и что делать."""


class LibraryUnreachable(DsLookupError):
    """`blocked: library-source-unreachable` (спека §20.3): требований ДС нет, они битые или построены для другой
    библиотеки. Резолв стоит ЗДЕСЬ, до первого кандидата."""
    reason = BLOCKED

    def __init__(self, cause: str):
        super().__init__(f"blocked: {BLOCKED} — {cause}. {BUILD_HINT}")
        self.cause = cause


# ── имена и слова ────────────────────────────────────────────────────────────

def words(name: str) -> Tuple[str, ...]:
    """Слова имени: границы — не-буквы/цифры, camelCase (aB, ABc: «XMLParser» → XML, Parser) и буква/цифра
    («Icon24» → Icon, 24); каждое слово — ключ имени карты (`name_key`)."""
    text = unicodedata.normalize("NFKC", name)
    out: List[str] = []
    cur: List[str] = []
    prev = ""
    for i, ch in enumerate(text):
        if not ch.isalnum():
            if cur:
                out.append("".join(cur))
                cur = []
            prev = ""
            continue
        cls = "d" if ch.isdigit() else ("u" if ch.isupper() else "l")
        if cur:
            cut = ((prev == "d") != (cls == "d")
                   or (prev == "l" and cls == "u")
                   or (prev == "u" and cls == "u" and i + 1 < len(text) and text[i + 1].islower()))
            if cut:
                out.append("".join(cur))
                cur = []
        cur.append(ch)
        prev = cls
    if cur:
        out.append("".join(cur))
    return tuple(k for k in (CM.name_key(w) for w in out) if k)


def _useful(word: str) -> bool:
    """Слово для «общих слов»: от 3 знаков и не число."""
    return len(word) >= MIN_PART and not word.isdigit()


def _has_run(hay: Tuple[str, ...], needle: Tuple[str, ...]) -> bool:
    m = len(needle)
    return m > 0 and any(hay[i:i + m] == needle for i in range(len(hay) - m + 1))


class _Name:
    """Имя в двух видах: ключ (равенство, вхождение) и слова (слова целиком, общие слова)."""
    __slots__ = ("raw", "key", "words")

    def __init__(self, raw: str):
        self.raw, self.key, self.words = raw, CM.name_key(raw), words(raw)


def relate(q: _Name, n: _Name) -> Optional[Tuple[str, int, str]]:
    """Связь имени запроса с именем компонента: (уровень, основа балла, что именно совпало) или None — связи нет."""
    if not q.key or not n.key:
        return None
    if q.key == n.key:
        return "exact", EXACT, ""
    s, l = (q, n) if len(q.key) <= len(n.key) else (n, q)
    if len(s.key) >= MIN_PART and s.key in l.key:
        share = (100 * len(s.key)) // len(l.key)
        if _has_run(l.words, s.words):
            return "word", WORD + share, " ".join(s.words)
        return "part", PART + share, f"«{s.key}» в «{l.key}»"
    a = {w for w in q.words if _useful(w)}
    b = {w for w in n.words if _useful(w)}
    common = a & b
    if common:
        return "shared", SHARED + (100 * len(common)) // len(a | b), ", ".join(sorted(common))
    return None


def title_leaf(title: Optional[str]) -> Optional[str]:
    """Последний сегмент `title` («Формы/Текстовое поле» → «Текстовое поле»): отображаемое имя компонента в ДС."""
    if not title:
        return None
    leaf = title.rsplit("/", 1)[-1].strip()
    return leaf or None


# ── чтение пары документов ───────────────────────────────────────────────────

class Library:
    """Прочитанная и проверенная пара требований ДС (контракт + подробности)."""
    __slots__ = ("source", "contract", "details", "comps", "det", "names", "by_key")

    def __init__(self, source: str, contract: Dict[str, Any], details: Dict[str, Any]):
        self.source = source
        self.contract = contract
        self.details = details
        self.comps: Dict[str, Any] = contract["components"]
        self.det: Dict[str, Any] = details["components"]
        # порядок обхода — по ключу имени, а не по порядку вставки в документ
        self.names: List[str] = sorted(self.comps, key=lambda n: (CM.name_key(n), n))
        self.by_key: Dict[str, List[str]] = {}
        for n in self.names:
            self.by_key.setdefault(CM.name_key(n), []).append(n)


def _problems_text(problems: List[str]) -> str:
    if all(p.startswith("нет ") for p in problems):
        return "требований ДС нет: " + "; ".join(problems)
    tail = f"; … и ещё {len(problems) - 3}" if len(problems) > 3 else ""
    return "требования ДС не читаются: " + "; ".join(problems[:3]) + tail


def load(root: Any) -> Library:
    """Пара документов через `ds_requirements.load_pair` (библиотеку не читает и не сканирует). Отказы: проект не
    инициализирован; library.source = storybook-mcp (документов там нет по построению, сервер и есть источник);
    пары нет / она битая / построена для другого library.source или scanPaths — `LibraryUnreachable`."""
    root = Path(root)
    cfg = CFG.load(root)
    if not cfg["initialized"]:
        why = "; ".join(cfg["problems"]) or "конфиг не разобран"
        raise DsLookupError(f"проект не инициализирован ({why}) — сначала daiver_init")
    lib = cfg["config"]["library"]
    if lib["source"] == "storybook-mcp":
        raise DsLookupError(
            f"library.source = storybook-mcp: компоненты и их сигнатуры отдаёт MCP-сервер сторибука «{lib['storybookMcp']}» — "
            f"спрашивай его; daiver_ds_inventory / daiver_ds_lookup / daiver_ds_details читают только документы режимов "
            f"stories | sources (при storybook-mcp их нет по построению)")
    contract, details, problems = DR.load_pair(root)
    if problems:
        raise LibraryUnreachable(_problems_text(problems))
    src = contract["source"]
    stale: List[str] = []
    if src["librarySource"] != lib["source"]:
        stale.append(f"library.source изменился: {src['librarySource']} → {lib['source']}")
    if src["scanPaths"] != list(lib["scanPaths"]):
        stale.append("library.scanPaths изменились с момента сборки")
    if stale:
        raise LibraryUnreachable("требования ДС устарели (" + "; ".join(stale) + ")")
    return Library(lib["source"], contract, details)


# ── import и значения ────────────────────────────────────────────────────────

def import_statement(name: str, imp: Optional[Dict[str, Any]]) -> Optional[str]:
    """Statement импорта компонента из записи контракта — в том же виде, что у карты компонентов (B17: вставляется
    дословно). Нет записи или она не складывается в корректный код — None: путь не угадывается."""
    if not imp:
        return None
    binding = name.split(".")[0]                       # составной Form.Item импортирует корень Form
    path = imp.get("path")
    if (not _IDENT.match(binding) or not isinstance(path, str) or '"' in path or "\\" in path or _CTRL.search(path)):
        return None
    if imp.get("default"):
        return f'import {binding} from "{path}";'
    named = imp.get("named")
    if not isinstance(named, str) or not _IDENT.match(named):
        return None
    if named == binding:
        return f'import {{ {binding} }} from "{path}";'
    return f'import {{ {named} as {binding} }} from "{path}";'


def _vkey(v: Any) -> str:
    """Ключ значения для сравнения: регистр и разделители не важны, число — как строка («16» = 16)."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return CM.name_key(str(v))


def accepted_value(prop: Dict[str, Any], used: List[Any], value: Any) -> Tuple[bool, Any]:
    """(принимает ли проп значение варианта, значение в написании ДС). enum — только options; boolean — true/false;
    остальные виды — значения, которые реально передавали истории (usedValues). Соответствий по смыслу нет."""
    kind = prop["kind"]
    if kind == "enum":
        pool = prop.get("options") or []
    elif kind == "boolean":
        pool = [True, False]
    else:
        pool = used
    want = _vkey(value)
    if want:
        for c in pool:
            if _vkey(c) == want:
                return True, c
    return False, None


def show(v: Any) -> str:
    """Скаляр для текста ответа: простые слова как есть, остальное — JSON (без неоднозначности)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v if _PLAIN.match(v) else json.dumps(v, ensure_ascii=False)
    return str(v)


def clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:max(limit - 1, 0)].rstrip() + "…"


def one_line(text: Optional[str], limit: int) -> str:
    return clip(" ".join((text or "").split()), limit)


# ── кредиты ──────────────────────────────────────────────────────────────────

class _Credit:
    __slots__ = ("accepted", "refused", "absent", "slots", "no_slot", "points", "stories")

    def __init__(self) -> None:
        self.accepted: List[Tuple[str, Any]] = []      # (проп, значение в написании ДС)
        self.refused: List[Tuple[str, Any]] = []       # (проп, значение варианта макета) — проп есть, значение не принято
        self.absent: List[str] = []                    # ключи вариантов макета, для которых пропа нет
        self.slots: List[str] = []                     # пропы-слоты, совпавшие с ключами текстовых слотов
        self.no_slot: List[str] = []                   # ключи слотов без подходящего пропа
        self.points = 0
        self.stories = 0


def prop_index(props: Dict[str, Any]) -> Dict[str, str]:
    """Ключ имени → проп. Два пропа с одним ключом (`aria-label`, `ariaLabel`) — берётся меньшее имя."""
    idx: Dict[str, str] = {}
    for p in sorted(props):
        k = CM.name_key(p)
        if k:
            idx.setdefault(k, p)
    return idx


def credit(comp: Dict[str, Any], variants: List[Tuple[str, Any]], slots: List[str]) -> _Credit:
    props, used = comp["props"], comp["usedValues"]
    idx = prop_index(props)
    c = _Credit()
    seen = set()
    declared = 0
    for key, value in variants:
        p = idx.get(CM.name_key(key))
        if p is None:
            c.absent.append(key)
            continue
        if p in seen:
            continue
        seen.add(p)
        declared += 1
        ok, shown = accepted_value(props[p], used.get(p, []), value)
        (c.accepted if ok else c.refused).append((p, shown if ok else value))
    for key in slots:
        p = idx.get(CM.name_key(key))
        if p is not None and props[p]["kind"] in ("string", "node"):
            c.slots.append(p)
        else:
            c.no_slot.append(key)
    c.stories = comp["stories"]
    c.points = min(CREDIT_CAP, declared * W_DECLARED + len(c.accepted) * W_ACCEPTED + len(c.slots) * W_SLOT
                   + min(c.stories, DOCS_MAX))
    return c


# ── ранжирование ─────────────────────────────────────────────────────────────

class _Query:
    __slots__ = ("name", "variants", "slots")

    def __init__(self, name: str, variants: List[Tuple[str, Any]], slots: List[str]):
        self.name, self.variants, self.slots = _Name(name), variants, slots


class _Hit:
    __slots__ = ("name", "kind", "score", "detail", "leaf", "credit")

    def __init__(self, name: str, kind: str, score: int, detail: str, leaf: Optional[str], cr: _Credit):
        self.name, self.kind, self.score, self.detail, self.leaf, self.credit = name, kind, score, detail, leaf, cr


def rank(lib: Library, q: _Query) -> List[_Hit]:
    """Все компоненты со связью по имени, лучшие первыми: балл по убыванию, затем ключ имени и имя. Порядок вставки
    документа на результат не влияет."""
    hits: List[_Hit] = []
    for name in lib.names:
        comp = lib.comps[name]
        best = relate(q.name, _Name(name))
        leaf_used = None
        leaf = title_leaf(comp["title"])
        if leaf and CM.name_key(leaf) != CM.name_key(name):
            r = relate(q.name, _Name(leaf))
            if r and r[0] == "exact":
                r = (r[0], TITLE_EXACT, r[2])                   # основное имя — в коде; title — запасное
            if r and (best is None or r[1] > best[1]):          # при равенстве — имя из кода
                best, leaf_used = r, leaf
        if best is None:
            continue
        cr = credit(comp, q.variants, q.slots)
        hits.append(_Hit(name, best[0], best[1] + cr.points, best[2], leaf_used, cr))
    hits.sort(key=lambda h: (-h.score, CM.name_key(h.name), h.name))
    return hits


def _listed(items: List[str]) -> str:
    shown = items[:SHOWN_KEYS]
    return ", ".join(shown) + (f" +{len(items) - SHOWN_KEYS}" if len(items) > SHOWN_KEYS else "")


def why(h: _Hit) -> str:
    """Почему кандидат здесь: уровень имени, варианты, слоты, истории — одной строкой, сегменты через « · »."""
    segs: List[str] = []
    if h.kind == "exact":
        segs.append("имя совпало")
    else:
        label = {"word": "слово в имени", "part": "часть имени", "shared": "общие слова"}[h.kind]
        segs.append(f"{label}: {h.detail}")
    if h.leaf:
        segs[0] += f" (по title «{h.leaf}»)"
    c = h.credit
    var: List[str] = []
    if c.accepted:
        var.append("принимает " + _listed([f"{p}={show(v)}" for p, v in c.accepted]))
    if c.refused:
        var.append("не принимает " + _listed([f"{p}={show(v)}" for p, v in c.refused]))
    if c.absent:
        var.append("нет пропа " + _listed([show(k) for k in c.absent]))
    if var:
        segs.append("варианты: " + "; ".join(var))
    slot: List[str] = []
    if c.slots:
        slot.append(_listed(c.slots))
    if c.no_slot:
        slot.append("нет пропа " + _listed([show(k) for k in c.no_slot]))
    if slot:
        segs.append("слоты: " + "; ".join(slot))
    if c.stories:
        segs.append(f"истории: {c.stories}")
    return clip(" · ".join(segs), WHY_MAX)


def deprecation_of(det: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Запись `@deprecated` самого компонента (не пропа) или None."""
    for d in (det or {}).get("deprecations", []):
        if d["target"] == "component":
            return d
    return None


def deprecation_text(d: Dict[str, Any]) -> str:
    msg = one_line(d.get("message"), DEPRECATION_TEXT)
    if msg:
        return msg
    return f"заменён на {d['replacement']}" if d.get("replacement") else "deprecated"


# ── аргументы ────────────────────────────────────────────────────────────────

def _name_arg(value: Any, problems: List[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append("figmaName: непустая строка — имя компонента из канона (component.figmaName)")
        return ""
    s = value.strip()
    if len(s) > MAX_NAME or _CTRL.search(s):
        problems.append(f"figmaName: не длиннее {MAX_NAME} символов и без управляющих символов")
        return ""
    if not CM.name_key(s):
        problems.append("figmaName: в имени нет ни букв, ни цифр — искать нечего")
        return ""
    return s


def _variants_arg(v: Any, problems: List[str]) -> List[Tuple[str, Any]]:
    if v is None:
        return []
    if not isinstance(v, dict):
        problems.append("variants: объект {ключ: значение} — варианты узла из канона (component.variants)")
        return []
    if len(v) > MAX_VARIANTS:
        problems.append(f"variants: больше {MAX_VARIANTS} вариантов")
        return []
    out: List[Tuple[str, Any]] = []
    for k, x in v.items():
        if (not isinstance(k, str) or not k.strip() or isinstance(x, (dict, list)) or x is None
                or (isinstance(x, str) and len(x) > MAX_VALUE)):
            problems.append("variants: ключ — непустая строка, значение — короткая строка, число или true/false")
            return []
        out.append((k.strip(), x))
    return sorted(out, key=lambda kv: (CM.name_key(kv[0]), kv[0]))


def _slots_arg(v: Any, problems: List[str]) -> List[str]:
    if v is None:
        return []
    if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
        problems.append("textSlotKeys: список строк — КЛЮЧИ текстовых слотов узла (component.textProps), без значений")
        return []
    if len(v) > MAX_SLOTS:
        problems.append(f"textSlotKeys: больше {MAX_SLOTS} ключей")
        return []
    out: Dict[str, str] = {}
    for x in v:
        s = x.strip()
        if not s or len(s) > MAX_VALUE or _CTRL.search(s):
            problems.append("textSlotKeys: ключ — непустая короткая строка без управляющих символов")
            return []
        out.setdefault(s, s)
    return sorted(out, key=lambda s: (CM.name_key(s), s))


def _limit_arg(v: Any, problems: List[str]) -> Tuple[int, Optional[int]]:
    """(применённый limit, заданный — если его пришлось привести к границе). Вне 1..20 — граница (§20.4: «схема
    клампит»); не целое число — отказ."""
    if v is None:
        return DEFAULT_LIMIT, None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not v.is_integer()):
        problems.append(f"limit: целое число {MIN_LIMIT}..{MAX_LIMIT} (по умолчанию {DEFAULT_LIMIT})")
        return DEFAULT_LIMIT, None
    n = int(v)
    lim = min(MAX_LIMIT, max(MIN_LIMIT, n))
    return lim, (n if lim != n else None)


# ── lookup ───────────────────────────────────────────────────────────────────

NO_MATCH = ("в библиотеке нет компонента с таким именем или его частью → решение local (свой компонент). Синонимов тут "
            "нет: если по смыслу подходит другое имя из daiver_ds_inventory — проверь его daiver_ds_details")


def _candidate(lib: Library, h: _Hit) -> Dict[str, Any]:
    comp = lib.comps[h.name]
    out: Dict[str, Any] = {"name": h.name, "score": h.score, "match": h.kind, "why": why(h),
                           "import": import_statement(h.name, comp["import"]), "hasDetails": h.name in lib.det}
    dep = deprecation_of(lib.det.get(h.name))
    if dep:
        out["deprecated"] = deprecation_text(dep)
    return out


def lookup(root: Any, figma_name: Any, variants: Any = None, text_slot_keys: Any = None,
           limit: Any = None) -> Dict[str, Any]:
    """Шортлист по имени узла макета: `{limit, total, candidates: [...]}`. Пустой `candidates` — настоящий ответ, не
    ошибка. Все нарушения аргументов называются одним сообщением."""
    problems: List[str] = []
    name = _name_arg(figma_name, problems)
    vs = _variants_arg(variants, problems)
    slots = _slots_arg(text_slot_keys, problems)
    lim, asked = _limit_arg(limit, problems)
    if problems:
        raise DsLookupError("; ".join(problems))
    lib = load(root)
    hits = rank(lib, _Query(name, vs, slots))
    out: Dict[str, Any] = {"limit": lim, "total": len(hits), "candidates": [_candidate(lib, h) for h in hits[:lim]]}
    if asked is not None:
        out["limitNote"] = f"limit {asked} приведён к {lim}: допустимо {MIN_LIMIT}..{MAX_LIMIT}"
    if not hits:
        out["note"] = NO_MATCH
    return out


# ── inventory ────────────────────────────────────────────────────────────────

def _size(obj: Any) -> int:
    """Размер ответа так, как его печатает MCP-сервер (indent=1) — в знаках."""
    return len(json.dumps(obj, ensure_ascii=False, indent=1))


def _marker(dep: Optional[Dict[str, Any]]) -> str:
    if not dep:
        return ""
    return f"[DEPRECATED → {dep['replacement']}]" if dep.get("replacement") else "[DEPRECATED]"


def _inventory_line(lib: Library, name: str, with_desc: bool) -> str:
    comp, det = lib.comps[name], lib.det.get(name)
    parts = [name]
    mark = _marker(deprecation_of(det))
    if mark:
        parts.append(mark)
    if comp["taxonomy"]:
        parts.append("(" + "/".join(comp["taxonomy"]) + ")")
    line = " ".join(parts)
    if with_desc:
        desc = one_line(det["description"] if det else None, INVENTORY_DESC)
        if desc:
            line += " — " + desc
    return line


def inventory(root: Any) -> Dict[str, Any]:
    """Список компонентов ДС из КОММИТНУТОГО контракта (библиотеку не читает и не сканирует). Строка — `Имя
    [DEPRECATED → замена] (группа) — описание`; счётчики — в шапке. Не влезает в INVENTORY_BUDGET — сначала
    отбрасываются описания, затем остаются одни имена: список имён не режется никогда."""
    lib = load(root)
    src = lib.contract["source"]
    comps = lib.comps
    deprecated = [n for n in lib.names if deprecation_of(lib.det.get(n))]
    out: Dict[str, Any] = {
        "librarySource": lib.source, "build": f"{src['scanner']} {src['sha256'][:12]}", "count": len(comps),
        "stories": sum(c["stories"] for c in comps.values()), "withDetails": len(lib.det),
        "withoutImport": sum(1 for c in comps.values() if c["import"] is None),
        "deprecated": deprecated[:20] + ([f"… и ещё {len(deprecated) - 20}"] if len(deprecated) > 20 else []),
        "format": "Имя [DEPRECATED → замена] (группа) — описание"}
    for with_desc in (True, False):
        out["components"] = [_inventory_line(lib, n, with_desc) for n in lib.names]
        if not with_desc:
            out["trimmed"] = "описания отброшены (список не влезает в потолок ответа): смысл компонента — daiver_ds_details"
        if _size(out) <= INVENTORY_BUDGET:
            break
    else:
        del out["components"]
        out["names"] = ", ".join(lib.names)
        out["trimmed"] = "оставлены одни имена (список не влезает в потолок ответа); группы и описания — daiver_ds_details"
        out["format"] = "names — все имена через запятую; deprecated — в шапке"
    out["next"] = ("список читается ОДИН раз на трек; на кандидата: daiver_map_lookup → если hit:false, daiver_ds_lookup → "
                   "daiver_ds_details для 1–2 финалистов")
    return out


# ── details ──────────────────────────────────────────────────────────────────

class _Level:
    """Ступень сокращения ответа details: чем выше номер, тем меньше входит."""
    __slots__ = ("docs", "examples", "pdesc", "props", "options", "desc")

    def __init__(self, docs: int, examples: int, pdesc: int, props: int, options: int, desc: int):
        self.docs, self.examples, self.pdesc, self.props, self.options, self.desc = docs, examples, pdesc, props, options, desc


LEVELS = (
    _Level(docs=600, examples=3, pdesc=100, props=40, options=12, desc=300),
    _Level(docs=0, examples=2, pdesc=100, props=40, options=12, desc=300),
    _Level(docs=0, examples=1, pdesc=60, props=30, options=8, desc=200),
    _Level(docs=0, examples=0, pdesc=40, props=20, options=6, desc=160),
    _Level(docs=0, examples=0, pdesc=0, props=10, options=4, desc=120),
)
MORE_PROPS_MAX = 300
USED_SHOWN = 6


def _scalar(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _prop_line(prop: Dict[str, Any], dprop: Optional[Dict[str, Any]], used: List[Any], dep: Optional[Dict[str, Any]],
               lv: _Level) -> str:
    kind = prop["kind"]
    if kind == "enum":
        opts = prop["options"]
        head = "enum: " + " | ".join(_scalar(o) for o in opts[:lv.options])
        if len(opts) > lv.options:
            head += f" | …+{len(opts) - lv.options}"
    else:
        head = "node (слот)" if kind == "node" else kind
    segs = [head]
    if prop.get("required"):
        segs.append("required")
    if dprop and "default" in dprop:
        segs.append("default " + _scalar(dprop["default"]))
    if kind != "enum" and used:
        segs.append("used " + ", ".join(_scalar(v) for v in used[:USED_SHOWN])
                    + (f", …+{len(used) - USED_SHOWN}" if len(used) > USED_SHOWN else ""))
    if dep is not None:
        segs.append("DEPRECATED" + (f" → {dep['replacement']}" if dep.get("replacement") else ""))
    line = "; ".join(segs)
    if lv.pdesc and dprop and dprop.get("description"):
        line += " — " + one_line(dprop["description"], lv.pdesc)
    return line


def _deprecations(det: Optional[Dict[str, Any]]) -> List[str]:
    items = (det or {}).get("deprecations", [])
    out = []
    for d in items[:MAX_DEPRECATIONS]:
        text = f"{d['target']} {d['name']}: {deprecation_text(d)}"
        if d.get("replacement"):
            text += f" [замена: {d['replacement']}]"
        out.append(text)
    if len(items) > MAX_DEPRECATIONS:
        out.append(f"… и ещё {len(items) - MAX_DEPRECATIONS}")
    return out


def _entry(lib: Library, name: str, lv: _Level) -> Dict[str, Any]:
    comp, det = lib.comps[name], lib.det.get(name)
    props = comp["props"]
    dprops = (det or {}).get("props", {})
    prop_deps = {d["name"]: d for d in (det or {}).get("deprecations", []) if d["target"] == "prop"}
    cut: List[str] = []
    out: Dict[str, Any] = {"name": name}
    if comp["title"]:
        out["title"] = comp["title"]
    out["import"] = import_statement(name, comp["import"])
    out["propsClosed"] = comp["propsClosed"]
    out["stories"] = comp["stories"]
    out["hasDetails"] = det is not None
    desc = one_line(det["description"] if det else None, lv.desc)
    if desc:
        out["description"] = desc
    deps = _deprecations(det)
    if deps:
        out["deprecations"] = deps
    order = sorted(props, key=lambda p: (not props[p].get("required"), CM.name_key(p), p))
    shown, rest = order[:lv.props], order[lv.props:]
    out["props"] = {p: _prop_line(props[p], dprops.get(p), comp["usedValues"].get(p, []), prop_deps.get(p), lv)
                    for p in shown}
    if rest:
        cut.append(f"props {len(shown)}/{len(order)}")
        out["moreProps"] = clip(", ".join(rest), MORE_PROPS_MAX)
    if lv.pdesc == 0 and any(dprops.get(p, {}).get("description") for p in shown):
        cut.append("prop descriptions")
    examples = (det or {}).get("examples", [])
    if examples and lv.examples:
        seen: Dict[str, int] = {}
        ex: Dict[str, str] = {}
        for e in examples[:lv.examples]:
            seen[e["name"]] = seen.get(e["name"], 0) + 1
            ex[e["name"] if seen[e["name"]] == 1 else f"{e['name']} ({seen[e['name']]})"] = e["jsx"]
        out["examples"] = ex
    if len(examples) > min(len(examples), lv.examples):
        cut.append(f"examples {min(len(examples), lv.examples)}/{len(examples)}")
    docs = (det or {}).get("docs")
    if docs and lv.docs:
        out["docs"] = clip(docs, lv.docs)
    elif docs:
        cut.append("docs")
    if cut:
        out["cut"] = "; ".join(cut)
    return out


def _fit(lib: Library, name: str, budget: int) -> Dict[str, Any]:
    """Самая полная ступень, что влезает в бюджет компонента; последняя берётся безусловно (она мала по построению)."""
    for lv in LEVELS[:-1]:
        entry = _entry(lib, name, lv)
        if _size(entry) <= budget:
            return entry
    return _entry(lib, name, LEVELS[-1])


def _components_arg(v: Any) -> List[str]:
    if not isinstance(v, list) or not v or any(not isinstance(x, str) for x in v):
        raise DsLookupError(f"components: непустой список имён компонентов (1..{MAX_DETAILS}) — берись за 1–2 финалистов "
                            f"daiver_ds_lookup")
    if len(v) > MAX_DETAILS:
        raise DsLookupError(f"components: {len(v)} имён — за вызов не больше {MAX_DETAILS}. Разбей на несколько вызовов "
                            f"(обычно хватает 1–2 финалистов lookup)")
    out = []
    for x in v:
        s = x.strip()
        if not s or len(s) > MAX_NAME or _CTRL.search(s):
            raise DsLookupError(f"components: имя — непустая строка не длиннее {MAX_NAME} символов без управляющих символов")
        out.append(s)
    return out


def _resolve(lib: Library, asked: List[str]) -> List[str]:
    """Имена запроса → имена контракта: точное имя, иначе единственное с тем же ключом (регистр и разделители не важны).
    Неизвестное и неоднозначное — отказ одним сообщением; компонент не выдумывается."""
    found: List[str] = []
    problems: List[str] = []
    for raw in asked:
        if raw in lib.comps:
            name = raw
        else:
            same = lib.by_key.get(CM.name_key(raw), [])
            if len(same) == 1:
                name = same[0]
            elif same:
                problems.append(f"«{raw}» подходит к нескольким компонентам ({', '.join(same)}) — укажи имя точно")
                continue
            else:
                near = [h.name for h in rank(lib, _Query(raw, [], []))[:3]] if CM.name_key(raw) else []
                problems.append(f"«{raw}» нет в контракте" + (f" (похожие по имени: {', '.join(near)})" if near else
                                                              " (по имени ничего похожего)"))
                continue
        if name not in found:
            found.append(name)
    if problems:
        raise DsLookupError("components: " + "; ".join(problems) + ". Имена берут из daiver_ds_lookup / "
                            "daiver_ds_inventory — компонент, которого там нет, придумывать нельзя")
    return found


def details(root: Any, components: Any) -> Dict[str, Any]:
    """Факты о названных компонентах (1..MAX_DETAILS за вызов), в порядке запроса. Каждый вмещается в свою долю
    потолка DETAILS_TOTAL: доки, примеры, описания и хвост пропов сокращаются кодом, `cut` называет что именно."""
    asked = _components_arg(components)
    lib = load(root)
    names = _resolve(lib, asked)
    budget = min(DETAILS_PER_COMPONENT, DETAILS_TOTAL // len(names))
    return {"components": [_fit(lib, n, budget) for n in names]}
