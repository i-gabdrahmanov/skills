#!/usr/bin/env python3
"""Тесты add_steps.py (feature-pipeline версия) — добавление шагов в manifest + ОБЯЗАТЕЛЬНАЯ
пересборка gate.json/phase-defs.json и проставление required_judges по маске. Раньше прямого
теста не было (логика косвенно пинилась test_phase_consistency). Здесь фиксируем поведение
рантайма: идемпотентность, маска судей для 04-build-*, синхронизацию gate.

Также покрываем п.9 KIDPPRB-9254: валидация task-id из step-id против task-plan.tasks[].id,
формат task-id (regex), warning при отсутствии plan, exit-коды (0/1/2).

Запуск:
  python3 test_add_steps.py            — script-style runner (PASSED/FAILED)
  python3 -m pytest test_add_steps.py  — pytest discovery (использует unittest.TestCase)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "add_steps.py"
SKILL = "feature-pipeline"
FEATURE = "demo"
PASSED = 0
FAILED = 0


def _project(td: str) -> Path:
    project = Path(td)
    d = project / "ground" / "statements" / SKILL / FEATURE
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "skill": SKILL, "feature": FEATURE, "context": {},
        "steps": [{"id": "02-design", "status": "completed"}],
    }), encoding="utf-8")
    return project


def run(project: Path, steps: list, task_plan: str | None = None,
        extra_args: list | None = None):
    """add_steps использует Path.cwd() — запускаем с cwd=project."""
    argv = [sys.executable, str(SCRIPT), "--skill", SKILL, "--feature", FEATURE,
            "--steps", json.dumps(steps)]
    if task_plan:
        argv += ["--task-plan", task_plan]
    if extra_args:
        argv += list(extra_args)
    r = subprocess.run(argv, capture_output=True, text=True, cwd=str(project))
    try:
        parsed = json.loads(r.stdout.strip())
    except json.JSONDecodeError:
        parsed = {}
    return r.returncode, parsed, (r.stdout + r.stderr).strip()


def _manifest(project: Path) -> dict:
    return json.loads(
        (project / "ground" / "statements" / SKILL / FEATURE / "manifest.json").read_text(encoding="utf-8"))


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  OK {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}  {detail}")


# ──────────────────────────────────────────────────────────────────────────
# Pytest-стиль: класс с test_* методами. Запускается и через pytest, и через
# ручной `python3 test_add_steps.py` (см. main()).
# ──────────────────────────────────────────────────────────────────────────

class TestAddStepsValidation(unittest.TestCase):
    """П.9 KIDPPRB-9254: валидация task-id из id шага против task-plan.tasks[].id."""

    def _make_project(self, plan_tasks: list | None, plan_location: str = "docs") -> tuple[Path, Path]:
        """Создаёт временный проект с манифестом и (опционально) task-plan.json.
        plan_location: 'docs' | 'ground' | 'gigacode' | 'missing' | 'custom'."""
        td = tempfile.mkdtemp()
        project = _project(td)
        if plan_tasks is None:
            self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))
            return project, Path(td)
        if plan_location == "docs":
            p = project / "docs" / SKILL / FEATURE / "task-plan.json"
        elif plan_location == "ground":
            p = project / "ground" / "statements" / SKILL / FEATURE / "task-plan.json"
        elif plan_location == "gigacode":
            p = project / ".gigacode" / "pipeline-state" / FEATURE / "task-plan.json"
        elif plan_location == "custom":
            p = project / "weird" / "place" / "task-plan.json"
        else:
            p = None
        if p is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"tasks": [{"id": t} for t in plan_tasks]}), encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))
        return project, p

    # 1. Неизвестный task-id → exit 2 + error содержит имя таска и список доступных
    def test_add_steps_rejects_unknown_task_id(self):
        project, plan = self._make_project(["TASK1", "TASK2"], "docs")
        rc, j, out = run(project, [{"id": "04-test-TASK9", "title": "t"}], task_plan=str(plan))
        self.assertEqual(rc, 2, f"ожидался exit 2, получили {rc}; out={out!r}")
        self.assertEqual(j.get("status"), "error", out)
        self.assertEqual(j.get("error_class"), "validation", out)
        self.assertIn("not found in task-plan", j.get("error", ""), out)
        self.assertIn("TASK9", j.get("error", ""), out)

    # 2. Известный task-id → exit 0 + шаг добавился
    def test_add_steps_accepts_known_task_id(self):
        project, plan = self._make_project(["TASK1", "TASK2", "TASK3"], "docs")
        rc, j, out = run(project, [
            {"id": "04-test-TASK2", "title": "RED TASK2", "depends_on": ["02-design"]},
            {"id": "04-build-TASK2", "depends_on": ["04-test-TASK2"]},
        ], task_plan=str(plan))
        self.assertEqual(rc, 0, f"ожидался exit 0, получили {rc}; out={out!r}")
        self.assertEqual(j.get("status"), "ok", out)
        self.assertEqual(j.get("added"), 2, out)
        man = _manifest(project)
        ids = [s["id"] for s in man["steps"]]
        self.assertIn("04-test-TASK2", ids, str(ids))
        self.assertIn("04-build-TASK2", ids, str(ids))

    # 3. Малформедный task-id (формат не подходит) → exit 2
    def test_add_steps_rejects_malformed_task_id(self):
        project, plan = self._make_project(["TASK1"], "docs")
        # Слишком короткий: 'a' — 1 символ (<3)
        rc, j, out = run(project, [{"id": "04-test-a", "title": "x"}], task_plan=str(plan))
        self.assertEqual(rc, 2, f"short id: ожидался exit 2, получили {rc}; out={out!r}")
        self.assertIn("недопустим", j.get("error", "").lower(), out)
        # Запрещённый символ: точка (1.2.3) — формат не пропустит даже если id в плане
        rc2, j2, out2 = run(project, [{"id": "04-test-T1.x", "title": "x"}],
                            task_plan=str(plan))
        self.assertEqual(rc2, 2, f"dot id: ожидался exit 2, получили {rc2}; out={out2!r}")
        self.assertIn("формат", j2.get("error", "").lower(), out2)
        # Слишком длинный (>64)
        long_id = "a" + "x" * 70
        rc3, j3, out3 = run(project, [{"id": f"04-test-{long_id}"}],
                            task_plan=str(plan))
        self.assertEqual(rc3, 2, f"long id: ожидался exit 2, получили {rc3}; out={out3!r}")
        self.assertIn("длин", j3.get("error", "").lower(), out3)
        # Старт не с буквы: цифра в начале
        rc4, j4, out4 = run(project, [{"id": "04-test-1abc"}],
                            task_plan=str(plan))
        self.assertEqual(rc4, 2, f"digit-start id: ожидался exit 2, получили {rc4}; out={out4!r}")

    # 4. Нет task-plan → warning в stderr, exit 0 (НЕ падаем)
    def test_add_steps_warns_when_no_task_plan(self):
        project, _ = self._make_project(None)
        rc, j, _out, stderr = _run_capture(project, [{"id": "04-test-TASK1", "title": "RED"}])
        # plan отсутствует → строгая сверка пропускается, шаг добавляется
        self.assertEqual(rc, 0, f"ожидался exit 0, получили {rc}; out={_out!r}")
        self.assertEqual(j.get("status"), "ok", _out)
        self.assertIn("WARNING", stderr)
        self.assertIn("task-plan.json", stderr)
        self.assertIn(FEATURE, stderr)

    # 5. error-сообщение содержит список доступных id
    def test_add_steps_lists_available_ids_in_error_message(self):
        project, plan = self._make_project(["TASK1", "TASK2", "TASK3"], "docs")
        rc, j, out = run(project, [{"id": "04-test-TASK9"}], task_plan=str(plan))
        self.assertEqual(rc, 2, out)
        err = j.get("error", "")
        for expected in ("TASK1", "TASK2", "TASK3"):
            self.assertIn(expected, err, f"available-id '{expected}' отсутствует в error: {err!r}")
        # Префикс формата 'available: '
        self.assertIn("available:", err, err)
        # Feature-key в сообщении (для ориентации оператора)
        self.assertIn(FEATURE, err, err)

    # 6. (бонус) auto-discovery task-plan: план в ground/statements/.../ берётся без --task-plan
    def test_add_steps_auto_discovers_task_plan_in_ground(self):
        project, _ = self._make_project(["KID1", "KID2"], "ground")
        # БЕЗ --task-plan — скрипт должен сам найти план в ground/statements/...
        rc, j, out = run(project, [{"id": "04-test-KID1", "title": "x"}])
        self.assertEqual(rc, 0, f"ожидался exit 0, получили {rc}; out={out!r}")
        self.assertEqual(j.get("status"), "ok", out)
        self.assertEqual(j.get("added"), 1, out)


def _run_capture(project: Path, steps: list, task_plan: str | None = None,
                 extra_args: list | None = None):
    """Возвращает (rc, json, stdout, stderr) — нужно для теста warning в stderr."""
    argv = [sys.executable, str(SCRIPT), "--skill", SKILL, "--feature", FEATURE,
            "--steps", json.dumps(steps)]
    if task_plan:
        argv += ["--task-plan", task_plan]
    if extra_args:
        argv += list(extra_args)
    r = subprocess.run(argv, capture_output=True, text=True, cwd=str(project))
    try:
        parsed = json.loads(r.stdout.strip())
    except json.JSONDecodeError:
        parsed = {}
    return r.returncode, parsed, r.stdout.strip(), r.stderr.strip()


# ──────────────────────────────────────────────────────────────────────────
# Script-style runner (legacy) — запускается как `python3 test_add_steps.py`.
# Дублирует ключевые кейсы из TestAddStepsValidation в процедурном виде,
# чтобы прогон был виден и без pytest.
# ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    print("=== script-style sanity ===")

    # 1. Добавление новых шагов → added=2, фазы видны сразу (без ребилда кэша)
    with tempfile.TemporaryDirectory() as td:
        project = _project(td)
        rc, j, out = run(project, [
            {"id": "04-test-TASK1", "title": "RED TASK1", "depends_on": ["02-design"]},
            {"id": "04-build-TASK1", "title": "GREEN TASK1", "depends_on": ["04-test-TASK1"]},
        ])
        check("exit 0", rc == 0, out)
        check("added=2", j.get("added") == 2, out)
        check("phase_count>0", j.get("phase_count", 0) > 0, out)

        # required_judges проставлены по маске: 04-build-* → содержит build-judge
        man = _manifest(project)
        build_step = next(s for s in man["steps"] if s["id"] == "04-build-TASK1")
        check("04-build-TASK1 имеет required_judges", bool(build_step.get("required_judges")), str(build_step))
        check("04-build-TASK1 включает build-judge",
              "build-judge" in build_step.get("required_judges", []), str(build_step))

        # Кэш фазовой машины на диск НЕ пишется: состояние выводится из манифеста.
        gate_files = list(project.glob("ground/**/gate.json"))
        check("gate.json на диск не пишется", gate_files == [], str(gate_files))
        check("current_phase выведен из манифеста", bool(j.get("current_phase")), out)

        # 2. Идемпотентность: повторное добавление тех же id → added=0, skipped=2
        rc, j2, out2 = run(project, [
            {"id": "04-test-TASK1", "title": "RED TASK1"},
            {"id": "04-build-TASK1", "title": "GREEN TASK1"},
        ])
        check("повтор → added=0", j2.get("added") == 0, out2)
        check("повтор → skipped=2", j2.get("skipped") == 2, out2)
        # шаг не задублировался
        man2 = _manifest(project)
        ids = [s["id"] for s in man2["steps"]]
        check("нет дублей id", len(ids) == len(set(ids)), str(ids))

    # 3. Нет манифеста → status=error (infra)
    with tempfile.TemporaryDirectory() as td:
        rc, j, out = run(Path(td), [{"id": "04-test-TASK1"}])
        check("нет манифеста → error", j.get("status") == "error", out)

    # 4. Шаблонный суффикс id отклоняется (п.9 KIDPPRB-9254): 04-test-task вместо 04-test-TASK1
    with tempfile.TemporaryDirectory() as td:
        project = _project(td)
        rc, j, out = run(project, [{"id": "04-test-task", "title": "RED"}])
        check("шаблонный суффикс 'task' → error", j.get("status") == "error"
              and "зарезервированный суффикс" in j.get("error", ""), out)

    # 5. Строгая сверка с task-plan: несуществующий task-id → error (validation, exit 2)
    with tempfile.TemporaryDirectory() as td:
        project = _project(td)
        plan_path = project / "docs" / SKILL / FEATURE / "task-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps({"tasks": [{"id": "TASK1"}, {"id": "TASK2"}]}), encoding="utf-8")
        rc, j, out = run(project, [{"id": "04-test-TASK9", "title": "t"}],
                         task_plan=str(plan_path))
        check("task T9 не в плане → exit 2", rc == 2, out)
        check("task T9 не в плане → validation", j.get("error_class") == "validation", out)
        check("task T9 не в плане → error содержит 'not found in task-plan'",
              "not found in task-plan" in j.get("error", ""), out)
        check("task T9 не в плане → error содержит available-список",
              "available:" in j.get("error", "") and "TASK1" in j.get("error", ""), out)

        # а валидный task-id из плана добавляется
        rc, j2, out2 = run(project, [{"id": "04-test-TASK1", "title": "RED TASK1"},
                                     {"id": "04-build-TASK2", "depends_on": ["04-test-TASK1"]}])
        check("валидные task-id из плана добавляются", j2.get("added") == 2, out2)

    # 6. Малформедный task-id (точка/слишком короткий) → exit 2
    with tempfile.TemporaryDirectory() as td:
        project = _project(td)
        plan_path = project / "docs" / SKILL / FEATURE / "task-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps({"tasks": [{"id": "TASK1"}]}), encoding="utf-8")
        rc, j, out = run(project, [{"id": "04-test-a.b"}],
                         task_plan=str(plan_path))
        check("точка в task-id → exit 2", rc == 2, out)
        check("точка в task-id → validation", j.get("error_class") == "validation", out)

    # 7. Нет task-plan → warning в stderr, exit 0, шаг добавляется
    with tempfile.TemporaryDirectory() as td:
        project = _project(td)
        rc, j, _out, stderr = _run_capture(project, [{"id": "04-test-TASK1"}])
        check("нет plan → exit 0", rc == 0, f"{_out!r} / stderr={stderr!r}")
        check("нет plan → WARNING в stderr", "WARNING" in stderr, stderr)
        check("нет plan → шаг добавлен", j.get("added") == 1, _out)

    # 8. --strict-task-plan + нет plan → exit 2
    with tempfile.TemporaryDirectory() as td:
        project = _project(td)
        rc, j, _out, _stderr = _run_capture(project, [{"id": "04-test-TASK1"}],
                                            extra_args=["--strict-task-plan"])
        check("--strict-task-plan без plan → exit 2", rc == 2, _out)
        check("--strict-task-plan без plan → validation",
              j.get("error_class") == "validation", _out)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
else:
    # При импорте через pytest — прогоняем unittest-кейсы здесь не нужно,
    # pytest сам найдёт TestAddStepsValidation.
    pass
