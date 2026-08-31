#!/usr/bin/env python3
"""check_sdd_doc.py — валидатор состава документа SDD (PDLC v3.5, стр. 66).

Проверяет ТОЛЬКО сам `sdd.md` (фаза 02-sdd, task-plan ещё нет):
  1. Файл существует.
  2. Есть все обязательные разделы по политике `sdd.security_gate`.
  3. Есть хотя бы один сценарий Given-When-Then.
  4. Нет утечки реализации (код-блоки/сигнатуры → FAIL; Liquibase → warning).

Состав разделов делится на группы:
  - CORE          — всегда обязательны (ядро спеки).
  - SECURITY_ARCH — Архитектурный контекст (границы доверия) + Модель угроз (ДКБ).
  - CONTEXTUAL    — Пользовательские истории/привилегированные сценарии + Принятые решения.
  - REGULATORY    — Регуляторные требования.

Жёсткость групп задаётся `sdd.security_gate` в ground/pipeline.json:
  - hard          — все группы обязательны с контентом; REGULATORY/SECURITY_ARCH допускают
                    явное «не применимо: <причина>».
  - applicability — (дефолт) SECURITY_ARCH обязателен (контент или «не применимо»);
                    CONTEXTUAL/REGULATORY — warning, если нет.
  - soft          — SECURITY_ARCH/CONTEXTUAL/REGULATORY только warning; жёстко только CORE.

Линковку task-plan ↔ sdd.md (acceptance + sdd_ref у каждой задачи) проверяет
`tech-design/scripts/check_sdd.py` уже на фазе 02-design.

Usage:
    check_sdd_doc.py <sdd.md> [--pipeline-config <pipeline.json>] [--policy hard|applicability|soft] [--json]
Exit: 0 = pass, 2 = чего-то не хватает.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import _root  # find_project_root: ко-локейд резолвер корня данных (как hooks/_project)

# --- Группы разделов. Каждый элемент — список синонимов-маркеров (любой в заголовке = раздел
#     найден). Синонимы дают обратную совместимость со старыми sdd.md и англ. заголовками. ---
CORE_SECTIONS = [
    ["назначение и результат", "purpose", "бизнес-контекст"],
    ["границы охвата", "scope"],
    ["функциональные требования", "functional"],
    ["ограничения и допущения", "constraints", "нефункциональные", "нфт", "nfr"],
    ["api-контракт", "api"],
    ["модель данных", "data model"],
    ["критерии приёмки", "критерии приемки", "acceptance"],
]
SECURITY_ARCH_SECTIONS = [
    ["архитектурный контекст", "architectural context", "границы доверия"],
    ["модель угроз", "threat model"],
]
CONTEXTUAL_SECTIONS = [
    ["пользовательские истории", "user stor", "привилегированные сценарии"],
    ["принятые решения", "prior decision"],
]
REGULATORY_SECTIONS = [
    ["регуляторные требования", "регуляторн", "regulatory", "compliance"],
]

_POLICIES = ("hard", "applicability", "soft")
_DEFAULT_POLICY = "applicability"

_GWT = re.compile(r"(?i)given.*when.*then")
_NOT_APPLICABLE = re.compile(r"(?i)не\s+примен|not\s+applicable|\bn/?a\b")
_PLACEHOLDER = re.compile(r"<[^>\n]+>")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")

# Признаки утечки реализации в SDD (спека = «что», а не «как»):
_CODE_FENCE = re.compile(r"```(?:java|diff|kotlin|sql|xml)\b", re.IGNORECASE)
_CODE_SIGNS = re.compile(
    r"(?m)^\s*(?:import\s+[\w.]+;|@(?:RestController|Service|Entity|Repository|Component"
    r"|GetMapping|PostMapping|PutMapping|DeleteMapping)\b|public\s+(?:class|interface|enum)\s)"
)
_LIQUIBASE = re.compile(r"(?i)\b(?:changeSet|databaseChangeLog|liquibase)\b")


def _parse_sections(raw: str) -> list[tuple[str, str]]:
    """Разбивает документ на разделы по заголовкам. Возвращает [(heading_lower, body), ...].

    Тело раздела — всё до следующего заголовка ТОГО ЖЕ ИЛИ БОЛЕЕ ВЫСОКОГО уровня, то есть
    ВКЛЮЧАЯ вложенные подразделы. Раньше тело обрывалось на любом следующем заголовке, и
    раздел, расписанный подразделами (обычная форма: «## 5. API-контракт» → «### 5.1 POST
    /users …»), получал ПУСТОЕ тело: судья валил корректный SDD с «раздел пуст», модель
    дописывала текст, снова получала fail — и так до исчерпания лимита ре-итераций.
    """
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
    """Тело раздела, чей ЗАГОЛОВОК содержит любой из markers. None — заголовок не найден.

    Маркеры перебираются В ПОРЯДКЕ СПИСКА (от специфичного к общему): иначе на документе
    «# SDD: API рассылки» общий маркер «api» цеплялся бы за заголовок документа, а не за
    «## 5. API-контракт», и телом раздела оказывался бы весь документ (или шапка)."""
    for mk in markers:
        for head, body in sections:
            if mk in head:
                return body
    return None


