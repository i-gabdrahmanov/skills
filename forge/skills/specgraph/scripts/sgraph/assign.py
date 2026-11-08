"""assign.py — раскладка блоков по утверждённым узлам (задача 1.5, рычаг 1, D24).

Узлы уже утверждены человеком (каталог 1.4). Модель на этом проходе решает одно: какой блок раздела к
какому узлу относится — классификация, а не творчество. Результат — план (`.spec-graph/plan/<id>.json`),
а не текст заметок: текст режет скрипт (1.6) байт в байт.

Очередь `assign-section` строится ТОЛЬКО из утверждённого каталога (без согласия graph-catalog — отказ
E-catalog-unapproved, кодом). Единица — непрерывный диапазон блоков одного файла: файл режется по
разделам верхнего уровня, крупный раздел — по подразделам, слишком крупный лист — по блокам (никогда
внутри блока). Каждый блок файла — ровно в одной единице, поэтому полнота раскладки обеспечена до ответа. Размер
единицы меряется по готовому тексту входа; блок, который один не влезает в бюджет, идёт отдельной
единицей с пометкой `indivisible` — сборку остальных он не роняет.

Проверки ответа (`put`): каждый блок единицы назначен ровно раз (узел / skip / unplaced); цель — узел
утверждённого каталога или новый узел этого же ответа (идёт в дельту `catalog.delta.json`, решается в
отчёте связей 2.6); блоки раздела узла-кандидата из каталога идут этому узлу, а не соседнему («разделите»); skip
duplicate-of — только для побайтного (с нормализацией пробелов) повтора; без обратных слэшей.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import blocks as B
from . import catalog as CT
from . import extract as X
from . import fsutil as FS
from . import ontology as O
from . import queue as Q

PLAN_REL = ".spec-graph/plan"
DELTA_REL = ".spec-graph/catalog.delta.json"
DELTA_DECISIONS_REL = CT.DELTA_DECISIONS_REL
DELTA_ACTIONS = ("accept", "merge", "rename", "drop")
BRIEF = "assign-section.md"
BRIEFS = Path(__file__).resolve().parents[2] / "references" / "briefs"
TOOL = "graph_assign_put"
SKIP_REASONS = ("toc", "boilerplate", "duplicate-of")
_BAD_TEXT = re.compile(r"[\\\x00-\x08\x0b\x0c\x0e-\x1f]")


class AssignError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def brief_chars() -> int:
    p = BRIEFS / BRIEF
    return len(p.read_text(encoding="utf-8")) if p.is_file() else 0


def unit_id(rel: str, first_block: str) -> str:
    return "assign-" + hashlib.sha1(f"{rel}|{first_block}".encode("utf-8")).hexdigest()[:12]


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ─── единицы ───────────────────────────────────────────────────────────────

def _ranges(blocks: List[Dict[str, Any]], room: int) -> List[Tuple[int, int]]:
    """Непрерывные диапазоны блоков, каждый в бюджете: по разделам, крупное — глубже, лист — по блокам."""
    secs = X.sections(blocks)
    size = lambda a, b: sum(len(x["text"]) + 40 for x in blocks[a:b])  # noqa: E731

    def split(a: int, b: int, level: int) -> List[Tuple[int, int]]:
        if size(a, b) <= room or b - a <= 1:
            return [(a, b)]
        heads = [s["start"] for s in secs if a < s["start"] < b and s["level"] == level]
        if not heads:
            deeper = [s["level"] for s in secs if a < s["start"] < b]
            if deeper and min(deeper) > level:
                return split(a, b, min(deeper))
            out, cur = [], a                               # лист: по блокам, не внутри блока
            for i in range(a, b):
                if i > cur and size(cur, i + 1) > room:
                    out.append((cur, i))
                    cur = i
            out.append((cur, b))
            return out
        cuts = [a] + heads + [b]
        out = []
        for x, y in zip(cuts, cuts[1:]):
            out += split(x, y, level + 1)
        return _join(out, size, room)

    top = min((s["level"] for s in secs), default=1)
    return split(0, len(blocks), top + 1 if any(s["level"] > top for s in secs) else top)


def _join(ranges, size, room):
    """Соседние мелкие диапазоны склеиваются, пока помещаются: меньше вызовов субагента."""
    out: List[Tuple[int, int]] = []
    for r in ranges:
        if out and size(out[-1][0], r[1]) <= room:
            out[-1] = (out[-1][0], r[1])
        else:
            out.append(r)
    return out


def _nodes_for_file(cat: Dict[str, Any], rel: str) -> List[Dict[str, Any]]:
    return [n for n in cat["nodes"] if n.get("in_scope", True) and any(s["file"] == rel for s in n["sources"])]


def _nodes_near(nodes: List[Dict[str, Any]], rel: str, spans: Dict[str, Tuple[int, int]], a: int, b: int):
    """Узлы файла, чьи разделы задевают диапазон блоков [a, b), и узлы без разделов (весь файл).
    Раздел, которого нет в оглавлении, считается задевающим: лучше лишняя строка, чем пропавший узел."""
    out = []
    for n in nodes:
        secs = [x for s in n["sources"] if s["file"] == rel for x in s.get("sections") or []]
        if not secs or any(x not in spans or (spans[x][0] < b and a < spans[x][1]) for x in secs):
            out.append(n)
    return out


def _fit(blocks: List[Dict[str, Any]], ranges: List[Tuple[int, int]], measure, limit: int):
    """Довести диапазоны до бюджета по НАСТОЯЩЕМУ тексту входа: перелив — пополам по ближайшему к
    середине заголовку (нет заголовка — по блокам). Один блок сверх бюджета — неделимый остаток."""
    out, todo = [], list(ranges)
    while todo:
        a, b = todo.pop(0)
        if measure(a, b) <= limit:
            out.append((a, b, False))
        elif b - a <= 1:
            out.append((a, b, True))
        else:
            mid = (a + b) // 2
            heads = [i for i in range(a + 1, b) if blocks[i]["type"] == "heading"]
            cut = min(heads, key=lambda i: (abs(i - mid), i)) if heads else mid
            todo[:0] = [(a, cut), (cut, b)]
    return out


def _input(rel: str, blocks: List[Dict[str, Any]], nodes: List[Dict[str, Any]], cands: List[Dict[str, Any]],
           part: str) -> str:
    lines = [f"# Раскладка блоков: {rel}{part}", "", "## Узлы каталога этого файла", ""]
    for n in nodes:
        kind = n["kind"] + (f":{n['role']}" if n.get("role") else "") + (f":{n['subkind']}" if n.get("subkind") else "")
        lines.append(f"- «{n['name']}» ({kind}{', ' + n['service'] if n.get('service') else ''})")
    if not nodes:
        lines.append("- нет: блоки — в skip/unplaced или новый узел с объяснением")
    if cands:
        lines += ["", "## Кандидаты экстракторов в этих блоках", ""]
        lines += [f"- {c['kind']} — «{c['name_hint']}» (правило {c['rule']})" for c in cands]
    lines += ["", "## Блоки (id — вид — текст дословно)", ""]
    for b in blocks:
        lines += [f"### `{b['id']}` — {b['type']}", "", b["text"].rstrip("\n"), ""]
    lines.append(f"Сдай раскладку инструментом `{TOOL}` с item_id этой единицы.")
    return "\n".join(lines) + "\n"


def start(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Очередь assign-section из утверждённого каталога (иначе отказ)."""
    ok, why = CT.approved(root)
    if not ok:
        raise AssignError(why, "E-catalog-unapproved")
    cat = CT.load_approved(root)
    files = sorted({s["file"] for n in cat["nodes"] if n.get("in_scope", True) for s in n["sources"]})
    # Бюджет — на НАСТОЯЩИЙ вход единицы (шапка + кандидаты + блоки), а не «бюджет − бриф − 2000» на
    # одни блоки: у жирного файла шапка со списком его узлов доходила до 26 тыс. знаков (≈250 узлов
    # rmoc-спеки), и сборка падала при любом бюджете — куски добивались до нового room (боевой прогон).
    # Поэтому в шапке — узлы, чьи разделы задевают кусок (весь список — только если он помещается),
    # а кусок с переливом делится дальше.
    limit = Q.settings(cfg)["budget_chars"] - brief_chars()
    specs = []
    for rel in files:
        data = B.load(root, rel) or B.scan_file(root, rel)
        blocks = data["blocks"]
        cand_p = Path(root) / X.CANDIDATES_REL / (rel + ".json")
        cands = json.loads(cand_p.read_text(encoding="utf-8"))["candidates"] if cand_p.is_file() else []
        nodes = _nodes_for_file(cat, rel)
        spans = {blocks[x["head"]]["id"]: (x["start"], x["end"]) for x in X.sections(blocks)}

        def make(a, b, part):
            ids = {x["id"] for x in blocks[a:b]}
            cc = [c for c in cands if set(c["blocks"]) & ids]
            text = _input(rel, blocks[a:b], nodes, cc, part)
            if len(text) > limit:
                text = _input(rel, blocks[a:b], _nodes_near(nodes, rel, spans, a, b), cc, part)
            return text

        base = len(_input(rel, [], [], [], " — часть 999 из 999"))
        ranges = _fit(blocks, _ranges(blocks, max(limit - base, 2000)),
                      lambda a, b: len(make(a, b, " — часть 999 из 999")), limit)
        for k, (a, b, indivisible) in enumerate(ranges, 1):
            ids = [x["id"] for x in blocks[a:b]]
            part = "" if len(ranges) == 1 else f" — часть {k} из {len(ranges)}"
            spec = {"id": unit_id(rel, ids[0]), "brief": BRIEF, "tool": TOOL, "input": make(a, b, part),
                    "refs": {"file": rel, "blocks": ids}}
            if indivisible:
                spec["indivisible"] = True
            specs.append(spec)
    counts = Q.build(root, "assign-section", specs, cfg, {BRIEF: brief_chars()})
    out = {"files": len(files), "units": len(specs), "queue": counts,
           "next": "цикл очереди assign-section: graph_next queue=assign-section"}
    if counts.get("oversize"):
        out["oversize"] = (f"{len(counts['oversize'])} единиц из одного блока сверх бюджета — блок не режется, "
                           f"идут как есть: " + ", ".join(f"{o['id']} ({o['chars']})" for o in counts["oversize"]))
    return out


