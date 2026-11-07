#!/usr/bin/env python3
"""Регрессии аудита 2026-11-07: обходы, подтверждённые живыми payload'ами хуков.

Каждый кейс — команда/вызов, который до починки проходил цепочку хуков (rc 0), и рядом —
штатная форма той же работы, которая обязана проходить по-прежнему.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent


def run(hook: str, tool: str, tool_input: dict, cwd: Path) -> subprocess.CompletedProcess:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
               "cwd": str(cwd), "session_id": "s-audit"}
    return subprocess.run([sys.executable, "-X", "utf8", str(HOOKS / hook)], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=str(cwd), timeout=60)


def sh(hook: str, cmd: str, cwd: Path) -> int:
    return run(hook, "run_shell_command", {"command": cmd}, cwd).returncode


class _Project(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._td.name))
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / "ground" / "statements" / "forgefix" / "f1").mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()


class StateWriteGuard(_Project):
    HOOK = "state-write-guard.py"

    def test_write_through_symlinked_dir(self):
        (self.root / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        os.symlink(self.root / "ground", self.root / "g")
        r = run(self.HOOK, "write_file", {"file_path": "g/policy.json", "content": "{}"}, self.root)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_trusted_files_guarded(self):
        for rel in ("ground/architecture-policy.json", "ground/spec-map.json",
                    "ground/statements/forgefix/f1/test-baseline.json"):
            with self.subTest(rel=rel):
                r = run(self.HOOK, "write_file", {"file_path": rel, "content": "{}"}, self.root)
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_feature_docs_not_carried_away(self):
        (self.root / "docs" / "feature-pipeline" / "F1").mkdir(parents=True)
        for cmd in ("mv docs/feature-pipeline/F1 /tmp/", "find docs/feature-pipeline -delete",
                    "python3 -c \"import shutil; shutil.rmtree('docs/feature-pipeline/F1')\""):
            with self.subTest(cmd=cmd):
                self.assertEqual(sh(self.HOOK, cmd, self.root), 2)
        self.assertEqual(sh(self.HOOK, "mv docs/feature-pipeline/F1 docs/feature-pipeline/S/fixes/F1",
                            self.root), 0, "перенос внутри feature-pipeline — штатная работа forgefix")

    def test_master_spec_only_via_merge(self):
        repo = self.root / "spec-repo"
        (repo / "svc").mkdir(parents=True)
        (repo / "svc" / "spec.md").write_text("# spec\n", encoding="utf-8")
        (self.root / "docs" / "specs" / "svc").mkdir(parents=True)
        os.symlink(repo / "svc" / "spec.md", self.root / "docs" / "specs" / "svc" / "spec.md")
        (self.root / "ground" / "spec-map.json").write_text(json.dumps({
            "schema_version": 1, "status": "confirmed", "base": str(repo),
            "capabilities": {"svc": {"spec": "svc/spec.md"}}}), encoding="utf-8")
        r = run(self.HOOK, "edit", {"file_path": "docs/specs/svc/spec.md", "old_string": "#",
                                    "new_string": "##"}, self.root)
        self.assertEqual(r.returncode, 2, r.stderr)
        for cmd in ("rm docs/specs/svc/spec.md", "ln -sf /tmp/x docs/specs/svc/spec.md"):
            self.assertEqual(sh(self.HOOK, cmd, self.root), 2, cmd)
        self.assertEqual(sh(self.HOOK, "cat docs/specs/svc/spec.md", self.root), 0)


class TddGuardShellTargets(_Project):
    def setUp(self):
        super().setUp()
        (self.root / "ground" / "pipeline.json").write_text(
            json.dumps({"quality": {"tdd": True, "tdd_integration_skip": False}}), encoding="utf-8")
        (self.root / "ground" / "statements" / "forgefix" / "f1" / "manifest.json").write_text(
            json.dumps({"steps": [{"id": "fix-red", "status": "pending"}]}), encoding="utf-8")
        (self.root / "src" / "main" / "java").mkdir(parents=True)
        (self.root / "fix.patch").write_text(
            "--- a/src/main/java/Foo.java\n+++ b/src/main/java/Foo.java\n@@ -0,0 +1 @@\n+x\n",
            encoding="utf-8")

    def test_red_cannot_be_bypassed(self):
        for cmd in ("git apply fix.patch", "patch -p1 < fix.patch",
                    "git checkout feature -- src/main/java/Foo.java",
                    "node -e \"require('fs').writeFileSync('src/main/java/Foo.java','x')\"",
                    "awk 'BEGIN{print \"x\" > \"src/main/java/Foo.java\"}'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(sh("tdd-guard.py", cmd, self.root), 2)
        self.assertEqual(sh("tdd-guard.py", "git apply --check fix.patch", self.root), 0)


class GitDiscard(_Project):
    def test_wrapped_discards_gated(self):
        (self.root / "docs").mkdir()
        for cmd in ('sh -c "git reset --hard"', "env git reset --hard", 'eval "git reset --hard"',
                    "GIT_DIR=.git git reset --hard", "echo x | xargs git reset --hard",
                    "git checkout HEAD -- docs"):
            with self.subTest(cmd=cmd):
                self.assertEqual(sh("gate-guard.py", cmd, self.root), 2)
        self.assertEqual(sh("gate-guard.py", "git checkout -- docs/x.md", self.root), 0,
                         "точечный откат файла свободен")


class DestructiveBlocker(_Project):
    def test_parent_and_history(self):
        for cmd in ("rm -rf ..", "rm -rf .git", "find .. -name '*.java' -delete", "git push origin +main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(sh("destructive-blocker.py", cmd, self.root), 2)
        for cmd in ("rm -rf build", "git push origin main", "grep -rn human_override docs"):
            with self.subTest(cmd=cmd):
                self.assertEqual(sh("destructive-blocker.py", cmd, self.root), 0)
        self.assertEqual(sh("destructive-blocker.py",
                            "python3 .gigacode/hooks/human_override.py -- config set quality.tdd false",
                            self.root), 2)


class GraphGuard(_Project):
    def setUp(self):
        super().setUp()
        (self.root / ".spec-graph").mkdir()
        (self.root / ".spec-graph" / "config.json").write_text('{"vault": "docs/graph"}', encoding="utf-8")
        (self.root / "docs" / "graph" / "services").mkdir(parents=True)
        (self.root / "docs" / "graph" / "_graph").mkdir()
        self.note = self.root / "docs" / "graph" / "services" / "a.md"
        self.note.write_text('---\nkind: service\ncalls:\n  - "[[b]]"\n---\n# A\nproza\n', encoding="utf-8")

    def test_removal_and_case(self):
        for cmd in ("rm -rf .spec-graph", "rm docs/graph/services/a.md", "mv docs/graph/services/a.md /tmp/",
                    "find docs/graph -name '*.md' -delete", "git checkout -- docs/graph"):
            with self.subTest(cmd=cmd):
                self.assertEqual(sh("graph-guard.py", cmd, self.root), 2)
        r = run("graph-guard.py", "write_file", {"file_path": "docs/graph/services/A.MD", "content": "x"},
                self.root)
        self.assertEqual(r.returncode, 2, "A.MD — та же заметка на macOS/Windows")
        r = run("graph-guard.py", "write_file", {"file_path": "docs/graph/_graph/index.json", "content": "{}"},
                self.root)
        self.assertEqual(r.returncode, 2, "файлы движка в _graph/ — любого расширения")

    def test_non_verbatim_edit_denied_prose_edit_allowed(self):
        fuzzy = {"file_path": str(self.note), "old_string": "calls:\n  - “[[b]]”",
                 "new_string": "calls:\n  - \"[[c]]\""}
        self.assertEqual(run("graph-guard.py", "edit", fuzzy, self.root).returncode, 2)
        prose = {"file_path": str(self.note), "old_string": "proza", "new_string": "proza 2"}
        self.assertEqual(run("graph-guard.py", "edit", prose, self.root).returncode, 0)


class StateRecorderUnit(_Project):
    def test_unit_report_writes_origin_without_closing(self):
        d = self.root / "ground" / "statements" / "specgraph" / "GRAPH-1"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"steps": [{"id": "graph-map", "status": "pending"}]}),
                                         encoding="utf-8")
        payload = {"hook_event_name": "SubagentStop", "cwd": str(self.root), "session_id": "s",
                   "agent_id": "a1",
                   "last_assistant_message": 'Сдал.\n{"step_id": "graph-map", "status": "unit"}'}
        subprocess.run([sys.executable, "-X", "utf8", str(HOOKS / "state-recorder.py")],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=60)
        events = (d / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"origin"', events)
        steps = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["steps"]
        self.assertEqual(steps[0]["status"], "pending", "единица не закрывает шаг")


if __name__ == "__main__":
    unittest.main()
