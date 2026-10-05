#!/usr/bin/env python3
"""check_acceptance.py — каждый критерий приёмки задачи подтверждён своим тестом.

Зачем. Критерии приёмки (`task-plan.tasks[].acceptance`) — единственное проверяемое «что
должно работать». Раньше их связь с кодом держалась только на словах: evals общие (compile,
coverage, «сьют зелёный»), RED-гейт мерил «все новые тесты падают», а какой тест какой критерий
проверяет — знал лишь LLM-судья. Задача закрывалась зелёной, хотя критерий мог не иметь ни
одного теста, и merge нёс его в мастер как сделанное.

Связь — МАРКЕР В КОДЕ ТЕСТА, рядом с тест-методом (комментарий или javadoc над ним):

    // @acceptance STOR-100:T1.2
    @Test
    void shouldRejectEmptyPeriod() { ... }

ID критерия — `<фича>:<задача>.<номер>`: фича — имя каталога доков с task-plan.json (у фикса —
ключ бага), номер — позиция в `acceptance[]` с единицы. Префикс фичи обязателен: ID задач
(`T1`, `T2`) повторяются от фичи к фиче, а маркеры старых фич остаются в тестах навсегда.
Полный список ID печатает `--list`. Один тест может закрывать несколько критериев
(`@acceptance STOR-100:T1.1, STOR-100:T1.3`).

Режимы:
  --list                  ID и тексты критериев (для тестописателя)
  (по умолчанию, static)  у каждого критерия ≥1 маркированный тест; маркеров на несуществующие
                          критерии фичи нет. Тесты не запускаются.
  --expect green          static + прогон маркированных тестов задачи: каждый выполнился и
                          ПРОШЁЛ (по-тестово, JUnit XML). Плюс артефакты задачи на диске
                          (check_build). Это гейт закрытия 04-build-<id> / fix-green.
  --expect red            static + прогон: каждый маркированный тест выполнился и УПАЛ.

НЕ ВСЁ ПРОВЕРЯЕТСЯ ТЕСТОМ (вид в UI, формат лога, нагрузка, поведение внешней системы,
документация). Такой критерий в task-plan — объект, решение принимается на дизайне и видно
пользователю при утверждении плана:

    {"text": "в аудит-логе пишется оператор и период", "verify": "manual",
     "reason": "формат лога проверяет сопровождение на стенде, юнит-тестом не ловится"}

Ручной критерий тест не требует, но и «просто так» не закрывается: на `--expect green` нужен
согласованный человеком маркер `acceptance-<ID>` (record_approval.py, в журнале approvals —
подделать его записью мимо скрипта нельзя). В evidence согласия — КАК проверено.
check_taskplan.py не пускает ручной критерий без reason и задачу с кодом, у которой ни одного
критерия под тестом.

Освобождённые от тестов задачи (no_test / слои из quality.no_test_layers): их тестовые
критерии печатаются как `exempt`, и /forge-merge пишет это в мастер как есть.

Usage:
    check_acceptance.py <task-plan.json> [--root .] [--task T1] [--list]
        [--expect green|red] [--test-cmd CMD] [--json]
Exit: 0 = pass, 2 = fail (нет теста у критерия / тест не прошёл / не выполнился).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

_SELF = Path(__file__).resolve().parent
for _p in (_SELF, _SELF.parents[1] / "pipeline-state" / "scripts", _SELF.parents[2] / "hooks"):
    if str(_p) not in sys.path and _p.is_dir():
        sys.path.insert(0, str(_p))

MARKER_RE = re.compile(r"@acceptance\s+((?:[\w.-]+:[\w-]+\.\d+)(?:\s*,\s*[\w.-]+:[\w-]+\.\d+)*)")
_ID_RE = re.compile(r"[\w.-]+:[\w-]+\.\d+")
# Объявление тест-метода: Java `void name(`, Kotlin `fun name(` / fun `name with spaces`(
_METHOD_RE = re.compile(r"\b(?:void|fun)\s+(`[^`]+`|[A-Za-z_]\w*)\s*\(")
_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)
_TEST_EXT = (".java", ".kt")
_SKIP = {"build", "out", "target", ".gradle", ".idea", "node_modules", ".git"}
_GRADLEW = "gradlew.bat" if sys.platform == "win32" else "./gradlew"


def feature_of(plan_path: Path) -> str:
    """Фича для ID критериев — имя каталога доков (как у стейта прогона и /forge-merge)."""
    return Path(plan_path).resolve().parent.name


def normalize(item) -> dict:
    """Критерий task-plan → {text, verify: test|manual, reason}. Строка — тестовый критерий."""
    if isinstance(item, dict):
        verify = str(item.get("verify") or "test").strip().lower()
        return {"text": str(item.get("text") or "").strip(),
                "verify": "manual" if verify == "manual" else "test",
                "reason": str(item.get("reason") or "").strip()}
    return {"text": str(item or "").strip(), "verify": "test", "reason": ""}


def items(task: dict) -> list:
    """Непустые критерии задачи в нормальной форме, в порядке плана."""
    raw = task.get("acceptance") or []
    if isinstance(raw, (str, dict)):
        raw = [raw]                  # одиночный критерий вместо массива — не разбирать посимвольно
    return [c for c in (normalize(a) for a in raw) if c["text"]]


def criteria(feature: str, task: dict) -> list:
    """[(id, текст)] критериев задачи; номер — позиция в acceptance[] с единицы."""
    tid = str(task.get("id") or "?")
    return [(f"{feature}:{tid}.{i}", c["text"]) for i, c in enumerate(items(task), 1)]


def approval_key(ac: str) -> str:
    """Ключ согласия ручного критерия — уже в форме, в которой его хранит record_approval."""
    return "acceptance-" + (re.sub(r"[^A-Za-z0-9._-]+", "-", ac).strip("-") or "x")


def manual_approval(root: Path, ac: str) -> "dict | None":
    try:
        import forge_events as FE
        return FE.approval(Path(root), approval_key(ac))
    except Exception:  # noqa: BLE001 — журнала нет: согласия нет
        return None


def _test_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP and not d.startswith(".")]
        parts = Path(dirpath).parts
        if "test" not in parts:
            continue
        for f in filenames:
            if f.endswith(_TEST_EXT):
                yield Path(dirpath) / f


def scan(root: Path, feature: "str | None" = None) -> dict:
    """{id критерия: [{"test": "pkg.Class.method", "class": "pkg.Class", "file": rel}]}.

    Маркер привязывается к БЛИЖАЙШЕМУ следующему объявлению метода в файле. Маркер без метода
    ниже (висит в конце файла) — в ключе "" не попадает никуда: такой тест не существует."""
    root = Path(root)
    out: dict = {}
    for f in _test_files(root):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "@acceptance" not in text:
            continue
        m = _PKG_RE.search(text)
        cls = (m.group(1) + "." if m else "") + f.stem
        lines = text.splitlines()
        pending: list = []
        for line in lines:
            mk = MARKER_RE.search(line)
            if mk:
                pending += _ID_RE.findall(mk.group(1))
                continue
            if not pending:
                continue
            mm = _METHOD_RE.search(line)
            if not mm:
                continue
            name = mm.group(1).strip("`")
            for ac in pending:
                if feature and not ac.startswith(feature + ":"):
                    continue
                out.setdefault(ac, []).append({
                    "test": f"{cls}.{name}", "class": cls, "method": name,
                    "file": str(f.relative_to(root)).replace("\\", "/")})
            pending = []
    return out


def _cfg(root: Path) -> dict:
    try:
        from _config_loader import load_project_config
        return load_project_config(root) or {}
    except Exception:  # noqa: BLE001
        return {}


def _exempt(task: dict, cfg: dict) -> bool:
    try:
        import pipeline_phases as PP
        return (not PP.task_touches_code(task)) or PP.task_is_test_exempt(task, cfg)
    except Exception:  # noqa: BLE001 — предикат не поднялся: не освобождаем (fail-closed)
        return False


def analyze(plan: dict, feature: str, found: dict, cfg: dict, task_id: "str | None" = None) -> dict:
    """Матрица «критерий → тесты» по задачам плана + нарушения (static-слой)."""
    tasks = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
    known = {ac for t in tasks for ac, _ in criteria(feature, t)}
    rows, errors = [], []
    for t in tasks:
        if task_id and str(t.get("id")) != task_id:
            continue
        exempt = _exempt(t, cfg)
        for (ac, text), c in zip(criteria(feature, t), items(t)):
            tests = found.get(ac, [])
            if c["verify"] == "manual":
                st = "manual"
                if not c["reason"]:
                    errors.append(f"{ac} «{text}»: ручная проверка без reason — почему не тестом?")
            else:
                st = "exempt" if exempt else ("covered" if tests else "no-test")
            if st == "no-test":
                errors.append(f"{ac} «{text}»: нет теста с маркером `@acceptance {ac}` "
                              f"(не проверяется тестом — это решение дизайна: "
                              f"verify:\"manual\" + reason в task-plan)")
            rows.append({"id": ac, "task": str(t.get("id")), "text": text, "status": st,
                         "reason": c["reason"], "tests": [x["test"] for x in tests]})
    scoped = {ac for ac in found if not task_id or ac.split(":", 1)[1].startswith(f"{task_id}.")}
    for ac in sorted(scoped - known):
        errors.append(f"маркер `@acceptance {ac}` ссылается на несуществующий критерий "
                      f"({', '.join(x['test'] for x in found[ac])}) — номер устарел после правки плана?")
    return {"rows": rows, "errors": errors}


def _test_cmd(cfg: dict, classes: list, override: "str | None") -> str:
    base = override or (cfg.get("quality") or {}).get("test_command") or ""
    maven = ((cfg.get("project") or {}).get("build_system") == "maven")
    if not base:
        base = "mvn -q test" if maven else f"{_GRADLEW} test"
    if maven:
        names = ",".join(c.rsplit(".", 1)[-1] for c in classes)
        return f"{base} -Dtest={shlex.quote(names)} -Dsurefire.failIfNoSpecifiedTests=false"
    return base + "".join(f" --tests {shlex.quote(c)}" for c in classes)


def run_check(root: Path, rows: list, found: dict, cfg: dict, expect: str,
              test_cmd: "str | None", timeout: int = 1800) -> list:
    """Прогон маркированных тестов; проставляет rows[].result. Возвращает нарушения."""
    import junit_report
    tests = sorted({t for r in rows if r["status"] == "covered" for t in r["tests"]})
    if not tests:
        return []
    classes = sorted({t.rsplit(".", 1)[0] for t in tests})
    cmd = _test_cmd(cfg, classes, test_cmd)
    started = time.time()
    try:
        subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True, text=True,
                       timeout=timeout)
    except subprocess.TimeoutExpired:
        return [f"прогон тестов не уложился в {timeout} с: {cmd}"]
    t = junit_report.summarize(root, since=started - 2)
    red, green = set(t["red"]), set(t["green"])

    def _state(name: str) -> str:
        cls, meth = name.rsplit(".", 1)
        simple = cls.rsplit(".", 1)[-1]
        for pool, st in ((red, "failed"), (green, "passed")):
            for n in pool:
                c, _, m = n.rpartition(".")
                # JUnit5 пишет имя метода с () и displayName; класс — FQN либо простое имя
                if (c == cls or c.rsplit(".", 1)[-1] == simple or c.endswith("$" + simple)) \
                        and m.split("(")[0] == meth:
                    return st
        return "not-run"

    errors = []
    want = "passed" if expect == "green" else "failed"
    for r in rows:
        if r["status"] != "covered":
            continue
        res = {x: _state(x) for x in r["tests"]}
        r["result"] = res
        bad = [f"{x} ({s})" for x, s in res.items() if s != want]
        if bad:
            errors.append(f"{r['id']} «{r['text']}»: тест не {'прошёл' if want == 'passed' else 'упал'}"
                          f" — {', '.join(bad)}")
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Критерии приёмки ↔ тесты.")
    ap.add_argument("plan")
    ap.add_argument("--root", default=".")
    ap.add_argument("--task", default=None)
    ap.add_argument("--list", action="store_true", help="напечатать ID и тексты критериев")
    ap.add_argument("--expect", choices=("green", "red"), default=None)
    ap.add_argument("--test-cmd", default=None, help="базовая команда тестов (без фильтра)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    plan_path = Path(args.plan)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"✗ task-plan нечитаем: {e}", file=sys.stderr)
        return 2
    feature = feature_of(plan_path)
    cfg = _cfg(root)

    if args.list:
        for t in plan.get("tasks") or []:
            if args.task and str(t.get("id")) != args.task:
                continue
            ex = " (задача освобождена от тестов — маркер не нужен)" if _exempt(t, cfg) else ""
            for (ac, text), c in zip(criteria(feature, t), items(t)):
                tail = (f" (ручная проверка, тест не нужен: {c['reason']})"
                        if c["verify"] == "manual" else ex)
                print(f"{ac}\t{text}{tail}")
        return 0

    found = scan(root, feature)
    res = analyze(plan, feature, found, cfg, args.task)
    errors = list(res["errors"])

    if args.expect == "green":
        import check_build
        for t in plan.get("tasks") or []:
            if args.task and str(t.get("id")) != args.task:
                continue
            for art in t.get("artifacts") or []:
                if check_build.find_artifact(root, str(art)) is None:
                    errors.append(f"{t.get('id')}: нет артефакта задачи в коде — {art}")
    if args.expect and not res["errors"]:
        errors += run_check(root, res["rows"], found, cfg, args.expect, args.test_cmd)
    if args.expect == "green":
        rp = Path(__file__).resolve().parents[2] / "pipeline-state" / "scripts" / "record_approval.py"
        for r in res["rows"]:
            if r["status"] != "manual":
                continue
            ok = manual_approval(root, r["id"])
            r["approval"] = ({k: ok.get(k) for k in ("approved_by", "evidence", "reason", "ts")}
                             if ok else None)
            if not ok:
                errors.append(
                    f"{r['id']} «{r['text']}»: ручная проверка не подтверждена человеком. "
                    f"Покажи пользователю критерий и что именно проверить, спроси, проверено ли "
                    f"и как; на его ответ — python3 {rp} --project {root} --key "
                    f"{approval_key(r['id'])} --kind acceptance --approver user --evidence "
                    f"\"<дословная фраза пользователя: как проверено>\" --reason \"<кратко>\". "
                    f"Цитата сверяется с транскриптом — своими словами не пройдёт")

    out = {"status": "fail" if errors else "pass", "feature": feature, "task": args.task,
           "expect": args.expect or "static", "criteria": res["rows"], "errors": errors}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"Acceptance gate ({out['expect']}): {'✓ PASS' if not errors else '✗ FAIL'}")
        for r in res["rows"]:
            mark = {"covered": "✓", "exempt": "·", "no-test": "✗", "manual": "✋"}[r["status"]]
            tail = (", ".join(r["tests"]) if r["tests"] and r["status"] != "manual"
                    else f"ручная проверка ({r['reason']})" if r["status"] == "manual"
                    else r["status"])
            print(f"  {mark} {r['id']} «{r['text']}» — {tail}")
        for e in errors:
            print(f"  ✗ {e}")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
