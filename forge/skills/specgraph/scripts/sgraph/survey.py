"""survey.py — разведка: обзор спеки и черновой каталог по оглавлению (задача 1.3, D24).

До дорогой нарезки надо понять, что лежит в спеке и сколько это работы. Решить, какие узлы есть, можно
по оглавлению и кандидатам экстракторов — без текста: дёшево, и имена общих узлов выбираются один раз на
весь корпус (иначе один шедулер закрытия рождается в разных секциях под разными именами).

`survey` (механика): скан (1.1), экстракторы (1.2) → `_graph/survey.md` для человека (живёт в окружении,
наружу не выносится): по файлу — сервис, размер, оглавление, кандидаты; аномалии; объём работ (единицы
очередей и вызовы субагентов); предложение scope. Плюс очередь `draft-file`: единица на файл (крупный —
частями по разделам верхнего уровня). Вход единицы — оглавление с id разделов и размерами, кандидаты
файла и имена кандидатов всего корпуса (для повторного использования), текста разделов НЕТ.

`put_draft` — ответ модели по единице: узлы файла и кандидаты «не узел» с причиной. Проверки строгие и
короткие (коды E-draft-*), ответ сохраняется в `.spec-graph/drafts/<id единицы>.json`.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import blocks as B
from . import extract as X
from . import ontology as O
from . import queue as Q

DRAFTS_REL = ".spec-graph/drafts"
BRIEF = "draft-file.md"
BRIEFS = Path(__file__).resolve().parents[2] / "references" / "briefs"
TOOL = "graph_draft_put"
_LONG_SECTION_CHARS = 12000
_BAD_TEXT = re.compile(r"[\\\x00-\x08\x0b\x0c\x0e-\x1f]")


def unit_id(rel: str, part: int = 0) -> str:
    h = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
    return f"draft-{h}" + (f"-p{part}" if part else "")


def brief_chars(name: str = BRIEF) -> int:
    p = BRIEFS / name
    return len(p.read_text(encoding="utf-8")) if p.is_file() else 0


def _section_rows(blocks, secs) -> List[Dict[str, Any]]:
    rows = []
    for s in secs:
        head = blocks[s["head"]]
        size = sum(len(b["text"]) for b in blocks[s["start"]:s["end"]])
        own = sum(1 for _ in blocks[s["start"]:s["own_end"]])
        rows.append({"id": head["id"], "level": s["level"], "title": s["title"], "chars": size, "own_blocks": own,
                     "start": s["start"], "end": s["end"]})
    return rows


def _input_text(rel: str, svc: Optional[str], rows: List[Dict[str, Any]], cands: List[Dict[str, Any]],
                known: List[str], part: str = "") -> str:
    lines = [f"# Разведка файла {rel}{part}", "", f"Каталог файла (возможный сервис-владелец): {svc or 'нет'}", "",
             "## Оглавление (id раздела — уровень — заголовок — размер)", ""]
    top = min((r["level"] for r in rows), default=1)
    for r in rows:
        lines.append(f"{'  ' * (r['level'] - top)}- `{r['id']}` H{r['level']} {r['title']} — {r['chars']} знаков")
    lines += ["", "## Кандидаты экстракторов в этом файле", ""]
    if not cands:
        lines.append("- нет")
    for c in cands:
        kind = c["kind"] + (f":{c['role']}" if c.get("role") else "") + (f":{c['subkind']}" if c.get("subkind") else "")
        key = f" ключ `{c['key']}`" if c.get("key") else ""
        sec = f" раздел `{c['section']}`" if c.get("section") else ""
        lines.append(f"- {kind} — «{c['name_hint']}»{key}{sec} (правило {c['rule']})")
    lines += ["", "## Имена кандидатов всего корпуса (бери их, если узел тот же)", ""]
    lines += [f"- {n}" for n in known] or ["- нет"]
    lines += ["", f"Сдай результат инструментом `{TOOL}` с item_id этой единицы."]
    return "\n".join(lines) + "\n"


_KNOWN_MAX = 60


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[^\W\d_]{3,}", s.lower())}


def _similar(names: List[str], probes: List[str], limit: int = _KNOWN_MAX) -> List[str]:
    """Имена корпуса, ближайшие к кандидатам единицы (по общим словам): общий узел назовут так же,
    а список не раздувает вход на большом корпусе."""
    pt = [_tokens(p) for p in probes]
    scored = []
    for n in names:
        nt = _tokens(n)
        best = max((len(nt & t) / max(1, len(nt | t)) for t in pt), default=0.0)
        if best > 0:
            scored.append((-best, n))
    return [n for _, n in sorted(scored)[:limit]]


def _chunks(rows, cands, room: int, rel: str, svc, others) -> List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """Жадная нарезка оглавления по бюджету: разделы по порядку, кандидаты — к своему разделу,
    кандидаты без раздела — в первую часть. Запас под шапку и список имён корпуса учтён."""
    by_sec: Dict[str, List[Dict[str, Any]]] = {}
    loose = []
    for c in cands:
        (by_sec.setdefault(c["section"], []) if c.get("section") else loose).append(c)
    overhead = len(_input_text(rel, svc, [], loose, others[:_KNOWN_MAX], part=" — часть 99"))
    out: List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = []
    cur, cc, size = [], list(loose), overhead
    for r in rows:
        cost = len(r["title"]) + 80 + sum(len(c["name_hint"]) + 120 for c in by_sec.get(r["id"], []))
        if cur and size + cost > room:
            out.append((cur, cc))
            cur, cc, size = [], [], overhead
        cur.append(r)
        cc += by_sec.get(r["id"], [])
        size += cost
    if cur or cc or not out:
        out.append((cur, cc))
    return out


def survey(root: Path, cfg: Dict[str, Any], scope: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root)
    ont = O.load(root)
    rules = X.load_rules(cfg)
    scan = B.scan(root, cfg, scope, limit=10 ** 6)
    files = [s["file"] for s in scan["summary"]]
    per_file, all_cands = [], []
    for rel in files:
        data = B.load(root, rel)
        res = X.extract_file(root, rel, cfg, rules)
        secs = X.sections(data["blocks"])
        per_file.append({"file": rel, "service": res["service"], "data": data, "secs": secs,
                         "rows": _section_rows(data["blocks"], secs), "cands": res["candidates"]})
        all_cands += res["candidates"]
    budget = Q.settings(cfg)["budget_chars"]
    bchars = brief_chars()
    specs, anomalies = [], []
    shas = Counter(pf["data"]["sha1"] for pf in per_file)
    for pf in per_file:
        rel, rows, cands = pf["file"], pf["rows"], pf["cands"]
        others = sorted({c["name_hint"] for c in all_cands if c not in cands} - {c["name_hint"] for c in cands})
        for k, (chunk, cc) in enumerate(_chunks(rows, cands, budget - bchars, rel, pf["service"], others), 1):
            known = _similar(others, [c["name_hint"] for c in cc] + [r["title"] for r in chunk])
            single = len(_chunks(rows, cands, budget - bchars, rel, pf["service"], others)) == 1
            text = _input_text(rel, pf["service"], chunk, cc, known, part="" if single else f" — часть {k}")
            specs.append({"id": unit_id(rel, 0 if single else k), "brief": BRIEF, "tool": TOOL, "input": text,
                          "refs": {"file": rel, "sections": [r["id"] for r in chunk],
                                   "candidates": [c["name_hint"] for c in cc]}})
        for w in pf["data"]["warnings"]:
            anomalies.append(f"{rel}: {w['code']} (строка {w['line']})")
        if shas[pf["data"]["sha1"]] > 1:
            anomalies.append(f"{rel}: файл побайтно совпадает с другим исходником")
        for r in rows:
            if r["chars"] > _LONG_SECTION_CHARS and r["own_blocks"] > 1:
                anomalies.append(f"{rel}: длинный раздел «{r['title']}» — {r['chars']} знаков")
    excluded = sorted(set(_all_md(root, cfg)) - set(B.source_files(root, cfg)))
    counts = Q.build(root, "draft-file", specs, cfg, {BRIEF: bchars})
    owners = Counter()
    est_nodes = {(c["kind"], c.get("key") or c["name_hint"]) for c in all_cands}
    est_ops = {k for k in est_nodes if ont.kinds[k[0]].get("key") == "task"}
    est_sections = sum(max(1, sum(1 for r in pf["rows"] if r["own_blocks"] > 1)) for pf in per_file)
    for c in all_cands:
        if c.get("service"):
            owners[c["service"]] += 1
    work = {"draft-file": len(specs), "assign-section": est_sections, "link-note": len(est_nodes), "card": len(est_ops)}
    work["subagent_calls"] = sum(work.values())
    scope_hint = [s for s, _ in owners.most_common(3)]
    md = _survey_md(per_file, anomalies, excluded, work, scope_hint)
    vault = Path(root) / cfg["vault"] / "_graph"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "survey.md").write_text(md, encoding="utf-8")
    return {"files": len(per_file), "candidates": len(all_cands), "work": work, "anomalies": anomalies[:15],
            "excluded": excluded[:10], "scope_hint": scope_hint, "survey": f"{cfg['vault']}/_graph/survey.md",
            "queue": counts, "next": "цикл очереди draft-file: graph_next queue=draft-file"}


def _all_md(root: Path, cfg: Dict[str, Any]) -> List[str]:
    out = []
    for s in cfg.get("sources") or []:
        base = Path(root) / s["root"]
        cands = [base] if base.is_file() else sorted(base.rglob("*.md")) if base.is_dir() else []
        out += [p.relative_to(root).as_posix() for p in cands]
    return out


def _survey_md(per_file, anomalies, excluded, work, scope_hint) -> str:
    ont = O.load()
    lines = ["# Разведка спеки", "",
             "Механический обзор исходников перед каталогом (spec-graph, шаг graph-map). Текст разделов не "
             "читался — только оглавления и кандидаты экстракторов.", "",
             "## Объём работ", "", "| очередь | единиц |", "|---|---|"]
    for k in ("draft-file", "assign-section", "link-note", "card"):
        lines.append(f"| {k} | {work[k]} |")
    lines += [f"| **вызовов субагентов** | **{work['subagent_calls']}** |", "",
              "## Предлагаемый scope", ""]
    lines += [f"- {s}" for s in scope_hint] or ["- весь корпус (сервисов-владельцев не найдено)"]
    lines += ["", "## Аномалии", ""]
    lines += [f"- {a}" for a in anomalies] or ["- нет"]
    lines += ["", "## Исключены конфигом", ""]
    lines += [f"- {e}" for e in excluded] or ["- нет"]
    lines += ["", "## Файлы", ""]
    for pf in per_file:
        kinds = Counter(c["kind"] for c in pf["cands"])
        kc = ", ".join(f"{ont.kinds[k]['title_ru']}: {n}" for k, n in sorted(kinds.items())) or "кандидатов нет"
        kb = round(pf["data"]["bytes"] / 1024, 1)
        lines += [f"### {pf['file']}", "", f"Каталог (возможный сервис): {pf['service'] or '—'} · {kb} КБ · блоков {len(pf['data']['blocks'])} · {kc}", ""]
        top = min((r["level"] for r in pf["rows"]), default=1)
        for r in pf["rows"]:
            if r["level"] <= top + 2:
                lines.append(f"{'  ' * (r['level'] - top)}- {r['title']}")
        lines.append("")
    return "\n".join(lines)


# ─── ответ модели по единице ───────────────────────────────────────────────

def _unit(root: Path, item_id: str) -> Dict[str, Any]:
    data = json.loads((Path(root) / Q.QUEUE_REL / "draft-file.json").read_text(encoding="utf-8"))
    return next(it for it in data["items"] if it["id"] == item_id)


def put_draft(root: Path, item_id: str, nodes: List[Dict[str, Any]], not_nodes: List[Dict[str, Any]],
              cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    """(сохранённый черновик, ошибки [(код, текст)]). Ошибки — не больше 10, короткие."""
    ont = O.load(root)
    unit = _unit(root, item_id)
    rel = unit["refs"]["file"]
    sections = set(unit["refs"]["sections"])
    res = json.loads((Path(root) / X.CANDIDATES_REL / (rel + ".json")).read_text(encoding="utf-8"))
    wanted = set(unit["refs"].get("candidates") or [])
    cands = [c for c in res["candidates"] if c["name_hint"] in wanted]
    errs: List[Tuple[str, str]] = []
    names = Counter()
    for i, n in enumerate(nodes):
        w = f"nodes[{i}]"
        if not isinstance(n, dict):
            errs.append(("E-draft-shape", f"{w}: объект"))
            continue
        for k, v in n.items():
            if isinstance(v, str) and _BAD_TEXT.search(v):
                errs.append(("E-draft-text", f"{w}.{k}: обратный слэш или управляющий символ"))
        kind = n.get("kind")
        if kind not in ont.kinds:
            errs.append(("E-draft-kind", f"{w}: вид «{kind}» — один из {', '.join(ont.kinds)}"))
            continue
        spec = ont.kinds[kind]
        if spec.get("roles") and n.get("role") not in spec["roles"]:
            errs.append(("E-draft-kind", f"{w}: role — {', '.join(spec['roles'])}"))
        if spec.get("subkinds") and n.get("subkind") not in spec["subkinds"]:
            errs.append(("E-draft-kind", f"{w}: subkind — {', '.join(spec['subkinds'])}"))
        if ont.owned_by_service(kind) and not n.get("service"):
            errs.append(("E-draft-owner", f"{w}: нужен service (вид принадлежит сервису)"))
        for e in ont.check_name(kind, n.get("name"), n.get("service")):
            errs.append(("E-draft-name", f"{w} «{n.get('name')}»: {e}"))
        if "key" in spec.get("required", []):
            prefix = spec.get("key")
            if not isinstance(n.get("key"), str) or not n["key"].startswith(f"{prefix}:"):
                errs.append(("E-draft-key", f"{w}: нужен key вида «{prefix}:…»"))
        bad = [s for s in n.get("sections") or [] if s not in sections]
        if bad:
            errs.append(("E-draft-section", f"{w}: разделы не этой единицы: {', '.join(bad[:3])}"))
        names[n.get("name")] += 1
    for name, k in names.items():
        if k > 1:
            errs.append(("E-draft-dup", f"узел «{name}» назван дважды"))
    accounted = {n.get("name") for n in nodes if isinstance(n, dict)} | {n.get("key") for n in nodes if isinstance(n, dict)}
    rejected = {x.get("candidate") for x in not_nodes if isinstance(x, dict) and x.get("reason")}
    for c in cands:
        if c["name_hint"] not in accounted and c.get("key") not in accounted and c["name_hint"] not in rejected:
            errs.append(("E-draft-candidate", f"кандидат «{c['name_hint']}» не учтён: узел или not_nodes с причиной"))
    for i, x in enumerate(not_nodes):
        if not isinstance(x, dict) or not x.get("candidate") or not x.get("reason"):
            errs.append(("E-draft-shape", f"not_nodes[{i}]: {{candidate, reason, suggested_kind?}}"))
    if errs:
        return {}, errs[:10]
    draft = {"item_id": item_id, "file": rel, "nodes": nodes, "not_nodes": not_nodes}
    p = Path(root) / DRAFTS_REL / f"{item_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(draft, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return draft, []


def load_drafts(root: Path) -> List[Dict[str, Any]]:
    d = Path(root) / DRAFTS_REL
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
