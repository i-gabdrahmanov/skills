"""anonymize.py — обезличивание Pixso DSL для фикстур в git (docs/forge-ui/tasks/1.0).

Образец макета — корпоративный дизайн: идентификаторы библиотеки и тексты в репозиторий класть
нельзя, а структура нужна тестам целиком. Обезличивание детерминированное: одинаковые вход,
соль и опции дают побайтно тот же файл; ссылки между полями сохраняются.

Что заменяется:
  • идентификаторы библиотеки — `publishFile`, `publishID`, `overrideKey`, `componentKey`.
    Замена ПО ЗНАЧЕНИЮ: одно и то же значение везде (в том числе как `defaultValue` свойства-слота
    типа instance) получает одну и ту же подмену, форма значения сохраняется (22 символа base64url /
    40 hex / «число:число»);
  • тексты — `nodeText` (и `characters` у других вариантов DSL): первое слово из букв становится
    «Текст<k>» (k — порядковый номер уникального текста), остальные слова — «слово»; цифры, знаки
    и пробелы остаются на местах (числа нужны тестам раскладки). Один исходный текст — одна подмена
    везде: связь «оверрайд совпал с плейсхолдером мастера» не теряется. Слой-текст, названный по
    своему содержимому, переименовывается так же;
  • (по флагу) названия шрифтов — подстрокой во всех значениях и ключах.
Не заменяется: guid, типы узлов, геометрия, краски, названия компонентов и стилей (это
структура, а не данные), ссылки между узлами.

Недостижимые из экрана определения компонентов по умолчанию выбрасываются (то же усечение
делает slim в задаче 1.2).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from . import dsl as D

DEFAULT_SALT = "forge-ui-fixture-v1"

ID_FIELDS = ("publishFile", "publishID", "overrideKey", "componentKey")
TEXT_FIELDS = ("nodeText", "characters")

_RE_FILE = re.compile(r"^[A-Za-z0-9_-]{22}$")
_RE_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_RE_NODE_ID = re.compile(r"^\d+:\d+$")
_LETTERS = re.compile(r"[^\W\d_]+", re.UNICODE)
_B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _digest(salt: str, kind: str, value: str, counter: int) -> bytes:
    return hashlib.sha256(f"{salt}\x00{kind}\x00{value}\x00{counter}".encode("utf-8")).digest()


def fake_id(value: str, salt: str, taken: Set[str]) -> str:
    """Подмена идентификатора той же формы; не совпадает ни с чем из taken (и пополняет его)."""
    for counter in range(1000):
        dg = _digest(salt, "id", value, counter)
        if _RE_HEX40.match(value):
            out = dg.hex()[:40]
        elif _RE_FILE.match(value):
            out = "".join(_B64URL[b % 64] for b in dg[:22])
        elif _RE_NODE_ID.match(value):
            # сессия 9000+ не бывает у настоящих guid файла («2:…», «22:…»)
            out = f"{9000 + int.from_bytes(dg[:2], 'big') % 900}:{100000 + int.from_bytes(dg[2:6], 'big') % 900000}"
        else:
            out = dg.hex()[:max(8, len(value))]
        if out not in taken and out != value:
            taken.add(out)
            return out
    raise RuntimeError(f"не удалось подобрать подмену для {value!r}")


def fake_text(text: str, k: int) -> str:
    """Подмена текста: первое слово из букв — «Текст<k>», остальные — «слово»; прочее на месте."""
    state = {"first": True}

    def rep(_m):
        if state["first"]:
            state["first"] = False
            return f"Текст{k}"
        return "слово"

    return _LETTERS.sub(rep, text)


def _walk_values(o: Any):
    """Все (ключ, значение) словарей и строки-элементы списков документа в порядке файла."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _walk_values(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_values(v)


def _collect_ids(doc: Any) -> List[str]:
    seen: List[str] = []
    marker: Set[str] = set()
    for k, v in _walk_values(doc):
        if k in ID_FIELDS and isinstance(v, str) and v and v not in marker:
            marker.add(v)
            seen.append(v)
    return seen


