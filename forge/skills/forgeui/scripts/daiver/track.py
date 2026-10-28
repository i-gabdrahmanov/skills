"""track.py — манифест трека `.daiver/tracks/<KEY>/track.yaml` (спека §4.2, INV-B14, A11).

Трек = фронт-часть ОДНОЙ стори: его имя — ключ стори, а не выдумка модели. Манифест — источник
правды о кадрах, требованиях и их хешах; пишут его только инструменты движка (`daiver_pixso_pull`
заводит трек и кадры, `daiver_track_manifest` правит остальное), руками он не правится — каждое
значение проверяется при записи.

Раскладка. Один кадр держит свою семью файлов прямо в папке трека (`dir: "."`); со второго кадра
ВСЕ кадры переезжают в `frames/<имя>/` (задача 6.1). Все пути к артефактам кадра строятся здесь
(`frame_paths`), а не склеиваются инструментами — иначе переезд сломал бы каждый из них.
Пути в манифесте — от папки трека (`specs` — от корня репо: спеки лежат рядом со стори, вне трека).
Подписи плана в манифесте нет: подпись — согласие человека `ui-plan-<KEY>` в журнале forge.

Кадр, который правит СУЩЕСТВУЮЩУЮ страницу (а не строит новый модуль), несёт `sources` (файлы страницы, пути от корня репо) и
`scope` (change | page); нет `sources` — кадр строится с нуля. Поля нужны контракту плана (задача 2.6: `sources` и `baseline`),
остальной поток правки страницы — 6.4.

Ревизия (задача 6.3). Pull поверх СОБРАННОГО кадра уводит его семью в `versions/<n>/` и пишет `versions` — ушедшие версии
`[{n, requirement, retiredAt, raster?, frameBox?}]` (требование, растр и рамка кадра в растре — какими они были у версии) — и `baseline`
`{kind: version, version: n}`: на какой собранной версии стоит изменение. Оба поля пишет только pull; версии не удаляются никогда.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import image as IMG
from . import recipe as R
from . import yamlite as Y

SCHEMA = 1
TRACKS_REL = Path(".daiver") / "tracks"
MANIFEST_NAME = "track.yaml"
HEADER = [
    "# track.yaml — манифест трека forge-ui (фронт-часть стори).",
    "# Пишут только инструменты движка (daiver_pixso_pull, daiver_track_manifest); руками не править:",
    "# значения проверяются при записи, руками испорченный файл читается как повреждённый.",
    "# Пути — от папки трека; specs — от корня репо.",
]

ROLES = ("screen", "dialog", "state", "breakpoint")
SCOPES = ("change", "page")             # кадр правит существующую страницу: точечно или целиком (§12.2, §14.4.6)
REQUIREMENT_KINDS = ("pixso", "figma-json-a", "figma-json-b", "figma-html")
REQUIREMENT_FILE = {"pixso": "visual-requirements.pixso.json", "figma-json-a": "visual-requirements.json",
                    "figma-json-b": "visual-requirements.json", "figma-html": "visual-requirements.html"}
RAW_FILE = "visual-requirements.pixso.raw.json"
CANONICAL_FILE = "visual-requirements.canonical.json"

FRAME_ORDER = ("name", "role", "dir", "frameUrl", "requirement", "raster", "frameBox", "url", "open",
               "stateOf", "sources", "scope", "baseline", "versions", "notes")
FRAME_SETTABLE = ("role", "url", "open", "stateOf", "frameBox", "sources", "scope", "notes")      # daiver_track_manifest
TRACK_SETTABLE = ("implementation", "specs")
PULL_OWNED = ("name", "dir", "frameUrl", "requirement", "raster", "baseline", "versions")   # пишут pull и rasterPath
BASELINE_KINDS = ("version",)           # ревизия кадра (6.3); база страницы, написанной не forge-ui, — 6.4
VERSION_KEYS = ("n", "requirement", "retiredAt", "raster", "frameBox")

_TRACK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FRAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_URL_RE = re.compile(r"^https?://[^\s]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class TrackError(ValueError):
    """Трека/кадра нет либо манифест повреждён или значение не прошло проверку."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_track_name(name: Any) -> str:
    if not isinstance(name, str) or not _TRACK_RE.match(name):
        raise TrackError(f"имя трека {name!r}: ключ стори вида STOR-123 (буквы, цифры, «.», «_», «-»; "
                         f"без «/» и «..»); имя не выдумывается — оно берётся из стори")
    return name


