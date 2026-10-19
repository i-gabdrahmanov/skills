"""build_state.py — build-state.json кадра: возобновляемая сборка по секциям (спека §13.1, §13.3–13.4; задача 3.1;
INV-C10, G1, G2, A11; QG U13).

Прогон, остановленный на секции 3 из 6, продолжается с `next`, а не с нуля; изменилась секция в макете — пересобирается
только она. Прогресс живёт ТОЛЬКО в `build-state.json` кадра (рядом с `plan.json`), и пишет его только движок:
`daiver_build_state` создаёт состояние из плана или сверяет его с планом, `daiver_build_state_set` двигает статус ОДНОЙ секции.
Модель файл не читает и не правит; `statePath` и `sections=buildOrder` спеки модель не передаёт (README → «Отклонения»):
путь даёт `track.frame_paths`, секции код читает из плана сам.

Формат (schema 1; «версия state = 1» спеки — это `schema`):
    {schema, track, frame, weightBudget, sections: [{id, name, hash, status, outputs?}]}
`weightBudget` — бюджет плана, при котором существуют эти id секций (`daiver_section` берётся с ним, а не с конфигом, который мог
измениться); тут он затем, чтобы ответ `daiver_build_state_set` называл его без чтения плана. Секции — в порядке `buildOrder`
плана; `status` — pending | in_progress | done | stale (тот же перечень читает лестница `daiver_status`: `building` ⇔ есть секция
не `done`; остальные поля она не читает — контракт 1.10 расширен только совместимо).
`outputs` — файлы секции, пути ОТ КОРНЯ ПРОЕКТА (абсолютный путь внутри репо приводится к такому), отсортированы, без повторов;
обязательны у `done`, у `stale` остаются от прежней сборки (секция перестраивается на месте). Времени, путей машины и случайности
в файле нет, ключи отсортированы: один вход — побайтно один файл (запись — приёмом plan.py: tmp + replace байтами).

Сверка (`daiver_build_state`). Источник секций — `buildOrder` плана кадра (`[{id, name, hash}]` в порядке сборки; из plan.json
читаются ТОЛЬКО он и `weightBudget`, остальное — дело плана). Хеш секции в плане не изменился → статус сохраняется; изменился:
`done` → `stale` (перестроить ТОЛЬКО её), pending / in_progress / stale остаются как есть, хеш обновляется; новые id → pending;
удалённые выпадают вместе с outputs; порядок и имена — по плану. `next` — первая секция не `done` в порядке плана (в том числе
оставленная `in_progress`: иначе прерванный прогон не вернулся бы к ней никогда). Ничего не изменилось — файл не пишется и замок
не берётся (читающий вызов следов не оставляет).

План против макета. Перед сверкой хеши `buildOrder` сличаются с секциями outline нынешнего канона при `weightBudget` плана
(`outline.section_list` — «для кода 2.6/3.1/1.10»). Расхождение — отказ «план не соответствует макету»: сборка по плану, не
равному макету, не начинается и не продолжается (секции строятся из канона, а решения по узлам взяты из плана). Через повторный
pull сюда не попасть (правило 1.2: кадр с `build-state.json` pull считает собранным и отказывает до задачи 6.3), но канон мог
пересобрать обновлённый движок. Подпись плана здесь не проверяется: её охраняет хук (3.3), а не инструмент состояния.

Переходы (`daiver_build_state_set`; код, а не бриф). status — только `in_progress` | `done` (`pending` и `stale` выставляет
сверка). Нельзя перескочить: `done` — только из `in_progress` (повторный `done` секции обновляет её outputs: так чинится путь после
предупреждения; тот же вызов — без изменений); `in_progress` — только для `next` (секции идут по порядку плана: создатель общего
local-компонента раньше потребителей, прогон не рестартует с первой секции); `done` → `in_progress` нет («уже собрана»). `done`
без `outputs` и без `hash` не бывает; `hash` — `section.hash` из ответа `daiver_section` ЭТОЙ секции, сверяется с записанным в
состоянии и в ответах инструментов не называется (иначе его списали бы из отказа, не вызвав `daiver_section`). Неизвестный id —
отказ с перечнем настоящих. Несуществующий путь в outputs — НЕ отказ, а `warnings` (спека: «разбирается немедленно»); они
повторяются в каждом ответе `daiver_build_state`, пока файла нет, а гейт сборки (3.2) считает их ошибкой. Каталог вместо файла и
голое имя файла (`Screen.tsx`: код-гейт откроет именно эту строку от корня проекта) — отказ.

Границы. Бриф и пролог `ui-build`, `ui_gate.py build`, правила сборки — 3.2 (здесь для гейта готов читатель `gate_problems`,
в `ui_gate.py` он не подключён); хук `ui-guard` — 3.3; `state-write-guard` / `phase_tools` — 3.4; несколько кадров — 6.1; регионы
состояния и ревизии — 6.2 / 6.3.

Допущения, не проверенные данными (боевых экранов и прогонов сборки нет): секция, чей корень решён `ignore`, ничего не строит, а
outputs у `done` обязателен — на боевом плане это тупик (модель назовёт файл модуля); число файлов секции и длина пути
(`MAX_OUTPUTS`, `MAX_PATH`) — наши константы; тексты отказов слабая модель не читалась (X.4).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import outline as OL
from . import plan as PL
from . import status as ST
from . import track as TR

SCHEMA = 1
STATUSES = ST.BUILD_STATUSES                                # один перечень с лестницей, которая читает файл (пин в тестах)
SETTABLE = ("in_progress", "done")                          # pending и stale выставляет только сверка с планом
STATE_KEYS = ("schema", "track", "frame", "weightBudget", "sections")
SECTION_KEYS = ("id", "name", "hash", "status", "outputs")
MAX_OUTPUTS = 50                    # файлов одной секции: в спеке не названо; больше — модель перечисляет каталог, а не файлы
MAX_PATH = 300                      # знаков в одном пути outputs
SHOWN = 10                          # сколько id / путей называет ответ
NAME_SHOWN = 64                     # имя секции в ответе (в файле — целиком, как в плане)
LOCK_NAME = "build-state.lock"
HOOKS = Path(__file__).resolve().parents[4] / "hooks"

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:/")
HANDS_OFF = ("Файл пишет только движок, руками не правится; повреждённый файл удаляется (решает человек) — сборка начнётся "
             "с первой секции, код на диске останется и будет перезаписан")


class BuildStateError(ValueError):
    """Состояние нельзя прочесть, сверить или записать, либо вызов не годится — одним сообщением, с тем, что делать."""


def refusal(head: str, problems: List[str], tail: str = "") -> BuildStateError:
    if len(problems) == 1:
        body = f"{head}: {problems[0]}"
    else:
        listed = "; ".join(f"{i}) {t}" for i, t in enumerate(problems[:8], 1))
        more = f"; …и ещё {len(problems) - 8}" if len(problems) > 8 else ""
        body = f"{head} ({len(problems)}): {listed}{more}"
    return BuildStateError(f"{body}.{(' ' + tail) if tail else ''}")


def _clip(s: Any, limit: int = NAME_SHOWN) -> str:
    t = " ".join(_CTRL.sub(" ", str(s)).split())
    return t if len(t) <= limit else t[: max(0, limit - 1)] + "…"


def _listed(items: List[str], limit: int = SHOWN) -> str:
    return ", ".join(items[:limit]) + (f" … +{len(items) - limit}" if len(items) > limit else "")


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _lock(root: Path):
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    import _project as P       # noqa: WPS433 — замки control-plane forge (hooks/ рядом со skills/)
    return P.exclusive_lock(P.ground_dir(root) / LOCK_NAME)


# ── пути outputs ─────────────────────────────────────────────────────────────

def output_problem(rel: Any) -> Optional[str]:
    """Нарушение у УЖЕ нормализованного пути (так лежит в файле): строка, от корня проекта, прямые слэши, без «.» и «..»,
    не меньше двух звеньев (голое имя файла — ничто для код-гейта)."""
    if not isinstance(rel, str) or not rel:
        return "ожидается непустая строка"
    if _CTRL.search(rel) or "\\" in rel or rel.startswith(("/", "~")) or _DRIVE.match(rel):
        return "ожидается путь от корня проекта с «/», без управляющих знаков, «~» и диска"
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "пустые звенья, «.» и «..» в пути недопустимы"
    if len(parts) < 2:
        return "голое имя файла"
    if len(rel) > MAX_PATH:
        return f"длиннее {MAX_PATH} знаков"
    return None


def normalize_output(root: Path, raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """(путь от корня проекта в posix-виде, нарушение). Абсолютный путь внутри репо приводится к относительному; путь,
    который не из корня проекта, а «просто имя файла», отклоняется: код-гейт откроет именно строку из outputs."""
    if not isinstance(raw, str):
        return None, f"{_clip(raw, 40)!r}: путь — строка"
    shown = f"«{_clip(raw, 80)}»"
    if not raw.strip():
        return None, "пустой путь"
    if _CTRL.search(raw):
        return None, f"{shown}: управляющие знаки в пути"
    if "\\" in raw:
        return None, f"{shown}: обратные слэши — пути пишутся с «/»"
    if raw.startswith("~"):
        return None, f"{shown}: «~» не раскрывается — путь от корня проекта (src/modules/x/Screen.tsx) или абсолютный"
    s = raw
    if s.startswith("/") or _DRIVE.match(s):
        try:
            s = Path(s).resolve().relative_to(Path(root).resolve()).as_posix()
        except (ValueError, OSError, RuntimeError):
            return None, f"{shown}: вне репозитория — outputs лежат внутри корня проекта"
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if ".." in parts:
        return None, f"{shown}: «..» в пути — путь от корня проекта, без выхода из репо"
    if not parts:
        return None, f"{shown}: это корень проекта, а не файл"
    if len(parts) < 2:
        return None, (f"{shown}: голое имя файла — нужен путь от корня проекта (src/modules/x/Screen.tsx) или абсолютный; "
                      f"код-гейт откроет именно эту строку")
    rel = "/".join(parts)
    if len(rel) > MAX_PATH:
        return None, f"{shown}: длиннее {MAX_PATH} знаков"
    try:
        (Path(root) / rel).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError, RuntimeError):
        return None, f"{shown}: выходит за корень репо (ссылка?)"
    return rel, None


def normalize_outputs(root: Path, raw: Any) -> Tuple[List[str], List[str]]:
    """(пути: отсортированы, без повторов; нарушения списком). Пустой список — тоже нарушение: `done` без файлов не бывает."""
    if not isinstance(raw, list) or not raw:
        return [], ["outputs: непустой список файлов секции (пути от корня проекта)"]
    if len(raw) > MAX_OUTPUTS:                         # до разбора: список «всего модуля» не должен стоить сотни обращений к диску
        return [], [f"outputs: {len(raw)} путей, больше {MAX_OUTPUTS} — называются файлы секции, а не каталоги и не весь модуль"]
    problems: List[str] = []
    out: List[str] = []
    for item in raw:
        rel, why = normalize_output(root, item)
        if why:
            problems.append(why)
        elif rel is not None:
            out.append(rel)
    return sorted(set(out)), problems


def file_state(root: Path, rel: str) -> str:
    """file | dir | missing — что лежит на диске по пути от корня проекта."""
    p = Path(root) / rel
    if p.is_dir():
        return "dir"
    return "file" if p.is_file() else "missing"


def missing_outputs(root: Path, sections: List[Dict[str, Any]], statuses: Tuple[str, ...] = ("done",)
                    ) -> List[Tuple[str, str]]:
    """[(id секции, путь)] — outputs секций с названным статусом, которых нет на диске (каталог вместо файла — тоже «нет»)."""
    return [(s["id"], o) for s in sections if s["status"] in statuses
            for o in s.get("outputs") or [] if file_state(root, o) != "file"]


# ── файл состояния ───────────────────────────────────────────────────────────

def check_state(data: Any, track: str, frame: str) -> List[str]:
    """Нарушения файла: всё, что движок сам не пишет (правка руками, чужой кадр, статус вне перечня, done без outputs)."""
    if not isinstance(data, dict):
        return ["ожидается JSON-объект"]
    problems = [f"{k}: неизвестное поле (допустимы {', '.join(STATE_KEYS)})" for k in data if k not in STATE_KEYS]
    if data.get("schema") != SCHEMA:
        problems.append(f"schema: ожидается {SCHEMA}, получено {_clip(data.get('schema'), 40)!r}")
    if data.get("track") != track:
        problems.append(f"track: ожидается «{track}», получено {_clip(data.get('track'), 40)!r} — файл другого трека")
    if data.get("frame") != frame:
        problems.append(f"frame: ожидается «{frame}», получено {_clip(data.get('frame'), 40)!r} — файл другого кадра")
    try:
        OL.check_budget(data.get("weightBudget"))
    except OL.OutlineError:
        problems.append(f"weightBudget: положительное число, получено {_clip(data.get('weightBudget'), 40)!r}")
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        problems.append("sections: непустой список секций")
        return problems
    seen: Dict[str, int] = {}
    for i, s in enumerate(sections):
        where = f"sections[{i}]"
        if not isinstance(s, dict):
            problems.append(f"{where}: ожидается объект")
            continue
        problems += [f"{where}.{k}: неизвестное поле (допустимы {', '.join(SECTION_KEYS)})" for k in s if k not in SECTION_KEYS]
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            problems.append(f"{where}.id: непустая строка")
        elif sid in seen:
            problems.append(f"{where}.id: «{_clip(sid, 40)}» уже есть в sections[{seen[sid]}]")
        else:
            seen[sid] = i
        if not isinstance(s.get("name"), str):
            problems.append(f"{where}.name: строка")
        if not isinstance(s.get("hash"), str) or not s.get("hash"):
            problems.append(f"{where}.hash: непустая строка")
        if s.get("status") not in STATUSES:
            problems.append(f"{where}.status: {_clip(s.get('status'), 40)!r} — ожидается {'|'.join(STATUSES)}")
        outs = s.get("outputs")
        if outs is not None:
            if not isinstance(outs, list):
                problems.append(f"{where}.outputs: список путей")
            else:
                for o in outs:
                    why = output_problem(o)
                    if why:
                        problems.append(f"{where}.outputs: {_clip(o, 60)!r} — {why}")
                        break
        if s.get("status") == "done" and not (isinstance(outs, list) and outs):
            problems.append(f"{where}: статус done без outputs")
    return problems


def read_state(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(данные, причина). Нет файла — (None, None); не читается / не JSON — (None, причина). Форму проверяет `check_state`."""
    path = Path(path)
    if not path.is_file():
        return None, None
    data, _raw, why = PL.read_plan(path)             # тот же разбор, что у plan.json: байты → UTF-8 → JSON-объект
    return data, why


