#!/usr/bin/env python3
"""doctor.py — self-check целостности feature-pipeline (детерминированный).

Ловит «полу-подключённость» ДО прогона: рассогласование имён судей, отсутствие
писателя вердикта, дрейф фазовых констант между копиями, неканонический порядок фаз,
битые пути реестра. Запускается из preflight (предупреждение при FAIL) и вручную.

Usage:
    python3 doctor.py [--project <root>] [--json]
Exit: 0 — всё ок, 1 — есть проблемы, 2 — не удалось выполнить.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[2]  # …/skills/feature-pipeline/scripts → repo root
sys.path.insert(0, str(SCRIPTS))

# Минимальная версия Python — ЕДИНЫЙ источник (скрипты/хуки используют PEP604 `X | None`
# и match; на 3.9 phase_sync падал → ложное «несоответствие стадий»). preflight.py пинит копию.
MIN_PYTHON = (3, 10)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(m)
    return m


def run_checks(project_root: Path | None = None) -> dict:
    problems: list[str] = []      # жёсткие нарушения целостности кода → passed=False
    warnings: list[str] = []      # средовые/конфиг-советы (Python/git/config) → не валят passed
    checks: list[dict] = []

    def ok(name): checks.append({"name": name, "status": "PASS"})
    def fail(name, detail):
        checks.append({"name": name, "status": "FAIL", "detail": detail})
        problems.append(f"{name}: {detail}")
    def warn(name, detail):
        # Средовой/конфиг-совет: виден и уходит в preflight как warning, но не делает doctor «красным»
        checks.append({"name": name, "status": "WARN", "detail": detail})
        warnings.append(f"{name}: {detail}")

    pp = _load(SCRIPTS / "pipeline_phases.py", "pp_doctor")
    rj = _load(SCRIPTS / "run_judge.py", "rj_doctor")
    ps = _load(REPO / "skills/pipeline-state/scripts/phase_sync.py", "ps_doctor")
    pm = _load(REPO / "skills/pipeline-state/scripts/patch_manifest_judges.py", "pm_doctor")
    asfp = _load(SCRIPTS / "add_steps.py", "asfp_doctor")
    pv = _load(SCRIPTS / "preflight-validate.py", "pv_doctor")

    # 1. Имена судей в маске производятся run_judge (детерминированно или через --from-output)
    producible = {f"{p}-judge" for p in rj.PHASE_MAP}
    for step, judges in pp.REQUIRED_JUDGES_MASK.items():
        for j in judges:
            if j not in producible:
                fail("judge-name-producible",
                     f"судья '{j}' (шаг {step}) не производится run_judge ({sorted(producible)})")
                break
        else:
            continue
        break
    else:
        ok("judge-name-producible")

    # 2. Каждый pass-through судья поддержан ingest (--from-output); детерминированные — фазой
    #    (build/delivery — ingest; остальные — собственная фаза). Проверяем, что фаза есть.
    judge_phases = {j[:-len("-judge")] for judges in pp.REQUIRED_JUDGES_MASK.values() for j in judges}
    missing_phase = [p for p in judge_phases if p not in rj.PHASE_MAP]
    if missing_phase:
        fail("judge-writer", f"нет фазы run_judge для судей: {missing_phase}")
    else:
        ok("judge-writer")

    # 3. Константы идентичны во всех копиях
    const_ok = True
    for mod in (ps, asfp, pv):
        if mod.PREFIX_PHASE != pp.PREFIX_PHASE or mod.MAIN_PHASES != pp.MAIN_PHASES:
            fail("phase-constants-consistent", f"{mod.__name__} расходится с pipeline_phases")
            const_ok = False
            break
    if pm.REQUIRED_JUDGES_MASK != pp.REQUIRED_JUDGES_MASK:
        fail("phase-constants-consistent", "patch_manifest_judges.REQUIRED_JUDGES_MASK расходится")
        const_ok = False
    if const_ok:
        ok("phase-constants-consistent")

    # 4. Канонический порядок фаз в build_gate
    sample = [{"id": "00-brd"}, {"id": "07-report"}, {"id": "04-build-T1"},
              {"id": "02-design"}, {"id": "05-tests"}]
    ids = [ph["id"] for ph in pp.build_gate(sample)["phases"]]
    expected = [m for m in pp.MAIN_PHASES if m in ids]
    if ids != expected:
        fail("canonical-phase-order", f"{ids} != {expected}")
    else:
        ok("canonical-phase-order")

    # 5. Пути реестра существуют (только если есть развёрнутый .gigacode)
    if project_root and (project_root / ".gigacode").exists():
        cp = _load(SCRIPTS / "check_paths.py", "cp_doctor")
        cfg = cp.find_skill_paths_json(project_root)
        if cfg.exists():
            valid, invalid = cp.check_paths(project_root, cfg)
            if invalid:
                fail("registry-paths-exist", f"битые пути: {invalid[:5]}")
            else:
                ok("registry-paths-exist")
        else:
            checks.append({"name": "registry-paths-exist", "status": "SKIP",
                           "detail": "skill-paths.json не найден"})
    else:
        checks.append({"name": "registry-paths-exist", "status": "SKIP",
                       "detail": "нет project/.gigacode (source-репо)"})

    # 6. Shell-lint: в fenced-блоках SKILL.md нет конструкций, которые режет рантайм Qwen
    #    ($(...), backticks, here-strings <<<). common.py:repo_root уже обходит это для git.
    import re as _re
    fenced_re = _re.compile(r"```.*?```", _re.DOTALL)
    forbidden = [("$(", "command substitution $()"), ("<<<", "here-string <<<")]
    shell_hits = []
    for md in sorted(REPO.glob("skills/*/SKILL.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        for block in fenced_re.findall(text):
            body = block[3:-3]  # без ограничителей ```
            for tok, label in forbidden:
                if tok in body:
                    shell_hits.append(f"{md.parent.name}/SKILL.md: {label}")
                    break
    if shell_hits:
        fail("shell-lint-skill-md", f"запретные shell-конструкции (Qwen режет): {shell_hits[:5]}")
    else:
        ok("shell-lint-skill-md")

    # 7. Хардкод путей: реальные пути ~/.gigacode/skills|hooks в SKILL.md (канон —
    #    <project>/.gigacode). Guideline-строки вида «не используй ~/.gigacode/...» (с …)
    #    легитимны и не считаются нарушением.
    hardcode_re = _re.compile(r"~/\.gigacode/(?:skills|hooks)\b")
    path_hits = []
    for md in sorted(REPO.glob("skills/*/SKILL.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        if hardcode_re.search(text):
            path_hits.append(f"{md.parent.name}/SKILL.md")
    if path_hits:
        fail("no-hardcoded-home-paths",
             f"~/.gigacode-хардкод (используй <project>/.gigacode): {path_hits}")
    else:
        ok("no-hardcoded-home-paths")

    # 8. Версия Python зафиксирована (скрипты требуют >= MIN_PYTHON; на 3.9 phase_sync падал).
    #    Средовой совет (warn), а не integrity-fail: doctor может гоняться и на старом интерпретаторе.
    if sys.version_info[:2] < MIN_PYTHON:
        have = f"{sys.version_info.major}.{sys.version_info.minor}"
        warn("python-version",
             f"Python {have} < {MIN_PYTHON[0]}.{MIN_PYTHON[1]} — скрипты используют синтаксис "
             f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ (PEP604/match). Обнови интерпретатор.")
    else:
        ok("python-version")

    # 9. git доступен (repo_root, ветки/доставка, ключ pipeline-state — всё на git)
    if shutil.which("git"):
        ok("git-available")
    else:
        warn("git-available", "git не найден в PATH — фазы доставки/состояния работать не будут")

    # 10. Конфиг валиден по типам + coverage-гейт обеспечен JaCoCo. Переиспускаем ЕДИНУЮ
    #     реализацию — config-helper validate --strict (там же кросс-проверка JaCoCo из P0-1/P3-15),
    #     чтобы не плодить вторую копию логики.
    cfg_validate = REPO / "skills" / "config-helper" / "scripts" / "config.py"
    pipeline_json = (project_root / "ground" / "pipeline.json") if project_root else None
    if not project_root:
        checks.append({"name": "config-valid", "status": "SKIP", "detail": "нет project root"})
    elif not cfg_validate.exists() or not (pipeline_json and pipeline_json.exists()):
        checks.append({"name": "config-valid", "status": "SKIP",
                       "detail": "нет config-helper или ground/pipeline.json"})
    else:
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", str(cfg_validate), "--project", str(project_root),
                 "validate", "--strict", "--json"],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            if r.returncode == 0:
                ok("config-valid")
            else:
                detail = ""
                try:
                    v = json.loads(r.stdout)
                    detail = "; ".join(i.get("error", "")[:90] for i in v.get("issues", [])[:3])
                except Exception:
                    detail = (r.stdout or r.stderr).strip()[:180]
                warn("config-valid", detail or "config validate FAIL")
        except Exception as e:
            checks.append({"name": "config-valid", "status": "SKIP", "detail": f"не выполнен: {e}"})

    result = {"passed": not problems, "checks": checks, "problems": problems}
    if warnings:
        result["warnings"] = warnings
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = Path(args.project).resolve() if args.project else None
    try:
        res = run_checks(root)
    except Exception as e:
        print(json.dumps({"passed": False, "error": str(e)}) if args.json
              else f"doctor: ERROR {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for c in res["checks"]:
            mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "·"}.get(c["status"], "?")
            print(f"  {mark} {c['name']}" + (f" — {c['detail']}" if c.get("detail") else ""))
        nwarn = len(res.get("warnings", []))
        tail = f" (+{nwarn} предупрежд.)" if nwarn else ""
        print(("doctor: OK" if res["passed"] else f"doctor: {len(res['problems'])} проблем(ы)") + tail)
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
