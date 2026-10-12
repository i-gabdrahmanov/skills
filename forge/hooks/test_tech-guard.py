#!/usr/bin/env python3
"""Тесты hooks/tech-guard.py — скоуп-гейт tech-ветки (forgetech).

Каждое условие пропуска записи в файл проекта проверяется отдельно: шаг tech-apply, согласие
tech-plan-<F> с привязкой к ТЕКУЩЕМУ плану (sha256), снимок «до» с evidence и целым файлом,
путь в targets. Плюс то, что гейт НЕ трогает: чужие ветки, docs/, выходные каталоги сборки,
пути вне проекта.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
HOOK = HOOKS / "tech-guard.py"
TECHLIB = HOOKS.parent / "skills" / "forgetech" / "scripts"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(TECHLIB))
import forge_events as FE  # noqa: E402
import _techlib as T  # noqa: E402

FEATURE = "KEY-1"
TARGET = "src/main/resources/application-dev.yml"
PLAN = {"feature_slug": FEATURE, "archetype": "config-change", "targets": [TARGET],
        "expected_changes": [{"kind": "config", "key": "clients.x.url", "profile": "dev",
                              "from": "https://old", "to": "https://new"}],
        "invariants": ["tests", "api", "config-keys"], "outside_repo": "нет",
        "rollback": "git revert"}


def _git(root: Path, *args) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class TechGuard(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        _git(self.root, "init", "-q")
        res = self.root / "src" / "main" / "resources"
        res.mkdir(parents=True)
        (res / "application-dev.yml").write_text("clients:\n  x:\n    url: https://old\n",
                                                encoding="utf-8")
        (res / "application.yml").write_text("server:\n  port: 8080\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("ground/\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.task = T.task_dir(self.root, FEATURE)
        self.task.mkdir(parents=True)
        self.plan_file = self.task / T.PLAN_FILE
        self.plan_file.write_text(json.dumps(PLAN), encoding="utf-8")
        self.set_step("tech-apply")

    def tearDown(self):
        self._td.cleanup()

    def set_step(self, current: str, skill: str = "forgetech") -> None:
        ids = ["tech-intake", "tech-diag", "tech-apply", "tech-verify", "tech-record"]
        if skill != "forgetech":
            ids = ["fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"]
        steps, done = [], True
        prev = None
        for sid in ids:
            if sid == current:
                done = False
            steps.append({"id": sid, "status": "completed" if done else "pending",
                          "depends_on": [prev] if prev else []})
            prev = sid
        d = self.root / "ground" / "statements" / skill / FEATURE
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"feature": FEATURE, "steps": steps}),
                                         encoding="utf-8")

    def approve(self, bind: bool = True) -> None:
        extra = {"bound_sha256": T.sha256_file(self.plan_file)} if bind else {}
        FE.append_approval(self.root, f"tech-plan-{FEATURE}", approved_by="user", reason="ok", **extra)

    def snapshot(self) -> None:
        r = subprocess.run([sys.executable, str(TECHLIB / "check_invariants.py"), "snapshot",
                            "--project", str(self.root), "--feature", FEATURE],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_hook(self, tool: str, tool_input: dict) -> subprocess.CompletedProcess:
        payload = {"hook_event_name": "PreToolUse", "cwd": str(self.root), "tool_name": tool,
                   "tool_input": tool_input}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)

    def write(self, rel: str) -> subprocess.CompletedProcess:
        return self.run_hook("write_file", {"file_path": str(self.root / rel), "content": "x"})

    # ── пропуск ────────────────────────────────────────────────────────────────
    def test_all_conditions_met_allows_target(self):
        self.approve()
        self.snapshot()
        r = self.write(TARGET)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_shell_write_to_target_judged_like_write(self):
        self.approve()
        self.snapshot()
        ok = self.run_hook("run_shell_command", {"command": f"echo x > {TARGET}"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = self.run_hook("run_shell_command",
                            {"command": "echo x > src/main/resources/application.yml"})
        self.assertEqual(bad.returncode, 2)

    def test_not_gated_paths(self):
        for rel in ("docs/feature-pipeline/tech/KEY-1/tech-sdd.md", "target/classes/x.txt",
                    "build/tmp/x.log"):
            with self.subTest(rel=rel):
                self.assertEqual(self.write(rel).returncode, 0)
        r = self.run_hook("write_file", {"file_path": "/tmp/forge-tech-guard-x.txt", "content": "x"})
        self.assertEqual(r.returncode, 0)

    def test_other_branch_is_noop(self):
        import shutil
        shutil.rmtree(self.root / "ground" / "statements" / "forgetech")
        self.set_step("fix-green", skill="forgefix")
        self.assertEqual(self.write("pom.xml").returncode, 0)

    # ── блок ───────────────────────────────────────────────────────────────────
    def test_wrong_step_blocks(self):
        self.set_step("tech-diag")
        r = self.write(TARGET)
        self.assertEqual(r.returncode, 2)
        self.assertIn("tech-diag", r.stderr)

    def test_no_approval_blocks(self):
        r = self.write(TARGET)
        self.assertEqual(r.returncode, 2)
        self.assertIn("нет согласия", r.stderr)

    def test_unbound_approval_blocks(self):
        self.approve(bind=False)
        r = self.write(TARGET)
        self.assertEqual(r.returncode, 2)
        self.assertIn("без привязки", r.stderr)

    def test_plan_changed_after_approval_blocks(self):
        self.approve()
        plan = dict(PLAN, targets=[TARGET, "src/main/resources/application.yml"])
        self.plan_file.write_text(json.dumps(plan), encoding="utf-8")
        r = self.write("src/main/resources/application.yml")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ПОСЛЕ согласия", r.stderr)

    def test_no_snapshot_blocks(self):
        self.approve()
        r = self.write(TARGET)
        self.assertEqual(r.returncode, 2)
        self.assertIn("снимка", r.stderr)

    def test_out_of_scope_blocks_including_tests(self):
        self.approve()
        self.snapshot()
        for rel in ("src/main/resources/application.yml", "src/test/java/XTest.java", "pom.xml"):
            with self.subTest(rel=rel):
                r = self.write(rel)
                self.assertEqual(r.returncode, 2)
                self.assertIn("needs_scope", r.stderr)


if __name__ == "__main__":
    unittest.main()
