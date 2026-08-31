#!/usr/bin/env python3
"""record_gate.py — запускает детерминированный гейт шага и пишет evidence-артефакт.

Зачем: update.py при закрытии build/verify-шагов (04-test/04-build/05-tests, lite-red/
lite-green/lite-verify) требует gates/<step_id>.json с провенансом produced_by:"record_gate".
Артефакт пишет ЭТОТ скрипт по фактическому exit-коду команды гейта — слово субагента
(«status: completed») доказательством не является.

Usage:
    # обычный гейт (сборка/тесты/coverage должны пройти): passed = exit 0
    record_gate.py --project <root> --skill <skill> --feature <slug> --step-id lite-green \
        --cmd "./gradlew build"

    # RED-гейт TDD: компиляция проходит, ВСЕ тесты прогона падают (по-тестово, JUnit XML)
    record_gate.py --project <root> --skill <skill> --feature <slug> --step-id lite-red \
        --expect red --compile-cmd "./gradlew compileTestJava" \
        --cmd "./gradlew test --tests 'FooTest'"

RED-гейт ПО-ТЕСТОВЫЙ: exit-кода раннера недостаточно (один красный тест валит весь прогон,
и N зелёных вакуумных тестов проходили как «RED»). Разбираются JUnit XML-отчёты текущего
прогона (junit_report): нужны ≥1 выполненный тест и НОЛЬ зелёных — поэтому команду тестов
скоупь на новые тест-классы (--tests / -Dtest).

Инварианты (KIDPPRB-9254 п.3): для задач с тестами-инвариантами (проверяют поведение,
которое НЕ меняется) RED-гейт НЕ должен валиться из-за их зелёного состояния.
Включается флагом `--allow-invariants` или централизованно через
`quality.red_gate.allow_invariants: true` в ground/pipeline.json. Прочие зелёные
(вакуумные) по-прежнему валят RED.

Exit: 0 — гейт пройден (артефакт passed:true); 1 — не пройден (артефакт passed:false);
      2 — отказ записать evidence: в `--cmd` нет гейта этой фазы (risk-policy.gate_cmd_expect)
          либо не хватает `--compile-cmd` для `--expect red`. Артефакт НЕ пишется.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# `_util` живёт в двух scripts-каталогах (skills/config-helper/scripts и здесь) с разным
# содержимым. `hooks/risk_ladder.py` предзагружает config-helperский `_util` в sys.modules
# при первом импорте любого хука — и без сброса `from _util import …` ниже берёт ЧУЖОЙ
# модуль (нет gate_result_path и др. → ImportError при сборе pytest).
_HERE = Path(__file__).resolve().parent
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))
_cached_util = sys.modules.get("_util")
if _cached_util is not None and getattr(_cached_util, "__file__", None) and \
        Path(_cached_util.__file__).resolve().parent != _HERE:
    del sys.modules["_util"]

import junit_report  # noqa: E402
from _util import (  # noqa: E402,F401
    gate_result_path,
    gates_dir,
    load_project_config,
    repo_root,
    safe_component,
)
import forge_events as FE

PRODUCED_BY = "record_gate"

# Общий санитайзер с читателем (update._check_gate_result) — см. _project.safe_component.
safe_step = safe_component


def _expected_tokens(step_id: str) -> list:
    """Токены, один из которых ОБЯЗАН быть в команде гейта этого шага
    (risk-policy.gate_cmd_expect). Пусто — проверка не применяется."""
    try:
        import risk_ladder as R
        policy = (R.load_policy() or {}).get("gate_cmd_expect") or {}
    except Exception:  # noqa: BLE001 — policy недоступна: не мешаем прогону
        return []
    best: list = []
    best_len = -1
    for prefix, tokens in policy.items():
        if prefix.startswith("_") or not isinstance(tokens, list):
            continue
        if step_id.startswith(prefix) and len(prefix) > best_len:
            best, best_len = tokens, len(prefix)
    return best


def _check_cmd_substance(step_id: str, cmd: str, compile_cmd: str = "") -> "str | None":
    """Причина отказа, если в команде нет гейта этого шага; иначе None.

    Провенанс (кто записал evidence) цепочка проверяла, а субстанцию — нет: `--cmd "true"`
    давал валидный `passed:true` и закрывал шаг. Это фиксировало «гейт прошёл» без гейта."""
    tokens = _expected_tokens(step_id)
    if not tokens:
        return None
    haystack = f"{cmd} {compile_cmd}"
    if any(str(t) in haystack for t in tokens):
        return None
    return (f"команда гейта шага '{step_id}' не содержит сам гейт "
            f"(ожидается одно из: {', '.join(map(str, tokens))}).\n"
            f"  Получено: --cmd {cmd!r}\n"
            f"  record_gate фиксирует ФАКТ прохождения гейта фазы, а не любой успешной команды: "
            f"evidence с посторонней командой — это закрытие шага без гейта. Возьми команду из "
            f"брифа фазы. Гейт объективно неприменим — это решение пользователя (R4): "
            f"override_judge --judge gate-result-{step_id} после approval-маркера.")


def _red_gate_settings(project: Path, cli_allow: bool | None, cli_pattern: str | None
                       ) -> tuple[bool, str]:
    """Резолв allow_invariants / invariant_pattern: CLI > policy.json|pipeline.json > дефолт.

    quality.red_gate.{allow_invariants, invariant_pattern} в ground/policy.json (legacy:
    ground/pipeline.json) — централизованная настройка для фичи. CLI-флаги имеют приоритет
    (разовый override). Дефолт: allow=False, pattern='INVARIANT' (см.
    junit_report.DEFAULT_INVARIANT_PATTERN). Phase 0 v2 refactor: двойной рид через
    load_project_config (policy.json → pipeline.json fallback)."""
    allow = cli_allow if cli_allow is not None else False
    pattern = cli_pattern if cli_pattern else junit_report.DEFAULT_INVARIANT_PATTERN
    try:
        cfg = load_project_config(project) or {}
    except Exception:  # noqa: BLE001 — policy недоступна, не мешаем прогону
        return allow, pattern
    rg = (cfg.get("quality") or {}).get("red_gate") or {}
    if isinstance(rg, dict):
        if cli_allow is None and isinstance(rg.get("allow_invariants"), bool):
            allow = rg["allow_invariants"]
        if not cli_pattern and isinstance(rg.get("invariant_pattern"), str) and rg["invariant_pattern"].strip():
            pattern = rg["invariant_pattern"].strip()
    return allow, pattern


def _run(cmd: str, cwd: Path, timeout: int) -> tuple[int, str]:
    """Запуск команды гейта. Хвост вывода идёт в артефакт для диагностики."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout)
        tail = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()[-2000:]
        return r.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT ({timeout}s): {cmd}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Корень репо (default: git toplevel/cwd)")
    p.add_argument("--skill", required=True)
    p.add_argument("--feature", required=True)
    p.add_argument("--step-id", required=True)
    p.add_argument("--cmd", required=True, help="Команда гейта (для --expect red — команда тестов)")
    p.add_argument("--expect", choices=["success", "red"], default="success",
                   help="success: passed при exit 0; red: компиляция OK + тесты падают")
    p.add_argument("--compile-cmd", help="Команда компиляции для --expect red")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--module-roots", help="скоуп JUnit-сканирования на конкретные модули "
                                          "(запятая-раздельно; моно-репо, п.4)")
    p.add_argument("--tolerate-green", action="store_true",
                   help="не валить RED из-за зелёных при наличии ≥1 красного (инварианты, п.3; "
                        "ГРУБЫЙ режим — пропускает ЛЮБЫЕ зелёные; для точечного см. "
                        "--allow-invariants)")
    p.add_argument("--allow-invariants", dest="allow_invariants", action="store_true",
                   default=None,
                   help="при --expect red: пропустить зелёные тесты с маркером 'INVARIANT' "
                        "в имени (точечный режим, KIDPPRB-9254 п.3). Дефолт берётся из "
                        "quality.red_gate.allow_invariants в ground/pipeline.json, "
                        "при отсутствии — False")
    p.add_argument("--strict-invariants", dest="allow_invariants", action="store_false",
                   help="явно ЗАПРЕТИТЬ allow_invariants (override pipeline.json)")
    p.add_argument("--invariant-pattern", default=None,
                   help="паттерн имени для инвариантов (case-insensitive regex). "
                        "Дефолт: 'INVARIANT' либо quality.red_gate.invariant_pattern "
                        "из ground/pipeline.json")
    args = p.parse_args()

    project = Path(args.project or repo_root()).resolve()
    module_roots: list[Path] | None = None
    if args.module_roots:
        module_roots = [project / part for part in args.module_roots.split(",") if part.strip()]

    # allow_invariants: CLI (--allow-invariants / --strict-invariants) > pipeline.json > False.
    # pattern: --invariant-pattern > quality.red_gate.invariant_pattern > 'INVARIANT'.
    allow_inv, inv_pattern = _red_gate_settings(
        project, args.allow_invariants, args.invariant_pattern)

    # Гейт субстанции — ДО запуска: evidence не пишется вовсе, если в команде нет гейта фазы.
    why = _check_cmd_substance(args.step_id, args.cmd, args.compile_cmd or "")
    if why:
        print(f"[record_gate] ОТКАЗ: {why}", file=sys.stderr)
        return 2

    record: dict = {
        "produced_by": PRODUCED_BY,
        "step_id": args.step_id,
        "expect": args.expect,
        "cmd": args.cmd,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if args.expect == "red":
        if not args.compile_cmd:
            print("ERROR: --expect red требует --compile-cmd", file=sys.stderr)
            return 2
        record["compile_cmd"] = args.compile_cmd
        record["allow_invariants"] = allow_inv
        record["invariant_pattern"] = inv_pattern
        compile_rc, compile_tail = _run(args.compile_cmd, project, args.timeout)
        record["compile_exit_code"] = compile_rc
        if compile_rc != 0:
            record["passed"] = False
            record["reason"] = "компиляция тестов упала — это не RED, чини сигнатуры/импорты"
            record["output_tail"] = compile_tail
        else:
            started = time.time()
            test_rc, test_tail = _run(args.cmd, project, args.timeout)
            record["exit_code"] = test_rc
            record["output_tail"] = test_tail
            # ПО-ТЕСТОВЫЙ вердикт по JUnit XML текущего прогона (-2s — гранулярность mtime):
            # exit-код недостаточен — 1 red + N green проходил как «RED».
            t = junit_report.summarize(project, since=started - 2, roots=module_roots)
            record["tests_total"] = len(t["red"]) + len(t["green"])
            record["tests_red"] = len(t["red"])
            record["tests_green"] = len(t["green"])
            if t["green"]:
                record["green_tests"] = t["green"][:10]
            if test_rc == 0:
                record["passed"] = False
                record["reason"] = "тесты прошли — это GREEN, RED-гейт не выполнен"
            else:
                reason = junit_report.red_reason(
                    t, hint_scope="Gradle: --tests 'FooTest'; Maven: -Dtest=FooTest",
                    allow_invariants=allow_inv,
                    invariant_pattern=inv_pattern,
                    tolerate_green=args.tolerate_green)
                record["passed"] = reason is None
                if reason:
                    record["reason"] = reason
    else:
        rc, tail = _run(args.cmd, project, args.timeout)
        record["exit_code"] = rc
        record["passed"] = rc == 0
        record["output_tail"] = tail

    # Evidence — строкой в журнал прогона (раньше: файл gates/<step_id>.json на каждый шаг).
    # produced_by проставляет сам журнал по kind, подделать записью мимо скрипта нельзя.
    record.pop("produced_by", None)
    FE.append_event(project, args.skill, args.feature, "gate", **record)
    out = FE.events_path(project, args.skill, args.feature)

    verdict = "PASSED" if record["passed"] else "FAILED"
    print(f"[record_gate] {args.step_id}: {verdict} → {out}")
    if not record["passed"]:
        print(f"[record_gate] причина: {record.get('reason', 'exit code != 0')}", file=sys.stderr)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
