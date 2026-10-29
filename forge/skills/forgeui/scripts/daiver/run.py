# -*- coding: utf-8 -*-
"""run.py — прогон forge-ui: открытие и закрытие, `run.json`, режим, именованные остановы с `supply` (спека §15.1, §15.3, §15.5, §20.3;
задача 7.1; INV-B22, C38, D2, D3, D5, D7, D10, E1, E2, E8, G8; QG-U11, P5).

Режим не вычисляется в голове и не носится через шаги: `open_run` его решает и ЗАПИСЫВАЕТ в `.daiver/tracks/<KEY>/run.json` (пишет только
движок — охраняет state-write-guard), остальные читают назад (`mode_of`: `daiver_status` → `tracks[].run`, `daiver_verify_state` → `run`,
`mode`; `daiver_verify_report`, `daiver_init`, `daiver_map_append`). Приоритет (INV-E1): `mode=` строки > `workflow.embeddedMode` (дефолт
autonomous — прогон начат директивой) > `workflow.mode` (дефолт interactive — начат человеком); кто начал, решает дефолт.

`open_run(track | directive)`:
  • malformed директива — каждое нарушение дословно (`directive.parse` + `check_against`), НИЧЕГО не пишется;
  • ключи строки, которые ложатся в манифест (url, url.<кадр>, state.<кадр>, sources, scope, specs), пишет сам — у кадров, которые уже
    есть в треке; ключи кадров, которых ещё нет (их заводит pull), — `pending` (повторное открытие той же строкой допишет их);
  • `track-conflict` (INV-D7): у кадра трека другая ссылка на макет или другая выгрузка, чем в строке — останов, не перезапись и не слияние
    (изменение собранного — `revision=`);
  • `startsAt`: track — строка несёт макет, которого в треке нет; resolve — требование без плана; build — план есть (или `from=plan`);
    verify — собрано; done — трек чист. `from=plan` (§15.5) и автономная сборка проверяют контракт плана ДО кода: не готов —
    `blocked: plan-incomplete (<id…>)`, не подписан — `blocked: plan-unsigned` (подпись — согласие человека, отклонение D9);
  • `policy` — что прогон делает в точках решения (подпись плана, резолв, verify: отчёт, решение, остановы, потолок итераций);
  • blocked — тоже запись (открытый прогон с `blocked` и `supply`): его закрывает `finish`, и всё остаётся возобновимым (INV-G8).
    Исключение — interactive без конфига: проекта нет, человек инициализирует сам (`startsAt: init`), записи нет;
  • устаревший рекорд (INV-E8): открыт прогон директивы, а начинает человек (или другая строка) — прежний закрывается с исходом из
    истории, новый открывается. Та же строка — возобновление того же прогона.
`finish_run(track, blocked?)` (INV-C38): исход — из ЗАПИСАННОЙ истории verify кадров, не из памяти: clean | defects-remaining (сколько
блокирующих осталось) | blocked:<причина> | never-run (кадр не проверен; `until=resolve|build` — честно never-run, INV-D10). Готовая
заметка для чекбокса таск-листа — `note`.

Отклонения: `spec-unapproved` и `no-browser` — причины forge поверх §20.3 (шаг ui-spec с утверждением человека и поиск браузера на
план-этапе — шаги forge-ui, в daiver их нет). Времени в записи нет (детерминизм): номер прогона `n` растёт с каждым открытием.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config as CFG
from . import directive as DR
from . import track as TR

RUN_FILE = "run.json"
SCHEMA = 1
STARTS = ("init", "track", "resolve", "build", "verify", "done")
STAGE_START = {"requirement": "resolve", "normalized": "resolve", "planned": "build", "building": "build", "built": "verify",
               "verifying": "verify", "clean": "done"}
LADDER = ("empty", "requirement", "normalized", "planned", "building", "built", "verifying", "clean")
BLOCKED = {
    "not-initialized": "человек: инициализация проекта (daiver_init в интерактивном прогоне — источник библиотеки, outputDir и buildCommand "
                       "не угадываются)",
    "no-requirement": "ссылка на кадр (frame= / frame.<кадр>=) и при нужде файл выгрузки (file=) в строке задачи — или pull кадра человеком",
    "track-conflict": "решение человека: новый трек (другой ключ) или продолжение — изменение собранного кадра (revision=)",
    "no-sources": "человек: найденные файлы существующей страницы (sources= в строке задачи или daiver_track_manifest)",
    "no-implementation": "адрес реализации кадра: url= / url.<кадр>= в строке задачи или запись daiver_track_manifest",
    "no-open-recipe": "рецепт открытия кадра (actions + clipSelector) — от того, кто добился его видимости на странице",
    "implementation-unreachable": "живой dev-сервер или рабочий implementation.devCommand в track.yaml",
    "library-source-unreachable": "доступный storybook-MCP или верные library.scanPaths конфига",
    "plan-incomplete": "ответы на блокирующие пункты контракта плана (daiver_plan_contract): {ids}",
    "plan-unsigned": "подпись плана человеком, прочитавшим ui-plan.md (согласие ui-plan-{track})",
    "no-verification-url": "адрес реализации, по которому проверять экран",
    "no-dev-command": "implementation.devCommand в track.yaml — команду запуска называет человек",
    "no-browser": "Chromium/Chrome на машине (CHROMIUM_EXECUTABLE_PATH или verify.browserPath конфига)",
    "spec-unapproved": "человек: утверждение фронт-спеки ui-spec.md (маркер ui-spec-approved-{track})",
}
APPLY_KEYS = ("url", "state", "sources")


class RunError(ValueError):
    """Вызов не годится (нет трека, не директива, прогон не открыт) — с причиной."""


def run_path(root: Path, track: str) -> Path:
    return TR.track_dir(Path(root), track) / RUN_FILE


def read(root: Path, track: str) -> Optional[Dict[str, Any]]:
    """Запись прогона трека или None (нет / не читается — читается как «прогона нет»: режим тогда из конфига)."""
    p = run_path(root, track)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def _write(root: Path, track: str, rec: Dict[str, Any]) -> None:
    from .verify import capture as CP
    p = run_path(root, track)
    p.parent.mkdir(parents=True, exist_ok=True)
    CP._atomic_write(p, (json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def mode_of(root: Path, track: Optional[str] = None) -> Tuple[str, str]:
    """(режим, источник): открытый прогон трека — его записанный режим; иначе `workflow.mode` конфига (прогон человека)."""
    rec = read(root, track) if track else None
    if rec and rec.get("open") and rec.get("mode") in DR.MODES:
        return rec["mode"], "run.json"
    return CFG.load(Path(root))["config"]["workflow"]["mode"], "workflow.mode"


def autonomous_open(root: Path) -> List[str]:
    """Треки с открытым автономным прогоном."""
    out: List[str] = []
    for name in TR.list_tracks(Path(root)):
        rec = read(root, name)
        if rec and rec.get("open") and rec.get("mode") == "autonomous":
            out.append(name)
    return out


def refuse_autonomous(root: Path, what: str) -> Optional[str]:
    """Текст отказа D3 для действия, которое автономный прогон не делает никогда; None — можно."""
    names = autonomous_open(root)
    if not names:
        return None
    return (f"{what} в автономном прогоне не делается никогда (INV-D3): открыт прогон {', '.join(names)} в режиме autonomous. Это решение "
            f"человека — останов с supply, а не догадка; закрой прогон daiver_run(track, finish: true, blocked: …) и скажи, что нужно")


def summary(rec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Короткий вид записи для daiver_status и daiver_verify_state."""
    if not rec:
        return None
    keys = ("n", "open", "mode", "modeSource", "startedBy", "startsAt", "until", "from", "blocked", "outcome")
    return {k: rec[k] for k in keys if rec.get(k) is not None}


