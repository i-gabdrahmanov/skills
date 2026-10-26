"""decisions.py — файл решений человека `verify-decisions.json` (спека §14.11 «Решения-файл инварианты»; задача 4.8; INV-C33, C34, QG-P6).

Человек отвечает на дефекты в отчёте (5.3–5.4) и кладёт файл в папку трека (богаче: заметка на дефект, один файл отвечает сразу всем кадрам)
или в папку кадра. Файл кладёт ЧЕЛОВЕК: модель его не пишет (хук `state-write-guard` закрывает путь) — иначе она отвечала бы сама себе.

Формат (schema 1): `{"schema": 1, "decisions": [{"id", "verdict": fix | skip | false_positive, "note"?, "reviewer"?, "decidedAt"?, "frame"?,
"snapshot"?: {"rows": [{МАКЕТ, РЕАЛИЗАЦИЯ}]}}]}`. Инварианты:
  • чужая версия схемы — отказ (`DecisionsError`), а не «прочитаю как смогу»;
  • решение ЗАМОРАЖИВАЕТ снимок дефекта (`snapshot.rows`): значения, уехавшие из-под него, — REOPENED с заметкой; переформулировка
    сообщения сама дефект не переоткрывает (сравниваются значения, не текст);
  • ответов три: fix (заметка дословно идёт в чинимое), skip → declined (не переспрашивать; дефект остаётся открытым), false_positive → noise
    с именем решившего (не блокирует);
  • неизвестные id названы (`unknown`), а не молча проигнорированы; негодная запись названа в `problems`;
  • reviewer переносится; пустая заметка не переносится;
  • файл одного кадра лежит в его папке; запись с `frame` другого кадра — «отвечено в другом месте» (`elsewhere`), здесь не применяется;
  • на один id позже `decidedAt` выигрывает (равные — файл кадра сильнее файла трека).
Ручные метки пользователя в отчёте (`marks`) — задача 5.4.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import track as TR

SCHEMA = 1
FILE = "verify-decisions.json"
VERDICTS = ("fix", "skip", "false_positive")


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


def load(root: Path, manifest: dict, frame: Optional[str]) -> Dict[str, Any]:
    """{decisions: {id: запись}, problems: [текст], elsewhere: [id], sources: [метка]} — файлы трека и кадра, слитые по правилу «позже выигрывает»."""
    fname = TR.get_frame(manifest, frame)["name"]
    merged: Dict[str, Tuple[str, int, Dict[str, Any]]] = {}
    problems: List[str] = []
    elsewhere: List[str] = []
    sources: List[str] = []
    for rank, (label, path) in enumerate(paths(root, manifest, frame)):
        data = _read(path, f"{FILE} {label}")
        if data is None:
            continue
        sources.append(label)
        items = data.get("decisions")
        if not isinstance(items, list):
            problems.append(f"{FILE} {label}: нет списка decisions[]")
            continue
        for k, e in enumerate(items):
            if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e["id"]:
                problems.append(f"{FILE} {label}: запись {k + 1} без id")
                continue
            if e.get("verdict") not in VERDICTS:
                problems.append(f"{FILE} {label}: {e['id']} — verdict {e.get('verdict')!r}, допустимо {'|'.join(VERDICTS)}")
                continue
            if e.get("frame") not in (None, fname):
                elsewhere.append(e["id"])
                continue
            stamp = e.get("decidedAt") if isinstance(e.get("decidedAt"), str) else ""
            prev = merged.get(e["id"])
            if prev is None or (stamp, rank) >= (prev[0], prev[1]):
                merged[e["id"]] = (stamp, rank, e)
    return {"decisions": {k: v[2] for k, v in sorted(merged.items())}, "problems": problems, "elsewhere": sorted(set(elsewhere)),
            "sources": sources}


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
