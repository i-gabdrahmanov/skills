#!/usr/bin/env python3
"""record_approval.py — фиксирует approval-маркер ground/approvals/<key>.json с провенансом.

Зачем (BLOCKER-1). Approval-маркеры — это «человек сказал да» для рисковых действий:
снятие детерминированного гейта (`gate-override-<judge>`, R4), доставка (`human-approval` R4,
`change-advisory` R5), чувствительные пути (`security-review` R3). Раньше маркер создавала САМА
модель прямым Write/echo в `ground/approvals/` — то есть сама себе выписывала согласие. Теперь
прямая запись туда заблокирована `state-write-guard`, а единственный легальный путь — ЭТОТ
скрипт, который штампует провенанс `produced_by:"record_approval"` (его проверяет gate-guard).

⚠️ Скрипт НЕ доказывает согласие сам по себе — он лишь централизует и логирует его. Запускать
ТОЛЬКО после ЯВНОГО «да» пользователя (сначала `ask_user_question`, покажи, что не сходится).
Молча вызывать этот скрипт ради само-разблокировки — прямое нарушение инварианта.

Usage:
    # одиночное согласие
    record_approval.py --project <root> --key gate-override-subagent-origin \\
        --approved-by user --reason "agent() недоступен на этом рантайме, деградация согласована"
    record_approval.py --project <root> --key human-approval --approved-by user --reason "..."

    # batch: для прогона на 30+ модулях (KIDPPRB-9254 п.6) — один вызов на все approvals,
    # атомарная запись в ground/approvals.jsonl, идемпотентность по ключу.
    record_approval.py --project <root> --batch approvals.yaml

Формат batch (YAML):
    approvals:
      - project: KIDPPRB-9254       # Jira-key / feature-slug (контекст, для аудита)
        key: phase-04               # обязательное поле
        kind: approval              # категория (approval / gate-override-… / human-approval)
        evidence: "sha256:..."      # доказательство
        approver: "tech-lead"       # кто согласовал (или approved_by: ...)
      - project: KIDPPRB-9254
        key: phase-05
        kind: approval
        evidence: "sha256:..."
        approver: "release-manager"

Допускается баре-список `[...]`. JSON — то же содержимое в JSON.

Свойства batch:
  • Атомарность: все записи валидируются ДО первого write, потом одним flock-вызовом
    пишутся в ground/approvals.jsonl. Падение на любой записи → ничего не записано.
  • Идемпотентность: повторный прогон того же батча пропускает ключи, по которым УЖЕ
    есть активное согласие (не revoked). После отзыва (rollback) — запись проходит
    заново, как и для одиночного режима.
  • Валидация: пустые key/reason отвергают весь батч (всё или ничего).

Exit: 0 — маркер записан (или ничего не изменилось при полной идемпотентности);
      2 — ошибка аргументов; 1 — ошибка batch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# `_util` живёт в двух scripts-каталогах (skills/config-helper/scripts и здесь) с разным
# содержимым. `hooks/risk_ladder.py` предзагружает config-helperский `_util` в sys.modules
# при первом импорте любого хука — и без сброса `from _util import …` ниже берёт ЧУЖОЙ
# модуль (нет approval_path → ImportError при сборе pytest в определённом порядке).
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != _HERE:
    del sys.modules["_util"]

from _util import approval_path, repo_root, safe_component  # noqa: E402,F401
import forge_events as FE

PRODUCED_BY = "record_approval"

# Санитайзер имени — общий с читателем (update._approval_marker_valid): своя копия здесь
# и отсутствие санитайза там уже расходились, маркер писался не туда, где его искали.
safe_key = safe_component


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Batch-хелперы ────────────────────────────────────────────────────────────

class BatchFormatError(ValueError):
    """Структурная ошибка batch-файла (формат/парсинг). Ловится в cmd_batch и превращается
    в rc=1 + понятное сообщение. SystemExit тут НЕ используется — он минует unittest
    и ломает контракт тестов «rc=1 при ошибке формата»."""


def _load_batch_file(path: Path) -> list[dict]:
    """Прочитать YAML/JSON batch-файл и вернуть список approval-записей.

    Поддерживает:
      • {"approvals": [...]}  — обёртка (наш YAML-пример из саммари)
      • [...]                 — баре-список
    PyYAML покрывает оба варианта; если его нет — фолбэк на json для .json-файлов.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise BatchFormatError(f"batch-файл не читается: {path}: {e}")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except ImportError:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise BatchFormatError(
                f"batch-файл не парсится как JSON и PyYAML недоступен: {path}: {e}\n"
                f"  установите pyyaml или используйте .json")
    except Exception as e:  # noqa: BLE001
        raise BatchFormatError(f"batch-файл не парсится как YAML/JSON: {path}: {e}")

    if isinstance(data, dict) and isinstance(data.get("approvals"), list):
        return data["approvals"]
    if isinstance(data, list):
        return data
    raise BatchFormatError(
        f"batch-файл {path} должен содержать список approvals "
        f"(либо ключ 'approvals:' со списком, либо баре-список)")


