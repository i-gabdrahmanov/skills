#!/usr/bin/env python3
"""Tests for hooks/inline-phase-guard.py — actor-aware блок inline-работы subagent-фаз.

Главный агент (agent_type пуст) не может производить артефакты/код subagent-only фазы;
субагент (agent_type задан) — может; control-plane и не-subagent-фазы — fail-open;
override снимает блок.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "inline-phase-guard.py"


def _make(tmp: Path, active_step: str | None, slug: str = "feat", override_step=...) -> None:
    d = tmp / "ground" / "statements" / "feature-pipeline" / slug
    d.mkdir(parents=True, exist_ok=True)
    steps = [{"id": active_step, "status": "in_progress"}] if active_step else []
    (d / "manifest.json").write_text(json.dumps({
        "context": {"feature": slug}, "steps": steps,
    }), encoding="utf-8")
    if override_step is not ...:
        ov = d / "overrides"
        ov.mkdir(parents=True, exist_ok=True)
        payload = {"reason": "agent unavailable"}
        if override_step is not None:
            payload["step_id"] = override_step
        (ov / "subagent-origin.json").write_text(json.dumps(payload), encoding="utf-8")


def _run(tmp: Path, payload: dict) -> int:
    return _run_full(tmp, payload)[0]


def _run_full(tmp: Path, payload: dict) -> tuple[int, str]:
    payload = {**payload, "cwd": str(tmp)}
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True)
    return r.returncode, r.stderr


class TestInlinePhaseGuard(unittest.TestCase):
    # ── главный агент блокируется на productive-работе фазы ──
    def test_main_agent_blocked_writing_tech_design(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 2)

    def test_main_agent_blocked_writing_task_plan(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/task-plan.json")}}), 2)

    def test_main_agent_blocked_writing_sdd(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/sdd.md")}}), 2)

    def test_main_agent_blocked_writing_src_main_in_build(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)

    def test_main_agent_blocked_writing_src_test_in_red(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/test/java/XTest.java")}}), 2)

    def test_main_agent_blocked_gradle_in_tests_phase(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "05-tests")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew test"}}), 2)

    # ── escape-hatch: флаг autonomy.allow_inline_build разрешает build/test оркестратору ──
    def test_allow_inline_build_flag_unblocks_gradle(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "05-tests")
            (tmp / "ground" / "pipeline.json").write_text(json.dumps(
                {"autonomy": {"allow_inline_build": True}}), encoding="utf-8")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew test"}}), 0)

    def test_allow_inline_build_flag_default_blocks(self):
        # по умолчанию (нет флага) build/test-команда оркестратора в subagent-фазе блокируется
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "05-tests")
            (tmp / "ground" / "pipeline.json").write_text(json.dumps({}), encoding="utf-8")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew test"}}), 2)

    def test_allow_inline_build_does_not_unblock_src_write(self):
        """Флаг снимает блок ТОЛЬКО с build/test-команд, НЕ с записи кода/тестов inline."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            (tmp / "ground" / "pipeline.json").write_text(json.dumps(
                {"autonomy": {"allow_inline_build": True}}), encoding="utf-8")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)

    # ── record_gate / ensure_inventory — оркестраторный control-plane ──
    def test_record_gate_control_plane_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "python3 .gigacode/skills/pipeline-state/scripts/record_gate.py "
                                          "--step-id 04-test-T1 --cmd './gradlew test'"}}), 0)

    # ── субагент НЕ блокируется ──
    def test_subagent_allowed_writing_tech_design(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "agent_type": "general-purpose",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)

    def test_subagent_allowed_writing_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "agent_type": "general-purpose",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    # ── control-plane и нерелевантные действия — fail-open ──
    def test_control_plane_update_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "python3 .gigacode/skills/pipeline-state/scripts/update.py --step-id 02-design"}}), 0)

    def test_read_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Read",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)

    def test_unrelated_write_in_design_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "README.md")}}), 0)

    def test_no_active_step_failopen(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, None)
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    def test_non_subagent_phase_failopen(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "03-jira")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    # ── override снимает блок ──
    def test_override_for_step_allows(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design", override_step="02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)

    def test_general_override_allows(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design", override_step=None)
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/tech-design.md")}}), 0)

    # ── Thrust 2: fix-diag — субагентная фаза (главный агент не пишет fix-plan inline) ──
    def test_main_agent_blocked_fix_diag_write(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "fix-diag", slug="f1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file",
                "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/f1/fix-plan.md")}}), 2)

    # ── Thrust 4: checkstyle/lint inline главным агентом в build/test-фазе → блок ──
    def test_main_agent_blocked_gradle_checkstyle_inline(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "fix-green")
            self.assertEqual(_run(tmp, {"tool_name": "run_shell_command",
                "tool_input": {"command": "./gradlew checkstyleMain"}}), 2)

    def test_main_agent_blocked_standalone_checkstyle_inline(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "run_shell_command",
                "tool_input": {"command": "checkstyle -c config.xml src/main/java"}}), 2)


