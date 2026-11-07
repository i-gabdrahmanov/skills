"""decisions.py — файл решений человека `verify-decisions.json` (спека §14.11 «Решения-файл инварианты»; задача 4.8; INV-C33, C34, QG-P6).

Человек отвечает на дефекты в отчёте (5.3–5.4) и кладёт файл в папку трека (богаче: заметка на дефект, один файл отвечает сразу всем кадрам)
или в папку кадра. Файл кладёт ЧЕЛОВЕК: модель его не пишет (хук `state-write-guard` закрывает путь) — иначе она отвечала бы сама себе.

Формат (schema 1): `{"schema": 1, "decisions": [{"id", "verdict": fix | skip | false_positive, "note"?, "reviewer"?, "decidedAt"?, "frame"?,
"snapshot"?: {"rows": [{МАКЕТ, РЕАЛИЗАЦИЯ}]}}], "marks": [{"id": "user-<итерация>-<n>", "note", "side": impl | design, "box": {x, y, w, h}, "frame"?, "iteration"?,
"nodeId"?, "reviewer"?, "decidedAt"?}]}`. Инварианты:
  • чужая версия схемы — отказ (`DecisionsError`), а не «прочитаю как смогу»;
  • решение ЗАМОРАЖИВАЕТ снимок дефекта (`snapshot.rows`): значения, уехавшие из-под него, — REOPENED с заметкой; переформулировка
    сообщения сама дефект не переоткрывает (сравниваются значения, не текст);
  • ответов три: fix (заметка дословно идёт в чинимое), skip → declined (не переспрашивать; дефект остаётся открытым), false_positive → noise
    с именем решившего (не блокирует);
  • неизвестные id названы (`unknown`), а не молча проигнорированы; негодная запись названа в `problems`;
  • reviewer переносится; пустая заметка не переносится;
  • файл одного кадра лежит в его папке; запись с `frame` другого кадра — «отвечено в другом месте» (`elsewhere`), здесь не применяется;
  • на один id позже `decidedAt` выигрывает (равные — файл кадра сильнее файла трека).
Ручные метки (`marks`, режим «Пометить проблему» в отчёте 5.4) — НАБЛЮДЕНИЯ человека о том, чего гейты не увидели: `reported`. Каждая метка становится пунктом итерации
`source: review`, `zone: vision`, `severity: major`, `problem` = заметка ДОСЛОВНО (вся постановка), `pin` = её рамка при `side: impl`; запись остаётся в файле, пока человек не
удалит её в позднем отчёте, поэтому метка едет по итерациям со старым id. Метка, указывающая на несуществующую картинку (нет `actual.png` / картинки макета той итерации),
отклоняется поимённо. Решение с `frame` другого кадра — `answeredElsewhere`: чинится в этом проходе, а записывается следующей итерацией своего кадра.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import track as TR

SCHEMA = 1
FILE = "verify-decisions.json"
VERDICTS = ("fix", "skip", "false_positive")
SIDES = ("impl", "design")


class DecisionsError(ValueError):
    """Файл решений читать нельзя: чужая версия схемы, не JSON, не объект."""


def _read(path: Path, label: str) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise DecisionsError(f"{label}: не читается как JSON ({e})")
    if not isinstance(data, dict):
        raise DecisionsError(f"{label}: ожидается JSON-объект {{schema, decisions}}")
    if data.get("schema") != SCHEMA:
        raise DecisionsError(f"{label}: версия схемы {data.get('schema')!r}, а умею {SCHEMA} — файл чужой, решения не применены")
    return data


def paths(root: Path, manifest: dict, frame: Optional[str]) -> List[Tuple[str, Path]]:
    """[(метка, путь)]: файл трека, затем файл кадра (он сильнее при равных decidedAt)."""
    fp = TR.frame_paths(root, manifest, frame)
    track_file = TR.track_dir(root, manifest["track"]) / FILE
    frame_file = fp["dir"] / FILE
    out = [("трека", track_file)]
    if frame_file != track_file:
        out.append(("кадра", frame_file))
    return out


def _mark_problem(m: Any) -> Optional[str]:
    if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not m["id"].startswith("user-"):
        return "id метки должен быть user-<итерация>-<n>"
    if not isinstance(m.get("note"), str) or not m["note"].strip():
        return f"{m['id']}: пустая заметка — метка без постановки не принимается"
    if m.get("side") not in SIDES:
        return f"{m['id']}: side — {' | '.join(SIDES)}"
    b = m.get("box")
    if not (isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) and not isinstance(b.get(k), bool) for k in ("x", "y", "w", "h")) and b["w"] > 0 and b["h"] > 0
            and b["x"] >= 0 and b["y"] >= 0):
        return f"{m['id']}: box — числа {{x, y, w, h}}, w и h > 0, x и y ≥ 0"
    return None


def load(root: Path, manifest: dict, frame: Optional[str]) -> Dict[str, Any]:
    """{decisions, marks, problems, elsewhere, foreign: {decisions, marks}, sources} — файлы трека и кадра, слитые по правилу «позже выигрывает»; `foreign` — записи
    других кадров целиком (экспорт отчёта отдаёт их обратно: файл один на трек)."""
    fname = TR.get_frame(manifest, frame)["name"]
    merged: Dict[str, Tuple[str, int, Dict[str, Any]]] = {}
    marks: Dict[str, Tuple[str, int, Dict[str, Any]]] = {}
    problems: List[str] = []
    elsewhere: List[str] = []
    foreign: Dict[str, List[Dict[str, Any]]] = {"decisions": [], "marks": []}
    sources: List[str] = []
    for rank, (label, path) in enumerate(paths(root, manifest, frame)):
        data = _read(path, f"{FILE} {label}")
        if data is None:
            continue
        sources.append(label)
        items = data.get("decisions")
        if not isinstance(items, list):
            problems.append(f"{FILE} {label}: нет списка decisions[]")
            items = []
        for k, e in enumerate(items):
            if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e["id"]:
                problems.append(f"{FILE} {label}: запись {k + 1} без id")
                continue
            if e.get("verdict") not in VERDICTS:
                problems.append(f"{FILE} {label}: {e['id']} — verdict {e.get('verdict')!r}, допустимо {'|'.join(VERDICTS)}")
                continue
            if e.get("frame") not in (None, fname):
                elsewhere.append(e["id"])
                foreign["decisions"].append(e)
                continue
            stamp = e.get("decidedAt") if isinstance(e.get("decidedAt"), str) else ""
            prev = merged.get(e["id"])
            if prev is None or (stamp, rank) >= (prev[0], prev[1]):
                merged[e["id"]] = (stamp, rank, e)
        raw_marks = data.get("marks")
        for k, m in enumerate(raw_marks if isinstance(raw_marks, list) else []):
            why = _mark_problem(m)
            if why:
                problems.append(f"{FILE} {label}: метка {k + 1}: {why}")
                continue
            if m.get("frame") not in (None, fname):
                foreign["marks"].append(m)
                continue
            stamp = m.get("decidedAt") if isinstance(m.get("decidedAt"), str) else ""
            prev = marks.get(m["id"])
            if prev is None or (stamp, rank) >= (prev[0], prev[1]):
                marks[m["id"]] = (stamp, rank, m)
    return {"decisions": {k: v[2] for k, v in sorted(merged.items())}, "marks": {k: v[2] for k, v in sorted(marks.items())}, "problems": problems,
            "elsewhere": sorted(set(elsewhere)), "foreign": foreign, "sources": sources}


def reported(loaded: Dict[str, Any], verify_dir: Path, iteration_dir: Path, design_image: Optional[Path], existing: Optional[set] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Ручные метки → пункты итерации (`source: review`, `zone: vision`, `severity: major`, `problem` = заметка дословно, `pin` = рамка при `side: impl`) и поимённые отказы.
    Метка на несуществующую картинку отклоняется; id, уже занятый другим пунктом, — отказ (метка не затирает дефект)."""
    out: List[Dict[str, Any]] = []
    rejected: List[str] = []
    taken = set(existing or ())
    for mid, m in loaded["marks"].items():
        folder = verify_dir / m["iteration"] if isinstance(m.get("iteration"), str) and m["iteration"] else iteration_dir
        if m["side"] == "impl" and not (folder / "actual.png").is_file():
            rejected.append(f"метка {mid} указывает на картинку, которой нет: {folder.name}/actual.png")
            continue
        if m["side"] == "design" and not (design_image is not None and design_image.is_file()) and not (folder / "reference.png").is_file():
            rejected.append(f"метка {mid} указывает на картинку макета, которой нет у кадра")
            continue
        if mid in taken:
            rejected.append(f"метка {mid}: id занят другим пунктом итерации")
            continue
        taken.add(mid)
        note = m["note"]
        d: Dict[str, Any] = {"id": mid, "code": "VISION", "layer": "pixel", "severity": "major", "zone": "vision", "source": "review", "reported": True,
                             "element": " ".join(note.split())[:60], "message": note, "problem": note, "side": m["side"]}
        if m["side"] == "impl":
            d["pin"] = dict(m["box"])
            if isinstance(m.get("nodeId"), str):
                d["impl"] = m["nodeId"]
        else:
            d["designPin"] = dict(m["box"])
        if isinstance(m.get("reviewer"), str) and m["reviewer"].strip():
            d["reviewer"] = m["reviewer"].strip()
        out.append(d)
    return out, rejected


