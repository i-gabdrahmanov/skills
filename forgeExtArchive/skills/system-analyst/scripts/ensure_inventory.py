#!/usr/bin/env python3
"""ensure_inventory.py — снять инвентарь проекта (топливо детерминированных гейтов).

Инвентарь — это НЕ документация и НЕ артефакт поставки. Это машинный срез того, что в коде
есть на самом деле: модули, классы по слоям, entity, эндпойнты, топики, таблицы, каталог
переиспользования. Его единственный потребитель — гейты, которым нужен список настоящих имён,
чтобы поймать выдуманное: `check_taskplan` (reuses/модули), `check_architecture` (граф
модулей), reuse-judge. Агент и сам грепнет класс за две секунды — но загейтить «мог бы
грепнуть» нельзя.

Поэтому инвентарь ЭФЕМЕРНЫЙ: живёт в `ground/inventory/` (каталог самоигнорирующийся), в git
не едет, снимается заново когда устарел. Так снимается разом весь класс проблем прошлой
схемы, где он лежал закоммиченным в общем спек-репо: конфликты на каждый merge, дрейф
относительно кода, слой «инкрементального обогащения» ради свежести и абсолютные пути машины
в чужом репозитории.

Идемпотентен: без изменений в коде второй прогон ничего не делает.

Usage:
    ensure_inventory.py [--root <project>] [--force] [--json] [--quiet]

Exit:
    0 — инвентарь готов
    2 — снят, но пуст (ни одного модуля и ни одной entity) — сканировать нечего либо
        передан не тот корень; гейтам такое скармливать нельзя
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import scan_all  # noqa: E402
from common import iter_files, repo_root  # noqa: E402

# Файлы, изменение которых делает инвентарь устаревшим.
SOURCE_SUFFIXES = (".java", ".kt", ".gradle", ".kts", ".xml", ".sql", ".yaml", ".yml", ".properties")

GITIGNORE = ("# Эфемерный инвентарь проекта — снимается ensure_inventory.py за секунды.\n"
             "# В git не едет: это производное от кода, а не документация.\n*\n")


def _resolve_dirs(root: Path) -> tuple[Path, Path, Path]:
    """(inventory_dir, scan_dir, excerpt_path) по единому резолверу."""
    try:
        sys.path.insert(0, str(SCRIPTS.parents[1] / "feature-pipeline" / "scripts"))
        import skill_paths  # type: ignore
        return (skill_paths.inventory_dir(root), skill_paths.scan_dir(root),
                skill_paths.grounding_excerpt_path(root))
    except Exception:  # резолвер недоступен — та же раскладка по умолчанию
        inv = root / "ground" / "inventory"
        return inv, inv / "scan", inv / "grounding-excerpt.json"


def fingerprint(root: Path) -> dict:
    """Отпечаток исходников: сколько файлов и когда самый свежий.

    Одного времени мало: удаление файла не двигает mtime остальных, и инвентарь считался бы
    актуальным, продолжая держать призрака удалённой сущности — ровно то, ради чего в старой
    схеме городили authoritative-замену из scan. Счётчик ловит и добавление, и удаление,
    mtime — правку на месте. Обход + stat на порядок дешевле полного скана.
    """
    count, newest = 0, 0.0
    for p in iter_files(root, SOURCE_SUFFIXES):
        count += 1
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest:
            newest = m
    return {"files": count, "newest": round(newest, 3)}


def is_stale(root: Path, inv: Path, scan_path: Path) -> tuple[bool, str]:
    if not (scan_path / "summary.json").exists():
        return True, "инвентарь не снят"
    fp_path = inv / ".fingerprint.json"
    if not fp_path.exists():
        return True, "нет отпечатка исходников"
    try:
        prev = json.loads(fp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, "отпечаток нечитаем"
    now = fingerprint(root)
    if prev.get("files") != now["files"]:
        return True, f"состав исходников изменился ({prev.get('files')} → {now['files']} файлов)"
    if now["newest"] > prev.get("newest", 0):
        return True, "код изменился после снятия инвентаря"
    return False, "инвентарь актуален"


# ── проекция scan → excerpt ───────────────────────────────────────────────────
# Компактный срез для tech-design и для фоллбэка гейтов. Строго производная от scan:
# никакого ручного ввода, никакой истории — иначе снова получим артефакт, который надо
# «поддерживать в свежести».

def _items(scan_path: Path, cat: str) -> list[dict]:
    p = scan_path / f"{cat}.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [i for i in data.get("items", []) if isinstance(i, dict)]


def _dedup_sorted(rows: list[dict], id_keys: tuple[str, ...]) -> list[dict]:
    """Порядок — по id-ключам, а не по обходу файловой системы: одинаковый код обязан давать
    одинаковый байт-в-байт инвентарь на любой машине."""
    seen, out = set(), []
    for r in rows:
        nid = tuple(str(r.get(k, "")) for k in id_keys)
        if nid in seen:
            continue
        seen.add(nid)
        out.append(r)
    out.sort(key=lambda r: tuple(str(r.get(k, "")) for k in id_keys))
    return out


def build_excerpt(scan_path: Path) -> dict:
    struct = {}
    sp = scan_path / "structure.json"
    if sp.exists():
        try:
            struct = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            struct = {}

    modules = _dedup_sorted(
        [{"name": m.get("name", "?"), "path": m.get("path", "?"),
          "depends_on": m.get("depends_on", [])}
         for m in struct.get("modules", []) if isinstance(m, dict)], ("name",))

    entities = _dedup_sorted(
        [{"name": i.get("name", "?"), "kind": i.get("kind", "entity"),
          "module": i.get("module", "?")} for i in _items(scan_path, "domain")], ("name",))

    components = _dedup_sorted(
        [{"name": i.get("name", "?"), "layer": i.get("layer", "?"),
          "package": i.get("package", ""), "module": i.get("module", "?")}
         for i in _items(scan_path, "components")], ("name", "layer"))

    api = _dedup_sorted(
        [{"method": i.get("http_method", "?"), "path": i.get("path", "?"),
          "handler": i.get("handler", "?"), "module": i.get("module", "?")}
         for i in _items(scan_path, "api")], ("method", "path"))

    async_rows = []
    for cat, direction in (("async_consumers", "consumer"), ("async_producers", "producer")):
        async_rows += [{"topic": i.get("topic", "?"), "direction": i.get("direction", direction),
                        "message_type": i.get("type", "?"), "module": i.get("module", "?")}
                       for i in _items(scan_path, cat)]
    async_items = _dedup_sorted(async_rows, ("topic", "direction"))

    clients = _dedup_sorted(
        # scan-сканер интеграций отдаёт вид клиента в `type` (feign/webclient/grpc/…).
        [{"name": i.get("name", "?"), "protocol": i.get("type") or i.get("protocol") or "?",
          "target": i.get("target", ""), "module": i.get("module", "?")}
         for i in _items(scan_path, "integration")], ("name",))

    tables = _dedup_sorted(
        [{"name": i.get("name", "?"), "source": i.get("source", "?")}
         for i in _items(scan_path, "db")], ("name",))

    deps, utils = [], []
    rp = scan_path / "reuse.json"
    if rp.exists():
        try:
            rd = json.loads(rp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rd = {}
        for d in rd.get("dependencies", []):
            coord = d.get("artifact", "?")
            if d.get("version"):
                coord = f"{coord}:{d['version']}"
            deps.append(coord)
        for u in rd.get("project_utils", []):
            pkg = u.get("package", "")
            utils.append(f"{pkg + '.' if pkg else ''}{u.get('class', '?')}")

    gate_total = sum(len(_items(scan_path, c)) for c in ("domain", "api", "async_consumers"))

    return {
        "$schema": "grounding-excerpt@2",
        "modules": modules,
        "entities": entities,
        "components": components,
        "api_endpoints": api,
        "async": async_items,
        "external_clients": clients,
        "tables": tables,
        "reuse": {"dependencies": sorted(set(deps)), "project_utils": sorted(set(utils))},
        "gate_total": gate_total,
    }


def ensure(root: Path, force: bool = False) -> dict:
    inv, scan_path, excerpt_path = _resolve_dirs(root)
    stale, reason = (True, "--force") if force else is_stale(root, inv, scan_path)

    inv.mkdir(parents=True, exist_ok=True)
    gi = inv / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")

    if stale:
        scan_all.write_scan([root.resolve()], scan_path)
        # Отпечаток пишем ПОСЛЕ скана: если скан упадёт, инвентарь останется помечен
        # устаревшим и следующий прогон попробует снова, а не сочтёт мусор актуальным.
        (inv / ".fingerprint.json").write_text(
            json.dumps(fingerprint(root), ensure_ascii=False), encoding="utf-8")
    excerpt = build_excerpt(scan_path)

    # Пишем, только если содержимое изменилось: лишняя перезапись сбивает mtime, по которому
    # определяется устаревание, и даёт шум там, где инвентарь всё-таки закоммитили.
    payload = json.dumps(excerpt, ensure_ascii=False, indent=2)
    if not excerpt_path.exists() or excerpt_path.read_text(encoding="utf-8") != payload:
        excerpt_path.write_text(payload, encoding="utf-8")

    substantive = bool(excerpt["modules"] or excerpt["entities"])
    return {
        "status": "ok" if substantive else "empty",
        "rescanned": stale,
        "reason": reason,
        "inventory_dir": str(inv),
        "scan_dir": str(scan_path),
        "excerpt_path": str(excerpt_path),
        "counts": {
            "modules": len(excerpt["modules"]), "entities": len(excerpt["entities"]),
            "components": len(excerpt["components"]), "endpoints": len(excerpt["api_endpoints"]),
            "async": len(excerpt["async"]), "external_clients": len(excerpt["external_clients"]),
            "tables": len(excerpt["tables"]),
        },
        "cross_check": _cross_check(root, excerpt),
    }


def _cross_check(root: Path, excerpt: dict) -> dict:
    """Грубый счёт по коду как нижняя граница против недосчёта самого сканера.

    Сверка excerpt'а со scan была бы тавтологией — excerpt из scan и построен. А вот
    независимый пересчёт @Entity/@KafkaListener/@*Mapping ловит дыру в самом сканере.
    """
    try:
        from verify_coverage import cross_check  # type: ignore
        raw = cross_check(root)
    except Exception as exc:
        return {"status": "skipped", "reason": str(exc)}
    got = {"domain": len(excerpt["entities"]),
           "api": len(excerpt["api_endpoints"]),
           "async_consumers": len([a for a in excerpt["async"] if a["direction"] == "consumer"])}
    under = {k: {"scanner": got.get(k, 0), "grep": v}
             for k, v in raw.items() if got.get(k, 0) < v}
    return {"status": "warn" if under else "ok", "undercount": under}


def main() -> int:
    ap = argparse.ArgumentParser(description="Снять эфемерный инвентарь проекта для гейтов.")
    ap.add_argument("--root", default=None, help="корень репо кода (default: git toplevel или cwd)")
    ap.add_argument("--force", action="store_true", help="пересканировать, даже если не устарел")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(repo_root())
    if not root.exists():
        print(f"ERROR: корень не найден: {root}", file=sys.stderr)
        return 1

    res = ensure(root, force=args.force)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif not args.quiet:
        c = res["counts"]
        head = "🔁 инвентарь снят заново" if res["rescanned"] else "· инвентарь актуален"
        print(f"{head} ({res['reason']}): {res['inventory_dir']}")
        print(f"  {c['modules']} модулей, {c['entities']} entities, {c['components']} классов, "
              f"{c['endpoints']} endpoints, {c['async']} топиков, "
              f"{c['external_clients']} клиентов, {c['tables']} таблиц")
        cc = res["cross_check"]
        if cc.get("status") == "warn":
            print(f"  ⚠️  сканер недосчитал: {cc['undercount']} — возможна дыра в сканере")

    if res["status"] == "empty":
        print("ERROR: инвентарь пуст (0 модулей и 0 entities) — не тот корень или сканировать нечего",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
