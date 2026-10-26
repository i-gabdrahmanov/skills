"""report_html.py — `report.html`: самодостаточный отчёт итерации (спека §14.10; задача 5.3; INV-C32, QG-P6).

Человек решает по итерации ГЛАЗАМИ — до любых слов агента: макет и реализация рядом, пронумерованные пины дефектов, таблица «МАКЕТ | РЕАЛИЗАЦИЯ» с переводом имён
свойств на русский, чипы-фильтры только из фактических кодов, ссылки на узлы макета и на файл:строку, ранее принятые решения. Читает ТОЛЬКО артефакты итерации и состояние
кадра (дефекты — тем же кодом, что запись итерации: `state._derive` + решения человека), поэтому отчёт не может разойтись с тем, что запишет движок.

Самодостаточность: ни одного внешнего ресурса — обе картинки (и дифф, если он есть) вшиты data-URI, CSS и скрипт внутри, шрифты системные. Дата — ВХОД (`date`), а не часы:
одинаковый вход → побайтно одинаковый файл (ключи отсортированы, порядок дефектов — как у движка). Всё, что уходит в страницу из дефектов и решений, экранируется:
текст — `html.escape`, JSON для скрипта — без `<`, `>`, `&`, U+2028/2029 (иначе `</script>` в тексте ломал бы страницу).

Пины: номер дефекта = номер в списке. Реализация — по `pin` (бокс страницы) со сдвигом `clip` захвата, процентами от размера картинки; макет — по `designPin` процентами от размера корня кадра
(нет числового размера корня — пинов макета нет, это сказано в отчёте). В автономном режиме отчёт не рендерится (триаж агента заменяет его).

Полоса кадров показывает кадры трека и их состояние из `verify-state.json` (непроверенный кадр назван «не проверялся»); переключение между кадрами и один отчёт на весь трек — 6.1.
Решения человека в отчёте (fix / skip / false_positive, разметка «Пометить проблему», экспорт `verify-decisions.json`) — 5.4: у каждого дефекта уже есть `data-id` и место под них.

Не проверено данными (боевых итераций нет): вид на настоящих экранах (длинные таблицы, десятки дефектов), точность пинов макета при `frameBox` и масштабе картинки дизайна, размер
файла на больших кадрах (картинки вшиты целиком), браузеры, кроме Chromium.
"""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import canon as C
from .. import config as CFG
from .. import image as IMG
from .. import outline as OL
from .. import track as TR
from . import capture as CP
from . import decisions as DEC
from . import state as ST

FILE = "report.html"
CODES_RU = {"MISSING": "нет на странице", "EXTRA": "лишнее на странице", "ORDER": "нарушен порядок", "TEXT": "другой текст", "SLOT_MISSING": "слот не нарисован",
            "SLOT_TEXT": "другой текст слота", "SLOT_TYPOGRAPHY": "типографика слота", "SLOT_COLOR": "цвет слота", "INVISIBLE": "невидимая поверхность",
            "SPACING": "отступы", "SIZE": "размер", "POSITION": "позиция", "OVERFLOW": "переполнение", "TYPOGRAPHY": "типографика", "COLOR": "цвет", "SHAPE": "форма",
            "DS_COMPONENT_WRONG": "не тот компонент", "DS_COMPONENT_MISSED": "нет компонента библиотеки", "DS_PROP_WRONG": "проп вне контракта", "DS_DEPRECATED": "deprecated",
            "DS_TOKEN_MISSED": "литерал вместо токена", "DS_OFF_SCALE": "мимо шкалы токенов", "VISION": "наблюдение обзора"}
PROPS_RU = {"padding.top": "внутренний отступ сверху", "padding.right": "внутренний отступ справа", "padding.bottom": "внутренний отступ снизу",
            "padding.left": "внутренний отступ слева", "gap": "промежуток между детьми", "w": "ширина", "h": "высота", "x": "позиция по горизонтали",
            "y": "позиция по вертикали", "background": "цвет фона", "color": "цвет текста", "border": "обводка", "radius": "скругление", "shadow": "тень",
            "gradient": "градиент", "text": "текст"}
