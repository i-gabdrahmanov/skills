"""ids.py — стабильные id находок и дефектов (спека §14.4.6 «ID-инварианты»; задача 4.6; INV-C28, QG-X3).

id = sha1(код | узел макета | узел страницы | свойство)[:12] — НИКОГДА не от значений: поменялось значение (было 8, стало 12) — id тот
же, «этот дефект всё ещё открыт»; другое свойство — другой id. Один адрес в разных гейтах — один id: у MISSING узел страницы в хеш не
входит (его нет по определению), поэтому DOM-гейт и код-гейт называют один и тот же пробел одним id (измеренное выигрывает).

Дефект складной слоя (geometry/typography/color/shape) — «это свойство у ЭТОГО ВИДА элемента»: его id строится от подписи вида
(`signature`: вид узла + имя компонента/слоя), а не от узла-представителя. Иначе частичный фикс (12 строк → 5) терял бы id, если исправлена
именно первая из строк, и жизненный цикл видел бы «новый» вместо «остался».
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

FOLD_LAYERS = ("geometry", "typography", "color", "shape")


def defect_id(code: str, design: Optional[str], impl: Optional[str], prop: Optional[str]) -> str:
    """Стабильный id находки/дефекта на узлах; у MISSING implId не входит."""
    parts = [code, design or "", "" if code == "MISSING" else (impl or ""), prop or ""]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def fold_id(code: str, signature: str, prop: Optional[str]) -> str:
    """Id складного дефекта: «свойство у вида элемента»."""
    return hashlib.sha1("|".join([code, "~" + signature, prop or ""]).encode("utf-8")).hexdigest()[:12]


def signature(node: Optional[Dict[str, Any]]) -> str:
    """Вид узла макета: kind + имя компонента (инстанс) или слоя; узла нет — пусто."""
    if not node:
        return ""
    comp = node.get("component") or {}
    return f"{node.get('kind', '?')}:{comp.get('figmaName') or node.get('name') or ''}"


def finding_id(f: Dict[str, Any]) -> str:
    """Id сырой находки: узлы макета/страницы; у DS-находок без узлов — файл вместо узла страницы (строка в id не входит: она едет)."""
    return defect_id(f["code"], f.get("design"), f.get("impl") or f.get("file"), f.get("prop"))
