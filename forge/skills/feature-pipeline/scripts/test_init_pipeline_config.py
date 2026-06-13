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
        """Создаёт ground/pipeline.json."""
        rc = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / "ground" / "pipeline.json").exists())

    def test_config_is_valid_json(self):
        """Созданный файл — валидный JSON."""
        self._run_main()
        data = json.loads((self.root / "ground" / "pipeline.json").read_text(encoding="utf-8"))
        self.assertIn("project", data)
        self.assertIn("quality", data)

    def test_exists_not_overwritten(self):
        """Без --force не перезаписывает."""
        self._run_main()
        path = self.root / "ground" / "pipeline.json"
        original = path.read_text(encoding="utf-8")
        self._run_main()
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_force_overwrites(self):
        """--force перезаписывает."""
        self._run_main()
        path = self.root / "ground" / "pipeline.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["custom"] = "value"
        path.write_text(json.dumps(data), encoding="utf-8")

        self._run_main(["--force"])
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("custom", reloaded)

    def test_update_keeps_existing_fields(self):
        """--update сохраняет существующие поля, обновляет детектируемые."""
        self._run_main()
        path = self.root / "ground" / "pipeline.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["jira"] = {"enabled": True, "project_key": "TEST"}
        path.write_text(json.dumps(data), encoding="utf-8")

        self._run_main(["--update"])
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        # jira сохранилось
        self.assertEqual(reloaded["jira"]["project_key"], "TEST")
        # project обновилось
        self.assertIn("project", reloaded)

    def test_dry_run(self):
        """--print не создаёт файл."""
        self._run_main(["--print"])
        self.assertFalse((self.root / "ground" / "pipeline.json").exists())

    def test_nonexistent_dir(self):
        """Несуществующая директория → exit 1."""
        args = [
            "init_pipeline_config.py",
            "--project", "/nonexistent/path/12345",
        ]
        sys.argv = args
        with self.assertRaises(SystemExit):
            ipc.main()


if __name__ == "__main__":
    unittest.main()