"""consent.py — согласия человека для шагов spec-graph через механизм forge (D13).

Решение человека — не слова модели, а маркер в `ground/approvals.jsonl`, который пишет только
`record_approval.py` (state-write-guard не даёт модели писать туда напрямую) с цитатой пользователя
(префиксы `graph-*` — в `forge_events.CONSENT_PREFIXES`). Согласие привязывается к файлу (`--bind-file`):
изменилось содержимое — согласие устарело и снимается, человек решает снова.

Ключ — `<префикс>-<KEY прогона>` (`graph-catalog-GRAPH-1`); вне прогона — `…-adhoc`.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import metrics as M

HOOKS = Path(__file__).resolve().parents[4] / "hooks"
PIPELINE_STATE = Path(__file__).resolve().parents[3] / "pipeline-state" / "scripts"


def _fe():
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    import forge_events  # noqa: WPS433 — общий журнал событий и согласий forge
    return forge_events


def key(root: Path, prefix: str) -> str:
    return f"{prefix}-{M.current_key(root)}"


def sha256_file(path: Path) -> Optional[str]:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def state(root: Path, prefix: str, bound_file: Path) -> Dict[str, Any]:
    """Состояние согласия: ok | none (нет) | unbound (без --bind-file) | stale (файл изменился)."""
    k = key(root, prefix)
    fe = _fe()
    rec = fe.approval(Path(root), k)
    sha = fe.file_sha256(Path(bound_file)) if Path(bound_file).is_file() else None
    rel = Path(bound_file).resolve().relative_to(Path(root).resolve()).as_posix()
    how = (f"после явного «да» пользователя оркестратор: python3 <поставка>/skills/pipeline-state/scripts/record_approval.py "
           f"--project . --key {k} --approved-by user --reason \"<что утверждено>\" --evidence \"<ответ дословно>\" "
           f"--bind-file {rel}")
    if rec is None:
        return {"state": "none", "key": k, "how": how}
    bound = rec.get("bound_sha256")
    if not bound:
        return {"state": "unbound", "key": k, "how": how}
    if bound != sha:
        return {"state": "stale", "key": k, "how": how}
    return {"state": "ok", "key": k}


def revoke(root: Path, prefix: str, reason: str) -> None:
    _fe().revoke_approval(Path(root), key(root, prefix), reason=reason)
