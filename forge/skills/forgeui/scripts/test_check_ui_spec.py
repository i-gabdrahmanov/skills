#!/usr/bin/env python3
"""test_check_ui_spec.py — гейт шага ui-spec (forge-ui): форма фронт-спеки стори.

Пинит: шапка с явной «Бэк-частью»; кадры только со ссылкой на макет и известной ролью; разделы
логики заполнены или явно «нет»; «Сценарии» разбираются; «Данные» называют источник; путь,
которого ещё нет в репо, — предупреждение, а не отказ; незаполненный шаблон гейт не проходит.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from daiver import uispec  # noqa: E402

SCRIPT = HERE / "check_ui_spec.py"
TEMPLATE = HERE.parent / "references" / "ui-spec-template.md"

GOOD = """# UI-спека: Подтверждение количества документов
**Jira:** STOR-100   **Статус:** draft   **Бэк-часть:** есть: STOR-100

## Назначение и границы
Модалка подтверждения количества документов в месте хранения перед сменой статуса.

## Кадры
| Имя | Роль | Макет | Маршрут | Как открыть |
|---|---|---|---|---|
| confirm-modal | dialog | https://pixso.example/app/design/abc?item-id=2:153702 | /storage | клик «Изменить статус» |

## Данные
types: `src/shared/api/storage.ts`, client: `src/shared/api/storage-client.ts`

## Состояния
- заполнено: количество документов и место хранения

## События
- «Подтвердить» → client.confirmCount, модалка закрывается
- «Отмена» → раскрывается список захваченных документов

## Валидации
нет

## Сценарии
1. **Given** модалка открыта для места 1-105_2-СПБ.1, **When** нажать «Подтвердить», **Then** модалка закрыта
2. **Given** модалка открыта, **When** нажать «Отмена», **Then** показан список захваченных документов

## Критерии приёмки
- экран совпадает с макетом (verify clean)
"""


def _check(text: str, project=None) -> dict:
    return uispec.check(text, "ui-spec.md", project)


class Good(unittest.TestCase):
    def test_good_spec_passes(self):
        res = _check(GOOD)
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual([s["id"] for s in res["scenarios"]], ["ui-spec#1", "ui-spec#2"])
        self.assertEqual(res["fields"]["back"], "есть: STOR-100")
        self.assertEqual(res["frames"][0]["name"], "confirm-modal")
        self.assertEqual(len(res["dataRefs"]), 2)

    def test_missing_path_is_warning_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = _check(GOOD, Path(tmp))
            self.assertTrue(res["ok"], res["errors"])
            self.assertEqual(len(res["warnings"]), 2)

    def test_presentational_screen_all_no(self):
        text = GOOD
        for sec in ("Данные", "Состояния", "События", "Сценарии"):
            start = text.index(f"## {sec}\n") + len(f"## {sec}\n")
            end = text.index("\n## ", start)
            text = text[:start] + "нет" + text[end:]
        res = _check(text)
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(res["scenarios"], [])


class Bad(unittest.TestCase):
    def assertFailsWith(self, text: str, needle: str):
        res = _check(text)
        self.assertFalse(res["ok"])
        self.assertTrue(any(needle in e for e in res["errors"]), res["errors"])

    def test_back_part_must_be_explicit(self):
        self.assertFailsWith(GOOD.replace("   **Бэк-часть:** есть: STOR-100", ""), "Бэк-часть")
        self.assertFailsWith(GOOD.replace("есть: STOR-100", "может быть"), "Бэк-часть")

    def test_status_enum(self):
        self.assertFailsWith(GOOD.replace("**Статус:** draft", "**Статус:** wip"), "Статус")

    def test_empty_logic_section(self):
        self.assertFailsWith(GOOD.replace("## Валидации\nнет\n", "## Валидации\n"), "Валидации")

    def test_missing_logic_section(self):
        self.assertFailsWith(GOOD.replace("## Валидации\nнет\n", ""), "Валидации")

    def test_frame_without_mockup(self):
        self.assertFailsWith(GOOD.replace("https://pixso.example/app/design/abc?item-id=2:153702", "TBD"),
                             "нет ссылки на макет")

    def test_frame_role(self):
        self.assertFailsWith(GOOD.replace("| dialog |", "| popup |"), "роль")

    def test_scenarios_not_parsed(self):
        broken = GOOD.replace("**Given** модалка открыта для места 1-105_2-СПБ.1, **When** нажать «Подтвердить», **Then** модалка закрыта",
                              "подтверждение работает").replace(
            "**Given** модалка открыта, **When** нажать «Отмена», **Then** показан список захваченных документов",
            "отмена работает")
        self.assertFailsWith(broken, "ни один сценарий")

    def test_data_names_source(self):
        self.assertFailsWith(GOOD.replace(
            "types: `src/shared/api/storage.ts`, client: `src/shared/api/storage-client.ts`",
            "данные приходят с бэка"), "источник")

    def test_mocks_are_a_source(self):
        res = _check(GOOD.replace(
            "types: `src/shared/api/storage.ts`, client: `src/shared/api/storage-client.ts`",
            "моки до готовности контракта"))
        self.assertTrue(res["ok"], res["errors"])

    def test_unfilled_template_fails(self):
        template = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)[1]
        res = _check(template)
        self.assertFalse(res["ok"])


class Cli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_exit_codes_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            story = Path(tmp) / "STOR-100"
            story.mkdir()
            (story / "ui-spec.md").write_text(GOOD, encoding="utf-8")
            r = self._run(str(story), "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(json.loads(r.stdout)["ok"])
            (story / "ui-spec.md").write_text(GOOD.replace("## Валидации\nнет\n", ""), encoding="utf-8")
            self.assertEqual(self._run(str(story)).returncode, 1)
            self.assertEqual(self._run(str(Path(tmp) / "nope")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
