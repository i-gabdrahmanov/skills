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


def run(project: Path, *args, stdin: str | None = None, approve_gated: bool = True):
    """Запуск config.py. По умолчанию заранее выдаёт approval, если правится переключатель
    enforcement (см. approve() ниже): тесты в этом файле проверяют приведение типов, роутинг
    и маркеры _incomplete, а не класс согласия. Сам гейт пинится отдельно —
    test_enforcement_switches_are_r4() здесь и TQualityDowngradeIsR4 в hooks/test_gate-guard.py;
    им передаётся approve_gated=False."""
    if approve_gated and len(args) >= 2 and args[0] == "set" and _is_gated(args[1]):
        approve(project, args[1])
    elif approve_gated and len(args) >= 3 and args[0] == "phase" and (
            args[1] == "disable" or "--enabled-by" in args):
        approve_key(project, f"policy-downgrade-phase.{args[2]}")
    elif approve_gated and len(args) >= 3 and args[0] == "risk" and args[1] in ("list-remove",
                                                                                 "cap-set"):
        scope = "agent_caps" if args[1] == "cap-set" else args[2]
        approve_key(project, f"policy-downgrade-risk.{scope}")
    r = subprocess.run([sys.executable, str(SCRIPT), "--project", str(project), *args],
                       capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


# Переключатели enforcement (quality.tdd, coverage_threshold, coverage_exclude_globs, …)
# с недавних пор R4: `config.py set` по ним требует approval-маркера с цитатой пользователя
# (risk-policy.json:quality_downgrade + gate-guard.check_quality_downgrade + второй слой в
# самом config.py). Тесты ниже проверяют ПРИВЕДЕНИЕ ТИПОВ и работу маркеров _incomplete, а
# не класс согласия, поэтому фикстура выдаёт маркер заранее. Нейтральной замены нет: оба
# list-параметра реестра гейтятся. Сам гейт пинится TestEnforcementSwitchesAreR4 ниже и
# hooks/test_gate-guard.py:TQualityDowngradeIsR4.
_RECORD_APPROVAL = (Path(__file__).resolve().parents[1].parent
                    / "pipeline-state" / "scripts" / "record_approval.py")


def _is_gated(param_id: str) -> bool:
    import json as _json
    pol = Path(__file__).resolve().parents[3] / "hooks" / "risk-policy.json"
    try:
        block = _json.loads(pol.read_text(encoding="utf-8")).get("quality_downgrade") or {}
    except Exception:
        return False
    return (param_id in set(block.get("params") or ())
            or param_id.startswith(tuple(block.get("param_prefixes") or ("security.",))))


def approve(project: Path, param_id: str):
    subprocess.run(
        [sys.executable, str(_RECORD_APPROVAL), "--project", str(project),
         "--key", f"policy-downgrade-{param_id}", "--approved-by", "user",
         "--reason", "фикстура теста", "--evidence", "да, меняй параметр для теста"],
        capture_output=True, text=True, check=False)


def approve_key(project: Path, key: str):
    subprocess.run(
        [sys.executable, str(_RECORD_APPROVAL), "--project", str(project),
         "--key", key, "--approved-by", "user",
         "--reason", "фикстура теста", "--evidence", "да, ослабляй политику для теста"],
        capture_output=True, text=True, check=False)


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def seed_policy(project: Path):
    """Сидит v2 policy.json (project-wide). autonomy/criticality/auto_max_risk в v2 — per-feature,
    живут в manifest.json, поэтому здесь их нет (иначе _check_deprecated_in_policy пометит их
    как ERROR в validate). $schema — v2, чтобы соответствовать init_pipeline_config.py v2."""
    (project / "ground").mkdir(parents=True, exist_ok=True)
    (project / "ground" / "policy.json").write_text(json.dumps({
        "$schema": "feature-pipeline/config@2",
        "project": {"name": "test"},
        "quality": {"coverage_threshold": 0.8, "eval_enabled": True},
        "phases_override": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def seed_risk(project: Path):
    (project / "hooks").mkdir(parents=True, exist_ok=True)
    (project / "hooks" / "risk-policy.json").write_text(json.dumps({
        "version": 1, "default_level": "R1", "autonomy_auto_max": "R1",
        "destructive_blacklist": ["rm -rf /"], "agent_caps": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        seed_policy(project)
        seed_risk(project)

        # list
        rc, out, err = run(project, "list", "--json")
        check("list --json возвращает каталог", rc == 0 and "quality.coverage_threshold" in out, err)

        # get текущее значение
        rc, out, _ = run(project, "get", "quality.coverage_threshold")
        check("get coverage = 0.8", rc == 0 and json.loads(out)["value"] == 0.8, out)

        # get дефолт (нет в файле)
        rc, out, _ = run(project, "get", "docs.feature_subdir")
        d = json.loads(out)
        check("get дефолт source=default", rc == 0 and d["source"] == "default" and d["value"] == "feature-pipeline", out)

        # dry-run не пишет
        rc, out, _ = run(project, "set", "quality.coverage_threshold", "0.9", "--dry-run")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("dry-run не меняет файл", rc == 0 and cfg["quality"]["coverage_threshold"] == 0.8, out)

        # реальный set + бэкап
        rc, out, _ = run(project, "set", "quality.coverage_threshold", "0.9")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        res = json.loads(out)
        baks = list((project / "ground" / "config-helper" / "backups").glob("policy.json.*.bak"))
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
        gates = json.loads((project / "ground" / "feature-gates.json").read_text(encoding="utf-8"))
        check("gates создан с дефолтами", rc == 0 and "_meta" in gates and len(gates["gates"]) == 7, out)
        check("gates tdd_enforced=false", gates["gates"]["tdd_enforced"]["enabled"] is False, out)
        check("gates прочий дефолт сохранён", gates["gates"]["eval_driven_dev"]["enabled"] is True)

        # sensitive без confirm → блок
        rc, out, _ = run(project, "set", "risk.autonomy_auto_max", "R2")
        check("sensitive без --confirm → exit 1", rc == 1 and json.loads(out).get("blocked"), out)

        # sensitive с confirm → пишет
        rc, out, _ = run(project, "set", "risk.autonomy_auto_max", "R2", "--confirm")
        risk = json.loads((project / "hooks" / "risk-policy.json").read_text(encoding="utf-8"))
        check("sensitive с --confirm пишет", rc == 0 and risk["autonomy_auto_max"] == "R2", out)

        # phase disable
        rc, out, _ = run(project, "phase", "disable", "04-tdd")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        ov = next((o for o in cfg.get("phases_override", []) if o["id"] == "04-tdd"), None)
        check("phase disable добавляет override", rc == 0 and ov and ov["enabled_by"] is False, out)

        # phase add со skill=null и gates
        rc, out, _ = run(project, "phase", "add", "05.5-security",
                         "--skill", "null", "--enabled-by", "gates.security_review",
                         "--gates", "security_approved", "--desc", "SAST + CVE")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        ov = next((o for o in cfg["phases_override"] if o["id"] == "05.5-security"), None)
        check("phase add мержит поля", rc == 0 and ov and ov["skill"] is None
              and ov["enabled_by"] == "gates.security_review" and ov["gates"] == ["security_approved"], out)

        # ── пин: ручки, которые ЧИТАЕТ пайплайн, обязаны быть в реестре ──
        # (run_judge: max_judge_iterations/coverage_exclude_globs/test_layer;
        #  check_architecture: module_dep_policy; resolve_phases+tdd-guard: tdd, eval_enabled)
        registry = json.loads((SCRIPT.parent.parent / "references" / "params-registry.json")
                              .read_text(encoding="utf-8"))
        reg_ids = {p["id"] for p in registry["params"]}
        readers_keys = ["quality.max_judge_iterations", "quality.module_dep_policy",
                        "quality.test_layer", "quality.coverage_exclude_globs",
                        "quality.tdd", "quality.eval_enabled", "quality.max_step_reopens"]
        missing = [k for k in readers_keys if k not in reg_ids]
        check("реестр покрывает ключи-читатели пайплайна", not missing, f"missing: {missing}")

        # живая ручка TDD пишется и читается
        rc, out, _ = run(project, "set", "quality.tdd", "false")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("set quality.tdd false пишет в policy.json",
              rc == 0 and cfg["quality"]["tdd"] is False, out)

        # enum-ручки
        rc, out, _ = run(project, "set", "quality.module_dep_policy", "deny_new")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("set module_dep_policy deny_new", rc == 0 and cfg["quality"]["module_dep_policy"] == "deny_new", out)
        rc, out, _ = run(project, "set", "quality.test_layer", "integration")
        check("плохой test_layer → exit 1", rc == 1, out)

        # list-тип: JSON-массив и CSV
        rc, out, _ = run(project, "set", "quality.coverage_exclude_globs", '["**/dto/**", "**/config/**"]')
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("list из JSON-массива", rc == 0 and cfg["quality"]["coverage_exclude_globs"] == ["**/dto/**", "**/config/**"], out)
        rc, out, _ = run(project, "set", "quality.coverage_exclude_globs", "**/entity/**,**/repo/**")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("list из CSV", rc == 0 and cfg["quality"]["coverage_exclude_globs"] == ["**/entity/**", "**/repo/**"], out)
        rc, out, _ = run(project, "set", "quality.coverage_exclude_globs", "null")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("list: null → None (дефолты слоя)", rc == 0 and cfg["quality"]["coverage_exclude_globs"] is None, out)
        rc, out, _ = run(project, "validate", "--json")
        check("validate ok после list/None-ручек", rc == 0 and json.loads(out)["status"] == "ok", out)

        # ── пин B2: set чистит маркер _incomplete (гейт арминга preflight §0.1) ──
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        cfg["_incomplete"] = ["project.build_system", "conventions.package_root",
                              "jira.enabled", "quality.tdd",
                              "project.is_git (нужен git init для чекпойнтов rollback и pipeline-state)"]
        (project / "ground" / "policy.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        run(project, "set", "project.build_system", "gradle")
        run(project, "set", "conventions.package_root", "com.acme.app")
        rc, out, _ = run(project, "set", "jira.enabled", "false")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("set снимает отвеченные поля из _incomplete (false — валидный ответ)",
              rc == 0 and cfg.get("_incomplete") == [
                  "quality.tdd",
                  "project.is_git (нужен git init для чекпойнтов rollback и pipeline-state)"], str(cfg.get("_incomplete")))
        run(project, "set", "quality.tdd", "false")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("запись с пояснением в скобках не снимается чужим set",
              cfg.get("_incomplete") == ["project.is_git (нужен git init для чекпойнтов rollback и pipeline-state)"],
              str(cfg.get("_incomplete")))
        cfg["_incomplete"] = ["jira.enabled"]
        (project / "ground" / "policy.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        run(project, "set", "jira.enabled", "true")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("пустой маркер снимается целиком", "_incomplete" not in cfg, str(cfg.get("_incomplete")))

        # risk list-add без confirm → блок
        rc, out, _ = run(project, "risk", "list-add", "destructive_blacklist", "DROP SCHEMA")
        check("risk без --confirm → exit 1", rc == 1, out)

        # risk list-add с confirm
        rc, out, _ = run(project, "risk", "list-add", "destructive_blacklist", "DROP SCHEMA", "--confirm")
        risk = json.loads((project / "hooks" / "risk-policy.json").read_text(encoding="utf-8"))
        check("risk list-add пишет", rc == 0 and "DROP SCHEMA" in risk["destructive_blacklist"], out)

        # risk cap-set
        rc, out, _ = run(project, "risk", "cap-set", "(?i)jira", "R3", "--confirm")
        risk = json.loads((project / "hooks" / "risk-policy.json").read_text(encoding="utf-8"))
        check("risk cap-set пишет", rc == 0 and risk["agent_caps"]["(?i)jira"] == "R3", out)

        # R4: ослабление политики мимо `set`. Раньше хватало --confirm, а его ставит сама
        # модель: `risk list-remove pii_patterns …` снимал детектор ПДн с exit 0.
        rc, out, _ = run(project, "risk", "list-remove", "destructive_blacklist", "DROP SCHEMA",
                         "--confirm", approve_gated=False)
        check("risk list-remove без согласия → exit 2",
              rc == 2 and "policy-downgrade-risk.destructive_blacklist" in out, out)
        rc, out, _ = run(project, "risk", "list-remove", "destructive_blacklist", "DROP SCHEMA",
                         "--confirm")
        risk = json.loads((project / "hooks" / "risk-policy.json").read_text(encoding="utf-8"))
        check("risk list-remove с согласием пишет",
              rc == 0 and "DROP SCHEMA" not in risk["destructive_blacklist"], out)
        run(project, "risk", "list-add", "destructive_blacklist", "DROP SCHEMA", "--confirm")
        rc, out, _ = run(project, "risk", "list-remove", "destructive_blacklist", "DROP SCHEMA",
                         "--confirm", approve_gated=False)
        check("согласие одноразовое — второй list-remove снова просит", rc == 2, out)
        rc, out, _ = run(project, "phase", "disable", "05-verify", approve_gated=False)
        check("phase disable обязательной фазы без согласия → exit 2", rc == 2, out)
        rc, out, _ = run(project, "phase", "disable", "03-jira", approve_gated=False)
        check("phase disable опциональной фазы — свободно", rc == 0, out)
        rc, out, _ = run(project, "phase", "enable", "05-verify", approve_gated=False)
        check("phase enable — свободно (усиление)", rc == 0, out)

        # B4: --project ПОСЛЕ подкоманды — так его пишут доки и брифы; argparse отвечал
        # «unrecognized arguments».
        r = subprocess.run([sys.executable, str(SCRIPT), "get", "quality.tdd",
                            "--project", str(project)], capture_output=True, text=True)
        check("--project после подкоманды принимается", r.returncode == 0, r.stderr)

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
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        cfg["quality"]["coverage_threshold"] = "0.8"   # строкой, а не числом
        (project / "ground" / "policy.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out, _ = run(project, "validate", "--json")
        v = json.loads(out)
        errs = [i for i in v["issues"] if i["severity"] == "error"]
        check("validate: строка-вместо-float → exit 1 + error",
              rc == 1 and v["status"] == "invalid"
              and any(i["id"] == "quality.coverage_threshold" for i in errs), out)

    # 'none' как ОСОЗНАННЫЙ ОТВЕТ (none_is_value), а не «значение не задано».
    # Регресс: 'none' коэрсился в null, а гейты решений отличают «ответили» от «не ответили»
    # по непустому значению → документированный ответ «стори не знаю» давал ДЕДЛОК: config.py
    # печатал "applied", а фаза продолжала требовать то же решение до R4-override.
    # v2: inputs.* пишется в manifest.json активной фичи (ground/statements/<skill>/<feature>/),
    # не в policy.json. Сидим manifest для каждой записи и проверяем путь inputs.X в нём.
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        seed_policy(project)
        # manifest должен существовать ДО set inputs.* (cmd_set для file=manifest не создаёт его сам).
        manifest_dir = project / "ground" / "statements" / "forgefix" / "f1"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({"version": 2, "skill": "forgefix", "feature": "f1",
                        "inputs": {}, "decisions": {}, "context": {}, "steps": []},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        for key in ("inputs.story", "inputs.spec_anchor"):
            rc, out, err = run(project, "set", key, "none")
            mf = json.loads((manifest_dir / "manifest.json").read_text(encoding="utf-8"))
            got = mf.get("inputs", {}).get(key.split(".", 1)[1])
            check(f"set {key} none → строка 'none', а не null",
                  rc == 0 and got == "none", f"rc={rc} got={got!r} {out}{err}")
        # прочие string-параметры null-семантику сохраняют (jira.project_key остаётся в policy.json)
        rc, _, _ = run(project, "set", "jira.project_key", "none")
        cfg = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("set jira.project_key none → null (обычный string-параметр)",
              cfg.get("jira", {}).get("project_key") is None, str(cfg.get("jira")))

    test_enforcement_switches_are_r4()
    test_battle_046_contracts()

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0



def test_enforcement_switches_are_r4():
    """ВТОРОЙ слой гейта: config.py сам требует approval на переключателях enforcement.

    Первый слой — gate-guard.check_quality_downgrade. Этот держится и при запуске мимо
    харнеса, как у repin. Регрессия, которую он пинит: прямая запись в ground/policy.json
    резалась state-write-guard, а санкционный скрипт не гейтился ничем — `set quality.tdd
    false` снимал TDD-гейт с exit 0, причём на ВЕСЬ проект (policy.json переживает прогон).
    """
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        (project / "ground").mkdir(parents=True, exist_ok=True)
        (project / "ground" / "policy.json").write_text(
            json.dumps({"quality": {"tdd": True}}), encoding="utf-8")

        for param, value in (("quality.tdd", "false"),
                             ("quality.eval_enabled", "false"),
                             ("quality.coverage_threshold", "0.5"),
                             ("quality.coverage_exclude_globs", "**/*")):
            rc, out, _ = run(project, "set", param, value, approve_gated=False)
            check(f"без approval {param} → rc 2", rc == 2, out)

        # ФАКТЫ о проекте в том же namespace гейтиться НЕ должны — это работа config-helper
        for param, value in (("quality.build_command", "./gradlew build"),
                             ("quality.test_command", "./gradlew test"),
                             ("quality.jacoco_configured", "true")):
            rc, out, _ = run(project, "set", param, value, approve_gated=False)
            check(f"факт о проекте {param} свободен", rc == 0, out)

        # с маркером — проходит и пишет
        approve(project, "quality.tdd")
        rc, out, _ = run(project, "set", "quality.tdd", "false", approve_gated=False)
        body = json.loads((project / "ground" / "policy.json").read_text(encoding="utf-8"))
        check("с approval quality.tdd пишется", rc == 0 and body["quality"]["tdd"] is False, out)


def test_battle_046_contracts():
    """Боевой прогон v0.4.6: контракты config.py, разошедшиеся с документами."""
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        seed_policy(project)
        pol = project / "ground" / "policy.json"

        # B-F2: --dry-run показывает «было → станет» и ничего не пишет — свободен, как обещают
        # risk-policy.json и gate-guard; второй слой требовал на него approval.
        before = pol.read_text(encoding="utf-8")
        rc, out, _ = run(project, "set", "quality.coverage_threshold", "0.5", "--dry-run",
                         approve_gated=False)
        check("set <переключатель> --dry-run без approval → rc 0",
              rc == 0 and '"dry_run": true' in out, out)
        check("--dry-run ничего не пишет", pol.read_text(encoding="utf-8") == before)

        # G-P1: значение вне словаря строкового параметра принималось молча.
        rc, out, _ = run(project, "set", "spec.grammar.requirement_kind", "table-like")
        check("значение вне enum строкового параметра → rc 1", rc == 1, out)
        rc, out, _ = run(project, "set", "spec.grammar.requirement_kind", "title-only")
        check("значение из enum принимается", rc == 0, out)

        # «none» из словаря — значение, а не null (иначе читатель брал дефолт from-bracket).
        rc, out, _ = run(project, "set", "spec.grammar.provenance", "none")
        got = json.loads(pol.read_text(encoding="utf-8")).get("spec", {}).get("grammar", {})
        check("set spec.grammar.provenance none → строка 'none'",
              rc == 0 and got.get("provenance") == "none", f"{got} {out}")

        # APPR-2: NaN/inf проходили границы и уезжали в policy.json литералом NaN.
        for v in ("nan", "inf", "Infinity"):
            rc, out, _ = run(project, "set", "quality.coverage_threshold", v)
            check(f"coverage_threshold {v} → rc 1", rc == 1, out)
        json.loads(pol.read_text(encoding="utf-8"))   # файл — валидный JSON

    # P3-5: предупреждение о снимке называет прогон, который резолвер хуков считает активным
    # (свежайший живой), а не первый живой по алфавиту.
    import os
    import time
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        seed_policy(project)
        for feat in ("STOR-101", "STOR-901"):
            md = project / "ground" / "statements" / "feature-pipeline" / feat
            md.mkdir(parents=True)
            (md / "manifest.json").write_text(json.dumps(
                {"skill": "feature-pipeline", "feature": feat,
                 "steps": [{"id": "02-design", "status": "pending"}]}), encoding="utf-8")
        old = time.time() - 3600
        os.utime(project / "ground/statements/feature-pipeline/STOR-101/manifest.json",
                 (old, old))
        rc, out, err = run(project, "set", "spec.grammar.requirement_kind", "numbered")
        check("WARNING о снимке называет свежайший живой прогон",
              rc == 0 and "STOR-901" in err and "STOR-101" not in err, err)


if __name__ == "__main__":
    sys.exit(main())
