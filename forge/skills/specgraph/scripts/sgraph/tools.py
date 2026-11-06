"""tools.py — реестр инструментов движка spec-graph (`graph_*`) для MCP-сервера и CLI.

Каждый инструмент: функция + JSON-схема (для tools/list) + РУЧНАЯ проверка аргументов —
неизвестный или неверный аргумент называется отказом, а не молча игнорируется. Нарушения
собираются в одно сообщение: слабой модели легче починить разом.

Сейчас: graph_status, graph_init. Остальные появляются по задачам docs/specgraph/; владельцы
продуктивных инструментов — `phase_tools` ветки specgraph в hooks/_branches.py.

Каждый вызов считает `metrics` (задача 4.1): исход, коды отказа, id единиц работы (аргументы,
названные в `x-ref-args` схемы), длительность, размер ответа. Аргументы и тексты не пишутся.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import config as C
from . import metrics as M
from . import status as ST


class ToolError(Exception):
    """Отказ инструмента (неверные аргументы, не прошла проверка) — ответ isError, сервер работает.

    `codes` — машинные коды отказа (`E-…`) для счётчиков и диагностики: по ним видно, где модель
    буксует, без текста сообщения."""

    def __init__(self, message: str, codes: Optional[List[str]] = None):
        super().__init__(message)
        self.codes = list(codes or [])


_TYPES = {"string": str, "object": dict, "boolean": bool, "array": list}


def _check_args(args: Dict[str, Any], schema: Dict[str, Any]) -> None:
    errors: List[str] = []
    codes: List[str] = []
    props = schema.get("properties", {})
    for k in args:
        if k not in props:
            errors.append(f"неизвестный аргумент «{k}» (допустимы: {', '.join(sorted(props)) or 'нет'})")
            codes.append("E-arg-unknown")
    for k in schema.get("required", []):
        if k not in args:
            errors.append(f"нет обязательного аргумента «{k}»")
            codes.append("E-arg-missing")
    for k, v in args.items():
        spec = props.get(k) or {}
        t = _TYPES.get(spec.get("type"))
        if t is not None and (not isinstance(v, t) or (t is not bool and isinstance(v, bool))):
            errors.append(f"«{k}»: ожидается {spec['type']}")
            codes.append("E-arg-type")
        elif spec.get("type") == "array" and (spec.get("items") or {}).get("type") == "string" \
                and not all(isinstance(x, str) for x in v):
            errors.append(f"«{k}»: ожидается список строк")
            codes.append("E-arg-type")
    if errors:
        raise ToolError("; ".join(errors), codes)


def _status(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    return ST.status(root)


def _init(root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    res = C.init(root, args)
    if res.get("status") == "invalid":
        raise ToolError("конфиг не записан: " + "; ".join(res["errors"]), ["E-config-invalid"])
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


def _refs(args: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """id единиц работы из аргументов, которые схема объявила ссылками (`x-ref-args`)."""
    return [args[k] for k in schema.get("x-ref-args", ()) if isinstance(args.get(k), str)]


def call(name: str, root: Path, args: Dict[str, Any]) -> Dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(name)
    args = args or {}
    root = Path(root)
    started = time.monotonic()
    refs = _refs(args, tool["schema"]) if isinstance(args, dict) else []
    elapsed = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
    try:
        _check_args(args, tool["schema"])
        fn: Callable[[Path, Dict[str, Any]], Dict[str, Any]] = tool["fn"]
        res = fn(root, args)
    except ToolError as e:
        M.record_call(root, name, "rejected", codes=e.codes, refs=refs, duration_ms=elapsed())
        raise
    except Exception as e:  # noqa: BLE001 — сбой движка считается и пробрасывается дальше серверу
        M.record_call(root, name, "error", refs=refs, duration_ms=elapsed(), error_type=type(e).__name__)
        raise
    size = len(json.dumps(res, ensure_ascii=False, default=str))
    M.record_call(root, name, "ok", refs=refs, duration_ms=elapsed(), size=size)
    return res
