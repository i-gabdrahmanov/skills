#!/usr/bin/env python3
"""ui_gate.py — гейты шагов ui-design / ui-build / ui-verify ветки forge-ui.

Гейт читает состояние движка с диска (`.daiver/`) и решает, закрыт ли шаг — модель свои шаги
словами не закрывает. Механика шагов появляется поэтапно (tasks/019-forge-ui.md):
  • ui-design — этапы 1–2 (макет → канон, компоненты и план секций, подпись плана);
  • ui-build  — этап 3 (сборка по секциям, buildCommand проекта);
  • ui-verify — этапы 4–5 (захват страницы, сравнение с макетом, отчёт).
Пока шага нет, гейт отвечает exit 3 «не реализовано» — это честная остановка прогона, а не
пропуск: `record_gate` запишет passed:false, и update.py шаг не закроет.

ui-design (задачи 2.5–2.6) закрыт, когда готов контракт плана и план подписан: резолв компонентов закончен и подтверждён
(`resolution-report.md` актуален, вердикты, которые обязан увидеть человек, — согласие `ui-resolve-<KEY>`), у каждого кадра есть план,
равный тому, что строит движок из текущих данных, нет блокирующих пунктов `daiver_plan_contract` (адрес страницы, браузер, источник
библиотеки…), и на ТЕКУЩИЙ `plan.lock.json` записано согласие `ui-plan-<KEY>`. Гейт только читает диск: ничего не пересобирает и не
снимает (пересобирает `daiver_plan_contract`).

exit 0 — шаг готов; 1 — не готов (причины в выводе); 2 — неверные аргументы;
3 — механика шага ещё не реализована (стоп прогона, решение за человеком).

Usage:
    python3 ui_gate.py <design|build|verify> --project <корень репо> --feature <KEY>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

STAGES = {
    "design": "этапы 1–2: макет → канон → компоненты библиотеки → план секций + подпись плана",
    "build": "этап 3: сборка модуля по секциям плана + buildCommand проекта",
    "verify": "этапы 4–5: захват страницы, сравнение с макетом, визуальный обзор, отчёт",
}


def _design(project: Path, feature: str) -> int:
    """Контракт плана готов и план подписан? Нет — exit 1 с причинами; да — exit 0."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from daiver import plan_contract as PC          # noqa: WPS433 — движок рядом со скриптом
    from daiver import tools as T                   # noqa: WPS433 — резолвер пути ui-spec.md стори (его знает хост)

    problems = PC.gate_problems(project, feature, T._story_specs(project))
    if problems:
        print(f"⛔ ui-design: шаг не готов к закрытию ({len(problems)}):", file=sys.stderr)
        for p in problems:
            print(f"   • {p}", file=sys.stderr)
        print("   Исправь перечисленное и повтори гейт; шаг не закрыт. Вопросы к человеку и порядок действий покажет "
              "daiver_plan_contract; подпись плана — согласие человека, а не инструмент.", file=sys.stderr)
        return 1
    print(f"✓ ui-design: резолв подтверждён, планы кадров собраны, контракт готов, план подписан "
          f"(согласие {PC.consent_key(feature)} привязано к текущему plan.lock.json).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Гейты шагов forge-ui (ui-design/ui-build/ui-verify).")
    ap.add_argument("step", choices=sorted(STAGES), help="какой шаг проверить")
    ap.add_argument("--project", required=True, help="корень репо (там .daiver/ и ground/)")
    ap.add_argument("--feature", required=True, help="ключ стори (KEY) — он же имя трека")
    args = ap.parse_args(argv)
    if args.step == "design":
        return _design(Path(args.project).resolve(), args.feature)
    print(f"⛔ ui-{args.step}: механика шага ещё не реализована ({STAGES[args.step]}; "
          f"план — tasks/019-forge-ui.md).\n"
          f"   Прогон честно останавливается здесь: шаг не закрыт. Скажи пользователю, что "
          f"forge-ui пока доходит до утверждённой ui-spec.md, и ничего не строй вручную.",
          file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
