#!/usr/bin/env python3
"""set_criticality.py — атомарно записывает критичность фичи И производный порог риска.

Гейт критичности (SKILL.md §«Гейт критичности») спрашивает у пользователя критичность
(low|medium|high). РАНЬШЕ модель дописывала `autonomy.criticality` сырым Edit'ом и часто
забывала обновить `autonomy.auto_max_risk` (он оставался хардкодом `R1` из init_pipeline_config)
→ при выборе low/high порог авто-прохода был неверным, а гейт критичности давал ложное чувство
контроля. Этот скрипт делает связь «ответ → порог» ДЕТЕРМИНИРОВАННОЙ: пишет ОБА поля по единой
карте. Модель обязана звать его, а не править pipeline.json руками.

Карта (PDLC v3.5):
    low    → R2  (фичекод авто; гейтятся доставка и R3+ пути)
    medium → R1  (commit/push/jira/секьюрные пути — под гейтами; дефолт)
    high   → R0  (почти всё требует подтверждения/approval/evidence)

Usage:
    set_criticality.py --criticality <low|medium|high> [--project-root .]

Exit:
    0 — записано
    2 — ошибка (нет pipeline.json / неизвестная критичность)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Единый источник правды о связи критичность → порог авто-прохода risk-ladder.
# Импортируется тестом (test_set_criticality.py) и должен совпадать с таблицей в SKILL.md
# («Гейт критичности») и комментарием в init_pipeline_config.py.
CRITICALITY_TO_RISK = {
    "low": "R2",
    "medium": "R1",
    "high": "R0",
}


def derive_risk(criticality: str) -> str:
    """Возвращает auto_max_risk для критичности или бросает ValueError при неизвестной."""
    key = (criticality or "").strip().lower()
    if key not in CRITICALITY_TO_RISK:
        raise ValueError(
            f"неизвестная критичность '{criticality}' — допустимо: "
            f"{', '.join(CRITICALITY_TO_RISK)}"
        )
    return CRITICALITY_TO_RISK[key]


def apply(config: dict, criticality: str) -> dict:
    """Возвращает обновлённый config: пишет criticality + производный auto_max_risk."""
    key = criticality.strip().lower()
    risk = derive_risk(key)
    autonomy = config.setdefault("autonomy", {})
    autonomy["criticality"] = key
    autonomy["auto_max_risk"] = risk
    return config


def main() -> int:
    ap = argparse.ArgumentParser(description="Записать критичность фичи и производный порог риска")
    ap.add_argument("--criticality", required=True,
                    help="low | medium | high")
    ap.add_argument("--project-root", default=".",
                    help="Корень проекта (по умолчанию cwd); pipeline.json — в <root>/ground/")
    args = ap.parse_args()

    try:
        risk = derive_risk(args.criticality)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    pipeline_path = Path(args.project_root).resolve() / "ground" / "pipeline.json"
    if not pipeline_path.exists():
        print(f"ERROR: не найден {pipeline_path} — сначала init_pipeline_config.py", file=sys.stderr)
        return 2

    try:
        config = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: не прочитать {pipeline_path}: {e}", file=sys.stderr)
        return 2

    apply(config, args.criticality)

    pipeline_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    key = args.criticality.strip().lower()
    print(f"✅ criticality={key} → auto_max_risk={risk} записано в {pipeline_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