SEVERITY_RU = {"critical": "критичный", "major": "значимый", "minor": "замечание"}
LIFECYCLE_RU = {"new": "новый", "persisting": "остался", "regressed": "вернулся"}
STATUS_RU = {"clean": "чисто", "defects": "есть дефекты", "hard-gate-fail": "страница — не этот кадр", "recapture": "нужен перезахват"}
GATE_RU = {"compare": "сравнение", "code": "код-гейт", "visualDiff": "пиксельный дифф", "review": "визуальный обзор", "e2e": "e2e"}


class ReportError(ValueError):
    """Отчёт построить нельзя — сообщение называет причину."""


def prop_ru(prop: Optional[str]) -> str:
    if not prop:
        return ""
    head, sep, tail = prop.partition(":")
    if sep and head not in PROPS_RU:
        return f"слот {head}: {PROPS_RU.get(tail, tail)}"
    return PROPS_RU.get(prop, prop)


def script_json(obj: Any) -> str:
    """JSON для `<script type="application/json">`: ни одного символа, способного закрыть тег или оборвать строку."""
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return s.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _uri(path: Path) -> Optional[Tuple[str, Dict[str, int]]]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    info = IMG.sniff_bytes(data[:1 << 20])
    if info is None:
        return None
    mime = "image/jpeg" if info["format"] == "jpeg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii"), {"w": info["width"], "h": info["height"]}


def _json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pct(v: float, whole: float) -> str:
    """Процент без хвоста нулей («20», «33.333»): стабильная строка, читаемая и в тесте."""
    return ("%.3f" % (v / whole * 100)).rstrip("0").rstrip(".") if whole else "0"


def pin_style(box: Dict[str, Any], origin: Tuple[float, float], size: Dict[str, int]) -> Optional[str]:
    if not isinstance(box, dict) or not all(isinstance(box.get(k), (int, float)) for k in ("x", "y", "w", "h")) or not size["w"] or not size["h"]:
        return None
    x, y = box["x"] - origin[0], box["y"] - origin[1]
    return f"left:{_pct(x, size['w'])}%;top:{_pct(y, size['h'])}%;width:{_pct(box['w'], size['w'])}%;height:{_pct(box['h'], size['h'])}%"


def _frames_bar(root: Path, manifest: Dict[str, Any], current: str) -> List[Dict[str, Any]]:
    out = []
    for f in manifest.get("frames") or []:
        st = ST._read(TR.frame_paths(root, manifest, f["name"])["verifyState"]) if TR.frame_paths(root, manifest, f["name"])["verifyState"].is_file() else None
        hist = (st or {}).get("history") or []
        out.append({"name": f["name"], "current": f["name"] == current, "iterations": len(hist),
                    "state": "не проверялся" if not hist else ("чисто" if hist[-1].get("clean") else f"блокирующих: {len(hist[-1].get('blocking') or [])}")})
    return out