def check_frame_name(name: Any) -> str:
    if not isinstance(name, str) or not _FRAME_RE.match(name):
        raise TrackError(f"имя кадра {name!r}: буквы, цифры, «_», «-» (до 64 символов); это имя каталога")
    return name


def rel_problem(p: Any, what: str) -> Optional[str]:
    """Относительный путь внутри каталога: без «..», абсолютных и обратных слэшей."""
    if not isinstance(p, str) or not p.strip():
        return f"{what}: непустой путь"
    if p.startswith(("/", "~")) or "\\" in p or re.match(r"^[A-Za-z]:", p) or ".." in Path(p).parts:
        return f"{what}: только относительный путь внутри каталога, без «..», «~» и обратных слэшей ({p!r})"
    return None


# ── проверка манифеста ───────────────────────────────────────────────────────

def validate(m: Any) -> List[str]:
    if not isinstance(m, dict):
        return ["track.yaml: ожидается словарь"]
    problems: List[str] = []
    allowed = {"track", "schema", "created", "frames", "implementation", "specs"}
    problems += [f"{k}: неизвестное поле манифеста" for k in m if k not in allowed]
    if m.get("schema") != SCHEMA:
        problems.append(f"schema: ожидается {SCHEMA}, получено {m.get('schema')!r}")
    try:
        check_track_name(m.get("track"))
    except TrackError as e:
        problems.append(f"track: {e}")
    if "created" in m and not isinstance(m["created"], str):
        problems.append("created: строка с временем")
    frames = m.get("frames")
    if not isinstance(frames, list):
        problems.append("frames: ожидается список кадров")
        frames = []
    names: List[str] = []
    for i, f in enumerate(frames):
        problems += _validate_frame(f, i)
        if isinstance(f, dict) and isinstance(f.get("name"), str):
            if f["name"] in names:
                problems.append(f"frames[{i}].name: кадр «{f['name']}» повторяется")
            names.append(f["name"])
    by_name = {f["name"]: f for f in frames if isinstance(f, dict) and isinstance(f.get("name"), str)}
    for i, f in enumerate(frames):
        if not isinstance(f, dict):
            continue
        so = f.get("stateOf")
        if so is not None:
            if so == f.get("name"):
                problems.append(f"frames[{i}].stateOf: кадр не может быть состоянием самого себя")
            elif so not in by_name:
                problems.append(f"frames[{i}].stateOf: кадра «{so}» в треке нет")
            elif by_name[so].get("stateOf") is not None:
                problems.append(f"frames[{i}].stateOf: «{so}» сам состояние — состояние состояния не бывает")
            if f.get("role") != "state":
                problems.append(f"frames[{i}].role: у кадра со stateOf роль state, а не «{f.get('role')}»")
        if i == 0 and (f.get("role") == "state" or so is not None):
            problems.append("frames[0]: первичный кадр не может быть состоянием — состояние строится "
                            "внутри экрана, которого здесь нет")
    # раскладка: один кадр — в папке трека или в frames/<имя>; со второго — все под frames/<имя>
    if len(frames) >= 2:
        for i, f in enumerate(frames):
            if isinstance(f, dict) and f.get("dir") != f"frames/{f.get('name')}":
                problems.append(f"frames[{i}].dir: при нескольких кадрах все лежат в frames/<имя>/ "
                                f"(получено {f.get('dir')!r})")
    impl = m.get("implementation")
    if impl is not None:
        if not isinstance(impl, dict):
            problems.append("implementation: ожидается {url, devCommand}")
        else:
            problems += [f"implementation.{k}: неизвестное поле" for k in impl if k not in ("url", "devCommand")]
            if "url" in impl and not (isinstance(impl["url"], str) and _URL_RE.match(impl["url"])):
                problems.append(f"implementation.url: полный http(s)-адрес, получено {impl.get('url')!r}")
            if "devCommand" in impl and not (isinstance(impl["devCommand"], str) and impl["devCommand"].strip()
                                             and "\n" not in impl["devCommand"]):
                problems.append("implementation.devCommand: одна непустая команда (её запускает автономный прогон)")
    specs = m.get("specs")
    if specs is not None:
        if not isinstance(specs, list):
            problems.append("specs: ожидается список путей от корня репо")
        else:
            for i, p in enumerate(specs):
                pr = rel_problem(p, f"specs[{i}]")
                if pr:
                    problems.append(pr)
            if len(set(x for x in specs if isinstance(x, str))) != len([x for x in specs if isinstance(x, str)]):
                problems.append("specs: путь повторяется")
    return problems


