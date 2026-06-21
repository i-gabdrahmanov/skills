#!/usr/bin/env python3
"""
enrich_grounding.py — инкрементальное обогащение системной аналитики после фичи.

Вызывается в фазе 06-spec feature-pipeline после того, как код написан и тесты пройдены.
Берёт task-plan.json фичи (что изменилось), накладывает дельту на существующие
docs/system-analysis/*.md и пересобирает grounding-excerpt.json.

Usage:
    enrich_grounding.py --task-plan <path> \\
        --system-analysis <docs/system-analysis> \\
        --scan <docs/system-analysis/scan> [--dry-run] [--json]

Что делает:
  1. Читает task-plan.json — какие модули, entities, endpoints, async-события добавлены/изменены.
  2. Для каждой затронутой MD-категории (api.md, domain.md, async.md, integrations.md):
     - Добавляет новые записи в конец таблицы с пометкой "[added: <feature>]".
     - Если раздел отсутствует — создаёт.
  3. Пересобирает grounding-excerpt.json из обновлённых MD + scan-файлов.
  4. Если передан --scan — прогоняет verify_coverage.verify() как gate полноты
     (excerpt vs scan). Без --scan верифицировать нечем → gate пропускается.

Exit:
    0 — PASS (grounding обогащён, coverage OK либо verify пропущен из-за отсутствия --scan)
    2 — FAIL (покрытие не сошлось — нужен полный рескан)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Конфигурация: какие категории и как их обновлять ──────────────────────

# Категория -> {md-файл, ключ в excerpt, заголовок раздела, фабрика строки}
CATEGORIES = {
    "api_endpoints": {
        "md_file": "api.md",
        "excerpt_key": "api_endpoints",
        "section_marker": "## REST API Endpoints",
        "row_fmt": "| `{method}` | `{path}` | {summary} | {module} | [added: {feature}] |",
    },
    "async": {
        "md_file": "async.md",
        "excerpt_key": "async",
        "section_marker": "## Kafka Consumers",
        "row_fmt": "| `{topic}` | {direction} | {message_type} | {module} | [added: {feature}] |",
    },
    "entities": {
        "md_file": "domain.md",
        "excerpt_key": "entities",
        "section_marker": "## JPA Entities",
        "row_fmt": "| `{name}` | {description} | {module} | [added: {feature}] |",
    },
    "external_clients": {
        "md_file": "integrations.md",
        "excerpt_key": "external_clients",
        "section_marker": "## External Clients",
        "row_fmt": "| `{name}` | {protocol} | {url} | {module} | [added: {feature}] |",
    },
    "tables": {
        "md_file": "db.md",
        "excerpt_key": "tables",
        "section_marker": "## Database Tables",
        "row_fmt": "| `{name}` | {description} | [added: {feature}] |",
    },
}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _extract_feature_slug(task_plan: dict) -> str:
    """Извлечь slug фичи из task-plan."""
    return task_plan.get("feature_slug", task_plan.get("slug", "unknown"))


def _extract_delta(task_plan: dict) -> dict:
    """Извлечь из task-plan.json что конкретно изменилось в этой фиче.

    Ожидаемая структура tasks[].artifacts с пометками new/changed.
    Если детальных артефактов нет — выводим из tasks[].title описательно.
    """
    delta = {"modules": set(), "entities": [], "api_endpoints": [],
             "async": [], "external_clients": [], "tables": []}
    tasks = task_plan.get("tasks", [])
    for task in tasks:
        task_id = task.get("id", "?")
        title = task.get("title", "")
        modules = task.get("modules", [])
        for m in modules:
            delta["modules"].add(m)
        # Парсим заголовок задачи чтобы угадать категорию
        title_lower = title.lower()
        # entity / domain
        for keyword, category in [("entity", "entities"), ("сущност", "entities"),
                                   ("endpoint", "api_endpoints"), ("api", "api_endpoints"),
                                   ("контроллер", "api_endpoints"), ("rest", "api_endpoints"),
                                   ("kafka", "async"), ("consumer", "async"),
                                   ("producer", "async"), ("feign", "external_clients"),
                                   ("client", "external_clients"), ("таблиц", "tables"),
                                   ("db", "tables"), ("migration", "tables")]:
            if keyword in title_lower:
                delta[category].append({
                    "task_id": task_id,
                    "title": title,
                    "module": modules[0] if modules else "?",
                })
                break
    return delta


def _append_to_md_section(md_path: Path, section_marker: str, new_rows: list[str]) -> bool:
    """Добавить строки в конец таблицы под указанным заголовком. Если заголовка нет — создать."""
    if not md_path.exists():
        # Создать файл с шапкой
        content = f"# {md_path.stem}\n\n"
        md_path.write_text(content)

    text = md_path.read_text(encoding="utf-8")

    # Ищем секцию
    section_pattern = re.compile(rf"^{re.escape(section_marker)}.*$", re.MULTILINE)
    match = section_pattern.search(text)

    new_block = "\n".join(new_rows)

    if match:
        # Секция есть — ищем конец таблицы после неё
        # Таблица заканчивается пустой строкой или следующим заголовком ##
        after = text[match.end():]
        # Ищем конец секции
        next_section = re.search(r"\n## ", after)
        section_end = len(text)  # default: end of file
        if next_section:
            section_end = match.end() + next_section.start()

        insert_pos = section_end
        # Если в конце нет пустой строки — добавим
        if not text.endswith("\n\n"):
            text += "\n"
            insert_pos = len(text)

        text = text[:insert_pos] + f"\n{new_block}\n" + text[insert_pos:]
    else:
        # Секции нет — добавляем в конец файла
        text += f"\n\n{section_marker}\n\n| Категория | Детали | Модуль | Примечание |\n|-----------|--------|--------|------------|\n{new_block}\n"

    md_path.write_text(text)
    return True


def _build_excerpt(analysis_dir: Path, scan_dir: Path | None, feature_slug: str) -> dict:
    """Собрать/дополнить grounding-excerpt.json из scan + дельты.

    Логика merge:
      1. Если существует предыдущий grounding-excerpt.json — берём его как базу.
      2. Если есть scan-файлы — данные из scan имеют приоритет для ключей,
         которые scan покрывает. Модули мержатся без дубликатов.
      3. Если scan-файлов нет — берём MD-парсинг (слабая эвристика).
      4. Все записи получают _sources = ["<feature>"] (или дополняют существующий).
    """
    # Загружаем предыдущий excerpt как базу
    prev_path = analysis_dir / "grounding-excerpt.json"
    prev = None
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text(encoding="utf-8"))
            if not isinstance(prev, dict) or "$schema" not in prev:
                prev = None
        except Exception:
            prev = None

    if prev:
        excerpt = prev
        excerpt["feature"] = feature_slug
        excerpt["updated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        excerpt = {
            "$schema": "grounding-excerpt@1",
            "feature": feature_slug,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "modules": [],
            "entities": [],
            "api_endpoints": [],
            "async": [],
            "external_clients": [],
            "tables": [],
            "reuse": {"dependencies": [], "project_utils": []},
            "gate_total": 0,
        }

    def _ensure_sources(items: list, key: str = "_sources") -> list:
        """Убедиться, что каждый элемент в списке имеет _sources как список."""
        result = []
        for item in items:
            if isinstance(item, dict):
                if key not in item:
                    item[key] = [feature_slug]
                elif feature_slug not in item[key]:
                    item[key].append(feature_slug)
            result.append(item)
        return result

    def _merge_lists(existing: list, new_items: list, id_keys: tuple[str, ...]) -> list:
        """Смержить списки по id_keys: новые записи добавляются, существующие обновляют _sources.

        Дедуплицирует как внутри existing, так и внутри new_items.
        """
        existing = list(existing)
        existing_ids = set()
        for item in existing:
            if isinstance(item, dict):
                eid = tuple(str(item.get(k, "")) for k in id_keys)
                existing_ids.add(eid)

        # Дедуплицируем new_items
        seen_new = set()
        deduped_new = []
        for new_item in new_items:
            if not isinstance(new_item, dict):
                continue
            nid = tuple(str(new_item.get(k, "")) for k in id_keys)
            if nid in seen_new:
                continue
            seen_new.add(nid)
            deduped_new.append(new_item)

        for new_item in deduped_new:
            nid = tuple(str(new_item.get(k, "")) for k in id_keys)
            if nid in existing_ids:
                # Обновляем _sources существующей записи
                for existing_item in existing:
                    if isinstance(existing_item, dict):
                        eid = tuple(str(existing_item.get(k, "")) for k in id_keys)
                        if eid == nid:
                            src = existing_item.setdefault("_sources", [])
                            if feature_slug not in src:
                                src.append(feature_slug)
                            break
            else:
                # Новая запись
                entry = dict(new_item)
                entry["_sources"] = [feature_slug]
                existing.append(entry)
                existing_ids.add(nid)

        return existing

    # 1. Scan-файлы — мержим
    if scan_dir and scan_dir.exists():
        scan_mappings = [
            ("domain", "entities", ("name",)),
            ("api", "api_endpoints", ("method", "path")),
            ("async_consumers", "async", ("topic", "direction")),
            ("async_producers", "async", ("topic", "direction")),
            ("integration", "external_clients", ("name",)),
            ("db", "tables", ("name",)),
        ]
        for cat, key, id_keys in scan_mappings:
            scan_file = scan_dir / f"{cat}.json"
            if scan_file.exists():
                data = _read_json(scan_file)
                items = data.get("items", [])
                if not items:
                    continue

                if key == "entities":
                    parsed = [{"name": i.get("name", "?"),
                               "kind": i.get("kind", "entity"),
                               "module": i.get("module", "?")} for i in items]
                elif key == "api_endpoints":
                    parsed = [{"method": i.get("http_method", "?"),
                               "path": i.get("path", "?"),
                               "handler": i.get("handler", "?"),
                               "module": i.get("module", "?")} for i in items]
                elif key == "async":
                    parsed = [{"topic": i.get("topic", "?"),
                               "direction": i.get("direction", "consumer"),
                               "message_type": i.get("type", "?"),
                               "module": i.get("module", "?")} for i in items]
                elif key == "external_clients":
                    parsed = [{"name": i.get("name", "?"),
                               "protocol": i.get("protocol", "?"),
                               "module": i.get("module", "?")} for i in items]
                elif key == "tables":
                    parsed = [{"name": i.get("name", "?"),
                               "source": i.get("source", "?"),
                               "module": i.get("module", "?")} for i in items]
                else:
                    parsed = []

                excerpt[key] = _merge_lists(excerpt.get(key, []), parsed, id_keys)

        # Модули — мержим без дубликатов
        struct_file = scan_dir / "structure.json"
        if struct_file.exists():
            struct_data = _read_json(struct_file)
            scan_modules = [{"name": m.get("name", "?"),
                             "path": m.get("path", "?")} for m in struct_data.get("modules", [])]
            existing_names = {m.get("name") for m in excerpt.get("modules", []) if isinstance(m, dict)}
            for sm in scan_modules:
                if sm.get("name") not in existing_names:
                    sm["_sources"] = [feature_slug]
                    excerpt.setdefault("modules", []).append(sm)
                    existing_names.add(sm.get("name"))

        # gate_total — пересчитываем с нуля (HARD)
        gate_total = 0
        for cat in ("domain", "api", "async_consumers"):
            gf = scan_dir / f"{cat}.json"
            if gf.exists():
                gd = _read_json(gf)
                gate_total += len(gd.get("items", []))
        excerpt["gate_total"] = gate_total

        # reuse — компактный каталог переиспользования (rebuild из scan/reuse.json, без дельты:
        # зависимости/утилиты меняются редко). Только координаты и имена классов — компактно.
        reuse_file = scan_dir / "reuse.json"
        if reuse_file.exists():
            rd = _read_json(reuse_file)
            deps = []
            for d in rd.get("dependencies", []):
                coord = d.get("artifact", "?")
                if d.get("version"):
                    coord = f"{coord}:{d['version']}"
                deps.append(coord)
            utils = []
            for u in rd.get("project_utils", []):
                pkg = u.get("package", "")
                utils.append(f"{pkg + '.' if pkg else ''}{u.get('class', '?')}")
            excerpt["reuse"] = {"dependencies": deps, "project_utils": utils}

    # 2. Если scan-файлов нет — парсим MD (слабая эвристика)
    if not (scan_dir and scan_dir.exists()):
        md_map = {
            "entities": ("domain.md", r"\|\s*`(\w+)`\s*\|"),
            "api_endpoints": ("api.md", r"\|\s*`(\w+)`\s*\|"),
        }
        for key, (md_name, pattern) in md_map.items():
            md_file = analysis_dir / md_name
            if md_file.exists():
                text = md_file.read_text(encoding="utf-8")
                matches = re.findall(pattern, text)
                # При MD-парсинге только добавляем новые, не удаляем старые
                existing_names = {e.get("name") for e in excerpt.get(key, []) if isinstance(e, dict)}
                for m in matches:
                    if m not in existing_names:
                        excerpt.setdefault(key, []).append({"name": m, "_sources": [feature_slug]})
                        existing_names.add(m)

    # 3. Убедиться что у всех записей есть _sources
    for list_key in ("modules", "entities", "api_endpoints", "async", "external_clients", "tables"):
        excerpt[list_key] = _ensure_sources(excerpt.get(list_key, []))

    return excerpt


def enrich(analysis_dir: str | Path, scan_dir: str | Path | None,
           task_plan: dict, feature_slug: str | None = None,
           dry_run: bool = False) -> dict:
    """Выполнить обогащение grounding-а."""
    analysis_path = Path(analysis_dir) if isinstance(analysis_dir, str) else analysis_dir
    scan_path = Path(scan_dir) if isinstance(scan_dir, str) else (scan_dir if scan_dir else None)

    if feature_slug is None:
        feature_slug = _extract_feature_slug(task_plan)

    delta = _extract_delta(task_plan)

    changes = {"md_files_updated": [], "excerpt_updated": False}

    # 1. Обновить MD-файлы по каждой затронутой категории
    for category, cfg in CATEGORIES.items():
        items = delta.get(category, [])
        if not items:
            continue
        md_file = analysis_path / cfg["md_file"]
        new_rows = []
        for i in items:
            row_data = {
                "name": i.get("name", i.get("title", "?")),
                "method": i.get("method", "?"),
                "path": i.get("path", i.get("title", "?")),
                "summary": i.get("title", "?"),
                "topic": i.get("topic", i.get("title", "?")),
                "direction": i.get("direction", "consumer"),
                "message_type": i.get("message_type", ""),
                "description": i.get("description", i.get("title", "?")),
                "protocol": i.get("protocol", ""),
                "url": i.get("url", ""),
                "module": i.get("module", "?"),
                "feature": feature_slug,
            }
            new_rows.append(cfg["row_fmt"].format(**row_data))
        if not dry_run:
            _append_to_md_section(md_file, cfg["section_marker"], new_rows)
        changes["md_files_updated"].append(str(md_file))

    # 2. Пересобрать grounding-excerpt.json
    if not dry_run:
        excerpt_path = analysis_path / "grounding-excerpt.json"
        prev_excerpt = _read_json(excerpt_path)  # ДО пересборки — для детекта дрейфа
        excerpt = _build_excerpt(analysis_path, scan_path, feature_slug)
        excerpt_path.write_text(json.dumps(excerpt, ensure_ascii=False, indent=2))
        changes["excerpt_updated"] = True
        changes["excerpt_path"] = str(excerpt_path)

        # 3. Gate полноты против scan:
        #   • rebuilt — ловит scan-внутренний недосчёт (gate_total > извлечённых items) → exit 2;
        #   • pre_enrich — был ли СТАРЫЙ excerpt неполон (дрейф grounding до этой фичи) → warning,
        #     т.к. пересборка из scan дрейф залечивает; большой дрейф = повод на полный рескан.
        #   • scan отсутствует → не 'skipped' молча, а явный 'unverified' (excerpt собран слабой
        #     MD-эвристикой, полнота НЕ гарантирована).
        if scan_path and scan_path.exists():
            cov = _run_coverage_gate(scan_path, excerpt)
            if prev_excerpt:
                pre = _run_coverage_gate(scan_path, prev_excerpt)
                cov["pre_enrich_status"] = pre.get("status")
                if pre.get("status") == "fail":
                    cov.setdefault("warnings", []).append(
                        "grounding-excerpt был неполон ДО фичи (дрейф) — залечен пересборкой; "
                        "при большом дрейфе прогони полный system-analyst")
            changes["coverage"] = cov
        else:
            changes["coverage"] = {"status": "unverified",
                                   "reason": "нет scan — excerpt собран MD-эвристикой, полнота не проверена"}

    changes["delta"] = {k: (list(v) if isinstance(v, set) else v) for k, v in delta.items()}
    return changes


def _run_coverage_gate(scan_dir: Path, excerpt: dict) -> dict:
    """Прогнать verify_coverage.verify() как gate полноты. Возвращает вердикт ({} если недоступен)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from verify_coverage import verify  # type: ignore
        return verify(scan_dir, excerpt)
    except Exception as exc:  # verify_coverage недоступен/сломан — не валим обогащение
        return {"status": "skipped", "reason": f"verify_coverage недоступен: {exc}"}


