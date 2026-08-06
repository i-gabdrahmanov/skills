#!/usr/bin/env python3
"""test_e2e_smoke.py — сквозной smoke крошечной фичи через РЕАЛЬНЫЕ детерминированные гейты (P3-14).

Самая дешёвая страховка от регрессий «на стыках фаз»: один связный фикстур (pipeline.json +
task-plan + sdd + сгенерённый eval-plan + продакшн/тест .java) прогоняется через
настоящие CLI-гейты. Ловит рассинхрон контрактов между скриптами (формат eval-plan, который ждёт
traceability, и т.п.) — то, что golden-cycle (статусы шагов) не проверяет, т.к. он подкладывает
вердикты руками.

Цепочка: check_taskplan → check_sdd → build_evals → check_traceability → check_architecture →
check_tautological_tests. (Доставки/evidence в пайплайне больше нет — коммитит пользователь.)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

S = Path(__file__).resolve().parent                    # feature-pipeline/scripts
TD = S.parents[1] / "tech-design" / "scripts"          # check_taskplan/check_sdd
MD = S.parents[1] / "minor-defect-fix" / "scripts"     # check_coverage (для build_evals)
PKG = "ru.demo.app"


def _run(script: Path, *args: str):
    r = subprocess.run([sys.executable, str(script), *map(str, args)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def _w(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class E2ESmoke(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.feat = "demo"
        self.fdir = self.proj / "docs" / "feature-pipeline" / self.feat
        self.fdir.mkdir(parents=True)

        _w(self.proj / "ground" / "pipeline.json", json.dumps({
            "project": {"name": "demo", "build_system": "gradle"},
            "conventions": {"package_root": PKG},
            "quality": {"coverage_threshold": 0.8, "eval_enabled": True},
            "evidence": {"threshold": 0.6},
        }))

        self.plan = {
            "feature_slug": self.feat, "title": "Demo",
            "brd_path": "brd.md", "design_path": "tech-design.md",
            "modules": [], "coverage_threshold": 0.8, "migrations": [],
            "tasks": [
                {"id": "T1", "title": "Entity+repo", "modules": [], "layers": ["entity", "repository"],
                 "artifacts": ["src/main/java/ru/demo/app/entity/Foo.java",
                               "src/main/java/ru/demo/app/repository/FooRepository.java"],
                 "acceptance": ["Given Foo When save Then persisted"], "depends_on": [],
                 "sdd_ref": "sdd.md#t1", "rationale": "нужно для demo"},
                {"id": "T2", "title": "Service", "modules": [], "layers": ["service"],
                 "artifacts": ["src/main/java/ru/demo/app/service/FooService.java"],
                 "acceptance": ["Given req When run Then ok"], "depends_on": ["T1"],
                 "sdd_ref": "sdd.md#t2", "rationale": "сервис demo"},
            ],
        }
        _w(self.fdir / "task-plan.json", json.dumps(self.plan))
        _w(self.fdir / "sdd.md",
           "# SDD\n## T1\nGiven Foo When save Then persisted\n## T2\nGiven req When run Then ok\n")

        # продакшн-исходники (чистые, по слоям) + тест с реальным ассертом
        _w(self.proj / "src/main/java/ru/demo/app/entity/Foo.java",
           "package ru.demo.app.entity;\nclass Foo {}\n")
        _w(self.proj / "src/main/java/ru/demo/app/repository/FooRepository.java",
           "package ru.demo.app.repository;\ninterface FooRepository {}\n")
        _w(self.proj / "src/main/java/ru/demo/app/service/FooService.java",
           "package ru.demo.app.service;\nimport ru.demo.app.repository.FooRepository;\nclass FooService {}\n")
        _w(self.proj / "src/test/java/ru/demo/app/service/FooServiceTest.java",
           "package ru.demo.app.service;\nclass FooServiceTest {\n@Test void run() { assertEquals(2, 1+1); }\n}\n")

        self.tp = self.fdir / "task-plan.json"
        self.sdd = self.fdir / "sdd.md"
        self.pcfg = self.proj / "ground" / "pipeline.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_chain_consistent(self):
        # 1. task-plan валиден
        rc, out, err = _run(TD / "check_taskplan.py", self.tp, "--json")
        self.assertEqual(rc, 0, f"check_taskplan: {out}{err}")

        # 2. SDD-линковка
        rc, out, err = _run(TD / "check_sdd.py", self.tp, "--sdd", self.sdd, "--json")
        self.assertEqual(rc, 0, f"check_sdd: {out}{err}")

        # 3. eval-plan генерится из task-plan (контракт build_evals)
        ep = self.fdir / "eval-plan.json"
        rc, out, err = _run(S / "build_evals_from_design.py", self.tp,
                            "--pipeline-config", self.pcfg,
                            "--coverage-script", MD / "check_coverage.py",
                            "--out", ep)
        self.assertEqual(rc, 0, f"build_evals: {out}{err}")
        self.assertTrue(ep.exists())
        evp = json.loads(ep.read_text(encoding="utf-8"))
        self.assertEqual({e["task_id"] for e in evp["evals"]}, {"T1", "T2"})

        # 4. трассируемость: eval-plan + sdd замыкаются с task-plan (стык P2-11)
        rc, out, err = _run(S / "check_traceability.py", self.tp,
                            "--sdd", self.sdd, "--eval-plan", ep, "--json")
        self.assertEqual(rc, 0, f"check_traceability: {out}{err}")
        v = json.loads(out)
        self.assertEqual(v["status"], "pass")
        self.assertTrue(all(row["sdd_resolved"] and row["evals"] >= 1 for row in v["matrix"]))

        # 5. архитектура — чистые слои
        java = ("src/main/java/ru/demo/app/entity/Foo.java "
                "src/main/java/ru/demo/app/repository/FooRepository.java "
                "src/main/java/ru/demo/app/service/FooService.java")
        rc, out, err = _run(S / "check_architecture.py", "--root", self.proj,
                            "--changed", java, "--pipeline-config", self.pcfg, "--json")
        self.assertEqual(rc, 0, f"check_architecture: {out}{err}")

        # 6. тавтологии — тест с реальным ассертом проходит
        rc, out, err = _run(S / "check_tautological_tests.py", "--root", self.proj,
                            "--changed", "src/test/java/ru/demo/app/service/FooServiceTest.java", "--json")
        self.assertEqual(rc, 0, f"check_tautological_tests: {out}{err}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
