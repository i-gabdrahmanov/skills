"""state.py — память цикла verify: `verify-state.json` кадра, открытие итерации и запись (спека §14.1, §14.2, §14.9, §14.11; задача 4.8;
INV-C21, C24, C25, C33, G4, G11, F12, F15).

Цикл заканчивает пользователь: чисто или стоп. Номера итераций в голове не считаются — всё на диске: итерация = папка `verify/iter-N/`
(не перезаписывается), запись — элемент `history[]` файла `verify-state.json` кадра. Модель файл не пишет (хук `state-write-guard`); пишет движок.

Три инструмента-обёртки (`tools.py`):
  • `open_iteration` (`daiver_verify_state`) — номер, `iterationDir`, `priorReview`, `goalImage`, `missingArt`, `warnStalling`, `history`,
    `implementation`, `mode`; итерация исполнена и не записана — `unrecordedIterations` и инструкция записать СЕЙЧАС; повтор до записи номер не
    двигает и ничего не пересоздаёт;
  • `record` (`daiver_verify_state_record`) — итог итерации: пять гейтов, `clean`, `blocking`, `progress` (`stalled`);
  • `decide` (`daiver_verify_decide`) — только решение человека (decision / fixed / declined) к уже записанной итерации: его зовёт ОРКЕСТРАТОР
    после ответа человека, поэтому он вне `phase_tools` (запись итерации — шаг субагента). Это отступление от спеки: там одна запись на оба случая.

Запись не доверяет словам агента там, где у движка есть факт: статусы гейтов `compare` и `code`, `matchRate`, `fidelity` берутся из
`compare-summary.json` / `code-summary.json` ПАПКИ ИТЕРАЦИИ, дефекты — из `defects.json` и `code-defects.json` (измеренное выигрывает у кода по общему id);
гейты `visualDiff` и `review`, `diffPercent` (проценты 0–100: 3.18 ≠ 0.0318), находки обзора и `reviewReport` — из `visual-diff.json` и `visual-review.json`
(нет артефакта — `skipped`, как бы ни назвал гейт агент; `fail` у этих двух невозможен: дифф и обзор — сигнал, не вердикт); гейт `e2e` и его падения — из `e2e-summary.json` /
`e2e-defects.json` (прогон `daiver_e2e_run`, 5.5; нет прогона — `skipped`, даже если агент записал `pass`). Агент добавляет только то, чего движок не знает: свои пункты vision / e2e в `defects`. Расхождение слов агента с фактом — в `warnings`, побеждает факт.

Правило clean (§14.9): блокируют только не-noise critical и major из зоны deterministic; vision не блокирует никогда; pre-existing
(`standing: pre-existing` при `scope` change — 6.4; при scope page считается всё) не блокирует; регрессия блокирует; hard gate fail и перезахват — не clean; любой другой failed-гейт —
не clean; skipped e2e/review не мешают; code fail блокирует (через блокирующие дефекты кода), skip — нет. Решения человека (`verify-decisions.json`)
накладываются до расчёта: false_positive → noise (не блокирует), skip → declined (блокирует, но не переспрашивается).

Пин захвата (4.9): первая записанная итерация закрепляет в `verify-state.json` рецепт открытия (actions, clipSelector…) и viewport из метаданных
снимка; `daiver_screenshot` отказывает на отличающиеся (`capture.run` → `implementation.pin_problems`), запись предупреждает, если отличие всё же просочилось.

Не проверено данными (боевых прогонов нет): порог «не лучше прошлой» для stall (число блокирующих; регресс — тоже stall); формат `reviewReport`
(его закрепит 5.2); как слабая модель читает `unrecordedIterations`; `implementation` — только то, что даёт манифест (URL и dev-сервер — 4.9).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import canon as C
from .. import config as CFG
from .. import svg_assets as SA
from .. import track as TR
from . import capture as CP
from . import decisions as DEC
from . import implementation as IMPL
from . import verify_report as VR

SCHEMA = 1
GATES = ("compare", "code", "visualDiff", "review", "e2e")
GATE_STATUS = ("pass", "fail", "skipped", "degraded")
DECISIONS = ("clean", "fix_all", "fix_some", "stop")
EXECUTED_MARKERS = ("snapshot.json", "compare-summary.json", "code-summary.json", "actual.png")
BLOCKING_SEVERITY = ("critical", "major")
KEEP = ("id", "code", "layer", "severity", "zone", "element", "message", "coverage", "lifecycle", "noise", "declined", "standing", "scope", "source",
        "decision", "designLink", "location", "rows", "rule", "prop", "design")
MARK_KEEP = ("reported", "problem", "pin", "designPin", "side", "impl")     # ручная метка человека несёт в историю всю постановку и рамку (INV-C34)
_ITER = re.compile(r"^iter-(\d+)$")


class VerifyStateError(ValueError):
    """Вызов не годится или файл состояния повреждён — сообщение называет причину и что делать."""


def _read(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise VerifyStateError(f"{path.name} не читается ({e}): файл пишет только движок — не правь его руками; удалить и начать цикл заново "
                               f"может только человек")
    return data if isinstance(data, dict) else None


def load(path: Path, track: str, frame: str) -> Dict[str, Any]:
    data = _read(path)
    if data is None:
        return {"schema": SCHEMA, "track": track, "frame": frame, "history": []}
    if data.get("schema") != SCHEMA or not isinstance(data.get("history"), list):
        raise VerifyStateError(f"{path.name}: схема {data.get('schema')!r} или нет history[] — файл не наш, поверх него не пишу")
    return data


def _write(path: Path, data: Dict[str, Any]) -> None:
    CP._atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def _rel(root: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _iter_dirs(verify_dir: Path) -> Dict[int, Path]:
    out: Dict[int, Path] = {}
    if verify_dir.is_dir():
        for p in verify_dir.iterdir():
            m = _ITER.match(p.name)
            if m and p.is_dir():
                out[int(m.group(1))] = p
    return dict(sorted(out.items()))


def _executed(d: Path) -> bool:
    return any((d / n).is_file() for n in EXECUTED_MARKERS)


def _json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ctx(root: Any, track: str, frame: Optional[str]) -> Tuple[Path, Dict[str, Any], Dict[str, Any], Dict[str, Path]]:
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise VerifyStateError(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    f = TR.get_frame(manifest, frame)
    return root, manifest, f, TR.frame_paths(root, manifest, f["name"])


def compact(h: Dict[str, Any]) -> Dict[str, Any]:
    """Строка истории для ответа state: без тяжёлых списков."""
    out = {"iteration": h["iteration"], "clean": h["clean"], "blocking": len(h.get("blocking") or []), "matchRate": h.get("matchRate"),
           "diffPercent": h.get("diffPercent"), "gates": {k: v["status"] for k, v in h["gates"].items()}}
    for k in ("decision", "fixed", "declined"):
        if h.get(k):
            out[k] = h[k]
    return out


def open_iteration(root: Any, track: str, frame: Optional[str] = None) -> Dict[str, Any]:
    root, manifest, f, paths = _ctx(root, track, frame)
    cfg = CFG.load(root)["config"]
    state = load(paths["verifyState"], track, f["name"])
    history = state["history"]
    last_n = max((h["iteration"] for h in history), default=0)
    dirs = _iter_dirs(paths["verify"])
    unrecorded = [n for n, d in dirs.items() if n > last_n and _executed(d)]
    warnings: List[str] = []
    if unrecorded:
        number = unrecorded[0]
        next_step = (f"итерация {number} исполнена, но НЕ записана: запиши её СЕЙЧАС (daiver_verify_state_record, iteration={number}) — без записи "
                     f"номер не двигается, priorReview пуст, progress и итог сломаны; новые гейты — только после записи")
    else:
        number = last_n + 1
        (paths["verify"] / f"iter-{number}").mkdir(parents=True, exist_ok=True)
        next_step = (f"итерация {number}: daiver_screenshot в {_rel(root, paths['verify'] / f'iter-{number}')} → daiver_verify_compare → "
                     f"daiver_code_compare → [дифф → обзор → e2e] → daiver_verify_state_record")
    iteration_dir = paths["verify"] / f"iter-{number}"
    goal = TR.raster_info(root, manifest, f["name"])
    try:
        arts, art_warn = SA.load_arts(root, manifest, f)
        missing = SA.missing_slugs(arts, paths["assets"])
        warnings.extend(art_warn)
    except SA.SvgAssetsError as e:
        missing = []
        warnings.append(str(e))
    if number == 1 and not unrecorded and (goal is None or not goal.get("exists")):
        warnings.append("у кадра нет goal image (visual-requirements.png/jpg): спроси у пользователя настоящую картинку дизайна и положи её в папку кадра "
                        "(повторный вызов безопасен: номер не растёт, пока нет записи); эталонный рендер — только если картинки нет вовсе")
    wf = cfg["workflow"]
    warn_after = wf.get("warnAfterVerifyIterations", 3)
    last = history[-1] if history else None
    warn: Optional[Dict[str, Any]] = None
    if last is not None and not last["clean"] and len(history) >= warn_after:
        warn = {"after": warn_after, "iterations": len(history),
                "text": f"записано итераций: {len(history)} (порог {warn_after}), а блокирующие дефекты остались — спроси человека, продолжать ли"}
    try:
        decided = DEC.load(root, manifest, f["name"])
    except DEC.DecisionsError as e:
        decided = {"decisions": {}, "problems": [str(e)], "elsewhere": [], "sources": []}
    declined = sorted({i for h in history for i in (h.get("declined") or [])} | {i for i, e in decided["decisions"].items() if e["verdict"] == "skip"})
    impl = IMPL.resolve(manifest, f["name"], wf.get("mode", "interactive"))
    pin = state.get("capture") if isinstance(state.get("capture"), dict) else None
    out: Dict[str, Any] = {"track": track, "frame": f["name"], "iteration": number, "iterationDir": _rel(root, iteration_dir),
                           "unrecordedIterations": unrecorded, "priorReview": (last or {}).get("reviewReport"),
                           "goalImage": goal, "missingArt": missing, "warnStalling": warn, "history": [compact(h) for h in history],
                           "implementation": impl, "pinned": pin, "mode": wf.get("mode", "interactive"),
                           "maxVerifyIterations": (wf.get("autonomous") or {}).get("maxVerifyIterations"),
                           "declinedSoFar": declined, "decisions": {"sources": decided["sources"], "problems": decided["problems"]},
                           "nextStep": next_step}
    if impl.get("blocked"):
        out["blocked"] = impl["blocked"]
        out["nextStep"] = impl["nextStep"]
    elif impl.get("need"):
        out["nextStep"] = "СНАЧАЛА недостающее для захвата: " + " ".join(impl["need"]) + " — " + out["nextStep"]
    if pin:
        out["nextStep"] += ("; рецепт, clipSelector и viewport уже закреплены прошлыми итерациями (pinned) — используй те же, "
                            "иначе daiver_screenshot откажет")
    if warnings:
        out["warnings"] = warnings
    return out


# ── запись ───────────────────────────────────────────────────────────────────────────────────────────

def _gate_arg(name: str, v: Any) -> Dict[str, Any]:
    if isinstance(v, str):
        v = {"status": v}
    if not isinstance(v, dict) or v.get("status") not in GATE_STATUS:
        raise VerifyStateError(f"gates.{name}: нужен статус {'|'.join(GATE_STATUS)} (строкой или {{status, reason?}})")
    out = {"status": v["status"]}
    if isinstance(v.get("reason"), str) and v["reason"].strip():
        out["reason"] = v["reason"].strip()
    return out


def _derive(iteration_dir: Path) -> Dict[str, Any]:
    """Факты итерации из артефактов движка: статусы compare / code, числа, дефекты обоих гейтов."""
    cmp_ = _json(iteration_dir / "compare-summary.json")
    code = _json(iteration_dir / "code-summary.json")
    facts: Dict[str, Any] = {"compare": None, "code": None, "matchRate": None, "fidelity": None, "hardGate": None, "defects": [], "unverified": None,
                             "visualDiff": None, "review": None, "diffPercent": None, "reviewReport": None, "e2e": None}
    if cmp_ is None:
        facts["compare"] = {"status": "skipped", "reason": "compare-summary.json нет: daiver_verify_compare в эту папку не вызывался"}
    else:
        st = cmp_.get("status")
        facts["matchRate"], facts["fidelity"] = cmp_.get("matchRate"), cmp_.get("fidelity")
        facts["unverified"] = (cmp_.get("unverified") or {}).get("summary")
        if st in ("clean", "defects"):
            facts["compare"] = {"status": "pass"}
        elif st == "hard-gate-fail":
            facts["compare"] = {"status": "fail", "reason": "hard gate: matchRate ниже порога — страница не этот кадр"}
            facts["hardGate"] = True
        else:
            facts["compare"] = {"status": "fail", "reason": cmp_.get("reason") or "перезахват: снимок не годится для сравнения"}
        for d in (_json(iteration_dir / "defects.json") or {}).get("defects") or []:
            if isinstance(d, dict) and isinstance(d.get("id"), str):
                facts["defects"].append(dict(d, source="dom"))
    if code is None:
        facts["code"] = {"status": "skipped", "reason": "code-summary.json нет: daiver_code_compare в эту папку не вызывался"}
    else:
        st = code.get("status")
        facts["code"] = {"status": st if st in GATE_STATUS else "skipped", **({"reason": code["reason"]} if code.get("reason") else {})}
        have = {d["id"] for d in facts["defects"]}
        for d in (_json(iteration_dir / "code-defects.json") or {}).get("defects") or []:
            if isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"] not in have:        # измеренное выигрывает у кода
                facts["defects"].append(dict(d, source="code", zone=d.get("zone") or "deterministic"))
    diff = _json(iteration_dir / "visual-diff.json")
    if diff is not None and isinstance(diff.get("diffPercent"), (int, float)) and not isinstance(diff["diffPercent"], bool):
        facts["visualDiff"], facts["diffPercent"] = {"status": "pass"}, diff["diffPercent"]
    e2e = _json(iteration_dir / "e2e-summary.json")
    if e2e is not None and e2e.get("status") in GATE_STATUS:
        facts["e2e"] = {"status": e2e["status"], **({"reason": e2e["reason"]} if e2e.get("reason") else {})}
        have = {d["id"] for d in facts["defects"]}
        for d in (_json(iteration_dir / "e2e-defects.json") or {}).get("defects") or []:
            if isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"] not in have:
                facts["defects"].append(dict(d, source="e2e", zone="e2e"))
    rev = _json(iteration_dir / "visual-review.json")
    if rev is not None and (iteration_dir / "visual-review.md").is_file():
        facts["review"] = {"status": "pass"}
        facts["reviewReport"] = {"mode": rev.get("mode"), "nothingFound": rev.get("nothingFound")}
        facts["visionDefects"] = [dict(d, source="review", zone="vision") for d in rev.get("defects") or [] if isinstance(d, dict) and isinstance(d.get("id"), str)]
    return facts


def reported_marks(f: Dict[str, Any], paths: Dict[str, Path], iteration_dir: Path, loaded: Dict[str, Any], existing: set) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Ручные метки человека (`verify-decisions.json → marks`) как пункты итерации и поимённые отказы — одним кодом для записи итерации и для отчёта."""
    design = paths["raster"] if f.get("raster") and paths["raster"].is_file() else None
    return DEC.reported(loaded, paths["verify"], iteration_dir, design, existing)