def _has_content(body: str) -> bool:
    """В теле есть осмысленный контент (не только placeholder'ы/буллеты/пробелы)."""
    stripped = _PLACEHOLDER.sub("", body)
    stripped = re.sub(r"[\s\-*#>|`.]+", "", stripped)
    return len(stripped) >= 10


def _is_na(body: str) -> bool:
    return bool(_NOT_APPLICABLE.search(body))


# Многострочный сценарий: Given / When / Then отдельными строками — КАНОНИЧЕСКАЯ форма
# Gherkin, и именно так их пишут люди и модели. Однострочный поиск `given.*when.*then`
# её не видел, а сообщение («не найден ни один сценарий Given-When-Then») формы не
# подсказывало: на e2e-прогоне SDD с корректным трёхстрочным сценарием был отклонён
# трижды подряд и сжёг лимит ре-итераций судьи до ESCALATE.
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


def _present(text_lower: str, sections: list[tuple[str, str]], markers: list[str]) -> bool:
    """Раздел присутствует: маркер в заголовке ИЛИ в тексте (лениво, обратно-совместимо)."""
    if _find_body(sections, markers) is not None:
        return True
    return any(mk in text_lower for mk in markers)


def _section_title(markers: list[str]) -> str:
    """Человекочитаемое имя раздела для сообщений (первый маркер)."""
    return markers[0]


def _load_policy(pipeline_config: Path | None, explicit: str | None) -> str:
    """Резолв политики: --policy > pipeline-config (legacy/произвольный JSON) > auto-detect (двойной рид policy.json → pipeline.json) > дефолт."""
    if explicit in _POLICIES:
        return explicit
    cfg: dict = {}
    if pipeline_config and pipeline_config.exists():
        try:
            cfg = json.loads(pipeline_config.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}
    if not cfg:
        try:
            from _config_loader import load_project_config  # noqa: E402
            cfg = load_project_config(_root.find_project_root()) or {}
        except Exception:
            cfg = {}
    val = (cfg.get("sdd") or {}).get("security_gate")
    return val if val in _POLICIES else _DEFAULT_POLICY