def _validate_frame(f: Any, i: int) -> List[str]:
    where = f"frames[{i}]"
    if not isinstance(f, dict):
        return [f"{where}: ожидается словарь"]
    problems = [f"{where}.{k}: неизвестное поле кадра" for k in f if k not in FRAME_ORDER]
    try:
        check_frame_name(f.get("name"))
    except TrackError as e:
        problems.append(f"{where}.name: {e}")
    if f.get("role") not in ROLES:
        problems.append(f"{where}.role: {f.get('role')!r} — допустимы {', '.join(ROLES)}")
    d = f.get("dir")
    if d != "." and (rel_problem(d, f"{where}.dir") or d != f"frames/{f.get('name')}"):
        problems.append(f"{where}.dir: «.» или frames/<имя кадра>, получено {d!r}")
    if "frameUrl" in f and not (isinstance(f["frameUrl"], str) and f["frameUrl"].strip()):
        problems.append(f"{where}.frameUrl: ссылка на кадр макета")
    if "requirement" in f:
        problems += _requirement_problems(f["requirement"], f"{where}.requirement")
    if "raster" in f:
        pr = rel_problem(f["raster"], f"{where}.raster")
        if pr:
            problems.append(pr)
    if "frameBox" in f:
        problems += _frame_box_problems(f["frameBox"], f"{where}.frameBox")
    if "url" in f and not (isinstance(f["url"], str) and _URL_RE.match(f["url"])):
        problems.append(f"{where}.url: полный http(s)-адрес страницы реализации, получено {f.get('url')!r}")
    if "open" in f:
        problems += R.validate_recipe(f["open"], f"{where}.open")
    if "stateOf" in f and not isinstance(f["stateOf"], str):
        problems.append(f"{where}.stateOf: имя кадра-экрана")
    if "sources" in f:
        src = f["sources"]
        if not isinstance(src, list):
            problems.append(f"{where}.sources: список файлов существующей страницы, пути от корня репо")
        else:
            for j, p in enumerate(src):
                pr = rel_problem(p, f"{where}.sources[{j}]")
                if pr:
                    problems.append(pr)
            if len(set(x for x in src if isinstance(x, str))) != len([x for x in src if isinstance(x, str)]):
                problems.append(f"{where}.sources: путь повторяется")
    if "scope" in f:
        if f["scope"] not in SCOPES:
            problems.append(f"{where}.scope: {f['scope']!r} — допустимы {', '.join(SCOPES)}")
        elif not (isinstance(f.get("sources"), list) and f["sources"]):
            problems.append(f"{where}.scope: задаётся только вместе с sources (страницы в коде нет — менять нечего)")
    if "notes" in f and not (isinstance(f["notes"], str) and "\n" not in f["notes"]):
        problems.append(f"{where}.notes: строка в одну линию")
    numbers = []
    if "versions" in f:
        vs = f["versions"]
        if not isinstance(vs, list) or not vs:
            problems.append(f"{where}.versions: непустой список ушедших версий кадра (его пишет pull поверх собранного кадра)")
        else:
            for j, v in enumerate(vs):
                w = f"{where}.versions[{j}]"
                if not isinstance(v, dict):
                    problems.append(f"{w}: ожидается {{n, requirement, retiredAt}}")
                    continue
                problems += [f"{w}.{k}: неизвестное поле версии" for k in v if k not in VERSION_KEYS]
                n = v.get("n")
                if not (isinstance(n, int) and not isinstance(n, bool) and n > (numbers[-1] if numbers else 0)):
                    problems.append(f"{w}.n: номер версии — целое больше предыдущего (1, 2, …)")
                else:
                    numbers.append(n)
                problems += _requirement_problems(v.get("requirement"), f"{w}.requirement")
                if not isinstance(v.get("retiredAt"), str):
                    problems.append(f"{w}.retiredAt: строка с временем")
                if "raster" in v:
                    pr = rel_problem(v["raster"], f"{w}.raster")
                    if pr:
                        problems.append(pr)
                if "frameBox" in v:
                    problems += _frame_box_problems(v["frameBox"], f"{w}.frameBox")
    if "baseline" in f:
        b = f["baseline"]
        if not (isinstance(b, dict) and set(b) == {"kind", "version"} and b.get("kind") in BASELINE_KINDS):
            problems.append(f"{where}.baseline: {{kind: version, version: N}} — на какой собранной версии стоит ревизия")
        elif b["version"] not in numbers:
            problems.append(f"{where}.baseline.version: версии {b['version']!r} в versions нет")
        if f.get("stateOf") is not None:
            problems.append(f"{where}.baseline: кадр-состояние не версионируется (его сборка — дельта экрана)")
    return problems