def collect(root: Any, track: str, frame: Optional[str], iteration_dir: Path) -> Dict[str, Any]:
    """Всё, что показывает отчёт, — данными (рендер — чистая функция от них)."""
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise ReportError(f"трек «{track}» не найден: кадр заводит daiver_pixso_pull")
    f = TR.get_frame(manifest, frame)
    paths = TR.frame_paths(root, manifest, f["name"])
    if not CP.iteration_dir_ok(paths, iteration_dir):
        raise ReportError(f"iterationDir: папка итерации лежит под «verify» (или «baseline») кадра, а не «{iteration_dir}»")
    if not (iteration_dir / "actual.png").is_file():
        raise ReportError("в папке итерации нет actual.png: сначала daiver_screenshot в эту папку")
    summary = _json(iteration_dir / "compare-summary.json") or {}
    facts = ST._derive(iteration_dir)
    defects = facts["defects"] + [d for d in facts.get("visionDefects", []) if d["id"] not in {x["id"] for x in facts["defects"]}]
    try:
        loaded = DEC.load(root, manifest, f["name"])
    except DEC.DecisionsError as e:
        raise ReportError(str(e))
    dres = DEC.apply(defects, loaded)
    ground = "goal" if (f.get("raster") and paths["raster"].is_file()) else ("reference" if (iteration_dir / "reference.png").is_file() else None)
    design_path = paths["raster"] if ground == "goal" else (iteration_dir / "reference.png" if ground else None)
    snap = _json(iteration_dir / "snapshot.json") or {}
    clip = (snap.get("capture") or {}).get("clip") or {}
    canon_env = None
    if paths["canonical"].is_file():
        try:
            canon_env = C.read_canonical(paths["canonical"])
        except C.CanonError:
            canon_env = None
    canon_root = canon_env["root"] if canon_env else None
    rb = (canon_root or {}).get("box") if isinstance((canon_root or {}).get("box"), dict) else {}
    rs = (canon_root or {}).get("size") or {}
    root_size = {"x": rb.get("x", 0), "y": rb.get("y", 0), "w": rs.get("w") if isinstance(rs.get("w"), (int, float)) else rb.get("w"),
                 "h": rs.get("h") if isinstance(rs.get("h"), (int, float)) else rb.get("h")}
    state = ST._read(paths["verifyState"]) if paths["verifyState"].is_file() else None
    recorded = any(h.get("dir") == iteration_dir.relative_to(root).as_posix() for h in (state or {}).get("history") or [])
    source_format = (canon_env.get("daiver") or {}).get("sourceFormat") if canon_env else None
    template = OL.node_url_template(f.get("frameUrl"), source_format)
    review = _json(iteration_dir / "visual-review.json")
    diff = _json(iteration_dir / "visual-diff.json")
    return {"track": track, "frame": f["name"], "iteration": iteration_dir.name, "summary": summary, "defects": defects, "decisions": loaded, "decisionResult": dres,
            "ground": ground, "design": _uri(design_path) if design_path else None, "actual": _uri(iteration_dir / "actual.png"),
            "diffImage": _uri(iteration_dir / "diff.png"), "diff": diff, "review": review, "clip": (float(clip.get("x", 0) or 0), float(clip.get("y", 0) or 0))
            if isinstance(clip, dict) else (0.0, 0.0), "rootSize": root_size, "recorded": recorded, "frames": _frames_bar(root, manifest, f["name"]),
            "template": template, "gates": {"compare": summary.get("status"),
            "code": (_json(iteration_dir / "code-summary.json") or {}).get("status"), "visualDiff": "pass" if diff else None, "review": "pass" if review else None}}


CSS = """:root{--bg:#fff;--fg:#1c1c24;--mut:#6b6b7a;--line:#d9d9e3;--card:#f6f6fa;--crit:#c62828;--maj:#d9730d;--min:#6b6b7a;--ok:#2e7d32;--pin:#d81b60;--acc:#3a5bd9}
@media (prefers-color-scheme:dark){:root{--bg:#16161d;--fg:#e8e8ef;--mut:#9a9aae;--line:#33333f;--card:#20202a;--pin:#ff6090;--acc:#8aa0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header,main{max-width:1280px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}
.meta{color:var(--mut)}.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:2px 4px 2px 0;background:var(--card);cursor:pointer;font-size:12px}
.chip.on{background:var(--acc);color:#fff;border-color:var(--acc)}.chip.static{cursor:default}
.frames .chip.cur{border-color:var(--acc);font-weight:600}.panes{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:760px){.panes{grid-template-columns:1fr}}
.pane{border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--card)}.pane h3{font-size:13px;margin:0 0 6px;color:var(--mut)}
.shot{position:relative;line-height:0}.shot img{width:100%;height:auto;display:block}
.pin{position:absolute;border:2px solid var(--pin);border-radius:3px;pointer-events:none}.pin b{position:absolute;left:-2px;top:-20px;background:var(--pin);color:#fff;border-radius:10px;min-width:18px;height:18px;line-height:18px;font-size:11px;text-align:center;pointer-events:auto;cursor:pointer;padding:0 4px}
.pin.hl{box-shadow:0 0 0 3px rgba(216,27,96,.35)}.zone{position:absolute;border:1px dashed var(--maj);background:rgba(217,115,13,.10);pointer-events:none}.zone.ex{border-color:var(--ok);background:rgba(46,125,50,.08)}
.defect{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--card)}.defect.hl{outline:2px solid var(--pin)}
.defect .head{display:flex;flex-wrap:wrap;gap:6px;align-items:baseline}.num{background:var(--pin);color:#fff;border-radius:10px;min-width:22px;text-align:center;font-weight:600;padding:0 6px}
.sev-critical{color:var(--crit);font-weight:600}.sev-major{color:var(--maj);font-weight:600}.sev-minor{color:var(--min)}
table{border-collapse:collapse;margin:6px 0}th,td{border:1px solid var(--line);padding:3px 10px;text-align:left}th{background:var(--bg);color:var(--mut);font-weight:600}
.note{color:var(--mut);font-size:12px}.tag{font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--mut)}
.gates td:first-child{color:var(--mut)}code{font:12px ui-monospace,Menlo,monospace}a{color:var(--acc)}"""

