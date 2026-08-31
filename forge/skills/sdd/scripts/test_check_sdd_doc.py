#!/usr/bin/env python3
"""test_check_sdd_doc.py — тесты валидатора состава SDD (check_sdd_doc.py).

Герметичны: работают во временном cwd, не пишут в REPO/ground (pollution-guard).
Exit 0 — всё зелёное, иначе unittest вернёт non-zero.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_sdd_doc as gate  # noqa: E402

SCRIPT = SCRIPT_DIR / "check_sdd_doc.py"

# --- Кирпичики документа -----------------------------------------------------

CORE_BLOCKS = {
    "purpose": "## 1. Назначение и результат\nФича закрывает потребность X для оператора.",
    "scope": "## 2. Границы охвата\n- В рамках: A. - Не в рамках: B. - Отложено: C.",
    "func": ("## 3. Функциональные требования (Given-When-Then)\n"
             "- **Given** заявка новая **When** оператор жмёт «принять» **Then** статус ACCEPTED."),
    "constraints": "## 4. Ограничения и допущения\nСтек: Java 21 + Spring Boot 3.3. Идемпотентно.",
    "api": "## 5. API-контракты\nPOST /api/v1/claims — тело ClaimRequest, ответ 201/400.",
    "data": "## 6. Модель данных\nТаблица claim: новое поле accepted_at (timestamp).",
    "acceptance": ("## 7. Критерии приёмки (Acceptance)\n"
                   "- Given валидная заявка When принята Then виден accepted_at."),
}
ARCH_BLOCK = ("## 8. Архитектурный контекст\nЖивёт в service-claims, за API-шлюзом; "
              "границы доверия: фронт в DMZ, бэк во внутреннем секторе.")
THREAT_BLOCK = ("## 9. Модель угроз и безопасность\nОбрабатываются ПДн (класс К2); "
                "threat surface — внешний REST; частная МУ ведётся отдельно.")
STORIES_BLOCK = ("## 10. Пользовательские истории и привилегированные сценарии\n"
                 "Как оператор, я принимаю заявку. Привилегированный: админ закрывает заявку (аудит).")
DECISIONS_BLOCK = "## 11. Принятые решения\nХраним у себя в PostgreSQL, а не проксируем."
REGULATORY_BLOCK = "## 12. Регуляторные требования\nСоответствие 152-ФЗ по обработке ПДн."


def full_doc() -> str:
    parts = ["# SDD: тестовая фича", "**Jira:** TEST-1", ""]
    parts += list(CORE_BLOCKS.values())
    parts += [ARCH_BLOCK, THREAT_BLOCK, STORIES_BLOCK, DECISIONS_BLOCK, REGULATORY_BLOCK]
    return "\n\n".join(parts) + "\n"


def core_plus_security_doc() -> str:
    """CORE + security/arch, но без contextual/regulatory (минимум под applicability)."""
    parts = ["# SDD: тестовая фича", "**Jira:** TEST-1", ""]
    parts += list(CORE_BLOCKS.values())
    parts += [ARCH_BLOCK, THREAT_BLOCK]
    return "\n\n".join(parts) + "\n"


class GateTest(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)  # герметичный cwd: нет ground/ → дефолтная политика

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _write(self, text: str) -> Path:
        p = Path(self._tmp.name) / "sdd.md"
        p.write_text(text, encoding="utf-8")
        return p

    # --- прямые вызовы check() ---

    def test_full_doc_passes_all_policies(self):
        p = self._write(full_doc())
        for pol in ("hard", "applicability", "soft"):
            v = gate.check(p, pol)
            self.assertEqual(v["status"], "pass", f"{pol}: {v['errors']}")

    def test_minimal_applicability_passes(self):
        v = gate.check(self._write(core_plus_security_doc()), "applicability")
        self.assertEqual(v["status"], "pass", v["errors"])
        # contextual/regulatory отсутствуют → предупреждения, но не ошибки
        self.assertTrue(v["warnings"])

    def test_missing_core_fails_every_policy(self):
        doc = full_doc().replace(CORE_BLOCKS["data"], "")
        for pol in ("hard", "applicability", "soft"):
            v = gate.check(self._write(doc), pol)
            self.assertEqual(v["status"], "fail", pol)
            self.assertTrue(any("модель данных" in e for e in v["errors"]))

    def test_missing_threat_model(self):
        doc = full_doc().replace(THREAT_BLOCK, "")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "fail")
        self.assertEqual(gate.check(self._write(doc), "hard")["status"], "fail")
        soft = gate.check(self._write(doc), "soft")
        self.assertEqual(soft["status"], "pass")
        self.assertTrue(any("модель угроз" in w for w in soft["warnings"]))

    def test_threat_marked_not_applicable_passes_applicability(self):
        doc = full_doc().replace(
            THREAT_BLOCK, "## 9. Модель угроз и безопасность\nне применимо: бэковый техсервис без ПДн.")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "pass")
        # hard тоже принимает NA для security-группы
        self.assertEqual(gate.check(self._write(doc), "hard")["status"], "pass")

    def test_empty_security_section_fails_applicability(self):
        doc = full_doc().replace(THREAT_BLOCK, "## 9. Модель угроз и безопасность\n")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("пуст" in e for e in v["errors"]))

    def test_missing_contextual_warns_under_applicability_fails_under_hard(self):
        doc = full_doc().replace(DECISIONS_BLOCK, "")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "pass")
        self.assertEqual(gate.check(self._write(doc), "hard")["status"], "fail")

    def test_code_block_fails(self):
        doc = full_doc() + "\n```java\nclass Foo {}\n```\n"
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("код-блок" in e for e in v["errors"]))

    def test_no_gwt_fails(self):
        doc = full_doc().replace(CORE_BLOCKS["func"],
                                 "## 3. Функциональные требования\nОписание без сценариев.")
        # acceptance тоже содержит GWT — уберём и его, чтобы не осталось ни одного
        doc = doc.replace(CORE_BLOCKS["acceptance"],
                          "## 7. Критерии приёмки (Acceptance)\nЧек-лист без сценариев.")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("Given-When-Then" in e for e in v["errors"]))

    # --- резолв политики ---

    def test_load_policy_default(self):
        # cwd — пустой tmp без ground/ → дефолт applicability
        self.assertEqual(gate._load_policy(None, None), "applicability")

    def test_load_policy_from_pipeline_config(self):
        cfg = Path(self._tmp.name) / "pipeline.json"
        cfg.write_text(json.dumps({"sdd": {"security_gate": "soft"}}), encoding="utf-8")
        self.assertEqual(gate._load_policy(cfg, None), "soft")

    def test_explicit_policy_overrides_config(self):
        cfg = Path(self._tmp.name) / "pipeline.json"
        cfg.write_text(json.dumps({"sdd": {"security_gate": "soft"}}), encoding="utf-8")
        self.assertEqual(gate._load_policy(cfg, "hard"), "hard")

    def test_load_policy_from_cwd_ground(self):
        ground = Path(self._tmp.name) / "ground"
        ground.mkdir()
        (ground / "pipeline.json").write_text(
            json.dumps({"sdd": {"security_gate": "hard"}}), encoding="utf-8")
        self.assertEqual(gate._load_policy(None, None), "hard")

    # --- CLI / exit-коды ---

    def test_cli_exit_codes(self):
        good = self._write(full_doc())
        r = subprocess.run([sys.executable, str(SCRIPT), str(good), "--json"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        bad_doc = full_doc().replace(THREAT_BLOCK, "")
        bad = self._write(bad_doc)
        # дефолт applicability → fail
        r = subprocess.run([sys.executable, str(SCRIPT), str(bad), "--json"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        # с --policy soft → pass
        r = subprocess.run([sys.executable, str(SCRIPT), str(bad), "--policy", "soft", "--json"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
