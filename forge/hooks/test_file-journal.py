#!/usr/bin/env python3
"""Тесты hooks/file-journal.py — безусловный журнал изменённых файлов пайплайна.

Инварианты: запись привязывается к step_id/phase активного in_progress-шага; Bash-мутации
журналируются с путями (или op:bash-opaque, если пути не извлеклись); читающие команды и
control-plane-пути не журналируются; вне пайплайна журнал не ведётся; параллельные вызовы
не рвут JSONL.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "file-journal.py"

SKILL = "feature-pipeline"
FEATURE = "feat"
STEP = "04-build-T1"


def _make_manifest(tmp: Path, status: str = "in_progress") -> None:
    d = tmp / "ground" / "statements" / SKILL / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "feature": FEATURE, "skill": SKILL,
        "steps": [{"id": STEP, "status": status}],
    }), encoding="utf-8")


def _journal(tmp: Path) -> Path:
    return tmp / "ground" / "statements" / SKILL / FEATURE / "journal" / "files.jsonl"


def _lines(tmp: Path) -> list[dict]:
    p = _journal(tmp)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _run(tmp: Path, tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
    payload = json.dumps({
        "hook_event_name": "PostToolUse", "cwd": str(tmp), "session_id": "sess1",
        "agent_type": "java-spring-dev", "agent_id": "abc12345",
        "tool_name": tool_name, "tool_input": tool_input,
    })
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


class TWriteTools(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        _make_manifest(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def test_write_records_step_and_phase(self):
        r = _run(self.tmp, "Write", {"file_path": "src/main/java/com/x/Foo.java"})
        self.assertEqual(r.returncode, 0, r.stderr)
        recs = _lines(self.tmp)
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["op"], "write")
        self.assertEqual(rec["paths"], ["src/main/java/com/x/Foo.java"])
        self.assertEqual(rec["step_id"], STEP)
        self.assertEqual(rec["phase"], "04-tdd")
        self.assertEqual(rec["agent"], "java-spring-dev-abc12345")

    def test_absolute_path_normalized_to_relative(self):
        r = _run(self.tmp, "Edit", {"file_path": str(self.tmp / "src" / "A.java")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_lines(self.tmp)[0]["paths"], ["src/A.java"])

    def test_control_plane_paths_not_journaled(self):
        for p in ("ground/phases/feat/gate.json", ".gigacode/hooks/x.py", ".git/config"):
            _run(self.tmp, "Write", {"file_path": p})
        self.assertEqual(_lines(self.tmp), [])

    def test_no_in_progress_step_still_journals(self):
        # шаг не in_progress (между шагами) — запись всё равно пишется, step_id=None:
        # скоуп отката работает по ts относительно чекпойнта
        _make_manifest(self.tmp, status="completed")
        _run(self.tmp, "Write", {"file_path": "src/B.java"})
        rec = _lines(self.tmp)[0]
        self.assertIsNone(rec["step_id"])


class TBashHeuristics(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        _make_manifest(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def _bash(self, cmd: str):
        return _run(self.tmp, "Bash", {"command": cmd})

    def test_redirect_extracts_path(self):
        self._bash("echo x > src/main/resources/app.yml")
        rec = _lines(self.tmp)[0]
        self.assertEqual(rec["op"], "bash-mutation")
        self.assertEqual(rec["paths"], ["src/main/resources/app.yml"])

    def test_sed_i_extracts_file(self):
        self._bash("sed -i 's/a/b/' src/main/java/com/x/Foo.java")
        rec = _lines(self.tmp)[0]
        self.assertEqual(rec["op"], "bash-mutation")
        self.assertIn("src/main/java/com/x/Foo.java", rec["paths"])

    def test_mv_records_both_sides(self):
        self._bash("mv src/A.java src/B.java")
        rec = _lines(self.tmp)[0]
        self.assertEqual(sorted(rec["paths"]), ["src/A.java", "src/B.java"])

    def test_pure_read_not_journaled(self):
        self._bash("echo hi")
        self._bash("cat src/A.java")
        self._bash("ls -la src/")
        self.assertEqual(_lines(self.tmp), [])

    def test_opaque_python_write(self):
        self._bash("python3 -c \"open('somewhere.txt','w').write('1')\"")
        rec = _lines(self.tmp)[0]
        self.assertEqual(rec["op"], "bash-opaque")
        self.assertIn("open(", rec["command"])

    def test_git_checkout_paths(self):
        self._bash("git checkout -- src/main/java/com/x/Foo.java")
        rec = _lines(self.tmp)[0]
        self.assertEqual(rec["op"], "bash-mutation")
        self.assertEqual(rec["paths"], ["src/main/java/com/x/Foo.java"])

    def test_redirect_into_ground_not_journaled(self):
        # control-plane мутации не журналируем (их сторожит state-write-guard,
        # а откат ground/ делает манифест-хирургия, не git-restore)
        self._bash("echo x > ground/notes.md")
        recs = _lines(self.tmp)
        # путь под ground/ отфильтрован → пути пустые → запись становится bash-opaque
        self.assertTrue(recs == [] or recs[0]["op"] == "bash-opaque")


class TOutsidePipeline(unittest.TestCase):
    def test_no_manifest_no_journal(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            r = _run(tmp, "Write", {"file_path": "src/A.java"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((tmp / "ground").exists())


class TConcurrency(unittest.TestCase):
    def test_parallel_appends_keep_jsonl_valid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            _make_manifest(tmp)
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = [ex.submit(_run, tmp, "Write", {"file_path": f"src/F{i}.java"})
                        for i in range(16)]
                for f in futs:
                    self.assertEqual(f.result().returncode, 0)
            recs = _lines(tmp)  # json.loads упадёт, если строки порваны
            self.assertEqual(len(recs), 16)
            self.assertEqual({r["paths"][0] for r in recs},
                             {f"src/F{i}.java" for i in range(16)})


class TContract(unittest.TestCase):
    def test_failopen_empty_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
