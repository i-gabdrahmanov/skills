"""source_nodes.py — daiver_source_nodes: декларации раскладки названных узлов из СЫРОГО экспорта.

Сырой экспорт модель не читает никогда (INV-B3, F1: сотни КБ), но иногда нужен ответ на узкий
вопрос — что дизайнер ОБЪЯВИЛ про узел. Типичный случай — вывод раскладки (§11.4): у узла, чьи дети
остались абсолютными, в экспорте может лежать `horizontalConstraint: SCALE` («следить за шириной
родителя»); декларация сильнее впечатления с картинки и не требует вопроса человеку.

Инструмент читает raw-файл с диска и возвращает ТОЛЬКО эти поля для названных узлов (не больше
`MAX_PROBE_NODES`); файл целиком и его части — никогда.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .pixso import dsl as D

MAX_PROBE_NODES = 40
GEOMETRY = ("left", "top", "width", "height")
DECLARED = ("horizontalConstraint", "verticalConstraint", "textAutoResize", "autoLayoutByParent",
            "overlayPositionType", "layerType")


class ProbeError(ValueError):
    pass


def _declared(node: dict) -> dict:
    out: Dict[str, Any] = {"name": node.get("name"), "type": node.get("type")}
    geo = {k: node[k] for k in GEOMETRY if isinstance(node.get(k), (int, float))}
    if geo:
        out["geometry"] = geo
    for k in DECLARED:
        if node.get(k) is not None:
            out[k] = node[k]
    al = node.get("autoLayout")
    if isinstance(al, dict):
        trimmed = {k: v for k, v in al.items() if v is not None}
        if trimmed:
            out["autoLayout"] = trimmed
    return out


def probe(raw_path: Path, node_ids: List[str]) -> dict:
    """{found: {id: декларации}, missing: [id], skipped: {id: причина}}."""
    if not isinstance(node_ids, list) or not node_ids or not all(isinstance(i, str) and i for i in node_ids):
        raise ProbeError("nodeIds: непустой список id узлов (строки)")
    if len(node_ids) > MAX_PROBE_NODES:
        raise ProbeError(f"за один вызов — не больше {MAX_PROBE_NODES} узлов, получено {len(node_ids)}")
    p = Path(raw_path)
    if not p.is_file():
        raise ProbeError(f"сырого экспорта нет: {p.name} (его сохраняет daiver_pixso_pull рядом с требованием)")
    try:
        doc = D.load(p)
        body, _ = D.unwrap(doc)
    except D.DslError as e:
        raise ProbeError(str(e))
    wanted = set(node_ids)
    index: Dict[str, dict] = {}
    for n in D.all_nodes(body):
        g = n.get("guid")
        if g in wanted and g not in index:
            index[g] = n
            if len(index) == len(wanted):
                break
    found: Dict[str, dict] = {}
    missing: List[str] = []
    skipped: Dict[str, str] = {}
    for i in dict.fromkeys(node_ids):
        if i.startswith("inferred:"):
            skipped[i] = "синтетическая группа, выведенная движком: в экспорте её нет — спросите про её детей"
        elif i in index:
            found[i] = _declared(index[i])
        else:
            missing.append(i)
    return {"found": found, "missing": missing, "skipped": skipped}
