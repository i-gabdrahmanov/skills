#!/usr/bin/env python3
"""merge_delta_to_master.py — движок слияния принятой дельты (sdd.md) в требования-мастер.

Операции наши: требование мастера — блок с проверяемым утверждением и вложенными сценариями.
Тождество требования держит стабильный ID, поэтому переименование не рвёт связь и отдельной
операции «renamed» не нужно.

ФОРМА мастера (уровень и вид заголовка требования, схема ID, стиль сценариев, якоря разделов,
провенанс) здесь НЕ зашита — её держит `spec_grammar.Grammar`: форже-родной `### REQ-0007: …`
это лишь дефолт (`spec_grammar.NATIVE`), а проект со своей спекой описывается профилем
(`analyze_spec.py` детектит, `spec.grammar.*` в policy.json подтверждает). Формат, который
профилем не выражается, — отказ (`status: unsupported`), а не запись в чужой документ
форже-блоками.

Операции плана:
  add    — кандидата дельты нет в мастере (совпадений по названию не нашлось)
  modify — название совпало, содержимое отличается (применяется только с явного разрешения)
  same   — название и содержимое совпали (идемпотентность: повторный merge ничего не делает)

Пользовательский вход — `spec_cli.py` (status/diff/merge/remove/check/migrate); этот модуль
можно звать и напрямую как CLI для скриптов.

Политика forge-no-delivery: пишет ТОЛЬКО в рабочее дерево клона мастер-репо; git add/commit/push
НЕ делает — это пользователь.

Usage:
    merge_delta_to_master.py --sdd <sdd.md> --feature <slug> [--project-root <root>]
        [--capability <cap>] [--spec <spec.md>] [--template <tpl>] [--id-prefix REQ]
        [--dry-run] [--allow-modify] [--modify REQ-0007] [--json]
Exit: 0 = ок, 2 = ошибка (нет дельты / не резолвится мастер), 3 = есть неразрешённые modify.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import spec_grammar as SG  # noqa: E402

DEFAULT_ID_PREFIX = SG.NATIVE["requirement"]["id_prefix"]

_GWT = SG._GWT
_HEADING = SG._HEADING
_FROM_TAG = SG._FROM_TAG

# Маркеры разделов мастера — дефолт из профиля (форже-родные значения). Живой разбор ходит
# через grammar.section_span: у проекта со своим мастером якоря другие.
_SEC_REQUIREMENTS = list(SG.NATIVE["requirements_section"])
_SEC_SCENARIOS_LEGACY = ["сценарии (given", "scenarios"]   # легаси-заголовки — только для migrate
_SEC_AUDIT = list(SG.NATIVE["audit_section"])

# Маркеры разделов дельты (sdd.md).
_SEC_DELTA_FUNC = ["функциональные требования", "given-when-then"]
_SEC_DELTA_ACCEPT = ["критерии приёмки", "критерии приемки", "acceptance"]
_SEC_DELTA_PURPOSE = ["назначение и результат", "purpose"]


# ── общие утилиты ──────────────────────────────────────────────────────

_norm = SG._norm
_norm_block = SG._norm_block


def _tags(text: str) -> list[str]:
    return _FROM_TAG.findall(text)


def grammar_for(prefix: str = DEFAULT_ID_PREFIX, grammar: "SG.Grammar | None" = None) -> "SG.Grammar":
    """Грамматика для операции: переданный профиль либо форже-родной с нужным префиксом ID."""
    if grammar is not None:
        return grammar
    profile = json.loads(json.dumps(SG.NATIVE))
    profile["requirement"]["id_prefix"] = prefix or DEFAULT_ID_PREFIX
    return SG.Grammar(profile)


def _req_pat(prefix: str) -> "re.Pattern[str]":
    return grammar_for(prefix).req_pattern()


def _h2_span(lines: list[str], markers: list[str]) -> "tuple[int, int] | None":
    """Границы раздела уровня `##` ВМЕСТЕ с его подзаголовками: (idx заголовка, idx конца)."""
    return SG.section_span(lines, markers)


# ── разбор мастера ─────────────────────────────────────────────────────

def parse_master(text: str, prefix: str = DEFAULT_ID_PREFIX,
                 grammar: "SG.Grammar | None" = None) -> list[dict]:
    """Требования мастера: [{id, num, title, statement, scenarios, tags, start, end}]."""
    return grammar_for(prefix, grammar).parse(text)


def render_requirement(rid: str, title: str, statement: str, scenarios: list[str],
                       tags: list[str], grammar: "SG.Grammar | None" = None) -> list[str]:
    return grammar_for(DEFAULT_ID_PREFIX, grammar).render(rid, title, statement, scenarios, tags)


# ── разбор дельты (sdd.md) ─────────────────────────────────────────────

def _delta_title(delta_text: str) -> str:
    for line in delta_text.splitlines():
        m = re.match(r"^#\s+SDD:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return ""


def _section_lines(lines: list[str], markers: list[str]) -> list[str]:
    span = _h2_span(lines, markers)
    return lines[span[0] + 1:span[1]] if span else []


def _gwt_lines(lines: list[str]) -> list[str]:
    out, seen = [], set()
    for line in lines:
        if line.lstrip().startswith("#"):
            continue                      # заголовок «(Given-When-Then)» — не сценарий
        if _GWT.search(line):
            n = _norm(line)
            if n and n not in seen:
                seen.add(n)
                out.append(line.strip())
    return out


def parse_delta(delta_text: str) -> list[dict]:
    """Кандидаты в требования из дельты: [{title, statement, scenarios}].

    Если §3 «Функциональные требования» структурирован подзаголовками `###` — каждый
    подзаголовок становится отдельным требованием со своими сценариями. Если нет (типовой
    плоский список GWT) — одно требование на фичу: название из заголовка дельты,
    утверждение из §1 «Назначение», сценарии из §3 плюс §7 «Критерии приёмки».
    """
    lines = delta_text.splitlines()
    func = _section_lines(lines, _SEC_DELTA_FUNC)

    subs: list[dict] = []
    cur: "dict | None" = None
    for line in func:
        m = _HEADING.match(line)
        if m and len(m.group(1)) >= 3:
            if cur:
                subs.append(cur)
            cur = {"title": m.group(2).strip(), "body": []}
        elif cur is not None:
            cur["body"].append(line)
    if cur:
        subs.append(cur)

    if subs:
        out = []
        for s in subs:
            scen = _gwt_lines(s["body"])
            stmt = " ".join(l.strip() for l in s["body"]
                            if l.strip() and not _GWT.search(l) and not l.lstrip().startswith("#"))
            out.append({"title": s["title"], "statement": stmt or s["title"], "scenarios": scen})
        return out

    scen = _gwt_lines(func) or []
    for extra in _gwt_lines(_section_lines(lines, _SEC_DELTA_ACCEPT)):
        if _norm(extra) not in {_norm(x) for x in scen}:
            scen.append(extra)

    purpose = [l.strip() for l in _section_lines(lines, _SEC_DELTA_PURPOSE)
               if l.strip() and not l.lstrip().startswith("#")]
    title = _delta_title(delta_text)
    return [{"title": title, "statement": (purpose[0] if purpose else title), "scenarios": scen}]


# ── план операций ──────────────────────────────────────────────────────

def plan_ops(master_reqs: list[dict], candidates: list[dict],
             grammar: "SG.Grammar | None" = None) -> list[dict]:
    """Сопоставление по нормализованному названию: same | modify | add."""
    g = grammar_for(DEFAULT_ID_PREFIX, grammar)
    by_title = {_norm(r["title"]): r for r in master_reqs}
    ops: list[dict] = []
    for c in candidates:
        existing = by_title.get(_norm(c["title"]))
        if existing is None:
            ops.append({"op": "add", "id": None, "cand": c})
            continue
        same_stmt = _norm(existing["statement"]) == _norm(c["statement"])
        same_scen = ("\n".join(g.scenario_key(x) for x in existing["scenarios"])
                     == "\n".join(g.scenario_key(x) for x in c["scenarios"]))
        ops.append({"op": "same" if (same_stmt and same_scen) else "modify",
                    "id": existing["id"], "cand": c, "existing": existing})
    return ops


def format_ops(ops: list[dict]) -> list[str]:
    sign = {"add": "+", "modify": "~", "same": "="}
    return [f"{sign[o['op']]} {o['id'] or '<new>'}: {o['cand']['title']}"
            f" ({len(o['cand']['scenarios'])} сценар.)" for o in ops]


# ── применение ─────────────────────────────────────────────────────────

def _append_audit(lines: list[str], entry: str, grammar: "SG.Grammar | None" = None) -> bool:
    g = grammar_for(DEFAULT_ID_PREFIX, grammar)
    span = g.section_span(lines, "audit")
    if span is None:
        return False
    i, end = span
    if any(_norm(entry) == _norm(l) for l in lines[i + 1:end]):
        return False
    k = end
    while k - 1 > i and not lines[k - 1].strip():
        k -= 1
    lines[k:k] = [entry]
    return True


def _num_of(rid: "str | None", g: "SG.Grammar"):
    """Номер из свежевыданного ID — чтобы следующая вставка в том же прогоне его учла."""
    if not rid:
        return None
    if g.kind == "numbered":
        return rid
    tail = rid.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def apply_ops(text: str, ops: list[dict], *, prefix: str, feature: str, today: str,
              allow_modify: bool = False, modify_ids: "set[str] | None" = None,
              grammar: "SG.Grammar | None" = None) -> dict:
    """Применяет план к тексту мастера. Возвращает {text, added, modified, blocked, audit}."""
    g = grammar_for(prefix, grammar)
    modify_ids = modify_ids or set()
    lines = text.splitlines()
    tag = g.provenance_tag(feature, today)

    added, modified, blocked = [], [], []

    # 1. modify — правим на месте, с хвоста, чтобы не сдвигать индексы предыдущих блоков
    mods = [o for o in ops if o["op"] == "modify"]
    for o in mods:
        if not (allow_modify or o["id"] in modify_ids):
            blocked.append(o)
    doable = [o for o in mods if all(o is not b for b in blocked)]
    for o in sorted(doable, key=lambda x: x["existing"]["start"], reverse=True):
        ex, c = o["existing"], o["cand"]
        tags = [t for t in ex["tags"] if t != tag] + ([tag] if tag else [])
        block = g.render(ex.get("rid", ex["id"]) if ex["num"] is not None else None,
                         c["title"], c["statement"], c["scenarios"], tags)
        lines[ex["start"]:ex["end"]] = block
        modified.append(ex["id"])

    # 2. add — в конец §5, ID продолжают нумерацию (после правок перечитываем мастер)
    adds = [o for o in ops if o["op"] == "add"]
    if adds:
        span = g.section_span(lines, "requirements")
        if span is None:
            sec = g.requirements_section[0] if g.requirements_section else "требований"
            return {"text": "\n".join(lines) + "\n", "added": [], "modified": modified,
                    "blocked": blocked, "audit": False,
                    "error": f"в мастере нет раздела «{sec}» — проверь шаблон либо якорь "
                             f"spec.grammar.requirements_section"}
        used = parse_master("\n".join(lines), prefix, g)
        insert_at = span[1]
        while insert_at - 1 > span[0] and not lines[insert_at - 1].strip():
            insert_at -= 1
        block: list[str] = []
        for o in adds:
            rid = g.next_id(used)
            c = o["cand"]
            # Тождество без ID держит название — оно и попадает в отчёт операций.
            used.append({"id": rid or c["title"], "num": _num_of(rid, g), "title": c["title"]})
            block += [""] + g.render(rid, c["title"], c["statement"], c["scenarios"],
                                     [tag] if tag else [])
            added.append(rid or c["title"])
            o["id"] = rid or c["title"]
        lines[insert_at:insert_at] = block

    # 3. журнал изменений
    audit = False
    if added or modified:
        parts = []
        if added:
            parts.append(f"добавлено {', '.join(added)}")
        if modified:
            parts.append(f"изменено {', '.join(modified)}")
        audit = _append_audit(lines, f"- {today} — {feature}: {'; '.join(parts)}", g)

    return {"text": "\n".join(lines) + "\n", "added": added, "modified": modified,
            "blocked": blocked, "audit": audit}


def remove_requirement(text: str, rid: str, *, reason: str, today: str,
                       prefix: str = DEFAULT_ID_PREFIX,
                       grammar: "SG.Grammar | None" = None) -> dict:
    """Снимает требование по ID и пишет причину в журнал изменений."""
    g = grammar_for(prefix, grammar)
    reqs = parse_master(text, prefix, g)
    target = next((r for r in reqs if r["id"] == rid), None)
    if target is None:
        return {"status": "error", "error": f"требования {rid} нет в мастере"}
    lines = text.splitlines()
    del lines[target["start"]:target["end"]]
    _append_audit(lines, f"- {today} — снято {rid} «{target['title']}»: {reason}", g)
    return {"status": "ok", "text": "\n".join(lines) + "\n", "removed": rid,
            "title": target["title"]}


# ── резолв путей / шаблон ──────────────────────────────────────────────

def template_skeleton(template_path: Path, capability: str) -> str:
    """Достаёт fenced ```markdown-скелет из master-spec-template.md и подставляет капабилити."""
    raw = template_path.read_text(encoding="utf-8")
    m = re.search(r"```markdown\n(.*?)\n```", raw, re.S)
    body = m.group(1) if m else raw
    return body.replace("<capability>", capability) + "\n"


# обратная совместимость с прежним именем
_template_skeleton = template_skeleton


def default_template() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "master-spec-template.md"


def resolve_spec(project_root: Path, capability: "str | None" = None,
                 explicit: "str | None" = None) -> "tuple[Path, str]":
    """Путь мастер-спеки и капабилити через skill_paths (master separate-repo aware)."""
    if explicit:
        return Path(explicit), (capability or "capability")
    # Без дублей: resolve_spec зовётся не один раз за процесс (archive.delta_state ходит по
    # прогонам), а голый insert растил sys.path на ДВЕ записи за вызов — 100 записей на
    # 50 вызовов, и каждый последующий импорт в процессе становился медленнее.
    for _p in (Path(__file__).resolve().parent,
               Path(__file__).resolve().parents[2] / "feature-pipeline" / "scripts"):
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
    import skill_paths
    cap = capability or skill_paths.master_capability(project_root)
    return skill_paths.master_spec_path(project_root, capability=cap), cap


def spec_options(project_root: Path) -> dict:
    """Блок `spec` из ground/{policy.json|pipeline.json} (поведение мастера).

    Phase 0 v2 refactor: двойной рид policy.json → pipeline.json через _config_loader."""
    try:
        from _config_loader import load_project_config
        cfg = load_project_config(project_root) or {}
    except Exception:
        # Fallback (на случай битого бандла): только legacy pipeline.json.
        p = Path(project_root) / "ground" / "pipeline.json"
        try:
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (json.JSONDecodeError, OSError):
            cfg = {}
    return cfg.get("spec") or {}


# ── верхнеуровневая операция ───────────────────────────────────────────

def merge(sdd_path: Path, spec_path: Path, template_path: Path, feature: str, capability: str,
          *, prefix: str = DEFAULT_ID_PREFIX, dry_run: bool = False,
          allow_modify: bool = False, modify_ids: "set[str] | None" = None,
          grammar: "SG.Grammar | None" = None) -> dict:
    if not sdd_path.exists():
        return {"status": "error", "error": f"нет дельты (sdd.md): {sdd_path}"}

    g = grammar_for(prefix, grammar)
    ok, why = g.supported()
    if not ok:
        # Fail-closed: форма мастера не выражается профилем. Писать сюда форже-блоками —
        # это порча чужого документа, а не «слияние», поэтому останавливаемся.
        return {"status": "unsupported", "spec": str(spec_path), "reasons": why,
                "profile": g.describe(),
                "error": "формат требований-мастера не описан профилем: " + "; ".join(why)}

    delta = sdd_path.read_text(encoding="utf-8", errors="replace")
    today = date.today().isoformat()

    created = False
    if not spec_path.exists():
        if not g.is_native():
            # Мастера нет по настроенному пути, а форма проекта известна — почти всегда это
            # docs.master.spec_path, который ещё не поправили (мастер лежит там, где его нашёл
            # ресерч). Создавать здесь форже-шаблон значит навязать проекту формат, от которого
            # его как раз и уводили, а вернуть "error" — соврать вызывающему, что «делать
            # нечего»: _state_of посчитал бы дельту слитой и отпустил её в архив мимо мастера.
            why = [f"мастера нет по настроенному пути ({spec_path}), а форма мастера у проекта "
                   f"своя — проверь docs.master.spec_path (/forge-spec research показывает, где "
                   f"мастер найден) либо заведи мастер сам"]
            return {"status": "unsupported", "spec": str(spec_path), "reasons": why,
                    "profile": g.describe(), "error": why[0]}
        if dry_run:
            text = template_skeleton(template_path, capability)
            created = True
        else:
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(template_skeleton(template_path, capability), encoding="utf-8")
            created = True
            text = spec_path.read_text(encoding="utf-8", errors="replace")
    else:
        text = spec_path.read_text(encoding="utf-8", errors="replace")

    candidates = parse_delta(delta)
    ops = plan_ops(parse_master(text, prefix, g), candidates, g)

    if dry_run:
        blocked = [o for o in ops if o["op"] == "modify"
                   and not (allow_modify or o["id"] in (modify_ids or set()))]
        return {"status": "ok", "dry_run": True, "spec": str(spec_path), "created": created,
                "feature": feature, "capability": capability, "ops": format_ops(ops),
                "kinds": [o["op"] for o in ops],
                "added": [], "modified": [], "blocked": [o["id"] for o in blocked],
                "candidates": len(candidates)}

    res = apply_ops(text, ops, prefix=prefix, feature=feature, today=today,
                    allow_modify=allow_modify, modify_ids=modify_ids, grammar=g)
    if res.get("error"):
        return {"status": "error", "error": res["error"]}
    spec_path.write_text(res["text"], encoding="utf-8")

    blocked = [o["id"] for o in res["blocked"]]
    return {"status": "blocked" if blocked else "ok", "spec": str(spec_path), "created": created,
            "feature": feature, "capability": capability, "ops": format_ops(ops),
            "kinds": [o["op"] for o in ops],
            "added": res["added"], "modified": res["modified"], "blocked": blocked,
            "audit_added": res["audit"], "candidates": len(candidates)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge accepted delta (sdd.md) into master spec.")
    ap.add_argument("--sdd", required=True, help="путь к принятой дельте sdd.md")
    ap.add_argument("--feature", required=True, help="slug фичи (провенанс)")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--capability", default=None)
    ap.add_argument("--spec", default=None, help="явный путь к specs/<cap>/spec.md (минует резолвер)")
    ap.add_argument("--template", default=None, help="путь к master-spec-template.md")
    ap.add_argument("--id-prefix", default=None)
    ap.add_argument("--dry-run", action="store_true", help="показать план операций, не писать")
    ap.add_argument("--allow-modify", action="store_true", help="применить все ~ (modify)")
    ap.add_argument("--modify", action="append", default=[], help="применить modify только для ID")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    try:
        spec_path, capability = resolve_spec(project_root, args.capability, args.spec)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"не резолвится мастер: {e}"},
                         ensure_ascii=False))
        return 2

    prefix = args.id_prefix or spec_options(project_root).get("id_prefix") or DEFAULT_ID_PREFIX
    template_path = Path(args.template) if args.template else default_template()
    grammar = SG.load_profile(project_root)
    if args.id_prefix:                      # явный префикс перекрывает профиль
        grammar.id_prefix = args.id_prefix

    result = merge(Path(args.sdd), spec_path, template_path, args.feature, capability,
                   prefix=prefix, dry_run=args.dry_run, allow_modify=args.allow_modify,
                   modify_ids=set(args.modify), grammar=grammar)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["status"] == "unsupported":
        print(f"✗ {result.get('error')}")
        print(result.get("profile", ""))
        print("   Уточни профиль: config.py set spec.grammar.<ручка> <значение> "
              "(разбор — /forge-spec research)")
    elif result["status"] == "error":
        print(f"✗ {result.get('error')}")
    else:
        head = "план" if result.get("dry_run") else "merge"
        print(f"{head} → {result['spec']}"
              f"{' (создан из шаблона)' if result['created'] else ''}")
        for line in result["ops"]:
            print(f"   {line}")
        if not result.get("dry_run"):
            print(f"   добавлено: {len(result['added'])}, изменено: {len(result['modified'])}")
            print("   Напоминание: закоммить/запушь мастер-репо сам (forge не коммитит).")
        if result["blocked"]:
            print(f"   ! требуют подтверждения (modify): {', '.join(result['blocked'])}")

    if result["status"] == "error":
        return 2
    if result["status"] == "unsupported":
        return 3                             # нужно решение человека, а не «ошибка скрипта»
    return 3 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
