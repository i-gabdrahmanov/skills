#!/usr/bin/env python3
"""test_check_master_spec.py — тесты валидатора состава требований-мастера."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_master_spec as gate  # noqa: E402

BLOCKS = {
    "purpose": "## 1. Назначение и результат\nКапабилити закрывает потребность оператора.",
    "arch": ("## 2. Архитектурный контекст\nВнутренний сектор сети, бэковый компонент; "
             "границы доверия описаны, компонентов на устройствах клиента нет."),
    "scope": "## 3. Границы охвата\nВ рамках A; не в рамках B; отложено C.",
    "constraints": "## 4. Ограничения и допущения\nСтек Java 21 + Spring Boot 3.3; идемпотентно.",
    "requirements": "## 5. Требования (Requirements)\n- Требование 1  [from: f1 2026-01-01]",
    "scenarios": ("## 6. Сценарии (Given-When-Then)\n"
                  "- **Given** X **When** Y **Then** Z  [from: f1]"),
    "acceptance": "## 7. Критерии приёмки и верификация\n- Given валидно When принято Then есть аудит.",
    "threat": ("## 8. Модель угроз и безопасность\nДанные внутренние; threat surface мал; "
               "частная модель угроз ведётся."),
    "regulatory": "## 9. Регуляторные требования\nНе применимо: внутренний техпроцесс.",
    "audit": "## 10. Журнал изменений\n- 2026-01-01 — f1: слито сценариев 1",
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

    def test_full_passes_all_policies(self):
        p = self._write(full_master())
        for pol in ("hard", "applicability", "soft"):
            v = gate.check(p, pol)
            self.assertEqual(v["status"], "pass", f"{pol}: {v['errors']}")

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
            BLOCKS["threat"], "## 8. Модель угроз и безопасность\nне применимо: внутренний сервис.")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "pass")

    def test_audit_missing_warns_applicability_fails_hard(self):
        doc = full_master().replace(BLOCKS["audit"], "")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "pass")
        self.assertEqual(gate.check(self._write(doc), "hard")["status"], "fail")

    def test_code_block_fails(self):
        doc = full_master() + "\n```java\nclass Foo {}\n```\n"
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "fail")

    def test_no_gwt_fails(self):
        doc = full_master().replace(BLOCKS["scenarios"],
                                    "## 6. Сценарии (Given-When-Then)\nбез сценариев")
        doc = doc.replace(BLOCKS["acceptance"],
                          "## 7. Критерии приёмки и верификация\nчек-лист без сценариев")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("Given-When-Then" in e for e in v["errors"]))

    def test_policy_default_applicability(self):
        self.assertEqual(gate._load_policy(None, None), "applicability")


if __name__ == "__main__":
    unittest.main(verbosity=2)
