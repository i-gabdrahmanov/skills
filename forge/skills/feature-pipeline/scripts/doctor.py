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
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[2]  # …/skills/feature-pipeline/scripts → repo root
sys.path.insert(0, str(SCRIPTS))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(m)
    return m


def run_checks(project_root: Path | None = None) -> dict:
    problems: list[str] = []
    checks: list[dict] = []

    def ok(name): checks.append({"name": name, "status": "PASS"})
    def fail(name, detail):
        checks.append({"name": name, "status": "FAIL", "detail": detail})
        problems.append(f"{name}: {detail}")

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

    return {"passed": not problems, "checks": checks, "problems": problems}


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
            mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "·"}.get(c["status"], "?")
            print(f"  {mark} {c['name']}" + (f" — {c['detail']}" if c.get("detail") else ""))
        print("doctor: OK" if res["passed"] else f"doctor: {len(res['problems'])} проблем(ы)")
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