def _validate_approval_item(item: dict, idx: int, total: int) -> tuple[str, dict]:
    """Валидация одной записи approval. Возвращает (key, payload).

    payload — поля, которые пойдут в запись журнала помимо ts/kind/produced_by/key
    (эти контролируются писателем FE._append). reason — обязателен.

    Маппинг YAML → CLI: approver ↔ approved_by (принимаем оба), project — контекстная
    Jira-key (НЕ путь к репо; путь = --project скрипта), kind — категория согласия.

    ⚠️ Коллизия имён: в журнале `kind` зарезервировано за ТИПОМ СОБЫТИЯ (approval / gate /
    override / judge). В пользовательском API оставляем `kind` (как в саммари), но внутри
    храним под именем `approval_kind` — иначе свёртка forge_events его выкинет как
    контрольное поле, и категория потеряется.
    """
    if not isinstance(item, dict):
        raise ValueError(f"запись #{idx + 1}/{total}: ожидается объект, "
                         f"получен {type(item).__name__}")

    key = item.get("key")
    reason = item.get("reason")
    approver = item.get("approver") or item.get("approved_by")
    kind = item.get("kind") or "approval"
    evidence = item.get("evidence")
    # project в batch-файле — контекст (Jira-key), не путь. Сохраняем как feature
    # для аудита: ключи бывают и фичевые (gate-override-<judge>-<feature>) и общие
    # (human-approval, security-review, change-advisory).
    feature_ctx = item.get("project") or item.get("feature") or ""

    if not key or not isinstance(key, str):
        raise ValueError(f"запись #{idx + 1}/{total}: обязательное поле key (строка)")
    norm_key = safe_key(key)
    if not norm_key:
        raise ValueError(f"запись #{idx + 1}/{total}: key {key!r} после нормализации пуст")
    if not reason or not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"запись #{idx + 1}/{total}: обязательное поле reason "
                         f"(непустая строка) для ключа {key!r}")
    if not approver or not isinstance(approver, str):
        raise ValueError(f"запись #{idx + 1}/{total}: обязательное поле approver "
                         f"(строка) для ключа {key!r}")

    payload = {
        "approved_by": approver,
        "reason": reason.strip(),
        # approval_kind — пользовательская категория (см. docstring выше).
        "approval_kind": kind,
    }
    if evidence is not None and evidence != "":
        payload["evidence"] = evidence
    if feature_ctx:
        payload["feature"] = feature_ctx
    return norm_key, payload


def _build_approval_record(key: str, payload: dict, ts: str) -> dict:
    """Собрать dict записи журнала, повторяя формат FE.append_approval.

    Копия сделана ради ОДИНАКОВОГО ts у всего батча — иначе повторный прогон читает
    записи как отдельные события, и логика «последний grant после revoke побеждает»
    в FE.approval() видит их непоследовательно (revoke между двумя grant'ами с
    разными ts). Один ts на батч делает поведение детерминированным.
    """
    rec = {k: v for k, v in payload.items() if v is not None}
    rec.update({
        "ts": ts,
        "kind": "approval",
        "produced_by": PRODUCED_BY,
        "key": key,
    })
    return rec


