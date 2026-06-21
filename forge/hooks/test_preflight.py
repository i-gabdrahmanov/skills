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

ESSENTIAL = ["gate-guard.py", "phase-gate.py", "state-recorder.py", "eval-guard.py", "log-agent.py"]


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


class TestPreflight(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
