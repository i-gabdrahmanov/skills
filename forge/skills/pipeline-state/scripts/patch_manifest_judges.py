#!/usr/bin/env python3
"""
patch_manifest_judges.py — добавляет required_judges в существующий манифест,
                           чтобы update.py мог детерминированно блокировать шаги без судей.

Применяется на манифесты, созданные ДО введения required_judges в init.py.

Для каждого шага определяет required_judges по маске id
(имена совпадают с вердиктами run_judge.py — <phase>-judge.json):
  - 02-design         → design-judge
  - 02-eval-plan      → eval-judge
  - 04-test-*         → red-judge
  - 04-build-*        → build-judge
  - 05-tests          → coverage-judge
  - 06-spec           → spec-judge
  - 07-deliver-*      → delivery-judge
  - остальные         → [] (без судей)
"""

import argparse
import json
import sys
from pathlib import Path

REQUIRED_JUDGES_MASK = {
    "02-design":       ["design-judge"],
    "02-eval-plan":    ["eval-judge"],
    "04-test-*":       ["red-judge"],
    "04-build-*":      ["build-judge"],
    "05-tests":        ["coverage-judge"],
    "06-spec":         ["spec-judge"],
    "07-deliver-*":    ["delivery-judge"],
}


def _match_phase(step_id: str) -> list:
    """По id шага определяет required_judges.
    Логика совпадает с init.py: точное совпадение → wildcard *."""
    # Прямое совпадение
    if step_id in REQUIRED_JUDGES_MASK:
        return list(REQUIRED_JUDGES_MASK[step_id])
    # Wildcard-совпадение (заканчивается на *)
    for mask, judges in REQUIRED_JUDGES_MASK.items():
        if mask.endswith("*") and step_id.startswith(mask[:-1]):
            return list(judges)
    return []


def patch_manifest(manifest_path: Path, dry_run: bool = False) -> bool:
    with open(manifest_path) as f:
        manifest = json.load(f)

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