JS = """(function(){var chips=document.querySelectorAll('[data-filter]'),cards=document.querySelectorAll('.defect'),active={};
function apply(){var any=Object.keys(active).length;cards.forEach(function(c){c.hidden=any&&!active[c.dataset.code]});
document.querySelectorAll('.pin').forEach(function(p){p.hidden=any&&!active[p.dataset.code]})}
chips.forEach(function(ch){ch.addEventListener('click',function(){var k=ch.dataset.filter;if(active[k]){delete active[k];ch.classList.remove('on')}else{active[k]=1;ch.classList.add('on')}apply()})});
function hl(n,on){document.querySelectorAll('[data-n="'+n+'"]').forEach(function(e){e.classList.toggle('hl',on)})}
document.querySelectorAll('.defect,.pin b').forEach(function(e){var n=(e.closest('[data-n]')||e).dataset.n;e.addEventListener('mouseenter',function(){hl(n,true)});e.addEventListener('mouseleave',function(){hl(n,false)})});
document.querySelectorAll('.pin b').forEach(function(b){b.addEventListener('click',function(){var d=document.querySelector('.defect[data-n="'+b.parentNode.dataset.n+'"]');if(d)d.scrollIntoView({block:'center'})})})})();"""


def _row_table(d: Dict[str, Any]) -> str:
    rows = d.get("rows") or []
    if not rows:
        return ""
    body = "".join(f"<tr><td>{_e(r.get('МАКЕТ'))}</td><td>{_e(r.get('РЕАЛИЗАЦИЯ'))}</td><td>{_e(r.get('count', 1))}</td></tr>" for r in rows)
    return f"<table><thead><tr><th>МАКЕТ</th><th>РЕАЛИЗАЦИЯ</th><th>раз</th></tr></thead><tbody>{body}</tbody></table>"


def _node_link(template: Optional[str], node_id: Optional[str]) -> str:
    if not node_id:
        return ""
    url = OL.node_url(template, node_id)
    return f'<a href="{_e(url)}">{_e(node_id)}</a>' if url else f"<code>{_e(node_id)}</code>"


def _editor(loc: Dict[str, Any]) -> str:
    where = f"{loc.get('file')}:{loc.get('line')}"
    return f"<code>{_e(where)}</code>"


