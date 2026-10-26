#!/usr/bin/env python3
"""test_ui_design_brief.py — doc-lint брифа шага ui-design в skills/forgeui/SKILL.md (задача 2.5; принципы плана 3 и 4).

Бриф — guidance для слабой модели; то, что обязано держаться, держат инструменты и гейты. Поэтому здесь не оценивается текст, а
проверяется, что он НЕ ВРЁТ о коде и не оставляет модели невыполнимых команд:
  • каждое `daiver_*` в брифе — зарегистрированный инструмент; в записях вызова `daiver_x(a, b)` каждый аргумент есть в схеме
    инструмента и названы все обязательные (модель копирует вызов как есть; опечатка в имени — отказ в бою);
  • бриф ui-design называет все инструменты шага (обрубок брифа не проходит) и не называет инструменты, которых ещё нет
    (verify — следующие задачи; инструмента подписи плана не будет никогда: подпись — согласие человека). Бриф ui-build (§4) живёт в
    test_ui_build_brief.py; здесь его команды и имена инструментов проверяются как часть всего живого текста;
  • команды оболочки в брифе исполнимы: однострочные, без `$(...)` и обратных кавычек (рантайм режет), парсятся POSIX-shlex;
    `record_approval.py` и `ui_gate.py` из брифа ЗАПУСКАЮТСЯ как написаны (плейсхолдеры подставлены) и принимаются скриптами;
  • у согласий из брифа есть цитата пользователя (--evidence) там, где ключ — класс согласий с цитатой, и привязка к тому, что
    человек прочёл (--bind-file: `resolution-report.md` у `ui-resolve`, `plan.lock.json` у `ui-plan`); классы согласий — те, что
    зарегистрированы в forge_events;
  • причина останова `blocked` — константа движка; статусы JSON, которые бриф разрешает субагенту, разобраны оркестратором (§3.1);
    виды вопросов (`kind`) — те, что оркестратор умеет задать и обработать (в том числе подпись плана, 2.6);
  • путь, который бриф предлагает для выгрузки DSL (`.daiver/inbox/…`), принимает `daiver_pixso_pull`;
  • длина: бриф читает слабая модель, поэтому файл и раздел §3 ограничены (растёт — выноси проверки в код, а не в текст).
Что НЕ проверяется: понимает ли слабая модель формулировки — это боевой прогон (X.4, чек-лист шага).
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
FORGE = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FORGE / "hooks"))

import forge_events as FE  # noqa: E402
from daiver import ds_lookup as DL  # noqa: E402
from daiver import plan_contract as PC  # noqa: E402
from daiver import resolution_report as RR  # noqa: E402
from daiver import tools as T  # noqa: E402

SKILL = HERE.parent / "SKILL.md"
FIXTURE = HERE.parent / "fixtures" / "pixso-modal" / "dsl.json"
TEXT = SKILL.read_text(encoding="utf-8")

# инструменты, без которых шаг ui-design не пройти: бриф обязан их назвать
STEP_TOOLS = ("daiver_status", "daiver_pixso_pull", "daiver_track_manifest", "daiver_normalize", "daiver_svg_assets",
              "daiver_outline", "daiver_ds_inventory", "daiver_ds_requirements", "daiver_ds_lookup", "daiver_ds_details",
              "daiver_resolve_candidates", "daiver_resolve_query", "daiver_resolve_finalize", "daiver_resolve_report",
              "daiver_source_nodes", "daiver_map_append", "daiver_plan_write", "daiver_plan_contract")
MAX_FILE_LINES = 350                            # 320 → 345: в файл вошёл шаг ui-build (3.2, §4 — до 52 строк); 345 → 350: абзац про daiver_screenshot (4.2); рост дальше — выноси в код
MAX_STEP_LINES = 110                            # шаг вырос на план, контракт и подпись (2.6): 8 → 10 пунктов


def section(start: str, end: str) -> str:
    a = TEXT.index(start)
    return TEXT[a:TEXT.index(end, a)]


STEP = section("## 3. Дизайн по макету", "## 4. Сборка")
LIVE = TEXT[:TEXT.index("## 5. Проверка")]     # §5 описывает то, что ещё строится («Будет: …»): его инструментов пока не существует


def shell_commands(text: str) -> List[str]:
    """Команды `python3 …` брифа: из строк кода и из однострочных `код`-спанов."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("python3 "):
            out.append(s)
    for m in re.finditer(r"`(python3 [^`\n]+)`", text):
        out.append(m.group(1))
    return out


