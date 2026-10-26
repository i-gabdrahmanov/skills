#!/usr/bin/env python3
"""test_branches_registry.py — реестр плоских веток (hooks/_branches.py) и его потребители.

Плоские ветки (fix, tech, ui) раньше жили кортежами в ~15 местах, и любой пропущенный — тихо
снятый гейт. Теперь источник один, а этот тест пинит две стороны:
  • реестр согласован сам с собой и со скиллом ветки (шаги = references/manifest-steps.json,
    субагентные/гейтовые шаги — подмножество шагов, роли — известные SoD-роли);
  • КАЖДЫЙ потребитель видит КАЖДУЮ ветку: update.py (субагент/гейт/артефакты), tdd-guard (RED),
    inline-phase-guard (артефакты фаз), sod-enforcer (роли), risk_ladder (design-алиас R2),
    update.py (утверждение документа ветки),
    archive.py (финальный шаг), resolve_phases (mode ↔ skill), params-registry (inputs.mode),
    skill_paths (docs-таргет), risk-policy (gate_cmd_expect на каждый гейтовый шаг),
    inline-phase-guard (phase_tools: продуктивные MCP-инструменты шага — главный агент их не вызывает).
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
                self.assertIsInstance(b["phase_tools"], dict, "поле phase_tools обязательно (у веток без MCP-движка — пустое)")
                self.assertLessEqual(set(b["phase_tools"]), set(b["subagent_steps"]),
                                     "продуктивные MCP-инструменты — у субагентных шагов")
                self.assertLessEqual(set(b["artifacts"]), steps)
                self.assertLessEqual(set(b.get("doc_approval") or {}), steps)

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
                for step, doc in (b.get("doc_approval") or {}).items():
                    self.assertIn((step, doc), upd._DOC_APPROVAL_STEPS,
                                  "утверждение документа ветки не видно update._check_doc_approval")

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
                for step, patterns in b["phase_tools"].items():
                    for pattern in patterns:
                        tool = pattern.replace("*", "probe")      # представитель шаблона
                        self.assertTrue(ipg._is_phase_work(step, f"mcp__forge-ui__{tool}", {}, REPO),
                                        f"inline-phase-guard не видит phase_tools {step}: {pattern}")

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


class PhaseToolsRegistry(unittest.TestCase):
    """Поле реестра `phase_tools` (задача 3.4): продуктивные MCP-инструменты шага, которые главный агент не вызывает."""

    # инструментов ещё нет — шаблоны ждут задач 4.x (daiver_screenshot появился в 4.2) (после них эти имена обязаны найтись в движке)
    PLANNED = {"daiver_verify_*"}
    # читающие и «оркестраторские» инструменты: в phase_tools не входят (иначе оркестратор заблокировал бы собственную работу)
    FREE = {"daiver_status", "daiver_init", "daiver_map_append", "daiver_map_lookup", "daiver_outline", "daiver_section",
            "daiver_source_nodes", "daiver_ds_inventory", "daiver_ds_lookup", "daiver_ds_details", "daiver_ds_requirements",
            "daiver_color_tokens", "daiver_spec", "daiver_track_manifest", "daiver_capabilities", "daiver_verify_decide"}
    EXPECTED_UI = {
        "ui-design": ("daiver_pixso_pull", "daiver_normalize", "daiver_resolve_*", "daiver_plan_write", "daiver_plan_contract"),
        "ui-build": ("daiver_build_state", "daiver_build_state_set", "daiver_svg_assets"),
        "ui-verify": ("daiver_screenshot", "daiver_code_compare", "daiver_verify_compare", "daiver_verify_state",
                      "daiver_verify_state_record", "daiver_render_reference", "daiver_visual_diff", "daiver_visual_review", "daiver_verify_report",
                      "daiver_e2e_plan", "daiver_e2e_run"),
    }

    @staticmethod
    def engine_tools():
        scripts = str(SKILLS / "forgeui" / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from daiver import tools as T
        return sorted(T.TOOLS)

    def test_the_field_is_in_every_branch(self):
        for skill, b in BR.BRANCHES.items():
            with self.subTest(skill=skill):
                self.assertIn("phase_tools", b)
        self.assertEqual(BR.BRANCHES["forgefix"]["phase_tools"], {})
        self.assertEqual(BR.BRANCHES["forgetech"]["phase_tools"], {})

    def test_the_ui_branch_by_step(self):
        self.assertEqual(BR.BRANCHES["forgeui"]["phase_tools"], self.EXPECTED_UI)
        self.assertEqual(BR.phase_tools(), self.EXPECTED_UI)

    def test_patterns_are_names_of_the_engine(self):
        import re
        for step, patterns in BR.phase_tools().items():
            for pattern in patterns:
                with self.subTest(step=step, pattern=pattern):
                    self.assertRegex(pattern, r"^daiver_[a-z0-9_]+\*?$")
        tools = self.engine_tools()
        for step, patterns in BR.phase_tools().items():
            for pattern in patterns:
                with self.subTest(step=step, pattern=pattern):
                    matched = [t for t in tools if re.fullmatch(pattern.replace("*", ".*"), t)]
                    self.assertTrue(matched or pattern in self.PLANNED,
                                    f"{pattern}: такого инструмента нет в движке (опечатка молча снимает enforcement шага)")

    def test_what_the_patterns_cover_in_the_engine_today(self):
        import fnmatch
        tools = self.engine_tools()
        cover = {step: [t for t in tools if any(fnmatch.fnmatchcase(t, p) for p in patterns)]
                 for step, patterns in BR.phase_tools().items()}
        self.assertEqual(cover, {
            "ui-design": ["daiver_normalize", "daiver_pixso_pull", "daiver_plan_contract", "daiver_plan_write",
                          "daiver_resolve_candidates", "daiver_resolve_finalize", "daiver_resolve_query", "daiver_resolve_report"],
            "ui-build": ["daiver_build_state", "daiver_build_state_set", "daiver_svg_assets"],
            "ui-verify": ["daiver_code_compare", "daiver_e2e_plan", "daiver_e2e_run", "daiver_render_reference", "daiver_screenshot", "daiver_verify_compare", "daiver_verify_report",
                          "daiver_verify_state", "daiver_verify_state_record", "daiver_visual_diff", "daiver_visual_review"],
        })

    def test_every_engine_tool_has_an_owner(self):
        """Новый инструмент движка обязан получить решение: продуктивный на каком-то шаге (phase_tools) или свободный (FREE).
        Иначе его вызов главным агентом пройдёт мимо enforcement молча."""
        productive = {t for t in self.engine_tools() if BR.tool_steps(t)}
        for tool in self.engine_tools():
            with self.subTest(tool=tool):
                self.assertTrue(tool in productive or tool in self.FREE,
                                f"{tool}: решить, чей он — phase_tools реестра веток или FREE этого теста")
                self.assertFalse(tool in productive and tool in self.FREE, f"{tool}: и продуктивный, и свободный")
        self.assertEqual(set(self.engine_tools()) & self.FREE, self.FREE, "в FREE остался инструмент, которого в движке нет")

    def test_orchestrator_tools_never_become_productive(self):
        """Оркестратор сам вызывает daiver_init (первый запуск) и daiver_map_append (после «да» пользователя, согласие ui-map-<KEY>)."""
        for tool in ("daiver_init", "daiver_map_append", "daiver_status"):
            for variant in (tool, f"mcp__forge-ui__{tool}"):
                with self.subTest(tool=variant):
                    self.assertEqual(BR.tool_steps(variant), ())

    def test_lookup_by_step_and_tool(self):
        self.assertEqual(BR.phase_tool("ui-design", "mcp__forge-ui__daiver_resolve_finalize"), "daiver_resolve_*")
        self.assertEqual(BR.phase_tool("ui-design", "daiver_plan_contract"), "daiver_plan_contract")
        self.assertIsNone(BR.phase_tool("ui-build", "mcp__forge-ui__daiver_plan_write"), "чужой шаг")
        self.assertIsNone(BR.phase_tool("ui-design", "mcp__forge-ui__daiver_status"))
        self.assertIsNone(BR.phase_tool("fix-diag", "mcp__forge-ui__daiver_plan_write"), "у fix-ветки инструментов нет")
        self.assertIsNone(BR.phase_tool("04-build-T1", "daiver_build_state_set"), "full-путь реестру не принадлежит")
        self.assertIsNone(BR.phase_tool(None, "daiver_plan_write"))
        self.assertIsNone(BR.phase_tool(["ui-design"], "daiver_plan_write"), "id шага — строка; нехэшируемое не роняет хук")
        self.assertIsNone(BR.phase_tool({"id": "ui-design"}, "daiver_plan_write"))
        self.assertIsNone(BR.phase_tool("ui-design", None))
        self.assertIsNone(BR.phase_tool("ui-design", ""))
        self.assertEqual(BR.tool_steps("mcp__forge-ui__daiver_build_state_set"), ("ui-build",))
        self.assertEqual(BR.tool_steps("mcp__forge-ui__daiver_resolve_anything"), ("ui-design",), "шаблон fnmatch")
        self.assertEqual(BR.tool_steps("mcp__forge-ui__daiver_verify_state_record"), ("ui-verify",))
        self.assertEqual(BR.tool_steps("mcp__forge-ui__daiver_verify_decide"), (), "решение человека вносит оркестратор — инструмент свободен")
        self.assertEqual(BR.tool_steps("mcp__atlassian__create_issue"), ())

    def test_the_server_prefix_does_not_matter_and_the_case_does(self):
        self.assertEqual(BR.tool_basename("mcp__forge-ui__daiver_plan_write"), "daiver_plan_write")
        self.assertEqual(BR.tool_basename("mcp__some__server__daiver_plan_write"), "daiver_plan_write")
        self.assertEqual(BR.tool_basename("daiver_plan_write"), "daiver_plan_write")
        self.assertEqual(BR.tool_basename(None), "")
        self.assertEqual(BR.tool_steps("mcp__forge-ui__DAIVER_PLAN_WRITE"), ())

    def test_the_brief_leaves_orchestrator_tools_to_the_orchestrator(self):
        brief = (SKILLS / "forgeui" / "SKILL.md").read_text(encoding="utf-8")
        for tool in ("daiver_map_append", "daiver_init"):
            self.assertIn(tool, brief)
            self.assertEqual(BR.tool_steps(tool), (), f"бриф велит оркестратору вызывать {tool} — в phase_tools ему не место")


if __name__ == "__main__":
    unittest.main()
