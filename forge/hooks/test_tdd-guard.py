#!/usr/bin/env python3
"""Smoke test for hooks/tdd-guard.py.

Раньше здесь был авто-стаб с `import tdd-guard as mod` — это SyntaxError (дефис в имени), поэтому
тест НИКОГДА не запускался (как и весь набор test_*.py хуков). Теперь: модуль грузится через
importlib (ловит регрессии синтаксиса/импорта) и проверяется fail-open на пустом stdin (общий
контракт хуков — не ронять инструмент на не-JSON входе). Поведенческое покрытие — hooks/evals/run-evals.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "tdd-guard.py"


def _seed_red_pending(project: Path) -> None:
    """Мини-пайплайн: quality.tdd on + незакрытый RED-шаг lite-red → запись src/main блокируется."""
    (project / "ground" / "statements" / "forgelite" / "f1").mkdir(parents=True)
    (project / "ground" / "pipeline.json").write_text(
        json.dumps({"quality": {"tdd": True, "tdd_integration_skip": False}}), encoding="utf-8")
    (project / "ground" / "statements" / "forgelite" / "f1" / "manifest.json").write_text(
        json.dumps({"steps": [{"id": "lite-red", "status": "pending"}]}), encoding="utf-8")


def _payload(project: Path, cwd: Path) -> str:
    return json.dumps({
        "hook_event_name": "PreToolUse", "cwd": str(cwd), "tool_name": "Write",
        "tool_input": {"file_path": str(project / "service" / "src" / "main" / "java" / "A.java"),
                       "content": "class A {}"},
    })


def _payload_path(project: Path, rel: str) -> str:
    return json.dumps({
        "hook_event_name": "PreToolUse", "cwd": str(project), "tool_name": "Write",
        "tool_input": {"file_path": str(project / rel), "content": "x"},
    })


def _seed_taskplan(project: Path, tasks: list) -> None:
    d = project / "docs" / "feature-pipeline" / "exp"
    d.mkdir(parents=True, exist_ok=True)
    (d / "task-plan.json").write_text(
        json.dumps({"feature_slug": "exp", "title": "t", "tasks": tasks}), encoding="utf-8")


def _seed_full(project: Path, steps: list, no_test_layers=None,
               config_file: str = "pipeline.json", quality_override: dict | None = None) -> None:
    """Full-манифест feature-pipeline (namespace exp) + конфиг (docs=in-repo).

    `config_file` — какой ground/{pipeline|policy}.json писать: "pipeline.json" (legacy v1)
    или "policy.json" (v2, канонический). `quality_override` — полный quality-словарь
    (для тестов, проверяющих конкретные ключи: block_jpa_test, test_layer и т.п.)."""
    d = project / "ground" / "statements" / "feature-pipeline" / "exp"
    d.mkdir(parents=True, exist_ok=True)
    if quality_override is not None:
        quality = dict(quality_override)
    else:
        quality = {"tdd": True, "tdd_integration_skip": False}
    if no_test_layers is not None:
        quality["no_test_layers"] = no_test_layers
    (project / "ground" / config_file).write_text(json.dumps({
        "quality": quality,
        "docs": {"mode": "in-repo", "docs_path": "docs", "feature_subdir": "feature-pipeline"},
    }), encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")


def _run(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


class T(unittest.TestCase):
    def test_module_loads(self):
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)          # регрессия синтаксиса/импорта
        self.assertTrue(hasattr(m, "main"))

    def test_failopen_empty_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_src_main_before_red(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_red_pending(project)
            r = subprocess.run([sys.executable, str(HOOK)], input=_payload(project, project),
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("lite-red", r.stderr)

    def test_allows_resources_migration(self):
        """Механизм A: запись в src/main/resources (liquibase changeset) НЕ гейтится RED,
        даже при незакрытом RED-шаге — ресурсы не покрываются unit-тестами."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_red_pending(project)  # lite-red pending
            r = _run(_payload_path(project, "svc/src/main/resources/db/changelog/x.xml"))
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_exempt_task_without_red_step(self):
        """Механизм B: код exempt-задачи (migration+entity, все слои в дефолте) пишется без
        04-test-<id>. Хук сам резолвит task-plan и освобождает задачу."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_taskplan(project, [
                {"id": "T1", "layers": ["migration", "entity"],
                 "artifacts": ["src/main/resources/db/changelog/x.xml", "entity/Foo.java"]},
            ])
            _seed_full(project, [{"id": "02-design", "status": "completed"},
                                 {"id": "04-build-T1", "status": "in_progress"}])
            r = _run(_payload_path(project, "svc/src/main/java/entity/Foo.java"))
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_nonexempt_task_without_red(self):
        """Fail-closed сохранён: service-задача (не exempt) с незакрытым 04-test-<id> — блок."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_taskplan(project, [
                {"id": "T2", "layers": ["service"], "artifacts": ["service/BarService.java"]},
            ])
            _seed_full(project, [{"id": "02-design", "status": "completed"},
                                 {"id": "04-test-T2", "status": "pending"},
                                 {"id": "04-build-T2", "status": "in_progress"}])
            r = _run(_payload_path(project, "svc/src/main/java/service/BarService.java"))
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("04-test-T2", r.stderr)

    def test_repository_exempt_by_default_but_gated_when_removed(self):
        """repository освобождён дефолтом; если сузить no_test_layers, RED снова требуется."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_taskplan(project, [
                {"id": "T3", "layers": ["repository"], "artifacts": ["repository/FooRepository.java"]},
            ])
            steps = [{"id": "02-design", "status": "completed"},
                     {"id": "04-test-T3", "status": "pending"},
                     {"id": "04-build-T3", "status": "in_progress"}]
            payload = _payload_path(project, "svc/src/main/java/repository/FooRepository.java")
            # дефолт (repository exempt) → пропуск
            _seed_full(project, steps)
            self.assertEqual(_run(payload).returncode, 0)
            # сузили список (только migration) → repository снова гейтится
            _seed_full(project, steps, no_test_layers=["migration"])
            r = _run(payload)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("04-test-T3", r.stderr)

    def test_blocks_when_cwd_is_subdir(self):
        """Пин m1: root = git-toplevel(cwd), а не сырой cwd. Раньше при cwd=подкаталог
        репозитория хук не находил ground/ и молча fail-open'ил (соседи по цепочке
        gate/sod/inline работали от toplevel — enforcement 'раздваивался')."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_red_pending(project)
            subprocess.run(["git", "init", "-q", str(project)], capture_output=True, timeout=30)
            subdir = project / "service" / "sub"
            subdir.mkdir(parents=True)
            r = subprocess.run([sys.executable, str(HOOK)], input=_payload(project, subdir),
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2,
                             f"cwd=подкаталог должен резолвиться в toplevel; stderr: {r.stderr}")


