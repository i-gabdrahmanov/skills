#!/usr/bin/env python3
"""Регрессионные guard-тесты на согласованность машины состояний feature-pipeline.

Ловят дрейф между копиями констант, который раньше давал жёсткие блокировки шагов:
  - P0-1: имена в REQUIRED_JUDGES_MASK должны совпадать с вердиктами run_judge.py.
  - P0-2: префикс "06-" должен маппиться в "06-document" во всех модулях.
  - P2-6: ключ "02-eval-plan" должен быть в PREFIX_PHASE везде.
  - P2-7: PREFIX_PHASE / MAIN_PHASES / REQUIRED_JUDGES_MASK не должны расходиться.

Требует Python 3.10+ (скрипты используют PEP 604 в сигнатурах).
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _load(rel: str, name: str):
    p = REPO / rel
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(p.parent))
    spec.loader.exec_module(m)
    return m


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


PP = _load("skills/feature-pipeline/scripts/pipeline_phases.py", "pipeline_phases")
PS = _load("skills/pipeline-state/scripts/phase_sync.py", "ps_phase_sync")
PM = _load("skills/pipeline-state/scripts/patch_manifest_judges.py", "ps_patch")
AS_FP = _load("skills/feature-pipeline/scripts/add_steps.py", "fp_add_steps")
PV = _load("skills/feature-pipeline/scripts/preflight-validate.py", "fp_preflight")
RJ = _load("skills/feature-pipeline/scripts/run_judge.py", "fp_run_judge")


class CanonicalSource(unittest.TestCase):
    """pipeline_phases — единственный источник; остальные модули используют его значения."""
    def test_modules_reuse_pp_constants(self):
        for mod in (PS, AS_FP, PV):
            self.assertEqual(mod.PREFIX_PHASE, PP.PREFIX_PHASE, f"{mod.__name__}.PREFIX_PHASE")
            self.assertEqual(mod.MAIN_PHASES, PP.MAIN_PHASES, f"{mod.__name__}.MAIN_PHASES")
        self.assertEqual(AS_FP.REQUIRED_JUDGES_MASK, PP.REQUIRED_JUDGES_MASK)

    def test_inline_copies_match_pp(self):
        # patch_manifest_judges держит свою копию маски — должна совпадать с канон.
        self.assertEqual(PM.REQUIRED_JUDGES_MASK, PP.REQUIRED_JUDGES_MASK)
        # init.py и state-recorder держат inline-копии — сверяем по исходнику
        init_src = _src("skills/pipeline-state/scripts/init.py")
        for judges in PP.REQUIRED_JUDGES_MASK.values():
            for j in judges:
                self.assertIn(f'"{j}"', init_src, f"init.py mask должен содержать {j}")


class TestJudgeNames(unittest.TestCase):
    def test_mask_names_are_produced_by_run_judge(self):
        """Каждое имя судьи из маски = <phase>-judge для существующей фазы run_judge."""
        producible = {f"{phase}-judge" for phase in RJ.PHASE_MAP}
        for mask in (AS_FP.REQUIRED_JUDGES_MASK, PM.REQUIRED_JUDGES_MASK):
            for step, judges in mask.items():
                for j in judges:
                    self.assertIn(
                        j, producible,
                        f"judge '{j}' (шаг {step}) не производится run_judge.py "
                        f"(есть только {sorted(producible)}). Это вернёт P0-1.")

    def test_no_legacy_judge_names(self):
        """Старые имена, которые никто не пишет, не должны вернуться ни в одну копию."""
        legacy = ("taskplan-check", "sdd-check", "coverage-check")
        for rel in ("skills/pipeline-state/scripts/init.py",
                    "skills/pipeline-state/scripts/patch_manifest_judges.py",
                    "skills/feature-pipeline/scripts/add_steps.py"):
            src = _src(rel)
            for name in legacy:
                self.assertNotIn(
                    f'"{name}"', src,
                    f"{rel} всё ещё содержит мёртвое имя судьи '{name}' (P0-1).")

    def test_init_uses_correct_names(self):
        """init.py определяет маску внутри main() — проверяем по исходнику."""
        src = _src("skills/pipeline-state/scripts/init.py")
        self.assertIn('"design-judge"', src)
        self.assertIn('"coverage-judge"', src)


class TestMaskConsistency(unittest.TestCase):
    def test_masks_identical(self):
        self.assertEqual(
            AS_FP.REQUIRED_JUDGES_MASK, PM.REQUIRED_JUDGES_MASK,
            "REQUIRED_JUDGES_MASK разошлась между add_steps и patch_manifest_judges (P2-7).")


class TestPrefixPhase(unittest.TestCase):
    def test_06_maps_to_document_everywhere(self):
        for mod in (PS, AS_FP, PV):
            self.assertEqual(mod.PREFIX_PHASE.get("06-"), "06-document",
                             f"{mod.__name__}: '06-' должен маппиться в '06-document' (P0-2).")
        # state-recorder держит PREFIX_PHASE inline — проверяем исходник
        sr = _src("hooks/state-recorder.py")
        self.assertIn('"06-": "06-document"', sr, "state-recorder: '06-doc' не исправлен (P0-2).")
        self.assertNotIn('"06-doc"', sr)

    def test_eval_plan_key_present(self):
        for mod in (PS, AS_FP, PV):
            self.assertEqual(mod.PREFIX_PHASE.get("02-eval-plan"), "02-eval-plan",
                             f"{mod.__name__}: нет ключа '02-eval-plan' в PREFIX_PHASE (P2-6).")


class TestMainPhases(unittest.TestCase):
    def test_main_phases_identical(self):
        self.assertEqual(PS.MAIN_PHASES, AS_FP.MAIN_PHASES)
        self.assertEqual(PS.MAIN_PHASES, PV.MAIN_PHASES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