def _requirement_problems(r: Any, where: str) -> List[str]:
    if not isinstance(r, dict):
        return [f"{where}: ожидается {{kind, path, sha256, pulledAt}}"]
    problems = [f"{where}.{k}: неизвестное поле" for k in r if k not in ("kind", "path", "sha256", "pulledAt")]
    if r.get("kind") not in REQUIREMENT_KINDS:
        problems.append(f"{where}.kind: {r.get('kind')!r} — допустимы {', '.join(REQUIREMENT_KINDS)}")
    pr = rel_problem(r.get("path"), f"{where}.path")
    if pr:
        problems.append(pr)
    if not (isinstance(r.get("sha256"), str) and _SHA_RE.match(r["sha256"])):
        problems.append(f"{where}.sha256: 64 hex (хеш файла требования)")
    if not isinstance(r.get("pulledAt"), str):
        problems.append(f"{where}.pulledAt: строка с временем")
    return problems


def _frame_box_problems(b: Any, where: str) -> List[str]:
    ok = isinstance(b, dict) and set(b) == {"x", "y", "w", "h"} and all(
        isinstance(b[k], (int, float)) and not isinstance(b[k], bool) for k in b) \
        and b["x"] >= 0 and b["y"] >= 0 and b["w"] > 0 and b["h"] > 0
    if ok:
        return []
    return [f"{where}: {{x, y, w, h}} — числа, x/y ≥ 0, w/h > 0 "
            f"(где кадр лежит внутри растра: у макета с тенью вокруг кадра растр больше кадра)"]


# ── файлы ────────────────────────────────────────────────────────────────────

def track_dir(root: Path, track: str) -> Path:
    return Path(root) / TRACKS_REL / check_track_name(track)


def manifest_path(root: Path, track: str) -> Path:
    return track_dir(root, track) / MANIFEST_NAME


def new_manifest(track: str, created: Optional[str] = None) -> dict:
    return {"track": check_track_name(track), "schema": SCHEMA, "created": created or now_iso(),
            "frames": []}


