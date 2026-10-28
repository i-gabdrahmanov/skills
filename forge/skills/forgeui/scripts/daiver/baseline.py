"""baseline.py — ревизия кадра: новая версия макета против собранной прежней (спека §7.3, §10.1, §13.7, §14.4.6; задача 6.3; INV-B15, G3, C18;
QG-U22 (часть), P2).

Дизайнер поменял макет уже собранного экрана: это ИЗМЕНЕНИЕ, которое строится внутрь существующего кода читаемым диффом, а не новый экран
рядом. Модуль держит четыре вещи:

  • `retire` — pull поверх СОБРАННОГО кадра (все секции build-state `done`, `status.frame_built`) уводит семью кадра в `versions/<n>/`
    переименованием: требование и сырой экспорт, растр, канон, арты, план, резолвы, build-state, verify-state, `verify/`, `baseline/`
    (`layout.family_names`, кроме самого `versions/`). Манифест получает запись версии `{n, requirement, retiredAt, raster?, frameBox?}` и
    `baseline {kind: version, version: n}`; растр и рамка кадра в растре уходят с версией. Переезд обратим до записи манифеста
    (`Retirement.rollback`); версии не удаляются и не перезаписываются никогда. Кадр-состояние не версионируется (его сборка — дельта экрана).
  • `analyze` — регионы новой версии против базы тем же сравнением, что у кадра-состояния (`states.compare`, но без гейта схожести: ревизия —
    тот же экран, сколько бы ни поменялось; низкая схожесть — предупреждение), дельта-корень `inferred:delta:<кадр>@v<n>` (на нём работают
    outline, section, резолв, план и build-state: секции — регионы added и changed, хеш региона = хеш его дельты, поэтому следующий pull
    недостроенной ревизии переоткроет только сдвинутые регионы). У каждого региона `files` — куда он построен: outputs секций build-state
    базы, которые содержат базовый узел или лежат внутри него; узел shared-части базы, которая сама была ревизией, ищется по цепочке версий;
    у added — файлы родителя (куда вписать).
  • `inherited` — вердикты старого плана для узлов shared-частей по base-id (§10.1: shared наследует вердикт, кандидаты — только в изменённом).
    Ими план ревизии отвечает проверке за всю страницу: мост имён матчера и DS-гейта читает их как решения узлов.
  • `verdict` — `baseline.verdict` захвата (§14.4.6): current (страница показывает новую версию — обычный ход) | previous (на странице
    прежняя версия: MISSING/EXTRA — само изменение; это НЕ жёсткий гейт, сначала проверить, что сервер отдаёт свежую сборку) | ambiguous |
    unknown. Свидетели — эксклюзивное содержимое версий, как у вердикта состояния.
  • `code_sections` — секции build-state всех версий для код-гейта и DS-гейта: код shared-частей собран прежними версиями, правки ревизии
    лежат в тех же файлах.

Модуль только читает (кроме `retire`, которое переименовывает); build_state не импортирует (цикл через outline): build-state версий читается
здесь терпимо — их писал движок, и они не меняются.

Не проверено данными (боевых ревизий нет, сверка — на синтетике и образце «Modal»): сохраняет ли Pixso id узлов между ревизиями (сравнение
от этого не зависит — матчер парит по якорям, но читаемость `baseId` для человека — да); «файлы региона» на модуле, где один файл держит
несколько секций или секция пишет общий файл оболочки; что делает слабая модель с `removed` (удаляет ли код и проверяет ли чужих потребителей).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import canon as C
from . import layout as LAY
from . import states as STS
from . import track as TR

DELTA_PREFIX = STS.DELTA_PREFIX
MAX_FILES = 10                       # файлов региона в ответе (в анализе — все)
MAX_CHAIN = 50                       # глубина цепочки версий при поиске файлов
VERDICTS = ("current", "previous", "ambiguous", "unknown")
_FROM_STATE = {"reached": "current", "not-reached": "previous", "ambiguous": "ambiguous", "unknown": "unknown"}


class BaselineError(ValueError):
    """Ревизию нельзя завести (каталог версии занят, переезд не удался) — с причиной."""


def version_of(f: dict) -> Optional[int]:
    return TR.baseline_version(f)


# ── retire: семья собранного кадра → versions/<n>/ ────────────────────────────

class Retirement:
    """Итог увода семьи: номер версии, перенесённое, запись версии для манифеста; `rollback()` возвращает перенесённое на место."""

    def __init__(self, n: int, base: Path, target: Path, record: Dict[str, Any]):
        self.n, self.base, self.target, self.record = n, base, target, record
        self.moved: List[str] = []
        self.created_target = False

    def rollback(self) -> None:
        for name in reversed(self.moved):
            src = self.target / name
            if src.exists() and not (self.base / name).exists():
                os.replace(str(src), str(self.base / name))
        self.moved = []
        if self.created_target:
            try:
                self.target.rmdir()
                self.target.parent.rmdir()
            except OSError:
                pass

    def message(self, frame: str) -> str:
        return (f"REVISION — кадр «{frame}» был собран: новый экспорт — ИЗМЕНЕНИЕ собранного экрана, а не перезапись. Прежняя версия целиком "
                f"ушла в versions/{self.n}/ ({len(self.moved)} записей семьи кадра) и не удаляется никогда; "
                f"база ревизии — версия {self.n}. Дальше как обычно: daiver_normalize, daiver_frame_baseline покажет регионы изменения; "
                f"план и подпись — заново (прежняя подпись снята), строится только изменённое, в существующий код")


def next_number(f: dict, versions_dir: Path) -> int:
    used = [v["n"] for v in f.get("versions") or [] if isinstance(v, dict) and isinstance(v.get("n"), int)]
    try:
        if versions_dir.is_dir():
            used += [int(p.name) for p in versions_dir.iterdir() if p.name.isdigit()]
    except OSError:
        pass
    return max(used, default=0) + 1


def retire(root: Path, manifest: dict, f: dict, now: str) -> Retirement:
    """Уводит семью кадра `f` в `versions/<n>/` (переименования). Манифест НЕ пишет: вызывающий сразу после зовёт `record` и сохраняет
    манифест, а при сбое — `rollback`. Каталог версии занят или переезд не удался — отказ, на диске всё как было."""
    paths = TR.frame_paths(Path(root), manifest, f["name"])
    base = paths["dir"]
    n = next_number(f, paths["versions"])
    target = paths["versions"] / str(n)
    if target.exists():
        raise BaselineError(f"versions/{n}/ уже существует: прежнюю версию некуда увести — разберись с каталогом вручную, ничего не изменено")
    rec: Dict[str, Any] = {"n": n, "requirement": dict(f.get("requirement") or {}), "retiredAt": now}
    for k in ("raster", "frameBox"):
        if f.get(k) is not None:
            rec[k] = f[k]
    ret = Retirement(n, base, target, rec)
    names = [x for x in LAY.family_names(f, base) if x != "versions"]
    ret.created_target = not paths["versions"].exists()
    try:
        target.mkdir(parents=True)
    except OSError as e:
        raise BaselineError(f"каталог versions/{n}/ не создаётся ({e.strerror or e}): прежнюю версию некуда увести — разберись с каталогом кадра "
                            f"вручную, ничего не изменено")
    failed = ""
    try:
        for name in names:
            failed = name
            os.replace(str(base / name), str(target / name))
            ret.moved.append(name)
    except OSError as e:
        ret.rollback()
        try:
            target.rmdir()
        except OSError:
            pass
        raise BaselineError(f"увод собранной версии в versions/{n}/ не удался на «{failed}» ({e.strerror or e}); всё возвращено на место, "
                            f"новый экспорт не записан")
    return ret


def record(f: dict, ret: Retirement) -> None:
    """Запись версии и базы ревизии в кадр манифеста; растр и рамка кадра в растре ушли с версией."""
    f["versions"] = list(f.get("versions") or []) + [ret.record]
    f["baseline"] = {"kind": "version", "version": ret.n}
    f.pop("raster", None)
    f.pop("frameBox", None)


# ── версии на диске ───────────────────────────────────────────────────────────

def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_bytes().decode("utf-8-sig")) if path.is_file() else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _sections_of(state: Any) -> List[Dict[str, Any]]:
    """[{id, outputs}] из build-state версии (его писал движок; читается терпимо: негодное пропускается)."""
    out: List[Dict[str, Any]] = []
    secs = state.get("sections") if isinstance(state, dict) else None
    for s in secs if isinstance(secs, list) else []:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        outs = [o for o in s.get("outputs") or [] if isinstance(o, str) and o]
        out.append({"id": s["id"], "outputs": sorted(set(outs))})
    return out


def _decisions_of(plan: Any) -> Dict[str, Dict[str, Any]]:
    """Решения узлов плана версии: `sections[].nodes[]` и унаследованные ею самой (`baseline.inherited`) — {id: {component | resolution}}."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(plan, dict):
        return out
    entries: List[Any] = []
    for s in plan.get("sections") or []:
        if isinstance(s, dict):
            entries += s.get("nodes") or []
    base = plan.get("baseline")
    if isinstance(base, dict):
        entries += base.get("inherited") or []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            continue
        if isinstance(e.get("component"), str) and e["component"].strip():
            out.setdefault(e["id"], {"component": e["component"]})
        elif e.get("resolution") in ("local", "ignore"):
            out.setdefault(e["id"], {"resolution": e["resolution"]})
    return out


