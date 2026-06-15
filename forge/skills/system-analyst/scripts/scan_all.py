#!/usr/bin/env python3
"""scan_all.py — детерминированный системный скан (ground truth для самопроверки).

Прогоняет все сканеры по одному или нескольким корням проекта, приписывает находки
к модулям и пишет per-category JSON + summary.json. Без LLM, без лимитов токенов,
без compact-усечения — поэтому recall ≈ 100% по механическим артефактам.

Usage:
    scan_all.py <root> [<root2> ...] [-o <out-dir>] [--quiet]

HARD-категории (точный счёт, gate падает при reported < deterministic):
    domain (entities), api (endpoints), async_consumers (@KafkaListener).
ADVISORY-категории (нечёткая семантика «что считать единицей» — gate только предупреждает):
    async_producers, integration, config, cross_cutting, db (tables).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_mod  # noqa: E402
import cross_cutting  # noqa: E402
import db  # noqa: E402
import domain  # noqa: E402
import endpoints as endpoints_mod  # noqa: E402
import integration  # noqa: E402
import kafka as kafka_mod  # noqa: E402
import reuse as reuse_mod  # noqa: E402
import structure  # noqa: E402
from common import attribute_module, repo_root  # noqa: E402

HARD = {"domain", "api", "async_consumers"}


def _attribute(items: list[dict], index, prefix: str = "") -> dict:
    counts: dict[str, int] = {}
    for it in items:
        f = it.get("file")
        mod = attribute_module(Path(f), index) if f else "?"
        if prefix:
            mod = f"{prefix}:{mod}"
        it["module"] = mod
        counts[mod] = counts.get(mod, 0) + 1
    return counts


def _cat(name: str, items: list[dict], index, prefix: str) -> dict:
    return {"category": name, "hard": name in HARD, "total": len(items),
            "gate_total": len(items),
            "counts_by_module": _attribute(items, index, prefix), "items": items}


def scan_root(root: Path, prefix: str = "") -> dict:
    root = root.resolve()
    struct = structure.scan(root)
    index = structure.module_dir_index(root, struct)

    ep_items: list[dict] = []
    for c in endpoints_mod.scan(root):
        for e in c.endpoints:
            ep_items.append({"controller": c.class_name, "http_method": e.http_method,
                             "path": e.path, "handler": e.method_name,
                             "return_type": e.return_type, "file": c.file})

    km = kafka_mod.scan(root)
    consumers = [{**dataclasses.asdict(c), "direction": "consumer"} for c in km.consumers]
    producers = [{**dataclasses.asdict(p), "direction": "producer"} for p in km.producers]

    cfg = config_mod.scan(root)
    dbres = db.scan(root)
    db_items = [{"name": t, "source": dbres["table_sources"][t], "file": None} for t in dbres["tables"]]

    if prefix:
        for m in struct["modules"]:
            m["name"] = f"{prefix}:{m['name']}"

    cats = {
        "structure": {"category": "structure", "hard": False,
                      "total": len([m for m in struct["modules"] if m["has_src_main"]]),
                      "build_system": struct["build_system"], "is_multi_module": struct["is_multi_module"],
                      "spring_boot_version": struct["spring_boot_version"], "java_version": struct["java_version"],
                      "spring_boot_applications": struct["spring_boot_applications"], "modules": struct["modules"]},
        "domain": _cat("domain", domain.scan(root), index, prefix),
        "api": _cat("api", ep_items, index, prefix),
        "async_consumers": _cat("async_consumers", consumers, index, prefix),
        "async_producers": _cat("async_producers", producers, index, prefix),
        "integration": _cat("integration", integration.scan(root), index, prefix),
        "cross_cutting": _cat("cross_cutting", cross_cutting.scan(root), index, prefix),
        "config": {"category": "config", "hard": False, "total": len(cfg["files"]),
                   "profiles": cfg["profiles"], "counts_by_module": _attribute(cfg["files"], index, prefix),
                   "items": cfg["files"]},
        "db": {"category": "db", "hard": False, "total": len(db_items), "gate_total": len(db_items),
               "migration_tool": dbres["migration_tool"], "migration_count": dbres["migration_count"],
               "counts_by_module": {}, "items": db_items},
    }

    # reuse — каталог переиспользования (ADVISORY): внешние зависимости + util-классы проекта.
    dep_items = reuse_mod.scan_dependencies(root)
    util_items = reuse_mod.scan_project_utils(root)
    dep_counts = _attribute(dep_items, index, prefix)
    util_counts = _attribute(util_items, index, prefix)
    reuse_counts = {m: dep_counts.get(m, 0) + util_counts.get(m, 0)
                    for m in set(dep_counts) | set(util_counts)}
    cats["reuse"] = {"category": "reuse", "hard": False,
                     "total": len(dep_items) + len(util_items),
                     "gate_total": len(dep_items) + len(util_items),
                     "dependencies": dep_items, "project_utils": util_items,
                     "counts_by_module": reuse_counts, "items": []}
    # gate_total для domain = только @Entity (без mapped_superclass), чтобы сверка была like-for-like.
    cats["domain"]["gate_total"] = sum(1 for i in cats["domain"]["items"] if i.get("kind") == "entity")
    return cats


def _merge(into: dict, src: dict) -> None:
    for name, cat in src.items():
        if name not in into:
            into[name] = cat
            continue
        if name == "structure":
            into[name]["modules"].extend(cat["modules"])
            into[name]["spring_boot_applications"] += cat["spring_boot_applications"]
            into[name]["total"] += cat["total"]
            into[name]["is_multi_module"] = True
        else:
            into[name]["items"].extend(cat["items"])
            into[name]["total"] += cat["total"]
            if "gate_total" in cat:
                into[name]["gate_total"] = into[name].get("gate_total", 0) + cat["gate_total"]
            for k, v in cat.get("counts_by_module", {}).items():
                into[name]["counts_by_module"][k] = into[name]["counts_by_module"].get(k, 0) + v
            if "profiles" in cat:
                into[name]["profiles"] = sorted(set(into[name].get("profiles", [])) | set(cat["profiles"]))
            # reuse держит две под-группы вместо items — сливаем их отдельно (multi-root)
            for sub in ("dependencies", "project_utils"):
                if sub in cat:
                    into[name].setdefault(sub, []).extend(cat[sub])


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic system scan (ground truth for self-check).")
    ap.add_argument("roots", nargs="*", help="project root(s) (default: git toplevel или cwd)")
    ap.add_argument("-o", "--out", default=None,
                    help="output dir (default: <root>/ground/statements/system-analysis/scan)")
    ap.add_argument("--quiet", action="store_true", help="do not print the summary table")
    args = ap.parse_args()

    roots = [Path(r).resolve() for r in (args.roots or [repo_root()])]
    multi = len(roots) > 1
    cats: dict = {}
    for r in roots:
        if not r.exists():
            print(f"ERROR: root not found: {r}", file=sys.stderr)
            return 1
        _merge(cats, scan_root(r, prefix=r.name if multi else ""))

    out = Path(args.out) if args.out else roots[0] / "ground/statements/system-analysis/scan"
    out.mkdir(parents=True, exist_ok=True)
    for name, cat in cats.items():
        (out / f"{name}.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {
        "modules": cats["structure"]["total"],
        "entities": cats["domain"]["total"],
        "endpoints": cats["api"]["total"],
        "kafka_consumers": cats["async_consumers"]["total"],
        "kafka_producers": cats["async_producers"]["total"],
        "external_clients": cats["integration"]["total"],
        "config_files": cats["config"]["total"],
        "profiles": len(cats["config"].get("profiles", [])),
        "cross_cutting": cats["cross_cutting"]["total"],
        "tables": cats["db"]["total"],
        "dependencies": len(cats["reuse"]["dependencies"]),
        "project_utils": len(cats["reuse"]["project_utils"]),
    }
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": [str(r) for r in roots],
        "hard_categories": sorted(HARD),
        "counts": counts,
        "out_dir": str(out),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        print(f"Deterministic scan → {out}")
        for k, v in counts.items():
            tag = " (HARD)" if k in ("entities", "endpoints", "kafka_consumers") else ""
            print(f"  {k:18} {v}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
