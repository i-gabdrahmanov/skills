"""verify_compare.py — `daiver_verify_compare`: модельное сравнение реализации с макетом одним вызовом (спека §14.4, §14.4.6–14.4.7, §16; задача 4.6).

Конвейер: снимок страницы (`snapshot.json`, 4.2) → модель реализации (`impl_model`) → матчер (4.3) → правила (4.4) → DS-гейт (4.5) → каскад
подавления (`suppress`) → свёртка (`group`) → стабильные id (`ids`) → жизненный цикл и шум (`lifecycle`) → метрики. Пишет в папку итерации
`impl-model.json`, `findings.json`, `defects.json`, `compare-summary.json` (в сводке — пары для код-гейта 4.7 и `defectIds` для дедупа).

Честность и порядок:
  • перезахват ДО показа: снимок не `ready` или недорисован (`elements` < половины узлов макета) — `status: recapture`, находок нет (дефекты
    захвата, который ещё грузился, фальшивые), nextStep называет, что поднять;
  • hard gate (`matchRate < MIN_MATCH_RATE`): страница — не этот кадр; дефектов нет, находки лежат уликами в findings.json, визуальные гейты
    записываются `skipped`, код-гейт идёт своим вызовом;
  • `matchRate` (сколько макета нашлось вообще) и `fidelity` (доля найденных элементов — узлов с содержимым или дефектом — без блокирующих неправильностей) — обе; unmatched > 0
    читается вслух, при matchRate < LOW_MATCH_RATE nextStep объясняет, почему второе число выглядит хорошо;
  • ничего не пропадает молча: подавленные находки хранятся с `suppressedBy`, dismissals и unverified — счётчиками и выборкой, `degraded` — списком.

Блокирующий дефект — `severity` critical | major без `noise`; `minor` (DS_DEPRECATED) предупреждает, но не блокирует. declaredNoise (прошлые
`noise: true` и `false_positive` из `verify-decisions.json`, 4.8) помечается, остаётся в дефектах и выходит из blocking и из знаменателя fidelity.

Не проверено данными (боевых прогонов нет): градация severity (critical/major — выбор 4.4); подпись вида для свёртки на реальных таблицах и
списках; формат `decisions.json` (его закрепит 5.4); fidelity на больших экранах; доля `recapture` на реальных страницах.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .. import canon as C
from .. import config as CFG
from .. import outline as OL
from .. import plan as PL
from .. import track as TR
from . import capture as CP
from . import check as CK
from . import decisions as DEC
from . import ds_gate as DS
from . import group as GR
from . import ids as IDS
from . import impl_model as IM
from . import lifecycle as LC
from . import match as MT
from . import suppress as SP

ARTIFACTS = ("impl-model.json", "findings.json", "defects.json", "compare-summary.json")
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}
BLOCKING = ("critical", "major")
ELEMENT_MAX = 60
SAMPLES = 5


class VerifyCompareError(ValueError):
    """Вызов или входы не годятся — сообщение называет причину."""


def _index(root: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Optional[str]]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    parent: Dict[str, Optional[str]] = {}
    stack: List[Tuple[Dict[str, Any], Optional[str]]] = [(root, None)]
    while stack:
        n, par = stack.pop()
        nodes[n["id"]] = n
        parent[n["id"]] = par
        stack.extend((k, n["id"]) for k in reversed([k for k in (n.get("children") or []) if isinstance(k, dict)]))
    return nodes, parent


def _clip(s: Any, n: int = ELEMENT_MAX) -> str:
    t = " ".join(str(s).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def element_name(node: Optional[Dict[str, Any]]) -> Optional[str]:
    """Элемент человеку — по видимому тексту: текст узла; у инстанса — компонент и первый слот; у фрейма — первый текст внутри, иначе имя слоя."""
    if not node:
        return None
    if isinstance(node.get("text"), str) and node["text"].strip():
        return _clip(node["text"])
    comp = node.get("component") or {}
    texts = C.texts(node)
    if comp.get("figmaName"):
        return _clip(f"{comp['figmaName']} «{texts[0]}»" if texts else comp["figmaName"])
    if texts:
        return _clip(texts[0])
    return _clip(node.get("name") or node.get("kind") or node["id"])


def _link(template: Optional[str], node_id: Optional[str]) -> Optional[str]:
    if not node_id:
        return None
    url = OL.node_url(template, node_id)
    if not url:
        return f"`{node_id}`"
    return f"[{node_id}]({url.replace('(', '%28').replace(')', '%29').replace(' ', '%20')})"


def _box(n: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    b = (n or {}).get("box")
    return dict(b) if isinstance(b, dict) else None


def _show(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _defect(g: Dict[str, Any], d_index: Dict[str, Dict[str, Any]], i_index: Dict[str, Dict[str, Any]], template: Optional[str]) -> Dict[str, Any]:
    f0 = g["findings"][0]
    dn = d_index.get(f0.get("design") or "")
    sev = min((f.get("severity", "major") for f in g["findings"]), key=lambda s: SEVERITY_RANK.get(s, 1))
    out: Dict[str, Any] = {"id": g["id"], "code": g["code"], "layer": g["layer"], "severity": sev, "zone": "deterministic",
                           "element": element_name(dn) if dn else (f0.get("prop") or f0.get("file")), "message": f0.get("message"),
                           "coverage": g["coverage"], "suppressedCount": g["suppressedCount"]}
    if g.get("prop"):
        out["prop"] = g["prop"]
    if f0.get("design"):
        out["design"] = f0["design"]
        out["designLink"] = _link(template, f0["design"])
        url = OL.node_url(template, f0["design"])
        if url:
            out["designUrl"] = url
    if f0.get("impl") and f0["impl"] in i_index:
        out["impl"] = f0["impl"]
    pin, dpin = _box(i_index.get(f0.get("impl") or "")), _box(dn)
    if pin:
        out["pin"] = pin
    if dpin:
        out["designPin"] = dpin
    if g["values"]:
        out["rows"] = [{"МАКЕТ": _show(v["expected"]), "РЕАЛИЗАЦИЯ": _show(v["actual"]), "count": v["count"]} for v in g["values"]]
    elif f0.get("expected") is not None or f0.get("actual") is not None:
        out["rows"] = [{"МАКЕТ": _show(f0.get("expected")), "РЕАЛИЗАЦИЯ": _show(f0.get("actual")), "count": 1}]
    if g["coverage"] > 1:
        out["designIds"] = sorted({f["design"] for f in g["findings"] if f.get("design")})[:20]
    if f0.get("rule"):
        out["rule"] = f0["rule"]
    if f0.get("file"):
        out["location"] = {"file": f0["file"], "line": f0.get("line")}
    for k in ("branch", "contentCount", "hint", "corners", "ids"):
        if f0.get(k) is not None:
            out[k] = f0[k]
    return out


def _content_ids(d_index: Dict[str, Dict[str, Any]]) -> Set[str]:
    out = set()
    for nid, n in d_index.items():
        if n.get("occluded") is True:
            continue
        if n.get("kind") in ("text", "icon", "image", "vector", "instance") or n.get("art"):
            if n.get("kind") == "text" and not (isinstance(n.get("text"), str) and n["text"].strip()):
                continue
            out.add(nid)
    return out


def _next_step(status: str, rate: float, fidelity: Optional[float], unmatched: int, counts: Dict[str, Any], low: bool, unverified: Optional[str]) -> str:
    parts: List[str] = []
    if status == "recapture":
        return ("захват не годится для сравнения: страница ещё грузилась или недорисована — перезахвати (daiver_screenshot с большим waitMs "
                "или названным загрузчиком) и повтори compare; дефекты такого снимка не репортятся")
    if status == "hard-gate-fail":
        return (f"matchRate {rate} < {MT.MIN_MATCH_RATE}: страница — не этот кадр (рецепт открытия, адрес или состояние); дефектов нет, "
                f"находки лежат уликами в findings.json. Исправь захват, а не код; визуальные гейты пропущены, код-гейт запускай отдельно")
    if status == "clean":
        parts.append(f"модельное сравнение чисто: matchRate {rate}, fidelity {fidelity}")
    else:
        parts.append(f"блокирующих дефектов: {counts['blocking']} (всего {counts['defects']}); чини по одному дефекту — id стабилен между итерациями")
    if unmatched:
        parts.append(f"unmatched {unmatched}: часть макета на странице не найдена — числа считаются по найденному")
    if low:
        parts.append(f"matchRate {rate} < {MT.LOW_MATCH_RATE}: fidelity выглядит хорошо, потому что считается только по найденному — "
                     f"чем меньше спарено, тем меньше есть что осуждать; смотри unmatched")
    if counts.get("suppressed"):
        parts.append(f"подавлено каскадом: {counts['suppressed']} (их объясняют находки выше по слоям)")
    if counts.get("noise"):
        parts.append(f"шум по прошлым решениям: {counts['noise']} — не блокирует")
    if unverified:
        parts.append(unverified)
    return "; ".join(parts)


def build_report(design: Any, impl: Any, plan: Any, tolerances: Optional[Dict[str, Any]], flags: Optional[Dict[str, Any]],
                 ds_inputs: Optional[Dict[str, Any]], history: List[Dict[str, Any]], noise: Set[str], template: Optional[str],
                 decisions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Чистая часть: макет, модель страницы, план, допуски, флаги, входы DS-гейта (`gather`-словарь | None), история, шум → отчёт."""
    d_root = design["root"] if isinstance(design, dict) and "root" in design and "kind" not in design else design
    i_root = impl["root"] if isinstance(impl, dict) and "root" in impl and "kind" not in impl else impl
    matched = MT.match(d_root, i_root, plan)
    rate, gate_ok = matched["stats"]["matchRate"], bool(matched["hardGate"]["pass"])
    res = CK.check(d_root, i_root, matched, tolerances, flags)
    react_found = ((impl.get("stats") or {}).get("react") or {}).get("found") if isinstance(impl, dict) and "root" in impl else None
    if ds_inputs is None:
        ds = {"ran": False, "reason": "входы DS-гейта не переданы", "parts": {}, "findings": [], "dismissals": [], "degraded": [], "stats": {}}
    else:
        ds = DS.run(d_root, i_root, matched, plan, ds_inputs.get("lib"), ds_inputs.get("sources"), ds_inputs.get("colors"),
                    ds_inputs.get("colorsFile"), react_found)
        if ds_inputs.get("libProblem") and not ds["ran"]:
            ds["libProblem"] = ds_inputs["libProblem"]
    findings = [dict(f) for f in res["findings"]] + [dict(f) for f in ds["findings"]]
    d_index, d_parent = _index(d_root)
    i_index, _i_parent = _index(i_root)
    sup = SP.apply(findings, d_index, d_parent, i_index)
    groups = GR.fold(findings, d_index)
    defects = [_defect(g, d_index, i_index, template) for g in groups]
    defects.sort(key=lambda x: (SEVERITY_RANK.get(x["severity"], 1), LC_ORDER.get(x["layer"], 9), x.get("design") or "", x["id"]))
    emitted = defects if gate_ok else []
    dres = DEC.apply(emitted, decisions) if decisions else {"unknown": [], "reopened": [], "declined": [], "noise": [], "fix": []}
    life = LC.apply(emitted, history, noise)
    blocking = [d for d in emitted if d["severity"] in BLOCKING and not d["noise"]]
    by_layer: Dict[str, int] = {}
    by_sev: Dict[str, int] = {}
    for d in emitted:
        by_layer[d["layer"]] = by_layer.get(d["layer"], 0) + 1
        by_sev[d["severity"]] = by_sev.get(d["severity"], 0) + 1
    paired = {p["design"] for p in matched["pairs"]}
    def _nodes(ds_: List[Dict[str, Any]]) -> Set[str]:
        return {x for d in ds_ for x in (d.get("designIds") or [d.get("design")]) if x in paired}
    wrong = _nodes(blocking)
    noisy = _nodes([d for d in emitted if d["noise"]])
    content = (_content_ids(d_index) | wrong | noisy) & paired        # элемент — найденный узел с содержимым или с дефектом (рамка с неверным gap тоже)
    noise_only = noisy - wrong
    den = content - noise_only
    fidelity = round((len(den) - len(wrong & den)) / len(den), 4) if gate_ok and den else (1.0 if gate_ok else None)
    unmatched = matched["stats"]["unmatched"]
    low = bool(matched["lowMatchRate"])
    counts = {"findings": len(findings), "suppressed": sup["suppressed"], "defects": len(emitted), "blocking": len(blocking),
              "noise": sum(1 for d in emitted if d["noise"]), "byLayer": dict(sorted(by_layer.items())), "bySeverity": dict(sorted(by_sev.items())),
              "dismissals": len(res["dismissals"]) + len(ds["dismissals"])}
    status = "hard-gate-fail" if not gate_ok else ("clean" if not blocking else "defects")
    unverified = res["unverified"]
    nxt = _next_step(status, rate, fidelity, unmatched, counts, low and gate_ok, unverified["summary"])
    pairs = [{"design": p["design"], "impl": p["impl"], "anchor": p["anchor"], "confidence": p["confidence"]} for p in matched["pairs"]]
    return {
        "status": status, "matchRate": rate, "fidelity": fidelity, "unmatched": unmatched, "lowMatchRate": low, "hardGate": matched["hardGate"],
        "counts": counts, "layers": sup["layers"], "lifecycle": life["counts"], "fixed": life["fixed"], "nextStep": nxt,
        "decisions": {**dres, "problems": (decisions or {}).get("problems", []), "elsewhere": (decisions or {}).get("elsewhere", [])},
        "gates": {"match": "pass" if gate_ok else "fail", "check": "degraded" if res["degraded"] else "pass",
                  "ds": ("degraded" if ds["degraded"] else "pass") if ds["ran"] else "skipped",
                  "pixel": "skipped" if not gate_ok else "not-run", "vision": "skipped" if not gate_ok else "not-run"},
        "ds": {"ran": ds["ran"], "reason": ds.get("reason"), "parts": ds["parts"], "degraded": ds["degraded"], "libProblem": ds.get("libProblem")},
        "degraded": res["degraded"], "tolerances": res["tolerances"], "matchStats": matched["stats"],
        "unverified": unverified, "dismissals": {"count": counts["dismissals"], "samples": (res["dismissals"] + ds["dismissals"])[:SAMPLES]},
        "pairs": pairs, "defectIds": [d["id"] for d in emitted], "defects": emitted, "findings": findings, "dismissalsAll": res["dismissals"] + ds["dismissals"],
        "designUrlTemplate": template, "matched": matched,
    }


