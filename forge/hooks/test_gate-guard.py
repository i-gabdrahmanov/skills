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
                json.dumps({"key": "gate-override-step-reopen-04-build-T1",
                            "produced_by": "record_approval", "approved_by": "user",
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
        # key обязателен в теле: legacy-читатель FE.approval засчитывает маркер только при
        # совпадении содержимого (переименованный/чужой файл не снимает гейт)
        body["key"] = key
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
        body["key"] = key  # legacy-читатель FE.approval сверяет содержимое с запрошенным ключом
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
            self.assertIn("inputs.spec", r.stderr)

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
            self.assertIn("inputs.story", r.stderr)

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


class TApprovalJsonl(unittest.TestCase):
    """Пин: gate-guard читает approval через FE.approval(), а не прямой _read_json по legacy
    файлу. Это регрессионный набор для KIDPPRB-9254 (п.2): record_approval пишет согласия
    строкой в ground/approvals.jsonl, и гейт ОБЯЗАН их видеть. Старые legacy-файлы
    approvals/<key>.json продолжают работать (обратная совместимость прогонов до миграции).
    Запись без провенанса или с подделанным produced_by — не засчитывается (BLOCKER-1)."""

    KEY_OVERRIDE = "gate-override-step-reopen-04-build-T1"
    CMD_OVERRIDE = ("python3 .gigacode/skills/pipeline-state/scripts/override_judge.py "
                    "--judge step-reopen-04-build-T1 --feature f1 --step-id 04-build-T1 "
                    "--reason \"ещё итерация\"")

    @staticmethod
    def _append_jsonl(td: str, rec: dict) -> None:
        """Дописать строку в ground/approvals.jsonl (как пишет record_approval.py)."""
        ground = Path(td) / "ground"
        ground.mkdir(parents=True, exist_ok=True)
        path = ground / "approvals.jsonl"
        # append-режим: не затираем уже существующие записи (re-grant, revoke и т.п.)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ─── (a) approval через JSONL → valid ──────────────────────────────────────

    def test_jsonl_approval_unlocks_gate_override(self):
        """(a) JSONL-запись с produced_by:record_approval снимает гейт gate-override.
        До правки gate-guard._approval_valid читал _read_json(approval_path(...)) и
        не видел ничего — каждый override блокировался."""
        with tempfile.TemporaryDirectory() as td:
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z",
                "kind": "approval",
                "produced_by": "record_approval",
                "key": self.KEY_OVERRIDE,
                "approved_by": "user",
                "reason": "ok",
            })
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_jsonl_approval_unlocks_phase_approval(self):
        """JSONL работает и через _phase_approval_missing (другой путь _approval_valid)."""
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, TPhaseApproval.STEPS, TPhaseApproval.CFG)
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z",
                "kind": "approval",
                "produced_by": "record_approval",
                "key": "fix-plan-BUG-512",
                "approved_by": "user",
                "reason": "ok",
            })
            r = _write_run(TPhaseApproval.TEST_FILE, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_jsonl_re_grant_after_revoke_unlocks(self):
        """revoked запись в логе перекрывает выданное согласие (см. FE.approval);
        re-grant ПОСЛЕ revoke — снова валиден (позиции в логе, а не index())."""
        with tempfile.TemporaryDirectory() as td:
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z", "kind": "approval",
                "produced_by": "record_approval", "key": self.KEY_OVERRIDE,
                "approved_by": "user", "reason": "first"})
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:01:00Z", "kind": "approval-revoked",
                "produced_by": "rollback", "key": self.KEY_OVERRIDE,
                "reason": "rollback"})
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:02:00Z", "kind": "approval",
                "produced_by": "record_approval", "key": self.KEY_OVERRIDE,
                "approved_by": "user", "reason": "again"})
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    # ─── (b) legacy approval по-прежнему valid (обратная совместимость) ──────

    def test_legacy_marker_still_unlocks(self):
        """(b) Старый файл ground/approvals/<key>.json (прогон до миграции) — всё ещё
        снимает гейт: FE.approval делает fallback на legacy после чтения журнала."""
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / f"{self.KEY_OVERRIDE}.json").write_text(
                json.dumps({"key": self.KEY_OVERRIDE,
                            "produced_by": "record_approval",
                            "approved_by": "user", "reason": "ok"}),
                encoding="utf-8")
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_jsonl_overrides_legacy_when_both_present(self):
        """Журнал — источник истины: запись в .jsonl с produced_by:record_approval
        засчитывается, даже если рядом лежит legacy-файл (новые записи не должны
        ломаться присутствием старой раскладки на диске)."""
        with tempfile.TemporaryDirectory() as td:
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z", "kind": "approval",
                "produced_by": "record_approval", "key": self.KEY_OVERRIDE,
                "approved_by": "user", "reason": "ok"})
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True, exist_ok=True)
            (appr / f"{self.KEY_OVERRIDE}.json").write_text("{}", encoding="utf-8")
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    # ─── (c) approval без produced_by → invalid ────────────────────────────────

    def test_jsonl_without_provenance_blocked(self):
        """(c) Строка в .jsonl без produced_by — рукописная/поддельная, не снимает гейт.
        BLOCKER-1: иначе модель могла бы дописать свою «согласие» мимо record_approval."""
        with tempfile.TemporaryDirectory() as td:
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z",
                "kind": "approval",
                # produced_by отсутствует
                "key": self.KEY_OVERRIDE,
                "approved_by": "user",
                "reason": "ok",
            })
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 2,
                             "запись без produced_by не должна снимать approval-гейт")

    def test_legacy_marker_without_provenance_blocked(self):
        """Legacy-файл без produced_by тоже не снимает гейт (BLOCKER-1 backstop)."""
        with tempfile.TemporaryDirectory() as td:
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / f"{self.KEY_OVERRIDE}.json").write_text(
                json.dumps({"key": self.KEY_OVERRIDE,
                            "approved_by": "user", "reason": "ok"}),
                encoding="utf-8")
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("провенанс", r.stderr.lower())

    # ─── (d) approval с подделанным produced_by → invalid ─────────────────────

    def test_jsonl_with_forged_provenance_blocked(self):
        """(d) Строка с produced_by != "record_approval" (например, "subagent_origin"
        или "manual") не считается согласием. _authentic() в FE.approval фильтрует
        по точному значению, и подделать kind/produced_by из payload нельзя — это
        _CONTROL_FIELDS, перебиваются журналом."""
        with tempfile.TemporaryDirectory() as td:
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z",
                "kind": "approval",
                "produced_by": "subagent_origin",  # подделка: не record_approval
                "key": self.KEY_OVERRIDE,
                "approved_by": "user",
                "reason": "ok",
            })
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 2,
                             "строка с чужим produced_by не должна снимать approval-гейт")

    def test_jsonl_with_forged_kind_blocked(self):
        """Строка с kind != "approval" (например, "judge" — попытка переиспользовать
        запись другого kind как approval) тоже не засчитывается. _authentic сверяет
        и kind, и produced_by."""
        with tempfile.TemporaryDirectory() as td:
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z",
                "kind": "override",  # подделка kind
                "produced_by": "record_approval",
                "key": self.KEY_OVERRIDE,
                "approved_by": "user",
                "reason": "ok",
            })
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 2,
                             "строка с kind != approval не должна снимать approval-гейт")

    def test_revoked_in_jsonl_blocks_legacy_fallback(self):
        """Если в логе есть revoked-запись ПОСЛЕ выданного согласия (re-grant
        потом rollback), legacy-файл не оживляет маркер: revoked-позиция в логе
        явно > granted-позиции, FE.approval возвращает None даже при наличии
        файла старой раскладки."""
        with tempfile.TemporaryDirectory() as td:
            # legacy-файл с валидным провенансом лежит на диске
            appr = Path(td) / "ground" / "approvals"
            appr.mkdir(parents=True)
            (appr / f"{self.KEY_OVERRIDE}.json").write_text(
                json.dumps({"key": self.KEY_OVERRIDE,
                            "produced_by": "record_approval",
                            "approved_by": "user", "reason": "ok"}),
                encoding="utf-8")
            # в журнале — revoke, который должен перекрыть любой источник
            self._append_jsonl(td, {
                "ts": "2026-08-22T12:00:00Z", "kind": "approval-revoked",
                "produced_by": "rollback", "key": self.KEY_OVERRIDE,
                "reason": "rolled back"})
            r = _run(self.CMD_OVERRIDE, td)
            self.assertEqual(r.returncode, 2,
                             "revoked в логе должен перекрывать legacy-файл")


