#!/usr/bin/env python3
from __future__ import annotations
"""
prepare_design_context.py — подготавливает компактный дата-контекст для tech-design.

Читает grounding-excerpt.json (2840 строк) и BRD/задачи фичи,
фильтрует только релевантные entities/api/async/tables по затронутым модулям,
генерирует компактный JSON (~50-200 строк) для передачи в контракт субагента.

Usage:
    python3 prepare_design_context.py \\
        --brd docs/feature-pipeline/<slug>/brd.md \\
        --grounding docs/system-analysis/grounding-excerpt.json \\
        --out docs/feature-pipeline/<slug>/design-context.json

    python3 prepare_design_context.py \\
        --task-plan docs/feature-pipeline/<slug>/task-plan.json \\
        --grounding docs/system-analysis/grounding-excerpt.json \\
        --out ...

    python3 prepare_design_context.py \\
        --modules service-taskservice,service-regservice \\
        --grounding ... --out ...
"""

import json
import re
import sys
from pathlib import Path


# Связанные модули по ключевым словам в BRD
KEYWORD_MODULE_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bзадач\w*\b"), "service-taskservice"),
    (re.compile(r"(?i)\bрегистрац\w*\b"), "service-regservice"),
    (re.compile(r"(?i)\bотказ\w*\b|\bрефузи\w*\b"), "service-refusionservice"),
    (re.compile(r"(?i)\bюрлиц\w*\b|\bюл\b"), "service-pprbulservice"),
    (re.compile(r"(?i)\bфизлиц\w*\b|\bфл\b"), "service-pprbflservice"),
    (re.compile(r"(?i)\bпоиск\b|\bsearch\b"), "service-searchservice"),
    (re.compile(r"(?i)\bсбертранспорт\b|\bтранспорт\b"), "service-sbertransport"),
    (re.compile(r"(?i)\bсбол\b|\bsbol\b|\bдокументооборот\b"), "service-sbolproservice"),
    (re.compile(r"(?i)\bпочт\w*\b|\bгибрид\w*\b"), "service-hybridmailservice"),
    (re.compile(r"(?i)\bрм\s*ос\b|\brmoc\b"), "service-rmocservice"),
    (re.compile(r"(?i)\bшпи\b|\bпочтов\w*\b"), "service-shpiservice"),
    (re.compile(r"(?i)\bконтрол\w*\s*оригинал\w*\b"), "service-originalcontrolservice"),
    (re.compile(r"(?i)\bкобот\b|\bробот\b"), "service-kobotservice"),
    (re.compile(r"(?i)\bкод\b|\bгенерац\w*\b|\bштрих\w*\b"), "service-bcgeneratorservice"),
    (re.compile(r"(?i)\bcars\b|\bкарс\b"), "service-carsservice"),
    (re.compile(r"(?i)\bfail.?track\b"), "service-failtrackservice"),
    (re.compile(r"(?i)\bupz\b"), "service-upzservice"),
]


def extract_modules_from_brd(brd_path: str) -> list[str]:
    """Парсит BRD, извлекая названия затронутых модулей по ключевым словам."""
    brd_file = Path(brd_path)
    if not brd_file.exists():
        return []

    text = brd_file.read_text(encoding="utf-8")

    # Сначала ищем явный блок "Затронутые модули" или "Модули"
    section_match = re.search(
        r"(?im)^(?:#{1,3}\s*)?(?:затронут[ыие]\s+модул[ия]|модули|затрагиваемые\s+сервис[ыа])\s*$",
        text,
    )
    if section_match:
        # Собираем строчки до следующей секции
        lines = text[section_match.end():].split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("#") or line.startswith("---"):
                break
            # Ищем паттерн имен модулей: service-name, utils-name
            modules = re.findall(r"(?:service|utils|api|database)[-\w]*", line, re.IGNORECASE)
            if modules:
                return [m.lower() for m in modules]

    # Иначе — поиск по ключевым словам
    modules = set()
    for pattern, module_name in KEYWORD_MODULE_MAP:
        if pattern.search(text):
            modules.add(module_name)
    return sorted(modules)


