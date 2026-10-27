#!/usr/bin/env python3
"""test_impl_names.py — тесты линтера имён реализации в спеке (impl_names.py).

Две стороны одинаково важны: утечка ловится (иначе гейт декоративный) и законный язык спеки
НЕ ловится (иначе гейт валит корректный SDD, модель переписывает его по кругу и сжигает лимит
ре-итераций судьи — так уже было с поиском Given-When-Then, tasks/014).
"""
from __future__ import annotations

import json
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
