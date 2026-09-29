#!/usr/bin/env python3
"""Smoke-тесты resolve_phases.py — валидация manifest.inputs.mode ↔ manifest.skill.

Покрывает _validate_mode_skill и её интеграцию в resolve_phases() и current_phase():
- mode == skill (forgefix / feature-pipeline) → OK, exit 0
- mode != skill → exit 3 + диагностическое сообщение (mode/mismatch/skill)
- mode вне реестра (напр. снятая ветка) → exit 3 (fail-closed, не «пропустим»)
- inputs.mode отсутствует → валидация пропускается (back-compat со старыми манифестами)

Запуск: python3 test_resolve_phases_mode_skill.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "resolve_phases.py"
PASSED = 0
FAILED = 0


FULL_POLICY = {
    "jira": {"enabled": True},
    "quality": {"tdd": True, "eval_enabled": True},
}


def _make_project(tmpdir, policy_dict, manifest_skill, manifest_feature, manifest_dict):
    """Фикстура: ground/policy.json + ground/statements/<skill>/<feature>/manifest.json.
    Возвращает project root (Path)."""
    project = Path(tmpdir)
    (project / "ground").mkdir(parents=True, exist_ok=True)
    if policy_dict is not None:
        (project / "ground" / "policy.json").write_text(
            json.dumps(policy_dict), encoding="utf-8"
        )
    manifest_dir = project / "ground" / "statements" / manifest_skill / manifest_feature
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(manifest_dict), encoding="utf-8"
    )
    return project


def _run(project, *extra_args):
    """Запуск resolve_phases.py с произвольными доп. аргументами CLI."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(project), *extra_args],
        capture_output=True,
        text=True,
    )


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


def test_match_forgefix():
    """manifest.skill='forgefix' + inputs.mode='forgefix' → валидация OK, exit 0."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {"mode": "forgefix"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        check("test_match_forgefix: exit 0",
              r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_match_feature_pipeline():
    """manifest.skill='feature-pipeline' + inputs.mode='feature-pipeline' → exit 0."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "feature-pipeline",
            "inputs": {"mode": "feature-pipeline"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "feature-pipeline", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        check("test_match_feature_pipeline: exit 0",
              r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_mismatch_exits_3():
    """manifest.skill='forgefix' при inputs.mode='feature-pipeline' → exit 3, диагностика mode/mismatch/skill."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {"mode": "feature-pipeline"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        combined = (r.stdout + r.stderr).lower()
        check("test_mismatch_exits_3: rc == 3",
              r.returncode == 3,
              f"rc={r.returncode} stderr={r.stderr!r}")
        check("test_mismatch_exits_3: stderr содержит mode/mismatch/skill",
              any(kw in combined for kw in ("mode", "mismatch", "skill")),
              f"combined={combined!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_mode_absent_skips_validation():
    """Манифест без inputs.mode → _validate_mode_skill возвращает None, exit 0 (back-compat)."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {},  # mode отсутствует → валидация пропускается
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        check("test_mode_absent_skips_validation: exit 0 (валидация пропущена)",
              r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_mode_vocab_matches_registry():
    """Словарь inputs.mode в карте резолвера == enum в params-registry.

    Регрессия, жившая молча: реестр и ВСЕ брифы предписывают `set inputs.mode fix|full`,
    а карта была ключена именами скиллов (`forgefix`/`feature-pipeline`). Предписанное
    значение отбивалось как unknown mode → rc 3 на резолве каждой фазы, то есть прогон
    ломался ровно при буквальном следовании инструкции. Два файла с одним словарём обязаны
    сверяться тестом, иначе расходятся снова.
    """
    import json as _json
    reg_path = (Path(__file__).resolve().parents[2]
                / "config-helper" / "references" / "params-registry.json")

    def _find(o):
        if isinstance(o, dict):
            if o.get("path") == "inputs.mode":
                return o
            for v in o.values():
                r = _find(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = _find(v)
                if r:
                    return r
        return None

    import importlib.util
    spec = importlib.util.spec_from_file_location("_rp_vocab", SCRIPT)
    rp = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    spec.loader.exec_module(rp)

    entry = _find(_json.loads(reg_path.read_text(encoding="utf-8")))
    check("test_mode_vocab_matches_registry: запись inputs.mode найдена", entry is not None)
    enum = set(entry.get("enum") or [])
    keys = set(rp._MODE_TO_SKILLS)
    missing = enum - keys
    check(f"test_mode_vocab_matches_registry: enum ⊆ карта (не хватает {sorted(missing)})",
          not missing)


def test_unknown_mode_exits_3():
    """Режим вне _MODE_TO_SKILLS (напр. манифест снятой ветки) → exit 3, а не молчаливый пропуск.

    Ветки forge снимаются вместе со своим namespace; манифест, переживший снятие, не должен
    проезжать валидацию как «неизвестный — значит ладно». Пол — unknown mode → exit 3.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {"mode": "forgeold"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x")
        combined = (r.stdout + r.stderr).lower()
        check("test_unknown_mode_exits_3: rc == 3",
              r.returncode == 3,
              f"rc={r.returncode} stderr={r.stderr!r}")
        check("test_unknown_mode_exits_3: диагностика говорит про unknown mode",
              "unknown" in combined and "mode" in combined,
              f"combined={combined!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_current_phase_mismatch_exits_3():
    """current_phase() тоже вызывает _validate_mode_skill — mismatch через --current → exit 3."""
    tmpdir = tempfile.mkdtemp()
    try:
        manifest = {
            "manifest_version": 2,
            "skill": "forgefix",
            "inputs": {"mode": "feature-pipeline"},
            "context": {},
        }
        project = _make_project(tmpdir, FULL_POLICY, "forgefix", "fix-x", manifest)
        r = _run(project, "--feature", "fix-x", "--current")
        check("test_current_phase_mismatch_exits_3: rc == 3",
              r.returncode == 3,
              f"rc={r.returncode} stderr={r.stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    test_match_forgefix()
    test_match_feature_pipeline()
    test_mismatch_exits_3()
    test_unknown_mode_exits_3()
    test_mode_absent_skips_validation()
    test_current_phase_mismatch_exits_3()
    test_mode_vocab_matches_registry()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
