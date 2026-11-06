"""tools.py — реестр инструментов движка spec-graph (`graph_*`) для MCP-сервера и CLI.

Каждый инструмент: функция + JSON-схема (для tools/list) + РУЧНАЯ проверка аргументов —
неизвестный или неверный аргумент называется отказом, а не молча игнорируется. Нарушения
собираются в одно сообщение: слабой модели легче починить разом.

Сейчас: graph_status, graph_init. Остальные появляются по задачам docs/specgraph/; владельцы
продуктивных инструментов — `phase_tools` ветки specgraph в hooks/_branches.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

from . import config as C
from . import status as ST


class ToolError(Exception):
    """Неверные аргументы инструмента — ответ isError, сервер продолжает работу."""


_TYPES = {"string": str, "object": dict, "boolean": bool, "array": list}


def _check_args(args: Dict[str, Any], schema: Dict[str, Any]) -> None:
    errors: List[str] = []
    props = schema.get("properties", {})
    for k in args:
        if k not in props:
            errors.append(f"неизвестный аргумент «{k}» (допустимы: {', '.join(sorted(props)) or 'нет'})")
    for k in schema.get("required", []):
        if k not in args:
            errors.append(f"нет обязательного аргумента «{k}»")
    for k, v in args.items():
        spec = props.get(k) or {}
        t = _TYPES.get(spec.get("type"))
        if t is not None and (not isinstance(v, t) or (t is not bool and isinstance(v, bool))):
            errors.append(f"«{k}»: ожидается {spec['type']}")
        elif spec.get("type") == "array" and (spec.get("items") or {}).get("type") == "string" \
                and not all(isinstance(x, str) for x in v):
            errors.append(f"«{k}»: ожидается список строк")
    if errors:
        raise ToolError("; ".join(errors))


def _status(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return ST.status(root)


def _init(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    res = C.init(root, args)
    if res.get("status") == "invalid":
        raise ToolError("конфиг не записан: " + "; ".join(res["errors"]))
    return res


TOOLS: Dict[str, Dict[str, Any]] = {
    "graph_status": {
        "fn": _status,
        "description": "Состояние spec-graph в проекте: инициализирован ли, а если нет — вопросы первого "
                       "запуска с кандидатами. Первый ход команды /graph.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "graph_init": {
        "fn": _init,
        "description": "Записать .spec-graph/config.json по ОТВЕТАМ пользователя (не по догадке). Существующий "
                       "конфиг не перезаписывается.",
        "schema": {"type": "object",
                   "properties": {
                       "sources": {"type": "array", "items": {"type": "string"},
                                   "description": "корни исходников спеки (каталоги или файлы), от корня репо"},
                       "exclude": {"type": "array", "items": {"type": "string"},
                                   "description": "glob-шаблоны исключений (черновики, архивы)"},
                       "vault": {"type": "string", "description": f"куда писать граф, по умолчанию {C.DEFAULT_VAULT}"},
                       "services": {"type": "object",
                                    "description": "каталог или файл → имя сервиса (если не совпадает с именем каталога)"},
                   },
                   "required": ["sources"], "additionalProperties": False},
    },
}


def call(name: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(name)
    _check_args(args or {}, tool["schema"])
    fn: Callable[[Path, Dict[str, Any]], Dict[str, Any]] = tool["fn"]
    return fn(Path(root), args or {})
