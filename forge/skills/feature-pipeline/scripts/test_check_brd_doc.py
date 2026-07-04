#!/usr/bin/env python3
"""Tests for check_brd_doc.py — детерминированный гейт BRD (Thrust 3)."""
from __future__ import annotations

import unittest

import check_brd_doc as m

_GOOD = """# BRD: рассылка уведомлений клиентам

## Бизнес-контекст и предпосылки
Клиенты не получают уведомления о статусе заявки, растёт нагрузка на поддержку и отток.
Нужен канал проактивного информирования, чтобы снизить обращения и повысить удовлетворённость.

## Цели и ожидаемый результат
Снизить число обращений в поддержку по статусу заявки на 30%. Повысить прозрачность процесса
для клиента. Зачем: меньше ручной работы операторов, выше NPS.

## Требования и объём (scope)
Сценарий: при смене статуса заявки клиент получает уведомление выбранным каналом.
В объёме: email и push. Вне объёма: SMS. Роли: клиент, оператор.

## Критерии приёмки и метрики успеха
Given заявка сменила статус When событие обработано Then клиент получил уведомление в течение 1 минуты.
Метрика успеха: доля доставленных уведомлений >= 99%.
"""


class TestCheckBrdDoc(unittest.TestCase):
    def test_good_brd_passes(self):
        errors, _ = m.check(_GOOD)
        self.assertEqual(errors, [], errors)

    def test_short_stub_fails(self):
        errors, _ = m.check("# BRD\nнадо сделать рассылку")
        self.assertTrue(any("короткий" in e or "заглушка" in e for e in errors), errors)

    def test_code_fence_fails(self):
        bad = _GOOD + "\n```java\n@Service\npublic class NotifyService {}\n```\n"
        errors, _ = m.check(bad)
        self.assertTrue(any("код-блок" in e for e in errors), errors)

    def test_class_signature_fails(self):
        bad = _GOOD + "\nimport org.springframework.stereotype.Service;\npublic class X {}\n"
        errors, _ = m.check(bad)
        self.assertTrue(any("сигнатуры кода" in e for e in errors), errors)

    def test_sql_ddl_fails(self):
        bad = _GOOD + "\nCREATE TABLE notifications (id bigint primary key);\n"
        errors, _ = m.check(bad)
        self.assertTrue(any("SQL DDL" in e for e in errors), errors)

    def test_missing_section_fails(self):
        # без секции критериев приёмки
        no_criteria = _GOOD.split("## Критерии")[0]
        errors, _ = m.check(no_criteria)
        self.assertTrue(any("критери" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
