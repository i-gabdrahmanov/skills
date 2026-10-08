#!/usr/bin/env python3
"""test_spec_map.py — карта мастера: сначала структура, потом merge.

Регрессия прогона 2026-10-05 (GigaCode, проект npf). Мастер — отдельный репо из сервисов:
`<сервис>/<файл>.md`, файлы не всегда совпадают с каталогом, в корне — навигационный
PROJECT_MAP.md. Старый ресерч объявил мастером PROJECT_MAP.md и посоветовал
`docs.master.spec_path=PROJECT_MAP.md`, а merge искал несуществующий `specs/npf/spec.md`. Пины:
  • карта находит сервисы и их файлы, индекс уходит в ignore;
  • без подтверждённой карты merge в такой мастер не пишет (exit 3 no-map);
  • сервис дельты определяется по модулю кода, выбор запоминается;
  • проза без раздела требований → no-section; --ensure-sections дописывает только хвост.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import spec_cli          # noqa: E402
import spec_map as SM    # noqa: E402

PROSE = """# Спецификация сервиса взаимодействия с внешними АС

## <a name="1"></a> 1. Общее
### <a name="11"></a> 1.1 Приём сообщений
Сервис принимает и валидирует входящие сообщения.
### <a name="12"></a> 1.2 Доставка
Гарантированная доставка во внутренние сервисы.

## <a name="2"></a> 2. SQL
```sql
select 1;
```
"""

PROJECT_MAP = """# Project map
quick navigation index for all subprojects

## 📋 Subproject details
### 1. artifact-processor
- endpoints: /a
### 2. db-service
- endpoints: /b
### 3. inbound-adapter
- endpoints: /c
"""

SDD = """# SDD: Метрика PENDING-очереди

## 1. Назначение
Размер очереди kafka_inbound_queue в модуле {module}.