# ─── ответ модели ──────────────────────────────────────────────────────────

def _unit(root: Path, item_id: str) -> Dict[str, Any]:
    data = json.loads((Path(root) / Q.QUEUE_REL / "assign-section.json").read_text(encoding="utf-8"))
    return next(it for it in data["items"] if it["id"] == item_id)


def _full_ids(rel: str, row: Any) -> Any:
    """Короткие id блоков (`#12`) — в полные (`<файл>#12`): единица всегда про один файл."""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    for k in ("blocks", "of"):
        if isinstance(out.get(k), list):
            out[k] = [B.full_id(rel, b) for b in out[k]]
    return out


def load_delta(root: Path) -> List[Dict[str, Any]]:
    p = Path(root) / DELTA_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def load_delta_decisions(root: Path) -> Dict[str, Dict[str, Any]]:
    """Решения человека по узлам дельты (отчёт связей, 2.6): имя узла → {action, into?, name?, why}."""
    p = Path(root) / DELTA_DECISIONS_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def delta_resolution(root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Optional[str]]]:
    """(узлы дельты, которые остаются — с новым именем после rename; старое имя → новое или None для drop).
    Узел без решения остаётся как есть — гейт link его не пропустит."""
    dec = load_delta_decisions(root)
    kept, remap = [], {}
    for n in load_delta(root):
        d = dec.get(n["name"]) or {"action": "accept"}
        if d["action"] == "accept":
            kept.append(n)
        elif d["action"] == "rename":
            kept.append(dict(n, name=d["name"]))
            remap[n["name"]] = d["name"]
        else:
            remap[n["name"]] = d.get("into") if d["action"] == "merge" else None
    return kept, remap


