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
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import blocks as B
from . import extract as X
from . import fsutil as FS
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
    auto = [c for c in cands if c.get("auto")]
    cands = [c for c in cands if not c.get("auto")]

    def row(c):
        kind = c["kind"] + (f":{c['role']}" if c.get("role") else "") + (f":{c['subkind']}" if c.get("subkind") else "")
        key = f" ключ `{c['key']}`" if c.get("key") else ""
        sec = f" раздел `{c['section']}`" if c.get("section") else ""
        return f"- {kind} — «{c['name_hint']}»{key}{sec} (правило {c['rule']})"
    lines += ["", "## Узлы правил — уже в каталоге (не повторяй и не отклоняй; бери их имена для связей)", ""]
    lines += [row(c) for c in auto] or ["- нет"]
    lines += ["", "## Кандидаты экстракторов в этом файле (реши по каждому)", ""]
    lines += [row(c) for c in cands] or ["- нет"]
    lines += ["", "## Имена кандидатов всего корпуса (бери их, если узел тот же)", ""]
    lines += [f"- {n}" for n in known] or ["- нет"]
    lines += ["", f"Сдай результат инструментом `{TOOL}` с item_id этой единицы."]
    return "\n".join(lines) + "\n"


_KNOWN_MAX = 60


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[^\W\d_]{3,}", s.lower())}


class _NameIndex:
    """Имена корпуса, сгруппированные по набору слов, и обратный индекс «слово → наборы». Сходство считается
    по различным наборам слов и кэшируется по набору слов пробы на всю разведку: в спеке заголовки и имена
    повторяются, а сравнение каждого имени с каждой пробой на каждой единице — квадрат по корпусу (40 с на
    «раздутом» варианте)."""

    def __init__(self, names: List[str]):
        self.groups: Dict[frozenset, List[str]] = {}
        for n in names:
            self.groups.setdefault(frozenset(_tokens(n)), []).append(n)
        self.inv: Dict[str, List[frozenset]] = {}
        for g in self.groups:
            for w in g:
                self.inv.setdefault(w, []).append(g)
        self._memo: Dict[frozenset, Dict[frozenset, float]] = {}

    def scores(self, probe: frozenset) -> Dict[frozenset, float]:
        if probe not in self._memo:
            inter: Dict[frozenset, int] = {}
            for w in probe:
                for g in self.inv.get(w, ()):
                    inter[g] = inter.get(g, 0) + 1
            self._memo[probe] = {g: c / max(1, len(g) + len(probe) - c) for g, c in inter.items()}
        return self._memo[probe]


def _similar(names: List[str], probes: List[str], limit: int = _KNOWN_MAX, index: Optional[_NameIndex] = None) -> List[str]:
    """Имена корпуса, ближайшие к кандидатам единицы (по общим словам, мера Жаккара): общий узел назовут
    так же, а список не раздувает вход на большом корпусе."""
    index = index or _NameIndex(names)
    allowed = set(names)
    best: Dict[frozenset, float] = {}
    for probe in {frozenset(_tokens(p)) for p in probes}:
        for g, s in index.scores(probe).items():
            if s > best.get(g, 0.0):
                best[g] = s
    scored = [(-s, n) for g, s in best.items() if s > 0 for n in index.groups[g] if n in allowed]
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


def _halve(chunk: List[Dict[str, Any]], cc: List[Dict[str, Any]]):
    """Кусок оглавления пополам. Несколько строк — по строкам (кандидат идёт со своим разделом,
    кандидаты без раздела в куске — в первую половину); одна строка — кандидаты пополам, строка в
    обеих частях (черновик ссылается на её раздел из любой). None — делить нечего."""
    if len(chunk) > 1:
        mid = len(chunk) // 2
        second = {r["id"] for r in chunk[mid:]}
        return [(chunk[:mid], [c for c in cc if c.get("section") not in second]),
                (chunk[mid:], [c for c in cc if c.get("section") in second])]
    if len(cc) > 1:
        mid = len(cc) // 2
        return [(chunk, cc[:mid]), (chunk, cc[mid:])]
    return None


