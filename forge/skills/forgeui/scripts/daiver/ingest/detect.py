"""detect.py — формат макета определяет КОД по содержимому файла; агент адаптер не выбирает (INV-B2).

Pixso распознаётся надёжно (по образцу). Figma-форматы — по признакам, пока без образцов (задача
X.2): их адаптеров нет, `daiver_normalize` на них отвечает названным отказом, а не «лучшим усилием».
Неизвестное — отказ с сообщением: молча разбирать то, что не распознано, значит выдумывать макет.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Tuple

from ..pixso import dsl as D

FIGMA_NODE_TYPES = ("DOCUMENT", "CANVAS", "FRAME", "GROUP", "COMPONENT", "COMPONENT_SET", "INSTANCE",
                    "RECTANGLE", "TEXT", "VECTOR", "SECTION")


def detect_obj(doc: Any) -> Tuple[Optional[str], str]:
    """(формат | None, причина)."""
    try:
        D.unwrap(doc)
        return "pixso", "pixTreeDslNodes с узлами {guid, type}"
    except D.DslError:
        pass
    if isinstance(doc, dict):
        inner = doc.get("document") if isinstance(doc.get("document"), dict) else doc
        if inner.get("type") in FIGMA_NODE_TYPES and isinstance(inner.get("children"), list):
            return "figma-json-a", "дерево узлов Figma (type + children)"
    return None, "ни Pixso DSL (pixTreeDslNodes), ни дерево узлов Figma, ни HTML-экспорт"


def detect_file(path: Path) -> Tuple[Optional[str], str, Any]:
    """(формат | None, причина, разобранный документ | текст HTML | None)."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return None, f"файл не читается: {e}", None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "файл не UTF-8", None
    head = text.lstrip()[:1]
    if head in ("{", "["):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            return None, f"не JSON: {e}", None
        fmt, why = detect_obj(doc)
        return fmt, why, doc
    if head == "<":
        return "figma-html", "HTML-экспорт", text
    return None, "не JSON и не HTML", None
