#!/usr/bin/env python3
"""forge_events.py — append-only журнал evidence пайплайна.

ЗАЧЕМ. Каждый факт о прогоне («шаг закрыл реальный субагент», «детерминированный гейт
прошёл», «судья вынес вердикт», «человек сказал да») раньше был ОТДЕЛЬНЫМ файлом:
_origins/<step>.json, gates/<step>.json, judges/<judge>.json, overrides/<name>.json,
approvals/<key>.json. На прогон фичи это ~30 файлов по сотне байт, и раскладка была не
прихотью, а следствием топологии: факты пишут ШЕСТЬ независимых процессов в разное время
(хук на SubagentStop, record_gate по exit-коду, run_judge, record_approval), а верифицирует
их один — update.py. Файл-на-факт давал lock-free запись без гонок за общий файл.

Append-only лог даёт то же свойство другим примитивом: несколько писателей дописывают
строки под общим замком (flock/msvcrt), read-modify-write общего файла ни у кого нет.
Взамен получаем то, чего у россыпи файлов не было:

  • Провенанс обязателен ПО КОНСТРУКЦИИ. Раньше проверялся неровно: approval-маркер
    сверялся по produced_by, а origin-маркер — простым .exists() (файл-пустышка по нужному
    пути засчитывался). Здесь запись без ожидаемого produced_by не видна свёртке вообще.
  • Переоткрытие шага — событие, а не файловая хирургия. rollback раньше руками переносил
    в архив _origins/X.json, gates/X.json и каждый judges/*.json. Теперь пишется одна
    запись kind="reopen", и свёртка перестаёт видеть evidence шага, сделанное ДО неё.
    История при этом остаётся на диске и читаема — раньше она уезжала в архив.
  • Один путь под защитой state-write-guard вместо шести regex-паттернов раскладки.

ДВА ЛОГА, ПО ДВУМ ОБЛАСТЯМ ВИДИМОСТИ (не сливаем — это разный скоуп):
  • ground/statements/<skill>/<feature>/events.jsonl — evidence прогона фичи;
  • ground/approvals.jsonl — согласия человека; часть ключей не привязана к фиче
    (human-approval, security-review, change-advisory), поэтому лог проектный.

СОВМЕСТИМОСТЬ. Читатели сначала смотрят лог, потом — старые файлы. Прогон, начатый до
обновления, дочитывается со старой раскладки; писатели в неё больше не пишут.

НЕ ТРОГАЕМ здесь: journal/files.jsonl и phases/agent-evidence.jsonl — они и так по одному
файлу на прогон, к «россыпи мелких файлов» отношения не имеют.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import (append_locked, approvals_dir, gate_result_path, ground_dir,  # noqa: E402
                      judge_path, origin_path, override_path, state_dir)

EVENTS_FILE = "events.jsonl"
APPROVALS_FILE = "approvals.jsonl"

# kind → скрипт/хук, которому разрешено этот факт порождать. Запись с чужим (или
# отсутствующим) produced_by свёрткой игнорируется: подделать evidence, дописав строку
# мимо писателя, нельзя — а прямая запись в лог инструментами блокируется
# state-write-guard'ом, как раньше блокировались каталоги-маркеры.
PRODUCERS = {
    "origin": "state-recorder",
    "gate": "record_gate",
    "judge": "run_judge",
    "override": "override_judge",
    "reopen": "rollback",
    "approval": "record_approval",
    "approval-revoked": "rollback",
    "grounding": "grounding-evidence",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Пути ──────────────────────────────────────────────────────────────────────

def events_path(root: Path, skill: str, feature: str) -> Path:
    return state_dir(root, skill, feature) / EVENTS_FILE


def approvals_path(root: Path) -> Path:
    return ground_dir(root) / APPROVALS_FILE


# ── Запись ────────────────────────────────────────────────────────────────────

# Поля, которые проставляет журнал и которые payload перебить НЕ может. Иначе вердикт
# судьи с полем "produced_by" в теле сам себе назначал бы провенанс — ровно та подделка,
# от которой защищает свёртка.
_CONTROL_FIELDS = ("ts", "kind", "produced_by")


def _append(path: Path, kind: str, produced_by: str, payload: dict) -> dict:
    rec = {k: v for k, v in payload.items() if v is not None and k not in _CONTROL_FIELDS}
    rec.update({"ts": _iso_now(), "kind": kind, "produced_by": produced_by})
    # Одна строка одним write под замком: частично записанной строки в логе не бывает.
    append_locked(path, json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# Параметры позиционно-обязательные (`/`): иначе payload с ключом "kind"/"produced_by"
# — а он приезжает как **verdict от run_judge — падал бы TypeError'ом вместо того, чтобы
# быть отброшенным как служебное поле.
def append_event(root: Path, skill: str, feature: str, kind: str, /, **payload) -> dict:
    """Дописать факт в лог прогона. produced_by берётся из PRODUCERS по kind."""
    if kind not in PRODUCERS:
        raise ValueError(f"неизвестный kind события: {kind!r} (ожидается {sorted(PRODUCERS)})")
    return _append(events_path(root, skill, feature), kind, PRODUCERS[kind], payload)


def append_approval(root: Path, key: str, /, **payload) -> dict:
    """Дописать согласие человека в проектный лог approvals."""
    payload.pop("key", None)
    return _append(approvals_path(root), "approval", PRODUCERS["approval"],
                   {"key": key, **payload})


# ── Чтение ────────────────────────────────────────────────────────────────────

def read_log(path: Path) -> list[dict]:
    """Строки лога как список dict. Битая строка пропускается с предупреждением, а не
    роняет чтение: лог append-only, повреждение одной строки не должно ослеплять гейт
    по остальным (полное отсутствие evidence и так означает блокировку)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[dict] = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            print(f"[forge-events] {path}:{i}: строка не JSON — пропущена", file=sys.stderr)
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def read_events(root: Path, skill: str, feature: str) -> list[dict]:
    return read_log(events_path(root, skill, feature))


