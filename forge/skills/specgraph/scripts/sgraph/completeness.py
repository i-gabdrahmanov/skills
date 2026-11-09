"""completeness.py — проверки полноты графа по ТЗ (задача 3.2, ТЗ §4–§6, §10).

Ноль ошибок валидатора значит только, что существующие ссылки целы. У операции может не оказаться
шедулера закрытия — её задачи никогда не закрываются, а граф формально чист. Здесь ищутся такие дыры:
правила — данными в `references/completeness.json` (вид и роль узла, ключи frontmatter, условие, порог,
severity, вопрос аналитику, флаг-исключение). Кода под конкретный вид нет.

Дыры идут в отчёт валидатора (раздел «Дыры графа», сгруппирован по бизнес-операциям) и вопросами в
`_graph/open-questions.md` — вместе с вопросами шага link (неоднозначные цели, призраки, операции без
описания). Файл вопросов пересобирается целиком при каждой проверке, поэтому дублей нет по построению.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from . import fm as F
from . import fsutil as FS
from . import ontology as O
from . import process as PR

RULES = Path(__file__).resolve().parents[2] / "references" / "completeness.json"
QUESTIONS_NAME = "open-questions.md"
LINK_QUESTIONS_REL = ".spec-graph/questions.json"


def load() -> Dict[str, Any]:
    return json.loads(RULES.read_text(encoding="utf-8"))


def self_check(rules: Dict[str, Any], ont: O.Ontology) -> List[str]:
    """Ошибки правил относительно онтологии (пусто — согласованы)."""
    errs = []
    if rules.get("group_by") not in ont.kinds:
        errs.append(f"group_by «{rules.get('group_by')}» — не вид онтологии")
    ids = set()
    for r in rules.get("rules") or []:
        rid = r.get("id", "?")
        if rid in ids:
            errs.append(f"{rid}: id повторяется")
        ids.add(rid)
        if not rid.startswith(("E-", "W-")) or {"E-": "error", "W-": "warning"}[rid[:2]] != r.get("severity"):
            errs.append(f"{rid}: префикс E-/W- и severity error/warning согласованы")
        if "{name}" not in (r.get("question") or ""):
            errs.append(f"{rid}: question с {{name}}")
        if r.get("check") is not None:
            if r["check"] not in PR.CHECKS:
                errs.append(f"{rid}: проверки «{r['check']}» нет ({', '.join(PR.CHECKS)})")
            if r.get("kind") or r.get("empty_all") or r.get("empty_any"):
                errs.append(f"{rid}: у правила с check нет kind / empty_all / empty_any — оно про процесс, не про узел")
            continue
        if r.get("kind") not in ont.kinds:
            errs.append(f"{rid}: kind «{r.get('kind')}» — не вид онтологии")
            continue
        roles = ont.kinds[r["kind"]].get("roles") or []
        if r.get("role") is not None and r["role"] not in roles:
            errs.append(f"{rid}: роли «{r['role']}» у вида нет")
            continue
        keys = r.get("empty_all") or r.get("empty_any") or []
        if not keys or ("empty_all" in r) == ("empty_any" in r):
            errs.append(f"{rid}: ровно одно из empty_all / empty_any, непустое")
        allowed = set(ont.fm_keys(r["kind"], r.get("role")))
        for k in keys:
            if k not in allowed:
                errs.append(f"{rid}: ключа «{k}» нет у вида {r['kind']}")
        for t in r.get("targets") or []:
            if t.get("kind") not in ont.kinds:
                errs.append(f"{rid}: targets — «{t.get('kind')}» не вид онтологии")
            elif t.get("role") is not None and t["role"] not in (ont.kinds[t["kind"]].get("roles") or []):
                errs.append(f"{rid}: targets — роли «{t['role']}» у вида {t['kind']} нет")
        if not isinstance(r.get("min", 1), int) or r.get("min", 1) < 1:
            errs.append(f"{rid}: min — целое ≥ 1")
    return errs


def _links(v: Any) -> List[str]:
    vals = v if isinstance(v, list) else [v]
    return [t for t in (F.link_target(x) for x in vals) if t]


def _counted(r: Dict[str, Any], links: List[str], nodes: Dict[str, Dict[str, Any]]) -> List[str]:
    """Ссылки, которые правило считает: все или только на узлы видов `targets` (и их ролей)."""
    want = r.get("targets")
    if not want:
        return links
    return [t for t in links if t in nodes and any(
        nodes[t]["kind"] == w["kind"] and w.get("role") in (None, nodes[t].get("role")) for w in want)]


def _ops(name: str, nodes: Dict[str, Dict[str, Any]], group: str) -> List[str]:
    n = nodes[name]
    return [name] if n["kind"] == group else sorted(
        t for v in n["data"].values() for t in _links(v) if t in nodes and nodes[t]["kind"] == group)


def check(root: Path, nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Дыры по правилам. nodes — узлы валидатора ({kind, role, path, data, body})."""
    ont = O.load(root)
    rules = load()
    errs = self_check(rules, ont)
    if errs:
        raise O.OntologyError("правила полноты: " + "; ".join(errs[:5]))
    group = rules["group_by"]
    out = []
    for name in sorted(nodes):
        n = nodes[name]
        data = n["data"]
        for r in rules["rules"]:
            if r.get("check") or n["kind"] != r["kind"] or (r.get("role") is not None and data.get("role") != r["role"]):
                continue
            if r.get("unless") and data.get(r["unless"]) is True:
                continue
            need = r.get("min", 1)
            if "empty_all" in r:
                hit = sum(len(_counted(r, _links(data.get(k)), nodes)) for k in r["empty_all"]) < need
                what = " / ".join(r["empty_all"])
            else:
                short = [k for k in r["empty_any"] if len(_counted(r, _links(data.get(k)), nodes)) < need]
                hit, what = bool(short), " / ".join(short)
            if not hit:
                continue
            if r.get("targets"):
                what += " (" + ", ".join(t["kind"] + (f":{t['role']}" if t.get("role") else "") for t in r["targets"]) + ")"
            out.append({"code": r["id"], "path": n["path"], "msg": f"пусто: {what}", "name": name,
                        "ops": _ops(name, nodes, group), "question": r["question"].format(name=name)})
    for h in PR.check(ont, nodes, rules["rules"]):
        if h["name"] in nodes:
            out.append(dict(h, path=nodes[h["name"]]["path"], ops=_ops(h["name"], nodes, group)))
    return out


