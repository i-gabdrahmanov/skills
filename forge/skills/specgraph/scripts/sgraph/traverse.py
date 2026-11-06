"""traverse.py — обход графа агентом (задача 3.4, ТЗ §8.2, §10, D7, D14).

Второй читатель графа — агент. Читая YAML заметок файлами, слабая модель тратит раунды и путается в
направлениях рёбер (бенч 2026-10-27: стоимость ответа — число раундов модели, а не скорость поиска).
Поэтому типовой вопрос — один вызов с короткой выдачей и жёсткими потолками; `[[ссылки]]` в ответе ведут
человека в Obsidian.

Индекс — из frontmatter vault (источник истины — заметки, D6): узлы и ПРЯМЫЕ рёбра (обратные ключи
выводятся, симметричное ребро — в обе стороны). Кэш `.spec-graph/index.json`, инвалидация по mtime и
размеру файлов. Шаг пути подписан ключом frontmatter: прямое ребро — его имя (`closes_task`), шаг против
ребра — обратный ключ (`closed_by`). Так путь читается так же, как свойства заметки в Obsidian.

Что затрагивает изменение (`impact`) — по полю `impact` рёбер онтологии; виды и рёбра в коде не названы.
Связи сервис ↔ сервис — производный вид, а не рёбра (D7).
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import fm as F
from . import ontology as O

INDEX_REL = ".spec-graph/index.json"
_SKIP = ("_graph", "_attachments")
LIMIT_MAX, DEPTH_MAX, PATH_DEPTH_MAX, PATHS_MAX, CHARS_MAX = 50, 3, 6, 3, 4000


class TraverseError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _links(v: Any) -> List[str]:
    vals = v if isinstance(v, list) else [v]
    return [t for t in (F.link_target(x) for x in vals) if t]


# ─── индекс ────────────────────────────────────────────────────────────────

def _files(vault: Path) -> List[Path]:
    return [p for p in sorted(vault.rglob("*.md")) if p.relative_to(vault).parts[0] not in _SKIP]


def _signature(vault: Path, files: List[Path]) -> str:
    h = hashlib.sha1()
    for p in files:
        st = p.stat()
        h.update(f"{p.relative_to(vault).as_posix()}|{st.st_mtime_ns}|{st.st_size}\n".encode("utf-8"))
    return h.hexdigest()


class Index:
    def __init__(self, ont: O.Ontology, nodes: Dict[str, Dict[str, Any]], edges: List[Tuple[str, str, str]]):
        self.ont, self.nodes = ont, nodes
        self.edges = edges
        self.out: Dict[str, List[Tuple[str, str]]] = {}
        self.inc: Dict[str, List[Tuple[str, str]]] = {}
        for a, e, b in edges:
            self.out.setdefault(a, []).append((e, b))
            self.inc.setdefault(b, []).append((e, a))

    def steps(self, name: str) -> List[Tuple[str, str, str, bool]]:
        """Все шаги от узла: (ключ шага, ребро, сосед, по ребру ли)."""
        out = [(e, e, b, True) for e, b in self.out.get(name, [])]
        for e, a in self.inc.get(name, []):
            if not self.ont.is_symmetric(e):
                out.append((self.ont.inverse_of(e), e, a, False))
        return sorted(set(out))

    def label(self, name: str) -> str:
        n = self.nodes.get(name)
        if n is None:
            return F.link(name)
        tag = n["kind"] + (f":{n['role']}" if n.get("role") else "") + (f", {n['service']}" if n.get("service") else "")
        return f"{F.link(name)} ({tag})"

    def edge_line(self, a: str, e: str, b: str) -> str:
        return f"{self.label(a)} —{e}→ {self.label(b)}"


def build_index(root: Path, cfg: Dict[str, Any], use_cache: bool = True) -> Index:
    root = Path(root)
    ont = O.load(root)
    vault = root / cfg["vault"]
    files = _files(vault) if vault.is_dir() else []
    sig = _signature(vault, files)
    cache_p = root / INDEX_REL
    if use_cache and cache_p.is_file():
        try:
            data = json.loads(cache_p.read_text(encoding="utf-8"))
            if data.get("signature") == sig and data.get("ontology") == ont.version:
                return Index(ont, data["nodes"], [tuple(e) for e in data["edges"]])
        except ValueError:
            pass
    nodes: Dict[str, Dict[str, Any]] = {}
    fms: Dict[str, Dict[str, Any]] = {}
    for p in files:
        try:
            data, _ = F.read(p.read_bytes().decode("utf-8"))
        except (F.FMError, UnicodeDecodeError):
            continue
        kind = data.get("kind")
        if kind not in ont.kinds or p.stem in nodes:
            continue
        owner = [t for e, spec in ont.edges.items() if spec.get("evidence") == "optional" and e in data
                 for t in _links(data[e])]
        nodes[p.stem] = {"kind": kind, "role": data.get("role"), "service": owner[0] if owner else None,
                         "key": data.get("key"), "aliases": [a for a in (data.get("aliases") or []) if isinstance(a, str)],
                         "path": p.relative_to(vault).as_posix()}
        fms[p.stem] = data
    edges: Set[Tuple[str, str, str]] = set()
    for name, data in fms.items():
        for e in ont.edges:
            if e not in data:
                continue
            for t in _links(data[e]):
                if t in nodes and t != name:
                    edges.add((name, e, t))
                    if ont.is_symmetric(e):
                        edges.add((t, e, name))
    idx = Index(ont, nodes, sorted(edges))
    cache_p.parent.mkdir(parents=True, exist_ok=True)
    cache_p.write_text(json.dumps({"signature": sig, "ontology": ont.version, "nodes": nodes, "edges": sorted(edges)},
                                  ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return idx


# ─── разрешение имени ──────────────────────────────────────────────────────

def _resolve(idx: Index, name: str) -> str:
    if name in idx.nodes:
        return name
    low = (name or "").strip().lower()
    hits = sorted(n for n, d in idx.nodes.items()
                  if n.lower() == low or low in (a.lower() for a in d["aliases"]) or str(d.get("key") or "").lower() == low)
    if len(hits) == 1:
        return hits[0]
    cands = hits or [h["name"] for h in find(idx, name, limit=5)["hits"]]
    raise TraverseError(f"нет узла «{name}»" + (f"; похожие: {', '.join(cands[:5])}" if cands else "") +
                        " — найди его через graph_find", "E-node-unknown")


def _clip(v: Any, default: int, top: int) -> Tuple[int, bool]:
    try:
        n = int(v) if v is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, top)), n > top


# ─── инструменты ───────────────────────────────────────────────────────────

def find(idx: Index, q: str, kind: Optional[str] = None, limit: Any = 20) -> Dict[str, Any]:
    lim, clipped = _clip(limit, 20, 20)
    low = (q or "").strip().lower()
    if not low:
        raise TraverseError("q — часть имени, алиас или ключ", "E-arg-q")
    if kind is not None and kind not in idx.ont.kinds:
        raise TraverseError(f"kind — один из {', '.join(idx.ont.kinds)}", "E-arg-kind")
    scored = []
    for name, d in idx.nodes.items():
        if kind and d["kind"] != kind:
            continue
        fields = [name.lower()] + [a.lower() for a in d["aliases"]] + ([str(d["key"]).lower()] if d.get("key") else [])
        if any(f == low for f in fields):
            rank = 0
        elif any(f.startswith(low) for f in fields):
            rank = 1
        elif any(low in f for f in fields):
            rank = 2
        else:
            continue
        scored.append((rank, len(name), name))
    scored.sort()
    hits = [{"name": n, "line": idx.label(n) + (f" key={idx.nodes[n]['key']}" if idx.nodes[n].get("key") else "")}
            for _, _, n in scored[:lim]]
    return {"hits": hits, "lines": [h["line"] for h in hits], "total": len(scored), "clipped": clipped or len(scored) > lim}


def node(idx: Index, root: Path, cfg: Dict[str, Any], name: str, body: str = "none", max_chars: Any = 1500) -> Dict[str, Any]:
    name = _resolve(idx, name)
    chars, _ = _clip(max_chars, 1500, CHARS_MAX)
    if body not in ("none", "card", "source", "evidence"):
        raise TraverseError("body — none | card | source | evidence", "E-arg-body")
    groups: Dict[str, List[str]] = {}
    for key, _, other, _ in idx.steps(name):
        groups.setdefault(key, []).append(F.link(other))
    lines = [idx.label(name)]
    for key in sorted(groups):
        title = idx.ont.edges[key]["title_ru"] if key in idx.ont.edges else \
            "← " + idx.ont.edges[idx.ont.edge_of_inverse(key)]["title_ru"]
        lines.append(f"- {key} ({title}): " + ", ".join(sorted(groups[key])))
    text = ""
    if body != "none":
        _, b = F.read((Path(root) / cfg["vault"] / idx.nodes[name]["path"]).read_bytes().decode("utf-8"))
        want = {"card": lambda n: n == "card", "source": lambda n: n.startswith("source"),
                "evidence": lambda n: n == "edges"}[body]
        text = "\n\n".join(blk["content"].strip("\n") for blk in F.blocks(b) if want(blk["name"]))
        if len(text) > chars:
            text = text[:chars] + f"\n…(обрезано {len(text) - chars} знаков; max_chars ≤ {CHARS_MAX})"
    return {"name": name, "lines": lines, "body": text}


def _edge_filter(idx: Index, edges: Optional[List[str]]) -> Optional[Set[str]]:
    if not edges:
        return None
    known = set(idx.ont.edges) | {e["inverse"] for e in idx.ont.edges.values()}
    bad = [e for e in edges if e not in known]
    if bad:
        raise TraverseError(f"неизвестные рёбра {', '.join(bad)} (есть: {', '.join(sorted(known))})", "E-arg-edges")
    return set(edges)


def neighbors(idx: Index, name: str, edges: Optional[List[str]] = None, dir: str = "both", depth: Any = 1,
              limit: Any = 20) -> Dict[str, Any]:
    name = _resolve(idx, name)
    if dir not in ("out", "in", "both"):
        raise TraverseError("dir — out | in | both", "E-arg-dir")
    want = _edge_filter(idx, edges)
    dep, dclip = _clip(depth, 1, DEPTH_MAX)
    lim, lclip = _clip(limit, 20, LIMIT_MAX)
    seen, lines, found = {name}, [], []
    queue = deque([(name, 0)])
    total = 0
    while queue:
        cur, d = queue.popleft()
        if d >= dep:
            continue
        for key, e, other, forward in idx.steps(cur):
            if dir == "out" and not forward or dir == "in" and forward:
                continue
            if want is not None and key not in want and e not in want:
                continue
            total += 1
            if len(lines) < lim:
                lines.append(idx.edge_line(cur, e, other) if forward else idx.edge_line(other, e, cur))
                found.append({"from": cur, "key": key, "to": other, "depth": d + 1})
            if other not in seen:
                seen.add(other)
                queue.append((other, d + 1))
    return {"name": name, "lines": lines, "steps": found, "total": total, "clipped": dclip or lclip or total > lim}


def _is_container(idx: Index, name: str) -> bool:
    return idx.ont.kinds[idx.nodes[name]["kind"]].get("owner") == "self"


def path(idx: Index, src: str, dst: str, max_depth: Any = 6) -> Dict[str, Any]:
    """Кратчайшие пути. Принадлежность сервису (рёбра с evidence optional) — не взаимодействие (D7): такой шаг
    допустим только первым из сервиса или последним в сервис, иначе путь срезал бы через узел сервиса."""
    a, b = _resolve(idx, src), _resolve(idx, dst)
    dep, clipped = _clip(max_depth, PATH_DEPTH_MAX, PATH_DEPTH_MAX)
    structural = {e for e, spec in idx.ont.edges.items() if spec.get("evidence") == "optional"}
    a_box, b_box = _is_container(idx, a), _is_container(idx, b)
    dist = {a: 0}
    parents: Dict[str, List[Tuple[str, str]]] = {a: []}
    frontier = [a]
    while frontier and b not in dist and dist[frontier[0]] < dep:
        nxt = []
        for cur in frontier:
            for key, e, other, _ in idx.steps(cur):
                if e in structural and not ((cur == a and a_box) or (other == b and b_box)):
                    continue
                if other not in dist:
                    dist[other] = dist[cur] + 1
                    parents[other] = []
                    nxt.append(other)
                if dist[other] == dist[cur] + 1:
                    parents[other].append((cur, key))
        frontier = sorted(set(nxt))
    if b not in dist:
        return {"from": a, "to": b, "paths": [], "lines": [f"пути длиной ≤ {dep} между {F.link(a)} и {F.link(b)} нет"],
                "clipped": clipped}
    paths: List[List[str]] = []

    def walk(cur: str, tail: List[str]) -> None:
        if len(paths) >= PATHS_MAX:
            return
        if cur == a:
            paths.append([a] + tail)
            return
        for prev, key in sorted(parents[cur]):
            walk(prev, [key, cur] + tail)
    walk(b, [])
    lines = []
    for p in paths:
        chain = F.link(p[0])
        for i in range(1, len(p), 2):
            chain += f" —{p[i]}→ {F.link(p[i + 1])}"
        lines.append(chain)
    return {"from": a, "to": b, "paths": paths, "lines": lines, "clipped": clipped}


def impact(idx: Index, name: str, limit: Any = 30) -> Dict[str, Any]:
    """Что затронет изменение узла: обход по полю impact рёбер (forward — по ребру, inverse — против, both)."""
    name = _resolve(idx, name)
    lim, lclip = _clip(limit, 30, LIMIT_MAX)
    via: Dict[str, str] = {name: ""}
    queue = deque([name])
    while queue:
        cur = queue.popleft()
        for key, e, other, forward in idx.steps(cur):
            mode = idx.ont.edges[e].get("impact")
            ok = mode == "both" or (mode == "forward" and forward) or (mode == "inverse" and not forward)
            if ok and other not in via:
                via[other] = f"{F.link(cur)} —{key}→"
                queue.append(other)
    hit = [n for n in via if n != name]
    by_kind: Dict[str, List[str]] = {}
    for n in hit:
        by_kind.setdefault(idx.nodes[n]["kind"], []).append(n)
    lines = []
    for kind in idx.ont.kinds:
        for n in sorted(by_kind.get(kind, [])):
            if len(lines) < lim:
                lines.append(f"{idx.label(n)} ← {via[n]}")
    return {"name": name, "affected": {k: sorted(v) for k, v in by_kind.items()}, "lines": lines, "total": len(hit),
            "clipped": lclip or len(hit) > lim}


def service_links(idx: Index, svc: str, limit: Any = 30) -> Dict[str, Any]:
    """Сервис ↔ сервис — производный вид (D7): прямые вызовы и топики, с операциями-посредниками."""
    svc = _resolve(idx, svc)
    if idx.ont.kinds[idx.nodes[svc]["kind"]].get("owner") != "self":
        raise TraverseError(f"«{svc}» — не сервис", "E-arg-svc")
    lim, lclip = _clip(limit, 30, LIMIT_MAX)

    def owner(n: str) -> Optional[str]:
        d = idx.nodes[n]
        return n if idx.ont.kinds[d["kind"]].get("owner") == "self" else d.get("service")
    rows: Set[str] = set()
    owned_edges = {e for e, spec in idx.ont.edges.items() if spec.get("evidence") == "optional"}
    for a, e, b in idx.edges:
        if e in owned_edges:
            continue
        sa, sb = owner(a), owner(b)
        if sa and sb and sa != sb and svc in (sa, sb):
            rows.add(f"{F.link(sa)} → {F.link(sb)}: {idx.edge_line(a, e, b)}")
    # через узел без сервиса (топик): издатель — ребро с impact forward к нему, читатель — с impact inverse
    for t in idx.nodes:
        if owner(t):
            continue
        ins = [(a, e) for e, a in idx.inc.get(t, []) if owner(a)]
        for a, ea in ins:
            for c, ec in ins:
                sa, sc = owner(a), owner(c)
                if a != c and ea != ec and sa != sc and svc in (sa, sc) and \
                        idx.ont.edges[ea].get("impact") == "forward" and idx.ont.edges[ec].get("impact") == "inverse":
                    rows.add(f"{F.link(sa)} → {F.link(sc)} через {idx.label(t)}: {F.link(a)} —{ea}→ … ←{ec}— {F.link(c)}")
    out = sorted(rows)
    return {"service": svc, "lines": out[:lim], "total": len(out), "clipped": lclip or len(out) > lim}