def _make_real(tmp: Path, order: list[str], done_upto: str, slug: str = "feat") -> None:
    """Манифест КАК НА ЖИВОМ ПРОГОНЕ: закрытые шаги + pending-хвост, БЕЗ in_progress.

    `update.py` ведёт шаг pending → completed, промежуточную пометку брифы не делают."""
    d = tmp / "ground" / "statements" / "feature-pipeline" / slug
    d.mkdir(parents=True, exist_ok=True)
    idx = order.index(done_upto)
    steps = [{"id": s, "status": "completed" if i <= idx else "pending",
              "depends_on": [order[i - 1]] if i else []} for i, s in enumerate(order)]
    (d / "manifest.json").write_text(json.dumps(
        {"context": {"feature": slug}, "steps": steps}), encoding="utf-8")


class RealManifestNoInProgress(unittest.TestCase):
    """Регресс: хук резолвил фазу ТОЛЬКО по `in_progress` — статусу, который на живых прогонах
    не проставляет никто. Поэтому он молчал всегда, и оркестратор писал артефакты фаз inline
    (без SubagentStop → без origin-evidence → шаг потом не закрывался, и путь «наружу» вёл в
    R4-override). Тесты выше этого не ловили: они выставляют `in_progress` руками."""

    FULL = ["01-grounding", "02-sdd", "02-design", "04-test-T1", "04-build-T1"]
    FIX = ["fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"]

    def test_blocks_sdd_write_when_step_only_pending(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FULL, "01-grounding")   # текущий шаг = 02-sdd
            self.assertEqual(_run(tmp, {"tool_name": "Write", "tool_input": {
                "file_path": str(tmp / "docs/feature-pipeline/feat/sdd.md")}}), 2)

    def test_blocks_fix_plan_write_when_step_only_pending(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-intake", slug="BUG-1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file", "tool_input": {
                "file_path": str(tmp / "docs/feature-pipeline/BUG-1/fix-plan.md")}}), 2)

    def test_subagent_still_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-intake", slug="BUG-1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file", "agent_type": "general-purpose",
                "tool_input": {
                    "file_path": str(tmp / "docs/feature-pipeline/BUG-1/fix-plan.md")}}), 0)

    def test_blocks_during_parallel_tasks(self):
        """На full-пути с параллельными задачами готовых шагов несколько (04-build-T1 и
        04-test-T2), и `current_step_id` намеренно отдаёт None — хук обязан всё равно ловить
        inline-работу оркестратора, иначе дыра ровно на build-фазе."""
        order = ["04-test-T1", "04-build-T1", "04-test-T2", "04-build-T2"]
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_real(tmp, order, "04-test-T1")     # готовы 04-build-T1 и 04-test-T2
            self.assertEqual(_run(tmp, {"tool_name": "Write", "tool_input": {
                "file_path": str(tmp / "src/main/java/A.java")}}), 2, "код фазы 04-build inline")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "tool_input": {
                "file_path": str(tmp / "src/test/java/BTest.java")}}), 2, "тесты 04-test inline")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "agent_type": "general-purpose",
                "tool_input": {"file_path": str(tmp / "src/main/java/A.java")}}), 0,
                "субагенту — можно")

    def test_all_steps_done_is_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-spec", slug="BUG-1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file", "tool_input": {
                "file_path": str(tmp / "docs/feature-pipeline/BUG-1/sdd.md")}}), 0)


