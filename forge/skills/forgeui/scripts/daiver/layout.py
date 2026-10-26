"""layout.py — переезд семьи первого кадра в `frames/<имя>/` при появлении второго (спека §4.2; задача 6.1; INV-C3).

Один кадр держит свою семью файлов прямо в папке трека (`dir: "."`); со второго кадра ВСЕ кадры равноправны и живут в `frames/<имя>/`. Переезд делает инструментальный вызов,
который добавил второй кадр (`daiver_pixso_pull`): двигается семья первого кадра, манифест пишет `track.upsert_frame` (там раскладка `dir`), пути в состоянии и отчётах
переписываются, ответ называет это строкой «LAYOUT — …». Обратно ничего не движется.

Что семья кадра, а что собственное трека — по ИМЕНАМ, не «всё остальное»: семью называет список `FAMILY` (требование и сырой экспорт, растр кадра, канон, арты, план, резолвы, build-state,
verify-state, `verify/`, `versions/`, `baseline/`) плюс имена из самого манифеста (`requirement.path`, `raster`) и `goal.*`. Собственные файлы трека не переезжают никогда (`track.yaml`,
`run.json`, спеки, `ui-plan.md`, `plan.lock.json`, `resolution-report.md`, `verify-report.md`, `verify-decisions.json`, телеметрия), как и чужие — файл пользователя, которого нет в списке, остаётся на месте.

Что переписывается после переезда: пути вида `.daiver/tracks/<трек>/<имя семьи>…` внутри JSON и Markdown ПЕРЕЕХАВШЕЙ семьи (`verify-state.json`: `history[].dir`, `reviewReport.path`; артефакты итераций),
и `verify-report.md` трека — он пересобирается из состояния. НЕ переписываются `plan.json`, `plan.md`, `resolutions.json` и `resolution-report.md`: их хеши связаны с подписью человека (`plan.lock.json`,
`record_approval --bind-file`), а переезд кадра не повод её отзывать. Переписывание — после структурного переезда и записи манифеста; сбой на одном файле — предупреждение, остальные идут дальше.

Сам переезд обратим до записи манифеста: сбой перекладывания или записи — всё возвращается на место (`Migration.rollback`), манифест не теряет первый кадр.

Старый путь продолжает резолвиться: `redirect` отдаёт новое место файла, если по старому пути ничего нет, а в `frames/<первичный>/` оно есть.

Не проверено данными (боевых треков нет): переезд на Windows (открытый в другой программе файл не переименуется — сбой с откатом, а не полупереезд), очень большие `verify/`
(переезд — переименования, не копии, но на другой файловой системе внутри трека такого нет).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import track as TR

FAMILY = ("visual-requirements.pixso.json", "visual-requirements.pixso.raw.json", "visual-requirements.json", "visual-requirements.html", "visual-requirements.png",
          "visual-requirements.jpg", "visual-requirements.jpeg", "visual-requirements.canonical.json", "assets", "plan.json", "plan.md", "resolutions.json",
          "build-state.json", "verify-state.json", "verify", "versions", "baseline")
NO_REWRITE = ("plan.json", "plan.md", "resolutions.json")          # хеши этих файлов связаны с подписью плана
TRACK_OWN = ("track.yaml", "run.json", "ui-plan.md", "plan.lock.json", "resolution-report.md", "verify-report.md", "verify-decisions.json", "tele.json", "tele.export.json", "frames")
REWRITE_SUFFIXES = (".json", ".md")
MAX_REWRITE_BYTES = 64 * 1024 * 1024


class LayoutError(ValueError):
    """Переезд не удался — сообщение называет причину; на диске всё как было."""


def family_names(frame: Dict[str, Any], track_dir: Path) -> List[str]:
    """Имена записей семьи кадра, существующие в папке трека (в стабильном порядке)."""
    names = list(FAMILY)
    req = (frame.get("requirement") or {}).get("path")
    if isinstance(req, str) and "/" not in req:
        names.append(req)
    if isinstance(frame.get("raster"), str) and "/" not in frame["raster"]:
        names.append(frame["raster"])
    if track_dir.is_dir():
        names += sorted(p.name for p in track_dir.iterdir() if p.name.startswith("goal.") and p.is_file())
    out: List[str] = []
    for n in names:
        if n in TRACK_OWN or n in out:
            continue
        if (track_dir / n).exists():
            out.append(n)
    return out


class Migration:
    """Итог переезда: что перенесено и что переписано; `rollback()` возвращает перенесённое на место."""

    def __init__(self, frame: str, base: Path, target: Path):
        self.frame, self.base, self.target = frame, base, target
        self.moved: List[str] = []
        self.rewritten: List[str] = []
        self.warnings: List[str] = []
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

    def message(self, track: str, new_frame: str) -> str:
        return (f"LAYOUT — в треке «{track}» появился второй кадр «{new_frame}»: кадры равноправны и живут в frames/<имя>/; семья кадра «{self.frame}» переехала в "
                f"{self.target.relative_to(self.base).as_posix()}/ ({len(self.moved)} записей), пути в состоянии и отчётах переписаны. Старые пути продолжают находить файлы по новому месту; "
                f"собственные файлы трека (track.yaml, run.json, спеки, ui-plan.md, plan.lock.json, resolution-report.md, verify-report.md, verify-decisions.json) остались на месте")


def check_target(root: Path, track: str, frame: Dict[str, Any]) -> None:
    """Предпроверка переезда: цель свободна. Вызывается ДО записи чего бы то ни было, чтобы отказ не оставлял за собой файлов нового кадра."""
    base = TR.track_dir(Path(root), track)
    target = base / "frames" / frame["name"]
    clash = [n for n in family_names(frame, base) if (target / n).exists()]
    if clash:
        raise LayoutError(f"frames/{frame['name']}/ уже содержит {', '.join(clash[:5])}: переезд не начат, чтобы не затереть — разберись с каталогом вручную")


def migrate(root: Path, track: str, frame: Dict[str, Any]) -> Migration:
    """Переносит семью `frame` (кадр с `dir: "."` до появления второго) в `frames/<имя>/`. Манифест НЕ пишет: это делает вызывающий сразу после (и при сбое зовёт `rollback`).
    Цель уже занята — отказ без единого движения."""
    base = TR.track_dir(Path(root), track)
    name = frame["name"]
    target = base / "frames" / name
    names = family_names(frame, base)
    clash = [n for n in names if (target / n).exists()]
    if clash:
        raise LayoutError(f"frames/{name}/ уже содержит {', '.join(clash[:5])}: переезд не начат, чтобы не затереть — разберись с каталогом вручную")
    mig = Migration(name, base, target)
    mig.created_target = not target.exists()
    failed = name
    try:
        target.mkdir(parents=True, exist_ok=True)
        for n in names:
            failed = n
            os.replace(str(base / n), str(target / n))
            mig.moved.append(n)
    except OSError as e:
        mig.rollback()
        raise LayoutError(f"переезд семьи кадра «{name}» не удался на «{failed}» ({e.strerror or e}); всё возвращено на место, второй кадр не заведён")
    return mig


def _pattern(track: str) -> "re.Pattern[str]":
    names = "|".join(re.escape(n) for n in sorted(FAMILY, key=len, reverse=True))
    return re.compile(r"(\.daiver/tracks/" + re.escape(track) + r"/)(" + names + r")(?=[/\"'\s)\]\},:;`]|$)")


def rewrite_paths(root: Path, track: str, mig: Migration) -> None:
    """Старые пути семьи → новое место внутри JSON/Markdown переехавших записей (кроме файлов под подписью). Сбой файла — предупреждение."""
    pat = _pattern(track)
    repl = r"\1frames/" + mig.frame + r"/\2"
    needle = f".daiver/tracks/{track}/"
    files: List[Path] = []
    for n in mig.moved:
        p = mig.target / n
        if p.is_file() and n not in NO_REWRITE:
            files.append(p)
        elif p.is_dir():
            files += [q for q in sorted(p.rglob("*")) if q.is_file()]
    for p in files:
        if p.suffix not in REWRITE_SUFFIXES:
            continue
        try:
            if p.stat().st_size > MAX_REWRITE_BYTES:
                mig.warnings.append(f"{p.relative_to(mig.base).as_posix()}: больше {MAX_REWRITE_BYTES >> 20} МБ — пути внутри не переписаны")
                continue
            text = p.read_text(encoding="utf-8")
            if needle not in text:
                continue
            new = pat.sub(repl, text)
            if new != text:
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(new, encoding="utf-8")
                os.replace(str(tmp), str(p))
                mig.rewritten.append(p.relative_to(mig.base).as_posix())
        except (OSError, UnicodeDecodeError) as e:
            mig.warnings.append(f"{p.relative_to(mig.base).as_posix()}: пути не переписаны ({e})")


def rebuild_report(root: Path, manifest: dict, mig: Migration) -> None:
    """`verify-report.md` трека пересобирается из состояния кадров (он не переезжает, а его текст ссылался на старые пути)."""
    if not (mig.target / "verify-state.json").is_file():
        return
    from .verify import verify_report as VR            # импорт здесь: verify тянет за собой браузерные модули, а pull без них не должен падать
    try:
        VR.write(Path(root), manifest)
    except OSError as e:
        mig.warnings.append(f"verify-report.md не пересобран ({e})")


# ── старые пути ──────────────────────────────────────────────────────────────

def redirect(root: Path, path: Path) -> Path:
    """Старый путь семьи первого кадра → новое место: `.daiver/tracks/<T>/<имя семьи>/…` без файла, а в `frames/<первичный>/` он есть. Остальные пути — как есть."""
    try:
        base = (Path(root) / TR.TRACKS_REL).resolve()
        rel = Path(path).resolve().relative_to(base)
    except (ValueError, OSError):
        return path
    parts = rel.parts
    if len(parts) < 2 or Path(path).exists():
        return path
    first = parts[1]
    if first not in FAMILY and not first.startswith("goal."):
        return path
    try:
        manifest, _ = TR.load_safe(Path(root), parts[0])
    except TR.TrackError:
        return path
    frames = (manifest or {}).get("frames") or []
    if len(frames) < 2 or not str(frames[0].get("dir", ".")).startswith("frames/"):
        return path
    moved = base / parts[0] / frames[0]["dir"] / Path(*parts[1:])
    return moved if moved.exists() else path