def _authentic(rec: dict, kind: str) -> bool:
    """Запись засчитывается только с ожидаемым для этого kind провенансом."""
    return rec.get("kind") == kind and rec.get("produced_by") == PRODUCERS.get(kind)


def _reopen_boundary(events: Iterable[dict], *, step: Optional[str] = None,
                     judge_name: Optional[str] = None,
                     override_target: Optional[str] = None) -> int:
    """Индекс ПОСЛЕДНЕГО переоткрытия, обнуляющего это evidence: всё до него не считается.

    Переоткрытие (rollback, ре-итерация после FAIL) обязано снимать доказательства шага —
    иначе откаченный шаг снова закроется по origin/gate от прошлой попытки. Раньше это
    делалось переносом файлов в архив; здесь — граница в логе, история остаётся видимой.

    Шаг обнуляет своё origin/gate по step_id, а вердикты судей и overrides — по спискам
    judges/overrides в самой записи reopen: судья привязан к фазе, а не к шагу, и его
    имя из step_id не выводится.
    """
    boundary = -1
    for i, rec in enumerate(events):
        if not _authentic(rec, "reopen"):
            continue
        if step is not None and rec.get("step_id") == step:
            boundary = i
        elif judge_name is not None and judge_name in (rec.get("judges") or []):
            boundary = i
        elif override_target is not None and override_target in (rec.get("overrides") or []):
            boundary = i
    return boundary


def _last(events: list[dict], kind: str, *, step_id: Optional[str] = None,
          match: Optional[dict] = None, boundary: int = -1) -> Optional[dict]:
    """Последняя аутентичная запись нужного вида (позже — важнее: ре-прогон судьи или
    гейта перекрывает прежний вердикт, ровно как раньше перезапись файла)."""
    found = None
    for i, rec in enumerate(events):
        if i <= boundary or not _authentic(rec, kind):
            continue
        if step_id is not None and rec.get("step_id") != step_id:
            continue
        if match and any(rec.get(k) != v for k, v in match.items()):
            continue
        found = rec
    return found