def _ordered(m: dict) -> dict:
    out: Dict[str, Any] = {}
    for k in ("track", "schema", "created", "frames", "implementation", "specs"):
        if k in m:
            if k == "frames":
                out[k] = [{fk: f[fk] for fk in FRAME_ORDER if fk in f} for f in m[k]]
            else:
                out[k] = m[k]
    return out


def load(root: Path, track: str) -> Optional[dict]:
    """Манифест трека; None — трека нет; TrackError — файл повреждён."""
    path = manifest_path(root, track)
    if not path.is_file():
        return None
    try:
        m = Y.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, Y.YamlError) as e:
        raise TrackError(f"{path.name} повреждён: {e}")
    problems = validate(m)
    if problems:
        raise TrackError(f"{path.name} повреждён: " + "; ".join(problems[:5]))
    if m["track"] != track:
        raise TrackError(f"{path.name}: имя трека «{m['track']}» не совпадает с каталогом «{track}»")
    return m


def load_safe(root: Path, track: str) -> Tuple[Optional[dict], List[str]]:
    """(манифест | None, предупреждения): повреждённый файл читается как отсутствующий и называется."""
    try:
        return load(root, track), []
    except TrackError as e:
        return None, [str(e)]


def save(root: Path, manifest: dict) -> Path:
    """Проверяет и атомарно пишет манифест; шапка-комментарий существующего файла сохраняется."""
    problems = validate(manifest)
    if problems:
        raise TrackError("манифест не прошёл проверку: " + "; ".join(problems[:8]))
    path = manifest_path(root, manifest["track"])
    path.parent.mkdir(parents=True, exist_ok=True)
    header = HEADER
    if path.is_file():
        try:
            old, _ = Y.split_header(path.read_text(encoding="utf-8"))
            if any(h.startswith("#") for h in old):
                header = [h for h in old]
        except (OSError, UnicodeDecodeError):
            pass
    while header and not header[-1].strip():
        header = header[:-1]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(Y.dumps(_ordered(manifest), header=list(header) + [""]), encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def list_tracks(root: Path) -> List[str]:
    base = Path(root) / TRACKS_REL
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and _TRACK_RE.match(p.name))


# ── кадры ────────────────────────────────────────────────────────────────────

def primary_frame(manifest: dict) -> Optional[dict]:
    return manifest["frames"][0] if manifest.get("frames") else None


def get_frame(manifest: dict, name: Optional[str] = None) -> dict:
    """Кадр по имени; без имени — первичный. TrackError с перечнем имеющихся."""
    frames = manifest.get("frames") or []
    if name is None:
        if not frames:
            raise TrackError("в треке нет ни одного кадра — его заводит daiver_pixso_pull")
        return frames[0]
    for f in frames:
        if f.get("name") == name:
            return f
    have = ", ".join(f["name"] for f in frames) or "кадров нет"
    raise TrackError(f"кадра «{name}» в треке нет (есть: {have}); кадр заводит daiver_pixso_pull")


def upsert_frame(manifest: dict, name: str, **fields: Any) -> dict:
    """Завести кадр или обновить его поля (для pull и тестов). Раскладку dir ведёт сам:
    единственный кадр — «.», при втором переезжают все (файлы переносит `layout.migrate`)."""
    check_frame_name(name)
    frames = manifest.setdefault("frames", [])
    frame = next((f for f in frames if f.get("name") == name), None)
    if frame is None:
        frame = {"name": name, "role": "screen", "dir": "."}
        frames.append(frame)
    frame.update(fields)
    if len(frames) >= 2:
        for f in frames:
            f["dir"] = f"frames/{f['name']}"
    return frame


def frame_dir(root: Path, manifest: dict, frame: Optional[str] = None) -> Path:
    f = get_frame(manifest, frame)
    base = track_dir(root, manifest["track"])
    return base if f["dir"] == "." else base / f["dir"]


