"""verify_gate.py — гейт шага ui-verify (`ui_gate.py verify`; спека §14.1, §3.1; задача 4.8; INV-C21, F12).

Шаг закрыт, когда цикл ЗАКОНЧЕН — а заканчивает его человек, не модель: у КАЖДОГО кадра трека последняя записанная итерация чистая ИЛИ человек
остановил цикл (`decision: stop`) и дал согласие `ui-accept-<KEY>` с цитатой (итог `defects-remaining` честно остаётся в `verify-report.md`).
Нет записанной итерации у кадра — шаг не закрыт (непроверенный кадр не «чист»). Гейт только читает диск и не пересчитывает ничего:
`clean` записан движком (`state.record`) по артефактам итерации.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .. import track as TR
from . import verify_report as VR


def check(root: Any, track: str) -> Dict[str, Any]:
    """{problems: [текст], outcome: clean | defects-remaining, frames: {имя: статус}}; problems пуст — шаг можно закрывать."""
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        return {"problems": [f"трек «{track}» не найден (.daiver/tracks/{track}/track.yaml)"], "outcome": None, "frames": {}}
    problems: List[str] = []
    frames: Dict[str, str] = {}
    consent = VR.accepted(root, track)
    for f in manifest.get("frames") or []:
        path = TR.frame_paths(root, manifest, f["name"])["verifyState"]
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError, UnicodeDecodeError):
            problems.append(f"кадр «{f['name']}»: verify-state.json не читается — цикл проверки не восстановить (файл пишет только движок)")
            frames[f["name"]] = "unreadable"
            continue
        history = (data or {}).get("history") if isinstance(data, dict) else None
        if not isinstance(history, list) or not history:
            problems.append(f"кадр «{f['name']}»: не проверялся — нет ни одной записанной итерации (daiver_verify_state → гейты → "
                            f"daiver_verify_state_record)")
            frames[f["name"]] = "not-checked"
            continue
        last = history[-1]
        if last.get("clean") is True:
            frames[f["name"]] = "clean"
            continue
        frames[f["name"]] = "defects-remaining"
        n = len(last.get("blocking") or [])
        if last.get("decision") == "stop":
            if not consent:
                problems.append(f"кадр «{f['name']}»: человек остановил цикл с открытыми дефектами ({n}), но согласия ui-accept-{track} нет — "
                                f"оркестратор записывает его после явного «да» (record_approval.py --key ui-accept-{track} --evidence \"<ответ>\")")
            continue
        problems.append(f"кадр «{f['name']}»: итерация {last.get('iteration')} не чистая (блокирующих: {n}), решения «стоп» нет — цикл заканчивает "
                        f"человек: чинить (переоткрыть ui-build) или остановить с согласием ui-accept-{track}")
    outcome = "clean" if frames and all(v == "clean" for v in frames.values()) else "defects-remaining"
    return {"problems": problems, "outcome": outcome, "frames": frames}
