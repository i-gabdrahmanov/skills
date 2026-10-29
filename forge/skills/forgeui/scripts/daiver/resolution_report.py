"""resolution_report.py — `resolution-report.md` и политика подтверждений резолва (спека §10.5, §16; INV-C6, C7, F7;
задача 2.5).

Что делает. Собирает из машинных хранилищ вердиктов (`resolutions.json` кадров, задача 2.4) ОДИН markdown-отчёт на трек —
`.daiver/tracks/<KEY>/resolution-report.md`: секция `## frame: <имя>` на кадр, в ней таблица ЭВРИСТИЧЕСКИХ вердиктов (резолвер и
копии по INV-B23; попадания в карту сюда не входят — их решил человек правкой карты), каждый узел — ссылка `[id](url)` по шаблону
`nodeUrlTemplate` из outline (1.8: Pixso — подмена `item-id`, Figma — `node-id`; адреса нет — голый id, URL не выдумывается).
Отчёт — чистая функция хранилищ, конфига и манифеста: ни времени, ни случайности (один вход — побайтно один выход). Узлы с одним
вердиктом одного figmaName+variants схлопнуты в одну строку «×N» (экран со списком не превращается в стену одинаковых строк).
Движок отчёт не разбирает: он для человека и для lock плана (2.6 кладёт в lock его sha256).

Политика подтверждений (`workflow.confirmResolutions`, §10.5) — КОД, а не просьба в брифе. Вердикт обязан увидеть человек, если он
эвристический (запись `resolver`; копия берёт confidence эталона и спрашивается там, где живёт эталон) и: уверенность не выше порога
(`high` — спрашивается всё, `medium` — medium и low, `low` — только low), ИЛИ уверенность low (спрашивается ВСЕГДА, при любом
пороге: молчаливая low-резолюция запрещена, INV-F7), ИЛИ это иконка без маппинга, решённая как `local`, при `icons.fallback: ask`
(§10.5: угаданная библиотечная иконка и рукописный `<svg>` запрещены, а плейсхолдер при `ask` показывается человеку).
Подтверждение — согласие `ui-resolve-<KEY>` (класс согласий с цитатой пользователя, `forge_events.CONSENT_PREFIXES`), записанное
`record_approval.py --bind-file` на ЭТОТ отчёт: оно считается, только пока sha256 файла равен `bound_sha256` (любая правка вердикта,
порога или конфига меняет отчёт и снимает подтверждение). `record_approval` не пишет второе согласие по активному ключу, поэтому
устаревшее подтверждение снимает сам движок при перегенерации отчёта (`revoke_approval`), а не модель.

Автономный прогон (7.2, §15.4; режим — `run.mode_of`): вопросов нет — подтверждение не требуется, вердикты, которые человек обязан был
увидеть, отдаются списком `unconfirmed` (и остаются в planUncertainty плана); low-вердикт компонента в автономии не принимается вовсе
(`resolve.finalize_frame`: только `local`), а гейт ловит прежний low-компонент без согласия; правило карты не пишется — блок «paste-ready»
отчёта. Текст отчёта от режима не зависит (на нём держится lock плана). Что НЕ делает (граница): подпись плана и lock (2.6); поле «подтверждено» в запись вердикта НЕ пишется (форма вердикта —
контракт INV-A7, а подтверждение привязано к тексту отчёта целиком, не к узлу).

Данные образца. «Modal» (корень-фрейм и три инстанса, «Modal Header» ×2 с одинаковыми вариантами) даёт после резолва три строки
таблицы на четыре узла. Боевых экранов нет: размер таблицы на большом кадре, число вопросов на пороге medium и то, как слабая модель
читает `toConfirm`, — допущения, не проверенные данными (X.4).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import component_map as CM
from . import config as CFG
from . import outline as OL
from . import resolve as RS
from . import track as TR

REPORT_NAME = "resolution-report.md"
CONSENT_PREFIX = "ui-resolve"
RANK = {"low": 0, "medium": 1, "high": 2}
# размеры — наши решения (в спеке не названы): отчёт читает человек, ответ инструмента читает слабая модель
ASK_LINES = 20                  # строк toConfirm в ответе инструмента; остальное — в файле (`toConfirmMore`)
ASK_RATIONALE = 160             # знаков основания в строке вопроса (в таблице отчёта — полностью, ≤ 400)
NODE_LINKS = 5                  # ссылок на узлы одной группы в ячейке таблицы
TODO_LISTED = 10                # нерешённых узлов, названных в отчёте
PROPOSALS_SHOWN = 5             # предложенных правил карты в ответе инструмента


class ReportError(ValueError):
    """Отчёт построить нельзя (нет трека, канона, повреждено хранилище) или он не записался — одним сообщением."""

    def __init__(self, message: str, problems: Optional[List[str]] = None):
        super().__init__(message)
        self.problems = list(problems or [message])


# ── ключи и пути ─────────────────────────────────────────────────────────────

def consent_key(track: str) -> str:
    """Ключ подтверждения вердиктов: `ui-resolve-<KEY>` (класс согласий с цитатой пользователя)."""
    return f"{CONSENT_PREFIX}-{track}"


def report_path(root: Any, track: str) -> Path:
    """Один файл на ТРЕК (не на кадр): корень трека, рядом с track.yaml."""
    return TR.track_dir(Path(root), track) / REPORT_NAME


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── политика подтверждений ───────────────────────────────────────────────────

def confirm_reason(rec: Dict[str, Any], threshold: str, icons_fallback: str) -> Optional[str]:
    """Почему человек обязан увидеть вердикт; None — не обязан. Эталон резолвера решает по своей уверенности; правило карты
    (`map`) и копия чужого кадра (`reuse` — её эталон спрашивается там) вопроса не требуют."""
    if rec.get("source") != "resolver":
        return None
    conf = rec.get("confidence")
    if conf == "low":
        return "low — спрашивается всегда"
    if conf in RANK and RANK[conf] <= RANK[threshold]:
        return f"{conf} не выше порога {threshold}"
    if rec.get("kind") == "icon" and rec.get("decision") == "local" and icons_fallback == "ask":
        return "иконка без маппинга: icons.fallback = ask"
    return None


# ── сбор: хранилища → группы вердиктов по кадрам ─────────────────────────────

class Group:
    """Узлы кадра с одним и тем же вердиктом (один figmaName+variants — INV-B23). `head` — запись-эталон (резолвер, а не копия),
    `ids` — узлы в порядке документа."""
    __slots__ = ("head", "ids", "reason")

    def __init__(self, head: Dict[str, Any]):
        self.head = head
        self.ids: List[str] = []
        self.reason: Optional[str] = None


class FrameView:
    __slots__ = ("name", "url", "tpl", "candidates", "by_map", "groups", "todo", "stale", "heuristic")

    def __init__(self, name: str):
        self.name = name
        self.url: Optional[str] = None
        self.tpl: Optional[str] = None
        self.candidates = 0
        self.by_map = 0
        self.groups: List[Group] = []
        self.todo: List[str] = []
        self.stale = 0
        self.heuristic = 0


class Report:
    __slots__ = ("track", "threshold", "fallback", "frames", "problems", "proposals")

    def __init__(self, track: str, threshold: str, fallback: str):
        self.track = track
        self.threshold = threshold
        self.fallback = fallback
        self.frames: List[FrameView] = []
        self.problems: List[str] = []
        self.proposals: List[Dict[str, Any]] = []


def _signature(rec: Dict[str, Any]) -> str:
    return json.dumps({k: rec.get(k) for k in ("decision", "component", "props", "confidence", "rationale")},
                      sort_keys=True, ensure_ascii=False)


def _frame_view(ctx: Any, st: Any, threshold: str, fallback: str) -> FrameView:
    fv = FrameView(ctx.fname)
    fv.url = ctx.frame.get("frameUrl")
    fv.tpl = OL.node_url_template(fv.url, (ctx.env.get("daiver") or {}).get("sourceFormat"))
    fv.candidates = len(ctx.sc.cands)
    by_sig: Dict[Tuple[str, str], Group] = {}
    for c in ctx.sc.cands:
        rec = st.valid.get(c.id)
        if rec is None:
            fv.todo.append(c.id)
            if c.id in st.stale:
                fv.stale += 1
            continue
        if rec["source"] == "map" or st.hits.get(c.id) is not None:     # решает карта, что бы ни говорила запись
            fv.by_map += 1
            continue
        fv.heuristic += 1
        gid = (rec["key"], _signature(rec)) if c.reusable else (c.id, "")
        g = by_sig.get(gid)
        if g is None:
            g = by_sig[gid] = Group(rec)
            fv.groups.append(g)
        elif rec["source"] == "resolver" and g.head["source"] != "resolver":
            g.head = rec                                    # эталон группы — запись резолвера, а не копия
        g.ids.append(c.id)
    for g in fv.groups:
        g.reason = confirm_reason(g.head, threshold, fallback)
    return fv


def _proposals(root: Path, track: str, fv: FrameView) -> List[Dict[str, Any]]:
    """Правила карты, которые резолвер предложил и которых в карте ещё нет (запись хранит предложение, пока вердикт не
    перезаписан или узел не стал попаданием карты). `yaml` — ровно то, что ляжет в файл после «да» (dry-run append)."""
    out: List[Dict[str, Any]] = []
    for g in fv.groups:
        rule = g.head.get("mapRuleProposal")
        if not isinstance(rule, dict):
            continue
        item: Dict[str, Any] = {"nodeId": g.ids[0], "frame": fv.name, "rule": rule}
        try:
            item["yaml"] = CM.append(root, track, rule, True)["yaml"]
        except CM.MapError as e:
            item["invalid"] = str(e)
        out.append(item)
    return out


def build(root: Any, track: str, sync: bool = False) -> Report:
    """Отчёт по текущему состоянию диска. `sync` раздаёт эталоны между кадрами (как `daiver_resolve_candidates`) — пишет
    хранилища, поэтому только для инструмента-писателя; гейт зовёт без него и видит нераздачу как «не решено»."""
    root = Path(root)
    try:
        manifest = TR.load(root, track)
    except TR.TrackError as e:
        raise ReportError(str(e))
    if manifest is None:
        raise ReportError(f"трека «{track}» нет — сначала daiver_pixso_pull")
    if not manifest.get("frames"):
        raise ReportError(f"в треке «{track}» нет ни одного кадра — их заводит daiver_pixso_pull")
    cfg = CFG.load(root)["config"]
    rep = Report(track, cfg["workflow"]["confirmResolutions"], "ask")
    fallback_known = False
    for f in manifest["frames"]:
        try:
            ctx = RS.Ctx(root, track, f["name"])
            st = RS._sync(ctx) if sync else ctx.state(RS.read_store(ctx.root, ctx.spath))
        except RS.ResolveError as e:
            rep.problems.append(f"кадр «{f['name']}»: {e}")
            continue
        if not fallback_known:                              # карта одна на проект: fallback берётся из первого кадра
            rep.fallback = ctx.mv.icons_fallback
            fallback_known = True
        fv = _frame_view(ctx, st, rep.threshold, rep.fallback)
        rep.frames.append(fv)
        rep.proposals += _proposals(root, track, fv)
    return rep


# ── разметка ─────────────────────────────────────────────────────────────────

def _flat(s: Any) -> str:
    """Одна строка без знаков, ломающих ячейку таблицы."""
    return RS._flat(s).replace("|", "\\|").replace("`", "'")


def _link(tpl: Optional[str], node_id: str) -> str:
    """`[id](url)`; адреса нет — голый id (адрес не выдумывается, §16). Скобки в адресе кодируются: ссылка не ломается."""
    url = OL.node_url(tpl, node_id)
    if not url:
        return f"`{_flat(node_id)}`"
    return f"[{_flat(node_id)}]({url.replace('(', '%28').replace(')', '%29').replace(' ', '%20')})"


def _links(tpl: Optional[str], ids: List[str], limit: int = NODE_LINKS) -> str:
    shown = ", ".join(_link(tpl, i) for i in ids[:limit])
    return shown + (f" … +{len(ids) - limit}" if len(ids) > limit else "")


def _label(rec: Dict[str, Any], count: int) -> str:
    """Что за узел: figmaName с вариантами (имя слоя инстанса — строка вариантов, компонент не называет)."""
    kind = rec.get("kind")
    if rec.get("figmaName"):
        text = _flat(rec["figmaName"])
        if rec.get("variants"):
            text += " " + _flat(RS._variants_text(rec["variants"]))
        if kind == "icon":
            text = "иконка " + text
    else:
        what = {"frame": "фрейм", "instance": "инстанс"}.get(kind, kind)
        text = f"{what} «{_flat(rec.get('name') or rec['nodeId'])}»"
    return text + (f" ×{count}" if count > 1 else "")


def _decision(rec: Dict[str, Any]) -> str:
    d = rec["decision"]
    if d == "component":
        text = f"component `{_flat(rec['component'])}`"
        if rec.get("props"):
            text += " `" + _flat(json.dumps(rec["props"], ensure_ascii=False, sort_keys=True)) + "`"
        return text
    if d == "local" and rec.get("kind") == "icon":
        return "local — плейсхолдер иконки"
    return d


def _origin(rec: Dict[str, Any]) -> str:
    if rec["source"] == "resolver":
        return "резолвер"
    if rec.get("fromFrame"):
        return f"копия: кадр «{_flat(rec['fromFrame'])}», узел `{_flat(rec.get('from') or '')}`"
    return f"копия узла `{_flat(rec.get('from') or '')}`"


def _frame_section(fv: FrameView, proposals: List[Dict[str, Any]]) -> List[str]:
    out = [f"## frame: {fv.name}", ""]
    out.append("Макет: " + (fv.url if fv.url else "адрес кадра не записан") +
               ("" if fv.tpl else " — ссылок на узлы нет, вместо них голые id (адрес не выдумывается)"))
    out.append("")
    counts = (f"Кандидатов: {fv.candidates} · по карте: {fv.by_map} · эвристических: {fv.heuristic} (строк {len(fv.groups)})"
              f" · не решено: {len(fv.todo)}")
    out += [counts, ""]
    if fv.groups:
        out += ["| Узлы | Макет | Решение | Уверенность | Основание | Откуда | Спросить |", "|---|---|---|---|---|---|---|"]
        for g in fv.groups:
            r = g.head
            ask = "да" if g.reason else "—"
            out.append(f"| {_links(fv.tpl, g.ids)} | {_label(r, len(g.ids))} | {_decision(r)} | {r['confidence']} | "
                       f"{_flat(r['rationale'])} | {_origin(r)} | {ask} |")
        out.append("")
    else:
        out += ["Эвристических вердиктов нет.", ""]
    if fv.todo:
        out += [f"Не решено ({len(fv.todo)}): {_links(fv.tpl, fv.todo, TODO_LISTED)}", ""]
    mine = [p for p in proposals if p["frame"] == fv.name]
    if mine:
        out += ["### Предложенные правила карты (в карте их нет)", ""]
        for p in mine:
            out.append(f"Узел {_link(fv.tpl, p['nodeId'])}:")
            out.append("")
            if "yaml" in p:
                out += ["```yaml", p["yaml"], "```", ""]
            else:
                out += [f"не проходит проверку карты: {_flat(p['invalid'])}", ""]
    return out


def render(rep: Report) -> str:
    """Текст отчёта. Чистая функция отчёта: ни времени, ни окружения."""
    out = [f"# Отчёт резолва — {rep.track}", "",
           "Эвристические вердикты резолва: компонент выбран не по правилу карты (`component-map.yaml`), а по библиотеке или по "
           "составу узла. Попадания в карту сюда не входят. Файл строит движок (`daiver_resolve_report`), руками не правится.",
           "",
           f"Порог подтверждения (`workflow.confirmResolutions`): {rep.threshold} — вердикты с уверенностью {rep.threshold} и "
           f"ниже показываются человеку, low — всегда; иконка без маппинга при `icons.fallback: ask` — тоже.", ""]
    for fv in rep.frames:
        out += _frame_section(fv, rep.proposals)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# ── что спросить человека ────────────────────────────────────────────────────

def to_confirm(rep: Report) -> List[str]:
    """Строки вопроса: вердикты, которые человек обязан увидеть. Порядок — low, medium, high (low не отрежется капом), внутри —
    кадры по манифесту и порядок документа. Строка готова к вставке в вопрос: ссылка, макет, решение, уверенность, основание."""
    items: List[Tuple[int, int, int, str]] = []
    multi = len(rep.frames) > 1
    for fi, fv in enumerate(rep.frames):
        for gi, g in enumerate(fv.groups):
            if not g.reason:
                continue
            r = g.head
            icon_note = " (иконка без маппинга, icons.fallback: ask)" if g.reason.startswith("иконка") else ""
            proposal = " · предложено правило карты" if isinstance(r.get("mapRuleProposal"), dict) else ""
            line = (f"{'[' + fv.name + '] ' if multi else ''}{_link(fv.tpl, g.ids[0])} {_label(r, len(g.ids))} → "
                    f"{_decision(r)} · {r['confidence']}{icon_note} — {RS._clip(_flat(r['rationale']), ASK_RATIONALE)}"
                    f"{proposal}")
            items.append((RANK.get(r["confidence"], 3), fi, gi, line))
    items.sort(key=lambda x: x[:3])
    return [x[3] for x in items]


# ── подтверждение человеком ──────────────────────────────────────────────────

def _hooks() -> Tuple[Any, Any]:
    return CM._hooks()


def consent_state(root: Path, track: str, sha: Optional[str]) -> Dict[str, Any]:
    """`none` — согласия нет; `ok` — есть и привязано к ЭТОМУ отчёту; `stale` — привязано к другому тексту; `unbound` —
    записано без `--bind-file` (на содержимое не опирается, не годится)."""
    FE, _P = _hooks()
    key = consent_key(track)
    rec = FE.approval(Path(root), key)
    if rec is None:
        state = "none"
    elif not rec.get("bound_sha256"):
        state = "unbound"
    elif sha is not None and rec["bound_sha256"] == sha:
        state = "ok"
    else:
        state = "stale"
    return {"key": key, "state": state}


def _write_atomic(root: Path, path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)                                # байтами: без перевода строк платформы
        os.replace(str(tmp), str(path))
    except OSError as e:
        raise ReportError(f"запись {_rel(root, path)} не удалась ({e}); отчёт не обновлён")
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _frame_line(fv: FrameView) -> str:
    return (f"{fv.name}: кандидатов {fv.candidates} · по карте {fv.by_map} · эвристических {fv.heuristic} · "
            f"не решено {len(fv.todo)}")


def write(root: Any, track: str) -> Dict[str, Any]:
    """`daiver_resolve_report`: перегенерировать отчёт, раздать эталоны между кадрами, снять устаревшее подтверждение и сказать,
    что спросить человека. Вердикты не меняет, согласия не пишет."""
    root = Path(root)
    rep = build(root, track, sync=True)
    if rep.problems:
        raise ReportError("отчёт не построен: " + "; ".join(rep.problems), rep.problems)
    data = render(rep).encode("utf-8")
    path = report_path(root, track)
    old = path.read_bytes() if path.is_file() else None
    written = old != data
    if written:
        _write_atomic(root, path, data)
    sha = sha256_bytes(data)
    consent = consent_state(root, track, sha)
    revoked = False
    if consent["state"] in ("stale", "unbound"):             # новое согласие по активному ключу record_approval не запишет
        FE, _P = _hooks()
        why = "отчёт резолва изменился" if consent["state"] == "stale" else "согласие записано без привязки к отчёту"
        FE.revoke_approval(root, consent["key"], reason=f"подтверждение снято: {why}")
        revoked = True
        consent = {"key": consent["key"], "state": "none"}
    todo = sum(len(fv.todo) for fv in rep.frames)
    out: Dict[str, Any] = {"track": track, "report": _rel(root, path), "written": written,
                           "frames": [_frame_line(fv) for fv in rep.frames], "unresolved": todo,
                           "policy": f"confirmResolutions={rep.threshold}; low спрашивается всегда; "
                                     f"icons.fallback={rep.fallback}"}
    if revoked:
        out["consentRevoked"] = "отчёт изменился после подтверждения — прежнее согласие снято, спросить заново"
    if todo:
        names = ", ".join(fv.name for fv in rep.frames if fv.todo)
        out["next"] = (f"не решено кандидатов: {todo} (кадры: {names}) — daiver_resolve_candidates → daiver_resolve_query → "
                       f"daiver_resolve_finalize; потом снова этот инструмент. Человека пока не спрашивай")
        return out
    asked = to_confirm(rep)
    out["consent"] = consent
    from . import run as RUN
    if RUN.mode_of(root, track)[0] == "autonomous":
        out["autonomous"] = True
        out["unconfirmed"] = asked[:ASK_LINES]
        if len(asked) > ASK_LINES:
            out["unconfirmedMore"] = len(asked) - ASK_LINES
        if rep.proposals:
            out["proposals"] = rep.proposals[:PROPOSALS_SHOWN]
        out["confirmed"] = consent["state"] == "ok" or not asked
        out["next"] = ("автономный прогон: вопросов нет — вердикты из unconfirmed приняты без подтверждения человека (названы в плане), low — только "
                       "local; правила карты не пишутся (paste-ready блок в отчёте); резолв закончен, дальше layoutHints")
        return out
    if rep.proposals:
        out["proposals"] = rep.proposals[:PROPOSALS_SHOWN]
        if len(rep.proposals) > PROPOSALS_SHOWN:
            out["proposalsMore"] = len(rep.proposals) - PROPOSALS_SHOWN
    out["toConfirm"] = asked[:ASK_LINES]
    if len(asked) > ASK_LINES:
        out["toConfirmMore"] = len(asked) - ASK_LINES
    confirmed = not asked or consent["state"] == "ok"
    out["confirmed"] = confirmed
    if rep.proposals:
        out["next"] = ("сначала предложенные правила карты (proposals): по одному вопросу пользователю — rule и yaml дословно; "
                       "«нет» и предложение с invalid (вопроса нет) — перезапиши вердикт узла без mapRuleProposal. Подтверждение "
                       "вердиктов — после, на свежем отчёте (правило меняет вердикты узлов, и отчёт тоже)")
    elif not asked:
        out["next"] = "вердиктов, которые обязан увидеть человек, нет — резолв закончен; дальше layoutHints"
    elif confirmed:
        out["next"] = "вердикты подтверждены человеком (согласие привязано к текущему отчёту) — резолв закончен; дальше layoutHints"
    else:
        tail = f" Остальное ({len(asked) - ASK_LINES}) — в файле отчёта: назови его путь в вопросе." if len(asked) > ASK_LINES else ""
        out["next"] = ("верни needs_input: вопрос kind=confirm-resolutions с строками toConfirm дословно (low первыми); согласие "
                       f"{consent['key']} запишет оркестратор после «да». «Правки» — исправь вердикты (daiver_resolve_finalize) "
                       "и вызови этот инструмент снова; согласие сам не выписывай." + tail)
    return out


# ── проверка финализации (гейт ui-design, read-only) ─────────────────────────

def check(root: Any, track: str) -> List[str]:
    """Что мешает считать резолв законченным и подтверждённым; пусто — можно идти к плану. Только читает диск: эталоны между кадрами
    не раздаёт (нераздача читается как «не решено» и лечится `daiver_resolve_report`)."""
    root = Path(root)
    try:
        rep = build(root, track, sync=False)
    except ReportError as e:
        return [str(e)]
    problems = list(rep.problems)
    for fv in rep.frames:
        if fv.todo:
            stale = f", из них устарели {fv.stale}" if fv.stale else ""
            problems.append(f"кадр «{fv.name}»: не решено кандидатов {len(fv.todo)}{stale} — daiver_resolve_candidates → "
                            f"daiver_resolve_query → daiver_resolve_finalize")
    if problems:
        return problems
    path = report_path(root, track)
    want = render(rep).encode("utf-8")
    if not path.is_file():
        return ["отчёта резолва нет — daiver_resolve_report"]
    have = path.read_bytes()
    if have != want:
        return ["отчёт резолва устарел (вердикты, порог или карта изменились после записи) — daiver_resolve_report"]
    asked = to_confirm(rep)
    from . import run as RUN
    auto = RUN.mode_of(root, track)[0] == "autonomous"
    if asked:
        st = consent_state(root, track, sha256_bytes(have))
        if auto and st["state"] != "ok":
            low = [f"{fv.name}: {', '.join(g.ids[:5])}" for fv in rep.frames for g in fv.groups
                   if g.reason and g.head.get("confidence") == "low" and g.head.get("decision") == "component"]
            if low:
                problems.append(f"автономный прогон: low-вердикты компонента без подтверждения человека ({'; '.join(low)}) — в автономии только "
                                f"local: перезапиши их daiver_resolve_finalize с decision: local")
        elif st["state"] != "ok":
            why = {"none": "согласия нет", "stale": "согласие привязано к другому тексту отчёта",
                   "unbound": "согласие записано без --bind-file"}[st["state"]]
            problems.append(f"вердикты, которые обязан увидеть человек ({len(asked)}), не подтверждены: {why} "
                            f"({st['key']}). Порядок: daiver_resolve_report → вопрос пользователю со строками toConfirm → "
                            f"после «да» record_approval.py --key {st['key']} --bind-file {_rel(root, path)} "
                            f"--evidence \"<ответ дословно>\"")
    return problems
