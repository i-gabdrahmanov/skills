#!/usr/bin/env python3
"""Smoke test for hooks/gate-guard.py.

Раньше здесь был авто-стаб с `import gate-guard as mod` — это SyntaxError (дефис в имени), поэтому
тест НИКОГДА не запускался (как и весь набор test_*.py хуков). Теперь: модуль грузится через
importlib (ловит регрессии синтаксиса/импорта) и проверяется fail-open на пустом stdin (общий
контракт хуков — не ронять инструмент на не-JSON входе). Поведенческое покрытие — hooks/evals/run-evals.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "gate-guard.py"


def _bash_payload(command: str, cwd: str) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "cwd": cwd,
                       "tool_name": "Bash", "tool_input": {"command": command}})


def _run(command: str, cwd: str):
    return subprocess.run([sys.executable, str(HOOK)], input=_bash_payload(command, cwd),
                          capture_output=True, text=True, timeout=30)


class T(unittest.TestCase):
    def test_module_loads(self):
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)          # регрессия синтаксиса/импорта
        self.assertTrue(hasattr(m, "main"))

    def test_failopen_empty_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


class TGateOverride(unittest.TestCase):
    """Пин: снятие детерминированного гейта (override_judge.py) — R4-класс.
    Раньше override требовал только --reason и печатался готовой командой в deny-баннерах —
    модель снимала step-reopen/gate-result/судей одной командой без человека."""

    CMD = ("python3 .gigacode/skills/pipeline-state/scripts/override_judge.py "
           "--judge step-reopen-04-build-T1 --feature f1 --step-id 04-build-T1 "
           "--reason \"ещё итерация\"")

    def test_create_without_approval_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("gate-override-step-reopen-04-build-T1.json", r.stderr)

    def test_create_with_approval_passes(self):
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            # маркер засчитывается только с провенансом record_approval (как пишет record_approval.py)
            (appr / "gate-override-step-reopen-04-build-T1.json").write_text(
                json.dumps({"produced_by": "record_approval", "approved_by": "user",
                            "reason": "ok"}), encoding="utf-8")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_handwritten_approval_without_provenance_blocked(self):
        # BLOCKER-1 backstop: маркер БЕЗ produced_by:"record_approval" (самовыписанный) не снимает гейт
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / "gate-override-step-reopen-04-build-T1.json").write_text(
                json.dumps({"approved_by": "user", "reason": "ok"}), encoding="utf-8")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "рукописный маркер без провенанса не должен снимать гейт")
            self.assertIn("провенанс", r.stderr.lower())

    def test_foreign_approval_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / "gate-override-coverage-judge.json").write_text("{}", encoding="utf-8")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "approval чужого судьи не должен снимать этот гейт")

    def test_reason_text_containing_list_is_not_readonly(self):
        # M2: --list ВНУТРИ значения --reason не должен трактоваться как readonly-флаг (обход)
        with tempfile.TemporaryDirectory() as td:
            cmd = ("python3 .gigacode/skills/pipeline-state/scripts/override_judge.py "
                   "--judge step-reopen-04-build-T1 --feature f1 --step-id 04-build-T1 "
                   "--reason \"cleanup --list marker\"")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 2,
                             "--list в тексте --reason не снимает approval-гейт")

    def test_list_and_remove_are_free(self):
        with tempfile.TemporaryDirectory() as td:
            base = "python3 .gigacode/skills/pipeline-state/scripts/override_judge.py --feature f1"
            r = _run(f"{base} --list", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(f"{base} --judge coverage-judge --remove", td)
            self.assertEqual(r.returncode, 0,
                             f"--remove (восстановление enforcement) не гейтится: {r.stderr}")


class TRollback(unittest.TestCase):
    """Пин: откат пайплайна (rollback.py) — R4-класс, deny-first. Уничтожает рабочие
    результаты (код, evidence шагов) и порождает сирот в Jira/PR — без approval-маркера
    rollback-<feature>-<to-step> (провенанс record_approval) скрипт не запускается;
    classify дал бы команде default-R1 — без deny-first прошёл бы авто."""

    CMD = ("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
           "--skill feature-pipeline --feature f1 --to-step 02-sdd")

    def _approve(self, td: str, key: str, provenance: bool = True) -> None:
        appr = Path(td) / "ground" / "approvals"
        appr.mkdir(parents=True, exist_ok=True)
        body = {"approved_by": "user", "reason": "ok"}
        if provenance:
            body["produced_by"] = "record_approval"
        (appr / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")

    def test_rollback_without_approval_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("rollback-f1-02-sdd.json", r.stderr)

    def test_rollback_with_approval_passes(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "rollback-f1-02-sdd")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_handwritten_marker_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "rollback-f1-02-sdd", provenance=False)
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "рукописный маркер без провенанса не снимает гейт")
            self.assertIn("провенанс", r.stderr.lower())

    def test_foreign_marker_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "rollback-f1-04-build-T1")  # согласие на ДРУГОЙ шаг
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "approval другого шага не снимает этот гейт")

    def test_dry_run_and_list_are_free(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(f"{self.CMD} --dry-run", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                     "--skill feature-pipeline --feature f1 --list", td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_dry_run_inside_value_is_not_readonly(self):
        # --dry-run внутри значения аргумента не должен трактоваться как readonly (обход)
        with tempfile.TemporaryDirectory() as td:
            cmd = ("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                   "--skill feature-pipeline --feature \"f1 --dry-run\" --to-step 02-sdd")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 2, "--dry-run в тексте значения не снимает гейт")

    def test_missing_target_args_blocked(self):
        # ключ маркера не резолвится без --feature/--to-step → deny с пояснением
        with tempfile.TemporaryDirectory() as td:
            r = _run("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                     "--skill feature-pipeline", td)
            self.assertEqual(r.returncode, 2)
            self.assertIn("не резолвится", r.stderr)

    def test_to_phase_uses_same_key_scheme(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = ("python3 .gigacode/skills/pipeline-state/scripts/rollback.py "
                   "--skill feature-pipeline --feature f1 --to-phase 02-sdd")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self._approve(td, "rollback-f1-02-sdd")
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 0, r.stderr)


class TSkipJudges(unittest.TestCase):
    """Пин: `update.py --skip-judges` снимает ВСЕ гейты закрытия шага (судьи, gate-result,
    subagent-origin, обязательные решения, артефакты) — R4-класс. Был bypass в одну опцию:
    флаг задумывался под восстановление стейта после init --force, а работал как общий
    выключатель enforcement'а. Второй слой — сам update.py валидирует маркер."""

    CMD = ("python3 .gigacode/skills/pipeline-state/scripts/update.py "
           "--skill forgefix --feature f1 --step-id fix-red --status completed --skip-judges")

    def _approve(self, td: str, key: str, provenance: bool = True) -> None:
        appr = Path(td) / "ground" / "approvals"
        appr.mkdir(parents=True, exist_ok=True)
        body = {"approved_by": "user", "reason": "restore after init --force"}
        if provenance:
            body["produced_by"] = "record_approval"
        (appr / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")

    def test_without_approval_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("skip-judges-f1.json", r.stderr)

    def test_with_approval_passes(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "skip-judges-f1")
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_handwritten_marker_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._approve(td, "skip-judges-f1", provenance=False)
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "рукописный маркер без провенанса не снимает гейт")

    def test_normal_update_without_flag_is_free(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run("python3 .gigacode/skills/pipeline-state/scripts/update.py "
                     "--skill forgefix --feature f1 --step-id fix-red --status completed", td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_flag_inside_value_is_not_a_bypass(self):
        # флаг, упомянутый в тексте значения, не должен считаться настоящим флагом
        with tempfile.TemporaryDirectory() as td:
            r = _run("python3 .gigacode/skills/pipeline-state/scripts/update.py "
                     "--skill forgefix --feature f1 --step-id fix-red --status failed "
                     "--error \"gate --skip-judges не помог\"", td)
            self.assertEqual(r.returncode, 0, r.stderr)


def _write_run(file_path: str, cwd: str):
    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": cwd,
                          "tool_name": "write_file", "tool_input": {"file_path": file_path}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


class TRequiredDecisions(unittest.TestCase):
    """Thrust 1 fail-closed: продуктивная запись фазы блокируется без записанного решения."""

    @staticmethod
    def _mk(td: str, spec: str | None = None):
        d = Path(td) / "ground" / "statements" / "forgelite" / "f1"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"steps": [{"id": "lite-design", "status": "in_progress"}]}),
            encoding="utf-8")
        cfg = {"autonomy": {"criticality": "medium", "auto_max_risk": "R2"}}
        if spec:
            cfg["sources"] = {"spec": spec}
        (Path(td) / "ground" / "pipeline.json").write_text(json.dumps(cfg), encoding="utf-8")

    def test_write_blocked_without_required_decision(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td)
            r = _write_run("docs/feature-pipeline/f1/tech-design.md", td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("sources.spec", r.stderr)

    def test_write_passes_when_decision_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, spec="docs/feature-pipeline/f1/existing-spec.md")
            r = _write_run("docs/feature-pipeline/f1/tech-design.md", td)
            self.assertEqual(r.returncode, 0, r.stderr)


