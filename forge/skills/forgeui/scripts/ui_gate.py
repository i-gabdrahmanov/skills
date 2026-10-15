#!/usr/bin/env python3
"""ui_gate.py — гейты шагов ui-design / ui-build / ui-verify ветки forge-ui.

Гейт читает состояние движка с диска (`.daiver/`) и решает, закрыт ли шаг — модель свои шаги
словами не закрывает. Механика шагов появляется поэтапно (tasks/019-forge-ui.md):
  • ui-design — этапы 1–2 (макет → канон, компоненты и план секций, подпись плана);
  • ui-build  — этап 3 (сборка по секциям, buildCommand проекта);
  • ui-verify — этапы 4–5 (захват страницы, сравнение с макетом, отчёт).
Пока шага нет, гейт отвечает exit 3 «не реализовано» — это честная остановка прогона, а не
пропуск: `record_gate` запишет passed:false, и update.py шаг не закроет.

exit 0 — шаг готов; 1 — не готов (причины в выводе); 2 — неверные аргументы;
3 — механика шага ещё не реализована (стоп прогона, решение за человеком).

Usage:
    python3 ui_gate.py <design|build|verify> --project <корень репо> --feature <KEY>
"""
from __future__ import annotations

import argparse
import sys

STAGES = {
    "design": "этапы 1–2: макет → канон → компоненты библиотеки → план секций + подпись плана",
    "build": "этап 3: сборка модуля по секциям плана + buildCommand проекта",
    "verify": "этапы 4–5: захват страницы, сравнение с макетом, визуальный обзор, отчёт",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Гейты шагов forge-ui (ui-design/ui-build/ui-verify).")
    ap.add_argument("step", choices=sorted(STAGES), help="какой шаг проверить")
    ap.add_argument("--project", required=True, help="корень репо (там .daiver/ и ground/)")
    ap.add_argument("--feature", required=True, help="ключ стори (KEY) — он же имя трека")
    args = ap.parse_args(argv)
    print(f"⛔ ui-{args.step}: механика шага ещё не реализована ({STAGES[args.step]}; "
          f"план — tasks/019-forge-ui.md).\n"
          f"   Прогон честно останавливается здесь: шаг не закрыт. Скажи пользователю, что "
          f"forge-ui пока доходит до утверждённой ui-spec.md, и ничего не строй вручную.",
          file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
