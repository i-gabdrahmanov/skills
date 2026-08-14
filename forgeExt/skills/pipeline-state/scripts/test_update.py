#!/usr/bin/env python3
"""Тесты update.py — закрытие шага манифеста, самая критичная логика pipeline-state.
Раньше скрипт был без парного теста, хотя несёт два гейта закрытия: проверку судей
(required_judges → вердикт produced_by:run_judge+passed) и subagent-origin (evidence-маркер
_origins/<id>.json от SubagentStop). Любая регрессия здесь молча открыла бы гейты.

Фиксируем: exit 3 (нет манифеста), exit 2 (нет шага), блок по отсутствию/FAIL/подделке
вердикта, разблок валидным вердиктом и override, блок/разблок subagent-origin, --skip-judges,
и doc-approval: доко-фазы (00-brd/02-sdd) не закрываются без маркера утверждения
<doc>-approved-<feature> (record_approval) — это же enforcement паузы после мерджа
на согласование.

Запуск: python3 test_update.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "update.py"
PASSED = 0
FAILED = 0


def _statedir(project: Path, skill="feature-pipeline", feature="demo") -> Path:
    d = project / "ground" / "statements" / skill / feature
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_manifest(project: Path, steps: list[dict], skill="feature-pipeline", feature="demo"):
    d = _statedir(project, skill, feature)
    (d / "manifest.json").write_text(
        json.dumps({"skill": skill, "feature": feature, "context": {}, "steps": steps}),
        encoding="utf-8")
    return d


def _verdict(d: Path, judge: str, passed: bool, *, fake=False):
    """Пишет вердикт судьи. fake=True → без provenance (имитация ручной подделки)."""
    (d / "judges").mkdir(exist_ok=True)
    body = {"passed": passed, "summary": "x"}
    if not fake:
        body["produced_by"] = "run_judge"
    (d / "judges" / f"{judge}.json").write_text(json.dumps(body), encoding="utf-8")


def _override(d: Path, judge: str, reason="env"):
    (d / "overrides").mkdir(exist_ok=True)
    (d / "overrides" / f"{judge}.json").write_text(
        json.dumps({"reason": reason}), encoding="utf-8")


def _origin(d: Path, step_id: str):
    (d / "_origins").mkdir(exist_ok=True)
    (d / "_origins" / f"{step_id}.json").write_text("{}", encoding="utf-8")


def _gate(project: Path, step_id: str, *, skill="feature-pipeline", feature="demo", passed=True):
    """Evidence прохождения детерминированного гейта шага (провенанс record_gate)."""
    d = _statedir(project, skill, feature) / "gates"
    d.mkdir(exist_ok=True)
    (d / f"{step_id}.json").write_text(
        json.dumps({"passed": passed, "produced_by": "record_gate", "cmd": "true"}),
        encoding="utf-8")


def _pipeline_cfg(project: Path, cfg: dict):
    """ground/pipeline.json — записанные решения прогона (sources.*, autonomy.* и пр.)."""
    p = project / "ground"
    p.mkdir(parents=True, exist_ok=True)
    (p / "pipeline.json").write_text(json.dumps(cfg), encoding="utf-8")


def _excerpt(project: Path, *, modules=("service-x",), entities=()):
    """Содержательная выжимка grounding — нужна, чтобы закрыть 01-grounding
    (update._check_grounding_substance)."""
    sa = project / "docs" / "system-analysis"
    sa.mkdir(parents=True, exist_ok=True)
    (sa / "grounding-excerpt.json").write_text(
        json.dumps({"modules": list(modules), "entities": list(entities)}), encoding="utf-8")


def _approval(project: Path, key: str, *, fake=False, inner_key: str | None = None):
    """Пишет approval-маркер ground/approvals/<key>.json. fake=True → без провенанса."""
    appr = project / "ground" / "approvals"
    appr.mkdir(parents=True, exist_ok=True)
    body = {"key": inner_key or key, "approved_by": "user", "reason": "test"}
    if not fake:
        body["produced_by"] = "record_approval"
    (appr / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")


def run(project: Path, step_id: str, *extra, skill="feature-pipeline", feature="demo"):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(project), "--skill", skill,
         "--feature", feature, "--step-id", step_id, "--status", "completed", *extra],
        capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def main() -> int:
    # 1. Нет манифеста → exit 3
    with tempfile.TemporaryDirectory() as td:
        rc, out = run(Path(td), "00-brd")
        check("нет манифеста → exit 3", rc == 3, f"rc={rc} {out}")

    # 2. Шаг не в манифесте → exit 2
    with tempfile.TemporaryDirectory() as td:
        _write_manifest(Path(td), [{"id": "00-brd", "status": "pending"}])
        rc, out = run(Path(td), "99-nope")
        check("нет шага → exit 2", rc == 2, f"rc={rc} {out}")

    # 3. required_judges, но вердикта нет → блок (exit != 0)
    with tempfile.TemporaryDirectory() as td:
        _write_manifest(Path(td), [{"id": "00-brd", "status": "pending",
                                    "required_judges": ["brd-judge"]}])
        rc, out = run(Path(td), "00-brd")
        check("нет вердикта → блок", rc != 0 and "brd-judge" in out, f"rc={rc} {out}")

    # 4. Валидный passing-вердикт + утверждение → закрытие (exit 0). 00-brd — не subagent-фаза.
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending",
                                        "required_judges": ["brd-judge"]}])
        _verdict(d, "brd-judge", True)
        _approval(Path(td), "brd-approved-demo")
        rc, out = run(Path(td), "00-brd")
        check("валидный вердикт → exit 0", rc == 0 and '"new_status": "completed"' in out, out)

    # 5. Вердикт FAIL → блок
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending",
                                        "required_judges": ["brd-judge"]}])
        _verdict(d, "brd-judge", False)
        rc, out = run(Path(td), "00-brd")
        check("вердикт FAIL → блок", rc != 0, out)

    # 6. Поддельный вердикт (без produced_by) → блок (anti-tamper)
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending",
                                        "required_judges": ["brd-judge"]}])
        _verdict(d, "brd-judge", True, fake=True)
        rc, out = run(Path(td), "00-brd")
        check("подделка вердикта → блок", rc != 0 and "run_judge" in out, out)

    # 7. FAIL-вердикт + override → закрытие
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending",
                                        "required_judges": ["brd-judge"]}])
        _verdict(d, "brd-judge", False)
        _override(d, "brd-judge")
        _approval(Path(td), "brd-approved-demo")
        rc, out = run(Path(td), "00-brd")
        check("FAIL + override → exit 0", rc == 0, out)

    # 8. subagent-фаза (02-sdd) без origin-маркера → блок
    with tempfile.TemporaryDirectory() as td:
        _write_manifest(Path(td), [{"id": "02-sdd", "status": "pending"}])
        rc, out = run(Path(td), "02-sdd")
        check("02-sdd без origin → блок", rc != 0 and "субагент" in out, out)

    # 9. subagent-фаза с origin-маркером (+ утверждение SDD) → закрытие
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "02-sdd", "status": "pending"}])
        _origin(d, "02-sdd")
        _approval(Path(td), "sdd-approved-demo")
        rc, out = run(Path(td), "02-sdd")
        check("02-sdd c origin → exit 0", rc == 0, out)

    # 10. subagent-origin override снимает блок (утверждение SDD всё равно нужно)
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "02-sdd", "status": "pending"}])
        _override(d, "subagent-origin")
        _approval(Path(td), "sdd-approved-demo")
        rc, out = run(Path(td), "02-sdd")
        check("02-sdd override origin → exit 0", rc == 0, out)

    # 11. --skip-judges обходит все гейты закрытия — но ТОЛЬКО с согласием пользователя (см. 22)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_manifest(p, [{"id": "02-sdd", "status": "pending",
                             "required_judges": ["sdd-judge"]}])
        _approval(p, "skip-judges-demo")
        rc, out = run(p, "02-sdd", "--skip-judges")
        check("--skip-judges (с approval) обходит гейты → exit 0", rc == 0, out)

    # ── doc-approval: доко-фаза не закрывается без утверждения дока ──────────

    # 12. 00-brd: судья PASS, но утверждения нет → блок с подсказкой record_approval
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending",
                                        "required_judges": ["brd-judge"]}])
        _verdict(d, "brd-judge", True)
        rc, out = run(Path(td), "00-brd")
        check("00-brd без утверждения → блок",
              rc != 0 and "brd-approved-demo" in out and "record_approval" in out,
              f"rc={rc} {out}")

    # 13. рукописный маркер утверждения (без провенанса) → блок
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending"}])
        _approval(Path(td), "brd-approved-demo", fake=True)
        rc, out = run(Path(td), "00-brd")
        check("подделка утверждения → блок", rc != 0, out)

    # 14. переименованный чужой маркер (key внутри не совпадает) → блок
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending"}])
        _approval(Path(td), "brd-approved-demo", inner_key="brd-approved-other")
        rc, out = run(Path(td), "00-brd")
        check("чужой key в маркере → блок", rc != 0, out)

    # 15. SDD без утверждения → блок с указанием нужного маркера
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "02-sdd", "status": "pending"}])
        _origin(d, "02-sdd")
        rc, out = run(Path(td), "02-sdd")
        check("SDD без утверждения → блок",
              rc != 0 and "sdd-approved-demo" in out, f"rc={rc} {out}")

    # 16. утверждение → закрытие
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "02-sdd", "status": "pending"}])
        _origin(d, "02-sdd")
        _approval(Path(td), "sdd-approved-demo")
        rc, out = run(Path(td), "02-sdd")
        check("утверждение → exit 0", rc == 0, out)

    # 17. override doc-approved-<step> снимает блок (деградация)
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "00-brd", "status": "pending"}])
        _override(d, "doc-approved-00-brd")
        rc, out = run(Path(td), "00-brd")
        check("override doc-approved → exit 0", rc == 0, out)

    # 18. не-доко-фаза утверждения не требует — но 01-grounding требует содержательную выжимку
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "01-grounding", "status": "pending"}])
        _excerpt(Path(td))
        rc, out = run(Path(td), "01-grounding")
        check("01-grounding с содержательным grounding → exit 0", rc == 0, out)

    # 19. 01-grounding без содержательной выжимки → блок (заглушка/отсутствие обзора)
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "01-grounding", "status": "pending"}])
        rc, out = run(Path(td), "01-grounding")
        check("01-grounding без grounding-excerpt → блок",
              rc != 0 and "grounding" in out, f"rc={rc} {out}")

    # 19b. пустой excerpt ({} — 0 модулей и 0 entities) — тоже блок (заглушка)
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "01-grounding", "status": "pending"}])
        _excerpt(Path(td), modules=(), entities=())
        rc, out = run(Path(td), "01-grounding")
        check("01-grounding с пустым excerpt → блок", rc != 0, f"rc={rc} {out}")

    # 19c. override grounding-substance снимает блок (деградация)
    with tempfile.TemporaryDirectory() as td:
        d = _write_manifest(Path(td), [{"id": "01-grounding", "status": "pending"}])
        _override(d, "grounding-substance-01-grounding")
        rc, out = run(Path(td), "01-grounding")
        check("override grounding-substance → exit 0", rc == 0, out)

    # 20. fix-intake не закрывается, пока ответ «к какой стори баг» не записан
    #     (прогон: агент спросил, пользователь ответил, config.py set не выполнился — молча).
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_manifest(p, [{"id": "fix-intake", "status": "pending"}],
                        skill="forgefix", feature="BUG-512")
        _gate(p, "fix-intake", skill="forgefix", feature="BUG-512")
        rc, out = run(p, "fix-intake", skill="forgefix", feature="BUG-512")
        check("fix-intake без sources.story → блок",
              rc != 0 and "sources.story" in out, f"rc={rc} {out}")

    # 20b. решение записано (в т.ч. осознанное 'none') → шаг закрывается
    for value in ("STOR-100", "none"):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            _write_manifest(p, [{"id": "fix-intake", "status": "pending"}],
                            skill="forgefix", feature="BUG-512")
            _gate(p, "fix-intake", skill="forgefix", feature="BUG-512")
            _pipeline_cfg(p, {"sources": {"story": value}})
            rc, out = run(p, "fix-intake", skill="forgefix", feature="BUG-512")
            check(f"fix-intake с sources.story={value} → exit 0", rc == 0, f"rc={rc} {out}")

    # 21. lite-design с документом под именем слага задачи → блок (имя = контракт).
    #     Так дельта/дизайн выпадали из /forge-spec и из следующих фаз.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        d = _write_manifest(p, [{"id": "lite-design", "status": "pending"}],
                            skill="forgelite", feature="KID-1234")
        _gate(p, "lite-design", skill="forgelite", feature="KID-1234")
        _origin(d, "lite-design")
        docs = p / "docs" / "feature-pipeline" / "KID-1234"
        docs.mkdir(parents=True)
        (docs / "KID-1234.md").write_text("# design", encoding="utf-8")
        (docs / "task-plan.json").write_text("{}", encoding="utf-8")
        rc, out = run(p, "lite-design", "--closed-by", "subagent",
                      skill="forgelite", feature="KID-1234")
        check("lite-design: документ по слагу вместо tech-design.md → блок",
              rc != 0 and "tech-design.md" in out, f"rc={rc} {out}")
        (docs / "KID-1234.md").rename(docs / "tech-design.md")
        rc, out = run(p, "lite-design", "--closed-by", "subagent",
                      skill="forgelite", feature="KID-1234")
        check("lite-design с каноническими именами → exit 0", rc == 0, f"rc={rc} {out}")

    # 21b. дельта фикса ищется /forge-spec строго как sdd.md — и лежит внутри папки стори
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        d = _write_manifest(p, [{"id": "fix-spec", "status": "pending"}],
                            skill="forgefix", feature="BUG-512")
        _gate(p, "fix-spec", skill="forgefix", feature="BUG-512")
        _origin(d, "fix-spec")
        _pipeline_cfg(p, {"sources": {"story": "STOR-100", "spec_anchor": "REQ-0007"}})
        fixdir = p / "docs" / "feature-pipeline" / "STOR-100" / "fixes" / "BUG-512"
        fixdir.mkdir(parents=True)
        (fixdir / "BUG-512.md").write_text("# delta", encoding="utf-8")
        rc, out = run(p, "fix-spec", "--closed-by", "subagent",
                      skill="forgefix", feature="BUG-512")
        check("fix-spec: дельта не под именем sdd.md → блок",
              rc != 0 and "sdd.md" in out, f"rc={rc} {out}")
        (fixdir / "BUG-512.md").rename(fixdir / "sdd.md")
        rc, out = run(p, "fix-spec", "--closed-by", "subagent",
                      skill="forgefix", feature="BUG-512")
        check("fix-spec с sdd.md в папке стори → exit 0", rc == 0, f"rc={rc} {out}")

    # 22. --skip-judges — R4: снимает ВСЕ гейты закрытия, поэтому требует согласия пользователя.
    #     Был bypass в одну опцию (help: «restoring state after init --force»).
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_manifest(p, [{"id": "02-sdd", "status": "pending",
                             "required_judges": ["sdd-judge"]}])
        rc, out = run(p, "02-sdd", "--skip-judges")
        check("--skip-judges без approval → стоп (exit 3)",
              rc == 3 and "skip-judges-demo" in out, f"rc={rc} {out}")
        _approval(p, "skip-judges-demo")
        rc, out = run(p, "02-sdd", "--skip-judges")
        check("--skip-judges с approval-маркером → exit 0", rc == 0, f"rc={rc} {out}")

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_manifest(p, [{"id": "02-sdd", "status": "pending"}])
        _approval(p, "skip-judges-demo", fake=True)
        rc, out = run(p, "02-sdd", "--skip-judges")
        check("--skip-judges с маркером БЕЗ провенанса → стоп", rc == 3, f"rc={rc} {out}")

    # 23. RED-шаг задачи без тестируемого кода пропускается ЛЕГАЛЬНО (брифы обещали
    #     «не заводи шаг», но манифест lite/fix статический — шаг всегда есть).
    def _taskplan(project: Path, layers: list, feature="demo"):
        d = project / "docs" / "feature-pipeline" / feature
        d.mkdir(parents=True, exist_ok=True)
        (d / "task-plan.json").write_text(json.dumps({"feature_slug": feature, "tasks": [
            {"id": "F1", "layers": layers,
             "artifacts": ["src/main/resources/db/changelog/001.xml"]}]}), encoding="utf-8")

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_manifest(p, [{"id": "lite-red", "status": "pending"}],
                        skill="forgelite", feature="demo")
        _taskplan(p, ["migration"])
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--project", str(p), "--skill", "forgelite",
             "--feature", "demo", "--step-id", "lite-red", "--status", "skipped"],
            capture_output=True, text=True)
        check("lite-red skipped при task-plan=migration → exit 0",
              r.returncode == 0, f"rc={r.returncode} {r.stdout}{r.stderr}")

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_manifest(p, [{"id": "lite-red", "status": "pending"}],
                        skill="forgelite", feature="demo")
        _taskplan(p, ["service"])
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--project", str(p), "--skill", "forgelite",
             "--feature", "demo", "--step-id", "lite-red", "--status", "skipped"],
            capture_output=True, text=True)
        check("lite-red skipped при задаче с кодом → стоп (exit 3)",
              r.returncode == 3, f"rc={r.returncode} {r.stdout}{r.stderr}")

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        _write_manifest(p, [{"id": "fix-red", "status": "pending"}],
                        skill="forgefix", feature="demo")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--project", str(p), "--skill", "forgefix",
             "--feature", "demo", "--step-id", "fix-red", "--status", "skipped"],
            capture_output=True, text=True)
        check("fix-red skipped без task-plan → стоп (fail-closed)",
              r.returncode == 3, f"rc={r.returncode} {r.stdout}{r.stderr}")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
