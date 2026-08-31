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
                  provenance: bool = True, config_file: str = "pipeline.json") -> None:
    """Поднимает минимальный проект. `config_file` — какой ground/{pipeline|policy}.json писать:
    "pipeline.json" (legacy v1) или "policy.json" (v2, канонический). По умолчанию legacy,
    потому что большинство существующих тестов на нём."""
    (tmp / "ground").mkdir(parents=True, exist_ok=True)
    (tmp / "ground" / config_file).write_text(
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

    def test_blocks_when_build_step_only_pending(self):
        """Регресс: задача резолвилась ТОЛЬКО по статусу `in_progress`, которого на живых
        прогонах нет (update.py ведёт pending → completed) — EDD-гейт не срабатывал никогда.
        Единственный готовый build-шаг и есть активная задача."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="pending", cache=None)
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 2)

    def test_failopen_when_no_build_steps_at_all(self):
        """Задачу определить нечем (build-шагов нет) — не блокируем."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="pending", cache=None)
            sdir = tmp / "ground/statements/feature-pipeline/feat-x"
            (sdir / "manifest.json").write_text(json.dumps({
                "context": {"feature": "feat-x"},
                "steps": [{"id": "02-design", "status": "pending"}],
            }), encoding="utf-8")
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 0)

    def test_slug_from_top_level_feature_field(self):
        """Слаг брался только из `context.feature`, а `--context` у init.py опционален:
        без него путь к eval-plan.json не складывался и гейт молча пропускал запись."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress", cache=None)
            sdir = tmp / "ground/statements/feature-pipeline/feat-x"
            (sdir / "manifest.json").write_text(json.dumps({
                "feature": "feat-x",                      # context отсутствует вовсе
                "steps": [{"id": "04-build-T1", "status": "in_progress"}],
            }), encoding="utf-8")
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 2)

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

    def test_v2_policy_json_eval_enabled_false_honored(self):
        """B2 регресс: v2-only проект (только ground/policy.json, без legacy pipeline.json).
        Раньше R.pipeline_cfg(root) читал ТОЛЬКО pipeline.json → возвращал {} →
        quality.eval_enabled=False НЕ работало, eval-guard блокировал запись. Теперь читаем
        через канонический load_project_config (dual-read fallback) → eval_enabled honored."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress", cache=None,
                          eval_enabled=False, config_file="policy.json")
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 0)

    def test_v2_policy_json_eval_enabled_true_blocks(self):
        """B2 регресс (контртест): policy.json с eval_enabled=True (по дефолту)
        продолжает блокировать запись в src/main при непройденных evals."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_project(tmp, build_status="in_progress", cache=None,
                          eval_enabled=True, config_file="policy.json")
            self.assertEqual(_run({
                "tool_name": "Write", "cwd": str(tmp),
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")},
            }), 2)


class TestPrewriteEvalsOnly(unittest.TestCase):
    """Задача 010: гейт не может требовать зелёную сюиту ДО написания кода.

    build_evals_from_design заводит на задачу compile + coverage + test_pass. Фаза 04-build
    стартует сразу после RED — сюита красная by design, значит `все три passed до записи`
    невыполнимо в принципе. coverage/test_pass форсятся при закрытии шага (gate_cmd_expect),
    поэтому из pre-write гейта они убраны."""

    def _project(self, tmp: Path, cache: dict | None) -> None:
        (tmp / "ground").mkdir(parents=True, exist_ok=True)
        (tmp / "ground" / "policy.json").write_text(
            json.dumps({"quality": {"eval_enabled": True}}), encoding="utf-8")
        sdir = tmp / "ground" / "statements" / "feature-pipeline" / "feat-x"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "manifest.json").write_text(json.dumps({
            "feature": "feat-x",
            "steps": [{"id": "04-build-T1", "status": "pending"}]}), encoding="utf-8")
        docs = tmp / "docs" / "feature-pipeline" / "feat-x"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "eval-plan.json").write_text(json.dumps({"evals": [
            {"id": "compile-t1", "task_id": "T1", "type": "compile"},
            {"id": "coverage-t1", "task_id": "T1", "type": "coverage"},
            {"id": "test_pass-t1", "task_id": "T1", "type": "test_pass"},
        ]}), encoding="utf-8")
        if cache is not None:
            cache = dict(cache)
            cache.setdefault("_meta", {})["produced_by"] = "run_pending_evals"
            (sdir / "evals.json").write_text(json.dumps(cache), encoding="utf-8")

    def _write(self, tmp: Path) -> int:
        return _run({"tool_name": "Write", "cwd": str(tmp),
                     "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}})

    def test_blocks_only_on_prewrite_eval(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._project(tmp, cache=None)
            err = io.StringIO()
            old, sys.stderr = sys.stderr, err
            try:
                rc = self._write(tmp)
            finally:
                sys.stderr = old
            self.assertEqual(rc, 2)
            self.assertIn("compile-t1", err.getvalue())
            self.assertNotIn("test_pass-t1", err.getvalue())
            self.assertNotIn("coverage-t1", err.getvalue())

    def test_compile_passed_unblocks_even_with_red_suite(self):
        """Ровно состояние после RED: compile зелёный, test_pass/coverage красные."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._project(tmp, cache={"compile-t1": {"status": "passed"},
                                      "test_pass-t1": {"status": "failed"},
                                      "coverage-t1": {"status": "failed"}})
            self.assertEqual(self._write(tmp), 0)


if __name__ == "__main__":
    unittest.main()