# ── Свёртка: вопросы, которые задают гейты ────────────────────────────────────

def origin(root: Path, skill: str, feature: str, step_id: str,
           events: Optional[list[dict]] = None) -> Optional[dict]:
    """Evidence «шаг закрыл реальный SubagentStop». None — нет.
    Фолбэк на старую раскладку _origins/<step>.json для прогонов до миграции."""
    ev = events if events is not None else read_events(root, skill, feature)
    rec = _last(ev, "origin", step_id=step_id,
                boundary=_reopen_boundary(ev, step=step_id))
    if rec is not None:
        return rec
    if _reopen_boundary(ev, step=step_id) >= 0:
        return None  # шаг переоткрыт — старый файл-маркер тоже не считается
    return _legacy_marker(origin_path(root, skill, feature, step_id), require_provenance=False)


def gate(root: Path, skill: str, feature: str, step_id: str,
         events: Optional[list[dict]] = None) -> Optional[dict]:
    """Результат детерминированного гейта шага (record_gate). None — гейт не запускался."""
    ev = events if events is not None else read_events(root, skill, feature)
    boundary = _reopen_boundary(ev, step=step_id)
    rec = _last(ev, "gate", step_id=step_id, boundary=boundary)
    if rec is not None:
        return rec
    if boundary >= 0:
        return None  # шаг переоткрыт — гейт надо прогнать заново
    return _legacy_marker(gate_result_path(root, skill, feature, step_id),
                          produced_by="record_gate")


def judge(root: Path, skill: str, feature: str, name: str,
          events: Optional[list[dict]] = None) -> Optional[dict]:
    """Вердикт судьи (run_judge). None — судья не отработал."""
    ev = events if events is not None else read_events(root, skill, feature)
    boundary = _reopen_boundary(ev, judge_name=name)
    rec = _last(ev, "judge", match={"judge": name}, boundary=boundary)
    if rec is not None:
        return rec
    if boundary >= 0:
        return None  # фаза откачена — вердикт прошлой попытки не засчитывается
    return _legacy_marker(judge_path(root, skill, feature, name), produced_by="run_judge")


def override(root: Path, skill: str, feature: str, name: str,
             events: Optional[list[dict]] = None) -> Optional[dict]:
    """Ручное снятие блокировки (override_judge, R4). None — нет либо отозван.

    Отзыв (бывший `--remove`, удалявший файл) — это запись revoked:true: снятие гейта
    остаётся в истории, а не исчезает бесследно."""
    ev = events if events is not None else read_events(root, skill, feature)
    boundary = _reopen_boundary(ev, override_target=name)
    rec = _last(ev, "override", match={"target": name}, boundary=boundary)
    if rec is not None:
        return None if rec.get("revoked") else rec
    if boundary >= 0:
        return None  # откат забрал и снятие гейта: новое согласие — новый override
    return _legacy_marker(override_path(root, skill, feature, name), require_provenance=False)


def overrides(root: Path, skill: str, feature: str,
              events: Optional[list[dict]] = None) -> list[dict]:
    """Все ДЕЙСТВУЮЩИЕ overrides прогона (последняя запись на target, отозванные — вне)."""
    ev = events if events is not None else read_events(root, skill, feature)
    latest: dict[str, dict] = {}
    for rec in ev:
        if _authentic(rec, "override") and isinstance(rec.get("target"), str):
            latest[rec["target"]] = rec
    out = [r for r in latest.values() if not r.get("revoked")]
    # Прогон до миграции: overrides лежат файлами.
    legacy_dir = override_path(root, skill, feature, "x").parent
    if legacy_dir.is_dir():
        for f in sorted(legacy_dir.glob("*.json")):
            if f.stem in latest:
                continue
            d = _legacy_marker(f, require_provenance=False)
            if d:
                out.append({**d, "target": d.get("judge", f.stem)})
    return sorted(out, key=lambda r: str(r.get("target", "")))


