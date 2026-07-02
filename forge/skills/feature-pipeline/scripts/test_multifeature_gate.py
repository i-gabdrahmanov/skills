#!/usr/bin/env python3
"""C1: две фичи в работе одновременно не затирают gate друг друга.

Раньше gate.json был глобальным (ground/phases/gate.json) — вторая фича пересобирала
его из своего манифеста, стирая прогресс первой. Теперь gate под фичу
(ground/phases/<feature>/gate.json).
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
ADD = REPO / "skills/feature-pipeline/scripts/add_steps.py"

STATIC = [
    {"id": "00-brd", "depends_on": []},
    {"id": "01-grounding", "depends_on": []},
    {"id": "02-design", "depends_on": []},
    {"id": "02-eval-plan", "depends_on": []},
    {"id": "03-jira", "depends_on": []},
    {"id": "05-tests", "depends_on": []},
    {"id": "06-spec", "depends_on": []},
    {"id": "07-report", "depends_on": []},
]
DYN = [
    {"id": "04-test-T1", "depends_on": []},
    {"id": "04-build-T1", "depends_on": []},
    {"id": "07-deliver-T1", "depends_on": []},
]
JUDGES = ["brd-judge", "design-judge", "eval-judge", "coverage-judge", "spec-judge",
          "red-judge", "build-judge", "reuse-judge", "delivery-judge"]
ALL_STEPS = [s["id"] for s in STATIC] + [s["id"] for s in DYN]


def _run(args, cwd):
    return subprocess.run([sys.executable, *map(str, args)], cwd=str(cwd),
                          capture_output=True, text=True, timeout=60)


class MultiFeatureGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _setup_feature(self, feat):
        (self.proj / "docs/feature-pipeline" / feat).mkdir(parents=True, exist_ok=True)
        _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
              "--feature", feat, "--steps", json.dumps(STATIC)], self.proj)
        _run([ADD, "--skill", "feature-pipeline", "--feature", feat,
              "--steps", json.dumps(DYN)], self.proj)
        jdir = self.proj / "ground/statements/feature-pipeline" / feat / "judges"
        jdir.mkdir(parents=True, exist_ok=True)
        for n in JUDGES:
            # реалистичный вердикт run_judge (verdict/passed/checks/summary) — schema-sanity update.py
            (jdir / f"{n}.json").write_text(json.dumps(
                {"produced_by": "run_judge", "judge": n, "verdict": "PASS", "passed": True,
                 "checks": [], "summary": "ok"}))

    def _gate(self, feat):
        return json.loads((self.proj / "ground/phases" / feat / "gate.json").read_text())

    def _close_all(self, feat):
        odir = self.proj / "ground/statements/feature-pipeline" / feat / "_origins"
        odir.mkdir(parents=True, exist_ok=True)
        gdir = self.proj / "ground/statements/feature-pipeline" / feat / "gates"
        gdir.mkdir(parents=True, exist_ok=True)
        for sid in ALL_STEPS:
            # evidence-маркер, который пишет state-recorder на реальном SubagentStop —
            # update._check_subagent_origin требует его для subagent-фаз (а не флаг --closed-by)
            (odir / f"{sid}.json").write_text(json.dumps({"step_id": sid}))
            # gate-result evidence (пишет record_gate.py) — нужен build/verify-шагам
            (gdir / f"{sid}.json").write_text(json.dumps(
                {"produced_by": "record_gate", "step_id": sid, "passed": True}))
            r = _run([UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                      "--feature", feat, "--step-id", sid, "--status", "completed",
                      "--closed-by", "subagent",  # как state-recorder на SubagentStop (C3)
                      "--output-json", json.dumps({"step_id": sid})], self.proj)
            self.assertEqual(r.returncode, 0, f"{feat}/{sid}: {r.stderr or r.stdout}")

    def test_two_features_isolated(self):
        self._setup_feature("alpha")
        self._setup_feature("beta")

        # отдельные gate-файлы
        self.assertTrue((self.proj / "ground/phases/alpha/gate.json").exists())
        self.assertTrue((self.proj / "ground/phases/beta/gate.json").exists())

        # завершаем alpha полностью
        self._close_all("alpha")
        self.assertEqual(self._gate("alpha")["current_phase"], "")

        # beta не затронута настройкой alpha
        self.assertNotEqual(self._gate("beta")["current_phase"], "",
                            "gate beta затёрт работой alpha (C1 регресс)")

        # завершаем beta — alpha остаётся завершённой
        self._close_all("beta")
        self.assertEqual(self._gate("beta")["current_phase"], "")
        self.assertEqual(self._gate("alpha")["current_phase"], "",
                         "после работы beta gate alpha изменился (C1 регресс)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
