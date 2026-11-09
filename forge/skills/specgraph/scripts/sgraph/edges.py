"""edges.py — предложения рёбер с основаниями: хранилище, проверка, механические правила (задачи 2.2, 2.3).

Ребро графа — не слово модели, а предложение, прошедшее механическую проверку (ТЗ §8.5 «связи только на
основании спецификации»):
  • ребро допустимо онтологией для концов (вид, роль, `same_service`);
  • цель найдена резолвером (2.1) — `ambiguous`/`ghost` не принимаются, а уходят вопросом с кандидатами;
  • цитата-основание (≥ 20 знаков, с нормализацией пробелов) ДОСЛОВНО есть в исходном тексте заметки-
    источника или заметки-цели (содержимое маркеров `graph:source*`) — выдуманная связь не проходит.
Одна проверка на всех: механические правила (2.2) и предложения модели (2.3) идут через неё.

Механические правила ставят то, что видно по форме текста: обработчик (`kafka-op:consume`) читает топик
из своего текста; фраза публикации «… в топик X» — `publishes_to`; `METHOD /path` эндпойнта ДРУГОГО
сервиса (у экрана — любого) — `calls`; точное имя или алиас узла данных — `uses_data`; таблица из SQL
текста заметки (FROM/JOIN/UPDATE/INSERT/DELETE, без CTE) — `uses_data` к узлу с ключом `table:<имя>`, в
`why` — чтение или запись; экран в папке `[код] Имя` — `ui_of` к операции `task:<код>` (структура спеки,
не слово модели; цитата — строка с кодом из текста экрана или операции). Строки разделов «См. также» не
дают рёбер: ТЗ §3 — взаимодействие, а не упоминание.

Хранилище — `.spec-graph/edges/<id заметки>.json`: предложения {from, edge, to, quote, origin
(rule:<id> | model | manual | improve), confidence, status proposed|accepted|rejected, why?}. Отклонённое
человеком повторно не предлагается.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import extract as X
from . import fm as F
from . import fsutil as FS
from . import keys as K
from . import ontology as O
from . import resolve as RS
from . import sqlref as SQ

EDGES_REL = ".spec-graph/edges"
QUOTE_MIN = 20
_PUBLISH = re.compile(r"(?:отправ|публику|направля|публикац|publish|send)\w*", re.I)


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def note_id(name: str) -> str:
    return "e-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


# ─── тексты заметок ────────────────────────────────────────────────────────

def source_files(root: Path, cfg: Dict[str, Any], node: Dict[str, Any]) -> List[str]:
    """Файлы-источники заметки — атрибут `file` маркеров graph:source*."""
    p = Path(root) / cfg["vault"] / node["path"]
    if not p.is_file():
        return []
    _, body = F.read(p.read_bytes().decode("utf-8"))
    return [b["attrs"].get("file") for b in F.blocks(body) if b["name"].startswith("source") and b["attrs"].get("file")]


def _cut(text: str, limit: int = 240) -> str:
    """Цитата из оператора: пробелы схлопнуты, длинное — по границе слова (подстрока _ws текста)."""
    t = _ws(text)
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0]


def source_text(root: Path, cfg: Dict[str, Any], node: Dict[str, Any]) -> str:
    """Исходный текст заметки — содержимое маркеров graph:source*."""
    p = Path(root) / cfg["vault"] / node["path"]
    if not p.is_file():
        return ""
    _, body = F.read(p.read_bytes().decode("utf-8"))
    return "\n".join(b["content"] for b in F.blocks(body) if b["name"].startswith("source"))


class Graph:
    """Индекс vault + тексты заметок + онтология: всё, что нужно проверке предложения."""

    def __init__(self, root: Path, cfg: Dict[str, Any]):
        self.root, self.cfg = Path(root), cfg
        self.ont = O.load(root)
        self.index = RS.vault_index(root, cfg)
        self.by_name = {n["name"]: n for n in self.index}
        self._texts: Dict[str, str] = {}

    def text(self, name: str) -> str:
        if name not in self._texts:
            node = self.by_name.get(name)
            self._texts[name] = source_text(self.root, self.cfg, node) if node else ""
        return self._texts[name]

    def check(self, src_name: str, p: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[str, str]]]:
        """(нормализованное предложение, None) или (None, (код, причина))."""
        src = self.by_name.get(src_name)
        if src is None:
            return None, ("E-edge-source", f"нет заметки «{src_name}»")
        edge = p.get("edge")
        if edge not in self.ont.edges:
            return None, ("E-edge-kind", f"ребро «{edge}» — одно из {', '.join(self.ont.edges)}")
        ends = self.ont.edges[edge]["to"]
        kinds = sorted({e.split(":")[0] for e in ends})
        target = p.get("to") or ""
        hit = self.by_name.get(target)
        if hit is None:
            res = RS.resolve(self.index, target, self.cfg, service=src.get("service"), kinds=kinds)
            if res["status"] != "node":
                why = {"ambiguous": f"неоднозначно: {', '.join(res.get('candidates', [])[:4])}",
                       "ghost": "нет такого узла" + (f" (опечатка? {', '.join(res['candidates'])})"
                                                     if res.get("hint") else "")}[res["status"]]
                return None, ("E-edge-target", f"цель «{target}»: {why}")
            hit = self.by_name[res["node"]]
        ok, why = self.ont.edge_allowed(edge, src, hit)
        if not ok:
            return None, ("E-edge-kind", why)
        quote = _ws(p.get("quote") or "")
        if len(quote) < QUOTE_MIN:
            return None, ("E-edge-quote", f"цитата короче {QUOTE_MIN} знаков")
        if quote not in _ws(self.text(src_name)) and quote not in _ws(self.text(hit["name"])):
            return None, ("E-edge-quote", "цитаты нет дословно в тексте заметок источника и цели")
        out = {"from": src_name, "edge": edge, "to": hit["name"], "quote": p["quote"],
               "origin": p.get("origin", "model"), "confidence": p.get("confidence", "medium"), "status": "proposed"}
        if p.get("why"):
            out["why"] = p["why"]
        return out, None


# ─── хранилище ─────────────────────────────────────────────────────────────

def _path(root: Path, name: str) -> Path:
    return Path(root) / EDGES_REL / f"{note_id(name)}.json"


def load(root: Path, name: str) -> List[Dict[str, Any]]:
    p = _path(root, name)
    return json.loads(p.read_text(encoding="utf-8"))["proposals"] if p.is_file() else []


def load_all(root: Path) -> List[Dict[str, Any]]:
    d = Path(root) / EDGES_REL
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        out += json.loads(p.read_text(encoding="utf-8"))["proposals"]
    return out


def save(root: Path, name: str, proposals: List[Dict[str, Any]]) -> None:
    p = _path(root, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    proposals = sorted(proposals, key=lambda x: (x["edge"], x["to"], x["origin"]))
    FS.write_text(p, json.dumps({"note": name, "proposals": proposals}, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                 encoding="utf-8")


def add(root: Path, name: str, new: List[Dict[str, Any]]) -> Dict[str, int]:
    """Слить новые предложения: тот же (edge, to) — одна запись, цитаты копятся в `quotes`;
    отклонённое человеком не воскрешается."""
    cur = load(root, name)
    idx = {(x["edge"], x["to"]): x for x in cur}
    added = merged = 0
    for p in new:
        k = (p["edge"], p["to"])
        if k in idx:
            prev = idx[k]
            qs = prev.setdefault("quotes", [prev["quote"]])
            if p["quote"] not in qs:
                qs.append(p["quote"])
            merged += 1
        else:
            idx[k] = p
            cur.append(p)
            added += 1
    save(root, name, cur)
    return {"added": added, "merged": merged}


# ─── механические правила ──────────────────────────────────────────────────

def _lines(text: str, stop: List[str]) -> List[str]:
    """Строки исходного текста вне разделов «См. также»."""
    out, skip, level = [], False, 0
    for line in text.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lv, title = len(m.group(1)), X.clean_title(m.group(2)).lower()
            if skip and lv <= level:
                skip = False
            if any(title.startswith(s) for s in stop):
                skip, level = True, lv
                continue
        if not skip:
            out.append(line)
    return out


def mechanical(g: Graph) -> Dict[str, List[Dict[str, Any]]]:
    """Предложения правил по всем заметкам: {имя заметки: [предложения]} (уже проверенные)."""
    rules = X.load_rules(g.cfg)
    topic_re = re.compile(rules["kafka_topic_re"])
    stop = rules["stop_headings"]
    data_nodes = [n for n in g.index if n["kind"] in g.ont.target_kinds("uses_data")]
    tables = {str(n.get("key"))[len("table:"):]: n for n in data_nodes if str(n.get("key") or "").startswith("table:")}
    ops_by_code = {str(n.get("key"))[len("task:"):].lower(): n for n in g.index
                   if n["kind"] in g.ont.target_kinds("ui_of") and str(n.get("key") or "").startswith("task:")}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for n in g.index:
        text = g.text(n["name"])
        if not text:
            continue
        props = []
        if g.ont.can_start("uses_data", n):
            by_table: Dict[str, List[Dict[str, str]]] = {}
            for ref in SQ.refs(text):
                if ref["table"].lower() in tables:
                    by_table.setdefault(ref["table"].lower(), []).append(ref)
            for low, refs in by_table.items():
                modes = {r["mode"] for r in refs}
                first = next((r for r in refs if r["mode"] == "write"), refs[0])   # цитата — запись, если есть
                props.append({"edge": "uses_data", "to": tables[low]["name"], "quote": _cut(first["statement"]),
                              "origin": "rule:R-edge-sql", "confidence": "high",
                              "why": "SQL: " + " и ".join(m for k, m in (("read", "чтение"), ("write", "запись")) if k in modes)})
        if g.ont.can_start("ui_of", n) and ops_by_code:
            for rel in source_files(g.root, g.cfg, n)[:1]:
                for fc in X.folder_codes(rel, rules):
                    op = ops_by_code.get(fc["code"].lower())
                    if op is None:
                        continue
                    rx = re.compile(r"(?<![\w])" + re.escape(fc["code"]) + r"(?![\w])", re.I)
                    quote = next((l.strip() for t in (text, g.text(op["name"])) for l in t.split("\n")
                                  if rx.search(l) and len(_ws(l)) >= QUOTE_MIN), None)
                    if quote:
                        props.append({"edge": "ui_of", "to": op["name"], "quote": quote, "origin": "rule:R-edge-folder",
                                      "confidence": "high", "why": f"экран в папке операции [{fc['code']}]"})
        for line in _lines(text, stop):
            q = line.strip()
            if len(_ws(q)) < QUOTE_MIN:
                continue
            for t in topic_re.findall(line):
                if g.ont.can_start("consumes", n):
                    props.append({"edge": "consumes", "to": t, "quote": q, "origin": "rule:R-edge-consume",
                                  "confidence": "high"})
                elif _PUBLISH.search(line) and g.ont.can_start("publishes_to", n):
                    props.append({"edge": "publishes_to", "to": t, "quote": q, "origin": "rule:R-edge-publish",
                                  "confidence": "high"})
            for method, path in K.find_http(line):
                res = RS.resolve(g.index, f"{method} {path}", g.cfg)
                if res["status"] != "node" or res["node"] == n["name"]:
                    continue
                target = g.by_name[res["node"]]
                own = n.get("service") and target.get("service") == n.get("service")
                props.append({"edge": "calls", "to": target["name"], "quote": q, "origin": "rule:R-edge-http",
                              "confidence": "medium" if own else "high"})
            for d in data_nodes:
                names = [d["name"]] + d["aliases"]
                if any(len(x) >= 8 and _ws(x) in _ws(line) for x in names):
                    props.append({"edge": "uses_data", "to": d["name"], "quote": q, "origin": "rule:R-edge-data",
                                  "confidence": "medium"})
        ok = []
        for p in props:
            norm, err = g.check(n["name"], p)
            if norm:
                ok.append(norm)
        if ok:
            out[n["name"]] = ok
    return out


def run_mechanical(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    g = Graph(root, cfg)
    res = mechanical(g)
    added = merged = 0
    for name, props in res.items():
        c = add(root, name, props)
        added, merged = added + c["added"], merged + c["merged"]
    by_edge: Dict[str, int] = {}
    for props in res.values():
        for p in props:
            by_edge[p["edge"]] = by_edge.get(p["edge"], 0) + 1
    return {"notes": len(res), "added": added, "merged": merged, "by_edge": dict(sorted(by_edge.items()))}
