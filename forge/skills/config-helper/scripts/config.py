#!/usr/bin/env python3
"""
config-helper — безопасная настройка параметров forge.

Запись в конфиги делает ТОЛЬКО этот скрипт (валидация по реестру params-registry.json,
атомарная запись, бэкап). Модель определяет намерение и зовёт скрипт; сама JSON не правит.

Подкоманды:
  list   [--category C] [--file pipeline|gates|risk] [--json]
  get    <id>
  set    <id> <value> [--dry-run] [--confirm]
  phase  <enable|disable|add> <phase-id> [--enabled-by EXPR] [--skill S] [--gates G...] [--desc D]
  risk   <list-add|list-remove> <key> <pattern> --confirm
  risk   cap-set <agent-regex> <R-level> --confirm
  validate [--strict] [--json]   проверка типов/диапазонов конфига + кросс-проверки (на ЧТЕНИЕ)

Все подкоманды принимают --project (дефолт: git toplevel / cwd).

Exit-коды: 0 ок · 1 валидация/блок (sensitive без --confirm) · 2 ошибка аргументов ·
           3 файл/параметр не найден.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _util import (assign, atomic_write, backup, coerce_and_validate, dig,
                   iso_now, load_json, repo_root, validate_typed)

REGISTRY = Path(__file__).resolve().parent.parent / "references" / "params-registry.json"


# ── Реестр ────────────────────────────────────────────────────────────────────

def load_registry() -> list:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return data.get("params", [])


def find_entry(params: list, pid: str) -> dict | None:
    return next((p for p in params if p["id"] == pid), None)


# ── Резолв файлов ─────────────────────────────────────────────────────────────

def resolve_file(project: Path, file_key: str) -> Path:
    if file_key == "pipeline":
        return project / "ground" / "pipeline.json"
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
    """Текущее значение параметра (из файла, иначе default)."""
    data = load_json(resolve_file(project, entry["file"]))
    if isinstance(data, dict):
        found, val = dig(data, entry["path"])
        if found:
            return val, "file"
    return entry.get("default"), "default"


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
        constraint = ""
        if "enum" in r:
            constraint = f"  [{' | '.join(r['enum'])}]"
        elif "range" in r:
            constraint = f"  [{r['range'][0]}..{r['range'][1]}]"
        print(f"  {r['id']}{flag} = {r['value']!r}  ({r['source']}){constraint}")
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
    }, ensure_ascii=False))
    return 0


# ── set ───────────────────────────────────────────────────────────────────────

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

    target = resolve_file(project, e["file"])
    data = load_json(target)

    if data is None:
        if e["file"] == "gates":
            data = gates_skeleton(params)  # создаём с дефолтами
        elif e["file"] == "pipeline":
            print(json.dumps({"error": f"{target} не найден. Сначала инициализируй: "
                              f"init_pipeline_config.py"}, ensure_ascii=False))
            return 3
        else:  # risk
            print(json.dumps({"error": f"{target} не найден — risk-policy не создаётся "
                              f"автоматически"}, ensure_ascii=False))
            return 3

    old_found, old_val = dig(data, e["path"])
    old_display = old_val if old_found else e.get("default")

    if args.dry_run:
        print(json.dumps({"dry_run": True, "id": e["id"], "file": str(target),
                          "old": old_display, "new": new_val}, ensure_ascii=False))
        return 0

    if e.get("sensitive") and not args.confirm:
        print(json.dumps({
            "blocked": True, "reason": "sensitive-параметр требует --confirm",
            "id": e["id"], "old": old_display, "new": new_val,
            "hint": "повтори вызов с флагом --confirm после подтверждения пользователя",
        }, ensure_ascii=False))
        return 1

    bak = backup(target, project)
    assign(data, e["path"], new_val)
    if e["file"] == "gates":
        assign(data, "_meta.updated_at", iso_now())
    atomic_write(target, data)

    print(json.dumps({"status": "applied", "id": e["id"], "file": str(target),
                      "old": old_display, "new": new_val, "backup": bak},
                     ensure_ascii=False))
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
    target = resolve_file(project, "pipeline")
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
    pcfg = load_json(resolve_file(project, "pipeline"))
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
        return [{"id": "quality.jacoco_configured", "file": "pipeline",
                 "path": "quality.jacoco_configured", "value": q.get("jacoco_configured", False),
                 "severity": "warning",
                 "error": "coverage-гейт активен (eval_enabled + coverage_threshold>0), но "
                          "jacoco_configured=false — coverage в --strict будет FAIL без отчёта. "
                          "Подключи JaCoCo, либо выставь coverage_threshold=0, либо гоняй --lenient."}]
    return []


def cmd_validate(project: Path, params: list, args) -> int:
    issues = []

    # 1. Типы/диапазоны/enum известных параметров — только то, что РЕАЛЬНО есть в файле
    #    (отсутствующие берут default из реестра, он валиден по построению).
    file_cache: dict[str, object] = {}
    for e in params:
        fk = e["file"]
        if fk not in file_cache:
            file_cache[fk] = load_json(resolve_file(project, fk))
        data = file_cache[fk]
        if not isinstance(data, dict):
            continue
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
    pl.add_argument("--file", choices=["pipeline", "gates", "risk"])
    pl.add_argument("--json", action="store_true")

    pg = sub.add_parser("get", help="Текущее значение параметра")
    pg.add_argument("id")

    ps = sub.add_parser("set", help="Установить значение параметра")
    ps.add_argument("id")
    ps.add_argument("value")
    ps.add_argument("--dry-run", action="store_true")
    ps.add_argument("--confirm", action="store_true", help="Подтверждение для sensitive-параметров")

    pp = sub.add_parser("phase", help="Вкл/выкл/добавить фазу в phases_override")
    pp.add_argument("action", choices=["enable", "disable", "add"])
    pp.add_argument("phase_id")
    pp.add_argument("--enabled-by", dest="enabled_by", default=None)
    pp.add_argument("--skill", default=None)
    pp.add_argument("--gates", nargs="*", default=None)
    pp.add_argument("--desc", default=None)

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
