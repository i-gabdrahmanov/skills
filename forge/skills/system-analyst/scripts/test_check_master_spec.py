#!/usr/bin/env python3
"""test_check_master_spec.py — тесты валидатора состава требований-мастера."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_master_spec as gate  # noqa: E402

REQ_1 = ("### REQ-0001: Экспорт отчёта оператору\n"
         "Система формирует отчёт по заявкам за период и отдаёт его оператору."
         "  [from: report-export 2026-01-01]\n"
         "- **Given** есть заявки за период **When** оператор запросил отчёт **Then** отчёт сформирован")
REQ_2 = ("### REQ-0002: Отказ при пустом периоде\n"
         "Система отклоняет запрос отчёта с пустым периодом.  [from: report-export 2026-01-01]\n"
         "- **Given** период пуст **When** оператор запросил отчёт **Then** запрос отклонён 400")

BLOCKS = {
    "purpose": "## 1. Назначение и результат\nКапабилити закрывает потребность оператора.",
    "arch": ("## 2. Архитектурный контекст\nВнутренний сектор сети, бэковый компонент; "
             "границы доверия описаны, компонентов на устройствах клиента нет."),
    "scope": "## 3. Границы охвата\nВ рамках A; не в рамках B; отложено C.",
    "constraints": "## 4. Ограничения и допущения\nСтек Java 21 + Spring Boot 3.3; идемпотентно.",
    "requirements": ("## 5. Требования и сценарии (Requirements)\n"
                     "Накопленный перечень требований капабилити.\n\n" + REQ_1 + "\n\n" + REQ_2),
    "acceptance": "## 6. Критерии приёмки и верификация\n- Отчёт подтверждён интеграционным тестом.",
    "threat": ("## 7. Модель угроз и безопасность\nДанные внутренние; threat surface мал; "
               "частная модель угроз ведётся."),
    "regulatory": "## 8. Регуляторные требования\nНе применимо: внутренний техпроцесс.",
    "audit": "## 9. Журнал изменений\n- 2026-01-01 — report-export: слито требований 2",
}


def full_master() -> str:
    head = "# Master Spec: claims\n**Spec ID:** r1\n**Статус:** living\n"
    return head + "\n\n" + "\n\n".join(BLOCKS.values()) + "\n"


class MasterGateTest(unittest.TestCase):
    def setUp(self):
        self._old = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old)
        self._tmp.cleanup()

    def _write(self, text: str) -> Path:
        p = Path(self._tmp.name) / "spec.md"
        p.write_text(text, encoding="utf-8")
        return p

    # ── состав разделов ────────────────────────────────────────────────
    def test_full_passes_all_policies(self):
        p = self._write(full_master())
        for pol in ("hard", "applicability", "soft"):
            v = gate.check(p, pol)
            self.assertEqual(v["status"], "pass", f"{pol}: {v['errors']}")
        self.assertEqual(gate.check(p, "applicability")["requirements"], 2)

    def test_missing_core_requirements_fails(self):
        doc = full_master().replace(BLOCKS["requirements"], "")
        for pol in ("hard", "applicability", "soft"):
            v = gate.check(self._write(doc), pol)
            self.assertEqual(v["status"], "fail", pol)

    def test_missing_threat_model(self):
        doc = full_master().replace(BLOCKS["threat"], "")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "fail")
        self.assertEqual(gate.check(self._write(doc), "hard")["status"], "fail")
        soft = gate.check(self._write(doc), "soft")
        self.assertEqual(soft["status"], "pass")
        self.assertTrue(any("модель угроз" in w for w in soft["warnings"]))

    def test_threat_na_passes_applicability(self):
        doc = full_master().replace(
            BLOCKS["threat"], "## 7. Модель угроз и безопасность\nне применимо: внутренний сервис.")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "pass")

    def test_audit_missing_warns_applicability_fails_hard(self):
        doc = full_master().replace(BLOCKS["audit"], "")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "pass")
        self.assertEqual(gate.check(self._write(doc), "hard")["status"], "fail")

    def test_code_block_fails(self):
        doc = full_master() + "\n```java\nclass Foo {}\n```\n"
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "fail")

    # ── требования с ID ────────────────────────────────────────────────
    def test_no_requirements_fails(self):
        doc = full_master().replace(REQ_1, "").replace(REQ_2, "")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("REQ-NNNN" in e for e in v["errors"]))

    def test_flat_legacy_format_fails_with_migrate_hint(self):
        """Плоский мастер старого формата: раздел есть, требований с ID нет."""
        doc = full_master().replace(
            BLOCKS["requirements"],
            "## 5. Требования (Requirements)\n- Экспорт отчёта  [from: f1 2026-01-01]")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("migrate" in e for e in v["errors"]))

    def test_requirement_without_scenario_fails(self):
        doc = full_master().replace(
            "- **Given** период пуст **When** оператор запросил отчёт **Then** запрос отклонён 400",
            "")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("REQ-0002" in e and "Given-When-Then" in e for e in v["errors"]))

    def test_scenario_floor_off_allows_requirement_without_scenario(self):
        doc = full_master().replace(
            "- **Given** период пуст **When** оператор запросил отчёт **Then** запрос отклонён 400",
            "")
        v = gate.check(self._write(doc), "applicability", scenario_floor=False)
        self.assertEqual(v["status"], "pass", v["errors"])

    def test_requirement_without_statement_fails(self):
        """Требование из одних сценариев, без проверяемого утверждения."""
        doc = full_master().replace(
            "Система отклоняет запрос отчёта с пустым периодом.  [from: report-export 2026-01-01]\n",
            "")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("REQ-0002" in e and "утверждения" in e for e in v["errors"]))

    def test_duplicate_id_fails(self):
        doc = full_master().replace("### REQ-0002:", "### REQ-0001:")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("REQ-0001" in e and "уникальны" in e for e in v["errors"]))

    def test_custom_id_prefix(self):
        doc = full_master().replace("REQ-", "KE-")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "fail")
        v = gate.check(self._write(doc), "applicability", id_prefix="KE")
        self.assertEqual(v["status"], "pass", v["errors"])

    def test_parse_requirements_body_stops_at_next_heading(self):
        reqs = gate._parse_requirements(full_master(), "REQ")
        self.assertEqual([r["id"] for r in reqs], ["REQ-0001", "REQ-0002"])
        self.assertNotIn("Критерии приёмки", reqs[-1]["body"])
        self.assertIn("период пуст", reqs[-1]["body"])

    # ── ручки конфига ──────────────────────────────────────────────────
    def test_policy_default_applicability(self):
        self.assertEqual(gate._load_policy(None, None), "applicability")

    def test_spec_opts_defaults(self):
        self.assertEqual(gate._load_spec_opts(None, None, None), ("REQ", True))

    def test_spec_opts_from_pipeline_config(self):
        cfg = Path(self._tmp.name) / "pipeline.json"
        cfg.write_text(json.dumps({"spec": {"id_prefix": "KE", "scenario_floor": False}}),
                       encoding="utf-8")
        self.assertEqual(gate._load_spec_opts(cfg, None, None), ("KE", False))

    def test_spec_opts_cli_wins(self):
        cfg = Path(self._tmp.name) / "pipeline.json"
        cfg.write_text(json.dumps({"spec": {"id_prefix": "KE"}}), encoding="utf-8")
        self.assertEqual(gate._load_spec_opts(cfg, "REQ", None), ("REQ", True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