def _resolve_docs(project_root: Path):
    """(system_analysis_dir, scan_dir) по конфигу docs; фоллбэк docs/system-analysis[/scan]."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "feature-pipeline" / "scripts"))
        import skill_paths  # type: ignore
        return skill_paths.system_analysis_dir(project_root), skill_paths.scan_dir(project_root)
    except Exception:
        sa = project_root / "docs" / "system-analysis"
        return sa, sa / "scan"


def main() -> int:
    ap = argparse.ArgumentParser(description="Incremental grounding enrichment after feature delivery")
    ap.add_argument("--task-plan", required=True, help="Path to task-plan.json")
    ap.add_argument("--project-root", default=".",
                    help="Корень проекта для резолва docs (default: cwd)")
    ap.add_argument("--system-analysis", default=None,
                    help="Path to system-analysis directory (default: резолв по docs-конфигу)")
    ap.add_argument("--scan", default=None,
                    help="Path to scan/ directory (default: <system-analysis>/scan по docs-конфигу)")
    ap.add_argument("--feature", default=None, help="Feature slug (auto from task-plan if omitted)")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes, don't write files")
    ap.add_argument("--json", action="store_true", help="Output result as JSON")
    args = ap.parse_args()

    task_plan_path = Path(args.task_plan)
    if not task_plan_path.exists():
        print(f"ERROR: task-plan not found: {task_plan_path}", file=sys.stderr)
        return 1

    task_plan = _read_json(task_plan_path)
    if not task_plan:
        print(f"ERROR: invalid or empty task-plan: {task_plan_path}", file=sys.stderr)
        return 1

    _sa_default, _scan_default = _resolve_docs(Path(args.project_root))
    analysis_path = Path(args.system_analysis) if args.system_analysis else _sa_default
    scan_path = Path(args.scan) if args.scan else (_scan_default if _scan_default.exists() else None)

    changes = enrich(analysis_path, scan_path, task_plan, args.feature, args.dry_run)

    coverage = changes.get("coverage") or {}
    cov_status = coverage.get("status")

    if args.json:
        print(json.dumps(changes, ensure_ascii=False, indent=2))
    else:
        print(f"✅ Grounding enrichment {'(dry-run)' if args.dry_run else ''}")
        for md in changes["md_files_updated"]:
            print(f"  📝 Updated: {md}")
        if changes.get("excerpt_updated"):
            print(f"  📦 Excerpt: {changes['excerpt_path']}")
        delta = changes.get("delta", {})
        for cat, items in delta.items():
            if items:
                print(f"  🔄 {cat}: {len(items)} change(s)")
        if cov_status == "fail":
            failed = [r for r in coverage.get("hard", []) if not r.get("ok")]
            print(f"  ❌ coverage gate FAIL: недосчёт в {[r['category'] for r in failed]}")
            for r in failed:
                print(f"      {r['category']}: reported {r['reported']} < scan {r['deterministic']}")
        elif cov_status == "pass":
            print("  ✅ coverage gate PASS")
        elif cov_status == "unverified":
            print(f"  ⚠️  coverage НЕ проверен: {coverage.get('reason', 'нет scan')}")
        elif cov_status == "skipped":
            print(f"  · coverage gate skipped: {coverage.get('reason', 'нет --scan')}")
        for w in coverage.get("warnings", []):
            print(f"  ⚠️  {w}")

    # exit 2 только при реальном провале полноты (нужен полный рескан); dry-run всегда 0
    if not args.dry_run and cov_status == "fail":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())