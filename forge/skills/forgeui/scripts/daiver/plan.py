"""plan.py — plan.json и plan.md кадра: дерево секций, решения по узлам, раскладка и buildOrder (спека §11.5, §12.2–12.4, §9.2;
задача 2.6; INV-A1, A11, B12, C9, F6).

План — контракт для сборки, и пишет его КОД, а не модель файлом: модель в `daiver_plan_write` называет только то, что не вычисляется
(решения по раскладке `unproven`; при желании — свой buildOrder, который код ПРОВЕРЯЕТ), а всё остальное берётся из диска и
считается: секции и их хеши — из канона (`outline`, тот же бюджет), решение по каждому кандидату — из подтверждённого резолва
(`resolutions.json`, карта компонентов), порядок сборки — арифметикой по общим локальным компонентам, спеки — из `daiver_spec`.
Один вход — побайтно один выход: ни времени, ни случайности (план входит в lock, который подписывает человек).

Что в `plan.json` (schema 1): `pins` (sha256 требования и `rootHash` канона — на чём построен), `weightBudget` (БЕЗ него id секций
чужие: `daiver_section` режет по бюджету, и сборка обязана брать тот же), `tree` (оболочка над секциями — контейнеры с дайджестом
раскладки layout/size/box — и ссылки `{section: id}`), `sections[]` (id, hash, вес, дайджест раскладки корня секции, `nodes[]` —
решения по кандидатам: `{component, props}` | `{resolution: local}` | `{resolution: ignore}` + confidence и откуда вердикт),
`state` и `delta` (ТОЛЬКО у кадра-состояния `stateOf`, 6.2: переменная состояния, её значения у экрана и у состояния, переключатель — из `triggers` или названный; `delta` — что состояние убирает и что берёт у экрана готовым;
секции такого плана — регионы added и changed дельты к экрану, а не страница),
`baseline` (ТОЛЬКО у ревизии, 6.3: `{kind: version, version, similarity, removed: [{baseId, name, parentId, files}], inherited: [{id, baseId, component | resolution}]}` —
на какой собранной версии стоит изменение, что удалить из кода и из каких файлов, решения узлов shared-частей, взятые у версии по base-id; секции — регионы
дельты к версии, `delta` — как у состояния; место корня не-первичного кадра (`module`) по умолчанию берётся у версии),
`module` (ТОЛЬКО у не-первичного кадра без `stateOf`: заявленное решение плана о месте корня кадра в едином модуле трека — {name: имя первичного кадра, root: {mode, component, host?, reason?}}, 6.1),
`buildOrder` (РЕАЛЬНЫЕ id секций с хешами: сначала секции, создающие общие local-компоненты, затем их потребители, дальше порядок
документа; occluded не строится; пролог — токены и моки — не секция и в порядок не входит, INV-B12), `locals`, `layout` (решение по
каждой записи `unproven`, §11.4: «принятый hint обязателен, непринятый не применяется»), `specs` (ссылки на разделы спек), `design`
(токены макета и цвета без токена), `uncertain` (узлы, в которых план не уверен), `stats`.

Общий local-компонент — инстанс с решением `local`, встретившийся ≥ 2 раз с одним figmaName+variants (INV-B23) либо названный
правилом карты (`outcome: local` + `component`: имя в вердикт не входит, его берёт план, 2.4). Имя без правила карты — PascalCase
латинских слов figmaName; нет латиницы — `Local<N>`; совпадение имён разных ключей — числовой суффикс (имя НЕ выдумывается моделью,
но и не с неба: от figmaName детерминированно).

Проверка плана (`check_frame`) — не «файл читается», а «файл равен тому, что строит движок из текущих данных»: план пересобирается
из канона, вердиктов, карты и спек с теми же решениями по раскладке (они лежат в самом плане) и сравнивается побайтно. Перетянутое
требование, пересобранный канон, правка вердикта или карты, правка файла руками — план `stale` с названной причиной. Неразбираемый
план — `open`, а не «проходит».

Допущения, не проверенные данными (боевых экранов и планов нет): правило общих local-компонентов и их имён; ровно три яруса
buildOrder (создатели → потребители → остальное); решения по раскладке и геометрия групп (gap/box из боксов канона в допуске 1 px
— `LAYOUT_TOLERANCE`, §20.4); состав `design` (токены макета и цвета без привязанного токена; сверка с `project.styling.colorsFile`
— при сборке, 3.2); `stateOf`-план (6.2) — регионы дельты и `plan.state`; ревизия (6.3) — регионы дельты к собранной версии и `baseline`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import baseline as BL
from . import canon as C
from . import component_map as CM
from . import outline as OL
from . import resolve as RS
from . import specs as S
from . import track as TR

SCHEMA = 1
LAYOUT_TOLERANCE = 1                  # §20.4: допуск реплея раскладки, px
LAYOUT_REST = "keep-px"
EVIDENCE = ("declaration", "goal-image", "user", "none")
LAYOUT_DECISIONS = {OL.HINTS: ("apply", "keep-px"), OL.ABSOLUTE: ("keep-px", "group")}
GROUP_DIRECTIONS = ("row", "column")
INPUT_KEYS = ("layout", "layoutRest", "buildOrder", "placement", "state")
STATE_KEYS = ("variable", "screenValue", "value", "toggle")
_VARIABLE_RE = re.compile(r"^[a-z][A-Za-z0-9]{0,31}$")
PLACEMENT_MODES = ("own-file", "inside-primary")
PLACEMENT_KEYS = ("mode", "component", "host", "reason")
_COMPONENT_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,63}$")
LAYOUT_KEYS = ("id", "reason", "decision", "evidence", "direction", "groups", "note")
BUILD_ORDER_KEYS = ("id", "hash", "name")
WHY_MAX = 160                         # знаков основания local/ignore в плане (полностью — в resolution-report.md)
NOTE_MAX = 200
SHOWN = 10                            # сколько id называет отказ
LIST_CAP = 40                         # токены и цвета макета в плане: не больше (остальное — счётчиком)

_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")
_NAME_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PlanError(ValueError):
    """План построить нельзя или входной план не принят — одним сообщением (все нарушения сразу), файлы не тронуты."""

    def __init__(self, message: str, problems: Optional[List[str]] = None):
        super().__init__(message)
        self.problems = list(problems or [message])


def refusal(head: str, problems: List[str], tail: str = "") -> PlanError:
    body = "; ".join(f"{i}) {t}" for i, t in enumerate(problems[:8], 1))
    more = f"; …и ещё {len(problems) - 8}" if len(problems) > 8 else ""
    return PlanError(f"{head} ({len(problems)}): {body}{more}.{(' ' + tail) if tail else ''}", problems)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(obj: Any) -> str:
    """Файл плана: ключи отсортированы, отступ 1, перевод строки в конце — как у resolutions.json."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def _listed(items: List[str], limit: int = SHOWN) -> str:
    return ", ".join(items[:limit]) + (f" … +{len(items) - limit}" if len(items) > limit else "")


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def write_atomic(root: Path, path: Path, data: bytes, what: str) -> None:
    """Атомарная запись байтами (tmp + replace): без перевода строк платформы, недописанный файл не виден."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(str(tmp), str(path))
    except OSError as e:
        raise PlanError(f"запись {_rel(root, path)} не удалась ({e}); {what} не обновлён")
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


# ── спеки: ссылки на разделы (§11.5) ─────────────────────────────────────────

StoryResolver = Callable[[str], Optional[Path]]


def spec_files(root: Path, track: str, manifest: dict, story_spec: Optional[StoryResolver]) -> Tuple[List[Path], List[Path]]:
    """(найденные, зарегистрированные) файлы спек трека — те же, что собирает `daiver_spec`: ui-spec.md стори + `specs[]` манифеста.
    Отсутствующий ui-spec.md стори уходит в зарегистрированные: parse_files назовёт его предупреждением."""
    story = story_spec(track) if story_spec is not None else None
    found: List[Path] = []
    registered: List[Path] = [Path(root) / rel for rel in manifest.get("specs") or []]
    if story is not None:
        story = Path(story)
        (found if story.is_file() else registered).append(story)
    return found, registered


def specs_digest(root: Path, track: str, manifest: dict, story_spec: Optional[StoryResolver]) -> Dict[str, Any]:
    """Ссылки плана на спеки — только то, что есть в артефактах: файлы с sha256, разделы логики (файл, заголовок, строка), сценарии
    e2e (id, заголовок), пути типов/клиента из «Данных». Тел разделов в плане нет."""
    found, registered = spec_files(root, track, manifest, story_spec)
    parsed = S.parse_files(found, registered=registered, root=Path(root))
    out: Dict[str, Any] = {
        "hasSpecs": bool(parsed["hasSpecs"]),
        "files": sorted(({"path": f["path"], "sha256": f["sha256"]} for f in parsed["files"] if f["parsed"]),
                        key=lambda f: f["path"]),
        "missing": sorted(f["path"] for f in parsed["files"] if not f["exists"]),
    }
    for key in ("data", "states", "events", "validations"):
        out[key] = [{"file": b["file"], "heading": b["heading"], "line": b["line"]}
                    for b in parsed[key] if not b.get("empty")]
    out["e2e"] = [{"id": sc["id"], "title": RS._clip(RS._flat(sc.get("title") or ""), 120)} for sc in parsed["e2e"]]
    out["dataRefs"] = sorted({(r["kind"], r["path"]) for r in parsed["dataRefs"]})
    out["dataRefs"] = [{"kind": k, "path": p} for k, p in out["dataRefs"]]
    out["mocks"] = bool(parsed["mocks"])
    return out


# ── вход модели ──────────────────────────────────────────────────────────────

def check_input(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Форма входного плана: `{layout?: [...], layoutRest?: "keep-px", buildOrder?: [{id, hash}]}`. Остальное — нарушение."""
    problems: List[str] = []
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {}, ["plan: ожидается объект {layout?, layoutRest?, buildOrder?, placement?, state?}"]
    for k in raw:
        if k not in INPUT_KEYS:
            problems.append(f"plan.{k}: неизвестное поле (допустимы {', '.join(INPUT_KEYS)}); дерево секций, решения по узлам, "
                            f"хеши и порядок сборки код строит сам — их не пишут")
    layout = raw.get("layout")
    if layout is not None and not (isinstance(layout, list) and all(isinstance(x, dict) for x in layout)):
        problems.append("plan.layout: ожидается список объектов {id, reason, decision, evidence, …}")
        layout = None
    rest = raw.get("layoutRest")
    if rest is not None and rest != LAYOUT_REST:
        problems.append(f"plan.layoutRest: допустимо только «{LAYOUT_REST}» (остальные контейнеры из unproven остаются px без "
                        f"свидетельства и попадают в planUncertainty)")
        rest = None
    order = raw.get("buildOrder")
    if order is not None and not (isinstance(order, list) and all(isinstance(x, dict) for x in order)):
        problems.append("plan.buildOrder: ожидается список {id, hash} (необязательно — код считает порядок сам)")
        order = None
    placement = raw.get("placement")
    if placement is not None:
        bad = placement_shape_problems(placement)
        if bad:
            problems += bad
            placement = None
    state = raw.get("state")
    if state is not None:
        bad = state_shape_problems(state)
        if bad:
            problems += bad
            state = None
    return {"layout": layout or [], "layoutRest": rest, "buildOrder": order, "placement": placement, "state": state}, problems


