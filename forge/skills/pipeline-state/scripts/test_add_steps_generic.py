#!/usr/bin/env python3
"""Тесты generic-версии add_steps.py (pipeline-state) — той, что документирована как канон
для НЕ-feature-pipeline скиллов (`skills/pipeline-state/SKILL.md`).

Тестов у этого файла не было ни одного, и это стоило дорого: в выводе осталась ссылка на
переменную `gate_synced`, удалённую вместе с кэшем gate.json, — NameError падал на КАЖДОМ
вызове, причём ПОСЛЕ записи манифеста. Вызывающий видел exit 1 «не получилось», а шаги были
записаны. Полный прогон при этом оставался зелёным.

Второй пробел той же природы: правило «depends_on не должен ссылаться на несуществующий шаг»
завели в два писателя из трёх, и этот остался дырой.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "skills/pipeline-state/scripts/add_steps.py"
INIT = REPO / "skills/pipeline-state/scripts/init.py"
SKILL = "system-analyst"
FEATURE = "SA-1"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


class GenericAddSteps(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "ground").mkdir(parents=True)
        (self.proj / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        r = _run(INIT, "--project", self.proj, "--skill", SKILL, "--feature", FEATURE,
                 "--steps", json.dumps([{"id": "01-structure", "title": "s"}]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest(self) -> dict:
        return json.loads((self.proj / "ground/statements" / SKILL / FEATURE /
                           "manifest.json").read_text(encoding="utf-8"))

    def _add(self, steps: list):
        return _run(SCRIPT, "--project", self.proj, "--skill", SKILL, "--feature", FEATURE,
                    "--steps", json.dumps(steps))

    def test_add_succeeds_and_prints_valid_json(self):
        r = self._add([{"id": "02-api", "title": "api", "depends_on": ["01-structure"]}])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NameError", r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["added"], ["02-api"])
        self.assertIn("02-api", [s["id"] for s in self._manifest()["steps"]])

    def test_idempotent_second_call_skips(self):
        self._add([{"id": "02-api", "title": "api"}])
        r = self._add([{"id": "02-api", "title": "api"}])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["added"], [])
        self.assertEqual(out["skipped_existing"], ["02-api"])

    def test_unknown_dep_rejected_and_manifest_untouched(self):
        before = [s["id"] for s in self._manifest()["steps"]]
        r = self._add([{"id": "03-x", "title": "x", "depends_on": ["never-exists"]}])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("depends_on", r.stderr)
        self.assertEqual([s["id"] for s in self._manifest()["steps"]], before,
                         "манифест изменён, хотя вызов отклонён")

    def test_dep_within_same_batch_ok(self):
        r = self._add([{"id": "02-api", "title": "a"},
                       {"id": "03-x", "title": "x", "depends_on": ["02-api"]}])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["added"], ["02-api", "03-x"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
