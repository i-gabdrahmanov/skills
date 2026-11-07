"""visual_diff.py — `daiver_visual_diff`: пиксельный дифф «макет | реализация» (спека §14.6; задача 5.1; INV-F17, QG-N15).

Дифф — СИГНАЛ, не вердикт: он локализует зоны расхождения для человека и визуального обзора и НИКОГДА сам не производит дефект и не ставит гейт в fail.
Ненулевой процент при пустом списке дефектов (сглаживание, плейсхолдеры эталона, рамки в пределах допуска) — нормальный исход; порог
`verify.visualDiffThreshold` — подсказка (`aboveHint`), а не блокирующий гейт. Зоны, которые уже объяснил гейт compare (пин дефекта накрывает зону), не
комментируются повторно (`explainedBy`).

Что с чем: `against: goal` — картинка дизайна кадра (`visual-requirements.png|jpg`, обрезка по `frameBox` и подложка на белое; JPEG без альфы иначе
давал чёрные поля); `against: reference` — эталонный рендер из канона (`daiver_render_reference`). Goal правдивее reference: без `against` берётся goal, если он есть, и
в ответе сказано, что использовано. Считает вкладка Chromium (canvas, `assets/pixeldiff.js` — порт pixelmatch): ни npm, ни PNG-декодера на Python.
Пишет в папку итерации `diff.png`, `diff-strip.png` (полоса «макет | реализация | дифф» с промежутками), `visual-diff.json` (числа и зоны для отчёта).

Не проверено данными (боевых экранов нет): порог 0.1 pixelmatch на живых страницах (сглаживание шрифтов разных рендереров), поведение на очень больших кадрах
(картинки уходят в страницу строкой base64, предел сообщения клиента 128 МБ), точность совмещения goal и actual при `clipSelector` (зоны объясняются по смещению clip).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config as CFG
from .. import image as IMG
from .. import track as TR
from ..browser import cdp as CD
from . import capture as CP

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "assets" / "pixeldiff.js"
AGAINST = ("goal", "reference")
THRESHOLD = 0.1                         # порог цвета pixelmatch (YIQ); не путать с visualDiffThreshold — подсказкой по проценту
EVAL_TIMEOUT = 120.0
EXPLAIN_SHARE = 0.5                     # доля зоны, накрытая пином дефекта, начиная с которой зона объяснена
FILES = ("diff.png", "diff-strip.png", "visual-diff.json")
_script: List[str] = []


class VisualDiffError(Exception):
    """Дифф невозможен или отказан: сообщение адресовано агенту и говорит, что делать."""


def script() -> str:
    if not _script:
        _script.append(SCRIPT_PATH.read_text(encoding="utf-8"))
    return _script[0]


def _read_png(path: Path, label: str) -> bytes:
    try:
        data = path.read_bytes()
    except OSError:
        raise VisualDiffError(f"{label}: файла нет ({path.name})")
    info = IMG.sniff_bytes(data[:1 << 20])
    if info is None:
        raise VisualDiffError(f"{label}: {path.name} — не PNG и не JPEG")
    return data


def _mime(data: bytes) -> str:
    return "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"


def pick_target(paths: Dict[str, Path], iteration_dir: Path, against: Optional[str], has_goal: bool) -> str:
    """Что берётся «макетом»: goal правдивее reference — без явного `against` берётся он, если есть."""
    if against is not None and against not in AGAINST:
        raise VisualDiffError(f"against: {' | '.join(AGAINST)}")
    has_ref = (iteration_dir / "reference.png").is_file()
    if against == "goal" and not has_goal:
        raise VisualDiffError("against: goal — у кадра нет картинки дизайна (visual-requirements.png|jpg): положи её в папку кадра или бери reference")
    if against == "reference" and not has_ref:
        raise VisualDiffError("against: reference — в папке итерации нет reference.png: сначала daiver_render_reference")
    if against is None:
        if has_goal:
            return "goal"
        if has_ref:
            return "reference"
        raise VisualDiffError("сравнивать не с чем: нет ни картинки дизайна кадра, ни reference.png — сначала daiver_render_reference")
    return against


def explain(zones: List[Dict[str, Any]], defects: List[Dict[str, Any]], origin: Tuple[float, float]) -> None:
    """Размечает зоны на месте: `explainedBy` — id дефектов compare, чей пин накрывает не меньше EXPLAIN_SHARE площади зоны.
    Зона в координатах картинки диффа, пин — в координатах страницы: сдвиг `origin` — левый верхний угол clip захвата."""
    for z in zones:
        zx, zy = z["x"] + origin[0], z["y"] + origin[1]
        area = float(z["w"] * z["h"]) or 1.0
        hit = []
        for d in defects:
            b = d.get("pin")
            if not isinstance(b, dict) or not all(isinstance(b.get(k), (int, float)) for k in ("x", "y", "w", "h")):
                continue
            w = min(zx + z["w"], b["x"] + b["w"]) - max(zx, b["x"])
            h = min(zy + z["h"], b["y"] + b["h"]) - max(zy, b["y"])
            if w > 0 and h > 0 and (w * h) / area >= EXPLAIN_SHARE:
                hit.append(d["id"])
        if hit:
            z["explainedBy"] = sorted(hit)[:5]


def _json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def run(root: Any, track: str, frame: Optional[str], iteration_dir: Path, against: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise VisualDiffError(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    f = TR.get_frame(manifest, frame)
    paths = TR.frame_paths(root, manifest, f["name"])
    if not CP.iteration_dir_ok(paths, iteration_dir):
        raise VisualDiffError(f"iterationDir: папка итерации лежит под «verify» (или «baseline») кадра, а не «{iteration_dir}»")
    actual = _read_png(iteration_dir / "actual.png", "реализация") if (iteration_dir / "actual.png").is_file() else None
    if actual is None:
        raise VisualDiffError("в папке итерации нет actual.png: сначала daiver_screenshot в эту папку")
    goal_path = paths["raster"]
    has_goal = bool(f.get("raster")) and goal_path.is_file()
    use = pick_target(paths, iteration_dir, against, has_goal)
    crop = None
    if use == "goal":
        left = _read_png(goal_path, "картинка дизайна")
        box = f.get("frameBox")
        if isinstance(box, dict):
            crop = {k: box[k] for k in ("x", "y", "w", "h")}
            info = IMG.sniff_bytes(left[:1 << 20]) or {}
            if crop["x"] + crop["w"] > info.get("width", 0) or crop["y"] + crop["h"] > info.get("height", 0):
                raise VisualDiffError(f"frameBox {crop} выходит за картинку дизайна {info.get('width')}×{info.get('height')}: поправь "
                                      f"daiver_track_manifest(frame, set: {{frameBox}})")
        left_note = "картинка дизайна" + (" (обрезана по frameBox)" if crop else "") + " — правдивее эталонного рендера"
    else:
        left = _read_png(iteration_dir / "reference.png", "эталонный рендер")
        left_note = "эталонный рендер из канона (картинки дизайна у кадра нет) — он реконструкция: плейсхолдеры и сглаживание не баги"
    cfg = CFG.load(root)["config"]["verify"]
    vp = cfg.get("viewport") or {}
    chrome, stop = CD.launch_or_skip(cfg, root=root, viewport=(int(vp.get("width", 1280)), int(vp.get("height", 900))))
    if stop is not None:
        return dict(stop, gate="skipped")
    args = {"a": base64.b64encode(left).decode("ascii"), "b": base64.b64encode(actual).decode("ascii"), "mimeA": _mime(left), "mimeB": _mime(actual),
            "threshold": THRESHOLD, "crop": crop}
    with chrome:
        try:
            page = chrome.new_page("about:blank", viewport=(int(vp.get("width", 1280)), int(vp.get("height", 900))))
            page.evaluate(script(), timeout=30)
            res = page.evaluate("window.__pixeldiff.run(" + json.dumps(args) + ")", await_promise=True, timeout=EVAL_TIMEOUT)
        except (CD.BrowserError, CD.JsException) as e:
            raise VisualDiffError(f"браузер не посчитал дифф: {e}")
    CP._atomic_write(iteration_dir / "diff.png", base64.b64decode(res["diffPng"]))
    CP._atomic_write(iteration_dir / "diff-strip.png", base64.b64decode(res["stripPng"]))
    zones = res["zones"]
    snap = _json(iteration_dir / "snapshot.json") or {}
    clip = (snap.get("capture") or {}).get("clip") or {}
    origin = (float(clip.get("x", 0) or 0), float(clip.get("y", 0) or 0)) if isinstance(clip, dict) else (0.0, 0.0)
    defects = (_json(iteration_dir / "defects.json") or {}).get("defects") or []
    explain(zones, [d for d in defects if isinstance(d, dict) and isinstance(d.get("id"), str)], origin)
    hint = cfg.get("visualDiffThreshold")
    pct = res["percent"]
    explained = sum(1 for z in zones if z.get("explainedBy"))
    out: Dict[str, Any] = {
        "against": use, "used": left_note, "diffPercent": pct, "diffPixels": res["diffPixels"], "antialiasedPixels": res["aaPixels"],
        "size": {"w": res["width"], "h": res["height"]}, "sizes": {"design": res["sizeA"], "actual": res["sizeB"]},
        "sizeMismatch": res["sizeA"] != res["sizeB"], "threshold": hint, "aboveHint": isinstance(hint, (int, float)) and pct > hint * 100,
        "zones": zones, "explainedZones": explained, "unexplainedZones": len(zones) - explained,
        "files": {n: (iteration_dir / n).relative_to(root).as_posix() for n in ("diff.png", "diff-strip.png", "visual-diff.json")},
        "gate": "pass", "signal": True,
        "nextStep": ("дифф — сигнал, а не вердикт: он дефекта не создаёт и гейт не валит. diffPercent — ПРОЦЕНТ (3.18 = 3.18 %), в daiver_verify_state_record "
                     "передай его как есть. Зоны без explainedBy — то, что измерение не объяснило: покажи их визуальному обзору (diff-strip.png); "
                     "сглаживание и плейсхолдеры эталона — не баги")}
    if out["sizeMismatch"]:
        out["warnings"] = [f"размеры разные: макет {res['sizeA']['w']}×{res['sizeA']['h']}, реализация {res['sizeB']['w']}×{res['sizeB']['h']} — "
                           f"обе положены на белый холст максимального размера; проверь clipSelector и viewport"]
    CP._atomic_write(iteration_dir / "visual-diff.json", (json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return out
