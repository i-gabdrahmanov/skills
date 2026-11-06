"""resolve.py — индекс узлов vault и резолвер упоминаний (задача 2.1).

Межсервисные связи склеиваются по машинному ключу, а не по имени файла (инвариант 7 прототипа). Спека
пишет «вызывает POST /api/v1/upz/tasks УПЗ-адаптера», а узел называется `upz-adapter POST api-v1-upz-tasks`.
Резолвер превращает упоминание в узел — или честно говорит `ambiguous` (кандидаты) / `ghost` (нет такого
узла, с подсказкой `path_typo`, если путь похож с точностью ≥ 0.93). Молча не выбирает никогда.

Индекс строится из frontmatter заметок vault (kind, key, aliases, role, сервис из belongs_to), он же — основа
обхода (3.4). Срезаемые префиксы путей (gateway, `/api`) — один раз на vault: `http.strip_prefixes` конфига.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import fm as F
from . import keys as K
from . import ontology as O

_TOPIC_HINT = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9_-]+){2,}$")
_TYPO_RATIO = 0.93


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("ё", "е").replace("Ё", "Е")).strip().lower()


def vault_index(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Узлы vault по frontmatter: {name, kind, role, subkind, key, aliases, service, path}."""
    vault = Path(root) / cfg["vault"]
    out = []
    for p in sorted(vault.rglob("*.md")):
        rel = p.relative_to(vault)
        if rel.parts[0] in ("_graph", "_attachments"):
            continue
        try:
            data, _ = F.read(p.read_bytes().decode("utf-8"))
        except (F.FMError, UnicodeDecodeError):
            continue
        if not data.get("kind"):
            continue
        owner = [F.link_target(x) for x in (data.get("belongs_to") or [])]
        out.append({"name": p.stem, "kind": data["kind"], "role": data.get("role"), "subkind": data.get("subkind"),
                    "key": data.get("key"), "aliases": [a for a in (data.get("aliases") or []) if isinstance(a, str)],
                    "service": owner[0] if owner and owner[0] else None, "path": rel.as_posix()})
    return out


def _http_keys(method: str, path: str, prefixes: List[str]) -> List[str]:
    keys = [K.http_key(method, path)]
    p = K.norm_path(path)
    for pre in sorted(prefixes, key=len, reverse=True):
        pre = "/" + pre.strip("/")
        if p.startswith(pre + "/"):
            keys.append(K.http_key(method, p[len(pre):]))
    return keys


def _node_http_keys(node: Dict[str, Any], prefixes: List[str]) -> set:
    scheme, rest = K.split_key(node.get("key") or "")
    if scheme != "http" or not rest:
        return set()
    method, _, path = rest.partition(" ")
    return set(_http_keys(method, path, prefixes))


def resolve(index: List[Dict[str, Any]], mention: str, cfg: Optional[Dict[str, Any]] = None,
            service: Optional[str] = None, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
    """Упоминание → {status: node|ambiguous|ghost, node?, candidates?, hint?}."""
    cfg = cfg or {}
    prefixes = ((cfg.get("http") or {}).get("strip_prefixes")) or []
    m = mention.strip().strip("`").strip()
    pool = [n for n in index if not kinds or n["kind"] in kinds]
    hits: List[Dict[str, Any]] = []
    http = K.find_http(m)
    if http:
        method, path = http[0]
        wanted = set(_http_keys(method, path, prefixes))
        hits = [n for n in pool if _node_http_keys(n, prefixes) & wanted]
        if not hits:
            target = K.http_key(method, path)
            close = [(difflib.SequenceMatcher(None, target, n["key"]).ratio(), n) for n in pool
                     if (n.get("key") or "").startswith(f"http:{method.upper()} ")]
            close = sorted([c for c in close if c[0] >= _TYPO_RATIO], key=lambda c: -c[0])
            if close:
                return {"status": "ghost", "hint": "path_typo", "candidates": [c[1]["name"] for c in close[:3]]}
            return {"status": "ghost"}
    elif m.startswith(("kafka:", "task:", "http:")):
        hits = [n for n in pool if n.get("key") == m]
    elif _TOPIC_HINT.match(m):
        hits = [n for n in pool if n.get("key") == K.topic_key(m) or n["name"] == m]
    else:
        nm = norm(m)
        hits = [n for n in pool if norm(n["name"]) == nm or any(norm(a) == nm for a in n["aliases"])]
        if not hits:
            hits = [n for n in pool if (n.get("key") or "") == K.task_key(m)]
    if len(hits) > 1 and service:
        own = [n for n in hits if n.get("service") == service]
        hits = own or hits
    if len(hits) == 1:
        return {"status": "node", "node": hits[0]["name"], "kind": hits[0]["kind"]}
    if hits:
        return {"status": "ambiguous", "candidates": sorted(n["name"] for n in hits)}
    return {"status": "ghost"}