def load_state(root: Path, path: Path, track: str, frame: str) -> Optional[Dict[str, Any]]:
    """Состояние кадра или None, если файла нет. Повреждённый файл — отказ с названной причиной (молчаливый сброс потерял бы
    прогресс), а не «пустое состояние»."""
    data, why = read_state(path)
    rel = _rel(root, path)
    if why:
        raise BuildStateError(f"{rel}: {why}. {HANDS_OFF}")
    if data is None:
        return None
    problems = check_state(data, track, frame)
    if problems:
        raise refusal(f"{rel} не проходит проверку", problems, HANDS_OFF)
    return data


def new_state(track: str, frame: str, budget: Any, sections: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"schema": SCHEMA, "track": track, "frame": frame, "weightBudget": budget, "sections": sections}


# ── сверка с планом ──────────────────────────────────────────────────────────

def read_seeds(root: Path, paths: Dict[str, Path]) -> Tuple[List[Dict[str, str]], Any]:
    """(buildOrder плана как [{id, name, hash}], weightBudget). Из plan.json читаются только они; всё, что не годится, — отказ."""
    rel = _rel(root, paths["plan"])
    data, _raw, why = PL.read_plan(paths["plan"])
    fix = "пересобери план: daiver_plan_write(track, frame)"
    if why:
        raise BuildStateError(f"{rel} не читается ({why}) — по неразбираемому плану не строят; {fix}")
    if data is None:
        raise BuildStateError("плана кадра нет — сборка идёт по plan.json: сначала шаг ui-design (daiver_plan_write, подпись плана)")
    try:
        budget = OL.check_budget(data.get("weightBudget"))
    except OL.OutlineError:
        raise BuildStateError(f"{rel}: нет положительного weightBudget — файл не от движка; {fix}")
    order = data.get("buildOrder")
    if not isinstance(order, list):
        raise BuildStateError(f"{rel}: нет buildOrder (список секций в порядке сборки) — файл не от движка; {fix}")
    problems: List[str] = []
    seeds: List[Dict[str, str]] = []
    seen = set()
    for i, e in enumerate(order):
        ok = (isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"] and isinstance(e.get("hash"), str)
              and e["hash"] and isinstance(e.get("name"), str))
        if not ok:
            problems.append(f"buildOrder[{i}]: нужны строки id и hash (непустые) и name")
        elif e["id"] in seen:
            problems.append(f"buildOrder[{i}]: секция «{_clip(e['id'], 40)}» названа дважды")
        else:
            seen.add(e["id"])
            seeds.append({"id": e["id"], "name": e["name"], "hash": e["hash"]})
    if problems:
        raise refusal(f"{rel}: buildOrder не годится", problems, fix[0].upper() + fix[1:])
    if not seeds:
        raise BuildStateError(f"{rel}: buildOrder пуст — строить нечего (все секции закрыты непрозрачным слоем?)")
    return seeds, budget


