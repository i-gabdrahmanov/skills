"""obsidian.py — Obsidian-слой графа (задача 3.3, D12, ТЗ §8.1, §8.2, §8.4, §10).

Аналитик работает в Obsidian, только с core-плагинами:
  • `.obsidian/graph.json` — `colorGroups` по видам (запрос `path:` из шаблона пути вида в онтологии; первая
    подходящая группа красит узел, поэтому более узкие запросы идут первыми). Слияние, а не перезапись: чужие
    ключи и группы пользователя сохраняются; свои запросы движок помнит в `.spec-graph/obsidian.json` и
    убирает только свои устаревшие; цвет, который пользователь поменял у нашей группы, остаётся;
  • `_graph/Начало.md` — входы (процессы, операции со счётчиками, сервисы), легенда цветов и ключей рёбер;
  • группы по статусу (заглушка — серым) — первыми, раньше групп видов;
  • `_graph/maps/<узел>.canvas` — карты Canvas (JSON Canvas): Graph view рёбра не красит и типа связи не
    показывает, а на карте ребро подписано типом и окрашено, узлы — цветом вида. Раскладка детерминирована:
    узел в центре, кто на него ссылается — левее по шагам, на кого ссылается он — правее;
  • виды-таблицы `_graph/views/`: Bases (`.base`), если `bases` включён в `.obsidian/core-plugins.json`,
    иначе — те же таблицы Markdown-заметками (версия Obsidian в окружении неизвестна: слой по факту).
Обзор сервиса (graph:overview) и ход процесса (graph:flow) пишет синк (overview.py), легенду свойств
`_graph/Свойства.md` — этот слой. Что и куда — данными в
`references/obsidian.json`; кода под конкретный вид здесь нет.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import fm as F
from . import fsutil as FS
from . import ontology as O
from . import resolve as RS

LAYER = Path(__file__).resolve().parents[2] / "references" / "obsidian.json"
GUIDE = Path(__file__).resolve().parents[2] / "references" / "guide-analyst.md"
GUIDE_NAME = "Руководство аналитика.md"
STATE_REL = ".spec-graph/obsidian.json"
START_NAME = "Начало.md"


def load() -> Dict[str, Any]:
    return json.loads(LAYER.read_text(encoding="utf-8"))


def self_check(layer: Dict[str, Any], ont: O.Ontology) -> List[str]:
    errs = []
    if not layer.get("palette") or not all(isinstance(c, int) and 0 <= c <= 0xFFFFFF for c in layer["palette"]):
        errs.append("palette: непустой список 0xRRGGBB")
    for where, items in (("entries", layer.get("entries") or []), ("views", layer.get("views") or [])):
        for i, it in enumerate(items):
            kind = it.get("kind")
            if kind not in ont.kinds:
                errs.append(f"{where}[{i}]: kind «{kind}» — не вид онтологии")
                continue
            allowed = set(ont.fm_keys(kind)) | set(ont.scalars)
            counts = [c.get("key") for c in it.get("counts") or [] if isinstance(c, dict) and c.get("label")]
            if len(counts) != len(it.get("counts") or []):
                errs.append(f"{where}[{i}]: counts — {{key, label}}")
            for k in counts + (it.get("columns") or []) + ([it["group"]] if it.get("group") else []):
                if k not in allowed:
                    errs.append(f"{where}[{i}]: ключа «{k}» нет у вида {kind}")
    for i, g in enumerate(layer.get("status_groups") or []):
        if g.get("status") not in ont.statuses or not isinstance(g.get("rgb"), int):
            errs.append(f"status_groups[{i}]: status — из статусов онтологии, rgb — 0xRRGGBB")
    if len(layer.get("edge_palette") or []) < len(ont.edges):
        errs.append(f"edge_palette: цвет на каждое ребро онтологии ({len(ont.edges)})")
    m = layer.get("maps")
    if m is not None and (m.get("kind") not in ont.kinds or not isinstance(m.get("hops"), int) or m["hops"] < 1
                          or any(e not in ont.edges for e in m.get("skip_edges") or [])):
        errs.append("maps: kind — вид онтологии, hops ≥ 1, skip_edges — рёбра онтологии")
    names = [v.get("file") for v in layer.get("views") or []]
    if len(names) != len(set(names)) or not all(isinstance(n, str) and n and "/" not in n for n in names):
        errs.append("views: file — уникальные имена без «/»")
    errs += _flow_check(layer.get("flow"), ont)
    return errs


def _flow_check(fl: Any, ont: O.Ontology) -> List[str]:
    """Раздел flow (навигационные блоки, overview.py): виды и роли — из онтологии, рёбра — её рёбра."""
    if not isinstance(fl, dict):
        return ["flow: объект (навигационные блоки заметок)"]
    errs = []
    for k in ("step_edge", "publish_edge", "consume_edge", "call_edge", "data_edge"):
        if fl.get(k) not in ont.edges:
            errs.append(f"flow.{k}: ребро онтологии")
    for e in fl.get("outputs") or []:
        if e not in ont.edges:
            errs.append(f"flow.outputs: «{e}» — не ребро онтологии")
    for i, e in enumerate(fl.get("entries") or []):
        if e.get("kind") not in ont.kinds:
            errs.append(f"flow.entries[{i}]: kind «{e.get('kind')}» — не вид онтологии")
        elif e.get("role") is not None and e["role"] not in (ont.kinds[e["kind"]].get("roles") or []):
            errs.append(f"flow.entries[{i}]: у вида {e['kind']} нет роли «{e['role']}»")
        for k in ("via", "source", "callers"):
            if e.get(k) is not None and e[k] not in ont.edges:
                errs.append(f"flow.entries[{i}].{k}: ребро онтологии")
        if not e.get("group") or not e.get("trigger"):
            errs.append(f"flow.entries[{i}]: group и trigger — подписи")
    for k in fl.get("steps") or []:
        if k not in ont.kinds:
            errs.append(f"flow.steps: «{k}» — не вид онтологии")
    lc = fl.get("lifecycle") or {}
    if lc.get("kind") not in ont.kinds or any(lc.get(k) not in ont.edges for k in ("make", "close", "ui")) \
            or (lc.get("process") is not None and lc["process"] not in ont.edges):
        errs.append("flow.lifecycle: kind — вид онтологии, make/close/ui/process — её рёбра")
    return errs


def _links(v: Any) -> List[str]:
    vals = v if isinstance(v, list) else [v]
    return [t for t in (F.link_target(x) for x in vals) if t]


def _write(p: Path, text: str, dry: bool = False) -> bool:
    """Изменился ли файл; dry — только сравнить."""
    if p.is_file() and p.read_text(encoding="utf-8") == text:
        return False
    if not dry:
        p.parent.mkdir(parents=True, exist_ok=True)
        FS.write_text(p, text, encoding="utf-8")
    return True


# ─── graph view ────────────────────────────────────────────────────────────

def _query(template: str) -> Tuple[str, int]:
    """Запрос path: из шаблона пути вида: постоянные куски пути с «/» на концах (имена узлов «/» не содержат)."""
    head = template.rsplit("/", 1)[0] + "/"
    parts = [p for p in re.split(r"\{[^}]+\}", head)]
    segs = []
    for i, p in enumerate(parts):
        if not p:
            continue
        s = p if i == 0 else p.lstrip("/")
        segs.append(s if s.startswith("/") or i == 0 else "/" + s)
    segs = [s for s in segs if s.strip("/")]
    return " ".join(f'path:"{s}"' for s in segs), len(segs)


def color_groups(ont: O.Ontology, palette: List[int], status_groups: Optional[List[Dict[str, Any]]] = None
                 ) -> List[Dict[str, Any]]:
    # группы статуса — первыми: первая подходящая группа красит узел, заглушка серая при любом виде
    head = [{"query": f"[status:{g['status']}]", "color": {"a": 1, "rgb": g["rgb"]}, "status": g["status"],
             "title": g.get("title", g["status"])} for g in status_groups or []]
    rows = []
    for i, (kind, spec) in enumerate(ont.kinds.items()):
        q, weight = _query(spec["path"])
        rows.append((-weight, i, {"query": q, "color": {"a": 1, "rgb": palette[i % len(palette)]}, "kind": kind}))
    return head + [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]


def merge_graph_json(current: Optional[Dict[str, Any]], ours: List[Dict[str, Any]], prev_queries: List[str]
                     ) -> Dict[str, Any]:
    """Наши группы — первыми (узкие запросы раньше), затем группы пользователя; чужие ключи не трогаем."""
    data = dict(current or {})
    groups = [g for g in (data.get("colorGroups") or []) if isinstance(g, dict)]
    by_query = {g.get("query"): g for g in groups}
    mine = {g["query"] for g in ours}
    out = []
    for g in ours:
        have = by_query.get(g["query"])
        out.append({"query": g["query"], "color": have["color"] if have and have.get("color") else g["color"]})
    out += [g for g in groups if g.get("query") not in mine and g.get("query") not in prev_queries]
    data["colorGroups"] = out
    return data


# ─── окружение ─────────────────────────────────────────────────────────────

def bases_enabled(vault: Path) -> bool:
    p = vault / ".obsidian" / "core-plugins.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if isinstance(data, list):
        return "bases" in data
    return isinstance(data, dict) and data.get("bases") is True


# ─── Начало и виды ─────────────────────────────────────────────────────────

def _hex(rgb: int) -> str:
    return f"#{rgb:06x}"


def _start(ont: O.Ontology, layer: Dict[str, Any], nodes: List[Dict[str, Any]], groups: List[Dict[str, Any]],
           mode: str) -> str:
    lines = ["# Начало", "", "Точка входа в граф спецификации. Заметки и связи строит движок spec-graph; правки "
             "человека — проза вне маркеров и свойства в Properties.",
             f"Как ходить по графу и что можно править — [[_graph/{GUIDE_NAME[:-3]}|{GUIDE_NAME[:-3]}]]; "
             f"что значит каждое свойство заметки — [[_graph/{PROPS_NAME[:-3]}|{PROPS_NAME[:-3]}]].", ""]
    for e in layer["entries"]:
        kind = e["kind"]
        mine = sorted((n for n in nodes if n["kind"] == kind), key=lambda n: n["name"])
        lines += [f"## {ont.kinds[kind]['title_ru']} ({len(mine)})", ""]
        maps = layer.get("maps") or {}
        for n in mine:
            counts = ", ".join(f"{c['label']}: {len(_links(n['data'].get(c['key'])))}" for c in e.get("counts") or [])
            card = f" · [[_graph/{maps['dir']}/{n['name']}.canvas|карта]]" if maps.get("kind") == kind else ""
            lines.append(f"- {F.link(n['name'])}" + (f" — {counts}" if counts else "") + card)
        lines += ([] if mine else ["- нет"]) + [""]
    lines += ["## Легенда", "", "| вид | цвет в графе | запрос группы |", "|---|---|---|"]
    for g in groups:
        title = ont.kinds[g["kind"]]["title_ru"] if g.get("kind") else g["title"]
        lines.append(f"| {title} | {_hex(g['color']['rgb'])} | `{g['query']}` |")
    lines += ["", "| ключ в свойствах | смысл | обратный ключ | его смысл | цвет на картах |", "|---|---|---|---|---|"]
    colors = edge_colors(ont, layer)
    for name, spec in ont.edges.items():
        sym = spec["inverse"] == name
        inv = "— (симметрично)" if sym else f"`{spec['inverse']}`"
        lines.append(f"| `{name}` | {spec['title_ru']} | {inv} | {'' if sym else ont.key_ru(spec['inverse'])} | {colors[name]} |")
    view_ext = ".base" if mode == "bases" else ""
    lines += ["", "## Виды", ""] + [f"- [[_graph/views/{v['file']}{view_ext}|{v['file']}]]" for v in layer["views"]] + [""]
    return "\n".join(lines)


PROPS_NAME = "Свойства.md"


def _props(ont: O.Ontology) -> str:
    """_graph/Свойства.md — что значит каждое свойство заметки (Properties): смысл, у каких видов, кто пишет.
    Аналитик видел `creates_task`, `role`, `kind` без объяснения (обратная связь 2026-11-09)."""
    lines = ["# Свойства заметок", "",
             "Что значит каждое свойство (Properties) в заметках графа. Свойство без значения не пишется. Прямые связи "
             "движок ставит по спеке с цитатой-основанием (таблица «связь | цель | основание» внизу заметки), "
             "обратные выводит сам из прямых — их не правят. Свою связь можно добавить в Properties: синк примет её "
             "как «внесено человеком»; удалённую из Properties связь движок больше не предложит.", "",
             "## Виды узлов (`kind`)", "", "| kind | что это | где лежит |", "|---|---|---|"]
    for k, spec in ont.kinds.items():
        extra = ""
        if spec.get("roles"):
            extra = " · role: " + ", ".join(spec["roles"])
        if spec.get("subkinds"):
            extra = " · subkind: " + ", ".join(spec["subkinds"])
        lines.append(f"| `{k}` | {spec['title_ru']}{extra} | `{spec['path']}` |")
    lines += ["", "## Описание узла", "", "| свойство | смысл |", "|---|---|"]
    for key in ont.scalars:
        if key in ("blocks", "confidence"):
            continue
        lines.append(f"| `{key}` | {ont.key_ru(key) or '—'} |")
    lines += ["", "## Связи — пишет движок по спеке", "", "| свойство | смысл | от каких видов | к каким |", "|---|---|---|---|"]
    for name, e in ont.edges.items():
        lines.append(f"| `{name}` | {e['title_ru']} | {', '.join(e['from'])} | {', '.join(e['to'])} |")
    lines += ["", "## Обратные связи — выводятся из прямых", "", "| свойство | смысл | обратное к |", "|---|---|---|"]
    for name, e in ont.edges.items():
        if e["inverse"] != name:
            lines.append(f"| `{e['inverse']}` | {ont.key_ru(e['inverse'])} | `{name}` |")
    lines += ["", "## Флаги аналитика", "", "Ставятся в Properties вручную, когда дыра графа — не дыра, а так задумано:", "",
              "| свойство | у какого вида | смысл |", "|---|---|---|"]
    from . import completeness as CM
    for r in CM.load()["rules"]:
        if r.get("unless"):
            lines.append(f"| `{r['unless']}: true` | {r['kind']} | {r.get('unless_ru') or r['question'].format(name='…')} |")
    return "\n".join(lines) + "\n"


def _key_title(ont: O.Ontology, key: str) -> str:
    if key in ont.edges:
        return ont.edges[key]["title_ru"]
    edge = ont.edge_of_inverse(key)
    return f"← {ont.edges[edge]['title_ru']}" if edge else key


def _cell(v: Any) -> str:
    links = _links(v)
    if links:
        return ", ".join(F.link(t) for t in sorted(links))
    if v in (None, "", []):
        return ""
    return str(v).replace("|", "\\|")


def _markdown_view(ont: O.Ontology, view: Dict[str, Any], nodes: List[Dict[str, Any]]) -> str:
    kind = view["kind"]
    cols = view["columns"]
    head = ["| заметка | " + " | ".join(_key_title(ont, c) for c in cols) + " |",
            "|---|" + "---|" * len(cols)]
    mine = sorted((n for n in nodes if n["kind"] == kind), key=lambda n: n["name"])
    lines = [f"# {view['file']}", "", f"{ont.kinds[kind]['title_ru']}: {len(mine)}. Вид пересобирается движком при "
             "каждой проверке графа — правки руками не сохранятся.", ""]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for n in mine:
        g = ", ".join(sorted(_links(n["data"].get(view["group"])))) if view.get("group") else ""
        groups.setdefault(g, []).append(n)
    for g in sorted(groups):
        if view.get("group"):
            lines += [f"## {F.link(g) if g else 'без значения'}", ""]
        lines += head + [f"| {F.link(n['name'])} | " + " | ".join(_cell(n["data"].get(c)) for c in cols) + " |"
                         for n in groups[g]] + [""]
    return "\n".join(lines)


def _base_view(ont: O.Ontology, view: Dict[str, Any]) -> str:
    lines = ["filters:", "  and:", f"    - 'note.kind == \"{view['kind']}\"'", "views:", "  - type: table",
             f"    name: {json.dumps(view['file'], ensure_ascii=False)}", "    order:", "      - file.name"]
    lines += [f"      - note.{c}" for c in view["columns"]]
    if view.get("group"):
        lines += ["    groupBy:", f"      property: note.{view['group']}", "      direction: ASC"]
    return "\n".join(lines) + "\n"


# ─── карты Canvas ──────────────────────────────────────────────────────────

_W, _H, _GX, _GY = 380, 140, 220, 50


def edge_colors(ont: O.Ontology, layer: Dict[str, Any]) -> Dict[str, str]:
    pal = layer.get("edge_palette") or ["#9e9e9e"]
    return {e: pal[i % len(pal)] for i, e in enumerate(ont.edges)}


def forward_edges(ont: O.Ontology, nodes: List[Dict[str, Any]], skip: List[str]) -> List[Tuple[str, str, str]]:
    """Прямые рёбра vault по frontmatter (обратные ключи выводятся из них — не дублируем)."""
    names = {n["name"] for n in nodes}
    out = set()
    for n in nodes:
        for edge in ont.edges:
            if edge in skip:
                continue
            for t in _links(n["data"].get(edge)):
                if t in names and t != n["name"]:
                    out.add(tuple(sorted((n["name"], t))) + (edge,) if ont.is_symmetric(edge) else (n["name"], t, edge))
    return sorted(out)


def _nid(name: str) -> str:
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]


def canvas(ont: O.Ontology, layer: Dict[str, Any], nodes: Dict[str, Dict[str, Any]], edges: List[Tuple[str, str, str]],
           center: str, kind_rgb: Dict[str, int]) -> str:
    """Карта узла, слева направо: колонка 0 — он сам; 1 — все его соседи (кто формирует, закрывает, его UI,
    связанные); 2 и дальше — на что ссылаются соседи (что шедулер вызывает, публикует, читает). Узлы вида
    карты (соседние операции) дальше не разворачиваются — их мир на их собственной карте. До hops шагов и
    max_nodes узлов. Рёбра — все прямые между узлами карты."""
    m = layer["maps"]
    status_rgb = {g["status"]: g["rgb"] for g in layer.get("status_groups") or []}
    col = {center: 0}
    frontier = [center]
    for step in range(1, m["hops"] + 1):
        nxt = []
        for x in frontier:
            if step > 1 and nodes[x]["kind"] == m["kind"]:
                continue
            for a, b, _e in edges:
                there = b if a == x else a if (b == x and step == 1) else None
                if there is not None and there not in col and len(col) < m["max_nodes"]:
                    col[there] = step
                    nxt.append(there)
        frontier = sorted(set(nxt))
    order = {k: i for i, k in enumerate(ont.kinds)}
    by_col: Dict[int, List[str]] = {}
    for name, c in col.items():
        by_col.setdefault(c, []).append(name)
    out_nodes = []
    pos = {}
    for c, names in sorted(by_col.items()):
        names.sort(key=lambda x: (order.get(nodes[x]["kind"], 99), x))
        top = -(len(names) * (_H + _GY) - _GY) // 2
        for i, name in enumerate(names):
            pos[name] = (c * (_W + _GX), top + i * (_H + _GY))
            out_nodes.append({"id": _nid(name), "type": "file", "file": nodes[name]["path"], "x": pos[name][0],
                              "y": pos[name][1], "width": _W, "height": _H,
                              "color": _hex(status_rgb.get(nodes[name]["data"].get("status"))
                                            or kind_rgb.get(nodes[name]["kind"], 0x9e9e9e))})
    colors = edge_colors(ont, layer)
    out_edges = []
    for a, b, e in edges:
        if a in pos and b in pos:
            right = pos[a][0] <= pos[b][0]
            out_edges.append({"id": _nid(f"{a}|{e}|{b}"), "fromNode": _nid(a), "toNode": _nid(b),
                              "fromSide": "right" if right else "left", "toSide": "left" if right else "right",
                              "toEnd": "none" if ont.is_symmetric(e) else "arrow",
                              "label": ont.edges[e]["title_ru"], "color": colors[e]})
    return json.dumps({"nodes": out_nodes, "edges": out_edges}, ensure_ascii=False, indent=1) + "\n"


def build(root: Path, cfg: Dict[str, Any], dry: bool = False) -> Dict[str, Any]:
    """Записать слой; dry — ничего не писать, только перечислить, что изменилось бы (гейт check)."""
    root = Path(root)
    ont = O.load(root)
    layer = load()
    errs = self_check(layer, ont)
    if errs:
        raise O.OntologyError("Obsidian-слой: " + "; ".join(errs[:5]))
    vault = root / cfg["vault"]
    nodes = []
    for n in RS.vault_index(root, cfg):
        data, _ = F.read((vault / n["path"]).read_bytes().decode("utf-8"))
        nodes.append(dict(n, data=data))
    state_p = root / STATE_REL
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.is_file() else {}
    changed = []
    ours = color_groups(ont, layer["palette"], layer.get("status_groups"))
    gp = vault / ".obsidian" / "graph.json"
    try:
        current = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else None
    except ValueError:
        raise O.OntologyError(f"{gp.relative_to(root)}: не JSON — исправьте файл в Obsidian или удалите его")
    merged = merge_graph_json(current, ours, state.get("queries") or [])
    if merged != current:
        _write(gp, json.dumps(merged, ensure_ascii=False, indent=2) + "\n", dry)
        changed.append(gp.relative_to(root).as_posix())
    mode = "bases" if bases_enabled(vault) else "markdown"
    views_dir = vault / "_graph" / "views"
    wanted = set()
    for v in layer["views"]:
        if mode == "bases":
            p, text = views_dir / f"{v['file']}.base", _base_view(ont, v)
        else:
            p, text = views_dir / f"{v['file']}.md", _markdown_view(ont, v, nodes)
        wanted.add(p.name)
        if _write(p, text, dry):
            changed.append(p.relative_to(root).as_posix())
    for old in state.get("views") or []:                 # виды прежнего режима — наши, снимаем
        p = views_dir / old
        if old not in wanted and p.is_file():
            if not dry:
                p.unlink()
            changed.append(p.relative_to(root).as_posix())
    maps = layer.get("maps")
    wanted_maps = set()
    if maps:
        by_name = {n["name"]: n for n in nodes}
        kind_rgb = {g["kind"]: g["color"]["rgb"] for g in ours if g.get("kind")}
        edges = forward_edges(ont, nodes, maps.get("skip_edges") or [])
        maps_dir = vault / "_graph" / maps["dir"]
        for n in sorted((x for x in nodes if x["kind"] == maps["kind"]), key=lambda x: x["name"]):
            p = maps_dir / f"{n['name']}.canvas"
            wanted_maps.add(p.name)
            if _write(p, canvas(ont, layer, by_name, edges, n["name"], kind_rgb), dry):
                changed.append(p.relative_to(root).as_posix())
        for old in state.get("maps") or []:              # карта переименованного/снятого узла — наша, снимаем
            p = maps_dir / old
            if old not in wanted_maps and p.is_file():
                if not dry:
                    p.unlink()
                changed.append(p.relative_to(root).as_posix())
    gp2 = vault / "_graph" / GUIDE_NAME
    if _write(gp2, GUIDE.read_text(encoding="utf-8"), dry):
        changed.append(gp2.relative_to(root).as_posix())
    sp = vault / "_graph" / START_NAME
    if _write(sp, _start(ont, layer, nodes, ours, mode), dry):
        changed.append(sp.relative_to(root).as_posix())
    pp = vault / "_graph" / PROPS_NAME
    if _write(pp, _props(ont), dry):
        changed.append(pp.relative_to(root).as_posix())
    new_state = {"queries": [g["query"] for g in ours], "views": sorted(wanted), "mode": mode, "maps": sorted(wanted_maps)}
    if new_state != state and not dry:
        state_p.parent.mkdir(parents=True, exist_ok=True)
        FS.write_text(state_p, json.dumps(new_state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"mode": mode, "changed": changed, "groups": len(ours), "views": len(wanted), "maps": len(wanted_maps),
            "start": sp.relative_to(root).as_posix()}