def frame_paths(root: Path, manifest: dict, frame: Optional[str] = None) -> Dict[str, Path]:
    """Все артефакты кадра по именам — единственное место, где они склеиваются."""
    f = get_frame(manifest, frame)
    d = frame_dir(root, manifest, f["name"])
    req = f.get("requirement") or {}
    return {
        "dir": d,
        "requirement": d / req["path"] if req.get("path") else d / REQUIREMENT_FILE["pixso"],
        "raw": d / RAW_FILE,
        "canonical": d / CANONICAL_FILE,
        "raster": d / f["raster"] if f.get("raster") else d / "visual-requirements.png",
        "assets": d / "assets",
        "plan": d / "plan.json",
        "planMd": d / "plan.md",
        "resolutions": d / "resolutions.json",
        "buildState": d / "build-state.json",
        "verifyState": d / "verify-state.json",
        "verify": d / "verify",
        "versions": d / "versions",
        "baseline": d / "baseline",
    }


def baseline_version(f: dict) -> Optional[int]:
    """Номер собранной версии, на которой стоит ревизия кадра (6.3); None — кадр не ревизия."""
    b = f.get("baseline")
    return b["version"] if isinstance(b, dict) and b.get("kind") == "version" and isinstance(b.get("version"), int) else None


def version_record(f: dict, n: int) -> Optional[dict]:
    return next((v for v in f.get("versions") or [] if isinstance(v, dict) and v.get("n") == n), None)


