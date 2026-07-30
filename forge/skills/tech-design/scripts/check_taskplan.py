#!/usr/bin/env python3
"""check_taskplan.py — детерминированная валидация task-plan.json (gate фазы Design).

Сверяет task-plan.json со схемой (tech-design/references/task-plan-schema.md) и с
ground-truth из scan/ (structure.json — модули, components.json — классы). Ловит то,
что LLM-дизайнер может тихо испортить: пустой acceptance, слой вне словаря,
висячие/циклические depends_on, галлюцинированные модули, а также reuses на
несуществующий класс (service/repository/mapper/dto/controller выдуманы «из памяти»).

Usage:
    check_taskplan.py <task-plan.json> [--scan <scan-dir>] [--json]
Exit: 0 = pass, 2 = есть errors.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LAYERS = {"migration", "entity", "repository", "dto", "mapper", "service", "controller", "scheduler"}
REQUIRED_TOP = ["feature_slug", "title", "tasks"]


def _load_modules(scan_dir: str | None):
    if not scan_dir:
        return None
    p = Path(scan_dir) / "structure.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {m["name"] for m in data.get("modules", [])}


def _read_scan(scan_dir: str, name: str) -> dict:
    p = Path(scan_dir) / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_existing_classes(scan_dir: str | None):
    """Множество имён РЕАЛЬНО существующих классов из детерминированного скана.

    Источник — components.json (service/repository/mapper/dto/controller/component) плюс
    контроллеры из api.json, entity из domain.json и util-классы из reuse.json. Возвращает
    None, если components.json нет — тогда кросс-чек reuses пропускается (грундинг не собирал
    инвентарь классов, ловить нечего, false-positive недопустим).
    """
    if not scan_dir:
        return None
    comp = _read_scan(scan_dir, "components.json")
    if not comp:
        return None
    names: set[str] = set()
    for it in comp.get("items", []):
        if it.get("name"):
            names.add(it["name"])
    for it in _read_scan(scan_dir, "api.json").get("items", []):
        if it.get("controller"):
            names.add(it["controller"])
    for it in _read_scan(scan_dir, "domain.json").get("items", []):
        if it.get("name"):
            names.add(it["name"])
    for it in _read_scan(scan_dir, "reuse.json").get("project_utils", []):
        if it.get("class"):
            names.add(it["class"])
    return names


def _reuse_class_name(ref: str) -> str:
    """Имя класса из ссылки reuses: путь `service/FooService.java`, FQN `com.x.FooService`
    или голое имя `FooService` → `FooService`."""
    ref = (ref or "").strip()
    if not ref:
        return ref
    base = ref.replace("\\", "/").split("/")[-1]
    for suf in (".java", ".kt"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    if "." in base and " " not in base:
        base = base.split(".")[-1]
    return base


def _task_modules(t: dict) -> list[str]:
    if isinstance(t.get("modules"), list):
        return t["modules"]
    if isinstance(t.get("module"), str):
        return [t["module"]]
    return []


def _find_cycle(tasks: list[dict]) -> list[str]:
    graph = {t["id"]: list(t.get("depends_on", [])) for t in tasks if t.get("id")}
    color = {n: 0 for n in graph}  # 0 white, 1 gray, 2 black
    found: list[str] = []

    def dfs(n: str, stack: list[str]) -> bool:
        color[n] = 1
        for d in graph.get(n, []):
            if d not in graph:
                continue
            if color[d] == 1:
                found.extend(stack + [n, d])
                return True
            if color[d] == 0 and dfs(d, stack + [n]):
                return True
        color[n] = 2
        return False

    for n in graph:
        if color[n] == 0 and dfs(n, []):
            break
    return found


def validate(plan: dict, known_modules: set[str] | None, scan_given: bool,
             known_classes: set[str] | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    for f in REQUIRED_TOP:
        if f not in plan:
            errors.append(f"missing top-level '{f}'")
    if "coverage_threshold" not in plan:
        warnings.append("coverage_threshold отсутствует — примут дефолт 0.80")

    tasks = plan.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks пуст или не массив")
        tasks = []

    ids: set[str] = set()
    for i, t in enumerate(tasks):
        tid = t.get("id")
        loc = tid or f"#{i}"
        if not tid:
            errors.append(f"task {loc}: нет id")
        elif tid in ids:
            errors.append(f"task {tid}: дублирующийся id")
        else:
            ids.add(tid)
        if not t.get("acceptance"):
            errors.append(f"task {loc}: пустой acceptance (нечем проверить задачу)")
        if not t.get("artifacts"):
            errors.append(f"task {loc}: пустой artifacts")
        layers = t.get("layers", [])
        if not layers:
            errors.append(f"task {loc}: пустой layers")
        for lay in layers:
            if lay not in LAYERS:
                warnings.append(f"task {loc}: слой '{lay}' вне словаря {sorted(LAYERS)}")

    for t in tasks:
        for d in t.get("depends_on", []):
            if d not in ids:
                errors.append(f"task {t.get('id')}: depends_on '{d}' не существует")

    cyc = _find_cycle(tasks)
    if cyc:
        errors.append(f"цикл в depends_on: {' -> '.join(cyc)}")

    for mg in plan.get("migrations", []):
        if mg.get("task_id") and mg["task_id"] not in ids:
            errors.append(f"migration {mg.get('changeset')}: task_id '{mg['task_id']}' не существует")

    if known_modules is not None:
        for mod in plan.get("modules", []):
            if mod not in known_modules:
                errors.append(f"top modules: '{mod}' нет среди модулей проекта (scan structure.json)")
        for t in tasks:
            for mod in _task_modules(t):
                if mod not in known_modules:
                    errors.append(f"task {t.get('id')}: module '{mod}' нет среди модулей проекта (scan)")
    elif scan_given:
        warnings.append("structure.json не найден — кросс-чек модулей пропущен")

    # Кросс-чек reuses: класс, помеченный как переиспользуемый/изменяемый, обязан существовать.
    # Ловит галлюцинированные «существующие» классы (service/repository/mapper/dto/controller).
    any_reuses = any(t.get("reuses") for t in tasks)
    if known_classes is not None:
        for t in tasks:
            for ref in t.get("reuses", []):
                cls = _reuse_class_name(ref)
                if cls and cls not in known_classes:
                    errors.append(
                        f"task {t.get('id')}: reuses '{ref}' — класса '{cls}' нет среди "
                        f"классов проекта (scan components.json). Если он реально есть — "
                        f"перезапусти grounding; иначе это новый класс, а не переиспользование")
    elif scan_given and any_reuses:
        warnings.append("components.json не найден — кросс-чек reuses пропущен (перезапусти grounding)")

    return {"status": "pass" if not errors else "fail",
            "tasks": len(tasks), "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic task-plan.json validation (Design gate).")
    ap.add_argument("plan")
    ap.add_argument("--scan", help="scan dir with structure.json for module cross-check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"status": "fail", "errors": [f"invalid JSON: {e}"]}, ensure_ascii=False))
        return 2

    verdict = validate(plan, _load_modules(args.scan), bool(args.scan),
                       _load_existing_classes(args.scan))
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if verdict["status"] == "pass" else "✗ FAIL"
        print(f"Task-plan check: {mark}  (задач: {verdict['tasks']})")
        for e in verdict["errors"]:
            print(f"  ✗ {e}")
        for w in verdict["warnings"]:
            print(f"  · warn: {w}")
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
