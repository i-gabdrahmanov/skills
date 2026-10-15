"""svg_assets.py — daiver_svg_assets: векторные арты кадра ↔ файлы .svg (спека §13.5 п.6, INV-B7, QG U23, N6).

Экспорт Pixso не несёт геометрии путей, поэтому иллюстрация приходит десятками шейпов; `ingest/art.py`
склеивает её в ОДИН узел `art:{slug}` канона, а этот модуль хранит и выдаёт ФАЙЛ, из которого узел строится.
Рисовать иллюстрацию кодом, подменять её голым div и писать инлайн-<svg> руками нельзя (INV-B7, F5).

Три действия одним инструментом (аргументы — по сочетанию):
  • без slug/svgPath/moduleDir — арты кадра из его канона: что уже зарегистрировано, `missingArt` (slug без
    файла), «осиротевшие» файлы assets/ без узла (после перетяжки макета) — всё коротким ответом;
  • slug + svgPath — регистрация экспорта Pixso как `<папка кадра>/assets/<slug>.svg`: slug обязан быть в
    каноне (опечатка не плодит мусор), файл — настоящий svg (разбирается как XML, корень <svg>), без DOCTYPE/
    ENTITY и активного содержимого (скрипт, on*=, javascript:); повтор идемпотентен (`unchanged`);
  • moduleDir (шаг ui-build) — копия файла в `<moduleDir>/<svg.assetsDir>/` и ТОЧНЫЕ import + JSX под
    `svg.render` из конфига. moduleDir обязан лежать в `project.outputDir` и не в служебном каталоге.
    Файла нет — не блокер (§13.5 п.6): вместо JSX из файла выдаётся помеченный placeholder правильного
    размера (`data-art-placeholder`, подпись «ART MISSING: <slug>.svg») и названный недостающий файл (N6).

Формы JSX (спека называет только режимы; формы — решение этой задачи, не проверено данными):
  component — SVGR: `import XArt from "./assets/x.svg?react"` (vite-plugin-svgr) либо
              `import { ReactComponent as XArt } from "./assets/x.svg"` (react-scripts); сборщик определяется по
              package.json, при отсутствии признаков берётся `?react` с предупреждением в ответе;
  url       — `import xUrl from "./assets/x.svg"` + `<img src={xUrl} … alt="" />` (одна форма для всех сборщиков);
  inline    — `import xMarkup from "./assets/x.svg?raw"` + `<span dangerouslySetInnerHTML={{ __html: xMarkup }} />`
              (разметку svg через контекст модели не гоняем; raw-импорт — возможность Vite, иначе предупреждение).
Путь импорта — от файла в КОРНЕ moduleDir; width/height — из узла канона, когда они числа (fill/hug — не пишутся).
Размер svg-файла образца (`Modal.svg`, экспорт кадра: viewBox + width/height в 6 знаках, служебные `effect` в
defs) проверен валидатором на реальном файле.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import canon as C
from . import config as CFG
from . import track as TR
from .ingest import art as AR

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SVG_BYTES = 10 * 1024 * 1024
LIST_LIMIT = 20
PLACEHOLDER_MIN = 32                  # сторона placeholder'а, когда размер узла не число (fill/hug)
FORBIDDEN_TOP = (".git", ".daiver", ".gigacode", "node_modules")
_DOCTYPE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_ACTIVE = re.compile(r"<\s*script\b|\son[a-z]+\s*=|javascript\s*:", re.IGNORECASE)
_LEN = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(?:px)?\s*$")
_NUM = re.compile(r"^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")


class SvgAssetsError(ValueError):
    """Вызов или файл не годятся — сообщение называет причину и что делать."""


def _rel(root: Path, p: Path) -> str:
    """Путь от корня репо; символическая ссылка в последнем звене не раскрывается (показываем её саму)."""
    p = Path(p)
    return (p.parent.resolve() / p.name).relative_to(Path(root).resolve()).as_posix()


# ── проверка .svg ────────────────────────────────────────────────────────────

def _length(v: Optional[str]) -> Optional[float]:
    m = _LEN.match(v) if isinstance(v, str) else None
    return C.round_num(float(m.group(1)), 2) if m else None


def check_svg(raw: bytes) -> Dict[str, Any]:
    """Файл — настоящий svg без активного содержимого; возвращает {width?, height?, viewBox?} (что файл объявил)."""
    if len(raw) > MAX_SVG_BYTES:
        raise SvgAssetsError(f"файл {len(raw) // 1024} КБ — больше {MAX_SVG_BYTES // (1 << 20)} МБ: это не "
                             f"экспорт одного рисунка")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SvgAssetsError("файл не UTF-8 — это не svg-экспорт Pixso")
    if not text.strip():
        raise SvgAssetsError("файл пуст")
    if _DOCTYPE.search(text):
        raise SvgAssetsError("в svg есть DOCTYPE/ENTITY — такой файл не принимается (раскрытие сущностей); "
                             "экспортируйте узел из Pixso заново")
    if _ACTIVE.search(text):
        raise SvgAssetsError("в svg есть скрипт, обработчик on*= или javascript: — активное содержимое не принимается")
    try:
        el = ET.fromstring(raw)
    except ET.ParseError as e:
        raise SvgAssetsError(f"файл не разбирается как XML: {e}")
    tag = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""
    if tag != "svg":
        raise SvgAssetsError(f"корневой элемент <{tag}>, ожидается <svg> — это не svg-экспорт")
    info: Dict[str, Any] = {}
    for key in ("width", "height"):
        v = _length(el.get(key))
        if v is not None:
            info[key] = v
    vb = el.get("viewBox")
    if isinstance(vb, str):
        parts = [p for p in re.split(r"[\s,]+", vb.strip()) if p]
        if len(parts) == 4 and all(_NUM.match(p) for p in parts):
            info["viewBox"] = " ".join(parts)
    return info


def _put(dest: Path, data: bytes) -> str:
    """Атомарная запись байтов: created | replaced | unchanged (тот же файл — не пишем)."""
    if dest.is_file():
        if dest.read_bytes() == data:
            return "unchanged"
        status = "replaced"
    else:
        status = "created"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(dest))
    return status


# ── арты кадра ───────────────────────────────────────────────────────────────

def load_arts(root: Path, manifest: dict, frame: dict) -> Tuple[List[dict], List[str]]:
    """(арты кадра по его канону без повторов slug, предупреждения). Канона нет — отказ с шагом «normalize»."""
    cp = TR.frame_paths(root, manifest, frame["name"])["canonical"]
    if not cp.is_file():
        raise SvgAssetsError(f"канона кадра «{frame['name']}» нет — сначала daiver_normalize: slug артов берутся из него")
    try:
        env = C.read_canonical(cp)
    except C.CanonError as e:
        raise SvgAssetsError(f"канон кадра не читается ({e}) — запустите daiver_normalize заново")
    warnings: List[str] = []
    req = frame.get("requirement") or {}
    src = (env.get("stats") or {}).get("sourceSha256")
    if src and req.get("sha256") and src != req["sha256"]:
        warnings.append("канон построен по другому экспорту, чем записан в манифесте (требование перетянуто) — "
                        "slug могли измениться: запустите daiver_normalize")
    seen: set = set()
    arts: List[dict] = []
    for a in AR.collect_vector_art(env["root"]):
        if not SLUG_RE.match(str(a["slug"])):
            raise SvgAssetsError(f"slug «{a['slug']}» в каноне не годится как имя файла — запустите daiver_normalize заново")
        if a["slug"] not in seen:
            seen.add(a["slug"])
            arts.append(a)
    return arts, warnings


def missing_slugs(arts: List[dict], assets_dir: Path) -> List[str]:
    """slug артов без файла; арт под непрозрачным слоем (occluded) — не контент экрана, файла не просит."""
    return [a["slug"] for a in arts if not a.get("occluded") and not (Path(assets_dir) / f"{a['slug']}.svg").is_file()]


NEXT_MISSING = ("иллюстрации строятся из .svg: получите экспорт узлов из missingArt (Pixso: экспорт слоя → SVG), "
                "положите файлы в репо и зарегистрируйте daiver_svg_assets(track, slug, svgPath); без файла сборка "
                "поставит помеченный placeholder и назовёт файл")


def summary(arts: List[dict], assets_dir: Path) -> Dict[str, Any]:
    """Короткая сводка для daiver_normalize: {count, missingArt, next?}; арты не найдены — {}."""
    if not arts:
        return {}
    missing = missing_slugs(arts, assets_dir)
    out: Dict[str, Any] = {"count": len(arts), "missingArt": missing}
    if missing:
        out["next"] = NEXT_MISSING
    return out


# ── JSX под svg.render ───────────────────────────────────────────────────────

def _deps(root: Path) -> set:
    try:
        pkg = json.loads((Path(root) / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out: set = set()
    if isinstance(pkg, dict):
        for key in ("dependencies", "devDependencies"):
            d = pkg.get(key)
            if isinstance(d, dict):
                out.update(k for k in d if isinstance(k, str))
    return out


def loader_style(root: Path, render: str) -> Tuple[str, List[str]]:
    """(форма импорта, предупреждения) по package.json: react-suffix | named | url | raw."""
    deps = _deps(root)
    if render == "component":
        if "vite-plugin-svgr" in deps:
            return "react-suffix", []
        if "react-scripts" in deps:
            return "named", []
        return "react-suffix", ["SVGR-загрузчик не найден в package.json (vite-plugin-svgr или react-scripts): "
                                "импорт `?react` принят по умолчанию — подключите vite-plugin-svgr либо "
                                "переключите svg.render на url"]
    if render == "inline":
        return "raw", [] if "vite" in deps else [
            "inline: raw-импорт `?raw` — возможность Vite; для другого сборщика настройте raw-загрузку .svg "
            "либо переключите svg.render на url"]
    return "url", []


def _pascal(slug: str) -> str:
    p = "".join(w[:1].upper() + w[1:] for w in slug.split("-"))
    return "Art" + p if p[:1].isdigit() else p


def _component_name(slug: str) -> str:
    p = _pascal(slug)
    return p if slug[:1].isdigit() or p.endswith("Art") else p + "Art"


def _var_name(slug: str) -> str:
    p = _pascal(slug)
    return p[:1].lower() + p[1:]


def _fmt(v: float) -> str:
    return str(C.round_num(v, 2))


def snippet(render: str, style: str, slug: str, rel_import: str, w: Optional[float], h: Optional[float]) -> Dict[str, str]:
    """{import, jsx} для одного арта. rel_import — путь к .svg от файла в корне moduleDir («./assets/x.svg»)."""
    size = (f" width={{{_fmt(w)}}}" if w is not None else "") + (f" height={{{_fmt(h)}}}" if h is not None else "")
    if render == "component":
        name = _component_name(slug)
        if style == "named":
            imp = f'import {{ ReactComponent as {name} }} from "{rel_import}";'
        else:
            imp = f'import {name} from "{rel_import}?react";'
        return {"import": imp, "jsx": f"<{name}{size} />"}
    if render == "url":
        var = _var_name(slug) + "Url"
        return {"import": f'import {var} from "{rel_import}";', "jsx": f'<img src={{{var}}}{size} alt="" />'}
    var = _var_name(slug) + "Markup"
    return {"import": f'import {var} from "{rel_import}?raw";',
            "jsx": f"<span dangerouslySetInnerHTML={{{{ __html: {var} }}}} />"}


def placeholder(slug: str, w: Optional[float], h: Optional[float]) -> str:
    """Помеченный placeholder правильного размера (N6): метка slug'а и название недостающего файла прямо в разметке."""
    label = f"ART MISSING: {slug}.svg"
    dims = [f"width: {_fmt(w)}" if w is not None else f"minWidth: {PLACEHOLDER_MIN}",
            f"height: {_fmt(h)}" if h is not None else f"minHeight: {PLACEHOLDER_MIN}"]
    style = ", ".join(dims + ['boxSizing: "border-box"', 'border: "1px dashed currentColor"', 'display: "flex"',
                              'alignItems: "center"', 'justifyContent: "center"', "fontSize: 11"])
    return (f'<div data-art-placeholder="{slug}" role="img" aria-label="{label}" '
            f'style={{{{ {style} }}}}>{label}</div>')


