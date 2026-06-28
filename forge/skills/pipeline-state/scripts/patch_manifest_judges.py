#!/usr/bin/env python3
"""
patch_manifest_judges.py — добавляет required_judges в существующий манифест,
                           чтобы update.py мог детерминированно блокировать шаги без судей.

Применяется на манифесты, созданные ДО введения required_judges в init.py.

Маска id→судьи берётся из единого реестра references/judges-registry.json
(judges_registry.match_step) — тот же источник, что и у init.py. Шаги без маски
получают [] (без судей).
"""

import argparse
import json
import sys
from pathlib import Path

import judges_registry
from _util import safe_load_json

# Back-compat: модули doctor/тесты читают REQUIRED_JUDGES_MASK как атрибут.
# Источник — единый реестр (judges-registry.json), не отдельная копия.
REQUIRED_JUDGES_MASK = judges_registry.step_masks()


def _match_phase(step_id: str) -> list:
    """По id шага определяет required_judges из единого реестра judges-registry.json."""
    return judges_registry.match_step(step_id)


def patch_manifest(manifest_path: Path, dry_run: bool = False) -> bool:
    manifest = safe_load_json(manifest_path, what="manifest")

    changed = 0
    for step in manifest.get("steps", []):
        step_id = step.get("id", "")
        existing = step.get("required_judges", [])
        required = _match_phase(step_id)
        if required and not existing:
            step["required_judges"] = required
            changed += 1
        elif required and existing != required:
            # Обновить, если маска изменилась
            step["required_judges"] = required
            changed += 1

    if changed == 0:
        return False

    if dry_run:
        print(f"[dry-run] {manifest_path}: {changed} шагов с новыми required_judges")
        return True

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    tmp.replace(manifest_path)
    print(f"{manifest_path}: {changed} шагов пропатчено")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Путь к manifest.json")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, какие шаги нуждаются в патче")
    args = parser.parse_args()
    patch_manifest(Path(args.manifest), dry_run=args.dry_run)


if __name__ == "__main__":
    main()