#!/usr/bin/env python3
"""test_branches_registry.py — реестр плоских веток (hooks/_branches.py) и его потребители.

Плоские ветки (fix, tech) раньше жили кортежами в ~15 местах, и любой пропущенный — тихо
снятый гейт. Теперь источник один, а этот тест пинит две стороны:
  • реестр согласован сам с собой и со скиллом ветки (шаги = references/manifest-steps.json,
    субагентные/гейтовые шаги — подмножество шагов, роли — известные SoD-роли);
  • КАЖДЫЙ потребитель видит КАЖДУЮ ветку: update.py (субагент/гейт/артефакты), tdd-guard (RED),
    inline-phase-guard (артефакты фаз), sod-enforcer (роли), risk_ladder (design-алиас R2),
    archive.py (финальный шаг), resolve_phases (mode ↔ skill), params-registry (inputs.mode),
    skill_paths (docs-таргет), risk-policy (gate_cmd_expect на каждый гейтовый шаг).
Новая ветка, не доведённая до всех потребителей, падает здесь, а не на боевом прогоне.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
REPO = HOOKS.parent
SKILLS = REPO / "skills"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(SKILLS / "feature-pipeline" / "scripts"))
sys.path.insert(0, str(SKILLS / "pipeline-state" / "scripts"))

import _branches as BR  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RegistrySelfConsistency(unittest.TestCase):
    def test_steps_match_skill_manifest(self):
        for skill, b in BR.BRANCHES.items():
            with self.subTest(skill=skill):
                steps = json.loads((SKILLS / skill / "references" / "manifest-steps.json")
                                   .read_text(encoding="utf-8"))
                self.assertEqual(tuple(s["id"] for s in steps), b["steps"])

    def test_subsets_and_prefixes(self):
        roles = {"design", "dev", "test", "spec", "jira"}
        for skill, b in BR.BRANCHES.items():
            with self.subTest(skill=skill):
                steps = set(b["steps"])
                for field in ("subagent_steps", "gate_steps", "build_steps"):
                    self.assertLessEqual(set(b[field]), steps, field)
                self.assertTrue(all(s.startswith(b["prefix"]) for s in steps))
                self.assertIn(b["final_step"], steps)
                self.assertIn(b["design_step"], steps)
                if b["red_step"]:
                    self.assertIn(b["red_step"], steps)
                self.assertEqual(set(b["roles"]), set(b["subagent_steps"]),
                                 "у каждой субагентной фазы — роль SoD")
                self.assertLessEqual(set(b["roles"].values()), roles)
                self.assertEqual(set(b["phase_work"]), set(b["subagent_steps"]))
                self.assertLessEqual(set(b["artifacts"]), steps)

    def test_prefixes_do_not_overlap(self):
        prefixes = [b["prefix"] for b in BR.BRANCHES.values()]
        for a in prefixes:
            for c in prefixes:
                if a != c:
                    self.assertFalse(a.startswith(c), (a, c))


class ConsumersSeeEveryBranch(unittest.TestCase):
    def test_pipeline_phases_and_update(self):
        import pipeline_phases as PP
        upd = _load(SKILLS / "pipeline-state" / "scripts" / "update.py", "update_mod")
        for skill, b in BR.BRANCHES.items():
            with self.subTest(skill=skill):
                for s in b["subagent_steps"]:
                    self.assertTrue(PP.requires_subagent(s), s)
                for s in b["gate_steps"]:
                    self.assertTrue(PP.requires_gate_result(s), s)
                for step, names in b["artifacts"].items():
                    self.assertEqual(upd._CANONICAL_ARTIFACTS.get(step), names)
                if b["red_step"]:
                    self.assertIn(b["red_step"], upd._RED_STEP_PREFIXES)

    def test_hooks(self):
        ipg = _load(HOOKS / "inline-phase-guard.py", "ipg_mod")
        sod = _load(HOOKS / "sod-enforcer.py", "sod_mod")
        import risk_ladder as R
        for skill, b in BR.BRANCHES.items():
            with self.subTest(skill=skill):
                for step, role in b["roles"].items():
                    self.assertEqual(sod.STEP_ROLE.get(step), role)
                for verify, impl in b["verify_to_impl"].items():
                    self.assertEqual(sod._VERIFY_TO_IMPL.get(verify), impl)
                self.assertIn(b["design_step"], R._STEP_PHASE_ALIASES["02-design"])
                for step in b["subagent_steps"]:
                    self.assertTrue(ipg._requires_subagent(step), step)

    def test_archive_resolve_phases_registry(self):
        arc = _load(SKILLS / "pipeline-state" / "scripts" / "archive.py", "archive_mod")
        rp = _load(SKILLS / "feature-pipeline" / "scripts" / "resolve_phases.py", "rp_mod")
        reg = json.loads((SKILLS / "config-helper" / "references" / "params-registry.json")
                         .read_text(encoding="utf-8"))
        mode_enum = next(p["enum"] for p in reg["params"] if p["id"] == "inputs.mode")
        for skill, b in BR.BRANCHES.items():
            with self.subTest(skill=skill):
                self.assertEqual(arc._FLAT_FINAL_FALLBACK.get(skill), b["final_step"])
                self.assertEqual(rp._MODE_TO_SKILLS.get(b["mode"]), {skill})
                self.assertIn(b["mode"], mode_enum)

    def test_policy_gate_tokens_and_paths(self):
        policy = json.loads((HOOKS / "risk-policy.json").read_text(encoding="utf-8"))
        expect = policy["gate_cmd_expect"]
        paths = json.loads((SKILLS / "feature-pipeline" / "references" / "skill-paths.json")
                           .read_text(encoding="utf-8"))["skills"]
        import skill_paths as SP
        for skill, b in BR.BRANCHES.items():
            with self.subTest(skill=skill):
                for step in b["gate_steps"]:
                    self.assertTrue(any(step.startswith(k) for k in expect if not k.startswith("_")),
                                    f"нет gate_cmd_expect для {step}: record_gate --cmd \"true\" закрыл бы шаг")
                self.assertIn(skill, paths, "скилл ветки не прописан в skill-paths.json")
                self.assertIn(b["docs_target"], SP._CLI_TARGETS)
                self.assertTrue((SKILLS / skill / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