# ── действия ─────────────────────────────────────────────────────────────────

def register(root: Path, assets_dir: Path, slug: str, src: Path) -> Dict[str, Any]:
    if not src.is_file():
        raise SvgAssetsError(f"файла {src.name} нет — путь svgPath внутри репо, файл — экспорт узла из Pixso (SVG)")
    size = src.stat().st_size
    if size > MAX_SVG_BYTES:
        raise SvgAssetsError(f"{src.name}: {size // (1 << 20)} МБ — больше {MAX_SVG_BYTES // (1 << 20)} МБ, "
                             f"это не экспорт одного рисунка")
    raw = src.read_bytes()
    try:
        info = check_svg(raw)
    except SvgAssetsError as e:
        raise SvgAssetsError(f"{src.name}: {e}")
    dest = Path(assets_dir) / f"{slug}.svg"
    try:
        status = _put(dest, raw)
    except OSError as e:
        raise SvgAssetsError(f"не удалось записать {_rel(root, dest)}: {e}")
    return {"slug": slug, "file": _rel(root, dest), "status": status, "bytes": len(raw), "svg": info}


def _module_dir(root: Path, cfg: dict, module_dir: Path) -> Tuple[Path, Path]:
    """(moduleDir, каталог файлов в модуле). Только внутри project.outputDir; служебные каталоги закрыты."""
    out_dir, adir = cfg["project"]["outputDir"], cfg["svg"]["assetsDir"]
    for what, val in (("project.outputDir", out_dir), ("svg.assetsDir", adir)):
        problem = TR.rel_problem(val, what)
        if problem:
            raise SvgAssetsError(f"конфиг: {problem}")
    base, repo = (Path(root) / out_dir).resolve(), Path(root).resolve()
    mdir = Path(module_dir).resolve()
    try:
        rel_repo = mdir.relative_to(repo)
        mdir.relative_to(base)
    except ValueError:
        raise SvgAssetsError(f"moduleDir вне outputDir «{out_dir}» из конфига: модули строятся только там")
    if rel_repo.parts and rel_repo.parts[0] in FORBIDDEN_TOP:
        raise SvgAssetsError(f"moduleDir в служебном каталоге «{rel_repo.parts[0]}» — модуль там не строится")
    dest_dir = (mdir / adir).resolve()
    try:
        dest_dir.relative_to(repo)
    except ValueError:
        raise SvgAssetsError(f"каталог файлов {adir} в модуле ведёт за пределы репо (символическая ссылка?)")
    return mdir, dest_dir