def blocked_reason(reason: Any) -> Tuple[str, str]:
    """(основа причины, полная строка): `plan-incomplete (a, b)` → plan-incomplete. Неизвестная — RunError."""
    if not isinstance(reason, str) or not reason.strip():
        raise RunError("blocked: имя причины из списка — " + ", ".join(BLOCKED))
    base = reason.strip().split(" ", 1)[0]
    if base not in BLOCKED:
        raise RunError(f"blocked: «{reason}» — не из списка именованных остановов ({', '.join(BLOCKED)})")
    return base, reason.strip()


def supply_of(reason: str, track: str, ids: str = "") -> str:
    base = reason.split(" ", 1)[0]
    if base == "plan-incomplete" and not ids and "(" in reason:
        ids = reason[reason.index("(") + 1:reason.rindex(")")] if ")" in reason else ""
    return BLOCKED[base].format(track=track, ids=ids or "см. daiver_plan_contract")


# ── открытие ──────────────────────────────────────────────────────────────────

def _file_sha(root: Path, rel: str) -> Tuple[Optional[str], Optional[str]]:
    """sha256 требования, которое дала бы выгрузка `rel` (тот же slim, что у pull), или причина."""
    from .pixso import pull as PP
    from .pixso import slim as S
    try:
        doc = PP._read_source(Path(root) / rel)
        slim_doc, _rep = S.slim(doc)
    except Exception as e:  # noqa: BLE001 — любая причина «не выгрузка» одна для прогона: требования из этого файла нет
        return None, f"file={rel}: не читается как выгрузка Pixso ({str(e)[:120]})"
    return hashlib.sha256(S.dumps(slim_doc).encode("utf-8")).hexdigest(), None


