"""verify_report.py — `verify-report.md`: ОДИН файл на трек, итог цикла проверки (спека §14.13, §14.12; задача 4.8; INV-C21, C24).

Пишет движок и только из `verify-state.json` кадров (модель текст не сочиняет: отчёт не может разойтись с памятью цикла) — при каждой записи
итерации и решения. Детерминирован: ни времени, ни случайности; ключи frontmatter в фиксированном порядке.

Frontmatter: `track`, `outcome` (clean — все кадры чистые; defects-remaining — иначе, в том числе когда есть непроверенные кадры), `iterations` (всего
по кадрам), `defectsRemaining` (блокирующие в последней записи каждого кадра), `mode`, `frames` — по кадру {iterations, outcome}, когда кадров больше
одного. Тело — секция `## frame: <имя>` на кадр: по итерациям статусы гейтов, matchRate / fidelity, diff %, вердикт обзора, фиксы и отказы, решение;
итог кадра — не исполнявшиеся гейты и почему, ВСЁ нерешённое из последней записи (включая minor, noise, vision — с пометками), кто закончил цикл и
на какой итерации. Непроверенные кадры названы, а не спрятаны.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config as CFG
from .. import resolution_report as RR
from .. import track as TR
from . import capture as CP

FILE = "verify-report.md"
ACCEPT_PREFIX = "ui-accept"
GATE_NAMES = {"compare": "сравнение", "code": "код-гейт", "visualDiff": "пиксельный дифф", "review": "визуальный обзор", "e2e": "e2e"}


def report_path(root: Path, track: str) -> Path:
    return TR.track_dir(root, track) / FILE


def _state(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("history"), list) else None


def accepted(root: Path, track: str) -> bool:
    """Есть ли согласие человека `ui-accept-<KEY>` на остановку с открытыми дефектами (с цитатой — это проверяет record_approval)."""
    FE, _P = RR._hooks()
    return FE.approval(Path(root), f"{ACCEPT_PREFIX}-{track}") is not None


def frame_outcome(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "not-checked"
    return "clean" if history[-1]["clean"] else "defects-remaining"


def finished_by(h: Dict[str, Any], consent: bool) -> str:
    d = h.get("decision")
    if d == "clean":
        return f"пользователь принял результат как чистый (итерация {h['iteration']})"
    if d == "stop":
        return (f"пользователь остановил цикл на итерации {h['iteration']}" + (" (согласие ui-accept записано)" if consent else " — согласия ui-accept нет"))
    if h["clean"]:
        return f"последняя итерация {h['iteration']} чистая; решение человека ещё не записано"
    return f"цикл не закончен: итерация {h['iteration']} с блокирующими дефектами, решение человека не записано"


def _gates(h: Dict[str, Any]) -> str:
    parts = []
    for n in ("compare", "code", "visualDiff", "review", "e2e"):
        g = h["gates"][n]
        parts.append(f"{GATE_NAMES[n]}: {g['status']}")
    return " · ".join(parts)


def _defect_line(d: Dict[str, Any]) -> str:
    marks = [m for m, on in (("noise", d.get("noise")), ("declined", d.get("declined")), ("vision", d.get("zone") == "vision"),
                             ("pre-existing", d.get("standing") == "pre-existing")) if on]
    where = f" — {d['location']['file']}:{d['location'].get('line')}" if isinstance(d.get("location"), dict) and d["location"].get("file") else ""
    tail = f" _[{', '.join(marks)}]_" if marks else ""
    name = d.get("element") or d.get("prop") or ""
    return f"- `{d['id']}` **{d.get('severity', '?')}** {d.get('code', '')} · {name}: {d.get('message', '')}{where}{tail}"


def render(root: Path, manifest: dict) -> str:
    track = manifest["track"]
    cfg = CFG.load(root)["config"]
    consent = accepted(root, track)
    frames = []
    for f in manifest.get("frames") or []:
        st = _state(TR.frame_paths(root, manifest, f["name"])["verifyState"])
        frames.append((f, (st or {}).get("history") or []))
    outcomes = {f["name"]: frame_outcome(h) for f, h in frames}
    total = sum(len(h) for _f, h in frames)
    remaining = sum(len(h[-1].get("blocking") or []) for _f, h in frames if h)
    outcome = "clean" if frames and all(o == "clean" for o in outcomes.values()) else "defects-remaining"
    lines = ["---", f"track: {track}", f"outcome: {outcome}", f"iterations: {total}", f"defectsRemaining: {remaining}",
             f"mode: {cfg['workflow'].get('mode', 'interactive')}"]
    if len(frames) > 1:
        lines.append("frames:")
        for f, h in frames:
            lines.append(f"  {f['name']}: {{iterations: {len(h)}, outcome: {outcomes[f['name']]}}}")
    lines += ["---", "", f"# Проверка {track}", ""]
    unchecked = [f["name"] for f, h in frames if not h]
    if unchecked:
        lines += [f"**Не проверялись:** {', '.join(unchecked)}.", ""]
    for f, h in frames:
        lines += [f"## frame: {f['name']}", ""]
        if not h:
            lines += ["Кадр не проверялся: ни одной записанной итерации.", ""]
            continue
        for rec in h:
            lines.append(f"### Итерация {rec['iteration']}")
            lines.append(f"- гейты — {_gates(rec)}")
            nums = []
            if rec.get("matchRate") is not None:
                nums.append(f"matchRate {rec['matchRate']}")
            if rec.get("fidelity") is not None:
                nums.append(f"fidelity {rec['fidelity']}")
            if rec.get("diffPercent") is not None:
                nums.append(f"diff {rec['diffPercent']}%")
            if nums:
                lines.append("- " + " · ".join(nums))
            if rec.get("reviewSkipped"):
                lines.append(f"- обзор пропущен: {rec['reviewSkipped']}")
            elif rec["gates"]["review"]["status"] != "skipped" and rec.get("reviewReport") is not None:
                lines.append("- вердикт обзора записан (reviewReport)")
            prog = rec.get("progress") or {}
            lines.append(f"- блокирующих: {len(rec.get('blocking') or [])}" + (f" (было {prog['previous']})" if prog.get("previous") is not None else "")
                         + ("; **застой**" if prog.get("stalled") else "") + f"; чисто: {'да' if rec['clean'] else 'нет'}")
            for label, key in (("исправлено", "fixed"), ("отклонено", "declined")):
                if rec.get(key):
                    lines.append(f"- {label}: {', '.join(rec[key])}")
            if rec.get("decision"):
                lines.append(f"- решение: {rec['decision']}")
            html = Path(root) / rec["dir"] / "report.html"
            if html.is_file():
                lines.append(f"- отчёт: [report.html]({rec['dir']}/report.html)")
            lines.append("")
        last = h[-1]
        lines += [f"### Итог кадра {f['name']}", ""]
        not_run = [f"{GATE_NAMES[n]} — {last['gates'][n]['status']}" + (f": {last['gates'][n]['reason']}" if last["gates"][n].get("reason") else "")
                   for n in GATE_NAMES if last["gates"][n]["status"] in ("skipped", "degraded")]      # порядок гейтов фиксирован, файл состояния — по алфавиту
        lines.append("- не исполнялись или исполнены не полностью: " + ("; ".join(not_run) if not_run else "нет"))
        if last.get("unverified"):
            lines.append(f"- {last['unverified']}")
        lines.append(f"- кто закончил цикл: {finished_by(last, consent)}")
        open_ = last.get("defects") or []
        lines += ["", "Нерешённое (последняя запись, включая minor, noise и vision):" if open_ else "Нерешённого нет.", ""]
        lines += [_defect_line(d) for d in open_]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write(root: Path, manifest: dict) -> Path:
    path = report_path(Path(root), manifest["track"])
    path.parent.mkdir(parents=True, exist_ok=True)
    CP._atomic_write(path, render(Path(root), manifest).encode("utf-8"))
    return path
