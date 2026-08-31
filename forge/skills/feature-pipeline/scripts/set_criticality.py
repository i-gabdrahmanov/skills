#!/usr/bin/env python3
"""set_criticality.py — атомарно записывает критичность фичи И производный порог риска.

Гейт критичности (SKILL.md §«Гейт критичности») спрашивает у пользователя критичность
(low|medium|high) и связывает её с порогом авто-прохода по risk-ladder через ДЕТЕРМИНИРОВАННУЮ
карту: модель ОБЯЗАНА звать этот скрипт вместо ручной правки конфига. Скрипт пишет ОБА поля
(`criticality` + `auto_max_risk`) одной операцией и в правильный файл (per-feature manifest.json
v2, не глобальный pipeline.json).

Карта (PDLC v3.5):
    low    → R2  (фичекод авто; гейтятся доставка и R3+ пути)
    medium → R1  (commit/push/jira/секьюрные пути — под гейтами; дефолт)
    high   → R0  (почти всё требует подтверждения/approval/evidence)

Usage:
    set_criticality.py --criticality <low|medium|high> --skill <name> --feature <slug|KEY> [--project-root .]

Exit:
    0 — записано
    2 — ошибка (нет manifest / неизвестная критичность / невалидный --skill/--feature)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Одолжить логику резолвинга пути манифеста и реестра имён скиллов из общего лоадера —
# иначе скрипт рискует уйти в «своё» представление о путях и разойтись с тем, что читает
# остальной форж (load_manifest/manifest_path в _config_loader.py). Здесь нам нужен только
# путь — без валидации версии (это writer, не reader).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "hooks"))
from _config_loader import manifest_path  # noqa: E402

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


def apply(manifest: dict, criticality: str) -> dict:
    """Обновляет decisions.criticality + decisions.auto_max_risk в манифесте фичи (v2).

    Не затирает соседние поля decisions (mode_task и т.п.) и не трогает inputs/context/steps —
    структура манифеста версии 2 (см. skills/pipeline-state/scripts/init.py). Возвращает тот же
    dict для удобства композиции (manifest = apply(load(...), "high"))."""
    key = criticality.strip().lower()
    risk = derive_risk(key)
    decisions = manifest.setdefault("decisions", {})
    decisions["criticality"] = key
    decisions["auto_max_risk"] = risk
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Записать критичность фичи и производный порог риска")
    ap.add_argument("--criticality", required=True,
                    help="low | medium | high")
    ap.add_argument("--skill", required=True,
                    help="Имя скилла (напр. forgefix, forgelite, feature-pipeline). "
                         "Входит в путь manifest.json: ground/statements/<skill>/<feature>/.")
    ap.add_argument("--feature", required=True,
                    help="Slug фичи или Jira-ключ (напр. STOR-123). Входит в путь manifest.json.")
    ap.add_argument("--project-root", default=".",
                    help="Корень проекта (по умолчанию cwd); manifest.json — "
                         "<root>/ground/statements/<skill>/<feature>/manifest.json")
    args = ap.parse_args()

    try:
        risk = derive_risk(args.criticality)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    project_root = Path(args.project_root).resolve()
    mp = manifest_path(project_root, args.skill, args.feature)
    if not mp.exists():
        print(f"ERROR: не найден {mp} — сначала инициализируй манифест через "
              f"pipeline-state/scripts/init.py --skill {args.skill} --feature {args.feature} --steps '<…>'",
              file=sys.stderr)
        return 2

    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: не прочитать {mp}: {e}", file=sys.stderr)
        return 2

    apply(manifest, args.criticality)

    # Атомарная запись: write to .tmp + rename, чтобы частичная запись не оставила
    # манифест в полу-обновлённом виде (update.py читает его под flock'ом и любая
    # порча → откат по manifest.migration.audit). tmp рядом с целевым файлом,
    # чтобы rename был атомарным (os.replace в пределах одной ФС).
    tmp = mp.with_suffix(mp.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(mp)

    key = args.criticality.strip().lower()
    print(f"✅ criticality={key} → auto_max_risk={risk} записано в {mp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
