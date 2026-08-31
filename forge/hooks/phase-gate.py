#!/usr/bin/env python3
"""phase-gate.py — Stop-хук: не дать агенту завершить ответ с «висящим» шагом пайплайна.

Ловит ситуацию «работа по шагу сделана, но шаг не закрыт» — агент уходит из хода, оставив
пайплайн в подвешенном состоянии. Блокирует завершение ОДИН раз с инструкцией закрыть шаг
(или пометить failed).

Что считается висящим (tasks/012). Раньше — только `status == "in_progress"`, а этот статус
на живых прогонах не проставляет НИКТО: `update.py` ведёт шаг `pending → completed`, брифы
промежуточную пометку не делают (то же самое зафиксировано в докстрингах
`risk_ladder.current_step_id` и `inline-phase-guard._active_step_id`). Хук числился активным
и не срабатывал ни разу. Теперь висящим считается и шаг в `pending`, для которого в журнале
прогона УЖЕ есть gate-result или origin: гейт прогнали, работу сделали — и не закрыли.

Защита от петли — двухслойная:
  1. `stop_hook_active` (если рантайм его шлёт) — штатный признак перезапуска после блока;
  2. одноразовый маркер на сессию в temp-каталоге. Поля `stop_hook_active` нет в перечне
     подтверждённых для форка (docs/v2/01-runtime-config-surface.md), а единственная защита,
     висящая на неподтверждённом поле, — это потенциальная бесконечная петля «не могу
     завершить ход».

Не вмешивается, если пайплайна нет (manifest отсутствует) или висящих шагов нет.

Вывод: JSON в stdout `{"decision": "block", "reason": "..."}` для блокировки; иначе exit 0.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import risk_ladder as R

try:
    import forge_events as FE
except Exception:  # pragma: no cover — бандл повреждён: деградируем до in_progress-признака
    FE = None


def _has_work_evidence(root: Path, skill: str, feature: str, step_id: str) -> bool:
    """Есть ли в журнале прогона след работы по шагу (gate-result или origin субагента)."""
    if FE is None or not step_id:
        return False
    try:
        return bool(FE.gate(root, skill, feature, step_id)
                    or FE.origin(root, skill, feature, step_id))
    except Exception:
        return False


def _already_blocked(session_id: str, signature: str) -> bool:
    """Блокировали ли уже в этой сессии по этой же причине (одноразовость без stop_hook_active).

    Маркер живёт в temp-каталоге ОС: он привязан к сессии, не засоряет ground/ и убирается
    системой сам. Любая ошибка ФС → считаем, что не блокировали (лучше лишний блок один раз,
    чем потерянная защита), но при повторном заходе сработает stop_hook_active."""
    if not session_id:
        return False
    tag = hashlib.sha256(f"{session_id}|{signature}".encode("utf-8")).hexdigest()[:16]
    marker = Path(tempfile.gettempdir()) / f"forge-phase-gate-{tag}"
    try:
        if marker.exists():
            return True
        marker.write_text(signature, encoding="utf-8")
    except OSError:
        return False
    return False


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        # NB: `stop_hook_active` здесь НЕ проверяется. На qwen-code 0.21.14 оно приходит
        # `true` на КАЖДОМ Stop, включая самый первый в свежей сессии (замерено e2e на
        # метапроекте, две независимые сессии) — то есть в качестве защиты от петли поле
        # означало «никогда не блокировать». Одноразовость держит собственный маркер сессии
        # ниже: он не зависит от семантики поля и работает на любом рантайме.

        root = R.project_root(data.get("cwd", ""))
        mp = R.active_manifest(root)  # активная фича (newest манифест, кроме archived)
        dangling = []

        # Манифест — единственный источник. Прежний фолбэк на gate.json снят вместе с самим
        # кэшем: он был производной ЭТОГО же манифеста, и без манифеста давать он мог только
        # устаревший ответ.
        if mp and mp.exists():
            manifest = json.loads(mp.read_text(encoding="utf-8"))
            skill, feature = mp.parent.parent.name, mp.parent.name
            for s in manifest.get("steps", []):
                sid, st = s.get("id"), s.get("status")
                if not sid:
                    continue
                if st == "in_progress":
                    dangling.append(sid)
                elif st == "pending" and _has_work_evidence(Path(root), skill, feature, sid):
                    dangling.append(sid)

        if not dangling:
            return 0

        if _already_blocked(str(data.get("session_id") or ""), ",".join(sorted(dangling))):
            return 0

        reason = (
            "Пайплайн оставлен в подвешенном состоянии: шаги "
            f"{dangling} не закрыты (работа по ним начата — есть gate-result/origin — либо "
            "статус in_progress). "
            "Закрой их через pipeline-state/update.py (status=completed после прохождения "
            "gate, либо status=failed с --error), затем заверши ход."
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
