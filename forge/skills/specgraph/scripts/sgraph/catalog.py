"""catalog.py — дедуп и каталог узлов: решение человека до нарезки (задача 1.4, D10, D13, D24).

Черновики делаются по файлам (1.3), а узел встречается в нескольких. Каталог сводит их в единый список,
и человек утверждает его один раз — до нарезки: видит масштаб, выбирает scope, правит имена, пока
нарезка их не размножила. Здесь же главный риск ТЗ: общий узел склеивается (§5), похожий — нет (§6).

Склейка — только по точному совпадению: `key`, либо вид + нормализованное имя (регистр, ё/е, пробелы,
кавычки). Похожие имена (общие слова, тот же вид и сервис) идут в `review` «это один узел?» и сами не
склеиваются никогда.

Решения человека (`decide`): merge, split, rename, move, drop, scope — журнал `.spec-graph/decisions.json`
и человеческий `_graph/decisions.md`; revoke {index} снимает запись журнала. Журнал применяется при
каждой сборке, поэтому повторная разведка даёт тот же каталог.

Ссылка на узел в решении — id (`n-…`) или имя, которое носит РОВНО один узел. Решение проверяется
сухим прогоном до записи: неоднозначное имя, узел, которого нет, split последнего файла узла — отказ
с перечнем id, а не тихий пропуск. На боевом прогоне GLM без этого merge по имени ушёл не в тот из
двух одноимённых узлов, merge по id молча не применился, split последнего файла молча пропустился, а
split-back затёр одноимённый узел вместе с его разделами. Записи журнала прежнего формата (без `v`)
применяются как раньше (по имени, одноимённые — последний вставленный), чтобы утверждённый каталог не
поменялся сам; неприменившееся видно в `problems` сборки и в отчёте. Сборка детерминирована: тот же вход → побайтно тот же `catalog.json` — согласие
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
from . import fsutil as FS
from . import ontology as O
from . import profile as PF
from . import survey as SV

CATALOG_REL = ".spec-graph/catalog.json"
APPROVED_REL = ".spec-graph/catalog.approved.json"
DECISIONS_REL = ".spec-graph/decisions.json"
DELTA_DECISIONS_REL = ".spec-graph/delta-decisions.json"   # пишет review.delta_decide
EDGE_DECISIONS_REL = ".spec-graph/edge-decisions.json"     # пишет review.edges_decide
CONSENT = "graph-catalog"
ACTIONS = ("merge", "split", "rename", "move", "drop", "scope", "revoke")
DECISION_V = 2
# поля узла, которые черновик переносит в каталог как есть
NODE_FIELDS = ("service", "key", "role", "subkind")
_REVIEW_MIN = 0.5
_REVIEW_MAX = 50
_STOP = {"для", "при", "по", "в", "и", "о", "на", "из"}


def norm_name(name: str) -> str:
    s = name.lower().replace("ё", "е")
    s = re.sub(r"[«»\"'`]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def identity(n: Dict[str, Any], ont: Optional[O.Ontology] = None) -> str:
    """Ключ узла; без ключа — вид и имя, у видов с сервисом-владельцем — и сервис: одноимённые шедулеры
    cars-service и shpi-service склеивались в один узел с сервисом первого (боевой прогон 2026-11-07)."""
    if n.get("key"):
        return n["key"]
    if ont is not None and n.get("kind") in ont.kinds and ont.owned_by_service(n["kind"]) and n.get("service"):
        return f"{n['kind']}|{n['service']}|{norm_name(n['name'])}"
    return legacy_identity(n)


def legacy_identity(n: Dict[str, Any]) -> str:
    """Ключ узла до 2026-11-08 (без сервиса и папки): id `n-…` в журнале решений резолвятся и по нему."""
    return n["key"] if n.get("key") else f"{n['kind']}|{norm_name(n['name'])}"


def folder(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


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


def _check_action(a: Dict[str, Any], ont: Optional[O.Ontology] = None) -> Optional[str]:
    if not isinstance(a, dict):
        return "решение — объект {action, …, why}"
    act = a.get("action")
    need = {"merge": ("into", "names"), "split": ("node", "file", "name"), "rename": ("node", "name"),
            "move": ("node", "service"), "drop": ("node",), "scope": ("include",), "revoke": ("index",)}
    if act is None and a.get("type") in need:
        return f"нет ключа action (передан type=«{a['type']}») — вид решения пишется в поле action"
    if act not in need:
        return f"action — одно из {', '.join(ACTIONS)}"
    miss = [f for f in need[act] if f not in a]
    if miss:
        return f"{act}: нет полей {', '.join(miss)}"
    if act == "merge" and not (isinstance(a["names"], list) and a["names"]):
        return "merge: names — непустой список имён или id узлов"
    if act == "split" and a.get("kind") is not None and ont is not None and a["kind"] not in ont.kinds:
        return f"split: kind — один из {', '.join(ont.kinds)}"
    if act == "revoke" and not (isinstance(a["index"], int) and not isinstance(a["index"], bool)):
        return "revoke: index — номер записи журнала (как в _graph/decisions.md)"
    return None


def decide(root: Path, actions: List[Dict[str, Any]], vault: str) -> Dict[str, Any]:
    """Дописать решения в журнал (повтор того же решения не дублируется) и в _graph/decisions.md.
    Новые решения сначала применяются вхолостую к текущему каталогу: не применилось — отказ всех."""
    ont = O.load(root)
    msgs: Dict[str, List[int]] = {}
    for i, a in enumerate(actions):
        e = _check_action(a, ont)
        if e:
            msgs.setdefault(e, []).append(i)
    if msgs:
        return {"errors": [f"actions[{','.join(map(str, ix))}]: {e}" for e, ix in msgs.items()]}
    log = load_decisions(root)
    revoke = sorted({a["index"] for a in actions if a["action"] == "revoke"}, reverse=True)
    bad = [n for n in revoke if not 1 <= n <= len(log)]
    if bad:
        return {"errors": [f"revoke: записи №{', '.join(map(str, bad))} нет (в журнале {len(log)})"]}
    revoked = [log.pop(n - 1) for n in revoke]
    fresh = []
    for a in actions:
        if a["action"] == "revoke":
            continue
        rec = dict({k: a[k] for k in sorted(a)}, v=DECISION_V)
        if rec not in log and rec not in fresh:
            fresh.append(rec)
    nodes, _, _ = _collect(root, ont)
    _apply(nodes, log, ont)
    problems = _apply(nodes, fresh, ont, base=len(log))
    errors = [f"actions «{fresh[p['n'] - len(log) - 1]['action']}» ({p['ref']}): {p['problem']}"
              for p in problems if p.get("strict")]
    if errors:
        return {"errors": errors}
    log += fresh
    p = Path(root) / DECISIONS_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_decisions_md(root, vault)
    out: Dict[str, Any] = {"added": len(fresh), "total": len(log)}
    if revoked:
        out["revoked"] = len(revoked)
    notes = [p["problem"] for p in problems if not p.get("strict")]
    if notes:
        out["notes"] = notes[:10]
    return out


def _load(root: Path, rel: str, empty: Any) -> Any:
    p = Path(root) / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else empty


def write_decisions_md(root: Path, vault: str) -> None:
    """_graph/decisions.md — все решения человека: каталог, узлы дельты, отклонённые связи (2.6)."""
    lines = ["# Решения человека", "", "Решение → почему. Применяются при каждой сборке; отклонённое больше не предлагается.", "",
             "## Каталог", ""]
    for i, a in enumerate(load_decisions(root), 1):
        what = ", ".join(f"{k}={v}" for k, v in a.items() if k not in ("action", "why", "v"))
        lines.append(f"{i}. **{a['action']}** {what} → {a.get('why', 'без пояснения')}")
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
    FS.write_text(md, "\n".join(lines) + "\n", encoding="utf-8")


# ─── сборка ────────────────────────────────────────────────────────────────

def _merge_into(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    if dst.get("stub") and not src.get("stub"):
        dst.pop("stub")                     # описан где-то разделом — уже не заглушка
    for s in src["sources"]:
        if s not in dst["sources"]:
            dst["sources"].append(s)
    for n in src["names_seen"]:
        if n not in dst["names_seen"]:
            dst["names_seen"].append(n)
    dst["from"] = sorted(set(dst["from"]) | set(src["from"]))


def _collect(root: Path, ont: O.Ontology) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Узлы черновиков, склеенные по точной идентичности (до решений человека). Виды merge=folder
    без ключа склеиваются только в пределах папки: из разных папок — разные узлы с одним именем,
    т.е. коллизия, и её решает человек (rename или merge), а не склейка по заголовку."""
    drafts = SV.load_drafts(root)
    drafts.sort(key=lambda d: (d["file"], d["item_id"]))
    nodes: Dict[str, Dict[str, Any]] = {}
    conflicts, not_nodes = [], []
    # узлы правил (auto) — первыми: модель их не подтверждает и не отклоняет, имя у них от правила
    for c in rule_nodes(root):
        ident = identity(c, ont)
        rec = {"kind": c["kind"], "name": c["name"], "sources": c["sources"], "names_seen": [c["name"]],
               "from": [f"rule:{c['rule']}"]}
        for f in NODE_FIELDS + ("stub",):
            if c.get(f):
                rec[f] = c[f]
        if ident in nodes:
            _merge_into(nodes[ident], rec)
        else:
            nodes[ident] = rec
    for d in drafts:
        for n in d["nodes"]:
            ident = identity(n, ont)
            if not n.get("key") and ont.merges_by_folder(n["kind"]) and ident in nodes and \
                    folder(d["file"]) not in {folder(s["file"]) for s in nodes[ident]["sources"]}:
                ident = f"{ident}@{folder(d['file'])}"
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
                                          "files": [_first_file(prev), d["file"]]})
                _merge_into(prev, rec)
            else:
                nodes[ident] = rec
        for x in d.get("not_nodes") or []:
            not_nodes.append(dict(x, file=d["file"]))
    return nodes, conflicts, not_nodes


