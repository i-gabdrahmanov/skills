#!/usr/bin/env python3
"""test_ui_build_brief.py — doc-lint брифа шага ui-build в skills/forgeui/SKILL.md, §4 (задача 3.2; принципы плана 3 и 4).

Бриф — guidance для слабой модели; то, что обязано держаться, держат инструменты и гейты (`build_state`, `ui_gate.py build`, хук 3.3).
Поэтому здесь не оценивается текст, а проверяется, что он НЕ ВРЁТ о коде и не оставляет модели невыполнимых команд:
  • каждый вызов `daiver_x(a, b)` в брифе использует аргументы схемы инструмента и называет все обязательные; бриф называет все
    инструменты шага и не тянет чужие: резолв и запись плана (план пересобирается на ui-design), verify, подпись плана (`daiver_plan_sign`
    нет и не будет — подпись это согласие человека);
  • поля ответов, на которые бриф опирается (`next`, `weightBudget`, `planMd`, `noCode`, `matched`, `raw`, `section.hash`), названы в
    описаниях инструментов, а разделы плана, на которые он ссылается («Раскладка», «Local-компоненты», «Оболочка над секциями», «Спеки»),
    есть в настоящем plan.md, который строит движок;
  • 14 правил сборки на месте, каждое один раз, шаги пронумерованы 1–5; метка `// TODO(forge-ui): wire business logic` — условленная;
  • команда гейта: раннер record_gate с `--step-id ui-build` и сам `ui_gate.py build` с обоими флагами; раннер принимает её как написана
    (субстанция проверена) и честно записывает красный гейт на пустом проекте; пути скриптов из брифа существуют;
  • статусы субагента разобраны оркестратором; длина §4 ограничена (растёт — выноси проверки в код, а не в текст).
Что НЕ проверяется: понимает ли слабая модель формулировки — это боевой прогон (X.4, чек-лист шага ui-build).
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORGE = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FORGE / "hooks"))

import test_build_state as TB  # noqa: E402 — построители синтетического экрана для настоящего plan.md
import test_ui_design_brief as DB  # noqa: E402 — общие помощники линтера брифов (команды, подстановка плейсхолдеров)
from daiver import tools as T  # noqa: E402

TEXT = DB.TEXT
BUILD = DB.section("## 4. Сборка", "## 5. Проверка")
PROMPT = BUILD.split("```")[1]                     # то, что видит субагент
ORCH = BUILD.split("```")[-1]                      # что делает оркестратор
MAX_BUILD_LINES = 52

BUILD_TOOLS = ("daiver_status", "daiver_build_state", "daiver_build_state_set", "daiver_section", "daiver_color_tokens",
               "daiver_svg_assets", "daiver_ds_details", "daiver_ds_lookup", "daiver_map_lookup", "daiver_spec")
NOT_FOR_BUILD = ("daiver_plan_sign", "daiver_plan_write", "daiver_plan_contract", "daiver_resolve_candidates", "daiver_resolve_query",
                 "daiver_resolve_finalize", "daiver_resolve_report", "daiver_pixso_pull", "daiver_normalize", "daiver_outline",
                 "daiver_map_append", "daiver_verify_compare", "daiver_screenshot")


class Tools(unittest.TestCase):
    def test_call_records_use_schema_arguments_and_name_the_required_ones(self):
        calls = re.findall(r"`(daiver_[a-z_]+)\(([^`()]*)\)`", PROMPT)
        documented = {name for name, _ in calls}
        self.assertEqual(documented - set(BUILD_TOOLS), set(), "вызов записан, а в списке инструментов шага его нет")
        self.assertGreaterEqual(len(documented), 6, sorted(documented))
        for name, raw in calls:
            schema = T.TOOLS[name]["schema"]
            args = [re.match(r"[A-Za-z_]+", a.strip()).group(0) for a in raw.split(",") if a.strip()]
            for a in args:
                self.assertIn(a, schema["properties"], f"{name}({raw}): аргумента «{a}» в схеме нет")
            for r in schema.get("required", []):
                self.assertIn(r, args, f"{name}({raw}): нет обязательного «{r}»")

    def test_the_step_names_every_tool_it_needs_and_none_that_belong_to_other_steps(self):
        for tool in BUILD_TOOLS:
            self.assertIn(tool, PROMPT, f"бриф ui-build не называет {tool}")
            self.assertIn(tool, T.TOOLS)
        for other in NOT_FOR_BUILD:
            self.assertNotIn(other, BUILD, f"{other}: инструмент чужого шага (план пересобирается на ui-design, проверка — ui-verify)")
        names = set(re.findall(r"\bdaiver_[a-z_]+\b", BUILD))
        self.assertEqual(sorted(n for n in names if n not in T.TOOLS), [], "бриф называет инструменты, которых нет")

    def test_the_response_fields_the_brief_relies_on_are_named_in_the_tool_descriptions(self):
        want = {"daiver_build_state": ("next", "weightBudget", "planMd", "noCode"), "daiver_color_tokens": ("matched", "raw"),
                "daiver_build_state_set": ("section.hash", "outputs")}
        for tool, keys in want.items():
            for key in keys:
                self.assertIn(key, T.TOOLS[tool]["description"], f"{tool}: описание не называет «{key}», а бриф на него ссылается")
                self.assertIn(key, PROMPT, f"бриф не называет «{key}»")

    def test_the_section_tool_takes_the_budget_the_brief_tells_to_pass(self):
        props = T.TOOLS["daiver_section"]["schema"]["properties"]
        self.assertIn("weightBudget", props)
        self.assertIn("бюджет конфига не подставляй", PROMPT)


class Rules(unittest.TestCase):
    def test_all_fourteen_rules_are_there_in_order_and_each_once(self):
        found = [int(n) for n in re.findall(r"\((\d{1,2})\) [А-ЯA-Z`]", PROMPT)]
        self.assertEqual(found, list(range(1, 15)), found)

    def test_the_numbered_steps_are_1_to_5(self):
        for n in range(1, 6):
            self.assertRegex(PROMPT, rf"(?m)^{n}\. ", f"нет пункта {n}")
        self.assertNotRegex(PROMPT, r"(?m)^6\. ")

    def test_the_plan_headings_the_brief_points_at_exist_in_a_real_plan_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            TB.make_world(root)
            md = (root / TB.PLAN_REL).with_name("plan.md").read_text(encoding="utf-8")
        self.assertIn("## Оболочка над секциями", md)
        for head in ("Раскладка", "Local-компоненты", "Оболочка над секциями", "Спеки"):
            self.assertIn(f"«{head}»", PROMPT, f"бриф ссылается на раздел «{head}»")
            self.assertRegex(md, rf"(?m)^## {re.escape(head)}", f"в plan.md нет раздела «{head}»")

    def test_the_business_logic_marker_is_the_agreed_one(self):
        self.assertIn("// TODO(forge-ui): wire business logic", PROMPT)

    def test_the_prohibitions_the_engine_and_the_spec_stand_on_are_in_the_text(self):
        for phrase in ("Инлайн-`<svg>` не пиши никогда", "ближний цвет не подбирай", "Значения (px, цвета, шрифты) — только из `daiver_section`",
                       "не с картинки", "Ничего не выдумывай", "дефолты не передавай", "набор не расширяй", "не разворачивай",
                       "не решай сам", "ТОЛЬКО", "Корневой px-размер кадра не хардкодь"):
            self.assertIn(phrase, PROMPT, phrase)

    def test_the_brief_never_sends_the_model_around_the_gate_or_back_to_the_plan(self):
        self.assertIn("шаг не обходи", PROMPT)
        self.assertIn("чини код сам (это не вопрос человеку)", PROMPT, "§13.8: красный билд — ремонт самого шага")
        self.assertIn("план пересобирается на `ui-design`", PROMPT)


class Commands(unittest.TestCase):
    def test_the_gate_command_names_the_runner_the_step_and_both_flags(self):
        cmds = [c for c in DB.shell_commands(BUILD) if "record_gate.py" in c]
        self.assertEqual(len(cmds), 1)
        argv = shlex.split(cmds[0])
        self.assertEqual(argv[argv.index("--step-id") + 1], "ui-build")
        self.assertEqual(argv[argv.index("--skill") + 1], "forgeui")
        inner = argv[argv.index("--cmd") + 1]
        self.assertIn("ui_gate.py build", inner)
        for flag in ("--project", "--feature"):
            self.assertIn(flag, inner)
        direct = [c for c in DB.shell_commands(BUILD) if "ui_gate.py" in c and "record_gate.py" not in c]
        self.assertEqual(len(direct), 1, "прямой запуск гейта для диагностики — одна команда")
        for flag in ("--project", "--feature"):
            self.assertIn(flag, direct[0])

    def test_the_runner_accepts_the_command_as_written_and_records_the_red_gate_on_an_empty_project(self):
        cmd = next(c for c in DB.shell_commands(BUILD) if "record_gate.py" in c)
        with tempfile.TemporaryDirectory() as tmp:
            argv = shlex.split(DB.substitute(cmd, Path(tmp)))
            inner = argv.index("--cmd") + 1
            argv[inner] = f"{sys.executable} -X utf8 " + argv[inner][len("python3 "):]
            r = subprocess.run([sys.executable, "-X", "utf8"] + argv[1:], capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 1, f"{r.stdout}\n{r.stderr}")                 # 2 — раннер отказал команде
        self.assertIn("ui-build: FAILED", r.stdout)

    def test_the_direct_gate_command_runs_as_written_and_says_why_it_is_not_ready(self):
        cmd = next(c for c in DB.shell_commands(BUILD) if "ui_gate.py" in c and "record_gate.py" not in c)
        with tempfile.TemporaryDirectory() as tmp:
            argv = shlex.split(DB.substitute(cmd, Path(tmp)))
            r = subprocess.run([sys.executable, "-X", "utf8"] + argv[1:], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ui-build: шаг не готов к закрытию", r.stderr)
        self.assertNotIn("usage:", r.stderr)

    def test_the_scripts_the_brief_runs_exist(self):
        for cmd in DB.shell_commands(BUILD):
            m = re.match(r"python3 <forge>/(\S+\.py)", cmd)
            self.assertTrue(m, cmd)
            self.assertTrue((FORGE / m.group(1)).is_file(), f"{m.group(1)}: скрипта нет")
            self.assertNotIn("\n", cmd)
            self.assertNotIn("$(", cmd)
            self.assertNotIn("`", cmd)
            shlex.split(cmd)

    def test_the_brief_asks_for_no_consent_and_writes_no_approval(self):
        self.assertNotIn("record_approval", BUILD, "подпись плана записана на ui-design; этот шаг согласий не пишет")
        self.assertIn("согласий этот шаг не пишет", ORCH)


class Statuses(unittest.TestCase):
    def test_every_subagent_status_the_brief_allows_is_handled_by_the_orchestrator(self):
        m = re.search(r'"status":"([a-z_|]+)"', PROMPT)
        self.assertTrue(m)
        statuses = set(m.group(1).split("|"))
        self.assertEqual(statuses, {"completed", "failed", "needs_input"})
        for st in statuses:
            self.assertIn(f"`{st}`", ORCH, f"{st}: оркестратор не знает, что с этим делать")

    def test_the_step_has_no_blocked_status_because_it_has_no_blocking_reasons(self):
        self.assertNotIn("blocked", BUILD)
        self.assertIn('`status:"needs_input"`', PROMPT, "вопрос человеку — через pending_questions, субагент сам не спрашивает")
        self.assertIn("(`failed`", PROMPT)


class Length(unittest.TestCase):
    def test_the_step_stays_short_enough_for_a_weak_reader(self):
        self.assertLessEqual(len(BUILD.splitlines()), MAX_BUILD_LINES, f"§4 — {len(BUILD.splitlines())} строк")

    def test_the_step_does_not_repeat_the_design_step_paragraphs(self):
        self.assertEqual(TEXT.count("--key ui-plan-<KEY>"), 1)
        self.assertEqual(TEXT.count("## 4. Сборка"), 1)
        self.assertNotIn("строится: этап 3", TEXT, "прежняя заглушка раздела убрана")
        self.assertNotIn("Будет: сборка модуля", TEXT)


if __name__ == "__main__":
    unittest.main()