## 3. Функциональные требования (Given-When-Then)
- **Given** в очереди есть PENDING-записи **When** снимается метрика **Then** отдаётся их число
"""


def run(*argv) -> "tuple[int, str]":
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = spec_cli.main(list(argv))
    return rc, out.getvalue() + err.getvalue()


class SpecMapBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        self.root = tmp / "proj"
        self.spec = tmp / "npf-spec"
        (self.root / "ground" / "inventory" / "scan").mkdir(parents=True)
        (self.root / "ground" / "policy.json").write_text(json.dumps({
            "project": {"name": "npf"},
            "docs": {"mode": "in-repo", "docs_path": "docs",
                     "master": {"enabled": True, "mode": "separate-repo",
                                "repo_path": str(self.spec)}}}), encoding="utf-8")
        (self.root / "ground" / "inventory" / "scan" / "structure.json").write_text(json.dumps({
            "modules": [{"name": "inboundservice", "path": "service/inboundservice"},
                        {"name": "dbservice", "path": "service/dbservice"}]}),
            encoding="utf-8")
        for cap, fname in (("inbound-adapter", "inbound-adapter.md"),
                           ("db-service", "dbservice.md"),
                           ("artifact-processor", "arifact-processor.md")):
            (self.spec / cap).mkdir(parents=True)
            (self.spec / cap / fname).write_text(PROSE, encoding="utf-8")
        (self.spec / "PROJECT_MAP.md").write_text(PROJECT_MAP, encoding="utf-8")
        self.inbound = self.spec / "inbound-adapter" / "inbound-adapter.md"

    def tearDown(self):
        spec_cli._GRAMMAR_CACHE.clear()
        self._tmp.cleanup()

    def delta(self, slug="NPF-42", module="service/inboundservice"):
        d = self.root / "docs" / "feature-pipeline" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "sdd.md").write_text(SDD.format(module=module), encoding="utf-8")

    def cli(self, *argv):
        spec_cli._GRAMMAR_CACHE.clear()
        return run("--project-root", str(self.root), *argv)

    def confirm(self):
        self.assertEqual(self.cli("research")[0], 3, "черновик карты ждёт решения")
        rc, out = self.cli("research", "--confirm")
        self.assertEqual(rc, 0, out)


class MissingScanDirTest(unittest.TestCase):
    """Боевой прогон v0.4.6 (SA-4): каталога из docs.master.spec_path нет — research падал
    сырым FileNotFoundError, а status при этом показывал «НЕ слито»."""

    def test_research_explains_missing_dir(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ground").mkdir()
            (root / "ground" / "policy.json").write_text(json.dumps({
                "project": {"name": "x"},
                "docs": {"mode": "in-repo", "docs_path": "docs",
                         "master": {"enabled": True, "spec_path": "specs/npf/spec.md"}}}),
                encoding="utf-8")
            (root / "docs").mkdir()
            spec_cli._GRAMMAR_CACHE.clear()
            rc, out = run("--project-root", str(root), "research")
            self.assertEqual(rc, 2, out)
            self.assertIn("каталога мастеров нет", out)
            self.assertNotIn("FileNotFoundError", out)


class BuildTest(SpecMapBase):
    def test_services_found_and_index_ignored(self):
        m = SM.build(self.root)
        self.assertEqual(m["layout"], "per-capability")
        self.assertEqual(sorted(m["capabilities"]),
                         ["artifact-processor", "db-service", "inbound-adapter"])
        self.assertEqual(m["capabilities"]["db-service"]["spec"], "db-service/dbservice.md")
        self.assertEqual(m["capabilities"]["artifact-processor"]["spec"],
                         "artifact-processor/arifact-processor.md")
        self.assertIn("PROJECT_MAP.md", [i["path"] for i in m["ignore"]])

    def test_prose_master_gets_own_section(self):
        e = SM.build(self.root)["capabilities"]["inbound-adapter"]
        self.assertEqual(e["form"], "prose")
        self.assertFalse(e["sections_present"])
        self.assertEqual(e["requirements_heading"], "Требования и сценарии")

    def test_modules_matched_by_name(self):
        caps = SM.build(self.root)["capabilities"]
        self.assertEqual(caps["inbound-adapter"]["modules"], ["service/inboundservice"])
        self.assertEqual(caps["db-service"]["modules"], ["service/dbservice"])
        self.assertEqual(caps["artifact-processor"]["modules"], [])

    def test_research_never_suggests_index_as_master_path(self):
        rc, out = self.cli("research")
        self.assertNotIn("spec_path 'PROJECT_MAP.md'", out)
        self.assertNotIn("config.py set docs.master.spec_path", out)

    def test_set_spec_outside_base_refused(self):
        self.cli("research")
        rc, out = self.cli("research", "--set", "db-service=../proj/ground/policy.json")
        self.assertEqual(rc, 2)
        self.assertIn("выходит за базу", out)

    def test_rescan_keeps_confirmation_when_structure_same(self):
        self.confirm()
        rc, _ = self.cli("research")
        self.assertEqual(rc, 0)
        self.assertEqual(SM.load(self.root)["status"], "confirmed")

    def test_rescan_drops_confirmation_on_new_service(self):
        self.confirm()
        (self.spec / "new-svc").mkdir()
        (self.spec / "new-svc" / "new-svc.md").write_text(PROSE, encoding="utf-8")
        self.assertEqual(self.cli("research")[0], 3)
        self.assertEqual(SM.load(self.root)["status"], "draft")


class MergeByMapTest(SpecMapBase):
    def test_no_map_refuses_and_writes_nothing(self):
        self.delta()
        before = self.inbound.read_text(encoding="utf-8")
        rc, out = self.cli("merge", "NPF-42", "-y", "--no-archive")
        self.assertEqual(rc, 3, out)
        self.assertIn("no-map", out)
        self.assertIn("/forge-spec research", out)
        self.assertEqual(self.inbound.read_text(encoding="utf-8"), before)
        self.assertFalse((self.spec / "specs").exists(), "шаблонный мастер не заводится")

    def test_draft_map_is_not_enough(self):
        self.delta()
        self.cli("research")
        rc, out = self.cli("merge", "NPF-42", "-y", "--no-archive")
        self.assertEqual(rc, 3)
        self.assertIn("--confirm", out)

    def test_missing_section_asks_then_ensure_writes_tail_only(self):
        self.delta()
        self.confirm()
        rc, out = self.cli("merge", "NPF-42", "-y", "--no-archive")
        self.assertEqual(rc, 3, out)
        self.assertIn("no-section", out)
        self.assertIn("--capability inbound-adapter --ensure-sections", out)
        self.assertEqual(self.inbound.read_text(encoding="utf-8"), PROSE)

        rc, out = self.cli("merge", "NPF-42", "-y", "--no-archive", "--ensure-sections")
        self.assertEqual(rc, 0, out)
        text = self.inbound.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(PROSE.rstrip("\n")), "проза выше не тронута")
        self.assertIn("## Требования и сценарии", text)
        self.assertIn("### REQ-0001: Метрика PENDING-очереди", text)
        self.assertIn("[from: NPF-42", text)
        self.assertIn("## Журнал изменений", text)
        for other in ("db-service/dbservice.md", "artifact-processor/arifact-processor.md"):
            self.assertEqual((self.spec / other).read_text(encoding="utf-8"), PROSE)
        m = SM.load(self.root)
        self.assertEqual(m["deltas"], {"NPF-42": "inbound-adapter"})
        self.assertTrue(m["capabilities"]["inbound-adapter"]["sections_present"])
        self.assertEqual(spec_cli.delta_state(self.root, "NPF-42"), "merged")

    def test_dry_run_with_ensure_writes_nothing(self):
        self.delta()
        self.confirm()
        rc, out = self.cli("merge", "NPF-42", "--dry-run", "--no-archive", "--ensure-sections")
        self.assertEqual(rc, 0, out)
        self.assertIn("будет дописан раздел", out)
        self.assertEqual(self.inbound.read_text(encoding="utf-8"), PROSE)

    def test_unknown_service_is_asked_then_remembered(self):
        self.delta(module="service/unrelated")
        self.confirm()
        rc, out = self.cli("merge", "NPF-42", "-y", "--no-archive")
        self.assertEqual(rc, 3, out)
        self.assertIn("no-capability", out)
        self.assertIn("--capability db-service", out)
        rc, out = self.cli("merge", "NPF-42", "-y", "--no-archive", "--ensure-sections",
                           "--capability", "db-service")
        self.assertEqual(rc, 0, out)
        self.assertIn("REQ-0001", (self.spec / "db-service" / "dbservice.md")
                      .read_text(encoding="utf-8"))
        self.assertEqual(SM.load(self.root)["deltas"]["NPF-42"], "db-service")
        rc, out = self.cli("status")
        self.assertIn("актуально: NPF-42", out)

    def test_unknown_capability_flag_is_error(self):
        self.delta()
        self.confirm()
        rc, out = self.cli("merge", "NPF-42", "-y", "--capability", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("сервиса «nope» в карте нет", out)

    def test_undecided_delta_holds_archive(self):
        self.delta(module="service/unrelated")
        self.confirm()
        self.assertEqual(spec_cli.delta_state(self.root, "NPF-42"), "unknown-format")

    def test_check_on_prose_master_uses_service_shape(self):
        """Гейт состава не требует форже-разделов от чужой прозы — только требования."""
        self.delta()
        self.confirm()
        self.cli("merge", "NPF-42", "-y", "--no-archive", "--ensure-sections")
        rc, out = self.cli("check", "--capability", "inbound-adapter")
        self.assertEqual(rc, 0, out)

    def test_single_master_commands_need_capability(self):
        self.confirm()
        rc, out = self.cli("check")
        self.assertEqual(rc, 2)
        self.assertIn("укажи --capability", out)


class LegacyUnaffectedTest(unittest.TestCase):
    """Однозначный мастер (в базе ничего, кроме настроенного пути) — как раньше, без карты."""

    def test_in_repo_native_merges_without_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "ground").mkdir()
            (root / "ground" / "policy.json").write_text(json.dumps({
                "project": {"name": "claims"},
                "docs": {"mode": "in-repo", "docs_path": "docs", "master": {"enabled": True}}}),
                encoding="utf-8")
            (root / "docs" / "architecture.md").parent.mkdir(parents=True)
            (root / "docs" / "architecture.md").write_text("# Арх\n", encoding="utf-8")
            d = root / "docs" / "feature-pipeline" / "x"
            d.mkdir(parents=True)
            (d / "sdd.md").write_text(SDD.format(module="m"), encoding="utf-8")
            spec_cli._GRAMMAR_CACHE.clear()
            rc, out = run("--project-root", str(root), "merge", "x", "-y", "--no-archive")
            spec_cli._GRAMMAR_CACHE.clear()
            self.assertEqual(rc, 0, out)
            self.assertTrue((root / "docs" / "specs" / "claims" / "spec.md").exists())


class StatementInsideScenarioTest(unittest.TestCase):
    """Короткое утверждение — подстрока сценария («Метрика» ⊂ «…When метрика…»).

    Разбор мастера выбрасывал его как «хвост сценария», и сразу после записи план видел
    `~ modify`: слитая дельта числилась разошедшейся, доки не уезжали."""

    def test_statement_survives(self):
        import spec_grammar as SG
        g = SG.native()
        body = ["Метрика  [from: NPF-42 2026-10-05]",
                "- **Given** очередь **When** метрика **Then** число"]
        self.assertEqual(g.statement_of(body), "Метрика")

    def test_scenario_continuation_still_dropped(self):
        import spec_grammar as SG
        g = SG.native()
        body = ["Утверждение.", "- **Given** a **When** b **Then** c"]
        self.assertEqual(g.statement_of(body), "Утверждение.")


class BriefsTest(unittest.TestCase):
    """Брифы ведут через карту и MCP, а не через подкрутку конфига и обходы рантайма."""

    COMMANDS = SCRIPT_DIR.parents[2] / "commands"

    def test_merge_brief(self):
        t = (self.COMMANDS / "forge-merge.md").read_text(encoding="utf-8")
        for must in ("master_merge", "forge-master", "no-map", "no-capability", "no-section",
                     "--ensure-sections", "spec-map.json", "стоп"):
            self.assertIn(must, t)
        self.assertIn("не собирай свои обёртки", t)

    def test_spec_brief(self):
        t = (self.COMMANDS / "forge-spec.md").read_text(encoding="utf-8")
        for must in ("--confirm", "--set", "--map-module", "master_research", "spec-map.json"):
            self.assertIn(must, t)
        self.assertNotIn("готовые команды `config.py set` — предложи их пользователю", t)


if __name__ == "__main__":
    unittest.main()
