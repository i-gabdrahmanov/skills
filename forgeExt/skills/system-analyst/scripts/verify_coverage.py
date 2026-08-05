#!/usr/bin/env python3
"""verify_coverage.py — gate самопроверки полноты.

Сверяет, что в записанном результате (grounding-excerpt.json или counts-JSON) не потерян
ни один артефакт, детерминированно найденный сканером. Правило: для HARD-категории
reported >= deterministic (LLM может ДОБАВИТЬ, но не молча выкинуть). HARD-недобор →
status=fail и exit-код 2, чтобы оркестратор не прошёл шаг с дырой.

Usage:
    verify_coverage.py --scan <scan-dir> --reported <excerpt.json> [--json]
    verify_coverage.py --scan <scan-dir> --reported-counts '{"entities":14,...}' [--json]

scan-dir — каталог из scan_all.py (domain.json, api.json, summary.json, …).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Независимый кросс-чек: грубый счёт аннотаций по коду (мимо структурных парсеров).
# Это НИЖНЯЯ граница того, что обязан найти сканер — ловит недосчёт самого сканера,
# который основной gate (reported>=scan) увидеть не может (он сверяет excerpt со scan,
# а не scan с кодом). Берём только аннотации, дающие чистую нижнюю границу:
#   • @Entity            — каждая = одна сущность (gate_total domain — тоже entity-only);
#   • @KafkaListener     — каждая = один консьюмер;
#   • @Get/Post/Put/Delete/PatchMapping — всегда method-level (в отличие от @RequestMapping,
#     который бывает на классе), значит их число ≤ числу эндпойнтов.
_CROSS_CHECK_RE = {
    "domain": re.compile(r"@Entity\b"),
    "async_consumers": re.compile(r"@KafkaListener\b"),
    "api": re.compile(r"@(?:Get|Post|Put|Delete|Patch)Mapping\b"),
}

# category(scan) -> (excerpt-ключ, hard?)
HARD = {
    "domain": "entities",
    "api": "api_endpoints",
    "async_consumers": "async",
}
ADVISORY = {
    "async_producers": "async",
    "integration": "external_clients",
    "db": "tables",
    "reuse": "reuse",
}


def _load(scan_dir: Path, cat: str) -> dict:
    p = scan_dir / f"{cat}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"gate_total": 0, "total": 0, "items": []}


def _reported_count(reported: dict, key: str, cat: str) -> int:
    if "counts" in reported and isinstance(reported["counts"], dict):
        alias = {"entities": "entities", "api_endpoints": "endpoints",
                 "external_clients": "external_clients", "tables": "tables"}.get(key, key)
        if alias in reported["counts"]:
            return int(reported["counts"][alias])
    val = reported.get(key, [])
    if cat == "reuse" and isinstance(val, dict):
        # excerpt-секция reuse: {dependencies:[...], project_utils:[...]}
        return len(val.get("dependencies", [])) + len(val.get("project_utils", []))
    if isinstance(val, list):
        if cat == "async_consumers":
            cons = [x for x in val if isinstance(x, dict) and x.get("direction") == "consumer"]
            return len(cons) if cons else len(val)
        if cat == "async_producers":
            return len([x for x in val if isinstance(x, dict) and x.get("direction") == "producer"])
        return len(val)
    return int(val) if isinstance(val, (int, float)) else 0


def _missing_entities(scan_dir: Path, reported: dict) -> list[str]:
    det = {i["name"] for i in _load(scan_dir, "domain").get("items", []) if i.get("kind") == "entity"}
    rep = {e.get("name") for e in reported.get("entities", []) if isinstance(e, dict)}
    return sorted(det - rep)


def cross_check(code_root: Path) -> dict:
    """Независимый счёт аннотаций по коду (нижняя граница) для детекта недосчёта сканера.

    Возвращает {category: raw_count}. Тестовые сорсы и комментарии исключены (как и в
    сканерах), чтобы границы были like-for-like.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from common import iter_java, read_text, strip_comments  # type: ignore
    except Exception:
        return {}
    counts = {cat: 0 for cat in _CROSS_CHECK_RE}
    for p in iter_java(Path(code_root)):
        text = strip_comments(read_text(p))
        for cat, rx in _CROSS_CHECK_RE.items():
            counts[cat] += len(rx.findall(text))
    return counts


