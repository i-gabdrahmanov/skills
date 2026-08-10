#!/usr/bin/env python3
"""Tests for check_fix_delta.py — гейт минимальной дельта-правки спеки (шаг fix-spec)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_fix_delta.py"

MASTER = """# Master Spec: claims

## 5. Требования и сценарии

### REQ-0007: Создание заявки
Система создаёт заявку по валидному запросу.  [from: claims 2026-01-01]
- **Given** валидный запрос **When** POST /api/claims **Then** заявка создана, код 201
- **Given** запрос без обязательного поля **When** POST /api/claims **Then** код 400

### REQ-0008: Поиск заявок
Система ищет заявки по фильтрам.  [from: claims 2026-01-01]
- **Given** есть заявки **When** GET /api/claims?status=NEW **Then** вернулись только NEW

## 9. Журнал изменений
"""

# Дельта фикса: название совпадает с REQ-0007, оба сценария мастера сохранены + регресс-сценарий.
GOOD_DELTA = """# SDD: Пустой email при создании заявки

## 1. Назначение и результат
Заявка без email отвергается с кодом 400, а не падает с 500.

## 3. Функциональные требования (Given-When-Then)

### Создание заявки
Система создаёт заявку по валидному запросу; запрос без email считается невалидным.
- **Given** валидный запрос **When** POST /api/claims **Then** заявка создана, код 201
- **Given** запрос без обязательного поля **When** POST /api/claims **Then** код 400
- **Given** запрос с пустым email **When** POST /api/claims **Then** код 400, заявка не создана
"""

PLAN = {"feature_slug": "STOR-1", "title": "fix", "tasks": [
    {"id": "F1", "layer": "service", "layers": ["service"], "title": "Валидация email",
     "acceptance": ["Given пустой email When POST /api/claims Then 400"],
     "sdd_ref": "REQ-0007"}]}


def _lean_delta() -> str:
    """Дельта, где от требования остался только новый сценарий (оба сценария мастера выкинуты)."""
    return "\n".join(ln for ln in GOOD_DELTA.splitlines()
                     if "POST /api/claims **Then** заявка создана" not in ln
                     and "без обязательного поля" not in ln) + "\n"


def _run(delta: str, *extra: str, master: str | None = MASTER,
         plan: dict | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "sdd.md").write_text(delta, encoding="utf-8")
        cmd = [sys.executable, str(SCRIPT), str(d / "sdd.md"), "--json"]
        if master is not None:
            (d / "spec.md").write_text(master, encoding="utf-8")
            cmd += ["--spec", str(d / "spec.md")]
        if plan is not None:
            (d / "task-plan.json").write_text(json.dumps(plan), encoding="utf-8")
            cmd += ["--plan", str(d / "task-plan.json")]
        return subprocess.run(cmd + list(extra), capture_output=True, text=True)


class TestCheckFixDelta(unittest.TestCase):
    def test_good_delta_passes_as_modify(self):
        r = _run(GOOD_DELTA, plan=PLAN)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        v = json.loads(r.stdout)
        self.assertEqual(v["requirements"], 1)
        self.assertTrue(any(op.startswith("~ REQ-0007") for op in v["ops"]), v["ops"])

    def test_scenario_loss_fails(self):
        """Дельта переписала требование «с нуля», потеряв сценарии мастера — merge их затрёт."""
        r = _run(_lean_delta())
        self.assertEqual(r.returncode, 2)
        self.assertIn("потеряны", r.stdout + r.stderr)

    def test_scenario_loss_allowed_with_flag(self):
        self.assertEqual(_run(_lean_delta(), "--allow-scenario-drop").returncode, 0)

    def test_no_gwt_fails(self):
        delta = "# SDD: fix\n\n## 3. Функциональные требования (Given-When-Then)\n\n### Создание заявки\nПочинили.\n"
        r = _run(delta)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Given-When-Then", r.stdout + r.stderr)

    def test_code_in_spec_fails(self):
        delta = GOOD_DELTA + "\n```java\npublic class Foo {}\n```\n"
        r = _run(delta)
        self.assertEqual(r.returncode, 2)
        self.assertIn("код-блок", r.stdout + r.stderr)

    def test_too_many_requirements_fails(self):
        """«Переписал спеку заново» — ровно то, от чего защищает fix-путь."""
        body = "\n".join(
            f"### Требование {i}\nУтверждение {i}.\n"
            f"- **Given** g{i} **When** w{i} **Then** t{i}\n" for i in range(5))
        delta = "# SDD: big\n\n## 3. Функциональные требования (Given-When-Then)\n\n" + body
        r = _run(delta)
        self.assertEqual(r.returncode, 2)
        self.assertIn("лимит", r.stdout + r.stderr)

    def test_too_long_fails(self):
        delta = GOOD_DELTA + "\n".join(f"Пояснение {i}." for i in range(200))
        r = _run(delta)
        self.assertEqual(r.returncode, 2)
        self.assertIn("строк", r.stdout + r.stderr)

    def test_no_anchor_warns_but_passes(self):
        """Баг в неописанном поведении — легитимно: предупреждаем, но не блокируем."""
        delta = GOOD_DELTA.replace("### Создание заявки", "### Совсем новое поведение")
        r = _run(delta)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        v = json.loads(r.stdout)
        self.assertTrue(any("не совпало" in w for w in v["warnings"]), v["warnings"])

    def test_plan_without_sdd_ref_fails(self):
        bad = {"feature_slug": "S", "title": "t", "tasks": [
            {"id": "F1", "acceptance": ["Given a When b Then c"]}]}
        r = _run(GOOD_DELTA, plan=bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("sdd_ref", r.stdout + r.stderr)

    def test_anchor_match_passes(self):
        self.assertEqual(_run(GOOD_DELTA, "--anchor", "REQ-0007").returncode, 0)

    def test_anchor_by_title_passes(self):
        self.assertEqual(_run(GOOD_DELTA, "--anchor", "Создание заявки").returncode, 0)

    def test_wrong_anchor_fails(self):
        """Решение зафиксировано на REQ-0008, а дельта правит REQ-0007 — гейт валит."""
        r = _run(GOOD_DELTA, "--anchor", "REQ-0008")
        self.assertEqual(r.returncode, 2)
        self.assertIn("не правит зафиксированный якорь", r.stdout + r.stderr)

    def test_anchor_none_skips_check(self):
        """'none' = поведение в спеке не описано: дельта заводит новое требование, это ок."""
        delta = GOOD_DELTA.replace("### Создание заявки", "### Совсем новое поведение")
        self.assertEqual(_run(delta, "--anchor", "none").returncode, 0)

    def test_anchor_unverifiable_without_master_warns(self):
        r = _run(GOOD_DELTA, "--anchor", "REQ-0007", master=None)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(any("не проверен" in w for w in json.loads(r.stdout)["warnings"]))

    def test_missing_delta_exit2(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "/nope/sdd.md"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_without_master_warns(self):
        r = _run(GOOD_DELTA, master=None)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(any("не резолвится" in w for w in json.loads(r.stdout)["warnings"]))


if __name__ == "__main__":
    unittest.main()