def to_module(root: Path, cfg: dict, targets: List[dict], assets_dir: Path, module_dir: Path) -> Dict[str, Any]:
    render, adir = cfg["svg"]["render"], Path(cfg["svg"]["assetsDir"]).as_posix()
    mdir, dest_dir = _module_dir(root, cfg, module_dir)
    style, notes = loader_style(root, render)
    items: List[Dict[str, Any]] = []
    for a in targets:
        slug, w, h = a["slug"], a.get("w"), a.get("h")
        src = Path(assets_dir) / f"{slug}.svg"
        if not src.is_file():
            items.append({"slug": slug, "status": "missing", "file": None, "import": None,
                          "jsx": placeholder(slug, w, h), "placeholder": True, "missingFile": _rel(root, src)})
            continue
        dest = dest_dir / f"{slug}.svg"
        try:
            status = _put(dest, src.read_bytes())
        except OSError as e:
            raise SvgAssetsError(f"не удалось записать {_rel(root, dest)}: {e}")
        items.append(dict({"slug": slug, "status": status, "file": _rel(root, dest)},
                          **snippet(render, style, slug, f"./{adir}/{slug}.svg", w, h)))
    return {"dir": _rel(root, mdir), "render": render, "assetsDir": adir, "items": items, "_notes": notes}


