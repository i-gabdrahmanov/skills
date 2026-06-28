#!/usr/bin/env python3
"""module_tests.py — baseline зелёного + детекция регрессий в тестах ЗАТРОНУТЫХ модулей.

Зачем (прогон #3): агент сломал существующие Spring-тесты и не признал этого. Нужен
детерминированный механизм «отметить, что зелёное ДО разработки, и не считать успехом, пока
затронутые тесты не зелёные». Подход baseline-diff:

    snapshot (ДО кода)  → запоминаем, какие тесты затронутых модулей проходят
    compare  (ПОСЛЕ кода) → прогоняем снова; РЕГРЕССИЯ = тест был passed, стал failed → блок.
                            Пре-существующие/infra-падения (падают и в baseline) НЕ блокируют.

Модуль определяется из task-plan (`tasks[].modules`) для baseline или из git-diff (изменённые
src-пути) для compare. Тесты гоняются полным сьютом модуля (`./gradlew :mod:test` / `mvn -pl`),
результат парсится из JUnit XML (`<module>/build/test-results/.../TEST-*.xml`).

Usage:
    module_tests.py snapshot --root . --from-taskplan <task-plan.json> --out <baseline.json> [--json]
    module_tests.py compare  --root . --baseline <baseline.json> [--from-diff <base>] [--json]
    module_tests.py snapshot --root . --modules service-taskservice,utils-web --out <baseline.json>

Exit:
    0 — OK (snapshot записан / при compare регрессий нет)
    2 — РЕГРЕССИЯ (compare: ранее зелёный тест теперь падает) либо не удалось прогнать тесты
    1 — ошибка аргументов/ввода
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "feature-pipeline/test-baseline@1"


# ── Определение модулей ──────────────────────────────────────────────────────

def gradle_module_path(module: str) -> str:
    """'service-taskservice' → ':service:taskservice' (как в run_judge.check_red).

    Уже нормализованный (':service:taskservice') и 'service:taskservice' — поддержаны.
    Одно-сегментный 'taskservice' → ':taskservice'.
    """
    gp = module
    if gp.startswith(":"):
        return gp
    if ":" in gp:
        return f":{gp}"
    parts = gp.rsplit("-", 1)
    if len(parts) == 2:
        return f":{parts[0]}:{parts[1]}"
    return f":{gp}"


def module_from_path(path: str) -> "str | None":
    """Путь исходника/теста → канонический модуль 'service-taskservice'.

    'service/taskservice/src/main/java/...' → 'service-taskservice'. 'src' в корне → None
    (одномодульный проект, корневой gradle). Совпадает с run_judge._module_from_test_path.
    """
    parts = path.replace("\\", "/").split("/")
    if "src" in parts:
        i = parts.index("src")
        if i >= 2:
            return f"{parts[i - 2]}-{parts[i - 1]}".lower()
        if i >= 1:
            return parts[i - 1].lower()
    return None


def modules_from_taskplan(taskplan_path: Path) -> list[str]:
    """Union модулей из task-plan: tasks[].modules (массив) / tasks[].module (строка)."""
    try:
        tp = json.loads(taskplan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    mods: set[str] = set()
    for t in tp.get("tasks", []):
        if isinstance(t.get("modules"), list):
            mods.update(m for m in t["modules"] if m)
        elif isinstance(t.get("module"), str) and t["module"]:
            mods.add(t["module"])
    return sorted(mods)


def modules_from_diff(root: Path, base: str) -> list[str]:
    """Модули изменённых *.java (git diff vs base + staged + untracked)."""
    files: set[str] = set()
    for args in (["diff", "--name-only", base], ["diff", "--name-only", "--cached"],
                 ["ls-files", "--others", "--exclude-standard"]):
        try:
            r = subprocess.run(["git", "-C", str(root), *args],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                files.update(r.stdout.splitlines())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    mods: set[str] = set()
    for f in files:
        if f.endswith(".java"):
            m = module_from_path(f)
            if m:
                mods.add(m)
    return sorted(mods)


# ── Прогон тестов и парсинг результатов ──────────────────────────────────────

def parse_junit_dir(results_dir: Path) -> dict:
    """Парсит JUnit XML каталога в {classname#method: passed|failed|skipped}."""
    out: dict[str, str] = {}
    if not results_dir.is_dir():
        return out
    for xml in results_dir.glob("TEST-*.xml"):
        try:
            root = ET.parse(xml).getroot()
        except (ET.ParseError, OSError):
            continue
        # корень может быть <testsuite> или <testsuites>
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        for suite in suites:
            for tc in suite.findall("testcase"):
                cls = tc.get("classname") or tc.get("class") or suite.get("name") or "?"
                name = tc.get("name", "?")
                tid = f"{cls}#{name}"
                if tc.find("failure") is not None or tc.find("error") is not None:
                    out[tid] = "failed"
                elif tc.find("skipped") is not None:
                    out[tid] = "skipped"
                else:
                    out[tid] = "passed"
    return out


def _module_test_cmd(module: str, build_system: str) -> list[str]:
    if build_system == "maven":
        # -pl <module> с разделителями '-'→'/' эвристически; для maven обычно один модуль = путь
        return ["mvn", "-q", "-pl", module.replace("-", "/"), "test",
                "-Dmaven.test.failure.ignore=true"]
    gp = gradle_module_path(module)
    return ["./gradlew", f"{gp}:test", "--no-daemon", "--continue"]


def _results_dirs(root: Path, module: str, build_system: str) -> list[Path]:
    """Где искать JUnit XML для модуля (gradle vs maven, разные раскладки путей)."""
    seg = module.replace("-", "/")          # service-taskservice → service/taskservice
    seg2 = module.replace("-", "/", 1)      # на случай group-artifact с одним дефисом
    cands = []
    for s in {seg, seg2, module}:
        cands.append(root / s / "build" / "test-results" / "test")   # gradle
        cands.append(root / s / "target" / "surefire-reports")       # maven
    return cands


def run_module_suite(root: Path, module: str, build_system: str, timeout: int) -> tuple[dict, bool]:
    """Гоняет тесты модуля и парсит результат. Возвращает ({test_id: status}, ran_ok).

    ran_ok=False, если не нашли НИ одного XML (тесты не отработали — нельзя судить).
    Код возврата gradle/maven игнорируем намеренно (падение тестов → ненулевой, но XML есть).
    """
    cmd = _module_test_cmd(module, build_system)
    try:
        subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass  # результат всё равно читаем из XML — частичный прогон лучше пустого
    results: dict[str, str] = {}
    found = False
    for d in _results_dirs(root, module, build_system):
        r = parse_junit_dir(d)
        if r:
            found = True
            results.update(r)
    return results, found


def run_suite(root: Path, modules: list[str], build_system: str, timeout: int) -> tuple[dict, list]:
    """Прогон по всем модулям. Возвращает ({test_id: status}, [модули без результатов])."""
    all_results: dict[str, str] = {}
    no_results = []
    for m in modules:
        res, ok = run_module_suite(root, m, build_system, timeout)
        all_results.update(res)
        if not ok:
            no_results.append(m)
    return all_results, no_results


# ── Diff baseline ────────────────────────────────────────────────────────────

def diff_baseline(baseline: dict, current: dict) -> dict:
    """Регрессии (passed→failed), починки (failed→passed), pre-existing, новые тесты."""
    regressions, fixed, pre_existing, new = [], [], [], []
    for tid, now in sorted(current.items()):
        was = baseline.get(tid)
        if was == "passed" and now == "failed":
            regressions.append(tid)
        elif was == "failed" and now == "passed":
            fixed.append(tid)
        elif was == "failed" and now == "failed":
            pre_existing.append(tid)
        elif was is None and now == "failed":
            new.append(tid)
    return {"regressions": regressions, "fixed": fixed,
            "pre_existing_failures": pre_existing, "new_failures": new}


# ── Конфиг ───────────────────────────────────────────────────────────────────

def _build_system(root: Path, override: "str | None") -> str:
    if override in ("gradle", "maven"):
        return override
    try:
        cfg = json.loads((root / "ground" / "pipeline.json").read_text(encoding="utf-8"))
        bs = cfg.get("project", {}).get("build_system")
        if bs in ("gradle", "maven"):
            return bs
    except (OSError, json.JSONDecodeError):
        pass
    return "gradle"


def _resolve_modules(root: Path, args) -> list[str]:
    if args.modules:
        return sorted({m.strip() for m in re.split(r"[,\s]+", args.modules) if m.strip()})
    if getattr(args, "from_taskplan", None):
        return modules_from_taskplan(Path(args.from_taskplan))
    if getattr(args, "from_diff", None) is not None:
        return modules_from_diff(root, args.from_diff)
    return []


# ── CLI ──────────────────────────────────────────────────────────────────────

def cmd_snapshot(args) -> int:
    root = Path(args.root).resolve()
    bs = _build_system(root, args.build_system)
    modules = _resolve_modules(root, args)
    if not modules:
        print("ERROR: не определены модули (--modules / --from-taskplan / --from-diff)", file=sys.stderr)
        return 1
    results, no_results = run_suite(root, modules, bs, args.timeout)
    baseline = {
        "$schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "build_system": bs,
        "modules": modules,
        "modules_without_results": no_results,
        "tests": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(1 for v in results.values() if v == "passed")
    failed = sum(1 for v in results.values() if v == "failed")
    if args.json:
        print(json.dumps({"status": "ok", "modules": modules, "passed": passed,
                          "failed": failed, "out": str(out)}, ensure_ascii=False))
    else:
        print(f"✅ baseline записан: {out}")
        print(f"   модули: {', '.join(modules)}")
        print(f"   тесты: {passed} passed, {failed} failed (pre-existing){_no_res(no_results)}")
    return 0


def cmd_compare(args) -> int:
    root = Path(args.root).resolve()
    bpath = Path(args.baseline)
    if not bpath.exists():
        print(f"ERROR: baseline не найден: {bpath} — снимок ДО разработки не делался "
              "(SKILL §7 baseline-шаг)", file=sys.stderr)
        return 2  # fail-closed: нет отметки зелёного → нельзя подтвердить отсутствие регресса
    try:
        bdata = json.loads(bpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: битый baseline {bpath}: {e}", file=sys.stderr)
        return 2
    base_tests = bdata.get("tests", {})
    bs = _build_system(root, args.build_system or bdata.get("build_system"))
    # сравниваем РОВНО те модули, что в baseline (одинаковый scope = чистый diff);
    # из git-diff можно расширить, но регресс считаем по baseline-модулям.
    modules = bdata.get("modules", [])
    if args.from_diff is not None:
        modules = sorted(set(modules) | set(modules_from_diff(root, args.from_diff)))
    current, no_results = run_suite(root, modules, bs, args.timeout)
    d = diff_baseline(base_tests, current)

    # Не удалось прогнать модуль(и) из baseline → нельзя подтвердить зелёное (fail-closed).
    blocked_no_run = [m for m in bdata.get("modules", []) if m in no_results]
    regressions = d["regressions"]

    if args.json:
        print(json.dumps({"status": "fail" if (regressions or blocked_no_run) else "ok",
                          **d, "modules_without_results": blocked_no_run}, ensure_ascii=False))
    else:
        if regressions:
            print(f"✗ РЕГРЕССИЯ: {len(regressions)} ранее зелёных теста(ов) теперь падают "
                  "(затронутые модули) — успеха нет, почини сломанный тест:")
            for t in regressions[:20]:
                print(f"   ✗ {t}")
        if blocked_no_run:
            print(f"✗ не удалось прогнать тесты модулей: {', '.join(blocked_no_run)} — "
                  "нельзя подтвердить зелёное (fail-closed)")
        if not regressions and not blocked_no_run:
            print(f"✓ регрессий нет. Починено: {len(d['fixed'])}, "
                  f"pre-existing-падений (не блокируют): {len(d['pre_existing_failures'])}")
    return 2 if (regressions or blocked_no_run) else 0


def _no_res(no_results: list) -> str:
    return f"; без результатов: {', '.join(no_results)}" if no_results else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Baseline зелёного + детекция регрессий тестов модулей")
    sub = ap.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    common.add_argument("--modules", help="явный список модулей (csv/space)")
    common.add_argument("--build-system", choices=["gradle", "maven"], default=None)
    common.add_argument("--timeout", type=int, default=600, help="таймаут прогона модуля, сек")
    common.add_argument("--json", action="store_true")

    sp = sub.add_parser("snapshot", parents=[common], help="снять baseline зелёного ДО разработки")
    sp.add_argument("--from-taskplan", help="task-plan.json — модули из tasks[].modules")
    sp.add_argument("--from-diff", help="git base — модули из изменённых файлов")
    sp.add_argument("--out", required=True, help="куда писать baseline.json")

    cp = sub.add_parser("compare", parents=[common], help="сравнить с baseline (регрессии → exit 2)")
    cp.add_argument("--baseline", required=True, help="baseline.json из snapshot")
    cp.add_argument("--from-diff", default=None, help="git base — расширить scope изменёнными модулями")

    args = ap.parse_args()
    if args.mode == "snapshot":
        return cmd_snapshot(args)
    return cmd_compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
