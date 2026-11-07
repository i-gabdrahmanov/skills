"""names.py — имена слоёв макета: автогенерированные имена и варианты в имени (спека §7.2).

Ingest только ПОМЕЧАЕТ автоимена («Frame 123», «Group 5», «TEXT», «Rectangle») — решать, осмысленно
ли имя, будет resolve (кандидаты резолва — инстансы и фреймы с осмысленными именами), поэтому
в каноне флага нет: тот же предикат зовут outline и resolve.

Варианты в имени инстанса — «State=hover, Size=L»: пары `ключ=значение` через запятую; ключи и
значения могут содержать пробелы, после запятой пробел необязателен («Size=Large,Type=Primary»).
Строка, где хоть одна часть не пара, вариантами не считается.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

_AUTO = re.compile(
    r"^(frame|group|rectangle|ellipse|vector|line|text|component|instance|section|star|polygon|image|slice|"
    r"boolean(?: operation)?|"
    r"фрейм|группа|прямоугольник|эллипс|вектор|линия|текст|компонент|экземпляр)\s*\d*$",
    re.IGNORECASE)
_SPLIT = re.compile(r",\s*(?=[^=,]+=)")


def is_auto_name(name: object) -> bool:
    """Имя, которое дизайнер не давал: «Frame 123», «Group 5», «TEXT», «Rectangle», «Фрейм 7»."""
    return isinstance(name, str) and bool(_AUTO.match(name.strip()))


def parse_variants(name: object) -> Optional[Dict[str, str]]:
    """«State=hover, Size=L» → {"State": "hover", "Size": "L"}; не варианты → None."""
    if not isinstance(name, str) or "=" not in name:
        return None
    out: Dict[str, str] = {}
    for part in _SPLIT.split(name.strip()):
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            return None
        out[key] = value
    return out or None
