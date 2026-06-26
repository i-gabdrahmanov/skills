#!/usr/bin/env python3
"""Тесты check_taskplan.py — судья (gate) фазы 02-design. Раньше скрипт был без парного
теста: любая правка валидатора могла молча сломать гейт. Фиксируем контракт exit-кодов
(0=pass, 2=errors) и каждую ветку ошибки (пустой acceptance/artifacts/layers, висячий и
циклический depends_on, дубль id, отсутствие top-level, битый JSON, кросс-чек модулей).

Запуск: python3 test_check_taskplan.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_taskplan.py"
PASSED = 0
FAILED = 0


def _task(tid: str, **over) -> dict:
    t = {
        "id": tid,
        "acceptance": "Given X When Y Then Z",
        "artifacts": ["src/main/java/Foo.java"],
        "layers": ["service"],
        "depends_on": [],
    }
    t.update(over)
    return t


def _plan(**over) -> dict:
    p = {
        "feature_slug": "demo",
        "title": "Demo feature",
        "coverage_threshold": 0.8,
        "tasks": [_task("T1")],
    }
    p.update(over)
    return p


def run(plan: dict | str, *extra):
    """Записывает plan во временный файл и гоняет скрипт с --json."""
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "task-plan.json"
        pf.write_text(plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(pf), "--json", *extra],
                           capture_output=True, text=True)
        try:
            parsed = json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            parsed = {}
        return r.returncode, parsed, (r.stdout + r.stderr).strip()


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def _has_err(j: dict, needle: str) -> bool:
    return any(needle in e for e in j.get("errors", []))


def main() -> int:
    # 1. Валидный план → pass, exit 0
    rc, j, raw = run(_plan())
    check("валидный план → exit 0", rc == 0 and j.get("status") == "pass", raw)
    check("число задач = 1", j.get("tasks") == 1, raw)

    # 2. Нет top-level 'tasks' → fail exit 2
    p = _plan(); del p["tasks"]
    rc, j, raw = run(p)
    check("нет tasks → exit 2", rc == 2 and j.get("status") == "fail", raw)

    # 3. Нет top-level 'title' → fail
    p = _plan(); del p["title"]
    rc, j, raw = run(p)
    check("нет title → exit 2 + ошибка", rc == 2 and _has_err(j, "title"), raw)

    # 4. Пустой acceptance → fail
    rc, j, raw = run(_plan(tasks=[_task("T1", acceptance="")]))
    check("пустой acceptance → fail", rc == 2 and _has_err(j, "acceptance"), raw)

    # 5. Пустой artifacts → fail
    rc, j, raw = run(_plan(tasks=[_task("T1", artifacts=[])]))
    check("пустой artifacts → fail", rc == 2 and _has_err(j, "artifacts"), raw)

    # 6. Пустой layers → fail
    rc, j, raw = run(_plan(tasks=[_task("T1", layers=[])]))
    check("пустой layers → fail", rc == 2 and _has_err(j, "layers"), raw)

    # 7. Висячий depends_on → fail
    rc, j, raw = run(_plan(tasks=[_task("T1", depends_on=["TX"])]))
    check("висячий depends_on → fail", rc == 2 and _has_err(j, "depends_on"), raw)

    # 8. Цикл depends_on → fail
    rc, j, raw = run(_plan(tasks=[_task("T1", depends_on=["T2"]),
                                  _task("T2", depends_on=["T1"])]))
    check("цикл depends_on → fail", rc == 2 and _has_err(j, "цикл"), raw)

    # 9. Дублирующийся id → fail
    rc, j, raw = run(_plan(tasks=[_task("T1"), _task("T1")]))
    check("дубль id → fail", rc == 2 and _has_err(j, "дублирующийся"), raw)

    # 10. Битый JSON → exit 2, invalid JSON
    rc, j, raw = run("{not valid json")
    check("битый JSON → exit 2", rc == 2 and _has_err(j, "invalid JSON"), raw)

    # 11. Кросс-чек модулей: задача ссылается на несуществующий модуль (--scan)
    with tempfile.TemporaryDirectory() as td:
        scan = Path(td)
        (scan / "structure.json").write_text(
            json.dumps({"modules": [{"name": "core"}]}), encoding="utf-8")
        # модуль 'ghost' нет в structure.json → fail
        rc, j, raw = run(_plan(tasks=[_task("T1", module="ghost")]), "--scan", str(scan))
        check("module не из scan → fail", rc == 2 and _has_err(j, "ghost"), raw)
        # модуль 'core' есть → pass
        rc, j, raw = run(_plan(tasks=[_task("T1", module="core")]), "--scan", str(scan))
        check("module из scan → pass", rc == 0, raw)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