def extract_modules_from_task_plan(task_plan_path: str) -> tuple[list[str], set[str]]:
    """Извлекает модули и ключевые слова из task-plan.json.

    Возвращает (modules, keywords).
    keywords — слова для фильтрации элементов с module="?".
    """
    tp_path = Path(task_plan_path)
    if not tp_path.exists():
        return [], set()

    tp = json.loads(tp_path.read_text(encoding="utf-8"))
    modules = set()
    keywords = set()

    for task in tp.get("tasks", []):
        # Модули. tech-design пишет их в tasks[].modules (массив; см. check_taskplan.py).
        # affected_modules / module — на случай старого формата.
        for mod in task.get("modules", []):
            if isinstance(mod, str) and mod:
                modules.add(mod.lower().replace(":", "-"))
        for mod in task.get("affected_modules", []):
            if isinstance(mod, str) and mod:
                modules.add(mod.lower().replace(":", "-"))
        mod = task.get("module")
        if isinstance(mod, str) and mod:
            modules.add(mod.lower().replace(":", "-"))

        # Ключевые слова из title и acceptance
        title = task.get("title", "")
        if title:
            for word in re.findall(r"[A-Za-zА-Яа-я][A-Za-zА-Яа-я_]{2,}", title):
                keywords.add(word)

        for acc in task.get("acceptance", []):
            if isinstance(acc, str):
                for word in re.findall(r"[A-Za-zА-Яа-я][A-Za-zА-Яа-я_]{2,}", acc):
                    keywords.add(word)

        # affected_entities
        for ent in task.get("affected_entities", []):
            keywords.add(ent)

    return sorted(modules), keywords


def filter_grounding(
    grounding: dict,
    relevant_modules: set[str],
    keywords: set[str] | None = None,
) -> dict:
    """Фильтрует grounding-excerpt, оставляя только релевантные модули.

    Если у элемента module = "?" — пытается сопоставить по ключевым словам
    из task-plan (title, acceptance, affected_entities).
    """
    keywords = keywords or set()

    def _name_matches_by_keywords(name: str) -> bool:
        """Проверяет имя элемента на совпадение с ключевыми словами."""
        if not keywords or not name:
            return False
        name_lower = name.lower()
        return any(kw.lower() in name_lower for kw in keywords)

    def _module_matches(item) -> bool:
        """Проверяет, относится ли элемент к релевантному модулю."""
        module = (item.get("module") or item.get("module_name") or "").lower()
        item_name = (item.get("name") or item.get("title") or "").strip()

        if module and module != "?":
            module_clean = module.replace(":", "-")
            if module_clean in relevant_modules or any(
                m in module_clean for m in relevant_modules
            ):
                return True

        # Fallback: module="?" — пытаемся сопоставить по имени и ключевым словам
        if _name_matches_by_keywords(item_name):
            return True

        # Если keywords нет — включаем (лучше избыточно, чем потерять)
        if not keywords:
            return True

        return False

    def _entities_for_modules(entities: list) -> list:
        """Фильтрует entity и находит связанные с модулями."""
        result = []
        for ent in entities:
            if _module_matches(ent):
                result.append(ent)
        return result

    def _endpoints_for_modules(endpoints: list) -> list:
        """Фильтрует API endpoints."""
        result = []
        for ep in endpoints:
            if _module_matches(ep):
                result.append(ep)
        return result

    def _async_for_modules(async_items: list) -> list:
        """Фильтрует Kafka consumers/producers."""
        result = []
        for item in async_items:
            if _module_matches(item):
                result.append(item)
        return result

    def _ext_clients_for_modules(clients: list) -> list:
        """Фильтрует внешние интеграции."""
        result = []
        for cl in clients:
            if _module_matches(cl):
                result.append(cl)
        return result

    def _tables_for_modules(tables: list) -> list:
        """Фильтрует таблицы БД. Строки включаем все; объекты фильтруем по модулю."""
        result = []
        for tbl in tables:
            if isinstance(tbl, str):
                result.append(tbl)
            elif _module_matches(tbl):
                result.append(tbl)
        return result

    context = {
        "$schema": "design-context@1",
        "generated_for_modules": sorted(relevant_modules),
        "updated_at": grounding.get("updated_at", ""),
        "entities": _entities_for_modules(grounding.get("entities", [])),
        "api_endpoints": _endpoints_for_modules(grounding.get("api_endpoints", [])),
        "async": _async_for_modules(grounding.get("async", [])),
        "external_clients": _ext_clients_for_modules(grounding.get("external_clients", [])),
        "tables": _tables_for_modules(grounding.get("tables", [])),
        # Каталог переиспользования — целиком (не фильтруется по модулям).
        # Java-writer и reuse-judge читают его чтобы знать доступные lib/utils.
        "reuse": grounding.get("reuse"),
    }

    return context


