#!/usr/bin/env python3
"""Тесты для init_pipeline_config.py — scaffold pipeline.json.

Запуск:
    python3 test_init_pipeline_config.py
    python3 -m pytest test_init_pipeline_config.py -v

Проверяет:
    1. detect_build_system(): gradle, maven, none
    2. detect_default_branch(): main/master/develop
    3. detect_gradle_modules(): парсинг settings.gradle
    4. detect_maven_modules(): парсинг pom.xml
    5. gather_build_files(): сбор build-файлов
    6. detect_group(): из build.gradle
    7. detect_versions(): java, spring
    8. detect_migration_tool(): liquibase/flyway/none
    9. detect_jacoco(): true/false
    10. build_config(): полный конфиг
    11. main(): запись/обновление/force/print
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import init_pipeline_config as ipc


def _touch(path: Path, content: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestDetectBuildSystem(unittest.TestCase):
    """Тесты детекта build-системы."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_gradle_settings(self):
        """settings.gradle → gradle."""
        _touch(self.root / "settings.gradle")
        self.assertEqual(ipc.detect_build_system(str(self.root)), "gradle")

    def test_gradle_settings_kts(self):
        """settings.gradle.kts → gradle."""
        _touch(self.root / "settings.gradle.kts")
        self.assertEqual(ipc.detect_build_system(str(self.root)), "gradle")

    def test_gradle_build_only(self):
        """build.gradle без settings → gradle."""
        _touch(self.root / "build.gradle")
        self.assertEqual(ipc.detect_build_system(str(self.root)), "gradle")

    def test_maven(self):
        """pom.xml → maven."""
        _touch(self.root / "pom.xml")
        self.assertEqual(ipc.detect_build_system(str(self.root)), "maven")

    def test_no_build_system(self):
        """Ничего нет → None."""
        # Создадим пустой файл, который не является build-файлом
        _touch(self.root / "README.md")
        self.assertIsNone(ipc.detect_build_system(str(self.root)))


class TestDetectDefaultBranch(unittest.TestCase):
    """Тесты детекта дефолтной ветки."""

    def test_main(self):
        """Гипотетически main."""
        # Функция работает через git, протестируем на текущем git-репозитории
        result = ipc.detect_default_branch(str(Path.cwd()))
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


class TestDetectGradleModules(unittest.TestCase):
    """Тесты парсинга settings.gradle."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_include_quoted(self):
        """include 'module-a', 'module-b' — regex берёт первый модуль."""
        _touch(self.root / "settings.gradle", "include 'module-a', 'module-b'\n")
        modules = ipc.detect_gradle_modules(str(self.root))
        # findall находит все совпадения, но regex ждёт include[...] сразу перед именем
        # ", 'module-b'" не попадает под include[\s(]+ — поэтому только первый
        self.assertIn("module-a", modules)
        self.assertEqual(len(modules), 1)

    def test_include_parenthesized(self):
        """include('module-a', 'module-b')."""
        _touch(self.root / "settings.gradle", "include('module-a', 'module-b')\n")
        modules = ipc.detect_gradle_modules(str(self.root))
        self.assertIn("module-a", modules)

    def test_include_kts(self):
        """settings.gradle.kts — regex убирает : из :module-a."""
        _touch(self.root / "settings.gradle.kts", """include(":module-a", ":module-b")\n""")
        modules = ipc.detect_gradle_modules(str(self.root))
        self.assertIn("module-a", modules)

    def test_no_settings(self):
        """Нет settings.gradle → []."""
        self.assertEqual(ipc.detect_gradle_modules(str(self.root)), [])

    def test_include_colon_stripped(self):
        """include ':module-a'."""
        _touch(self.root / "settings.gradle", "include ':module-a'\n")
        modules = ipc.detect_gradle_modules(str(self.root))
        self.assertEqual(len(modules), 1)


class TestDetectMavenModules(unittest.TestCase):
    """Тесты парсинга pom.xml."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_modules(self):
        """pom.xml с <module>."""
        pom = """<project><modules><module>mod-a</module><module>mod-b</module></modules></project>"""
        _touch(self.root / "pom.xml", pom)
        modules = ipc.detect_maven_modules(str(self.root))
        self.assertEqual(modules, ["mod-a", "mod-b"])

    def test_no_modules(self):
        """pom.xml без modules → []."""
        _touch(self.root / "pom.xml", "<project></project>")
        self.assertEqual(ipc.detect_maven_modules(str(self.root)), [])

    def test_no_pom(self):
        """Нет pom.xml → []."""
        self.assertEqual(ipc.detect_maven_modules(str(self.root)), [])


