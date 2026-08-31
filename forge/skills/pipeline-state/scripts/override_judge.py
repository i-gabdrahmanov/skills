#!/usr/bin/env python3
from __future__ import annotations
"""
override_judge.py — ручное подтверждение пропуска гейта судьи.

Когда судья заблокировал шаг по причине, которую нельзя устранить автоматически
(нет тестовой БД, внешний сервис недоступен, acceptance намеренно ослаблен и т.д.),
пользователь создаёт override-файл с объяснением. update.py учитывает его и пропускает
блокировку этого судьи — но фиксирует факт отклонения в manifest.

Использование:
    python3 override_judge.py \\
        --judge <judge-name>         # напр. red-judge, coverage-judge
        --feature <slug>             # фича (slug / Jira-key)
        --step-id <id>               # напр. 04-test-T1
        --reason "<объяснение>"      # ОБЯЗАТЕЛЬНО — почему пропуск допустим
        [--evidence "<ref>"]         # ссылка на доказательство (sha256, ticket, отчёт)
        [--approver "<who>"]         # кто согласовал (default: user)
        [--project <root>]           # корень проекта (по умолчанию — cwd/git)
        [--skill feature-pipeline]   # скилл (по умолчанию feature-pipeline)
        [--list]                     # показать существующие overrides
        [--remove]                   # удалить override
        [--json]                     # JSON-вывод

Batch-режим (KIDPPRB-9254 п.6): для прогона на 30+ модулях одиночные вызовы плодили 60+
записей в журнале и CLI-вызовов. `--batch <path>` принимает YAML/JSON со списком
override'ов и пишет их атомарно за один проход:

    python3 override_judge.py --project <root> --batch overrides.yaml

Формат (YAML):
    overrides:
      - task: KIDPPRB-9254          # = feature
        gate: quality               # = judge
        decision: pass              # информационно; override всегда «пропуск»
        reason: "Quality confirmed by QA"
        evidence: "qa-report-2026-08-22.html"
        approver: "qa-lead"
      - task: KIDPPRB-9254
        gate: red
        reason: "Tests suppressed by supervisor"
        evidence: "ticket SUP-123"
        approver: "release-manager"

Допускается также баре-список `[...]` без обёртки `overrides:`. Формат .json — то же
содержимое в JSON. Скилл по умолчанию = feature-pipeline; если нужен другой — передавайте
поля `skill:` рядом с `task/gate/reason/evidence/approver` (необязательно).

Свойства batch:
  • Атомарность: сначала валидируются ВСЕ записи, потом одним write под flock пишутся
    в ground/statements/<skill>/<feature>/events.jsonl. Падение на любой записи →
    ничего не записано.
  • Идемпотентность: повторный прогон того же батча пропускает уже активные
    override'ы (по target, т.е. по `gate` для данного feature). Идемпотентность
    работает по последнему состоянию журнала (с учётом отзывов): если override отозван,
    новая запись создаётся заново.
  • Валидация: пустые task/gate/reason отвергаются целиком (все или ничего).

Exit:
    0 — override создан / показан / удалён / batch применён (включая «ничего не изменилось»
        если все записи уже активны)
    1 — ошибка (не указана причина, файл не найден, невалидный batch и т.д.)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# `_util` живёт в двух scripts-каталогах (skills/config-helper/scripts и здесь) с разным
# содержимым. `hooks/risk_ladder.py` предзагружает config-helperский `_util` в sys.modules
# при первом импорте любого хука — и без сброса `from _util import …` ниже берёт ЧУЖОЙ
# модуль (нет override_path → ImportError при сборе pytest).
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != _HERE:
    del sys.modules["_util"]

from _util import override_path, overrides_dir, repo_root, resolve_skill  # noqa: E402,F401
import forge_events as FE


SCHEMA = "pipeline/judge-override@1"

# ВАЖНО: значение жёстко прибито к PRODUCERS["override"] в hooks/forge_events.py.
# Подделать запись, проставив сюда чужое produced_by, нельзя — свёртка игнорирует такие
# строки, и override не снимет гейт.
PRODUCED_BY_OVERRIDE = "override_judge"


class BatchFormatError(ValueError):
    """Структурная ошибка batch-файла (формат/парсинг). Ловится в cmd_batch и превращается
    в rc=1 + понятное сообщение. SystemExit тут НЕ используется — он минует unittest
    и ломает контракт тестов «rc=1 при ошибке формата»."""


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_batch_file(path: Path) -> list[dict]:
    """Прочитать YAML/JSON batch-файл и вернуть список override'ов.

    Поддерживает два формата:
      • {"overrides": [...]}  — обёртка (наш YAML-пример из саммари)
      • [...]                 — баре-список

    JSON-синтаксис YAML 1.2 — подавсегда (PyYAML), так что для .json достаточно того же
    парсера: json — частный случай YAML."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise BatchFormatError(f"batch-файл не читается: {path}: {e}")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except ImportError:
        # PyYAML недоступен — падаем на json-парсер: JSON — частный случай YAML 1.2,
        # и без библиотеки мы хотя бы покроем .json-формат.
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise BatchFormatError(
                f"batch-файл не парсится как JSON и PyYAML недоступен: {path}: {e}\n"
                f"  установите pyyaml или используйте .json")
    except Exception as e:  # noqa: BLE001 — yaml.YAMLError и пр.
        raise BatchFormatError(f"batch-файл не парсится как YAML/JSON: {path}: {e}")

    if isinstance(data, dict) and isinstance(data.get("overrides"), list):
        return data["overrides"]
    if isinstance(data, list):
        return data
    raise BatchFormatError(
        f"batch-файл {path} должен содержать список overrides "
        f"(либо ключ 'overrides:' со списком, либо баре-список)")