def _mk_full_state(td: str, steps: list, feature: str = "feat-x"):
    """Манифест full-ветки (feature-pipeline) с явными decisions."""
    d = Path(td) / "ground" / "statements" / "feature-pipeline" / feature
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "version": 2, "skill": "feature-pipeline", "feature": feature,
        "decisions": {"criticality": "low", "auto_max_risk": "R2"},
        "steps": steps}), encoding="utf-8")
    return d


def _run_payload(payload: dict):
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)


class TAgentTypeNotAPincer(unittest.TestCase):
    """Задача 008: generic-тип субагента — не заявка на скилл.

    Форк зовёт всех субагентов как `general-purpose`, а `allowed_skills` фазы перечисляет
    ИМЕНА СКИЛЛОВ. Пока проверка сравнивала одно с другим, из субагента в фазе 02-design не
    проходил даже `ls -la`; а пустой agent_type в это же время рубил inline-phase-guard —
    работу блокировал один из двух хуков при ЛЮБОМ значении поля."""

    STEPS = [{"id": "01-grounding", "status": "completed", "depends_on": []},
             {"id": "02-design", "status": "pending", "depends_on": ["01-grounding"]}]

    def _payload(self, td: str, agent_type):
        p = {"hook_event_name": "PreToolUse", "cwd": td, "tool_name": "Bash",
             "tool_input": {"command": "ls -la"}}
        if agent_type is not None:
            p["agent_type"] = agent_type
        return p

    def test_generic_subagent_type_passes(self):
        for at in ("general-purpose", "General-Purpose", "task", "subagent"):
            with tempfile.TemporaryDirectory() as td:
                _mk_full_state(td, self.STEPS)
                r = _run_payload(self._payload(td, at))
                self.assertEqual(r.returncode, 0,
                                 f"generic agent_type={at!r} не должен упираться в "
                                 f"allowed_skills:\n{r.stderr}")

    def test_named_foreign_skill_still_blocked(self):
        """Ради этого случая гейт и заводился — он обязан остаться."""
        with tempfile.TemporaryDirectory() as td:
            _mk_full_state(td, self.STEPS)
            r = _run_payload(self._payload(td, "jira-task-writer"))
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("не разрешает скилл", r.stderr)

    def test_named_allowed_skill_passes(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_full_state(td, self.STEPS)
            r = _run_payload(self._payload(td, "tech-design"))
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_agent_type_passes(self):
        """Оркестратор (пустой/отсутствующий тип) фазовым скилл-гейтом не рубится —
        его зона ответственности у inline-phase-guard."""
        with tempfile.TemporaryDirectory() as td:
            _mk_full_state(td, self.STEPS)
            self.assertEqual(_run_payload(self._payload(td, None)).returncode, 0)
            self.assertEqual(_run_payload(self._payload(td, "")).returncode, 0)


class TReadOnlyCommandsNotGated(unittest.TestCase):
    """Задача 011: ladder оценивал риск по ПУТИ из команды и не отличал чтение от записи.

    `cat src/main/resources/application.yml` уходил в R4 (нужен approval человека, причём и
    ВНЕ пайплайна), `grep … /auth/Foo.java` — в R3 с требованием шага 02-design."""

    READS = [
        "cat src/main/resources/application.yml",
        "grep -rn foo src/main/java/com/x/auth/Service.java",
        "cat src/main/resources/db/changelog/001.xml",
        "head -20 src/main/resources/application-prod.yml",
        "git diff --stat",
    ]

    def test_reads_pass_outside_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            for cmd in self.READS:
                self.assertEqual(_run(cmd, td).returncode, 0, f"ложный блок: {cmd}")

    def test_reads_pass_inside_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_fix_state(td, [{"id": "fix-intake", "status": "completed"},
                               {"id": "fix-diag", "status": "completed"},
                               {"id": "fix-green", "status": "pending"}])
            for cmd in self.READS:
                self.assertEqual(_run(cmd, td).returncode, 0, f"ложный блок: {cmd}")

    def test_write_to_same_path_still_classified(self):
        """Признак записи снимает read-only: редирект в прод-конфиг остаётся рисковым."""
        with tempfile.TemporaryDirectory() as td:
            r = _run("echo x > src/main/resources/application-prod.yml", td)
            self.assertEqual(r.returncode, 2, r.stderr)


class TJiraApprovalKey(unittest.TestCase):
    """Задача 011: создание Jira-задачи (R3) требовало маркер 'security-review' — чужой ключ
    без рецепта. Теперь у kind'а свой ключ, а отказ печатает команду record_approval."""

    CMD = "acli jira issue create --summary x"

    def _state(self, td: str):
        _mk_full_state(td, [{"id": "02-design", "status": "completed", "depends_on": []},
                            {"id": "03-jira", "status": "pending",
                             "depends_on": ["02-design"]}])

    def test_blocked_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as td:
            self._state(td)
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("jira-create", r.stderr)
            self.assertIn("record_approval.py", r.stderr)
            self.assertNotIn("security-review", r.stderr)

    def test_passes_with_approval(self):
        with tempfile.TemporaryDirectory() as td:
            self._state(td)
            _approval(td, "jira-create")
            self.assertEqual(_run(self.CMD, td).returncode, 0)


if __name__ == "__main__":
    unittest.main()
