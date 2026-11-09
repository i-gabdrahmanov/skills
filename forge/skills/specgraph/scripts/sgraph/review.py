"""review.py — отчёт связей и решения человека на шаге graph-link (задача 2.6, ТЗ §8.4–8.5, D13).

Одно решение человека на шаге — утвердить связи. Аналитику — не сотни строк подряд, а отчёт
`_graph/edges-review.md`, сгруппированный по смыслу:
  1. бизнес-операции: кто формирует, кто закрывает, UI, связанные операции, карточка;
  2. межсервисные вызовы и топики (кто кому и через что);
  3. сомнительные предложения модели (confidence low/medium) и «запускает» правила только по имени в тексте
     (R-edge-mention, medium: «в отличие от алгоритма X» — тоже упоминание) с цитатами;
  4. отклонённое: брошенные единицы очередей (коды проверок) и связи, отклонённые человеком;
  5. вопросы: неоднозначные цели, призраки, операции-заготовки (stub);
  6. узлы, добавленные раскладкой (`catalog.delta.json`), и решение по каждому;
  7. прочие связи — всё живое, чего нет в разделах 1–3 (по виду ребра: число и первые строки с цитатой):
     «да» утверждает и их, а до ревью 2026-11-08 их не было в отчёте вовсе (`invokes` модели с high,
     `uses_data` правил — 6 из 33 на фикстуре).

Отчёт читает vault после синка (frontmatter) и хранилище предложений. Статус proposed/accepted в отчёт не
попадает: согласие `graph-edges-<KEY>` привязано к хешу отчёта, и после «да» `review` переводит висящие
proposed в accepted — отчёт от этого не меняется, согласие остаётся действительным.

Решения: `edges_decide` (accept/reject по id из отчёта или тройке {from, edge, to}; отклонённое — в журнал и
`_graph/decisions.md`, больше не предлагается), `delta_decide` (accept | merge into | rename | drop по узлу
дельты: повторная нарезка, заметка старого имени снимается, предложения перенацеливаются; drop — блоки в
unplaced).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import assign as AS
from . import catalog as CT
from . import consent as CS
from . import edges as E
from . import fm as F
from . import fsutil as FS
from . import ontology as O
from . import queue as Q
from . import resolve as RS

CONSENT = "graph-edges"
REVIEW_NAME = "edges-review.md"
_QUOTE_SHOW = 220
_OTHER_SHOW = 10
_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


class ReviewError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def edge_id(x: Dict[str, Any]) -> str:
    return "r-" + hashlib.sha1(f"{x['from']}|{x['edge']}|{x['to']}".encode("utf-8")).hexdigest()[:8]


def review_path(root: Path, cfg: Dict[str, Any]) -> Path:
    return Path(root) / cfg["vault"] / "_graph" / REVIEW_NAME


def _stores(root: Path) -> Dict[str, List[Dict[str, Any]]]:
    d = Path(root) / E.EDGES_REL
    out = {}
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        data = json.loads(p.read_text(encoding="utf-8"))
        out[data["note"]] = data["proposals"]
    return out


def pending(root: Path) -> int:
    return sum(1 for ps in _stores(root).values() for x in ps if x["status"] == "proposed")


def undecided_delta(root: Path) -> List[str]:
    dec = AS.load_delta_decisions(root)
    return [n["name"] for n in AS.load_delta(root) if n["name"] not in dec]


# ─── отчёт ─────────────────────────────────────────────────────────────────

def _links(values: Any) -> List[str]:
    return sorted(t for t in (F.link_target(v) for v in (values or []) if isinstance(v, str)) if t)


def _list(names: List[str]) -> str:
    return ", ".join(F.link(n) for n in names) if names else "—"


def _quote(q: str) -> str:
    q = " ".join((q or "").split())
    return q if len(q) <= _QUOTE_SHOW else q[:_QUOTE_SHOW - 1] + "…"


def report(root: Path, cfg: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    root = Path(root)
    ont = O.load(root)
    vault = root / cfg["vault"]
    index = RS.vault_index(root, cfg)
    by_name = {n["name"]: n for n in index}
    notes = {}
    for n in index:
        notes[n["name"]] = F.read((vault / n["path"]).read_bytes().decode("utf-8"))
    stores = _stores(root)
    live = [x for ps in stores.values() for x in ps if x["status"] != "rejected"]
    ops = [n for n in index if ont.kinds[n["kind"]].get("key") == "task"]
    stubs = sum(1 for n in ops if notes[n["name"]][0].get("status") == "stub")
    origins: Dict[str, int] = {}
    for x in live:
        origins[x["origin"]] = origins.get(x["origin"], 0) + 1
    lines = ["# Отчёт связей", "",
             f"Связей: {len(live)} (" + ", ".join(f"{k} {v}" for k, v in sorted(origins.items())) + f"). "
             f"Бизнес-операций: {len(ops)}, из них заготовок: {stubs}.", "",
             "Решение пользователя — одно: утвердить связи. Правки: отклонить связь — по id `r-…` или по тройке "
             "«откуда — связь — куда»; по узлу из раздела 6 — принять, слить, переименовать или убрать.", ""]

    shown = set()                                   # (from, edge, to), названные в разделах 1–3
    lines += ["## 1. Бизнес-операции", ""]
    for n in sorted(ops, key=lambda x: x["name"]):
        data, body = notes[n["name"]]
        for edge in ("creates_task", "closes_task", "ui_of"):
            shown |= {(x, edge, n["name"]) for x in _links(data.get(ont.inverse_of(edge)))}
        shown |= {(n["name"], edge, x) for edge in ("related", "part_of") for x in _links(data.get(edge))}
        card = F.get_block(body, "card")
        state = ("заготовка — описания в спеке нет" if data.get("status") == "stub"
                 else "готова" if card else "нет")
        lines.append(f"**{n['name']}**" + (f" (`{n['key']}`)" if n.get("key") else ""))
        inv = ont.inverse_of
        lines += [f"- формирует — {_list(_links(data.get(inv('creates_task'))))}",
                  f"- закрывает — {_list(_links(data.get(inv('closes_task'))))}",
                  f"- UI — {_list(_links(data.get(inv('ui_of'))))}",
                  f"- связана с — {_list(_links(data.get('related')))}",
                  f"- входит в процесс — {_list(_links(data.get('part_of')))}",
                  f"- карточка — {state}", ""]
    if not ops:
        lines += ["_бизнес-операций нет_", ""]

    lines += ["## 2. Межсервисные связи", ""]
    cross = []
    for n in index:
        for t in _links(notes[n["name"]][0].get("calls")):
            tn = by_name.get(t)
            if tn and tn.get("service") != n.get("service"):
                shown.add((n["name"], "calls", t))
                where = tn.get("service") or ont.kinds[tn["kind"]]["title_ru"]
                cross.append(f"- {n.get('service') or ont.kinds[n['kind']]['title_ru']}: {F.link(n['name'])} → {where}: {F.link(t)}")
    lines += sorted(cross) or ["- вызовов между сервисами нет"]
    lines.append("")
    pub, con = ont.inverse_of("publishes_to"), ont.inverse_of("consumes")
    topics = [n for n in index if pub in ont.fm_keys(n["kind"]) or con in ont.fm_keys(n["kind"])]

    def side(data, key):
        return ", ".join(f"{F.link(s)} ({by_name[s].get('service') or '—'})" if s in by_name else F.link(s)
                         for s in _links(data.get(key))) or "—"
    for n in sorted(topics, key=lambda x: x["name"]):
        data = notes[n["name"]][0]
        shown |= {(s, "publishes_to", n["name"]) for s in _links(data.get(pub))}
        shown |= {(s, "consumes", n["name"]) for s in _links(data.get(con))}
        lines.append(f"- топик {F.link(n['name'])}: публикуют — {side(data, pub)}; читают — {side(data, con)}")
    if topics:
        lines.append("")

    lines += ["## 3. Сомнительные связи (уверенность модели low/medium; правило — только по имени в тексте)", ""]
    doubt = sorted((x for x in live if (x["origin"] == "model" and x.get("confidence") in ("low", "medium"))
                    or (x["origin"] == E.GUESS_ORIGIN and x.get("confidence") == E.GUESS_CONFIDENCE)),
                   key=lambda x: (x["from"], x["edge"], x["to"]))
    for x in doubt:
        lines.append(f"- `{edge_id(x)}` {F.link(x['from'])} — {ont.edges[x['edge']]['title_ru']} → {F.link(x['to'])} "
                     f"({x['confidence']}): «{_quote(x['quote'])}»" + (f" — {x['why']}" if x.get("why") else ""))
    lines += ([] if doubt else ["- нет"]) + [""]
    shown |= {(x["from"], x["edge"], x["to"]) for x in doubt}

    lines += ["## 4. Отклонённое", ""]
    rej = []
    for name in ("link-note", "card"):
        p = root / Q.QUEUE_REL / f"{name}.json"
        for it in json.loads(p.read_text(encoding="utf-8"))["items"] if p.is_file() else []:
            if it["status"] == "abandoned":
                rej.append(f"- единица {name} «{it.get('refs', {}).get('note', it['id'])}» брошена после "
                           f"{it['attempts']} попыток: {', '.join(it.get('last_codes') or []) or 'без кода'}")
    for ps in stores.values():
        for x in ps:
            if x["status"] == "rejected":
                rej.append(f"- {F.link(x['from'])} — {ont.edges[x['edge']]['title_ru']} → {F.link(x['to'])}: "
                           f"{x.get('rejected_why') or 'отклонено человеком'}")
    lines += sorted(rej) or ["- нет"]
    lines.append("")

    lines += ["## 5. Вопросы", ""]
    qs_p = root / ".spec-graph" / "questions.json"
    qs = json.loads(qs_p.read_text(encoding="utf-8")) if qs_p.is_file() else []
    lines += ["### Цели без узла — по числу ссылок", "",
              "Цель, на которую ссылаются многие заметки, — недостающий узел (в Obsidian так выглядит частая "
              "неразрешённая ссылка). Решение пользователя — graph_stubs_decide: создать заглушки для выбранных целей, "
              "связи к ним встанут из этих вопросов сами.", ""]
    groups = question_groups(qs)
    for g in groups:
        notes = ", ".join(F.link(x) for x in g["notes"][:_GROUP_NOTES])
        more = f" … ещё {len(g['notes']) - _GROUP_NOTES}" if len(g["notes"]) > _GROUP_NOTES else ""
        lines.append(f"- «{g['target']}» — {g['edge']}, {len(g['notes'])} заметок: {notes}{more} ({g['reason']})")
    lines += ([] if groups else ["- нет"]) + [""]
    cards = sorted({f"- {F.link(q['note'])}: {q['reason']}" for q in qs if q["edge"] == "card"})
    lines += ["### Карточки операций", ""] + (cards or ["- нет"]) + [""]

    lines += ["## 6. Узлы, добавленные раскладкой", ""]
    dec = AS.load_delta_decisions(root)
    delta = AS.load_delta(root)
    for n in sorted(delta, key=lambda x: x["name"]):
        d = dec.get(n["name"])
        what = ("**нет решения** — принять, слить с существующим, переименовать или убрать?" if not d else
                f"решение: {d['action']}" + (f" → «{d.get('into') or d.get('name')}»" if d.get("into") or d.get("name") else "")
                + f" ({d.get('why', '')})")
        lines.append(f"- «{n['name']}» ({ont.kinds[n['kind']]['title_ru']}" + (f", {n['service']}" if n.get("service") else "")
                     + f"): {n.get('why', '')} — {what}")
    lines += ([] if delta else ["- нет"]) + [""]

    other = [x for x in live if x["from"] in by_name and x["to"] in by_name
             and (x["from"], x["edge"], x["to"]) not in shown
             and not (ont.is_symmetric(x["edge"]) and (x["to"], x["edge"], x["from"]) in shown)]
    lines += ["## 7. Прочие связи", "",
              "Связей нет в разделах 1–3, а «да» утверждает и их. По каждому виду — число и первые "
              f"{_OTHER_SHOW} (сначала предложения модели, менее уверенные — выше); все — в таблицах оснований "
              "заметок. Отклонить — по id `r-…`.", ""]
    by_edge: Dict[str, List[Dict[str, Any]]] = {}
    for x in other:
        by_edge.setdefault(x["edge"], []).append(x)
    for edge in [e for e in ont.edges if e in by_edge]:
        xs = sorted(by_edge[edge], key=lambda x: (x["origin"] != "model", _CONF_RANK.get(x.get("confidence"), 1),
                                                   x["from"], x["to"]))
        lines += [f"### {ont.edges[edge]['title_ru']} (`{edge}`) — {len(xs)}", ""]
        lines += [f"- `{edge_id(x)}` {F.link(x['from'])} → {F.link(x['to'])} ({x['origin']}, {x.get('confidence') or '—'}): "
                  f"«{_quote(x['quote'])}»" + (f" — {x['why']}" if x.get("why") else "") for x in xs[:_OTHER_SHOW]]
        lines += ([f"- … ещё {len(xs) - _OTHER_SHOW}"] if len(xs) > _OTHER_SHOW else []) + [""]
    lines += ([] if other else ["- нет", ""])
    summary = {"edges": len(live), "doubtful": len(doubt), "other": len(other), "operations": len(ops), "stubs": stubs,
               "questions": len(qs), "delta": len(delta), "delta_undecided": len(undecided_delta(root))}
    return "\n".join(lines), summary


def review(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Пересобрать отчёт; согласие на ЭТОТ отчёт есть — висящие proposed становятся accepted."""
    text, summary = report(root, cfg)
    p = review_path(root, cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.is_file() or p.read_text(encoding="utf-8") != text:
        FS.write_text(p, text, encoding="utf-8")
    st = CS.state(root, CONSENT, p)
    rel = p.relative_to(Path(root)).as_posix()
    out = dict(summary, report=rel, consent=st["state"], key=st["key"])
    if st["state"] == "ok":
        accepted = 0
        for name, ps in _stores(root).items():
            hit = [x for x in ps if x["status"] == "proposed"]
            for x in hit:
                x["status"] = "accepted"
            if hit:
                E.save(root, name, ps)
                accepted += len(hit)
        out.update(accepted=accepted, next="гейт шага: graph_status → run.commands.gate, затем run.commands.close")
    elif summary["delta_undecided"]:
        # «Да» поверх нерешённого раздела 6 устаревает сразу: graph_delta_decide меняет отчёт и
        # перезапускает link/card (боевой прогон 2026-11-08 — согласие спросили, потом решали дельту)
        out["next"] = (f"сначала раздел 6 {rel}: {summary['delta_undecided']} узлов раскладки без решения — покажи их "
                       f"пользователю и спроси по каждому (принять / слить / переименовать / убрать); его решения — "
                       f"graph_delta_decide, затем шаги из её next и снова graph_edges_review. Согласие на связи — "
                       f"только после этого")
    else:
        out["next"] = (f"покажи пользователю {rel}: операции, межсервисные связи, сомнительное, вопросы, узлы раздела 6, прочие связи (раздел 7); "
                       f"спроси — утвердить связи или что поменять. Правки — graph_edges_decide / graph_delta_decide, "
                       f"затем graph_sync и снова graph_edges_review. «Да» — {st['how']}; затем снова graph_edges_review.")
    return out


# ─── решения человека ──────────────────────────────────────────────────────

_GROUP_NOTES = 6


def question_groups(qs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Вопросы-цели, сгруппированные по (ребро, цель): самые частые — первыми."""
    by: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for q in qs:
        if q["edge"] == "card":
            continue
        g = by.setdefault((q["edge"], q["target"]), {"edge": q["edge"], "target": q["target"], "notes": [],
                                                    "reason": q["reason"]})
        if q["note"] not in g["notes"]:
            g["notes"].append(q["note"])
    return sorted(by.values(), key=lambda g: (-len(g["notes"]), g["edge"], g["target"]))


def stubs_decide(root: Path, cfg: Dict[str, Any], targets: List[Any], why: str) -> Dict[str, Any]:
    """Решение пользователя по частым целям без узла: заглушка (узел без текста спеки) на каждую — узел
    дельты с решением accept, заметка со статусом stub; вопросы к этим целям перепроверяются и становятся
    предложениями связей (та же проверка цитаты, что у модели). Цель-неоднозначность заглушкой не лечится."""
    from . import split as SP
    if not (why or "").strip():
        raise ReviewError("решение — с причиной (why) со слов пользователя", "E-stub-why")
    ont = O.load(root)
    qs_p = Path(root) / ".spec-graph" / "questions.json"
    qs = json.loads(qs_p.read_text(encoding="utf-8")) if qs_p.is_file() else []
    groups = {g["target"]: g for g in question_groups(qs)}
    taken = {O.fold(n["name"]) for n in RS.vault_index(root, cfg)} | {O.fold(n["name"]) for n in AS.load_delta(root)}
    errs, recs = [], []
    for i, t in enumerate(targets or []):
        t = {"name": t} if isinstance(t, str) else t if isinstance(t, dict) else {}
        name = t.get("name") or t.get("target")
        g = groups.get(name)
        if g is None:
            errs.append(f"[{i}] «{name}» — не цель вопросов отчёта (раздел 5)")
            continue
        stub = (ont.edges.get(g["edge"]) or {}).get("stub") or {}      # вид заглушки по ребру — из онтологии
        kind, subkind = (t["kind"], t.get("subkind")) if t.get("kind") else (stub.get("kind"), stub.get("subkind"))
        if kind not in ont.kinds:
            errs.append(f"[{i}] «{name}»: вид заглушки — укажи kind ({', '.join(ont.target_kinds(g['edge']))})")
            continue
        bad = ont.check_name(kind, name)
        if bad or O.fold(name) in taken or "неоднозначно" in g["reason"]:
            errs.append(f"[{i}] «{name}»: " + ("; ".join(bad) or ("неоднозначная цель — узел есть, выбери его в связи"
                                                               if "неоднозначно" in g["reason"] else "имя занято")))
            continue
        rec = {"kind": kind, "name": name, "why": why.strip(), "sources": [], "proposed_by": "human", "stub": True}
        if subkind:
            rec["subkind"] = subkind
        if stub.get("key") and stub.get("kind") == rec["kind"] and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
            rec["key"] = f"{stub['key']}:{name.lower()}"
        rec["id"] = CT.node_id(CT.identity(rec, ont))
        recs.append(rec)
    if errs or not recs:
        raise ReviewError("; ".join(errs[:6]) or "targets пусто", "E-stub-target")
    delta = AS.load_delta(root) + recs
    FS.write_text(Path(root) / AS.DELTA_REL, json.dumps(delta, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                  encoding="utf-8")
    dec = AS.load_delta_decisions(root)
    for r in recs:
        dec[r["name"]] = {"action": "accept", "why": f"заглушка: {why.strip()}"}
    FS.write_text(Path(root) / AS.DELTA_DECISIONS_REL, json.dumps(dec, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                  encoding="utf-8")
    SP.split(root, cfg)
    g = E.Graph(root, cfg)
    names = {r["name"] for r in recs}
    linked, left = 0, []
    for q in qs:
        if q["edge"] != "card" and q["target"] in names:
            norm, err = g.check(q["note"], {"edge": q["edge"], "to": q["target"], "quote": q.get("quote", ""),
                                            "origin": "model", "confidence": "medium"})
            if norm:
                E.add(root, q["note"], [norm])
                linked += 1
                continue
            q = dict(q, reason=f"после заглушки: {err[1]}")
        left.append(q)
    FS.write_text(qs_p, json.dumps(left, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    CT.write_decisions_md(root, cfg["vault"])
    return {"stubs": sorted(names), "linked": linked, "questions_left": len(left),
            "next": "graph_sync, затем graph_edges_review — и снова вопрос пользователю по обновлённому отчёту"}


def _find(stores, item) -> Optional[Tuple[str, Dict[str, Any]]]:
    for name, ps in stores.items():
        for x in ps:
            if (isinstance(item, str) and edge_id(x) == item) or (
                    isinstance(item, dict) and all(x.get(k) == item.get(k) for k in ("from", "edge", "to"))):
                return name, x
    return None


def edges_decide(root: Path, cfg: Dict[str, Any], accept: List[Any], reject: List[Any], why: str) -> Dict[str, Any]:
    if reject and not (why or "").strip():
        raise ReviewError("отклонение — с причиной (why): она идёт в _graph/decisions.md", "E-decide-why")
    stores = _stores(root)
    errs, hits = [], []
    for verb, items in (("accept", accept or []), ("reject", reject or [])):
        for i, item in enumerate(items):
            f = _find(stores, item)
            if f is None:
                errs.append(f"{verb}[{i}]: нет связи {item!r} — id `r-…` из отчёта или {{from, edge, to}}")
            else:
                hits.append((verb, f))
    if errs:
        raise ReviewError("; ".join(errs[:6]), "E-decide-unknown")
    log_p = Path(root) / CT.EDGE_DECISIONS_REL
    log = json.loads(log_p.read_text(encoding="utf-8")) if log_p.is_file() else []
    touched = set()
    for verb, (name, x) in hits:
        touched.add(name)
        rec = {"from": x["from"], "edge": x["edge"], "to": x["to"]}
        if verb == "accept":
            x["status"] = "accepted"
            x.pop("rejected_why", None)
            log = [e for e in log if {k: e[k] for k in rec} != rec]
        else:
            x["status"], x["rejected_why"] = "rejected", why.strip()
            if not any({k: e[k] for k in rec} == rec for e in log):
                log.append(dict(rec, why=why.strip()))
    for name in touched:
        E.save(root, name, stores[name])
    log_p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(log_p, json.dumps(log, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    CT.write_decisions_md(root, cfg["vault"])
    return {"accepted": sum(1 for v, _ in hits if v == "accept"), "rejected": sum(1 for v, _ in hits if v == "reject"),
            "next": "graph_sync, затем graph_edges_review — и снова вопрос пользователю по обновлённому отчёту"}


def _retarget(root: Path, cfg: Dict[str, Any], remap: Dict[str, Optional[str]]) -> int:
    """Предложения со старым именем узла дельты: перенос на новое имя (merge/rename) или rejected (drop).
    Перенесённая связь перепроверяется онтологией: после слияния с узлом другого вида она может стать
    недопустимой — тогда rejected с причиной (в отчёт, раздел 4)."""
    ont = O.load(root)
    by_name = {n["name"]: n for n in RS.vault_index(root, cfg)}
    changed = 0
    for old, new in remap.items():
        own = E.load(root, old)
        if own:
            E._path(root, old).unlink()
            if new:
                E.add(root, new, [dict(x, **{"from": new}) for x in own if x["to"] != new])
            changed += len(own)
    for name, ps in _stores(root).items():
        hit = False
        for x in ps:
            if x["to"] in remap and x["status"] != "rejected":
                new = remap[x["to"]]
                if new is None or new == x["from"]:
                    x["status"], x["rejected_why"] = "rejected", f"узел «{x['to']}» убран решением по дельте"
                else:
                    x["to"] = new
                    ok, why = (ont.edge_allowed(x["edge"], by_name[x["from"]], by_name[new])
                               if x["from"] in by_name and new in by_name else (False, "узла нет в vault"))
                    if not ok:
                        x["status"], x["rejected_why"] = "rejected", f"после решения по дельте: {why}"
                hit = True
                changed += 1
        if hit:
            merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
            for x in ps:
                k = (x["edge"], x["to"], x["status"] == "rejected")
                if k in merged:
                    qs = merged[k].setdefault("quotes", [merged[k]["quote"]])
                    if x["quote"] not in qs:
                        qs.append(x["quote"])
                else:
                    merged[k] = x
            E.save(root, name, list(merged.values()))
    return changed


def delta_decide(root: Path, cfg: Dict[str, Any], decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    from . import split as SP
    ont = O.load(root)
    delta = {n["name"]: n for n in AS.load_delta(root)}
    if not delta:
        raise ReviewError("узлов дельты нет — решать нечего", "E-delta-none")
    approved = {n["name"] for n in CT.load_approved(root)["nodes"] if n.get("in_scope", True)}
    errs = []
    for i, d in enumerate(decisions or []):
        node, act = d.get("node"), d.get("action")
        if node not in delta:
            errs.append(f"[{i}] «{node}» — не узел дельты (есть: {', '.join(sorted(delta)[:5])})")
        elif act not in AS.DELTA_ACTIONS:
            errs.append(f"[{i}] action — одно из {', '.join(AS.DELTA_ACTIONS)}")
        elif not (d.get("why") or "").strip():
            errs.append(f"[{i}] решение — с причиной (why)")
        elif act == "merge" and (d.get("into") not in approved | set(delta) or d.get("into") == node):
            errs.append(f"[{i}] merge: into — существующий узел каталога, не сам «{node}»")
        elif act == "rename":
            bad = ont.check_name(delta[node]["kind"], d.get("name"), delta[node].get("service"))
            if bad or d.get("name") in approved | set(delta):
                errs.append(f"[{i}] rename: имя «{d.get('name')}» — {'; '.join(bad) or 'уже занято'}")
    if errs or not decisions:
        raise ReviewError("; ".join(errs[:6]) or "decisions пусто", "E-delta-decision")
    dec = AS.load_delta_decisions(root)
    for d in decisions:
        dec[d["node"]] = {k: d[k] for k in ("action", "into", "name", "why") if d.get(k)}
    p = Path(root) / AS.DELTA_DECISIONS_REL
    FS.write_text(p, json.dumps(dec, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _, remap = AS.delta_resolution(root)
    vault = Path(root) / cfg["vault"]
    for old in remap:
        n = delta[old]
        note = vault / ont.path_for(n["kind"], old, n.get("service"), n.get("subkind"))
        if note.is_file():
            note.unlink()                         # заметку старого имени снимает движок; блоки — новому владельцу
    res = SP.split(root, cfg)
    moved = _retarget(root, cfg, remap)
    CT.write_decisions_md(root, cfg["vault"])
    return {"decided": len(decisions), "undecided": undecided_delta(root), "split_written": res["written"],
            "unplaced": res["unplaced"], "proposals_retargeted": moved,
            "next": "graph_edges_mechanical; graph_link_start и graph_card_start (заново — только затронутые единицы) с "
                    "циклами очередей; graph_sync; graph_edges_review"}