class _Version:
    """Ушедшая версия кадра: канон (родители и поддеревья), секции build-state, решения плана, её собственная база (если она была ревизией)."""

    def __init__(self, root: Path, manifest: dict, frame: str, n: int):
        self.n = n
        vp = TR.version_paths(root, manifest, frame, n)
        self.dir = vp["dir"]
        self.env: Optional[dict] = None
        self.why: Optional[str] = None
        if not vp["canonical"].is_file():
            self.why = f"канона версии {n} нет (versions/{n}/{TR.CANONICAL_FILE})"
        else:
            try:
                self.env = C.read_canonical(vp["canonical"])
            except (C.CanonError, RecursionError) as e:
                self.why = f"канон версии {n} не читается ({str(e)[:160]})"
        self.parent: Dict[str, Optional[str]] = {}
        self.kids: Dict[str, List[str]] = {}
        if self.env is not None:
            self.parent[self.env["root"]["id"]] = None
            for node in C.iter_nodes(self.env["root"]):
                ks = [k["id"] for k in node.get("children") or [] if isinstance(k, dict)]
                self.kids[node["id"]] = ks
                for k in ks:
                    self.parent[k] = node["id"]
        self.sections = _sections_of(_read_json(vp["buildState"]))
        plan = _read_json(vp["plan"])
        self.plan = plan if isinstance(plan, dict) else {}
        self.decisions = _decisions_of(self.plan)
        pb = self.plan.get("baseline") if isinstance(self.plan.get("baseline"), dict) else {}
        self.base: Optional[int] = pb.get("version") if isinstance(pb.get("version"), int) else None
        delta = self.plan.get("delta") if isinstance(self.plan.get("delta"), dict) else {}
        self.shared = [(x["stateId"], x["baseId"]) for x in delta.get("shared") or []
                       if isinstance(x, dict) and isinstance(x.get("stateId"), str) and isinstance(x.get("baseId"), str)]

    def chain(self, node_id: str) -> List[str]:
        out: List[str] = []
        cur: Optional[str] = node_id
        while cur is not None and cur in self.parent and len(out) < 10000:
            out.append(cur)
            cur = self.parent[cur]
        return out

    def subtree(self, node_id: str) -> Set[str]:
        out: Set[str] = set()
        stack = [node_id]
        while stack:
            n = stack.pop()
            if n in out:
                continue
            out.add(n)
            stack.extend(self.kids.get(n, []))
        return out


