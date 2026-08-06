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

Exit: 0 — гейт пройден (артефакт passed:true); 1 — не пройден (артефакт passed:false).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import junit_report
from _util import repo_root

PRODUCED_BY = "record_gate"


def safe_step(step_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(step_id)).strip("-") or "x"


def gates_dir(project: Path, skill: str, feature: str) -> Path:
    return project / "ground" / "statements" / skill / feature / "gates"


def gate_result_path(project: Path, skill: str, feature: str, step_id: str) -> Path:
    return gates_dir(project, skill, feature) / f"{safe_step(step_id)}.json"


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
    args = p.parse_args()

    project = Path(args.project or repo_root()).resolve()

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
            t = junit_report.summarize(project, since=started - 2)
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
                    t, hint_scope="Gradle: --tests 'FooTest'; Maven: -Dtest=FooTest")
                record["passed"] = reason is None
                if reason:
                    record["reason"] = reason
    else:
        rc, tail = _run(args.cmd, project, args.timeout)
        record["exit_code"] = rc
        record["passed"] = rc == 0
        record["output_tail"] = tail

    out = gate_result_path(project, args.skill, args.feature, args.step_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)

    verdict = "PASSED" if record["passed"] else "FAILED"
    print(f"[record_gate] {args.step_id}: {verdict} → {out}")
    if not record["passed"]:
        print(f"[record_gate] причина: {record.get('reason', 'exit code != 0')}", file=sys.stderr)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
