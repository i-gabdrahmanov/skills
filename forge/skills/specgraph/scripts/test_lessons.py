#!/usr/bin/env python3
"""test_lessons.py — матрица уроков прототипа (задача X.1).

Пинит `references/lessons.json`:
  • все 27 инвариантов SKILL.md прототипа (P-inv-1…27) и 8 инцидентов PROBLEMS.md §G (P-G-1…8) есть,
    id уникальны, статус из списка;
  • covered → «файл::Класс» существует (тест в skills/specgraph/scripts или hooks, класс объявлен);
  • planned → id задачи из карты README, и задача НЕ закрыта (закрыл — переведи урок в covered или
    by-construction);
  • by-construction и n/a — с объяснением, а не пустой отпиской.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
FORGE = SKILL.parents[1]
LESSONS = json.loads((SKILL / "references" / "lessons.json").read_text(encoding="utf-8"))["lessons"]
README = FORGE / "docs" / "specgraph" / "README.md"
STATUSES = {"covered", "by-construction", "n/a", "planned"}


def task_status() -> dict:
    out = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| М\d \| [^|]+ \| \[(\S+) ", line)
        if m:
            out[m.group(1)] = line.rstrip(" |").rsplit("|", 1)[-1].strip()
    return out


def test_exists(ref: str) -> bool:
    file, _, cls = ref.partition("::")
    for base in (SCRIPTS, FORGE / "hooks"):
        p = base / file
        if p.is_file():
            return bool(cls) and re.search(rf"^class {re.escape(cls)}\b", p.read_text(encoding="utf-8"), re.M) is not None
    return False


class Lessons(unittest.TestCase):
    def test_all_prototype_lessons_are_listed(self):
        ids = [l["id"] for l in LESSONS]
        self.assertEqual(len(ids), len(set(ids)), "id уникальны")
        for n in range(1, 28):
            self.assertIn(f"P-inv-{n}", ids)
        for n in range(1, 9):
            self.assertIn(f"P-G-{n}", ids)

    def test_statuses_and_refs(self):
        status = task_status()
        self.assertTrue(status, "карта задач README прочиталась")
        for l in LESSONS:
            with self.subTest(l["id"]):
                self.assertIn(l["status"], STATUSES)
                self.assertTrue(l.get("text"))
                ref = l.get("ref", "")
                if l["status"] == "covered":
                    self.assertTrue(test_exists(ref), f"нет теста {ref}")
                elif l["status"] == "planned":
                    self.assertIn(ref, status, f"нет задачи {ref} в карте")
                    self.assertNotEqual(status[ref], "done",
                                        f"задача {ref} закрыта — переведи урок в covered или by-construction")
                else:
                    self.assertGreaterEqual(len(ref), 20, "объяснение, а не отписка")


if __name__ == "__main__":
    unittest.main()
