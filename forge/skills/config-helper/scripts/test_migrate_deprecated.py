#!/usr/bin/env python3
"""test_migrate_deprecated.py — `config.py migrate-deprecated` снимает наследство старых версий.

Лог 2026-10-05: в policy.json обновлённого проекта висели pipeline.mode, sources.spec,
autonomy.criticality/auto_max_risk. validate считает их ошибкой, поэтому цепочка
`config.py set … && validate` давала exit 1 по чужой причине, а модель докладывала «готово».
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "config.py"

STALE = {"project": {"name": "npf"},
         "pipeline": {"mode": "full", "phases_override": []},
         "sources": {"spec": "jira_issue_description"},
         "autonomy": {"criticality": "medium", "auto_max_risk": "R1", "auto_approve": False}}


def run(project: Path, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--project", str(project), *args],
                          capture_output=True, text=True, timeout=60)


class MigrateDeprecatedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ground").mkdir()
        self.policy = self.root / "ground" / "policy.json"
        self.policy.write_text(json.dumps(STALE), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_validate_red_then_green(self):
        self.assertEqual(run(self.root, "validate").returncode, 1)
        r = run(self.root, "migrate-deprecated")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(sorted(i["path"] for i in out["removed"]),
                         ["autonomy.auto_max_risk", "autonomy.criticality", "pipeline.mode",
                          "sources.spec"])
        self.assertTrue(Path(out["backup"]).exists())
        data = json.loads(self.policy.read_text(encoding="utf-8"))
        self.assertNotIn("sources", data, "пустой родитель тоже снимается")
        self.assertEqual(data["autonomy"], {"auto_approve": False}, "соседи остаются")
        self.assertEqual(data["pipeline"], {"phases_override": []})
        self.assertEqual(run(self.root, "validate").returncode, 0)

    def test_dry_run_and_idempotent(self):
        before = self.policy.read_text(encoding="utf-8")
        r = run(self.root, "migrate-deprecated", "--dry-run")
        self.assertEqual(json.loads(r.stdout)["dry_run"], True)
        self.assertEqual(self.policy.read_text(encoding="utf-8"), before)
        run(self.root, "migrate-deprecated")
        r = run(self.root, "migrate-deprecated")
        self.assertEqual(json.loads(r.stdout)["removed"], [])


if __name__ == "__main__":
    unittest.main()