def _fit(chunks, measure, limit: int):
    """Довести нарезку до бюджета по НАСТОЯЩЕМУ тексту входа. `_chunks` режет по оценке, а шапка
    (имена корпуса подбираются по сходству под каждую часть) и строки кандидатов (ключ, раздел)
    длиннее оценки — на боевой спеке две части вышли на 523 знака сверх бюджета и уронили сборку
    всей очереди. Неделимый остаток помечается `indivisible` (решает Q.build)."""
    out = []
    todo = list(chunks)
    while todo:
        chunk, cc = todo.pop(0)
        if measure(chunk, cc) <= limit:
            out.append((chunk, cc, False))
            continue
        halves = _halve(chunk, cc)
        if halves is None:
            out.append((chunk, cc, True))
        else:
            todo[:0] = halves
    return out


def survey(root: Path, cfg: Dict[str, Any], scope: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root)
    ont = O.load(root)
    rules = X.load_rules(cfg)
    scan = B.scan(root, cfg, scope, limit=10 ** 6)
    files = [s["file"] for s in scan["summary"]]
    corpus = X.corpus_info(files, rules)
    per_file, all_cands = [], []
    for rel in files:
        data = B.load(root, rel)
        res = X.extract_file(root, rel, cfg, rules, corpus)
        secs = X.sections(data["blocks"])
        per_file.append({"file": rel, "service": res["service"], "data": data, "secs": secs,
                         "rows": _section_rows(data["blocks"], secs), "cands": res["candidates"]})
        all_cands += res["candidates"]
    budget = Q.settings(cfg)["budget_chars"]
    bchars = brief_chars()
    specs, anomalies = [], []
    shas = Counter(pf["data"]["sha1"] for pf in per_file)
    index = _NameIndex(sorted({c["name_hint"] for c in all_cands}))
    by_file_names: Dict[str, Counter] = {}
    for pf in per_file:
        by_file_names[pf["file"]] = Counter(c["name_hint"] for c in pf["cands"])
    total_names = Counter(c["name_hint"] for c in all_cands)
    for pf in per_file:
        rel, rows, cands = pf["file"], pf["rows"], pf["cands"]
        own = by_file_names[rel]
        # имена кандидатов других файлов, кроме своих (как раньше: «не мой кандидат» минус «мои имена»)
        others = sorted(n for n, k in total_names.items() if n not in own and k > 0)
        def known_for(chunk, cc):
            return _similar(others, [c["name_hint"] for c in cc] + [r["title"] for r in chunk], index=index)

        def measure(chunk, cc):
            return len(_input_text(rel, pf["service"], chunk, cc, known_for(chunk, cc), part=" — часть 999"))

        chunks = _fit(_chunks(rows, cands, budget - bchars, rel, pf["service"], others), measure, budget - bchars)
        single = len(chunks) == 1
        for k, (chunk, cc, indivisible) in enumerate(chunks, 1):
            text = _input_text(rel, pf["service"], chunk, cc, known_for(chunk, cc),
                               part="" if single else f" — часть {k}")
            spec = {"id": unit_id(rel, 0 if single else k), "brief": BRIEF, "tool": TOOL, "input": text,
                    "refs": {"file": rel, "sections": [r["id"] for r in chunk],
                             "candidates": [c["name_hint"] for c in cc]}}
            if indivisible:
                spec["indivisible"] = True
            specs.append(spec)
        for w in pf["data"]["warnings"]:
            anomalies.append(f"{rel}: {w['code']} (строка {w['line']})")
        if shas[pf["data"]["sha1"]] > 1:
            anomalies.append(f"{rel}: файл побайтно совпадает с другим исходником")
        for r in rows:
            if r["chars"] > _LONG_SECTION_CHARS and r["own_blocks"] > 1:
                anomalies.append(f"{rel}: длинный раздел «{r['title']}» — {r['chars']} знаков")
    excluded = sorted(set(_all_md(root, cfg)) - set(B.source_files(root, cfg)))
    counts = Q.build(root, "draft-file", specs, cfg, {BRIEF: bchars})
    for o in counts.get("oversize", []):
        anomalies.append(f"{o['refs']['file']}: единица {o['id']} — {o['chars']} знаков сверх бюджета "
                         f"{budget}, мельче не режется (один раздел, один кандидат)")
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
    FS.write_text((vault / "survey.md"), md, encoding="utf-8")
    return {"files": len(per_file), "candidates": len(all_cands), "work": work, "anomalies": anomalies[:15],
            "excluded": excluded[:10], "scope_hint": scope_hint, "survey": f"{cfg['vault']}/_graph/survey.md",
            "queue": counts, "next": "цикл очереди draft-file: graph_next queue=draft-file"}