def check_plan_against_canon(env: dict, seeds: List[Dict[str, str]], budget: Any) -> None:
    """Хеши buildOrder против секций outline нынешнего канона при weightBudget плана. Расхождение — отказ «план не соответствует макету»."""
    real = {s["id"]: s["hash"] for s in OL.section_list(env, budget)}
    changed = [s["id"] for s in seeds if s["id"] in real and real[s["id"]] != s["hash"]]
    gone = [s["id"] for s in seeds if s["id"] not in real]
    if not changed and not gone:
        return
    parts = []
    if changed:
        parts.append(f"хеш изменился у секций {_listed(changed)}")
    if gone:
        parts.append(f"секций {_listed(gone)} нет в каноне при weightBudget={budget}")
    raise BuildStateError("план не соответствует макету (" + "; ".join(parts) + "): канон изменился после плана. Сборка по "
                          "устаревшему плану не начинается и не продолжается — пересобери план: daiver_plan_write(track, frame), "
                          "затем daiver_plan_contract (подпись плана слетит, её подтверждает человек); после этого снова "
                          "daiver_build_state")


def reconcile(old: Optional[List[Dict[str, Any]]], seeds: List[Dict[str, str]]
              ) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """Секции после сверки с buildOrder (§13.4) и что изменилось. Чистая функция: файлов и времени нет.

    Порядок и имена — по плану. Хеш прежний → статус и outputs сохраняются; хеш другой → done становится stale (outputs
    остаются: секция перестраивается на месте), остальные статусы не трогаются, хеш обновляется; id, которого не было, →
    pending; id, которого в плане нет, выпадает вместе с outputs. `added` называется только у уже существовавшего состояния
    (при создании «новыми» были бы все секции)."""
    prev = {s["id"]: s for s in old or []}
    out: List[Dict[str, Any]] = []
    stale: List[str] = []
    added: List[str] = []
    for seed in seeds:
        had = prev.get(seed["id"])
        if had is None:
            out.append({"id": seed["id"], "name": seed["name"], "hash": seed["hash"], "status": "pending"})
            added.append(seed["id"])
            continue
        sec = dict(had)
        sec["name"] = seed["name"]
        if had["hash"] != seed["hash"]:
            sec["hash"] = seed["hash"]
            if had["status"] == "done":
                sec["status"] = "stale"
                stale.append(seed["id"])
        out.append(sec)
    keep = {s["id"] for s in seeds}
    removed = [s["id"] for s in old or [] if s["id"] not in keep]
    return out, {"stale": stale, "added": added if old else [], "removed": removed}


