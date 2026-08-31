#!/usr/bin/env python3
"""Юнит-тесты роутинга записи config-helper v2 (manifest vs policy).

Покрывает:
  - inputs.* / decisions.* → ground/statements/<skill>/<feature>/manifest.json
  - quality.* / conventions.* / docs.* / jira.* / project.* / autonomy.* →
    ground/policy.json (immutable на прогоне активной фичи)
  - dual-read fallback в risk_ladder.config_get (legacy pipeline.json →
    sources.story / autonomy.criticality и т.п.)

Запуск: python3 test_config_routing.py (без pytest)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CONFIG_PY = Path(__file__).resolve().parent / "config.py"
HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"


# ── helpers ──────────────────────────────────────────────────────────────────


def _run(project: Path, *args: str) -> subprocess.CompletedProcess:
    """Запустить config.py с --project <tmpdir>. Возвращает CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(CONFIG_PY), "--project", str(project), *args],
        capture_output=True, text=True,
    )


def _seed_policy(project: Path, body: dict | None = None) -> Path:
    """Создать ground/policy.json с заданным (или пустым quality) телом."""
    p = project / "ground" / "policy.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body if body is not None else {"quality": {}},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _seed_manifest(project: Path, skill: str, feature: str,
                   body: dict | None = None) -> Path:
    """Создать ground/statements/<skill>/<feature>/manifest.json."""
    p = project / "ground" / "statements" / skill / feature / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body if body is not None else {},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _run_risk_ladder_config_get(project: Path, dotpath: str) -> subprocess.CompletedProcess:
    """Вызвать risk_ladder.config_get(root, dotpath) через subprocess.

    Используется, когда нужен прямой dual-read fallback (напр. inputs.story без
    активного манифеста → legacy sources.story в pipeline.json). config.py.get
    в этой ситуации вернёт дефолт, а risk_ladder.config_get — реальное значение."""
    script = f"""
import sys
sys.path.insert(0, {str(HOOKS_DIR)!r})
from pathlib import Path
from risk_ladder import config_get
print(config_get(Path({str(project)!r}), {dotpath!r}))
"""
    return subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True)


# ── tests ────────────────────────────────────────────────────────────────────


