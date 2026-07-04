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
JR = _load("skills/pipeline-state/scripts/judges_registry.py", "ps_judges_registry")
RP = _load("skills/feature-pipeline/scripts/resolve_phases.py", "fp_resolve_phases")


class CanonicalSource(unittest.TestCase):
    """pipeline_phases — единственный источник; остальные модули используют его значения."""
    def test_modules_reuse_pp_constants(self):
        for mod in (PS, AS_FP, PV):
            self.assertEqual(mod.PREFIX_PHASE, PP.PREFIX_PHASE, f"{mod.__name__}.PREFIX_PHASE")
            self.assertEqual(mod.MAIN_PHASES, PP.MAIN_PHASES, f"{mod.__name__}.MAIN_PHASES")
        self.assertEqual(AS_FP.REQUIRED_JUDGES_MASK, PP.REQUIRED_JUDGES_MASK)

    def test_loaders_match_registry(self):
        # ЕДИНЫЙ источник — judges-registry.json. Все загрузчики обязаны совпасть с ним.
        reg = JR.step_masks()
        self.assertTrue(reg, "judges-registry.json пуст или не найден")
        self.assertEqual(PP.REQUIRED_JUDGES_MASK, reg, "pipeline_phases расходится с реестром")
        self.assertEqual(PM.REQUIRED_JUDGES_MASK, reg, "patch_manifest_judges расходится с реестром")
        # init.py больше не держит inline-копию — читает реестр через judges_registry
        init_src = _src("skills/pipeline-state/scripts/init.py")
        self.assertIn("judges_registry", init_src,
                      "init.py должен читать маску из judges_registry (единый источник)")


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

    def test_registry_has_core_judges(self):
        """Ключевые судьи присутствуют в едином реестре judges-registry.json."""
        all_judges = {j for js in JR.step_masks().values() for j in js}
        self.assertIn("design-judge", all_judges)
        self.assertIn("coverage-judge", all_judges)


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


class TestStepIdConventions(unittest.TestCase):
    """P1-6: соглашения об id шагов — единый источник pipeline_phases; копии в хуках/скриптах
    не должны разойтись (раньше '04-build-'/subagent-set были разрозненными магическими строками)."""

    def test_build_task_id_helper(self):
        self.assertEqual(PP.build_task_id("04-build-T1"), "T1")
        self.assertEqual(PP.build_task_id("04-build-KIDPPRB-8639"), "KIDPPRB-8639")
        self.assertIsNone(PP.build_task_id("04-test-T1"))
        self.assertIsNone(PP.build_task_id("04-build-"))   # пустой суффикс → None
        self.assertIsNone(PP.build_task_id(None))

    def test_requires_subagent(self):
        for sid in ("02-sdd", "02-design-x", "04-test-T1", "04-build-T1", "05-tests", "06-spec"):
            self.assertTrue(PP.requires_subagent(sid), sid)
        for sid in ("00-brd", "01-grounding", "03-jira", "07-deliver-T1", "07-report", None):
            self.assertFalse(PP.requires_subagent(sid), sid)

    def test_preflight_uses_pp_requires_subagent(self):
        """preflight больше не держит свой hardcoded-set, а зовёт pp.requires_subagent (P1-6)."""
        src = _src("skills/feature-pipeline/scripts/preflight-validate.py")
        self.assertIn("pp.requires_subagent", src)
        self.assertNotIn('"02-sdd", "02-design", "04-test", "04-build", "05-tests", "06-spec"', src)

    def test_eval_guard_build_prefix_matches_pp(self):
        """eval-guard: fallback-префикс build-шага совпадает с pp.BUILD_STEP_PREFIX, и нет
        старого .replace('04-build-', '') (P1-6)."""
        src = _src("hooks/eval-guard.py")
        self.assertIn(f'_BUILD_STEP_PREFIX = "{PP.BUILD_STEP_PREFIX}"', src)
        self.assertIn("_build_task_id", src)
        self.assertNotIn('.replace("04-build-", "")', src)

    def test_subagent_origin_set_matches_pp(self):
        """update._check_subagent_origin (бывший subagent-enforcer, перенесён с PreToolUse на
        закрытие шага): inline-fallback набора фаз совпадает с pp.SUBAGENT_PHASE_PREFIXES."""
        src = _src("skills/pipeline-state/scripts/update.py")
        self.assertEqual(PP.SUBAGENT_PHASE_PREFIXES,
                         ("02-sdd", "02-design", "04-test", "04-build", "05-tests", "06-spec",
                          "lite-design", "lite-red", "lite-green", "lite-verify"))
        for ph in PP.SUBAGENT_PHASE_PREFIXES:
            self.assertIn(f'"{ph}"', src, f"{ph} пропал из fallback update._check_subagent_origin")


class ResolvePhasesSource(unittest.TestCase):
    """M4: resolve_phases.DEFAULT_PHASES — не второй нескоординированный источник списка фаз.
    Его id обязаны быть подмножеством pipeline_phases.MAIN_PHASES и идти в каноническом порядке."""
    def test_default_phases_subset_of_main_in_order(self):
        ids = [p["id"] for p in RP.DEFAULT_PHASES]
        for pid in ids:
            self.assertIn(pid, PP.MAIN_PHASES, f"{pid} нет в pipeline_phases.MAIN_PHASES")
        canonical = [m for m in PP.MAIN_PHASES if m in ids]
        self.assertEqual(ids, canonical,
                         "порядок DEFAULT_PHASES (resolve_phases) разошёлся с MAIN_PHASES")


if __name__ == "__main__":
    unittest.main(verbosity=2)
