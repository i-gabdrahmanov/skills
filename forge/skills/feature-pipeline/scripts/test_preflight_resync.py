#!/usr/bin/env python3
from __future__ import annotations
"""Регрессия: preflight пересинхронизирует устаревший gate.json из manifest.

Баг: gate.json создавался один раз и держался актуальным только через
update.py→sync_gate_from_manifest. Если тот sync падал (на Python 3.9 в phase_sync)
или был пропущен, gate.json устаревал, и preflight давал ложное «несоответствие
стадий» (_check_gate_phase блокировал легальный следующий шаг).

Фикс: preflight._ensure_phases всегда пересинхронизирует существующий gate из
manifest перед проверкой. Manifest — источник истины.

Тест интеграционный: гоняет реальные init.py / update.py / preflight-validate.py.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FP = Path(__file__).resolve().parent                     # feature-pipeline/scripts
PS = FP.parents[1] / "pipeline-state" / "scripts"        # pipeline-state/scripts
INIT = PS / "init.py"
UPDATE = PS / "update.py"
PREFLIGHT = FP / "preflight-validate.py"

BASE_STEPS = [
    {"id": "00-brd", "title": "BRD", "depends_on": []},
    {"id": "01-grounding", "title": "Grounding", "depends_on": ["00-brd"]},
    {"id": "02-design", "title": "Design", "depends_on": ["00-brd", "01-grounding"]},
]


def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *map(str, cmd)],
                          capture_output=True, text=True)


class TestPreflightResync(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.feature = "resync-feat"
        self.sd = self.proj / "ground" / "statements" / "feature-pipeline" / self.feature
        r = _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", self.feature, "--steps", json.dumps(BASE_STEPS), "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self._tmp.cleanup()

    def _gate(self) -> dict:
        return json.loads((self.proj / "ground" / "phases" / self.feature / "gate.json").read_text(encoding="utf-8"))

    def _pass_judge(self, name: str):
        jd = self.sd / "judges"
        jd.mkdir(parents=True, exist_ok=True)
        (jd / f"{name}.json").write_text(json.dumps({
            "$schema": "feature-pipeline/judge-verdict@1", "produced_by": "run_judge", "judge": name,
            "feature_slug": self.feature, "passed": True, "verdict": "PASS",
            "checks": [], "blocking_issues": [], "warnings": [], "summary": "ok",
            "evaluated_at": "2026-06-16T00:00:00Z",
        }), encoding="utf-8")

    def _preflight(self, step_id: str) -> int:
        return _run([PREFLIGHT, "--project", self.proj, "--feature", self.feature,
                     "--step-id", step_id]).returncode

    def _complete(self, step_id: str) -> int:
        return _run([UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                     "--feature", self.feature, "--step-id", step_id, "--status",
                     "completed", "--output-json", json.dumps({"step_id": step_id})]).returncode

    def test_preflight_creates_gate(self):
        self.assertEqual(self._preflight("00-brd"), 0)
        self.assertEqual(self._gate()["current_phase"], "00-brd")

    def test_stale_gate_self_heals(self):
        """Главная регрессия: устаревший gate → preflight чинит, легальный шаг проходит."""
        self.assertEqual(self._preflight("00-brd"), 0)   # создаёт gate
        self._pass_judge("brd-judge")
        self.assertEqual(self._complete("00-brd"), 0)

        # Искусственно устариваем gate: откатываем на 00-brd
        gp = self.proj / "ground" / "phases" / self.feature / "gate.json"
        g = json.loads(gp.read_text(encoding="utf-8"))
        g["current_phase"] = "00-brd"
        for ph in g["phases"]:
            if ph["id"] == "00-brd":
                ph["status"] = "in_progress"
        gp.write_text(json.dumps(g), encoding="utf-8")

        # preflight следующего легального шага должен ПРОЙТИ (самоисцеление)
        self.assertEqual(self._preflight("01-grounding"), 0,
                         "preflight должен пересинхронизировать устаревший gate и пропустить 01-grounding")
        self.assertEqual(self._gate()["current_phase"], "01-grounding",
                         "gate должен быть пересинхронизирован на 01-grounding")

    def test_skip_ahead_still_blocked(self):
        """Фикс не должен ослаблять guard: пропуск фазы по-прежнему блокируется."""
        self.assertEqual(self._preflight("00-brd"), 0)
        # 01-grounding и 02-design ещё pending → прыжок на 02-design запрещён
        self.assertNotEqual(self._preflight("02-design"), 0,
                            "пропуск фазы (02-design мимо 01-grounding) должен блокироваться")

    def test_full_in_order_walk(self):
        """Последовательный проход 00→01→02 — все переходы PASS."""
        self.assertEqual(self._preflight("00-brd"), 0)
        self._pass_judge("brd-judge")
        self.assertEqual(self._complete("00-brd"), 0)

        self.assertEqual(self._preflight("01-grounding"), 0)
        self.assertEqual(self._complete("01-grounding"), 0)

        self.assertEqual(self._preflight("02-design"), 0)
        self.assertEqual(self._gate()["current_phase"], "02-design")


if __name__ == "__main__":
    unittest.main(verbosity=2)
