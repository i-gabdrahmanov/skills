#!/usr/bin/env python3
"""Doc-lint: каждая per-task/phase фаза TDD-цикла закрывается ЯВНОЙ командой update.py.

Регрессия «состояние не записалось при двух вызовах (primary → judge FAIL → fix)»:
закрытие судимого шага делает ТОЛЬКО явный `update.py --status completed` ПОСЛЕ судьи —
`state-recorder` на `SubagentStop` рабочего субагента закрыть шаг не может (судья ещё не
прошёл, `_check_judges` детерминированно блокирует). Раньше per-task фазы
(04-test/04-build/05-tests/06-spec/07-deliver/07-report) закрывались лишь прозой
(«закрой ... при pass»); после fix-раунда модель пропускала закрытие → шаг застревал в
`in_progress`. Тест требует явный командный блок update.py для каждого такого шага, чтобы
фикс §7–§10 SKILL.md не отрегрессировал обратно в прозу.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SKILL = (REPO / "skills/feature-pipeline/SKILL.md").read_text(encoding="utf-8")

# Шаги per-task/phase TDD-цикла, закрываемые ЯВНОЙ командой (а не прозой).
CLOSABLE_STEPS = [
    "04-test-<taskId>",
    "04-build-<taskId>",
    "05-tests",
    "06-spec",
    "07-deliver-<taskId>",
    "07-report",
]


class ExplicitCloseCommands(unittest.TestCase):
    def test_each_step_has_explicit_update_command(self):
        for step in CLOSABLE_STEPS:
            pat = re.compile(r"--step-id\s+" + re.escape(step) + r"\s+--status\s+completed")
            self.assertRegex(
                SKILL, pat,
                f"В SKILL.md нет явной команды закрытия шага '{step}' "
                f"(`update.py --step-id {step} --status completed`). Закрытие судимого шага "
                f"не должно держаться на прозе — после fix-раунда оно теряется и шаг "
                f"застревает в in_progress.")

    def test_reiteration_rule_demands_same_step_id(self):
        """§0.6: fix-прогон обязан вернуть тот же step_id — иначе state-recorder не запишет."""
        self.assertIn("тот же финальный JSON с `step_id`", SKILL,
                      "§0.6 не требует от fix-субагента вернуть тот же step_id "
                      "(state-recorder уйдёт в ветку «нет step_id» и не запишет fix-прогон).")


if __name__ == "__main__":
    unittest.main(verbosity=2)