def main():
    args = sys.argv[1:]

    # Парсим аргументы
    brd_path = None
    task_plan_path = None
    modules_str = None
    grounding_path = None
    out_path = None
    pipeline_path = None

    for key, val in zip(args[::2], args[1::2]):
        if key == "--brd":
            brd_path = val
        elif key == "--task-plan":
            task_plan_path = val
        elif key == "--modules":
            modules_str = val
        elif key == "--grounding":
            grounding_path = val
        elif key == "--out":
            out_path = val
        elif key == "--pipeline":
            pipeline_path = val

    if not grounding_path:
        print(json.dumps({"error": "--grounding is required"}, ensure_ascii=False))
        sys.exit(1)

    grounding_file = Path(grounding_path)
    if not grounding_file.exists():
        print(json.dumps({"error": f"Grounding file not found: {grounding_path}"}, ensure_ascii=False))
        sys.exit(1)

    grounding = json.loads(grounding_file.read_text(encoding="utf-8"))

    # 1. Определяем релевантные модули
    relevant_modules = set()

    if modules_str:
        for m in modules_str.split(","):
            m = m.strip().lower().replace(":", "-")
            if m:
                relevant_modules.add(m)

    if brd_path:
        brd_modules = extract_modules_from_brd(brd_path)
        relevant_modules.update(brd_modules)

    keywords = set()
    if task_plan_path:
        tp_modules, tp_keywords = extract_modules_from_task_plan(task_plan_path)
        relevant_modules.update(tp_modules)
        keywords.update(tp_keywords)

    if not relevant_modules:
        # Если ничего не нашли — включаем все модули (fallback)
        relevant_modules = {
            m["name"].lower() for m in grounding.get("modules", [])
        }

    # 2. Читаем test_layer из pipeline.json (передаётся TDD-writer и java-writer)
    test_layer = "service-unit"
    _pipeline_file = Path(pipeline_path) if pipeline_path else None
    if not _pipeline_file and task_plan_path:
        # Автодетект ground/pipeline.json. Идём вверх от task-plan, ищем ground/pipeline.json
        # в каждом предке — устойчиво к относительным/коротким путям (Qwen может передать
        # просто 'task-plan.json'); .parents[2] упал бы с IndexError.
        for _anc in Path(task_plan_path).resolve().parents:
            _cand = _anc / "ground" / "pipeline.json"
            if _cand.exists():
                _pipeline_file = _cand
                break
    if _pipeline_file and _pipeline_file.exists():
        try:
            _pcfg = json.loads(_pipeline_file.read_text(encoding="utf-8"))
            test_layer = _pcfg.get("quality", {}).get("test_layer", test_layer)
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Фильтруем
    if keywords:
        print(f"  keywords: {sorted(keywords)[:20]}...", file=sys.stderr)
    context = filter_grounding(grounding, relevant_modules, keywords)

    # 4. Добавляем метаданные
    context["total_entities"] = len(grounding.get("entities", []))
    context["filtered_entities"] = len(context["entities"])
    context["total_endpoints"] = len(grounding.get("api_endpoints", []))
    context["filtered_endpoints"] = len(context["api_endpoints"])
    context["total_tables"] = len(grounding.get("tables", []))
    context["filtered_tables"] = len(context["tables"])
    # test_layer — машиночитаемый флаг для TDD-writer и java-writer.
    # service-unit: только Mockito (@ExtendWith(MockitoExtension.class)); @DataJpaTest/@SpringBootTest запрещены.
    context["test_layer"] = test_layer

    output = json.dumps(context, ensure_ascii=False, indent=2)

    if out_path:
        Path(out_path).write_text(output, encoding="utf-8")

    # Выводим статистику в stderr, сам JSON в stdout (если не --out)
    stats = {
        "modules": len(relevant_modules),
        "entities": f"{context['filtered_entities']}/{context['total_entities']}",
        "endpoints": f"{context['filtered_endpoints']}/{context['total_endpoints']}",
        "tables": f"{context['filtered_tables']}/{context['total_tables']}",
    }
    print(json.dumps(stats, ensure_ascii=False), file=sys.stderr)

    if not out_path:
        print(output)


if __name__ == "__main__":
    main()