"""states.py — кадр-состояние против своего экрана: регионы-дельты (спека §10.1, §13.6, §14.4.6; задача 6.2; INV-C4, QG-U21, P3).

Кадр «ошибка», «пусто», «раскрыто» — состояние экрана, а не второй экран: он строится ВНУТРИ модуля экрана регионами и переключателем, а проверяется на URL экрана. Модуль сравнивает
два канона (кадр-состояние и кадр, на который указывает `stateOf`) тем же матчером, что и проверка страницы (`verify/match.py`: канон против канона), и отвечает:

  • `available` — это вообще состояние ЭТОГО экрана: схожесть ≥ `SAME_SCREEN_SIMILARITY` (0.6; доля содержимого, найденная в другом кадре, — берётся лучшее из двух направлений: состояние
    может и добавлять, и убирать) и корни одной ширины ±`WIDTH_TOLERANCE` px. Нет — `reason` с выходом (чинить stateOf или завести кадр как экран), а НЕ план от целой страницы (INV-C4);
  • `regions` в порядке документа состояния: `shared` — maximal чистое поддерево, уже собранное экраном (стаб с `baseId` — id узла экрана); `added` — поддерево, которого на экране нет;
    `removed` — поддерево экрана, которого в состоянии нет (`baseId`, `parentId` — где было); `changed` — сам узел, у которого изменилось содержимое (`changes`: variant | text | color | style | art),
    дети такого узла — отдельные регионы или стабы. Изменения размера и позиции, следующие из контента, — `consequence: true`: они НИКОГДА не пинятся и сами регион не создают (узел с одними
    последствиями остаётся `shared`, последствия — в `consequences`);
  • `triggers` — кандидаты-переключатели на экране: узлы, чей вариант в состоянии другой (сильнее всего), и управляющие инстансы (кнопка, вкладка, переключатель…);
  • дельта-корень (`delta_root`): синтетический корень `inferred:delta:…` с детьми — регионами `added` и `changed` (у `changed` дети заменены стабами `inferred:ref:…`), на нём работают outline, section,
    резолв и план вместо целого кадра: секция состояния — регион, хеш региона — хеш его дельты (меняется только то, что подвинулось). `removed` и `shared` лежат на корне списками.

Не проверено данными (боевых пар «экран / состояние» нет, сверка — на синтетике): порог 0.6 на настоящих макетах (модалка поверх экрана против «пустого» состояния), сигнатуры «содержимого» (что считать
изменением цвета или варианта), триггеры по имени компонента (русские и английские слова), матчер на макетах, где состояние — отдельный кадр с чужими именами слоёв.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import canon as C
from . import track as TR
from .verify import match as MT

SAME_SCREEN_SIMILARITY = 0.6                    # §20.4
WIDTH_TOLERANCE = 1                             # px, §20.4
MAX_TRIGGERS = 10
STUB_PREFIX = "inferred:ref:"
DELTA_PREFIX = "inferred:delta:"
KINDS = ("shared", "added", "removed", "changed")
CONTROL = re.compile(r"(button|btn|tab\b|tabs|switch|toggle|chip|radio|checkbox|select|dropdown|accordion|collapse|expand|кнопк|вкладк|переключ|чекбокс|аккордеон)", re.IGNORECASE)
_PAINT_STYLE = ("background", "gradient", "shadow", "opacity", "radius")


def _kids(n: dict) -> List[dict]:
    k = n.get("children")
    return [c for c in k if isinstance(c, dict)] if isinstance(k, list) else []


def _comp(n: dict) -> dict:
    c = n.get("component")
    return c if isinstance(c, dict) else {}


def _variants(n: dict) -> Dict[str, str]:
    v = _comp(n).get("variants")
    return {k: x for k, x in v.items() if isinstance(k, str) and isinstance(x, str)} if isinstance(v, dict) else {}


def _texts(n: dict) -> List[Tuple[str, str]]:
    """Тексты инстанса (слоты) как пары (имя слота, текст) в порядке документа."""
    out: List[Tuple[str, str]] = []
    for item in _comp(n).get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            out.append((str(item.get("name") or ""), MT.norm_text(item["text"])))
    return out


def _width(root: dict) -> Optional[float]:
    size = root.get("size") if isinstance(root.get("size"), dict) else {}
    for v in (size.get("w"), (root.get("box") or {}).get("w") if isinstance(root.get("box"), dict) else None):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def _color_of(n: dict) -> List[Any]:
    style = n.get("style") if isinstance(n.get("style"), dict) else {}
    border = style.get("border") if isinstance(style.get("border"), dict) else {}
    return [n.get("color"), style.get("background"), border.get("color")]


def node_changes(b: dict, s: dict) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Отличия узла экрана `b` и парного узла состояния `s` → (изменения, последствия). Изменение — вариант, текст, цвет, прочий вид, арт; последствие — размер, раскладка, позиция."""
    changes: List[Dict[str, Any]] = []
    cons: List[Dict[str, Any]] = []
    if b.get("kind") != s.get("kind"):
        changes.append({"kind": "style", "prop": "kind", "from": b.get("kind"), "to": s.get("kind")})
        return changes, cons
    if _comp(b).get("figmaName") != _comp(s).get("figmaName"):
        changes.append({"kind": "component", "prop": "figmaName", "from": _comp(b).get("figmaName"), "to": _comp(s).get("figmaName")})
    vb, vs = _variants(b), _variants(s)
    for k in sorted(set(vb) | set(vs)):
        if vb.get(k) != vs.get(k):
            changes.append({"kind": "variant", "prop": k, "from": vb.get(k), "to": vs.get(k)})
    if MT.norm_text(b.get("text")) != MT.norm_text(s.get("text")):
        changes.append({"kind": "text", "prop": "text", "from": b.get("text"), "to": s.get("text")})
    tb, ts = _texts(b), _texts(s)
    if tb != ts:
        changes.append({"kind": "text", "prop": "slots", "from": [t for _n, t in tb], "to": [t for _n, t in ts]})
    if _color_of(b) != _color_of(s):
        changes.append({"kind": "color", "prop": "color", "from": _color_of(b), "to": _color_of(s)})
    sb = b.get("style") if isinstance(b.get("style"), dict) else {}
    ss = s.get("style") if isinstance(s.get("style"), dict) else {}
    for k in _PAINT_STYLE:
        if sb.get(k) != ss.get(k) and k != "background":
            changes.append({"kind": "style", "prop": k, "from": sb.get(k), "to": ss.get(k)})
    if b.get("art") != s.get("art"):
        changes.append({"kind": "art", "prop": "art", "from": b.get("art"), "to": s.get("art")})
    if b.get("size") != s.get("size"):
        cons.append({"kind": "size", "from": b.get("size"), "to": s.get("size"), "consequence": True})
    if b.get("layout") != s.get("layout"):
        cons.append({"kind": "layout", "from": b.get("layout"), "to": s.get("layout"), "consequence": True})
    if b.get("box") != s.get("box"):
        cons.append({"kind": "position", "from": b.get("box"), "to": s.get("box"), "consequence": True})
    return changes, cons