def _collect_texts(doc: Any) -> List[str]:
    seen: List[str] = []
    marker: Set[str] = set()
    for k, v in _walk_values(doc):
        if k in TEXT_FIELDS and isinstance(v, str) and v not in marker:
            marker.add(v)
            seen.append(v)
    return seen


def _rewrite(o: Any, id_map: Dict[str, str], text_map: Dict[str, str], fonts: Dict[str, str]) -> Any:
    """Копия структуры с подменами: значения-идентификаторы везде, тексты в своих полях."""
    if isinstance(o, dict):
        text_original = o.get("nodeText") if isinstance(o.get("nodeText"), str) else None
        out: Dict[str, Any] = {}
        for k, v in o.items():
            nk = _font(_id(k, id_map), fonts) if isinstance(k, str) else k
            if k in TEXT_FIELDS and isinstance(v, str) and v in text_map:
                nv: Any = text_map[v]
            elif k == "name" and isinstance(v, str) and text_original is not None \
                    and v == text_original and v in text_map:
                nv = text_map[v]            # слой-текст, названный по содержимому
            else:
                nv = _rewrite(v, id_map, text_map, fonts)
            out[nk] = nv
        return out
    if isinstance(o, list):
        return [_rewrite(v, id_map, text_map, fonts) for v in o]
    if isinstance(o, str):
        return _font(_id(o, id_map), fonts)
    return o


def _id(s: str, id_map: Dict[str, str]) -> str:
    return id_map.get(s, s)


def _font(s: str, fonts: Dict[str, str]) -> str:
    for old, new in fonts.items():
        if old in s:
            s = s.replace(old, new)
    return s


def anonymize(doc: Any, *, salt: str = DEFAULT_SALT, keep_texts: bool = False,
              fonts: Optional[Dict[str, str]] = None, prune: bool = True) -> Tuple[Any, dict]:
    """(обезличенный документ, отчёт). Исходный документ не меняется."""
    fonts = dict(fonts or {})
    body, _wrapped = D.unwrap(doc)
    work = copy.deepcopy(doc)
    dropped: List[str] = []
    if prune:
        work, dropped = D.prune_definitions(work)

    # guid файла не должны совпасть с подменами идентификаторов
    taken: Set[str] = {n["guid"] for n in D.all_nodes(D.unwrap(work)[0]) if isinstance(n.get("guid"), str)}
    ids = _collect_ids(work)
    taken.update(ids)
    id_map: Dict[str, str] = {}
    for v in ids:
        id_map[v] = fake_id(v, salt, taken)

    texts = _collect_texts(work)
    text_map: Dict[str, str] = {}
    kept_texts = 0
    if not keep_texts:
        k = 0
        for t in texts:
            if _LETTERS.search(t):
                k += 1
                text_map[t] = fake_text(t, k)
            else:
                kept_texts += 1             # числа и знаки — как есть

    new_doc = _rewrite(work, id_map, text_map, fonts)

    # самопроверка: ни одно исходное значение не должно остаться в выходе
    dumped = json.dumps(new_doc, ensure_ascii=False, separators=(",", ":"))
    leaks: List[str] = []
    for orig in list(id_map) + [t for t in text_map if text_map[t] != t]:
        needle = json.dumps(orig, ensure_ascii=False)[1:-1]
        if needle in dumped:
            leaks.append(orig if orig in text_map else f"<id:{orig[:4]}…>")
    for old in fonts:
        if old in dumped:
            leaks.append(f"<font:{old}>")

    nbody, _ = D.unwrap(new_doc)
    report = {
        "idsReplaced": len(id_map),
        "textsReplaced": len([t for t in text_map if text_map[t] != t]),
        "textsKeptAsIs": kept_texts if not keep_texts else len(texts),
        "definitionsKept": len(nbody.get(D.DEFS_KEY) or []),
        "definitionsDropped": len(dropped),
        "fontsRenamed": sorted(fonts),
        "leaks": leaks,
    }
    return new_doc, report


def dumps(doc: Any) -> str:
    """Детерминированная сериализация: минифицированный JSON, порядок ключей файла."""
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