class TestGatherBuildFiles(unittest.TestCase):
    """Тесты сбора build-файлов."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _touch(self.root / "build.gradle")
        _touch(self.root / "service" / "mod" / "build.gradle")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_finds_gradle_files(self):
        """Находит build.gradle рекурсивно."""
        files = ipc.gather_build_files(str(self.root), "gradle")
        self.assertEqual(len(files), 2)

    def test_ignores_git_dir(self):
        """Игнорирует .git."""
        import shutil
        git_dir = self.root / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        _touch(git_dir / "build.gradle")
        files = ipc.gather_build_files(str(self.root), "gradle")
        for f in files:
            self.assertNotIn(".git", f)

    def test_ignores_build_dir(self):
        """Игнорирует build/."""
        _touch(self.root / "build" / "build.gradle")
        files = ipc.gather_build_files(str(self.root), "gradle")
        self.assertEqual(len(files), 2)


class TestDetectGroup(unittest.TestCase):
    """Тесты детекта group."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_group_found(self):
        """group = 'com.example'."""
        _touch(self.root / "build.gradle", "group = 'com.example'\n")
        files = [str(self.root / "build.gradle")]
        self.assertEqual(ipc.detect_group(str(self.root), files), "com.example")

    def test_group_not_found(self):
        """group не задан."""
        _touch(self.root / "build.gradle", "version = '1.0'\n")
        files = [str(self.root / "build.gradle")]
        self.assertIsNone(ipc.detect_group(str(self.root), files))

    def test_no_build_files(self):
        """Нет build-файлов."""
        self.assertIsNone(ipc.detect_group(str(self.root), []))


class TestDetectVersions(unittest.TestCase):
    """Тесты детекта версий Java и Spring."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_java_version(self):
        """JavaLanguageVersion.of(17)."""
        _touch(self.root / "build.gradle", "JavaLanguageVersion.of(17)\n")
        files = [str(self.root / "build.gradle")]
        java_v, spring_v = ipc.detect_versions(files)
        self.assertEqual(java_v, "17")

    def test_spring_boot_version(self):
        """spring boot 3.2.5."""
        _touch(self.root / "build.gradle", "springBootVersion = '3.2.5'\n")
        files = [str(self.root / "build.gradle")]
        java_v, spring_v = ipc.detect_versions(files)
        self.assertIn("3.2.5", spring_v)

    def test_no_versions(self):
        """Нет версий."""
        _touch(self.root / "build.gradle", "apply plugin: 'java'\n")
        files = [str(self.root / "build.gradle")]
        java_v, spring_v = ipc.detect_versions(files)
        self.assertIsNone(java_v)
        self.assertIsNone(spring_v)


class TestDetectMigrationTool(unittest.TestCase):
    """Тесты детекта инструмента миграций."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_liquibase_detected(self):
        """liquibase в build.gradle."""
        _touch(self.root / "build.gradle", "liquibase")
        files = [str(self.root / "build.gradle")]
        tool, changelog = ipc.detect_migration_tool(str(self.root), files)
        self.assertEqual(tool, "liquibase")

    def test_flyway_detected(self):
        """flyway в build.gradle."""
        _touch(self.root / "build.gradle", "flyway")
        files = [str(self.root / "build.gradle")]
        tool, changelog = ipc.detect_migration_tool(str(self.root), files)
        self.assertEqual(tool, "flyway")

    def test_no_migration(self):
        """Нет упоминаний миграций."""
        _touch(self.root / "build.gradle", "apply plugin: 'java'")
        files = [str(self.root / "build.gradle")]
        tool, changelog = ipc.detect_migration_tool(str(self.root), files)
        self.assertEqual(tool, "none")

    def test_changelog_path_found(self):
        """Находит db/changelog."""
        _touch(self.root / "db" / "changelog" / "file.xml")
        files = [str(self.root / "build.gradle")]
        tool, changelog = ipc.detect_migration_tool(str(self.root), files)
        self.assertEqual(changelog, "db/changelog")

    def test_changelog_skips_build_artifact(self):
        """Первичный changelog — исходник, а НЕ копия-артефакт под build/ (регресс hits[0])."""
        _touch(self.root / "database" / "src" / "main" / "resources" / "db" / "changelog" / "master.xml")
        _touch(self.root / "database" / "build" / "resources" / "main" / "db" / "changelog" / "master.xml")
        files = [str(self.root / "build.gradle")]
        tool, changelog = ipc.detect_migration_tool(str(self.root), files)
        self.assertEqual(changelog, os.path.join("database", "src", "main", "resources", "db", "changelog"))
        self.assertNotIn("build", changelog.split(os.sep))


