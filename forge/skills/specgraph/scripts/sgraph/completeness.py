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
from . import ontology as O

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
        if "{name}" not in (r.get("question") or ""):
            errs.append(f"{rid}: question с {{name}}")
        if not isinstance(r.get("min", 1), int) or r.get("min", 1) < 1:
            errs.append(f"{rid}: min — целое ≥ 1")
    return errs


def _links(v: Any) -> List[str]:
    vals = v if isinstance(v, list) else [v]
    return [t for t in (F.link_target(x) for x in vals) if t]


def check(root: Path, nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Дыры по правилам. nodes — узлы валидатора ({kind, role, path, data})."""
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
            if n["kind"] != r["kind"] or (r.get("role") is not None and data.get("role") != r["role"]):
                continue
            if r.get("unless") and data.get(r["unless"]) is True:
                continue
            need = r.get("min", 1)
            if "empty_all" in r:
                hit = sum(len(_links(data.get(k))) for k in r["empty_all"]) < need
                what = " / ".join(r["empty_all"])
            else:
                short = [k for k in r["empty_any"] if len(_links(data.get(k))) < need]
                hit, what = bool(short), " / ".join(short)
            if not hit:
                continue
            ops = [name] if n["kind"] == group else sorted(
                t for v in data.values() for t in _links(v) if t in nodes and nodes[t]["kind"] == group)
            out.append({"code": r["id"], "path": n["path"], "msg": f"пусто: {what}", "name": name, "ops": ops,
                        "question": r["question"].format(name=name)})
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
        p.write_text(text, encoding="utf-8")
    return text