class Versions:
    """Ленивый доступ к ушедшим версиям кадра."""

    def __init__(self, root: Path, manifest: dict, frame: str):
        self.root, self.manifest, self.frame = Path(root), manifest, frame
        self._cache: Dict[int, _Version] = {}

    def get(self, n: int) -> _Version:
        if n not in self._cache:
            self._cache[n] = _Version(self.root, self.manifest, self.frame, n)
        return self._cache[n]

    def files(self, n: int, node_id: str) -> List[str]:
        """Файлы, куда построен узел `node_id` версии `n`: outputs секций, которые его содержат или лежат внутри него; не нашлось, а версия
        сама была ревизией — тот же вопрос к её базе через shared-часть, в которой узел лежит."""
        seen: Set[Tuple[int, str]] = set()
        cur: Optional[Tuple[int, str]] = (n, node_id)
        while cur is not None and cur not in seen and len(seen) < MAX_CHAIN:
            seen.add(cur)
            v = self.get(cur[0])
            chain = set(v.chain(cur[1]))
            inside = v.subtree(cur[1]) if cur[1] in v.parent else {cur[1]}
            got = sorted({o for s in v.sections if s["id"] in chain or s["id"] in inside for o in s["outputs"]})
            if got:
                return got
            nxt = None
            if v.base is not None:
                for sid, bid in v.shared:
                    if sid in chain:
                        nxt = (v.base, bid)
                        break
            cur = nxt
        return []