def _validate_override_item(item: dict, idx: int, total: int) -> tuple[str, str, dict]:
    """Валидация одной записи override. Возвращает (feature, judge, payload).

    payload — всё, что пойдёт в запись журнала помимо ts/kind/produced_by/feature_slug/
    target/judge (эти поля контролируются писателем). reason — обязателен.

    Бросает ValueError с понятным сообщением на первой же проблеме: на прогонах в 60+
    записей ловить индекс конкретной сломанной строки руками — лишняя работа."""
    if not isinstance(item, dict):
        raise ValueError(f"запись #{idx + 1}/{total}: ожидается объект, получен {type(item).__name__}")

    # Маппинг YAML → CLI: task → feature, gate → judge, decision — информационно.
    # Принимаем оба варианта написания (task/feature, gate/judge), чтобы YAML из саммари
    # и существующий одиночный CLI говорили на одном языке.
    feature = item.get("task") or item.get("feature")
    judge = item.get("gate") or item.get("judge")
    reason = item.get("reason")
    evidence = item.get("evidence")
    approver = item.get("approver") or item.get("approved_by") or "user"
    decision = item.get("decision")  # информационно: pass / fail / override — что имел в виду оператор
    step_id = item.get("step_id") or item.get("step") or "unknown"

    if not feature or not isinstance(feature, str):
        raise ValueError(f"запись #{idx + 1}/{total}: обязательное поле task/feature (строка)")
    if not judge or not isinstance(judge, str):
        raise ValueError(f"запись #{idx + 1}/{total}: обязательное поле gate/judge (строка)")
    if not reason or not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"запись #{idx + 1}/{total}: обязательное поле reason (непустая строка)")

    payload = {
        "judge": judge,
        "step_id": step_id,
        "reason": reason.strip(),
        "approved_by": approver,
        "$schema": SCHEMA,
    }
    if evidence is not None and evidence != "":
        payload["evidence"] = evidence
    if decision is not None and decision != "":
        # Сохраняем, что оператор явно обозначил решение (override ≠ pass by default).
        payload["decision"] = decision
    return feature, judge, payload


