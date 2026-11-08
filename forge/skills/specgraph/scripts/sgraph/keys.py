"""keys.py — машинные ключи склейки узлов (задачи 1.2, 2.1).

Межсервисные связи склеиваются по ключу, а не по имени файла (урок прототипа, инвариант 7):
    http:<METHOD> <path>  — метод в верхнем регистре, путь без хоста и завершающего «/», `{любое}` → `{}`,
                            версия в пути сохраняется;
    kafka:<name>          — имя топика как есть;
    task:<КОД>            — код типа задачи (регистр как в спеке: `ABC_TASK`, `abc_task`);
    table:<имя>           — таблица БД, имя в нижнем регистре (из SQL текста или раздела «Таблица `x`»).
Здесь — нормализация и имя заметки эндпойнта; резолвер упоминаний и срезаемые префиксы — задача 2.1.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_HTTP_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+((?:https?://[^/\s`]+)?/[^\s`)\]»,;]*)")


def norm_path(path: str) -> str:
    p = re.sub(r"^https?://[^/]+", "", path.strip())
    p = p.split("?", 1)[0].split("#", 1)[0]
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


def http_key(method: str, path: str) -> str:
    return f"http:{method.upper()} " + re.sub(r"\{[^}]*\}", "{}", norm_path(path))


def find_http(text: str) -> list:
    """Все упоминания `METHOD /path` в тексте: [(METHOD, путь как написан)]."""
    return [(m.group(1), m.group(2).rstrip(".:")) for m in _HTTP_RE.finditer(text)]


def endpoint_name(service: str, method: str, path: str) -> str:
    """Имя заметки эндпойнта: `<svc> <METHOD> <путь-через-дефисы>` (переменные пути — как в спеке)."""
    return f"{service} {method.upper()} " + norm_path(path).strip("/").replace("/", "-")


def topic_key(name: str) -> str:
    return f"kafka:{name}"


def task_key(code: str) -> str:
    return f"task:{code}"


def table_key(name: str) -> str:
    return f"table:{name.lower()}"


def split_key(key: str) -> Tuple[str, Optional[str]]:
    scheme, _, rest = key.partition(":")
    return scheme, rest or None