def _stub(child: dict) -> dict:
    return {"id": STUB_PREFIX + str(child["id"]), "kind": "frame", "name": child.get("name") or "", "stub": True, "ref": child["id"]}


def _name(n: dict) -> str:
    return str(n.get("name") or n["id"])


def compare(screen_env: dict, state_env: dict, screen_name: str, state_name: str) -> Dict[str, Any]:
    """Сравнение двух канонов. → публичный ответ + `_delta` (дельта-корень) и `_pairs` для вызывающих внутри пакета."""
    broot, sroot = screen_env["root"], state_env["root"]
    out: Dict[str, Any] = {"screen": screen_name, "state": state_name, "similarityMin": SAME_SCREEN_SIMILARITY}
    wb, ws = _width(broot), _width(sroot)
    fwd = MT.match(screen_env, state_env)
    rev = MT.match(state_env, screen_env)
    sim = max(fwd["stats"]["matchRate"], rev["stats"]["matchRate"])
    out["similarity"] = sim
    problems: List[str] = []
    if sim < SAME_SCREEN_SIMILARITY:
        problems.append(f"схожесть кадров {sim} < {SAME_SCREEN_SIMILARITY}: «{state_name}» — не состояние экрана «{screen_name}», а другой экран")
    if wb is not None and ws is not None and abs(wb - ws) > WIDTH_TOLERANCE:
        problems.append(f"корни разной ширины ({ws:g} px у «{state_name}» и {wb:g} px у «{screen_name}»; допуск ±{WIDTH_TOLERANCE}): это другой брейкпоинт или другой экран")
    if problems:
        out.update(available=False, reason="; ".join(problems) + ". Чини причину: stateOf указывает не на тот кадр (daiver_track_manifest) — либо заведи кадр как экран; "
                                                                   "план от целой страницы состояния не строится")
        out.update(regions=[], triggers=[], consequences=[])
        return out
    s2b: Dict[str, str] = {}
    conf: Dict[str, str] = {}
    for p in fwd["pairs"]:
        s2b[p["impl"]] = p["design"]
        conf[p["impl"]] = p["confidence"]
    s2b.setdefault(sroot["id"], broot["id"])             # корни кадров — одна пара по построению (ширина проверена)
    conf.setdefault(sroot["id"], "high")
    b_by_id = {n["id"]: n for n in C.iter_nodes(broot)}
    s_nodes = list(C.iter_nodes(sroot))
    b2s = {b: s for s, b in s2b.items()}
    s_parent: Dict[str, dict] = {}
    for n in s_nodes:
        for k in _kids(n):
            s_parent[k["id"]] = n
    changes: Dict[str, List[dict]] = {}
    consequences: List[Dict[str, Any]] = []
    for n in s_nodes:
        b = b_by_id.get(s2b.get(n["id"], ""))
        if b is None:
            continue
        ch, cons = node_changes(b, n)
        if ch:
            changes[n["id"]] = ch
        for c in cons:
            consequences.append(dict(c, nodeId=n["id"], baseId=b["id"]))
    removed_under: Dict[str, List[dict]] = {}            # id узла состояния (парного родителя) → пропавшие дети экрана
    for bn in C.iter_nodes(broot):
        for k in _kids(bn):
            if k["id"] not in b2s and bn["id"] in b2s and not bn.get("occluded"):
                removed_under.setdefault(b2s[bn["id"]], []).append(k)
    dirty: Dict[str, bool] = {}
    for n in reversed(s_nodes):                           # потомки раньше предков
        dirty[n["id"]] = (n["id"] not in s2b or n["id"] in changes or n["id"] in removed_under or any(dirty[k["id"]] for k in _kids(n)))
    regions: List[Dict[str, Any]] = []
    delta_children: List[dict] = []
    removed_list: List[dict] = []
    shared_list: List[dict] = []
    stack: List[dict] = [sroot]
    while stack:
        n = stack.pop()
        nid = n["id"]
        if nid not in s2b:
            parent = s_parent.get(nid)
            sibs = _kids(parent) if parent else []
            idx = next((i for i, k in enumerate(sibs) if k["id"] == nid), 0)
            node = dict(copy.copy(n), delta={"kind": "added", "parentId": parent["id"] if parent else None, "afterId": sibs[idx - 1]["id"] if idx else None})
            delta_children.append(node)
            regions.append({"id": f"added:{nid}", "kind": "added", "nodeId": nid, "name": _name(n), "parentId": parent["id"] if parent else None,
                            "hash": C.subtree_hash(node)})
            continue
        if not dirty[nid]:
            shared_list.append({"stateId": nid, "baseId": s2b[nid], "name": _name(n), "hash": C.subtree_hash(n)})
            regions.append({"id": f"shared:{nid}", "kind": "shared", "nodeId": nid, "baseId": s2b[nid], "name": _name(n), "hash": C.subtree_hash(n)})
            continue
        if nid in changes:
            node = {k: v for k, v in n.items() if k != "children"}
            node["children"] = [_stub(k) for k in _kids(n)]
            node["delta"] = {"kind": "changed", "baseId": s2b[nid], "changes": changes[nid]}
            delta_children.append(node)
            regions.append({"id": f"changed:{nid}", "kind": "changed", "nodeId": nid, "baseId": s2b[nid], "name": _name(n), "changes": changes[nid],
                            "hash": C.subtree_hash(node)})
        for k in removed_under.get(nid, []):
            h = C.subtree_hash(k)
            removed_list.append({"baseId": k["id"], "name": _name(k), "parentId": nid, "hash": h})
            regions.append({"id": f"removed:{k['id']}", "kind": "removed", "nodeId": k["id"], "baseId": k["id"], "name": _name(k), "parentId": nid, "hash": h})
        stack.extend(reversed(_kids(n)))
    droot = {"id": DELTA_PREFIX + state_name, "kind": "frame", "name": f"{state_name} Δ {screen_name}", "children": delta_children, "deltaRoot": True,
             "removed": removed_list, "shared": shared_list}
    out.update(available=True, regions=regions, consequences=consequences[:20])
    out["counts"] = {k: sum(1 for r in regions if r["kind"] == k) for k in KINDS}
    out["triggers"] = _triggers(broot, b_by_id, changes, s2b)
    out["_delta"] = droot
    out["_pairs"] = {"state→screen": s2b, "confidence": conf}
    out["_screenRoot"] = broot
    out["_exclusive"] = _exclusive(regions, changes, b_by_id, {n["id"]: n for n in s_nodes}, s2b)
    return out