def _build_override_record(project: Path, skill: str, feature: str,
                           judge: str, payload: dict, ts: str) -> dict:
    """Собрать dict записи журнала с теми же полями, что пишет FE.append_event(kind="override").

    Копия формата сделана сознательно: _append дёргает _iso_now() внутри, а нам нужны
    ОДИНАКОВЫЕ ts у всех записей батча (иначе повторный прогон читает их как отдельные
    события и не схлопывает в один override — скорее мержит по target через _last).
    Чтобы атомарность и идемпотентность работали предсказуемо, батч пишет под своим ts.
    """
    from _project import safe_component
    rec = {k: v for k, v in payload.items() if v is not None}
    rec.update({
        "ts": ts,
        "kind": "override",
        "produced_by": PRODUCED_BY_OVERRIDE,
        "target": judge,
        "feature_slug": feature,
        # file_slug нужен для legacy-фолбэка FE.overrides() — там проверка по basename.
        # Для batch-записей он не пишется на диск (events.jsonl один на прогон), но в
        # payload передаётся на случай, если FE.overrides начнёт его использовать.
        "_file_slug": safe_component(judge),
    })
    return rec


def _atomic_batch_write(path: Path, records: list[dict]) -> None:
    """Записать все записи одним вызовом append_locked (одна fsync, один flock).

    Подготовка payload (валидация + JSON) ДОЛЖНА быть завершена до вызова: любое
    исключение тут = ни одна запись не попала на диск. Это и есть «атомарность
    батча»: либо весь батч, либо ничего."""
    if not records:
        return
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    # Гарантируем, что каждая строка терминирована \n: append_locked пишет as-is.
    content = "\n".join(lines) + "\n"
    from _project import append_locked
    append_locked(path, content)


def _active_override_targets(project: Path, skill: str, feature: str) -> set[str]:
    """Множество target'ов, по которым уже есть АКТИВНЫЙ (не revoked) override.

    Используется для идемпотентности: повторный прогон батча пропускает эти gate'ы,
    чтобы не дублировать записи. Если override был ОТОЗВАН (FE.override() == None),
    target не считается активным — новая запись пройдёт, что и требуется для
    пересогласования после отзыва."""
    return {o["target"] for o in FE.overrides(project, skill, feature)
            if isinstance(o.get("target"), str)}