def _first_file(n: Dict[str, Any]) -> str:
    """Первый файл-источник узла; у заглушки (stub) источников нет."""
    return n["sources"][0]["file"] if n.get("sources") else f"заглушка {n.get('key') or ''}".strip()


def survey_files(root: Path) -> List[str]:
    """Файлы текущей разведки — по очереди draft-file (кандидаты прошлых разведок вне scope не в счёт)."""
    p = Path(root) / ".spec-graph" / "queue" / "draft-file.json"
    items = json.loads(p.read_text(encoding="utf-8"))["items"] if p.is_file() else []
    return sorted({it["refs"]["file"] for it in items if (it.get("refs") or {}).get("file")})


def rule_nodes(root: Path) -> List[Dict[str, Any]]:
    """Кандидаты правил с auto: true — узлы каталога без модели. Источник — файл и раздел кандидата
    (без раздела — файл целиком, как у черновика); у заглушки (stub) источника нет: своего текста нет,
    и целью раскладки блоков она не становится."""
    from . import extract as X
    out = []
    for rel in survey_files(root):
        p = Path(root) / X.CANDIDATES_REL / (rel + ".json")
        if not p.is_file():
            continue
        for c in json.loads(p.read_text(encoding="utf-8"))["candidates"]:
            if not c.get("auto"):
                continue
            n = {"kind": c["kind"], "name": c["name_hint"], "rule": c["rule"],
                 "sources": [] if c.get("stub") else [{"file": rel, "sections": [c["section"]] if c.get("section") else []}]}
            for f in NODE_FIELDS + ("stub",):
                if c.get(f):
                    n[f] = c[f]
            out.append(n)
    return out


