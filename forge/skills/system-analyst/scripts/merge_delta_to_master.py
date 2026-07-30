#!/usr/bin/env python3
"""merge_delta_to_master.py — слияние принятой дельты (sdd.md) в требования-мастер.

Аналог OpenSpec «archive delta→master» на фазе 06. Берёт принятый `sdd.md` фичи (дельта,
рядом с кодом) и АПСЕРТИТ его сценарии/требования в `specs/<cap>/spec.md` (мастер, обычно в
отдельном/удалённом репо), с провенансом `[from: <feature> <date>]`. Идемпотентно (повтор не
плодит дубли). Создаёт spec.md из шаблона, если его ещё нет.

Политика forge-no-delivery: пишет ТОЛЬКО в рабочее дерево клона мастер-репо; git add/commit/push
НЕ делает — это пользователь.

Usage:
    merge_delta_to_master.py --sdd <sdd.md> --feature <slug> [--project-root <root>]
        [--capability <cap>] [--spec <spec.md>] [--template <tpl>] [--json]
Exit: 0 = ок, 2 = ошибка (нет дельты / не резолвится мастер).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

_GWT = re.compile(r"(?i)given.*when.*then")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_FROM_TAG = re.compile(r"\[from:[^\]]*\]")

# Маркеры целевых разделов мастера (см. master-spec-template.md).
_SEC_REQUIREMENTS = ["requirements", "требования (require"]
_SEC_SCENARIOS = ["сценарии", "scenarios"]
_SEC_AUDIT = ["журнал изменений", "audit trail"]


def _norm(s: str) -> str:
    s = _FROM_TAG.sub("", s)
    s = re.sub(r"[*`\-]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _template_skeleton(template_path: Path, capability: str) -> str:
    """Достаёт fenced ```markdown-скелет из master-spec-template.md и подставляет капабилити."""
    raw = template_path.read_text(encoding="utf-8")
    m = re.search(r"```markdown\n(.*?)\n```", raw, re.S)
    body = m.group(1) if m else raw
    return body.replace("<capability>", capability) + "\n"


def _delta_title(delta_text: str) -> str:
    for line in delta_text.splitlines():
        m = re.match(r"^#\s+SDD:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return ""


def _extract_gwt(delta_text: str) -> list[str]:
    """GWT-строки дельты (из §3 Функциональные требования и §7 Критерии приёмки)."""
    out, seen = [], set()
    for line in delta_text.splitlines():
        if line.lstrip().startswith("#"):
            continue  # заголовок «(Given-When-Then)» — не сценарий
        if _GWT.search(line):
            norm = _norm(line)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(line.strip())
    return out


def _sections(lines: list[str]) -> list[tuple[int, int]]:
    """Границы разделов [(heading_idx, end_idx_exclusive), ...]."""
    heads = [i for i, ln in enumerate(lines) if _HEADING.match(ln)]
    spans = []
    for k, i in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        spans.append((i, end))
    return spans


def _find_section(lines: list[str], markers: list[str]) -> "tuple[int, int] | None":
    for i, end in _sections(lines):
        head = lines[i].lower()
        if any(mk in head for mk in markers):
            return (i, end)
    return None


def _upsert(lines: list[str], markers: list[str], new_lines: list[str]) -> int:
    """Апсертит new_lines в конец раздела (дедуп по _norm). Возвращает число вставленных."""
    span = _find_section(lines, markers)
    if span is None:
        return 0
    i, end = span
    existing = {_norm(l) for l in lines[i + 1:end] if l.strip()}
    filtered = [nl for nl in new_lines if _norm(nl) and _norm(nl) not in existing]
    if not filtered:
        return 0
    # точка вставки — после последней непустой строки тела раздела
    k = end
    while k - 1 > i and not lines[k - 1].strip():
        k -= 1
    lines[k:k] = filtered
    return len(filtered)


def merge(sdd_path: Path, spec_path: Path, template_path: Path,
          feature: str, capability: str) -> dict:
    if not sdd_path.exists():
        return {"status": "error", "error": f"нет дельты (sdd.md): {sdd_path}"}

    delta = sdd_path.read_text(encoding="utf-8", errors="replace")
    today = date.today().isoformat()
    tag = f"[from: {feature} {today}]"

    created = False
    if not spec_path.exists():
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(_template_skeleton(template_path, capability), encoding="utf-8")
        created = True

    lines = spec_path.read_text(encoding="utf-8", errors="replace").splitlines()

    # 1. Сценарии (GWT) дельты → §6 Сценарии
    gwt = _extract_gwt(delta)
    scen_lines = []
    for g in gwt:
        body = g.lstrip("-* ").rstrip()
        scen_lines.append(f"- {body}  {tag}")
    scenarios_added = _upsert(lines, _SEC_SCENARIOS, scen_lines)

    # 2. Требование-запись фичи → §5 Требования (одна строка на фичу, идемпотентно)
    title = _delta_title(delta) or feature
    req_line = f"- {title}  {tag}"
    requirement_added = _upsert(lines, _SEC_REQUIREMENTS, [req_line])

    # 3. Журнал изменений → §10 Audit trail (дедуп по фиче+дате)
    audit_line = f"- {today} — {feature}: слито сценариев {scenarios_added} (требование: {title})"
    audit_span = _find_section(lines, _SEC_AUDIT)
    audit_added = 0
    if audit_span is not None:
        i, end = audit_span
        if not any(f"— {feature}:" in l and today in l for l in lines[i + 1:end]):
            audit_added = _upsert(lines, _SEC_AUDIT, [audit_line])

    spec_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"status": "ok", "spec": str(spec_path), "created": created,
            "feature": feature, "capability": capability,
            "scenarios_added": scenarios_added, "requirement_added": requirement_added,
            "audit_added": audit_added, "gwt_in_delta": len(gwt)}


def _resolve_spec(project_root: Path, capability: "str | None",
                  explicit: "str | None") -> "tuple[Path, str]":
    """Резолв пути мастер-спеки и капабилити через skill_paths (master separate-repo aware)."""
    if explicit:
        cap = capability or "capability"
        return Path(explicit), cap
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "feature-pipeline" / "scripts"))
    import skill_paths
    cap = capability or skill_paths.master_capability(project_root)
    return skill_paths.master_spec_path(project_root, capability=cap), cap


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge accepted delta (sdd.md) into master spec.")
    ap.add_argument("--sdd", required=True, help="путь к принятой дельте sdd.md")
    ap.add_argument("--feature", required=True, help="slug фичи (провенанс)")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--capability", default=None)
    ap.add_argument("--spec", default=None, help="явный путь к specs/<cap>/spec.md (минует резолвер)")
    ap.add_argument("--template", default=None, help="путь к master-spec-template.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    try:
        spec_path, capability = _resolve_spec(project_root, args.capability, args.spec)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"не резолвится мастер: {e}"},
                         ensure_ascii=False))
        return 2

    template_path = (Path(args.template) if args.template
                     else Path(__file__).resolve().parent.parent / "references" / "master-spec-template.md")

    result = merge(Path(args.sdd), spec_path, template_path, args.feature, capability)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["status"] != "ok":
            print(f"✗ {result.get('error')}")
        else:
            print(f"✅ merge → {result['spec']} "
                  f"({'создан из шаблона; ' if result['created'] else ''}"
                  f"сценариев +{result['scenarios_added']}, требований +{result['requirement_added']}, "
                  f"audit +{result['audit_added']})")
            print("   Напоминание: закоммить/запушь мастер-репо сам (forge не коммитит).")
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