def _check_group(
    group: list[list[str]],
    text_lower: str,
    sections: list[tuple[str, str]],
    *,
    hard: bool,
    allow_na: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Проверяет группу разделов.

    hard=True  → отсутствие раздела ИЛИ пустое тело (без контента/NA) даёт error;
                 при allow_na=False «не применимо» не засчитывается как контент.
    hard=False → отсутствие раздела даёт warning.
    """
    for markers in group:
        title = _section_title(markers)
        present = _present(text_lower, sections, markers)
        if not present:
            (errors if hard else warnings).append(
                f"{'нет' if hard else 'желателен'} обязательный раздел SDD: «{title}»"
            )
            continue
        # Раздел присутствует — при hard проверяем, что он не пустой.
        if not hard:
            continue
        body = _find_body(sections, markers)
        if body is None:
            # Присутствует только в прозе (без заголовка) — засчитываем как контент.
            continue
        if _has_content(body):
            continue
        if allow_na and _is_na(body):
            continue
        if _is_na(body):
            # NA есть, но политика его не принимает (hard без allow_na).
            errors.append(f"раздел «{title}» помечен «не применимо», но политика требует контент")
        else:
            errors.append(f"раздел «{title}» пуст — заполни или пометь «не применимо: <причина>»")


def check(sdd_path: Path, policy: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not sdd_path.exists():
        errors.append(f"нет SDD-документа: {sdd_path}")
        return {"status": "fail", "sdd": str(sdd_path), "policy": policy,
                "errors": errors, "warnings": warnings}

    raw = sdd_path.read_text(encoding="utf-8", errors="replace")
    text = raw.lower()
    sections = _parse_sections(raw)

    # 1. CORE — всегда жёстко (контент подразумевается наличием раздела).
    _check_group(CORE_SECTIONS, text, sections,
                 hard=True, allow_na=False, errors=errors, warnings=warnings)

    # 2. SECURITY_ARCH / CONTEXTUAL / REGULATORY — по политике.
    if policy == "hard":
        _check_group(SECURITY_ARCH_SECTIONS, text, sections,
                     hard=True, allow_na=True, errors=errors, warnings=warnings)
        _check_group(CONTEXTUAL_SECTIONS, text, sections,
                     hard=True, allow_na=False, errors=errors, warnings=warnings)
        _check_group(REGULATORY_SECTIONS, text, sections,
                     hard=True, allow_na=True, errors=errors, warnings=warnings)
    elif policy == "applicability":
        _check_group(SECURITY_ARCH_SECTIONS, text, sections,
                     hard=True, allow_na=True, errors=errors, warnings=warnings)
        _check_group(CONTEXTUAL_SECTIONS, text, sections,
                     hard=False, allow_na=True, errors=errors, warnings=warnings)
        _check_group(REGULATORY_SECTIONS, text, sections,
                     hard=False, allow_na=True, errors=errors, warnings=warnings)
    else:  # soft
        _check_group(SECURITY_ARCH_SECTIONS, text, sections,
                     hard=False, allow_na=True, errors=errors, warnings=warnings)
        _check_group(CONTEXTUAL_SECTIONS, text, sections,
                     hard=False, allow_na=True, errors=errors, warnings=warnings)
        _check_group(REGULATORY_SECTIONS, text, sections,
                     hard=False, allow_na=True, errors=errors, warnings=warnings)

    # 3. Хотя бы один сценарий Given-When-Then (в теле, не в заголовке).
    if not _has_gwt(raw):
        errors.append("в sdd.md не найден ни один сценарий Given-When-Then")

    # 4. SDD — «что», не «как»: код в спеке = утечка реализации.
    if _CODE_FENCE.search(raw):
        errors.append("в sdd.md есть код-блок (```java/diff/sql/...) — спека описывает "
                      "поведение словами, а не листингом; убери код (он уровень tech-design)")
    if _CODE_SIGNS.search(raw):
        errors.append("в sdd.md есть сигнатуры кода (import/@RestController/public class) — "
                      "это уровень tech-design, убери из спеки")
    if _LIQUIBASE.search(raw):
        warnings.append("в sdd.md упомянут Liquibase changeset — миграции описывай на уровне "
                        "«какие таблицы/поля», детали changeset — в tech-design")

    status = "pass" if not errors else "fail"
    return {"status": status, "sdd": str(sdd_path), "policy": policy,
            "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="Strict SDD composition gate.")
    ap.add_argument("sdd", help="путь к sdd.md")
    ap.add_argument("--pipeline-config", default=None,
                    help="путь к ground/pipeline.json (для sdd.security_gate)")
    ap.add_argument("--policy", choices=_POLICIES, default=None,
                    help="явно задать политику (перекрывает конфиг)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pipeline_config = Path(args.pipeline_config) if args.pipeline_config else None
    policy = _load_policy(pipeline_config, args.policy)

    verdict = check(Path(args.sdd), policy)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        print(f"SDD doc check [{policy}]: "
              f"{'✓ PASS' if verdict['status'] == 'pass' else '✗ FAIL'}")
        for e in verdict["errors"]:
            print(f"  ✗ {e}")
        for w in verdict["warnings"]:
            print(f"  · warn: {w}")
    return 0 if verdict["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
