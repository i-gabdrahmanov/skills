"""e2e.py — гейт e2e: сценарии ui-spec становятся тестами playwright (спека §14.8, §11.2; задача 5.5; INV-C36, B19, F15, QG-N17).

Два инструмента:
  • `daiver_e2e_plan(track)` — из разобранных спек (`daiver_spec`) список `{id, file, title, body, context, line, skip?}`. Имя файла детерминировано по id сценария
    (`ui-spec#2` → `ui-spec-2.e2e.spec.ts`), повторный прогон заменяет СВОИ файлы. Сценарий с контекстом REMOVED описывает удаляемое поведение: тест не пишется,
    в плане он есть с `skip` и причиной (чтобы агент сказал, что скипнуто). Нет сценариев (`hasE2e: false`) — гейт `skipped`, об этом говорится один раз.
    Тела тестов пишет агент (ассерты — по текстам `mocks.ts`): код сценарии не «понимает», он решает, КАКИЕ файлы нужны и где они лежат.
  • `daiver_e2e_run(track, iterationDir)` — прогон `playwright test <файлы> --reporter=junit` и разбор JUnit-отчёта в дефекты итерации. Пишет в папку итерации
    `e2e-summary.json` (статус гейта), `e2e-defects.json` (падения), `e2e-junit.xml`. Статус гейта в `daiver_verify_state_record` берётся из этих файлов, слова агента
    проигрывают (как у дифф-гейта и обзора).

Каталог тестов — вне `project.outputDir` (`ui-guard` запрещает любую запись в outputDir вне шага ui-build, в том числе тестов): `testDir` из `playwright.config.*`, иначе `e2e/`;
каталог внутри outputDir отклоняется поимённо (запасной — `e2e/`).

Вердикт: `skipped` — нет сценариев; нет написанных файлов; нет `@playwright/test` (объявлен? установлен?) или node — ВСЕГДА с причиной, не блокирует clean (спека §14.9); ничего не
ставится и не скачивается. `pass` — прогон завершился кодом 0. `fail` — есть падения (каждое — дефект `zone: e2e`, `severity: major`), прогон не дошёл до тестов (нет отчёта,
конфиг сломан) или не уложился во время: падения называются, не глушатся. Файлов, названных планом и не найденных, в прогон не берём — они в ответе `notWritten`.

Не проверено данными (боевого playwright-проекта нет): формат JUnit разных версий playwright (разбираем `testsuite/testcase/failure|error|skipped`), `webServer` и `baseURL` репо (их
задаёт `playwright.config`, а не движок), время прогона реального набора, `npx` на Windows, `testDir` в нестандартных конфигах (читается строковым литералом).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import capabilities as CA
from .. import config as CFG
from .. import proc as P
from .. import track as TR
from . import capture as CP

SUFFIX = ".e2e.spec.ts"
DEFAULT_DIR = "e2e"
SUMMARY = "e2e-summary.json"
DEFECTS = "e2e-defects.json"
JUNIT = "e2e-junit.xml"
RUN_TIMEOUT = 600.0
MAX_MESSAGE = 600
CONFIGS = ("playwright.config.ts", "playwright.config.js", "playwright.config.mjs", "playwright.config.cjs", "playwright.config.mts")
_TEST_DIR = re.compile(r"""\btestDir\s*:\s*(['"`])([^'"`$\n]+)\1""")


class E2eError(ValueError):
    """Вызов не годится — сообщение называет причину."""


# ── план ─────────────────────────────────────────────────────────────────────────────────────────────

def file_name(scenario_id: str) -> str:
    """Имя файла теста по id сценария: `#` → `-`, всё, кроме букв, цифр, `.`, `_`, `-`, → `_` (имя без каталогов); детерминировано."""
    stem = re.sub(r"[^\w.\-]", "_", scenario_id.replace("#", "-")).strip(".") or "scenario"
    return stem + SUFFIX


def _inside(root: Path, rel: str) -> Optional[Path]:
    if not rel or os.path.isabs(rel) or ".." in Path(rel).parts:
        return None
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p


def _in_output(rel: str, out_dir: str) -> bool:
    a, b = Path(rel).parts, Path(out_dir.strip("/")).parts
    return bool(b) and a[:len(b)] == b


def test_dir(root: Path) -> Tuple[str, List[str]]:
    """(каталог тестов от корня репо, предупреждения): `testDir` из playwright.config, иначе `e2e`; внутри outputDir нельзя."""
    warnings: List[str] = []
    out_dir = str(CFG.load(root)["config"]["project"]["outputDir"])
    found: Optional[str] = None
    for name in CONFIGS:
        path = root / name
        if path.is_file():
            try:
                m = _TEST_DIR.search(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                m = None
            if m:
                found = os.path.normpath(m.group(2).strip()).replace(os.sep, "/")
            break
    if found is None:
        return DEFAULT_DIR, warnings
    if found.startswith("./"):
        found = found[2:]
    if _inside(root, found) is None:
        warnings.append(f"testDir «{found}» из playwright.config вне репо — тесты кладутся в {DEFAULT_DIR}/")
        return DEFAULT_DIR, warnings
    if found == "." or _in_output(found, out_dir):
        warnings.append(f"testDir «{found}» из playwright.config лежит внутри project.outputDir ({out_dir}) — туда пишет только ui-build; тесты кладутся в {DEFAULT_DIR}/ "
                        f"(направь testDir или --config на этот каталог)")
        return DEFAULT_DIR, warnings
    return found, warnings


def plan(root: Any, track: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """`daiver_e2e_plan`: список сценариев с путями файлов. `spec` — ответ `daiver_spec` (разбор спек трека)."""
    root = Path(root).resolve()
    directory, warnings = test_dir(root)
    used: Dict[str, str] = {}
    scenarios: List[Dict[str, Any]] = []
    for sc, removed in [(s, False) for s in spec.get("e2e") or []] + [(s, True) for s in spec.get("removedScenarios") or []]:
        name = file_name(sc["id"])
        if name in used and used[name] != sc["id"]:             # разные id дали одно имя после очистки — различаем детерминированно
            name = file_name(sc["id"] + "-" + hashlib.sha1(sc["id"].encode("utf-8")).hexdigest()[:6])
        used[name] = sc["id"]
        item: Dict[str, Any] = {"id": sc["id"], "file": f"{directory}/{name}", "title": sc.get("title"), "body": sc.get("body"),
                                "context": sc.get("context") or [], "line": sc.get("line"), "spec": sc.get("file")}
        if removed:
            item["skip"] = "контекст REMOVED Requirements: поведение удаляется — тест не пишется"
        scenarios.append(item)
    scenarios.sort(key=lambda s: s["id"])
    live = [s for s in scenarios if "skip" not in s]
    out: Dict[str, Any] = {"track": track, "hasE2e": bool(spec.get("hasE2e")), "dir": directory, "scenarios": scenarios,
                           "files": [s["file"] for s in live], "skipped": [{"id": s["id"], "reason": s["skip"]} for s in scenarios if "skip" in s]}
    if warnings:
        out["warnings"] = warnings
    if not live:
        out["gate"] = {"status": "skipped", "reason": ("в спеках нет сценариев (hasE2e: false)" if not scenarios
                                                       else "все сценарии описывают удаляемое поведение (REMOVED) — тестов нет")}
        out["nextStep"] = "e2e-гейт: skipped — " + out["gate"]["reason"] + "; скажи об этом ОДИН раз и иди дальше (в gates e2e — skipped с этой причиной)"
    else:
        out["nextStep"] = (f"напиши тесты playwright по сценариям в {directory}/ — по одному файлу на сценарий, имена из поля file; ассерты — по текстам mocks.ts (не по "
                           f"выдуманным); повторный прогон заменяет эти файлы. Скипнутые (skipped) не пиши, но скажи, что они скипнуты и почему. Потом daiver_e2e_run")
    return out


# ── разбор JUnit ─────────────────────────────────────────────────────────────────────────────────────

def _clip(text: Optional[str]) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= MAX_MESSAGE else t[:MAX_MESSAGE - 1] + "…"


def parse_junit(text: str) -> Dict[str, Any]:
    """JUnit playwright → {tests, passed, failed, skipped, cases[{file, title, status, message?, line?}]}. Негодный XML — E2eError."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise E2eError(f"JUnit-отчёт не разбирается как XML: {e}")
    cases: List[Dict[str, Any]] = []
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for tc in suite.findall("testcase"):
            file = tc.get("file") or tc.get("classname") or suite.get("name") or ""
            title = tc.get("name") or ""
            fail = tc.find("failure")
            err = tc.find("error")
            bad = fail if fail is not None else err
            c: Dict[str, Any] = {"file": file, "title": title, "status": "failed" if bad is not None else ("skipped" if tc.find("skipped") is not None else "passed")}
            if bad is not None:
                c["message"] = _clip(bad.get("message") or bad.text or bad.get("type") or "тест упал")
                m = re.search(r"\.(?:ts|tsx|js|mjs):(\d+)(?::\d+)?", bad.text or "")
                if m:
                    c["line"] = int(m.group(1))
            cases.append(c)
    return {"tests": len(cases), "passed": sum(c["status"] == "passed" for c in cases), "failed": sum(c["status"] == "failed" for c in cases),
            "skipped": sum(c["status"] == "skipped" for c in cases), "cases": cases}


def _scenario_of(file: str, planned: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    base = Path(file).name
    for s in planned:
        if Path(s["file"]).name == base:
            return s
    return None


def defects_of(parsed: Dict[str, Any], planned: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Падения → дефекты итерации: `zone: e2e`, `severity: major`, id — от сценария и названия теста (стабилен между итерациями)."""
    out: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for c in parsed["cases"]:
        if c["status"] != "failed":
            continue
        sc = _scenario_of(c["file"], planned)
        key = f"{(sc or {}).get('id') or c['file']}|{c['title']}"
        did = "e2e-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        seen[did] = seen.get(did, 0) + 1
        if seen[did] > 1:
            did += f"-{seen[did]}"
        d: Dict[str, Any] = {"id": did, "code": "E2E_FAIL", "layer": "behavior", "severity": "major", "zone": "e2e", "source": "e2e", "element": c["title"] or c["file"],
                             "message": f"e2e упал: {c['message']}", "rule": (sc or {}).get("id")}
        file = (sc or {}).get("file") or c["file"]
        d["location"] = {"file": file, "line": c.get("line") or 1}
        if sc is None:
            d["message"] += f" (файл {c['file']} не из плана сценариев)"
        out.append(d)
    return out


# ── прогон ───────────────────────────────────────────────────────────────────────────────────────────

Runner = Callable[[Path, List[str], Path, float], Tuple[Optional[int], str, bool]]


def run_playwright(root: Path, files: List[str], junit: Path, timeout: float) -> Tuple[Optional[int], str, bool]:
    """(код возврата, хвост вывода, не уложился во время). `npx --no-install`: ничего не скачивается (INV-F11)."""
    npx = shutil.which("npx")
    if npx is None:
        return None, "npx не найден в PATH", False
    env = dict(os.environ, PLAYWRIGHT_JUNIT_OUTPUT_FILE=str(junit), CI="1")
    cmd = [npx, "--no-install", "playwright", "test", *files, "--reporter=junit"]
    try:
        proc = P.popen_group(cmd, cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        return None, f"{npx}: не запустился ({e.strerror or e})", False
    try:
        out, _ = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        P.kill_group(proc)
        out, _ = proc.communicate()
        timed_out = True
    tail = (out or b"").decode("utf-8", "replace")[-1500:]
    return (None if timed_out else proc.returncode), tail, timed_out


def _write(path: Path, data: Dict[str, Any]) -> None:
    CP._atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _skipped(iteration_dir: Path, reason: str, **extra: Any) -> Dict[str, Any]:
    out = {"status": "skipped", "ran": False, "reason": reason, "nextStep": "e2e-гейт: skipped — " + reason + "; в gates e2e — skipped с этой причиной, вслух", **extra}
    _write(iteration_dir / SUMMARY, out)
    _write(iteration_dir / DEFECTS, {"defects": []})
    return out


def run(root: Any, track: str, frame: Optional[str], iteration_dir: Path, spec: Dict[str, Any], *, runner: Optional[Runner] = None,
        timeout: float = RUN_TIMEOUT) -> Dict[str, Any]:
    """`daiver_e2e_run`: гейт e2e итерации."""
    root = Path(root).resolve()
    manifest = TR.load(root, track)
    if manifest is None:
        raise E2eError(f"трек «{track}» не найден")
    f = TR.get_frame(manifest, frame)
    paths = TR.frame_paths(root, manifest, f["name"])
    if not CP.iteration_dir_ok(paths, iteration_dir):
        raise E2eError(f"iterationDir: папка итерации лежит под «verify» (или «baseline») кадра, а не «{iteration_dir}»")
    iteration_dir.mkdir(parents=True, exist_ok=True)
    pl = plan(root, track, spec)
    if "gate" in pl:
        return _skipped(iteration_dir, pl["gate"]["reason"], files=[])
    files = [s["file"] for s in pl["scenarios"] if "skip" not in s]
    present = [x for x in files if (root / x).is_file()]
    missing = [x for x in files if x not in present]
    if not present:
        return _skipped(iteration_dir, "сценарии есть, а тестов нет: ни одного файла плана не написано — напиши их (daiver_e2e_plan) и повтори", files=[], notWritten=missing)
    pw = CA._playwright(root)
    if not pw["found"] and not pw["installed"]:
        return _skipped(iteration_dir, "в package.json репо нет @playwright/test — прогонять нечем (ничего не ставлю: это решение команды)", files=present, notWritten=missing)
    if not pw["installed"]:
        return _skipped(iteration_dir, "@playwright/test объявлен в package.json, но не установлен (нет node_modules/@playwright/test) — установи зависимости сам, я не ставлю",
                        files=present, notWritten=missing)
    junit = iteration_dir / JUNIT
    if junit.exists():
        junit.unlink()
    rc, tail, timed_out = (runner or run_playwright)(root, present, junit, timeout)
    out: Dict[str, Any] = {"files": present, "notWritten": missing, "ran": True}
    if rc is None and not timed_out:
        return _skipped(iteration_dir, f"прогон не запустился: {tail}", files=present, notWritten=missing)
    if timed_out:
        out.update(status="fail", reason=f"прогон не уложился в {int(timeout)} с и остановлен — это не «прошёл»; хвост вывода: {tail.strip()[-300:] or 'пусто'}")
        out["nextStep"] = "e2e упал по времени: найди зависание (webServer, ожидание) — гейт fail, итерация не чистая"
        _write(iteration_dir / SUMMARY, out)
        _write(iteration_dir / DEFECTS, {"defects": []})
        return out
    parsed: Optional[Dict[str, Any]] = None
    if junit.is_file():
        try:
            parsed = parse_junit(junit.read_text(encoding="utf-8"))
        except (E2eError, OSError, UnicodeDecodeError) as e:
            out.update(status="fail", reason=f"отчёт JUnit не читается ({e}): прогон нельзя засчитать; хвост вывода: {tail.strip()[-300:] or 'пусто'}")
    if parsed is None and "status" not in out:
        out.update(status="fail", reason=f"прогон закончился кодом {rc} без JUnit-отчёта — до тестов не дошёл (конфиг, зависимости, webServer); хвост вывода: {tail.strip()[-300:] or 'пусто'}")
    defects: List[Dict[str, Any]] = []
    if parsed is not None:
        defects = defects_of(parsed, pl["scenarios"])
        out.update(tests=parsed["tests"], passed=parsed["passed"], failed=parsed["failed"], skipped=parsed["skipped"], exitCode=rc)
        if parsed["failed"]:
            out["status"] = "fail"
            out["reason"] = f"упало тестов: {parsed['failed']} из {parsed['tests']}"
        elif rc != 0:
            out["status"] = "fail"
            out["reason"] = f"код возврата {rc} при чистом отчёте — прогон не засчитан; хвост вывода: {tail.strip()[-300:] or 'пусто'}"
        elif parsed["tests"] == 0:
            out["status"] = "fail"
            out["reason"] = "в отчёте нет ни одного теста: файлы плана не содержат test() — прогон не засчитан"
        else:
            out["status"] = "pass"
    out["defects"] = len(defects)
    out["nextStep"] = ("e2e: все тесты прошли" if out["status"] == "pass" else
                       "e2e упал: падения — дефекты итерации (zone: e2e, у каждого file:line); чини код или тест по ТЕКСТУ падения, не глуши и не удаляй тесты")
    _write(iteration_dir / SUMMARY, out)
    _write(iteration_dir / DEFECTS, {"defects": defects})
    return out
