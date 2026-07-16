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


class TDocReview(unittest.TestCase):
    """Пин: доставка дока (brd|sdd) на ветку задачи docs/<slug> (doc_review_push.py) —
    R4-класс, deny-first. Без approval-маркера <doc>-review-<slug> (провенанс record_approval)
    скрипт не запускается; classify дал бы команде default-R1 — без deny-first прошёл бы авто.
    Легаси sdd_review_push.py (остаётся в старых деплоях) — под тем же гейтом."""

    CMD = ("python3 .gigacode/skills/feature-pipeline/scripts/doc_review_push.py "
           "--doc sdd --feature f1 --jira-key STOR-1 --json")

    @staticmethod
    def _marker(td: str, key: str, payload: dict):
        appr = Path(td) / "ground" / "approvals"
        appr.mkdir(parents=True, exist_ok=True)
        (appr / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_without_approval_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("sdd-review-f1.json", r.stderr)

    def test_with_valid_approval_passes(self):
        with tempfile.TemporaryDirectory() as td:
            self._marker(td, "sdd-review-f1",
                         {"produced_by": "record_approval", "approved_by": "user",
                          "reason": "ok"})
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_brd_doc_uses_own_marker(self):
        cmd = ("python3 .gigacode/skills/feature-pipeline/scripts/doc_review_push.py "
               "--doc brd --feature f1 --json")
        with tempfile.TemporaryDirectory() as td:
            # sdd-маркер НЕ снимает гейт brd-доставки
            self._marker(td, "sdd-review-f1",
                         {"produced_by": "record_approval", "approved_by": "user",
                          "reason": "ok"})
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 2, "маркер другого дока не должен снимать гейт")
            self.assertIn("brd-review-f1.json", r.stderr)
            self._marker(td, "brd-review-f1",
                         {"produced_by": "record_approval", "approved_by": "user",
                          "reason": "ok"})
            r = _run(cmd, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_legacy_sdd_review_push_still_gated(self):
        # старый скрипт мог остаться в .gigacode (деплой не удаляет файлы) — гейт держит и его
        legacy = ("python3 .gigacode/skills/feature-pipeline/scripts/sdd_review_push.py "
                  "--feature f1 --json")
        with tempfile.TemporaryDirectory() as td:
            r = _run(legacy, td)
            self.assertEqual(r.returncode, 2, "легаси-скрипт обязан остаться под гейтом")
            self.assertIn("sdd-review-f1.json", r.stderr)
            self._marker(td, "sdd-review-f1",
                         {"produced_by": "record_approval", "approved_by": "user",
                          "reason": "ok"})
            r = _run(legacy, td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_handwritten_marker_without_provenance_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._marker(td, "sdd-review-f1", {"approved_by": "user", "reason": "ok"})
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "рукописный маркер без провенанса не должен снимать гейт")
            self.assertIn("провенанс", r.stderr.lower())

    def test_foreign_feature_marker_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as td:
            self._marker(td, "sdd-review-other",
                         {"produced_by": "record_approval", "approved_by": "user",
                          "reason": "ok"})
            r = _run(self.CMD, td)
            self.assertEqual(r.returncode, 2, "маркер чужой фичи не должен снимать этот гейт")

    def test_status_is_free(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run("python3 .gigacode/skills/feature-pipeline/scripts/doc_review_push.py "
                     "--doc sdd --feature f1 --status", td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_status_inside_arg_value_is_not_readonly(self):
        # --status ВНУТРИ кавычённого значения другого аргумента не должен считаться ридонли
        with tempfile.TemporaryDirectory() as td:
            r = _run("python3 .gigacode/skills/feature-pipeline/scripts/doc_review_push.py "
                     "--doc sdd --feature f1 --jira-key \"X --status\"", td)
            self.assertEqual(r.returncode, 2,
                             "--status в тексте значения не снимает approval-гейт")

    def test_without_feature_or_doc_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            # без --feature ключ не резолвится → блок, даже с маркерами
            r = _run("python3 .gigacode/skills/feature-pipeline/scripts/doc_review_push.py "
                     "--doc sdd --json", td)
            self.assertEqual(r.returncode, 2, "без --feature ключ маркера не резолвится → блок")
            # без --doc (для нового скрипта) ключ тоже не резолвится → блок
            self._marker(td, "sdd-review-f1",
                         {"produced_by": "record_approval", "approved_by": "user",
                          "reason": "ok"})
            r = _run("python3 .gigacode/skills/feature-pipeline/scripts/doc_review_push.py "
                     "--feature f1 --json", td)
            self.assertEqual(r.returncode, 2, "без --doc ключ маркера не резолвится → блок")


def _sh(cwd: str, *args: str):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                          timeout=30)


class TBranchProtection(unittest.TestCase):
    """Пин: интеграционная ветка фичи feature/<slug> собирается ТОЛЬКО мерджем PR сабветок
    задач — прямые commit/merge на ней и любой push в неё gate-guard блокирует deny-first.
    Скоуп — активная фича feature-pipeline (forgelite коммитит в свою feature/<KEY> легально)."""

    SLUG = "f1"
    PROT = "feature/f1"

    @classmethod
    def _mk(cls, td: str, skill: str = "feature-pipeline", branch: str | None = None):
        _sh(td, "init", "-q", "-b", "main")
        _sh(td, "config", "user.email", "t@t")
        _sh(td, "config", "user.name", "t")
        (Path(td) / "seed.txt").write_text("seed", encoding="utf-8")
        _sh(td, "add", "seed.txt")
        _sh(td, "commit", "-qm", "seed")
        if branch:
            _sh(td, "checkout", "-qb", branch)
        d = Path(td) / "ground" / "statements" / skill / cls.SLUG
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"steps": [{"id": "07-deliver-T1", "status": "in_progress"}]}),
            encoding="utf-8")
        # критичность/порог, чтобы легальный commit (R2) проходил авто и allow-кейсы
        # проверяли именно branch-protection, а не гейт критичности
        (Path(td) / "ground" / "pipeline.json").write_text(json.dumps(
            {"autonomy": {"criticality": "medium", "auto_max_risk": "R2"}}), encoding="utf-8")

    def test_commit_on_feature_branch_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch=self.PROT)
            r = _run("git commit -m 'x'", td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(self.PROT, r.stderr)
            self.assertIn("запрещён", r.stderr)

    def test_merge_and_dash_c_variants_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch=self.PROT)
            r = _run("git merge feature/STOR-201", td)
            self.assertEqual(r.returncode, 2, "merge в интеграционную ветку — блок")
            r = _run(f"git -C {td} commit -m x", td)
            self.assertEqual(r.returncode, 2, "git -C не обходит защиту ветки")

    def test_commit_on_subtask_branch_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch="feature/f1-T1")
            r = _run("git commit -m 'STOR-201 x'", td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_detached_head_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch=self.PROT)
            _sh(td, "checkout", "-q", "--detach")
            r = _run("git commit -m x", td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_push_forms_into_feature_branch_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch="feature/f1-T1")  # НЕ на защищённой ветке
            for cmd in (f"git push origin {self.PROT}",
                        f"git push -u origin HEAD:{self.PROT}",
                        f"git push origin main:refs/heads/{self.PROT}",
                        f"git push origin :{self.PROT}",
                        f"git push origin +{self.PROT}",
                        "git push --all origin"):
                r = _run(cmd, td)
                self.assertEqual(r.returncode, 2, f"{cmd}: должен блокироваться")
                self.assertIn(self.PROT, r.stderr, cmd)

    def test_bare_push_from_feature_branch_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch=self.PROT)
            r = _run("git push", td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(self.PROT, r.stderr)

    def test_push_of_subtask_branch_not_branch_protected(self):
        # push сабветки может блокироваться ЛЕСТНИЦЕЙ (R4: human-approval/evidence),
        # но НЕ защитой интеграционной ветки — проверяем текст deny
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch="feature/f1-T1")
            r = _run("git push origin feature/f1-T1", td)
            self.assertNotIn("ветку фичи", r.stderr)

    def test_forgelite_feature_branch_not_protected(self):
        # в forgelite feature/<KEY> — сама ветка задачи: прямые коммиты легальны
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, skill="forgelite", branch=self.PROT)
            r = _run("git commit -m 'JIRA-1 x'", td)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_story_branch_script_not_matched(self):
        # санкционированный story_branch_push.py не матчится git-детектом
        with tempfile.TemporaryDirectory() as td:
            self._mk(td, branch="feature/f1-T1")
            r = _run("python3 .gigacode/skills/feature-pipeline/scripts/story_branch_push.py "
                     f"--feature {self.SLUG} --json", td)
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


if __name__ == "__main__":
    unittest.main()
