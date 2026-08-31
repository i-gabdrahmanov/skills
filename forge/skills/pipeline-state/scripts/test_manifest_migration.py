#!/usr/bin/env python3
"""Tests for manifest v1 → v2 migration в init.py.

Покрывает:
  - migrate_manifest_v1_to_v2: копирует legacy-поля из ground/pipeline.json
    в inputs/decisions, проставляет version=2, пишет audit в manifest.migration.
  - ensure_manifest_sections: гарантирует наличие inputs/decisions (идемпотентно).
  - CLI --inputs: проброс per-feature входов в манифест.
  - CLI --no-migrate: подавляет авто-миграцию из legacy pipeline.json.

Замечание про семантику полей (расхождения с user-stories):
  - В коде init.py поле версии называется «version» (int), не «manifest_version».
  - migration.from / migration.to — строки «1»/«2», не int.
  - migration.migrated_fields — dict {inputs: [...], decisions: [...]}, не list.
  Тесты ниже сверяются с РЕАЛЬНЫМИ полями, не с user-stories (иначе тесты
  будут ложно-зелёными и не поймают регрессию).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import init  # noqa: E402

INIT = HERE / "init.py"
STEPS = json.dumps([{"id": "01-grounding", "title": "g"}])


def _legacy_pipeline_payload() -> dict:
    """v1-shape ground/pipeline.json: legacy-поля, которые migrate_manifest_v1_to_v2
    должен подхватить и положить в inputs/decisions нового манифеста."""
    return {
        "sources": {
            "story": "STOR-100",
            "spec": "docs/feature-pipeline/STOR-100/sdd.md",
            "spec_anchor": "REQ-0007",
        },
        "pipeline": {
            "mode": "fix",
            "mode_task": "spec-fix",
        },
        "autonomy": {
            "criticality": "high",
            "auto_max_risk": "low",
        },
    }


def _v1_manifest() -> dict:
    """Сырой манифест v1: без inputs/decisions/migration."""
    return {
        "version": 1,
        "skill": "feature-pipeline",
        "feature": "STOR-100",
        "steps": [{"id": "01-grounding", "status": "pending"}],
    }


def _write_legacy_pipeline(project: Path) -> None:
    """Кладём v1-shape pipeline.json в ground/ — источник legacy-полей."""
    ground = project / "ground"
    ground.mkdir(parents=True, exist_ok=True)
    (ground / "pipeline.json").write_text(
        json.dumps(_legacy_pipeline_payload(), ensure_ascii=False),
        encoding="utf-8",
    )


def _run_init(tmp: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INIT), "--project", str(tmp),
         "--skill", "pipeline-state", "--feature", "my-feat",
         "--steps", STEPS, *extra],
        capture_output=True, text=True,
    )


def _manifest_path(tmp: Path) -> Path:
    return tmp / "ground" / "statements" / "pipeline-state" / "my-feat" / "manifest.json"


# ── 1. migrate_manifest_v1_to_v2: заполняет inputs/decisions из pipeline.json ──
class TestMigrateV1ToV2(unittest.TestCase):
    def test_migrate_v1_to_v2_creates_inputs_and_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _write_legacy_pipeline(project)

            manifest = _v1_manifest()
            init.migrate_manifest_v1_to_v2(manifest, project)

            # version поднят до 2 (в коде — поле «version», int)
            self.assertEqual(manifest["version"], init.MANIFEST_VERSION)
            self.assertEqual(manifest["version"], 2)

            # inputs: подтянуто всё из sources.* и pipeline.mode
            self.assertEqual(manifest["inputs"]["story"], "STOR-100")
            self.assertEqual(manifest["inputs"]["spec"],
                             "docs/feature-pipeline/STOR-100/sdd.md")
            self.assertEqual(manifest["inputs"]["spec_anchor"], "REQ-0007")
            self.assertEqual(manifest["inputs"]["mode"], "fix")

            # decisions: подтянуто из pipeline.mode_task и autonomy.*
            self.assertEqual(manifest["decisions"]["mode_task"], "spec-fix")
            self.assertEqual(manifest["decisions"]["criticality"], "high")
            self.assertEqual(manifest["decisions"]["auto_max_risk"], "low")

            # migration: audit с from/to и migrated_fields
            mig = manifest.get("migration")
            self.assertIsInstance(mig, dict)
            self.assertEqual(mig["from"], "1")
            self.assertEqual(mig["to"], "2")
            mf = mig["migrated_fields"]
            self.assertIsInstance(mf, dict)
            # inputs/decisions — dict с list-ами ключей; оба должны быть непусты
            self.assertIsInstance(mf["inputs"], list)
            self.assertIsInstance(mf["decisions"], list)
            self.assertGreater(len(mf["inputs"]), 0)
            self.assertGreater(len(mf["decisions"]), 0)
            for k in ("story", "mode"):
                self.assertIn(k, mf["inputs"])
            for k in ("mode_task", "criticality", "auto_max_risk"):
                self.assertIn(k, mf["decisions"])

    # ── 2. Идемпотентность migrate ────────────────────────────────────────────
    def test_migrate_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _write_legacy_pipeline(project)

            manifest = _v1_manifest()
            init.migrate_manifest_v1_to_v2(manifest, project)
            first_inputs = dict(manifest["inputs"])
            first_decisions = dict(manifest["decisions"])
            first_migration = dict(manifest["migration"])

            # Повторный вызов не должен бросать и не должен перетирать.
            init.migrate_manifest_v1_to_v2(manifest, project)

            self.assertEqual(manifest["inputs"], first_inputs)
            self.assertEqual(manifest["decisions"], first_decisions)
            self.assertEqual(manifest["version"], 2)
            # migration остался прежним (no-op на v2-родном)
            self.assertEqual(manifest["migration"], first_migration)


# ── 3. ensure_manifest_sections: идемпотентность с заполненными данными ──────
class TestEnsureManifestSections(unittest.TestCase):
    def test_ensure_manifest_sections_idempotent(self):
        manifest = {
            "version": 2,
            "inputs": {"story": "STOR-1", "mode": "fix"},
            "decisions": {"criticality": "high", "mode_task": "spec-fix"},
        }
        init.ensure_manifest_sections(manifest)
        first_inputs = dict(manifest["inputs"])
        first_decisions = dict(manifest["decisions"])

        init.ensure_manifest_sections(manifest)

        # Данные сохранены (не сброшены в {}).
        self.assertEqual(manifest["inputs"], first_inputs)
        self.assertEqual(manifest["decisions"], first_decisions)
        self.assertEqual(manifest["inputs"]["story"], "STOR-1")
        self.assertEqual(manifest["inputs"]["mode"], "fix")
        self.assertEqual(manifest["decisions"]["criticality"], "high")
        self.assertEqual(manifest["decisions"]["mode_task"], "spec-fix")

# ── 4. CLI --inputs: per-feature входы попадают в манифест ──────────────────
class TestInitInputsFlag(unittest.TestCase):
    def test_init_with_inputs_flag(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            r = _run_init(tmp, "--inputs", '{"story":"PROJ-1"}')
            self.assertEqual(r.returncode, 0,
                             f"stdout={r.stdout}\nstderr={r.stderr}")

            mp = _manifest_path(tmp)
            self.assertTrue(mp.exists(), f"manifest not created at {mp}")
            data = json.loads(mp.read_text(encoding="utf-8"))

            # init.py пишет version=MANIFEST_VERSION (поле «version», не manifest_version)
            self.assertEqual(data["version"], 2)
            self.assertEqual(data["inputs"]["story"], "PROJ-1")


# ── 5. CLI --no-migrate: legacy pipeline.json НЕ мигрируется в манифест ─────
class TestInitNoMigrateFlag(unittest.TestCase):
    def test_init_no_migrate_flag(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            _write_legacy_pipeline(tmp)

            r = _run_init(tmp, "--no-migrate")
            self.assertEqual(r.returncode, 0,
                             f"stdout={r.stdout}\nstderr={r.stderr}")

            mp = _manifest_path(tmp)
            self.assertTrue(mp.exists(), f"manifest not created at {mp}")
            data = json.loads(mp.read_text(encoding="utf-8"))

            # Манифест создан в v2, но legacy-поля НЕ перенесены (--no-migrate).
            self.assertEqual(data["version"], 2)
            self.assertEqual(data["inputs"], {},
                             f"--no-migrate должен оставить inputs пустыми, "
                             f"получили: {data['inputs']}")
            self.assertEqual(data["decisions"], {},
                             f"--no-migrate должен оставить decisions пустыми, "
                             f"получили: {data['decisions']}")
            # migration-аудит не пишется (нет миграции).
            self.assertNotIn("migration", data)


if __name__ == "__main__":
    unittest.main()
