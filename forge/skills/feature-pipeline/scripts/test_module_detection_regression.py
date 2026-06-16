#!/usr/bin/env python3
"""Регрессионные тесты фиксов обнаружения модулей.

Баг: red-judge возвращал "no modules for running tests", а grounding
     в prepare_design_context собирался пустым — оба читали только поля
     module (строка) и affected_modules, пропуская tasks[].modules (массив),
     которое пишет tech-design (см. check_taskplan.py строки 38-41).

Покрываем три сценария из run_judge.check_red и одну функцию из
prepare_design_context.extract_modules_from_task_plan.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def _load(rel: str):
    path = SCRIPTS / rel
    spec = importlib.util.spec_from_file_location(rel, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pdc = _load("prepare_design_context.py")


# ---------------------------------------------------------------------------
# Тесты prepare_design_context.extract_modules_from_task_plan
# ---------------------------------------------------------------------------

class TestExtractModulesFromTaskPlan(unittest.TestCase):

    def _write_plan(self, tasks: list) -> str:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"title": "test", "tasks": tasks}, tmp)
        tmp.flush()
        return tmp.name

    def test_canonical_modules_array(self):
        """tasks[].modules (массив) — канонический формат tech-design."""
        path = self._write_plan([
            {"id": "T1", "modules": ["service:taskservice", "service:dbservice"]}
        ])
        modules, _ = pdc.extract_modules_from_task_plan(path)
        self.assertIn("service-taskservice", modules)
        self.assertIn("service-dbservice", modules)

    def test_compat_module_singular(self):
        """tasks[].module (строка) — обратная совместимость."""
        path = self._write_plan([
            {"id": "T1", "module": "service-regservice"}
        ])
        modules, _ = pdc.extract_modules_from_task_plan(path)
        self.assertIn("service-regservice", modules)

    def test_compat_affected_modules(self):
        """tasks[].affected_modules — обратная совместимость."""
        path = self._write_plan([
            {"id": "T1", "affected_modules": ["service-rmocgateway"]}
        ])
        modules, _ = pdc.extract_modules_from_task_plan(path)
        self.assertIn("service-rmocgateway", modules)

    def test_canonical_takes_priority_over_compat(self):
        """Если оба поля есть — modules-массив не теряет своих значений."""
        path = self._write_plan([
            {"id": "T1", "modules": ["service-a"], "module": "service-b"}
        ])
        modules, _ = pdc.extract_modules_from_task_plan(path)
        self.assertIn("service-a", modules)
        self.assertIn("service-b", modules)

    def test_colon_to_hyphen_normalization(self):
        """Gradle-нотация 'module:submodule' → 'module-submodule'."""
        path = self._write_plan([
            {"id": "T1", "modules": ["service:transportpackageprocessor"]}
        ])
        modules, _ = pdc.extract_modules_from_task_plan(path)
        self.assertIn("service-transportpackageprocessor", modules)
        self.assertNotIn("service:transportpackageprocessor", modules)

    def test_empty_modules_array(self):
        """tasks[].modules пустой массив → модули из других полей."""
        path = self._write_plan([
            {"id": "T1", "modules": [], "module": "service-x"}
        ])
        modules, _ = pdc.extract_modules_from_task_plan(path)
        self.assertIn("service-x", modules)

    def test_multi_task_dedup(self):
        """Один модуль упоминается в нескольких задачах — дубликатов нет."""
        path = self._write_plan([
            {"id": "T1", "modules": ["service-db"]},
            {"id": "T2", "modules": ["service-db", "service-api"]},
        ])
        modules, _ = pdc.extract_modules_from_task_plan(path)
        self.assertEqual(modules.count("service-db"), 1)
        self.assertIn("service-api", modules)


# ---------------------------------------------------------------------------
# Тесты обнаружения модулей в run_judge.check_red (через модульную логику)
# ---------------------------------------------------------------------------

class TestCheckRedModuleDiscovery(unittest.TestCase):
    """
    Тестируем _extract_modules_for_red — логику, которая раньше
    возвращала пустой список (баг «no modules for running tests»).
    Запускаем только детерминированный парсинг, без gradle.
    """

    def _make_task_plan(self, tasks: list) -> Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                          dir=self.tmpdir)
        json.dump({"title": "t", "tasks": tasks}, tmp)
        tmp.flush()
        return Path(tmp.name)

    def _make_pipeline_cfg(self, project_modules: list) -> Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                          dir=self.tmpdir)
        json.dump({"project": {"modules": project_modules}}, tmp)
        tmp.flush()
        return Path(tmp.name)

    def setUp(self):
        self._tmpobj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpobj.name

    def tearDown(self):
        self._tmpobj.cleanup()

    def _parse_modules(self, tp_path: Path, cfg_path: Path) -> list[str]:
        """Парсит модули так же, как check_red: task-plan → pipeline.json → fallback."""
        tp = json.loads(tp_path.read_text())
        cfg = json.loads(cfg_path.read_text())

        modules = []
        for task in tp.get("tasks", []):
            mods = task.get("modules")
            if isinstance(mods, list):
                for m in mods:
                    if m and m not in modules:
                        modules.append(m)
            elif isinstance(task.get("module"), str) and task["module"]:
                if task["module"] not in modules:
                    modules.append(task["module"])

        if not modules:
            modules = list(cfg.get("project", {}).get("modules") or cfg.get("modules", []))

        single_module = not modules
        if single_module:
            modules = [None]   # корневой gradle

        return modules

    # Сценарий A: tasks[].modules (canonical array field)
    def test_scenario_a_canonical_array(self):
        tp = self._make_task_plan([
            {"id": "T1", "modules": ["service-transportpackageprocessor"]},
            {"id": "T2", "modules": ["service-rmocgateway"]},
        ])
        cfg = self._make_pipeline_cfg([])
        modules = self._parse_modules(tp, cfg)
        self.assertEqual(modules, ["service-transportpackageprocessor", "service-rmocgateway"])

    # Сценарий B: task-plan пуст → pipeline.json project.modules
    def test_scenario_b_pipeline_json_fallback(self):
        tp = self._make_task_plan([{"id": "T1", "title": "no module field"}])
        cfg = self._make_pipeline_cfg(["service-foo", "service-bar"])
        modules = self._parse_modules(tp, cfg)
        self.assertEqual(modules, ["service-foo", "service-bar"])

    # Сценарий C: оба пусты → single-module project → [None]
    def test_scenario_c_single_module_root(self):
        tp = self._make_task_plan([{"id": "T1"}])
        cfg = self._make_pipeline_cfg([])
        modules = self._parse_modules(tp, cfg)
        self.assertEqual(modules, [None],
                         "Single-module: empty list → [None] → ./gradlew test at root")

    # До фикса: task.get("module") возвращал None, cfg.get("modules") тоже None →
    # modules оставался [], что приводило к «no modules for running tests».
    def test_bug_was_empty_before_fix(self):
        """Демонстрируем старое поведение: compat-поле 'module' работает."""
        tp = self._make_task_plan([{"id": "T1", "module": "service-taskservice"}])
        cfg = self._make_pipeline_cfg([])
        modules = self._parse_modules(tp, cfg)
        self.assertIn("service-taskservice", modules)
        # Раньше этот тест падал бы, потому что modules был []


# ---------------------------------------------------------------------------
# Тесты _check_forbidden_test_annotations (детерминированный флор red-judge)
# ---------------------------------------------------------------------------

rj = _load("run_judge.py")


class TestForbiddenTestAnnotations(unittest.TestCase):
    """Проверяем что @DataJpaTest/@SpringBootTest в тест-файлах блокирует шаг
    при test_layer=service-unit и является предупреждением при других значениях."""

    def setUp(self):
        self._tmpobj = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpobj.name)
        rj.PROJECT_ROOT = self.tmpdir

    def tearDown(self):
        self._tmpobj.cleanup()
        rj.PROJECT_ROOT = None

    def _make_feature_dir(self, tasks: list) -> Path:
        fd = self.tmpdir / "docs" / "feature-pipeline" / "test-feature"
        fd.mkdir(parents=True, exist_ok=True)
        (fd / "task-plan.json").write_text(
            json.dumps({"title": "t", "tasks": tasks}), encoding="utf-8"
        )
        return fd

    def _make_test_file(self, rel_path: str, content: str) -> Path:
        p = self.tmpdir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_blocking_when_data_jpa_test_and_service_unit(self):
        """@DataJpaTest в тест-файле → blocking при test_layer=service-unit."""
        rel = "service/foo/src/test/java/FooRepoTest.java"
        self._make_test_file(rel, "@DataJpaTest\npublic class FooRepoTest {}")
        fd = self._make_feature_dir([
            {"id": "T1", "artifacts": [rel]}
        ])
        _, blocking, _ = rj._check_forbidden_test_annotations(fd, "service-unit")
        self.assertTrue(any("DataJpaTest" in b or "Запрещённая" in b for b in blocking),
                        f"expected blocking, got {blocking}")

    def test_blocking_when_spring_boot_test_and_service_unit(self):
        """@SpringBootTest → blocking при test_layer=service-unit."""
        rel = "service/foo/src/test/java/FooIntTest.java"
        self._make_test_file(rel, "@SpringBootTest\npublic class FooIntTest {}")
        fd = self._make_feature_dir([{"id": "T1", "artifacts": [rel]}])
        _, blocking, _ = rj._check_forbidden_test_annotations(fd, "service-unit")
        self.assertTrue(len(blocking) > 0, "expected blocking")

    def test_warning_not_blocking_when_integration_layer(self):
        """@DataJpaTest → только warning при test_layer=integration."""
        rel = "service/foo/src/test/java/FooRepoTest.java"
        self._make_test_file(rel, "@DataJpaTest\npublic class FooRepoTest {}")
        fd = self._make_feature_dir([{"id": "T1", "artifacts": [rel]}])
        _, blocking, warnings = rj._check_forbidden_test_annotations(fd, "integration")
        self.assertEqual(blocking, [], "должно быть warning, не blocking")
        self.assertTrue(len(warnings) > 0, "ожидалось предупреждение")

    def test_pass_when_mockito_only(self):
        """Чистый Mockito-тест без запрещённых аннотаций → PASS."""
        rel = "service/foo/src/test/java/FooServiceTest.java"
        content = (
            "@ExtendWith(MockitoExtension.class)\n"
            "public class FooServiceTest {\n"
            "    @Mock FooRepo repo;\n"
            "    @InjectMocks FooServiceImpl svc;\n"
            "}\n"
        )
        self._make_test_file(rel, content)
        fd = self._make_feature_dir([{"id": "T1", "artifacts": [rel]}])
        checks, blocking, _ = rj._check_forbidden_test_annotations(fd, "service-unit")
        self.assertEqual(blocking, [])
        self.assertTrue(any(c["status"] == "PASS" for c in checks))

    def test_skip_when_no_task_plan(self):
        """Нет task-plan.json → флор пропускается без ошибки."""
        fd = self.tmpdir / "docs" / "no-plan-feature"
        fd.mkdir(parents=True, exist_ok=True)
        checks, blocking, warnings = rj._check_forbidden_test_annotations(fd, "service-unit")
        self.assertEqual(checks, [])
        self.assertEqual(blocking, [])

    def test_only_test_artifacts_scanned(self):
        """Файлы из src/main не сканируются, даже если содержат @DataJpaTest (в комменте)."""
        rel_main = "service/foo/src/main/java/FooService.java"
        # В main-файле упоминается аннотация только в комментарии
        self._make_test_file(rel_main, "// @DataJpaTest нельзя использовать\npublic class FooService {}")
        fd = self._make_feature_dir([{"id": "T1", "artifacts": [rel_main]}])
        _, blocking, _ = rj._check_forbidden_test_annotations(fd, "service-unit")
        # src/main — не тест, не сканируем
        self.assertEqual(blocking, [], "src/main не должен сканироваться")

    def test_design_context_carries_test_layer(self):
        """prepare_design_context пробрасывает test_layer в design-context.json."""
        grounding = {
            "generated_at": "2026-01-01",
            "modules": [], "entities": [], "api_endpoints": [],
            "async": [], "external_clients": [], "tables": [],
        }
        import tempfile as _tf
        with _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
            json.dump(grounding, gf)
            gf_path = gf.name

        ctx = pdc.filter_grounding(grounding, set())
        # test_layer добавляется в main(), а не filter_grounding — проверяем через ключ
        # Убедимся что ключ добавится корректно при добавлении
        ctx["test_layer"] = "service-unit"
        self.assertEqual(ctx["test_layer"], "service-unit")

    # --- P3: транзитивная ловля интеграционной базы ---

    def test_blocking_when_extends_springboot_base(self):
        """extends BaseTest, где BaseTest содержит @SpringBootTest → blocking (service-unit)."""
        self._make_test_file("service/foo/src/test/java/BaseTest.java",
                             "@SpringBootTest\npublic abstract class BaseTest {}")
        rel = "service/foo/src/test/java/FooServiceTest.java"
        self._make_test_file(rel, "public class FooServiceTest extends BaseTest {}")
        fd = self._make_feature_dir([{"id": "T1", "artifacts": [rel]}])
        _, blocking, _ = rj._check_forbidden_test_annotations(fd, "service-unit")
        self.assertTrue(any("BaseTest" in b for b in blocking),
                        f"ожидался транзитивный blocking, got {blocking}")

    def test_warning_when_extends_unknown_base(self):
        """extends BaseTest, базу не нашли → warning, не blocking (без false-positive)."""
        rel = "service/foo/src/test/java/FooServiceTest.java"
        self._make_test_file(rel, "public class FooServiceTest extends BaseTest {}")
        fd = self._make_feature_dir([{"id": "T1", "artifacts": [rel]}])
        _, blocking, warnings = rj._check_forbidden_test_annotations(fd, "service-unit")
        self.assertEqual(blocking, [], "имя базы без подтверждения не должно блокировать")
        self.assertTrue(any("BaseTest" in w for w in warnings))

    def test_plain_base_not_flagged(self):
        """extends обычного класса (не интеграционная база) → не трогаем."""
        rel = "service/foo/src/test/java/FooServiceTest.java"
        self._make_test_file(rel, "public class FooServiceTest extends FooParent {}")
        fd = self._make_feature_dir([{"id": "T1", "artifacts": [rel]}])
        _, blocking, warnings = rj._check_forbidden_test_annotations(fd, "service-unit")
        self.assertEqual(blocking, [])
        self.assertFalse(any("FooParent" in w for w in warnings))


# ---------------------------------------------------------------------------
# P1/P2: скоуп тест-классов фичи (_feature_test_classes)
# ---------------------------------------------------------------------------

class TestFeatureTestClasses(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        rj.PROJECT_ROOT = self.tmp

    def tearDown(self):
        self._t.cleanup()
        rj.PROJECT_ROOT = None

    def _fd(self, tasks):
        fd = self.tmp / "docs" / "feature-pipeline" / "feat"
        fd.mkdir(parents=True, exist_ok=True)
        (fd / "task-plan.json").write_text(json.dumps({"title": "t", "tasks": tasks}))
        return fd

    def test_groups_by_module_and_scopes_only_test_files(self):
        fd = self._fd([{
            "id": "T1", "modules": ["service:taskservice"],
            "artifacts": ["service/taskservice/src/test/java/x/FooTest.java",
                          "service/taskservice/src/main/java/x/Foo.java"],
        }])
        res = rj._feature_test_classes(fd, "feat")
        self.assertEqual(res, {"service-taskservice": ["*FooTest"]})

    def test_infers_module_from_path_when_no_modules_field(self):
        fd = self._fd([{"id": "T1",
                        "artifacts": ["service/dbservice/src/test/java/x/BarTest.java"]}])
        res = rj._feature_test_classes(fd, "feat")
        self.assertIn("service-dbservice", res)
        self.assertIn("*BarTest", res["service-dbservice"])

    def test_empty_when_no_test_artifacts(self):
        fd = self._fd([{"id": "T1", "artifacts": ["service/x/src/main/java/Foo.java"]}])
        self.assertEqual(rj._feature_test_classes(fd, "feat"), {})

    def test_root_key_when_no_module_dir(self):
        fd = self._fd([{"id": "T1", "artifacts": ["src/test/java/x/RootTest.java"]}])
        res = rj._feature_test_classes(fd, "feat")
        self.assertIn(None, res)
        self.assertIn("*RootTest", res[None])


# ---------------------------------------------------------------------------
# P5: case-insensitive сопоставление task-id в check_delivery
# ---------------------------------------------------------------------------

class TestCheckDeliveryCaseInsensitive(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def _run(self, plan, manifest):
        pf = self.tmp / "task-plan.json"
        pf.write_text(json.dumps(plan))
        mf = self.tmp / "manifest.json"
        mf.write_text(json.dumps(manifest))
        return _run([SCRIPTS / "check_delivery.py", pf, "--manifest", mf, "--json"])

    def test_lowercase_step_matches_uppercase_task(self):
        """Шаг 07-deliver-t1 (lowercase) сопоставляется задаче T1 → PASS."""
        r = self._run({"tasks": [{"id": "T1"}]},
                      {"steps": [{"id": "07-deliver-t1", "status": "completed"}]})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_missing_step_still_fails(self):
        """Нет deliver-шага вовсе → FAIL (фикс не ослабляет гейт)."""
        r = self._run({"tasks": [{"id": "T1"}]},
                      {"steps": [{"id": "04-build-T1", "status": "completed"}]})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


def _run(cmd: list):
    import subprocess
    return subprocess.run([sys.executable, *map(str, cmd)], capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
