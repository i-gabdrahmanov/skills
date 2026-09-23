#!/usr/bin/env python3
"""
Append steps to an existing pipeline manifest (idempotent).

Usage:
    add_steps.py --project <path> --skill <name> --steps <json-string-or-@file>

Steps format (JSON array) — same shape as init.py:
    [
      {"id": "04-build-T1", "title": "Build T1", "depends_on": ["02-design"]},
      {"id": "05-tests",    "title": "Tests green", "depends_on": ["04-build-T1"]}
    ]

Use this when the step list is only known mid-run (e.g. feature-pipeline learns the
task breakdown after the design phase and must add `04-build-<taskId>` / `05-tests`).

Idempotent: steps whose `id` already exists are left untouched and reported as skipped,
so it is safe to re-run after a resume. Fails if the manifest does not exist yet
(run init.py first). Does not modify existing steps' status or output.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# `_util` живёт в двух scripts-каталогах (skills/config-helper/scripts и здесь) с разным
# содержимым. `hooks/risk_ladder.py` предзагружает config-helperский `_util` в sys.modules
# при первом импорте любого хука — и без сброса `from _util import …` ниже берёт ЧУЖОЙ
# модуль (нет repo_root/safe_load_json → ImportError при сборе pytest).
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != _HERE:
    del sys.modules["_util"]

from _util import repo_root  # noqa: E402

# Единый источник истины фаз/судей — pipeline_phases (из feature-pipeline/scripts).
# best-effort: pipeline-state может жить отдельно — тогда судьи/gate не трогаем.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "feature-pipeline" / "scripts"))
    import pipeline_phases as pp
except Exception:  # pragma: no cover
    pp = None


def load_json_arg(value: str):
    """Accepts either inline JSON or @<filepath>."""
    if value.startswith("@"):
        with open(value[1:], encoding="utf-8") as f:
            return json.load(f)
    return json.loads(value)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# База данных скиллов внутри проекта (НЕ dot-папка — иначе рантайм режет доступ).
DATA_DIR = "ground"


def pipeline_dir(project: Path, skill: str, feature: str = "pipeline") -> Path:
    return project / DATA_DIR / "statements" / skill / feature


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    p.add_argument("--skill", required=True)
    p.add_argument("--feature", default="pipeline", help="Namespace стейта на фичу (как в init.py)")
    p.add_argument("--steps", required=True, help="Steps JSON array or @file")
    args = p.parse_args()

    project = Path(args.project or repo_root()).resolve()
    pdir = pipeline_dir(project, args.skill, args.feature)
    manifest_path = pdir / "manifest.json"

    if not manifest_path.exists():
        print(f"ERROR: manifest not found at {manifest_path}. Run init.py first.", file=sys.stderr)
        sys.exit(3)

    steps_data = load_json_arg(args.steps)
    if not isinstance(steps_data, list) or not steps_data:
        print("ERROR: --steps must be a non-empty JSON array", file=sys.stderr)
        sys.exit(2)

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    existing_ids = {s["id"] for s in manifest.get("steps", [])}

    # Висячий depends_on — отказ на записи (паритет с feature-pipeline/add_steps.py и init.py).
    # Шаг со ссылкой на несуществующий id не становится готовым никогда, и читатели теперь
    # fail-closed: пайплайн встанет, а причина будет видна только в blocked_by_unknown_deps.
    # Правило было заведено в два писателя из трёх — этот оставался дырой.
    _known = existing_ids | {s.get("id") for s in steps_data if isinstance(s, dict)}
    _bad = [f"'{s.get('id')}' -> '{d}'" for s in steps_data if isinstance(s, dict)
            for d in (s.get("depends_on") or []) if d not in _known]
    if _bad:
        print(f"ERROR: depends_on ссылается на несуществующие шаги: {', '.join(_bad)}. "
              f"Заведи недостающий шаг в этом же вызове или убери зависимость.",
              file=sys.stderr)
        sys.exit(2)

    added, skipped = [], []
    for s in steps_data:
        if "id" not in s:
            print(f"ERROR: step missing 'id': {s}", file=sys.stderr)
            sys.exit(2)
        if s["id"] in existing_ids:
            skipped.append(s["id"])
            continue
        step = {
            "id": s["id"],
            "title": s.get("title", s["id"]),
            "status": s.get("status", "pending"),
            "depends_on": s.get("depends_on", []),
            "attempts": 0,
        }
        # Паритет с feature-pipeline/add_steps: проставляем required_judges по единой маске.
        # Для не-feature-pipeline step-id маска вернёт [] — безопасно.
        if pp is not None:
            req = pp.match_required_judges(s["id"])
            if req:
                step["required_judges"] = req
        manifest["steps"].append(step)
        existing_ids.add(s["id"])
        added.append(s["id"])

    if added:
        manifest["last_update"] = iso_now()
        tmp = manifest_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp, manifest_path)

        # Синхронизировать нечего: фазовое состояние выводится из манифеста
        # (pipeline_phases.live_state), кэша gate.json на диске больше нет.

    # `gate_synced` осталось от кэша gate.json, которого больше нет: переменную удалили, а
    # ссылку в выводе — нет. NameError падал на КАЖДОМ вызове, причём ПОСЛЕ записи манифеста:
    # вызывающий видел exit 1 «не получилось», а шаги уже были записаны. Тестов на этот файл
    # нет ни одного, поэтому зелёный прогон дефект не показывал.
    print(json.dumps({
        "status": "updated",
        "added": added,
        "skipped_existing": skipped,
        "steps_total": len(manifest["steps"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
