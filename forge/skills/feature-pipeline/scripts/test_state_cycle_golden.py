#!/usr/bin/env python3
"""Golden end-to-end тест машины состояний feature-pipeline на временном проекте.

Прогоняет реальные скрипты (init → add_steps → update) и проверяет, что:
  - шаги 02-design и 05-tests РЕАЛЬНО закрываются при наличии вердиктов судей
    (регрессия P0-1: раньше маска требовала несуществующие taskplan-check/coverage-check);
  - фазовая машина gate.json доходит до конца — current_phase == "" (регрессия P0-2:
    раньше застревала на 06-document из-за '06-doc');
  - без вердикта судьи шаг закрыть НЕЛЬЗЯ (enforcement жив, --skip-judges не нужен).

Требует Python 3.10+. Использует sys.executable для запуска скриптов.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INIT = REPO / "skills/pipeline-state/scripts/init.py"
UPDATE = REPO / "skills/pipeline-state/scripts/update.py"
ADD_STEPS_FP = REPO / "skills/feature-pipeline/scripts/add_steps.py"

SLUG = "demo-feature"

STATIC_STEPS = [
    {"id": "00-brd", "depends_on": []},
    {"id": "01-grounding", "depends_on": []},
    {"id": "02-design", "depends_on": ["00-brd", "01-grounding"]},
    {"id": "02-eval-plan", "depends_on": ["02-design"]},
    {"id": "03-jira", "depends_on": ["02-design"]},
    {"id": "05-tests", "depends_on": []},
    {"id": "06-spec", "depends_on": ["05-tests"]},
    {"id": "07-report", "depends_on": []},
]
DYNAMIC_STEPS = [
    {"id": "04-test-T1", "depends_on": ["02-design"]},
    {"id": "04-build-T1", "depends_on": ["04-test-T1", "02-eval-plan"]},
    {"id": "07-deliver-T1", "depends_on": ["05-tests", "06-spec"]},
]
# Вердикты, имена которых совпадают с required_judges всех шагов выше.
JUDGE_VERDICTS = ["design-judge", "eval-judge", "coverage-judge", "spec-judge",
                  "red-judge", "build-judge", "delivery-judge"]

UPDATE_ORDER = ["00-brd", "01-grounding", "02-design", "02-eval-plan", "03-jira",
                "04-test-T1", "04-build-T1", "05-tests", "06-spec",
                "07-deliver-T1", "07-report"]


def _run(args, cwd):
    return subprocess.run([sys.executable, *map(str, args)],
                          cwd=str(cwd), capture_output=True, text=True, timeout=60)


class GoldenStateCycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "docs/feature-pipeline" / SLUG).mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _state_dir(self, feature=SLUG):
        return self.proj / "ground/statements/feature-pipeline" / feature

    def _write_verdicts(self, feature=SLUG):
        jdir = self._state_dir(feature) / "judges"
        jdir.mkdir(parents=True, exist_ok=True)
        for name in JUDGE_VERDICTS:
            (jdir / f"{name}.json").write_text(json.dumps({
                "judge": name, "passed": True, "verdict": "PASS",
                "blocking_issues": [],
            }))

    def test_full_cycle_reaches_empty_current_phase(self):
        # 1. init со статическими шагами
        r = _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", SLUG, "--steps", json.dumps(STATIC_STEPS)], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

        # 2. add_steps (версия feature-pipeline) — добавляет динамические шаги + строит gate
        r = _run([ADD_STEPS_FP, "--skill", "feature-pipeline", "--feature", SLUG,
                  "--steps", json.dumps(DYNAMIC_STEPS)], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

        # 3. вердикты судей (passed=true) — иначе update.py не закроет шаги
        self._write_verdicts()

        # 4. закрываем все шаги БЕЗ --skip-judges
        for step_id in UPDATE_ORDER:
            r = _run([UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                      "--feature", SLUG, "--step-id", step_id, "--status", "completed",
                      "--output-json", json.dumps({"step_id": step_id})], self.proj)
            self.assertEqual(r.returncode, 0,
                             f"шаг {step_id} не закрылся: {r.stderr or r.stdout}")

        # 5. все шаги completed
        manifest = json.loads((self._state_dir() / "manifest.json").read_text())
        statuses = {s["id"]: s["status"] for s in manifest["steps"]}
        self.assertTrue(all(v == "completed" for v in statuses.values()),
                        f"не все шаги completed: {statuses}")

        # 6. фазовая машина дошла до конца (gate под фичу — C1)
        gate = json.loads((self.proj / "ground/phases" / SLUG / "gate.json").read_text())
        self.assertEqual(gate["current_phase"], "",
                         f"gate застрял на '{gate['current_phase']}' (P0-2?)")
        # 6a. фазы в каноническом порядке (динамические 04-tdd/07-deliver не в конце)
        main_order = ["00-brd", "01-grounding", "02-design", "02-eval-plan", "03-jira",
                      "04-tdd", "05-verify", "06-document", "07-deliver", "07-report"]
        ids = [p["id"] for p in gate["phases"]]
        self.assertEqual(ids, [m for m in main_order if m in ids],
                         f"фазы не в каноническом порядке: {ids}")
        self.assertTrue(all(p["status"] == "completed" for p in gate["phases"]),
                        f"не все фазы completed: {[(p['id'], p['status']) for p in gate['phases']]}")

    def test_step_blocked_without_verdict(self):
        """02-design нельзя закрыть, пока нет design-judge.json (enforcement жив)."""
        r = _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", "neg",
                  "--steps", json.dumps([{"id": "02-design", "depends_on": []}])], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        # без вердикта design-judge
        r = _run([UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", "neg", "--step-id", "02-design", "--status", "completed",
                  "--output-json", json.dumps({"step_id": "02-design"})], self.proj)
        self.assertNotEqual(r.returncode, 0,
                            "02-design закрылся без вердикта судьи — enforcement сломан")


if __name__ == "__main__":
    unittest.main(verbosity=2)
