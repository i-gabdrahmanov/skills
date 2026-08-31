#!/usr/bin/env python3
"""P1-4: единая деривация фазовой машины из manifest.

Пиним инвариант: gate.json — производный view, и его статусы считаются ОДНОЙ функцией
(build_gate), с единой семантикой container-шагов. Раньше build_gate (учитывал container)
и phase_sync (исключал container) расходились; здесь фиксируем согласованную семантику.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_phases as pp


class TestIsContainerStep(unittest.TestCase):
    def test_main_phase_ids_are_containers(self):
        for pid in ("04-tdd", "05-verify", "06-document", "00-brd"):
            self.assertTrue(pp.is_container_step(pid), pid)

    def test_dynamic_steps_are_not_containers(self):
        for sid in ("04-test-T1", "04-build-T1", "02-sdd-foo"):
            self.assertFalse(pp.is_container_step(sid), sid)


class TestBuildGateContainerSemantics(unittest.TestCase):
    """Фаза с container-шагом И динамическими: завершённость считается по динамическим,
    статус самого container-шага не должен гейтить фазу."""

    def _manifest(self, statuses: dict) -> dict:
        steps = [{"id": sid, "status": st, "title": sid} for sid, st in statuses.items()]
        return {"pipeline_id": "p", "feature": "f", "steps": steps}

    def test_phase_completed_by_dynamic_despite_container_in_progress(self):
        # 04-tdd (container) ещё in_progress, но обе динамические задачи completed → фаза completed
        m = self._manifest({
            "04-tdd": "in_progress",
            "04-test-T1": "completed",
            "04-build-T1": "completed",
        })
        gate = pp.build_gate(m["steps"], m)
        tdd = next(p for p in gate["phases"] if p["id"] == "04-tdd")
        self.assertEqual(tdd["status"], "completed")

    def test_phase_not_completed_if_dynamic_pending(self):
        m = self._manifest({
            "04-tdd": "completed",          # container completed, но
            "04-test-T1": "completed",
            "04-build-T1": "in_progress",   # динамика не закрыта → фаза НЕ completed
        })
        gate = pp.build_gate(m["steps"], m)
        tdd = next(p for p in gate["phases"] if p["id"] == "04-tdd")
        self.assertNotEqual(tdd["status"], "completed")

    def test_container_only_phase_uses_container_status(self):
        # Нет динамических шагов — статус берётся по самому container-шагу
        m = self._manifest({"00-brd": "completed", "01-grounding": "pending"})
        gate = pp.build_gate(m["steps"], m)
        brd = next(p for p in gate["phases"] if p["id"] == "00-brd")
        self.assertEqual(brd["status"], "completed")


class TestLivePhaseDecision(unittest.TestCase):
    def test_current_phase_from_manifest(self):
        m = {"steps": [
            {"id": "00-brd", "status": "completed", "title": "brd"},
            {"id": "01-grounding", "status": "pending", "title": "gr"},
            {"id": "02-design", "status": "pending", "title": "d"},
        ]}
        d = pp.live_phase_decision(m)
        self.assertEqual(d["current_phase"], "01-grounding")

    def test_all_completed_empty_current(self):
        m = {"steps": [
            {"id": "00-brd", "status": "completed", "title": "brd"},
            {"id": "07-report", "status": "completed", "title": "r"},
        ]}
        self.assertEqual(pp.live_phase_decision(m)["current_phase"], "")

    def test_empty_manifest(self):
        self.assertEqual(pp.live_phase_decision({})["current_phase"], "")
        self.assertEqual(pp.live_phase_decision(None)["current_phase"], "")


if __name__ == "__main__":
    unittest.main()
