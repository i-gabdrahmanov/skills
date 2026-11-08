"""sync.py — синк рёбер в заметки vault (задача 2.4, рычаг 2, D6, D9).

Прямое ребро записывается у владельца (вершины «from» по онтологии), обратное ВЫВОДИТСЯ из всех прямых —
поэтому «ребро с одной стороны» невозможно по построению (класс ремонтных проходов прототипа исчезает).

Синк:
  • прямые ключи FM — из предложений (status proposed/accepted; rejected — нет) плюс ссылки, которые человек
    добавил в Properties сам (`manual`: прямая ссылка в FM без предложения);
  • ссылка, которую синк писал раньше, а человек убрал из FM, — человек её отклонил: предложение
    становится rejected и больше не возвращается (журнал записанного — `.spec-graph/synced.json`);
  • таблица оснований — маркер `graph:edges` в теле: связь | цель | основание | пояснение | источник;
  • обратные ключи у ВСЕХ заметок пересчитываются из прямых целиком (устаревшие уходят сами); у
    симметричного ребра (`related`) — зеркало; ручная правка обратного ключа перезаписывается и попадает в
    отчёт `W-manual-inverse`;
  • `belongs_to` ставит нарезка (из каталога) — синк его не трогает, но выводит из него `operations`.
Повтор без изменений входа — 0 изменённых файлов. Заметки — байтами (CRLF исходника не трогаем).
`dry=True` — ничего не пишет, только считает (гейт link проверяет так «синк чистый»).
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from . import edges as E
from . import fm as F
from . import fsutil as FS
from . import ontology as O
from . import resolve as RS

SYNCED_REL = ".spec-graph/synced.json"
_FIXED = ("belongs_to",)                  # ставит нарезка из каталога


def _cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _targets(values: Any) -> List[str]:
    return [t for t in (F.link_target(v) for v in (values or []) if isinstance(v, str)) if t]


def sync(root: Path, cfg: Dict[str, Any], dry: bool = False) -> Dict[str, Any]:
    root = Path(root)
    ont = O.load(root)
    vault = root / cfg["vault"]
    index = RS.vault_index(root, cfg)
    names = {n["name"] for n in index}
    notes: Dict[str, Tuple[Path, "OrderedDict[str, Any]", str, str]] = {}
    for n in index:
        p = vault / n["path"]
        text = p.read_bytes().decode("utf-8")
        data, body = F.read(text)
        notes[n["name"]] = (p, data, body, text)
    synced_p = root / SYNCED_REL
    synced: Dict[str, Dict[str, List[str]]] = json.loads(synced_p.read_text(encoding="utf-8")) if synced_p.is_file() else {}
    forward: Dict[str, Dict[str, "OrderedDict[str, Dict[str, Any]]"]] = {}
    removed_by_human = manual = 0
    # живые прямые рёбра всех заметок: зеркало симметричного ребра у цели — не ручная ссылка человека
    live_all = {(x["from"], x["edge"], x["to"]) for n in index for x in E.load(root, n["name"]) if x["status"] != "rejected"}
    for n in index:
        name, kind = n["name"], n["kind"]
        p, data, body, _ = notes[name]
        props = [x for x in E.load(root, name)]
        live = {(x["edge"], x["to"]): x for x in props if x["status"] != "rejected" and x["to"] in names}
        before = synced.get(name, {})
        changed_props = False
        for edge in ont.edges_from(n):
            if edge in _FIXED:
                continue
            in_fm = set(_targets(data.get(edge)))
            for t in before.get(edge, []):
                if t not in in_fm and (edge, t) in live:          # человек убрал ссылку в Obsidian
                    live.pop((edge, t))["status"] = "rejected"
                    removed_by_human += 1
                    changed_props = True
            rows: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
            for (e, t), x in sorted(live.items()):
                if e == edge:
                    rows[t] = x
            for t in sorted(in_fm - set(rows)):
                if ont.is_symmetric(edge) and (t, edge, name) in live_all:
                    continue
                if t in names and not any(x["edge"] == edge and x["to"] == t and x["status"] == "rejected" for x in props):
                    rows[t] = {"from": name, "edge": edge, "to": t, "quote": "", "origin": "manual", "status": "accepted"}
                    manual += 1
            forward.setdefault(name, {})[edge] = rows
        if changed_props and not dry:
            E.save(root, name, props)
    inverse: Dict[str, Dict[str, Set[str]]] = {}
    for src, by_edge in forward.items():
        for edge, rows in by_edge.items():
            inv = ont.inverse_of(edge)
            for t in rows:
                inverse.setdefault(t, {}).setdefault(inv, set()).add(src)
    for n in index:                                              # belongs_to — из нарезки
        _, data, _, _ = notes[n["name"]]
        for edge in _FIXED:
            for t in _targets(data.get(edge)):
                inverse.setdefault(t, {}).setdefault(ont.inverse_of(edge), set()).add(n["name"])
    written, manual_inverse, new_synced = 0, [], {}
    for n in index:
        name = n["name"]
        p, data, body, old_text = notes[name]
        data = OrderedDict(data)
        order = ont.fm_keys(n["kind"], n.get("role"))
        mine = forward.get(name, {})
        for edge, rows in mine.items():
            vals = set(rows)
            if ont.is_symmetric(edge):
                vals |= inverse.get(name, {}).get(edge, set())
            data[edge] = [F.link(t) for t in sorted(vals)]
        for key in order:
            edge = ont.edge_of_inverse(key)
            if edge is None or key in mine or (ont.is_symmetric(edge) and key in data and edge in mine):
                continue
            want = sorted(inverse.get(name, {}).get(key, set()))
            have = sorted(_targets(data.get(key)))
            if key in data and have != want and have:
                manual_inverse.append(f"{name}.{key}")
            data[key] = [F.link(t) for t in want]
        new_synced[name] = {e: sorted(r) for e, r in mine.items() if r}
        table = ["| связь | цель | основание | пояснение | источник |", "|---|---|---|---|---|"]
        for edge, rows in mine.items():
            for t, x in rows.items():
                quote = _cell(x.get("quote")) or "внесено человеком"
                # пояснение: у правила — что оно увидело («SQL: чтение и запись»), у модели — её why
                table.append(f"| {ont.edges[edge]['title_ru']} | {F.link(t)} | {quote} | {_cell(x.get('why'))} | {x['origin']} |")
        if len(table) > 2:
            body = F.set_block(body, "edges", "\n".join(table))
        elif F.get_block(body, "edges"):
            body = F.set_block(body, "edges", "_рёбер нет_")
        new_text = F.write(data, body if body.endswith("\n") else body + "\n", order)
        if new_text != old_text:
            if not dry:
                FS.write_bytes(p, new_text.encode("utf-8"))
            written += 1
    journal_changed = new_synced != synced
    if not dry:
        synced_p.parent.mkdir(parents=True, exist_ok=True)
        FS.write_text(synced_p, json.dumps(new_synced, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    edges_total = sum(len(r) for by in forward.values() for r in by.values())
    return {"notes": len(index), "written": written, "forward_edges": edges_total, "manual": manual,
            "removed_by_human": removed_by_human, "manual_inverse": manual_inverse[:20], "journal_changed": journal_changed}
