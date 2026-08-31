#!/usr/bin/env python3
"""check_fix_delta.py — гейт дельта-правки спеки на fix-пути (шаг fix-spec).

Философия fix-пути: спека правится ТОЧЕЧНО (правки, а не заново). Дельта фикса — крошечный
`sdd.md` с ОДНИМ-ДВУМЯ затронутыми требованиями, названия которых дословно совпадают с
требованиями мастера (`specs/<cap>/spec.md`), чтобы `/forge-spec merge` дал операцию `~`
(modify), а не `+` (add). Полный SDD-шаблон фичи (ДКБ, threat model, API-контракты) для фикса
не пишется — его гейт (`sdd/scripts/check_sdd_doc.py`) здесь НЕ применяется.

Что проверяем (детерминированно, без модели):
  1. Дельта есть, непустая, из неё парсятся требования-кандидаты (`### <название>` в §3).
  2. Есть хотя бы один сценарий Given-When-Then — регресс-сценарий бага (спека должна
     научиться описывать поведение, которого раньше не покрывала).
  3. В спеке нет кода (fence java/sql/xml/diff, import/@RestController/public class).
  4. Дельта МИНИМАЛЬНА: не больше --max-requirements требований и --max-lines строк.
     Превышение = «переписал спеку заново вместо правки» — это и есть то, что fix-путь чинит.
  5. Если мастер резолвится: у требований, попавших в `~ modify`, число GWT-сценариев не
     меньше, чем в мастере. Иначе merge затрёт блок требования и сценарии ПОТЕРЯЮТСЯ
     (modify заменяет требование целиком — дельта обязана нести его полный текст с правкой).
  6. Если передан --plan: у каждой задачи мини-плана непустой acceptance и sdd_ref.
  7. Если передан --anchor (зафиксированное решение `sources.spec_anchor` — какое требование
     мастера чинит баг, см. find_spec_anchor.py): дельта обязана править ИМЕННО его.
     `--anchor none` = «поведение в спеке не описано», проверка пропускается.

Usage:
    check_fix_delta.py <sdd.md> [--plan <task-plan.json>] [--project-root <root>]
        [--anchor <REQ-ID|none>] [--spec <master spec.md>] [--capability <cap>]
        [--max-requirements N] [--max-lines N] [--allow-scenario-drop] [--json]
Exit: 0 = pass, 2 = есть errors.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Движок мастера co-located: skills/system-analyst/scripts (тот же, что у /forge-spec).
_ENGINE_DIR = Path(__file__).resolve().parents[2] / "system-analyst" / "scripts"
try:
    sys.path.insert(0, str(_ENGINE_DIR))
    import merge_delta_to_master as _engine  # noqa: E402
except Exception:  # noqa: BLE001 — мастер-движок недоступен: якорь не проверяем, но гейт живёт
    _engine = None

_GWT = re.compile(r"(?i)given.*when.*then")
_CODE_FENCE = re.compile(r"```(?:java|diff|kotlin|sql|xml)\b", re.IGNORECASE)
_CODE_SIGNS = re.compile(
    r"(?m)^\s*(?:import\s+[\w.]+;|@(?:RestController|Service|Entity|Repository|Component"
    r"|GetMapping|PostMapping|PutMapping|DeleteMapping)\b|public\s+(?:class|interface|enum)\s)"
)

DEFAULT_MAX_REQUIREMENTS = 3
DEFAULT_MAX_LINES = 160


def _load_master_reqs(project_root: Path | None, spec: str | None, capability: str | None):
    """(requirements, spec_path) мастера или (None, None), если мастер не резолвится."""
    if _engine is None or (project_root is None and not spec):
        return None, None
    try:
        # explicit --spec резолвится без project_root (так же, как в merge_delta_to_master)
        spec_path, _cap = _engine.resolve_spec(project_root or Path("."), capability, spec)
        if not spec_path or not Path(spec_path).exists():
            return None, None
        prefix = _engine.DEFAULT_ID_PREFIX
        if project_root is not None:
            prefix = _engine.spec_options(project_root).get("id_prefix") or prefix
        text = Path(spec_path).read_text(encoding="utf-8")
        return _engine.parse_master(text, prefix), Path(spec_path)
    except Exception:  # noqa: BLE001 — мастер не заведён/битый: якорь просто не проверяем
        return None, None


def check_delta(raw: str, *, master_reqs=None, plan: dict | None = None,
                anchor: str | None = None,
                max_requirements: int = DEFAULT_MAX_REQUIREMENTS,
                max_lines: int = DEFAULT_MAX_LINES,
                allow_scenario_drop: bool = False) -> dict:
    """Вердикт по тексту дельты. {status, requirements, ops, errors, warnings}."""
    errors: list[str] = []
    warnings: list[str] = []

    if not raw.strip():
        return {"status": "fail", "requirements": 0, "ops": [],
                "errors": ["дельта пустая — нечего сливать в мастер"], "warnings": []}

    lines = raw.splitlines()
    if len(lines) > max_lines:
        errors.append(f"дельта на {len(lines)} строк (лимит {max_lines}) — это переписанная "
                      f"спека, а не точечная правка фикса; оставь только затронутые требования")

    candidates = []
    if _engine is not None:
        candidates = [c for c in _engine.parse_delta(raw) if (c.get("title") or "").strip()]
    else:  # деградация без движка: считаем `###`-подзаголовки сами
        candidates = [{"title": m.group(1).strip(), "scenarios": []}
                      for m in re.finditer(r"(?m)^\s{0,3}###\s+(.+?)\s*$", raw)]

    if not candidates:
        errors.append("не распознано ни одного требования: в §3 «Функциональные требования "
                      "(Given-When-Then)» нужен подзаголовок `### <название требования>` — "
                      "дословно как в мастере, иначе merge заведёт новое требование вместо правки")
    elif len(candidates) > max_requirements:
        errors.append(f"в дельте {len(candidates)} требований (лимит {max_requirements}) — "
                      f"минорный фикс не переписывает спеку; вынеси лишнее из дельты")

    # Заголовок «(Given-When-Then)» — не сценарий: считаем только содержательные строки.
    if not any(_GWT.search(ln) for ln in lines if not ln.lstrip().startswith("#")):
        errors.append("нет ни одного сценария Given-When-Then — дельта фикса обязана нести "
                      "регресс-сценарий бага (Given <условие> When <действие> Then <ожидание>)")

    if _CODE_FENCE.search(raw):
        errors.append("в дельте есть код-блок (```java/sql/xml/diff) — спека описывает "
                      "поведение, а не реализацию")
    if _CODE_SIGNS.search(raw):
        errors.append("в дельте есть сигнатуры кода (import/@RestController/public class) — "
                      "спека описывает поведение, а не реализацию")

    # ── якорь в мастере: modify vs add + защита от потери сценариев ────────────────
    ops_fmt: list[str] = []
    if master_reqs is None:
        warnings.append("требования-мастер не резолвится — якоря не проверены; после прогона "
                        "сверь план слияния: /forge-spec diff <slug>")
    elif candidates and _engine is not None:
        ops = _engine.plan_ops(master_reqs, candidates)
        ops_fmt = _engine.format_ops(ops)
        if master_reqs and all(o["op"] == "add" for o in ops):
            warnings.append("ни одно название требования не совпало с мастером — merge заведёт "
                            "НОВЫЕ требования (+) вместо правки существующих (~). Если баг ломает "
                            "уже описанное поведение, скопируй название требования мастера дословно")
        for o in ops:
            if o["op"] != "modify":
                continue
            was = len(o["existing"].get("scenarios") or [])
            now = len(o["cand"].get("scenarios") or [])
            if now < was and not allow_scenario_drop:
                errors.append(
                    f"{o['id']}: в дельте {now} сценариев, в мастере {was} — merge заменяет "
                    f"требование ЦЕЛИКОМ, недостающие сценарии будут потеряны. Скопируй блок "
                    f"требования из мастера полностью и внеси в него точечную правку "
                    f"(осознанное удаление сценария — флаг --allow-scenario-drop)")

    # ── зафиксированное решение о якоре (sources.spec_anchor) ─────────────────────
    # Якорь выбирает find_spec_anchor.py (детерминированно) либо пользователь, если
    # свидетельств не хватило. Здесь проверяем, что дельта правит ИМЕННО его — иначе
    # решение записано для галочки, а дельта ушла в другое требование.
    anchor_norm = (anchor or "").strip()
    if anchor_norm and anchor_norm.lower() not in ("none", "нет", "-"):
        if master_reqs is None or _engine is None:
            warnings.append(f"якорь '{anchor_norm}' не проверен — мастер не резолвится")
        else:
            ops = _engine.plan_ops(master_reqs, candidates) if candidates else []
            hit = any(
                o["op"] == "modify" and (
                    (o["id"] or "").lower() == anchor_norm.lower()
                    or _engine._norm(o["cand"]["title"]) == _engine._norm(anchor_norm))
                for o in ops)
            if not hit:
                touched = ", ".join(f"{o['op']} {o['id'] or '<new>'}: {o['cand']['title']}"
                                    for o in ops) or "ничего"
                errors.append(
                    f"дельта не правит зафиксированный якорь '{anchor_norm}' (sources.spec_anchor). "
                    f"Она трогает: {touched}. Либо назови требование дословно как в мастере, "
                    f"либо перепиши решение: config.py set sources.spec_anchor <REQ-ID|none>")

    # ── связь мини-плана с дельтой ────────────────────────────────────────────────
    if plan is not None:
        tasks = plan.get("tasks") or []
        if not tasks:
            errors.append("в мини-плане нет задач (tasks) — нечего связывать со спекой")
        for t in tasks:
            tid = t.get("id", "?")
            acc = t.get("acceptance")
            if not acc or not isinstance(acc, list) or not any(str(a).strip() for a in acc):
                errors.append(f"task {tid}: пустой/отсутствует acceptance")
            if not t.get("sdd_ref"):
                errors.append(f"task {tid}: нет sdd_ref (ссылки на затронутое требование)")

    return {"status": "pass" if not errors else "fail",
            "requirements": len(candidates), "ops": ops_fmt,
            "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("delta", help="путь к дельте (sdd.md фикса)")
    ap.add_argument("--plan", help="мини-план фикса (task-plan.json) — проверить acceptance/sdd_ref")
    ap.add_argument("--anchor", help="зафиксированный якорь (sources.spec_anchor): REQ-ID, "
                                     "название требования мастера либо 'none'")
    ap.add_argument("--project-root", help="корень репо кода (для резолва мастера через docs.master.*)")
    ap.add_argument("--spec", help="явный путь к мастеру specs/<cap>/spec.md")
    ap.add_argument("--capability", help="capability мастера")
    ap.add_argument("--max-requirements", type=int, default=DEFAULT_MAX_REQUIREMENTS)
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    ap.add_argument("--allow-scenario-drop", action="store_true",
                    help="разрешить, что в дельте сценариев меньше, чем в мастере (осознанное удаление)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    delta_path = Path(args.delta)
    if not delta_path.exists():
        msg = {"status": "fail", "errors": [f"нет дельты: {delta_path}"], "warnings": []}
        print(json.dumps(msg, ensure_ascii=False) if args.json
              else f"[check_fix_delta] FAIL: нет дельты: {delta_path}", file=sys.stderr)
        return 2

    plan = None
    if args.plan:
        try:
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[check_fix_delta] FAIL: не читается мини-план {args.plan}: {e}", file=sys.stderr)
            return 2

    root = Path(args.project_root).resolve() if args.project_root else None
    master_reqs, spec_path = _load_master_reqs(root, args.spec, args.capability)

    verdict = check_delta(delta_path.read_text(encoding="utf-8"),
                          master_reqs=master_reqs, plan=plan, anchor=args.anchor,
                          max_requirements=args.max_requirements, max_lines=args.max_lines,
                          allow_scenario_drop=args.allow_scenario_drop)
    verdict["delta"] = str(delta_path)
    if spec_path:
        verdict["master"] = str(spec_path)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        head = "PASS" if verdict["status"] == "pass" else "FAIL"
        print(f"[check_fix_delta] {head}: требований в дельте — {verdict['requirements']}")
        for op in verdict["ops"]:
            print(f"   {op}")
        for w in verdict["warnings"]:
            print(f"   ⚠ {w}")
        for e in verdict["errors"]:
            print(f"   ✗ {e}", file=sys.stderr)
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