def run(root: Path, track: str, frame: Optional[str] = None, slug: Optional[str] = None,
        svg_path: Optional[Path] = None, module_dir: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root)
    manifest = TR.load(root, track)
    if manifest is None:
        raise SvgAssetsError(f"трека «{track}» нет — сначала daiver_pixso_pull")
    f = TR.get_frame(manifest, frame)
    assets_dir = TR.frame_paths(root, manifest, f["name"])["assets"]
    if svg_path is not None and slug is None:
        raise SvgAssetsError("svgPath без slug: укажите, какому арту принадлежит файл (slug из missingArt)")
    arts, warnings = load_arts(root, manifest, f)
    by_slug = {a["slug"]: a for a in arts}
    if slug is not None:
        if not SLUG_RE.match(slug):
            raise SvgAssetsError(f"slug «{slug}»: латиница нижнего регистра, цифры и «-» — берите из канона (missingArt)")
        if slug not in by_slug:
            known = ", ".join(sorted(by_slug)[:LIST_LIMIT]) or "артов в кадре нет"
            raise SvgAssetsError(f"slug «{slug}» в каноне кадра нет (есть: {known}) — slug не придумывается")
    out: Dict[str, Any] = {"track": track, "frame": f["name"], "assetsDir": _rel(root, assets_dir)}
    if svg_path is not None:
        out["registered"] = register(root, assets_dir, slug, Path(svg_path))
        if not out["registered"]["svg"]:
            warnings.append("в svg нет ни viewBox, ни width/height — размер при вставке не определён")
    if module_dir is not None:
        cfg = CFG.load(root)["config"]
        targets = [by_slug[slug]] if slug is not None else [a for a in arts if not a.get("occluded")]
        mod = to_module(root, cfg, targets, assets_dir, Path(module_dir))
        warnings.extend(mod.pop("_notes"))
        out["module"] = mod
    view = []
    for a in arts:
        rec = dict(a)
        file = assets_dir / f"{a['slug']}.svg"
        rec["file"] = _rel(root, file) if file.is_file() else None
        view.append(rec)
    out["arts"] = view[:LIST_LIMIT]
    if len(view) > LIST_LIMIT:
        out["artsTotal"] = len(view)
    missing = missing_slugs(arts, assets_dir)
    out["missingArt"] = missing
    known_files = {f"{a['slug']}.svg" for a in arts}
    orphans = sorted(p.name for p in assets_dir.glob("*.svg") if p.name not in known_files) if assets_dir.is_dir() else []
    if orphans:
        out["orphans"] = orphans[:LIST_LIMIT]
    if warnings:
        out["warnings"] = warnings
    out["next"] = (NEXT_MISSING if missing else "все арты кадра зарегистрированы" if arts
                   else "в кадре нет артов — .svg не нужны")
    return out