def _atomic_batch_write(path: Path, records: list[dict]) -> None:
    """Записать все записи одним flock-вызовом. См. override_judge._atomic_batch_write."""
    if not records:
        return
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    content = "\n".join(lines) + "\n"
    from _project import append_locked
    append_locked(path, content)


def _active_approval_keys(project: Path) -> set[str]:
    """Множество ключей, по которым УЖЕ есть активное согласие.

    Активное = grant, после которого не было revoke (см. FE.approval). Идемпотентность
    батча: повторный прогон пропускает эти ключи. После revoke ключ возвращается в
    «можно согласовать снова», что и требуется (одно согласие = одно использование)."""
    log_path = FE.approvals_path(project)
    if not log_path.exists():
        return set()
    log = FE.read_log(log_path)
    granted_at: dict[str, int] = {}
    revoked_at: dict[str, int] = {}
    for i, rec in enumerate(log):
        if not isinstance(rec, dict):
            continue
        key = rec.get("key")
        if not isinstance(key, str):
            continue
        if FE._authentic(rec, "approval"):
            granted_at[key] = i
        elif FE._authentic(rec, "approval-revoked"):
            revoked_at[key] = i
    active = set()
    for k, gi in granted_at.items():
        if revoked_at.get(k, -1) <= gi:
            active.add(k)
    return active


# ── Одиночный режим (back-compat) ────────────────────────────────────────────

def cmd_single(args) -> int:
    """Записать ОДИН approval-маркер. Старое поведение скрипта сохранено 1:1."""
    key = safe_key(args.key)
    if not key:
        print("ERROR: пустой --key после нормализации", file=sys.stderr)
        return 2
    if not (args.reason or "").strip():
        print("ERROR: --reason обязателен (аудит согласия)", file=sys.stderr)
        return 2

    project = Path(args.project or repo_root()).resolve()
    record = {
        "approved_by": args.approver or args.approved_by or "user",
        "reason": args.reason.strip(),
        # approval_kind — пользовательская категория (см. коллизию имён в _validate_approval_item).
        "approval_kind": args.kind or "approval",
    }
    if args.evidence:
        record["evidence"] = args.evidence
    if args.feature_ctx:
        record["feature"] = args.feature_ctx

    # Идемпотентность для одиночного режима — мягкая: если ключ уже активен, не дублируем.
    # Для одиночного скрипта это поведение НОВОЕ (раньше было «всегда пишем»). Сделано
    # сознательно: batch-режим требует идемпотентности, а одиночный и batch должны
    # вести себя одинаково на одних и тех же данных. Если оператор хочет пересогласовать
    # ключ — сначала rollback/revoke, потом новый батч.
    if key in _active_approval_keys(project):
        out = FE.approvals_path(project)
        print(f"[record_approval] approval '{key}' уже активен — пропущено (идемпотентность) → {out}")
        print("[record_approval] ⚠️ это согласие должно было прозвучать от пользователя ЯВНО. "
              "Если ты вызвал скрипт без реального «да» — останови работу и спроси.", file=sys.stderr)
        return 0

    FE.append_approval(project, key, **record)
    out = FE.approvals_path(project)

    print(f"[record_approval] approval '{key}' зафиксирован "
          f"(approved_by={record['approved_by']}, kind={record['approval_kind']}) → {out}")
    print("[record_approval] ⚠️ это согласие должно было прозвучать от пользователя ЯВНО. "
          "Если ты вызвал скрипт без реального «да» — останови работу и спроси.", file=sys.stderr)
    return 0


# ── Batch-режим ──────────────────────────────────────────────────────────────