def _mk_fix_state(td: str, steps: list[dict], cfg: dict | None = None, feature: str = "BUG-512"):
    """Манифест fix-ветки + pipeline.json. steps — [{"id":..., "status":...}, ...]."""
    d = Path(td) / "ground" / "statements" / "forgefix" / feature
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
    base = {"autonomy": {"criticality": "medium", "auto_max_risk": "R2"}}
    base.update(cfg or {})
    (Path(td) / "ground" / "pipeline.json").write_text(json.dumps(base), encoding="utf-8")
    return d


def _approval(td: str, key: str, provenance: bool = True):
    p = Path(td) / "ground" / "approvals"
    p.mkdir(parents=True, exist_ok=True)
    rec = {"key": key, "approved_by": "user", "reason": "ok"}
    if provenance:
        rec["produced_by"] = "record_approval"
    (p / f"{key}.json").write_text(json.dumps(rec), encoding="utf-8")


class TCurrentStepResolver(unittest.TestCase):
    """Фазовые гейты обязаны работать БЕЗ статуса in_progress.

    Его никто не проставляет: update.py ведёт шаг pending → completed, промежуточную пометку
    брифы не делают. Пока гейты смотрели только на in_progress, весь слой «активная фаза»
    (required_decisions, phase_approvals) молчал на живых прогонах — фикс уходил писать код,
    не спросив ни стори, ни утверждения плана."""

    FIX_PLAN = "docs/feature-pipeline/STOR-100/fixes/BUG-512/fix-plan.md"

    def test_required_decision_fires_without_in_progress(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, [{"id": "fix-intake", "status": "completed"},
                               {"id": "fix-diag", "status": "pending",
                                "depends_on": ["fix-intake"]}])
            r = _write_run(self.FIX_PLAN, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("sources.story", r.stderr)

    def test_passes_when_story_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, [{"id": "fix-intake", "status": "completed"},
                               {"id": "fix-diag", "status": "pending",
                                "depends_on": ["fix-intake"]}],
                          cfg={"sources": {"story": "STOR-100"}})
            r = _write_run(self.FIX_PLAN, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_explicit_in_progress_still_wins(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, [{"id": "fix-intake", "status": "in_progress"},
                               {"id": "fix-diag", "status": "pending",
                                "depends_on": ["fix-intake"]}])
            # активна fix-intake (у неё требований нет) — запись не блокируется
            r = _write_run(self.FIX_PLAN, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_parallel_phases_stay_fail_open(self):
        """Готовы к работе шаги РАЗНЫХ фаз (параллельные задачи full-пути) — фазу не угадываем."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "ground" / "statements" / "forgelite" / "f1"
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(json.dumps({"steps": [
                {"id": "lite-design", "status": "pending"},
                {"id": "lite-red", "status": "pending"},
            ]}), encoding="utf-8")
            (Path(td) / "ground" / "pipeline.json").write_text(
                json.dumps({"autonomy": {"criticality": "medium", "auto_max_risk": "R2"}}),
                encoding="utf-8")
            r = _write_run("docs/feature-pipeline/f1/tech-design.md", td)
            self.assertEqual(r.returncode, 0, r.stderr)


class TPhaseApproval(unittest.TestCase):
    """Гейт утверждения плана человеком: без approval-маркера фазы fix-red/fix-green не пишут.
    Раньше «покажи план и спроси» жило только в брифе — и прогон уходил писать код молча."""

    STEPS = [{"id": "fix-intake", "status": "completed"},
             {"id": "fix-diag", "status": "completed", "depends_on": ["fix-intake"]},
             {"id": "fix-red", "status": "pending", "depends_on": ["fix-diag"]}]
    CFG = {"sources": {"story": "STOR-100", "spec_anchor": "REQ-0007"}}
    TEST_FILE = "src/test/java/com/acme/ReportServiceTest.java"

    def test_red_write_blocked_without_approval(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, self.STEPS, self.CFG)
            r = _write_run(self.TEST_FILE, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("fix-plan-BUG-512.json", r.stderr)

    def test_red_write_passes_with_approval(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, self.STEPS, self.CFG)
            _approval(td, "fix-plan-BUG-512")
            r = _write_run(self.TEST_FILE, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_handwritten_marker_without_provenance_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, self.STEPS, self.CFG)
            _approval(td, "fix-plan-BUG-512", provenance=False)
            r = _write_run(self.TEST_FILE, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("провенанса", r.stderr)

    def test_green_code_write_blocked_without_approval(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, [{"id": "fix-red", "status": "completed"},
                               {"id": "fix-green", "status": "pending",
                                "depends_on": ["fix-red"]}], self.CFG)
            r = _write_run("src/main/java/com/acme/ReportService.java", td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("fix-plan-BUG-512.json", r.stderr)

    def test_approval_of_other_feature_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, self.STEPS, self.CFG)
            _approval(td, "fix-plan-BUG-999")
            r = _write_run(self.TEST_FILE, td)
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_gate_is_inert_outside_fix_phases(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, [{"id": "fix-spec", "status": "pending"}], self.CFG)
            r = _write_run("docs/feature-pipeline/STOR-100/fixes/BUG-512/sdd.md", td)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
