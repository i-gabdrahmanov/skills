#!/usr/bin/env python3
"""ui_gate.py — гейты шагов ui-design / ui-build / ui-verify ветки forge-ui.

Гейт читает состояние движка с диска (`.daiver/`) и решает, закрыт ли шаг — модель свои шаги
словами не закрывает. Механика шагов появляется поэтапно (tasks/019-forge-ui.md):
  • ui-design — этапы 1–2 (макет → канон, компоненты и план секций, подпись плана);
  • ui-build  — этап 3 (сборка по секциям, buildCommand проекта);
  • ui-verify — этапы 4–5 (захват страницы, сравнение с макетом, отчёт).
Пока механики шага нет (сейчас — ui-verify), гейт отвечает exit 3 «не реализовано» — это честная
остановка прогона, а не пропуск: `record_gate` запишет passed:false, и update.py шаг не закроет.

ui-design (задачи 2.5–2.6) закрыт, когда готов контракт плана и план подписан: резолв компонентов закончен и подтверждён
(`resolution-report.md` актуален, вердикты, которые обязан увидеть человек, — согласие `ui-resolve-<KEY>`), у каждого кадра есть план,
равный тому, что строит движок из текущих данных, нет блокирующих пунктов `daiver_plan_contract` (адрес страницы, браузер, источник
библиотеки…), и на ТЕКУЩИЙ `plan.lock.json` записано согласие `ui-plan-<KEY>`. Гейт только читает диск: ничего не пересобирает и не
снимает (пересобирает `daiver_plan_contract`).

ui-build (задача 3.2) закрыт, когда собраны все секции плана каждого кадра (`build-state.json` сверен с планом, ничего не pending /
in_progress / stale, `outputs` готовых секций лежат на диске; секции без кода — корень решён ignore — движок закрыл сам) И проект
собирается: `project.buildCommand` из конфига запускается из корня репо с таймаутом (`--timeout`, по умолчанию 900 с), красный итог —
красный гейт с хвостом вывода. Команда запускается, только когда секции готовы. Модуль, который не компилируется, чинит субагент
сам (§13.8). Логика — `daiver/build_gate.py`; подпись плана здесь не перепроверяется (её охраняет хук ui-guard, 3.3).

exit 0 — шаг готов; 1 — не готов (причины в выводе); 2 — неверные аргументы;
3 — механика шага ещё не реализована (стоп прогона, решение за человеком).

Usage:
    python3 ui_gate.py <design|build|verify> --project <корень репо> --feature <KEY> [--timeout <секунд>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

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


def _build(project: Path, feature: str, timeout: Optional[int]) -> int:
    """Секции собраны и проект собирается? Нет — exit 1 с причинами (или хвостом вывода buildCommand); да — exit 0."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from daiver import build_gate as BG             # noqa: WPS433 — движок рядом со скриптом

    timeout = BG.DEFAULT_TIMEOUT if timeout is None else timeout
    res = BG.check(project, feature, timeout)
    if res["problems"]:
        print(f"⛔ ui-build: шаг не готов к закрытию ({len(res['problems'])}):", file=sys.stderr)
        for p in res["problems"]:
            print(f"   • {p}", file=sys.stderr)
        print("   Закончи сборку по циклу (daiver_build_state → next → … → daiver_build_state_set done) и повтори гейт; buildCommand "
              "запускается, только когда все секции готовы. Шаг не закрыт.", file=sys.stderr)
        return 1
    command = res["command"]
    if res["ok"]:
        print(f"✓ ui-build: секции кадров ({', '.join(res['frames'])}) собраны, outputs на месте; `{command}` — exit 0.")
        return 0
    if res["timedOut"]:
        head = f"`{command}` не уложилась в {timeout} с и остановлена"
    elif res["exit"] is None:
        head = f"`{command}` не запустилась"
    else:
        head = f"`{command}` завершилась с кодом {res['exit']}"
    print(f"⛔ ui-build: {head} — проект не собирается, шаг не закрыт. Это ремонт самого шага (§13.8): исправь код, не спрашивая "
          f"человека, и повтори гейт. Хвост вывода:", file=sys.stderr)
    print(res["tail"] or "(вывода нет)", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Гейты шагов forge-ui (ui-design/ui-build/ui-verify).")
    ap.add_argument("step", choices=sorted(STAGES), help="какой шаг проверить")
    ap.add_argument("--project", required=True, help="корень репо (там .daiver/ и ground/)")
    ap.add_argument("--feature", required=True, help="ключ стори (KEY) — он же имя трека")
    ap.add_argument("--timeout", type=int, default=None, help="секунд на project.buildCommand (только для build; по умолчанию 900)")
    args = ap.parse_args(argv)
    if args.timeout is not None and args.timeout <= 0:
        ap.error("--timeout: положительное число секунд")
    if args.step == "design":
        return _design(Path(args.project).resolve(), args.feature)
    if args.step == "build":
        return _build(Path(args.project).resolve(), args.feature, args.timeout)
    print(f"⛔ ui-{args.step}: механика шага ещё не реализована ({STAGES[args.step]}; "
          f"план — tasks/019-forge-ui.md).\n"
          f"   Прогон честно останавливается здесь: шаг не закрыт. Скажи пользователю, что "
          f"forge-ui пока доходит до сборки (ui-build), и ничего не проверяй вручную вместо движка.",
          file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
