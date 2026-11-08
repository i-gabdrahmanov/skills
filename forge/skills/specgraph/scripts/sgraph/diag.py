"""diag.py — пакет диагностики `/forge-graph diag` (задача 4.3, D17).

Единственный канал, по которому знание из окружения доходит до разработки: реальной спеки разработка не
увидит. Пакет безопасен для выноса — в нём только ФОРМА: коды, числа, id единиц работы (хеши), хеши слов и
файлов (общий словарь — как есть), виды и рёбра онтологии, id правил. Имён узлов, путей и текста спеки нет.

Самопроверка утечки — кодом, перед записью: если последовательность из 4 слов или подстрока из 24 знаков
(от начала слова корпуса) из исходников или заметок vault встретилась в пакете, файл не пишется, а отказ
называет номера строк. Пакета без самопроверки не бывает. `level="excerpt"` — по явному решению
пользователя добавляет выбранные блоки текстом с пометкой «ПРОВЕРЬ ПЕРЕД ВЫНОСОМ»; на этот раздел
самопроверка не действует, на остальное — как обычно.

Разделы: 1 версии, 2 окружение, 3 прогоны, 4 здоровье графа, 5 где буксовала модель, 6 правила
(попадания, промахи с формой заголовка, ложные срабатывания; рёбра по происхождению и доле отклонённого),
7 профиль входа и кандидаты в виды, 8 самопроверка и обратная связь, 9 машинный блок JSON. Под лимит
строк сначала режутся списки (10 → 5), потом разделы 7 и 8; разделы 1–6 не режутся. Хранятся 10 пакетов.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import VERSION
from . import blocks as B
from . import fsutil as FS
from . import metrics as M
from . import ontology as O
from . import profile as PF
from . import queue as Q

DIAG_DIR = "_graph/diag"
KEEP = 10
MAX_LINES = 400
SHINGLE_WORDS, SHINGLE_CHARS = 4, 24
EXT_ROOT = Path(__file__).resolve().parents[4]
_WORDS = re.compile(r"\w+", re.U)
_MANIFESTS = ("gigacode-extension.json", "qwen-extension.json")
_QUEUES_SECTIONS = ("draft-file", "assign-section", "link-note", "card")


class DiagError(Exception):
    def __init__(self, message: str, code: str, lines: Optional[List[int]] = None):
        super().__init__(message)
        self.code = code
        self.lines = lines or []


def _load(p: Path, empty: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else empty
    except (OSError, ValueError):
        return empty


# ─── самопроверка утечки ───────────────────────────────────────────────────

def _norm(text: str) -> str:
    return " ".join(text.replace(" ", " ").lower().split())


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


class Corpus:
    """Отпечатки текста окружения: 4-словные последовательности и 24-знаковые окна от начала слова."""

    def __init__(self, texts: Iterable[str]):
        self.words: Set[int] = set()
        self.chars: Set[int] = set()
        for t in texts:
            n = _norm(t)
            ws = _WORDS.findall(n)
            for i in range(len(ws) - SHINGLE_WORDS + 1):
                self.words.add(_h(" ".join(ws[i:i + SHINGLE_WORDS])))
            for m in _WORDS.finditer(n):
                w = n[m.start():m.start() + SHINGLE_CHARS]
                if len(w) == SHINGLE_CHARS:
                    self.chars.add(_h(w))

    def leaks(self, text: str) -> List[int]:
        """Номера строк (с 1), где есть текст окружения."""
        bad = []
        for no, line in enumerate(text.split("\n"), 1):
            n = _norm(line)
            ws = _WORDS.findall(n)
            hit = any(_h(" ".join(ws[i:i + SHINGLE_WORDS])) in self.words for i in range(len(ws) - SHINGLE_WORDS + 1))
            if not hit:
                hit = any(_h(n[i:i + SHINGLE_CHARS]) in self.chars for i in range(len(n) - SHINGLE_CHARS + 1))
            if hit:
                bad.append(no)
        return bad


def corpus(root: Path, cfg: Dict[str, Any]) -> Corpus:
    texts = []
    for s in cfg.get("sources") or []:                    # исключённые файлы — тоже текст окружения
        base = Path(root) / s["root"]
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file() and p.suffix.lower() in (".md", ".markdown", ".txt", ".xml", ".csv", ".json", ".yaml", ".yml"):
                try:
                    texts.append(p.read_bytes().decode("utf-8", "replace"))
                except OSError:
                    pass
    vault = Path(root) / cfg["vault"]
    for p in sorted(vault.rglob("*.md")) if vault.is_dir() else []:
        if p.relative_to(vault).parts[0] not in ("_graph",):
            texts.append(p.read_bytes().decode("utf-8", "replace"))
    return Corpus(texts)


# ─── сбор ──────────────────────────────────────────────────────────────────

def _versions(root: Path, ont: O.Ontology) -> Dict[str, Any]:
    man = next((_load(EXT_ROOT / m, {}) for m in _MANIFESTS if (EXT_ROOT / m).is_file()), {})
    local = Path(root) / O.LOCAL_REL
    return {"forge": man.get("forgeRevision") or "источник (поставка не собрана)", "engine": VERSION,
            "ontology": ont.version,
            "ontology_local": hashlib.sha1(local.read_bytes()).hexdigest()[:12] if local.is_file() else "нет",
            "runtime": [m.split("-")[0] for m in _MANIFESTS if (EXT_ROOT / m).is_file()] or ["нет манифеста"],
            "python": ".".join(map(str, sys.version_info[:3]))}


def _environment(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    man = next((_load(EXT_ROOT / m, {}) for m in _MANIFESTS if (EXT_ROOT / m).is_file()), {})
    hooks = _load(EXT_ROOT / "hooks" / "hooks.json", {})
    hooks_text = json.dumps(hooks)
    vault = Path(root) / cfg["vault"]
    plugins = _load(vault / ".obsidian" / "core-plugins.json", None)
    bases = (("bases" in plugins) if isinstance(plugins, list) else
             (plugins.get("bases") is True) if isinstance(plugins, dict) else None)
    return {"mcp_spec_graph": "spec-graph" in (man.get("mcpServers") or {}),
            "hook_graph_guard": "graph-guard.py" in hooks_text, "hook_count": hooks_text.count('"command"'),
            "installed_in_project": (Path(root) / ".gigacode" / "extensions" / "spec-graph").is_dir(),
            "obsidian_core_plugins": "нет файла" if plugins is None else "есть",
            "obsidian_bases": bases, "obsidian_layer": _load(Path(root) / ".spec-graph/obsidian.json", {}).get("mode")}


def _steps(root: Path) -> Dict[str, int]:
    """Нарушений по гейтам шагов (только число: тексты нарушений называют узлы)."""
    scripts = Path(__file__).resolve().parents[1]
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import graph_gate as G  # noqa: WPS433
    out = {}
    for step, fn in (("map", G.check_map), ("link", G.check_link), ("check", G.check_check)):
        try:
            out[step] = len(fn(Path(root))[0])
        except Exception:  # noqa: BLE001 — диагностика не падает на сломанном шаге
            out[step] = -1
    return out


def _runs(root: Path, top: int) -> List[Dict[str, Any]]:
    d = Path(root) / M.RUNS_REL
    out = []
    for kd in sorted(d.iterdir()) if d.is_dir() else []:
        s = M.summarize(root, kd.name, top=top)
        calls = M.read_calls(root, kd.name)
        ts = [c.get("t", 0) for c in calls if c.get("t")]
        units = sum(1 for c in calls if c.get("tool") == "graph_next" and c.get("outcome") == "ok")
        out.append({"key": kd.name, "calls": s["calls"], "minutes": round((max(ts) - min(ts)) / 60000, 1) if ts else 0,
                    "units_leased": units, "outcomes": {t: v for t, v in sorted(s["by_tool"].items())},
                    "counters": s["counters"], "top_codes": s["top_codes"], "rejected_units": s["most_rejected_refs"],
                    "engine_errors": s["engine_errors"]})
    return out


def _queues(root: Path, top: int) -> Dict[str, Any]:
    out = {}
    for name in _QUEUES_SECTIONS:
        p = Path(root) / Q.QUEUE_REL / f"{name}.json"
        data = _load(p, None)
        if not data:
            continue
        items = data.get("items") or []
        hard = sorted((it for it in items if it.get("attempts")), key=lambda it: (-it["attempts"], it["id"]))[:top]
        out[name] = {"total": len(items), "done": sum(1 for it in items if it["status"] == "done"),
                     "abandoned": [{"id": it["id"], "codes": it.get("last_codes") or []}
                                   for it in items if it["status"] == "abandoned"][:top],
                     "hardest": [{"id": it["id"], "attempts": it["attempts"], "codes": it.get("last_codes") or [],
                                  "shapes": _shapes(root, it.get("refs") or {})} for it in hard]}
    return out


def _shapes(root: Path, refs: Dict[str, Any]) -> List[str]:
    """Формы заголовков разделов единицы (по профайлеру), без текста."""
    ids = refs.get("sections") or refs.get("blocks") or []
    out = []
    for bid in ids[:5]:
        rel = bid.rsplit("#", 1)[0]
        data = B.load(root, rel) or {}
        blk = next((b for b in data.get("blocks", []) if b["id"] == bid), None)
        if blk and blk.get("type") == "heading":
            out.append(f"h{blk.get('level', '?')}:{PF._heading_shape(blk.get('title', ''))}")
    return sorted(set(out))


def _rules(root: Path, cfg: Dict[str, Any], ont: O.Ontology, top: int) -> Dict[str, Any]:
    from . import extract as X
    cands = []
    d = Path(root) / X.CANDIDATES_REL
    for p in sorted(d.rglob("*.json")) if d.is_dir() else []:
        cands += _load(p, {}).get("candidates") or []
    snap = _load(Path(root) / ".spec-graph/catalog.approved.json", {}) or _load(Path(root) / ".spec-graph/catalog.json", {})
    nodes = snap.get("nodes") or []
    approved = {(n["kind"], n["name"].lower()) for n in nodes} | {(n["kind"], str(n.get("key"))) for n in nodes if n.get("key")}
    try:
        rules = X.load_rules(cfg)["rules"]
    except Exception:  # noqa: BLE001
        rules = []
    per_rule = {r["id"]: {"kind": r["kind"], "hits": 0, "false": 0} for r in rules}
    covered = set()
    for c in cands:
        r = per_rule.setdefault(c.get("rule", "?"), {"kind": c.get("kind"), "hits": 0, "false": 0})
        k1, k2 = (c.get("kind"), (c.get("name_hint") or "").lower()), (c.get("kind"), str(c.get("key")))
        if k1 in approved or (c.get("key") and k2 in approved):
            r["hits"] += 1
            covered.update({k1, k2})
        else:
            r["false"] += 1
    misses: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        if (n["kind"], n["name"].lower()) in covered or (n.get("key") and (n["kind"], str(n["key"])) in covered):
            continue
        m = misses.setdefault(n["kind"], {"count": 0, "shapes": {}, "rules": sorted(i for i, r in per_rule.items()
                                                                                     if r["kind"] == n["kind"])})
        m["count"] += 1
        for s in n.get("sources") or []:
            for sh in _shapes(root, {"sections": s.get("sections") or []}) or ["нет раздела"]:
                m["shapes"][sh] = m["shapes"].get(sh, 0) + 1
    edges: Dict[str, Dict[str, int]] = {}
    ed = Path(root) / ".spec-graph" / "edges"
    for p in sorted(ed.glob("*.json")) if ed.is_dir() else []:
        for x in _load(p, {}).get("proposals") or []:
            origin = "rule" if str(x.get("origin", "")).startswith("rule:") else x.get("origin", "?")
            e = edges.setdefault(x.get("edge", "?"), {})
            e[origin] = e.get(origin, 0) + 1
            if x.get("status") == "rejected":
                e["rejected"] = e.get("rejected", 0) + 1
    return {"per_rule": dict(sorted(per_rule.items())), "misses": dict(sorted(misses.items())),
            "edges": dict(sorted(edges.items()))}


def _health(root: Path, top: int) -> Dict[str, Any]:
    v = _load(Path(root) / ".spec-graph/validate.json", {})
    hist = _load(Path(root) / M.HEALTH_REL, {})
    runs = hist.get("runs") or []
    trend = [{"errors": r.get("metrics", {}).get("errors"), "warnings": r.get("metrics", {}).get("warnings"),
              "new_findings": r.get("new_findings")} for r in runs[-top:]]
    cov = _load(Path(root) / ".spec-graph/coverage.json", {}).get("files") or {}
    tot = {k: sum(f.get(k, 0) for f in cov.values()) for k in ("blocks", "assigned", "unplaced", "unassigned")}
    transfer = v.get("transfer") or []
    return {"notes": v.get("notes"), "errors": v.get("errors"), "warnings": v.get("warnings"),
            "counts": v.get("counts") or {}, "trend": trend, "stable": hist.get("stable"),
            "coverage": tot, "files": len(cov), "files_full": sum(1 for t in transfer if t.get("full"))}


def _profile(root: Path, cfg: Dict[str, Any], top: int) -> Dict[str, Any]:
    try:
        p = PF.profile(root, cfg, limit=0)
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    masker = PF.Masker(root)
    cat = _load(Path(root) / ".spec-graph/catalog.json", {})
    kinds = sorted({masker.h("k", str(r["suggested_kind"]).lower()) for r in cat.get("not_nodes") or []
                    if r.get("suggested_kind")})
    return {"files": p["files"], "blocks": p["totals"]["blocks"], "heading_shapes": p["totals"]["heading_shapes"],
            "heading_words": p["totals"]["heading_words"][:top], "new_kind_candidates": kinds}


def collect(root: Path, cfg: Dict[str, Any], top: int = 10) -> Dict[str, Any]:
    ont = O.load(root)
    return {"versions": _versions(root, ont), "environment": _environment(root, cfg), "steps": _steps(root),
            "runs": _runs(root, top), "health": _health(root, top), "queues": _queues(root, top),
            "rules": _rules(root, cfg, ont, top), "profile": _profile(root, cfg, top),
            "feedback": {"self_check": "нет данных (задача 4.5)", "analyst": "нет данных (задача 4.8)",
                         "learned_rules": "нет данных (задача 4.7)"}}


# ─── рендер ────────────────────────────────────────────────────────────────

def _kv(d: Dict[str, Any]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in d.items()) or "—"


def render_excerpts(excerpts: List[Tuple[str, str]]) -> str:
    L = ["## Фрагменты — ПРОВЕРЬ ПЕРЕД ВЫНОСОМ", "",
         "Добавлены по решению пользователя (level excerpt); самопроверка утечки на этот раздел не действует.", ""]
    for bid, text in excerpts:
        L += [f"### {bid}", "", "```text", text.rstrip("\n"), "```", ""]
    return "\n".join(L)


def render(data: Dict[str, Any], key: str, stamp: str, top: int = 10, short: bool = False) -> str:
    v, env, h, rules = data["versions"], data["environment"], data["health"], data["rules"]
    L = [f"# Диагностика spec-graph — {stamp}, прогон {key}", "",
         "Пакет без текста спеки: коды, числа, хеши. Самопроверка утечки пройдена перед записью.", "",
         "## 1. Версии", "", f"- forge: {v['forge']}; движок: {v['engine']}; онтология: v{v['ontology']}, локальная: "
         f"{v['ontology_local']}", f"- рантайм: {', '.join(v['runtime'])}; python: {v['python']}", "",
         "## 2. Окружение", "", f"- MCP spec-graph в манифесте: {env['mcp_spec_graph']}; хук graph-guard: "
         f"{env['hook_graph_guard']}; команд хуков: {env['hook_count']}; поставка в проекте: {env['installed_in_project']}",
         f"- Obsidian: core-plugins.json — {env['obsidian_core_plugins']}; bases: {env['obsidian_bases']}; "
         f"слой видов: {env['obsidian_layer']}", "",
         "## 3. Прогоны", "", f"- нарушений гейтов сейчас: {_kv(data['steps'])} (-1 — гейт не отработал)"]
    for r in data["runs"][-top:]:
        L += [f"- `{r['key']}`: вызовов {r['calls']}, минут {r['minutes']}, единиц выдано {r['units_leased']}",
              f"  - счётчики: {_kv(r['counters'])}"]
        for tool, outs in r["outcomes"].items():
            L.append(f"  - {tool}: {_kv(outs)}")
    L += ["", "## 4. Здоровье графа", "",
          f"- заметок {h['notes']}, ошибок {h['errors']}, предупреждений {h['warnings']}; стабилизация: {h['stable']}",
          f"- по кодам: {_kv(h['counts'])}", f"- покрытие блоков: {_kv(h['coverage'])}; файлов {h['files']}, "
          f"перенесено полностью {h['files_full']}",
          "- тренд (последние снимки): " + ("; ".join(_kv(t) for t in h["trend"][-top:]) or "—"), "",
          "## 5. Где буксовала модель", ""]
    for r in data["runs"][-3:]:
        L.append(f"- `{r['key']}`: коды отказов — " + (", ".join(f"{c} ×{n}" for c, n in r["top_codes"][:top]) or "—"))
        if r["engine_errors"]:
            L.append("  - ошибки движка: " + ", ".join(f"{c} ×{n}" for c, n in r["engine_errors"][:top]))
    for name, q in data["queues"].items():
        L.append(f"- очередь {name}: единиц {q['total']}, сделано {q['done']}, брошено {len(q['abandoned'])}")
        for it in q["hardest"][:top]:
            L.append(f"  - `{it['id']}` попыток {it['attempts']}, коды {', '.join(it['codes']) or '—'}, "
                     f"формы {', '.join(it['shapes']) or '—'}")
        for it in q["abandoned"][:top]:
            L.append(f"  - брошена `{it['id']}`: {', '.join(it['codes']) or '—'}")
    L += ["", "## 6. Правила", "", "| правило | вид | попадания | ложные |", "|---|---|---|---|"]
    L += [f"| {rid} | {r['kind']} | {r['hits']} | {r['false']} |" for rid, r in rules["per_rule"].items()]
    L += ["", "Промахи — узел вида в утверждённом каталоге без кандидата экстрактора:", ""]
    for kind, m in rules["misses"].items():
        L.append(f"- {kind}: промахов {m['count']}; формы заголовков: {_kv(dict(sorted(m['shapes'].items())))}; "
                 f"правила вида: {', '.join(m['rules']) or 'нет'}")
    if not rules["misses"]:
        L.append("- нет")
    L += ["", "Рёбра по происхождению (rejected — отклонено человеком или синком):", ""]
    L += [f"- {e}: {_kv(c)}" for e, c in rules["edges"].items()] or ["- нет"]
    if short:
        L += ["", "## 7–8. Сокращено под лимит строк", ""]
    else:
        p, fb = data["profile"], data["feedback"]
        L += ["", "## 7. Профиль входа", "",
              f"- файлов {p.get('files')}; блоки: {_kv(p.get('blocks') or {})}",
              f"- формы заголовков: {_kv(p.get('heading_shapes') or {})}",
              "- слова заголовков: " + (", ".join(f"{w} {n}" for w, n in (p.get("heading_words") or [])[:top]) or "—"),
              f"- кандидаты в новые виды: {', '.join(p.get('new_kind_candidates') or []) or 'нет'}",
              f"- выученные правила: {fb['learned_rules']}", "",
              "## 8. Самопроверка и обратная связь", "", f"- самопроверка: {fb['self_check']}",
              f"- обратная связь аналитиков: {fb['analyst']}"]
    L += ["", "## 9. Машинный блок", "", "```json", json.dumps(data, ensure_ascii=False, sort_keys=True), "```", ""]
    return "\n".join(L)


def _shrink(data: Dict[str, Any], top: int) -> Dict[str, Any]:
    d = json.loads(json.dumps(data))
    for r in d["runs"]:
        for k in ("top_codes", "rejected_units", "engine_errors"):
            r[k] = r[k][:top]
    for q in d["queues"].values():
        q["hardest"], q["abandoned"] = q["hardest"][:top], q["abandoned"][:top]
    d["health"]["trend"] = d["health"]["trend"][-top:]
    if isinstance(d["profile"].get("heading_words"), list):
        d["profile"]["heading_words"] = d["profile"]["heading_words"][:top]
    return d


def build(root: Path, cfg: Dict[str, Any], key: Optional[str] = None, level: str = "form",
          excerpt_blocks: Optional[List[str]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Собрать пакет, проверить на утечку, записать в _graph/diag/ (хранится 10)."""
    if level not in ("form", "excerpt"):
        raise DiagError("level — form | excerpt", "E-arg-level")
    key = key or M.current_key(root)
    t = time.localtime(now if now is not None else time.time())
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", t)
    data = collect(root, cfg)
    text, cut = "", "нет"
    for top, short in ((10, False), (5, False), (5, True)):
        d = _shrink(data, top)
        text = render(d, key, stamp, top=top, short=short)
        if text.count("\n") + 1 <= MAX_LINES:
            cut = "нет" if top == 10 else ("списки до 5" if not short else "списки до 5, разделы 7–8")
            break
    bad = corpus(root, cfg).leaks(text)
    if bad:
        raise DiagError(f"самопроверка утечки: текст окружения в строках пакета {', '.join(map(str, bad[:20]))} — "
                        "пакет не записан", "E-diag-leak", bad)
    if level == "excerpt":
        picked = []
        for bid in excerpt_blocks or []:
            data_b = B.load(root, bid.rsplit("#", 1)[0]) or {}
            blk = next((b for b in data_b.get("blocks", []) if b["id"] == bid), None)
            if blk is None:
                raise DiagError(f"нет блока {bid}", "E-arg-blocks")
            picked.append((bid, blk["text"]))
        if not picked:
            raise DiagError("level excerpt — с блоками, которые выбрал пользователь (blocks)", "E-arg-blocks")
        text = text.rstrip("\n") + "\n\n" + render_excerpts(picked)
    out_dir = Path(root) / cfg["vault"] / DIAG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"diag-{time.strftime('%Y%m%d-%H%M%S', t)}-{key}.md"
    FS.write_text((out_dir / name), text, encoding="utf-8")
    old = sorted(out_dir.glob("diag-*.md"))
    for p in old[:-KEEP]:
        p.unlink()
    rel = (out_dir / name).relative_to(Path(root)).as_posix()
    return {"file": rel, "lines": text.count("\n") + 1, "cut": cut, "level": level, "kept": min(len(old), KEEP)}
