#!/usr/bin/env python3
"""merge_delta_to_master.py — движок слияния принятой дельты (sdd.md) в требования-мастер.

Аналог OpenSpec «archive delta→master», но операции наши: требование мастера — блок
`### <PREFIX>-NNNN: <название>` с проверяемым утверждением и вложенными сценариями
Given-When-Then. Тождество требования держит стабильный ID, поэтому переименование не рвёт
связь и отдельной операции «renamed» не нужно.

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

DEFAULT_ID_PREFIX = "REQ"

_GWT = re.compile(r"(?i)given.*when.*then")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_FROM_TAG = re.compile(r"\[from:[^\]]*\]")

# Маркеры разделов мастера (см. master-spec-template.md). Легаси-заголовки — для migrate.
_SEC_REQUIREMENTS = ["требования и сценарии", "requirements", "требования (require"]
_SEC_SCENARIOS_LEGACY = ["сценарии (given", "scenarios"]
_SEC_AUDIT = ["журнал изменений", "audit trail"]

# Маркеры разделов дельты (sdd.md).
_SEC_DELTA_FUNC = ["функциональные требования", "given-when-then"]
_SEC_DELTA_ACCEPT = ["критерии приёмки", "критерии приемки", "acceptance"]
_SEC_DELTA_PURPOSE = ["назначение и результат", "purpose"]


# ── общие утилиты ──────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Нормализация для сравнения: без провенанса, разметки и лишних пробелов."""
    s = _FROM_TAG.sub("", s)
    s = re.sub(r"[*`\-]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _norm_block(lines: list[str]) -> str:
    return "\n".join(_norm(l) for l in lines if _norm(l))


def _tags(text: str) -> list[str]:
    return _FROM_TAG.findall(text)


def _req_pat(prefix: str) -> "re.Pattern[str]":
    return re.compile(r"^\s{0,3}###\s+(" + re.escape(prefix) + r"-(\d+))\s*:\s*(.+?)\s*$")


def _h2_span(lines: list[str], markers: list[str]) -> "tuple[int, int] | None":
    """Границы раздела уровня `##` ВМЕСТЕ с его подзаголовками: (idx заголовка, idx конца)."""
    start = None
    for i, ln in enumerate(lines):
        m = _HEADING.match(ln)
        if not m:
            continue
        level, head = len(m.group(1)), m.group(2).lower()
        if start is None:
            if level == 2 and any(mk in head for mk in markers):
                start = i
        elif level <= 2:
            return (start, i)
    return (start, len(lines)) if start is not None else None


# ── разбор мастера ─────────────────────────────────────────────────────

def parse_master(text: str, prefix: str = DEFAULT_ID_PREFIX) -> list[dict]:
    """Требования мастера: [{id, num, title, statement, scenarios, tags, start, end}]."""
    pat = _req_pat(prefix)
    lines = text.splitlines()
    out: list[dict] = []
    cur: "dict | None" = None
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            if cur is not None:
                cur["end"] = i
                out.append(cur)
            cur = {"id": m.group(1), "num": int(m.group(2)), "title": m.group(3),
                   "body": [], "start": i}
            continue
        if cur is None:
            continue
        if _HEADING.match(line):
            cur["end"] = i
            out.append(cur)
            cur = None
        else:
            cur["body"].append(line)
    if cur is not None:
        cur["end"] = len(lines)
        out.append(cur)

    for r in out:
        body = r.pop("body")
        r["scenarios"] = [l.strip() for l in body if _GWT.search(l)]
        stmt = [l for l in body if l.strip() and not _GWT.search(l)]
        r["statement"] = _FROM_TAG.sub("", " ".join(x.strip() for x in stmt)).strip()
        r["tags"] = _tags("\n".join(body))
        # хвостовые пустые строки блока в замену не входят
        while r["end"] - 1 > r["start"] and not lines[r["end"] - 1].strip():
            r["end"] -= 1
    return out


def render_requirement(rid: str, title: str, statement: str,
                       scenarios: list[str], tags: list[str]) -> list[str]:
    out = [f"### {rid}: {title.strip()}"]
    stmt = statement.strip() or title.strip()
    out.append(f"{stmt}  {' '.join(tags)}".rstrip())
    for s in scenarios:
        s = s.strip()
        out.append(s if s.startswith("-") else f"- {s}")
    return out


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

def plan_ops(master_reqs: list[dict], candidates: list[dict]) -> list[dict]:
    """Сопоставление по нормализованному названию: same | modify | add."""
    by_title = {_norm(r["title"]): r for r in master_reqs}
    ops: list[dict] = []
    for c in candidates:
        existing = by_title.get(_norm(c["title"]))
        if existing is None:
            ops.append({"op": "add", "id": None, "cand": c})
            continue
        same_stmt = _norm(existing["statement"]) == _norm(c["statement"])
        same_scen = _norm_block(existing["scenarios"]) == _norm_block(c["scenarios"])
        ops.append({"op": "same" if (same_stmt and same_scen) else "modify",
                    "id": existing["id"], "cand": c, "existing": existing})
    return ops


def format_ops(ops: list[dict]) -> list[str]:
    sign = {"add": "+", "modify": "~", "same": "="}
    return [f"{sign[o['op']]} {o['id'] or '<new>'}: {o['cand']['title']}"
            f" ({len(o['cand']['scenarios'])} сценар.)" for o in ops]


# ── применение ─────────────────────────────────────────────────────────

def _append_audit(lines: list[str], entry: str) -> bool:
    span = _h2_span(lines, _SEC_AUDIT)
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


def apply_ops(text: str, ops: list[dict], *, prefix: str, feature: str, today: str,
              allow_modify: bool = False, modify_ids: "set[str] | None" = None) -> dict:
    """Применяет план к тексту мастера. Возвращает {text, added, modified, blocked, audit}."""
    modify_ids = modify_ids or set()
    lines = text.splitlines()
    tag = f"[from: {feature} {today}]"

    added, modified, blocked = [], [], []

    # 1. modify — правим на месте, с хвоста, чтобы не сдвигать индексы предыдущих блоков
    mods = [o for o in ops if o["op"] == "modify"]
    for o in mods:
        if not (allow_modify or o["id"] in modify_ids):
            blocked.append(o)
    doable = [o for o in mods if all(o is not b for b in blocked)]
    for o in sorted(doable, key=lambda x: x["existing"]["start"], reverse=True):
        ex, c = o["existing"], o["cand"]
        tags = [t for t in ex["tags"] if t != tag] + [tag]
        block = render_requirement(ex["id"], c["title"], c["statement"], c["scenarios"], tags)
        lines[ex["start"]:ex["end"]] = block
        modified.append(ex["id"])

    # 2. add — в конец §5, ID продолжают нумерацию (после правок перечитываем мастер)
    adds = [o for o in ops if o["op"] == "add"]
    if adds:
        span = _h2_span(lines, _SEC_REQUIREMENTS)
        if span is None:
            return {"text": "\n".join(lines) + "\n", "added": [], "modified": modified,
                    "blocked": blocked, "audit": False,
                    "error": "в мастере нет раздела «Требования и сценарии» — проверь шаблон"}
        used = [r["num"] for r in parse_master("\n".join(lines), prefix)]
        insert_at = span[1]
        while insert_at - 1 > span[0] and not lines[insert_at - 1].strip():
            insert_at -= 1
        block: list[str] = []
        for o in adds:
            num = max(used, default=0) + 1
            used.append(num)
            rid = f"{prefix}-{num:04d}"
            c = o["cand"]
            block += [""] + render_requirement(rid, c["title"], c["statement"],
                                               c["scenarios"], [tag])
            added.append(rid)
            o["id"] = rid
        lines[insert_at:insert_at] = block

    # 3. журнал изменений
    audit = False
    if added or modified:
        parts = []
        if added:
            parts.append(f"добавлено {', '.join(added)}")
        if modified:
            parts.append(f"изменено {', '.join(modified)}")
        audit = _append_audit(lines, f"- {today} — {feature}: {'; '.join(parts)}")

    return {"text": "\n".join(lines) + "\n", "added": added, "modified": modified,
            "blocked": blocked, "audit": audit}


def remove_requirement(text: str, rid: str, *, reason: str, today: str,
                       prefix: str = DEFAULT_ID_PREFIX) -> dict:
    """Снимает требование по ID и пишет причину в журнал изменений."""
    reqs = parse_master(text, prefix)
    target = next((r for r in reqs if r["id"] == rid), None)
    if target is None:
        return {"status": "error", "error": f"требования {rid} нет в мастере"}
    lines = text.splitlines()
    del lines[target["start"]:target["end"]]
    _append_audit(lines, f"- {today} — снято {rid} «{target['title']}»: {reason}")
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
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "feature-pipeline" / "scripts"))
    import skill_paths
    cap = capability or skill_paths.master_capability(project_root)
    return skill_paths.master_spec_path(project_root, capability=cap), cap