def _conflicts_and_pulls(root: Path, manifest: Optional[dict], frames: Dict[str, Dict[str, Any]]) -> Tuple[List[str], List[str], List[str]]:
    """(конфликты, кадры, которым нужен pull, причины «нет требования») по ключам кадров строки против трека."""
    conflicts: List[str] = []
    pulls: List[str] = []
    missing: List[str] = []
    have = {f["name"]: f for f in (manifest or {}).get("frames") or []}
    for name, slot in sorted(frames.items()):
        f = have.get(name)
        url = slot.get("revision") or slot.get("frame")
        if f is None or not f.get("requirement"):
            if url is None and "file" in slot:
                missing.append(f"кадр «{name}»: file= без ссылки на кадр (frame=) — pull без ссылки ссылок на узлы не строит")
            elif url is not None or "file" in slot:
                pulls.append(name)
            continue
        if slot.get("frame") and f.get("frameUrl") != slot["frame"]:
            conflicts.append(f"кадр «{name}» трека смотрит на {f.get('frameUrl')}, а строка — на {slot['frame']}")
            continue
        if slot.get("revision") and f.get("frameUrl") != slot["revision"]:
            pulls.append(name)                        # ревизия ещё не вытянута
            continue
        if "file" in slot:
            sha, why = _file_sha(root, slot["file"])
            if why:
                missing.append(why)
            elif sha != (f.get("requirement") or {}).get("sha256"):
                if slot.get("revision"):
                    pulls.append(name)
                else:
                    conflicts.append(f"кадр «{name}»: выгрузка {slot['file']} — другое требование, чем записано в треке")
    for name, f in sorted(have.items()):
        url = (frames.get(name) or {}).get("frame")
        if url is None:
            continue
        other = next((x for x in have.values() if x is not f and x.get("frameUrl") == url), None)
        if other is not None:
            conflicts.append(f"ссылка {url} уже принадлежит кадру «{other['name']}», а строка даёт её кадру «{name}»")
    return conflicts, sorted(set(pulls)), missing


