"""ingest/normalize.py — daiver_normalize: требование кадра → каноническая модель в папке кадра.

Единственная точка входа нормализации (INV-B2): формат определяет код по содержимому файла, агент
адаптер не выбирает и `outPath` не передаёт — канон всегда ложится в папку своего кадра. Файл
требования сверяется с хешем из манифеста: изменённый мимо pull файл (stale) нормализовать нельзя —
канон строился бы по одному, а манифест и подпись плана ссылались бы на другое.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .. import canon as C
from .. import track as TR
from ..pixso import dsl as D
from . import detect
from . import pixso as PX

FIGMA_PENDING = ("формат {fmt} распознан, но адаптера пока нет (задача X.2: нужны обезличенные образцы "
                 "экспорта Figma). Pixso-экспорт кадра берёт daiver_pixso_pull")


class NormalizeError(ValueError):
    pass


def normalize_frame(root: Path, track: str, frame: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root)
    try:
        manifest = TR.load(root, track)
    except TR.TrackError as e:
        raise NormalizeError(str(e))
    if manifest is None:
        raise NormalizeError(f"трека «{track}» нет — сначала daiver_pixso_pull")
    try:
        f = TR.get_frame(manifest, frame)
    except TR.TrackError as e:
        raise NormalizeError(str(e))
    if not f.get("requirement"):
        raise NormalizeError(f"у кадра «{f['name']}» нет требования — сначала daiver_pixso_pull")
    status = TR.requirement_status(root, manifest, f["name"])
    paths = TR.frame_paths(root, manifest, f["name"])
    if status == "missing":
        raise NormalizeError(f"файла требования {paths['requirement'].name} нет — тяните кадр заново (daiver_pixso_pull)")
    if status == "stale":
        raise NormalizeError(f"{paths['requirement'].name} изменён мимо pull: хеш не совпал с манифестом. "
                             f"Канон строился бы по одному файлу, а подпись плана ссылалась бы на другой — "
                             f"тяните кадр заново (daiver_pixso_pull)")
    fmt, why, doc = detect.detect_file(paths["requirement"])
    if fmt is None:
        raise NormalizeError(f"формат требования не распознан ({why}): угадывать макет нельзя")
    if fmt != "pixso":
        raise NormalizeError(FIGMA_PENDING.format(fmt=fmt))
    try:
        env = PX.normalize_doc(doc, source_sha256=f["requirement"]["sha256"])
        written = C.write_canonical(paths["canonical"], env)
    except (D.DslError, ValueError) as e:
        raise NormalizeError(str(e))
    stats = written["stats"]
    return {
        "track": track, "frame": f["name"], "format": fmt,
        "canonical": paths["canonical"].resolve().relative_to(root.resolve()).as_posix(),
        "bytes": paths["canonical"].stat().st_size,
        "rootId": written["root"]["id"], "rootHash": C.subtree_hash(written["root"]),
        "weight": C.weight(written["root"]),
        "stats": {k: v for k, v in stats.items() if k != "warnings"},
        "warnings": stats.get("warnings", []),
    }