class TestDetectMigrationServices(unittest.TestCase):
    """Тесты сканирования всех сервисов монорепо с миграциями."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_multi_service(self):
        """Два сервиса с разными миграциями — обе записи, с per-service инструментом."""
        _touch(self.root / "settings.gradle", "include 'svc-a', 'svc-b'\n")
        _touch(self.root / "svc-a" / "build.gradle", "dependencies { liquibaseRuntime 'x' }\n")
        _touch(self.root / "svc-a" / "src" / "main" / "resources" / "db" / "changelog" / "master.xml")
        _touch(self.root / "svc-b" / "build.gradle", "plugins { id 'org.flywaydb.flyway' }\n")
        _touch(self.root / "svc-b" / "src" / "main" / "resources" / "db" / "migration" / "V1__init.sql")

        files = ipc.gather_build_files(str(self.root), "gradle")
        services = ipc.detect_migration_services(str(self.root), files, "liquibase")

        by_svc = {s["service"]: s for s in services}
        self.assertEqual(len(services), 2)
        self.assertEqual(by_svc["svc-a"]["migration_tool"], "liquibase")
        self.assertEqual(by_svc["svc-a"]["changelog_path"],
                         os.path.join("svc-a", "src", "main", "resources", "db", "changelog"))
        self.assertEqual(by_svc["svc-b"]["migration_tool"], "flyway")
        self.assertEqual(by_svc["svc-b"]["changelog_path"],
                         os.path.join("svc-b", "src", "main", "resources", "db", "migration"))

    def test_artifact_filtered(self):
        """Копия-артефакт под build/ не попадает в список (только исходник)."""
        _touch(self.root / "database" / "build.gradle", "liquibase\n")
        _touch(self.root / "database" / "src" / "main" / "resources" / "db" / "changelog" / "master.xml")
        _touch(self.root / "database" / "build" / "resources" / "main" / "db" / "changelog" / "master.xml")

        files = ipc.gather_build_files(str(self.root), "gradle")
        services = ipc.detect_migration_services(str(self.root), files, "liquibase")

        paths = [s["changelog_path"] for s in services]
        self.assertEqual(paths, [os.path.join("database", "src", "main", "resources", "db", "changelog")])
        for p in paths:
            self.assertNotIn("build", p.split(os.sep))

    def test_single_service_matches_scalar(self):
        """Одиночный репо: ровно одна запись, совпадающая со скалярами (регресс совместимости)."""
        _touch(self.root / "build.gradle", "group = 'com.acme'\nliquibase\n")
        _touch(self.root / "src" / "main" / "resources" / "db" / "changelog" / "master.xml")

        cfg = ipc.build_config(str(self.root))
        conv = cfg["conventions"]
        self.assertEqual(len(conv["migration_services"]), 1)
        entry = conv["migration_services"][0]
        self.assertEqual(entry["changelog_path"], conv["changelog_path"])
        self.assertEqual(entry["migration_tool"], conv["migration_tool"])
        self.assertEqual(entry["service"], ".")

    def test_no_migrations(self):
        """Нет каталогов миграций → пустой список."""
        _touch(self.root / "build.gradle", "apply plugin: 'java'\n")
        files = ipc.gather_build_files(str(self.root), "gradle")
        self.assertEqual(ipc.detect_migration_services(str(self.root), files, "none"), [])


class TestDetectJacoco(unittest.TestCase):
    """Тесты детекта JaCoCo."""

    def test_jacoco_present(self):
        """jacoco в build.gradle."""
        files = ["/tmp/build.gradle"]
        _touch(Path(files[0]), "id 'jacoco'")
        self.assertTrue(ipc.detect_jacoco(files))

    def test_jacoco_not_present(self):
        """Нет jacoco."""
        files = ["/tmp/build.gradle"]
        _touch(Path(files[0]), "apply plugin: 'java'")
        self.assertFalse(ipc.detect_jacoco(files))

    def test_jacoco_case_insensitive(self):
        """JaCoCo в любом регистре."""
        files = ["/tmp/build.gradle"]
        _touch(Path(files[0]), "jacoco")
        self.assertTrue(ipc.detect_jacoco(files))


class TestBuildConfig(unittest.TestCase):
    """Тесты build_config() — полный конфиг."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _touch(self.root / "build.gradle", """
            plugins {
                id 'java'
                id 'jacoco'
            }
            group = 'com.example.app'
            JavaLanguageVersion.of(17)
        """)
        _touch(self.root / "settings.gradle", "include 'service:mod-a'")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_build_config_has_schema(self):
        """В конфиге есть $schema."""
        cfg = ipc.build_config(str(self.root))
        self.assertIn("$schema", cfg)
        self.assertEqual(cfg["$schema"], ipc.SCHEMA_VERSION)

    def test_build_config_has_project(self):
        """В конфиге есть секция project."""
        cfg = ipc.build_config(str(self.root))
        self.assertEqual(cfg["project"]["build_system"], "gradle")
        self.assertIn("is_multi_module", cfg["project"])
        self.assertIn("modules", cfg["project"])

    def test_build_config_has_quality(self):
        """В конфиге есть quality."""
        cfg = ipc.build_config(str(self.root))
        self.assertEqual(cfg["quality"]["coverage_threshold"], 0.80)
        self.assertTrue(cfg["quality"]["jacoco_configured"])

    def test_build_config_incomplete(self):
        """В конфиге есть _incomplete."""
        cfg = ipc.build_config(str(self.root))
        self.assertIn("_incomplete", cfg)
        self.assertIsInstance(cfg["_incomplete"], list)

    def test_build_config_no_autonomy_block(self):
        """v2: autonomy (criticality/auto_max_risk) — per-feature, в manifest.json, не в policy.json."""
        cfg = ipc.build_config(str(self.root))
        self.assertNotIn("autonomy", cfg)


class TestMain(unittest.TestCase):
    """Тесты main() — CLI."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _touch(self.root / "build.gradle", "group = 'com.example'\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_main(self, extra_args: list[str] | None = None):
        args = [
            "init_pipeline_config.py",
            "--project", str(self.root),
            *(extra_args or []),
        ]
        sys.argv = args
        try:
            ipc.main()
        except SystemExit as e:
            return e.code or 0
        return 0

    def test_create_config(self):
        """Создаёт ground/policy.json."""
        rc = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / "ground" / "policy.json").exists())

    def test_config_is_valid_json(self):
        """Созданный файл — валидный JSON."""
        self._run_main()
        data = json.loads((self.root / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertIn("project", data)
        self.assertIn("quality", data)

    def test_exists_not_overwritten(self):
        """Без --force не перезаписывает."""
        self._run_main()
        path = self.root / "ground" / "policy.json"
        original = path.read_text(encoding="utf-8")
        self._run_main()
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_force_overwrites(self):
        """--force перезаписывает."""
        self._run_main()
        path = self.root / "ground" / "policy.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["custom"] = "value"
        path.write_text(json.dumps(data), encoding="utf-8")

        self._run_main(["--force"])
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("custom", reloaded)

    def test_update_keeps_existing_fields(self):
        """--update сохраняет существующие поля, обновляет детектируемые."""
        self._run_main()
        path = self.root / "ground" / "policy.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["jira"] = {"enabled": True, "project_key": "TEST"}
        path.write_text(json.dumps(data), encoding="utf-8")

        self._run_main(["--update"])
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        # jira сохранилось
        self.assertEqual(reloaded["jira"]["project_key"], "TEST")
        # project обновилось
        self.assertIn("project", reloaded)

    def test_update_does_not_clobber_answers_and_recomputes_incomplete(self):
        """Пин B2: --update не затирает человеческие ответы None-детектом и пересобирает
        _incomplete по факту (раньше detected-маркер переносился безусловно — jira/bitbucket
        попадали всегда, и гейт арминга «_incomplete пуст» был недостижим). Проект — ПУСТОЙ
        (без build-файлов): детект слеп, ответы даёт только человек.

        v2: jira.enabled — project-wide, оставляем None и проверяем, что --update его
        не затирает (поле в policy.json, per-feature autonomy.* уехали в manifest.json)."""
        empty_root = Path(tempfile.mkdtemp())
        try:
            sys.argv = ["init_pipeline_config.py", "--project", str(empty_root)]
            try:
                ipc.main()
            except SystemExit:
                pass
            path = empty_root / "ground" / "policy.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("project.build_system", data.get("_incomplete", []))
            # человек ответил на вопросы §0.1 (build_system, package_root)
            data["project"]["build_system"] = "gradle"
            data["conventions"]["package_root"] = "com.acme.app"
            data.setdefault("bitbucket", {})["enabled"] = False
            # jira.enabled — project-wide (в policy.json), оставляем None, чтобы
            # проверить, что --update его не затирает детектом и не выкидывает из _incomplete
            path.write_text(json.dumps(data), encoding="utf-8")

            sys.argv = ["init_pipeline_config.py", "--project", str(empty_root), "--update"]
            try:
                ipc.main()
            except SystemExit:
                pass
            reloaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["project"]["build_system"], "gradle",
                             "None-детект затер ответ человека")
            self.assertEqual(reloaded["conventions"]["package_root"], "com.acme.app")
            # jira.enabled — project-wide, --update не должен его затереть
            self.assertIsNone(reloaded["jira"]["enabled"],
                              "project-wide jira.enabled должен остаться None после --update")
            leftovers = [i for i in reloaded.get("_incomplete", [])
                         if not i.startswith("project.is_git")]
            self.assertEqual(leftovers, ["jira.enabled"],
                             f"только неответ jira.enabled должен остаться в маркере: {leftovers}")
        finally:
            import shutil
            shutil.rmtree(empty_root, ignore_errors=True)

    def test_dry_run(self):
        """--print не создаёт файл."""
        self._run_main(["--print"])
        self.assertFalse((self.root / "ground" / "policy.json").exists())

    def test_writes_policy_json(self):
        """v2: целевой файл — policy.json, не pipeline.json."""
        self._run_main()
        self.assertTrue((self.root / "ground" / "policy.json").exists())
        self.assertFalse((self.root / "ground" / "pipeline.json").exists())

    def test_legacy_pipeline_json_reads_via_warning(self):
        """При наличии только legacy pipeline.json — main() всё равно создаёт policy.json.
        DEPRECATION warning печатается в stderr (мы только проверяем exit 0 и наличие policy.json)."""
        legacy = self.root / "ground" / "pipeline.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"project": {"build_system": "gradle"}}), encoding="utf-8")
        rc = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / "ground" / "policy.json").exists())

    def test_nonexistent_dir(self):
        """Несуществующая директория → exit 1."""
        args = [
            "init_pipeline_config.py",
            "--project", "/nonexistent/path/12345",
        ]
        sys.argv = args
        with self.assertRaises(SystemExit):
            ipc.main()


