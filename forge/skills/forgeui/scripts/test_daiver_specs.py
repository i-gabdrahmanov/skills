#!/usr/bin/env python3
"""test_daiver_specs.py — парсер спек бизнес-логики (daiver_spec, спека §11.2; INV-B19, B20, C36).

Пинит: пять разделов и русские алиасы на любом уровне заголовка; диалект OpenSpec
(Requirement:/Scenario:) и Given-When-Then строкой или блоком; контекст REMOVED выводит сценарий из
e2e; id `<stem>#<n>` и разведение совпавших стемов; «ноль спек» — нормальный исход, а
зарегистрированный и отсутствующий файл — предупреждение; упоминание формата — не сценарий.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daiver import specs  # noqa: E402

OPENSPEC = """# Promo
## ADDED Requirements
### Requirement: Промо-баннер
Баннер показывается на главной.
#### Scenario: Показ баннера
- GIVEN пользователь на главной
- WHEN промо активно
- THEN виден баннер

## REMOVED Requirements
### Requirement: Старый попап
#### Scenario: Попап при входе
- GIVEN пользователь вошёл
- WHEN прошло 3 секунды
- THEN показан попап
"""


class Sections(unittest.TestCase):
    def test_aliases_any_level_case_and_colon(self):
        text = ("# x\n### данные:\ntypes: `src/a.ts`\n#### STATES\nзагрузка\n"
                "## События\nклик\n## Validations:\nнет\n##### Сценарии\nнет\n")
        res = specs.parse_text(text, "x.md", "x")
        for sec in ("data", "states", "events", "validations", "e2e"):
            self.assertEqual(len(res["sections"][sec]), 1, sec)
        self.assertTrue(res["sections"]["validations"][0]["empty"], "«нет» — явный пустой раздел")
        self.assertEqual(res["dataRefs"], [{"kind": "types", "path": "src/a.ts", "file": "x.md"}])

    def test_block_closes_on_same_or_higher_level(self):
        text = "## Данные\nмоки\n### подробности\nещё\n## Прочее\nпроза\n"
        res = specs.parse_text(text, "x.md", "x")
        self.assertIn("ещё", res["sections"]["data"][0]["body"])
        self.assertNotIn("проза", res["sections"]["data"][0]["body"])
        self.assertEqual([p["heading"] for p in res["prose"]], ["Прочее"])

    def test_comments_are_not_content(self):
        text = "## Сценарии\n<!-- Given a, When b, Then c -->\n"
        res = specs.parse_text(text, "x.md", "x")
        self.assertEqual(res["e2e"], [])
        self.assertEqual(res["sections"]["e2e"][0]["body"], "")


class Scenarios(unittest.TestCase):
    def test_openspec_blocks_and_removed_context(self):
        res = specs.parse_text(OPENSPEC, "spec.md", "promo")
        self.assertEqual([r["title"] for r in res["requirements"]], ["Промо-баннер", "Старый попап"])
        self.assertEqual([s["title"] for s in res["e2e"]], ["Показ баннера", "Попап при входе"])
        self.assertEqual([s["id"] for s in res["e2e"]], ["promo#1", "promo#2"])
        self.assertEqual([s["removed"] for s in res["e2e"]], [False, True],
                         "сценарий под REMOVED описывает удаляемое поведение")
        self.assertIn("ADDED Requirements", res["e2e"][0]["context"])

    def test_gwt_line_and_block(self):
        text = ("## Сценарии\n"
                "1. **Given** модалка открыта, **When** нажать «Отмена», **Then** модалка закрыта\n"
                "\n"
                "Дано список пуст\nКогда открыт экран\nТогда показана заглушка\n")
        res = specs.parse_text(text, "ui-spec.md", "ui-spec")
        self.assertEqual([s["id"] for s in res["e2e"]], ["ui-spec#1", "ui-spec#2"])
        self.assertIn("Тогда показана заглушка", res["e2e"][1]["body"])

    def test_format_mention_is_not_a_scenario(self):
        text = "4. **Сценарии** — Given/When/Then (или Дано/Когда/Тогда), одной строкой\n"
        self.assertEqual(specs.parse_text(text, "x.md", "x")["e2e"], [])

    def test_block_without_then_is_not_a_scenario(self):
        text = "Given что-то\nAnd ещё\n"
        self.assertEqual(specs.parse_text(text, "x.md", "x")["e2e"], [])


class Files(unittest.TestCase):
    def test_stem_collisions_get_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "openspec" / "specs" / "promo" / "spec.md"
            b = root / "openspec" / "specs" / "cart" / "spec.md"
            for p in (a, b):
                p.parent.mkdir(parents=True)
                p.write_text("## Сценарии\nGiven x, When y, Then z\n", encoding="utf-8")
            res = specs.parse_files([a, b], root=root)
            self.assertEqual(sorted(s["id"] for s in res["e2e"]), ["cart-spec#1", "promo-spec#1"])

    def test_stem_strips_spec_suffix(self):
        self.assertEqual(specs.stem_of(Path("promo.spec.md")), "promo")
        self.assertEqual(specs.stem_of(Path("ui-spec.md")), "ui-spec")
        self.assertEqual(specs.stem_of(Path("spec.md")), "spec")

    def test_no_specs_is_normal_and_missing_registered_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            res = specs.parse_files([], root=root)
            self.assertFalse(res["hasSpecs"])
            self.assertEqual(res["warnings"], [], "ноль спек — нормальный исход, без предупреждений")
            res = specs.parse_files([], registered=[root / "gone.md"], root=root)
            self.assertEqual(len(res["warnings"]), 1)
            self.assertFalse(res["files"][0]["exists"])

    def test_unknown_format_degrades_to_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "notes.md"
            p.write_text("# Заметки\n## Что-то своё\nтекст\n", encoding="utf-8")
            res = specs.parse_files([p], root=Path(tmp))
            self.assertFalse(res["hasSpecs"])
            self.assertEqual(res["unrecognized"], ["notes.md"])
            self.assertEqual(res["prose"][0]["heading"], "Что-то своё")
            self.assertEqual(len(res["files"][0]["sha256"]), 64)

    def test_removed_scenarios_leave_e2e(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "promo" / "spec.md"
            p.parent.mkdir()
            p.write_text(OPENSPEC, encoding="utf-8")
            res = specs.parse_files([p], root=Path(tmp))
            self.assertEqual([s["title"] for s in res["e2e"]], ["Показ баннера"])
            self.assertEqual([s["title"] for s in res["removedScenarios"]], ["Попап при входе"])
            self.assertTrue(res["hasE2e"])


if __name__ == "__main__":
    unittest.main()