def state_shape_problems(s: Any) -> List[str]:
    """Форма `state` (план кадра-состояния): {variable, screenValue, value, toggle: {trigger: id} | {named: текст}}."""
    if not isinstance(s, dict):
        return ["plan.state: ожидается объект {variable, screenValue, value, toggle}"]
    out = [f"plan.state.{k}: неизвестное поле (допустимы {', '.join(STATE_KEYS)})" for k in s if k not in STATE_KEYS]
    if not (isinstance(s.get("variable"), str) and _VARIABLE_RE.match(s["variable"])):
        out.append("plan.state.variable: имя переменной состояния латиницей в camelCase (decision, step, isOpen)")
    for k in ("screenValue", "value"):
        v = s.get(k)
        if not (isinstance(v, str) and v.strip() and len(v) <= 40 and "\n" not in v):
            out.append(f"plan.state.{k}: короткая строка — значение переменной ({'на экране' if k == 'screenValue' else 'в этом состоянии'}), например yes | no")
    if isinstance(s.get("value"), str) and s.get("value") == s.get("screenValue"):
        out.append("plan.state: value и screenValue одинаковы — переключать нечего")
    t = s.get("toggle")
    if not isinstance(t, dict) or set(t) not in ({"trigger"}, {"named"}) or not isinstance(next(iter(t.values())), str) or not next(iter(t.values())).strip():
        out.append("plan.state.toggle: ровно одно из {trigger: id из triggers кадра-состояния} | {named: что переключает, названное пользователем}")
    return out


