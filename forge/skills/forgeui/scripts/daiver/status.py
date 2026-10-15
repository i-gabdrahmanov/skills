"""status.py — daiver_status: первый ход агента — состояние forge-ui с диска (спека §15.7).

Этап 0: инициализация проекта и список треков. Лестница трека (requirement → normalized →
planned → building → built → verifying → clean) появляется вместе с механикой этапов 1–4.
Каталог `.daiver/` руками не листается — какая ступень у трека, решает код.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from . import VERSION
from . import config as C


def status(root: Path) -> Dict[str, Any]:
    root = Path(root)
    cfg = C.load(root)
    tracks_dir = root / ".daiver" / "tracks"
    tracks = sorted(p.name for p in tracks_dir.iterdir() if p.is_dir()) if tracks_dir.is_dir() else []
    c = cfg["config"]
    summary = {
        "framework": c["project"]["framework"], "language": c["project"]["language"],
        "styling": c["project"]["styling"]["mode"], "outputDir": c["project"]["outputDir"],
        "buildCommand": c["project"]["buildCommand"], "librarySource": c["library"]["source"],
        "vision": "on" if c["verify"]["vision"] else "off",
    } if cfg["exists"] else None
    if not cfg["initialized"]:
        next_step = ("init: спроси настройки проекта (daiver_init без ответов — кандидаты с "
                     "доказательствами), затем daiver_init с ответами пользователя")
    else:
        next_step = "ui-spec: фронт-спека стори (ui-spec.md)"
    return {
        "engine": VERSION,
        "initialized": cfg["initialized"],
        "config": summary,
        "problems": cfg["problems"],
        "warnings": cfg["warnings"],
        "tracks": tracks,
        "nextStep": next_step,
    }
