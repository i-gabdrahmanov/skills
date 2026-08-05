#!/usr/bin/env python3
"""phase-gate.py — Stop-хук: не дать агенту завершить ответ с «висящим» шагом пайплайна.

Ловит ситуацию «субагент/фаза помечена in_progress, но не закрыта (completed/failed)» —
агент уходит из хода, оставив пайплайн в подвешенном состоянии. Блокирует завершение один
раз с инструкцией закрыть шаг (или пометить failed).

Защита от петли: если рантайм уже перезапустил нас из-за предыдущего блока
(stop_hook_active == true) — НЕ блокируем повторно (decision allow), иначе агент зациклится.

Не вмешивается, если пайплайна нет (manifest отсутствует) или висящих шагов нет.

Вывод: JSON в stdout `{"decision": "block", "reason": "..."}` для блокировки; иначе exit 0.
"""
from __future__ import annotations

import json
import sys

import risk_ladder as R


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        # уже в перезапуске из-за нашего же блока — не зацикливаемся
        if data.get("stop_hook_active"):
            return 0

        root = R.project_root(data.get("cwd", ""))
        mp = R.active_manifest(root)  # активная фича (newest манифест, кроме archived)
        dangling = []

        if mp and mp.exists():
            manifest = json.loads(mp.read_text(encoding="utf-8"))
            dangling = [s.get("id") for s in manifest.get("steps", [])
                        if s.get("status") == "in_progress"]
        else:
            # Fallback на gate.json фичи если manifest не найден
            from _project import active_feature, gate_file
            gate_path = gate_file(root, active_feature(root))
            if gate_path.exists():
                try:
                    gate = json.loads(gate_path.read_text(encoding="utf-8"))
                    dangling = [p.get("id") for p in gate.get("phases", [])
                                if p.get("status") == "in_progress"]
                except Exception:
                    pass

        if not dangling:
            return 0

        reason = (
            "Пайплайн оставлен в подвешенном состоянии: шаги "
            f"{dangling} помечены in_progress, но не закрыты. "
            "Закрой их через pipeline-state/update.py (status=completed после прохождения "
            "gate, либо status=failed с --error), затем заверши ход."
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
