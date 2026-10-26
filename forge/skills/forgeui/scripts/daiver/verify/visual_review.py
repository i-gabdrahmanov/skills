"""visual_review.py — визуальный обзор: рукопожатие двух вызовов `daiver_visual_review` (спека §14.7, §3.4; задача 5.2; INV-C30, C31, A8, F3, QG-U19, N19, N20).

Спросить модель ровно о том, чего измерение не видит, и не дать ей «описать картинку, которую она не открывала». У движка нет vision-бэкенда: картинки открывает САМ
агент (ничто не уходит наружу), движок собирает вопрос, принимает ответ по форме и пишет `visual-review.md`.

Первый вызов (без `review`) ничего не пишет и отдаёт: `images` (картинка дизайна — ПЕРВАЯ, реализация — ВТОРАЯ; нет картинки — эталонный рендер с оговоркой, что он
реконструкция), `prompt`, `reportPath`, `priorFixes` и, для длинного экрана, `slices` (парные полосы goal/actual по высоте — модель слабее, дроби мельче).
  • Есть доказанные дефекты compare/code (`defects.json`, `code-defects.json`, без noise) — вопрос сужается до ОСТАТКА: иконки и изображения, перекрытия и обрезка,
    перенос и усечение текста, визуальный порядок, сбои рендера. Дефекты названы построчно БЕЗ значений, таблица «макет | реализация» намеренно НЕ передаётся (числа
    измерены движком, кормить ими модель, не умеющую мерить, нельзя); значения (px, HEX, шрифт) в ответе запрещены; «ничего не нашёл» — законный ответ;
  • доказанных нет — ПОЛНЫЙ обзор по семи осям, формат плана ремонта (Сейчас / Должно быть / Фикс / Проверка), числа обязательны, «слегка» и «примерно» запрещены;
  • `priorFixes` — пункты Fix plan прошлого `visual-review.md` (путь — из `priorReview` записанной итерации, другой файл не подставляется; пустой план — нет пунктов):
    каждый переотчитывается FIXED / NOT FIXED / REGRESSED.
Второй вызов (`review`) проверяет ответ ПО ФОРМЕ (код, а не просьба) и пишет `visual-review.md` (движок рендерит текст сам — руками файл не пишется, хук 3.4) и
`visual-review.json` (находки для записи итерации). Форма: `opened` — перечень открытых картинок ровно как в `images` (нет — отказ: описывать неоткрытое нельзя);
`prior` — вердикт на каждый прежний пункт; `findings` — [{axis?, title, now, shouldBe, fix, check}] либо `nothingFound: true`; в остатке значения запрещены (px, %, HEX, rgb, hsl),
в полном обзоре обязательны поля и числа, а слова-«оговорки» запрещены. Не смог открыть картинки или `verify.vision: off` — `review: {skipped: "причина"}`: файл не пишется,
гейт `skipped` с `reviewSkipped`.

Находки обзора — НЕПОДТВЕРЖДЁННЫЕ: `zone: vision`, `source: review`; показываются и триажатся, clean не блокируют никогда, автономия их не чинит (кроме измеренного
значения или отсутствующего файла после собственной проверки).

Не проверено данными (боевых экранов и моделей-читателей нет): насколько слабая модель следует форме ответа и честно ли пишет `opened`; границы оговорок и значений (регулярки —
грубая сеть, а не разбор смысла); нарезка длинных экранов на реальных кадрах (порог MAX_SLICE_H — подбор, не измерение); читает ли GigaCode PNG (JPEG-копии — `prepare: jpeg`).
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config as CFG
from .. import image as IMG
from .. import track as TR
from ..browser import cdp as CD
from . import capture as CP
from . import ids as IDS
from . import visual_diff as VD

FILE_MD = "visual-review.md"
FILE_JSON = "visual-review.json"
IMAGES_DIR = "review-images"
MAX_SLICE_H = 1200
SLICE_OVERLAP = 48
VERDICTS = ("FIXED", "NOT FIXED", "REGRESSED")
RESIDUE_AXES = ("иконки и изображения (нет, не те, не на месте)", "перекрытия и обрезка (элемент залез на соседа или срезан)",
                "перенос и усечение текста (лишний перенос, многоточие, вылезает за рамку)", "визуальный порядок блоков",
                "сбои рендера (пустоты, битые картинки, наложенные слои)")
FULL_AXES = ("структура и порядок блоков", "иконки и изображения", "типографика", "цвет и фон", "отступы и размеры", "перекрытия, обрезка, перенос текста",
             "сбои рендера (пустоты, битые картинки)")
HEDGES = ("слегка", "примерно", "чуть ", "немного", "около ", "приблизительно", "небольш", "немножко", "как-то", "вроде")
VALUE_RE = re.compile(r"(\d+(?:[.,]\d+)?\s*(?:px|пикс|pt|пт|rem|em|%)|#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(|\bpx\b)", re.IGNORECASE)
SOURCE = "review"
ZONE = "vision"
_PRIOR_ITEM = re.compile(r"^\s*(\d+)\.\s+(\S.*?)\s*$")


class ReviewError(Exception):
    """Вызов не годится или ответ не принят по форме — сообщение адресовано агенту и говорит, что исправить."""


def slices(height: int, width: int = 0, max_h: int = MAX_SLICE_H, overlap: int = SLICE_OVERLAP) -> List[Dict[str, int]]:
    """Парные полосы по высоте для длинного экрана: [] — режется не нужно; иначе [{n, y, h}] с перекрытием, последняя доходит до низа."""
    if height <= max_h:
        return []
    out: List[Dict[str, int]] = []
    y = 0
    while True:
        h = min(max_h, height - y)
        out.append({"n": len(out) + 1, "y": y, "h": h})
        if y + h >= height:
            return out
        y += max_h - overlap


def parse_fix_plan(text: Optional[str]) -> List[Dict[str, Any]]:
    """Пункты раздела «## Fix plan» прошлого visual-review.md; пустой раздел и голый заголовок — нет пунктов (None-ответ и пустая строка одно и то же)."""
    if not isinstance(text, str):
        return []
    items: List[Dict[str, Any]] = []
    in_plan = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_plan = line[3:].strip().lower() == "fix plan"
            continue
        if in_plan:
            m = _PRIOR_ITEM.match(line)
            if m and m.group(2).strip():
                items.append({"n": int(m.group(1)), "text": m.group(2).strip()})
    return items


