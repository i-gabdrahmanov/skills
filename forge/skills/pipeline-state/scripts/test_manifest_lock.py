#!/usr/bin/env python3
"""Параллельные писатели манифеста не теряют записи друг друга.

Регрессия (FORGE.md, «Подтверждено, но НЕ закрыто»): update.py делал read-modify-write
manifest.json без замка и через общий manifest.json.tmp. Параллельные шаги — `04-test-T1`,
`04-test-T2`, … — норма пайплайна: каждый SubagentStop закрывает свой шаг отдельным
процессом update.py. Каждый читал манифест до записи соседа и затирал её своей копией —
выживала одна запись из нескольких, а все вызовы печатали {"status":"updated"}.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "update.py"
N = 8


class TestParallelUpdates(unittest.TestCase):
    def test_no_lost_updates(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            d = project / "ground" / "statements" / "feature-pipeline" / "demo"
            d.mkdir(parents=True)
            steps = [{"id": f"04-test-T{i}", "status": "pending"} for i in range(N)]
            (d / "manifest.json").write_text(
                json.dumps({"skill": "feature-pipeline", "feature": "demo", "context": {},
                            "steps": steps}), encoding="utf-8")
            procs = [subprocess.Popen(
                [sys.executable, str(SCRIPT), "--project", str(project),
                 "--skill", "feature-pipeline", "--feature", "demo",
                 "--step-id", f"04-test-T{i}", "--status", "in_progress"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(N)]
            for p in procs:
                _, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, err)
            man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            lost = [s["id"] for s in man["steps"] if s.get("status") != "in_progress"]
            self.assertEqual(lost, [], f"потеряны записи параллельных шагов: {lost}")

    def test_other_writers_share_the_lock(self):
        """Решение пользователя (`config.py set inputs.*`) и критичность (`set_criticality`)
        пишутся посреди прогона, пока шаги закрываются своими update.py. Каждый писал свою
        копию, прочитанную до записи соседа, — терялось то решение, то статус шага."""
        scripts = SCRIPT.parent.parent.parent
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "ground").mkdir()
            (project / "ground" / "policy.json").write_text("{}", encoding="utf-8")
            d = project / "ground" / "statements" / "feature-pipeline" / "demo"
            d.mkdir(parents=True)
            steps = [{"id": f"04-test-T{i}", "status": "pending"} for i in range(N)]
            (d / "manifest.json").write_text(json.dumps({
                "version": 2, "skill": "feature-pipeline", "feature": "demo", "context": {},
                "inputs": {}, "decisions": {}, "steps": steps}), encoding="utf-8")
            cmds = [[sys.executable, str(SCRIPT), "--project", str(project), "--skill",
                     "feature-pipeline", "--feature", "demo", "--step-id", f"04-test-T{i}",
                     "--status", "in_progress"] for i in range(N)]
            cmds.append([sys.executable, str(scripts / "config-helper" / "scripts" / "config.py"),
                         "--project", str(project), "set", "inputs.story", "STOR-7",
                         "--skill", "feature-pipeline", "--feature", "demo"])
            cmds.append([sys.executable,
                         str(scripts / "feature-pipeline" / "scripts" / "set_criticality.py"),
                         "--criticality", "high", "--skill", "feature-pipeline",
                         "--feature", "demo", "--project-root", str(project)])
            procs = [subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) for c in cmds]
            for p in procs:
                out, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, out + err)
            man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            lost = [s["id"] for s in man["steps"] if s.get("status") != "in_progress"]
            self.assertEqual(lost, [], f"потеряны статусы шагов: {lost}")
            self.assertEqual(man["inputs"].get("story"), "STOR-7", "потеряно решение пользователя")
            self.assertEqual(man["decisions"].get("criticality"), "high", "потеряна критичность")


if __name__ == "__main__":
    unittest.main()
