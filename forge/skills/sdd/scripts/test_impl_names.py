#!/usr/bin/env python3
"""test_impl_names.py — тесты линтера имён реализации в спеке (impl_names.py).

Две стороны одинаково важны: утечка ловится (иначе гейт декоративный) и законный язык спеки
НЕ ловится (иначе гейт валит корректный SDD, модель переписывает его по кругу и сжигает лимит
ре-итераций судьи — так уже было с поиском Given-When-Then, tasks/014).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import impl_names as L  # noqa: E402


def rules(text: str, **kw) -> "dict[str, list[str]]":
    out: dict[str, list[str]] = {}
    for h in L.find(text, **kw):
        out.setdefault(h["rule"], []).append(h["token"])
    return out


class LeaksAreCaught(unittest.TestCase):
    def assertCaught(self, text: str, rule: str, token: str):
        found = rules(text)
        self.assertIn(token, found.get(rule, []), f"{rule}: {text!r} → {found}")

    def test_method_calls_and_refs(self):
        self.assertCaught("сервис вызывает `ClaimService.accept()`", "method", "ClaimService.accept(")
        self.assertCaught("метод findByStatus(status) отдаёт список", "method", "findByStatus(")
        self.assertCaught("через claimRepository.save(claim)", "method", "claimRepository.save(")
        self.assertCaught("ссылка Claim::getId", "method", "Claim::getId")
        self.assertCaught("см. ClaimService#accept", "method", "ClaimService#accept")

    def test_layer_classes(self):
        for tok in ("ClaimService", "ClaimServiceImpl", "ClaimController", "ClaimRepository",
                    "ClaimMapper", "ClaimDto", "ClaimDTO", "ClaimNotFoundException",
                    "GlobalExceptionHandler", "ClaimEventListener", "KafkaConfig", "DateUtils"):
            self.assertCaught(f"логика в {tok}, дальше ответ", "class", tok)

    def test_framework_types(self):
        for tok in ("RestTemplate", "ResponseEntity", "WebClient", "KafkaTemplate"):
            self.assertCaught(f"вызов через {tok}", "class", tok)

    def test_packages_and_fqn(self):
        self.assertCaught("класс ru.bank.claims.ClaimService", "package", "ru.bank.claims.ClaimService")
        self.assertCaught("лежит в пакете ru.bank.claims.service.", "package", "ru.bank.claims.service")

    def test_source_files(self):
        self.assertCaught("правим ClaimController.java", "file", "ClaimController.java")
        self.assertCaught("в src/main/java/ru/bank", "file", "src/main/")

    def test_annotations_and_generics(self):
        self.assertCaught("метод под @Transactional", "annotation", "@Transactional")
        self.assertCaught("возвращает List<Claim>", "generic", "List<Claim>")
        self.assertCaught("ответ Map<String, Object>", "generic", "Map<String, Object>")

    def test_cyrillic_glued_to_name_is_still_a_name(self):
        """ASCII-границы: «вOrderService» — кириллица имя не маскирует."""
        self.assertCaught("логика вClaimService", "class", "ClaimService")


class SpecLanguageIsNotFlagged(unittest.TestCase):
    """Законный язык спеки: контракт, данные, топики, стек, ссылки."""

    CLEAN = """\
# SDD: приём заявок
**Jira:** CLM-1
**Spec ID:** claims-accept
**Фронт-часть:** нет

## 3. Функциональные требования (Given-When-Then)
- **Given** заявка новая **When** оператор жмёт «принять» **Then** статус ACCEPTED.

## 4. Ограничения и допущения
Стек: Java 21 + Spring Boot 3.3, PostgreSQL 15, Spring Data JPA, Kafka, OpenAPI 3,
Testcontainers, WireMock, Resilience4j CircuitBreaker, Keycloak (OAuth2, mTLS(взаимный)).

## 5. API-контракты
POST /api/v1/claims/{claimId}/accept — тело ClaimRequest {claimId, isUrgent, createdAt},
ответ 201 ClaimResponse; 404 CLAIM_NOT_FOUND, 409 CLAIM_ALREADY_ACCEPTED.
```json
{"claimId": "c-1", "isActive": true, "updatedBy": "op-7", "type": "ClaimAccepted"}
```
Kafka: топик ru.bank.claims.events.v1, ключ claimId, consumer group claims-consumer.

## 6. Модель данных
Сущность Claim, юрлицо LegalEntity, клиент CorporateClient; таблица claim: поле accepted_at
(timestamp, default now()), индекс по claim.status.

## 8. Архитектурный контекст
Живёт в claims-service за API Gateway (claims.svc.cluster.local); ЦФТ — через SOAP WebService.
Роль RelationshipManager подтверждает; InvestmentStrategy клиента не меняется.

