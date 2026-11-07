"""dsl.py — общие хелперы над Pixso DSL (формат образца docs/daiver/dsl.json, dslVersion 2.1.15).

Устройство экспорта (проверено по образцу «Modal»):
  • `{"dsl": {converterVersion, dslVersion, localStyleMap, pixTreeDslNodes, pixComponentTreeDslNodes,
    sourceMapByUrl, specialNode, variableMap, variableSetMap}}` — обёртка `dsl` у экспорта Pixso MCP;
  • `pixTreeDslNodes` — корни экрана (дерево, дети в `childNode`); у модалки это фрейм и три
    инстанса БЕЗ детей;
  • `pixComponentTreeDslNodes` — верхнеуровневые ОПРЕДЕЛЕНИЯ компонентов (SYMBOL), на которые
    ссылаются инстансы: вся анатомия и все тексты живут здесь;
  • инстанс → определение: `mainComponent` (guid варианта) и `mainStateGroup` (guid группы
    состояний) — оба указывают на узлы внутри верхнеуровневого определения;
  • свойство-слот типа `instance`: `propDefMap[*].defaultValue` — это publishID компонента по
    умолчанию; `propAssignMap[*].value` — guid подставленного компонента (его определения в
    экспорте может не быть).

Модуль без побочных эффектов: его используют и slim (задача 1.2), и анонимайзер фикстур.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

SCREEN_KEY = "pixTreeDslNodes"
DEFS_KEY = "pixComponentTreeDslNodes"
STYLES_KEY = "localStyleMap"
CHILDREN_KEY = "childNode"


class DslError(ValueError):
    """Файл не похож на Pixso DSL или повреждён."""


def looks_like_dsl(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    screen = body.get(SCREEN_KEY)
    if not isinstance(screen, list) or not screen:
        return False
    if not all(isinstance(n, dict) and "guid" in n and "type" in n for n in screen):
        return False
    return isinstance(body.get(DEFS_KEY, []), list)


def unwrap(doc: Any) -> Tuple[dict, bool]:
    """(тело DSL, была ли обёртка {"dsl": …}). DslError — это не Pixso DSL."""
    wrapped = isinstance(doc, dict) and isinstance(doc.get("dsl"), dict)
    body = doc["dsl"] if wrapped else doc
    if not looks_like_dsl(body):
        raise DslError("это не Pixso DSL: нет непустого списка pixTreeDslNodes с узлами {guid, type}")
    return body, wrapped


def load(path: Path) -> Any:
    """Документ как есть (с обёрткой, если она была). Не Pixso / битый JSON → DslError."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise DslError(f"не читается {path}: {e}")
    except json.JSONDecodeError as e:
        raise DslError(f"не JSON: {e}")
    unwrap(doc)
    return doc


def walk(node: dict) -> Iterator[dict]:
    """Узел и все потомки в порядке документа (итеративно: глубокие деревья не рвут стек)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        kids = n.get(CHILDREN_KEY)
        if isinstance(kids, list):
            stack.extend(reversed([k for k in kids if isinstance(k, dict)]))


def screen_nodes(body: dict) -> Iterator[dict]:
    for root in body.get(SCREEN_KEY) or []:
        if isinstance(root, dict):
            yield from walk(root)


def definition_nodes(body: dict) -> Iterator[dict]:
    for top in body.get(DEFS_KEY) or []:
        if isinstance(top, dict):
            yield from walk(top)


def all_nodes(body: dict) -> Iterator[dict]:
    yield from screen_nodes(body)
    yield from definition_nodes(body)


def owner_index(body: dict) -> Dict[str, str]:
    """guid любого узла ВНУТРИ определений → guid верхнеуровневого определения."""
    out: Dict[str, str] = {}
    for top in body.get(DEFS_KEY) or []:
        if not isinstance(top, dict):
            continue
        for n in walk(top):
            g = n.get("guid")
            if isinstance(g, str):
                out[g] = top.get("guid")
    return out


def publish_index(body: dict) -> Dict[str, str]:
    """publishID → guid верхнеуровневого определения, где лежит узел с этим publishID."""
    out: Dict[str, str] = {}
    for top in body.get(DEFS_KEY) or []:
        if not isinstance(top, dict):
            continue
        for n in walk(top):
            p = n.get("publishID")
            if isinstance(p, str) and p:
                out.setdefault(p, top.get("guid"))
    return out


def node_refs(node: dict) -> List[str]:
    """Значения, которыми узел ссылается на другие определения (guid или publishID).

    Инстанс — mainComponent/mainStateGroup; свойство-слот типа `instance` — defaultValue
    (publishID) и value подстановки (guid). Все ссылки разрешаются через owner/publish индексы;
    неразрешимые (определения нет в экспорте) просто не дают достижимости."""
    out: List[str] = []
    for k in ("mainComponent", "mainStateGroup"):
        v = node.get(k)
        if isinstance(v, str) and v:
            out.append(v)
    for pm, field in (("propDefMap", "defaultValue"), ("propAssignMap", "value")):
        props = node.get(pm)
        if isinstance(props, dict):
            for p in props.values():
                if isinstance(p, dict) and p.get("type") == "instance" and isinstance(p.get(field), str):
                    out.append(p[field])
    return out


def reachable_definitions(body: dict) -> List[str]:
    """guid верхнеуровневых определений, достижимых из экрана (транзитивно), в порядке файла."""
    owner = owner_index(body)
    pub = publish_index(body)
    tops = {t.get("guid"): t for t in body.get(DEFS_KEY) or [] if isinstance(t, dict)}

    def resolve(ref: str) -> Optional[str]:
        return owner.get(ref) or pub.get(ref)

    seen: set = set()
    stack: List[str] = []
    for n in screen_nodes(body):
        for r in node_refs(n):
            t = resolve(r)
            if t:
                stack.append(t)
    while stack:
        g = stack.pop()
        if g in seen or g not in tops:
            continue
        seen.add(g)
        for n in walk(tops[g]):
            for r in node_refs(n):
                t = resolve(r)
                if t and t not in seen:
                    stack.append(t)
    return [g for g in tops if g in seen]


def prune_definitions(doc: Any) -> Tuple[Any, List[str]]:
    """Копия документа без недостижимых определений; список выброшенных guid."""
    body, wrapped = unwrap(doc)
    keep = set(reachable_definitions(body))
    dropped = [t.get("guid") for t in body.get(DEFS_KEY) or []
               if isinstance(t, dict) and t.get("guid") not in keep]
    new_body = dict(body)
    new_body[DEFS_KEY] = [t for t in body.get(DEFS_KEY) or []
                          if isinstance(t, dict) and t.get("guid") in keep]
    return ({"dsl": new_body} if wrapped else new_body), dropped