def cmd_batch(args) -> int:
    """Атомарный batch-режим: один файл → N approval'ов одной транзакцией.

    Семантика идентична override_judge.cmd_batch:
      1. Парсим и валидируем ВСЕ записи до первого write.
      2. Идемпотентность: ключи с активным согласием пропускаются.
      3. Все «новые» записи пишутся одним write под flock.
    Лог approvals.jsonl — ПРОЕКТНЫЙ (не пофичный), поэтому группировка по feature не нужна.
    """
    path = Path(args.batch)
    if not path.exists():
        print(f"ERROR: --batch файл не найден: {path}", file=sys.stderr)
        return 1

    try:
        raw_items = _load_batch_file(path)
    except BatchFormatError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not raw_items:
        print(f"ERROR: batch-файл {path} пустой или не содержит approval-записей",
              file=sys.stderr)
        return 1

    project = Path(args.project or repo_root()).resolve()

    to_write: list[tuple[str, dict]] = []
    skipped: list[str] = []
    errors: list[str] = []

    for idx, item in enumerate(raw_items):
        try:
            key, payload = _validate_approval_item(item, idx, len(raw_items))
        except ValueError as e:
            errors.append(str(e))
            continue
        to_write.append((key, payload))

    if errors:
        print("ERROR: batch отвергнут целиком (атомарность):", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        return 1

    # Идемпотентность: читаем активные ключи и фильтруем дубли ПОСЛЕ валидации, ДО записи.
    active = _active_approval_keys(project)
    final = [(k, p) for (k, p) in to_write if k not in active]
    skipped.extend(k for (k, _) in to_write if k in active)

    ts = _iso_now()
    records = [_build_approval_record(k, p, ts) for (k, p) in final]

    log_path = FE.approvals_path(project)
    if records:
        try:
            _atomic_batch_write(log_path, records)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: запись в {log_path} провалилась: {e}", file=sys.stderr)
            return 1

    # Отчёт
    if args.json:
        print(json.dumps({
            "status": "ok",
            "requested": len(to_write),
            "written": len(records),
            "skipped_existing": len(skipped),
            "skipped": skipped,
            "batch_file": str(path),
            "log": str(log_path),
        }, ensure_ascii=False))
    else:
        print(f"[record_approval] batch: запрошено {len(to_write)}, "
              f"записано {len(records)}, пропущено (уже активно) {len(skipped)}")
        if skipped:
            print("[record_approval] уже активные (идемпотентность):")
            for k in skipped:
                print(f"  • {k}")
        print(f"[record_approval] источник: {path}")
        print(f"[record_approval] журнал:   {log_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Корень репо (default: git toplevel/cwd)")
    p.add_argument("--key", help="Ключ approval (напр. gate-override-<judge>, "
                                  "human-approval, security-review, change-advisory). "
                                  "Несовместим с --batch.")
    p.add_argument("--approved-by", help="[deprecated, use --approver] Кто согласовал "
                                         "(обычно user)")
    p.add_argument("--approver", help="Кто согласовал (обычно user). Алиас для --approved-by.")
    p.add_argument("--reason", help="Кто/почему — для аудита")
    p.add_argument("--kind", default=None,
                   help="Категория согласия (approval / gate-override-<…> / human-approval / "
                        "security-review / change-advisory). Дефолт: approval")
    p.add_argument("--evidence", default=None,
                   help="Ссылка на доказательство (sha256, ticket, отчёт)")
    p.add_argument("--feature-ctx", default=None,
                   help="Jira-key / feature-slug (контекст для аудита, не путь к репо)")
    p.add_argument("--batch", help="Batch-режим: путь к YAML/JSON со списком approval'ов "
                                   "(см. саммари в шапке скрипта)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    # Batch имеет приоритет; --batch несовместим с одиночными --key/--reason.
    if args.batch:
        if args.key or args.reason or args.approved_by or args.approver:
            print("ERROR: --batch несовместим с --key/--reason/--approver "
                  "(они берутся из файла)", file=sys.stderr)
            return 2
        return cmd_batch(args)

    # Одиночный режим — обратная совместимость: --key и --reason обязательны.
    if not args.key:
        p.error("--key обязателен (кроме --batch)")
    if not args.reason:
        p.error("--reason обязателен (кроме --batch)")

    return cmd_single(args)


if __name__ == "__main__":
    sys.exit(main())