LC_ORDER = {layer: i for i, layer in enumerate(CK.LAYERS)}
LC_ORDER["ds"] = 2


# ---------------------------------------------------------------------------------------------------------------- диск

def _flags(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """overflowing / scrolls по id страницы: `impl_model` их сбрасывает, а правило OVERFLOW (4.4) читает их отсюда."""
    out: Dict[str, Dict[str, Any]] = {}
    stack = [snapshot.get("root")]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if n.get("overflowing") or n.get("scrolls"):
            out[n["id"]] = {k: True for k in ("overflowing", "scrolls") if n.get(k)}
        stack.extend(n.get("children") or [])
    return out


def _write(path: Path, doc: Any) -> None:
    CP._atomic_write(path, (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def run(root: Any, track: str, frame: Optional[str], iteration_dir: Path) -> Dict[str, Any]:
    """`daiver_verify_compare`: читает snapshot.json итерации, канон, план, требования ДС, историю; пишет четыре артефакта."""
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise VerifyCompareError(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    paths = TR.frame_paths(root, manifest, frame)
    if not CP.iteration_dir_ok(paths, iteration_dir):
        raise VerifyCompareError(f"iterationDir: папка итерации лежит под «verify» (или «baseline») кадра, а не «{iteration_dir}»")
    f = TR.get_frame(manifest, frame)
    if not paths["canonical"].is_file():
        raise VerifyCompareError("у кадра нет канона: сравнивать не с чем (его строит daiver_normalize)")
    try:
        design = C.read_canonical(paths["canonical"])
    except C.CanonError as e:
        raise VerifyCompareError(f"канон кадра не читается: {e}")
    snap_path = iteration_dir / "snapshot.json"
    if not snap_path.is_file():
        raise VerifyCompareError(f"в «{iteration_dir.name}» нет snapshot.json: сначала daiver_screenshot в эту папку")
    try:
        snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
        impl = IM.to_model(snapshot)
    except (OSError, ValueError, UnicodeDecodeError, IM.ImplModelError, C.CanonError) as e:
        raise VerifyCompareError(f"snapshot.json не читается как снимок страницы: {e}")
    cfg = CFG.load(root)["config"]
    capture = snapshot.get("capture") or {}
    design_nodes = CP._design_count(design) or 0
    elements = snapshot.get("elements", 0)
    under = bool(design_nodes) and elements < design_nodes * CP.UNDER_RENDER_RATIO
    ready = capture.get("ready") is not False
    out_files = {n: (iteration_dir / n).relative_to(root).as_posix() for n in ARTIFACTS}
    template = OL.node_url_template(f.get("frameUrl"), (design.get("daiver") or {}).get("sourceFormat"))
    iteration_dir.mkdir(parents=True, exist_ok=True)
    cap = {"ready": ready, "elements": elements, "designNodes": design_nodes, "underRendered": under,
           "domSettled": (capture.get("readiness") or {}).get("ready") if isinstance(capture.get("readiness"), dict) else None}
    _write(iteration_dir / "impl-model.json", impl)
    if not ready or under:
        why = "снимок не готов (страница ещё грузилась)" if not ready else f"страница недорисована: {elements} элементов при {design_nodes} узлах макета"
        summary = {"track": track, "frame": f["name"], "status": "recapture", "reason": why, "capture": cap, "defects": [], "defectIds": [], "pairs": [],
                   "nextStep": _next_step("recapture", 0.0, None, 0, {}, False, None), "files": out_files}
        _write(iteration_dir / "findings.json", {"findings": [], "dismissals": []})
        _write(iteration_dir / "defects.json", {"defects": [], "fixed": []})
        _write(iteration_dir / "compare-summary.json", summary)
        return {k: summary[k] for k in ("status", "reason", "capture", "nextStep", "files")}
    plan, _raw, _why = PL.read_plan(paths["plan"])
    try:
        ds_inputs = DS.gather(root, track, frame)
    except (DS.DsGateError, OSError, ValueError) as e:
        ds_inputs = {"lib": None, "libProblem": str(e), "sources": {}, "colors": None, "colorsFile": None}
    verify_dir = iteration_dir.parent
    history = LC.load_history(verify_dir, iteration_dir.name)
    noise = LC.declared_noise(history)
    try:
        decisions = DEC.load(root, manifest, frame)
    except DEC.DecisionsError as e:
        raise VerifyCompareError(str(e))
    rep = build_report(design, impl, plan, cfg["verify"]["tolerances"], _flags(snapshot), ds_inputs, history, noise, template, decisions)
    summary = {k: rep[k] for k in ("status", "matchRate", "fidelity", "unmatched", "lowMatchRate", "hardGate", "counts", "layers", "lifecycle", "fixed",
                                   "nextStep", "gates", "ds", "degraded", "tolerances", "unverified", "dismissals", "pairs", "defectIds",
                                   "designUrlTemplate", "decisions")}
    summary.update(track=track, frame=f["name"], capture=cap, files=out_files, declaredNoise=sorted(d["id"] for d in rep["defects"] if d.get("noise")))
    _write(iteration_dir / "findings.json", {"findings": rep["findings"], "dismissals": rep["dismissalsAll"]})
    _write(iteration_dir / "defects.json", {"defects": rep["defects"] if rep["status"] != "hard-gate-fail" else [], "fixed": rep["fixed"]})
    _write(iteration_dir / "compare-summary.json", summary)
    out = {k: summary[k] for k in ("status", "matchRate", "fidelity", "unmatched", "lowMatchRate", "counts", "lifecycle", "gates", "nextStep", "files",
                                   "capture") if k in summary}
    out["defects"] = rep["defects"][:20] if rep["status"] != "hard-gate-fail" else []
    if rep["unverified"]["summary"]:
        out["unverified"] = rep["unverified"]["summary"]
    if rep["degraded"]:
        out["degraded"] = rep["degraded"]
    return out