def questions_path(root: Path, cfg: Dict[str, Any]) -> Path:
    return Path(root) / cfg["vault"] / "_graph" / QUESTIONS_NAME


def write_questions(root: Path, cfg: Dict[str, Any], holes: List[Dict[str, Any]], write: bool = True) -> str:
    """_graph/open-questions.md: вопросы шага link (цели, призраки, описания) и дыры полноты; текст файла."""
    lines = ["# Открытые вопросы", "", "Вопросы аналитику, которые граф не может решить по спеке. Ответ — правка спеки "
             "или свойства заметки в Obsidian; следующая проверка уберёт закрытый вопрос сама.", ""]
    qp = Path(root) / LINK_QUESTIONS_REL
    link_qs = json.loads(qp.read_text(encoding="utf-8")) if qp.is_file() else []
    desc = sorted({f"- {F.link(q['note'])}: {q['reason']}" for q in link_qs if q.get("edge") == "card"})
    targets = sorted({f"- {F.link(q['note'])} — {q['edge']} → «{q['target']}»: {q['reason']}"
                      for q in link_qs if q.get("edge") != "card"})
    gaps = sorted({f"- {F.link(h['name'])} ({h['code']}): {h['question']}" for h in holes})
    for title, rows in (("Дыры графа", gaps), ("Операции без описания", desc), ("Связи: неясная цель", targets)):
        lines += [f"## {title}", ""] + (rows or ["- нет"]) + [""]
    p = questions_path(root, cfg)
    text = "\n".join(lines)
    if write and (not p.is_file() or p.read_text(encoding="utf-8") != text):
        p.parent.mkdir(parents=True, exist_ok=True)
        FS.write_text(p, text, encoding="utf-8")
    return text
