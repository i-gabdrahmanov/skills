#!/usr/bin/env python3
"""
Initialize a new pipeline manifest.

Usage:
    init.py --project <path> --skill <name> \\
        --steps <json-string-or-@file> \\
        [--context <json-string-or-@file>] \\
        [--inputs <json-string-or-@file>]

Creates: <project>/ground/statements/<skill>/<feature>/manifest.json

С версии 2 manifest содержит ТРИ секции данных помимо steps/context:
  inputs.*     — per-feature входы (story, spec, spec_anchor, mode)
  decisions.*  — per-feature решения по ходу (mode_task, criticality, auto_max_risk)
  context.*    — свободный контекст прогона (как раньше, для backward-compat)

Совместимость:
  - init.py при первом запуске проверяет manifest.json на наличие legacy-полей в
    ground/pipeline.json (sources.*/pipeline.mode*/autonomy.*) и АВТО-МИГРИРУЕТ их
    в inputs/decisions манифеста (ставит version=2, пишет audit в manifest.migration).
    Миграция идемпотентна — повторный запуск ничего не меняет.
  - Старый manifest v1 (без inputs/decisions) работает в dual-read режиме до первой
    записи в inputs/decisions (через config.py set inputs.*), после чего init.py
    автоматически дописывает пустые секции.

Steps format (JSON array):
    [
      {"id": "01-structure", "title": "Map structure", "depends_on": []},
      {"id": "02-api", "title": "Map API", "depends_on": ["01-structure"]},
      ...
    ]

Inputs format (JSON object, опционально):
    {
      "story": "STOR-100",
      "spec": "docs/feature-pipeline/STOR-100/sdd.md",
      "spec_anchor": "REQ-0007",
      "mode": "fix"
    }

If manifest already exists, fails with non-zero exit (use update.py for changes).
Caller should call read.py first and decide what to do.
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
# модуль (нет approval_path/is_jira_key и др. → ImportError при сборе pytest).
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != _HERE:
    del sys.modules["_util"]

import judges_registry  # noqa: E402
from _util import repo_root, feature_docs_dir, safe_slug, safe_load_json, is_jira_key  # noqa: E402


def load_json_arg(value: str):
    """Accepts either inline JSON or @<filepath>."""
    if value.startswith("@"):
        return safe_load_json(value[1:], what="--steps файл")
    return json.loads(value)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# База данных скиллов внутри проекта (НЕ dot-папка — иначе рантайм режет доступ
# по path-гарду и seatbelt). Единый каталог для всех скиллов конвейера.
DATA_DIR = "ground"

# Скиллы, где стейт намеспейсится Jira-ключом входного issue (а не свободным слагом).
# feature-pipeline: ключ ОБЯЗАТЕЛЕН — без валидного Jira-ключа папка стейта не создаётся,
# оверрайда нет. Ловит баг «MCP не подключён → безслаговая папка ground/.../pipeline/».
SKILLS_REQUIRING_JIRA_KEY = {"feature-pipeline"}

# Текущая версия формата manifest.json. v2 = inputs/decisions-секции добавлены;
# до этого были только context (и стаейт per-feature случайно лежал в pipeline.json).
MANIFEST_VERSION = 2

# Маппинг legacy pipeline.json → новые секции манифеста для авто-миграции v1→v2.
# Используется в migrate_manifest_v1_to_v2(): ищем эти dot-path в legacy pipeline.json
# и копируем найденные значения в manifest.inputs / manifest.decisions.
LEGACY_TO_INPUTS = {
    "sources.story": "story",
    "sources.spec": "spec",
    "sources.spec_anchor": "spec_anchor",
    "pipeline.mode": "mode",
}
LEGACY_TO_DECISIONS = {
    "pipeline.mode_task": "mode_task",
    "autonomy.criticality": "criticality",
    "autonomy.auto_max_risk": "auto_max_risk",
}


def pipeline_dir(project: Path, skill: str, feature: str = "pipeline") -> Path:
    # feature намеспейсит стейт на фичу: statements/<skill>/<feature>/.
    # Дефолт "pipeline" сохраняет прежнее поведение (system-analyst/minor-defect-fix).
    return project / DATA_DIR / "statements" / skill / feature


def _dig_legacy(dotted: str, legacy: dict):
    """(found, value) по dot-path в legacy pipeline.json. None-значения игнорируются
    (отсутствующее поле в pipeline.json трактуется как «не заполнено»)."""
    cur = legacy
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return (False, None)
        cur = cur[part]
    if cur is None:
        return (False, None)
    return (True, cur)


def migrate_manifest_v1_to_v2(manifest: dict, project: Path, *, force: bool = False) -> tuple:
    """Идемпотентная авто-миграция v1 → v2: копирует legacy-поля из pipeline.json
    в inputs/decisions, проставляет version=2, пишет audit в manifest.migration.

    Возвращает (manifest, migrated). migrated=True если миграция произошла.

    force=True: мигрировать даже если манифест уже v2 (используется для тестов и
    ручного вызова). По умолчанию (force=False) — no-op на v2.

    Правила:
      - Если version>=2 и есть manifest.migration → no-op (уже мигрировали, не трогаем).
      - Если version>=2 и нет manifest.migration → no-op (v2 родной, без миграции).
      - Если version==1 или version отсутствует → читаем pipeline.json, копируем
        найденные legacy-поля, ставим version=2, пишем audit."""
    cur_version = manifest.get("version", 1)
    if cur_version >= 2 and not force:
        return manifest, False  # уже v2, не трогаем

    legacy_path = project / "ground" / "pipeline.json"
    legacy = {}
    if legacy_path.exists():
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            legacy = {}

    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
    decisions = manifest.get("decisions")
    if not isinstance(decisions, dict):
        decisions = {}

    found_any = False
    for legacy_path_, new_key in LEGACY_TO_INPUTS.items():
        if inputs.get(new_key) is not None:
            continue  # уже заполнено в манифесте — не перетираем
        found, val = _dig_legacy(legacy_path_, legacy)
        if found and val not in (None, ""):
            inputs[new_key] = val
            found_any = True
    for legacy_path_, new_key in LEGACY_TO_DECISIONS.items():
        if decisions.get(new_key) is not None:
            continue
        found, val = _dig_legacy(legacy_path_, legacy)
        if found and val not in (None, ""):
            decisions[new_key] = val
            found_any = True

    manifest["inputs"] = inputs
    manifest["decisions"] = decisions
    manifest["version"] = MANIFEST_VERSION
    if found_any or force:
        manifest["migration"] = {
            "from": "1",
            "to": "2",
            "at": iso_now(),
            "source": "ground/pipeline.json (legacy fields)",
            "migrated_fields": {
                "inputs": [k for k in inputs.keys() if inputs.get(k) is not None],
                "decisions": [k for k in decisions.keys() if decisions.get(k) is not None],
            },
        }
    return manifest, found_any or force


def ensure_manifest_sections(manifest: dict) -> dict:
    """Гарантирует наличие inputs/decisions (создаёт пустые если отсутствуют).
    Используется при ЛЮБОЙ записи через update.py / config.py — иначе конкурентный
    writer может упасть на KeyError."""
    if not isinstance(manifest.get("inputs"), dict):
        manifest["inputs"] = {}
    if not isinstance(manifest.get("decisions"), dict):
        manifest["decisions"] = {}
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    p.add_argument("--skill", required=True, help="Skill name (e.g. system-analysis)")
    p.add_argument("--steps", required=True, help="Steps JSON array or @file")
    p.add_argument("--context", default="{}", help="Context JSON object or @file (optional)")
    p.add_argument("--inputs", default=None,
                   help="Inputs JSON object или @file (опционально, v2: per-feature входы: "
                        "story, spec, spec_anchor, mode). По умолчанию — авто-миграция из "
                        "legacy pipeline.json (см. migrate_manifest_v1_to_v2).")
    p.add_argument("--no-migrate", action="store_true",
                   help="Не выполнять авто-миграцию из legacy pipeline.json (используется в "
                        "тестах и для новых проектов, где pipeline.json уже policy.json).")
    p.add_argument("--force", action="store_true", help="Archive existing manifest and create fresh")
    p.add_argument("--feature", default="pipeline", help="Namespace стейта на фичу (slug/Jira-key). По умолчанию 'pipeline'.")
    args = p.parse_args()

    # slug идёт в пути (statements/<skill>/<feature>/, docs/.../<feature>/) — fail-closed на traversal
    try:
        safe_slug(args.feature)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    # Жёсткий гейт «Jira-ключ обязателен» (без оверрайда): для скиллов из набора --feature
    # обязан быть валидным Jira-ключом входного issue, а не дефолтом/свободным слагом.
    if args.skill in SKILLS_REQUIRING_JIRA_KEY and not is_jira_key(args.feature):
        print(f"ERROR: {args.skill} требует Jira-ключ входного issue как --feature "
              f"(формат PROJ-123), получено {args.feature!r}. Передай ключ, напр. STOR-123. "
              f"Подключать Jira MCP не обязательно, но ключ обязателен — оверрайда нет.",
              file=sys.stderr)
        sys.exit(2)

    project = Path(args.project or repo_root()).resolve()
    if not project.exists():
        print(f"ERROR: project root not found: {project}", file=sys.stderr)
        sys.exit(2)

    steps_data = load_json_arg(args.steps)
    context_data = load_json_arg(args.context) if args.context else {}
    inputs_data = load_json_arg(args.inputs) if args.inputs else {}

    if not isinstance(steps_data, list) or not steps_data:
        print("ERROR: --steps must be a non-empty JSON array", file=sys.stderr)
        sys.exit(2)
    if not isinstance(context_data, dict):
        print(f"ERROR: --context must be a JSON object, got {type(context_data).__name__}",
              file=sys.stderr)
        sys.exit(2)
    if not isinstance(inputs_data, dict):
        print(f"ERROR: --inputs must be a JSON object, got {type(inputs_data).__name__}",
              file=sys.stderr)
        sys.exit(2)

    pdir = pipeline_dir(project, args.skill, args.feature)
    manifest_path = pdir / "manifest.json"

    if manifest_path.exists():
        if not args.force:
            print(f"ERROR: manifest already exists at {manifest_path}", file=sys.stderr)
            print("Use read.py to inspect, or --force to archive and recreate", file=sys.stderr)
            sys.exit(3)
        # Archive existing (per-feature)
        archive_dir = project / DATA_DIR / "statements" / args.skill / "archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive_target = archive_dir / f"{args.feature}-{ts}"
        pdir.rename(archive_target)
        print(f"Archived previous state to {archive_target}", file=sys.stderr)
        # Уборка: чекпойнт-refs прежнего прогона фичи больше не резолвятся из манифеста
        try:
            from checkpoint import delete_checkpoints
            n = delete_checkpoints(project, args.feature)
            if n:
                print(f"Deleted {n} stale checkpoint ref(s) for '{args.feature}'", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: checkpoint cleanup failed: {e}", file=sys.stderr)

    pdir.mkdir(parents=True, exist_ok=True)

    now = iso_now()
    pipeline_id = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")

    # Маска required_judges — из единого реестра references/judges-registry.json
    # (judges_registry.match_step). Раньше дублировалась здесь и в patch_manifest_judges.py.
    steps = []
    for s in steps_data:
        if "id" not in s:
            print(f"ERROR: step missing 'id': {s}", file=sys.stderr)
            sys.exit(2)
        req = judges_registry.match_step(s["id"])
        step = {
            "id": s["id"],
            "title": s.get("title", s["id"]),
            "status": s.get("status", "pending"),
            "depends_on": s.get("depends_on", []),
            "attempts": 0,
        }
        if req:
            step["required_judges"] = req
        steps.append(step)

    manifest = {
        "version": MANIFEST_VERSION,
        "skill": args.skill,
        "feature": args.feature,
        "pipeline_id": pipeline_id,
        "started_at": now,
        "last_update": now,
        "project_root": str(project),
        "context": context_data,
        "inputs": dict(inputs_data),         # копия, чтобы --inputs не шерился по ссылке
        "decisions": {},                      # заполняется в процессе прогона (mode_task, criticality, ...)
        "steps": steps,
    }

    # Авто-миграция из legacy pipeline.json (если не --no-migrate).
    # Если legacy pipeline.json пустой или не содержит нужных полей — миграция молча
    # оставляет inputs/decisions как есть (пустые или из --inputs).
    if not args.no_migrate:
        manifest, migrated = migrate_manifest_v1_to_v2(manifest, project)
        if migrated and manifest.get("migration", {}).get("source"):
            print("Auto-migrated legacy fields from ground/pipeline.json → "
                  "manifest.inputs/decisions", file=sys.stderr)

    # Auto-resolve artifacts for existing files by convention.
    # Файлы лежат в <feature_docs_dir>/<feature>/<name> — каталог резолвится по docs-конфигу
    # (in-repo / separate-repo), а не хардкодом docs/feature-pipeline.
    # Map: step-id prefix → list of (artifact_key, filename)
    ARTIFACT_CONVENTIONS = {
        "00-brd":       [("brd", "brd.md")],
        "02-sdd":       [("sdd", "sdd.md")],
        "02-design":    [("tech-design", "tech-design.md"), ("task-plan", "task-plan.json")],
        "02-eval-plan": [("eval-plan", "eval-plan.json")],
        "03-jira":      [("jira-result", "jira-tasks-result.json")],
    }
    fdir = feature_docs_dir(project) / args.feature
    for step in steps:
        step_id = step["id"]
        # Find matching convention prefix
        matched = None
        for prefix, convs in ARTIFACT_CONVENTIONS.items():
            if step_id == prefix or step_id.startswith(prefix):
                matched = convs
                break
        if not matched:
            continue
        artifacts = {}
        for key, name in matched:
            candidate = fdir / name
            if candidate.exists():
                # relative — если под проектом (in-repo); иначе absolute (separate-repo)
                try:
                    artifacts[key] = str(candidate.relative_to(project))
                except ValueError:
                    artifacts[key] = str(candidate)
        if artifacts:
            step["artifacts"] = artifacts

    manifest["steps"] = steps

    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)

    # Baseline-чекпойнт worktree — точка восстановления для отката к самым ранним шагам
    # (rollback.py: fallback-цепочка кончается на 00-baseline). Fail-soft.
    try:
        from checkpoint import create_checkpoint
        create_checkpoint(project, args.feature, "00-baseline")
    except Exception as e:
        print(f"WARNING: baseline checkpoint failed: {e}", file=sys.stderr)

    print(json.dumps({
        "status": "initialized",
        "manifest": str(manifest_path),
        "steps_count": len(steps),
        "artifacts_resolved": any(s.get("artifacts") for s in steps),
        "version": manifest["version"],
        "migrated": manifest.get("migration") is not None,
        "inputs": manifest.get("inputs", {}),
        "decisions": manifest.get("decisions", {}),
    }, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