def _ref(nodes: Dict[str, Dict[str, Any]], ref: Any, strict: bool) -> Tuple[Optional[str], Optional[str]]:
    """(ключ узла, проблема). strict (решения нового формата): id `n-…` или имя ровно одного узла.
    Прежний формат: только имя, из одноимённых — последний вставленный (как было)."""
    if strict:
        for k in nodes:
            if node_id(k) == ref:
                return k, None
        # id, записанный до 2026-11-08: ключ узла тогда был без сервиса и папки
        old = [k for k, n in nodes.items() if node_id(legacy_identity(n)) == ref]
        if len(old) == 1:
            return old[0], None
        if old:
            desc = "; ".join(f"{node_id(k)} — {nodes[k]['name']}, {_first_file(nodes[k])}" for k in old)
            return None, f"id «{ref}» прежнего формата теперь у {len(old)} узлов ({desc}) — укажи новый id"
    hits = [k for k, n in nodes.items() if n["name"] == ref]
    if not hits:
        return None, f"узла «{ref}» нет в каталоге (сейчас) — имя или id `n-…` из catalog.json"
    if len(hits) > 1:
        desc = "; ".join(f"{node_id(k)} — {nodes[k]['kind']}, {_first_file(nodes[k])}" for k in hits)
        if strict:
            return None, f"имя «{ref}» у {len(hits)} узлов ({desc}) — укажи id нужного"
        return hits[-1], f"имя «{ref}» у {len(hits)} узлов — применено к последнему ({node_id(hits[-1])}): {desc}"
    return hits[0], None


