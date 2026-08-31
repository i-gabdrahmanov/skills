#!/usr/bin/env python3
"""
config-helper — безопасная настройка параметров forge.

Запись в конфиги делает ТОЛЬКО этот скрипт (валидация по реестру params-registry.json,
атомарная запись, бэкап). Модель определяет намерение и зовёт скрипт; сама JSON не правит.

Подкоманды:
  list   [--category C] [--file pipeline|gates|risk] [--json]
  get    <id>
  set    <id> <value> [--dry-run] [--confirm] [--skill S] [--feature F]
  phase  <enable|disable|add> <phase-id> [--enabled-by EXPR] [--skill S] [--gates G...] [--desc D]
  risk   <list-add|list-remove> <key> <pattern> --confirm
  risk   cap-set <agent-regex> <R-level> --confirm
  validate [--strict] [--json]   проверка типов/диапазонов конфига + кросс-проверки (на ЧТЕНИЕ)

Все подкоманды принимают --project (дефолт: git toplevel / cwd).

Роутинг записи (v2):
  - quality.* / conventions.* / docs.* / jira.* / autonomy.level / gates.* / project.*
    → ground/policy.json (общая конфигурация проекта; immutable на прогоне активной фичи);
  - inputs.* / decisions.*
    → ground/statements/<skill>/<feature>/manifest.json (per-feature входы/решения).

Совместимость:
  - file_key="pipeline" (старое имя) → пишется в policy.json;
  - legacy `pipeline.mode` / `sources.*` / `autonomy.criticality` в pipeline.json
    читаются через dual-read fallback (см. risk_ladder.config_get), но ЗАПИСЬ идёт
    в новые пути (manifest.inputs.* / manifest.decisions.*);
  - config.py validate помечает DEPRECATED поля в policy.json как WARNING.

Exit-коды: 0 ок · 1 валидация/блок (sensitive без --confirm / immutable на прогоне) ·
           2 ошибка аргументов · 3 файл/параметр не найден.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _util import (LEGACY_PATHS, assign, atomic_write, backup, coerce_and_validate,
                   dig, iso_now, legacy_path_for, load_json, repo_root,
                   route_path, validate_typed)


# ── Импорт hooks/_project.load_active_manifest (единая реализация mtime-freshest lookup
# среди ground/statements/*/*/manifest.json). Раньше _resolve_manifest_path() дублировал её;
# теперь обёртка поверх load_active_manifest (см. ниже). Поиск каталога hooks/ от
# расположения этого файла — forge/ (source) или <project>/.gigacode (deploy).
def _hooks_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "hooks" / "_project.py").is_file():
            return parent / "hooks"
    raise ImportError("forge: hooks/_project.py не найден — бандл повреждён")


_HOOKS = str(_hooks_dir())
if _HOOKS not in sys.path:
    sys.path.append(_HOOKS)

from _project import load_active_manifest  # noqa: E402  (после sys.path.append)


REGISTRY = Path(__file__).resolve().parent.parent / "references" / "params-registry.json"


# ── Реестр ────────────────────────────────────────────────────────────────────

def load_registry() -> list:
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: реестр параметров нечитаем/повреждён ({REGISTRY}): {e}", file=sys.stderr)
        raise SystemExit(4)
    return data.get("params", [])


def find_entry(params: list, pid: str) -> dict | None:
    return next((p for p in params if p["id"] == pid), None)


# ── Резолв файлов ─────────────────────────────────────────────────────────────

def resolve_file(project: Path, file_key: str) -> Path:
    """Имя файла для file-key реестра.

    v2 модели: 'pipeline' → ground/policy.json (общая конфигурация), 'gates' →
    ground/feature-gates.json, 'risk' → risk-policy.json (co-located с хуками).
    Поле file="manifest" резолвится через _resolve_manifest_path() — здесь не обрабатывается.
    """
    if file_key == "pipeline":
        return project / "ground" / "policy.json"     # v2: pipeline.* → policy.json
    if file_key == "policy":
        return project / "ground" / "policy.json"
    if file_key == "gates":
        return project / "ground" / "feature-gates.json"
    if file_key == "risk":
        cand = project / ".gigacode" / "hooks" / "risk-policy.json"
        if cand.exists():
            return cand
        alt = project / "hooks" / "risk-policy.json"  # source-layout forge
        if alt.exists():
            return alt
        return cand
    raise ValueError(f"неизвестный file-key: {file_key}")


def _resolve_manifest_path(project: Path, skill: str | None, feature: str | None) -> Path:
    """Путь к manifest.json активной фичи (тонкая обёртка над hooks/_project.load_active_manifest).

    Если --skill/--feature переданы — точно в эту фичу (прямой путь, без сканирования).
    Если нет — берём САМУЮ СВЕЖУЮ фичу (mtime) среди всех namespace через ЕДИНЫЙ резолвер
    hooks/_project.load_active_manifest (им же пользуются risk_ladder и gate-guard).
    Совместимость по семантике: при отсутствии фичей — FileNotFoundError (как раньше)."""
    if skill and feature:
        # Прямой путь без сканирования — пользователь явно указал обе координаты.
        return project / "ground" / "statements" / skill / feature / "manifest.json"
    mp, _ = load_active_manifest(project, skill)
    if mp is None:
        base = project / "ground" / "statements"
        hint = ("ground/statements/" if base.is_dir()
                else f"нет ground/statements/ в {project}")
        raise FileNotFoundError(
            f"нет ни одного manifest.json в ground/statements/*/*/; сначала init.py ({hint})"
        )
    return mp


def gates_skeleton(params: list) -> dict:
    """Полный feature-gates.json с дефолтами всех gate-параметров."""
    data = {
        "_meta": {"version": 1, "updated_at": iso_now(),
                  "cache_ttl_hours": 6, "source": "config-helper"},
        "gates": {},
    }
    for p in params:
        if p["file"] == "gates":
            name = p["path"].split(".")[1]  # gates.<name>.enabled
            data["gates"][name] = {"enabled": p.get("default"), "description": ""}
    return data


def current_value(project: Path, params: list, entry: dict):
    """Текущее значение параметра (из файла, иначе default).

    v2 модели:
      - file="policy" / file="pipeline" → читаем ground/policy.json (с fallback на
        ground/pipeline.json для обратной совместимости);
      - file="manifest" → читаем manifest.json активной фичи;
      - file="gates" → ground/feature-gates.json;
      - file="risk" → risk-policy.json (co-located с хуками).

    Для inputs.*/decisions.* дополнительно делается dual-read fallback в legacy
    pipeline.json (см. legacy_path_for)."""
    fk = entry["file"]
    if fk in ("pipeline", "policy"):
        # Новая модель читает policy.json; если его нет — legacy pipeline.json.
        target = resolve_file(project, "policy")
        data = load_json(target)
        if data is None:
            data = load_json(project / "ground" / "pipeline.json")
        src_hint = "policy.json" if target.exists() else "pipeline.json (legacy)"
    elif fk == "manifest":
        try:
            mp = _resolve_manifest_path(project, None, None)
        except FileNotFoundError:
            data, src_hint = None, "manifest (нет фичи)"
        else:
            data = load_json(mp)
            src_hint = "manifest.json"
    elif fk == "gates":
        data = load_json(resolve_file(project, "gates"))
        src_hint = "feature-gates.json"
    elif fk == "risk":
        data = load_json(resolve_file(project, "risk"))
        src_hint = "risk-policy.json"
    else:
        data = None
        src_hint = "unknown"

    if isinstance(data, dict):
        # Сначала новый путь (напр. inputs.story → inputs.story в манифесте).
        found, val = dig(data, entry["path"])
        if found:
            return val, f"file:{src_hint}"
        # Для manifest-* путей — fallback на legacy pipeline.json.
        if fk == "manifest":
            legacy = legacy_path_for(entry["path"])
            if legacy:
                legacy_data = load_json(project / "ground" / "pipeline.json")
                if isinstance(legacy_data, dict):
                    lf, lv = dig(legacy_data, legacy)
                    if lf:
                        return lv, "file:pipeline.json (legacy fallback)"
    return entry.get("default"), "default"


def _policy_path(project: Path) -> Path:
    """Алиас для ясности в этом модуле."""
    return project / "ground" / "policy.json"


def _legacy_pipeline_path(project: Path) -> Path:
    """Legacy ground/pipeline.json — DEPRECATED, но читается через dual-read."""
    return project / "ground" / "pipeline.json"


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list(project: Path, params: list, args) -> int:
    rows = []
    for e in params:
        if args.category and e["category"] != args.category:
            continue
        if args.file and e["file"] != args.file:
            continue
        val, src = current_value(project, params, e)
        row = {
            "id": e["id"], "category": e["category"], "file": e["file"],
            "title": e["title"], "type": e["type"], "value": val, "source": src,
            "default": e.get("default"), "sensitive": e.get("sensitive", False),
            "description": e["description"],
        }
        if "enum" in e:
            row["enum"] = e["enum"]
        if "min" in e or "max" in e:
            row["range"] = [e.get("min"), e.get("max")]
        if e.get("deprecated"):
            row["deprecated"] = True
            if e.get("deprecated_aliases"):
                row["deprecated_aliases"] = e["deprecated_aliases"]
        rows.append(row)

    if args.json:
        print(json.dumps({"params": rows}, ensure_ascii=False, indent=2))
        return 0

    cat = None
    for r in rows:
        if r["category"] != cat:
            cat = r["category"]
            print(f"\n=== {cat} ===")
        flag = " 🔒" if r["sensitive"] else ""
        dep = " ⛔DEPRECATED" if r.get("deprecated") else ""
        constraint = ""
        if "enum" in r:
            constraint = f"  [{' | '.join(r['enum'])}]"
        elif "range" in r:
            constraint = f"  [{r['range'][0]}..{r['range'][1]}]"
        print(f"  {r['id']}{flag}{dep} = {r['value']!r}  ({r['source']}){constraint}")
        print(f"      {r['title']} — {r['description']}")
    print()
    return 0


# ── get ───────────────────────────────────────────────────────────────────────

def cmd_get(project: Path, params: list, args) -> int:
    e = find_entry(params, args.id)
    if e is None:
        print(json.dumps({"error": f"параметр '{args.id}' не найден в реестре"},
                         ensure_ascii=False))
        return 3
    val, src = current_value(project, params, e)
    print(json.dumps({
        "id": e["id"], "value": val, "source": src,
        "default": e.get("default"), "file": e["file"], "path": e["path"],
        "type": e["type"], "sensitive": e.get("sensitive", False),
        "deprecated": bool(e.get("deprecated")),
        "deprecated_aliases": e.get("deprecated_aliases"),
    }, ensure_ascii=False))
    return 0


# ── set ───────────────────────────────────────────────────────────────────────

def _has_active_feature(project: Path) -> bool:
    """Есть ли хоть один manifest.json в ground/statements/ (т.е. идёт прогон)."""
    base = project / "ground" / "statements"
    if not base.is_dir():
        return False
    for skill_dir in base.iterdir():
        if not skill_dir.is_dir():
            continue
        for d in skill_dir.iterdir():
            if not d.is_dir() or d.name == "archived":
                continue
            if (d / "manifest.json").exists():
                return True
    return False


def cmd_set(project: Path, params: list, args) -> int:
    e = find_entry(params, args.id)
    if e is None:
        print(json.dumps({"error": f"параметр '{args.id}' не найден в реестре. "
                          f"Запусти `list`, чтобы увидеть допустимые id."},
                         ensure_ascii=False))
        return 3

    try:
        new_val = coerce_and_validate(e, args.value)
    except ValueError as ex:
        print(json.dumps({"error": f"невалидное значение для {e['id']}: {ex}",
                          "param": e["id"], "type": e["type"],
                          "enum": e.get("enum"), "range": [e.get("min"), e.get("max")]},
                         ensure_ascii=False))
        return 1

    # v2: определяем целевой файл по file-key и роутингу пути.
    file_key = e["file"]
    if file_key == "manifest":
        # inputs.* / decisions.* → manifest.json активной фичи
        try:
            target = _resolve_manifest_path(project, args.skill, args.feature)
        except FileNotFoundError as ex:
            print(json.dumps({"error": str(ex),
                              "hint": "сначала инициализируй manifest: "
                                      "skills/pipeline-state/scripts/init.py "
                                      "--skill <S> --feature <F> --steps '...'"},
                             ensure_ascii=False))
            return 3
        # Путь ВНУТРИ манифеста: после route_path остаётся inputs.X или decisions.X.
        target_section, sub_path = route_path(e["path"])
        if target_section != "manifest":
            print(json.dumps({"error": "internal: file=manifest, но путь не в inputs./decisions.",
                              "path": e["path"]}, ensure_ascii=False))
            return 2
        section_name = "inputs" if e["path"].startswith("inputs.") else "decisions"
    elif file_key in ("pipeline", "policy"):
        # Общая конфигурация проекта → policy.json. Immutable на прогоне активной фичи.
        if _has_active_feature(project):
            print(json.dumps({
                "blocked": True,
                "reason": "policy.json immutable на прогоне активной фичи",
                "id": e["id"],
                "hint": "заверши текущий прогон (все шаги completed) или архивируй "
                        "manifest, потом set. Per-feature входы/решения пишутся в "
                        "manifest.json (file=manifest, см. реестр).",
            }, ensure_ascii=False))
            return 1
        target = resolve_file(project, "policy")
        sub_path = e["path"]
        section_name = None
    elif file_key == "gates":
        target = resolve_file(project, "gates")
        sub_path = e["path"]
        section_name = None
    elif file_key == "risk":
        target = resolve_file(project, "risk")
        sub_path = e["path"]
        section_name = None
    else:
        print(json.dumps({"error": f"неизвестный file-key {file_key}"},
                         ensure_ascii=False))
        return 2

    data = load_json(target)

    if data is None:
        if file_key == "gates":
            data = gates_skeleton(params)  # создаём с дефолтами
        elif file_key in ("pipeline", "policy"):
            print(json.dumps({"error": f"{target} не найден. Сначала инициализируй: "
                              f"init_pipeline_config.py"}, ensure_ascii=False))
            return 3
        elif file_key == "manifest":
            print(json.dumps({"error": f"{target} не найден (manifest активной фичи). "
                              "Сначала init.py манифеста"}, ensure_ascii=False))
            return 3
        else:  # risk
            print(json.dumps({"error": f"{target} не найден — risk-policy не создаётся "
                              f"автоматически"}, ensure_ascii=False))
            return 3

    # Для manifest: пишем в data[section_name][sub_path], не в data[sub_path]
    if file_key == "manifest":
        if section_name not in data or not isinstance(data.get(section_name), dict):
            data[section_name] = {}
        old_found, old_val = dig(data[section_name], sub_path)
        old_display = old_val if old_found else e.get("default")
    else:
        old_found, old_val = dig(data, sub_path)
        old_display = old_val if old_found else e.get("default")

    if args.dry_run:
        print(json.dumps({"dry_run": True, "id": e["id"], "file": str(target),
                          "old": old_display, "new": new_val,
                          "section": section_name}, ensure_ascii=False))
        return 0

    if e.get("sensitive") and not args.confirm:
        print(json.dumps({
            "blocked": True, "reason": "sensitive-параметр требует --confirm",
            "id": e["id"], "old": old_display, "new": new_val,
            "hint": "повтори вызов с флагом --confirm после подтверждения пользователя",
        }, ensure_ascii=False))
        return 1

    bak = backup(target, project)
    if file_key == "manifest":
        assign(data[section_name], sub_path, new_val)
    else:
        assign(data, sub_path, new_val)
    if file_key == "gates":
        assign(data, "_meta.updated_at", iso_now())
    # Ответ на вопрос §0.1 снимает поле из маркера _incomplete (его читает preflight как
    # гейт арминга). Раньше маркер НИКТО не чистил → preflight не мог позеленеть в принципе.
    # В v2: чистим и в policy.json, и в legacy pipeline.json (на случай гибридного проекта).
    if file_key in ("pipeline", "policy") and new_val is not None:
        inc = data.get("_incomplete")
        if isinstance(inc, list):
            keep = [i for i in inc
                    if not (i == sub_path or str(i).startswith(sub_path + " "))]
            if not keep:
                data.pop("_incomplete", None)
            elif keep != inc:
                data["_incomplete"] = keep
    atomic_write(target, data)

    print(json.dumps({"status": "applied", "id": e["id"], "file": str(target),
                      "section": section_name, "old": old_display, "new": new_val,
                      "backup": bak}, ensure_ascii=False))
    return 0


# ── phase ─────────────────────────────────────────────────────────────────────

def _parse_enabled_by(raw: str):
    if raw is None:
        return True
    low = raw.strip().lower()
    if low in ("true", "1"):
        return True
    if low in ("false", "0"):
        return False
    return raw  # путь-выражение вроде "gates.security_review"


def cmd_phase(project: Path, params: list, args) -> int:
    # phases_override — общая конфигурация (какие фазы включены в принципе), → policy.json.
    if _has_active_feature(project):
        print(json.dumps({
            "blocked": True,
            "reason": "phases_override (policy.json) immutable на прогоне активной фичи",
            "hint": "заверши прогон или архивируй manifest",
        }, ensure_ascii=False))
        return 1
    target = resolve_file(project, "policy")
    data = load_json(target)
    if data is None:
        print(json.dumps({"error": f"{target} не найден. Сначала init_pipeline_config.py"},
                         ensure_ascii=False))
        return 3

    overrides = data.get("phases_override")
    if not isinstance(overrides, list):
        overrides = []

    existing = next((o for o in overrides if o.get("id") == args.phase_id), None)
    if existing is None:
        existing = {"id": args.phase_id}
        overrides.append(existing)

    if args.action == "enable":
        existing["enabled_by"] = _parse_enabled_by(args.enabled_by)
    elif args.action == "disable":
        existing["enabled_by"] = False
    elif args.action == "add":
        existing["enabled_by"] = _parse_enabled_by(args.enabled_by)
        if args.skill is not None:
            existing["skill"] = None if args.skill.lower() in ("null", "none") else args.skill
        if args.gates:
            existing["gates"] = args.gates
        if args.desc:
            existing["description"] = args.desc
        if args.after:
            existing["after"] = args.after

    data["phases_override"] = overrides
    bak = backup(target, project)
    atomic_write(target, data)
    print(json.dumps({"status": "applied", "action": args.action,
                      "phase": existing, "backup": bak}, ensure_ascii=False))
    return 0


# ── risk (list/map мутации) ───────────────────────────────────────────────────

_RISK_LIST_KEYS = {"destructive_blacklist", "pii_patterns", "injection_markers"}


def cmd_risk(project: Path, params: list, args) -> int:
    if not args.confirm:
        print(json.dumps({"blocked": True,
                          "reason": "правка risk-policy требует --confirm"},
                         ensure_ascii=False))
        return 1

    target = resolve_file(project, "risk")
    data = load_json(target)
    if data is None:
        print(json.dumps({"error": f"{target} не найден"}, ensure_ascii=False))
        return 3

    if args.action in ("list-add", "list-remove"):
        key = args.key
        if key not in _RISK_LIST_KEYS:
            print(json.dumps({"error": f"ключ {key!r} не из списочных: {sorted(_RISK_LIST_KEYS)}"},
                             ensure_ascii=False))
            return 2
        lst = data.get(key)
        if not isinstance(lst, list):
            lst = []
        if args.action == "list-add":
            if args.value in lst:
                print(json.dumps({"status": "noop", "reason": "уже есть", "key": key},
                                 ensure_ascii=False))
                return 0
            lst.append(args.value)
        else:  # list-remove
            if args.value not in lst:
                print(json.dumps({"error": f"паттерн не найден в {key}"}, ensure_ascii=False))
                return 3
            lst.remove(args.value)
        data[key] = lst

    elif args.action == "cap-set":
        level = args.value.strip()
        if level not in {"R0", "R1", "R2", "R3", "R4", "R5"}:
            print(json.dumps({"error": f"уровень {level!r} не R0..R5"}, ensure_ascii=False))
            return 2
        caps = data.get("agent_caps")
        if not isinstance(caps, dict):
            caps = {}
        caps[args.key] = level
        data["agent_caps"] = caps
    else:
        print(json.dumps({"error": f"неизвестное risk-действие {args.action}"},
                         ensure_ascii=False))
        return 2

    bak = backup(target, project)
    atomic_write(target, data)
    print(json.dumps({"status": "applied", "action": args.action, "key": args.key,
                      "value": args.value, "file": str(target), "backup": bak},
                     ensure_ascii=False))
    return 0


# ── validate ──────────────────────────────────────────────────────────────────

def _check_coverage_jacoco(project: Path) -> list:
    """Остаток P0-1: coverage-гейт активен, но JaCoCo не подключён → coverage в --strict
    будет FAIL-иться (нет отчёта). Это preflight «JaCoCo есть, если гейт включён»."""
    pcfg = load_json(resolve_file(project, "policy"))
    if not isinstance(pcfg, dict):
        return []
    q = pcfg.get("quality")
    if not isinstance(q, dict):
        return []
    eval_enabled = q.get("eval_enabled", True)
    try:
        cov_active = bool(eval_enabled) and float(q.get("coverage_threshold", 0)) > 0
    except (TypeError, ValueError):
        cov_active = bool(eval_enabled)
    if cov_active and not q.get("jacoco_configured", False):
        return [{"id": "quality.jacoco_configured", "file": "policy",
                 "path": "quality.jacoco_configured", "value": q.get("jacoco_configured", False),
                 "severity": "warning",
                 "error": "coverage-гейт активен (eval_enabled + coverage_threshold>0), но "
                          "jacoco_configured=false — coverage в --strict будет FAIL без отчёта. "
                          "Подключи JaCoCo, либо выставь coverage_threshold=0, либо гоняй --lenient."}]
    return []


def _check_deprecated_in_policy(project: Path) -> list:
    """WARN: per-feature поля (sources.*/pipeline.mode*/autonomy.criticality/auto_max_risk)
    в policy.json или legacy pipeline.json. Целевая модель — manifest.json активной фичи.

    Legacy pipeline.json не валится (обратная совместимость), но в policy.json эти поля
    считаются ошибкой архитектуры: кто-то явно мигрировал на новый файл, но забыл убрать
    per-feature блок. Severity warning для pipeline.json, error для policy.json."""
    issues = []
    # 1. policy.json (новая модель) — DEPRECATED поля = ERROR.
    policy = load_json(_policy_path(project))
    if isinstance(policy, dict):
        for path in DEPRECATED_IN_POLICY_FROM_LEGACY:
            found, val = dig(policy, path)
            if found:
                # Подсказка по новому пути.
                hint_path = next((k for k, v in LEGACY_PATHS.items() if v == path), path)
                issues.append({
                    "id": path, "file": "policy", "path": path, "value": val,
                    "severity": "error",
                    "error": (f"DEPRECATED: '{path}' больше не живёт в policy.json — это "
                              f"per-feature поле. Пиши в manifest.json активной фичи: "
                              f"`config.py set {hint_path} <value> --skill <S> --feature <F>`."),
                })
    # 2. legacy pipeline.json — DEPRECATED поля = WARNING (совместимость).
    legacy = load_json(_legacy_pipeline_path(project))
    if isinstance(legacy, dict):
        for path in DEPRECATED_IN_POLICY_FROM_LEGACY:
            found, val = dig(legacy, path)
            if found:
                hint_path = next((k for k, v in LEGACY_PATHS.items() if v == path), path)
                issues.append({
                    "id": path, "file": "pipeline (legacy)", "path": path, "value": val,
                    "severity": "warning",
                    "error": (f"DEPRECATED: '{path}' в legacy pipeline.json читается через "
                              f"dual-read fallback, но НОВЫЕ записи идут в manifest.json "
                              f"активной фичи. Перенеси: `config.py set {hint_path} <value> "
                              f"--skill <S> --feature <F>` (или дождись авто-миграции v1→v2 "
                              f"при init.py манифеста)."),
                })
    return issues


DEPRECATED_IN_POLICY_FROM_LEGACY = {
    "sources.story",
    "sources.spec",
    "sources.spec_anchor",
    "pipeline.mode",
    "pipeline.mode_task",
    "autonomy.criticality",
    "autonomy.auto_max_risk",
}


def cmd_validate(project: Path, params: list, args) -> int:
    issues = []

    # 1. Типы/диапазоны/enum известных параметров — только то, что РЕАЛЬНО есть в файле
    #    (отсутствующие берут default из реестра, он валиден по построению).
    #    v2: file="manifest" читает manifest.json активной фичи.
    file_cache: dict[str, object] = {}
    for e in params:
        fk = e["file"]
        if fk not in file_cache:
            if fk in ("pipeline", "policy"):
                data = load_json(_policy_path(project))
                if data is None:
                    data = load_json(_legacy_pipeline_path(project))
                file_cache[fk] = data
            elif fk == "manifest":
                try:
                    mp = _resolve_manifest_path(project, None, None)
                    file_cache[fk] = load_json(mp)
                except FileNotFoundError:
                    file_cache[fk] = None
            else:
                file_cache[fk] = load_json(resolve_file(project, fk))
        data = file_cache[fk]
        if not isinstance(data, dict):
            continue
        # Для manifest — копаем внутри section (inputs/decisions).
        if fk == "manifest":
            section_name = "inputs" if e["path"].startswith("inputs.") else "decisions"
            sub = e["path"].split(".", 1)[1] if "." in e["path"] else e["path"]
            section = data.get(section_name)
            if not isinstance(section, dict):
                continue
            found, val = dig(section, sub)
        else:
            found, val = dig(data, e["path"])
        if not found:
            continue
        try:
            validate_typed(e, val)
        except ValueError as ex:
            issues.append({"id": e["id"], "file": fk, "path": e["path"],
                           "value": val, "severity": "error", "error": str(ex)})

    # 2. Кросс-проверки конфига
    issues.extend(_check_coverage_jacoco(project))
    # 3. DEPRECATED поля в policy.json (error) и в legacy pipeline.json (warning).
    issues.extend(_check_deprecated_in_policy(project))

    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    # --strict: предупреждения тоже валят (для preflight-гейта)
    failed = bool(errors) or (args.strict and bool(warnings))
    status = "invalid" if failed else "ok"

    if args.json:
        print(json.dumps({"status": status, "issues": issues,
                          "counts": {"error": len(errors), "warning": len(warnings)}},
                         ensure_ascii=False, indent=2))
    else:
        if not issues:
            print("config validate: ✓ OK — рассинхрона типов не найдено")
        else:
            mark = "✗ INVALID" if failed else "⚠ есть предупреждения"
            print(f"config validate: {mark} (ошибок {len(errors)}, предупреждений {len(warnings)})")
            for i in issues:
                flag = "✗" if i["severity"] == "error" else "⚠"
                print(f"  {flag} {i['id']} ({i['file']}:{i['path']}) = {i['value']!r}")
                print(f"      {i['error']}")
    return 1 if failed else 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Корень проекта (дефолт: git toplevel / cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="Каталог параметров с текущими значениями")
    pl.add_argument("--category")
    pl.add_argument("--file", choices=["pipeline", "policy", "gates", "risk", "manifest"])
    pl.add_argument("--json", action="store_true")

    pg = sub.add_parser("get", help="Текущее значение параметра")
    pg.add_argument("id")

    ps = sub.add_parser("set", help="Установить значение параметра")
    ps.add_argument("id")
    ps.add_argument("value")
    ps.add_argument("--dry-run", action="store_true")
    ps.add_argument("--confirm", action="store_true", help="Подтверждение для sensitive-параметров")
    ps.add_argument("--skill", default=None,
                    help="Для inputs.*/decisions.*: namespace скилла (forgefix/forgelite/feature-pipeline). "
                         "По умолчанию — самая свежая фича по mtime.")
    ps.add_argument("--feature", default=None,
                    help="Для inputs.*/decisions.*: слаг фичи. По умолчанию — самая свежая фича по mtime.")

    pp = sub.add_parser("phase", help="Вкл/выкл/добавить фазу в phases_override")
    pp.add_argument("action", choices=["enable", "disable", "add"])
    pp.add_argument("phase_id")
    pp.add_argument("--enabled-by", dest="enabled_by", default=None)
    pp.add_argument("--skill", default=None)
    pp.add_argument("--gates", nargs="*", default=None)
    pp.add_argument("--desc", default=None)
    pp.add_argument("--after", default=None,
                    help="Для add: id фазы, СРАЗУ ПОСЛЕ которой вставить новую (без него — в конец)")

    pr = sub.add_parser("risk", help="Мутации risk-policy (всегда --confirm)")
    pr.add_argument("action", choices=["list-add", "list-remove", "cap-set"])
    pr.add_argument("key", help="имя ключа-списка или agent-regex для cap-set")
    pr.add_argument("value", help="паттерн (list) или R-level (cap-set)")
    pr.add_argument("--confirm", action="store_true")

    pv = sub.add_parser("validate", help="Проверить типы/диапазоны конфига + кросс-проверки")
    pv.add_argument("--strict", action="store_true",
                    help="Предупреждения тоже валят (exit 1) — для preflight-гейта")
    pv.add_argument("--json", action="store_true")

    args = p.parse_args()
    project = Path(args.project or repo_root()).resolve()
    params = load_registry()

    if args.cmd == "list":
        return cmd_list(project, params, args)
    if args.cmd == "get":
        return cmd_get(project, params, args)
    if args.cmd == "set":
        return cmd_set(project, params, args)
    if args.cmd == "phase":
        return cmd_phase(project, params, args)
    if args.cmd == "risk":
        return cmd_risk(project, params, args)
    if args.cmd == "validate":
        return cmd_validate(project, params, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