def substitute(cmd: str, root: Path) -> str:
    cmd = cmd.replace("<forge>", FORGE.as_posix()).replace("<toplevel>", root.as_posix()).replace("<KEY>", "STOR-1")
    cmd = cmd.replace("<ответ дословно>", "да, подтверждаю").replace("<match>", "Modal Header")
    return re.sub(r"<[^<>\s][^<>]*>", "x", cmd)


class Tools(unittest.TestCase):
    def test_every_daiver_name_in_the_brief_is_a_registered_tool(self):
        names = set(re.findall(r"\bdaiver_[a-z_]+\b", LIVE))
        self.assertTrue(names)
        self.assertEqual(sorted(n for n in names if n not in T.TOOLS), [],
                         "бриф называет инструменты, которых нет (verify — следующие задачи)")

    def test_call_records_use_schema_arguments_and_name_the_required_ones(self):
        calls = re.findall(r"`(daiver_[a-z_]+)\(([^`()]*)\)`", STEP)
        documented = {name for name, _ in calls}
        self.assertEqual(documented - set(STEP_TOOLS), set(), "вызов записан, а в списке инструментов шага его нет")
        self.assertGreaterEqual(len(documented), 13, sorted(documented))
        for name, raw in calls:
            self.assertIn(name, T.TOOLS, name)
            schema = T.TOOLS[name]["schema"]
            args = [re.match(r"[A-Za-z_]+", a.strip()).group(0) for a in raw.split(",") if a.strip()]
            for a in args:
                self.assertIn(a, schema["properties"], f"{name}({raw}): аргумента «{a}» в схеме нет")
            for r in schema.get("required", []):
                self.assertIn(r, args, f"{name}({raw}): нет обязательного «{r}»")

    def test_the_step_names_every_tool_it_needs_and_no_future_ones(self):
        for tool in STEP_TOOLS:
            self.assertTrue(tool in STEP, f"бриф ui-design не называет {tool}")
        for future in ("daiver_verify_compare", "daiver_screenshot", "daiver_plan_sign"):
            self.assertTrue(future not in LIVE, f"{future}: инструмента нет, бриф шага не должен его обещать")

    def test_one_place_per_rule_no_duplicated_paragraphs(self):
        # согласия описаны один раз (§3.1), прежние абзацы про карту/библиотеку/резолв влиты в процедуру шага
        self.assertEqual(TEXT.count("--key ui-resolve-<KEY>"), 1)
        self.assertEqual(TEXT.count("--key ui-map-<KEY>"), 1)
        self.assertEqual(TEXT.count("--key ui-plan-<KEY>"), 1)
        for old in ("**Карта компонентов**", "**Требования дизайн-системы**", "**Библиотека, когда в карте нет правила**",
                    "**Резолв кандидатов**", "отдельная часть этого шага, в брифе её пока нет"):
            self.assertTrue(old not in TEXT, f"абзац вернулся: {old}")


