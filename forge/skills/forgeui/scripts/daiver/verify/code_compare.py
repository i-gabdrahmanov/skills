"""code_compare.py — код-гейт `daiver_code_compare`: исходники модуля против макета без браузера (спека §14.5; задача 4.7; INV-C29, F4).

Второй детерминированный свидетель после DOM-гейта: читает СОБСТВЕННЫЕ исходники модуля (allowlist — `outputs` build-state + `mocks.ts`
рядом), строит дерево формы канона (`jsx_tree.build`), сверяет с макетом тем же матчером и проверяет только text · content · coverage —
ничего числового (у дерева кода нет стилей: числовое правило осудило бы каждый библиотечный компонент). Даёт `file:line` и защищает от
ложного MISSING.

Сверка (защита от ЛОЖНОГО MISSING — критической ошибки, блокирующей «чисто»):
  1. пары DOM-гейта (`compare-summary.json` рядом): узел макета, сматченный страницей, — dismissal «рендерится кодом вне сканлиста»;
     нет ни в странице, ни в коде — `corroborated` (сильнейшее свидетельство отсутствия, тот же id, что у DOM-гейта). Нет сводки DOM —
     MISSING остаётся `unconfirmed`: уликой, но не дефектом;
  2. маркеры динамики: `unresolved-import` в поддереве сматченного предка снимает MISSING в unverified content; `dynamic-text` — текстовый
     MISSING в unverified copy; `conditional` на узле — расхождение текста демотируется (макет рисует одно состояние);
  3. планка доверия: matchRate < MIN_MATCH_RATE → `treeRecognized: false`; находки остаются уликами, дефектов нет.

Дедуп с DOM-гейтом по построению: id = sha1(code | designId | implId | prop)[:12], у MISSING implId не входит — тот же id, что у DOM-гейта,
и если он уже записан DOM-гейтом (`defectIds` сводки), код-гейт его не повторяет (измеренное выигрывает). Жизненный цикл дефектов
(появление/исчезновение между итерациями) — задача 4.6; здесь дефект просто несёт стабильный id.

Вердикт гейта: `fail` — только при блокирующих дефектах кода (critical); `skipped` — Vue/Svelte, нет build-state, исходники пропали, дерево
не узнано — ВСЕГДА с причиной и никогда не блокирует; конфиг (`project.framework`) против файлов: файлы сильнее, расхождение — warning.

Правка существующей страницы (6.4): allowlist — записанные `sources` кадра (секция `page`; секции сборки с общими файлами сливаются с ней).
В `<кадр>/baseline` (захват базы ДО правок) гейт сначала копирует `sources` в `baseline/sources/` (`page_base.snapshot_sources`) и строит дерево
из них; на проверке дефекты кода получают `standing`/`scope` по регионам и ключам базы (находки DOM- и код-гейта базы) — pre-existing при scope
change не валит гейт.

Не проверено данными (боевых модулей и сводок DOM нет): доля ложных MISSING на реальном модуле (хуки, условный рендер через вызовы,
компоненты из алиасов `@/…`); формат `compare-summary.json` (его пишет 4.6 — читается терпимо: `pairs[{design,impl}]`, `defectIds[]`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from .. import baseline as BL
from .. import build_state as BS
from .. import canon as C
from .. import config as CFG
from .. import page_base as PB
from .. import plan as PL
from .. import track as TR
from . import capture as CP
from . import check as CK
from . import ids as IDS
from . import jsx_tree as JT
from . import match as MT
from . import tsx_scan as TS

KEPT_CODES = ("MISSING", "TEXT", "SLOT_TEXT", "SLOT_MISSING")
ARTIFACTS = ("code-model.json", "code-findings.json", "code-defects.json", "code-summary.json")
MAX_SOURCE_BYTES = 1024 * 1024
NON_REACT = (".vue", ".svelte")
SAMPLES = 5


defect_id = IDS.defect_id                 # id дефекта живёт в ids.py (общий с DOM-гейтом); имя оставлено для вызывающих и тестов


class CodeCompareError(ValueError):
    """Вызов или входы не годятся — сообщение называет причину."""


def editor_link(template: str, root: Path, file: str, line: int, column: int) -> str:
    """`{path}` — абсолютный путь с URL-кодированными сегментами, `{line}`/`{column}` — с 1."""
    full = (Path(root).resolve() / file).as_posix()
    path = "/".join(quote(seg, safe="") for seg in full.split("/"))
    return template.replace("{path}", path).replace("{line}", str(line)).replace("{column}", str(column))


def _index(node: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Optional[str]]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    parent: Dict[str, Optional[str]] = {}
    stack: List[Tuple[Dict[str, Any], Optional[str]]] = [(node, None)]
    while stack:
        n, par = stack.pop()
        nodes[n["id"]] = n
        parent[n["id"]] = par
        stack.extend((k, n["id"]) for k in (n.get("children") or []) if isinstance(k, dict))
    return nodes, parent


def _under(model: Dict[str, Any], code_id: str, kinds: Iterable[str]) -> Optional[Tuple[str, str]]:
    """Первая метка из kinds у узла кода или его потомка: (id узла, вид)."""
    want = set(kinds)
    for nid in sorted(model["marks"]):
        if code_id == "code:root" or nid == code_id or nid.startswith(code_id + "/"):
            for k in model["marks"][nid]:
                if k in want:
                    return nid, k
    return None


def compare(design: Any, model: Dict[str, Any], dom: Optional[Dict[str, Any]] = None, plan: Any = None,
            tolerances: Optional[Dict[str, Any]] = None, editor_template: Optional[str] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """Макет, модель кода (`jsx_tree.build`), сводка DOM-гейта (необязательно) → {findings, dismissals, defects, corroborated, unverified,
    treeRecognized, matchRate, ledger, status, designLocations, stats}."""
    d_root = design["root"] if isinstance(design, dict) and "root" in design and "kind" not in design else design
    matched = MT.match(d_root, model["root"], plan, descend_root=False)
    rate = matched["stats"]["matchRate"]
    recognized = bool(matched["hardGate"]["pass"])
    res = CK.check(d_root, model["root"], matched, tolerances, rules=("structure", "text", "slots"))
    d_nodes, d_parent = _index(d_root)
    c_nodes, _c_parent = _index(model["root"])
    paired = {p["design"]: p["impl"] for p in matched["pairs"]}
    dom_pairs = {p["design"] for p in (dom or {}).get("pairs") or [] if isinstance(p, dict) and p.get("design")}
    dom_ids = set((dom or {}).get("defectIds") or [])
    findings = [f for f in res["findings"] if f["code"] in KEPT_CODES]
    kept: List[Dict[str, Any]] = []
    dismissals: List[Dict[str, Any]] = [dict(d, gate="code") for d in res["dismissals"] if d["code"] in KEPT_CODES]
    unverified: List[Dict[str, Any]] = []
    corroborated: List[str] = []

    def dismiss(f: Dict[str, Any], reason: str, aspect: Optional[str] = None) -> None:
        dismissals.append({"code": f["code"], "layer": f["layer"], "design": f.get("design"), "impl": f.get("impl"), "reason": reason,
                           "gate": "code", **({"prop": f["prop"]} if f.get("prop") else {})})
        if aspect:
            unverified.append({"design": f.get("design"), "aspect": aspect, "reason": reason})

    for f in findings:
        d = f.get("design")
        if f["code"] == "MISSING":
            anc = d_parent.get(d)
            while anc is not None and anc not in paired:
                anc = d_parent.get(anc)
            ic = paired.get(anc) if anc else None
            if ic:
                hit = _under(model, ic, ("unresolved-import",))
                if hit:
                    dismiss(f, f"рядом неразрешённый импорт ({hit[0]}): что внутри — скан не видит; нет суждения", "content")
                    continue
                if d_nodes[d].get("kind") == "text" and _under(model, ic, ("dynamic-text",)):
                    dismiss(f, "рядом динамический текст: слова неизвестны — копия не оценивается", "copy")
                    continue
            if dom is None:
                kept.append(dict(f, status="unconfirmed", note="сводки DOM-гейта нет: отсутствие не подтверждено страницей"))
            elif d in dom_pairs:
                dismiss(f, "рендерится кодом вне сканлиста: DOM-гейт нашёл этот узел на странице")
            else:
                corroborated.append(d)
                kept.append(dict(f, status="corroborated"))
            continue
        ic = f.get("impl")
        marks = model["marks"].get(ic or "", [])
        if "dynamic-text" in marks:
            dismiss(f, "динамический текст: слова неизвестны — копия не оценивается", "copy")
            continue
        if "conditional" in marks:
            dismiss(f, "условный рендер: макет рисует одно состояние — расхождение не судится")
            continue
        kept.append(dict(f, status="confirmed"))

    template = editor_template or CFG.DEFAULTS["verify"]["editorUrlTemplate"]
    defects: List[Dict[str, Any]] = []
    deduped = 0
    if recognized:
        for f in kept:
            if f["status"] == "unconfirmed":
                continue
            did = IDS.defect_id(f["code"], f.get("design"), f.get("impl"), f.get("prop"))
            if did in dom_ids:
                deduped += 1
                continue
            loc = _locate(model, f, paired, d_parent)
            rec: Dict[str, Any] = {"id": did, "code": f["code"], "layer": f["layer"], "severity": f["severity"], "design": f.get("design"),
                                   "message": f["message"], "status": f["status"], "gate": "code"}
            for k in ("impl", "prop", "expected", "actual"):
                if f.get(k) is not None:
                    rec[k] = f[k]
            if loc:
                rec["location"] = loc
                if root is not None:
                    rec["editor"] = editor_link(template, root, loc["file"], loc["line"], loc["column"])
            defects.append(rec)
    defects.sort(key=lambda x: (x["code"], x.get("design") or "", x["id"]))
    blocking = [x for x in defects if x["severity"] == "critical"]
    if not recognized:
        status, reason = "skipped", (f"дереву кода гейт не доверяет: matchRate {rate} < {MT.MIN_MATCH_RATE} — находки уликами, дефектов нет")
    else:
        status, reason = ("fail" if blocking else "pass"), None
    by = lambda codes: [x for x in defects if x["code"] in codes]      # noqa: E731
    ledger = {
        "code-tree": {"status": "pass" if recognized else "skipped", "matchRate": rate, **({"reason": reason} if reason else {})},
        "code-structure": {"status": "skipped" if not recognized else ("fail" if by(("MISSING",)) else "pass"), "defects": len(by(("MISSING",)))},
        "code-text": {"status": "skipped" if not recognized else ("fail" if by(("TEXT", "SLOT_TEXT", "SLOT_MISSING")) else "pass"),
                      "defects": len(by(("TEXT", "SLOT_TEXT", "SLOT_MISSING")))},
        "code-reconcile": {"status": "pass", "dismissed": len([d for d in dismissals if d["gate"] == "code"]), "corroborated": len(corroborated),
                           "unconfirmed": len([k for k in kept if k["status"] == "unconfirmed"]), "deduped": deduped},
    }
    design_locations: Dict[str, Dict[str, Any]] = {}
    for d, ic in sorted(paired.items()):
        loc = model["locations"].get(ic)
        if loc:
            design_locations[d] = dict(loc, **({"editor": editor_link(template, root, loc["file"], loc["line"], loc["column"])} if root is not None else {}))
    return {"status": status, **({"reason": reason} if reason else {}), "treeRecognized": recognized, "matchRate": rate, "findings": kept,
            "dismissals": dismissals, "defects": defects, "corroborated": sorted(corroborated), "unverified": unverified, "ledger": ledger,
            "designLocations": design_locations, "degraded": res["degraded"],
            "stats": {"pairs": len(matched["pairs"]), "lowConfidence": matched["stats"]["lowConfidence"], "missing": len(matched["missing"]),
                      "findings": len(kept), "defects": len(defects), "blocking": len(blocking), "dismissals": len(dismissals)}}


def _locate(model: Dict[str, Any], f: Dict[str, Any], paired: Dict[str, str], d_parent: Dict[str, Optional[str]]) -> Optional[Dict[str, Any]]:
    ic = f.get("impl")
    if ic is None and f.get("design"):
        anc = d_parent.get(f["design"])
        while anc is not None and anc not in paired:
            anc = d_parent.get(anc)
        ic = paired.get(anc) if anc else None
    return model["locations"].get(ic) if ic else None


# ---------------------------------------------------------------------------------------------------------------- диск

def _dom_summary(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _siblings_mocks(root: Path, outputs: List[str]) -> List[str]:
    found: List[str] = []
    dirs = sorted({str(Path(o).parent) for o in outputs} | {str(Path(o).parent.parent) for o in outputs})
    for d in dirs:
        for name in ("mocks.ts", "mocks.tsx", "mocks.js"):
            rel = (Path(d) / name).as_posix()
            if rel not in outputs and rel not in found and (root / rel).is_file():
                found.append(rel)
    return found


def _skipped(reason: str, warnings: List[str]) -> Dict[str, Any]:
    return {"status": "skipped", "reason": reason, "treeRecognized": None, "matchRate": None, "findings": [], "dismissals": [], "defects": [],
            "corroborated": [], "unverified": [], "designLocations": {}, "degraded": [], "warnings": warnings,
            "ledger": {k: {"status": "skipped", "reason": reason} for k in ("code-tree", "code-structure", "code-text", "code-reconcile")},
            "stats": {"pairs": 0, "findings": 0, "defects": 0, "blocking": 0, "dismissals": 0}}


def _write(path: Path, doc: Any) -> None:
    CP._atomic_write(path, (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def run(root: Any, track: str, frame: Optional[str], iteration_dir: Path) -> Dict[str, Any]:
    """`daiver_code_compare`: читает макет, build-state, исходники outputs, сводку DOM-гейта рядом; пишет четыре артефакта в папку итерации."""
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise CodeCompareError(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    paths = TR.frame_paths(root, manifest, frame)
    if not CP.iteration_dir_ok(paths, iteration_dir):
        raise CodeCompareError(f"iterationDir: папка итерации лежит под «verify» (или «baseline») кадра, а не «{iteration_dir}»")
    fr = TR.get_frame(manifest, frame)
    fname = fr["name"]
    why = PB.capture_problem(root, manifest, fr, iteration_dir)
    if why:
        raise CodeCompareError(why)
    capturing = PB.in_baseline(paths, iteration_dir)
    cfg = CFG.load(root)["config"]
    warnings: List[str] = []
    design = None
    if paths["canonical"].is_file():
        try:
            design = C.read_canonical(paths["canonical"])
        except C.CanonError as e:
            raise CodeCompareError(f"канон кадра не читается: {e}")
    if design is None:
        raise CodeCompareError("у кадра нет канона: сравнивать не с чем (его строит daiver_normalize)")
    iteration_dir.mkdir(parents=True, exist_ok=True)

    def finish(result: Dict[str, Any], model: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        result.setdefault("warnings", warnings)
        summary = {k: result[k] for k in ("status", "reason", "treeRecognized", "matchRate", "ledger", "stats", "corroborated", "unverified",
                                          "designLocations", "standing") if k in result}
        summary["warnings"] = result["warnings"]
        if model is not None:
            summary["sections"] = model["sections"]
            summary["dynamics"] = model["stats"]["dynamics"]
        summary["dismissalSamples"] = result["dismissals"][:SAMPLES]
        _write(iteration_dir / "code-summary.json", summary)
        _write(iteration_dir / "code-findings.json", {"findings": result["findings"], "dismissals": result["dismissals"]})
        _write(iteration_dir / "code-defects.json", {"defects": result["defects"]})
        _write(iteration_dir / "code-model.json", {"daiver": {"version": "0.1.0", "role": "canonical", "sourceFormat": "code"},
                                                   "root": model["root"] if model else None, "locations": model["locations"] if model else {},
                                                   "dynamics": (model or {"markers": []})["markers"], "warnings": result["warnings"]})
        out = {k: result[k] for k in ("status", "reason", "treeRecognized", "matchRate", "stats", "ledger", "standing") if k in result}
        if capturing:
            out["pageBaseline"] = ("копии файлов страницы записаны в baseline/sources/: база снята целиком — дальше daiver_frame_baseline "
                                   "(регионы изменения и их место в коде)")
        out.update(files={n: (iteration_dir / n).relative_to(root).as_posix() for n in ARTIFACTS}, defects=result["defects"][:20],
                   warnings=result["warnings"], track=track, frame=fname)
        return out

    if capturing:
        copied, warns = PB.snapshot_sources(root, fr, paths)
        warnings.extend(warns)
        if not copied:
            return finish(_skipped("ни одного файла страницы (sources) не прочесть — копии базы нет", warnings), None)
        sections, mine = [{"id": "page", "outputs": copied}], copied
    else:
        state = BS.load_state(root, paths["buildState"], track, fname)
        current = [{"id": s["id"], "outputs": list(s.get("outputs") or [])} for s in state["sections"] if s.get("outputs")] if state else []
        if TR.is_page(fr):
            # правка страницы (6.4): страница — записанные sources; правки сборки лежат в тех же файлах (или в новых рядом)
            sections = BL.code_sections([[{"id": "page", "outputs": sorted(fr["sources"])}]] + ([current] if current else []))
            mine = sorted({o for s in sections for o in s["outputs"]})
        else:
            if state is None:
                return finish(_skipped("нет build-state.json: исходников модуля нет (сборка не начата)", warnings), None)
            mine = sorted({o for s in current for o in s["outputs"]})
            if not mine:
                return finish(_skipped("в build-state нет ни одного файла outputs", warnings), None)
            # ревизия (6.3): код shared-частей собран прежними версиями, правки ревизии — в тех же файлах: сканируются файлы КАЖДОЙ версии
            sections = BL.code_sections(BL.version_sections(root, manifest, fname) + [current])
    outputs = sorted({o for s in sections for o in s["outputs"]})
    non_react = [o for o in outputs if o.lower().endswith(NON_REACT)]
    react_files = [o for o in outputs if TS.is_js(o)]
    if non_react and not react_files:
        return finish(_skipped(f"Vue/Svelte ({non_react[0]}): адаптера для этого фреймворка нет — шиппабл только React", warnings), None)
    framework = cfg["project"]["framework"]
    if react_files and framework != "react":
        warnings.append(f"project.framework = {framework}, а в outputs файлы React ({react_files[0]}): файлы сильнее конфига — разбираю как React")
    files: Dict[str, str] = {}
    for rel in outputs + _siblings_mocks(root, outputs):
        full = root / rel
        try:
            full.resolve().relative_to(root)
            data = full.read_bytes()
        except (OSError, ValueError):
            if rel in mine:                     # файл прежней версии, удалённый ревизией, — не находка
                warnings.append(f"{rel}: файла нет или он не читается")
            continue
        if len(data) > MAX_SOURCE_BYTES:
            warnings.append(f"{rel}: больше {MAX_SOURCE_BYTES} байт — пропущен")
            continue
        try:
            files[rel] = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            warnings.append(f"{rel}: не UTF-8 — пропущен")
    if not any(o in files for o in react_files):
        return finish(_skipped("исходники из outputs пропали с диска (см. warnings)", warnings), None)
    model = JT.build(files, sections)
    warnings.extend(model["warnings"])
    if not model["root"].get("children"):
        return finish(_skipped("в исходниках не нашлось ни одного компонента с JSX", warnings), model)
    problems = C.validate_node(model["root"])
    if problems:
        raise CodeCompareError("модель кода нарушает схему канона (баг адаптера): " + "; ".join(problems[:3]))
    plan, _raw, _why = PL.read_plan(paths["plan"])
    dom = _dom_summary(iteration_dir / "compare-summary.json")
    result = compare(design, model, dom, plan, cfg["verify"]["tolerances"], cfg["verify"]["editorUrlTemplate"], root)
    result["warnings"] = warnings
    if TR.is_page(fr) and not capturing and TR.page_captured(fr):
        _page_standing(root, manifest, fr, paths, design, result)
    return finish(result, model)


def _page_standing(root: Path, manifest: dict, f: dict, paths: Dict[str, Path], design: Any, result: Dict[str, Any]) -> None:
    """standing дефектов кода правки страницы: регионы изменения и ключи базы (находки DOM- и код-гейта, записанные при захвате)."""
    res = PB.analyze(root, manifest, f["name"], design)
    if not res.get("available"):
        result["warnings"].append(f"правка страницы: регионов нет ({res.get('reason')}) — дефекты кода считаются все")
        return
    keys = set()
    for name in ("findings.json", "code-findings.json"):
        got = _dom_summary(paths["baseline"] / name) or {}
        keys |= PB.base_keys([x for x in got.get("findings") or [] if isinstance(x, dict) and isinstance(x.get("code"), str)], [])
    st = PB.classify(result["defects"], res, keys)
    blocking = [x for x in result["defects"] if x["severity"] == "critical" and not (x.get("standing") == "pre-existing" and x.get("scope") != "page")]
    result["stats"]["blocking"] = len(blocking)
    result["standing"] = st
    if result["status"] == "fail" and not blocking:
        result["status"] = "pass"