def verify(scan_dir: Path, reported: dict, code_root: Path | None = None) -> dict:
    hard_rows, advisory_rows, ok = [], [], True
    raw = cross_check(code_root) if code_root else {}
    warnings: list[str] = []
    for cat, key in HARD.items():
        det = _load(scan_dir, cat).get("gate_total", 0)
        rep = _reported_count(reported, key, cat)
        passed = rep >= det
        row = {"category": cat, "reported_as": key, "reported": rep, "deterministic": det, "ok": passed}
        if cat == "domain" and not passed:
            miss = _missing_entities(scan_dir, reported)
            row["missing_examples"] = miss[:20]
            row["missing_count"] = len(miss)
        # Кросс-чек: сканер обязан найти не меньше, чем грубый счёт аннотаций.
        if cat in raw:
            row["raw_annotations"] = raw[cat]
            if det < raw[cat]:
                row["scanner_undercount"] = raw[cat] - det
                warnings.append(
                    f"{cat}: сканер нашёл {det}, аннотаций в коде ≥{raw[cat]} "
                    f"(возможен недосчёт сканера — gate против него слеп; прогони полный system-analyst)")
        if not passed:
            ok = False
        hard_rows.append(row)
    for cat, key in ADVISORY.items():
        det = _load(scan_dir, cat).get("gate_total", 0)
        rep = _reported_count(reported, key, cat)
        advisory_rows.append({"category": cat, "reported_as": key, "reported": rep,
                              "deterministic": det, "ok": rep >= det})
    verdict = {
        "status": "pass" if ok else "fail",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "hard": hard_rows,
        "advisory": advisory_rows,
    }
    if warnings:
        verdict["warnings"] = warnings
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(description="Self-check: reported coverage vs deterministic ground truth.")
    ap.add_argument("--scan", required=True, help="scan dir produced by scan_all.py")
    ap.add_argument("--reported", help="grounding-excerpt.json (or any json with entities/api_endpoints/...)")
    ap.add_argument("--reported-counts", help="inline JSON: {\"entities\":N,\"endpoints\":N,...}")
    ap.add_argument("--code-root", default=None,
                    help="корень кода для независимого кросс-чека (детект недосчёта сканера)")
    ap.add_argument("--json", action="store_true", help="print full verdict JSON")
    args = ap.parse_args()

    scan_dir = Path(args.scan)
    if args.reported_counts:
        reported = {"counts": json.loads(args.reported_counts)}
    elif args.reported:
        reported = json.loads(Path(args.reported).read_text(encoding="utf-8"))
    else:
        print("ERROR: pass --reported or --reported-counts", file=sys.stderr)
        return 1

    verdict = verify(scan_dir, reported, Path(args.code_root) if args.code_root else None)
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✓ PASS" if verdict["status"] == "pass" else "✗ FAIL"
        print(f"Coverage self-check: {mark}")
        for r in verdict["hard"]:
            flag = "✓" if r["ok"] else "✗"
            extra = ""
            if not r["ok"] and "missing_count" in r:
                extra = f"  missing {r['missing_count']}: {', '.join(r.get('missing_examples', [])[:6])}"
            if "scanner_undercount" in r:
                extra += f"  ⚠ raw≥{r['raw_annotations']} (сканер недосчитал {r['scanner_undercount']})"
            print(f"  [HARD] {flag} {r['category']:18} reported {r['reported']:>4} / det {r['deterministic']:>4}{extra}")
        for r in verdict["advisory"]:
            flag = "✓" if r["ok"] else "·"
            print(f"  [adv ] {flag} {r['category']:18} reported {r['reported']:>4} / det {r['deterministic']:>4}")
        for w in verdict.get("warnings", []):
            print(f"  ⚠️  {w}")
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