def _apply(nodes: Dict[str, Dict[str, Any]], log: List[Dict[str, Any]], ont: O.Ontology,
           base: int = 0) -> List[Dict[str, Any]]:
    """Применить решения к узлам на месте. Ответ — проблемы [{n, action, ref, problem, strict}]: n — номер
    записи журнала; strict — решение нового формата не применилось (decide его не запишет). Итоговый
    scope — в nodes под ключом-маркером не кладётся: его возвращает `_scope(log)`."""
    problems: List[Dict[str, Any]] = []

    def note(n, a, ref, msg, strict):
        problems.append({"n": n, "action": a["action"], "ref": ref, "problem": msg, "strict": strict})

    for n, a in enumerate(log, base + 1):
        act, strict = a["action"], a.get("v", 1) >= DECISION_V

        def get(ref):
            k, msg = _ref(nodes, ref, strict)
            if msg:
                note(n, a, ref, msg, strict and k is None)
            return k

        if act == "merge":
            into = get(a["into"])
            for name in a["names"]:
                k = get(name)
                if into and k and k != into:
                    _merge_into(nodes[into], nodes.pop(k))
        elif act == "split":
            key = get(a["node"])
            if key is None:
                continue
            src = nodes[key]
            part = [x for x in src["sources"] if x["file"] == a["file"]]
            if not part:
                note(n, a, a["node"], f"у узла нет источника {a['file']}", strict)
                continue
            if len(part) == len(src["sources"]):
                note(n, a, a["node"], "split забирает у узла последний файл — это rename (action rename, node, name)",
                     strict)
                continue
            src["sources"] = [x for x in src["sources"] if x["file"] != a["file"]]
            kind = (a.get("kind") or src["kind"]) if strict else src["kind"]
            new = {k: v for k, v in src.items() if k in ("role", "subkind") and kind == src["kind"]}
            if src.get("service"):
                new["service"] = src["service"]
            new.update(kind=kind, name=a["name"], sources=part, names_seen=[a["name"]], **{"from": []})
            if strict and a.get("key"):
                new["key"] = a["key"]
            ident = identity(new, ont)
            if ident in nodes:
                # тот же вид и имя (или ключ) — это тот же узел: источник сливается в него. Прежде
                # узел перезаписывался, и его разделы пропадали из каталога (README kid_narochno).
                _merge_into(nodes[ident], new)
                note(n, a, a["node"], f"«{a['name']}» уже есть ({node_id(ident)}) — файл {a['file']} слит в него",
                     False)
            else:
                nodes[ident] = new
        elif act in ("rename", "move", "drop"):
            key = get(a["node"])
            if key is None:
                continue
            if act == "rename":
                nodes[key]["name"] = a["name"]
            elif act == "move":
                nodes[key]["service"] = a["service"]
            else:
                nodes.pop(key)
    return problems


def _scope(log: List[Dict[str, Any]]) -> Optional[List[str]]:
    scope = None
    for a in log:
        if a["action"] == "scope":
            scope = sorted(a["include"])
    return scope


def _suggest(same: List[Dict[str, Any]], ont: O.Ontology) -> List[str]:
    """Имена для одноимённых: по сервису или виду; не различают — по папке источника
    («Алгоритм обработки ([kid_seizure] Изъятие…)»)."""
    by_owner = [n.get("service") or ont.kinds[n["kind"]]["title_ru"].lower() for n in same]
    if len(set(by_owner)) == len(same):
        return [f"{n['name']} ({o})" for n, o in zip(same, by_owner)]
    def where(n):
        if not n["sources"]:                      # заглушка: файла нет — различает ключ
            return n.get("key") or "заглушка"
        f = n["sources"][0]["file"]
        return folder(f).rsplit("/", 1)[-1] or f
    return [f"{n['name']} ({where(n)})" for n in same]