def version_paths(root: Path, manifest: dict, frame: Optional[str], n: int) -> Dict[str, Path]:
    """Артефакты ушедшей версии `n` кадра по тем же именам, что у `frame_paths` (семья лежит в `versions/<n>/` целиком)."""
    f = get_frame(manifest, frame)
    d = frame_paths(root, manifest, f["name"])["versions"] / str(n)
    rec = version_record(f, n) or {}
    req = rec.get("requirement") if isinstance(rec.get("requirement"), dict) else {}
    return {
        "dir": d,
        "requirement": d / req["path"] if req.get("path") else d / REQUIREMENT_FILE["pixso"],
        "raw": d / RAW_FILE,
        "canonical": d / CANONICAL_FILE,
        "raster": d / rec["raster"] if rec.get("raster") else d / "visual-requirements.png",
        "assets": d / "assets",
        "plan": d / "plan.json",
        "planMd": d / "plan.md",
        "resolutions": d / "resolutions.json",
        "buildState": d / "build-state.json",
        "verifyState": d / "verify-state.json",
        "verify": d / "verify",
        "baseline": d / "baseline",
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def requirement_status(root: Path, manifest: dict, frame: Optional[str] = None) -> str:
    """none — требование не заведено; missing — файла нет; stale — хеш разошёлся; ok."""
    f = get_frame(manifest, frame)
    req = f.get("requirement")
    if not req:
        return "none"
    path = frame_paths(root, manifest, f["name"])["requirement"]
    if not path.is_file():
        return "missing"
    return "ok" if sha256_file(path) == req["sha256"] else "stale"


def implementation_url(manifest: dict, frame: Optional[str] = None) -> Optional[str]:
    """Адрес страницы реализации, по которому проверяется кадр (спека §14.2); None — адреса нет.

    `frames[].url`; первичный кадр падает на `implementation.url` трека; состояние — на адрес своего
    экрана (свой url экрана, а у первичного экрана — url трека); диалог не наследует НИКОГДА (его
    открывает рецепт поверх страницы, адрес диалогу не принадлежит). Роль breakpoint в спеке не
    названа: адреса не наследует (не проверено данными)."""
    frames = manifest.get("frames") or []
    f = get_frame(manifest, frame)
    if f.get("url"):
        return f["url"]
    track_url = (manifest.get("implementation") or {}).get("url")
    primary = frames[0]["name"] if frames else None
    if f.get("role") == "dialog":
        return None
    if f.get("role") == "state" and f.get("stateOf"):
        screen = next((x for x in frames if x.get("name") == f["stateOf"]), None)
        if screen is None:
            return None
        if screen.get("url"):
            return screen["url"]
        return track_url if screen["name"] == primary and screen.get("role") != "dialog" else None
    return track_url if f["name"] == primary else None


def raster_info(root: Path, manifest: dict, frame: Optional[str] = None) -> Optional[dict]:
    f = get_frame(manifest, frame)
    if not f.get("raster"):
        return None
    p = frame_paths(root, manifest, f["name"])["raster"]
    info = IMG.sniff(p) if p.is_file() else None
    return {"path": f["raster"], "exists": p.is_file(), **(info or {})}


def attach_raster(root: Path, manifest: dict, frame: Optional[str], src: Path) -> dict:
    """Копия goal image в папку кадра под именем visual-requirements.<png|jpg> (по содержимому, а
    не по расширению источника). Манифест правит вызывающий; прежний растр другого формата
    удаляется — два файла с разными изображениями кадра не бывает."""
    f = get_frame(manifest, frame)
    src = Path(src)
    if not src.is_file():
        raise TrackError(f"файла {src} нет")
    info = IMG.sniff(src)
    if not info:
        raise TrackError(f"{src.name}: не PNG и не JPEG (или повреждён заголовок) — "
                         f"экспортируйте изображение кадра в PNG/JPEG")
    d = frame_dir(root, manifest, f["name"])
    d.mkdir(parents=True, exist_ok=True)
    name = "visual-requirements" + IMG.EXT[info["format"]]
    for ext in IMG.EXT.values():
        other = d / ("visual-requirements" + ext)
        if ext != IMG.EXT[info["format"]] and other.is_file():
            other.unlink()
    shutil.copyfile(str(src), str(d / name))
    f["raster"] = name
    return {"path": name, **info}


# ── правка полей (daiver_track_manifest) ─────────────────────────────────────

def apply_set(manifest: dict, frame_name: Optional[str], changes: Any) -> Tuple[dict, List[str], List[str]]:
    """(новый манифест, изменённые поля, нарушения). Исходный манифест не меняется.

    `null` снимает поле. Поля, которыми владеет pull (frameUrl, requirement, raster, dir, name),
    отсюда не правятся; поле трека и поле кадра различаются наличием `frame`."""
    problems: List[str] = []
    if not isinstance(changes, dict) or not changes:
        return manifest, [], ["set: нужен непустой объект {поле: значение}"]
    settable = FRAME_SETTABLE if frame_name else TRACK_SETTABLE
    for k in changes:
        if k in PULL_OWNED:
            problems.append(f"«{k}»: это поле заводит daiver_pixso_pull"
                            f"{' (растр — rasterPath инструмента)' if k == 'raster' else ''}; отсюда не правится")
        elif k in (TRACK_SETTABLE if frame_name else FRAME_SETTABLE):
            problems.append(f"«{k}»: это поле {'трека — уберите frame' if frame_name else 'кадра — укажите frame'}")
        elif k not in settable:
            problems.append(f"«{k}»: неизвестное поле; допустимы {', '.join(settable)}")
    if problems:
        return manifest, [], problems
    new = copy.deepcopy(manifest)
    target = get_frame(new, frame_name) if frame_name else new
    changed: List[str] = []
    for k, v in changes.items():
        if v is None:
            if k in target:
                del target[k]
                changed.append(k)
        elif target.get(k) != v:
            target[k] = v
            changed.append(k)
    if frame_name and "stateOf" in changes and changes["stateOf"] is not None and "role" not in changes:
        if target.get("role") != "state":
            target["role"] = "state"           # stateOf без роли state — частая забывчивость; фиксируем явно
            changed.append("role")
    problems = validate(new)
    return (new if not problems else manifest), (changed if not problems else []), problems