def rows_of(defect: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Значения дефекта в канонической форме для снимка: [{МАКЕТ, РЕАЛИЗАЦИЯ}] без счётчиков."""
    return [{"МАКЕТ": r.get("МАКЕТ"), "РЕАЛИЗАЦИЯ": r.get("РЕАЛИЗАЦИЯ")} for r in defect.get("rows") or [] if isinstance(r, dict)]


def snapshot_of(defect: Dict[str, Any]) -> Dict[str, Any]:
    """Снимок дефекта, который отчёт (5.3) кладёт в решение: по нему видно, что человек решал именно про ЭТИ значения."""
    return {"rows": rows_of(defect)}


def apply(defects: List[Dict[str, Any]], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """Накладывает решения на дефекты на месте; возвращает {unknown, reopened, declined, noise, fix, notes}."""
    by_id = {d["id"]: d for d in defects}
    res: Dict[str, List[str]] = {"unknown": [], "reopened": [], "declined": [], "noise": [], "fix": []}
    for did, e in loaded["decisions"].items():
        d = by_id.get(did)
        if d is None:
            res["unknown"].append(did)
            continue
        info: Dict[str, Any] = {"verdict": e["verdict"]}
        if isinstance(e.get("reviewer"), str) and e["reviewer"].strip():
            info["reviewer"] = e["reviewer"].strip()
        if isinstance(e.get("note"), str) and e["note"].strip():
            info["note"] = e["note"]
        snap = e.get("snapshot")
        if isinstance(snap, dict) and snap.get("rows") != rows_of(d):
            info["status"] = "reopened"
            info["reopenedNote"] = "значения уехали из-под решения: макет и реализация теперь расходятся иначе, чем когда человек отвечал"
            d["decision"] = info
            res["reopened"].append(did)
            continue
        d["decision"] = info
        if e["verdict"] == "skip":
            d["declined"] = True
            res["declined"].append(did)
        elif e["verdict"] == "false_positive":
            d["noise"] = True
            res["noise"].append(did)
        else:
            res["fix"].append(did)
    return res