def build(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ont = O.load(root)
    nodes, conflicts, not_nodes = _collect(root, ont)
    log = load_decisions(root)
    problems = _apply(nodes, log, ont)
    scope = _scope(log)
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
        # без учёта регистра: «Задача А» и «задача а» — один файл на macOS/Windows и одна
        # ссылка [[…]] в Obsidian (его ссылки регистронезависимы)
        names.setdefault(n["name"].casefold(), []).append(n)
    collisions = []
    for k, v in names.items():
        if len(v) < 2:
            continue
        c = {"name": v[0]["name"], "kinds": [n["kind"] for n in v], "ids": [n["id"] for n in v], "suggest": _suggest(v, ont)}
        if len({n["kind"] for n in v}) == 1 and len({n.get("service") for n in v}) == 1:
            # один вид, одно имя, разные ключи (код папки kid_x и код реестра kid_xe) — скорее один узел
            c["merge_hint"] = ("вид и имя совпадают" + (": ключи " + " / ".join(str(n.get("key")) for n in v)
                                                       if any(n.get("key") for n in v) else "") + " — вероятно, один узел: merge")
        collisions.append(c)
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
    FS.write_text(p, json.dumps(cat, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    # проблемы решений, сдвиг к прошлому каталогу и коды без узла — в отчёт и ответ, но не в catalog.json:
    # к его байтам привязано согласие
    cat = dict(cat, problems=[{k: v for k, v in x.items() if k != "strict"} for x in problems],
               drift=drift(root, cat, ont), missing_codes=missing_codes(root, cat))
    _write_md(root, cfg, cat, ont)
    return cat


DRIFT_RATIO = 0.8
DRIFT_MIN = 3
_DRIFT_NAMES = 12


def _baseline(root: Path, cat: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """С чем сравнивать: утверждённый снимок (если каталог уже не он), иначе — каталог последнего
    архива графа («граф заново»). Регрессия 34 → 1 бизнес-операции между двумя прогонами одной спеки
    прошла незамеченной: сравнивать было не с чем."""
    snap = Path(root) / APPROVED_REL
    if snap.is_file():
        prev = json.loads(snap.read_text(encoding="utf-8"))
        if prev.get("nodes") != cat["nodes"]:
            return APPROVED_REL, prev
        return None
    arch = Path(root) / ".spec-graph" / "archive"
    for d in sorted(arch.iterdir(), reverse=True) if arch.is_dir() else []:
        for name in ("catalog.approved.json", "catalog.json"):
            if (d / name).is_file():
                return (d / name).relative_to(root).as_posix(), json.loads((d / name).read_text(encoding="utf-8"))
    return None


def drift(root: Path, cat: Dict[str, Any], ont: O.Ontology) -> Optional[Dict[str, Any]]:
    """Сдвиг каталога к прошлому: счётчики по видам, пропавшие узлы; флаг — вид просел больше чем на
    20 % (и не меньше чем на 3 узла) или пропал узел с ключом. Флаг — пункт решения человека, а не отказ:
    сдвиг бывает и законным (поменяли scope, слили дубли)."""
    base = _baseline(root, cat)
    if base is None:
        return None
    rel, prev = base
    cur_ids = {(n["kind"], n.get("key") or norm_name(n["name"])) for n in cat["nodes"]}
    kinds: Dict[str, List[int]] = {}
    lost: Dict[str, List[str]] = {}
    for n in prev.get("nodes") or []:
        kinds.setdefault(n["kind"], [0, 0])[0] += 1
        if (n["kind"], n.get("key") or norm_name(n["name"])) not in cur_ids:
            lost.setdefault(n["kind"], []).append(n["name"])
    for n in cat["nodes"]:
        kinds.setdefault(n["kind"], [0, 0])[1] += 1
    flags = []
    for k, (old, new) in sorted(kinds.items(), key=lambda x: list(ont.kinds).index(x[0]) if x[0] in ont.kinds else 99):
        keyed = [x for x in lost.get(k, []) if ont.kinds.get(k, {}).get("key")]
        if (old - new >= DRIFT_MIN and new < old * DRIFT_RATIO) or keyed:
            title = ont.kinds[k]["title_ru"] if k in ont.kinds else k
            names = sorted(lost.get(k, []))
            flags.append(f"{title}: {old} → {new}" + (f", пропали: " + ", ".join(f"«{x}»" for x in names[:_DRIFT_NAMES])
                                                     + (f" … ещё {len(names) - _DRIFT_NAMES}" if len(names) > _DRIFT_NAMES else "")
                                                     if names else ""))
    return {"base": rel, "kinds": kinds, "lost": {k: sorted(v) for k, v in lost.items()}, "flags": flags}


def missing_codes(root: Path, cat: Dict[str, Any]) -> List[str]:
    """Ключи типов задач (task:…), которые правила нашли в спеке, а узла с таким ключом в каталоге нет
    (сняты решением или не дошли): сверка с перечнем, а не с памятью модели."""
    from . import extract as X
    have = {n.get("key") for n in cat["nodes"] if n.get("key")}
    found: Dict[str, str] = {}
    for rel in survey_files(root):
        p = Path(root) / X.CANDIDATES_REL / (rel + ".json")
        for c in json.loads(p.read_text(encoding="utf-8"))["candidates"] if p.is_file() else []:
            if str(c.get("key") or "").startswith("task:") and c["key"] not in have:
                found.setdefault(c["key"], c["name_hint"])
    return [f"{k} «{v}»" for k, v in sorted(found.items())]


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
             lambda r: f"- «{r['name']}»: предлагаю " + " / ".join(
                 f"{x} (`{i}`)" if i else x for x, i in zip(r["suggest"], r.get("ids") or [""] * len(r["suggest"])))
             + " — rename по id или merge, если это один узел" + (f" (**{r['merge_hint']}**)" if r.get("merge_hint") else "")),
            ("Кандидаты, признанные «не узлом»", cat["not_nodes"],
             lambda r: f"- «{r.get('candidate')}» ({r['file']}): {r.get('reason')}"
                       + (f" — предложен вид «{r['suggested_kind']}»" if r.get("suggested_kind") else ""))):
        lines += [f"## {title}", ""] + ([fmt(r) for r in rows] or ["- нет"]) + [""]
    d = cat.get("drift")
    if d:
        lines += ["## Сравнение с прошлым каталогом", "", f"База: `{d['base']}`.", "", "| вид | было | стало |", "|---|---|---|"]
        lines += [f"| {ont.kinds[k]['title_ru'] if k in ont.kinds else k} | {o} | {n} |" for k, (o, n) in d["kinds"].items()]
        lines += [""] + ([f"- **{f}** — это решение или регрессия?" for f in d["flags"]] or ["- просадок нет"]) + [""]
    if cat.get("missing_codes"):
        lines += ["## Коды типов задач без узла", ""] + [f"- {x}" for x in cat["missing_codes"]] + [""]
    if cat.get("problems"):
        lines += ["## Решения, применённые не так, как записаны", ""]
        lines += [f"- №{x['n']} {x['action']} «{x['ref']}»: {x['problem']}" for x in cat["problems"]] + [""]
    kinds_new = sorted({r["suggested_kind"] for r in cat["not_nodes"] if r.get("suggested_kind")})
    lines += ["## Кандидаты в новые виды", ""] + ([f"- {k}" for k in kinds_new] or ["- нет"]) + [""]
    p = Path(root) / cfg["vault"] / "_graph" / "catalog.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, "\n".join(lines), encoding="utf-8")


# ─── утверждение ───────────────────────────────────────────────────────────

def approval(root: Path) -> Dict[str, Any]:
    """Согласие на ТЕКУЩИЙ каталог; при совпадении — снимок catalog.approved.json."""
    st = CS.state(root, CONSENT, Path(root) / CATALOG_REL)
    if st["state"] == "ok":
        shutil.copyfile(Path(root) / CATALOG_REL, Path(root) / APPROVED_REL)
    return st


def collision_names(cat: Dict[str, Any]) -> List[str]:
    return [c["name"] for c in cat.get("collisions") or []]


def approved(root: Path) -> Tuple[bool, str]:
    """Утверждён ли снимок: согласие привязано к содержимому catalog.approved.json."""
    snap = Path(root) / APPROVED_REL
    if not snap.is_file() and (Path(root) / CATALOG_REL).is_file():
        # согласие записано (record_approval --bind-file catalog.json), а graph_catalog после него
        # не звали — снимок делается здесь же: раньше graph_assign_start отвечал «нет снимка» сразу
        # после записанного «да», и модель гадала, что ещё нужно
        approval(root)
    if not snap.is_file():
        return False, "каталог не утверждён: нет снимка (покажи _graph/catalog.md и спроси пользователя)"
    dup = collision_names(json.loads(snap.read_text(encoding="utf-8")))
    if dup:
        # имя узла — это имя заметки, ссылка [[…]] и id единицы link-note; одноимённые проходили
        # раскладку и нарезку, а падали только на graph_link_start (боевой прогон 2026-11-07)
        return False, ("в каталоге одинаковые имена у разных узлов: " + ", ".join(f"«{x}»" for x in dup)
                       + " — переименуй (graph_catalog_decide rename по id `n-…`), graph_catalog и утверди заново")
    st = CS.state(root, CONSENT, snap)
    if st["state"] != "ok":
        return False, f"согласие {st['key']}: {st['state']} — каталог утверждают заново"
    return True, ""


def load_approved(root: Path) -> Dict[str, Any]:
    return json.loads((Path(root) / APPROVED_REL).read_text(encoding="utf-8"))
