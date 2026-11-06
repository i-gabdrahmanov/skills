"""catalog.py — дедуп и каталог узлов: решение человека до нарезки (задача 1.4, D10, D13, D24).

Черновики делаются по файлам (1.3), а узел встречается в нескольких. Каталог сводит их в единый список,
и человек утверждает его один раз — до нарезки: видит масштаб, выбирает scope, правит имена, пока
нарезка их не размножила. Здесь же главный риск ТЗ: общий узел склеивается (§5), похожий — нет (§6).

Склейка — только по точному совпадению: `key`, либо вид + нормализованное имя (регистр, ё/е, пробелы,
кавычки). Похожие имена (общие слова, тот же вид и сервис) идут в `review` «это один узел?» и сами не
склеиваются никогда.

Решения человека (`decide`): merge, split, rename, move, drop, scope — журнал `.spec-graph/decisions.json`
и человеческий `_graph/decisions.md`. Журнал применяется при каждой сборке, поэтому повторная разведка
даёт тот же каталог. Сборка детерминирована: тот же вход → побайтно тот же `catalog.json` — согласие
`graph-catalog-<KEY>`, привязанное к нему, не слетает от простой пересборки, только от изменения.
Утверждённый снимок — `catalog.approved.json`; узлы, которые позже предложит раскладка (1.5), идут
отдельной дельтой и снимок не меняют.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import consent as CS
from . import ontology as O
from . import profile as PF
from . import survey as SV

CATALOG_REL = ".spec-graph/catalog.json"
APPROVED_REL = ".spec-graph/catalog.approved.json"
DECISIONS_REL = ".spec-graph/decisions.json"
DELTA_DECISIONS_REL = ".spec-graph/delta-decisions.json"   # пишет review.delta_decide
EDGE_DECISIONS_REL = ".spec-graph/edge-decisions.json"     # пишет review.edges_decide
CONSENT = "graph-catalog"
ACTIONS = ("merge", "split", "rename", "move", "drop", "scope")
# поля узла, которые черновик переносит в каталог как есть
NODE_FIELDS = ("service", "key", "role", "subkind")
_REVIEW_MIN = 0.5
_REVIEW_MAX = 50
_STOP = {"для", "при", "по", "в", "и", "о", "на", "из"}


def norm_name(name: str) -> str:
    s = name.lower().replace("ё", "е")
    s = re.sub(r"[«»\"'`]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def identity(n: Dict[str, Any]) -> str:
    return n["key"] if n.get("key") else f"{n['kind']}|{norm_name(n['name'])}"


def node_id(ident: str) -> str:
    return "n-" + hashlib.sha1(ident.encode("utf-8")).hexdigest()[:10]


def _tokens(name: str, generic: Optional[set] = None) -> set:
    """Значимые слова имени: без служебных и без общего словаря (у всех шедулеров есть «шедулер»)."""
    generic = generic or set()
    return {w for w in re.findall(r"[^\W\d_]{2,}", norm_name(name)) if w not in _STOP and w not in generic}


# ─── решения человека ──────────────────────────────────────────────────────

def load_decisions(root: Path) -> List[Dict[str, Any]]:
    p = Path(root) / DECISIONS_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def _check_action(a: Dict[str, Any]) -> Optional[str]:
    act = a.get("action")
    need = {"merge": ("into", "names"), "split": ("node", "file", "name"), "rename": ("node", "name"),
            "move": ("node", "service"), "drop": ("node",), "scope": ("include",)}
    if act not in need:
        return f"action — одно из {', '.join(ACTIONS)}"
    miss = [f for f in need[act] if f not in a]
    return f"{act}: нет полей {', '.join(miss)}" if miss else None


def decide(root: Path, actions: List[Dict[str, Any]], vault: str) -> Dict[str, Any]:
    """Дописать решения в журнал (повтор того же решения не дублируется) и в _graph/decisions.md."""
    errors = [f"actions[{i}]: {e}" for i, a in enumerate(actions) for e in [_check_action(a)] if e]
    if errors:
        return {"errors": errors}
    log = load_decisions(root)
    added = 0
    for a in actions:
        rec = {k: a[k] for k in sorted(a)}
        if rec not in log:
            log.append(rec)
            added += 1
    p = Path(root) / DECISIONS_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_decisions_md(root, vault)
    return {"added": added, "total": len(log)}


def _load(root: Path, rel: str, empty: Any) -> Any:
    p = Path(root) / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else empty


def write_decisions_md(root: Path, vault: str) -> None:
    """_graph/decisions.md — все решения человека: каталог, узлы дельты, отклонённые связи (2.6)."""
    lines = ["# Решения человека", "", "Решение → почему. Применяются при каждой сборке; отклонённое больше не предлагается.", "",
             "## Каталог", ""]
    for a in load_decisions(root):
        what = ", ".join(f"{k}={v}" for k, v in a.items() if k not in ("action", "why"))
        lines.append(f"- **{a['action']}** {what} → {a.get('why', 'без пояснения')}")
    delta = _load(root, DELTA_DECISIONS_REL, {})
    if delta:
        lines += ["", "## Узлы, добавленные раскладкой", ""]
        for name, d in sorted(delta.items()):
            what = d.get("into") or d.get("name") or ""
            lines.append(f"- **{d['action']}** «{name}»" + (f" → «{what}»" if what else "") + f" → {d.get('why', '')}")
    edges = _load(root, EDGE_DECISIONS_REL, [])
    if edges:
        lines += ["", "## Отклонённые связи", ""]
        for e in edges:
            lines.append(f"- «{e['from']}» —{e['edge']}→ «{e['to']}» → {e.get('why', '')}")
    md = Path(root) / vault / "_graph" / "decisions.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── сборка ────────────────────────────────────────────────────────────────

def _merge_into(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for s in src["sources"]:
        if s not in dst["sources"]:
            dst["sources"].append(s)
    for n in src["names_seen"]:
        if n not in dst["names_seen"]:
            dst["names_seen"].append(n)
    dst["from"] = sorted(set(dst["from"]) | set(src["from"]))


def build(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ont = O.load(root)
    drafts = SV.load_drafts(root)
    drafts.sort(key=lambda d: (d["file"], d["item_id"]))
    nodes: Dict[str, Dict[str, Any]] = {}
    conflicts, not_nodes = [], []
    for d in drafts:
        for n in d["nodes"]:
            ident = identity(n)
            rec = {"kind": n["kind"], "name": n["name"],
                   "sources": [{"file": d["file"], "sections": list(n.get("sections") or [])}],
                   "names_seen": [n["name"]], "from": [d["item_id"]]}
            for f in NODE_FIELDS:
                if n.get(f):
                    rec[f] = n[f]
            if ident in nodes:
                prev = nodes[ident]
                for f in ("kind", "role", "subkind", "service"):
                    if prev.get(f) != rec.get(f):
                        conflicts.append({"node": prev["name"], "field": f, "values": [prev.get(f), rec.get(f)],
                                          "files": [prev["sources"][0]["file"], d["file"]]})
                _merge_into(prev, rec)
            else:
                nodes[ident] = rec
        for x in d.get("not_nodes") or []:
            not_nodes.append(dict(x, file=d["file"]))
    scope = None
    for a in load_decisions(root):
        by_name = {n["name"]: k for k, n in nodes.items()}
        act = a["action"]
        if act == "merge":
            into = by_name.get(a["into"])
            for name in a["names"]:
                k = by_name.get(name)
                if into and k and k != into:
                    _merge_into(nodes[into], nodes.pop(k))
        elif act == "split" and by_name.get(a["node"]):
            src = nodes[by_name[a["node"]]]
            part = [s for s in src["sources"] if s["file"] == a["file"]]
            if part and len(src["sources"]) > 1:
                src["sources"] = [s for s in src["sources"] if s["file"] != a["file"]]
                new = {k: v for k, v in src.items() if k in ("kind", "service", "role", "subkind")}
                new.update(name=a["name"], sources=part, names_seen=[a["name"]], **{"from": []})
                nodes[f"{src['kind']}|{norm_name(a['name'])}"] = new
        elif act == "rename" and by_name.get(a["node"]):
            nodes[by_name[a["node"]]]["name"] = a["name"]
        elif act == "move" and by_name.get(a["node"]):
            nodes[by_name[a["node"]]]["service"] = a["service"]
        elif act == "drop" and by_name.get(a["node"]):
            nodes.pop(by_name[a["node"]])
        elif act == "scope":
            scope = sorted(a["include"])
    order = {k: i for i, k in enumerate(ont.kinds)}
    out = []
    for ident, n in nodes.items():
        n = dict(n, id=node_id(ident))
        if scope is not None:
            files = {s["file"] for s in n["sources"]}
            n["in_scope"] = bool(n.get("service") in scope or any(any(f.startswith(x) for x in scope) for f in files)
                                 or not ont.owned_by_service(n["kind"]))
        out.append(n)
    out.sort(key=lambda n: (order.get(n["kind"], 99), n.get("service") or "", n["name"]))
    names: Dict[str, List[Dict[str, Any]]] = {}
    for n in out:
        names.setdefault(n["name"], []).append(n)
    collisions = [{"name": k, "kinds": [n["kind"] for n in v],
                   "suggest": [f"{k} ({n.get('service') or ont.kinds[n['kind']]['title_ru'].lower()})" for n in v]}
                  for k, v in names.items() if len(v) > 1]
    review = []
    generic = PF.generic_words()
    for i, a in enumerate(out):
        ta = _tokens(a["name"], generic)
        for b in out[i + 1:]:
            if a["kind"] != b["kind"] or a.get("service") != b.get("service") or a.get("key") and b.get("key"):
                continue
            tb = _tokens(b["name"], generic)
            sim = len(ta & tb) / max(1, min(len(ta), len(tb)))      # коэффициент перекрытия
            if ta and tb and sim >= _REVIEW_MIN:
                review.append({"a": a["name"], "b": b["name"], "kind": a["kind"], "similarity": round(sim, 2)})
    review.sort(key=lambda r: (-r["similarity"], r["a"], r["b"]))
    review = review[:_REVIEW_MAX]
    cat = {"version": 1, "nodes": out, "review": review, "conflicts": conflicts, "collisions": collisions,
           "not_nodes": sorted(not_nodes, key=lambda x: (x["file"], x.get("candidate", ""))), "scope": scope}
    p = Path(root) / CATALOG_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cat, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _write_md(root, cfg, cat, ont)
    return cat


def _write_md(root: Path, cfg: Dict[str, Any], cat: Dict[str, Any], ont: O.Ontology) -> None:
    lines = ["# Каталог узлов", "",
             "Решение человека шага graph-map: какие узлы станут заметками графа и в каком scope. Утвердите "
             "каталог — или скажите, что слить, разделить, переименовать, перенести, убрать.", ""]
    survey = Path(root) / cfg["vault"] / "_graph" / "survey.md"
    if survey.is_file():
        txt = survey.read_text(encoding="utf-8")
        m = re.search(r"## Объём работ\n\n(.*?)\n\n", txt, re.S)
        if m:
            lines += ["## Объём работ (из разведки)", "", m.group(1), ""]
    if cat["scope"] is not None:
        lines += ["## Scope", "", ", ".join(cat["scope"]), ""]
    for kind, spec in ont.kinds.items():
        rows = [n for n in cat["nodes"] if n["kind"] == kind]
        if not rows:
            continue
        lines += [f"## {spec['title_ru']} — {len(rows)}", "", "| имя | владелец | источники | склеен из |", "|---|---|---|---|"]
        for n in rows:
            src = "; ".join(f"{s['file']}" for s in n["sources"])
            seen = ", ".join(x for x in n["names_seen"] if x != n["name"]) or (f"{len(n['sources'])} файлов"
                                                                             if len(n["sources"]) > 1 else "")
            flag = "" if n.get("in_scope", True) else " *(вне scope)*"
            lines.append(f"| {n['name']}{flag} | {n.get('service') or '—'} | {src} | {seen} |")
        lines.append("")
    for title, rows, fmt in (
            ("Это один узел? (похожие имена — сами не склеиваются)", cat["review"],
             lambda r: f"- «{r['a']}» и «{r['b']}» ({ont.kinds[r['kind']]['title_ru']}, сходство {r['similarity']})"),
            ("Противоречия черновиков", cat["conflicts"],
             lambda r: f"- «{r['node']}»: {r['field']} = {r['values'][0]} / {r['values'][1]} ({', '.join(r['files'])})"),
            ("Одинаковые имена у разных узлов", cat["collisions"],
             lambda r: f"- «{r['name']}»: предлагаю {' / '.join(r['suggest'])}"),
            ("Кандидаты, признанные «не узлом»", cat["not_nodes"],
             lambda r: f"- «{r.get('candidate')}» ({r['file']}): {r.get('reason')}"
                       + (f" — предложен вид «{r['suggested_kind']}»" if r.get("suggested_kind") else ""))):
        lines += [f"## {title}", ""] + ([fmt(r) for r in rows] or ["- нет"]) + [""]
    kinds_new = sorted({r["suggested_kind"] for r in cat["not_nodes"] if r.get("suggested_kind")})
    lines += ["## Кандидаты в новые виды", ""] + ([f"- {k}" for k in kinds_new] or ["- нет"]) + [""]
    p = Path(root) / cfg["vault"] / "_graph" / "catalog.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


# ─── утверждение ───────────────────────────────────────────────────────────

def approval(root: Path) -> Dict[str, Any]:
    """Согласие на ТЕКУЩИЙ каталог; при совпадении — снимок catalog.approved.json."""
    st = CS.state(root, CONSENT, Path(root) / CATALOG_REL)
    if st["state"] == "ok":
        shutil.copyfile(Path(root) / CATALOG_REL, Path(root) / APPROVED_REL)
    return st


def approved(root: Path) -> Tuple[bool, str]:
    """Утверждён ли снимок: согласие привязано к содержимому catalog.approved.json."""
    snap = Path(root) / APPROVED_REL
    if not snap.is_file():
        return False, "каталог не утверждён: нет снимка (покажи _graph/catalog.md и спроси пользователя)"
    st = CS.state(root, CONSENT, snap)
    if st["state"] != "ok":
        return False, f"согласие {st['key']}: {st['state']} — каталог утверждают заново"
    return True, ""


def load_approved(root: Path) -> Dict[str, Any]:
    return json.loads((Path(root) / APPROVED_REL).read_text(encoding="utf-8"))
