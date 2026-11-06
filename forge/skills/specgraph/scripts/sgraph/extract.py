"""extract.py — экстракторы кандидатов в узлы (задача 1.2, D14).

Модель слабая: чем больше узлов найдено механически, тем меньше она решает и тем меньше ошибается.
Экстрактор предлагает кандидата с доказательством — блоками и id сработавшего правила; модель на
разведке (1.3) подтверждает и называет, на раскладке (1.5) раскладывает остальное.

Код знает только ТИПЫ матчеров; какой вид узла даёт правило и по каким словам — данные
(`references/extract-defaults.json` + ключ `extract` конфига проекта, куда пишет и доучивание 4.7).
Видов узлов этот модуль не называет (пинит test_ontology): владение сервисом берётся из онтологии.

Разделы: настоящие заголовки и «жирные строки» (абзац `**…**` целиком — заголовок уровнем ниже
последнего настоящего; так пишут спеки без разметки заголовков). Заголовок чистится от нумерации
(`2.1`, `2.1.`, «Раздел 2.»), кода в скобках (`[abc_task]`) и жирного. Групповой заголовок («Шедулеры»,
«Динамические атрибуты»), у которого есть дочерние разделы с кандидатами, сам кандидатом не становится.
Разделы «См. также» не дают кандидатов из упоминаний: ТЗ §3 — «реальные взаимодействия, а не упоминания».
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import blocks as B
from . import keys as K
from . import ontology as O

DEFAULTS = Path(__file__).resolve().parents[2] / "references" / "extract-defaults.json"
CANDIDATES_REL = ".spec-graph/candidates"
MATCH_TYPES = ("heading_http", "body_http_line", "openapi_fence", "heading_markers", "code_line", "code_table",
               "file_title", "service_file", "topic_regex", "phrase")
_BOLD = re.compile(r"^\*\*([^*\n]+)\*\*\s*$")
_CLEAN = (re.compile(r"^\[[^\]]+\]\s*"), re.compile(r"^(?:раздел|глава|section|chapter)\s+\d+(?:\.\d+)*\.?\s+", re.I),
          re.compile(r"^\d+(?:\.\d+)*\\?[.)]?\s+"))
_BODY_HTTP = re.compile(r"^(?:(?:метод|method|endpoint|эндпойнт)\s*:?\s*)?`?(GET|POST|PUT|PATCH|DELETE)\s+(/\S*?)`?\.?$", re.I)
_DESCR = re.compile(r"^(общее описание|описание|overview|description|назначение)$", re.I)


class ExtractError(Exception):
    pass


# ─── правила ───────────────────────────────────────────────────────────────

def load_rules(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Правила по умолчанию + проектные: `extract.rules` (новые или переопределение по id),
    `extract.markers_add` ({id: [маркеры]}), `extract.kafka_topic_re`, `extract.stop_headings`."""
    data = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    proj = (cfg or {}).get("extract", {})
    if not isinstance(proj, dict):
        raise ExtractError("extract: объект")
    rules = {r["id"]: r for r in data["rules"]}
    for r in proj.get("rules") or []:
        if not isinstance(r, dict) or not r.get("id"):
            raise ExtractError("extract.rules: объекты с id")
        rules[r["id"]] = dict(rules.get(r["id"], {}), **r)
    for rid, extra in (proj.get("markers_add") or {}).items():
        if rid not in rules:
            raise ExtractError(f"extract.markers_add: нет правила «{rid}»")
        rules[rid] = dict(rules[rid], markers=list(rules[rid].get("markers", [])) + list(extra))
    for k in ("kafka_topic_re", "stop_headings", "group_suppress"):
        if k in proj:
            data[k] = proj[k]
    ont = O.load()
    for r in rules.values():
        if r.get("match") not in MATCH_TYPES:
            raise ExtractError(f"правило {r.get('id')}: неизвестный тип матчера «{r.get('match')}»")
        if r.get("kind") not in ont.kinds:
            raise ExtractError(f"правило {r['id']}: нет вида «{r.get('kind')}» в онтологии")
        for f in ("regex", "header_regex"):
            if f in r:
                re.compile(r[f], re.I)
    re.compile(data["kafka_topic_re"])
    data["rules"] = list(rules.values())
    return data


# ─── разделы ───────────────────────────────────────────────────────────────

def norm_ws(text: str) -> str:
    """Неразрывные и прочие пробелы → обычный: маркеры и регулярки пишутся с обычным пробелом."""
    return re.sub(r"[\u00a0\u2007\u202f\t]", " ", text)


def clean_title(title: str) -> str:
    t = re.sub(r" {2,}", " ", norm_ws(title)).strip().strip("*").strip()
    changed = True
    while changed:
        changed = False
        for rx in _CLEAN:
            n = rx.sub("", t, count=1)
            if n != t:
                t, changed = n.strip(), True
    return t