class TestSubagentDetection(unittest.TestCase):
    """Задача 008: рантайм может не слать agent_type вовсе.

    Тогда субагент неотличим от оркестратора и хук блокирует ровно ту работу, которую сам
    же требует делать субагентом. Признак `agent_id` подхватывается ТОЛЬКО когда ключа
    agent_type в payload нет совсем — иначе на рантайме, проставляющем agent_id всем,
    хук молча перестал бы работать."""

    def test_agent_id_counts_as_subagent_when_agent_type_absent(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "agent_id": "sub-42",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    def test_subagent_scope_marker_counts_as_subagent(self):
        """Главный путь на живом рантайме: в PreToolUse нет ни agent_type, ни agent_id
        (замерено на qwen-code 0.21.14), и субагента опознаёт только отметка сессии,
        которую ставит SubagentStart."""
        import subagent_scope
        sid = "e2e-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            payload = {"tool_name": "Write", "session_id": sid,
                       "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}
            self.assertEqual(_run(tmp, payload), 2, "без отметки — это оркестратор inline")
            subagent_scope.enter(sid, "general-purpose-1", "general-purpose")
            try:
                self.assertEqual(_run(tmp, payload), 0, "субагент работает — пропускаем")
            finally:
                subagent_scope.leave(sid, "general-purpose-1", "general-purpose")
            self.assertEqual(_run(tmp, payload), 2, "субагент закончил — гейт снова активен")

    # ── запись артефакта фазы средствами shell (обход write-инструмента) ──
    def test_shell_copy_into_phase_artifact_blocked(self):
        """Реальный обход с прогона: write_file в /tmp/sdd-temp.md (имя не совпадает) +
        `cp /tmp/sdd-temp.md docs/<slug>/sdd.md`. Хук смотрел только на Write/Edit, а для
        Bash — только на build/test-команды, и артефакт фазы появлялся без субагента."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            for cmd in ("cp /tmp/sdd-temp.md docs/feature-pipeline/feat/sdd.md",
                        "mv /tmp/sdd-temp.md docs/feature-pipeline/feat/sdd.md",
                        "printf x > docs/feature-pipeline/feat/sdd.md",
                        "cat /tmp/a | tee docs/feature-pipeline/feat/sdd.md",
                        "sed -i s/a/b/ docs/feature-pipeline/feat/sdd.md"):
                self.assertEqual(_run(tmp, {"tool_name": "Bash",
                                            "tool_input": {"command": cmd}}), 2, cmd)

    def test_shell_write_into_phase_code_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Bash", "tool_input": {
                "command": "cp /tmp/T.java src/main/java/T.java"}}), 2)

    def test_shell_read_of_phase_artifact_allowed(self):
        """Чтение и прогон судьи с путём артефакта в argv — не запись, блокировать нельзя."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            for cmd in ("cat docs/feature-pipeline/feat/sdd.md",
                        "python3 .gigacode/skills/sdd/scripts/check_sdd_doc.py "
                        "docs/feature-pipeline/feat/sdd.md",
                        "cp docs/feature-pipeline/feat/sdd.md /tmp/backup.md",
                        "ls -la docs"):
                self.assertEqual(_run(tmp, {"tool_name": "Bash",
                                            "tool_input": {"command": cmd}}), 0, cmd)

    def test_override_from_journal_lifts_block(self):
        """Escape-hatch обязан читаться ОТТУДА, КУДА ЕГО ПИШЕТ override_judge — из журнала.

        Регрессия: хук открывал файл overrides/subagent-origin.json, а overrides уехали в
        events.jsonl; прямая запись файла запрещена state-write-guard. Хатч был мёртв, и
        оркестратор на живом прогоне перебирал record_approval → override_judge → echo> →
        --closed-by inline по кругу, а затем обошёл гейт копированием из /tmp."""
        import forge_events as FE
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            payload = {"tool_name": "Write",
                       "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/sdd.md")}}
            self.assertEqual(_run(tmp, payload), 2, "без override — блок")
            FE.append_event(tmp, "feature-pipeline", "feat", "override",
                            target="subagent-origin", judge="subagent-origin",
                            step_id="02-sdd", reason="agent() недоступен",
                            approved_by="user", produced_by="override_judge")
            self.assertEqual(_run(tmp, payload), 0, "override из журнала снимает блок")

    def test_deny_names_missing_actor_signal(self):
        """Отказ обязан различать «ты правда оркестратор» и «actor-сигнала нет вовсе».

        Второе — тупик: без SubagentStart хук блокирует и САМ субагент, и запуск ещё одного
        субагента даёт тот же отказ по кругу (tasks/008). Причина должна быть в тексте, иначе
        она недиагностируема — на прогоне это выглядит как «субагент не смог применить правки»."""
        import subagent_scope
        sid = "diag-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            payload = {"tool_name": "Write", "session_id": sid,
                       "tool_input": {"file_path": str(tmp / "docs/feature-pipeline/feat/sdd.md")}}

            code, err = _run_full(tmp, payload)
            self.assertEqual(code, 2)
            self.assertIn("не пришло НИ ОДНОГО SubagentStart", err)

            # SubagentStart прошёл и субагент уже закончил: сигнал жив, блок корректен.
            subagent_scope.enter(sid, "general-purpose-1", "general-purpose")
            subagent_scope.leave(sid, "general-purpose-1", "general-purpose")
            code, err = _run_full(tmp, payload)
            self.assertEqual(code, 2)
            self.assertIn("Actor-сигнал в этой сессии РАБОТАЕТ", err)
            self.assertNotIn("не пришло НИ ОДНОГО SubagentStart", err)

    def test_start_seen_is_sticky_across_stop(self):
        """`seen` переживает SubagentStop — это факт про рантайм, а не про живого субагента."""
        import subagent_scope
        sid = "sticky-" + uuid.uuid4().hex
        self.assertFalse(subagent_scope.start_seen(sid))
        subagent_scope.enter(sid, "gp-1", "general-purpose")
        self.assertTrue(subagent_scope.start_seen(sid))
        subagent_scope.leave(sid, "gp-1", "general-purpose")
        self.assertFalse(subagent_scope.active(sid), "субагент закончил")
        self.assertTrue(subagent_scope.start_seen(sid), "но сигнал рантайма — был")

    def test_empty_agent_type_still_means_orchestrator(self):
        """Ключ ЕСТЬ и пуст — рантайм умеет это поле, доверяем ему, agent_id не спасает."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write", "agent_type": "",
                "agent_id": "sub-42",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)


# ── фронт: продуктивные MCP-инструменты шага (`phase_tools`) и сборка npm/tsc (forge-ui; задача 3.4) ───────────
UI_STEPS = ["ui-spec", "ui-design", "ui-build", "ui-verify"]
UI_KEY = "STOR-100"


def _make_ui(tmp: Path, current: str | None, feature: str = UI_KEY, skill: str = "forgeui") -> None:
    """Манифест прогона forgeui КАК НА ЖИВОМ: шаги до `current` закрыты, `current` и дальше — pending; None — закрыты все."""
    d = tmp / "ground" / "statements" / skill / feature
    d.mkdir(parents=True, exist_ok=True)
    steps, done, prev = [], True, None
    for sid in UI_STEPS:
        if sid == current:
            done = False
        steps.append({"id": sid, "status": "completed" if done else "pending", "depends_on": [prev] if prev else []})
        prev = sid
    (d / "manifest.json").write_text(json.dumps({"context": {"feature": feature}, "steps": steps}), encoding="utf-8")


def _mcp(tool: str, server: str = "forge-ui") -> str:
    return f"mcp__{server}__{tool}"


# что главный агент НЕ вызывает на шаге (продуктивное) — по задаче 3.4 и её заметкам (2.6, 3.1, 3.2)
PRODUCTIVE = {
    "ui-design": ("daiver_pixso_pull", "daiver_normalize", "daiver_resolve_candidates", "daiver_resolve_query",
                  "daiver_resolve_finalize", "daiver_resolve_report", "daiver_plan_write", "daiver_plan_contract"),
    "ui-build": ("daiver_build_state", "daiver_build_state_set", "daiver_svg_assets"),
    "ui-verify": ("daiver_screenshot", "daiver_verify_state", "daiver_verify_compare"),
}
# читающие и «оркестраторские» инструменты — свободны на любом шаге: иначе оркестратор заблокировал бы собственную работу
FREE_TOOLS = ("daiver_status", "daiver_init", "daiver_map_append", "daiver_map_lookup", "daiver_outline", "daiver_section",
              "daiver_source_nodes", "daiver_ds_inventory", "daiver_ds_lookup", "daiver_ds_details", "daiver_ds_requirements",
              "daiver_color_tokens", "daiver_spec", "daiver_track_manifest")


class TPhaseTools(unittest.TestCase):
    """Главный агент не вызывает продуктивные MCP-инструменты шага: фазу исполняет субагент (как с записью артефактов)."""

    def test_main_agent_is_blocked_on_every_productive_tool_of_the_active_step(self):
        for step, tools in PRODUCTIVE.items():
            for tool in tools:
                for name in (_mcp(tool), tool, _mcp(tool, "forge_ui")):
                    with self.subTest(step=step, tool=name), tempfile.TemporaryDirectory() as d:
                        tmp = Path(d); _make_ui(tmp, step)
                        rc, err = _run_full(tmp, {"tool_name": name, "tool_input": {"track": UI_KEY}})
                        self.assertEqual(rc, 2, f"главный агент вызвал {name} на {step}: {err}")
                        self.assertIn(f"фаза '{step}'", err)
                        self.assertIn(tool, err)
                        self.assertIn("agent(subagent_type", err)

    def test_the_subagent_does_the_work(self):
        for step, tools in PRODUCTIVE.items():
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make_ui(tmp, step)
                for tool in tools:
                    with self.subTest(step=step, tool=tool):
                        self.assertEqual(_run(tmp, {"tool_name": _mcp(tool), "agent_type": "general-purpose",
                                                    "tool_input": {}}), 0)
                        self.assertEqual(_run(tmp, {"tool_name": _mcp(tool), "agent_id": "sub-1", "tool_input": {}}), 0)

    def test_the_subagent_scope_marker_counts_as_subagent_for_tools_too(self):
        import subagent_scope
        sid = "e2e-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_ui(tmp, "ui-design")
            payload = {"tool_name": _mcp("daiver_plan_write"), "session_id": sid, "tool_input": {}}
            self.assertEqual(_run(tmp, payload), 2, "без отметки — это оркестратор inline")
            subagent_scope.enter(sid, "general-purpose-1", "general-purpose")
            try:
                self.assertEqual(_run(tmp, payload), 0, "субагент работает — пропускаем")
            finally:
                subagent_scope.leave(sid, "general-purpose-1", "general-purpose")
            self.assertEqual(_run(tmp, payload), 2)

    def test_reading_and_orchestrator_owned_tools_are_free_on_every_step(self):
        for step in UI_STEPS:
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make_ui(tmp, step)
                for tool in FREE_TOOLS:
                    with self.subTest(step=step, tool=tool):
                        self.assertEqual(_run(tmp, {"tool_name": _mcp(tool), "tool_input": {}}), 0)

    def test_a_tool_of_another_step_does_not_block(self):
        """Инструмент шага блокируется только пока шаг идёт: после закрытия ui-design оркестратору можно, например, перетянуть макет."""
        for step, tools in PRODUCTIVE.items():
            for other, other_tools in PRODUCTIVE.items():
                if other == step:
                    continue
                with tempfile.TemporaryDirectory() as d:
                    tmp = Path(d); _make_ui(tmp, step)
                    for tool in other_tools:
                        if tool in tools:
                            continue
                        with self.subTest(step=step, tool=tool):
                            self.assertEqual(_run(tmp, {"tool_name": _mcp(tool), "tool_input": {}}), 0)

    def test_nothing_is_blocked_when_the_run_is_over_or_absent(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_ui(tmp, None)
            self.assertEqual(_run(tmp, {"tool_name": _mcp("daiver_plan_write"), "tool_input": {}}), 0)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_run(Path(d), {"tool_name": _mcp("daiver_plan_write"), "tool_input": {}}), 0)

    def test_the_back_end_runs_are_untouched(self):
        """Другая ветка: те же имена инструментов ничего не значат; чужие MCP (Jira) не судятся вовсе."""
        for skill_step in ("04-build-T1", "02-design", "03-jira", "02-sdd"):
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make(tmp, skill_step)
                for name in (_mcp("daiver_plan_write"), _mcp("daiver_build_state_set"), "mcp__atlassian__create_issue",
                             "mcp__jira__jira_create_issue", "mcp__confluence__search"):
                    with self.subTest(step=skill_step, tool=name):
                        self.assertEqual(_run(tmp, {"tool_name": name, "tool_input": {"summary": "x"}}), 0)

    def test_override_of_the_subagent_origin_lifts_the_block_with_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_ui(tmp, "ui-design")
            ov = tmp / "ground" / "statements" / "forgeui" / UI_KEY / "overrides"
            ov.mkdir()
            (ov / "subagent-origin.json").write_text(json.dumps({"reason": "agent недоступен", "step_id": "ui-design"}),
                                                     encoding="utf-8")
            rc, err = _run_full(tmp, {"tool_name": _mcp("daiver_plan_write"), "tool_input": {}})
            self.assertEqual(rc, 0, err)
            self.assertIn("WARN", err)

    def test_other_non_write_tools_are_still_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_ui(tmp, "ui-design")
            for name in ("read_file", "grep_search", "agent", "ask_user_question", "web_fetch", "mcp__forge-ui__"):
                with self.subTest(tool=name):
                    self.assertEqual(_run(tmp, {"tool_name": name, "tool_input": {"file_path": "x"}}), 0)


class TFrontBuildInline(unittest.TestCase):
    """На build-шагах ui (ui-build, ui-verify) главный агент не гоняет npm/tsc/vite/playwright — это работа субагента; субагент ИХ гоняет
    свободно (красный билд чинит он). Слова в путях и аргументах сборкой не считаются: control-plane с `--project /home/me/vite-app` жив."""

    CMDS = ("npm run build", "npm test", "pnpm build", "yarn test", "npx tsc --noEmit", "npx vite build", "npx playwright test",
            "tsc -p .", "vite build", "playwright test", "cd web && npm run build", "CI=1 npm test")

    def test_main_agent_is_blocked_on_build_steps(self):
        for step in ("ui-build", "ui-verify"):
            for cmd in self.CMDS:
                with self.subTest(step=step, cmd=cmd), tempfile.TemporaryDirectory() as d:
                    tmp = Path(d); _make_ui(tmp, step)
                    rc, err = _run_full(tmp, {"tool_name": "run_shell_command", "tool_input": {"command": cmd}})
                    self.assertEqual(rc, 2, f"оркестратор собрал фронт inline на {step}: {cmd}")
                    self.assertIn("запуск сборки/тестов", err)
                    self.assertIn("npm|pnpm|yarn|npx", err)
                    self.assertNotIn("(?:", err, "отказ называет запрещённое словами, а не регулярным выражением")

    def test_the_subagent_builds_and_fixes_a_red_build(self):
        for step in ("ui-build", "ui-verify"):
            for cmd in self.CMDS:
                with self.subTest(step=step, cmd=cmd), tempfile.TemporaryDirectory() as d:
                    tmp = Path(d); _make_ui(tmp, step)
                    self.assertEqual(_run(tmp, {"tool_name": "run_shell_command", "agent_type": "general-purpose",
                                                "tool_input": {"command": cmd}}), 0)

    def test_it_is_not_the_job_of_this_hook_on_design_and_spec_steps(self):
        """На ui-spec/ui-design сборку фронта запрещает роль (sod-enforcer); это хук про build-шаги."""
        for step in ("ui-spec", "ui-design"):
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make_ui(tmp, step)
                self.assertEqual(_run(tmp, {"tool_name": "run_shell_command", "tool_input": {"command": "npm run build"}}), 0)

    def test_control_plane_with_a_project_path_that_looks_like_a_tool_is_alive(self):
        proj = "/home/me/vite-app"
        for cmd in (f"python3 .gigacode/skills/pipeline-state/scripts/record_gate.py --project {proj} --skill forgeui --feature {UI_KEY} "
                    f"--step-id ui-build --cmd \"python3 .gigacode/skills/forgeui/scripts/ui_gate.py build --project {proj} --feature {UI_KEY}\"",
                    f"python3 .gigacode/skills/forgeui/scripts/ui_gate.py build --project {proj} --feature {UI_KEY}",
                    f"python3 .gigacode/skills/pipeline-state/scripts/update.py --project /work/playwright-tests --skill forgeui "
                    f"--feature {UI_KEY} --step-id ui-build --status completed",
                    "cat vite.config.ts", "grep playwright package.json", "npm install", "git status"):
            with self.subTest(cmd=cmd[:80]), tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make_ui(tmp, "ui-build")
                rc, err = _run_full(tmp, {"tool_name": "run_shell_command", "tool_input": {"command": cmd}})
                self.assertEqual(rc, 0, f"{cmd}\n{err}")

    def test_the_inline_build_flag_lifts_it_for_npm_too(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_ui(tmp, "ui-build")
            payload = {"tool_name": "run_shell_command", "tool_input": {"command": "npm run build"}}
            self.assertEqual(_run(tmp, payload), 2)
            (tmp / "ground" / "pipeline.json").write_text(json.dumps({"autonomy": {"allow_inline_build": True}}), encoding="utf-8")
            self.assertEqual(_run(tmp, payload), 0)

    def test_back_end_builds_are_blocked_as_before(self):
        for step, cmd in (("04-build-T1", "./gradlew test"), ("05-tests", "./mvnw verify"), ("04-test-T1", "mvn -q test")):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make(tmp, step)
                self.assertEqual(_run(tmp, {"tool_name": "Bash", "tool_input": {"command": cmd}}), 2)
                self.assertEqual(_run(tmp, {"tool_name": "Bash", "agent_type": "general-purpose",
                                            "tool_input": {"command": cmd}}), 0)

    def test_back_end_step_does_not_stop_the_front_tools_by_name(self):
        """`npm run build` на бэковой build-фазе тоже сборка (в Java-репо бывает фронт-часть): оркестратору inline нельзя."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Bash", "tool_input": {"command": "npm run build"}}), 2)

if __name__ == "__main__":
    unittest.main()
