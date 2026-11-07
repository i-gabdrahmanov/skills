#!/usr/bin/env python3
"""test_inv_matrix.py — матрица инвариантов спеки daiver (`references/inv-matrix.json`, задача X.1).

Пинит «сделали всё» кодом: каждый из 135 инвариантов §18 на месте и без лишних; у `covered` — тесты,
которые существуют (файл, класс верхнего уровня с хотя бы одним test-методом); у `degraded` — причина;
у `planned` — файл задачи в `docs/forge-ui/tasks/`, и эта задача ещё НЕ закрыта в README: закрытие задачи
с открытым инвариантом валит тест (Definition of Done п. 4), а финальная приёмка — «planned не осталось» —
наступает сама, когда закрыты все задачи. Спека (`docs/daiver/`) тесту не нужна: слои и их размеры — §19.8.
"""
from __future__ import annotations

import ast
import json
import re
import unittest
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORGE = HERE.parents[2]
MATRIX = HERE.parent / "references" / "inv-matrix.json"
TASKS = FORGE / "docs" / "forge-ui" / "tasks"
README = FORGE / "docs" / "forge-ui" / "README.md"

# §19.8: A13 + B23 + C39 + D10 + E8 + F20 + G12 + H10 = 135
LAYERS = {"A": 13, "B": 23, "C": 39, "D": 10, "E": 8, "F": 20, "G": 12, "H": 10}
STATUSES = ("covered", "degraded", "planned")
TEST_ROOTS = ("skills/forgeui/scripts/", "hooks/")
_ROW = re.compile(r"^\|[^|]*\|\s*\[(\S+)\s[^\]]*\]\(tasks/([^)]+)\)\s*\|[^|]*\|\s*(\w+)\s*\|\s*$")


def load() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def task_statuses() -> dict:
    """README «Карта задач»: id задачи → (файл, статус)."""
    out = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3))
    return out


def test_classes(path: Path) -> dict:
    """Классы верхнего уровня файла → число собственных test-методов."""
    with warnings.catch_warnings():                      # чужие файлы тестов: «\;» в обычной строке — не наша забота
        warnings.simplefilter("ignore")
        tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: sum(1 for x in n.body if isinstance(x, ast.FunctionDef) and x.name.startswith("test"))
            for n in tree.body if isinstance(n, ast.ClassDef)}


class Ids(unittest.TestCase):
    def test_all_135_ids_are_there_and_nothing_else(self):
        want = [f"{layer}{i}" for layer, n in LAYERS.items() for i in range(1, n + 1)]
        self.assertEqual(len(want), 135)
        got = list(load()["inv"])
        self.assertEqual(sorted(got), sorted(want), f"лишние: {sorted(set(got) - set(want))}, нет: {sorted(set(want) - set(got))}")
        self.assertEqual(got, want, "порядок — как в §18: слой за слоем, по номеру")

    def test_every_entry_has_a_known_status_and_only_known_fields(self):
        for k, e in load()["inv"].items():
            self.assertIn(e.get("status"), STATUSES, k)
            self.assertLessEqual(set(e), {"status", "tests", "task", "note"}, k)


class Entries(unittest.TestCase):
    def setUp(self):
        self.inv = load()["inv"]
        self.tasks = task_statuses()
        self.assertGreater(len(self.tasks), 40, "карта задач README не прочиталась")
        self._classes = {}

    def classes(self, rel: str) -> dict:
        if rel not in self._classes:
            self._classes[rel] = test_classes(FORGE / rel)
        return self._classes[rel]

    def test_covered_names_tests_that_exist(self):
        for k, e in self.inv.items():
            if e["status"] != "covered":
                continue
            self.assertTrue(e.get("tests"), f"{k}: covered без тестов")
        for k, e in self.inv.items():
            for ref in e.get("tests", []):
                rel, _, cls = ref.partition("::")
                self.assertTrue(rel.startswith(TEST_ROOTS) and Path(rel).name.startswith("test_") and cls, f"{k}: «{ref}» — ожидается <путь test_*.py>::<Класс>")
                self.assertTrue((FORGE / rel).is_file(), f"{k}: нет файла {rel}")
                got = self.classes(rel)
                self.assertIn(cls, got, f"{k}: в {rel} нет класса {cls}")
                self.assertGreater(got[cls], 0, f"{k}: у {rel}::{cls} нет своих test-методов")
            self.assertEqual(len(e.get("tests", [])), len(set(e.get("tests", []))), f"{k}: тест назван дважды")

    def test_degraded_and_planned_say_why(self):
        for k, e in self.inv.items():
            if e["status"] in ("degraded", "planned"):
                self.assertGreater(len(e.get("note", "").strip()), 20, f"{k}: {e['status']} без причины")

    def test_every_named_task_exists(self):
        for k, e in self.inv.items():
            task = e.get("task")
            if task:
                self.assertIn(task, self.tasks, f"{k}: задачи {task} нет в карте задач README")
                self.assertTrue((TASKS / self.tasks[task][0]).is_file(), f"{k}: нет файла задачи {self.tasks[task][0]}")

    def test_planned_points_at_a_task_that_is_still_open(self):
        for k, e in self.inv.items():
            if e["status"] != "planned":
                continue
            self.assertTrue(e.get("task"), f"{k}: planned без задачи")
            status = self.tasks[e["task"]][1]
            self.assertNotEqual(status, "done", f"{k}: задача {e['task']} закрыта, а инвариант всё ещё planned — "
                                                f"переведи его в covered (тест) или degraded (причина)")


class Wiring(unittest.TestCase):
    def test_the_task_readme_points_at_the_matrix(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("references/inv-matrix.json", text)
        self.assertIn("test_inv_matrix.py", text)

    def test_the_telemetry_deviation_names_every_degraded_telemetry_id(self):
        # README «Отклонения»: строка §17 перечисляет то, что снято вместе с телеметрией
        row = next(l for l in README.read_text(encoding="utf-8").splitlines() if l.startswith("| §17 телеметрия"))
        named = set(re.findall(r"\b([A-H]\d+)\b", row)) | ({f"H{i}" for i in range(1, 11)} if "INV-H*" in row else set())
        for part in re.findall(r"\b([A-H])(\d+)–\1?(\d+)\b", row):
            named |= {f"{part[0]}{i}" for i in range(int(part[1]), int(part[2]) + 1)}
        tele = {k for k, e in load()["inv"].items() if e["status"] == "degraded" and "телеметри" in e.get("note", "")}
        self.assertEqual(tele, named, "матрица и строка отклонения §17 расходятся")


if __name__ == "__main__":
    unittest.main(verbosity=2)