def cmd_batch(args, project: Path) -> int:
    """Атомарный batch-режим: один файл → N override'ов одной транзакцией.

    Семантика:
      1. Парсим и валидируем ВСЕ записи до первого write — иначе половина батча уже
         на диске, а вторая провалилась.
      2. Идемпотентность: записи с target'ом, по которому УЖЕ есть активный override,
         пропускаются (новой записи не пишем). Это даёт безопасный повторный прогон.
      3. Все «новые» записи пишутся одним write под flock (см. _atomic_batch_write).
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
        print(f"ERROR: batch-файл {path} пустой или не содержит override'ов", file=sys.stderr)
        return 1

    # Группируем записи по (skill, feature) — events.jsonl один на фичу, и писать надо
    # в правильные файлы. По умолчанию всё идёт в skill=feature-pipeline, но запись
    # может переопределить skill явно.
    by_path: dict[tuple[str, str], list[dict]] = {}
    skipped: list[dict] = []  # уже активные — отдельно для отчёта
    errors: list[str] = []

    for idx, item in enumerate(raw_items):
        try:
            feature, judge, payload = _validate_override_item(item, idx, len(raw_items))
        except ValueError as e:
            errors.append(str(e))
            continue
        skill = (item.get("skill") if isinstance(item.get("skill"), str) else args.skill) \
            or "feature-pipeline"
        by_path.setdefault((skill, feature), []).append({
            "feature": feature, "judge": judge, "payload": payload,
        })

    if errors:
        # Целиком батч невалиден → ни одной записи не пишем (атомарность).
        print("ERROR: batch отвергнут целиком (атомарность):", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        return 1

    # Идемпотентность — после валидации, до записи: читаем активные target'ы для каждого
    # (skill, feature) и фильтруем дубли.
    total_requested = sum(len(v) for v in by_path.values())
    total_written = 0
    ts = iso_now()

    for (skill, feature), items in by_path.items():
        active = _active_override_targets(project, skill, feature)
        to_write = [it for it in items if it["judge"] not in active]
        skipped.extend({"skill": skill, "feature": feature, "judge": it["judge"]}
                       for it in items if it["judge"] in active)

        if not to_write:
            continue

        records = [
            _build_override_record(project, skill, feature,
                                   it["judge"], it["payload"], ts)
            for it in to_write
        ]
        events_path = FE.events_path(project, skill, feature)
        try:
            _atomic_batch_write(events_path, records)
        except Exception as e:  # noqa: BLE001
            # append_locked под flock не должен падать, но если упал — отдаём ошибку
            # с понятным контекстом. Записи до этого уже могли быть на диске (для
            # ДРУГИХ skill/feature); здесь мы НЕ откатываем их — append_locked пишет
            # в разные файлы, и при ошибке хотя бы один файл уже мог быть обновлён.
            # Это осознанно: cross-file rollback в одиночном скрипте без транзакционного
            # слоя опаснее, чем partial-write с понятным отчётом.
            print(f"ERROR: запись в {events_path} провалилась: {e}", file=sys.stderr)
            return 1
        total_written += len(records)

    # Отчёт — и в stdout, и в JSON-режиме без изменений (тот же набор полей, что и
    # для одиночного cmd_create: status/count/path).
    if args.json:
        print(json.dumps({
            "status": "ok",
            "requested": total_requested,
            "written": total_written,
            "skipped_existing": len(skipped),
            "by_path": {f"{s}/{f}": len(items) for (s, f), items in by_path.items()},
            "skipped": skipped,
            "batch_file": str(path),
        }, ensure_ascii=False))
    else:
        print(f"[override_judge] batch: запрошено {total_requested}, "
              f"записано {total_written}, пропущено (уже активно) {len(skipped)}")
        if skipped:
            print("[override_judge] уже активные (идемпотентность):")
            for s in skipped:
                print(f"  • {s['skill']}/{s['feature']}: {s['judge']}")
        print(f"[override_judge] источник: {path}")
    return 0


def cmd_create(args, project: Path, judges: list[str] | None = None) -> int:
    if not args.reason:
        print("ERROR: --reason обязателен при создании override", file=sys.stderr)
        return 1
    # Поддержка batch: --judge допускает CSV ('a,b,c') — одним вызовом снимет несколько гейтов
    # (п.6 KIDPPRB-9254: 6 override'ов на gate-result раньше требовали 6 отдельных вызовов).
    targets = judges or [args.judge]
    # getattr для back-compat: старые тесты (и сторонние скрипты) могут не передавать
    # новые поля. Дефолты совпадают со значениями argparse.
    approver = getattr(args, "approver", None) or "user"
    evidence = getattr(args, "evidence", None)  # может быть None — тогда поле не пишется
    created = 0
    for judge in targets:
        record = {
            "$schema": SCHEMA,
            "judge": judge,
            "feature_slug": args.feature,
            "step_id": args.step_id or "unknown",
            "override_at": iso_now(),
            "reason": args.reason,
            "approved_by": approver,
        }
        if evidence:
            record["evidence"] = evidence
        # target — ключ, по которому override ищет update._load_override (имя судьи либо
        # синтетическое gate-result-<step>/doc-approved-<step>/step-skip-<step>).
        FE.append_event(project, args.skill, args.feature, "override",
                        target=judge, **record)
        created += 1

    path = FE.events_path(project, args.skill, args.feature)
    if args.json:
        print(json.dumps({"status": "created", "count": created, "judges": targets,
                          "path": str(path), "evidence": evidence, "approver": approver},
                         ensure_ascii=False))
    else:
        for judge in targets:
            print(f"✅ Override создан: {judge}")
        print(f"   Шаг:    {args.step_id or 'unknown'}")
        if evidence:
            print(f"   Evidence: {evidence}")
        print(f"   Approver: {approver}")
        print(f"   Причина: {args.reason}")
        print()
        print("Теперь можно закрыть шаг:")
        print(f"  python3 update.py --skill {args.skill} --feature {args.feature} "
              f"--step-id {args.step_id or 'unknown'} --status completed")
    return 0


def cmd_list(args, project: Path) -> int:
    records = FE.overrides(project, args.skill, args.feature)

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        if not records:
            print(f"Нет активных overrides для {args.feature}")
        else:
            print(f"Активные overrides ({args.feature}):")
            for r in records:
                name = r.get("judge") or r.get("target", "?")
                print(f"  • {name:25s} шаг={r.get('step_id','?'):20s} "
                      f"дата={r.get('override_at','?')[:10]}")
                print(f"    причина: {r.get('reason','')[:100]}")
    return 0


def cmd_remove(args, project: Path) -> int:
    """Отзыв override. Раньше удалял файл; теперь дописывает запись revoked:true —
    снятие гейта и его отмена остаются в истории прогона, а не исчезают с диска."""
    if FE.override(project, args.skill, args.feature, args.judge) is None:
        print(f"Override не найден (или уже отозван): {args.judge}", file=sys.stderr)
        return 1
    # Файл старой раскладки не трогаем: запись отзыва перекрывает его при чтении
    # (FE.override), а история снятия и его отмены остаётся полной.
    FE.append_event(project, args.skill, args.feature, "override",
                    target=args.judge, judge=args.judge, revoked=True,
                    revoked_at=iso_now())
    if args.json:
        print(json.dumps({"status": "removed", "judge": args.judge}))
    else:
        print(f"🗑  Override отозван: {args.judge}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge", help="Имя судьи (напр. red-judge, coverage-judge)")
    ap.add_argument("--feature", help="Slug фичи / Jira-key")
    ap.add_argument("--step-id", help="ID шага (для справки, не влияет на механику)")
    ap.add_argument("--reason", help="Почему пропуск допустим (обязательно при создании)")
    ap.add_argument("--evidence", help="Ссылка на доказательство (sha256, ticket, отчёт)")
    ap.add_argument("--approver", default=None,
                    help="Кто согласовал override (default: user)")
    ap.add_argument("--project", default=None)
    ap.add_argument("--skill", default=None,
                    help="Namespace ground/statements/<skill>/. По умолчанию — namespace "
                         "активного прогона (_util.resolve_skill): прежний хардкод "
                         "feature-pipeline уводил override forgefix/forgelite в чужой каталог, "
                         "и снятый гейт продолжал блокировать.")
    ap.add_argument("--list", action="store_true", help="Показать существующие overrides")
    ap.add_argument("--remove", action="store_true", help="Удалить override")
    ap.add_argument("--batch", help="Batch-режим: путь к YAML/JSON со списком override'ов "
                                    "(см. саммари в шапке скрипта)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    project = Path(args.project or repo_root()).resolve()
    # Namespace прогона: без резолва хардкод feature-pipeline уводил override в чужой
    # каталог на forgefix/forgelite — снятый гейт продолжал блокировать (см. _util).
    args.skill = resolve_skill(project, args.skill)

    # Batch имеет приоритет над одиночными командами: --batch + --reason взаимоисключающи
    # (batch читает reason из файла), но если оба указаны — побеждает batch (явное действие).
    if args.batch:
        if args.list or args.remove:
            print("ERROR: --batch несовместим с --list/--remove", file=sys.stderr)
            return 1
        if args.judge or args.reason:
            print("ERROR: --batch несовместим с --judge/--reason "
                  "(reason берётся из файла)", file=sys.stderr)
            return 1
        return cmd_batch(args, project)

    if args.list:
        return cmd_list(args, project)

    if not args.judge:
        ap.error("--judge обязателен (кроме --list и --batch)")
    if not args.feature:
        ap.error("--feature обязателен (кроме --list и --batch)")

    # Поддержка batch: --judge 'a,b,c' — несколько гейтов одним вызовом.
    judges = [j.strip() for j in args.judge.split(",") if j.strip()] or [args.judge]

    if args.remove:
        # отозвать можно тоже все перечисленные (одиночное поведение сохранено для 1)
        rc = 0
        for judge in judges:
            args.judge = judge
            rc = rc or cmd_remove(args, project)
        return rc

    return cmd_create(args, project, judges)


if __name__ == "__main__":
    sys.exit(main())