def state_of(ctx: "RS.Ctx", given: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """План состояния кадра-состояния: переменная, значения, переключатель. Обязателен у `stateOf`-кадра, чужд остальным. → (state | None, нарушения)."""
    st = ctx.env.get("state")
    if not ctx.frame.get("stateOf"):
        return None, (["plan.state: поле только у кадра-состояния (stateOf) — у этого кадра его нет"] if given is not None else [])
    if given is None:
        trig = ", ".join(t["id"] for t in (st or {}).get("triggers", [])[:5]) or "кандидатов нет — назови переключатель словами"
        return None, [f"plan.state обязателен у кадра-состояния: {{variable, screenValue, value, toggle: {{trigger: id}} | {{named: …}}}}; кандидаты-переключатели экрана: {trig}"]
    out = {"screen": ctx.frame["stateOf"], "variable": given["variable"], "values": {"screen": given["screenValue"], "state": given["value"]}}
    problems: List[str] = []
    if "trigger" in given["toggle"]:
        tid = given["toggle"]["trigger"]
        hit = next((t for t in (st or {}).get("triggers", []) if t["id"] == tid), None)
        if hit is None:
            problems.append(f"plan.state.toggle.trigger: «{tid}» нет среди переключателей-кандидатов (daiver_frame_states → triggers); названного пользователем — toggle.named")
        else:
            out["toggle"] = {"kind": "trigger", "id": tid, "name": hit["name"]}
    else:
        out["toggle"] = {"kind": "named", "name": " ".join(given["toggle"]["named"].split())[:WHY_MAX]}
    return out, problems


def placement_shape_problems(p: Any) -> List[str]:
    """Форма `placement` (решение о месте корня не-первичного кадра в модуле): {mode: own-file | inside-primary, component?, host?, reason?}."""
    if not isinstance(p, dict):
        return ["plan.placement: ожидается объект {mode, component?, host?, reason?}"]
    out = [f"plan.placement.{k}: неизвестное поле (допустимы {', '.join(PLACEMENT_KEYS)})" for k in p if k not in PLACEMENT_KEYS]
    if p.get("mode") not in PLACEMENT_MODES:
        out.append(f"plan.placement.mode: {' | '.join(PLACEMENT_MODES)} — own-file: корень кадра отдельным компонентом в каталоге модуля; "
                   f"inside-primary: внутри компонента первичного кадра (нужен host)")
    comp = p.get("component")
    if comp is not None and not (isinstance(comp, str) and _COMPONENT_RE.match(comp)):
        out.append("plan.placement.component: имя компонента латиницей в PascalCase (Dialog, EditForm)")
    host = p.get("host")
    if host is not None and not (isinstance(host, str) and host.strip()):
        out.append("plan.placement.host: id узла первичного кадра, внутри которого рисуется корень (строка)")
    if p.get("mode") == "inside-primary" and not host:
        out.append("plan.placement.host: для inside-primary назови узел первичного кадра (его id из канона), в который встроен корень")
    if p.get("mode") == "own-file" and host is not None:
        out.append("plan.placement.host: host бывает только у inside-primary (own-file — отдельный файл модуля)")
    reason = p.get("reason")
    if reason is not None and not (isinstance(reason, str) and reason.strip()):
        out.append("plan.placement.reason: одна строка — почему так")
    return out


def host_ids(root: Path, manifest: dict, primary: str) -> Optional[set]:
    """id узлов канона первичного кадра (для проверки `placement.host`); None — канона первичного нет (проверить нечем)."""
    try:
        path = TR.frame_paths(Path(root), manifest, primary)["canonical"]
        if not path.is_file():
            return None
        return {n["id"] for n in C.iter_nodes(C.read_canonical(path)["root"]) if isinstance(n.get("id"), str)}
    except (C.CanonError, OSError, KeyError):
        return None


def module_of(ctx: "RS.Ctx", placement: Optional[Dict[str, Any]], hosts: Optional[set]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Решение о месте корня кадра в модуле: только у не-первичного кадра без stateOf (состояние живёт ВНУТРИ экрана — 6.2). → (module | None, нарушения)."""
    frames = ctx.manifest.get("frames") or []
    primary = frames[0]["name"] if frames else ctx.fname
    if ctx.fname == primary or ctx.frame.get("stateOf"):
        if placement is not None:
            why = "у первичного кадра корень и есть модуль" if ctx.fname == primary else "кадр-состояние строится ВНУТРИ экрана (stateOf), место корня ему не нужно"
            return None, [f"plan.placement: {why} — поле не принимается"]
        return None, []
    problems: List[str] = []
    mode = (placement or {}).get("mode", "own-file")
    comp = (placement or {}).get("component") or _pascal(ctx.fname) or "Frame"
    if not _COMPONENT_RE.match(comp):
        comp = "Frame" + comp
    root: Dict[str, Any] = {"mode": mode, "component": comp}
    host = (placement or {}).get("host")
    if host:
        root["host"] = host
        if hosts is not None and host not in hosts:
            problems.append(f"plan.placement.host: узла «{host}» нет в каноне первичного кадра «{primary}» — host берётся из его канона (daiver_outline / daiver_section)")
        elif hosts is None:
            problems.append(f"plan.placement.host: у первичного кадра «{primary}» нет канона — проверить host нечем (сначала daiver_normalize первичного кадра)")
    if (placement or {}).get("reason"):
        root["reason"] = " ".join(placement["reason"].split())[:WHY_MAX]
    return {"name": primary, "root": root}, problems


def _layout_entry_problems(i: int, d: Dict[str, Any], by_key: Dict[Tuple[str, str], dict], m: "OL._Map"
                           ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Одно решение по раскладке: форма и сверка с записью unproven. → (нормализованное решение | None, нарушения)."""
    where = f"layout[{i}]"
    problems = [f"{where}.{k}: неизвестное поле (допустимы {', '.join(LAYOUT_KEYS)})" for k in d if k not in LAYOUT_KEYS]
    nid, reason, decision, evidence = d.get("id"), d.get("reason"), d.get("decision"), d.get("evidence")
    if not (isinstance(nid, str) and nid):
        problems.append(f"{where}.id: id узла из unproven daiver_outline")
    if reason not in LAYOUT_DECISIONS:
        problems.append(f"{where}.reason: {reason!r} — {OL.ABSOLUTE} | {OL.HINTS} (как в записи unproven)")
    if evidence not in EVIDENCE:
        problems.append(f"{where}.evidence: {evidence!r} — {' | '.join(EVIDENCE)} (на чём решение: декларация дизайнера из "
                        f"daiver_source_nodes, картинка кадра, ответ пользователя, либо нет свидетельства)")
    if problems:
        return None, problems
    entry = by_key.get((nid, reason))
    if entry is None:
        return None, [f"{where}: узла «{nid}» с причиной «{reason}» в unproven кадра нет — решения берутся только для записей "
                      f"daiver_outline.unproven (id не придумываются)"]
    if decision not in LAYOUT_DECISIONS[reason]:
        return None, [f"{where}.decision: для причины «{reason}» допустимо {' | '.join(LAYOUT_DECISIONS[reason])}, получено {decision!r}"]
    out: Dict[str, Any] = {"id": nid, "reason": reason, "decision": decision, "evidence": evidence}
    if decision in ("apply", "group") and evidence == "none":
        problems.append(f"{where}: решение «{decision}» без свидетельства не принимается — px остаётся px, пока нет декларации "
                        f"дизайнера (daiver_source_nodes), картинки (только для группировки) или ответа пользователя")
    note = d.get("note")
    if note is not None:
        if isinstance(note, str) and len(RS._flat(note)) <= NOTE_MAX:
            out["note"] = RS._flat(note)
        else:
            problems.append(f"{where}.note: строка до {NOTE_MAX} знаков")
    if decision == "apply":
        hints = m.by_id[nid].get("layoutHints")
        out["hints"] = dict(hints) if isinstance(hints, dict) else {}
        for k in ("direction", "groups"):
            if k in d:
                problems.append(f"{where}.{k}: только при decision=group")
    elif decision == "group":
        problems += _group_problems(where, d, m.by_id[nid], out)
    else:
        for k in ("direction", "groups"):
            if k in d:
                problems.append(f"{where}.{k}: только при decision=group")
    return (None if problems else out), problems


def _group_problems(where: str, d: Dict[str, Any], node: dict, out: Dict[str, Any]) -> List[str]:
    """Группировка детей контейнера (§11.4 п.2): ряд/колонка из названных детей; gap и бокс считает код по боксам канона."""
    direction, groups = d.get("direction"), d.get("groups")
    problems: List[str] = []
    if direction not in GROUP_DIRECTIONS:
        problems.append(f"{where}.direction: row | column")
    if not (isinstance(groups, list) and groups and all(isinstance(g, list) and all(isinstance(x, str) for x in g)
                                                         for g in groups)):
        problems.append(f"{where}.groups: непустой список групп — списков id детей контейнера")
        return problems
    if problems:
        return problems
    kids = {k["id"]: k for k in OL._kids(node)}
    seen: Dict[str, int] = {}
    computed: List[Dict[str, Any]] = []
    for gi, g in enumerate(groups):
        if len(g) < 2:
            problems.append(f"{where}.groups[{gi}]: в группе от двух узлов (один узел — не ряд и не колонка)")
            continue
        missing = [x for x in g if x not in kids]
        if missing:
            problems.append(f"{where}.groups[{gi}]: {_listed(missing, 5)} — не дети узла «{node['id']}» (группируются только его "
                            f"прямые дети из children записи unproven)")
            continue
        dup = [x for x in g if x in seen]
        if dup or len(set(g)) != len(g):
            problems.append(f"{where}.groups[{gi}]: узел в двух группах или дважды в одной ({_listed(dup or g, 5)})")
            continue
        for x in g:
            seen[x] = gi
        res = _group_geometry([kids[x] for x in g], direction)
        if isinstance(res, str):
            problems.append(f"{where}.groups[{gi}]: {res}")
        else:
            computed.append(res)
    if not problems:
        out["direction"] = direction
        out["groups"] = computed
    return problems


def _group_geometry(members: List[dict], direction: str) -> Any:
    """Бокс группы и зазоры между соседями по главной оси — арифметика кода по `box` канона. Строка — причина отказа."""
    if any(not isinstance(k.get("box"), dict) for k in members):
        return "у части узлов нет box — группировать нечего (они не абсолютные)"
    main, size, cross = ("x", "w", "y") if direction == "row" else ("y", "h", "x")
    ordered = sorted(members, key=lambda k: (k["box"][main], k["box"][cross]))     # порядок — ПОЗИЦИЕЙ, не порядком массива (§8.1)
    gaps: List[Any] = []
    for a, b in zip(ordered, ordered[1:]):
        gap = C.round_num(b["box"][main] - (a["box"][main] + a["box"][size]))
        if gap < -LAYOUT_TOLERANCE:
            return (f"узлы «{a['id']}» и «{b['id']}» перекрываются по оси {main} на {-gap} px — это не "
                    f"{'ряд' if direction == 'row' else 'колонка'}")
        gaps.append(max(gap, 0))                       # перекрытие в пределах допуска — это нулевой зазор, не отрицательный
    x0 = min(k["box"]["x"] for k in ordered)
    y0 = min(k["box"]["y"] for k in ordered)
    x1 = max(k["box"]["x"] + k["box"]["w"] for k in ordered)
    y1 = max(k["box"]["y"] + k["box"]["h"] for k in ordered)
    out: Dict[str, Any] = {"ids": [k["id"] for k in ordered],
                           "box": {"x": C.round_num(x0), "y": C.round_num(y0), "w": C.round_num(x1 - x0), "h": C.round_num(y1 - y0)}}
    if max(gaps) - min(gaps) <= LAYOUT_TOLERANCE:
        out["gap"] = C.round_num(sum(gaps) / len(gaps))
    else:
        out["gaps"] = gaps
    return out


def layout_plan(root_node: dict, m: "OL._Map", explicit: List[Dict[str, Any]], rest: Optional[str]
                ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Решения по ВСЕМ записям unproven кадра (§11.4): названные моделью — проверены и нормализованы; остальные — `keep-px` без
    свидетельства при `layoutRest` либо нарушение. → (решения в порядке unproven, нарушения)."""
    entries = OL.unproven_entries(root_node)
    by_key = {(e["id"], e["reason"]): e for e in entries}
    problems: List[str] = []
    decided: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for i, d in enumerate(explicit):
        norm, bad = _layout_entry_problems(i, d, by_key, m)
        problems += bad
        if norm is not None:
            key = (norm["id"], norm["reason"])
            if key in decided:
                problems.append(f"layout[{i}]: узел «{norm['id']}» с причиной «{norm['reason']}» решён дважды")
            decided[key] = norm
    out: List[Dict[str, Any]] = []
    left: List[str] = []
    for e in entries:
        key = (e["id"], e["reason"])
        if key in decided:
            out.append(decided[key])
        elif rest == LAYOUT_REST:
            out.append({"id": e["id"], "reason": e["reason"], "decision": "keep-px", "evidence": "none", "rest": True})
        else:
            left.append(f"{e['id']} ({e['reason']})")
    if left:
        problems.append(f"не решено записей unproven: {len(left)} — {_listed(left)}. Лестница §11.4: декларации "
                        f"(daiver_source_nodes) → картинка кадра (только для группировки) → вопрос пользователю; не решаешь — "
                        f"plan.layoutRest: «{LAYOUT_REST}» (px остаётся px, узлы попадут в planUncertainty)")
    return out, problems


# ── порядок сборки (§11.5, INV-B12) ──────────────────────────────────────────

def _pascal(name: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in _ASCII_WORD.findall(name))


def _decision_of(e: Dict[str, Any]) -> str:
    return "component" if "component" in e else e["resolution"]


def _node_entry(c: "RS.Cand", rec: Dict[str, Any]) -> Dict[str, Any]:
    """Решение по кандидату в форме плана: `{component, props}` | `{resolution: local|ignore}` (+ откуда и с какой уверенностью)."""
    e: Dict[str, Any] = {"id": c.id, "kind": c.kind}
    if c.name:
        e["name"] = RS._clip(RS._flat(c.name), RS.MAX_NAME)
    if c.reusable:
        e["figmaName"] = RS._clip(RS._flat(c.lookup), CM.MAX_NAME)
        if c.variants:
            e["variants"] = dict(c.variants)
    if rec["decision"] == "component":
        e["component"] = rec["component"]
        if rec.get("props"):
            e["props"] = dict(rec["props"])
    else:
        e["resolution"] = rec["decision"]
        e["why"] = RS._clip(RS._flat(rec["rationale"]), WHY_MAX)
    e["confidence"] = rec["confidence"]
    e["source"] = rec["source"]
    return e


def _name_locals(pairs: List[Tuple[Dict[str, Any], "RS.Cand"]], hits: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """Имена local-компонентов: id узла → (имя, откуда: map | derived). Имя из правила карты (`outcome: local` + `component`) —
    всегда; без него имя получают только ОБЩИЕ (≥ 2 узла с одним figmaName+variants). Повтор имени разных ключей — суффикс 2, 3…"""
    local = [(e, c) for e, c in pairs if e.get("resolution") == "local" and c.kind == "instance"]
    out: Dict[str, Tuple[str, str]] = {}
    taken = set()
    for _e, c in local:
        hit = hits.get(c.id)
        name = hit.get("component") if isinstance(hit, dict) and hit.get("outcome") == "local" else None
        if name:
            out[c.id] = (name, "map")
            taken.add(name)
    groups: Dict[str, List["RS.Cand"]] = {}
    for _e, c in local:
        if c.id not in out and c.reusable:
            groups.setdefault(c.key, []).append(c)
    fallback = 0
    for _key, cands in groups.items():                       # порядок вставки = порядок первого вхождения в документе
        if len(cands) < 2:
            continue
        base = _pascal(cands[0].lookup)
        if not base:
            fallback += 1
            base = f"Local{fallback}"
        elif not _NAME_OK.match(base):
            base = "Local" + base
        name, k = base, 2
        while name in taken:
            name, k = f"{base}{k}", k + 1
        taken.add(name)
        for c in cands:
            out[c.id] = (name, "derived")
    return out


def _local_list(pairs: List[Tuple[Dict[str, Any], "RS.Cand"]], names: Dict[str, Tuple[str, str]], owner: Dict[str, str]
                ) -> List[Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    for e, c in pairs:
        got = names.get(c.id)
        if not got:
            continue
        e["local"] = got[0]
        item = by_name.get(got[0])
        if item is None:
            item = by_name[got[0]] = {"name": got[0], "origin": got[1], "keys": [], "figmaNames": [], "nodes": 0,
                                      "sections": [], "creator": owner.get(c.id) or "shell"}
        item["nodes"] += 1
        if c.key not in item["keys"]:
            item["keys"].append(c.key)
        if c.lookup not in item["figmaNames"]:
            item["figmaNames"].append(c.lookup)
        sec = owner.get(c.id)
        if sec and sec not in item["sections"]:
            item["sections"].append(sec)
    out = list(by_name.values())
    for item in out:
        item["shared"] = item["nodes"] >= 2
    return out


def _build_order(order_ids: List[str], locals_: List[Dict[str, Any]]) -> List[str]:
    """Три яруса, каждый в порядке документа: секции, СОЗДАЮЩИЕ общие local-компоненты (первое вхождение), затем секции, которые
    их только используют, затем остальное. Создатель идёт раньше любого потребителя того же компонента: он первый в документе."""
    creators = {x["creator"] for x in locals_ if x["shared"]}
    users = {s for x in locals_ if x["shared"] for s in x["sections"]} - creators
    return ([s for s in order_ids if s in creators] + [s for s in order_ids if s in users]
            + [s for s in order_ids if s not in creators and s not in users])


def check_build_order(raw: List[Dict[str, Any]], expected: List[Dict[str, Any]], sections: Dict[str, Dict[str, Any]],
                      budget: Any) -> List[str]:
    """Свой buildOrder модели: id — реальные секции кадра при этом бюджете, hash — их настоящий хеш, все секции названы по разу, порядок
    — тот, что считает код. Всё нарушенное — списком (отказ `daiver_plan_write`, ничего не записано)."""
    problems: List[str] = []
    seen: Dict[str, int] = {}
    real = {sid for sid, s in sections.items() if not s.get("occluded")}
    for i, x in enumerate(raw):
        where = f"buildOrder[{i}]"
        extra = [k for k in x if k not in BUILD_ORDER_KEYS]
        if extra:
            problems.append(f"{where}: неизвестные поля {', '.join(extra)} (допустимы {', '.join(BUILD_ORDER_KEYS)})")
        sid, h = x.get("id"), x.get("hash")
        if not (isinstance(sid, str) and sid and isinstance(h, str) and h):
            problems.append(f"{where}: нужны строки id и hash секции (из sections[] daiver_outline)")
            continue
        if sid in seen:
            problems.append(f"{where}: секция «{sid}» уже названа в buildOrder[{seen[sid]}]")
            continue
        seen[sid] = i
        sec = sections.get(sid)
        if sec is None:
            problems.append(f"{where}: «{sid}» не секция кадра при weightBudget={budget} — buildOrder состоит только из "
                            f"sections[].id daiver_outline; пролог (токены, моки) и придуманные шаги в него не входят")
        elif sec.get("occluded"):
            problems.append(f"{where}: секция «{sid}» закрыта непрозрачным слоем (occluded) — не контент экрана, не строится")
        elif h != sec["hash"]:
            problems.append(f"{where}: хеш секции «{sid}» — {h!r}, в каноне {sec['hash']!r} (хеш берётся из sections[].hash "
                            f"daiver_outline, а не из памяти)")
    absent = [s["id"] for s in expected if s["id"] not in seen]
    if absent:
        problems.append(f"в buildOrder нет секций: {_listed(absent)} — называются все секции, кроме occluded")
    ids = [x.get("id") for x in raw if isinstance(x, dict)]
    want = [s["id"] for s in expected]
    if not problems and ids != want:
        first = next(i for i, (a, b) in enumerate(zip(ids, want)) if a != b)
        problems.append(f"порядок сборки считает код: сначала секции, создающие общие local-компоненты, затем их потребители, "
                        f"дальше порядок документа; на месте {first + 1} ожидается «{want[first]}», получено «{ids[first]}» "
                        f"(верный порядок: {_listed(want)})")
    return problems


# ── дайджест токенов и цветов макета ─────────────────────────────────────────

def _design(root_node: dict) -> Dict[str, Any]:
    tokens, raw = set(), set()
    stack = [root_node]
    while stack:
        n = stack.pop()
        if n.get("occluded") is True:
            continue
        tk = n.get("tokens") if isinstance(n.get("tokens"), dict) else {}
        tokens.update(v for v in tk.values() if isinstance(v, str) and v)
        font = n.get("font")
        if isinstance(font, dict) and isinstance(font.get("token"), str) and font["token"]:
            tokens.add(font["token"])
        style = n.get("style") if isinstance(n.get("style"), dict) else {}
        if isinstance(style.get("background"), str) and style["background"] and "fill" not in tk:
            raw.add(style["background"])
        border = style.get("border")
        if isinstance(border, dict) and isinstance(border.get("color"), str) and border["color"] and "stroke" not in tk:
            raw.add(border["color"])
        if isinstance(n.get("color"), str) and n["color"] and "text" not in tk:
            raw.add(n["color"])
        if not OL._black_box(n):                              # цвета внутри библиотечного компонента — его, не проекта
            stack.extend(OL._kids(n))
    return {"tokens": sorted(tokens)[:LIST_CAP], "tokensCount": len(tokens),
            "rawColors": sorted(raw)[:LIST_CAP], "rawColorsCount": len(raw)}


# ── сборка плана из данных ───────────────────────────────────────────────────

def _digest(n: dict) -> Dict[str, Any]:
    """Дайджест раскладки контейнера (§11.5): direction/gap/padding/align/wrap (`layout`), size или absolute `box` — как в каноне."""
    return {k: n[k] for k in ("layout", "size", "box") if k in n}


def derive(ctx: "RS.Ctx", st: "RS.State", budget: Any, explicit: List[Dict[str, Any]], rest: Optional[str],
           specs: Dict[str, Any], placement: Optional[Dict[str, Any]] = None, hosts: Optional[set] = None,
           state_in: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """План кадра из текущих данных: канон + подтверждённый резолв + карта + спеки + решения по раскладке. → (план, нарушения
    решений по раскладке). Чистая функция: ни времени, ни случайности, ни файлов."""
    env, root_node = ctx.env, ctx.env["root"]
    todo = [c.id for c in ctx.sc.cands if c.id not in st.valid]
    if todo:
        raise PlanError(f"кадр «{ctx.fname}»: не решено кандидатов {len(todo)} ({_listed(todo, 5)}) — daiver_resolve_candidates → "
                        f"daiver_resolve_finalize → daiver_resolve_report")
    m = OL._Map(root_node)
    parts = OL.partition(m, budget)
    meta: Dict[str, dict] = {}
    owner: Dict[str, str] = {}
    sec_ids: List[str] = []
    for n, depth, oversize, occ in parts:
        sid = n["id"]
        sec_ids.append(sid)
        meta[sid] = OL._section_meta(m, n, depth, oversize, occ)
        for d in C.iter_nodes(n):
            owner[d["id"]] = sid
    pairs = [(_node_entry(c, st.valid[c.id]), c) for c in ctx.sc.cands]
    names = _name_locals(pairs, st.hits)
    locals_ = _local_list(pairs, names, owner)
    sec_nodes: Dict[str, List[dict]] = {sid: [] for sid in sec_ids}
    shell_nodes: Dict[str, dict] = {}
    for e, c in pairs:
        sid = owner.get(c.id)
        if sid is None:
            shell_nodes[c.id] = e
        else:
            sec_nodes[sid].append(e)
    sections: List[Dict[str, Any]] = []
    for sid in sec_ids:
        mt = meta[sid]
        s: Dict[str, Any] = {"id": sid, "name": mt["name"], "kind": mt["kind"], "hash": mt["hash"], "weight": mt["weight"],
                             "depth": mt["depth"]}
        for k in ("figmaName", "oversize", "occluded"):
            if k in mt:
                s[k] = mt[k]
        s.update(_digest(m.by_id[sid]))
        if root_node.get("deltaRoot"):                       # секция кадра-состояния — регион (или часть добавленного региона): какого вида
            s["region"] = (m.by_id[sid].get("delta") or OL._region_of(m, m.by_id[sid])).get("kind")
        s["nodes"] = sec_nodes[sid]
        sections.append(s)
    order_ids = [sid for sid in sec_ids if not meta[sid].get("occluded")]
    build_order = [{"id": sid, "name": meta[sid]["name"], "hash": meta[sid]["hash"]}
                   for sid in _build_order(order_ids, locals_)]
    # оболочка над секциями: контейнеры с дайджестом раскладки; секции — ссылками {section: id}
    secset = set(sec_ids)

    def shell_node(n: dict) -> Dict[str, Any]:
        o: Dict[str, Any] = {"id": n["id"], "kind": n["kind"]}
        if n.get("name"):
            o["name"] = RS._clip(RS._flat(n["name"]), RS.MAX_NAME)
        o.update(_digest(n))
        if n["id"] in shell_nodes:
            o["decision"] = shell_nodes[n["id"]]
        return o

    if root_node["id"] in secset:
        tree: Dict[str, Any] = {"section": root_node["id"]}
    else:
        tree = shell_node(root_node)
        tree["children"] = []
        stack = [(root_node, tree)]
        while stack:
            n, out = stack.pop()
            for k in OL._kids(n):
                if k["id"] in secset:
                    out["children"].append({"section": k["id"]})
                else:
                    child = shell_node(k)
                    child["children"] = []
                    out["children"].append(child)
                    stack.append((k, child))
    layout, problems = layout_plan(root_node, m, explicit, rest)
    seen: set = set()
    uncertain: List[str] = []
    for e, c in pairs:
        if e["source"] != "map" and e["confidence"] != "high" and c.id not in seen:
            seen.add(c.id)
            uncertain.append(c.id)
    for d in layout:
        if d["evidence"] == "none" and d["id"] not in seen:
            seen.add(d["id"])
            uncertain.append(d["id"])
    decisions = [_decision_of(e) for e, _c in pairs]
    req_sha = (ctx.frame.get("requirement") or {}).get("sha256")
    plan: Dict[str, Any] = {
        "schema": SCHEMA, "track": ctx.track, "frame": ctx.fname, "role": ctx.frame.get("role"),
        "frameUrl": ctx.frame.get("frameUrl"),
        "nodeUrlTemplate": OL.node_url_template(ctx.frame.get("frameUrl"), (env.get("daiver") or {}).get("sourceFormat")),
        "pins": {"requirementSha256": req_sha, "rootHash": C.subtree_hash(root_node)},
        "weightBudget": budget, "tree": tree, "sections": sections, "buildOrder": build_order, "locals": locals_,
        "layout": {"decisions": layout, "rest": rest}, "specs": specs, "design": _design(root_node), "uncertain": uncertain,
        "stats": {"sections": len(sec_ids), "buildable": len(order_ids), "candidates": len(pairs),
                  "component": decisions.count("component"), "local": decisions.count("local"),
                  "ignore": decisions.count("ignore"), "sharedLocals": sum(1 for x in locals_ if x["shared"]),
                  "uncertain": len(uncertain)},
    }
    if ctx.frame.get("stateOf"):
        plan["stateOf"] = ctx.frame["stateOf"]
    state, bad_state = state_of(ctx, state_in)
    problems = list(problems) + bad_state
    if state is not None:
        plan["state"] = state
    if root_node.get("deltaRoot"):
        plan["delta"] = {"removed": root_node.get("removed") or [], "shared": root_node.get("shared") or [],
                         "similarity": (ctx.env.get("state") or ctx.env.get("baseline") or {}).get("similarity")}
    bl = ctx.env.get("baseline")
    if bl:
        plan["baseline"] = {"kind": "version", "version": bl["version"], "similarity": bl["similarity"], "removed": BL.removed_view(bl),
                            "inherited": list(bl.get("inherited") or [])}
    module, bad = module_of(ctx, placement, hosts)
    problems = list(problems) + bad
    if module is not None:
        plan["module"] = module
    return plan, problems


# ── plan.md: человекочитаемый двойник ────────────────────────────────────────

def _cell(s: Any) -> str:
    """Одна строка без знаков, ломающих ячейку таблицы."""
    return RS._flat(s).replace("|", "\\|").replace("`", "'")


def _link(tpl: Optional[str], node_id: str) -> str:
    url = OL.node_url(tpl, node_id)
    if not url:
        return f"`{_cell(node_id)}`"
    return f"[{_cell(node_id)}]({url.replace('(', '%28').replace(')', '%29').replace(' ', '%20')})"


def _links(tpl: Optional[str], ids: List[str], limit: int = 5) -> str:
    return ", ".join(_link(tpl, i) for i in ids[:limit]) + (f" … +{len(ids) - limit}" if len(ids) > limit else "")


def _layout_text(d: Dict[str, Any]) -> str:
    lay = d.get("layout")
    parts: List[str] = []
    if isinstance(lay, dict):
        parts.append(lay.get("direction", ""))
        for k in ("gap", "crossGap"):
            if lay.get(k):
                parts.append(f"{k}={lay[k]}")
        pad = lay.get("padding")
        if isinstance(pad, dict) and any(pad.get(k) for k in ("top", "right", "bottom", "left")):
            parts.append("pad=" + "/".join(str(pad.get(k, 0)) for k in ("top", "right", "bottom", "left")))
        for k in ("main", "cross"):
            if (lay.get("align") or {}).get(k):
                parts.append(f"{k}={lay['align'][k]}")
        if lay.get("wrap"):
            parts.append("wrap")
    size = d.get("size")
    if isinstance(size, dict):
        parts += [f"{k}={size[k]}" for k in ("w", "h") if k in size]
    box = d.get("box")
    if isinstance(box, dict):
        parts.append("box=" + ",".join(str(box[k]) for k in ("x", "y", "w", "h")))
    return " ".join(p for p in parts if p) or "—"


def node_decision_text(e: Dict[str, Any]) -> str:
    if "component" in e:
        text = f"component `{_cell(e['component'])}`"
        if e.get("props"):
            text += " `" + _cell(json.dumps(e["props"], ensure_ascii=False, sort_keys=True)) + "`"
        return text
    if e["resolution"] == "local":
        return "local" + (f" → `{_cell(e['local'])}`" if e.get("local") else "")
    return "ignore"


def _label(e: Dict[str, Any], count: int) -> str:
    if e.get("figmaName"):
        text = _cell(e["figmaName"])
        if e.get("variants"):
            text += " " + _cell(RS._variants_text(e["variants"]))
    else:
        text = f"{'фрейм' if e['kind'] == 'frame' else e['kind']} «{_cell(e.get('name') or e['id'])}»"
    return text + (f" ×{count}" if count > 1 else "")


_SOURCE = {"map": "правило карты", "resolver": "резолвер", "reuse": "копия вердикта"}


def group_nodes(nodes: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], List[str]]]:
    """Узлы секции с одним и тем же решением и макетом (figmaName+variants, INV-B23) — в одну группу, порядок — по первому узлу."""
    groups: Dict[str, List[Any]] = {}
    for e in nodes:
        sig = json.dumps({k: v for k, v in e.items() if k not in ("id", "name", "why", "source")}, sort_keys=True,
                         ensure_ascii=False)
        if "figmaName" not in e:
            sig += "|" + e["id"]                                   # фреймы без figmaName в группы не склеиваются
        g = groups.get(sig)
        if g is None:
            groups[sig] = [e, [e["id"]]]
            continue
        g[1].append(e["id"])
        if e["source"] == "resolver" and g[0]["source"] != "resolver":
            g[0] = e                                               # эталон группы — запись резолвера, а не копия
    return [(g[0], g[1]) for g in groups.values()]


def _tree_lines(tree: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    stack = [(tree, 0)]
    while stack:
        n, depth = stack.pop()
        if "section" in n:
            s = by_id.get(n["section"], {})
            out.append("  " * depth + f"- секция `{_cell(n['section'])}` «{_cell(s.get('name', ''))}»")
            continue
        dec = f" · {node_decision_text(n['decision'])}" if n.get("decision") else ""
        out.append("  " * depth + f"- `{_cell(n['id'])}` {n['kind']} «{_cell(n.get('name', ''))}» · {_cell(_layout_text(n))}{dec}")
        for ch in reversed(n.get("children") or []):
            stack.append((ch, depth + 1))
    return out


def _iter_shell(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    stack = [tree]
    while stack:
        n = stack.pop()
        if "section" in n:
            continue
        out.append(n)
        stack.extend(reversed(n.get("children") or []))
    return out


def render_md(plan: Dict[str, Any]) -> str:
    """plan.md по plan.json — чистая функция (тест сверяет файл с этим текстом: правка руками видна)."""
    tpl = plan.get("nodeUrlTemplate")
    st = plan["stats"]
    by_id = {s["id"]: s for s in plan["sections"]}
    role = plan.get("role") or "screen"
    role_text = role + (f" (состояние экрана «{_cell(plan['stateOf'])}»)" if plan.get("stateOf") else "")
    out = [f"# План кадра «{plan['frame']}» — {plan['track']}", "",
           "Файл строит движок (`daiver_plan_write`), руками не правится. Подписывается не он, а lock всего трека "
           "(`plan.lock.json`, согласие `ui-plan-" + plan["track"] + "`).", "",
           f"Роль: {role_text} · макет: {plan['frameUrl'] if plan.get('frameUrl') else 'адрес кадра не записан'} · "
           f"бюджет секции (weightBudget): {plan['weightBudget']} Б",
           f"Требование: sha256 `{(plan['pins'].get('requirementSha256') or '—')[:12]}` · канон: rootHash `{plan['pins']['rootHash']}`",
           f"Кандидатов: {st['candidates']} — component {st['component']}, local {st['local']}, ignore {st['ignore']} · "
           f"секций: {st['sections']} (строится {st['buildable']}) · общих local-компонентов: {st['sharedLocals']} · "
           f"неуверенных узлов: {st['uncertain']}", "", "## Порядок сборки", "",
           "Пролог (токены, моки) — не секция и в порядок не входит; секции под непрозрачным слоем (occluded) не строятся.", ""]
    if plan.get("module"):
        out[out.index("## Порядок сборки"):out.index("## Порядок сборки")] = module_md(plan["module"])
    if plan.get("state"):
        out[out.index("## Порядок сборки"):out.index("## Порядок сборки")] = state_md(plan)
    if plan.get("baseline"):
        out[out.index("## Порядок сборки"):out.index("## Порядок сборки")] = baseline_md(plan)
        out[out.index("## Порядок сборки") + 2] = ("Пролога у ревизии нет: токены, моки и модуль уже в коде (mocks.ts дополняется); секции под непрозрачным слоем "
                                                  "(occluded) не строятся.")
    creates: Dict[str, List[str]] = {}
    for x in plan["locals"]:
        if x["shared"]:
            creates.setdefault(x["creator"], []).append(x["name"])
    for i, b in enumerate(plan["buildOrder"], 1):
        s = by_id[b["id"]]
        mark = f" · создаёт local: {', '.join(creates[b['id']])}" if b["id"] in creates else ""
        out.append(f"{i}. {_link(tpl, b['id'])} «{_cell(b['name'])}» · hash `{b['hash']}` · {s['weight']} Б{mark}")
    if not plan["buildOrder"]:
        out.append("Строить нечего: все секции кадра закрыты непрозрачным слоем.")
    if "section" not in plan["tree"] and not plan.get("state") and not plan.get("baseline"):
        out += ["", "## Оболочка над секциями", ""]
        out += _tree_lines(plan["tree"], by_id)
    out += ["", "## Секции", ""]
    for s in plan["sections"]:
        flags = "".join(f" · {w}" for w, k in (("oversize", "oversize"), ("occluded — не строится", "occluded")) if s.get(k))
        out += [f"### {_link(tpl, s['id'])} «{_cell(s['name'])}»", "",
                f"{s['kind']} · hash `{s['hash']}` · {s['weight']} Б · раскладка корня: {_cell(_layout_text(s))}{flags}", ""]
        if not s["nodes"]:
            out += ["Кандидатов резолва в секции нет (простая разметка по канону).", ""]
            continue
        out += ["| Узлы | Макет | Решение | Уверенность | Откуда |", "|---|---|---|---|---|"]
        for head, ids in group_nodes(s["nodes"]):
            out.append(f"| {_links(tpl, ids)} | {_label(head, len(ids))} | {node_decision_text(head)} | {head['confidence']} | "
                       f"{_SOURCE.get(head['source'], head['source'])} |")
        out.append("")
    shell = [n for n in _iter_shell(plan["tree"]) if n.get("decision")]
    if shell:
        out += ["### Кандидаты в оболочке", "", "| Узлы | Макет | Решение | Уверенность | Откуда |", "|---|---|---|---|---|"]
        for head, ids in group_nodes([n["decision"] for n in shell]):
            out.append(f"| {_links(tpl, ids)} | {_label(head, len(ids))} | {node_decision_text(head)} | {head['confidence']} | "
                       f"{_SOURCE.get(head['source'], head['source'])} |")
        out.append("")
    lay = plan["layout"]["decisions"]
    out += ["## Раскладка (unproven)", ""]
    if not lay:
        out += ["Контейнеров с абсолютными детьми и непринятых подсказок нет — раскладка макета объявлена или доказана.", ""]
    else:
        out += ["px → flex меняет только план; принятый hint обязателен для сборки, непринятый не применяется.", "",
                "| Узел | Причина | Решение | Свидетельство | Что |", "|---|---|---|---|---|"]
        for d in lay:
            what = ""
            if d["decision"] == "apply":
                what = " ".join(f"{k}={v if not isinstance(v, dict) else json.dumps(v, sort_keys=True)}"
                                for k, v in sorted(d.get("hints", {}).items()))
            elif d["decision"] == "group":
                what = d["direction"] + ": " + "; ".join(
                    "[" + ", ".join(g["ids"]) + "] " + (f"gap={g['gap']}" if "gap" in g else f"gaps={g['gaps']}")
                    + " box=" + ",".join(str(g["box"][k]) for k in ("x", "y", "w", "h"))         # сборке нужен и бокс группы (3.2)
                    for g in d["groups"])
            elif d.get("rest"):
                what = "не решено (layoutRest)"
            if d.get("note"):
                what += (" · " if what else "") + d["note"]
            out.append(f"| {_link(tpl, d['id'])} | {d['reason']} | {d['decision']} | {d['evidence']} | {_cell(what) or '—'} |")
        out.append("")
    out += ["## Local-компоненты", ""]
    named = plan["locals"]
    if not named:
        out += ["Общих и названных local-компонентов нет.", ""]
    else:
        out += ["| Имя | Откуда имя | Узлов | Секции | Создаёт |", "|---|---|---|---|---|"]
        for x in named:
            out.append(f"| `{_cell(x['name'])}` | {'правило карты' if x['origin'] == 'map' else 'от figmaName'} | {x['nodes']}"
                       f"{' (общий)' if x['shared'] else ''} | {_cell(', '.join(x['sections']) or '—')} | "
                       f"{_cell(x['creator']) if x['shared'] else '—'} |")
        out.append("")
    sp = plan["specs"]
    out += ["## Спеки", ""]
    if not sp["hasSpecs"]:
        out += ["Разделов логики в спеках нет — модуль презентационный.", ""]
    else:
        out.append("Файлов: " + (", ".join(f["path"] for f in sp["files"]) or "—"))
        for key, title in (("data", "Данные"), ("states", "Состояния"), ("events", "События"), ("validations", "Валидации")):
            if sp[key]:
                out.append(f"- {title}: " + "; ".join(f"{_cell(b['file'])}:{b['line']} «{_cell(b['heading'])}»" for b in sp[key]))
        if sp["e2e"]:
            out.append("- Сценарии e2e: " + "; ".join(f"`{_cell(x['id'])}` {_cell(x['title'])}" for x in sp["e2e"]))
        out.append("")
    ds = plan["design"]
    out += ["## Дизайн-система", "",
            f"Токены макета ({ds['tokensCount']}): " + (", ".join(ds["tokens"]) or "—")
            + (" …" if ds["tokensCount"] > len(ds["tokens"]) else ""),
            f"Цвета без привязанного токена ({ds['rawColorsCount']}): " + (", ".join(ds["rawColors"]) or "—")
            + (" …" if ds["rawColorsCount"] > len(ds["rawColors"]) else ""), ""]
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# ── запись и проверка ────────────────────────────────────────────────────────

def read_plan(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[bytes], Optional[str]]:
    """(план, байты файла, причина). Нет файла — (None, None, None); не читается / не JSON-объект — (None, None, причина)."""
    if not Path(path).is_file():
        return None, None, None
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError as e:
        return None, None, f"не читается: {e.strerror or e}"
    except UnicodeDecodeError:
        return None, None, "не UTF-8"
    except RecursionError:
        return None, None, "вложенность JSON слишком глубока"
    except ValueError:
        return None, None, "не JSON"
    if not isinstance(data, dict):
        return None, None, "ожидается JSON-объект"
    return data, raw, None


def no_code_sections(data: Any) -> List[str]:
    """id секций плана, чей КОРЕНЬ решён `ignore` (§5.2: «узел не строится вовсе»): строить в такой секции нечего, и сборка закрывает
    её сама (build_state, 3.2). Признак — запись `sections[].nodes[]` с id самой секции и `resolution: ignore`; читаются только
    они. Неразобранное — не «без кода»: целостность плана проверяет `check_frame`, а не этот читатель."""
    secs = data.get("sections") if isinstance(data, dict) else None
    out: List[str] = []
    for s in secs if isinstance(secs, list) else []:
        sid, nodes = (s.get("id"), s.get("nodes")) if isinstance(s, dict) else (None, None)
        if isinstance(sid, str) and isinstance(nodes, list) and any(
                isinstance(e, dict) and e.get("id") == sid and e.get("resolution") == "ignore" for e in nodes):
            out.append(sid)
    return out


def _prepare(root: Path, track: str, frame: Optional[str]) -> Tuple["RS.Ctx", "RS.State"]:
    try:
        ctx = RS.Ctx(root, track, frame)
        st = ctx.state(RS.read_store(ctx.root, ctx.spath))
    except RS.ResolveError as e:
        raise PlanError(str(e))
    return ctx, st


def build_frame(root: Path, track: str, frame: Optional[str], raw_plan: Any, weight_budget: Any,
                story_spec: Optional[StoryResolver], resolution_problems: Callable[[], List[str]]
                ) -> Tuple[Dict[str, Any], "RS.Ctx"]:
    """План кадра из входа модели: проверка формы, резолв закончен и подтверждён, бюджет, решения по раскладке, свой buildOrder
    модели (если назван) — всё нарушенное списком; ничего не пишет."""
    inp, problems = check_input(raw_plan)
    if problems:
        raise refusal("план не принят", problems, "Запись не изменена — исправь всё перечисленное сразу и вызови снова")
    ctx, st = _prepare(root, track, frame)
    blockers = resolution_problems()
    if blockers:
        raise refusal("план строится по подтверждённому резолву, а он не готов", blockers[:5],
                      "Порядок: daiver_resolve_candidates → finalize → daiver_resolve_report → вопрос человеку → согласие ui-resolve")
    try:
        budget, _warn = OL.effective_budget(Path(root), weight_budget)
    except OL.OutlineError as e:
        raise PlanError(str(e))
    placement = effective_placement(ctx, inp["placement"])
    plan, problems = derive(ctx, st, budget, inp["layout"], inp["layoutRest"],
                            specs_digest(root, track, ctx.manifest, story_spec), placement, _hosts(root, ctx, placement), inp["state"])
    if inp["buildOrder"] is not None:
        problems += check_build_order(inp["buildOrder"], plan["buildOrder"], {s["id"]: s for s in plan["sections"]}, budget)
    if problems:
        raise refusal("план не принят", problems, "Запись не изменена — исправь всё перечисленное сразу и вызови снова")
    return plan, ctx


def describe(root: Path, plan: Dict[str, Any], paths: Dict[str, Path], written: bool) -> Dict[str, Any]:
    st = plan["stats"]
    out: Dict[str, Any] = {"ok": True, "track": plan["track"], "frame": plan["frame"], "written": written,
                           "plan": _rel(root, paths["plan"]), "planMd": _rel(root, paths["planMd"]),
                           "weightBudget": plan["weightBudget"], "sections": st["sections"],
                           "buildOrder": [f"{b['id']} «{b['name']}»" for b in plan["buildOrder"][:SHOWN]],
                           "nodes": {"component": st["component"], "local": st["local"], "ignore": st["ignore"]}}
    if len(plan["buildOrder"]) > SHOWN:
        out["buildOrderMore"] = len(plan["buildOrder"]) - SHOWN
    shared = [f"{x['name']} ×{x['nodes']} (создаёт секция {x['creator']})" for x in plan["locals"] if x["shared"]]
    if shared:
        out["sharedLocals"] = shared[:SHOWN]
    lay = plan["layout"]["decisions"]
    if lay:
        out["layout"] = {d: sum(1 for x in lay if x["decision"] == d) for d in ("apply", "group", "keep-px")
                         if any(x["decision"] == d for x in lay)}
        left = sum(1 for x in lay if x.get("rest"))
        if left:
            out["layout"]["notDecided"] = left
    out["uncertain"] = st["uncertain"]
    out["next"] = ("план кадра записан. Остальные кадры трека — daiver_plan_write(track, frame, plan); когда планы есть у всех — "
                   "daiver_plan_contract(track): он покажет вопросы к человеку, соберёт ui-plan.md и plan.lock.json")
    return out


def write_frame(root: Any, track: str, frame: Optional[str], raw_plan: Any, weight_budget: Any,
                story_spec: Optional[StoryResolver], resolution_problems: Callable[[], List[str]]) -> Dict[str, Any]:
    """`daiver_plan_write`: валидация и арифметика в коде, затем plan.json + plan.md кадра (атомарно, байтами)."""
    root = Path(root)
    plan, ctx = build_frame(root, track, frame, raw_plan, weight_budget, story_spec, resolution_problems)
    paths = TR.frame_paths(root, ctx.manifest, ctx.fname)
    body, md = dumps(plan).encode("utf-8"), render_md(plan).encode("utf-8")
    old_json = paths["plan"].read_bytes() if paths["plan"].is_file() else None
    old_md = paths["planMd"].read_bytes() if paths["planMd"].is_file() else None
    written = old_json != body or old_md != md
    if old_json != body:
        write_atomic(root, paths["plan"], body, "plan.json")
    if old_md != md:
        write_atomic(root, paths["planMd"], md, "plan.md")
    return describe(root, plan, paths, written)


def _input_from_file(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Входные решения по раскладке, восстановленные из плана: названные явно (без вычисленных полей) и layoutRest."""
    lay = data.get("layout")
    if not isinstance(lay, dict):
        return [], None
    explicit: List[Dict[str, Any]] = []
    for d in lay.get("decisions") or []:
        if not isinstance(d, dict) or d.get("rest"):
            continue
        e = {k: d[k] for k in ("id", "reason", "decision", "evidence", "note") if k in d}
        if d.get("decision") == "group" and isinstance(d.get("groups"), list):
            e["direction"] = d.get("direction")
            e["groups"] = [list(g.get("ids") or []) for g in d["groups"] if isinstance(g, dict)]
        explicit.append(e)
    return explicit, lay.get("rest") if lay.get("rest") == LAYOUT_REST else None


def _placement_from_file(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Решение о месте корня, восстановленное из плана (вход для пересборки): то, что записано в `module.root`."""
    mod = data.get("module")
    root = mod.get("root") if isinstance(mod, dict) else None
    if not isinstance(root, dict) or placement_shape_problems({k: v for k, v in root.items() if k in PLACEMENT_KEYS}):
        return None
    return {k: v for k, v in root.items() if k in PLACEMENT_KEYS}


def _state_from_file(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Решение о состоянии, восстановленное из плана (вход для пересборки)."""
    s = data.get("state")
    if not isinstance(s, dict) or not isinstance(s.get("values"), dict) or not isinstance(s.get("toggle"), dict):
        return None
    t = s["toggle"]
    toggle = {"trigger": t.get("id")} if t.get("kind") == "trigger" else {"named": t.get("name")}
    given = {"variable": s.get("variable"), "screenValue": s["values"].get("screen"), "value": s["values"].get("state"), "toggle": toggle}
    return None if state_shape_problems(given) else given


def effective_placement(ctx: "RS.Ctx", placement: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Место корня не-первичного кадра: названное в плане — оно; у ревизии без названного — то, что решил план собранной версии (корень уже в коде)."""
    if placement is not None:
        return placement
    mod = (ctx.env.get("baseline") or {}).get("module")
    root = mod.get("root") if isinstance(mod, dict) else None
    if not isinstance(root, dict):
        return None
    got = {k: v for k, v in root.items() if k in PLACEMENT_KEYS}
    return None if placement_shape_problems(got) else got


def _hosts(root: Path, ctx: "RS.Ctx", placement: Optional[Dict[str, Any]]) -> Optional[set]:
    """Узлы первичного кадра грузятся только когда нужен host (канон может быть большим)."""
    if not (placement or {}).get("host"):
        return None
    frames = ctx.manifest.get("frames") or []
    return host_ids(root, ctx.manifest, frames[0]["name"]) if frames else None


def state_md(plan: Dict[str, Any]) -> List[str]:
    s, d = plan["state"], plan.get("delta") or {}
    tg = s["toggle"]
    toggle = f"узел экрана `{tg['id']}` «{_cell(tg['name'])}»" if tg["kind"] == "trigger" else f"названный пользователем: {_cell(tg['name'])}"
    out = ["## Состояние экрана", "",
           f"Кадр — состояние экрана «{s['screen']}» (схожесть {d.get('similarity')}): строится ВНУТРИ его модуля, отдельной копии экрана и маршрута нет. Переменная состояния `{s['variable']}`: "
           f"на экране `{s['values']['screen']}`, в этом состоянии `{s['values']['state']}`. Переключатель: {toggle}. Секции ниже — регионы added и changed; их условие — "
           f"`{s['variable']} === '{s['values']['state']}'`.", ""]
    if d.get("removed"):
        out += ["Экран рисует, а состояние — нет (условие на скрытие):", ""] + [f"- `{r['baseId']}` «{_cell(r['name'])}» (внутри `{r['parentId']}`)" for r in d["removed"]] + [""]
    if d.get("shared"):
        out += ["Берётся у экрана готовым (не пишется заново): " + ", ".join(f"`{r['baseId']}`" for r in d["shared"][:SHOWN]) + (" …" if len(d["shared"]) > SHOWN else ""), ""]
    return out


def baseline_md(plan: Dict[str, Any]) -> List[str]:
    b = plan["baseline"]
    out = ["## Изменение версии", "",
           f"Кадр — РЕВИЗИЯ собранной версии {b['version']} (схожесть {b['similarity']}): строится В СУЩЕСТВУЮЩИЙ код читаемым диффом — ни пролога, ни "
           f"нового модуля, маршрута и моков с нуля; структура, имена и стиль файлов сохраняются. Секции ниже — регионы added (дописать туда, где "
           f"собран родитель) и changed (правка на месте); файлы региона даёт `daiver_section` (`baseline.region.files`). Прежняя версия — "
           f"`versions/{b['version']}/` (её `plan.md` — решения, по которым код собран).", ""]
    if b["removed"]:
        out += ["Удалить из кода — в новой версии этого нет (если код использует ещё кто-то в проекте, скажи об этом и чужое не ломай):", ""]
        for r in b["removed"]:
            where = ", ".join(f"`{_cell(x)}`" for x in r["files"]) if r["files"] else "файлы не найдены — ищи по плану версии"
            out.append(f"- `{_cell(r['baseId'])}` «{_cell(r['name'])}» (внутри `{_cell(r['parentId'])}`) — {where}")
        out.append("")
    out += [f"Решений узлов, взятых у собранной версии без пересмотра (shared-части): {len(b['inherited'])}.", ""]
    return out


def module_md(module: Dict[str, Any]) -> List[str]:
    r = module["root"]
    where = (f"компонентом `{r['component']}` внутри компонента первичного кадра «{module['name']}», в узле `{r['host']}`" if r["mode"] == "inside-primary"
             else f"отдельным компонентом `{r['component']}` в каталоге единого модуля трека (имя модуля — по первичному кадру «{module['name']}»)")
    return ["## Модуль", "", f"Кадр строится в ОДИН модуль с первичным кадром «{module['name']}»; корень этого кадра — {where}."
            + (f" Основание: {_cell(r['reason'])}." if r.get("reason") else ""), ""]


def _stale_reasons(data: Dict[str, Any], want: Dict[str, Any]) -> List[str]:
    """Почему файл плана не равен тому, что строит движок, — по частям, языком, понятным человеку."""
    out: List[str] = []
    fp = data.get("pins") if isinstance(data.get("pins"), dict) else {}
    if fp.get("requirementSha256") != want["pins"]["requirementSha256"]:
        out.append("требование кадра перетянуто после плана (sha256 требования не совпал)")
    if fp.get("rootHash") != want["pins"]["rootHash"]:
        out.append("канон кадра изменился после плана (rootHash не совпал)")

    def ids(plan: Dict[str, Any]) -> List[Any]:
        return [(s.get("id"), s.get("hash")) for s in plan.get("sections") or [] if isinstance(s, dict)]

    if ids(data) != ids(want):
        out.append("секции макета изменились после плана (набор id или хеши секций не совпали)")
    elif data.get("sections") != want["sections"]:
        out.append("вердикты резолва или карта компонентов изменились после плана (решения по узлам не совпали)")
    if data.get("specs") != want["specs"]:
        out.append("спеки стори изменились после плана (файлы или разделы не совпали)")
    return out


def check_frame(root: Any, ctx: "RS.Ctx", st: "RS.State", story_spec: Optional[StoryResolver]) -> Dict[str, Any]:
    """Состояние плана кадра: {state: ok|open|stale|unknown, detail, plan?}. План пересобирается из текущих данных с теми же решениями
    по раскладке (они лежат в плане) и сравнивается побайтно; plan.md сверяется с отрисовкой plan.json."""
    root = Path(root)
    paths = TR.frame_paths(root, ctx.manifest, ctx.fname)
    data, raw, why = read_plan(paths["plan"])
    if why:
        return {"state": "open", "detail": f"plan.json не читается ({why}) — неразбираемый план не проходит; пересобери: "
                                           f"daiver_plan_write(track, frame, plan)"}
    if data is None:
        return {"state": "open", "detail": "плана кадра нет — daiver_plan_write(track, frame, plan)"}
    budget = data.get("weightBudget")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        return {"state": "open", "detail": "plan.json: нет положительного weightBudget — файл не от движка; пересобери: "
                                           "daiver_plan_write(track, frame, plan)"}
    explicit, rest = _input_from_file(data)
    placement = effective_placement(ctx, _placement_from_file(data))
    try:
        want, problems = derive(ctx, st, budget, explicit, rest, specs_digest(root, ctx.track, ctx.manifest, story_spec), placement, _hosts(root, ctx, placement),
                                _state_from_file(data))
    except PlanError as e:
        return {"state": "unknown", "detail": f"план нельзя сверить с данными: {e}"}
    if problems:
        return {"state": "stale", "detail": "решения плана по раскладке больше не подходят к макету: " + "; ".join(problems[:3])
                                            + ". Пересобери: daiver_plan_write(track, frame, plan)"}
    if raw != dumps(want).encode("utf-8"):
        reasons = _stale_reasons(data, want) or ["plan.json не совпадает с тем, что строит движок из текущих данных (правлен руками?)"]
        return {"state": "stale", "detail": "план устарел: " + "; ".join(reasons) + ". Пересобери: daiver_plan_write(track, frame, plan)"}
    md = paths["planMd"].read_bytes() if paths["planMd"].is_file() else None
    if md != render_md(want).encode("utf-8"):
        return {"state": "stale", "detail": "plan.md нет или он не соответствует plan.json (правлен руками?). Пересобери: "
                                            "daiver_plan_write(track, frame, plan)"}
    return {"state": "ok", "detail": "план равен тому, что строит движок из текущих данных", "plan": want}
