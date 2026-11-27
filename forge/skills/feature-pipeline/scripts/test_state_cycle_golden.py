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
RECORD_APPROVAL = REPO / "skills/pipeline-state/scripts/record_approval.py"
STATE_RECORDER = REPO / "hooks/state-recorder.py"
ADD_STEPS_FP = REPO / "skills/feature-pipeline/scripts/add_steps.py"

sys.path.insert(0, str(REPO / "skills/feature-pipeline/scripts"))
import pipeline_phases as _pp  # noqa: E402
import forge_events as FE  # noqa: E402

SLUG = "KID-1"  # Jira-ключ: init.py гейтит feature-pipeline на формат ключа

STATIC_STEPS = [
    {"id": "00-brd", "depends_on": []},
    {"id": "01-grounding", "depends_on": []},
    {"id": "02-design", "depends_on": ["00-brd", "01-grounding"]},
    {"id": "02-eval-plan", "depends_on": ["02-design"]},
    {"id": "03-jira", "depends_on": ["02-design"]},
    {"id": "05-tests", "depends_on": []},
    {"id": "06-spec", "depends_on": ["05-tests"]},
]
DYNAMIC_STEPS = [
    {"id": "04-test-TASK1", "depends_on": ["02-design"]},
    {"id": "04-build-TASK1", "depends_on": ["04-test-TASK1", "02-eval-plan"]},
]
# Вердикты, имена которых совпадают с required_judges всех шагов выше.
JUDGE_VERDICTS = ["brd-judge", "eval-judge", "coverage-judge", "spec-judge", "red-judge"]

UPDATE_ORDER = ["00-brd", "01-grounding", "02-design", "02-eval-plan", "03-jira",
                "04-test-TASK1", "04-build-TASK1", "05-tests", "06-spec"]


def _run(args, cwd):
    return subprocess.run([sys.executable, *map(str, args)],
                          cwd=str(cwd), capture_output=True, text=True, timeout=60)


class GoldenStateCycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.docs = self.proj / "docs/feature-pipeline" / SLUG
        self.docs.mkdir(parents=True)
        (self.docs / "tech-design.md").write_text("# Исходный дизайн\n", encoding="utf-8")
        (self.docs / "task-plan.json").write_text('{"tasks":[{"id":"TASK1"}]}', encoding="utf-8")
        (self.docs / "sdd.md").write_text("# SDD\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _state_dir(self, feature=SLUG):
        return self.proj / "ground/statements/feature-pipeline" / feature

    def _design_digest(self):
        r = _run([RECORD_APPROVAL, "--project", self.proj, "--design-digest", SLUG], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def _approve_design(self, digest):
        return _run([RECORD_APPROVAL, "--project", self.proj, "--key", f"design-approved-{SLUG}",
                     "--approved-by", "user", "--reason", "Гейт 2",
                     "--evidence", f"Да, утверждаю техдизайн для {SLUG}",
                     "--design-sha256", digest], self.proj)

    def _write_verdicts(self, feature=SLUG, *, approved_docs=("brd", "sdd")):
        jdir = self._state_dir(feature) / "judges"
        jdir.mkdir(parents=True, exist_ok=True)
        for name in JUDGE_VERDICTS:
            (jdir / f"{name}.json").write_text(json.dumps({
                "produced_by": "run_judge",
                "judge": name, "passed": True, "verdict": "PASS",
                "blocking_issues": [],
            }), encoding="utf-8")
        self._write_design_verdict()
        for name in FE.TASK_JUDGES:
            FE.append_event(self.proj, "feature-pipeline", feature, "judge",
                            judge=name, layer="final", step_id="04-build-TASK1", passed=True,
                            verdict="PASS")
        # маркеры утверждения доков (их пишет record_approval после «да» пользователя) —
        # update._check_doc_approval не закроет документные фазы без них
        appr = self.proj / "ground/approvals"
        appr.mkdir(parents=True, exist_ok=True)
        for doc in approved_docs:
            key = f"{doc}-approved-{feature}"
            (appr / f"{key}.json").write_text(json.dumps(
                {"produced_by": "record_approval", "key": key, "approved_by": "user",
                 "reason": "test"}), encoding="utf-8")
        # Содержательная выжимка системы — update._check_grounding_substance не закроет
        # 01-grounding без неё (0 модулей и 0 entities = заглушка).
        sa = self.proj / "ground/inventory"
        sa.mkdir(parents=True, exist_ok=True)
        (sa / "grounding-excerpt.json").write_text(json.dumps(
            {"modules": [{"name": "service-x"}], "entities": [{"name": "Foo"}]}),
            encoding="utf-8")

    def _write_design_verdict(self):
        FE.append_event(self.proj, "feature-pipeline", SLUG, "judge",
                        judge="design-judge", layer="final", step_id="02-design",
                        passed=True, verdict="PASS", **FE.design_judge_inputs(self.docs))

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
        approved = self._approve_design(self._design_digest())
        self.assertEqual(approved.returncode, 0, approved.stderr)

        # 4. закрываем все шаги БЕЗ --skip-judges
        odir = self._state_dir() / "_origins"
        odir.mkdir(parents=True, exist_ok=True)
        gdir = self._state_dir() / "gates"
        gdir.mkdir(parents=True, exist_ok=True)
        for step_id in UPDATE_ORDER:
            # evidence-маркер от SubagentStop (его пишет state-recorder) — update требует его
            # для subagent-фаз, флаг --closed-by больше не доказательство
            (odir / f"{step_id}.json").write_text(json.dumps({"step_id": step_id}), encoding="utf-8")
            # gate-result evidence (его пишет record_gate.py) — update требует его
            # для build/verify-шагов (GATE_RESULT_PREFIXES)
            (gdir / f"{step_id}.json").write_text(json.dumps(
                {"produced_by": "record_gate", "step_id": step_id, "passed": True}), encoding="utf-8")
            r = _run([UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                      "--feature", SLUG, "--step-id", step_id, "--status", "completed",
                      # как state-recorder на SubagentStop — иначе subagent-фазы блокируются (C3)
                      "--closed-by", "subagent",
                      "--output-json", json.dumps({"step_id": step_id})], self.proj)
            self.assertEqual(r.returncode, 0,
                             f"шаг {step_id} не закрылся: {r.stderr or r.stdout}")

        # 5. все шаги completed
        manifest = json.loads((self._state_dir() / "manifest.json").read_text(encoding="utf-8"))
        statuses = {s["id"]: s["status"] for s in manifest["steps"]}
        self.assertTrue(all(v == "completed" for v in statuses.values()),
                        f"не все шаги completed: {statuses}")

        # 6. фазовая машина дошла до конца (состояние выводится из манифеста, кэша нет)
        manifest_now = json.loads((self._state_dir() / "manifest.json").read_text(encoding="utf-8"))
        gate = _pp.live_phase_decision(manifest_now)
        self.assertEqual(gate["current_phase"], "",
                         f"gate застрял на '{gate['current_phase']}' (P0-2?)")
        # 6a. фазы в каноническом порядке (динамическая 04-tdd не в конце)
        main_order = ["00-brd", "01-grounding", "02-design", "02-eval-plan", "03-jira",
                      "04-tdd", "05-verify", "06-document"]
        ids = [p["id"] for p in gate["phases"]]
        self.assertEqual(ids, [m for m in main_order if m in ids],
                         f"фазы не в каноническом порядке: {ids}")
        self.assertTrue(all(p["status"] == "completed" for p in gate["phases"]),
                        f"не все фазы completed: {[(p['id'], p['status']) for p in gate['phases']]}")

    def test_design_requires_its_own_approval(self):
        r = _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", SLUG, "--steps", json.dumps([{"id": "02-design", "depends_on": []}])],
                 self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self._write_verdicts()
        recorded = subprocess.run([sys.executable, str(STATE_RECORDER)], cwd=self.proj,
                                  input=json.dumps({"hook_event_name": "SubagentStop", "cwd": str(self.proj),
                                                    "last_assistant_message": json.dumps({
                                                        "step_id": "02-design", "status": "unit-done"})}),
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        stopped = subprocess.run([sys.executable, str(REPO / "hooks/phase-gate.py")], cwd=self.proj,
                                 input=json.dumps({"cwd": str(self.proj)}),
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(stopped.stdout, "", stopped.stdout)
        update = [UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", SLUG, "--step-id", "02-design", "--status", "completed"]
        denied = _run(update, self.proj)
        self.assertEqual(denied.returncode, 2, denied.stderr)
        self.assertIn(f"design-approved-{SLUG}", denied.stderr)
        manifest = json.loads((self._state_dir() / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["steps"][0]["status"], "pending")
        self._write_verdicts(approved_docs=("design",))
        legacy = _run(update, self.proj)
        self.assertEqual(legacy.returncode, 2, legacy.stderr)
        self.assertIn("не подтверждает текущие", legacy.stderr)

        digest = self._design_digest()
        approved = self._approve_design(digest)
        self.assertEqual(approved.returncode, 0, approved.stderr)
        old_inputs = FE.design_judge_inputs(self.docs)
        del old_inputs["design_check_version"]
        FE.append_event(self.proj, "feature-pipeline", SLUG, "judge", judge="design-judge",
                        layer="final", step_id="02-design", passed=True, verdict="PASS", **old_inputs)
        old_pass = _run(update, self.proj)
        self.assertEqual(old_pass.returncode, 2, old_pass.stderr)
        self.assertIn("до добавления обязательной проверки разделов", old_pass.stderr)
        self._write_design_verdict()
        for name, contents in (("tech-design.md", "# Новый дизайн\n"),
                               ("task-plan.json", '{"tasks":[{"id":"TASK2"}]}')):
            with self.subTest(name=name):
                (self.docs / name).write_text(contents, encoding="utf-8")
                stale = _run(update, self.proj)
                self.assertEqual(stale.returncode, 2, stale.stderr)
                self.assertIn("design-judge проверял другую версию", stale.stderr)
                refused = self._approve_design(digest)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("версия техдизайна изменилась", refused.stderr)
                digest = self._design_digest()
                approved = self._approve_design(digest)
                self.assertEqual(approved.returncode, 0, approved.stderr)
                stale = _run(update, self.proj)
                self.assertIn("design-judge проверял другую версию", stale.stderr)
                self._write_design_verdict()
        closed = _run(update, self.proj)
        self.assertEqual(closed.returncode, 0, closed.stderr)

    def test_step_blocked_without_verdict(self):
        """02-design нельзя закрыть, пока нет design-judge.json (enforcement жив)."""
        r = _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", "KID-2",
                  "--steps", json.dumps([{"id": "02-design", "depends_on": []}])], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        origins = self._state_dir("KID-2") / "_origins"
        origins.mkdir()
        (origins / "02-design.json").write_text('{"step_id":"02-design"}', encoding="utf-8")
        # без вердикта design-judge
        r = _run([UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", "KID-2", "--step-id", "02-design", "--status", "completed",
                  "--output-json", json.dumps({"step_id": "02-design"})], self.proj)
        self.assertNotEqual(r.returncode, 0,
                            "02-design закрылся без вердикта судьи — enforcement сломан")
        self.assertIn("Вердикт 'design-judge' не найден", r.stderr)

    def test_build_step_cannot_close_on_another_steps_verdicts(self):
        first, second = "04-build-TASK1", "04-build-TASK2"
        initialized = _run([INIT, "--project", self.proj, "--skill", "feature-pipeline",
                            "--feature", SLUG, "--steps",
                            json.dumps([{"id": sid, "depends_on": []} for sid in (first, second)])],
                           self.proj)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        for judge in FE.TASK_JUDGES:
            FE.append_event(self.proj, "feature-pipeline", SLUG, "judge", judge=judge,
                            layer="final", step_id=first, passed=True, verdict="PASS")
        FE.append_event(self.proj, "feature-pipeline", SLUG, "origin", step_id=second)
        FE.append_event(self.proj, "feature-pipeline", SLUG, "gate", step_id=second, passed=True)
        update = [UPDATE, "--project", self.proj, "--skill", "feature-pipeline",
                  "--feature", SLUG, "--step-id", second, "--status", "completed"]
        denied = _run(update, self.proj)
        self.assertEqual(denied.returncode, 2, denied.stderr)
        for judge in FE.TASK_JUDGES:
            self.assertIn(f"Вердикт '{judge}' не найден", denied.stderr)
            FE.append_event(self.proj, "feature-pipeline", SLUG, "judge", judge=judge,
                            layer="final", step_id=second, passed=True, verdict="PASS")
        closed = _run(update, self.proj)
        self.assertEqual(closed.returncode, 0, closed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