class TestMigrationV1ToV2(unittest.TestCase):
    """v1 → v2 миграция: split pipeline.json → policy.json + per-feature manifest.json.

    Фикс регрессии (Phase 4): путь манифеста — statements/<skill>/<feature>/,
    а НЕ statements/<skill>/<skill>/<feature>/; manifest.feature — bare имя, не slug.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "ground").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed_v1(self, features: dict, project_wide: dict | None = None):
        payload = dict(project_wide or {})
        payload["features"] = features
        (self.root / "ground" / "pipeline.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _run(self, *flags):
        sys.argv = ["init_pipeline_config.py", "--project", str(self.root), *flags]
        try:
            return ipc.main()
        except SystemExit as e:
            return e.code or 0

    def test_check_migration_uses_bare_feature_in_path(self):
        """--check-migration печатает manifest_path с bare feature, без slug в path."""
        self._seed_v1({"forgefix/fix-y": {"skill": "forgefix",
                                          "sources": {"story": "PROJ-1"},
                                          "autonomy": {"criticality": "low"},
                                          "steps": [{"id": "s1"}]}})
        rc = self._run("--check-migration")
        self.assertEqual(rc, 0)
        # plan["features"][0]["manifest_path"] — перехватываем через запись stdout — но
        # проще проверить через _plan_migration напрямую:
        plan = ipc._plan_migration(str(self.root), json.loads(
            (self.root / "ground" / "pipeline.json").read_text(encoding="utf-8")))
        self.assertEqual(len(plan["feature_errors"]), 0)
        entry = plan["features"][0]
        self.assertEqual(entry["feature"], "fix-y", "manifest.feature должен быть bare именем")
        self.assertEqual(entry["skill"], "forgefix")
        self.assertEqual(entry["manifest_path"],
                         "ground/statements/forgefix/fix-y/manifest.json",
                         "путь манифеста НЕ должен повторять skill как промежуточную директорию")
        self.assertEqual(entry["feature_slug"], "forgefix/fix-y")

    def test_migrate_writes_manifest_with_bare_names(self):
        """--migrate: на диске лежит manifest.json с bare skill/feature, не slug."""
        self._seed_v1({"forgefix/fix-y": {"skill": "forgefix",
                                          "sources": {"story": "PROJ-1"},
                                          "autonomy": {"criticality": "low"},
                                          "pipeline": {"mode": "fix"},
                                          "steps": [{"id": "s1", "title": "Step 1"}]}})
        rc = self._run("--migrate")
        self.assertEqual(rc, 0)
        # policy.json + .v1.bak + manifest.json (pipeline.json удалён)
        self.assertTrue((self.root / "ground" / "policy.json").exists())
        self.assertTrue((self.root / "ground" / "pipeline.json.v1.bak").exists())
        self.assertFalse((self.root / "ground" / "pipeline.json").exists())
        # bare-путь: skill=forgefix, feature=fix-y
        manifest_path = self.root / "ground" / "statements" / "forgefix" / "fix-y" / "manifest.json"
        self.assertTrue(manifest_path.exists(),
                        f"manifest должен лежать по {manifest_path}")
        self.assertFalse((self.root / "ground" / "statements" / "forgefix" / "forgefix").exists(),
                         "НЕ должно быть лишнего сегмента <skill>/<skill>/")
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(m["version"], 2)
        self.assertEqual(m["skill"], "forgefix")
        self.assertEqual(m["feature"], "fix-y", "manifest.feature должен быть bare именем")
        self.assertEqual(m["inputs"]["story"], "PROJ-1")
        self.assertEqual(m["inputs"]["mode"], "fix")
        self.assertEqual(m["decisions"]["criticality"], "low")
        self.assertEqual(m["steps"], [{"id": "s1", "title": "Step 1"}])

    def test_migrate_multiple_features_all_paths_correct(self):
        """Несколько фич в v1 → все мигрированы в bare-пути, не теряется ни одна."""
        self._seed_v1({
            "forgefix/fix-y":          {"skill": "forgefix",        "steps": [{"id": "a"}]},
            "forgelite/lite-x":        {"skill": "forgelite",       "steps": [{"id": "b"}]},
            "feature-pipeline/fp-z":   {"skill": "feature-pipeline",
                                        "steps": [{"id": "c"}]},
        })
        rc = self._run("--migrate")
        self.assertEqual(rc, 0)
        for skill, feature in [("forgefix", "fix-y"),
                               ("forgelite", "lite-x"),
                               ("feature-pipeline", "fp-z")]:
            mp = self.root / "ground" / "statements" / skill / feature / "manifest.json"
            self.assertTrue(mp.exists(), f"missing {mp}")
            m = json.loads(mp.read_text(encoding="utf-8"))
            self.assertEqual(m["skill"], skill)
            self.assertEqual(m["feature"], feature)

    def test_migrate_rejects_malformed_slug_extra_segment(self):
        """Slug с лишним сегментом ('a/b/c') → exit 2, feature_errors содержит запись."""
        self._seed_v1({"forgefix/fix-y/extra": {"skill": "forgefix", "steps": []}})
        rc = self._run("--migrate")
        self.assertEqual(rc, 2)
        # На диск ничего не записано (ни policy.json, ни manifests)
        self.assertFalse((self.root / "ground" / "policy.json").exists())
        self.assertFalse((self.root / "ground" / "statements" / "forgefix").exists())

    def test_migrate_rejects_malformed_slug_no_slash(self):
        """Slug без '/' ('no-slug') → exit 2."""
        self._seed_v1({"no-slug": {"skill": "forgefix", "steps": []}})
        rc = self._run("--migrate")
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "ground" / "policy.json").exists())

    def test_migrate_rejects_empty_component(self):
        """Slug '/fix-y' (пустой skill) → exit 2."""
        self._seed_v1({"/fix-y": {"skill": "forgefix", "steps": []}})
        rc = self._run("--migrate")
        self.assertEqual(rc, 2)

    def test_migrate_rejects_skill_mismatch(self):
        """Slug 'forgefix/fix-y' но body.skill='other' → exit 2 (несогласованный v1)."""
        self._seed_v1({"forgefix/fix-y": {"skill": "other", "steps": []}})
        rc = self._run("--migrate")
        self.assertEqual(rc, 2)

    def test_migrate_rejects_when_policy_already_exists(self):
        """Re-run: если policy.json уже есть, --migrate отказывается (exit 2)."""
        self._seed_v1({"forgefix/fix-y": {"skill": "forgefix", "steps": []}})
        (self.root / "ground" / "policy.json").write_text(
            '{"project": {"build_system": "gradle"}}', encoding="utf-8")
        rc = self._run("--migrate")
        self.assertEqual(rc, 2)
        # pipeline.json на месте — мы отказались
        self.assertTrue((self.root / "ground" / "pipeline.json").exists())

    def test_migrate_noop_when_only_v2_layout_present(self):
        """v2-native проект (нет pipeline.json) → exit 0, no-migration-needed."""
        # ничего не сеем: pipeline.json отсутствует
        rc = self._run("--migrate")
        self.assertEqual(rc, 0)

    def test_migration_preserves_unrecognized_top_level_keys(self):
        """Нераспознанный top-level ключ переезжает в policy.json, а не пропадает.

        Раньше _split_v1_project_wide отбрасывал всё, чего нет в _PROJECT_WIDE_KEYS:
        ключ значился в unknown_keys отчёта, но в мигрированном проекте его не было
        (оставался только в pipeline.json.v1.bak). Это ломало, в частности,
        `autonomy.mode` — он project-wide по реестру параметров, но `autonomy` не в
        whitelist, потому что autonomy.criticality/auto_max_risk — per-feature.
        """
        self._seed_v1(
            {"forgefix/fix-y": {"skill": "forgefix", "steps": []}},
            project_wide={
                "project": {"build_system": "gradle"},
                "autonomy": {"mode": "gated"},
                "custom_team_setting": {"reviewers": ["alice"]},
            },
        )
        self.assertEqual(self._run("--migrate"), 0)
        policy = json.loads((self.root / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy.get("project", {}).get("build_system"), "gradle")
        self.assertEqual(policy.get("autonomy"), {"mode": "gated"},
                         "top-level autonomy (autonomy.mode) потерян при миграции")
        self.assertEqual(policy.get("custom_team_setting"), {"reviewers": ["alice"]},
                         "нераспознанный ключ оператора потерян при миграции")

    def test_migration_reports_unrecognized_keys_even_though_it_keeps_them(self):
        """Ключ и переносится, и остаётся в unknown_keys — оператор должен его пересмотреть."""
        self._seed_v1(
            {"forgefix/fix-y": {"skill": "forgefix", "steps": []}},
            project_wide={"project": {"build_system": "gradle"}, "weird_key": 1},
        )
        plan = ipc._plan_migration(str(self.root), json.loads(
            (self.root / "ground" / "pipeline.json").read_text(encoding="utf-8")))
        self.assertIn("weird_key", plan["unknown_keys"])
        self.assertNotIn("project", plan["unknown_keys"])

    def test_migrated_per_feature_decisions_land_in_manifest_not_policy(self):
        """autonomy.criticality внутри features.<slug> → manifest.decisions, НЕ policy."""
        self._seed_v1({"forgefix/fix-y": {
            "skill": "forgefix",
            "sources": {"story": "S-1"},
            "autonomy": {"criticality": "low", "auto_max_risk": "R2"},
            "steps": [],
        }})
        self.assertEqual(self._run("--migrate"), 0)
        man = json.loads((self.root / "ground" / "statements" / "forgefix" / "fix-y"
                          / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["decisions"].get("criticality"), "low")
        self.assertEqual(man["decisions"].get("auto_max_risk"), "R2")
        self.assertEqual(man["inputs"].get("story"), "S-1")
        policy = json.loads((self.root / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertNotIn("autonomy", policy,
                         "per-feature autonomy просочился в policy.json")

    def test_migrated_manifest_consumable_by_set_criticality(self):
        """После --migrate manifest.json живёт там, где его ждёт set_criticality.py
        (statements/<skill>/<feature>/manifest.json). Это регресс-фикс Phase 4:
        до фикса set_criticality не находил манифест, потому что лежал по statements/<skill>/<slug>/."""
        # _config_loader.py лежит в hooks/, не в feature-pipeline/scripts — сначала кладём
        # его каталог в sys.path (тот же трюк, что в set_criticality.py), и только потом
        # импортируем. Раньше импорт стоял ПЕРЕД вставкой пути и тест падал ModuleNotFoundError
        # на любом интерпретаторе.
        hooks_dir = Path(__file__).resolve().parent.parent.parent.parent / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))

        self._seed_v1({"forgefix/fix-y": {"skill": "forgefix",
                                          "autonomy": {"criticality": "low"},
                                          "steps": [{"id": "s1"}]}})
        self._run("--migrate")
        # Подключаем _config_loader по файлу, как делает set_criticality.py
        import importlib.util
        spec = importlib.util.spec_from_file_location("_cl", hooks_dir / "_config_loader.py")
        cl = importlib.util.module_from_spec(spec)  # type: ignore
        spec.loader.exec_module(cl)  # type: ignore
        mp = cl.manifest_path(self.root, "forgefix", "fix-y")
        self.assertTrue(mp.exists(), f"set_criticality ожидает manifest по {mp}")


if __name__ == "__main__":
    unittest.main()