def render(data: Dict[str, Any], date: Optional[str] = None) -> str:
    d_list = data["defects"]
    codes = sorted({d["code"] for d in d_list})
    summary = data["summary"]
    out: List[str] = ['<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
                      f"<title>{_e(data['track'])} · {_e(data['frame'])} · {_e(data['iteration'])}</title><style>{CSS}</style></head><body>"]
    status = summary.get("status")
    out.append(f"<header><h1>Проверка {_e(data['track'])} · кадр {_e(data['frame'])} · {_e(data['iteration'])}</h1>")
    meta = [STATUS_RU.get(status, status or "сравнение не выполнялось")]
    if summary.get("matchRate") is not None:
        meta.append(f"matchRate {summary['matchRate']}")
    if summary.get("fidelity") is not None:
        meta.append(f"fidelity {summary['fidelity']}")
    if data["diff"] and data["diff"].get("diffPercent") is not None:
        meta.append(f"дифф {data['diff']['diffPercent']}%")
    if date:
        meta.append(str(date))
    out.append(f'<div class="meta">{" · ".join(_e(m) for m in meta)}</div>')
    if not data["recorded"]:
        out.append('<p class="note"><b>Итерация ещё не записана</b> (stateRecorded: false): сначала daiver_verify_state_record — без записи номер не двигается, а числа в отчёте не попадут в историю.</p>')
    out.append('<div class="frames">' + "".join(
        f'<span class="chip static{" cur" if f["current"] else ""}">{_e(f["name"])} — {_e(f["state"])}</span>' for f in data["frames"]) + "</div></header><main>")
    if summary.get("nextStep"):
        out.append(f'<p class="note">{_e(summary["nextStep"])}</p>')
    # картинки с пинами
    design, actual = data["design"], data["actual"]
    out.append('<section class="panes">')
    rs = data["rootSize"]
    dpins, apins = [], []
    for n, d in enumerate(d_list, 1):
        if actual and d.get("pin"):
            st = pin_style(d["pin"], data["clip"], actual[1])
            if st:
                apins.append(f'<div class="pin" data-n="{n}" data-code="{_e(d["code"])}" style="{st}"><b>{n}</b></div>')
        if design and d.get("designPin") and isinstance(rs.get("w"), (int, float)) and isinstance(rs.get("h"), (int, float)):
            st = pin_style(d["designPin"], (rs["x"], rs["y"]), {"w": int(rs["w"]), "h": int(rs["h"])})
            if st:
                dpins.append(f'<div class="pin" data-n="{n}" data-code="{_e(d["code"])}" style="{st}"><b>{n}</b></div>')
    zones = []
    if data["diff"] and actual:
        for z in data["diff"].get("zones") or []:
            zones.append(f'<div class="zone{" ex" if z.get("explainedBy") else ""}" style="{pin_style(z, (0, 0), actual[1]) or ""}"></div>')
    label = {"goal": "Макет (картинка дизайна)", "reference": "Макет (эталонный рендер — реконструкция)"}.get(data["ground"], "Макет")
    if design:
        out.append(f'<div class="pane"><h3>{_e(label)}</h3><div class="shot"><img alt="макет" src="{design[0]}">{"".join(dpins)}</div>'
                   + ("" if dpins or not d_list else '<p class="note">пины макета не показаны: у корня кадра нет числового размера</p>') + "</div>")
    else:
        out.append('<div class="pane"><h3>Макет</h3><p class="note">картинки дизайна у кадра нет, эталонный рендер не строился (daiver_render_reference)</p></div>')
    out.append(f'<div class="pane"><h3>Реализация</h3><div class="shot"><img alt="реализация" src="{actual[0]}">{"".join(apins)}{"".join(zones)}</div></div></section>')
    if data["diffImage"]:
        z = data["diff"] or {}
        out.append(f'<h2>Дифф</h2><div class="pane"><div class="shot"><img alt="дифф" src="{data["diffImage"][0]}"></div><p class="note">красное — расхождение, жёлтое — сглаживание; '
                   f'на реализации пунктиром отмечены зоны (зелёные уже объяснены дефектами). Дифф — сигнал, не вердикт. Зон: {len(z.get("zones") or [])}.</p></div>')
    # гейты
    out.append('<h2>Гейты</h2><table class="gates"><tbody>' + "".join(
        f"<tr><td>{_e(GATE_RU[g])}</td><td>{_e(data['gates'].get(g) or 'не исполнялся')}</td></tr>" for g in GATE_RU if g != "e2e") + "</tbody></table>")
    if summary.get("unverified", {}).get("summary") if isinstance(summary.get("unverified"), dict) else False:
        out.append(f'<p class="note">{_e(summary["unverified"]["summary"])}</p>')
    # дефекты
    out.append(f"<h2>Дефекты ({len(d_list)})</h2>")
    if codes:
        out.append('<div class="chips">' + "".join(f'<span class="chip" data-filter="{_e(c)}">{_e(CODES_RU.get(c, c))} · {sum(1 for d in d_list if d["code"] == c)}</span>' for c in codes) + "</div>")
    if not d_list:
        out.append('<p class="note">Дефектов нет.</p>')
    for n, d in enumerate(d_list, 1):
        dec = d.get("decision") or {}
        tags = [f'<span class="sev-{_e(d.get("severity"))}">{_e(SEVERITY_RU.get(d.get("severity"), d.get("severity")))}</span>', f'<span class="tag">{_e(CODES_RU.get(d["code"], d["code"]))}</span>']
        if d.get("lifecycle"):
            tags.append(f'<span class="tag">{_e(LIFECYCLE_RU.get(d["lifecycle"], d["lifecycle"]))}</span>')
        for flag, text in (("noise", "шум"), ("declined", "отклонён человеком")):
            if d.get(flag):
                tags.append(f'<span class="tag">{text}</span>')
        if d.get("zone") == "vision":
            tags.append('<span class="tag">не подтверждено (обзор)</span>')
        if d.get("coverage", 1) > 1:
            tags.append(f'<span class="tag">×{_e(d["coverage"])}</span>')
        if d.get("source"):
            tags.append(f'<span class="tag">{_e(d["source"])}</span>')
        body = [f'<div class="defect" data-n="{n}" data-id="{_e(d["id"])}" data-code="{_e(d["code"])}"><div class="head"><span class="num">{n}</span>'
                f'<b>{_e(d.get("element") or d.get("prop") or d["code"])}</b>{" ".join(tags)}</div>']
        if d.get("prop"):
            body.append(f'<div class="note">свойство: {_e(prop_ru(d["prop"]))}</div>')
        if d.get("message"):
            body.append(f"<div>{_e(d['message'])}</div>")
        body.append(_row_table(d))
        links = []
        if d.get("design"):
            links.append("макет: " + _node_link(data["template"], d["design"]))
        if isinstance(d.get("location"), dict) and d["location"].get("file"):
            links.append("код: " + _editor(d["location"]))
        if d.get("rule"):
            links.append(f"правило: <code>{_e(d['rule'])}</code>")
        if links:
            body.append('<div class="note">' + " · ".join(links) + "</div>")
        if d.get("suppressedCount"):
            body.append(f'<div class="note">объясняет ещё находок: {_e(d["suppressedCount"])}</div>')
        if dec:
            who = f" ({_e(dec['reviewer'])})" if dec.get("reviewer") else ""
            note = f": {_e(dec['note'])}" if dec.get("note") else ""
            status_ = " — переоткрыт: значения уехали из-под решения" if dec.get("status") == "reopened" else ""
            body.append(f'<div class="note">решение человека: {_e(dec.get("verdict"))}{who}{note}{status_}</div>')
        body.append("</div>")
        out.append("".join(body))
    rev = data["review"] or {}
    if rev:
        out.append("<h2>Визуальный обзор</h2>")
        out.append(f'<p class="note">режим: {_e(rev.get("mode"))}; находки НЕПОДТВЕРЖДЁННЫЕ — они в списке выше с пометкой и чистоту не блокируют.'
                   + (" Ничего не найдено." if rev.get("nothingFound") else "") + "</p>")
    seeded = {k: {kk: vv for kk, vv in v.items() if kk in ("verdict", "note", "reviewer", "decidedAt")} for k, v in data["decisions"]["decisions"].items()}
    out.append(f'<script type="application/json" id="seeded-decisions">{script_json(seeded)}</script>')
    out.append(f'<script type="application/json" id="report-data">{script_json({"track": data["track"], "frame": data["frame"], "iteration": data["iteration"], "defects": [d["id"] for d in d_list]})}</script>')
    out.append(f"<script>{JS}</script></main></body></html>")
    return "".join(out)


