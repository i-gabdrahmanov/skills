"""reset.py — «начать граф заново»: состояние движка и vault уходят в архив, конфиг остаётся.

Боевой прогон 2026-11-07: на «заархивируй граф, строим новый» модель перенесла vault руками, а журнал
решений каталога (36 записей), очереди и снимок каталога остались в .spec-graph/ — новый прогон
унаследовал решения прошлой попытки, и они висели непринятыми (decision_problems) при каждом
graph_catalog. Здесь сброс целиком и обратимо: перенос, не удаление.

Живой прогон (шаг не закрыт) не трогаем: какой прогон брошен, решает пользователь, а снимает его
pipeline-state (archive.py abandon, согласие R4) — иначе гейты прогона смотрели бы на пустое состояние.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Dict

from . import status as ST

ARCHIVE_REL = ".spec-graph/archive"
# остаётся: настройки пользователя и движка, метрики прогонов, замок проекта (его держит сам вызов)
KEEP = {"config.json", "ontology.local.json", "obsidian.json", "salt", "runs", ".lock", "archive"}


class ResetError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def archive(root: Path, cfg: Dict[str, Any], reason: str) -> Dict[str, Any]:
    root = Path(root)
    run = ST.run_info(root)
    if run.get("step") is not None:
        cmd = (f"{ST.script('pipeline-state/scripts/archive.py')} --project {ST.q(root)} abandon {run['key']} "
               f"--skill specgraph --reason \"<почему>\"")
        raise ResetError(f"прогон {run['key']} не закрыт (шаг {run['step']}): граф заново — только после того, как "
                         f"пользователь ЯВНО скажет бросить этот прогон; тогда запиши согласие abandon-{run['key']} "
                         f"(record_approval.py, его цитата) и выполни: {cmd}. Затем снова graph_archive",
                         "E-reset-live-run")
    state = root / ".spec-graph"
    dst = root / ARCHIVE_REL / time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while dst.exists():
        dst = dst.with_name(f"{dst.name}-{n}")
        n += 1
    moved = []
    for p in sorted(state.iterdir()) if state.is_dir() else []:
        if p.name in KEEP:
            continue
        (dst / "state").mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst / "state" / p.name))
        moved.append(f".spec-graph/{p.name}")
    vault = root / cfg["vault"]
    # .obsidian/ — настройки аналитика (плагины, Bases, горячие клавиши): остаются в vault, иначе «граф заново»
    # сбрасывал Obsidian и слой видов уходил из Bases в Markdown (ревью 2026-11-08)
    content = [p for p in sorted(vault.iterdir()) if p.name != ".obsidian"] if vault.is_dir() else []
    if content:
        (dst / "vault").mkdir(parents=True, exist_ok=True)
        for p in content:
            shutil.move(str(p), str(dst / "vault" / p.name))
        moved.append(cfg["vault"])
        if not any(vault.iterdir()):
            vault.rmdir()
    if not moved:
        return {"archived": None, "moved": [], "next": "архивировать нечего — граф и так пуст; graph_status"}
    (dst / "REASON.txt").write_text(reason.strip() + "\n", encoding="utf-8")
    rel = dst.relative_to(root).as_posix()
    return {"archived": rel, "moved": moved, "kept": sorted(k for k in KEEP if (state / k).exists()),
            "next": f"граф с нуля: graph_status (конфиг и источники прежние). Вернуть — перенести {rel}/state/* "
                    f"обратно в .spec-graph/ и {rel}/vault/* в {cfg['vault']}/ (.obsidian/ остался на месте)"}