def _all_md(root: Path, cfg: Dict[str, Any]) -> List[str]:
    out = []
    for s in cfg.get("sources") or []:
        base = Path(root) / s["root"]
        out += [p.relative_to(root).as_posix() for p in B.md_files(base)]
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
    cands = [c for c in res["candidates"] if c["name_hint"] in wanted and not c.get("auto")]
    nodes = [dict(n, sections=[B.full_id(rel, x) for x in n["sections"]])
             if isinstance(n, dict) and isinstance(n.get("sections"), list) else n for n in nodes]
    # имя — в NFC: модель копирует имена из входа, а «ё»/«й» в NFD (имена папок macOS) дробили узел надвое
    nodes = [dict(n, name=unicodedata.normalize("NFC", n["name"])) if isinstance(n, dict) and isinstance(n.get("name"), str)
             else n for n in nodes]
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
    accounted = {n.get("name") for n in nodes if isinstance(n, dict)} | {n["key"] for n in nodes if isinstance(n, dict) and n.get("key")}
    rejected = {x.get("candidate") for x in not_nodes if isinstance(x, dict) and x.get("reason")}
    for c in cands:
        if c["name_hint"] not in accounted and c.get("key") not in accounted and c["name_hint"] not in rejected:
            errs.append(("E-draft-candidate", f"кандидат «{c['name_hint']}» не учтён: узел или not_nodes с причиной"))
    for i, x in enumerate(not_nodes):
        if not isinstance(x, dict) or not (x.get("candidate") or x.get("section")) or not x.get("reason"):
            errs.append(("E-draft-shape", f"not_nodes[{i}]: {{candidate | section, reason, suggested_kind?}}"))
    if not errs:
        for sec in uncovered(root, rel, sections, nodes, not_nodes, res["candidates"]):
            errs.append(("E-draft-uncovered", f"раздел `{sec['id']}` «{sec['title']}» ({sec['chars']} знаков) без узла: "
                                              "узел в nodes или not_nodes {section, reason}"))
    if errs:
        return {}, errs[:10]
    draft = {"item_id": item_id, "file": rel, "nodes": nodes, "not_nodes": not_nodes}
    p = Path(root) / DRAFTS_REL / f"{item_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, json.dumps(draft, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return draft, []


UNCOVERED_MIN = 1500


def uncovered(root: Path, rel: str, unit_sections: set, nodes: List[Dict[str, Any]], not_nodes: List[Dict[str, Any]],
              cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Дозапрос по перечню (gleaning): крупные разделы единицы, которые ни один узел не покрывает (ни сам
    раздел, ни предок, ни потомок не в `sections` узла), кандидатов в них нет и в not_nodes их нет.
    GraphRAG переспрашивает «что пропущено?» вслепую; здесь пропущенное перечислено поимённо — модель
    решает только по ним. Узел без разделов (файл целиком: экран, сервис) покрывает весь файл."""
    if any(isinstance(n, dict) and not n.get("sections") for n in nodes):
        return []
    data = B.load(root, rel)
    if not data:
        return []
    blocks = data["blocks"]
    secs = X.sections(blocks)
    ids = [blocks[s["head"]]["id"] for s in secs]
    covered = set()
    for n in nodes:
        covered |= set(n.get("sections") or []) if isinstance(n, dict) else set()
    covered |= {B.full_id(rel, x["section"]) for x in not_nodes if isinstance(x, dict) and x.get("section")}
    covered |= {c["section"] for c in cands if c.get("section")}
    out = []
    for i, s in enumerate(secs):
        if ids[i] not in unit_sections or ids[i] in covered:
            continue
        near = [j for j, t in enumerate(secs) if (t["start"] <= s["start"] < t["end"]) or (s["start"] <= t["start"] < s["end"])]
        if any(ids[j] in covered for j in near):
            continue
        chars = sum(len(b["text"]) for b in blocks[s["start"] + 1:s["own_end"]])
        if chars >= UNCOVERED_MIN:
            out.append({"id": ids[i], "title": s["title"], "chars": chars})
    return out


def load_drafts(root: Path) -> List[Dict[str, Any]]:
    """Черновики сданных единиц текущей разведки: черновик прошлой разведки (другой scope, другая нарезка
    файла на части) в каталог не идёт."""
    d = Path(root) / DRAFTS_REL
    done = Q.done_ids(root, "draft-file")
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        draft = json.loads(p.read_text(encoding="utf-8"))
        if done is None or draft.get("item_id") in done:
            out.append(draft)
    return out
