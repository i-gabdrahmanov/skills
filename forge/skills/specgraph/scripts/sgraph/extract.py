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
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import blocks as B
from . import fsutil as FS
from . import keys as K
from . import naming as NM
from . import ontology as O
from . import sqlref as SQ

DEFAULTS = Path(__file__).resolve().parents[2] / "references" / "extract-defaults.json"
CANDIDATES_REL = ".spec-graph/candidates"
MATCH_TYPES = ("heading_http", "body_http_line", "openapi_fence", "heading_markers", "code_line", "code_table",
               "file_title", "service_file", "topic_regex", "phrase", "folder_code", "code_title", "sql_tables")
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
    nm = proj.get("naming")
    if nm is not None:
        if not isinstance(nm, dict):
            raise ExtractError("extract.naming: объект")
        base = data.get("naming") or {}
        kinds = dict(base.get("kinds") or {})
        for kind, spec in (nm.get("kinds") or {}).items():
            kinds[kind] = dict(kinds.get(kind) or {}, **spec)
        data["naming"] = dict(base, **{k: v for k, v in nm.items() if k != "kinds"}, kinds=kinds)
    ont = O.load()
    for r in rules.values():
        if r.get("match") not in MATCH_TYPES:
            raise ExtractError(f"правило {r.get('id')}: неизвестный тип матчера «{r.get('match')}»")
        if r.get("kind") not in ont.kinds:
            raise ExtractError(f"правило {r['id']}: нет вида «{r.get('kind')}» в онтологии")
        for f in ("regex", "header_regex", "code_re", "key_regex"):
            if f in r:
                re.compile(r[f], re.I)
    re.compile(data["kafka_topic_re"])
    for kind, spec in ((data.get("naming") or {}).get("kinds") or {}).items():
        if kind not in ont.kinds:
            raise ExtractError(f"naming.kinds: нет вида «{kind}» в онтологии")
        for rx in spec.get("strip") or []:
            re.compile(rx, re.I)
    data["rules"] = list(rules.values())
    return data


# ─── разделы ───────────────────────────────────────────────────────────────

def norm_ws(text: str) -> str:
    """Неразрывные и прочие пробелы → обычный: маркеры и регулярки пишутся с обычным пробелом."""
    return re.sub(r"[\u00a0\u2007\u202f\t]", " ", text)


_HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")


def clean_title(title: str) -> str:
    # HTML в заголовке (якоря оглавления `<a name="21-…"></a>`) — разметка, а не имя: с ним имя
    # кандидата несло «/» из `</a>`, и черновик падал на запрещённом символе (боевой прогон).
    t = re.sub(r" {2,}", " ", norm_ws(_HTML_TAG.sub("", title))).strip().strip("*").strip()
    changed = True
    while changed:
        changed = False
        for rx in _CLEAN:
            n = rx.sub("", t, count=1)
            if n != t:
                t, changed = n.strip(), True
    return t


def safe_name(name: str, forbidden: str) -> str:
    """Имя кандидата, годное в имя узла (оно же имя файла заметки и ссылка [[…]]): разделители пути
    «/ \\ |» — в дефис («UI/UX Описание» → «UI-UX Описание»), прочие запрещённые — в пробел, без
    точки в конце. Модель берёт имя кандидата как есть — раньше «UI/UX» отбивал черновик."""
    name = unicodedata.normalize("NFC", name)          # имена папок macOS бывают в NFD
    out = "".join("-" if c in "/\\|" else " " if c in forbidden else c for c in name)
    out = re.sub(r"\s+", " ", out).strip().rstrip(".").strip()
    while len(out.encode("utf-8")) > O.NAME_MAX_BYTES:    # длинный заголовок — по границе слова, в пределах имени файла
        cut = out.rsplit(" ", 1)[0] if " " in out else out[:-1]
        out = cut.rstrip(" .,;:—-")
    return out


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


