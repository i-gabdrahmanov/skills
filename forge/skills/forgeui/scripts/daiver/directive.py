# -*- coding: utf-8 -*-
"""directive.py — строка задачи forge-ui: разбор директивы (спека §15.2; задача 7.1; INV-B22, E3, E7; QG-S12, U11).

Директива — строка таск-листа внешнего оркестратора (или человека): `forge-ui: track=<KEY> …` (алиас `daiver:`). Её читает код, а не
агент: разбор детерминирован, а неправильная строка — malformed с КАЖДЫМ нарушением дословно и никогда не чинится (фикс живёт в таск-файле,
где его ревьюит человек). Ничего не пишет: `parse` — чистая функция, `check_against` сверяет строку с манифестом трека (читает).

Грамматика (значения без пробелов, пары `ключ=значение` через пробел):
    track=<KEY> [frame=<url>] [file=<путь>] [frame.<кадр>=<url>…] [specs=<путь>[,<путь>]] [url=<url>] [url.<кадр>=<url>…]
    [state.<кадр>=<кадр>…] [revision[.<кадр>]=<url>…] [sources[.<кадр>]=<путь>[,<путь>]] [scope=change|page]
    [until=resolve|build|verify] [from=plan] [mode=autonomous|interactive]
Ключи без суффикса (`frame`, `file`, `revision`, `sources`) — о первичном кадре (`main` в строке; у существующего трека — его первый кадр);
`url=` — адрес реализации трека (первичный кадр падает на него, `track.implementation_url`).

Malformed (§15.2): неизвестный ключ (в том числе `autonomous=true`); пробелы вокруг «=» (`mode = autonomous`); недопустимое значение
(`mode=auto`, scope, until, from, имя кадра или трека, не http(s)-адрес); повтор ключа; `from=plan` вместе с ключом требования (`frame`,
`frame.<кадр>`, `file` — re-track запрещён, §15.5); `revision` вместе с `frame` того же кадра; `revision` при `from=plan`; `state.x=x`;
`state.main=…` (main — первичный кадр, не состояние); ключ кадра, которого нет ни в треке, ни в строке (`check_against`: у кадра нет
требования, а строка его не даёт). Предупреждение (не fault): `state.<кадр>=<экран>`, где экран строка не называет.
Нет `mode=` — режим не interactive, а дефолт директивы (`workflow.embeddedMode`); его решает `run.open_run`, не разбор.

Отклонение от спеки: `file=` и `frame=` первичного кадра допустимы ВМЕСТЕ (выгрузка и ссылка: pull Pixso без ссылки на кадр не строит
ссылки на узлы); одного `file=` хватает разбору, а открытие прогона назовёт `blocked: no-requirement`, если ссылки на кадр нет нигде.
`devCommand=` (пример QG-P5) — неизвестный ключ: команда запуска с пробелами в строку без пробелов не ложится, она пишется в track.yaml.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import track as TR

PREFIXES = ("forge-ui:", "daiver:")
MODES = ("autonomous", "interactive")
UNTIL = ("resolve", "build", "verify")
FROM = ("plan",)
PRIMARY = "main"
PER_FRAME = ("frame", "url", "state", "revision", "sources")          # ключи с суффиксом `.<кадр>`
PLAIN = ("track", "frame", "file", "specs", "url", "revision", "sources", "scope", "until", "from", "mode")
REQUIREMENT_KEYS = ("frame", "file")
_URL = re.compile(r"^https?://\S+$")


def is_directive(line: Any) -> bool:
    return isinstance(line, str) and line.strip().startswith(PREFIXES)


def _paths(v: str) -> List[str]:
    return [p for p in v.split(",")]


def parse(line: Any) -> Dict[str, Any]:
    """Строка → {directive, ok, faults, warnings, keys, line}. Не директива (нет префикса) — {directive: False}."""
    if not is_directive(line):
        return {"directive": False, "ok": False, "faults": [], "warnings": [], "keys": None, "line": line if isinstance(line, str) else None}
    text = line.strip()
    prefix = next(p for p in PREFIXES if text.startswith(p))
    tokens = text[len(prefix):].split()
    faults: List[str] = []
    warnings: List[str] = []
    seen: Dict[str, str] = {}
    pairs: List[Tuple[str, str]] = []
    spaced = ("значения без пробелов — пишется ключ=значение слитно; такой ключ не распознан и ничего не даёт (без mode=… режим — дефолт "
              "директивы, а не автономия по догадке)")
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < n else None
        if "=" not in tok:
            if nxt is not None and nxt.startswith("="):                 # «mode = x», «mode =x»
                group, j = [tok, nxt], i + 1
                if nxt == "=" and i + 2 < n:
                    group.append(tokens[i + 2])
                    j = i + 2
                faults.append(f"«{' '.join(group)}»: {spaced}")
                i = j + 1
                continue
            faults.append(f"«{tok}»: не пара ключ=значение")
            i += 1
            continue
        if tok.startswith("="):
            faults.append(f"«{tok}»: не пара ключ=значение")
            i += 1
            continue
        key, _, val = tok.partition("=")
        if not val:
            if nxt is not None and "=" not in nxt:                       # «mode= x»
                faults.append(f"«{tok} {nxt}»: {spaced}")
                i += 2
                continue
            faults.append(f"«{tok}»: пустое значение")
            i += 1
            continue
        if key in seen:
            faults.append(f"«{key}» повторяется ({seen[key]} и {val}): ключ задаётся один раз")
        else:
            seen[key] = val
            pairs.append((key, val))
        i += 1
    keys: Dict[str, Any] = {"primary": {}, "frames": {}}

    def frame_slot(name: Optional[str]) -> Dict[str, Any]:
        return keys["primary"] if name is None else keys["frames"].setdefault(name, {})

    for key, val in pairs:
        base, dot, suffix = key.partition(".")
        name: Optional[str] = None
        if dot:
            if base not in PER_FRAME:
                faults.append(f"«{key}={val}»: неизвестный ключ" + (f" (у «{base}» нет суффикса кадра)" if base in PLAIN else ""))
                continue
            try:
                name = TR.check_frame_name(suffix)
            except TR.TrackError as e:
                faults.append(f"«{key}»: {e}")
                continue
            if name == PRIMARY and base != "state":
                name = None                              # frame.main= — то же, что frame=
        elif key not in PLAIN:
            hint = " — режим задаётся mode=autonomous" if key in MODES else ""
            faults.append(f"«{key}={val}»: неизвестный ключ{hint}")
            continue
        if base in ("frame", "url", "revision") and not _URL.match(val):
            faults.append(f"«{key}={val}»: ожидается http(s)-адрес")
            continue
        if base == "track":
            try:
                keys["track"] = TR.check_track_name(val)
            except TR.TrackError as e:
                faults.append(f"«track={val}»: {e}")
        elif base == "frame":
            frame_slot(name)["frame"] = val
        elif base == "file":
            pr = TR.rel_problem(val, "file")
            if pr:
                faults.append(f"«file={val}»: {pr} (путь от корня репо)")
            else:
                keys["primary"]["file"] = val
        elif base == "url":
            if name is None:
                keys["url"] = val
            else:
                frame_slot(name)["url"] = val
        elif base == "state":
            if name is None:
                faults.append("«state=…»: у состояния нужен кадр — state.<кадр>=<экран>")
                continue
            try:
                target = TR.check_frame_name(val)
            except TR.TrackError as e:
                faults.append(f"«{key}={val}»: {e}")
                continue
            if name == PRIMARY:
                faults.append(f"«{key}={val}»: main — первичный кадр, а не состояние")
            elif target == name:
                faults.append(f"«{key}={val}»: кадр не может быть состоянием самого себя")
            else:
                frame_slot(name)["state"] = target
        elif base == "revision":
            frame_slot(name)["revision"] = val
        elif base == "sources":
            ps = _paths(val)
            bad = [p for p in ps if TR.rel_problem(p, "sources")]
            if bad:
                faults.append(f"«{key}={val}»: пути файлов страницы от корня репо через запятую, без «..» и абсолютных ({', '.join(bad)})")
            else:
                frame_slot(name)["sources"] = ps
        elif base == "specs":
            ps = _paths(val)
            bad = [p for p in ps if TR.rel_problem(p, "specs")]
            if bad:
                faults.append(f"«specs={val}»: пути спек от корня репо через запятую ({', '.join(bad)})")
            else:
                keys["specs"] = ps
        elif base == "scope":
            if val not in TR.SCOPES:
                faults.append(f"«scope={val}»: допустимы {' | '.join(TR.SCOPES)}")
            else:
                keys["scope"] = val
        elif base == "until":
            if val not in UNTIL:
                faults.append(f"«until={val}»: допустимы {' | '.join(UNTIL)}")
            else:
                keys["until"] = val
        elif base == "from":
            if val not in FROM:
                faults.append(f"«from={val}»: допустимо только from=plan")
            else:
                keys["from"] = val
        elif base == "mode":
            if val not in MODES:
                faults.append(f"«mode={val}»: допустимы autonomous | interactive — нераспознанное значение автономию не даёт")
            else:
                keys["mode"] = val
    if "track" not in keys and not any("track" in f for f in faults):
        faults.append("нет track=<KEY>: трек — ключ стори, без него строке не к чему относиться")
    slots = [("main", keys["primary"])] + sorted(keys["frames"].items())
    if keys.get("from") == "plan":
        for name, slot in slots:
            for k in REQUIREMENT_KEYS:
                if k in slot:
                    faults.append(f"«from=plan» вместе с «{k}{'' if name == 'main' else '.' + name}=»: прогон от подписанного плана макет не "
                                  f"перетягивает (план отревьюен против записанного требования)")
            if "revision" in slot:
                faults.append(f"«from=plan» вместе с «revision{'' if name == 'main' else '.' + name}=»: ревизия — новый план, не прогон от подписанного")
    for name, slot in slots:
        if "revision" in slot and "frame" in slot:
            faults.append(f"«revision{'' if name == 'main' else '.' + name}» рядом с «frame{'' if name == 'main' else '.' + name}» того же кадра: "
                          f"либо новый кадр, либо ревизия собранного — не оба")
    named = set(keys["frames"]) | ({PRIMARY} if keys["primary"] else set())
    for name, slot in sorted(keys["frames"].items()):
        target = slot.get("state")
        if target and target not in named:
            warnings.append(f"state.{name}={target}: экран «{target}» строка не называет — трек может уже держать этот кадр; иначе состояние "
                            f"спланируется как отдельный экран")
    if keys.get("scope") and not any(s.get("sources") for _n, s in slots):
        warnings.append("scope= без sources=: применится к кадрам трека, у которых sources уже записаны")
    return {"directive": True, "ok": not faults, "faults": faults, "warnings": warnings, "keys": keys, "line": text}


def primary_name(manifest: Optional[dict]) -> str:
    frames = (manifest or {}).get("frames") or []
    return frames[0]["name"] if frames else PRIMARY


def frames_of(keys: Dict[str, Any], manifest: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    """Ключи кадров строки по настоящим именам кадров трека: первичный — первый кадр манифеста (или main)."""
    out = {primary_name(manifest): dict(keys["primary"])} if keys["primary"] else {}
    for name, slot in keys["frames"].items():
        out.setdefault(name, {}).update(slot)
    return out


def check_against(parsed: Dict[str, Any], manifest: Optional[dict]) -> Dict[str, Any]:
    """Сверка разобранной строки с треком (читает манифест, не пишет): ключ кадра без требования и без frame= в строке — fault;
    состояние, указывающее на первичный кадр трека под его настоящим именем, — fault, как state.main."""
    if not parsed.get("ok"):
        return parsed
    out = dict(parsed, faults=list(parsed["faults"]), warnings=list(parsed["warnings"]))
    have = {f["name"] for f in (manifest or {}).get("frames") or [] if f.get("requirement")}
    primary = primary_name(manifest)
    for name, slot in sorted(frames_of(parsed["keys"], manifest).items()):
        if name == primary and slot.get("state"):
            out["faults"].append(f"«state.{name}={slot['state']}»: «{name}» — первичный кадр трека, а не состояние")
        gives = any(k in slot for k in REQUIREMENT_KEYS)
        if not gives and name not in have:
            what = ", ".join(f"{k}{'' if name == primary else '.' + name}=" for k in sorted(slot)) or "ключи"
            if name == primary and not slot:
                continue
            out["faults"].append(f"{what}: у кадра «{name}» нет требования ни в треке, ни в строке (ни frame=, ни file=) — ему не к чему "
                                 f"относиться; добавь frame{'' if name == primary else '.' + name}=<ссылка на кадр>")
    out["ok"] = not out["faults"]
    return out