# ── analyze: регионы ревизии ──────────────────────────────────────────────────

def _unavailable(out: Dict[str, Any], reason: str) -> Dict[str, Any]:
    out.update(available=False, reason=reason, regions=[], consequences=[], counts={k: 0 for k in STS.KINDS}, inherited=[])
    return out


def analyze(root: Any, manifest: dict, frame_name: str) -> Dict[str, Any]:
    """Новая версия кадра против собранной базы по файлам трека. Кадр не ревизия — TrackError."""
    root = Path(root)
    f = TR.get_frame(manifest, frame_name)
    n = version_of(f)
    if n is None:
        raise TR.TrackError(f"кадр «{frame_name}» не ревизия: базы версии нет (её заводит daiver_pixso_pull поверх СОБРАННОГО кадра — "
                            f"прежняя семья уходит в versions/<n>/)")
    out: Dict[str, Any] = {"frame": frame_name, "version": n}
    vs = Versions(root, manifest, frame_name)
    base = vs.get(n)
    if base.env is None:
        return _unavailable(out, f"{base.why}: versions/{n}/ — база ревизии, её пишет только pull и никто не удаляет; верни каталог из git")
    cp = TR.frame_paths(root, manifest, frame_name)["canonical"]
    if not cp.is_file():
        return _unavailable(out, f"канона новой версии кадра «{frame_name}» нет — сначала daiver_normalize(track, frame={frame_name})")
    try:
        new_env = C.read_canonical(cp)
    except (C.CanonError, RecursionError) as e:
        return _unavailable(out, f"канон кадра «{frame_name}» не читается ({str(e)[:160]}) — daiver_normalize заново")
    res = STS.compare(base.env, new_env, f"{frame_name} v{n}", frame_name, gate=False, delta_id=f"{DELTA_PREFIX}{frame_name}@v{n}")
    pairs = res["_pairs"]["state→screen"]
    new_by_id = {x["id"]: x for x in C.iter_nodes(new_env["root"])}
    files: Dict[str, List[str]] = {}
    regions: List[Dict[str, Any]] = []
    for r in res["regions"]:
        where = pairs.get(r["parentId"]) if r["kind"] == "added" else r.get("baseId")
        got = vs.files(n, where) if where else []
        files[r["nodeId"]] = got
        regions.append(dict(r, files=got))
    for x in res["_delta"].get("removed") or []:
        x["files"] = files.get(x["baseId"], [])
    inherited: List[Dict[str, Any]] = []
    for r in regions:
        if r["kind"] != "shared":
            continue
        for node in C.iter_nodes(new_by_id[r["nodeId"]]):
            bid = pairs.get(node["id"])
            d = base.decisions.get(bid) if bid else None
            if d:
                inherited.append(dict({"id": node["id"], "baseId": bid}, **d))
    warnings: List[str] = []
    if res.get("_mismatch"):
        warnings.append("ревизия почти целиком другая (" + "; ".join(res["_mismatch"]) + "): это всё ещё изменение собранного экрана — строится в "
                        "существующий код; если на самом деле это другой экран, заведи его отдельным кадром (daiver_pixso_pull с новым frame)")
    if not base.sections:
        warnings.append(f"build-state версии {n} не читается или пуст: файлы регионов неизвестны — ищи код по именам компонентов плана версии "
                        f"(versions/{n}/plan.md)")
    out.update(available=True, similarity=res["similarity"], counts=res["counts"], regions=regions, consequences=res["consequences"],
               inherited=inherited, warnings=warnings, files=files)
    if isinstance(base.plan.get("module"), dict):
        out["module"] = base.plan["module"]
    for k in ("_delta", "_pairs", "_screenRoot", "_exclusive"):
        out[k] = res[k]
    return out


