"""slim.py — «худение» Pixso DSL на входе (спека §7.3, INV-B3): библиотека и служебное — прочь.

Реальный экран ~12 МБ, из них 99,4 % — компоненты библиотеки файла, которых на экране нет, и
служебные данные редактора. Модели это читать нельзя (и незачем), поэтому `daiver_pixso_pull`
худеет экспорт и сохраняет рядом сырой как свидетельство — на случай исправления самого slim.

Что остаётся: дерево экрана, ДОСТИЖИМЫЕ из него определения компонентов (транзитивно: вложенные
инстансы, слоты подмены), только используемые стили и шрифты. Результат — тот же формат Pixso DSL,
поэтому нормализатор читает сырой и слимнутый документ одинаково.

Что выбрасывается: недостижимые определения; прототипные настройки оверлея и `interactions`
(одинаковы на каждом узле и к виду экрана не относятся); значения `null` (null ≡ «поля нет»);
стили, шрифты и specialNode, на которые никто не ссылается; неизвестные ключи верхнего уровня
(их список возвращается — сырой лежит рядом). Пустые списки, `false` и нули НЕ трогаются: это
значения. Отклонение от оригинала: определения остаются по guid, а не сливаются по имени —
экспорт ссылается на них по guid; совпавшие имена только считаются.

Диагностики (число случаев, а не дефекты страницы): MISSING_MAIN_COMPONENT — инстанс без
определения (пойдёт как разметка), MISSING_COMPONENT_BASE — у инстанса указана группа состояний, а её в экспорте нет,
MISSING_COMPONENT_DEFINITION — слот подмены ссылается на компонент, которого в экспорте нет,
COMPONENT_EXPANSION_CYCLE — определение ссылается само на себя через цепочку вложенных инстансов.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

from . import dsl as D

KNOWN_TOP = ("converterVersion", "dslVersion", "isContainFixed", "localStyleMap", "pixTreeDslNodes",
             "pixComponentTreeDslNodes", "sourceMapByUrl", "specialNode", "variableMap", "variableSetMap")
EDITOR_ONLY = ("overlayBackgroundAppearance", "overlayBackgroundInteraction", "overlayPositionType",
               "interactions")
STYLE_REF_SUFFIX = "StyleID"
EXAMPLES = 5


def dumps(o: Any) -> str:
    """Минифицированный JSON в порядке ключей источника (хеш — по этим байтам)."""
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def clean(src: Any) -> Any:
    """Глубокая копия без служебных полей и null-значений словарей (итеративно: деревья глубокие)."""
    if not isinstance(src, (dict, list)):
        return src
    dst_root: Any = {} if isinstance(src, dict) else []
    stack: List[Tuple[Any, Any]] = [(src, dst_root)]
    while stack:
        s, d = stack.pop()
        items = s.items() if isinstance(s, dict) else enumerate(s)
        for k, v in items:
            if isinstance(s, dict) and (v is None or k in EDITOR_ONLY):
                continue
            if isinstance(v, dict):
                val: Any = {}
                stack.append((v, val))
            elif isinstance(v, list):
                val = []
                stack.append((v, val))
            else:
                val = v
            if isinstance(d, dict):
                d[k] = val
            else:
                d.append(val)
    return dst_root


def _style_refs(nodes) -> Set[str]:
    refs: Set[str] = set()
    for n in nodes:
        for k, v in n.items():
            if k.startswith("inherit") and k.endswith(STYLE_REF_SUFFIX) and isinstance(v, str) and v:
                refs.add(v)
        for p in n.get("props") or []:
            if isinstance(p, dict):
                for k, v in p.items():
                    if k.startswith("inherit") and k.endswith(STYLE_REF_SUFFIX) and isinstance(v, str) and v:
                        refs.add(v)
    return refs


def _fonts_used(nodes, styles: Dict[str, Any]) -> Set[Tuple[str, str]]:
    used: Set[Tuple[str, str]] = set()
    for n in nodes:
        if isinstance(n.get("fontFamily"), str):
            used.add((n["fontFamily"], str(n.get("fontStyle") or "")))
        for p in n.get("props") or []:
            if isinstance(p, dict) and isinstance(p.get("fontFamily"), str):
                used.add((p["fontFamily"], str(p.get("fontStyle") or "")))
    for st in styles.values():
        t = st.get("text") if isinstance(st, dict) else None
        if isinstance(t, dict) and isinstance(t.get("fontFamily"), str):
            used.add((t["fontFamily"], str(t.get("fontStyle") or "")))
    return used


def diagnostics(body: dict, kept: List[str]) -> Dict[str, dict]:
    """Счётчики проблем входного экспорта по оставленной части (только ненулевые)."""
    owner = D.owner_index(body)
    pub = D.publish_index(body)
    tops = {t.get("guid"): t for t in body.get(D.DEFS_KEY) or [] if isinstance(t, dict)}
    out: Dict[str, dict] = {}

    def bump(code: str, example: Optional[dict]) -> None:
        e = out.setdefault(code, {"count": 0, "examples": []})
        e["count"] += 1
        if example and example not in e["examples"] and len(e["examples"]) < EXAMPLES:
            e["examples"].append(example)

    scopes: List[Any] = list(D.screen_nodes(body))
    for g in kept:
        scopes.extend(D.walk(tops[g]))
    for n in scopes:
        if n.get("type") == "INSTANCE":
            mc, sg = n.get("mainComponent"), n.get("mainStateGroup")
            if not isinstance(mc, str) or mc not in owner:
                bump("MISSING_MAIN_COMPONENT", {"id": n.get("guid"), "name": n.get("name")})
            elif isinstance(sg, str) and sg not in owner:
                # у инстанса обычного компонента mainStateGroup нет вовсе (mainComponent — само
                # определение); ключ есть, а группы в экспорте нет — вот это пропавшая база
                bump("MISSING_COMPONENT_BASE", {"id": n.get("guid"), "name": n.get("name")})
        for pm, field in (("propDefMap", "defaultValue"), ("propAssignMap", "value")):
            props = n.get(pm)
            if isinstance(props, dict):
                for p in props.values():
                    if isinstance(p, dict) and p.get("type") == "instance" and isinstance(p.get(field), str):
                        ref = p[field]
                        if ref not in owner and ref not in pub:
                            bump("MISSING_COMPONENT_DEFINITION", {"id": n.get("guid"), "ref": ref})
    # циклы в графе определений: A → B → … → A
    graph: Dict[str, Set[str]] = {g: set() for g in kept}
    for g in kept:
        for n in D.walk(tops[g]):
            for r in D.node_refs(n):
                t = owner.get(r) or pub.get(r)
                if t in graph:
                    graph[g].add(t)
    color: Dict[str, int] = {}
    for start in graph:
        if color.get(start):
            continue
        stack: List[Tuple[str, Any]] = [(start, iter(sorted(graph[start])))]
        color[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[node] = 2
                stack.pop()
            elif color.get(nxt) == 1:
                bump("COMPONENT_EXPANSION_CYCLE", {"definition": nxt, "via": node})
            elif not color.get(nxt):
                color[nxt] = 1
                stack.append((nxt, iter(sorted(graph[nxt]))))
    return out


def slim(doc: Any) -> Tuple[Any, dict]:
    """(слимнутый документ, отчёт {stats, diagnostics, droppedTopLevel, warnings}).

    Исходный документ не меняется. Бросает D.DslError, если это не Pixso DSL."""
    body, wrapped = D.unwrap(doc)
    kept = D.reachable_definitions(body)
    tops = {t.get("guid"): t for t in body.get(D.DEFS_KEY) or [] if isinstance(t, dict)}
    defs_in = list(tops)
    nodes_in = sum(1 for _ in D.all_nodes(body))
    names = [tops[g].get("name") for g in kept]
    dup_names = sorted({n for n in names if names.count(n) > 1 and isinstance(n, str)})

    screen = [clean(r) for r in body.get(D.SCREEN_KEY) or []]
    defs = [clean(tops[g]) for g in kept]
    new_body: Dict[str, Any] = {}
    node_iter = [n for r in screen for n in D.walk(r)] + [n for t in defs for n in D.walk(t)]
    all_guids = {n["guid"] for n in node_iter if isinstance(n.get("guid"), str)}

    styles_in = body.get(D.STYLES_KEY) if isinstance(body.get(D.STYLES_KEY), dict) else {}
    refs = _style_refs(node_iter)
    styles = {k: clean(v) for k, v in styles_in.items() if k in refs}
    fonts = _fonts_used(node_iter, styles)

    for key in KNOWN_TOP:
        if key not in body:
            continue
        if key == D.SCREEN_KEY:
            new_body[key] = screen
        elif key == D.DEFS_KEY:
            new_body[key] = defs
        elif key == D.STYLES_KEY:
            new_body[key] = styles
        elif key == "sourceMapByUrl" and isinstance(body[key], dict):
            new_body[key] = {k: clean(v) for k, v in body[key].items()
                             if not (isinstance(v, dict) and isinstance(v.get("fontKey"), dict)
                                     and (v["fontKey"].get("family"), v["fontKey"].get("style", "")) not in fonts)}
        elif key == "specialNode" and isinstance(body[key], list):
            new_body[key] = [clean(s) for s in body[key] if isinstance(s, dict) and s.get("id") in all_guids]
        else:
            new_body[key] = clean(body[key])
    dropped_top = sorted(k for k in body if k not in KNOWN_TOP)
    new_doc = {"dsl": new_body} if wrapped else new_body

    diag = diagnostics(body, kept)
    nodes_out = len(node_iter)
    warnings: List[str] = []
    msgs = {
        "MISSING_MAIN_COMPONENT": "инстансов без определения в экспорте — они пойдут как fallback-разметка, "
                                  "а тексты и вид этих мест экспорт не донёс (содержимое проверить нечем)",
        "MISSING_COMPONENT_BASE": "инстансов, чья группа состояний (mainStateGroup) в экспорте не найдена",
        "MISSING_COMPONENT_DEFINITION": "ссылок слотов подмены на компоненты, которых в экспорте нет — выгрузите "
                                        "их вместе с кадром, иначе подмена останется неизвестной",
        "COMPONENT_EXPANSION_CYCLE": "циклических ссылок определений друг на друга — раскрытие остановится на цикле",
    }
    for code, info in diag.items():
        warnings.append(f"{info['count']} {msgs[code]} ({code})")
    roots = len(body.get(D.SCREEN_KEY) or [])
    if roots > 1:
        warnings.append(f"в экспорте {roots} корня экрана — это {roots} отдельных кадра; тяните каждый "
                        f"отдельным pull'ом (frame=…), а не один экспорт на всё")
    if dropped_top:
        warnings.append("ключи верхнего уровня выброшены как незнакомые: " + ", ".join(dropped_top)
                        + " (сырой экспорт сохранён рядом)")
    unresolved_styles = sorted(refs - set(styles_in))
    stats = {
        "nodesIn": nodes_in, "nodesOut": nodes_out,
        "screenRoots": roots, "screenNodes": sum(1 for r in screen for _ in D.walk(r)),
        "definitionsIn": len(defs_in), "definitionsOut": len(defs),
        "definitionsDropped": len(defs_in) - len(defs),
        "stylesIn": len(styles_in), "stylesOut": len(styles), "styleRefsUnresolved": len(unresolved_styles),
        "fontsOut": len(new_body.get("sourceMapByUrl") or {}),
        "duplicateDefinitionNames": dup_names,
    }
    return new_doc, {"stats": stats, "diagnostics": diag, "droppedTopLevel": dropped_top, "warnings": warnings}
