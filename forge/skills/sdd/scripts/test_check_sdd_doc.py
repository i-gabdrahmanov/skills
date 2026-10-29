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
    parts = ["# SDD: тестовая фича", "**Jira:** TEST-1", "**Фронт-часть:** нет", ""]
    parts += list(CORE_BLOCKS.values())
    parts += [ARCH_BLOCK, THREAT_BLOCK, STORIES_BLOCK, DECISIONS_BLOCK, REGULATORY_BLOCK]
    return "\n\n".join(parts) + "\n"


def core_plus_security_doc() -> str:
    """CORE + security/arch, но без contextual/regulatory (минимум под applicability)."""
    parts = ["# SDD: тестовая фича", "**Jira:** TEST-1", "**Фронт-часть:** нет", ""]
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

    def test_section_written_as_subsections_is_not_empty(self):
        """Раздел, расписанный подразделами, — НЕ пустой.

        Регрессия: тело раздела обрывалось на любом следующем заголовке, поэтому
        «## 5. API-контракты» → «### 5.1 POST /claims …» имел пустое тело и судья валил
        корректный SDD с «раздел пуст». Модель дописывала текст, снова получала fail —
        и сжигала лимит ре-итераций."""
        doc = full_doc().replace(
            THREAT_BLOCK,
            "## 9. Модель угроз и безопасность\n\n"
            "### 9.1 Обрабатываемые данные\nПДн класса К2: ФИО, телефон.\n\n"
            "### 9.2 Threat surface\nВнешний REST за шлюзом; перебор закрыт rate-limit.",
        )
        for pol in ("hard", "applicability", "soft"):
            v = gate.check(self._write(doc), pol)
            self.assertEqual(v["status"], "pass", f"{pol}: {v['errors']}")

    def test_core_section_written_as_subsections_is_not_empty(self):
        """То же для CORE-группы (она жёсткая при любой политике)."""
        doc = full_doc().replace(
            CORE_BLOCKS["api"],
            "## 5. API-контракты\n\n"
            "### 5.1 POST /api/v1/claims\nТело ClaimRequest, ответ 201/400, авторизация JWT.",
        )
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "pass", v["errors"])

    def test_truly_empty_section_still_fails(self):
        """Обратная сторона: раздел с ПУСТЫМИ подразделами по-прежнему валится."""
        doc = full_doc().replace(
            THREAT_BLOCK,
            "## 9. Модель угроз и безопасность\n\n### 9.1\n\n### 9.2\n",
        )
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("пуст" in e for e in v["errors"]), v["errors"])

    def test_na_inside_subsection_counts(self):
        """«не применимо» в подразделе засчитывается — тело раздела включает подразделы."""
        doc = full_doc().replace(
            REGULATORY_BLOCK,
            "## 12. Регуляторные требования\n\n### 12.1\nне применимо: внутренний инструмент.",
        )
        self.assertEqual(gate.check(self._write(doc), "hard")["status"], "pass")

    def test_generic_marker_does_not_grab_document_title(self):
        """Общий маркер «api» не должен цепляться за заголовок документа вместо раздела."""
        doc = full_doc().replace("# SDD: тестовая фича", "# SDD: API приёма заявок")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "pass", v["errors"])
        self.assertEqual(
            gate._find_body(gate._parse_sections(doc), ["api-контракт", "api"]).strip(),
            "POST /api/v1/claims — тело ClaimRequest, ответ 201/400.",
        )

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

    def test_front_part_field_is_required(self):
        """Стори бывает бэк, фронт или обе: связь с фронт-частью — явное поле шапки."""
        doc = full_doc().replace("**Фронт-часть:** нет", "")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("Фронт-часть" in e for e in v["errors"]))
        doc = full_doc().replace("**Фронт-часть:** нет", "**Фронт-часть:** может быть")
        self.assertEqual(gate.check(self._write(doc), "applicability")["status"], "fail")
        doc = full_doc().replace("**Фронт-часть:** нет", "**Фронт-часть:** есть: TEST-1")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "pass", v["errors"])

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

    # --- имена из кода (impl_names) ---

    def test_class_and_method_names_fail(self):
        doc = full_doc().replace(
            "**Then** статус ACCEPTED.",
            "**Then** `ClaimService.accept()` ставит статус ACCEPTED.")
        v = gate.check(self._write(doc), "applicability")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("ClaimService.accept(" in e for e in v["errors"]), v["errors"])
        self.assertEqual({h["rule"] for h in v["impl_names"]}, {"method", "class"})

    def test_inventory_names_fail_via_project_root(self):
        """Имя без суффикса слоя ловится только по инвентарю — и инвентарь ищется в корне
        проекта, переданном явно (run_judge передаёт --project-root), а не от cwd."""
        proj = Path(self._tmp.name) / "proj"
        inv = proj / "ground" / "inventory"
        inv.mkdir(parents=True)
        (inv / "grounding-excerpt.json").write_text(json.dumps(
            {"components": [{"name": "CftGateway", "layer": "component"}]}), encoding="utf-8")
        doc = full_doc().replace("Живёт в service-claims", "Живёт в service-claims, ходит в CftGateway")
        path = self._write(doc)
        self.assertEqual(gate.check(path, "applicability")["status"], "pass")   # cwd: инвентаря нет
        r = subprocess.run([sys.executable, str(SCRIPT), str(path), "--json",
                            "--project-root", str(proj)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("CftGateway", r.stdout)

    def test_entity_with_layer_suffix_passes_after_grounding_rebuild(self):
        """`CreateTaskConfig.name` при устаревшем grounding: гейт сверяется с кодом, пересобирает
        grounding и пропускает сущность, сообщая об этом предупреждением."""
        proj = Path(self._tmp.name) / "proj"
        (proj / "ground").mkdir(parents=True)
        (proj / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        src = proj / "src" / "main" / "java" / "CreateTaskConfig.java"
        src.parent.mkdir(parents=True)
        src.write_text("@Entity\n@Table(name = \"create_task_config\")\n"
                       "public class CreateTaskConfig { private String name; }\n",
                       encoding="utf-8")
        doc = full_doc().replace("**Then** статус ACCEPTED.",
                                 "**Then** заполнено поле CreateTaskConfig.name.")
        v = gate.check(self._write(doc), "applicability", project_root=proj)
        self.assertEqual(v["impl_names"], [], v["errors"])
        self.assertTrue(any("CreateTaskConfig" in w for w in v["warnings"]), v["warnings"])

    def test_unknown_object_waits_for_analyst_only_when_nothing_else_fails(self):
        """Имя в форме класса, которого нет ни среди сущностей, ни в коде, — вопрос аналитику.
        `awaiting_analyst` — только когда спека во всём остальном готова: иначе это обычная
        ре-итерация писателя (run_judge её засчитывает)."""
        proj = Path(self._tmp.name) / "proj"
        (proj / "ground").mkdir(parents=True)
        (proj / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        doc = full_doc().replace("**Then** статус ACCEPTED.",
                                 "**Then** заполнено поле TaskUpzConfig.name.")
        v = gate.check(self._write(doc), "applicability", project_root=proj)
        self.assertEqual(v["status"], "fail")
        self.assertEqual((v["pending_entities"], v["awaiting_analyst"]), (["TaskUpzConfig"], True))

        doc = doc.replace("**Then** заполнено", "`findByStatus(x)` **Then** заполнено")
        v = gate.check(self._write(doc), "applicability", project_root=proj)
        self.assertEqual((v["pending_entities"], v["awaiting_analyst"]), (["TaskUpzConfig"], False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