class PerTaskBindingWithoutInProgress(unittest.TestCase):
    """Регресс: привязка блока к КОНКРЕТНОЙ задаче держалась на статусе `in_progress`, которого
    на живых прогонах нет. Хук уходил в консервативный фолбэк «любой pending test-шаг блокирует
    src/main» — то есть код задачи T1 (её RED закрыт) блокировался незакрытым RED задачи T2.
    Ровно та ложная блокировка, которую привязка и должна была снять."""

    STEPS = [{"id": "04-test-T1", "status": "completed"},
             {"id": "04-build-T1", "status": "pending"},
             {"id": "04-test-T2", "status": "pending"},
             {"id": "04-build-T2", "status": "pending"}]
    TASKS = [{"id": "T1", "layers": ["service"], "artifacts": ["src/main/java/A.java"]},
             {"id": "T2", "layers": ["service"], "artifacts": ["src/main/java/B.java"]}]

    def test_own_red_closed_allows_write(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_full(project, self.STEPS)
            _seed_taskplan(project, self.TASKS)
            r = _run(_payload_path(project, "src/main/java/A.java"))
            self.assertEqual(r.returncode, 0,
                             f"код T1 заблокирован RED-шагом чужой задачи; stderr: {r.stderr}")

    def test_other_task_red_pending_still_blocks_its_own_file(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_full(project, self.STEPS)
            _seed_taskplan(project, self.TASKS)
            r = _run(_payload_path(project, "src/main/java/B.java"))
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("04-test-T2", r.stderr)

    def test_unknown_owner_stays_fail_closed(self):
        """Файла нет ни в одном task-plan → владельца не определить → консервативный блок."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_full(project, self.STEPS)
            _seed_taskplan(project, self.TASKS)
            r = _run(_payload_path(project, "src/main/java/Unrelated.java"))
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_v2_policy_json_test_layer_mixed_unblocks_jpa(self):
        """B3 регресс: v2-only проект (только ground/policy.json). Раньше R.pipeline_cfg(root)
        читал ТОЛЬКО pipeline.json → возвращал {} → quality.test_layer=mixed и
        quality.block_jpa_test=false из policy.json НЕ работали: @DataJpaTest в файле
        блокировался дефолтами (test_layer=unit, block_jpa=true). Теперь читаем через
        канонический load_project_config → test_layer=mixed снимает блок @DataJpaTest.

        04-test-T1:completed — чтобы пройти RED-гейт ниже и изолировать именно JPA-проверку."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_full(project, [{"id": "04-test-T1", "status": "completed"},
                                 {"id": "04-build-T1", "status": "in_progress"}],
                       config_file="policy.json",
                       quality_override={"tdd": True, "test_layer": "mixed",
                                         "block_jpa_test": True})
            payload = json.dumps({
                "hook_event_name": "PreToolUse", "cwd": str(project),
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(project / "src/main/java/Foo.java"),
                    "content": "@DataJpaTest class FooTests {}",
                },
            })
            r = _run(payload)
            self.assertEqual(r.returncode, 0,
                             f"v2 policy.json: test_layer=mixed должен снять JPA-блок; "
                             f"stderr: {r.stderr}")

    def test_v2_policy_json_block_jpa_test_false_unblocks_jpa(self):
        """B3 регресс (контртест через block_jpa_test=false): v2-only проект, test_layer
        остаётся unit, но block_jpa_test=false явно разрешает @DataJpaTest → запись проходит.

        04-test-T1:completed — чтобы пройти RED-гейт ниже и изолировать именно JPA-проверку."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_full(project, [{"id": "04-test-T1", "status": "completed"},
                                 {"id": "04-build-T1", "status": "in_progress"}],
                       config_file="policy.json",
                       quality_override={"tdd": True, "test_layer": "unit",
                                         "block_jpa_test": False})
            payload = json.dumps({
                "hook_event_name": "PreToolUse", "cwd": str(project),
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(project / "src/main/java/Foo.java"),
                    "content": "@SpringBootTest class FooTests {}",
                },
            })
            r = _run(payload)
            self.assertEqual(r.returncode, 0,
                             f"v2 policy.json: block_jpa_test=false должен снять блок; "
                             f"stderr: {r.stderr}")

    def test_v2_policy_json_tdd_false_allows_src_main(self):
        """B3 регресс: quality.tdd=false в v2-only проекте. Раньше tdd-guard с R.pipeline_cfg
        видел {} и tdd_enabled дефолтил в True → блокировал. Теперь tdd=false из policy.json
        честно выключает TDD."""
        with tempfile.TemporaryDirectory() as td:
            project = Path(td).resolve()
            _seed_full(project, [{"id": "04-build-T1", "status": "in_progress"}],
                       config_file="policy.json",
                       quality_override={"tdd": False})
            r = _run(_payload_path(project, "src/main/java/Bar.java"))
            self.assertEqual(r.returncode, 0,
                             f"v2 policy.json: tdd=false должен пропускать src/main; "
                             f"stderr: {r.stderr}")


class TestJpaGateScopedToTestSources(unittest.TestCase):
    """Задача 011: JPA-гейт стоял ДО фильтра пути и блокировал любой файл, где аннотация
    просто УПОМЯНУТА — включая docs/tech-design.md с фразой «@SpringBootTest поднимает
    контекст»."""

    def _payload(self, project: Path, rel: str, content: str) -> str:
        return json.dumps({
            "hook_event_name": "PreToolUse", "cwd": str(project), "tool_name": "Write",
            "tool_input": {"file_path": str(project / rel), "content": content},
        })

    def test_doc_mentioning_annotation_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            _seed_full(project, [{"id": "04-build-T1", "status": "pending"}],
                        quality_override={"tdd": True, "block_jpa_test": True})
            r = _run(self._payload(project, "docs/tech-design.md",
                                   "Пример: @SpringBootTest поднимает контекст"))
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_integration_test_source_still_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            _seed_full(project, [{"id": "04-build-T1", "status": "pending"}],
                        quality_override={"tdd": True, "block_jpa_test": True})
            r = _run(self._payload(project, "src/test/java/FooIT.java",
                                   "@SpringBootTest\nclass FooIT {}"))
            self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main()