_BRACKET = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def folder_codes(rel: str, rules: Dict[str, Any]) -> List[Dict[str, str]]:
    """Коды папок пути `…/[код] Имя/…` по правилам folder_code: [{code, name, folder, rule}] — самая
    внутренняя папка с кодом. Метки без кода (`[UI]`, `[СБОЛпро]`) правило не трогает: код задаёт `code_re`."""
    out = []
    parts = rel.split("/")[:-1]
    for r in rules["rules"]:
        if r.get("match") != "folder_code":
            continue
        code_re = re.compile(r.get("code_re") or r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")
        for i in range(len(parts) - 1, -1, -1):
            m = _BRACKET.match(parts[i].strip())
            if m and code_re.fullmatch(m.group(1).strip()) and clean_title(m.group(2)):
                out.append({"code": m.group(1).strip(), "name": clean_title(m.group(2)),
                            "folder": "/".join(parts[:i + 1]), "rule": r["id"]})
                break
    return out


def corpus_info(files: List[str], rules: Dict[str, Any]) -> Dict[str, Any]:
    """Сведения о корпусе для правил, которым мало одного файла: коды папок (`code_title` узнаёт их в
    заголовках реестра) и списки файлов папок (у папки без README операция — без собственного текста)."""
    codes: Dict[str, Dict[str, str]] = {}
    folders: Dict[str, List[str]] = {}
    for rel in sorted(files):
        for fc in folder_codes(rel, rules):
            codes.setdefault(fc["code"].lower(), fc)
            folders.setdefault(fc["folder"], []).append(rel)
    return {"codes": codes, "folders": folders}


def extract_blocks(blocks: List[Dict[str, Any]], rel: str, cfg: Dict[str, Any],
                   rules: Optional[Dict[str, Any]] = None, corpus: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rules = rules or load_rules(cfg)
    corpus = corpus or corpus_info([rel], rules)
    ont = O.load()
    svc = service_of(rel, cfg)
    secs = sections(blocks)
    topic_re = re.compile(rules["kafka_topic_re"])
    stop = [s for s in secs if any(s["title"].lower().startswith(h) for h in rules["stop_headings"])]
    stop_idx = {i for s in stop for i in range(s["start"], s["end"])}
    cands: List[Dict[str, Any]] = []
    by_section: Dict[int, List[Dict[str, Any]]] = {}

    naming = NM.settings(rules)

    def add(rule, name, blk_ids, key=None, section=None, extra=None):
        kind = rule["kind"]
        if ont.kinds[kind].get("name") == "phrase":
            name = safe_name(name, ont.forbidden)
            name = safe_name(NM.normalize(kind, rule.get("role"), name, NM.context(blocks, secs, section, rel, topic_re),
                                          naming), ont.forbidden) or name
        c = {"kind": kind, "name_hint": name, "blocks": blk_ids, "rule": rule["id"],
             "confidence": rule.get("confidence", "medium")}
        for f in ("role", "subkind", "auto", "stub"):
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
                if prev.get("stub") and not rule.get("stub"):
                    # таблица описана своим разделом — уже не заглушка: имя и раздел у описания
                    prev.pop("stub")
                    prev.update(name_hint=name, rule=rule["id"])
                    if section is not None:
                        prev["section"] = blocks[secs[section]["head"]]["id"]
                return prev
        cands.append(c)
        if section is not None:
            by_section.setdefault(section, []).append(c)
        return c

    rules_by_type: Dict[str, List[Dict[str, Any]]] = {}
    for r in rules["rules"]:
        rules_by_type.setdefault(r["match"], []).append(r)

    # реестр типов задач: раздел, в заголовке которого код папки другой части спеки, и рядом (у того же
    # родителя) ещё такие же — перечень типов. Одиночное упоминание кода в заголовке (шедулер «… код_операции»)
    # реестром не считается: иначе раздел шедулера стал бы текстом операции
    own_folders = {fc["folder"] for fc in folder_codes(rel, rules)}
    code_sec: Dict[int, Dict[str, str]] = {}
    for si, s in enumerate(secs):
        if si == 0 and s["level"] == min((x["level"] for x in secs), default=1) or s["start"] in stop_idx:
            continue
        for low, fc in corpus.get("codes", {}).items():
            if fc["folder"] not in own_folders and re.search(r"(?<![\w])" + re.escape(low) + r"(?![\w])",
                                                              norm_ws(s["raw"]).lower()):
                code_sec[si] = fc
                break
    siblings = Counter(secs[si]["parent"] for si in code_sec)
    registry = {si: fc for si, fc in code_sec.items() if siblings[secs[si]["parent"]] >= 2}

    for si, s in enumerate(secs):
        if s["start"] in stop_idx:
            continue
        span_ids = _ids(blocks, s["start"], s["end"])
        span_text = _text(blocks, s["start"], s["end"])
        own = blocks[s["start"] + 1:s["own_end"]]
        taken = False
        if si in registry:
            fc = registry[si]
            for r in rules_by_type.get("code_title", []):
                title = clean_title(re.sub(r"[\[(`]?\s*" + re.escape(fc["code"]) + r"\s*[\])`]?", " ", s["raw"],
                                           flags=re.I)).strip(" -—–:")
                add(r, title or fc["name"], span_ids, f"{r.get('key_prefix', 'task')}:{fc['code']}", si)
                taken = True
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
                    key = None
                    if r.get("key_regex"):
                        km = re.search(r["key_regex"], s["title"])
                        key = f"{r.get('key_prefix', 'table')}:{km.group(1).lower()}" if km else None
                        if key is None and r.get("require_key"):
                            continue
                    add(r, km.group(1) if key else s["title"], span_ids, key, si, {"_heading_rule": True})
                    taken = True
                    break
        # код в строке раздела, уже занятого другим правилом (шедулер «Тип задачи: X»), — не текст операции
        for r in [] if taken else rules_by_type.get("code_line", []):
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
            # групповой заголовок — тот, под которым кандидаты ТОГО ЖЕ вида («Шедулеры» → шедулеры). Раньше
            # подавлялся любой: обработчик сообщения с разделами алгоритмов внутри пропадал, и алгоритмы
            # оставались без того, кто их запускает (History Service, обратная связь 2026-11-09)
            inner = {(c["kind"], c.get("role")) for j in descendants(si) for c in by_section.get(j) or []}
            drop += [c for c in cs if c.get("_heading_rule") and (c["kind"], c.get("role")) in inner]
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
                if len(cells) >= 2 and re.fullmatch(r.get("code_re") or r"[A-Z][A-Z0-9_]+", cells[0]) and cells[1]:
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
    for r in rules_by_type.get("folder_code", []):
        for fc in folder_codes(rel, {"rules": [r]}):
            members = sorted(corpus.get("folders", {}).get(fc["folder"]) or [rel])
            heads = [m for m in members if m.rsplit("/", 1)[-1].lower() in [h.lower() for h in r.get("head_files") or []]]
            key = f"{r.get('key_prefix', 'task')}:{fc['code']}"
            if heads and rel == heads[0]:
                add(r, fc["name"], [b["id"] for b in blocks], key)
            elif not heads and rel == members[0]:
                # у папки нет README — операция есть (по структуре), своего текста нет: заглушка
                add(dict(r, stub=True), fc["name"], [], key)
    for r in rules_by_type.get("sql_tables", []):
        for i, b in enumerate(blocks):
            if i in stop_idx:
                continue
            for frag in SQ.fragments(b["text"]):
                for st in SQ.statements(frag):
                    for t, _mode in SQ.tables(st):
                        add(r, t, [b["id"]], f"{r.get('key_prefix', 'table')}:{t.lower()}")
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


def extract_file(root: Path, rel: str, cfg: Dict[str, Any], rules: Optional[Dict[str, Any]] = None,
                 corpus: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = B.load(root, rel) or B.scan_file(root, rel)
    res = extract_blocks(data["blocks"], rel, cfg, rules, corpus)
    p = Path(root) / CANDIDATES_REL / (rel + ".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    FS.write_text(p, json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return res


def summary(res: Dict[str, Any]) -> List[str]:
    """Короткая сводка кандидатов файла для модели: «вид[:роль] — имя (правило)»."""
    out = []
    for c in res["candidates"]:
        kind = c["kind"] + (f":{c['role']}" if c.get("role") else "") + (f":{c['subkind']}" if c.get("subkind") else "")
        out.append(f"{kind} — {c['name_hint']} ({c['rule']})")
    return out