def public(res: Dict[str, Any]) -> Dict[str, Any]:
    """Ответ без внутренних полей (и без служебной карты файлов: файлы у каждого региона)."""
    return {k: v for k, v in res.items() if not k.startswith("_") and k != "files"}


def removed_view(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"baseId": r["baseId"], "name": r["name"], "parentId": r["parentId"], "files": r["files"][:MAX_FILES]}
            for r in res.get("regions") or [] if r["kind"] == "removed"]


# ── verdict: какая версия на странице ────────────────────────────────────────

def verdict(res: Dict[str, Any], matched: Dict[str, Any], impl_root: dict) -> Dict[str, Any]:
    """`baseline.verdict` одного захвата: эксклюзивное содержимое новой версии и базы против страницы (только уверенные пары)."""
    if not res.get("available"):
        return {"verdict": "unknown", "version": res.get("version"), "reason": res.get("reason") or "база ревизии недоступна"}
    n = res["version"]
    v = STS.verdict(dict(res, screen=f"{res.get('frame') or 'кадр'} v{n}"), matched, impl_root)   # причины свои: «экран» у ревизии — собранная версия
    out: Dict[str, Any] = {"verdict": _FROM_STATE[v["verdict"]], "version": n}
    for src, dst in (("stateExclusive", "newExclusive"), ("stateFound", "newFound"), ("screenExclusive", "baseExclusive"), ("screenFound", "baseFound")):
        if src in v:
            out[dst] = v[src]
    out["reason"] = {
        "current": "на странице новая версия макета: её эксклюзивное содержимое найдено, содержимого прежней версии нет — обычный ход проверки",
        "previous": (f"на странице ПРЕЖНЯЯ версия макета (v{n}): MISSING и EXTRA ниже — это и есть само изменение, не собранное или не отданное "
                     f"сервером. Это не жёсткий гейт: сначала проверь, что dev-сервер отдаёт свежую сборку (перезапусти и перезахвати), потом чини"),
        "ambiguous": "картина смешанная: на странице есть содержимое обеих версий (или ни одной) — открой actual.png и реши глазами; дефекты ниже не отменены",
        "unknown": ("у ревизии нет эксклюзивного содержимого (меняются только вариант, цвет или раскладка): по содержимому страницы версии не "
                    "различить — дефекты ниже читаются как обычно"),
    }[out["verdict"]]
    return out


# ── код всех версий для гейтов ────────────────────────────────────────────────

def version_sections(root: Any, manifest: dict, frame_name: str) -> List[List[Dict[str, Any]]]:
    """Секции build-state ушедших версий кадра, от старой к новой (версия без читаемого build-state пропускается)."""
    f = TR.get_frame(manifest, frame_name)
    vs = Versions(Path(root), manifest, frame_name)
    out: List[List[Dict[str, Any]]] = []
    for rec in f.get("versions") or []:
        if isinstance(rec, dict) and isinstance(rec.get("n"), int):
            secs = [s for s in vs.get(rec["n"]).sections if s["outputs"]]
            if secs:
                out.append(secs)
    return out


def code_sections(layers: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Слои секций (версии от старой к новой, текущая сборка последней) → одна раскладка для код-гейта: секция с тем же id или с общим файлом
    сливается с уже взятой (правка ревизии лежит в файлах прежней сборки — второй вход того же компонента дал бы дубль дерева), остальные
    добавляются. Один слой — как есть."""
    if len(layers) == 1:
        return [dict(s) for s in layers[0]]
    out: List[Dict[str, Any]] = []
    for layer in layers:
        for s in layer:
            outs = set(s.get("outputs") or [])
            if not outs:
                continue
            hit = next((o for o in out if o["id"] == s["id"]), None) or next((o for o in out if outs & set(o["outputs"])), None)
            if hit is not None:
                hit["outputs"] = sorted(set(hit["outputs"]) | outs)
            else:
                out.append({"id": s["id"], "outputs": sorted(outs)})
    return out
