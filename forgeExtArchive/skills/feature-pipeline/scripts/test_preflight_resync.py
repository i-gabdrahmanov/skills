#!/usr/bin/env python3
from __future__ import annotations
"""Фазовая машина preflight: решение следует за манифестом, кэша нет.

История бага: gate.json создавался один раз и держался актуальным только через
update.py→sync_gate_from_manifest. Если тот sync падал или был пропущен, gate.json
устаревал, и preflight давал ложное «несоответствие стадий» — блокировал легальный
следующий шаг. Сначала это лечили пересинхронизацией в preflight._ensure_phases.

Теперь кэш снят совсем: состояние ВЫЧИСЛЯЕТСЯ из манифеста, и рассинхрон невозможен
структурно, а не потому что где-то не забыли вызвать sync. Тесты пинят это свойство
(файла кэша нет; решение меняется сразу за манифестом) и то, что снятие кэша не
ослабило сам guard — пропуск фазы по-прежнему блокируется.

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

sys.path.insert(0, str(FP))
import pipeline_phases as _pp  # noqa: E402

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
        self.feature = "KID-1"  # Jira-ключ: init.py гейтит feature-pipeline на формат ключа
        self.sd = self.proj / "ground" / "statements" / "feature-pipeline" / self.feature
        r = _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", self.feature, "--steps", json.dumps(BASE_STEPS), "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        # маркеры утверждения доков (их пишет record_approval после «да» пользователя) —
        # update._check_doc_approval не закроет 00-brd/02-sdd без них
        appr = self.proj / "ground" / "approvals"
        appr.mkdir(parents=True, exist_ok=True)
        for doc in ("brd", "sdd"):
            key = f"{doc}-approved-{self.feature}"
            (appr / f"{key}.json").write_text(json.dumps(
                {"produced_by": "record_approval", "key": key, "approved_by": "user",
                 "reason": "test"}), encoding="utf-8")
        # Содержательная выжимка — update._check_grounding_substance не закроет 01-grounding без неё.
        sa = self.proj / "ground/inventory"
        sa.mkdir(parents=True, exist_ok=True)
        (sa / "grounding-excerpt.json").write_text(json.dumps(
            {"modules": [{"name": "svc"}], "entities": [{"name": "Foo"}]}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _gate(self) -> dict:
        """Фазовое решение — деривация из манифеста (то же, что видит preflight)."""
        manifest = json.loads((self.sd / "manifest.json").read_text(encoding="utf-8"))
        return _pp.live_phase_decision(manifest)

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

    def test_preflight_needs_no_cache_file(self):
        """preflight работает, ничего не создавая: фазовое состояние выводится из манифеста."""
        self.assertEqual(self._preflight("00-brd"), 0)
        self.assertEqual(self._gate()["current_phase"], "00-brd")
        self.assertEqual(list(self.proj.glob("ground/phases/**/*.json")), [],
                         "кэш фазовой машины вернулся на диск")

    def test_decision_follows_manifest_without_sync_step(self):
        """Закрыли шаг — решение сместилось СРАЗУ, без промежуточной синхронизации.

        Это и есть замена «самоисцелению устаревшего gate»: рассинхрону неоткуда взяться,
        потому что второго носителя состояния больше нет.
        """
        self.assertEqual(self._preflight("00-brd"), 0)
        self.assertEqual(self._gate()["current_phase"], "00-brd")

        self._pass_judge("brd-judge")
        self.assertEqual(self._complete("00-brd"), 0)

        self.assertEqual(self._gate()["current_phase"], "01-grounding",
                         "решение не сместилось за манифестом")
        self.assertEqual(self._preflight("01-grounding"), 0,
                         "следующий легальный шаг должен проходить сразу после закрытия предыдущего")

    def test_skip_ahead_still_blocked(self):
        """Снятие кэша не ослабило guard: пропуск фазы по-прежнему блокируется."""
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