def run(root: Any, track: str, frame: Optional[str], iteration_dir: Path, date: Optional[str] = None) -> Dict[str, Any]:
    """`daiver_verify_report`: пишет report.html в папку итерации. В автономном режиме не рендерится."""
    root = Path(root).resolve()
    cfg = CFG.load(root)["config"]
    if cfg["workflow"].get("mode") == "autonomous":
        return {"rendered": False, "reason": "автономный режим: report.html не рендерится — его заменяет триаж агента в verify-report.md",
                "nextStep": "пропусти шаг отчёта"}
    if date is not None and not (isinstance(date, str) and date.strip() and len(date) <= 40 and "\n" not in date):
        raise ReportError("date: одна короткая строка (например 2026-10-26) — дата входит в отчёт как вход, а не берётся из часов")
    data = collect(root, track, frame, iteration_dir)
    text = render(data, date.strip() if isinstance(date, str) else None)
    CP._atomic_write(iteration_dir / FILE, text.encode("utf-8"))
    path = (iteration_dir / FILE).relative_to(root).as_posix()
    out: Dict[str, Any] = {"rendered": True, "report": path, "stateRecorded": data["recorded"], "defects": len(data["defects"]), "bytes": len(text.encode("utf-8")),
                           "filters": sorted({d["code"] for d in data["defects"]}), "ground": data["ground"],
                           "nextStep": f"ПУТЬ ОТЧЁТА — {path} — сообщи пользователю ВСЕГДА, до любых слов о результатах; он решает по картинкам, а не по твоему пересказу"}
    if not data["recorded"]:
        out["nextStep"] = "итерация НЕ записана (stateRecorded: false): сначала daiver_verify_state_record, потом снова отчёт. " + out["nextStep"]
    if data["decisions"]["problems"]:
        out["decisionProblems"] = data["decisions"]["problems"]
    return out
