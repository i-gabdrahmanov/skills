#!/usr/bin/env python3
"""check_master_spec.py — валидатор состава требований-мастера (specs/<cap>/spec.md).

Мастер описывает ЧТО делает капабилити (накопленные требования + сценарии), а не КАК.
Проверяет:
  1. Файл существует.
  2. Обязательные разделы по политике sdd.security_gate (та же ручка, что у дельты).
  3. Требования: ≥1 блок `### <PREFIX>-NNNN: <название>`, ID уникальны, у КАЖДОГО —
     минимум один сценарий Given-When-Then (пол сценариев, spec.scenario_floor).
  4. Нет утечки реализации (код-блоки/сигнатуры → FAIL; Liquibase → warning).

Группы разделов:
  CORE          — Назначение, Границы охвата, Ограничения, Критерии приёмки.
  REQUIREMENTS  — §5 «Требования и сценарии» (проверяется отдельно: блоки с ID + сценарии).
  SECURITY_ARCH — Архитектурный контекст (границы доверия) + Модель угроз (ДКБ).
  CONTEXTUAL    — Журнал изменений (Audit trail).
  REGULATORY    — Регуляторные требования.

Политика sdd.security_gate: hard | applicability (дефолт) | soft — см. check_sdd_doc.py.
Ручки требований (ground/pipeline.json): spec.id_prefix (дефолт REQ),
spec.scenario_floor (дефолт true).

Usage:
    check_master_spec.py <spec.md> [--pipeline-config <pipeline.json>] [--policy P]
        [--id-prefix REQ] [--no-scenario-floor] [--json]
Exit: 0 = pass, 2 = чего-то не хватает.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import find_project_root  # noqa: E402  (co-локейд резолвер корня данных)
# Phase 0 v2 refactor: load_project_config — двойной рид policy.json → pipeline.json.
# common.py уже проложил путь к hooks/ через sys.path (см. его блок импортов).
from _config_loader import load_project_config  # noqa: E402

CORE_SECTIONS = [
    ["назначение и результат", "purpose"],
    ["границы охвата", "scope"],
    ["ограничения и допущения", "constraints", "нфт", "nfr"],
    ["критерии приёмки", "критерии приемки", "acceptance"],
]
# §5 — единственный раздел, тело которого живёт в подзаголовках (### PREFIX-NNNN), поэтому
# generic-проверка «раздел непуст» ему не подходит: он валидируется _check_requirements.
# Маркеры включают легаси-заголовки плоского формата (до перехода на ID) — тогда раздел
# найдётся, но требований с ID в нём не будет и ошибка укажет на /forge-spec migrate.
REQUIREMENTS_SECTION = ["требования и сценарии", "requirements", "требования (require"]
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
_DEFAULT_ID_PREFIX = "REQ"

_GWT = re.compile(r"(?i)given.*when.*then")
_NOT_APPLICABLE = re.compile(r"(?i)не\s+примен|not\s+applicable|\bn/?a\b")
_PLACEHOLDER = re.compile(r"<[^>\n]+>")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")

_CODE_FENCE = re.compile(r"```(?:java|diff|kotlin|sql|xml)\b", re.IGNORECASE)
_CODE_SIGNS = re.compile(
    r"(?m)^\s*(?:import\s+[\w.]+;|@(?:RestController|Service|Entity|Repository|Component"
    r"|GetMapping|PostMapping|PutMapping|DeleteMapping)\b|public\s+(?:class|interface|enum)\s)"
)
_LIQUIBASE = re.compile(r"(?i)\b(?:changeSet|databaseChangeLog|liquibase)\b")


def _parse_sections(raw: str) -> list[tuple[str, str]]:
    """[(heading_lower, body), ...]; тело — до следующего заголовка ТОГО ЖЕ ИЛИ БОЛЕЕ ВЫСОКОГО
    уровня, то есть ВКЛЮЧАЯ вложенные подразделы. Раньше тело обрывалось на любом следующем
    заголовке, и раздел, расписанный подразделами, судья считал пустым (см. одноимённую
    правку в sdd/scripts/check_sdd_doc.py)."""
    lines = raw.splitlines()
    heads: list[tuple[int, int, str]] = []   # (номер строки, уровень, заголовок)
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    sections: list[tuple[str, str]] = []
    for n, (i, level, title) in enumerate(heads):
        end = len(lines)
        for j, lvl, _title in heads[n + 1:]:
            if lvl <= level:
                end = j
                break
        sections.append((title.lower(), "\n".join(lines[i + 1:end])))
    return sections


def _find_body(sections: list[tuple[str, str]], markers: list[str]) -> str | None:
    """Маркеры перебираются В ПОРЯДКЕ СПИСКА (от специфичного к общему), иначе общий маркер
    цепляется за заголовок документа вместо своего раздела."""
    for mk in markers:
        for head, body in sections:
            if mk in head:
                return body
    return None


def _has_content(body: str) -> bool:
    stripped = _PLACEHOLDER.sub("", body)
    stripped = re.sub(r"[\s\-*#>|`.]+", "", stripped)
    return len(stripped) >= 10


def _is_na(body: str) -> bool:
    return bool(_NOT_APPLICABLE.search(body))


# Многострочный сценарий: Given / When / Then отдельными строками — КАНОНИЧЕСКАЯ форма
# Gherkin, и именно так их пишут люди и модели. Однострочный поиск `given.*when.*then` её не
# видел и валил корректную спеку «нет ни одного сценария Given-When-Then» (та же правка уже
# сделана в sdd/scripts/check_sdd_doc.py — здесь дубль логики отставал).
_GWT_STEP = re.compile(r"(?i)^\s*(?:[-*+>]\s*)?(?:\*\*|__|_|\*)?\s*(given|when|then)\b")
_GWT_WINDOW = 12          # строк между Given и Then — сценарий, а не совпадение через весь док


def _has_gwt(raw: str) -> bool:
    """≥1 реальный сценарий Given-When-Then в ТЕЛЕ (не в заголовке «(Given-When-Then)»).

    Засчитывается и однострочная запись, и многострочная (Given/When/Then подряд, в пределах
    _GWT_WINDOW строк, с учётом markdown-разметки: `**Given** …`, `- given …`, `> Given …`)."""
    seen = None                       # какой шаг сценария ждём следующим
    start = -1
    for i, line in enumerate(raw.splitlines()):
        if line.lstrip().startswith("#"):
            continue
        if _GWT.search(line):
            return True               # всё в одной строке — прежнее поведение
        m = _GWT_STEP.match(line)
        if not m:
            continue
        step = m.group(1).lower()
        if step == "given":
            seen, start = "when", i
        elif step == "when" and seen == "when" and i - start <= _GWT_WINDOW:
            seen = "then"
        elif step == "then" and seen == "then" and i - start <= _GWT_WINDOW:
            return True
        else:
            seen, start = (None, -1) if step != "given" else ("when", i)
    return False


def _req_heading(prefix: str) -> "re.Pattern[str]":
    """`### REQ-0007: <название>` — подзаголовок требования со стабильным ID."""
    return re.compile(r"^\s{0,3}###\s+(" + re.escape(prefix) + r"-(\d+))\s*:\s*(.+?)\s*$")


def _parse_requirements(raw: str, prefix: str) -> list[dict]:
    """Блоки требований [{id, num, title, body}]; тело — до следующего заголовка уровня ###
    и выше. Вложенные под-заголовки (`#### …`) остаются ЧАСТЬЮ требования: иначе требование,
    расписанное подпунктами, теряло тело и валилось как «нет проверяемого утверждения»."""
    pat = _req_heading(prefix)
    out: list[dict] = []
    cur: "dict | None" = None
    for line in raw.splitlines():
        m = pat.match(line)
        if m:
            if cur is not None:
                out.append(cur)
            cur = {"id": m.group(1), "num": int(m.group(2)), "title": m.group(3), "body": []}
            continue
        if cur is None:
            continue
        h = _HEADING.match(line)
        if h and len(h.group(1)) <= 3:  # заголовок уровня ### и выше закрывает блок
            out.append(cur)
            cur = None
        else:
            cur["body"].append(line)
    if cur is not None:
        out.append(cur)
    for r in out:
        r["body"] = "\n".join(r["body"])
    return out


def _check_requirements(raw, sections, text_lower, prefix, scenario_floor, errors, warnings):
    """§5: раздел на месте, ≥1 требование с ID, ID уникальны, у каждого — утверждение и сценарий."""
    if not _present(text_lower, sections, REQUIREMENTS_SECTION):
        errors.append("нет обязательный раздел мастера: «требования и сценарии»")

    reqs = _parse_requirements(raw, prefix)
    if not reqs:
        errors.append(
            f"нет ни одного требования вида «### {prefix}-NNNN: <название>» — мастер наполняет "
            f"/forge-spec merge (плоский легаси-формат переносится /forge-spec migrate)")
        return

    counts: dict[str, int] = {}
    for r in reqs:
        counts[r["id"]] = counts.get(r["id"], 0) + 1
    for rid, n in sorted(counts.items()):
        if n > 1:
            errors.append(f"ID {rid} встречается {n} раза — идентификаторы требований уникальны")

    for r in reqs:
        title = r["title"].strip()
        if not title or _PLACEHOLDER.fullmatch(title):
            errors.append(f"{r['id']}: пустое название требования")
        # утверждение = тело без строк-сценариев; требование без него — это просто набор сценариев
        statement = "\n".join(l for l in r["body"].splitlines()
                               if not _GWT.search(l) and not _GWT_STEP.match(l))
        if not _has_content(statement):
            errors.append(f"{r['id']} «{title}»: нет проверяемого утверждения (что система делает)")
        if scenario_floor and not _has_gwt(r["body"]):
            errors.append(f"{r['id']} «{title}»: нет ни одного сценария Given-When-Then")


def _present(text_lower: str, sections: list[tuple[str, str]], markers: list[str]) -> bool:
    if _find_body(sections, markers) is not None:
        return True
    return any(mk in text_lower for mk in markers)


def _cfg(pipeline_config: Path | None) -> dict:
    """ground/{policy.json|pipeline.json} (явный путь или из cwd). Никогда не бросает.

    Phase 0 v2 refactor: явный --pipeline-config путь читается как есть (для совместимости
    с CLI и тестами, передающими произвольный JSON-конфиг), а фолбэк идёт через
    load_project_config — двойной рид policy.json → pipeline.json, легаси v1 продолжает
    работать. Новые v2-проекты без --pipeline-config теперь находятся по policy.json."""
    if pipeline_config and pipeline_config.exists():
        try:
            return json.loads(pipeline_config.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    # Фолбэк через _config_loader (доступен через sys.path, добавленный common.py).
    return load_project_config(find_project_root())


def _load_policy(pipeline_config: Path | None, explicit: str | None) -> str:
    if explicit in _POLICIES:
        return explicit
    val = (_cfg(pipeline_config).get("sdd") or {}).get("security_gate")
    return val if val in _POLICIES else _DEFAULT_POLICY


def _load_spec_opts(pipeline_config: Path | None, id_prefix: str | None,
                    scenario_floor: bool | None) -> tuple[str, bool]:
    """Ручки требований: CLI > ground/pipeline.json → spec.* > дефолты (REQ, floor включён)."""
    spec = _cfg(pipeline_config).get("spec") or {}
    prefix = id_prefix or spec.get("id_prefix") or _DEFAULT_ID_PREFIX
    floor = scenario_floor if scenario_floor is not None else spec.get("scenario_floor", True)
    return str(prefix), bool(floor)


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


def check(spec_path: Path, policy: str, *, id_prefix: str = _DEFAULT_ID_PREFIX,
          scenario_floor: bool = True) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not spec_path.exists():
        errors.append(f"нет мастер-спеки: {spec_path}")
        return {"status": "fail", "spec": str(spec_path), "policy": policy,
                "id_prefix": id_prefix, "scenario_floor": scenario_floor, "requirements": 0,
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

    _check_requirements(raw, sections, text, id_prefix, scenario_floor, errors, warnings)

    if _CODE_FENCE.search(raw):
        errors.append("в spec.md есть код-блок (```java/diff/sql/...) — мастер описывает "
                      "поведение словами, а не листингом (реализация — в коде/tech-design)")
    if _CODE_SIGNS.search(raw):
        errors.append("в spec.md есть сигнатуры кода (import/@RestController/public class) — убери")
    if _LIQUIBASE.search(raw):
        warnings.append("в spec.md упомянут Liquibase changeset — детали миграций не уровень мастера")

    status = "pass" if not errors else "fail"
    return {"status": status, "spec": str(spec_path), "policy": policy,
            "id_prefix": id_prefix, "scenario_floor": scenario_floor,
            "requirements": len(_parse_requirements(raw, id_prefix)),
            "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="Master spec composition gate.")
    ap.add_argument("spec", help="путь к specs/<cap>/spec.md")
    ap.add_argument("--pipeline-config", default=None)
    ap.add_argument("--policy", choices=_POLICIES, default=None)
    ap.add_argument("--id-prefix", default=None, help="префикс ID требований (дефолт spec.id_prefix)")
    ap.add_argument("--no-scenario-floor", dest="scenario_floor", action="store_false", default=None,
                    help="не требовать сценарий у каждого требования")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pcfg = Path(args.pipeline_config) if args.pipeline_config else None
    policy = _load_policy(pcfg, args.policy)
    prefix, floor = _load_spec_opts(pcfg, args.id_prefix, args.scenario_floor)
    verdict = check(Path(args.spec), policy, id_prefix=prefix, scenario_floor=floor)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"Master spec check [{policy}, {verdict['requirements']} требований]: "
              f"{'✓ PASS' if verdict['status'] == 'pass' else '✗ FAIL'}")
        for e in verdict["errors"]:
            print(f"  ✗ {e}")
        for w in verdict["warnings"]:
            print(f"  · warn: {w}")
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