## Реализация и задачи
См. ./tech-design.md и ./task-plan.json, якорь docs/feature-pipeline/<slug>/sdd.md#t1,
https://docs.spring.io/spring-boot/Reference. Владелец @team, почта ops@Bank.ru.
"""

    def test_clean_spec_has_no_hits(self):
        self.assertEqual(L.find(self.CLEAN), [])

    def test_bare_layer_word_is_prose(self):
        """Суффикс без префикса — слово, а не класс: «Service», «Repository», «Config»."""
        self.assertEqual(L.find("Service Repository Config Handler Dto Exception"), [])


class EntityFieldsAreSpecLanguage(unittest.TestCase):
    """Сущность.поле через точку — модель данных, а не код: и в SQL-, и в Java-формате.
    Линтер в первой версии валил схема.таблица.колонка как пакет и поле с типом в скобках
    как вызов метода."""

    FIELDS = (
        "task_upz.name", "taskUpz.name", "TaskUpz.name", "TASK_UPZ.NAME",
        "task_upz.status_id", "taskUpz.statusId", "taskUpz.department.name",
        "npf.task_upz.name", "npf.task_upz.NAME", "npf.task_upz.Name", "NPF.TASK_UPZ.NAME",
        "npf.task_upz.config", "task_upz.department.service", "npf.taskupz.NAME",
        "task_upz.name(varchar 255)", "task_upz.priority(int, not null)",
        "taskUpz.name(String)", "taskUpz.createdAt(LocalDateTime)",
        "taskUpz.priority(обязательное)",
    )

    def test_entity_fields_pass(self):
        for tok in self.FIELDS:
            with self.subTest(tok=tok):
                self.assertEqual(L.find(f"поле `{tok}` заполняется при создании"), [])
                self.assertEqual(L.find(f"| {tok} | Название задачи |"), [])

    def test_calls_and_packages_still_caught(self):
        """Послабление не открывает дыру: вызовы, пакеты и FQN валятся как раньше."""
        cases = (
            ("claimService.accept(UUID)", "method", "claimService.accept("),
            ("ClaimService.accept(String)", "method", "ClaimService.accept("),
            ("claim.accept()", "method", "claim.accept("),
            ("repo.save(claim)", "method", "repo.save("),
            ('Map.of("a", 1)', "method", "Map.of("),
            ("findByStatus(String status)", "method", "findByStatus("),
            ("ru.bank.claims.ClaimService", "package", "ru.bank.claims.ClaimService"),
            ("ru.bank.claims.service", "package", "ru.bank.claims.service"),
        )
        for text, rule, tok in cases:
            with self.subTest(text=text):
                self.assertIn(tok, rules(text).get(rule, []))


def _excerpt(tmp: Path, data: dict) -> Path:
    p = L.excerpt_path(tmp)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


INVENTORY = {
    "entities": [{"name": "Claim"}, {"name": "ClaimConfig"}],
    "tables": [{"name": "claim"}],
    "components": [{"name": "CftGateway", "layer": "component"},
                   {"name": "ClaimService", "layer": "service"},
                   {"name": "ClaimAcceptRequest", "layer": "dto"},
                   {"name": "Scheduler", "layer": "component"}],
    "api_endpoints": [{"method": "GET", "path": "/api/v1/claims", "handler": "getClaims"},
                      {"method": "POST", "path": "/api/v1/claims", "handler": "create"}],
    "external_clients": [{"name": "CftProxy"}],
    "reuse": {"project_utils": ["ru.bank.common.DateTimeSupport"]},
}


class InventoryLayer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.known, self.domain = L.load_inventory(_excerpt(Path(self._tmp.name), INVENTORY))

    def tearDown(self):
        self._tmp.cleanup()

    def test_known_names(self):
        """Компоненты (кроме dto), клиенты, утилиты, хендлеры; только составные имена."""
        self.assertEqual(self.known, {"CftGateway", "ClaimService", "getClaims", "CftProxy",
                                      "DateTimeSupport"})

    def test_names_without_layer_suffix_are_caught(self):
        found = rules("данные через CftGateway и CftProxy, метод getClaims, дата DateTimeSupport",
                      known=self.known, domain=self.domain)
        self.assertEqual(sorted(found.get("inventory", [])),
                         ["CftGateway", "CftProxy", "DateTimeSupport", "getClaims"])

    def test_dto_and_single_word_names_pass(self):
        """Схема запроса — контракт; «create»/«Scheduler» из одного слова — проза."""
        self.assertEqual(L.find("тело ClaimAcceptRequest; create заявки; Scheduler кластера",
                                known=self.known, domain=self.domain), [])

    def test_entity_is_exempt_even_with_layer_suffix(self):
        """Сущность из инвентаря — предметная область, даже если имя похоже на класс слоя."""
        self.assertEqual(L.find("настройки ClaimConfig", known=self.known, domain=self.domain), [])

    def test_entity_in_any_spelling_is_exempt(self):
        """grounding дал таблицу claim_config / entity TaskUpz — спека пишет любое написание."""
        tmp = Path(self._tmp.name) / "variants"
        known, domain = L.load_inventory(_excerpt(tmp, {
            "entities": [{"name": "TaskUpz"}],
            "tables": [{"name": "create_task_config"}],
            "api_endpoints": [{"handler": "taskCreate"}, {"handler": "taskUpz"}]}))
        self.assertEqual(known, {"taskCreate"})          # хендлер taskUpz = имя сущности
        text = ("CreateTaskConfig.name, createTaskConfig.name, CREATE_TASK_CONFIG.NAME, "
                "taskUpz.taskCreate, TASK_UPZ.TASK_CREATE")
        self.assertEqual(L.find(text, known=known, domain=domain), [])

    def test_field_of_entity_is_not_inventory_but_bare_name_is(self):
        """Поле сущности, совпавшее с хендлером, — поле; само имя хендлера или вызов — утечка."""
        domain = {"TaskUpz"}
        self.assertEqual(L.find("поле taskUpz.getClaims", known=self.known, domain=domain), [])
        self.assertEqual(rules("метод getClaims", known=self.known, domain=domain),
                         {"inventory": ["getClaims"]})
        self.assertIn("getClaims", rules("taskUpz.getClaims()", known=self.known,
                                         domain=domain).get("inventory", []))

    def test_form_hit_is_not_duplicated_by_inventory(self):
        found = rules("логика в ClaimService", known=self.known, domain=self.domain)
        self.assertEqual(found, {"class": ["ClaimService"]})

    def test_missing_or_broken_excerpt_gives_empty_inventory(self):
        self.assertEqual(L.load_inventory(None), (set(), set()))
        self.assertEqual(L.load_inventory(Path(self._tmp.name) / "nope.json"), (set(), set()))
        broken = Path(self._tmp.name) / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        self.assertEqual(L.load_inventory(broken), (set(), set()))
        broken.write_text("[]", encoding="utf-8")
        self.assertEqual(L.load_inventory(broken), (set(), set()))


ENTITY_JAVA = """package ru.bank.model;
import jakarta.persistence.*;
@Entity
@Table(name = "create_task_config")
public class CreateTaskConfig {
  @Id private Long id;
  private String name;
}
"""
CONFIG_JAVA = """package ru.bank.config;
@Configuration
public class KafkaConfig {
  private String topic;
}
"""


class _ProjectCase(unittest.TestCase):
    """Проект пайплайна во временном каталоге: ground/policy.json и KafkaConfig в коде."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ground").mkdir()
        (self.root / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        self._java("config/KafkaConfig.java", CONFIG_JAVA)

    def tearDown(self):
        self._tmp.cleanup()

    def _java(self, rel: str, body: str) -> None:
        p = self.root / "src" / "main" / "java" / "ru" / "bank" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def _lint(self, text: str):
        inv = L.inventory_for(self.root, text)
        return L.find(text, known=inv.known, domain=inv.domain, code=inv.code), inv.rebuilt


class EntitySuspicionIsCheckedAgainstCode(_ProjectCase):
    """Имя в форме класса (`CreateTaskConfig.name`) без сущности в grounding — подозрение:
    grounding пересобирается по коду, и сущность из кода проходит, а класс-компонент — нет."""

    def test_missing_grounding_is_built_and_entity_passes(self):
        self._java("model/CreateTaskConfig.java", ENTITY_JAVA)
        hits, added = self._lint("поле CreateTaskConfig.name обязательно")
        self.assertEqual((hits, added), ([], ["CreateTaskConfig"]))
        names = [e["name"] for e in json.loads(
            L.excerpt_path(self.root).read_text(encoding="utf-8"))["entities"]]
        self.assertIn("CreateTaskConfig", names)

    def test_stale_grounding_is_rebuilt(self):
        self._lint("KafkaConfig")                                    # grounding без сущности
        self.assertTrue(L.excerpt_path(self.root).is_file())
        self._java("model/CreateTaskConfig.java", ENTITY_JAVA)       # код ушёл вперёд
        hits, added = self._lint("CreateTaskConfig.name")
        self.assertEqual((hits, added), ([], ["CreateTaskConfig"]))

    def test_component_with_field_is_still_code(self):
        """`KafkaConfig.topic` — не сущность: сверка с кодом это подтверждает, утечка остаётся."""
        hits, added = self._lint("топик KafkaConfig.topic")
        self.assertEqual(([h["token"] for h in hits], added), (["KafkaConfig"], []))

    def test_no_pipeline_no_rebuild(self):
        """Голый ground/ (остаток логов) — не проект пайплайна: инвентарь гейт не заводит."""
        (self.root / "ground" / "policy.json").unlink()
        self._java("model/CreateTaskConfig.java", ENTITY_JAVA)
        hits, added = self._lint("CreateTaskConfig.name")
        self.assertEqual(([h["token"] for h in hits], added), (["CreateTaskConfig"], []))
        self.assertFalse((self.root / "ground" / "inventory").exists())

    def test_no_suspects_no_rebuild(self):
        self._lint("поле task_upz.name, эндпойнт POST /api/v1/claims")
        self.assertFalse((self.root / "ground" / "inventory").exists())

    def test_added_warning(self):
        self.assertEqual(L.added_warning([]), [])
        (w,) = L.added_warning(["CreateTaskConfig"])
        self.assertIn("`CreateTaskConfig`", w)


RECORD_APPROVAL = SCRIPT_DIR.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"


class AnalystDecidesUnknownObjects(_ProjectCase):
    """Имя в форме класса, которого нет ни среди сущностей, ни среди классов кода (новая
    сущность фичи или выдуманный класс), — не FAIL «перепиши», а список аналитику. Утверждённое
    им согласием spec-entity-<Имя> проходит; рукописная запись в журнале — нет."""

    TEXT = "новая сущность TaskUpzConfig: поле TaskUpzConfig.name; KafkaConfig.topic; RestTemplate"

    def _approve(self, name: str, evidence: "str | None" = "Да, все это сущности"):
        cmd = [sys.executable, str(RECORD_APPROVAL), "--project", str(self.root),
               "--key", L.ENTITY_APPROVAL_PREFIX + name, "--approved-by", "user",
               "--reason", "аналитик: сущность предметной области"]
        if evidence is not None:
            cmd += ["--evidence", evidence]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_unknown_class_shaped_name_goes_to_analyst(self):
        hits, _ = self._lint(self.TEXT)
        by_rule = {}
        for h in hits:
            by_rule.setdefault(h["rule"], []).append(h["token"])
        self.assertEqual(by_rule, {"entity": ["TaskUpzConfig"],
                                   "class": ["KafkaConfig", "RestTemplate"]},
                         "класс из кода и тип фреймворка аналитику не носим")
        self.assertEqual(L.pending_entities(hits), ["TaskUpzConfig"])
        (err,) = [e for e in L.errors(hits) if "аналитик" in e]
        self.assertIn("`TaskUpzConfig`", err)
        self.assertIn("spec-entity-<Имя>", err)

    def test_approved_name_passes_in_every_spelling(self):
        self.assertEqual(self._approve("TaskUpzConfig").returncode, 0)
        hits, _ = self._lint("TaskUpzConfig.name, taskUpzConfig.name, task_upz_config.name")
        self.assertEqual(hits, [])

    def test_approval_requires_users_quote(self):
        """Класс согласий с цитатой: без --evidence модель сама себе имя не освободит."""
        r = self._approve("TaskUpzConfig", evidence=None)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(L.pending_entities(self._lint("TaskUpzConfig")[0]), ["TaskUpzConfig"])

    def test_handwritten_approval_line_is_ignored(self):
        log = self.root / "ground" / "approvals.jsonl"
        log.write_text(json.dumps({"kind": "approval", "key": "spec-entity-TaskUpzConfig",
                                   "produced_by": "model"}) + "\n", encoding="utf-8")
        self.assertEqual(L.pending_entities(self._lint("TaskUpzConfig")[0]), ["TaskUpzConfig"])

    def test_outside_pipeline_nobody_to_ask(self):
        (self.root / "ground" / "policy.json").unlink()
        hits, _ = self._lint("TaskUpzConfig")
        self.assertEqual([h["rule"] for h in hits], ["class"])


class IgnoreAndMessages(unittest.TestCase):
    def test_ignore_drops_inherited_tokens_only(self):
        found = rules("ClaimService и ClaimMapper", ignore={"ClaimService"})
        self.assertEqual(found, {"class": ["ClaimMapper"]})

    def test_one_error_per_rule_with_lines_and_single_hint(self):
        text = "строка\nClaimService\nfindByStatus(x)\nClaimMapper\n"
        errs = L.errors(L.find(text))
        self.assertEqual(len(errs), 2, errs)            # method + class
        self.assertIn("стр. 3 `findByStatus(`", errs[0])
        self.assertIn("стр. 2 `ClaimService`", errs[1])
        self.assertIn("стр. 4 `ClaimMapper`", errs[1])
        self.assertEqual(sum("tech-design" in e for e in errs), 1, errs)

    def test_samples_are_capped(self):
        text = "\n".join(f"Claim{i}Service" for i in range(L.MAX_SAMPLES + 3))
        (err,) = L.errors(L.find(text))
        self.assertIn("и ещё 3", err)

    def test_no_hits_no_errors(self):
        self.assertEqual(L.errors([]), [])


if __name__ == "__main__":
    unittest.main()