CONTENT_KINDS = ("text", "icon", "image", "vector", "instance")


def _content_ids(node: dict) -> List[str]:
    out = []
    for n in C.iter_nodes(node):
        if n.get("occluded") is True:
            continue
        if n.get("kind") in CONTENT_KINDS or n.get("art"):
            if n.get("kind") == "text" and not (isinstance(n.get("text"), str) and n["text"].strip()):
                continue
            out.append(n["id"])
    return out


def _exclusive(regions: List[dict], changes: Dict[str, List[dict]], b_by_id: Dict[str, dict], s_by_id: Dict[str, dict], s2b: Dict[str, str]) -> Dict[str, List[str]]:
    """Эксклюзивное содержимое сторон: что есть ТОЛЬКО в состоянии (added-поддеревья и узлы с изменённым текстом) и ТОЛЬКО на экране (removed-поддеревья и узлы со старым текстом).
    Узлы с изменённым лишь вариантом или цветом не годятся в свидетели: компонент на странице есть в обоих состояниях."""
    state: List[str] = []
    screen: List[str] = []
    for r in regions:
        if r["kind"] == "added":
            state += _content_ids(s_by_id[r["nodeId"]])
        elif r["kind"] == "removed":
            screen += _content_ids(b_by_id[r["nodeId"]])
        elif r["kind"] == "changed" and any(c["kind"] == "text" for c in changes.get(r["nodeId"], [])):
            state.append(r["nodeId"])
            screen.append(s2b[r["nodeId"]])
    return {"state": state, "screen": screen}