class TestConfigRouting(unittest.TestCase):
    """Роутинг записи (v2): inputs.*/decisions.* → manifest, всё остальное → policy."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="forge-config-routing-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── 1. inputs.* пишется в manifest.json активной фичи, не в policy.json ───

    def test_set_inputs_writes_to_manifest_not_policy(self) -> None:
        # Манифест должен существовать (init.py создаёт — config.py set
        # для file="manifest" не создаёт файл с нуля).
        _seed_manifest(self.tmpdir, "forgefix", "fix-x", body={})

        r = _run(self.tmpdir, "set", "inputs.story", "PROJ-123",
                 "--skill", "forgefix", "--feature", "fix-x")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        manifest = self.tmpdir / "ground" / "statements" / "forgefix" / "fix-x" / "manifest.json"
        self.assertTrue(manifest.is_file(), f"manifest not written: {manifest}")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data.get("inputs", {}).get("story"), "PROJ-123",
                         f"manifest.inputs.story != 'PROJ-123': {data}")
        # policy.json НЕ должен появиться — это per-feature запись.
        self.assertFalse((self.tmpdir / "ground" / "policy.json").exists(),
                         "policy.json появился при записи inputs.* — это баг роутинга")

    # ── 2. decisions.* тоже пишется в manifest.json ──────────────────────────

    def test_set_decisions_writes_to_manifest(self) -> None:
        # autonomy.criticality в реестре: file=manifest, path=decisions.criticality,
        # enum: low|medium|high. Это per-feature запись → manifest.json.
        _seed_manifest(self.tmpdir, "forgefix", "fix-x", body={})

        r = _run(self.tmpdir, "set", "autonomy.criticality", "high",
                 "--skill", "forgefix", "--feature", "fix-x")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        manifest = self.tmpdir / "ground" / "statements" / "forgefix" / "fix-x" / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data.get("decisions", {}).get("criticality"), "high",
                         f"manifest.decisions.criticality != 'high': {data}")
        # policy.json не создаётся — decisions.* это per-feature.
        self.assertFalse((self.tmpdir / "ground" / "policy.json").exists(),
                         "policy.json появился при записи decisions.*")

    # ── 3. quality.* пишется в policy.json, манифестов не создаёт ────────────

    def test_set_quality_writes_to_policy(self) -> None:
        _seed_policy(self.tmpdir, body={"quality": {}})

        r = _run(self.tmpdir, "set", "quality.coverage_threshold", "0.85")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        policy = self.tmpdir / "ground" / "policy.json"
        self.assertTrue(policy.is_file(), "policy.json не записан")
        data = json.loads(policy.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data.get("quality", {}).get("coverage_threshold"),
                               0.85, places=6,
                               msg=f"quality.coverage_threshold != 0.85: {data}")
        # Ни одного manifest.json не должно появиться в ground/statements/.
        statements = self.tmpdir / "ground" / "statements"
        if statements.is_dir():
            stray = list(statements.rglob("manifest.json"))
            self.assertFalse(stray, f"manifest.json появился при quality.*: {stray}")

    # ── 4. file_key="pipeline" в реестре → policy.json (resolve_file роутинг) ─

    def test_set_pipeline_key_writes_to_policy(self) -> None:
        """Любой ключ с file="pipeline" в реестре пишется в policy.json.

        pipeline.mode НЕ существует как id (DEPRECATED alias от inputs.mode) —
        для проверки resolve_file("pipeline") → policy.json берём валидный id
        с file="pipeline" (quality.max_step_reopens)."""
        _seed_policy(self.tmpdir, body={"quality": {}})

        r = _run(self.tmpdir, "set", "quality.max_step_reopens", "5")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        policy = self.tmpdir / "ground" / "policy.json"
        data = json.loads(policy.read_text(encoding="utf-8"))
        self.assertEqual(data.get("quality", {}).get("max_step_reopens"), 5,
                         f"quality.max_step_reopens != 5: {data}")
        # Явная проверка resolve_file-семантики: в JSON-ответе set указан
        # именно policy.json (НЕ legacy pipeline.json).
        applied = json.loads(r.stdout)
        self.assertEqual(applied.get("file", "").endswith("policy.json"), True,
                         f"set указал не policy.json: {applied}")

    # ── 5. policy.json immutable, когда есть активная фича ───────────────────

    def test_policy_immutable_when_active_feature_exists(self) -> None:
        _seed_manifest(self.tmpdir, "forgefix", "fix-x",
                       body={"feature": "fix-x", "skill": "forgefix"})
        # policy.json можно не создавать — immutability-проверка стреляет раньше
        # загрузки файла.

        r = _run(self.tmpdir, "set", "quality.coverage_threshold", "0.5")

        self.assertNotEqual(r.returncode, 0,
                            f"ожидался nonzero exit при активной фиче, "
                            f"получили {r.returncode}; stdout={r.stdout!r}")
        # JSON-блок пишется в stdout (не stderr) — print(json.dumps({...})).
        combined = (r.stdout or "") + (r.stderr or "")
        self.assertTrue(
            "immutable" in combined.lower() or "active feature" in combined.lower(),
            f"в выводе нет 'immutable'/'active feature': stdout={r.stdout!r} "
            f"stderr={r.stderr!r}",
        )

    # ── 6. policy.json пишется, когда нет активной фичи ─────────────────────

    def test_policy_writable_when_no_active_feature(self) -> None:
        _seed_policy(self.tmpdir, body={"quality": {}})

        r = _run(self.tmpdir, "set", "quality.coverage_threshold", "0.5")

        self.assertEqual(r.returncode, 0,
                         f"set не прошёл без активной фичи: rc={r.returncode} "
                         f"stdout={r.stdout!r} stderr={r.stderr!r}")
        policy = self.tmpdir / "ground" / "policy.json"
        data = json.loads(policy.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data.get("quality", {}).get("coverage_threshold"),
                               0.5, places=6,
                               msg=f"quality.coverage_threshold != 0.5: {data}")

    # ── 7. dual-read fallback: inputs.story в manifest.json читается через
    #       config.py.get даже при наличии policy.json без inputs ─────────────

    def test_dual_read_fallback_for_inputs_story(self) -> None:
        # canonical reader call: `config.py get inputs.story`. Подкоманда `get`
        # не знает --skill/--feature (аргументы для set) → argparse их режет.
        # current_value() сам резолвит активный манифест через load_active_manifest.
        _seed_policy(self.tmpdir, body={"quality": {}})
        _seed_manifest(self.tmpdir, "forgefix", "fix-x",
                       body={"inputs": {"story": "PROJ-9"}})

        r = _run(self.tmpdir, "get", "inputs.story")

        self.assertEqual(r.returncode, 0,
                         f"get failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        # cmd_get выводит JSON-объект {value, source, ...}, не плоскую строку.
        payload = json.loads(r.stdout)
        self.assertEqual(payload.get("value"), "PROJ-9",
                         f"value != 'PROJ-9': {payload}")

    # ── 8. legacy pipeline.json fallback через risk_ladder.config_get ────────

    def test_legacy_pipeline_json_fallback_for_inputs_story(self) -> None:
        """risk_ladder.config_get('inputs.story') без манифеста:
          1) manifest отсутствует → шаг 1 skip;
          2) policy.json без inputs → шаг 2 не находит;
          3) LEGACY_PATHS['inputs.story'] = 'sources.story' → читает
             legacy pipeline.json.

        config.py.get в этой ситуации вернёт дефолт (""), потому что его
        dual-read fallback внутри current_value() срабатывает только если
        manifest.json СУЩЕСТВУЕТ. Поэтому зовём risk_ladder.config_get напрямую.
        """
        ground = self.tmpdir / "ground"
        ground.mkdir(parents=True, exist_ok=True)
        # Legacy v1: ground/pipeline.json с sources.story
        (ground / "pipeline.json").write_text(
            json.dumps({"sources": {"story": "PROJ-legacy"}},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # v2 policy.json без inputs
        (ground / "policy.json").write_text(
            json.dumps({"quality": {}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Манифеста нет (иначе config_get пошёл бы по шагу 1).

        r = _run_risk_ladder_config_get(self.tmpdir, "inputs.story")

        self.assertEqual(r.returncode, 0,
                         f"risk_ladder subprocess упал: rc={r.returncode} "
                         f"stderr={r.stderr!r}")
        self.assertEqual(r.stdout.strip(), "PROJ-legacy",
                         f"dual-read fallback не сработал: "
                         f"stdout={r.stdout!r} stderr={r.stderr!r}")

    # ── 9. get unknown id → nonzero exit ─────────────────────────────────────

    def test_get_unknown_key_exits_nonzero(self) -> None:
        # NB: argparse подкоманды `get` не знает про --skill/--feature, поэтому
        # они могут быть отвергнуты с exit 2. Любой nonzero exit приемлем — это
        # семантика "не silent failure".
        r = _run(self.tmpdir, "get", "inputs.does_not_exist",
                 "--skill", "X", "--feature", "Y")

        self.assertNotEqual(r.returncode, 0,
                            f"ожидался nonzero exit, получили {r.returncode}; "
                            f"stdout={r.stdout!r} stderr={r.stderr!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
