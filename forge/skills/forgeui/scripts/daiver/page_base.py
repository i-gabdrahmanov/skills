"""page_base.py — правка существующей страницы, написанной не forge-ui (спека §12.2, §13.7 page, §14.4.6 baseline.standing; задача 6.4;
INV-C18, C19, F18; QG-U22, P4).

Страницу написали люди; макет — её изменение. Строится оно В файлы страницы (`sources` кадра) точечно, а проверка отличает свои регрессии
от старых расхождений страницы с макетом. Модуль держит пять вещей:

  • `capture_problem` — база снимается ДО правок кода тремя вызовами в `<кадр>/baseline` (ровно эта папка): `daiver_screenshot` (снимок
    страницы) → `daiver_verify_compare` (разрыв страницы с макетом; пишет `baseline {kind: page}` в манифест) → `daiver_code_compare` (копии
    `sources` в `baseline/sources/` и индекс `sources.json` — `snapshot_sources`). Только у кадра с `sources` (не ревизия, не состояние) и только
    пока сборка не начата (в build-state все секции pending): база после правок — уже не база.
  • `analyze` — регионы изменения из разрыва базы (pageGap → regions). Считаются заново от durable-входов базы (снимок страницы и копии файлов)
    против ТЕКУЩЕГО канона: перетяжка макета не делает их протухшими. Матчер и правила те же, что у проверки (без плана: план строится по
    регионам). added — поддерево макета, которого на странице нет (`missing` матчера); removed — содержимое страницы, которого нет в макете
    (`extra`); changed — найденный узел, у которого другое «что показано» (CHANGE_CODES: текст, порядок, слоты, видимость). Расхождения
    значений (геометрия, типографика, цвет, форма) у найденных узлов вне регионов — старые расхождения страницы (`preExisting`): при
    scope change их не трогают, при scope page они тоже регионы changed. Дельта-корень `inferred:delta:<кадр>@page` — как у состояния и
    ревизии: на нём работают outline, section, резолв, план и build-state.
  • `location` региона (own | inside | ancestor) — `file:line` из дерева кода копий базы, сматченного с каноном тем же матчером, что у
    код-гейта: own — сам узел найден в коде; inside — найдены его потомки (место — их общий пролёт); ancestor — ближайший найденный предок
    (added вписывается туда; removed страницы ищется в коде по видимому тексту). Пролёт элемента — до парного закрывающего тега (`endLine`).
  • `classify` — `baseline.standing` каждого дефекта проверки: change (в регионе изменения) | pre-existing (вне регионов, и тот же ключ был
    в базе) | regression (вне регионов, в базе не было). Ключ — (code, designId, prop) или (code, элемент по видимому тексту), НИКОГДА id
    дефекта (тот хеширует путь страницы). Ключи базы — находки и дефекты сравнения базы с тем же планом, каноном и правилами. scope change —
    pre-existing не блокирует; scope page — считается всё (дефект несёт `scope`).
  • `outside_edits` — INV-C19 «вне регионов дифф нулевой»: построчный дифф копии базы с текущим файлом; правка разрешена внутри пролёта
    региона (changed/removed — сам элемент, added — между найденными соседями в предке), в import-операторах и вставкой целых операторов
    между операторами верхнего уровня (новый локальный компонент рядом). Остальное — нарушение с `file:line`; гейт сборки его не пускает.

Не проверено данными (боевой «ручной» страницы нет, сверка — на синтетике): правило «изменение = структура и слоты, значения вне регионов —
старые расхождения» (дизайнер, поменявший только цвет или отступ, при scope change увидит его в preExisting ui-plan.md и выберет scope page);
доля узлов, которые матчер кода находит в чужом коде (компоненты из алиасов, условный рендер) — от неё зависит точность location; стили
(.css/.less/.scss) на правки вне регионов не проверяются (пролёта элемента в стилях нет).
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import canon as C
from . import config as CFG
from . import states as STS
from . import track as TR
from .verify import match as MT

DELTA_PREFIX = STS.DELTA_PREFIX
SOURCES_DIR = "sources"
SOURCES_INDEX = "sources.json"
SNAPSHOT = "snapshot.json"
CHANGE_CODES = ("TEXT", "ORDER", "SLOT_MISSING", "SLOT_TEXT", "SLOT_TYPOGRAPHY", "SLOT_COLOR", "INVISIBLE")   # «что показано» изменилось
STANDINGS = ("change", "pre-existing", "regression")
LOCATION_KINDS = ("own", "inside", "ancestor")
KINDS = ("added", "changed", "removed")
SHOWN = 10
MAX_EDITS = 20
MAX_SOURCE_BYTES = 1024 * 1024
ELEMENT_MAX = 60


def _rel(root: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).casefold()


def _clip(s: Any, n: int = 80) -> str:
    t = " ".join(str(s).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _read_json(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    if not path.is_file():
        return None, "нет файла"
    try:
        return json.loads(path.read_bytes().decode("utf-8-sig")), None
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return None, f"не читается ({str(e)[:120]})"


def _write(path: Path, doc: Any) -> None:
    from .verify import capture as CP
    path.parent.mkdir(parents=True, exist_ok=True)
    CP._atomic_write(path, (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


# ── захват базы ───────────────────────────────────────────────────────────────

def build_started(paths: Dict[str, Path]) -> bool:
    """Сборка кадра начата: в build-state есть секция не pending (нечитаемый файл — тоже «начата»: базу поверх неизвестного не снимаем)."""
    data, why = _read_json(paths["buildState"])
    if why == "нет файла":
        return False
    secs = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(secs, list):
        return True
    return any(not isinstance(s, dict) or s.get("status") != "pending" for s in secs)


def in_baseline(paths: Dict[str, Path], iteration_dir: Path) -> bool:
    base = paths["baseline"].resolve()
    real = Path(iteration_dir).resolve()
    return real == base or base in real.parents


def capture_problem(root: Path, manifest: dict, f: dict, iteration_dir: Path) -> Optional[str]:
    """Папка итерации — база страницы? Тогда снимать можно только у правки страницы, ровно в `<кадр>/baseline` и до начала сборки.
    None — можно (или папка не база)."""
    paths = TR.frame_paths(Path(root), manifest, f["name"])
    if not in_baseline(paths, iteration_dir):
        return None
    base = paths["baseline"]
    if Path(iteration_dir).resolve() != base.resolve():
        return (f"база страницы снимается ровно в «{_rel(root, base)}» (без подпапок): там её читают регионы, сборка и проверка")
    if TR.baseline_version(f) is not None:
        return "кадр — ревизия собранной версии: его база — versions/<n>/ (её завёл pull), страницу в baseline/ не снимают"
    if f.get("stateOf"):
        return "кадр-состояние: его база — экран (stateOf), страницу в baseline/ не снимают"
    if not f.get("sources"):
        return ("у кадра нет sources: база снимается только у существующей страницы — сначала daiver_propose_sources, чтение кандидатов и "
                "daiver_track_manifest(track, frame, set={sources, scope})")
    if build_started(paths):
        return ("сборка кадра уже начата (в build-state есть секции не pending): база снимается только ДО правок кода — прежняя база остаётся, "
                "проверка идёт в папку итерации из daiver_verify_state")
    return None


def snapshot_sources(root: Path, f: dict, paths: Dict[str, Path]) -> Tuple[List[str], List[str]]:
    """Копии файлов страницы — `baseline/sources/<путь>` и индекс `sources.json` {files: [{path, sha256, bytes}]}. → (скопированные, предупреждения)."""
    root = Path(root)
    base = paths["baseline"]
    index: List[Dict[str, Any]] = []
    warnings: List[str] = []
    from .verify import capture as CP
    for rel in f.get("sources") or []:
        try:
            data = (root / rel).read_bytes()
        except OSError as e:
            warnings.append(f"{rel}: не читается ({e.strerror or e}) — копии в базе нет")
            continue
        dst = base / SOURCES_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        CP._atomic_write(dst, data)
        index.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    _write(base / SOURCES_INDEX, {"files": index})
    return [x["path"] for x in index], warnings


def read_copies(paths: Dict[str, Path]) -> Tuple[Dict[str, str], List[str]]:
    """Тексты копий базы {путь от корня репо: текст}; индекса нет — пусто и причина."""
    data, why = _read_json(paths["baseline"] / SOURCES_INDEX)
    if data is None:
        return {}, [f"копий файлов страницы в базе нет ({SOURCES_INDEX}: {why}) — daiver_code_compare в baseline не вызывался"]
    out: Dict[str, str] = {}
    warnings: List[str] = []
    for x in data.get("files") or [] if isinstance(data, dict) else []:
        rel = x.get("path") if isinstance(x, dict) else None
        if not isinstance(rel, str):
            continue
        p = paths["baseline"] / SOURCES_DIR / rel
        try:
            raw = p.read_bytes()
            if len(raw) > MAX_SOURCE_BYTES:
                warnings.append(f"{rel}: копия больше {MAX_SOURCE_BYTES} байт — пропущена")
                continue
            out[rel] = raw.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            warnings.append(f"{rel}: копия базы не читается ({str(e)[:80]})")
    return out, warnings


def load_snapshot(paths: Dict[str, Path]) -> Tuple[Optional[dict], Optional[dict], Optional[str]]:
    """(снимок базы, модель страницы, причина)."""
    from .verify import impl_model as IM
    snap, why = _read_json(paths["baseline"] / SNAPSHOT)
    if snap is None:
        return None, None, f"снимка страницы в базе нет ({SNAPSHOT}: {why}) — daiver_screenshot в baseline"
    try:
        return snap, IM.to_model(snap), None
    except (IM.ImplModelError, C.CanonError) as e:
        return snap, None, f"снимок базы не читается как страница ({str(e)[:120]}) — перезахвати базу"


# ── регионы ───────────────────────────────────────────────────────────────────

class _Tree:
    def __init__(self, root: dict):
        self.by_id: Dict[str, dict] = {}
        self.parent: Dict[str, Optional[str]] = {}
        self.order: Dict[str, int] = {}
        stack: List[Tuple[dict, Optional[str]]] = [(root, None)]
        while stack:
            n, par = stack.pop()
            self.order[n["id"]] = len(self.order)
            self.by_id[n["id"]] = n
            self.parent[n["id"]] = par
            stack.extend((k, n["id"]) for k in reversed(_kids(n)))

    def subtree(self, nid: str) -> List[str]:
        out: List[str] = []
        stack = [nid]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(k["id"] for k in reversed(_kids(self.by_id[x])))
        return out

    def chain(self, nid: str) -> List[str]:
        out: List[str] = []
        cur = self.parent.get(nid)
        while cur is not None:
            out.append(cur)
            cur = self.parent.get(cur)
        return out


def _kids(n: dict) -> List[dict]:
    k = n.get("children")
    return [c for c in k if isinstance(c, dict)] if isinstance(k, list) else []


def element_of(node: Optional[dict]) -> Optional[str]:
    """Элемент по видимому тексту (как у дефекта проверки)."""
    from .verify import verify_compare as VC
    return VC.element_name(node)


def _unavailable(out: Dict[str, Any], reason: str, **extra: Any) -> Dict[str, Any]:
    out.update(available=False, reason=reason, regions=[], counts={k: 0 for k in KINDS + ("preExisting",)},
               preExisting={"count": 0, "sample": []}, warnings=[], files={}, locations={}, **extra)
    out["_regionIds"], out["_removed"], out["_delta"], out["_locator"] = set(), set(), None, None
    return out


def analyze(root: Any, manifest: dict, frame_name: str, design_env: Optional[dict] = None) -> Dict[str, Any]:
    """Регионы правки страницы по базе и текущему канону. Кадр не правка страницы — TrackError."""
    from .verify import check as CK
    from .verify import verify_compare as VC
    root = Path(root)
    f = TR.get_frame(manifest, frame_name)
    if not TR.is_page(f):
        raise TR.TrackError(f"кадр «{f['name']}» не правка существующей страницы: у него нет sources (или он ревизия либо состояние)")
    scope = f.get("scope") if f.get("scope") in TR.SCOPES else "change"
    out: Dict[str, Any] = {"frame": f["name"], "kind": "page", "scope": scope, "sources": list(f["sources"])}
    paths = TR.frame_paths(root, manifest, f["name"])
    if not TR.page_captured(f):
        if f.get("baselineSkip"):
            return _unavailable(out, f"база страницы не снята, пропуск записан открыто: {f['baselineSkip']}. Регрессии неотличимы от старых "
                                     f"расхождений: кадр строится целиком в файлы sources, при проверке считается всё", skipped=True)
        return _unavailable(out, f"база страницы не снята: пока код не тронут — daiver_screenshot → daiver_verify_compare → daiver_code_compare "
                                 f"с iterationDir={_rel(root, paths['baseline'])}")
    env = design_env
    if env is None:
        try:
            env = C.read_canonical(paths["canonical"])
        except (C.CanonError, OSError, RecursionError) as e:
            return _unavailable(out, f"канона кадра нет или он не читается ({str(e)[:120]}) — daiver_normalize")
    snap, model, why = load_snapshot(paths)
    if model is None:
        return _unavailable(out, str(why))
    d_root, i_root = env["root"], model["root"]
    matched = MT.match(d_root, i_root, None)
    rate = matched["stats"]["matchRate"]
    out["matchRate"] = rate
    if not matched["hardGate"]["pass"]:
        return _unavailable(out, f"страница базы — не этот кадр (matchRate {rate} < {MT.MIN_MATCH_RATE}): адрес или рецепт открытия не те — "
                                 f"перезахвати базу (пока сборка не начата); если страница перестраивается целиком, это новый экран — кадр без sources",
                            matchRate=rate)
    res = CK.check(d_root, i_root, matched, CFG.load(root)["config"]["verify"]["tolerances"], VC._flags(snap))
    D, I = _Tree(d_root), _Tree(i_root)
    d2i = {p["design"]: p["impl"] for p in matched["pairs"]}
    i2d = {v: k for k, v in d2i.items()}
    added: List[str] = []
    for m in matched["missing"]:
        added += [x for x in (m.get("ids") or [m["id"]]) if x in D.by_id]
    added_set = set(added)
    inside_added = {x for a in added for x in D.subtree(a)}
    changes: Dict[str, List[Dict[str, Any]]] = {}
    pre: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for fd in res["findings"]:
        d = fd.get("design")
        if not d or d not in D.by_id or d in inside_added or fd["code"] in ("MISSING", "EXTRA"):
            continue
        rec = {"code": fd["code"], **({"prop": fd["prop"]} if fd.get("prop") else {})}
        if fd["code"] in CHANGE_CODES or scope == "page":
            if rec not in changes.setdefault(d, []):
                changes[d].append(rec)
        else:
            pre[(d, fd["code"], fd.get("prop") or "")] = dict(rec, design=d)
    for d in changes:
        changes[d].sort(key=lambda r: (r["code"], r.get("prop") or ""))
    pre_list = sorted((v for k, v in pre.items() if k[0] not in changes), key=lambda v: (D.order[v["design"]], v["code"], v.get("prop") or ""))
    removed: List[Dict[str, Any]] = []
    for e in matched["extra"]:
        anc = I.parent.get(e["id"])
        while anc is not None and anc not in i2d:
            anc = I.parent.get(anc)
        removed.append({"implId": e["id"], "element": element_of(I.by_id.get(e["id"])) or e["id"], "parentId": i2d.get(anc) if anc else d_root["id"]})
    copies, warnings = read_copies(paths)
    loc = _Locator(copies, d_root, D)
    warnings += loc.warnings
    regions: List[Dict[str, Any]] = []
    delta_children: List[dict] = []
    for nid in sorted(added_set | set(changes), key=lambda x: D.order[x]):
        n = D.by_id[nid]
        if nid in added_set:
            par = D.parent.get(nid)
            sibs = _kids(D.by_id[par]) if par else []
            idx = next((i for i, k in enumerate(sibs) if k["id"] == nid), 0)
            after = sibs[idx - 1]["id"] if idx else None
            node = dict(copy.copy(n), delta={"kind": "added", "parentId": par, "afterId": after})
            r: Dict[str, Any] = {"id": f"added:{nid}", "kind": "added", "nodeId": nid, "name": element_of(n) or nid, "parentId": par,
                                 "afterId": after, "hash": C.subtree_hash(node)}
        else:
            node = {k: v for k, v in n.items() if k != "children"}
            node["children"] = [STS._stub(k) for k in _kids(n)]
            node["delta"] = {"kind": "changed", "changes": changes[nid]}
            r = {"id": f"changed:{nid}", "kind": "changed", "nodeId": nid, "name": element_of(n) or nid, "changes": changes[nid],
                 "hash": C.subtree_hash(node)}
        r["location"] = loc.of_design(nid, r["kind"])
        delta_children.append(node)
        regions.append(r)
    removed_list: List[Dict[str, Any]] = []
    for x in removed:
        h = hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        r = {"id": f"removed:{x['implId']}", "kind": "removed", "implId": x["implId"], "name": _clip(x["element"], ELEMENT_MAX),
             "element": x["element"], "parentId": x["parentId"], "hash": h, "location": loc.of_element(x["element"], x["parentId"])}
        regions.append(r)
        removed_list.append({"id": r["id"], "implId": x["implId"], "name": r["name"], "parentId": x["parentId"], "hash": h,
                             "location": r["location"]})
    regions.sort(key=lambda r: (D.order.get(r.get("nodeId") or r["parentId"], 0), KINDS.index(r["kind"]), r["id"]))
    for r in regions:
        r["files"] = [r["location"]["file"]] if r.get("location") else list(f["sources"])
    droot = {"id": f"{DELTA_PREFIX}{f['name']}@page", "kind": "frame", "name": f"{f['name']} Δ страница", "children": delta_children,
             "deltaRoot": True, "removed": removed_list, "shared": []}
    if not regions:
        warnings.append("разрыва страницы с макетом не нашлось: регионов изменения нет — строить нечего (страница уже такая, как в макете)")
    counts = {k: sum(1 for r in regions if r["kind"] == k) for k in KINDS}
    counts["preExisting"] = len(pre_list)
    out.update(available=True, regions=regions, counts=counts, warnings=warnings,
               preExisting={"count": len(pre_list), "sample": [dict(p, element=element_of(D.by_id[p["design"]])) for p in pre_list[:SHOWN]]},
               files={r.get("nodeId") or r["id"]: r["files"] for r in regions},
               locations={r.get("nodeId") or r["id"]: r["location"] for r in regions})
    out["_delta"] = droot
    out["_regionIds"] = inside_added | set(changes)
    out["_removed"] = {_norm(x["element"]) for x in removed}
    out["_locator"] = loc
    return out


def public(res: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in res.items() if not k.startswith("_") and k not in ("files", "locations")}


def removed_view(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"implId": r["implId"], "name": r["name"], "parentId": r["parentId"], "location": r.get("location")}
            for r in res.get("regions") or [] if r["kind"] == "removed"]


# ── место региона в коде ──────────────────────────────────────────────────────

def _span(text: str, line: int, column: int) -> int:
    """Последняя строка элемента, который начинается в (line, column): до парного закрывающего тега; не элемент — та же строка."""
    from .ds import lex as L
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    if not (1 <= line <= len(starts)):
        return line
    off = starts[line - 1] + max(0, column - 1)
    if text[off:off + 1] != "<":
        return line
    try:
        end = L.Lexer(text, True).scan_jsx(off)
    except (IndexError, RecursionError, ValueError):
        return line
    if end is None or end <= off:
        return line
    return text.count("\n", 0, end - 1) + 1


class _Locator:
    """Дерево кода копий базы, сматченное с каноном: где в файлах страницы стоит узел макета."""

    def __init__(self, copies: Dict[str, str], d_root: dict, D: _Tree):
        from .verify import jsx_tree as JT
        from .verify import tsx_scan as TS
        self.copies, self.D = copies, D
        self.warnings: List[str] = []
        self.locs: Dict[str, Dict[str, Any]] = {}
        self.texts: List[Tuple[str, Dict[str, Any]]] = []
        js = {r: t for r, t in sorted(copies.items()) if TS.is_js(r)}
        if not copies:
            return
        if not js:
            self.warnings.append("в файлах страницы нет JS/TS (Vue/Svelte/стили): место регионов в коде не находится — правь по чтению файлов")
            return
        model = JT.build(js, [{"id": "page", "outputs": sorted(js)}])
        self.warnings += [f"дерево кода страницы: {w}" for w in model["warnings"][:3]]
        if not model["root"].get("children"):
            self.warnings.append("в файлах страницы не нашлось компонента с JSX: место регионов в коде неизвестно")
            return
        cm = MT.match(d_root, model["root"], None, descend_root=False)
        for p in cm["pairs"]:
            got = model["locations"].get(p["impl"]) or _first_located(model, p["impl"])
            if got:
                self.locs[p["design"]] = self._loc(got)
        for nid, got in sorted(model["locations"].items()):
            node = _find(model["root"], nid)
            if node is not None and isinstance(node.get("text"), str) and node["text"].strip():
                self.texts.append((_norm(node["text"]), self._loc(got)))

    def _loc(self, got: Dict[str, Any]) -> Dict[str, Any]:
        text = self.copies.get(got["file"], "")
        return {"file": got["file"], "line": got["line"], "column": got.get("column") or 1,
                "endLine": _span(text, got["line"], got.get("column") or 1)}

    def of_design(self, nid: str, kind: str) -> Optional[Dict[str, Any]]:
        if nid in self.locs:
            return dict(self.locs[nid], kind="own")
        inner = [self.locs[x] for x in self.D.subtree(nid)[1:] if x in self.locs]
        if inner:
            file = inner[0]["file"]
            same = [x for x in inner if x["file"] == file]
            return {"kind": "inside", "file": file, "line": min(x["line"] for x in same), "column": 1,
                    "endLine": max(x["endLine"] for x in same)}
        return self._ancestor(nid)

    def _ancestor(self, nid: str) -> Optional[Dict[str, Any]]:
        for a in self.D.chain(nid) if nid in self.D.by_id else []:
            if a in self.locs:
                return dict(self.locs[a], kind="ancestor", ancestorId=a)
        return None

    def of_element(self, element: str, parent_id: str) -> Optional[Dict[str, Any]]:
        want = _norm(element)
        for t, got in self.texts:
            if want and (t == want or (len(want) >= 3 and want in t)):
                return dict(got, kind="own")
        if parent_id in self.locs:
            return dict(self.locs[parent_id], kind="ancestor", ancestorId=parent_id)
        return self._ancestor(parent_id)

    def sibling_window(self, nid: str) -> Optional[Tuple[str, int, int]]:
        """Куда вписать добавленный узел: (файл, нижняя, верхняя строка) — между найденными соседями в найденном предке."""
        anc = self._ancestor(nid)
        if anc is None:
            return None
        par = self.D.parent.get(nid)
        sibs = [k["id"] for k in _kids(self.D.by_id[par])] if par else []
        idx = sibs.index(nid) if nid in sibs else 0
        lo, hi = anc["line"], anc["endLine"]
        for s in reversed(sibs[:idx]):
            got = self.locs.get(s)
            if got and got["file"] == anc["file"]:
                lo = max(lo, got["endLine"])
                break
        for s in sibs[idx + 1:]:
            got = self.locs.get(s)
            if got and got["file"] == anc["file"]:
                hi = min(hi, got["line"])
                break
        return anc["file"], lo, max(lo, hi)


def _first_located(model: Dict[str, Any], code_id: str) -> Optional[Dict[str, Any]]:
    """Пара без места (синтетический `code:root`, обёртка секции) — место её первого потомка с местом: корень компонента страницы."""
    node = _find(model["root"], code_id)
    stack = list(reversed(_kids(node))) if node else []
    while stack:
        n = stack.pop()
        if n.get("id") in model["locations"]:
            return model["locations"][n["id"]]
        stack.extend(reversed(_kids(n)))
    return None


def _find(root: dict, nid: str) -> Optional[dict]:
    stack = [root]
    while stack:
        n = stack.pop()
        if n.get("id") == nid:
            return n
        stack.extend(_kids(n))
    return None


# ── standing дефектов проверки ────────────────────────────────────────────────

def _defect_keys(d: Dict[str, Any]) -> Set[Tuple[str, str, str]]:
    ids = d.get("designIds") or ([d["design"]] if d.get("design") else [])
    if ids:
        return {(d["code"], i, d.get("prop") or "") for i in ids}
    return {(d["code"], "@" + _norm(d.get("element")), "")} if d.get("element") else set()


def base_keys(findings: List[Dict[str, Any]], defects: List[Dict[str, Any]]) -> Set[Tuple[str, str, str]]:
    """Ключи расхождений базы: находки сравнения базы (в том числе подавленные — они тоже были) и её дефекты (у EXTRA — по элементу)."""
    keys: Set[Tuple[str, str, str]] = set()
    for fd in findings:
        if fd.get("design"):
            keys.add((fd["code"], fd["design"], fd.get("prop") or ""))
    for d in defects:
        keys |= _defect_keys(d)
    return keys


def classify(defects: List[Dict[str, Any]], res: Dict[str, Any], keys: Set[Tuple[str, str, str]]) -> Dict[str, Any]:
    """Проставляет `standing` и `scope` каждому дефекту на месте; → {scope, change, preExisting, regression}."""
    scope = res.get("scope") or "change"
    regions, removed = res.get("_regionIds") or set(), res.get("_removed") or set()
    counts = {"change": 0, "pre-existing": 0, "regression": 0}
    for d in defects:
        ids = d.get("designIds") or ([d["design"]] if d.get("design") else [])
        if any(i in regions for i in ids) or (not ids and d.get("code") == "EXTRA" and _norm(d.get("element")) in removed):
            st = "change"
        elif _defect_keys(d) & keys:
            st = "pre-existing"
        else:
            st = "regression"
        d["standing"] = st
        d["scope"] = scope
        counts[st] += 1
    return {"scope": scope, "change": counts["change"], "preExisting": counts["pre-existing"], "regression": counts["regression"]}


def standing_words(st: Dict[str, Any]) -> str:
    """Три числа словами для nextStep."""
    tail = ("старые расхождения не блокируют (scope change)" if st["scope"] == "change"
            else "считается всё, старые расхождения тоже (scope page)")
    return (f"правка страницы: в регионах изменения {st['change']}, старых расхождений страницы {st['preExisting']}, "
            f"регрессий {st['regression']} — {tail}; регрессия (сломано вне регионов) блокирует всегда")


# ── INV-C19: вне регионов дифф нулевой ────────────────────────────────────────

def _statements(text: str, rel: str) -> List[Tuple[int, int, bool]]:
    """Операторы верхнего уровня: (первая строка, последняя строка, это import)."""
    from .ds import lex as L
    try:
        t = L.Toks(text, L.is_jsx_file(rel))
    except (IndexError, RecursionError, ValueError, KeyError, TypeError):
        return []
    out: List[Tuple[int, int, bool]] = []
    for i, j in t.statements():
        a, b = t.t[i].s, t.t[j - 1].e
        out.append((text.count("\n", 0, a) + 1, text.count("\n", 0, max(a, b - 1)) + 1, t.t[i].v == "import"))
    return out


def _windows(res: Dict[str, Any], loc: "_Locator") -> Tuple[Dict[str, List[Tuple[int, int]]], Dict[str, List[Tuple[int, int]]]]:
    """(окна правок, окна вставок) по файлам. Правка — внутри пролёта элемента changed/removed; added — вставка между найденными
    соседями (граничные строки соседей — их код, не регион), правка — только строго между ними."""
    edit: Dict[str, List[Tuple[int, int]]] = {}
    ins: Dict[str, List[Tuple[int, int]]] = {}
    for r in res.get("regions") or []:
        where = r.get("location")
        if r["kind"] == "added" and r.get("nodeId"):
            w = loc.sibling_window(r["nodeId"])
            if w:
                ins.setdefault(w[0], []).append((w[1], w[2]))
                if w[2] - w[1] >= 2:
                    edit.setdefault(w[0], []).append((w[1] + 1, w[2] - 1))
            continue
        if where:
            edit.setdefault(where["file"], []).append((where["line"], where["endLine"]))
            ins.setdefault(where["file"], []).append((where["line"], where["endLine"]))
    return {f: _merge(ws) for f, ws in edit.items()}, {f: _merge(ws) for f, ws in ins.items()}


def _merge(ws: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Смежные и перекрытые окна — одно: дифф склеивает правки соседних строк двух регионов в один хунк."""
    out: List[Tuple[int, int]] = []
    for lo, hi in sorted(ws):
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def outside_edits(root: Any, manifest: dict, frame_name: str, res: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Правки файлов страницы вне регионов (копия базы против текущего файла). → {checked, edits, unchecked, reason?}."""
    from .verify import tsx_scan as TS
    root = Path(root)
    f = TR.get_frame(manifest, frame_name)
    if res is None:
        res = analyze(root, manifest, f["name"])
    if not res.get("available"):
        return {"checked": False, "reason": res.get("reason"), "edits": [], "unchecked": []}
    copies = res["_locator"].copies if res.get("_locator") is not None else {}
    if not copies:
        return {"checked": False, "reason": "копий файлов страницы в базе нет — проверить правки вне регионов не с чем", "edits": [], "unchecked": []}
    wins, ins_wins = _windows(res, res["_locator"])
    edits: List[Dict[str, Any]] = []
    unchecked: List[str] = []
    for rel in sorted(set(f.get("sources") or []) - set(copies)):
        unchecked.append(f"{rel}: копии в базе нет (файл добавлен в sources после захвата или не читался) — правки не с чем сверить")
    for rel in sorted(copies):
        if not TS.is_js(rel):
            unchecked.append(f"{rel}: стили и разметка не-JSX на правки вне регионов не проверяются")
            continue
        try:
            cur = (root / rel).read_bytes().decode("utf-8-sig")
        except FileNotFoundError:
            edits.append({"file": rel, "kind": "delete", "lines": [1, copies[rel].count("\n") + 1], "text": "файл страницы удалён"})
            continue
        except (OSError, UnicodeDecodeError) as e:
            unchecked.append(f"{rel}: не читается ({str(e)[:80]})")
            continue
        old_text = copies[rel]
        if cur == old_text:
            continue
        stmts = _statements(old_text, rel)
        imports = [(a, b) for a, b, imp in stmts if imp]
        a_lines, b_lines = old_text.splitlines(), cur.splitlines()
        sm = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            ws = wins.get(rel, [])
            if tag == "insert":
                p = i1                                   # вставка после строки p (0 — в начало)
                ok = (any(lo <= p < hi for lo, hi in ins_wins.get(rel, [])) or not any(a <= p < b for a, b, _imp in stmts))
                if not ok:
                    edits.append({"file": rel, "kind": "insert", "after": p, "text": _clip(b_lines[j1] if j1 < len(b_lines) else "")})
                continue
            s, e = i1 + 1, i2
            ok = (any(lo <= s and e <= hi for lo, hi in ws) or all(any(a <= x <= b for a, b in imports) for x in range(s, e + 1))
                  or (all(not a_lines[x - 1].strip() for x in range(s, e + 1)) and not any(a <= s <= b or a <= e <= b for a, b, _i in stmts)))
            if not ok:
                edits.append({"file": rel, "kind": tag, "lines": [s, e], "text": _clip(a_lines[s - 1])})
    return {"checked": True, "edits": edits, "unchecked": unchecked}


def edit_words(e: Dict[str, Any]) -> str:
    where = f"{e['file']}:{e['lines'][0]}" + (f"-{e['lines'][1]}" if e["lines"][1] != e["lines"][0] else "") if "lines" in e else f"{e['file']}: после строки {e['after']}"
    return f"{where} ({e['kind']}: «{e['text']}»)"


def gate_problems(root: Any, track: str, frame_name: str) -> List[str]:
    """Нарушения INV-C19 кадра-правки страницы для гейта сборки (пусто — нет или проверять нечего)."""
    root = Path(root)
    try:
        manifest = TR.load(root, track)
        f = TR.get_frame(manifest, frame_name) if manifest else None
    except TR.TrackError:
        return []
    if f is None or not TR.is_page(f) or not TR.page_captured(f):
        return []
    got = outside_edits(root, manifest, f["name"])
    if not got["edits"]:
        return []
    shown = "; ".join(edit_words(e) for e in got["edits"][:MAX_EDITS]) + (" …" if len(got["edits"]) > MAX_EDITS else "")
    return [f"кадр «{f['name']}»: правки файлов страницы вне регионов изменения ({len(got['edits'])}) — {shown}. Всё вне регионов выходит из "
            f"сборки ровно тем, чем вошло (INV-C19): верни эти строки как в базе ({SOURCES_DIR}/ в папке baseline кадра), ни реформата, ни "
            f"переименований; новый код — только в регионах (daiver_frame_baseline: location) или новыми операторами между существующими"]
