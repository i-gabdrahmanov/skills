#!/usr/bin/env python3
"""Tests for hooks/eval-guard.py (read-only Eval-Driven Development gate).

eval-guard читает кэш evals.json (его пишет run_pending_evals.py) и блокирует запись в
src/main, если для активной задачи есть eval'ы без статуса passed. Сам eval'ы НЕ гоняет.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("eval_guard", HOOKS / "eval-guard.py")
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(HOOKS))
spec.loader.exec_module(mod)


def _run(payload: dict) -> int:
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        return mod.main()
    finally:
        sys.stdin = old


def _make_project(tmp: Path, *, build_status: str, cache: dict | None,
                  eval_enabled: bool = True, slug: str = "feat-x",
                  provenance: bool = True) -> None:
    (tmp / "ground").mkdir(parents=True, exist_ok=True)
    (tmp / "ground" / "pipeline.json").write_text(
        json.dumps({"quality": {"eval_enabled": eval_enabled}}), encoding="utf-8")

    sdir = tmp / "ground" / "statements" / "feature-pipeline" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "manifest.json").write_text(json.dumps({
        "context": {"feature": slug},
        "steps": [{"id": "04-build-T1", "status": build_status}],
    }), encoding="utf-8")

    docs = tmp / "docs" / "feature-pipeline" / slug
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "eval-plan.json").write_text(json.dumps({
        "evals": [{"id": "e1", "task_id": "T1", "type": "compile"}],
    }), encoding="utf-8")

    if cache is not None:
        cache = dict(cache)
        # Легитимный кэш несёт провенанс run_pending_evals; provenance=False моделирует подделку.
        if provenance:
            cache.setdefault("_meta", {})["produced_by"] = "run_pending_evals"
        (sdir / "evals.json").write_text(json.dumps(cache), encoding="utf-8")


class TestEvalGuard(unittest.TestCase):
    def test_main_exists(self):
        self.assertTrue(hasattr(mod, "main"))

    def test_empty_stdin_failopen(self):
        self.assertEqual(_run({}), 0)

    def test_not_src_main_passes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress", cache=None)
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/test/java/XTest.java")},
            }), 0)

    def test_block_when_cache_missing(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress", cache=None)
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 2)

    def test_block_when_eval_failed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress",
                          cache={"e1": {"status": "failed"}})
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 2)

    def test_allow_when_eval_passed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress",
                          cache={"e1": {"status": "passed"}})
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 0)

    def test_failopen_when_eval_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress", cache=None, eval_enabled=False)
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 0)

    def test_passes_when_no_active_build_step(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="pending", cache=None)
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 0)

    def test_block_forged_cache_without_provenance(self):
        # подделка: все passed, но без _meta.produced_by — eval-guard не засчитывает → блок
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress",
                          cache={"e1": {"status": "passed"}}, provenance=False)
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 2)

    def test_block_relative_src_main_path(self):
        # рантайм Qwen может отдать относительный file_path — раньше `/src/main/` его не ловил
        # и EDD молча fail-open'ил. Теперь `(?:^|/)src/main/` ловит и относительный.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress",
                          cache={"e1": {"status": "failed"}})
            self.assertEqual(_run({
                "tool_name": "write_file", "cwd": str(tmp),
                "tool_input": {"file_path": "src/main/java/X.java"},
            }), 2)


if __name__ == "__main__":
    unittest.main()