def spec_options(project_root: Path) -> dict:
    """Блок `spec` из ground/pipeline.json (поведение мастера)."""
    p = Path(project_root) / "ground" / "pipeline.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        cfg = {}
    return cfg.get("spec") or {}


# ── верхнеуровневая операция ───────────────────────────────────────────

def merge(sdd_path: Path, spec_path: Path, template_path: Path, feature: str, capability: str,
          *, prefix: str = DEFAULT_ID_PREFIX, dry_run: bool = False,
          allow_modify: bool = False, modify_ids: "set[str] | None" = None) -> dict:
    if not sdd_path.exists():
        return {"status": "error", "error": f"нет дельты (sdd.md): {sdd_path}"}

    delta = sdd_path.read_text(encoding="utf-8", errors="replace")
    today = date.today().isoformat()

    created = False
    if not spec_path.exists():
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
    ops = plan_ops(parse_master(text, prefix), candidates)

    if dry_run:
        blocked = [o for o in ops if o["op"] == "modify"
                   and not (allow_modify or o["id"] in (modify_ids or set()))]
        return {"status": "ok", "dry_run": True, "spec": str(spec_path), "created": created,
                "feature": feature, "capability": capability, "ops": format_ops(ops),
                "kinds": [o["op"] for o in ops],
                "added": [], "modified": [], "blocked": [o["id"] for o in blocked],
                "candidates": len(candidates)}

    res = apply_ops(text, ops, prefix=prefix, feature=feature, today=today,
                    allow_modify=allow_modify, modify_ids=modify_ids)
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

    result = merge(Path(args.sdd), spec_path, template_path, args.feature, capability,
                   prefix=prefix, dry_run=args.dry_run, allow_modify=args.allow_modify,
                   modify_ids=set(args.modify))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
    return 3 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
