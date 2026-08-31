#!/usr/bin/env python3
"""Тесты charset_hygiene.py + врезки charset-floor в судьи run_judge.

Скрипт ловит китайские/CJK-символы (блок) и текстовый мусор (mojibake/zero-width/homoglyph,
warn), которые слабая модель вставляет в спеки. Правописание валидной кириллицы — на LLM-судье.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charset_hygiene as ch
import run_judge as rj

# Валидный BRD со всеми обязательными секциями (>400 знаков) — чтобы единственным блоком
# в check_brd оставался CJK, а не структура. Совпадает по духу с test_check_brd_doc._GOOD.
_GOOD_BRD = """# BRD: рассылка уведомлений клиентам

## Бизнес-контекст и предпосылки
Клиенты не получают уведомления о статусе заявки, растёт нагрузка на поддержку и отток.
Нужен канал проактивного информирования, чтобы снизить обращения и повысить удовлетворённость.

## Цели и ожидаемый результат
Снизить число обращений в поддержку по статусу заявки на 30%. Повысить прозрачность процесса.

## Требования и объём (scope)
Сценарий: при смене статуса заявки клиент получает уведомление выбранным каналом.
В объёме: email и push. Вне объёма: SMS. Роли: клиент, оператор.

## Критерии приёмки и метрики успеха
Given заявка сменила статус When событие обработано Then клиент получил уведомление за минуту.
"""


class TestScan(unittest.TestCase):
    def test_cjk_blocks(self):
        errors, _ = ch.scan("Сервис 实现订单服务 обрабатывает заказы")
        self.assertTrue(any("CJK" in e for e in errors), errors)

    def test_hiragana_katakana_hangul_fullwidth_block(self):
        for bad in ("текст ひらがな", "текст カタカナ", "текст 한글", "текст ｆｕｌｌｗｉｄｔｈ"):
            errors, _ = ch.scan(bad)
            self.assertTrue(errors, f"не заблокировано: {bad!r}")

    def test_clean_ru_en_passes(self):
        text = ("Сервис OrderService обрабатывает заказы через REST-эндпоинт /orders.\n"
                "`OrderRepository` из пакета orders. Термины: DTO, Kafka, Liquibase.")
        errors, warnings = ch.scan(text)
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)

    def test_mixed_script_warns(self):
        # латинская 'a' внутри русского слова
        errors, warnings = ch.scan("Система выполняет рaсчёт стоимости")
        self.assertEqual(errors, [])
        self.assertTrue(any("смешанной" in w for w in warnings), warnings)

    def test_mixed_script_ignores_code_fence_and_inline(self):
        # кириллица внутри кода/URL не должна считаться homoglyph-словом
        text = ("Пример: ```java\nclass Расчёт {}\n``` и `методРусский()` и http://тест.рф/путь")
        _errors, warnings = ch.scan(text)
        self.assertFalse(any("смешанной" in w for w in warnings), warnings)

    def test_mojibake_and_zero_width_warn(self):
        errors, warnings = ch.scan("Заказ� оформлен​ успешно")
        self.assertEqual(errors, [])
        self.assertTrue(any("U+FFFD" in w for w in warnings), warnings)
        self.assertTrue(any("zero-width" in w or "невидим" in w for w in warnings), warnings)

    def test_cfg_off_disables_cjk(self):
        errors, warnings = ch.scan("订单", {"cjk": "off"})
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_cfg_cjk_as_warn(self):
        errors, warnings = ch.scan("订单", {"cjk": "warn"})
        self.assertEqual(errors, [])
        self.assertTrue(warnings)

    def test_enabled_false_short_circuits(self):
        errors, warnings = ch.scan("订单 рaсчёт", {"enabled": False})
        self.assertEqual((errors, warnings), ([], []))


class TestCharsetFloor(unittest.TestCase):
    def _write(self, name: str, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_clean_file_passes(self):
        p = self._write("sdd.md", "Чистый русский текст без иероглифов и мусора.")
        checks, blocking, _w = rj._charset_floor([p], {})
        self.assertEqual(blocking, [])
        self.assertTrue(any(c["status"] == "PASS" for c in checks))

    def test_cjk_file_blocks(self):
        p = self._write("sdd.md", "Сервис 订单 обрабатывает заказы")
        checks, blocking, _w = rj._charset_floor([p], {})
        self.assertTrue(blocking)
        self.assertTrue(any("CJK" in b for b in blocking))
        self.assertTrue(any(c["status"] == "FAIL" for c in checks))

    def test_missing_file_skipped(self):
        checks, blocking, warnings = rj._charset_floor([Path("/nope/missing.md")], {})
        self.assertEqual((checks, blocking, warnings), ([], [], []))

    def test_disabled_gate_skips(self):
        p = self._write("sdd.md", "订单")
        checks, blocking, _w = rj._charset_floor([p], {"charset_gate": {"enabled": False}})
        self.assertEqual(blocking, [])
        self.assertEqual(checks, [])

    def test_filename_prefixes_findings(self):
        p = self._write("tech-design.md", "订单")
        _checks, blocking, _w = rj._charset_floor([p], {})
        self.assertTrue(any(b.startswith("tech-design.md:") for b in blocking), blocking)


class TestJudgeWiring(unittest.TestCase):
    """CJK в документе валит соответствующего судью (проверка врезки floor)."""

    def _project(self) -> Path:
        root = Path(tempfile.mkdtemp())
        rj._set_paths(root, skill="feature-pipeline")
        return root

    def test_check_brd_blocks_cjk(self):
        root = self._project()
        feat = rj.FEATURE_DOCS_DIR / "feat-x"
        feat.mkdir(parents=True)
        (feat / "brd.md").write_text(_GOOD_BRD + "\nОтдельно: 订单 сервис.\n", encoding="utf-8")
        verdict = rj.check_brd("feat-x", feat)
        self.assertFalse(verdict["passed"], verdict["summary"])
        self.assertTrue(any("CJK" in b for b in verdict["blocking_issues"]),
                        verdict["blocking_issues"])

    def test_check_brd_clean_passes(self):
        root = self._project()
        feat = rj.FEATURE_DOCS_DIR / "feat-y"
        feat.mkdir(parents=True)
        (feat / "brd.md").write_text(_GOOD_BRD, encoding="utf-8")
        verdict = rj.check_brd("feat-y", feat)
        self.assertTrue(verdict["passed"], verdict["blocking_issues"])

    def test_check_sdd_doc_blocks_cjk(self):
        root = self._project()
        feat = rj.FEATURE_DOCS_DIR / "feat-z"
        feat.mkdir(parents=True)
        (feat / "sdd.md").write_text("# SDD\nОписание. 订单 服务.\n", encoding="utf-8")
        verdict = rj.check_sdd_doc("feat-z", feat)
        self.assertFalse(verdict["passed"])
        self.assertTrue(any("CJK" in b for b in verdict["blocking_issues"]),
                        verdict["blocking_issues"])


if __name__ == "__main__":
    unittest.main()