def _changes(keys: Dict[str, Any], frames: Dict[str, Dict[str, Any]], manifest: dict) -> Tuple[Dict[Optional[str], Dict[str, Any]], List[str]]:
    """{кадр | None (трек): {поле: значение}} для манифеста и кадры, которых ещё нет (pending)."""
    have = {f["name"]: f for f in manifest.get("frames") or []}
    out: Dict[Optional[str], Dict[str, Any]] = {}
    pending: List[str] = []
    if keys.get("url"):
        impl = dict(manifest.get("implementation") or {})
        if impl.get("url") != keys["url"]:
            impl["url"] = keys["url"]
            out.setdefault(None, {})["implementation"] = impl
    if keys.get("specs") and list(manifest.get("specs") or []) != keys["specs"]:
        out.setdefault(None, {})["specs"] = keys["specs"]
    for name, slot in sorted(frames.items()):
        ch: Dict[str, Any] = {}
        if slot.get("url"):
            ch["url"] = slot["url"]
        if slot.get("state"):
            ch.update(stateOf=slot["state"], role="state")
        if slot.get("sources"):
            ch["sources"] = slot["sources"]
        if not ch:
            continue
        f = have.get(name)
        if f is None:
            pending.append(name)
            continue
        ch = {k: v for k, v in ch.items() if f.get(k) != v}
        if ch:
            out[name] = ch
    if keys.get("scope"):
        for name, f in sorted(have.items()):
            src = (out.get(name) or {}).get("sources") or f.get("sources")
            if src and f.get("scope") != keys["scope"]:
                out.setdefault(name, {})["scope"] = keys["scope"]
    return out, sorted(pending)


def _apply(root: Path, manifest: dict, changes: Dict[Optional[str], Dict[str, Any]]) -> Tuple[dict, List[str], List[str]]:
    """Ключи строки → манифест (все разом или ничего). → (манифест, записанные поля, нарушения)."""
    m = manifest
    written: List[str] = []
    for name in sorted(changes, key=lambda x: (x is not None, x or "")):
        m, changed, problems = TR.apply_set(m, name, changes[name])
        if problems:
            return manifest, [], [f"{'трек' if name is None else 'кадр «' + name + '»'}: {p}" for p in problems]
        written += [f"{name}.{c}" if name else c for c in changed]
    return m, written, []


