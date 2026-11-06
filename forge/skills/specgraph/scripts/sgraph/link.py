"""link.py — предложения рёбер моделью по одной заметке (задача 2.3, рычаг 2, D9, D14).

Связи, которые требуют понимания текста («шедулер формирует задачи укомплектования», «этот экран — UI
передачи упаковок», «закрывает задачи трёх типов»), предлагает модель — по ОДНОЙ заметке за вызов. Каждое
предложение идёт через ту же проверку, что и механические рёбра (`edges.Graph.check`): выдуманная связь не
проходит (ТЗ §8.5).

Очередь `link-note`: единица на заметку, у вида которой онтология разрешает исходящие рёбра (кроме
`belongs_to` — его ставит нарезка). Вход единицы готовит движок: имя, вид, сервис; допустимые рёбра вида с
видами целей; текст заметки дословно; подсказки резолвера (упомянутые в тексте узлы); бизнес-операции с
ключами; уже предложенные рёбра (механика) — не повторять.

Ответ (`put`): предложения {edge, to, quote, confidence, why?}. Недопустимая пара или цитата не из текста —
отказ всей единицы (модель чинит и сдаёт снова). Неоднозначная или несуществующая цель — НЕ отказ: такое
предложение не ставится, а уходит в вопросы `.spec-graph/questions.json` с кандидатами.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import catalog as CT
from . import edges as E
from . import keys as K
from . import queue as Q
from . import resolve as RS

BRIEF = "link-note.md"
BRIEFS = Path(__file__).resolve().parents[2] / "references" / "briefs"
TOOL = "graph_link_put"
QUESTIONS_REL = ".spec-graph/questions.json"
_SET_BY_SPLIT = ("belongs_to",)


class LinkError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def brief_chars() -> int:
    p = BRIEFS / BRIEF
    return len(p.read_text(encoding="utf-8")) if p.is_file() else 0


def unit_id(name: str, part: int = 0) -> str:
    return "link-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12] + (f"-p{part}" if part else "")


def _hints(g: E.Graph, name: str, text: str) -> List[str]:
    """Узлы, упомянутые в тексте: по ключу (METHOD /path, топик) и по точному имени/алиасу."""
    found = []
    for method, path in K.find_http(text):
        r = RS.resolve(g.index, f"{method} {path}", g.cfg)
        if r["status"] == "node" and r["node"] != name:
            found.append(r["node"])
    flat = E._ws(text).lower()
    for n in g.index:
        if n["name"] != name and any(len(x) >= 4 and E._ws(x).lower() in flat for x in [n["name"]] + n["aliases"]):
            found.append(n["name"])
    return sorted(set(found))


def _input(g: E.Graph, node: Dict[str, Any], text: str, ops: List[Dict[str, Any]], known: List[Dict[str, Any]],
           part: str) -> str:
    ont = g.ont
    edges = [e for e in ont.edges_from(node) if e not in _SET_BY_SPLIT]
    lines = [f"# Связи заметки «{node['name']}»{part}", "",
             f"Вид: {node['kind']}" + (f":{node['role']}" if node.get("role") else "")
             + (f" · сервис: {node['service']}" if node.get("service") else ""), "",
             "## Допустимые рёбра этой заметки", ""]
    for e in edges:
        to = ", ".join(ont.edges[e]["to"])
        lines.append(f"- `{e}` — {ont.edges[e]['title_ru']} → {to}")
    lines += ["", "## Бизнес-операции (цели creates_task / closes_task / ui_of / related)", ""]
    lines += [f"- «{o['name']}»" + (f" — `{o['key']}`" if o.get("key") else "") for o in ops] or ["- нет"]
    hints = _hints(g, node["name"], text)
    lines += ["", "## Узлы, упомянутые в тексте (подсказка резолвера)", ""]
    lines += [f"- «{h}» ({g.by_name[h]['kind']})" for h in hints] or ["- нет"]
    lines += ["", "## Уже предложено правилами (не повторяй)", ""]
    lines += [f"- {k['edge']} → «{k['to']}»" for k in known] or ["- нет"]
    lines += ["", "## Текст заметки (дословно; цитаты — только отсюда)", "", text.strip("\n"), "",
              f"Сдай рёбра инструментом `{TOOL}` с item_id этой единицы (рёбер нет — пустой список)."]
    return "\n".join(lines) + "\n"


def start(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    ok, why = CT.approved(root)
    if not ok:
        raise LinkError(why, "E-catalog-unapproved")
    g = E.Graph(root, cfg)
    if not g.index:
        raise LinkError("в vault нет заметок — сначала шаг graph-map (graph_split)", "E-link-empty")
    ops = [n for n in g.index if n["kind"] in g.ont.target_kinds("creates_task")]
    budget = Q.settings(cfg)["budget_chars"] - brief_chars()
    specs = []
    for n in sorted(g.index, key=lambda x: x["path"]):
        if not [e for e in g.ont.edges_from(n) if e not in _SET_BY_SPLIT]:
            continue
        text = g.text(n["name"])
        known = [p for p in E.load(root, n["name"]) if p["status"] != "rejected"]
        parts = [text]
        if len(_input(g, n, text, ops, known, "")) > budget:
            chunks, cur = [], ""
            for para in re.split(r"(\n\n+)", text):
                if cur and len(_input(g, n, cur + para, ops, known, " — часть 99")) > budget:
                    chunks.append(cur)
                    cur = ""
                cur += para
            parts = chunks + ([cur] if cur else [])
        for k, part in enumerate(parts, 1):
            single = len(parts) == 1
            specs.append({"id": unit_id(n["name"], 0 if single else k), "brief": BRIEF, "tool": TOOL,
                          "input": _input(g, n, part, ops, known, "" if single else f" — часть {k} из {len(parts)}"),
                          "refs": {"note": n["name"]}})
    counts = Q.build(root, "link-note", specs, cfg, {BRIEF: brief_chars()})
    return {"units": len(specs), "queue": counts, "next": "цикл очереди link-note: graph_next queue=link-note"}


def _unit(root: Path, item_id: str) -> Dict[str, Any]:
    data = json.loads((Path(root) / Q.QUEUE_REL / "link-note.json").read_text(encoding="utf-8"))
    return next(it for it in data["items"] if it["id"] == item_id)


def load_questions(root: Path) -> List[Dict[str, Any]]:
    p = Path(root) / QUESTIONS_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def put(root: Path, cfg: Dict[str, Any], item_id: str, proposals: List[Dict[str, Any]]
        ) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    note = _unit(root, item_id)["refs"]["note"]
    g = E.Graph(root, cfg)
    ok, questions, errs = [], [], []
    for i, p in enumerate(proposals):
        if not isinstance(p, dict) or not {"edge", "to", "quote"} <= set(p):
            errs.append(("E-edge-shape", f"proposals[{i}]: {{edge, to, quote, confidence, why?}}"))
            continue
        if p["edge"] in _SET_BY_SPLIT:
            errs.append(("E-edge-kind", f"proposals[{i}]: {p['edge']} ставит нарезка, не модель"))
            continue
        norm, err = g.check(note, dict(p, origin="model"))
        if norm:
            ok.append(norm)
        elif err[0] == "E-edge-target":
            questions.append({"note": note, "edge": p["edge"], "target": p["to"], "reason": err[1],
                              "quote": p.get("quote", "")})
        else:
            errs.append((err[0], f"proposals[{i}] {p['edge']} → «{p['to']}»: {err[1]}"))
    if errs:
        return {}, errs[:10]
    here, there = [], {}
    for n in ok:                      # симметричное ребро: одно основание на пару — копим у той стороны, где оно уже есть
        if g.ont.is_symmetric(n["edge"]) and any(x["edge"] == n["edge"] and x["to"] == note for x in E.load(root, n["to"])):
            there.setdefault(n["to"], []).append(dict(n, **{"from": n["to"], "to": note}))
        else:
            here.append(n)
    stats = E.add(root, note, here)
    for t, ps in sorted(there.items()):
        stats["merged"] += E.add(root, t, ps)["merged"]
    if questions:
        cur = load_questions(root)
        for q in questions:
            if q not in cur:
                cur.append(q)
        Path(root, QUESTIONS_REL).write_text(json.dumps(cur, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                             encoding="utf-8")
    return {"note": note, "accepted": len(ok), "questions": len(questions), **stats}, []
