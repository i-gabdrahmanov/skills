"""plan_digest.py — `ui-plan.md`: дайджест плана для ревьюера и человека, который его подписывает (спека §12.4; задача 2.6).

Что это. Один файл на трек (`.daiver/tracks/<KEY>/ui-plan.md`, рядом с track.yaml): кадры / секции / компоненты (+ local и почему) /
дизайн-система (токены макета, цвета без токена) / ассеты (+ плейсхолдеры) / бизнес-логика (спеки → e2e) / принятые деградации (с
ценой) / неопределённость / открытые вопросы / строка задачи. Каждый факт — из существующего артефакта (plan.json кадров, track.yaml,
конфиг, состояние контракта), не с картинки и не из памяти модели. Дайджест — чистая функция входа: ни времени, ни случайности, ни
чтения диска (всё передаёт `plan_contract`), поэтому один и тот же трек даёт побайтно один файл, и его sha256 годится для lock.

Дайджест не заменяет plan.md кадра (там узлы и секции подробно) и не дублирует структуру экрана: он говорит, ЧТО будет построено
и ЧТО принято как деградация; за подробностями — plan.md и resolution-report.md рядом.

Что НЕ входит. Состояние подписи (оно зависит от хеша самого дайджеста — цикл) и блокирующие вопросы контракта: файл пишется,
только когда их нет, а подпись не должна слетать оттого, что на машине пропал браузер (готовность и подпись — разные вещи); сверка цветов макета с переменными проекта
(`project.styling.colorsFile`) — она делается при сборке (`daiver_color_tokens`, 3.2), здесь честно названы цвета без привязанного
токена; кадры-состояния (6.2) — раздел «Состояния экранов» (переменная, значения, переключатель, регионы).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from . import plan as PL

SHOWN = 12                      # строк в перечислениях (имена, id): остальное — «… +N»
WHY = 140                       # знаков основания local в таблице


def _list(items: List[str], limit: int = SHOWN) -> str:
    return ", ".join(items[:limit]) + (f" … +{len(items) - limit}" if len(items) > limit else "")


def _frames_table(frames: List[Dict[str, Any]]) -> List[str]:
    out = ["| Кадр | Роль | Макет | Адрес страницы | Открытие | Картинка макета |", "|---|---|---|---|---|---|"]
    for f in frames:
        role = f["role"] + (f" ← «{PL._cell(f['stateOf'])}»" if f.get("stateOf") else "")
        out.append(f"| {PL._cell(f['name'])} | {role} | {PL._cell(f.get('frameUrl') or 'не записан')} | "
                   f"{PL._cell(f.get('url') or 'не записан')} | {'рецепт записан' if f.get('open') else '—'} | "
                   f"{'есть' if f.get('goalImage') else 'нет'} |")
    return out


def _module(frames: List[Dict[str, Any]]) -> List[str]:
    """Раздел «Модуль» — только когда у трека есть не-первичные кадры с решением о месте корня (у одного кадра раздела нет: дайджест прежних треков не меняется)."""
    rows = [(f["name"], f["plan"]["module"]) for f in frames if isinstance(f.get("plan"), dict) and f["plan"].get("module")]
    if not rows:
        return []
    out = [f"Трек строится в ОДИН модуль — по первичному кадру «{rows[0][1]['name']}». Место корня остальных кадров — решение плана:", "",
           "| Кадр | Где корень | Компонент | Основание |", "|---|---|---|---|"]
    for name, m in rows:
        r = m["root"]
        where = f"внутри первичного кадра, узел `{r['host']}`" if r["mode"] == "inside-primary" else "отдельный компонент в каталоге модуля"
        out.append(f"| {name} | {where} | {r['component']} | {r.get('reason') or '—'} |")
    return out + [""]


def _states(frames: List[Dict[str, Any]]) -> List[str]:
    """Раздел «Состояния экранов» — только когда в треке есть кадры-состояния с планом: переменная, значения, переключатель, регионы дельты."""
    rows = [(f["name"], f["plan"]) for f in frames if isinstance(f.get("plan"), dict) and f["plan"].get("state")]
    if not rows:
        return []
    out = ["Кадры-состояния строятся ВНУТРИ модуля своего экрана: переменная состояния, условие на регион, переключатель; отдельной копии экрана и маршрута нет.", "",
           "| Кадр | Экран | Переменная: значения | Переключатель | Регионы (added / changed / убрано) |", "|---|---|---|---|---|"]
    for name, p in rows:
        s, t = p["state"], p["state"]["toggle"]
        toggle = f"узел `{t['id']}`" if t["kind"] == "trigger" else f"со слов пользователя: {PL._cell(t['name'])}"
        kinds = [x.get("region") for x in p.get("sections") or []]
        out.append(f"| {name} | {s['screen']} | `{s['variable']}`: {s['values']['screen']} → {s['values']['state']} | {toggle} | "
                   f"{len([k for k in kinds if k == 'added'])} / {len([k for k in kinds if k == 'changed'])} / {len((p.get('delta') or {}).get('removed') or [])} |")
    return out + [""]


def _sections(frames: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for f in frames:
        plan = f["plan"]
        st = plan["stats"]
        by_id = {s["id"]: s for s in plan["sections"]}
        creates: Dict[str, List[str]] = {}
        for x in plan["locals"]:
            if x["shared"]:
                creates.setdefault(x["creator"], []).append(x["name"])
        out.append(f"**Кадр «{PL._cell(f['name'])}»** — секций {st['sections']} (строится {st['buildable']}), бюджет секции "
                   f"{plan['weightBudget']} Б. Порядок сборки (пролог — токены и моки — не секция):")
        out.append("")
        for i, b in enumerate(plan["buildOrder"][:SHOWN * 2], 1):
            mark = f" — создаёт local: {', '.join(creates[b['id']])}" if b["id"] in creates else ""
            out.append(f"{i}. `{PL._cell(b['id'])}` «{PL._cell(b['name'])}» · {by_id[b['id']]['weight']} Б · hash `{b['hash']}`{mark}")
        if len(plan["buildOrder"]) > SHOWN * 2:
            out.append(f"… ещё {len(plan['buildOrder']) - SHOWN * 2} (полный порядок — plan.md кадра)")
        skipped = [s["id"] for s in plan["sections"] if s.get("occluded")]
        if skipped:
            out.append(f"Не строятся (закрыты непрозрачным слоем): {_list(skipped)}")
        out.append("")
    return out


def _components(frames: List[Dict[str, Any]]) -> List[str]:
    lib: Dict[str, Dict[str, Any]] = {}
    local: Dict[str, Dict[str, Any]] = {}
    ignore: List[str] = []
    for f in frames:
        plan = f["plan"]
        nodes = [e for s in plan["sections"] for e in s["nodes"]]
        nodes += [n["decision"] for n in PL._iter_shell(plan["tree"]) if n.get("decision")]
        for e in nodes:
            if "component" in e:
                x = lib.setdefault(e["component"], {"nodes": 0, "frames": [], "props": [], "sources": set()})
                x["nodes"] += 1
                if f["name"] not in x["frames"]:
                    x["frames"].append(f["name"])
                props = json.dumps(e.get("props") or {}, ensure_ascii=False, sort_keys=True)
                if props not in x["props"]:
                    x["props"].append(props)
                x["sources"].add(e["source"])
            elif e["resolution"] == "local":
                label = e.get("local") or e.get("figmaName") or f"{e['kind']} «{e.get('name') or e['id']}»"
                x = local.setdefault(label, {"nodes": 0, "frames": [], "why": e.get("why", ""), "shared": bool(e.get("local"))})
                x["nodes"] += 1
                if f["name"] not in x["frames"]:
                    x["frames"].append(f["name"])
            else:
                ignore.append(f"{f['name']}:{e['id']}" if len(frames) > 1 else e["id"])
    out = ["### Компоненты библиотеки", ""]
    if not lib:
        out += ["Узлов с решением component нет.", ""]
    else:
        out += ["| Компонент | Узлов | Пропсы | Откуда | Кадры |", "|---|---|---|---|---|"]
        for name in sorted(lib):
            x = lib[name]
            props = [p for p in x["props"] if p != "{}"]
            origin = ", ".join(sorted({PL._SOURCE.get(s, s) for s in x["sources"]}))
            out.append(f"| `{PL._cell(name)}` | {x['nodes']} | {PL._cell('; '.join(props) or '—')} | {origin} | "
                       f"{PL._cell(', '.join(x['frames']))} |")
        out.append("")
    out += ["### Свои (local) — и почему", ""]
    if not local:
        out += ["Узлов с решением local нет.", ""]
    else:
        out += ["| Что | Узлов | Почему local | Кадры |", "|---|---|---|---|"]
        for name in sorted(local):
            x = local[name]
            out.append(f"| {PL._cell(name)}{' (общий компонент)' if x['shared'] else ''} | {x['nodes']} | "
                       f"{PL._cell(x['why']) or '—'} | {PL._cell(', '.join(x['frames']))} |")
        out.append("")
    if ignore:
        out += [f"### Не строятся (ignore): {len(ignore)}", "", _list(ignore), ""]
    return out


def _design(frames: List[Dict[str, Any]], design_system: str) -> List[str]:
    out = [f"Источник компонентной библиотеки: {design_system}.", ""]
    for f in frames:
        ds = f["plan"]["design"]
        out.append(f"- Кадр «{PL._cell(f['name'])}»: токены макета ({ds['tokensCount']}): "
                   + (_list(ds["tokens"]) or "—") + f"; цвета без привязанного токена ({ds['rawColorsCount']}): "
                   + (_list(ds["rawColors"]) or "—"))
    out += ["", "Сверка цветов с переменными проекта (`project.styling.colorsFile`) делается при сборке: совпавшее по ТОЧНОМУ значению "
            "станет переменной, остальное останется значением макета; близкий цвет не угадывается.", ""]
    return out


def _assets(frames: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for f in frames:
        arts = f.get("arts") or []
        missing = f.get("missingArts") or []
        if not arts:
            out.append(f"- Кадр «{PL._cell(f['name'])}»: векторных иллюстраций нет.")
        else:
            out.append(f"- Кадр «{PL._cell(f['name'])}»: иллюстраций {len(arts)}, с файлом .svg {len(arts) - len(missing)}"
                       + (f"; БЕЗ файла (будет помеченный placeholder): {_list(missing)}" if missing else ""))
    out.append("")
    return out


def _business(frames: List[Dict[str, Any]]) -> List[str]:
    sp = frames[0]["plan"]["specs"]
    if not sp["hasSpecs"]:
        return ["Разделов логики в спеках нет — модуль презентационный: данные, события, валидации не подключаются.", ""]
    out = ["Файлы: " + (", ".join(f["path"] for f in sp["files"]) or "—")]
    for key, title in (("data", "Данные"), ("states", "Состояния"), ("events", "События"), ("validations", "Валидации")):
        if sp[key]:
            out.append(f"- {title}: " + "; ".join(f"{PL._cell(b['file'])}:{b['line']} «{PL._cell(b['heading'])}»" for b in sp[key]))
    if sp["dataRefs"]:
        out.append("- Типы и клиент из «Данных»: " + ", ".join(f"{r['kind']}: `{PL._cell(r['path'])}`" for r in sp["dataRefs"]))
    elif sp["data"]:
        out.append("- «Данные» не называют путей типов и клиента — моки и пропсы onX" + (" (в спеке упомянуты моки)" if sp["mocks"] else ""))
    if sp["e2e"]:
        out.append("- Сценарии e2e (проверяются на verify): " + "; ".join(f"`{PL._cell(x['id'])}` {PL._cell(x['title'])}" for x in sp["e2e"]))
    out.append("")
    return out


def _degradations(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return ["Нет: всё, что план мог спросить, выяснено.", ""]
    out = []
    for i in items:
        where = f" (кадр «{PL._cell(i['frame'])}»)" if i.get("frame") else ""
        out.append(f"- **{i['id']}**{where}: {PL._cell(i['detail'])} → цена: {PL._cell(i.get('degradesTo') or 'не названа')}")
    out.append("")
    return out


def _uncertainty(u: Dict[str, Any]) -> List[str]:
    if not u["count"]:
        return ["Нет: все решения по узлам — с высокой уверенностью или по правилу карты, раскладка подтверждена свидетельствами.", ""]
    out = [f"Узлов, в которых план не уверен: {u['count']} — по вердиктам резолва (уверенность не high): "
           f"{u['by'].get('resolutions', 0)}, по раскладке без свидетельства (px оставлен): {u['by'].get('layout', 0)}.",
           "Узлы: " + _list(u["nodes"], 20), ""]
    return out


def render(track: str, frames: List[Dict[str, Any]], design_system: str, degradations: List[Dict[str, Any]],
           uncertainty: Dict[str, Any], task_line: str) -> str:
    """Текст ui-plan.md. frames[] — по одному на кадр в порядке манифеста: {name, role, stateOf?, frameUrl?, url?, open, goalImage,
    plan (содержимое plan.json), arts, missingArts}; degradations — неблокирующие пункты контракта (без подписи)."""
    out = [f"# План — {track}", "",
           "Дайджест для ревьюера: что будет построено и что принято как деградация. Файл строит движок (`daiver_plan_contract`) из "
           "артефактов трека, руками не правится. Подписывается lock `plan.lock.json` (в нём sha256 требований, планов кадров, "
           "`resolution-report.md` и этого файла): согласие `ui-plan-" + track + "`. Подробности — plan.md кадров и "
           "resolution-report.md рядом.", "",
           "## Кадры", ""]
    out += _frames_table(frames)
    mod = _module(frames)
    if mod:
        out += ["", "## Модуль", ""] + mod
    states = _states(frames)
    if states:
        out += ["", "## Состояния экранов", ""] + states
    out += ["", "## Секции", ""] + _sections(frames)
    out += ["## Компоненты", ""] + _components(frames)
    out += ["## Дизайн-система", ""] + _design(frames, design_system)
    out += ["## Ассеты", ""] + _assets(frames)
    out += ["## Бизнес-логика", ""] + _business(frames)
    out += ["## Принятые деградации", ""] + _degradations(degradations)
    out += ["## Неопределённость плана", ""] + _uncertainty(uncertainty)
    out += ["## Открытые вопросы", "",
            "Блокирующих вопросов нет: файл собирается, только когда контракт плана готов (`daiver_plan_contract`).", ""]
    out += ["## Строка задачи", "", f"`{task_line}`", ""]
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"