def sections(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Разделы файла: {head (индекс блока), level, title (очищенный), raw, start, end, own_end, parent}."""
    heads = []
    real_level = 0
    for i, b in enumerate(blocks):
        if b["type"] == "heading":
            heads.append((i, b["level"], b["title"]))
            real_level = b["level"]
        elif b["type"] == "paragraph":
            m = _BOLD.match(b["text"].strip())
            if m and "\n" not in b["text"].strip():
                heads.append((i, real_level + 1, m.group(1)))
    out = []
    for k, (i, level, raw) in enumerate(heads):
        end = len(blocks)
        own_end = heads[k + 1][0] if k + 1 < len(heads) else len(blocks)
        for i2, l2, _ in heads[k + 1:]:
            if l2 <= level:
                end = i2
                break
        parent = None
        for j in range(k - 1, -1, -1):
            if heads[j][1] < level:
                parent = j
                break
        out.append({"head": i, "level": level, "title": clean_title(raw), "raw": raw, "start": i, "end": end,
                    "own_end": own_end, "parent": parent})
    return out


def _word_start(text: str, marker: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(marker.lower()), norm_ws(text).lower()) is not None


# ─── сервис файла ──────────────────────────────────────────────────────────

def service_of(rel: str, cfg: Dict[str, Any]) -> Optional[str]:
    """Сервис-владелец файла: карта `services` конфига (самый длинный префикс), иначе каталог под корнем
    исходников, иначе имя файла."""
    best = None
    for prefix, svc in (cfg.get("services") or {}).items():
        p = prefix.rstrip("/")
        if (rel == p or rel.startswith(p + "/")) and (best is None or len(p) > len(best[0])):
            best = (p, svc)
    if best:
        return best[1]
    for s in cfg.get("sources") or []:
        root = s["root"].rstrip("/")
        if rel.startswith(root + "/"):
            parts = rel[len(root) + 1:].split("/")
            return parts[0] if len(parts) > 1 else Path(parts[0]).stem
    return Path(rel).stem


# ─── матчеры ───────────────────────────────────────────────────────────────

def _ids(blocks, a, b):
    return [blk["id"] for blk in blocks[a:b]]


def _text(blocks, a, b):
    return "".join(blk["text"] for blk in blocks[a:b])


def extract_blocks(blocks: List[Dict[str, Any]], rel: str, cfg: Dict[str, Any],
                   rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rules = rules or load_rules(cfg)
    ont = O.load()
    svc = service_of(rel, cfg)
    secs = sections(blocks)
    topic_re = re.compile(rules["kafka_topic_re"])
    stop = [s for s in secs if any(s["title"].lower().startswith(h) for h in rules["stop_headings"])]
    stop_idx = {i for s in stop for i in range(s["start"], s["end"])}
    cands: List[Dict[str, Any]] = []
    by_section: Dict[int, List[Dict[str, Any]]] = {}

    def add(rule, name, blk_ids, key=None, section=None, extra=None):
        kind = rule["kind"]
        c = {"kind": kind, "name_hint": name, "blocks": blk_ids, "rule": rule["id"],
             "confidence": rule.get("confidence", "medium")}
        for f in ("role", "subkind"):
            if rule.get(f):
                c[f] = rule[f]
        if ont.owned_by_service(kind) and svc:
            c["service"] = svc
        if key:
            c["key"] = key
        if section is not None:
            c["section"] = blocks[secs[section]["head"]]["id"]
        if extra:
            c.update(extra)
        for prev in cands:
            same = (key and prev.get("key") == key) or (not key and prev["kind"] == kind and prev["name_hint"] == name)
            if same:
                prev["blocks"] = prev["blocks"] + [b for b in blk_ids if b not in prev["blocks"]]
                return prev
        cands.append(c)
        if section is not None:
            by_section.setdefault(section, []).append(c)
        return c

    rules_by_type: Dict[str, List[Dict[str, Any]]] = {}
    for r in rules["rules"]:
        rules_by_type.setdefault(r["match"], []).append(r)

    for si, s in enumerate(secs):
        if s["start"] in stop_idx:
            continue
        span_ids = _ids(blocks, s["start"], s["end"])
        span_text = _text(blocks, s["start"], s["end"])
        own = blocks[s["start"] + 1:s["own_end"]]
        taken = False
        for r in rules_by_type.get("heading_http", []):
            hits = K.find_http(s["title"])
            if hits:
                m, p = hits[0]
                add(r, K.endpoint_name(svc or "?", m, p), span_ids, K.http_key(m, p), si)
                taken = True
        if not taken:
            for r in rules_by_type.get("body_http_line", []):
                for b in own[:3]:
                    mm = _BODY_HTTP.match(b["text"].strip()) if b["type"] == "paragraph" and "\n" not in b["text"].strip() else None
                    if mm:
                        add(r, K.endpoint_name(svc or "?", mm.group(1), mm.group(2)), span_ids,
                            K.http_key(mm.group(1), mm.group(2)), si)
                        taken = True
                        break
        if not taken:
            for r in rules_by_type.get("openapi_fence", []):
                for b in own:
                    if b["type"] == "fence" and b.get("lang", "").lower() in ("yaml", "yml") and "paths:" in b["text"]:
                        path = None
                        for line in b["text"].splitlines():
                            pm = re.match(r"^\s{2}(/\S*):\s*$", line)
                            mm = re.match(r"^\s{4}(get|post|put|patch|delete):\s*$", line)
                            if pm:
                                path = pm.group(1)
                            elif mm and path:
                                add(r, K.endpoint_name(svc or "?", mm.group(1), path), span_ids,
                                    K.http_key(mm.group(1), path), si)
                                taken = True
        if not taken:
            for r in rules_by_type.get("heading_markers", []):
                if any(_word_start(s["title"], mk) for mk in r.get("markers", [])):
                    if r.get("requires_topic") and not topic_re.search(span_text):
                        continue
                    add(r, s["title"], span_ids, None, si, {"_heading_rule": True})
                    taken = True
                    break
        for r in rules_by_type.get("code_line", []):
            rx = re.compile(r["regex"], re.I)
            for b in own:
                mm = rx.search(norm_ws(b["text"]))
                if mm:
                    add(r, s["title"], span_ids, f"{r.get('key_prefix', 'task')}:{mm.group(1)}", si)
                    break

    if rules.get("group_suppress", True):
        def descendants(si):
            return [j for j, s in enumerate(secs) if j != si and secs[si]["start"] < s["start"] < secs[si]["end"]]
        drop = []
        for si, cs in by_section.items():
            if any(by_section.get(j) for j in descendants(si)):
                drop += [c for c in cs if c.get("_heading_rule")]
        cands = [c for c in cands if not any(c is d for d in drop)]

    for r in rules_by_type.get("code_table", []):
        hx = re.compile(r["header_regex"], re.I)
        for i, b in enumerate(blocks):
            if b["type"] != "table" or i in stop_idx:
                continue
            lines = [l for l in b["text"].splitlines() if l.strip()]
            if not lines or not hx.search(lines[0]):
                continue
            for row in lines[2:]:
                cells = [norm_ws(c).strip().strip("`") for c in row.strip().strip("|").split("|")]
                if len(cells) >= 2 and re.fullmatch(r"[A-Z][A-Z0-9_]+", cells[0]) and cells[1]:
                    add(r, cells[1], [b["id"]], f"{r.get('key_prefix', 'task')}:{cells[0]}")

    first_head = next((s for s in secs if blocks[s["head"]]["type"] == "heading"), None)
    for r in rules_by_type.get("file_title", []):
        if first_head and any(re.match(re.escape(mk) + r"\b", first_head["title"], re.I) for mk in r.get("markers", [])):
            add(r, first_head["title"], [b["id"] for b in blocks], None)
    for r in rules_by_type.get("service_file", []):
        d = Path(rel).parent.name
        if svc and first_head and (Path(rel).stem == d or first_head["title"] == d or first_head["title"] == svc):
            ids = _ids(blocks, 0, first_head["own_end"])
            for s in secs:
                if s["parent"] == secs.index(first_head) and _DESCR.match(s["title"]):
                    ids += _ids(blocks, s["start"], s["end"])
            add(r, svc, ids, None)

    for r in rules_by_type.get("topic_regex", []):
        for i, b in enumerate(blocks):
            if i in stop_idx:
                continue
            for t in topic_re.findall(b["text"]):
                add(r, t, [b["id"]], K.topic_key(t))
    for r in rules_by_type.get("phrase", []):
        rx = re.compile(r["regex"])
        for i, b in enumerate(blocks):
            if i in stop_idx:
                continue
            for name in rx.findall(norm_ws(b["text"])):
                add(r, name, [b["id"]], None)

    for c in cands:
        c.pop("_heading_rule", None)
    return {"file": rel, "service": svc, "candidates": cands}


def extract_file(root: Path, rel: str, cfg: Dict[str, Any], rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = B.load(root, rel) or B.scan_file(root, rel)
    res = extract_blocks(data["blocks"], rel, cfg, rules)
    p = Path(root) / CANDIDATES_REL / (rel + ".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return res


def summary(res: Dict[str, Any]) -> List[str]:
    """Короткая сводка кандидатов файла для модели: «вид[:роль] — имя (правило)»."""
    out = []
    for c in res["candidates"]:
        kind = c["kind"] + (f":{c['role']}" if c.get("role") else "") + (f":{c['subkind']}" if c.get("subkind") else "")
        out.append(f"{kind} — {c['name_hint']} ({c['rule']})")
    return out
