#!/usr/bin/env python3
"""check_master_spec.py — валидатор состава требований-мастера (specs/<cap>/spec.md).

Мастер описывает ЧТО делает капабилити (накопленные требования + сценарии), а не КАК.
Проверяет:
  1. Файл существует.
  2. Обязательные разделы по политике sdd.security_gate (та же ручка, что у дельты).
  3. Есть хотя бы один сценарий Given-When-Then.
  4. Нет утечки реализации (код-блоки/сигнатуры → FAIL; Liquibase → warning).

Группы разделов:
  CORE          — Назначение, Границы охвата, Ограничения, Требования, Сценарии, Критерии приёмки.
  SECURITY_ARCH — Архитектурный контекст (границы доверия) + Модель угроз (ДКБ).
  CONTEXTUAL    — Журнал изменений (Audit trail).
  REGULATORY    — Регуляторные требования.

Политика sdd.security_gate: hard | applicability (дефолт) | soft — см. check_sdd_doc.py.

Usage:
    check_master_spec.py <spec.md> [--pipeline-config <pipeline.json>] [--policy P] [--json]
Exit: 0 = pass, 2 = чего-то не хватает.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CORE_SECTIONS = [
    ["назначение и результат", "purpose"],
    ["границы охвата", "scope"],
    ["ограничения и допущения", "constraints", "нфт", "nfr"],
    ["requirements", "требования (require"],
    ["сценарии", "scenarios"],
    ["критерии приёмки", "критерии приемки", "acceptance"],
]
SECURITY_ARCH_SECTIONS = [
    ["архитектурный контекст", "architectural context", "границы доверия"],
    ["модель угроз", "threat model"],
]
CONTEXTUAL_SECTIONS = [
    ["журнал изменений", "audit trail"],
]
REGULATORY_SECTIONS = [
    ["регуляторные требования", "регуляторн", "regulatory", "compliance"],
]

_POLICIES = ("hard", "applicability", "soft")
_DEFAULT_POLICY = "applicability"

_GWT = re.compile(r"(?i)given.*when.*then")
_NOT_APPLICABLE = re.compile(r"(?i)не\s+примен|not\s+applicable|\bn/?a\b")
_PLACEHOLDER = re.compile(r"<[^>\n]+>")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")

_CODE_FENCE = re.compile(r"```(?:java|diff|kotlin|sql|xml)\b", re.IGNORECASE)
_CODE_SIGNS = re.compile(
    r"(?m)^\s*(?:import\s+[\w.]+;|@(?:RestController|Service|Entity|Repository|Component"
    r"|GetMapping|PostMapping|PutMapping|DeleteMapping)\b|public\s+(?:class|interface|enum)\s)"
)
_LIQUIBASE = re.compile(r"(?i)\b(?:changeSet|databaseChangeLog|liquibase)\b")


def _parse_sections(raw: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    head: str | None = None
    body: list[str] = []
    for line in raw.splitlines():
        m = _HEADING.match(line)
        if m:
            if head is not None:
                sections.append((head.lower(), "\n".join(body)))
            head, body = m.group(1).strip(), []
        elif head is not None:
            body.append(line)
    if head is not None:
        sections.append((head.lower(), "\n".join(body)))
    return sections


def _find_body(sections: list[tuple[str, str]], markers: list[str]) -> str | None:
    for head, body in sections:
        if any(mk in head for mk in markers):
            return body
    return None


def _has_content(body: str) -> bool:
    stripped = _PLACEHOLDER.sub("", body)
    stripped = re.sub(r"[\s\-*#>|`.]+", "", stripped)
    return len(stripped) >= 10


def _is_na(body: str) -> bool:
    return bool(_NOT_APPLICABLE.search(body))


def _has_gwt(raw: str) -> bool:
    """≥1 реальный сценарий Given-When-Then в ТЕЛЕ (не в заголовке «(Given-When-Then)»)."""
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if _GWT.search(line):
            return True
    return False


def _present(text_lower: str, sections: list[tuple[str, str]], markers: list[str]) -> bool:
    if _find_body(sections, markers) is not None:
        return True
    return any(mk in text_lower for mk in markers)


def _load_policy(pipeline_config: Path | None, explicit: str | None) -> str:
    if explicit in _POLICIES:
        return explicit
    candidates: list[Path] = []
    if pipeline_config:
        candidates.append(pipeline_config)
    candidates.append(Path.cwd() / "ground" / "pipeline.json")
    for path in candidates:
        try:
            if path.exists():
                cfg = json.loads(path.read_text(encoding="utf-8"))
                val = cfg.get("sdd", {}).get("security_gate")
                if val in _POLICIES:
                    return val
        except (json.JSONDecodeError, OSError):
            continue
    return _DEFAULT_POLICY


def _check_group(group, text_lower, sections, *, hard, allow_na, errors, warnings):
    for markers in group:
        title = markers[0]
        if not _present(text_lower, sections, markers):
            (errors if hard else warnings).append(
                f"{'нет' if hard else 'желателен'} обязательный раздел мастера: «{title}»")
            continue
        if not hard:
            continue
        body = _find_body(sections, markers)
        if body is None or _has_content(body):
            continue
        if allow_na and _is_na(body):
            continue
        if _is_na(body):
            errors.append(f"раздел «{title}» помечен «не применимо», но политика требует контент")
        else:
            errors.append(f"раздел «{title}» пуст — заполни или пометь «не применимо: <причина>»")


def check(spec_path: Path, policy: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not spec_path.exists():
        errors.append(f"нет мастер-спеки: {spec_path}")
        return {"status": "fail", "spec": str(spec_path), "policy": policy,
                "errors": errors, "warnings": warnings}

    raw = spec_path.read_text(encoding="utf-8", errors="replace")
    text = raw.lower()
    sections = _parse_sections(raw)

    _check_group(CORE_SECTIONS, text, sections,
                 hard=True, allow_na=False, errors=errors, warnings=warnings)

    if policy == "hard":
        sec_hard, sec_na, ctx_hard, reg_hard = True, True, True, True
    elif policy == "applicability":
        sec_hard, sec_na, ctx_hard, reg_hard = True, True, False, False
    else:  # soft
        sec_hard, sec_na, ctx_hard, reg_hard = False, True, False, False

    _check_group(SECURITY_ARCH_SECTIONS, text, sections,
                 hard=sec_hard, allow_na=sec_na, errors=errors, warnings=warnings)
    _check_group(CONTEXTUAL_SECTIONS, text, sections,
                 hard=ctx_hard, allow_na=True, errors=errors, warnings=warnings)
    _check_group(REGULATORY_SECTIONS, text, sections,
                 hard=reg_hard, allow_na=True, errors=errors, warnings=warnings)

    if not _has_gwt(raw):
        errors.append("в spec.md не найден ни один сценарий Given-When-Then")
    if _CODE_FENCE.search(raw):
        errors.append("в spec.md есть код-блок (```java/diff/sql/...) — мастер описывает "
                      "поведение словами, а не листингом (реализация — в коде/tech-design)")
    if _CODE_SIGNS.search(raw):
        errors.append("в spec.md есть сигнатуры кода (import/@RestController/public class) — убери")
    if _LIQUIBASE.search(raw):
        warnings.append("в spec.md упомянут Liquibase changeset — детали миграций не уровень мастера")

    status = "pass" if not errors else "fail"
    return {"status": status, "spec": str(spec_path), "policy": policy,
            "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="Master spec composition gate.")
    ap.add_argument("spec", help="путь к specs/<cap>/spec.md")
    ap.add_argument("--pipeline-config", default=None)
    ap.add_argument("--policy", choices=_POLICIES, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    policy = _load_policy(Path(args.pipeline_config) if args.pipeline_config else None, args.policy)
    verdict = check(Path(args.spec), policy)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"Master spec check [{policy}]: "
              f"{'✓ PASS' if verdict['status'] == 'pass' else '✗ FAIL'}")
        for e in verdict["errors"]:
            print(f"  ✗ {e}")
        for w in verdict["warnings"]:
            print(f"  · warn: {w}")
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