FOUND = 0.5                    # доля эксклюзивного содержимого стороны, найденная на странице, с которой сторона «видна»


def verdict(res: Dict[str, Any], state_match: Dict[str, Any], impl_root: dict) -> Dict[str, Any]:
    """Вердикт кадра-состояния по ОДНОМУ захвату (§14.4.6): эксклюзивное содержимое макета состояния и макета экрана против страницы, только уверенные пары (anchor high).
    reached — страница показывает состояние (его эксклюзивное найдено, экранное — нет); not-reached — видно эксклюзивное экрана, а состояния нет (причина в рецепте открытия, не в сборке);
    ambiguous — картина смешанная (смотри actual.png); unknown — сравнивать не по чему (нет эксклюзивного содержимого вовсе, нет уверенных пар) либо состояние недоступно."""
    if not res.get("available"):
        return {"verdict": "unknown", "reason": res.get("reason") or "состояние недоступно"}
    ex = res["_exclusive"]
    if not ex["state"] and not ex["screen"]:
        return {"verdict": "unknown", "reason": "у состояния нет эксклюзивного содержимого (меняются только вариант или цвет): по содержимому страницы состояние не отличить от экрана",
                "stateExclusive": 0, "screenExclusive": 0}
    sm = {p["design"] for p in state_match["pairs"] if p["confidence"] == "high"}
    bm = {p["design"] for p in MT.match(res["_screenRoot"], impl_root)["pairs"] if p["confidence"] == "high"}
    sf = sum(1 for i in ex["state"] if i in sm)
    bf = sum(1 for i in ex["screen"] if i in bm)
    s_rate = sf / len(ex["state"]) if ex["state"] else None
    b_rate = bf / len(ex["screen"]) if ex["screen"] else None
    out: Dict[str, Any] = {"stateExclusive": len(ex["state"]), "stateFound": sf, "screenExclusive": len(ex["screen"]), "screenFound": bf}
    state_seen = s_rate is not None and s_rate >= FOUND
    screen_seen = b_rate is not None and b_rate >= FOUND
    if state_seen and not screen_seen:
        v = "reached"
    elif screen_seen and s_rate is not None and not state_seen:
        v = "not-reached"
    elif s_rate is None and b_rate is not None and not screen_seen:
        v = "reached"                                   # состояние только убирает: убранное на странице исчезло
    elif s_rate is None and screen_seen:
        v = "not-reached"                               # состояние только убирает, а убранное на месте
    else:
        v = "ambiguous"
    out["verdict"] = v
    out["reason"] = {
        "reached": "на странице есть эксклюзивное содержимое состояния и нет эксклюзивного содержимого экрана",
        "not-reached": "на странице видно эксклюзивное содержимое экрана «%s», а состояния нет: страница открыта не в этом состоянии — причина в рецепте открытия (open), а не в сборке" % res["screen"],
        "ambiguous": "картина смешанная (эксклюзивное содержимое обеих сторон найдено или не найдено вместе): открой actual.png и реши глазами; дефекты ниже не отменены",
    }[v]
    return out


