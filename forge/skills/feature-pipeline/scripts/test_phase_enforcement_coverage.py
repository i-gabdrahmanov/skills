#!/usr/bin/env python3
"""test_phase_enforcement_coverage.py — инвариант: каждая subagent-only фаза реально
покрыта enforcement-хуками, а не только guidance в SKILL.md.

Для каждого префикса из pipeline_phases.SUBAGENT_PHASE_PREFIXES утверждаем:
  (а) requires_subagent() == True;
  (б) inline-phase-guard._is_phase_work блокирует представительное productive-действие
      главного агента в этой фазе (GAP A);
  (в) у префикса есть роль в sod-enforcer.STEP_ROLE (separation of duties).

Так добавление новой subagent-фазы без покрытия хуком будет падать здесь, а не молча
оставлять дыру (как было до фикса «модель сама правит шаги inline»).
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
HOOKS = SCRIPTS.parents[2] / "hooks"

import pipeline_phases as pp


def _load(path: Path, name: str):
    """Импорт модуля с дефисом в имени файла (inline-phase-guard, sod-enforcer)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IPG = _load(HOOKS / "inline-phase-guard.py", "inline_phase_guard")
SOD = _load(HOOKS / "sod-enforcer.py", "sod_enforcer")

# Представительное productive-действие главного агента в каждой subagent-фазе.
REPRESENTATIVE = {
    "02-sdd":   ("02-sdd",      "Write", {"file_path": "docs/feature-pipeline/f/sdd.md"}),
    "02-design":("02-design",   "Write", {"file_path": "docs/feature-pipeline/f/tech-design.md"}),
    "04-test":  ("04-test-T1",  "Write", {"file_path": "src/test/java/XTest.java"}),
    "04-build": ("04-build-T1", "Write", {"file_path": "src/main/java/X.java"}),
    "05-tests": ("05-tests",    "Bash",  {"command": "./gradlew test"}),
    "06-spec":  ("06-spec",     "Write", {"file_path": "docs/system-analysis/spec.md"}),
    # Lite-ветка (forgelite): плоские subagent-фазы.
    "lite-red":    ("lite-red",    "Write", {"file_path": "src/test/java/XTest.java"}),
    "lite-green":  ("lite-green",  "Write", {"file_path": "src/main/java/X.java"}),
    "lite-verify": ("lite-verify", "Bash",  {"command": "./gradlew test"}),
}


class TestPhaseEnforcementCoverage(unittest.TestCase):
    def test_every_subagent_phase_is_enforced(self):
        for prefix in pp.SUBAGENT_PHASE_PREFIXES:
            with self.subTest(prefix=prefix):
                # (а) единый источник истины согласован
                rep = REPRESENTATIVE.get(prefix)
                self.assertIsNotNone(rep, f"нет представительного действия для '{prefix}' "
                                          f"— добавь в REPRESENTATIVE при новой subagent-фазе")
                step_id, tool, tin = rep
                self.assertTrue(pp.requires_subagent(step_id),
                                f"requires_subagent('{step_id}') должно быть True")

                # (б) GAP A: inline-guard ловит productive-работу главного агента
                self.assertIsNotNone(
                    IPG._is_phase_work(step_id, tool, tin),
                    f"inline-phase-guard НЕ покрывает фазу '{prefix}' "
                    f"({tool} {tin}) — дыра inline-enforcement")

                # (в) separation of duties: роль фазы определена
                matched = any(step_id.startswith(k) or k.startswith(prefix)
                              for k in SOD.STEP_ROLE)
                self.assertTrue(matched, f"sod-enforcer.STEP_ROLE не покрывает '{prefix}'")

    def test_representative_map_has_no_stale_prefixes(self):
        for prefix in REPRESENTATIVE:
            self.assertIn(prefix, pp.SUBAGENT_PHASE_PREFIXES,
                          f"'{prefix}' в REPRESENTATIVE, но больше не subagent-фаза — почисти")


if __name__ == "__main__":
    unittest.main()
