#!/usr/bin/env python3
"""Юнит-тесты config-helper: валидация (fail-closed), запись, бэкап, gates-скелет,
phase-override, risk-мутации. Запуск: python3 test_config.py (без pytest)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "config.py"
PASSED = 0
FAILED = 0


def run(project: Path, *args, stdin: str | None = None):
    r = subprocess.run([sys.executable, str(SCRIPT), "--project", str(project), *args],
                       capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def seed_pipeline(project: Path):
    (project / "ground").mkdir(parents=True, exist_ok=True)
    (project / "ground" / "pipeline.json").write_text(json.dumps({
        "$schema": "feature-pipeline/config@1",
        "project": {"name": "test"},
        "quality": {"coverage_threshold": 0.8, "eval_enabled": True},
        "autonomy": {"mode": "gated", "auto_max_risk": "R1"},
    }, ensure_ascii=False, indent=2))


def seed_risk(project: Path):
    (project / "hooks").mkdir(parents=True, exist_ok=True)
    (project / "hooks" / "risk-policy.json").write_text(json.dumps({
        "version": 1, "default_level": "R1", "autonomy_auto_max": "R1",
        "destructive_blacklist": ["rm -rf /"], "agent_caps": {},
    }, ensure_ascii=False, indent=2))


def main():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        seed_pipeline(project)
        seed_risk(project)

        # list
        rc, out, err = run(project, "list", "--json")
        check("list --json возвращает каталог", rc == 0 and "quality.coverage_threshold" in out, err)

        # get текущее значение
        rc, out, _ = run(project, "get", "quality.coverage_threshold")
        check("get coverage = 0.8", rc == 0 and json.loads(out)["value"] == 0.8, out)

        # get дефолт (нет в файле)
        rc, out, _ = run(project, "get", "delivery.branch_prefix")
        d = json.loads(out)
        check("get дефолт source=default", rc == 0 and d["source"] == "default" and d["value"] == "feature/", out)

        # dry-run не пишет
        rc, out, _ = run(project, "set", "quality.coverage_threshold", "0.9", "--dry-run")
        cfg = json.loads((project / "ground" / "pipeline.json").read_text())
        check("dry-run не меняет файл", rc == 0 and cfg["quality"]["coverage_threshold"] == 0.8, out)

        # реальный set + бэкап
        rc, out, _ = run(project, "set", "quality.coverage_threshold", "0.9")
        cfg = json.loads((project / "ground" / "pipeline.json").read_text())
        res = json.loads(out)
        baks = list((project / "ground" / "config-helper" / "backups").glob("pipeline.json.*.bak"))
        check("set пишет значение", rc == 0 and cfg["quality"]["coverage_threshold"] == 0.9, out)
        check("set создаёт бэкап", res.get("backup") and len(baks) == 1, out)

        # структура цела (соседние ключи не затёрты)
        check("соседние ключи целы", cfg["project"]["name"] == "test" and cfg["quality"]["eval_enabled"] is True)

        # негатив: вне диапазона
        rc, out, _ = run(project, "set", "quality.coverage_threshold", "1.5")
        check("вне диапазона → exit 1", rc == 1 and "error" in json.loads(out), out)

        # негатив: не enum
        rc, out, _ = run(project, "set", "autonomy.mode", "yolo")
        check("плохой enum → exit 1", rc == 1, out)

        # негатив: неизвестный параметр
        rc, out, _ = run(project, "set", "unknown.param", "x")
        check("неизвестный id → exit 3", rc == 3, out)

        # негатив: плохой bool
        rc, out, _ = run(project, "set", "quality.eval_enabled", "maybe")
        check("плохой bool → exit 1", rc == 1, out)

        # gates: файла нет → создаётся с дефолтами
        rc, out, _ = run(project, "set", "tdd_enforced", "false")
        gates = json.loads((project / "ground" / "feature-gates.json").read_text())
        check("gates создан с дефолтами", rc == 0 and "_meta" in gates and len(gates["gates"]) == 9, out)
        check("gates tdd_enforced=false", gates["gates"]["tdd_enforced"]["enabled"] is False, out)
        check("gates прочий дефолт сохранён", gates["gates"]["eval_driven_dev"]["enabled"] is True)

        # sensitive без confirm → блок
        rc, out, _ = run(project, "set", "risk.autonomy_auto_max", "R2")
        check("sensitive без --confirm → exit 1", rc == 1 and json.loads(out).get("blocked"), out)

        # sensitive с confirm → пишет
        rc, out, _ = run(project, "set", "risk.autonomy_auto_max", "R2", "--confirm")
        risk = json.loads((project / "hooks" / "risk-policy.json").read_text())
        check("sensitive с --confirm пишет", rc == 0 and risk["autonomy_auto_max"] == "R2", out)

        # phase disable
        rc, out, _ = run(project, "phase", "disable", "04-tdd")
        cfg = json.loads((project / "ground" / "pipeline.json").read_text())
        ov = next((o for o in cfg.get("phases_override", []) if o["id"] == "04-tdd"), None)
        check("phase disable добавляет override", rc == 0 and ov and ov["enabled_by"] is False, out)

        # phase add со skill=null и gates
        rc, out, _ = run(project, "phase", "add", "05.5-security",
                         "--skill", "null", "--enabled-by", "gates.security_review",
                         "--gates", "security_approved", "--desc", "SAST + CVE")
        cfg = json.loads((project / "ground" / "pipeline.json").read_text())
        ov = next((o for o in cfg["phases_override"] if o["id"] == "05.5-security"), None)
        check("phase add мержит поля", rc == 0 and ov and ov["skill"] is None
              and ov["enabled_by"] == "gates.security_review" and ov["gates"] == ["security_approved"], out)

        # risk list-add без confirm → блок
        rc, out, _ = run(project, "risk", "list-add", "destructive_blacklist", "DROP SCHEMA")
        check("risk без --confirm → exit 1", rc == 1, out)

        # risk list-add с confirm
        rc, out, _ = run(project, "risk", "list-add", "destructive_blacklist", "DROP SCHEMA", "--confirm")
        risk = json.loads((project / "hooks" / "risk-policy.json").read_text())
        check("risk list-add пишет", rc == 0 and "DROP SCHEMA" in risk["destructive_blacklist"], out)

        # risk cap-set
        rc, out, _ = run(project, "risk", "cap-set", "(?i)jira", "R3", "--confirm")
        risk = json.loads((project / "hooks" / "risk-policy.json").read_text())
        check("risk cap-set пишет", rc == 0 and risk["agent_caps"]["(?i)jira"] == "R3", out)

        # ── validate (P3-15) ──
        # На текущем стейте: eval_enabled=True, coverage_threshold>0, jacoco не выставлен →
        # warning про JaCoCo, но не ошибка → exit 0, status ok.
        rc, out, _ = run(project, "validate", "--json")
        v = json.loads(out)
        warns = [i for i in v["issues"] if i["severity"] == "warning"]
        check("validate: чистые типы → status ok", rc == 0 and v["status"] == "ok", out)
        check("validate: JaCoCo-варнинг при активном coverage-гейте",
              any(i["id"] == "quality.jacoco_configured" for i in warns), out)

        # --strict: предупреждение валит ворота (для preflight)
        rc, out, _ = run(project, "validate", "--strict", "--json")
        check("validate --strict: warning → exit 1", rc == 1 and json.loads(out)["status"] == "invalid", out)

        # JaCoCo подключён → варнинг исчезает
        run(project, "set", "quality.jacoco_configured", "true")
        rc, out, _ = run(project, "validate", "--strict", "--json")
        check("validate: с jacoco_configured=true → ok", rc == 0 and json.loads(out)["status"] == "ok", out)

        # Рассинхрон типа: строка там, где ждём float → ошибка (ловит то, ради чего P3-15)
        cfg = json.loads((project / "ground" / "pipeline.json").read_text())
        cfg["quality"]["coverage_threshold"] = "0.8"   # строкой, а не числом
        (project / "ground" / "pipeline.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        rc, out, _ = run(project, "validate", "--json")
        v = json.loads(out)
        errs = [i for i in v["issues"] if i["severity"] == "error"]
        check("validate: строка-вместо-float → exit 1 + error",
              rc == 1 and v["status"] == "invalid"
              and any(i["id"] == "quality.coverage_threshold" for i in errs), out)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