def _triggers(broot: dict, b_by_id: Dict[str, dict], changes: Dict[str, List[dict]], s2b: Dict[str, str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for sid, ch in changes.items():
        if any(c["kind"] == "variant" for c in ch):
            b = b_by_id.get(s2b.get(sid, ""))
            if b is not None and b["id"] not in seen:
                seen.add(b["id"])
                v = next(c for c in ch if c["kind"] == "variant")
                out.append({"id": b["id"], "name": _name(b), "why": f"вариант «{v['prop']}» в состоянии другой: {v['from']} → {v['to']}"})
    for n in C.iter_nodes(broot):
        if len(out) >= MAX_TRIGGERS:
            break
        name = _comp(n).get("figmaName") if n.get("kind") == "instance" else None
        if name and n["id"] not in seen and CONTROL.search(name):
            seen.add(n["id"])
            out.append({"id": n["id"], "name": _name(n), "why": f"управляющий компонент «{name}» на экране"})
    return out[:MAX_TRIGGERS]


def public(res: Dict[str, Any]) -> Dict[str, Any]:
    """Ответ без внутренних полей."""
    return {k: v for k, v in res.items() if not k.startswith("_")}


def analyze(root: Path, manifest: dict, frame_name: str) -> Dict[str, Any]:
    """Сравнение кадра-состояния с его экраном по файлам трека. Не-состояние (нет `stateOf`) — TrackError."""
    f = TR.get_frame(manifest, frame_name)
    screen_name = f.get("stateOf")
    if not screen_name:
        raise TR.TrackError(f"кадр «{frame_name}» не состояние: у него нет stateOf (его задаёт daiver_track_manifest(track, frame, set={{stateOf}}))")
    out: Dict[str, Any] = {"screen": screen_name, "state": frame_name}
    screen = next((x for x in manifest.get("frames") or [] if x["name"] == screen_name), None)
    if screen is None:
        out.update(available=False, reason=f"stateOf указывает на кадр «{screen_name}», которого в треке нет: поправь stateOf (daiver_track_manifest) или заведи экран (daiver_pixso_pull)",
                   regions=[], triggers=[], consequences=[])
        return out
    if screen_name == frame_name:
        out.update(available=False, reason="кадр объявлен состоянием самого себя", regions=[], triggers=[], consequences=[])
        return out
    pair = {}
    for label, name in (("экран", screen_name), ("состояние", frame_name)):
        path = TR.frame_paths(Path(root), manifest, name)["canonical"]
        if not path.is_file():
            out.update(available=False, reason=f"канона кадра «{name}» ({label}) нет — сначала daiver_normalize(track, frame={name})", regions=[], triggers=[], consequences=[])
            return out
        try:
            pair[label] = C.read_canonical(path)
        except C.CanonError as e:
            out.update(available=False, reason=f"канон кадра «{name}» ({label}) не читается ({e}) — daiver_normalize заново", regions=[], triggers=[], consequences=[])
            return out
    return compare(pair["экран"], pair["состояние"], screen_name, frame_name)