def _extra_defects(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise VerifyStateError("defects: ожидается список дополнительных пунктов (vision / e2e) — дефекты compare и code движок берёт сам")
    out = []
    for k, d in enumerate(raw):
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"]:
            raise VerifyStateError(f"defects[{k}]: нужен объект с id")
        if d.get("zone") not in ("vision", "e2e"):
            raise VerifyStateError(f"defects[{k}] ({d['id']}): сюда идут только пункты зон vision и e2e (zone), а дефекты compare/code движок читает "
                                   f"из папки итерации сам — не переписывай их")
        if d.get("severity") not in ("critical", "major", "minor"):
            raise VerifyStateError(f"defects[{k}] ({d['id']}): severity critical | major | minor")
        out.append({k2: v for k2, v in d.items() if k2 in KEEP or k2 in ("pin", "problem", "fix")})
    return out


def is_blocking(d: Dict[str, Any]) -> bool:
    """Правило clean для одного дефекта (§14.9)."""
    return (d.get("severity") in BLOCKING_SEVERITY and not d.get("noise") and d.get("zone", "deterministic") != "vision"
            and not (d.get("standing") == "pre-existing" and d.get("scope") != "page"))


def _progress(prev: Optional[Dict[str, Any]], blocking: List[str], defects: List[Dict[str, Any]]) -> Dict[str, Any]:
    regressed = [d["id"] for d in defects if d.get("lifecycle") == "regressed" and d["id"] in blocking]
    cur = len(blocking)
    out: Dict[str, Any] = {"blocking": cur, "regressed": regressed}
    if prev is None:
        out.update(previous=None, delta=None, stalled=False)
        return out
    before = len(prev.get("blocking") or [])
    stalled = bool(cur) and (cur >= before or bool(regressed))      # не лучше прошлой; регресс — тоже stall
    out.update(previous=before, delta=cur - before, stalled=stalled)
    return out


def record(root: Any, track: str, frame: Optional[str], iteration: Any, gates: Any, diff_percent: Any = None, review_report: Any = None,
           review_skipped: Any = None, defects: Any = None, decision: Any = None, fixed: Any = None, declined: Any = None) -> Dict[str, Any]:
    root, manifest, f, paths = _ctx(root, track, frame)
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 1:
        raise VerifyStateError("iteration: номер итерации из ответа daiver_verify_state (целое ≥ 1)")
    state = load(paths["verifyState"], track, f["name"])
    history = state["history"]
    existing = next((h for h in history if h["iteration"] == iteration), None)
    if existing is not None:
        raise VerifyStateError(f"итерация {iteration} уже записана: её значения не переписываются (решение человека добавляет daiver_verify_decide)")
    last_n = max((h["iteration"] for h in history), default=0)
    if iteration <= last_n:
        raise VerifyStateError(f"итерация {iteration} раньше последней записанной ({last_n}): история идёт вперёд")
    iteration_dir = paths["verify"] / f"iter-{iteration}"
    if not iteration_dir.is_dir() or not _executed(iteration_dir):
        raise VerifyStateError(f"в iter-{iteration} нет следов исполненных гейтов (snapshot.json / compare-summary.json): записывать нечего — "
                               f"запиши ту итерацию, номер которой назвал daiver_verify_state")
    if not isinstance(gates, dict) or set(gates) != set(GATES):
        raise VerifyStateError(f"gates: ровно пять имён — {', '.join(GATES)} (structural-гейта больше нет; не записывай «для истории»)")
    given = {n: _gate_arg(n, gates[n]) for n in GATES}
    if diff_percent is not None and (isinstance(diff_percent, bool) or not isinstance(diff_percent, (int, float)) or not 0 <= diff_percent <= 100):
        raise VerifyStateError("diffPercent: проценты 0–100 ровно так, как вернул дифф (3.18, а не 0.0318)")
    if decision is not None and decision not in DECISIONS:
        raise VerifyStateError(f"decision: {' | '.join(DECISIONS)}")
    for n in ("visualDiff", "review"):
        if given[n]["status"] == "fail":
            raise VerifyStateError(f"gates.{n}: fail невозможен — дифф и визуальный обзор сигнал, а не вердикт (pass | skipped | degraded)")
    extras = _extra_defects(defects)
    facts = _derive(iteration_dir)
    warnings: List[str] = []
    for name in ("compare", "code"):
        if given[name]["status"] != facts[name]["status"]:
            warnings.append(f"gates.{name}: записано «{given[name]['status']}», а артефакты итерации говорят «{facts[name]['status']}» — записан факт")
    try:
        loaded = DEC.load(root, manifest, f["name"])
    except DEC.DecisionsError as e:
        raise VerifyStateError(str(e))
    base = facts["defects"] + [d for d in facts.get("visionDefects", []) if d["id"] not in {x["id"] for x in facts["defects"]}]
    merged = base + [dict(d, source=d.get("source") or d["zone"]) for d in extras if d["id"] not in {x["id"] for x in base}]
    marks, rejected_marks = reported_marks(f, paths, iteration_dir, loaded, {d["id"] for d in merged})
    merged = merged + marks
    if rejected_marks:
        warnings.extend(rejected_marks)
    dres = DEC.apply(merged, loaded)
    gate_out: Dict[str, Dict[str, Any]] = {"compare": facts["compare"], "code": facts["code"]}
    for n in ("visualDiff", "review"):
        if facts[n] is not None:
            gate_out[n] = facts[n]
            if given[n]["status"] != "pass":
                warnings.append(f"gates.{n}: записано «{given[n]['status']}», а артефакты итерации говорят «pass» — записан факт")
        else:
            said = given[n].get("reason") or (review_skipped.strip() if n == "review" and isinstance(review_skipped, str) and review_skipped.strip() else None)
            gate_out[n] = {"status": "skipped", "reason": said or "гейт не исполнялся: артефактов итерации нет"}
            if given[n]["status"] != "skipped":
                warnings.append(f"gates.{n}: записано «{given[n]['status']}», а артефактов итерации нет (гейт не вызывался) — записан skipped")
    if facts["e2e"] is not None:
        gate_out["e2e"] = facts["e2e"]
        if given["e2e"]["status"] != facts["e2e"]["status"]:
            warnings.append(f"gates.e2e: записано «{given['e2e']['status']}», а прогон e2e (e2e-summary.json) говорит «{facts['e2e']['status']}» — записан факт")
    else:
        gate_out["e2e"] = given["e2e"]
        if given["e2e"]["status"] == "pass":                    # «прошёл» без прогона — слова; «упал» без прогона остаётся упавшим (сомнение в пользу остановки)
            gate_out["e2e"] = {"status": "skipped", "reason": "гейт не исполнялся: e2e-summary.json в папке итерации нет (daiver_e2e_run не вызывался)"}
            warnings.append("gates.e2e: записано «pass», а прогона e2e нет (daiver_e2e_run не вызывался) — записан skipped")
        elif given["e2e"]["status"] == "fail":
            warnings.append("gates.e2e: «fail» записан без прогона e2e (daiver_e2e_run не вызывался) — остаётся fail, причину назови в reason")
    if diff_percent is not None and facts["diffPercent"] is None:
        warnings.append("diffPercent передан, а visual-diff.json в папке итерации нет (daiver_visual_diff не вызывался) — значение отброшено")
        diff_percent = None
    elif facts["diffPercent"] is not None:
        if diff_percent is not None and diff_percent != facts["diffPercent"]:
            warnings.append(f"diffPercent: передано {diff_percent}, а дифф вернул {facts['diffPercent']} (проценты 0–100) — записан факт")
        diff_percent = facts["diffPercent"]
    if facts["reviewReport"] is not None:
        review_report = dict(facts["reviewReport"], path=_rel(root, iteration_dir / "visual-review.md"))
        review_skipped = None
    blocking_defects = [d for d in merged if is_blocking(d)]
    blocking = [d["id"] for d in blocking_defects]
    code_blocking = [d for d in blocking_defects if d.get("source") == "code"]
    if gate_out["code"]["status"] == "fail" and not code_blocking:
        gate_out["code"] = {"status": "pass", "reason": "блокирующих дефектов кода не осталось (noise / решения человека)"}
    e2e_defects = [d for d in merged if d.get("source") == "e2e"]
    if gate_out["e2e"]["status"] == "fail" and e2e_defects and not any(is_blocking(d) for d in e2e_defects):
        gate_out["e2e"] = {"status": "pass", "reason": "блокирующих падений e2e не осталось (noise / решения человека)"}
    failed = [n for n, g in gate_out.items() if g["status"] == "fail"]
    skipped_required = [n for n in ("compare",) if gate_out[n]["status"] == "skipped"]
    clean = not blocking and not failed and not skipped_required
    if decision == "clean" and not clean:
        raise VerifyStateError("decision: clean нельзя — итерация не чистая (" + ("; ".join(
            ([f"блокирующих дефектов: {len(blocking)}"] if blocking else []) + ([f"гейты fail: {', '.join(failed)}"] if failed else [])
            + ([f"не исполнен: {', '.join(skipped_required)}"] if skipped_required else []))) + ")")
    prev = history[-1] if history else None
    prog = _progress(prev, blocking, merged)
    rec: Dict[str, Any] = {"iteration": iteration, "dir": _rel(root, iteration_dir), "gates": gate_out, "matchRate": facts["matchRate"],
                           "fidelity": facts["fidelity"], "diffPercent": diff_percent, "clean": clean, "blocking": blocking, "progress": prog,
                           "defects": [{k: v for k, v in d.items() if k in KEEP or (d.get("reported") and k in MARK_KEEP)} for d in merged]}
    if review_report is not None:
        rec["reviewReport"] = review_report
    if isinstance(review_skipped, str) and review_skipped.strip():
        rec["reviewSkipped"] = review_skipped.strip()
    elif gate_out["review"]["status"] == "skipped" and gate_out["review"].get("reason"):
        rec["reviewSkipped"] = gate_out["review"]["reason"]
    if facts["unverified"]:
        rec["unverified"] = facts["unverified"]
    if decision is not None:
        rec["decision"] = decision
    for k, v in (("fixed", fixed), ("declined", declined)):
        ids = _ids(k, v, {d["id"] for d in merged})
        if ids:
            rec[k] = ids
    got_pin = IMPL.pin_of((_json(iteration_dir / "snapshot.json") or {}).get("capture"))
    if got_pin is not None:
        if "capture" not in state:
            state["capture"] = got_pin                          # первая записанная итерация пинит рецепт и viewport на весь цикл
        else:
            drift = IMPL.pin_problems(state["capture"], got_pin["recipe"], got_pin["viewport"])
            if drift:
                warnings.append("захват этой итерации отличается от закреплённого прошлыми (INV-C23): " + "; ".join(drift))
    history.append(rec)
    _write(paths["verifyState"], state)
    report_path = VR.write(root, manifest)
    answered = {"fix": dres["fix"], "skip": dres["declined"], "false_positive": dres["noise"], "reopened": dres["reopened"]}
    out: Dict[str, Any] = {"iteration": iteration, "clean": clean, "blocking": len(blocking), "blockingIds": blocking, "progress": prog,
                           "gates": {k: v["status"] for k, v in gate_out.items()},
                           "decisions": {"answered": {k: v for k, v in answered.items() if v}, "reported": [m["id"] for m in marks], "answeredElsewhere": loaded["elsewhere"],
                                         "unknown": dres["unknown"]},
                           "report": _rel(root, report_path), "stateRecorded": True}
    if warnings:
        out["warnings"] = warnings
    if loaded["problems"]:
        out["decisionProblems"] = loaded["problems"]
    return out


def _ids(label: str, v: Any, known: set) -> List[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise VerifyStateError(f"{label}: список id дефектов")
    return sorted(set(v))


def decide(root: Any, track: str, frame: Optional[str], iteration: Any, decision: Any, fixed: Any = None, declined: Any = None) -> Dict[str, Any]:
    """Решение человека к записанной итерации. `clean` — только если итерация чистая; `stop` — всегда."""
    root, manifest, f, paths = _ctx(root, track, frame)
    if decision not in DECISIONS:
        raise VerifyStateError(f"decision: {' | '.join(DECISIONS)}")
    state = load(paths["verifyState"], track, f["name"])
    rec = next((h for h in state["history"] if h["iteration"] == iteration), None)
    if rec is None:
        raise VerifyStateError(f"итерация {iteration} не записана: сначала daiver_verify_state_record (её пишет шаг итерации)")
    if rec.get("decision") and rec["decision"] != decision:
        raise VerifyStateError(f"итерация {iteration}: решение уже записано — «{rec['decision']}»; переписывать его нельзя")
    if decision == "clean" and not rec["clean"]:
        raise VerifyStateError(f"decision: clean нельзя — итерация {iteration} не чистая (блокирующих дефектов: {len(rec['blocking'])})")
    known = {d["id"] for d in rec["defects"]}
    rec["decision"] = decision
    for k, v in (("fixed", fixed), ("declined", declined)):
        ids = _ids(k, v, known)
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise VerifyStateError(f"{k}: неизвестные id дефектов этой итерации — {', '.join(unknown[:5])}")
        if ids:
            rec[k] = ids
    _write(paths["verifyState"], state)
    return {"iteration": iteration, "decision": decision, "fixed": rec.get("fixed", []), "declined": rec.get("declined", []),
            "report": _rel(root, VR.write(root, manifest)), "clean": rec["clean"]}
