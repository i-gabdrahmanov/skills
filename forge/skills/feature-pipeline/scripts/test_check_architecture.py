#!/usr/bin/env python3
"""Тесты check_architecture.py — ArchUnit-lite гейт слоёв (P2-9)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_architecture import analyze_file, analyze, _layer_from_pkg, _import_layer

ROOT = "ru.x.app"


def _java(pkg, body):
    return f"package {pkg};\n{body}\n"


class TestLayerResolution(unittest.TestCase):
    def test_layer_from_pkg(self):
        self.assertEqual(_layer_from_pkg("ru.x.app.controller"), "controller")
        self.assertEqual(_layer_from_pkg("ru.x.app.repo"), "repository")  # repo→repository
        self.assertIsNone(_layer_from_pkg("ru.x.app.util"))

    def test_import_layer_internal_only(self):
        self.assertEqual(_import_layer("ru.x.app.service.FooService", ROOT), "service")
        self.assertIsNone(_import_layer("org.springframework.Foo", ROOT))  # внешний


class TestPackageRoot(unittest.TestCase):
    def test_package_outside_root_is_error(self):
        f = "service/x/src/main/java/Foo.java"
        v = analyze_file(f, _java("com.evil.controller", "class FooController {}"), ROOT)
        self.assertTrue(any(x["rule"] == "package-root" and x["severity"] == "error" for x in v))

    def test_package_under_root_ok(self):
        f = "src/main/java/Foo.java"
        v = analyze_file(f, _java("ru.x.app.controller", "class FooController {}"), ROOT)
        self.assertFalse(any(x["rule"] == "package-root" for x in v))

    def test_no_package_root_configured_skips(self):
        f = "src/main/java/Foo.java"
        v = analyze_file(f, _java("com.any.controller", "class FooController {}"), None)
        self.assertFalse(any(x["rule"] == "package-root" for x in v))


class TestClassPlacement(unittest.TestCase):
    def test_controller_in_wrong_package_warns(self):
        f = "src/main/java/Foo.java"
        v = analyze_file(f, _java("ru.x.app.service", "class FooController {}"), ROOT)
        self.assertTrue(any(x["rule"] == "class-placement" and x["severity"] == "warning" for x in v))

    def test_controller_in_controller_pkg_ok(self):
        f = "src/main/java/Foo.java"
        v = analyze_file(f, _java("ru.x.app.controller", "class FooController {}"), ROOT)
        self.assertFalse(any(x["rule"] == "class-placement" for x in v))

    def test_serviceimpl_maps_to_service(self):
        f = "src/main/java/Foo.java"
        v = analyze_file(f, _java("ru.x.app.service", "class FooServiceImpl {}"), ROOT)
        self.assertFalse(any(x["rule"] == "class-placement" for x in v))


class TestLayerDependency(unittest.TestCase):
    def test_entity_importing_service_is_error(self):
        f = "src/main/java/E.java"
        body = "import ru.x.app.service.FooService;\nclass Ent {}"
        v = analyze_file(f, _java("ru.x.app.entity", body), ROOT)
        self.assertTrue(any(x["rule"] == "layer-dependency" and x["severity"] == "error" for x in v))

    def test_controller_importing_repository_is_warning(self):
        f = "src/main/java/C.java"
        body = "import ru.x.app.repository.FooRepository;\nclass FooController {}"
        v = analyze_file(f, _java("ru.x.app.controller", body), ROOT)
        deps = [x for x in v if x["rule"] == "layer-dependency"]
        self.assertTrue(deps and all(x["severity"] == "warning" for x in deps))

    def test_service_importing_repository_ok(self):
        f = "src/main/java/S.java"
        body = "import ru.x.app.repository.FooRepository;\nclass FooService {}"
        v = analyze_file(f, _java("ru.x.app.service", body), ROOT)
        self.assertFalse(any(x["rule"] == "layer-dependency" for x in v))

    def test_external_import_ignored(self):
        f = "src/main/java/E.java"
        body = "import org.springframework.stereotype.Service;\nclass Ent {}"
        v = analyze_file(f, _java("ru.x.app.entity", body), ROOT)
        self.assertFalse(any(x["rule"] == "layer-dependency" for x in v))


class TestScope(unittest.TestCase):
    def test_test_files_ignored(self):
        v = analyze_file("src/test/java/FooTest.java",
                         _java("com.evil", "class FooTest {}"), ROOT)
        self.assertEqual(v, [])

    def test_non_java_ignored(self):
        self.assertEqual(analyze_file("src/main/resources/app.yml", "x: 1", ROOT), [])

    def test_non_src_main_ignored(self):
        self.assertEqual(analyze_file("build/gen/Foo.java", _java("com.evil", "class Foo {}"), ROOT), [])


class TestAnalyzeVerdict(unittest.TestCase):
    def test_clean_passes(self):
        files = {
            "src/main/java/C.java": _java("ru.x.app.controller", "class FooController {}"),
            "src/main/java/S.java": _java("ru.x.app.service",
                                          "import ru.x.app.repository.R;\nclass FooService {}"),
        }
        r = analyze(files, ROOT)
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["error"], 0)

    def test_error_fails(self):
        files = {"src/main/java/E.java": _java("ru.x.app.entity",
                 "import ru.x.app.controller.C;\nclass Ent {}")}
        r = analyze(files, ROOT)
        self.assertEqual(r["status"], "fail")
        self.assertGreaterEqual(r["counts"]["error"], 1)

    def test_warning_only_still_passes(self):
        files = {"src/main/java/C.java": _java("ru.x.app.controller",
                 "import ru.x.app.repository.R;\nclass FooController {}")}
        r = analyze(files, ROOT)
        self.assertEqual(r["status"], "pass")          # warning не валит status
        self.assertEqual(r["counts"]["warning"], 1)


if __name__ == "__main__":
    unittest.main()
