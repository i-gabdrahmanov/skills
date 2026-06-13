#!/usr/bin/env python3
"""C3: run_judge --from-output сохраняет вердикт pass-through судей (build/delivery).

Раньше build/delivery вердикты только читались, но их никто не писал → шаги
04-build/07-deliver не закрывались. Теперь субагентский вердикт сохраняется через
--from-output, и --recheck его подтверждает.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RJ = REPO / "skills/feature-pipeline/scripts/run_judge.py"


def _run(args, cwd, stdin=None):
    return subprocess.run([sys.executable, str(RJ), *map(str, args)], cwd=str(cwd),
                          input=stdin, capture_output=True, text=True, timeout=60)


class Ingest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "docs/feature-pipeline/feat").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _verdict_path(self, judge):
        return self.proj / "ground/statements/feature-pipeline/feat/judges" / f"{judge}.json"

    def test_ingest_then_recheck_passes(self):
        vf = self.proj / "verdict.json"
        vf.write_text(json.dumps({"passed": True, "blocking_issues": [], "summary": "ok"}))
        r = _run(["build", "feat", "--from-output", str(vf), "--project-root", self.proj], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._verdict_path("build-judge").exists())
        saved = json.loads(self._verdict_path("build-judge").read_text())
        self.assertTrue(saved["passed"])
        # recheck подтверждает
        r2 = _run(["build", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_recheck_without_ingest_fails(self):
        r = _run(["delivery", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertNotEqual(r.returncode, 0, "delivery --recheck без вердикта должен падать")

    def test_failing_verdict_ingest_fails(self):
        r = _run(["delivery", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj, stdin=json.dumps({"passed": False, "blocking_issues": ["stub left"]}))
        self.assertEqual(r.returncode, 1)
        saved = json.loads(self._verdict_path("delivery-judge").read_text())
        self.assertFalse(saved["passed"])
        # errors.json накоплен
        self.assertTrue((self.proj / "ground/statements/feature-pipeline/feat/judges/errors.json").exists())

    def test_malformed_input_errors(self):
        r = _run(["build", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj, stdin="{not json")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