def _frames_view(root: Path, manifest: Optional[dict]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    from . import status as ST
    if not manifest or not manifest.get("frames"):
        return [], {}
    view, _w = ST.track_status(root, manifest["track"])
    stages = {v["name"]: v["stage"] for v in view["frames"]}
    out = [{"name": f["name"], "dir": f.get("dir") or ".", "stage": stages.get(f["name"], "empty"), "url": TR.implementation_url(manifest, f["name"])}
           for f in manifest["frames"]]
    return out, stages


def _contract(root: Path, track: str, resolver: Optional[Callable[[str], Optional[Path]]]) -> Dict[str, Any]:
    from . import plan_contract as PC
    try:
        return PC.evaluate(root, track, resolver, write=False).to_dict()
    except Exception as e:  # noqa: BLE001 — контракт не считается: блокирующий план, а не «готов»
        return {"ready": False, "signed": False, "blocked": "plan-incomplete (contract)", "blocks": [f"contract: {str(e)[:120]}"]}


def _policy(mode: str, cfg: Dict[str, Any], signed: bool, until: Optional[str]) -> Dict[str, Any]:
    wf = cfg["workflow"]
    auto = mode == "autonomous"
    return {"confirmPlan": "satisfied-by-signature" if signed else ("blocked: plan-unsigned" if auto else "pause"),
            "resolutions": ("medium — принят с пометкой unconfirmed, low — resolution: local (вопросов нет)" if auto
                            else "medium и low — подтверждает человек (confirm-resolutions)"),
            "verify": {"reportHtml": not auto, "decision": "triage" if auto else "human",
                       "stop": ["clean", "cap", "stall"] if auto else ["human"],
                       "iterationCap": (wf.get("autonomous") or {}).get("maxVerifyIterations") if auto else None,
                       "warnAfter": None if auto else wf.get("warnAfterVerifyIterations")},
            "until": until}


def _next(rec: Dict[str, Any]) -> str:
    if rec.get("blocked"):
        return (f"СТОП: blocked: {rec['blocked']} — нужно: {rec['supply']}. Не обходи и не угадывай: закрой прогон daiver_run(track, finish: true) "
                f"и назови причину и supply дословно; всё остаётся возобновимым")
    s = rec["startsAt"]
    tail = f"; прогон кончается после шага {rec['until']} (until=) — исход будет never-run, так и скажи" if rec.get("until") else ""
    if s == "init":
        return "проект не инициализирован: daiver_init (вопросы — человеку), затем daiver_run(track) снова"
    if s == "track":
        return (f"ui-design: вытяни кадры строки задачи ({', '.join(rec.get('pull') or [])}) — daiver_pixso_pull по ссылке и выгрузке; потом "
                f"daiver_run с той же строкой снова — он допишет ключи кадров, которых ещё не было (pending)" + tail)
    if s == "resolve":
        return "ui-design: требование есть, плана нет — нормализация, резолв, план и контракт" + tail
    if s == "build":
        if (rec.get("policy") or {}).get("confirmPlan") == "satisfied-by-signature":
            return "ui-build: план подписан — сборка по секциям" + tail
        return ("ui-design: план есть, но не подписан — подпись человека (sign-plan по контракту daiver_plan_contract), затем ui-build; без подписи "
                "хук ui-guard код не пустит" + tail)
    if s == "verify":
        return "ui-verify: собрано — итерации проверки" + tail
    return "трек чист: закрой прогон daiver_run(track, finish: true)"


def open_run(root: Any, track: Optional[str] = None, directive: Optional[str] = None,
             resolver: Optional[Callable[[str], Optional[Path]]] = None) -> Dict[str, Any]:
    root = Path(root)
    parsed: Optional[Dict[str, Any]] = None
    if directive is not None:
        parsed = DR.parse(directive)
        if not parsed["directive"]:
            raise RunError("строка без префикса forge-ui: (или daiver:) — это не директива; прогон человека открывается daiver_run(track)")
        if parsed["ok"]:
            if track is not None and track != parsed["keys"]["track"]:
                raise RunError(f"track={track} рядом с директивой трека {parsed['keys']['track']}: трек задаёт строка")
            try:
                parsed = DR.check_against(parsed, TR.load(root, parsed["keys"]["track"]))
            except TR.TrackError as e:
                parsed = dict(parsed, ok=False, faults=parsed["faults"] + [f"track.yaml трека не читается: {e}"])
        if not parsed["ok"]:
            return {"malformed": True, "faults": parsed["faults"], "warnings": parsed["warnings"], "written": False,
                    "nextStep": "СТОП: строка задачи malformed — назови каждое нарушение дословно; строку чинит человек в таск-файле, не ты; "
                                "ничего не записано"}
        track = parsed["keys"]["track"]
    if track is None:
        raise RunError("нужен track (прогон человека) или directive (строка задачи)")
    TR.check_track_name(track)
    loaded = CFG.load(root)
    cfg = loaded["config"]
    keys = parsed["keys"] if parsed else None
    started_by = "directive" if parsed else "human"
    if parsed:
        mode, source = (keys["mode"], "line") if keys.get("mode") else (cfg["workflow"]["embeddedMode"], "workflow.embeddedMode")
    else:
        mode, source = cfg["workflow"]["mode"], "workflow.mode"
    warnings = list(loaded["warnings"]) + (list(parsed["warnings"]) if parsed else [])
    prev = read(root, track)
    resumed = bool(prev and prev.get("open") and (
        (parsed and prev.get("startedBy") == "directive" and prev.get("directive") == parsed["line"])
        or (not parsed and prev.get("startedBy") == "human")))                  # тот же начавший и та же строка — тот же прогон
    closed_prev = None
    if prev and prev.get("open") and not resumed:
        why = "stale" if prev.get("startedBy") == "directive" and started_by == "human" else "superseded"
        closed_prev = _close(root, track, prev, None, why)
    n = (prev or {}).get("n", 0) if resumed else (prev or {}).get("n", 0) + 1
    rec: Dict[str, Any] = {"schema": SCHEMA, "track": track, "n": n, "open": True, "startedBy": started_by, "mode": mode, "modeSource": source,
                           "directive": parsed["line"] if parsed else None, "keys": keys,
                           "from": (keys or {}).get("from"), "until": (keys or {}).get("until")}
    if prev and not resumed and (closed_prev or not prev.get("open")):
        last = closed_prev or prev
        rec["previous"] = {k: last.get(k) for k in ("n", "outcome", "closedBy", "startedBy", "mode") if last.get(k) is not None}
    elif resumed and prev.get("previous"):
        rec["previous"] = prev["previous"]
    if not loaded["initialized"]:
        if mode != "autonomous":
            return {"track": track, "mode": mode, "modeSource": source, "startedBy": started_by, "startsAt": "init", "written": False,
                    "warnings": warnings, "nextStep": _next({"startsAt": "init"})}
        return _finish_open(root, track, dict(rec, startsAt="init", blocked="not-initialized", supply=supply_of("not-initialized", track),
                                              policy=_policy(mode, cfg, False, rec["until"]), frames=[]), resumed, closed_prev, warnings)
    try:
        manifest = TR.load(root, track)
    except TR.TrackError as e:
        raise RunError(str(e))
    frames_keys = DR.frames_of(keys, manifest) if keys else {}
    applied: List[str] = []
    pending: List[str] = []
    conflicts, pulls, missing = _conflicts_and_pulls(root, manifest, frames_keys)
    if not conflicts and manifest is not None and keys:
        changes, pending = _changes(keys, frames_keys, manifest)
        new, applied, problems = _apply(root, manifest, changes)
        if problems:
            return {"malformed": True, "faults": problems, "warnings": warnings, "written": False,
                    "nextStep": "СТОП: ключи строки задачи не ложатся в манифест — назови нарушения дословно; строку чинит человек; ничего не записано"}
        if applied:
            TR.save(root, new)
            manifest = new
    elif manifest is None and keys:
        pending = sorted(n for n, s in frames_keys.items() if any(k in s for k in APPLY_KEYS)) + (["track"] if keys.get("url") or keys.get("specs") else [])
    frames, stages = _frames_view(root, manifest)
    rec.update(frames=frames, pull=pulls, pending=pending)
    blocked: Optional[str] = None
    if conflicts:
        blocked = "track-conflict"
        rec["conflicts"] = conflicts
        starts = "track"
    elif pulls:
        starts = "track"
    elif not stages:
        blocked, starts = "no-requirement", "track"
        if missing:
            rec["why"] = missing
    else:
        low = min((LADDER.index(s) for s in stages.values()), default=0)
        if low == 0:
            blocked, starts = "no-requirement", "track"
            rec["why"] = missing or [f"у кадров {', '.join(n for n, s in stages.items() if s == 'empty')} нет требования"]
        else:
            starts = STAGE_START[LADDER[low]]
    signed = False
    contract = None
    auto = mode == "autonomous"
    if not blocked and (rec["from"] == "plan" or starts in ("build", "verify", "done")):
        contract = _contract(root, track, resolver)
        signed = bool(contract.get("signed"))
        if rec["from"] == "plan" or (auto and starts == "build"):
            if not contract.get("ready"):
                blocked = contract.get("blocked") or "plan-incomplete (" + ", ".join(contract.get("blocks") or []) + ")"
            elif not signed:
                blocked = "plan-unsigned"
    rec.update(startsAt=starts, policy=_policy(mode, cfg, signed, rec["until"]))
    if blocked:
        base, full = blocked_reason(blocked)
        rec.update(blocked=full, supply=supply_of(full, track))
    return _finish_open(root, track, rec, resumed, closed_prev, warnings, applied)


def _finish_open(root: Path, track: str, rec: Dict[str, Any], resumed: bool, closed_prev: Optional[Dict[str, Any]], warnings: List[str],
                 applied: Optional[List[str]] = None) -> Dict[str, Any]:
    old = read(root, track)
    body = {k: v for k, v in rec.items()}
    written = old != body
    if written:
        _write(root, track, body)
    out = dict(body, written=written, resumed=resumed, applied=applied or [], warnings=warnings, nextStep=_next(body))
    if closed_prev:
        out["closedPrevious"] = {k: closed_prev.get(k) for k in ("n", "outcome", "closedBy")}
    return out


# ── закрытие ──────────────────────────────────────────────────────────────────

def outcome_of(root: Path, track: str, rec: Dict[str, Any], blocked: Optional[str]) -> Dict[str, Any]:
    """Исход прогона из записанной истории verify кадров (никогда из памяти)."""
    from .verify import verify_report as VRP
    if blocked:
        return {"outcome": f"blocked:{blocked}", "supply": supply_of(blocked, track)}
    try:
        manifest = TR.load(root, track)
    except TR.TrackError:
        manifest = None
    frames = (manifest or {}).get("frames") or []
    hist = {f["name"]: VRP._state(TR.frame_paths(root, manifest, f["name"])["verifyState"]) for f in frames} if frames else {}
    hist = {k: (v or {}).get("history") or [] for k, v in hist.items()}
    iterations = sum(len(h) for h in hist.values())
    report = TR.track_dir(root, track) / "verify-report.md"
    rep = report.relative_to(root).as_posix() if report.is_file() else None
    if rec.get("until") in ("resolve", "build"):
        return {"outcome": "never-run", "why": f"прогон до шага {rec['until']} (until=): экран не проверялся", "iterations": iterations, "report": rep}
    states = [VRP.frame_outcome(h) for h in hist.values()]
    if not states or "not-checked" in states:
        unchecked = [k for k, h in hist.items() if not h] or ["кадров нет"]
        return {"outcome": "never-run", "why": f"не проверен: {', '.join(unchecked)}", "iterations": iterations, "report": rep}
    if all(s == "clean" for s in states):
        return {"outcome": "clean", "iterations": iterations, "report": rep}
    remaining = sum(len(h[-1].get("blocking") or []) for h in hist.values() if h and not h[-1]["clean"])
    return {"outcome": "defects-remaining", "remaining": remaining, "iterations": iterations, "report": rep}


def _note(track: str, got: Dict[str, Any]) -> str:
    o = got["outcome"]
    rep = f", отчёт {got['report']}" if got.get("report") else ""
    if o == "clean":
        return f"{track}: clean — итераций проверки {got['iterations']}{rep}"
    if o == "defects-remaining":
        return f"{track}: defects-remaining — блокирующих осталось {got['remaining']}, итераций {got['iterations']}{rep}"
    if o.startswith("blocked:"):
        return f"{track}: {o} — нужно: {got['supply']}"
    return f"{track}: never-run — {got.get('why') or 'экран не проверен'}"


def _close(root: Path, track: str, rec: Dict[str, Any], blocked: Optional[str], closed_by: str) -> Dict[str, Any]:
    got = outcome_of(root, track, rec, blocked)
    done = dict(rec, open=False, closedBy=closed_by, note=_note(track, got), **got)
    _write(root, track, done)
    return done


def finish_run(root: Any, track: str, blocked: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root)
    TR.check_track_name(track)
    rec = read(root, track)
    if rec is None:
        raise RunError(f"прогона трека {track} нет: закрывать нечего (его открывает daiver_run)")
    if not rec.get("open"):
        return {"track": track, "alreadyClosed": True, "outcome": rec.get("outcome"), "note": rec.get("note"),
                "nextStep": "прогон уже закрыт — исход записан, повторно не считается"}
    reason = None
    if blocked is not None:
        reason = blocked_reason(blocked)[1]
    elif rec.get("blocked"):
        reason = rec["blocked"]
    done = _close(root, track, rec, reason, "finish")
    out = {k: done.get(k) for k in ("track", "n", "outcome", "remaining", "iterations", "report", "supply", "why", "note") if done.get(k) is not None}
    out["checked"] = done["outcome"] == "clean"
    out["nextStep"] = ("чекбокс задачи — " + ("отметить" if out["checked"] else "НЕ отмечать") + f", заметка: «{done['note']}»; "
                       f"другие задачи таск-листа продолжаются")
    return out
