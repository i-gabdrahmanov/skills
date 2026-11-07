"""obsidian.py — Obsidian-слой графа (задача 3.3, D12, ТЗ §8.1, §8.2, §8.4, §10).

Аналитик работает в Obsidian, только с core-плагинами:
  • `.obsidian/graph.json` — `colorGroups` по видам (запрос `path:` из шаблона пути вида в онтологии; первая
    подходящая группа красит узел, поэтому более узкие запросы идут первыми). Слияние, а не перезапись: чужие
    ключи и группы пользователя сохраняются; свои запросы движок помнит в `.spec-graph/obsidian.json` и
    убирает только свои устаревшие; цвет, который пользователь поменял у нашей группы, остаётся;
  • `_graph/Начало.md` — входы (процессы, операции со счётчиками, сервисы), легенда цветов и ключей рёбер;
  • виды-таблицы `_graph/views/`: Bases (`.base`), если `bases` включён в `.obsidian/core-plugins.json`,
    иначе — те же таблицы Markdown-заметками (версия Obsidian в окружении неизвестна: слой по факту).
Оглавление в заметке сервиса (маркер graph:toc) пишет нарезка (1.6). Что и куда — данными в
`references/obsidian.json`; кода под конкретный вид здесь нет.
"""
from __future__ import annotations

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
    names = [v.get("file") for v in layer.get("views") or []]
    if len(names) != len(set(names)) or not all(isinstance(n, str) and n and "/" not in n for n in names):
        errs.append("views: file — уникальные имена без «/»")
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


def color_groups(ont: O.Ontology, palette: List[int]) -> List[Dict[str, Any]]:
    rows = []
    for i, (kind, spec) in enumerate(ont.kinds.items()):
        q, weight = _query(spec["path"])
        rows.append((-weight, i, {"query": q, "color": {"a": 1, "rgb": palette[i % len(palette)]}, "kind": kind}))
    return [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]


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
             f"Как ходить по графу и что можно править — [[_graph/{GUIDE_NAME[:-3]}|{GUIDE_NAME[:-3]}]].", ""]
    for e in layer["entries"]:
        kind = e["kind"]
        mine = sorted((n for n in nodes if n["kind"] == kind), key=lambda n: n["name"])
        lines += [f"## {ont.kinds[kind]['title_ru']} ({len(mine)})", ""]
        for n in mine:
            counts = ", ".join(f"{c['label']}: {len(_links(n['data'].get(c['key'])))}" for c in e.get("counts") or [])
            lines.append(f"- {F.link(n['name'])}" + (f" — {counts}" if counts else ""))
        lines += ([] if mine else ["- нет"]) + [""]
    lines += ["## Легенда", "", "| вид | цвет в графе | запрос группы |", "|---|---|---|"]
    for g in groups:
        lines.append(f"| {ont.kinds[g['kind']]['title_ru']} | {_hex(g['color']['rgb'])} | `{g['query']}` |")
    lines += ["", "| ключ в свойствах | смысл | обратный ключ |", "|---|---|---|"]
    for name, spec in ont.edges.items():
        inv = spec["inverse"] if spec["inverse"] != name else "— (симметрично)"
        lines.append(f"| `{name}` | {spec['title_ru']} | `{inv}` |")
    view_ext = ".base" if mode == "bases" else ""
    lines += ["", "## Виды", ""] + [f"- [[_graph/views/{v['file']}{view_ext}|{v['file']}]]" for v in layer["views"]] + [""]
    return "\n".join(lines)


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
    ours = color_groups(ont, layer["palette"])
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
    gp2 = vault / "_graph" / GUIDE_NAME
    if _write(gp2, GUIDE.read_text(encoding="utf-8"), dry):
        changed.append(gp2.relative_to(root).as_posix())
    sp = vault / "_graph" / START_NAME
    if _write(sp, _start(ont, layer, nodes, ours, mode), dry):
        changed.append(sp.relative_to(root).as_posix())
    new_state = {"queries": [g["query"] for g in ours], "views": sorted(wanted), "mode": mode}
    if new_state != state and not dry:
        state_p.parent.mkdir(parents=True, exist_ok=True)
        FS.write_text(state_p, json.dumps(new_state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"mode": mode, "changed": changed, "groups": len(ours), "views": len(wanted),
            "start": sp.relative_to(root).as_posix()}