def prior_fixes(root: Path, paths: Dict[str, Path]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """(пункты, заметка). Источник — ТОЛЬКО `reviewReport.path` последней записи verify-state: priorReview=null → пунктов нет, другой файл не подставляется."""
    try:
        data = json.loads(paths["verifyState"].read_text(encoding="utf-8")) if paths["verifyState"].is_file() else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None, "verify-state.json не читается: прежние пункты не восстановить"
    history = (data or {}).get("history") if isinstance(data, dict) else None
    last = history[-1] if isinstance(history, list) and history else None
    report = (last or {}).get("reviewReport")
    rel = report.get("path") if isinstance(report, dict) else None
    if not isinstance(rel, str) or not rel:
        return None, None
    p = (root / rel).resolve()
    try:
        p.relative_to(paths["verify"].resolve())
    except ValueError:
        return None, f"reviewReport.path «{rel}» вне verify/ кадра — не читаю"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None, f"прошлый {rel} не найден: пунктов для переотчёта нет"
    items = parse_fix_plan(text)
    return (items or None), None


def _json(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    items = data.get("defects") if isinstance(data, dict) else None
    return [d for d in (items or []) if isinstance(d, dict) and isinstance(d.get("id"), str)]


def proven(iteration_dir: Path) -> List[Dict[str, Any]]:
    """Доказанные дефекты гейтов compare и code этой итерации (без noise); дубль по id — один (измеренное первым)."""
    seen: Dict[str, Dict[str, Any]] = {}
    for name in ("defects.json", "code-defects.json"):
        for d in _json(iteration_dir / name):
            if not d.get("noise") and d.get("zone", "deterministic") != ZONE and d["id"] not in seen:
                seen[d["id"]] = d
    return list(seen.values())


def proven_lines(defects: List[Dict[str, Any]]) -> List[str]:
    """Дефекты построчно БЕЗ значений: код, свойство, элемент по видимому тексту — ни `rows`, ни `expected`, ни числа из сообщения."""
    out = []
    for d in defects:
        prop = f" ({d['prop']})" if d.get("prop") and not VALUE_RE.search(str(d["prop"])) else ""
        el = str(d.get("element") or "").strip()
        el = "" if VALUE_RE.search(el) else el
        out.append(f"{d.get('code', '?')}{prop}" + (f" — «{el}»" if el else ""))
    return out


def build_prompt(mode: str, ground: str, lines: List[str], prior: Optional[List[Dict[str, Any]]], sl: List[Dict[str, int]], report_path: str) -> str:
    parts: List[str] = []
    truth = ("ПЕРВАЯ картинка — дизайн кадра (то, как должно быть: он правдивее любой реконструкции), ВТОРАЯ — реализация (как есть)." if ground == "goal" else
             "ПЕРВАЯ картинка — эталонный рендер, собранный из канона макета (РЕКОНСТРУКЦИЯ: поверхность библиотечных компонентов и плейсхолдеры в нём не нарисованы, "
             "пунктир — метка недостающего), ВТОРАЯ — реализация. Картинки дизайна у кадра нет — скажи об этом в ответе.")
    parts.append("Открой ОБЕ картинки сам (список `images`, порядок обязателен) и ответь только по тому, что на них видно. Не описывай картинку по имени файла, пути или "
                 "по канону: не смог открыть — ответь `review: {skipped: \"причина\"}` и остановись.")
    parts.append(truth)
    if sl:
        parts.append(f"Экран длинный ({len(sl)} полос): смотри парами полос goal/actual по порядку {', '.join(str(s['n']) for s in sl)} (y и высота — в `slices`), "
                     f"каждая пара — отдельный взгляд; находка называет номер полосы.")
    if mode == "residue":
        parts.append("Измерение УЖЕ нашло и доказало эти расхождения — НЕ повторяй их:\n" + "\n".join(f"- {x}" for x in lines))
        parts.append("Тебя спрашивают только про ОСТАТОК — то, чего измерение не видит:\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(RESIDUE_AXES, 1)))
        parts.append("ЗАПРЕЩЕНЫ любые значения: пиксели, проценты, HEX и rgb-цвета, имена и размеры шрифтов, отступы — их мерил движок, а ты мерить не умеешь. "
                     "Описывай словами, что и где видишь. «Ничего не нашёл» — законный ответ: верни `nothingFound: true`, не придумывай находок.")
    else:
        parts.append("Доказанных измерением дефектов нет — сделай ПОЛНЫЙ обзор по семи осям:\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(FULL_AXES, 1)))
        parts.append("Каждая находка — пункт плана ремонта из четырёх полей: `now` (что сейчас), `shouldBe` (что должно быть), `fix` (что именно поменять), `check` (как проверить). "
                     "Числа ОБЯЗАТЕЛЬНЫ (размер, цвет, позиция); слова «слегка», «примерно», «чуть», «немного», «около» запрещены. «Ничего не нашёл» — законный ответ: `nothingFound: true`.")
    if prior:
        parts.append("Прежний Fix plan — по КАЖДОМУ пункту скажи FIXED, NOT FIXED или REGRESSED (`prior: [{n, verdict, note?}]`):\n" +
                     "\n".join(f"{p['n']}. {p['text']}" for p in prior))
    parts.append(f"Ответь вторым вызовом daiver_visual_review с `review: {{opened: [пути из images, как есть], prior?, findings | nothingFound}}`. Движок сам напишет {report_path}; "
                 f"руками этот файл не пишется. Каждая находка — НЕПОДТВЕРЖДЁННОЕ наблюдение (zone vision): она показывается человеку и не блокирует чистоту.")
    return "\n\n".join(parts)


def _size(path: Path) -> Dict[str, int]:
    info = IMG.sniff(path) or {}
    return {"w": info.get("width", 0), "h": info.get("height", 0)}


def _check_text(f: Dict[str, Any], key: str, where: str) -> str:
    v = f.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ReviewError(f"{where}: нет поля «{key}» (непустая строка)")
    return v.strip()


def validate(review: Any, images: List[str], mode: str, prior: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Проверка ответа по форме; → нормализованный ответ. Любое нарушение — `ReviewError` с перечнем, ничего не пишется."""
    if not isinstance(review, dict):
        raise ReviewError("review: ожидается объект {opened, prior?, findings | nothingFound} либо {skipped: «причина»}")
    allowed = {"opened", "prior", "findings", "nothingFound", "skipped"}
    extra = sorted(k for k in review if k not in allowed)
    if extra:
        raise ReviewError(f"review: неизвестные поля {', '.join(extra)}")
    problems: List[str] = []
    if review.get("opened") != images:
        problems.append(f"opened: нужен перечень открытых картинок РОВНО как в images и в том же порядке ({', '.join(images)}) — без него обзор принять нельзя: "
                        f"описывать неоткрытое нельзя, не открылись — review: {{skipped: «причина»}}")
    norm_prior: List[Dict[str, Any]] = []
    if prior:
        given = review.get("prior")
        if not isinstance(given, list):
            problems.append(f"prior: вердикт FIXED / NOT FIXED / REGRESSED нужен на каждый прежний пункт ({', '.join(str(p['n']) for p in prior)})")
        else:
            by = {}
            for e in given:
                if not isinstance(e, dict) or not isinstance(e.get("n"), int) or e.get("verdict") not in VERDICTS:
                    problems.append(f"prior: запись {e!r} — нужны n (номер пункта) и verdict {' | '.join(VERDICTS)}")
                else:
                    by[e["n"]] = e
            miss = [p["n"] for p in prior if p["n"] not in by]
            unk = sorted(n for n in by if n not in {p["n"] for p in prior})
            if miss:
                problems.append(f"prior: нет вердикта на пункты {', '.join(map(str, miss))}")
            if unk:
                problems.append(f"prior: нет таких прежних пунктов: {', '.join(map(str, unk))}")
            norm_prior = [{"n": p["n"], "text": p["text"], "verdict": by[p["n"]]["verdict"], "note": str(by[p["n"]].get("note") or "").strip()}
                          for p in prior if p["n"] in by]
    elif review.get("prior"):
        problems.append("prior: прежнего плана нет — переотчитываться не о чем")
    findings = review.get("findings")
    nothing = review.get("nothingFound") is True
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        problems.append("findings: ожидается список находок")
        findings = []
    if nothing and findings:
        problems.append("nothingFound: true несовместимо с непустым findings")
    if not nothing and not findings:
        problems.append("пустой обзор: «ничего не нашёл» — законный ответ, но его говорят явно: nothingFound: true")
    out_findings: List[Dict[str, Any]] = []
    for k, f in enumerate(findings, 1):
        where = f"findings[{k}]"
        if not isinstance(f, dict):
            problems.append(f"{where}: ожидается объект")
            continue
        try:
            item = {"title": _check_text(f, "title", where), "axis": str(f.get("axis") or "").strip()}
            fields = ("now", "shouldBe", "fix", "check") if mode == "full" else ("now",)
            for key in fields:
                item[key] = _check_text(f, key, where)
            for key in ("shouldBe", "fix", "check"):
                if key not in item and isinstance(f.get(key), str) and f[key].strip():
                    item[key] = f[key].strip()
        except ReviewError as e:
            problems.append(str(e))
            continue
        blob = " ".join(str(v) for v in item.values())
        if mode == "residue":
            bad = sorted(set(m.group(0).strip() for m in VALUE_RE.finditer(blob)))
            if bad:
                problems.append(f"{where}: в обзоре остатка значения запрещены (их мерил движок): {', '.join(bad[:5])} — опиши словами, что и где видишь")
        else:
            hedge = sorted(h.strip() for h in HEDGES if h in blob.lower())
            if hedge:
                problems.append(f"{where}: слова-оговорки запрещены ({', '.join(hedge)}) — назови число")
            if not re.search(r"\d", item.get("shouldBe", "") + " " + item.get("fix", "")):
                problems.append(f"{where}: в «Должно быть» / «Фикс» нужны числа (размер, цвет, позиция)")
        out_findings.append(item)
    if problems:
        raise ReviewError("обзор не принят (" + str(len(problems)) + "): " + "; ".join(problems[:8]))
    return {"prior": norm_prior, "findings": out_findings, "nothingFound": nothing}


def finding_id(f: Dict[str, Any]) -> str:
    return IDS.defect_id("VISION", None, None, re.sub(r"\s+", " ", (f.get("axis") or "") + "|" + f["title"]).strip().casefold())


def render_md(iteration: str, mode: str, ground: str, images: List[str], norm: Dict[str, Any]) -> str:
    lines = ["---", f"iteration: {iteration}", f"mode: {mode}", f"ground: {ground}", "opened:"] + [f"  - {p}" for p in images] + ["---", "",
             f"# Визуальный обзор — {iteration}", ""]
    lines.append("Эталон: " + ("картинка дизайна кадра" if ground == "goal" else "эталонный рендер из канона (реконструкция; картинки дизайна у кадра нет)"))
    lines.append("Режим: " + ("остаток после измерения (значения не называются)" if mode == "residue" else "полный обзор по семи осям"))
    lines += ["", "Находки обзора НЕПОДТВЕРЖДЁННЫЕ (zone: vision): показываются и триажатся, чистоту не блокируют.", ""]
    if norm["prior"]:
        lines += ["## Прежние пункты", ""]
        for p in norm["prior"]:
            lines.append(f"{p['n']}. [{p['verdict']}] {p['text']}" + (f" — {p['note']}" if p["note"] else ""))
        lines.append("")
    lines += ["## Находки", ""]
    if not norm["findings"]:
        lines += ["Ничего не найдено.", ""]
    for n, f in enumerate(norm["findings"], 1):
        lines.append(f"### {n}. {f['title']}")
        if f.get("axis"):
            lines.append(f"- Ось: {f['axis']}")
        lines.append(f"- Сейчас: {f['now']}")
        for label, key in (("Должно быть", "shouldBe"), ("Фикс", "fix"), ("Проверка", "check")):
            if f.get(key):
                lines.append(f"- {label}: {f[key]}")
        lines.append("")
    lines += ["## Fix plan", ""]
    plan = [f for f in norm["findings"] if f.get("fix")]
    lines += [f"{n}. {f['title']} — {f['fix']}" for n, f in enumerate(plan, 1)] if plan else []
    return "\n".join(lines).rstrip("\n") + "\n"


JPEG_JS = """
(async function (args) {
  function load(b64, mime) { return new Promise(function (res, rej) { var i = new Image(); i.onload = function () { res(i); }; i.onerror = function () { rej(new Error("не декодируется")); }; i.src = "data:" + mime + ";base64," + b64; }); }
  var img = await load(args.data, args.mime), out = [];
  for (var k = 0; k < args.jobs.length; k++) {
    var j = args.jobs[k], c = document.createElement("canvas"); c.width = j.w; c.height = j.h;
    var ctx = c.getContext("2d"); ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, j.w, j.h);
    ctx.drawImage(img, j.sx, j.sy, j.sw, j.sh, 0, 0, j.sw, j.sh);
    out.push(c.toDataURL("image/jpeg", 0.92).split(",", 2)[1]);
  }
  return out;
})
"""


def _jpeg_copies(root: Path, iteration_dir: Path, cfg: Dict[str, Any], jobs: Dict[str, Tuple[bytes, List[Dict[str, int]]]]) -> Optional[Dict[str, Any]]:
    """JPEG-копии картинок (и полос) через canvas Chromium: PNG отдаётся как есть, не прочёлся — запасной путь. → {имя: путь} или ответ «браузера нет»."""
    vp = cfg.get("viewport") or {}
    chrome, stop = CD.launch_or_skip(cfg, root=root, viewport=(int(vp.get("width", 1280)), int(vp.get("height", 900))))
    if stop is not None:
        return {"stop": stop}
    written: Dict[str, str] = {}
    outdir = iteration_dir / IMAGES_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    with chrome:
        page = chrome.new_page("about:blank")
        for name, (data, js_jobs) in jobs.items():
            mime = VD._mime(data)
            res = page.evaluate("(" + JPEG_JS.strip() + ")(" + json.dumps({"data": base64.b64encode(data).decode("ascii"), "mime": mime, "jobs": js_jobs}) + ")",
                                await_promise=True, timeout=VD.EVAL_TIMEOUT)
            for job, b64 in zip(js_jobs, res):
                target = outdir / job["name"]
                CP._atomic_write(target, base64.b64decode(b64))
                written[job["name"]] = target.relative_to(root).as_posix()
    return {"written": written}


def run(root: Any, track: str, frame: Optional[str], iteration_dir: Path, review: Any = None, prepare: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise ReviewError(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    f = TR.get_frame(manifest, frame)
    paths = TR.frame_paths(root, manifest, f["name"])
    if not CP.iteration_dir_ok(paths, iteration_dir):
        raise ReviewError(f"iterationDir: папка итерации лежит под «verify» (или «baseline») кадра, а не «{iteration_dir}»")
    cfg = CFG.load(root)["config"]["verify"]
    report_path = (iteration_dir / FILE_MD).relative_to(root).as_posix()
    if prepare not in (None, "jpeg"):
        raise ReviewError("prepare: только jpeg (JPEG-копии картинок через canvas — запасной путь, если PNG не прочёлся)")
    if cfg.get("vision") is False:
        if review is not None and not (isinstance(review, dict) and set(review) == {"skipped"}):
            raise ReviewError("verify.vision: off — визуальный обзор выключен конфигом: ответ принять нельзя; гейт review записывается skipped")
        return {"gate": "skipped", "reviewSkipped": "verify.vision: off — обзор картинок выключен в конфиге", "written": False,
                "nextStep": "обзор не проводится; в daiver_verify_state_record передай review: skipped и reviewSkipped этой причиной"}
    actual = iteration_dir / "actual.png"
    if not actual.is_file():
        raise ReviewError("в папке итерации нет actual.png: сначала daiver_screenshot в эту папку")
    has_goal = bool(f.get("raster")) and paths["raster"].is_file()
    ground = VD.pick_target(paths, iteration_dir, None, has_goal)
    design = paths["raster"] if ground == "goal" else iteration_dir / "reference.png"
    box = f.get("frameBox") if ground == "goal" and isinstance(f.get("frameBox"), dict) else None
    images = [design.relative_to(root).as_posix(), actual.relative_to(root).as_posix()]
    items, prior_note = prior_fixes(root, paths)
    defects = proven(iteration_dir)
    mode = "residue" if defects else "full"
    if review is None:
        d_size, a_size = _size(design), _size(actual)
        if box:
            d_size = {"w": box["w"], "h": box["h"]}
        height = max(d_size["h"], a_size["h"])
        sl = slices(height)
        out: Dict[str, Any] = {
            "mode": mode, "ground": ground, "vision": True,
            "images": [{"role": "design" if ground == "goal" else "reference", "path": images[0], "size": d_size, **({"cropBox": box} if box else {})},
                       {"role": "actual", "path": images[1], "size": a_size}],
            "prompt": build_prompt(mode, ground, proven_lines(defects), items, sl, report_path), "reportPath": report_path,
            "priorFixes": items, "proven": len(defects), "slices": sl or None, "written": False,
            "nextStep": ("открой картинки из images по порядку (дизайн ПЕРВОЙ), ответь по prompt и вызови daiver_visual_review ещё раз с review=…; "
                         "не можешь открыть — review: {skipped: «причина»}, ничего не описывай по имени файла")}
        if ground == "reference":
            out["warnings"] = ["картинки дизайна у кадра нет: «макетом» служит эталонный рендер из канона — он реконструкция, скажи об этом в ответе"]
        if prior_note:
            out.setdefault("warnings", []).append(prior_note)
        if prepare == "jpeg":
            jobs: Dict[str, Tuple[bytes, List[Dict[str, int]]]] = {}
            for role, p, size, b in (("design", design, d_size, box), ("actual", actual, a_size, None)):
                data = p.read_bytes()
                bands = sl or [{"n": 0, "y": 0, "h": size["h"]}]
                js_jobs = []
                for s in bands:
                    sx, sy = (b["x"], b["y"] + s["y"]) if b else (0, s["y"])
                    sw, sh = size["w"], min(s["h"], max(0, (size["h"] - s["y"])))
                    js_jobs.append({"name": f"{role}.jpg" if not sl else f"{role}-{s['n']}.jpg", "sx": sx, "sy": sy, "sw": sw, "sh": sh, "w": max(sw, 1), "h": max(s["h"], 1)})
                jobs[role] = (data, js_jobs)
            res = _jpeg_copies(root, iteration_dir, cfg, jobs)
            if "stop" in res:
                out["prepare"] = {"blocked": res["stop"].get("blocked"), "reason": res["stop"].get("reason")}
            else:
                out["jpegCopies"] = res["written"]
                out["written"] = True
                out["nextStep"] += "; PNG не прочёлся — открой JPEG-копии из jpegCopies (те же картинки и полосы)"
        return out
    # второй вызов
    if isinstance(review, dict) and set(review) == {"skipped"}:
        why = review["skipped"]
        if not isinstance(why, str) or not why.strip():
            raise ReviewError("review.skipped: одна строка причины («картинки не открылись», «модель не принимает изображения»)")
        return {"gate": "skipped", "reviewSkipped": " ".join(why.split()), "written": False,
                "nextStep": "обзор не проводился: передай review: skipped и reviewSkipped этой строкой в daiver_verify_state_record; visual-review.md руками не пиши"}
    norm = validate(review, images, mode, items)
    md = render_md(iteration_dir.name, mode, ground, images, norm)
    CP._atomic_write(iteration_dir / FILE_MD, md.encode("utf-8"))
    vision = [{"id": finding_id(x), "code": "VISION", "layer": "pixel", "severity": "major", "zone": ZONE, "source": SOURCE, "element": x["title"],
               "message": x["now"], **({"fix": x["fix"]} if x.get("fix") else {})} for x in norm["findings"]]
    seen = set()
    vision = [v for v in vision if not (v["id"] in seen or seen.add(v["id"]))]
    CP._atomic_write(iteration_dir / FILE_JSON, (json.dumps({"mode": mode, "ground": ground, "nothingFound": norm["nothingFound"], "defects": vision,
                                                              "prior": norm["prior"]}, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    verdicts: Dict[str, int] = {}
    for p in norm["prior"]:
        verdicts[p["verdict"]] = verdicts.get(p["verdict"], 0) + 1
    return {"gate": "pass", "written": True, "mode": mode, "ground": ground, "reportPath": report_path, "findings": len(vision), "nothingFound": norm["nothingFound"],
            "priorVerdicts": verdicts, "defects": vision, "reviewReport": {"path": report_path, "mode": mode, "fixPlan": sum(1 for x in norm["findings"] if x.get("fix"))},
            "signal": True,
            "nextStep": ("обзор записан. Находки — НЕПОДТВЕРЖДЁННЫЕ (zone vision): покажи их человеку, чистоту они не блокируют. В daiver_verify_state_record "
                         "gates.review = pass; движок сам возьмёт находки и reviewReport из папки итерации")}