def _all_blocks_text(root: Path) -> Dict[str, str]:
    out = {}
    d = Path(root) / B.BLOCKS_REL
    for p in sorted(d.rglob("*.json")) if d.is_dir() else []:
        for b in json.loads(p.read_text(encoding="utf-8"))["blocks"]:
            out[b["id"]] = b["text"]
    return out


def put(root: Path, item_id: str, args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    ont = O.load(root)
    unit = _unit(root, item_id)
    rel, ids = unit["refs"]["file"], unit["refs"]["blocks"]
    cat = CT.load_approved(root)
    by_name = {n["name"]: n for n in cat["nodes"]}
    by_name.update({n["name"]: n for n in load_delta(root)})
    by_id = {n["id"]: n for n in cat["nodes"]}
    errs: List[Tuple[str, str]] = []
    assign, skip, unplaced, new_nodes = (args.get(k) or [] for k in ("assign", "skip", "unplaced", "new_nodes"))
    assign, skip, unplaced = ([_full_ids(rel, x) for x in rows] for rows in (assign, skip, unplaced))
    for k, v in (("assign", assign), ("skip", skip), ("unplaced", unplaced), ("new_nodes", new_nodes)):
        for i, x in enumerate(v):
            if not isinstance(x, dict):
                errs.append(("E-assign-shape", f"{k}[{i}]: объект"))
            elif any(isinstance(s, str) and _BAD_TEXT.search(s) for s in x.values()):
                errs.append(("E-assign-text", f"{k}[{i}]: обратный слэш или управляющий символ"))
    if errs:
        return {}, errs[:10]
    new_by_name = {}
    for i, n in enumerate(new_nodes):
        kind = n.get("kind")
        if kind not in ont.kinds:
            errs.append(("E-assign-new", f"new_nodes[{i}]: вид «{kind}» — один из {', '.join(ont.kinds)}"))
            continue
        for e in ont.check_name(kind, n.get("name"), n.get("service")):
            errs.append(("E-assign-new", f"new_nodes[{i}] «{n.get('name')}»: {e}"))
        if ont.owned_by_service(kind) and not n.get("service"):
            errs.append(("E-assign-new", f"new_nodes[{i}]: нужен service"))
        if n.get("name") in by_name:
            errs.append(("E-assign-new", f"new_nodes[{i}]: «{n['name']}» уже есть в каталоге — назначай на него"))
        if not n.get("why"):
            errs.append(("E-assign-new", f"new_nodes[{i}]: нужен why — почему узла нет в каталоге"))
        new_by_name[n.get("name")] = n
    seen: Dict[str, str] = {}
    owner: Dict[str, str] = {}
    for where, rows in (("assign", assign), ("skip", skip), ("unplaced", unplaced)):
        for i, x in enumerate(rows):
            bl = x.get("blocks") or []
            if not isinstance(bl, list) or not bl:
                errs.append(("E-assign-shape", f"{where}[{i}]: blocks — непустой список id"))
                continue
            for b in bl:
                if b not in ids:
                    errs.append(("E-assign-foreign", f"{where}[{i}]: блок {b} не из этой единицы"))
                elif b in seen:
                    errs.append(("E-assign-twice", f"блок {b} назначен дважды ({seen[b]} и {where})"))
                else:
                    seen[b] = where
            if where == "assign":
                target = x.get("node")
                node = by_name.get(target) or by_id.get(target) or new_by_name.get(target)
                if node is None:
                    errs.append(("E-assign-node", f"assign[{i}]: узла «{target}» нет в каталоге — назначь на "
                                                  f"существующий или добавь в new_nodes с why"))
                else:
                    for b in bl:
                        owner[b] = node["name"]
            if where == "skip" and x.get("reason") not in SKIP_REASONS:
                errs.append(("E-assign-skip", f"skip[{i}]: reason — {', '.join(SKIP_REASONS)}"))
    missing = [b for b in ids if b not in seen]
    if missing:
        errs.append(("E-assign-lost", f"блоки без назначения: {', '.join(missing[:5])}"
                                       + (f" и ещё {len(missing) - 5}" if len(missing) > 5 else "")))
    texts = None
    for i, x in enumerate(skip):
        if x.get("reason") == "duplicate-of":
            texts = texts if texts is not None else _all_blocks_text(root)
            of = x.get("of") or []
            mine = _ws("".join(texts.get(b, "") for b in x.get("blocks") or []))
            theirs = _ws("".join(texts.get(b, "") for b in of))
            if not of or any(b in (x.get("blocks") or []) for b in of) or mine != theirs:
                errs.append(("E-assign-dup", f"skip[{i}]: duplicate-of — только для побайтного повтора блоков `of`"))
    cand_p = Path(root) / X.CANDIDATES_REL / (rel + ".json")
    cands = json.loads(cand_p.read_text(encoding="utf-8"))["candidates"] if cand_p.is_file() else []
    # блок → самый внутренний раздел-кандидат, который есть в каталоге: его блоки — его узлу
    inner: Dict[str, Tuple[int, str]] = {}
    for c in cands:
        # раздел — к своему узлу, только если этот узел из ЭТОГО файла: одноимённый узел другого файла
        # («Алгоритм обработки» из [kid_seizure]) заставлял субагента уносить разделы в unplaced
        if c.get("section") and c["name_hint"] in by_name and any(
                s.get("file") == rel for s in by_name[c["name_hint"]].get("sources") or []):
            for b in c["blocks"]:
                if b not in inner or len(c["blocks"]) < inner[b][0]:
                    inner[b] = (len(c["blocks"]), c["name_hint"])
    wrong: Dict[Tuple[str, str], int] = {}
    for b, node in owner.items():
        if b in inner and inner[b][1] != node:
            wrong[(node, inner[b][1])] = wrong.get((node, inner[b][1]), 0) + 1
    for (node, other), k in sorted(wrong.items()):
        errs.append(("E-assign-split", f"узел «{node}» взял {k} блок(а) раздела «{other}» — а «{other}» есть в "
                                       f"каталоге: каждый раздел к своему узлу"))
    if errs:
        return {}, errs[:10]
    plan = {"item_id": item_id, "file": rel, "assign": assign, "skip": skip, "unplaced": unplaced}
    p = Path(root) / PLAN_REL / f"{item_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, json.dumps(plan, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if new_nodes:
        delta = load_delta(root)
        names = {n["name"] for n in delta}
        for n in new_nodes:
            if n["name"] not in names:
                rec = {k: n[k] for k in ("kind", "name", "service", "key", "role", "subkind", "why") if n.get(k)}
                rec.update(id=CT.node_id(CT.identity(rec, ont)), sources=[{"file": rel, "sections": []}],
                           proposed_by=item_id)
                delta.append(rec)
        dp = Path(root) / DELTA_REL
        FS.write_text(dp, json.dumps(delta, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return plan, []


def load_plans(root: Path) -> List[Dict[str, Any]]:
    d = Path(root) / PLAN_REL
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
