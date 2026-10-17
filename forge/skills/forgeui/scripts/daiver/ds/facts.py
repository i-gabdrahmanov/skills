"""facts.py — общая схема ФАКТОВ сканера ДС (JSON-совместимая) и кодирование значений.

Фронт-энды (Python-лексер `csf/decl/mdx`, необязательный node-хелпер `ds-scan.mjs`) отдают одну и ту же
схему; интерпретация (какой проп какого вида, что победило при слиянии) — одна, в `merge.py`. Поэтому два
движка расходятся только там, где расходится разбор, а не там, где документы собираются.

Факты файла (`FileFacts`)
  file      путь от корня репо (прямые слэши)
  kind      stories | mdx | types | source
  imports   {локальное имя: {"from": спецификатор, "named": имя|None, "default": bool, "ns": bool}}
  groups    [{"meta": Meta, "stories": [Story]}] — группа = один default export CSF / один `storiesOf` / один
            `<Meta>` MDX
  prose     текст MDX (без импортов и JSX-блоков) | None
  docOf     {"of": имя из imports|None, "title": str|None, "component": str|None} у MDX-документации
  types     {имя: TypeDecl}      — interface / type / enum файла
  components [Binding]           — экспортированные компоненты (имя + тип пропсов + JSDoc)
  warnings  [str]

Meta     {title, component (имя|None), args, argTypes, description, includeStories, excludeStories}
Story    {export, name, format (csf3|csf2|csf1|storiesOf|mdx), args, argTypes, jsx, usage, component,
          description}
          usage = [[тег, {проп: [скаляры]}]] — литеральные значения из JSX истории
TypeDecl {kind: interface|alias|enum, extends: [TypeAst], members: [Member], type: TypeAst|None, enum: {…},
          open: bool, doc: Doc|None}
Member   {name, optional, type: TypeAst|None, method: bool, doc: Doc|None}
Binding  {name, props: TypeAst|None, doc: Doc|None, module: str|None}
Doc      {text, deprecated: str|None, default: str|None}   (deprecated "" = помечен без пояснения)
TypeAst  {"k": lit|kw|ref|union|inter|obj|fn|arr|tuple|tpl|other, …} — упрощённое представление типа

Значения (`args`, `argTypes`) — JSON с тремя маркерами вместо Python-объектов: {"$":"opaque"} (значение есть,
статически не известно), {"$":"ref","name":…} (неразрешённое имя), {"$":"jsx","text":…} (JSX-элемент).
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Optional

from . import value as V


def enc(v: Any) -> Any:
    """Python-значение вычислителя → JSON-совместимое (маркеры вместо OPAQUE/Ref/Jsx)."""
    if v is V.OPAQUE:
        return {"$": "opaque"}
    if isinstance(v, V.Ref):
        return {"$": "ref", "name": v.name}
    if isinstance(v, V.Jsx):
        return {"$": "jsx", "text": v.text}
    if isinstance(v, dict):
        return {str(k): enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [enc(x) for x in v]
    if isinstance(v, float) and not math.isfinite(v):
        return {"$": "opaque"}
    return v


def _marker(v: Any, kind: str) -> bool:
    return isinstance(v, dict) and v.get("$") == kind


def is_opaque(v: Any) -> bool:
    return _marker(v, "opaque")


def is_ref(v: Any) -> bool:
    return _marker(v, "ref")


def is_jsx(v: Any) -> bool:
    return _marker(v, "jsx")


def is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool))


def has_opaque(v: Any) -> bool:
    """В закодированном значении есть невычисленные места (opaque/ref)."""
    if is_opaque(v) or is_ref(v):
        return True
    if isinstance(v, list):
        return any(has_opaque(x) for x in v)
    if isinstance(v, dict) and not is_jsx(v):
        return any(has_opaque(x) for x in v.values())
    return False


def obj_get(v: Any, *path: str) -> Any:
    """Вложенное поле закодированного объекта или None."""
    for p in path:
        if not isinstance(v, dict) or p not in v:
            return None
        v = v[p]
    return v


def story_name_from_export(export: str) -> str:
    """Имя истории из имени экспорта, как в Storybook (startCase): WithIcon → «With Icon»,
    icon_only2 → «Icon Only 2»."""
    s = re.sub(r"[_\-]+", " ", export)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", s)
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def empty_facts(path: str, kind: str) -> Dict[str, Any]:
    return {"file": path, "kind": kind, "imports": {}, "groups": [], "prose": None, "docOf": None,
            "types": {}, "components": [], "warnings": []}


def doc_fact(doc: Any) -> Optional[Dict[str, Any]]:
    """Doc лексера → факт {text, deprecated, default} или None, если сказать нечего."""
    if doc is None:
        return None
    dep = doc.tag("deprecated")
    default = doc.tag("default")
    if default is None:
        default = doc.tag("defaultValue")
    if not doc.text and dep is None and default is None:
        return None
    return {"text": doc.text, "deprecated": dep, "default": default}
