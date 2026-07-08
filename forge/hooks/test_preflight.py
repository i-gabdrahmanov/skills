#!/usr/bin/env python3
"""Tests for hooks/preflight.py — wiring-aware готовность харнеса.

Главное: preflight обязан падать, если essential-хук НЕ подключён в settings.json
(а не просто лежит файлом), и если risk-policy.json отсутствует/битый.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("preflight", HOOKS / "preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

ESSENTIAL = ["gate-guard.py", "phase-gate.py", "state-recorder.py", "eval-guard.py",
             "state-write-guard.py", "log-agent.py"]


def _hooks_block(root: Path, names: list[str]) -> dict:
    cmds = [{"type": "command",
             "command": f"python3 {root}/.gigacode/hooks/{n}", "name": n}
            for n in names]
    return {"PreToolUse": [{"matcher": "*", "hooks": cmds}]}


def _make(tmp: Path, *, wired: list[str], policy_ok: bool = True) -> None:
    (tmp / "ground").mkdir(parents=True, exist_ok=True)
    (tmp / "ground" / "pipeline.json").write_text(json.dumps({"quality": {}}), encoding="utf-8")

    gh = tmp / ".gigacode" / "hooks"
    gh.mkdir(parents=True, exist_ok=True)
    for n in ESSENTIAL:                       # все файлы есть на диске
        (gh / n).write_text("# stub\n", encoding="utf-8")

    block = _hooks_block(tmp, wired)
    (gh / "settings.hooks.json").write_text(json.dumps({"hooks": block}), encoding="utf-8")
    (tmp / ".gigacode" / "settings.json").write_text(
        json.dumps({"hooks": block, "disableAllHooks": False}), encoding="utf-8")

    rp = gh / "risk-policy.json"
    rp.write_text('{"version":1}' if policy_ok else "{ broken json", encoding="utf-8")


def _fake_doctor(tmp: Path, problems: list[str]) -> None:
    """Стаб doctor.py, возвращающий заданные problems (exit 1)."""
    d = tmp / ".gigacode" / "skills" / "feature-pipeline" / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"passed": not problems, "problems": problems, "checks": []})
    (d / "doctor.py").write_text(
        f"import sys\nprint({payload!r})\nsys.exit({1 if problems else 0})\n", encoding="utf-8")


class TestPreflight(unittest.TestCase):
    def test_broken_registry_paths_is_error(self):
        # Битые межскилловые пути (skill-paths.json) должны ВАЛИТЬ preflight, не warn
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            _fake_doctor(tmp, ["registry-paths-exist: битые пути: ['minor-defect-fix/...']"])
            res = preflight.preflight(str(tmp))
            self.assertFalse(res["passed"])
            self.assertTrue(any("registry-paths-exist" in e for e in res["errors"]), res)

    def test_other_doctor_problems_stay_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            _fake_doctor(tmp, ["evals-declared: у скилла нет evals"])
            res = preflight.preflight(str(tmp))
            self.assertTrue(res["passed"], res.get("errors"))
            self.assertTrue(any("evals-declared" in w for w in res.get("warnings", [])), res)

    def test_all_wired_passes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            res = preflight.preflight(str(tmp))
            self.assertTrue(res["passed"], res.get("errors"))

    def test_eval_guard_not_wired_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=[n for n in ESSENTIAL if n != "eval-guard.py"])
            res = preflight.preflight(str(tmp))
            self.assertFalse(res["passed"])
            self.assertTrue(any("eval-guard.py" in e for e in res["errors"]), res["errors"])

    def test_corrupt_risk_policy_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL, policy_ok=False)
            res = preflight.preflight(str(tmp))
            self.assertFalse(res["passed"])
            self.assertTrue(any("risk-policy" in e for e in res["errors"]), res["errors"])

    def test_missing_risk_policy_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / ".gigacode" / "hooks" / "risk-policy.json").unlink()
            res = preflight.preflight(str(tmp))
            self.assertFalse(res["passed"])
            self.assertTrue(any("risk-policy" in e for e in res["errors"]), res["errors"])


class TestMatcherCanonical(unittest.TestCase):
    """BLOCKER-0 на уровне preflight: матчеры блок-цепочек обязаны матчить канон-имена рантайма."""

    @staticmethod
    def _block(bash_m: str, write_m: str) -> dict:
        return {"PreToolUse": [
            {"matcher": bash_m, "hooks": [{"command": "python3 x/destructive-blocker.py"}]},
            {"matcher": write_m, "hooks": [{"command": "python3 x/tdd-guard.py"}]},
        ]}

    def test_claude_notation_matchers_flagged(self):
        errs = preflight._check_matchers_canonical(
            self._block("^Bash$", "(Write|Edit|WriteFile|NotebookEdit)"), "settings.json")
        self.assertEqual(len(errs), 2, errs)
        self.assertTrue(any("run_shell_command" in e for e in errs))

    def test_canonical_matchers_ok(self):
        errs = preflight._check_matchers_canonical(
            self._block("^(run_shell_command|Bash)$",
                        "^(write_file|edit|notebook_edit|Write|Edit|WriteFile|NotebookEdit)$"),
            "settings.json")
        self.assertEqual(errs, [])


class TestFindForeignHookPaths(unittest.TestCase):
    """Регрессия: на Windows project_root — обратные слэши (Path(...).resolve()), хвост из
    шаблона settings.hooks.json — прямые; итоговый путь в command смешанный. Раньше
    expected_prefix строился через os.path.join() (чисто обратные слэши на Windows) и
    никогда не совпадал с реальным смешанным форматом — свои хуки считались "чужими"."""

    def test_windows_mixed_separator_own_path_not_foreign(self):
        project_root = r"C:\Work\JavaProjects\pprb-kid"
        cmd = (r'"C:\Program Files\Python313\python.exe" -X utf8 '
               rf"{project_root}/.gigacode/hooks/destructive-blocker.py")
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
        found = preflight._find_foreign_hook_paths(settings, project_root)
        self.assertEqual(found, [], found)

    def test_windows_foreign_path_still_flagged(self):
        project_root = r"C:\Work\JavaProjects\pprb-kid"
        cmd = (r'"C:\Program Files\Python313\python.exe" -X utf8 '
               r"C:\Other\proj/.gigacode/hooks/destructive-blocker.py")
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
        found = preflight._find_foreign_hook_paths(settings, project_root)
        self.assertEqual(len(found), 1, found)

    def test_posix_own_path_not_foreign(self):
        project_root = "/home/user/proj"
        cmd = "/usr/bin/python3 -X utf8 /home/user/proj/.gigacode/hooks/gate-guard.py"
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
        found = preflight._find_foreign_hook_paths(settings, project_root)
        self.assertEqual(found, [], found)


if __name__ == "__main__":
    unittest.main()