def next_section(sections: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Точка возобновления: первая секция не done в порядке плана (в том числе оставленная in_progress)."""
    return next((s for s in sections if s["status"] != "done"), None)


def progress(sections: List[Dict[str, Any]]) -> Tuple[int, int]:
    return sum(1 for s in sections if s["status"] == "done"), len(sections)


def next_view(sec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if sec is None:
        return None
    v: Dict[str, Any] = {"sectionId": sec["id"], "name": _clip(sec["name"]), "status": sec["status"]}
    if sec.get("outputs"):
        v["outputs"] = list(sec["outputs"])             # у stale — файлы прежней сборки, их правят на месте
    return v


def cycle_text(sid: str, budget: Any) -> str:
    """Тело цикла секции с готовыми аргументами (слабая модель копирует вызов, а не выводит его)."""
    return (f"daiver_section(sectionId={sid}, weightBudget={budget}) → строй ТОЛЬКО её поддерево → "
            f"daiver_build_state_set(sectionId={sid}, status=done, outputs=[файлы секции от корня проекта], "
            f"hash=section.hash из ответа daiver_section)")


def step_text(sec: Optional[Dict[str, Any]], budget: Any, total: int) -> str:
    """Что делать дальше — одной фразой."""
    if sec is None:
        return f"все секции готовы ({total} из {total}) — сборка кадра по плану закончена; дальше сборка проекта и проверка"
    sid, name = sec["id"], _clip(sec["name"])
    start = f"daiver_build_state_set(sectionId={sid}, status=in_progress) → "
    cycle = cycle_text(sid, budget)
    if sec["status"] == "in_progress":
        return f"секция {sid} «{name}» начата раньше (прогон прерван) — продолжай: {cycle}"
    if sec["status"] == "stale":
        return (f"секция {sid} «{name}» устарела (макет изменился) — перестрой ТОЛЬКО её, на месте (файлы прежней сборки — "
                f"next.outputs): {start}{cycle}")
    return f"следующая секция {sid} «{name}»: {start}{cycle}"


def output_warnings(root: Path, sections: List[Dict[str, Any]]) -> List[str]:
    """Одно предупреждение: outputs done-секций, которых нет на диске. Пока файла нет, оно повторяется в каждом ответе."""
    miss = missing_outputs(root, sections)
    if not miss:
        return []
    shown = _listed([f"{sid}: {path}" for sid, path in miss])
    return [f"outputs, которых нет на диске ({len(miss)}): {shown}. Разбери сразу: создай файл или поправь путь повторным "
            f"daiver_build_state_set(status=done, outputs=[…], hash=…) — код-гейт открывает именно эти пути"]


def _write(root: Path, path: Path, state: Dict[str, Any]) -> None:
    try:
        PL.write_atomic(root, path, PL.dumps(state).encode("utf-8"), "build-state.json")
    except PL.PlanError as e:
        raise BuildStateError(str(e))


def run(root: Any, track: str, frame: Optional[str] = None) -> Dict[str, Any]:
    """`daiver_build_state`: создать состояние из buildOrder плана или сверить с ним; вернуть next и прогресс (§13.3 п.1)."""
    root = Path(root)
    try:
        _manifest, f, paths, env, canon_warnings = OL._load(root, track, frame)
    except OL.OutlineError as e:
        raise BuildStateError(str(e))
    seeds, budget = read_seeds(root, paths)
    check_plan_against_canon(env, seeds, budget)
    fname, path = f["name"], paths["buildState"]

    def compute() -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Dict[str, List[str]], bytes]:
        old = load_state(root, path, track, fname)
        sections, rep = reconcile(old["sections"] if old else None, seeds)
        state = new_state(track, fname, budget, sections)
        return old, state, rep, PL.dumps(state).encode("utf-8")

    old, state, rep, body = compute()
    written = (path.read_bytes() if path.is_file() else None) != body
    if written:
        with _lock(root):
            old, state, rep, body = compute()                       # свежие данные под замком
            written = (path.read_bytes() if path.is_file() else None) != body
            if written:
                _write(root, path, state)
    sections = state["sections"]
    done, total = progress(sections)
    nxt = next_section(sections)
    out: Dict[str, Any] = {"track": track, "frame": fname, "created": old is None, "written": written,
                           "progress": f"{done}/{total}", "weightBudget": budget, "next": next_view(nxt)}
    for key in ("stale", "added", "removed"):
        if rep[key]:
            out[key] = rep[key][:SHOWN]
            if len(rep[key]) > SHOWN:
                out[key + "More"] = len(rep[key]) - SHOWN
    warnings = list(canon_warnings) + output_warnings(root, sections)
    if warnings:
        out["warnings"] = warnings
    out["nextStep"] = step_text(nxt, budget, total)
    return out


# ── переходы статуса ─────────────────────────────────────────────────────────

def _frame_paths(root: Path, track: str, frame: Optional[str]) -> Tuple[str, Dict[str, Path]]:
    try:
        manifest = TR.load(root, track)
        if manifest is None:
            raise BuildStateError(f"трека «{track}» нет — сначала daiver_pixso_pull")
        f = TR.get_frame(manifest, frame)
        return f["name"], TR.frame_paths(root, manifest, f["name"])
    except TR.TrackError as e:
        raise BuildStateError(str(e))


def transition_problems(sec: Dict[str, Any], nxt: Optional[Dict[str, Any]], status: str, hash_: Any) -> List[str]:
    """Что мешает перевести секцию в статус: нельзя перескочить (done только из in_progress, in_progress только для next),
    done не воскресает, хеш секции — тот, что записан в состоянии. Хеш в тексте отказа НЕ называется."""
    sid, cur = sec["id"], sec["status"]
    if status == "in_progress":
        if cur == "done":
            return [f"секция «{sid}» уже done — перестраивать нечего (если макет изменится, сверка сама пометит её stale). "
                    f"Поправил файлы — повтори set done с актуальными outputs и hash"]
        if nxt is not None and nxt["id"] != sid:
            return [f"секции строятся по порядку плана: сейчас next — «{nxt['id']}» «{_clip(nxt['name'])}» ({nxt['status']}), "
                    f"а не «{sid}»; начни с неё (daiver_build_state покажет next)"]
        return []
    problems: List[str] = []
    if cur not in ("in_progress", "done"):
        problems.append(f"секция «{sid}» в статусе {cur}: перескочить нельзя — сначала daiver_build_state_set(sectionId={sid}, "
                        f"status=in_progress), затем daiver_section и сборка, затем done")
    if hash_ != sec["hash"]:
        problems.append(f"hash не совпал с хешем секции «{sid}» в build-state: нужен section.hash из ответа daiver_section для "
                        f"ИМЕННО этой секции; если макет или план менялись после последней сверки — сначала daiver_build_state, "
                        f"затем заново daiver_section")
    return problems


def set_section(root: Any, track: str, frame: Optional[str], section_id: Any, status: Any, outputs: Any = None,
                hash_: Any = None) -> Dict[str, Any]:
    """`daiver_build_state_set`: перевести ОДНУ секцию в in_progress | done (§13.3 п.2, §13.4); остальное — отказ."""
    root = Path(root)
    problems: List[str] = []
    if not isinstance(section_id, str) or not section_id.strip():
        problems.append("sectionId: id секции — next.sectionId из ответа daiver_build_state")
    outs: List[str] = []
    if status not in SETTABLE:
        problems.append(f"status: {_clip(status, 40)!r} — допустимы {' | '.join(SETTABLE)}; pending и stale выставляет только "
                        f"движок при сверке с планом (daiver_build_state)")
    elif status == "done":
        if outputs is None:
            problems.append("outputs: при done — непустой список файлов секции (пути от корня проекта или абсолютные)")
        else:
            outs, more = normalize_outputs(root, outputs)
            problems += more
            problems += [f"«{o}»: каталог, а не файл — назови файлы секции" for o in outs if file_state(root, o) == "dir"]
        if not isinstance(hash_, str) or not hash_.strip():
            problems.append("hash: при done — section.hash из ответа daiver_section для этой секции")
    else:
        given = [k for k, v in (("outputs", outputs), ("hash", hash_)) if v is not None]
        if given:
            problems.append(f"{' и '.join(given)}: только при status=done")
    if problems:
        raise refusal("вызов не принят", problems, "Состояние не изменено — исправь всё перечисленное сразу и вызови снова")
    fname, paths = _frame_paths(root, track, frame)
    path = paths["buildState"]
    with _lock(root):
        state = load_state(root, path, track, fname)                # свежие данные под замком
        if state is None:
            raise BuildStateError(f"build-state кадра «{fname}» ещё нет — его создаёт daiver_build_state(track) из buildOrder "
                                  f"плана; секции без него не двигаются")
        sections = state["sections"]
        sec = next((s for s in sections if s["id"] == section_id), None)
        if sec is None:
            ids = [s["id"] for s in sections]
            raise BuildStateError(f"секции «{_clip(section_id, 40)}» в build-state нет (есть: {_listed(ids)}); sectionId берётся "
                                  f"из next ответа daiver_build_state и не выдумывается")
        nxt = next_section(sections)
        bad = transition_problems(sec, nxt, status, hash_)
        if bad:
            raise refusal("переход не принят", bad, "Состояние не изменено")
        new = dict(sec, status=status)                                # outputs прежней сборки (stale) остаются до done
        if status == "done":
            new["outputs"] = outs
        changed = new != sec
        if changed:
            sections = [new if s is sec else s for s in sections]
            state = new_state(track, fname, state["weightBudget"], sections)
            _write(root, path, state)
    budget = state["weightBudget"]
    done, total = progress(sections)
    after = next_section(sections)
    out: Dict[str, Any] = {"ok": True, "track": track, "frame": fname, "sectionId": section_id, "name": _clip(sec["name"]),
                           "status": status, "changed": changed, "progress": f"{done}/{total}"}
    if status == "done":
        out["outputs"] = outs
    out["next"] = next_view(after)
    gone = [o for o in outs if file_state(root, o) != "file"]
    if gone:
        out["warnings"] = [f"outputs, которых нет на диске ({len(gone)}): {_listed(gone)}. Разбери сразу: создай файл или "
                           f"поправь путь повторным daiver_build_state_set(sectionId={section_id}, status=done, outputs=[…], "
                           f"hash=…) — код-гейт открывает именно эти пути"]
    out["nextStep"] = cycle_text(section_id, budget) if status == "in_progress" else step_text(after, budget, total)
    return out


# ── читатель для гейта сборки (3.2) ──────────────────────────────────────────

def gate_problems(root: Any, track: str, frame: Optional[str] = None) -> List[str]:
    """Что мешает считать сборку кадра законченной: нет состояния / оно не сверено с планом / есть секции не done / outputs
    done-секций нет на диске. Пусто — все секции плана собраны, файлы на месте. Только читает диск. Подключает его `ui_gate.py
    build` задачи 3.2; сборка проекта (buildCommand) — тоже там."""
    root = Path(root)
    try:
        _manifest, f, paths, env, _warn = OL._load(root, track, frame)
        seeds, budget = read_seeds(root, paths)
        check_plan_against_canon(env, seeds, budget)
        old = load_state(root, paths["buildState"], track, f["name"])
    except (OL.OutlineError, BuildStateError) as e:
        return [str(e)]
    who = f"кадр «{f['name']}»"
    if old is None:
        return [f"{who}: build-state нет — daiver_build_state(track) создаёт его из buildOrder плана, секции ещё не собирались"]
    sections, _rep = reconcile(old["sections"], seeds)
    out: List[str] = []
    if sections != old["sections"] or old["weightBudget"] != budget:
        out.append(f"{who}: build-state не сверен с планом — вызови daiver_build_state")
    undone = [f"{s['id']} ({s['status']})" for s in sections if s["status"] != "done"]
    if undone:
        out.append(f"{who}: секций не готово {len(undone)} из {len(sections)}: {_listed(undone)}")
    miss = missing_outputs(root, sections)
    if miss:
        out.append(f"{who}: outputs, которых нет на диске ({len(miss)}): {_listed([f'{sid}: {p}' for sid, p in miss])}")
    return out