class Commands(unittest.TestCase):
    def test_commands_are_one_line_shlex_parsable_and_free_of_substitutions(self):
        cmds = shell_commands(TEXT)
        self.assertGreaterEqual(len(cmds), 8)
        for c in cmds:
            self.assertNotIn("$(", c, c)
            self.assertNotIn("`", c, c)
            self.assertNotIn("\n", c)
            try:
                shlex.split(c)
            except ValueError as e:                  # pragma: no cover — сообщение для человека
                self.fail(f"команда не парсится POSIX-shlex ({e}): {c}")

    def test_record_approval_commands_carry_what_the_script_demands(self):
        cmds = [c for c in shell_commands(TEXT) if "record_approval.py" in c]
        keys = {}
        for c in cmds:
            m = re.search(r"--key (\S+)", c)
            self.assertTrue(m, c)
            key = m.group(1)
            keys[key] = c
            for flag in ("--project", "--reason"):
                self.assertIn(flag, c, f"{key}: без {flag} скрипт откажет")
            if FE.consent_required(key.replace("<KEY>", "STOR-1")):
                self.assertIn("--evidence", c, f"{key}: класс согласий с цитатой пользователя")
        self.assertIn("ui-resolve-<KEY>", keys)
        self.assertIn("ui-map-<KEY>", keys)
        self.assertIn("ui-plan-<KEY>", keys)
        bound = keys["ui-resolve-<KEY>"]
        self.assertIn(f"--bind-file .daiver/tracks/<KEY>/{RR.REPORT_NAME}", bound,
                      "подтверждение привязано к отчёту, который строит движок")
        self.assertIn(f"--bind-file .daiver/tracks/<KEY>/{PC.LOCK_NAME}", keys["ui-plan-<KEY>"],
                      "подпись привязана к lock плана, который строит движок: без привязки она пережила бы любую правку плана")

    def test_consent_classes_named_in_the_brief_are_registered(self):
        classes = set(re.findall(r"`(ui-[a-z]+)-<KEY>`", TEXT)) | set(re.findall(r"`(ui-[a-z]+)-\*`", TEXT))
        classes.discard("ui-spec-approved")
        self.assertEqual(classes, {"ui-plan", "ui-map", "ui-resolve", "ui-accept"})
        for c in classes:
            self.assertIn(c, FE.CONSENT_PREFIXES, f"{c}: класс согласий не зарегистрирован")

    def test_the_commands_of_the_brief_actually_run(self):
        """Команды согласий и гейта запускаются так, как написаны (плейсхолдеры подставлены), и принимаются скриптами."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        report = root / ".daiver" / "tracks" / "STOR-1" / RR.REPORT_NAME
        report.parent.mkdir(parents=True)
        report.write_text("# отчёт\n", encoding="utf-8")
        (report.parent / PC.LOCK_NAME).write_text("{}\n", encoding="utf-8")
        ran = 0
        for c in shell_commands(TEXT):
            if "record_approval.py" not in c and "ui_gate.py" not in c:
                continue
            if "record_gate.py" in c:
                continue                                         # раннер гейта — отдельный тест ниже
            argv = shlex.split(substitute(c, root))
            r = subprocess.run([sys.executable, "-X", "utf8"] + argv[1:], capture_output=True, text=True,
                               encoding="utf-8", timeout=60)
            if "ui_gate.py" in c:
                self.assertIn(r.returncode, (1, 3), f"{c}\n{r.stderr}")        # 2 — ошибка аргументов
                self.assertNotIn("usage:", r.stderr)
            else:
                self.assertEqual(r.returncode, 0, f"{c}\n{r.stderr}")
            ran += 1
        self.assertGreaterEqual(ran, 4, "согласия ui-spec, ui-resolve, ui-map и ui-plan")

    def test_the_gate_runner_command_runs_as_written_and_is_accepted_by_the_substance_check(self):
        """record_gate.py не откажет команде из брифа (в ней есть сам гейт фазы) и честно запишет FAILED на пустом проекте."""
        cmds = [c for c in shell_commands(TEXT) if "record_gate.py" in c and "--step-id ui-design" in c]
        self.assertEqual(len(cmds), 1)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        argv = shlex.split(substitute(cmds[0], root))
        inner = argv.index("--cmd") + 1
        self.assertTrue(argv[inner].startswith("python3 "), argv[inner])
        argv[inner] = f"{sys.executable} -X utf8 " + argv[inner][len("python3 "):]       # тот же интерпретатор, что у теста
        r = subprocess.run([sys.executable, "-X", "utf8"] + argv[1:], capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 1, f"{r.stdout}\n{r.stderr}")                  # 2 — раннер отказал команде
        self.assertIn("ui-design: FAILED", r.stdout)

    def test_the_design_gate_command_names_both_required_flags(self):
        gate = [c for c in shell_commands(STEP) if "ui_gate.py design" in c]
        self.assertEqual(len(gate), 1)
        self.assertIn("--project", gate[0])
        self.assertIn("--feature", gate[0])


class Statuses(unittest.TestCase):
    def test_every_subagent_status_the_brief_allows_is_handled_by_the_orchestrator(self):
        m = re.search(r'"status":"([a-z_|]+)"', STEP)
        self.assertTrue(m)
        statuses = set(m.group(1).split("|"))
        self.assertEqual(statuses, {"needs_input", "blocked", "completed", "failed"})
        orchestrator = section("### 3.1", "## 4. Сборка")
        for st in ("needs_input", "blocked", "completed"):
            self.assertIn(st, orchestrator, f"{st}: оркестратор не знает, что с этим делать")

    def test_the_blocked_reason_is_the_engines_constant(self):
        self.assertEqual(DL.BLOCKED, "library-source-unreachable")
        self.assertIn(f"blocked: {DL.BLOCKED} —", STEP)
        self.assertIn(f'blocked:"{DL.BLOCKED}"', STEP)
        self.assertEqual(set(re.findall(r"blocked:\s?\"?([a-z-]+)", STEP)) - {""}, {DL.BLOCKED})

    def test_pending_question_kinds_match_what_the_report_tool_speaks_of(self):
        for kind in ("map-rule", "confirm-resolutions", "plan-question", "sign-plan"):
            self.assertIn(f'kind:"{kind}"', STEP)
            self.assertIn(f"`{kind}`", section("### 3.1", "## 4. Сборка"))
        desc = T.TOOLS["daiver_plan_contract"]["description"]
        for word in ("ask", "record", "toSign", "planUncertainty"):
            self.assertIn(word, desc, f"инструмент контракта не говорит про {word}, а бриф на него ссылается")
        desc = T.TOOLS["daiver_resolve_report"]["description"]
        self.assertIn("toConfirm", desc)
        self.assertIn("proposals", desc)


class Paths(unittest.TestCase):
    def test_the_dsl_path_the_brief_suggests_is_accepted_by_the_pull_tool(self):
        m = re.search(r"`(\.daiver/inbox/[^`]+)`", STEP)
        self.assertTrue(m, "бриф предлагает путь для выгрузки DSL")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        target = root / m.group(1).replace("<кадр>", "main")
        target.parent.mkdir(parents=True)
        shutil.copyfile(str(FIXTURE), str(target))
        out = T.call("daiver_pixso_pull", root, {"track": "STOR-1", "dslPath": target.relative_to(root).as_posix(),
                                                 "frameUrl": "https://pixso.example/a?item-id=2:153702", "frame": "main"})
        self.assertTrue(out.get("ok", True), out)
        self.assertTrue((root / ".daiver" / "tracks" / "STOR-1" / "track.yaml").is_file())


class Length(unittest.TestCase):
    def test_the_file_and_the_step_stay_short_enough_for_a_weak_reader(self):
        lines = TEXT.splitlines()
        self.assertLessEqual(len(lines), MAX_FILE_LINES, f"SKILL.md разросся до {len(lines)} строк")
        self.assertLessEqual(len(STEP.splitlines()), MAX_STEP_LINES, f"раздел ui-design — {len(STEP.splitlines())} строк")
        self.assertTrue(TEXT.startswith("---\nname: forgeui\n"))

    def test_the_step_is_numbered_and_every_number_is_there(self):
        for n in range(1, 11):
            self.assertRegex(STEP, rf"(?m)^{n}\. ", f"нет пункта {n}")
        self.assertNotRegex(STEP, r"(?m)^11\. ")


if __name__ == "__main__":
    unittest.main()