def grounding_read(root: Path, skill: str, feature: str,
                   events: Optional[list[dict]] = None) -> bool:
    """Сверялся ли агент с grounding-excerpt. Снимает блок фазы 01-grounding в gate-guard.

    Раньше жило отдельным файлом ground/phases/<feature>/agent-evidence.jsonl рядом с
    производным кэшем фазовой машины; кэш снят, а evidence переехало сюда — к остальным
    фактам о прогоне, с тем же провенанс-контролем.
    """
    ev = events if events is not None else read_events(root, skill, feature)
    if _last(ev, "grounding") is not None:
        return True
    # Прогон до миграции: строки в ground/phases/<feature>/agent-evidence.jsonl.
    legacy = ground_dir(root) / "phases" / feature / "agent-evidence.jsonl"
    if not legacy.exists():
        legacy = ground_dir(root) / "phases" / "agent-evidence.jsonl"
    for rec in read_log(legacy):
        if rec.get("event") == "read_grounding":
            return True
    return False


def approval(root: Path, key: str) -> Optional[dict]:
    """Согласие человека по ключу (record_approval). None — нет либо отозвано откатом.

    key внутри записи обязан совпадать с запрошенным: переименованная/чужая запись
    не засчитывается (инвариант сохранён с файловой раскладки).

    Отзыв — отдельный вид записи (produced_by:"rollback"), а не удаление: одно согласие
    = один откат, и потребление согласия обязано быть видно в истории. Лог approvals
    проектный, поэтому пофичная граница reopen сюда не дотягивается.
    """
    log = read_log(approvals_path(root))
    # Позиции считаем в обходе, а не через list.index(): две выдачи одного согласия в
    # пределах секунды дают ИДЕНТИЧНЫЕ записи (ts посекундный), и index() возвращал бы
    # позицию первой — свежее согласие после отзыва выглядело бы уже потраченным.
    granted_at, revoked_at = -1, -1
    granted = None
    for i, rec in enumerate(log):
        if _authentic(rec, "approval") and rec.get("key") == key:
            granted, granted_at = rec, i
        elif _authentic(rec, "approval-revoked") and rec.get("key") == key:
            revoked_at = i
    if granted is not None:
        return None if revoked_at > granted_at else granted
    if revoked_at >= 0:
        return None  # согласие лежало файлом старой раскладки и уже потреблено откатом
    legacy = _legacy_marker(approvals_dir(root) / f"{_safe(key)}.json",
                            produced_by="record_approval")
    return legacy if (legacy is None or legacy.get("key") == key) else None


def revoke_approval(root: Path, key: str, reason: str = "") -> dict:
    """Потребить/снять согласие (rollback).

    Файл старой раскладки НЕ удаляем: запись отзыва и так перекрывает его при чтении
    (см. approval()), а откат обязан evidence архивировать, а не уничтожать."""
    return _append(approvals_path(root), "approval-revoked", PRODUCERS["approval-revoked"],
                   {"key": key, "reason": reason})


def _safe(key: str) -> str:
    from _project import safe_component
    return safe_component(key)


def _legacy_marker(path: Path, *, produced_by: Optional[str] = None,
                   require_provenance: bool = True) -> Optional[dict]:
    """Файл-маркер старой раскладки. Возвращает его содержимое (или {} для маркеров,
    у которых содержимое никогда не читалось — _origins/overrides)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return {} if not require_provenance else None
    if not isinstance(d, dict):
        return None
    if require_provenance and produced_by and d.get("produced_by") != produced_by:
        return None
    return d


def has_legacy_layout(root: Path, skill: str, feature: str) -> bool:
    """Есть ли на диске каталоги-маркеры прежней раскладки (для диагностики/доктора)."""
    base = state_dir(root, skill, feature)
    return any((base / d).is_dir() for d in ("_origins", "gates", "judges", "overrides")) \
        or approvals_dir(root).is_dir()
