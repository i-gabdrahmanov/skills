#!/usr/bin/env python3
"""analyze_spec.py — ресерч формата требований-мастера: как спека устроена ИМЕННО в этом проекте.

Зачем. Движок слияния и гейт состава знали ровно одну форму мастера — форже-родную
(`### REQ-0007: <название>` + строки Given-When-Then). У проекта со своей спекой это давало
тихую порчу: требований не находилось ни одного, все требования дельты считались новыми, и
`/forge-merge` дописывал в чужой документ блоки в форже-грамматике. Поэтому форма сначала
СНИМАЕТСЯ с мастера, а потом уже применяется.

Результат — `ground/inventory/spec-conventions.json`, рядом с остальным эфемерным инвентарём:
производное от мастер-спеки, в git не едет, снимается заново когда мастер изменился. Ручки,
которые подтвердил человек, живут отдельно — в `ground/policy.json` (`spec.grammar.*`): их
правит `config.py set`, они версионируются и снапшотятся на прогон.

Детект детерминированный (частоты заголовков, не LLM). Где регулярок не хватает — exit 2 и
разбор субагентом-ресерчером, чей вывод вмерживается сюда же (`--apply-research`).

Usage:
    analyze_spec.py --root <project> [--out <path>] [--if-missing | --refresh]
                    [--apply-research <json>] [--json] [--quiet]

Exit:
    0 — профиль записан (в том числе «мастера нет» — исследовать нечего)
    2 — форма не форже-родная И уверенность ниже порога: нужен ресерчер или человек
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import spec_grammar as SG  # noqa: E402

SCHEMA_VERSION = 1
CONFIDENCE_FLOOR = SG.DETECT_FLOOR   # ниже — не гадаем, а зовём ресерчера (порог один на двоих)
SKIP_DIRS = {".git", "adr", "system-analysis", "feature-pipeline", "archive", "node_modules",
             ".gigacode", "ground", "build", "target"}
SKIP_NAMES = {"readme.md", "changelog.md", "contributing.md", "index.md"}
MAX_DEPTH = 3

_HEADING = SG._HEADING
_GWT = SG._GWT
_ID_IN_HEAD = re.compile(r"^([A-Za-z][A-Za-z0-9_]{1,9})-(\d+)\s*:")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+\S")
_LEAD = re.compile(r"^([^:]{3,40}?)\s*:\s*\S")
_BULLET_ID = re.compile(r"^\s{0,3}[-*]\s+\**([A-Za-z][A-Za-z0-9_]{1,9})-(\d+)\**\s*[:—–-]\s*\S")
_AUDIT_HEAD = re.compile(r"(?i)журнал изменений|audit trail|changelog|история изменений")
_SCEN_LEAD = re.compile(r"(?i)scenario|сценарий")
# Словарь ЗАГОЛОВКОВ РАЗДЕЛОВ. Нумерованный раздел «## 5. Требования» синтаксически неотличим
# от нумерованного требования «### 3.1 Возврат»; вложенность ловит это только когда требования
# есть внутри. У плоского легаси-мастера их нет — и раздел уезжал в «требования уровня 2».
_SECTION_WORDS = re.compile(
    r"(?i)^(?:назначен|границы|ограничен|критери|требован|сценари|модель угроз|архитектур|"
    r"регулятор|журнал изменени|истори[яи] изменени|реализац|глоссари|приложени|содержани|"
    r"purpose|scope|constraint|acceptance|requirement|scenario|threat|regulatory|security|"
    r"audit|changelog|overview|introduction|appendix|contents|implementation|glossary)")


def _is_section_title(head: str) -> bool:
    """Заголовок выглядит названием РАЗДЕЛА, а не требованием."""
    return bool(_SECTION_WORDS.match(re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", head).strip()))
_FROM_TAG = SG._FROM_TAG


# ── кандидаты ──────────────────────────────────────────────────────────

def _resolve_master(root: Path):
    """(configured_spec_path, master_base) через общий резолвер; фолбэк — раскладка по умолчанию."""
    try:
        sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "feature-pipeline" / "scripts"))
        import skill_paths
        return skill_paths.master_spec_path(root), skill_paths.master_specs_dir(root).parent
    except Exception:  # noqa: BLE001
        base = root / "docs"
        return base / "specs" / "capability" / "spec.md", base


def _candidates(root: Path) -> List[Path]:
    """Файлы, которые могут быть мастером: конфигурный путь плюс *.md под базой мастера."""
    configured, base = _resolve_master(root)
    out: List[Path] = [configured] if configured.exists() else []
    if base.exists():
        for p in sorted(base.rglob("*.md")):
            try:
                rel = p.relative_to(base)
            except ValueError:
                continue
            if len(rel.parts) > MAX_DEPTH:
                continue
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if p.name.lower() in SKIP_NAMES or p in out:
                continue
            out.append(p)
    return out


def fingerprint(paths: List[Path]) -> dict:
    """Отпечаток кандидатов: состав + самый свежий mtime. Тот же приём, что у ensure_inventory."""
    newest = 0.0
    for p in paths:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return {"files": len(paths), "newest": round(newest, 3)}


# ── гипотезы формы ─────────────────────────────────────────────────────

def _headings(lines: List[str]):
    for i, ln in enumerate(lines):
        m = _HEADING.match(ln)
        if m:
            yield i, len(m.group(1)), m.group(2).strip()


def _hypotheses(text: str) -> List[dict]:
    """Все разборы, которые дали хоть одно требование, отсортированные по числу блоков."""
    lines = text.splitlines()
    heads = list(_headings(lines))
    out: List[dict] = []

    for level in (2, 3, 4):
        at = [(i, h) for i, lv, h in heads if lv == level]
        if not at:
            continue
        # id-colon: `REQ-0007: <название>`
        ids = [_ID_IN_HEAD.match(h) for _, h in at]
        pref = Counter(m.group(1) for m in ids if m)
        if pref:
            prefix, n = pref.most_common(1)[0]
            width = Counter(len(m.group(2)) for m in ids if m and m.group(1) == prefix)
            out.append({"kind": "id-colon", "level": level, "n": n, "lead": "",
                        "id_prefix": prefix, "id_width": width.most_common(1)[0][0]})
        # numbered: `3.1.2 <название>`. Одноуровневый номер на `##` — почти всегда оглавление
        # раздела, а не требование, поэтому требуем либо вложенный номер, либо уровень ≥3.
        n_num = sum(1 for _, h in at
                    if _NUMBERED.match(h) and not _is_section_title(h)
                    and ("." in _NUMBERED.match(h).group(1) or level >= 3))
        if n_num:
            out.append({"kind": "numbered", "level": level, "n": n_num, "lead": ""})
        # title-only с ведущим словом: `Requirement: <название>`
        leads = Counter()
        for _, h in at:
            m = _LEAD.match(h)
            # Словарь разделов здесь НЕ применяем: «## Requirement: The system SHALL …» —
            # это как раз требование, а голое «## Requirements» под _LEAD не подходит вовсе
            # (нет двоеточия с названием после него).
            if m and not _ID_IN_HEAD.match(h) and not _SCEN_LEAD.search(m.group(1)):
                leads[m.group(1).strip()] += 1          # написание документа сохраняем
        if leads:
            lead, n_lead = leads.most_common(1)[0]
            if n_lead > 1:
                out.append({"kind": "title-only", "level": level, "n": n_lead,
                            "lead": lead})

    # bullet-id: `- **REQ-0007** — <название>`
    bullets = [_BULLET_ID.match(l) for l in lines]
    bp = Counter(m.group(1) for m in bullets if m)
    if bp:
        prefix, n = bp.most_common(1)[0]
        width = Counter(len(m.group(2)) for m in bullets if m and m.group(1) == prefix)
        out.append({"kind": "bullet-id", "level": 3, "n": n, "lead": "",
                    "id_prefix": prefix, "id_width": width.most_common(1)[0][0]})

    _score(out, lines)
    out.sort(key=lambda h: h["score"], reverse=True)
    return out


def _starts_of(hyp: dict, lines: List[str]) -> List[int]:
    g = _probe_grammar(hyp)
    return [i for i, ln in enumerate(lines) if g.match_requirement(ln)]


def _probe_grammar(hyp: dict) -> "SG.Grammar":
    req = {"level": hyp["level"], "kind": hyp["kind"], "lead": hyp.get("lead", ""),
           "id_prefix": hyp.get("id_prefix", "REQ"), "id_width": hyp.get("id_width", 4),
           "scope": "document"}
    return SG.Grammar({"requirement": req, "requirements_section": [], "audit_section": [],
                       "scenario": {"style": "gwt-inline"}, "provenance": "none"})


def _bounds(starts: List[int], lines: List[str], level: int) -> List[int]:
    """Конец блока требования: следующее требование ЛИБО заголовок своего уровня и выше.

    Без второго условия последний блок тянулся бы до конца файла и «содержал» бы все
    последующие разделы — гипотеза требований выглядела бы контейнером и проигрывала разделам.
    """
    ends = []
    for s in starts:
        end = len(lines)
        for i in range(s + 1, len(lines)):
            if i in starts:
                end = i
                break
            m = _HEADING.match(lines[i])
            if m and len(m.group(1)) <= level and not _SCEN_LEAD.search(m.group(2)):
                end = i
                break
        ends.append(end)
    return ends


def _score(hyps: List[dict], lines: List[str]) -> None:
    """Раздел «## 5. Требования» синтаксически неотличим от требования «## 3.1 Возврат» —
    отличает ВЛОЖЕННОСТЬ: если внутри блоков гипотезы лежат блоки другой, это контейнер
    (раздел), а требования — та гипотеза, что внутри. Без этого нумерованный мастер и обычный
    форже-мастер с разделами `## 5. …` детектились как «требования уровня 2»."""
    starts = {id(h): _starts_of(h, lines) for h in hyps}
    for h in hyps:
        mine = starts[id(h)]
        ends = _bounds(mine, lines, h["level"])
        contains = 0
        for other in hyps:
            if other is h:
                continue
            for s2 in starts[id(other)]:
                if any(s1 < s2 < e1 for s1, e1 in zip(mine, ends)):
                    contains += 1
        # Содержательность: у требования есть утверждение либо сценарии, у оглавления — нет.
        filled = 0
        for s1, e1 in zip(mine, ends):
            body = lines[s1 + 1:e1]
            if any(l.strip() and not l.lstrip().startswith("#") for l in body):
                filled += 1
        h["contains"] = contains
        h["filled"] = filled
        h["score"] = h["n"] * (1 + (filled / max(1, h["n"]))) * (0.15 if contains else 1.0)


def _bodies(text: str, g: "SG.Grammar") -> List[List[str]]:
    """Тела требований: от заголовка до следующего требования или заголовка своего уровня.

    Считать по разобранному блоку нельзя: при стиле gwt-inline подзаголовок «#### Scenario:»
    закрыл бы требование, и блочные сценарии не нашлись бы вовсе — детект стиля стал бы
    самоисполняющимся.
    """
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if g.match_requirement(ln)]
    return [lines[s + 1:e] for s, e in zip(starts, _bounds(starts, lines, g.level))]


def _scenario_style(text: str, g: "SG.Grammar") -> tuple:
    """(style, level, доля требований со сценариями) по блокам, найденным грамматикой."""
    bodies = _bodies(text, g)
    if not bodies:
        return "none", g.level + 1, 0.0
    inline = block = bullet = 0
    scen_levels = Counter()
    for body in bodies:
        has_inline = any(_GWT.search(l) for l in body if not l.lstrip().startswith("#"))
        heads = [len(m.group(1)) for m in (SG._SCEN_HEAD.match(l) for l in body) if m]
        if heads:
            block += 1
            scen_levels.update(heads)
        elif has_inline:
            inline += 1
        elif any(l.strip().startswith(("-", "*")) for l in body):
            bullet += 1
    covered = inline + block + bullet
    if not covered:
        return "none", g.level + 1, 0.0
    style = max((("gwt-inline", inline), ("gwt-block", block), ("bullet", bullet)),
                key=lambda kv: kv[1])[0]
    level = scen_levels.most_common(1)[0][0] if scen_levels else g.level + 1
    return style, level, round(covered / len(bodies), 2)


def _sections(text: str, g: "SG.Grammar") -> tuple:
    """(якорь раздела требований, якорь журнала изменений) — по тому, где реально лежат блоки."""
    lines = text.splitlines()
    heads = [(i, lv, h) for i, lv, h in _headings(lines)]
    starts = [r["start"] for r in SG.Grammar({**g.profile,
                                              "requirement": {**g.profile["requirement"],
                                                              "scope": "document"}}).parse(text)]
    best, best_n = None, 0
    h2 = [(i, h) for i, lv, h in heads if lv == 2 and not g.match_requirement(lines[i])]
    for k, (i, h) in enumerate(h2):
        end = h2[k + 1][0] if k + 1 < len(h2) else len(lines)
        n = sum(1 for s in starts if i < s < end)
        if n > best_n:
            best, best_n = h, n
    audit = next((h for _, lv, h in heads if lv == 2 and _AUDIT_HEAD.search(h)), None)
    return (_marker(best) if best_n else None), (_marker(audit) if audit else None)


def _marker(head: Optional[str]) -> Optional[str]:
    """Якорь раздела — опознаваемый кусок заголовка без номера и скобок."""
    if not head:
        return None
    h = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", head)
    h = re.sub(r"\(.*?\)", "", h).strip(" :—-")
    return h.lower() or None


# ── детект целиком ─────────────────────────────────────────────────────

def analyze(root: Path) -> dict:
    root = Path(root).resolve()
    configured, base = _resolve_master(root)
    cands = _candidates(root)
    result: dict = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": str(root),
        "source": {"configured": str(configured), "spec": None,
                   "candidates": [str(p) for p in cands]},
        "fingerprint": fingerprint(cands),
        "matches_native": True,
        "confidence": 0.0,
        "axes": {},
        "grammar": {},
        "spec_path": None,
        "exemplars": [],
        "warnings": [],
        "suggested_config": [],
    }
    if not cands:
        result["warnings"].append("no_master")
        return result

    # Мастер — кандидат с наибольшим числом требований по лучшей гипотезе.
    scored = []
    for p in cands:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hyps = _hypotheses(text)
        scored.append((hyps[0]["score"] if hyps else 0, p, text, hyps))
    scored.sort(key=lambda t: (t[0], t[1] == configured), reverse=True)
    top_n, spec, text, hyps = scored[0]
    result["source"]["spec"] = str(spec)

    if not hyps or top_n == 0:
        result["warnings"].append("no_requirements_found")
        result["matches_native"] = False
        result["confidence"] = 0.0
        return result

    win = hyps[0]
    top_n = win["n"]
    runner = hyps[1]["score"] if len(hyps) > 1 else 0
    req = {"level": win["level"], "kind": win["kind"], "lead": win.get("lead", ""),
           "id_prefix": win.get("id_prefix", SG.NATIVE["requirement"]["id_prefix"]),
           "id_width": win.get("id_width", 4)}
    req["scope"] = "section" if (win["kind"] == "title-only" and not win.get("lead")) else "document"
    probe = SG.Grammar({"requirement": req, "requirements_section": [], "audit_section": [],
                        "scenario": {"style": "gwt-inline"}, "provenance": "from-bracket"})
    style, scen_level, coverage = _scenario_style(text, probe)
    sec_req, sec_audit = _sections(text, probe)

    grammar = {
        "requirement": req,
        "requirements_section": [sec_req] if sec_req else [],
        "audit_section": [sec_audit] if sec_audit else [],
        "scenario": {"style": style, "level": scen_level},
        "provenance": "from-bracket" if _FROM_TAG.search(text) else "none",
    }
    g = SG.Grammar(grammar)
    result["grammar"] = grammar
    result["matches_native"] = g.is_native()
    result["axes"] = {
        "requirement": round(min(1.0, top_n / 2.0) * (1.0 if win["score"] > runner * 1.5 else 0.7), 2),
        "scenario": coverage,
        "sections": 1.0 if sec_req else 0.5,
    }
    # Главная ось — распознан ли блок требования; сценарии и якорь раздела лишь уточняют.
    result["confidence"] = round(
        0.6 * result["axes"]["requirement"] + 0.25 * min(1.0, coverage + 0.3)
        + 0.15 * result["axes"]["sections"], 2)
    result["exemplars"] = _exemplars(text, g, spec)
    result["spec_path"] = _spec_path_tpl(spec, base, root)

    if coverage == 0.0:
        result["warnings"].append("no_scenarios_found")
    if runner and win["score"] <= runner * 1.5:
        result["warnings"].append("mixed_grammar")
    if result["confidence"] < CONFIDENCE_FLOOR:
        result["warnings"].append("low_confidence")
    result["suggested_config"] = _suggested(result)
    return result


def _exemplars(text: str, g: "SG.Grammar", path: Path, limit: int = 3) -> List[dict]:
    lines = text.splitlines()
    out = []
    for r in g.parse(text)[:limit]:
        out.append({"path": str(path), "line": r["start"] + 1,
                    "text": "\n".join(lines[r["start"]:min(r["end"], r["start"] + 8)])})
    return out


def _spec_path_tpl(spec: Path, base: Path, root: Path) -> Optional[str]:
    """Путь мастера относительно базы, с {capability} вместо имени капабилити."""
    try:
        rel = spec.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return None
    try:
        sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "feature-pipeline" / "scripts"))
        import skill_paths
        cap = skill_paths.master_capability(root)
        if cap and f"/{cap}/" in f"/{rel}":
            rel = rel.replace(cap, "{capability}")
    except Exception:  # noqa: BLE001
        pass
    return rel


def _suggested(result: dict) -> List[str]:
    """Готовые команды: ровно те ручки, что расходятся с форже-родными."""
    if result.get("matches_native"):
        return []
    g = result.get("grammar") or {}
    req = g.get("requirement") or {}
    scn = g.get("scenario") or {}
    nat, nreq = SG.NATIVE, SG.NATIVE["requirement"]
    out = []
    if req.get("level") != nreq["level"]:
        out.append(f"config.py set spec.grammar.requirement_level {req.get('level')}")
    if req.get("kind") != nreq["kind"]:
        out.append(f"config.py set spec.grammar.requirement_kind {req.get('kind')}")
    if req.get("lead"):
        out.append(f"config.py set spec.grammar.requirement_lead '{req.get('lead')}'")
    if req.get("scope") != nreq["scope"]:
        out.append(f"config.py set spec.grammar.requirement_scope {req.get('scope')}")
    if req.get("id_prefix") and req["id_prefix"] != nreq["id_prefix"]:
        out.append(f"config.py set spec.id_prefix {req['id_prefix']}")
    if scn.get("style") != nat["scenario"]["style"]:
        out.append(f"config.py set spec.grammar.scenario_style {scn.get('style')}")
    if scn.get("level") and scn["level"] != nat["scenario"]["level"]:
        out.append(f"config.py set spec.grammar.scenario_level {scn['level']}")
    sec = g.get("requirements_section") or []
    if not SG.markers_compatible(sec, nat["requirements_section"]):
        out.append(f"config.py set spec.grammar.requirements_section "
                   f"'{sec[0] if sec else ''}'")
    aud = g.get("audit_section") or []
    if not SG.markers_compatible(aud, nat["audit_section"]):
        out.append(f"config.py set spec.grammar.audit_section '{aud[0] if aud else ''}'")
    if g.get("provenance") != nat["provenance"]:
        out.append(f"config.py set spec.grammar.provenance {g.get('provenance')}")
    if result.get("spec_path") and result["spec_path"] != "specs/{capability}/spec.md":
        out.append(f"config.py set docs.master.spec_path '{result['spec_path']}'")
    # Состав разделов форже-шаблона на чужом мастере — гарантированный FAIL гейта за чужую
    # структуру: у проекта свои разделы, и требовать «Границы охвата» бессмысленно.
    out.append("config.py set spec.profile detected")
    return out


# ── запись / чтение кэша ───────────────────────────────────────────────

def out_path(root: Path, explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit)
    try:
        sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "feature-pipeline" / "scripts"))
        import skill_paths
        return skill_paths.spec_conventions_path(root)
    except Exception:  # noqa: BLE001
        return Path(root) / "ground" / "inventory" / "spec-conventions.json"


def load_cache(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) and "schema_version" in data else None


def is_fresh(cached: Optional[dict], root: Path) -> bool:
    """Свежесть — по отпечатку кандидатов: мастер не менялся, пересканировать нечего."""
    if not cached or cached.get("schema_version") != SCHEMA_VERSION:
        return False
    return cached.get("fingerprint") == fingerprint(_candidates(root))


def write(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gi = path.parent / ".gitignore"
    if not gi.exists():
        gi.write_text("# Эфемерный инвентарь проекта — снимается заново за секунды.\n*\n",
                      encoding="utf-8")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure(root: Path, *, refresh: bool = False, out: Optional[str] = None) -> dict:
    """Профиль детекта: из кэша, если мастер не менялся, иначе свежий скан."""
    path = out_path(Path(root), out)
    cached = load_cache(path)
    if not refresh and is_fresh(cached, Path(root)):
        return cached
    result = analyze(Path(root))
    if cached and isinstance(cached.get("research"), dict):
        result["research"] = cached["research"]       # разбор субагента переживает рескан
        result = _apply_research(result, cached["research"])
    write(result, path)
    return result


def _apply_research(result: dict, research: dict) -> dict:
    """Вывод субагента-ресерчера поверх детекта: он видит то, чего не берут регулярки."""
    g = research.get("grammar")
    if isinstance(g, dict):
        result["grammar"] = SG._deep_merge(result.get("grammar") or dict(SG.NATIVE), g)
        result["matches_native"] = SG.Grammar(result["grammar"]).is_native()
        result["confidence"] = max(float(result.get("confidence") or 0.0),
                                   float(research.get("confidence") or 0.0))
        result["warnings"] = [w for w in result.get("warnings", []) if w != "low_confidence"]
        result["suggested_config"] = _suggested(result)
    if research.get("spec_path"):
        result["spec_path"] = research["spec_path"]
    return result


def describe(result: dict) -> str:
    """Человекочитаемый разбор: что нашли, насколько уверены и что предложить человеку."""
    if "no_master" in result.get("warnings", []):
        return "Мастер-спеки в проекте нет — исследовать нечего (docs.master.enabled?)."
    if "no_requirements_found" in result.get("warnings", []):
        return (f"Мастер: {result['source'].get('spec')}\n"
                "Форма требований не распознана: ни одного блока требования регулярками не "
                "нашлось (частый случай — требования таблицей или сплошным текстом).\n"
                "Нужен разбор субагентом-ресерчером (контракт §4.0b subagent-prompts.md), его "
                "JSON применяется так:\n"
                "   analyze_spec.py --root <project> --apply-research <файл.json>\n"
                "До этого /forge-merge будет отказывать: писать в неразобранный мастер нельзя.")
    lines = [f"Мастер: {result['source'].get('spec')}",
             f"Уверенность: {result.get('confidence')} "
             f"({', '.join(f'{k}={v}' for k, v in sorted((result.get('axes') or {}).items()))})",
             ""]
    if result.get("grammar"):
        lines.append(SG.Grammar(result["grammar"]).describe())
    if result.get("matches_native"):
        lines.append("\nФормат форже-родной — профиль настраивать не нужно.")
    for ex in result.get("exemplars", [])[:2]:
        lines.append(f"\nПример ({Path(ex['path']).name}:{ex['line']}):")
        lines += [f"   {l}" for l in ex["text"].splitlines()[:5]]
    if result.get("warnings"):
        lines.append("\nЗамечания: " + ", ".join(result["warnings"]))
    if result.get("suggested_config"):
        lines.append("\nПрименить профиль (config.py — skills/config-helper/scripts/config.py):")
        lines += [f"   {c}" for c in result["suggested_config"]]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ресерч формата требований-мастера.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=None)
    ap.add_argument("--if-missing", action="store_true",
                    help="не сканировать, если валидный свежий кэш уже есть")
    ap.add_argument("--refresh", action="store_true", help="пересканировать в любом случае")
    ap.add_argument("--apply-research", default=None,
                    help="JSON субагента-ресерчера: вмержить в детект")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    path = out_path(root, args.out)

    if args.apply_research:
        raw = Path(args.apply_research)
        try:
            research = json.loads(raw.read_text(encoding="utf-8") if raw.exists()
                                  else args.apply_research)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"✗ не читается JSON ресерчера: {e}", file=sys.stderr)
            return 2
        result = load_cache(path) or analyze(root)
        result["research"] = research
        result = _apply_research(result, research)
        write(result, path)
    elif args.if_missing and is_fresh(load_cache(path), root):
        result = load_cache(path)
    else:
        result = ensure(root, refresh=args.refresh, out=args.out)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not args.quiet:
        print(describe(result))
        print(f"\nПрофиль: {path}")

    if "no_master" in result.get("warnings", []):
        return 0
    if result.get("matches_native"):
        return 0
    return 0 if float(result.get("confidence") or 0) >= CONFIDENCE_FLOOR else 2


if __name__ == "__main__":
    raise SystemExit(main())
