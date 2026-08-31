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


import check_architecture as ca  # noqa: E402


class TestModuleDeps(unittest.TestCase):
    """Гейт межмодульных зависимостей (прогон #3: молча подключённый модуль)."""

    def test_module_from_build_path(self):
        self.assertEqual(ca._module_from_build_path("service/taskservice/build.gradle"),
                         "service:taskservice")
        self.assertIsNone(ca._module_from_build_path("build.gradle"))

    def test_canon_module(self):
        self.assertEqual(ca._canon_module(":service:upzservice"), "service:upzservice")
        self.assertEqual(ca._canon_module("service-upzservice"), "service:upzservice")
        self.assertEqual(ca._canon_module(":api:taskservice-api"), "api:taskservice-api")

    def test_project_ref_regex(self):
        m = ca._PROJECT_REF_RE.search('implementation project(":service:upzservice")')
        self.assertEqual(m.group(1), ":service:upzservice")
        m2 = ca._PROJECT_REF_RE.search("api project(path: ':a:b')")
        self.assertEqual(m2.group(1), ":a:b")

    def _patch_edges(self, edges):
        self._orig = ca._added_module_dep_edges
        ca._added_module_dep_edges = lambda root, base: edges

    def tearDown(self):
        if hasattr(self, "_orig"):
            ca._added_module_dep_edges = self._orig

    EDGE = [{"file": "service/taskservice/build.gradle", "from": "service:taskservice",
             "to": "service:upzservice", "line": 'implementation project(":service:upzservice")'}]

    def test_deny_new_blocks(self):
        self._patch_edges(self.EDGE)
        v = ca.check_module_deps(Path("/tmp"), "HEAD", "deny_new")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["severity"], "error")
        self.assertEqual(v[0]["rule"], "module-dependency")

    def test_off_skips(self):
        self._patch_edges(self.EDGE)
        self.assertEqual(ca.check_module_deps(Path("/tmp"), "HEAD", "off"), [])

    def test_policy_allows_non_forbidden(self):
        # mode=policy: новая, но не forbidden → не блок
        self._patch_edges(self.EDGE)
        self.assertEqual(ca.check_module_deps(Path("/tmp"), "HEAD", "policy"), [])

    def test_allowed_new_whitelist(self):
        import tempfile
        self._patch_edges(self.EDGE)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ground").mkdir()
            (root / "ground" / "architecture-policy.json").write_text(
                '{"module_deps":{"allowed_new":[["service:taskservice","service:upzservice"]]}}', encoding="utf-8")
            self.assertEqual(ca.check_module_deps(root, "HEAD", "deny_new"), [])

    def test_forbidden_blocks_in_policy_mode(self):
        import tempfile
        self._patch_edges(self.EDGE)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ground").mkdir()
            (root / "ground" / "architecture-policy.json").write_text(
                '{"module_deps":{"forbidden":[["service:taskservice","service:upzservice"]]}}', encoding="utf-8")
            v = ca.check_module_deps(root, "HEAD", "policy")
            self.assertEqual(len(v), 1)
            self.assertIn("ЗАПРЕЩЕНА", v[0]["detail"])


class TestArchGround(unittest.TestCase):
    """Архитектурный граунд: модули можно соединять по правилам проекта (graph-режим)."""

    def test_build_module_graph(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "service" / "taskservice").mkdir(parents=True)
            (root / "api" / "taskservice-api").mkdir(parents=True)
            (root / "service" / "taskservice" / "build.gradle").write_text(
                'dependencies { implementation project(":api:taskservice-api") }', encoding="utf-8")
            (root / "api" / "taskservice-api" / "build.gradle").write_text("dependencies {}", encoding="utf-8")
            g = ca.build_module_graph(root)
            self.assertIn(["service:taskservice", "api:taskservice-api"], g["edges"])
            self.assertIn(["service", "api"], g["allowed_group_couplings"])

    def test_group(self):
        self.assertEqual(ca._group("service:taskservice"), "service")
        self.assertEqual(ca._group("database"), "database")

    def test_creates_cycle(self):
        edges = {("service:a", "api:b")}
        self.assertTrue(ca._creates_cycle(edges, "api:b", "service:a"))   # b→a замыкает
        self.assertFalse(ca._creates_cycle(edges, "service:a", "api:c"))  # новое, не цикл

    GROUND = {"edges": [["service:taskservice", "api:taskservice-api"]],
              "allowed_group_couplings": [["service", "api"]]}

    def _patch(self, edges):
        self._orig = ca._added_module_dep_edges
        ca._added_module_dep_edges = lambda root, base: edges

    def tearDown(self):
        if hasattr(self, "_orig"):
            ca._added_module_dep_edges = self._orig

    def test_accepted_coupling_passes(self):
        # новая service→api — проект так уже соединяет → пропуск
        self._patch([{"file": "service/taskservice/build.gradle", "from": "service:taskservice",
                      "to": "api:other", "line": 'project(":api:other")'}])
        v = ca.check_module_deps(Path("/nonexistent"), "HEAD", "graph", arch_ground=self.GROUND)
        self.assertEqual(v, [])

    def test_new_group_coupling_blocks(self):
        # service→service — проект так не соединяет → блок
        self._patch([{"file": "service/taskservice/build.gradle", "from": "service:taskservice",
                      "to": "service:upzservice", "line": 'project(":service:upzservice")'}])
        v = ca.check_module_deps(Path("/nonexistent"), "HEAD", "graph", arch_ground=self.GROUND)
        self.assertEqual(len(v), 1)
        self.assertIn("group-связка", v[0]["detail"])

    def test_cycle_blocks(self):
        self._patch([{"file": "api/taskservice-api/build.gradle", "from": "api:taskservice-api",
                      "to": "service:taskservice", "line": 'project(":service:taskservice")'}])
        v = ca.check_module_deps(Path("/nonexistent"), "HEAD", "graph", arch_ground=self.GROUND)
        self.assertEqual(len(v), 1)
        self.assertIn("ЦИКЛ", v[0]["detail"])

    def test_policy_allowed_new_overrides_graph(self):
        import tempfile
        self._patch([{"file": "service/taskservice/build.gradle", "from": "service:taskservice",
                      "to": "service:upzservice", "line": 'project(":service:upzservice")'}])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ground").mkdir()
            (root / "ground" / "architecture-policy.json").write_text(
                '{"module_deps":{"allowed_new":[["service:taskservice","service:upzservice"]]}}', encoding="utf-8")
            v = ca.check_module_deps(root, "HEAD", "graph", arch_ground=self.GROUND)
            self.assertEqual(v, [])  # allow-list побеждает graph-правило


class TestMavenModuleGraph(unittest.TestCase):
    """Поддержка Maven pom.xml в гейте межмодульных зависимостей (универсально, без допущений о структуре)."""

    POM_PARENT = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>ru.x.app</groupId>
  <artifactId>app-parent</artifactId>
  <version>1.0</version>
  <packaging>pom</packaging>
  <modules>
    <module>service/upzservice</module>
    <module>service/taskservice</module>
  </modules>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.springframework</groupId>
        <artifactId>spring-core</artifactId>
        <version>6.0</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
"""

    POM_UPZ = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>ru.x.app</groupId>
    <artifactId>app-parent</artifactId>
    <version>1.0</version>
  </parent>
  <artifactId>upzservice</artifactId>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
    </dependency>
  </dependencies>
</project>
"""

    # taskservice зависит от upzservice (sibling) + внешняя либа guava
    POM_TASK = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>ru.x.app</groupId>
    <artifactId>app-parent</artifactId>
    <version>1.0</version>
  </parent>
  <artifactId>taskservice</artifactId>
  <dependencies>
    <!-- межмодульная зависимость -->
    <dependency>
      <groupId>ru.x.app</groupId>
      <artifactId>upzservice</artifactId>
    </dependency>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
    </dependency>
  </dependencies>
</project>
"""

    # та же taskservice БЕЗ межмодульной зависимости (baseline для diff)
    POM_TASK_NO_UPZ = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>ru.x.app</groupId>
    <artifactId>app-parent</artifactId>
    <version>1.0</version>
  </parent>
  <artifactId>taskservice</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
    </dependency>
  </dependencies>
</project>
"""

    def test_parse_pom_own_coords_and_parent_inheritance(self):
        p = ca._parse_pom(self.POM_TASK)
        self.assertEqual(p["artifact"], "taskservice")    # своё, НЕ parent app-parent
        self.assertEqual(p["group"], "ru.x.app")          # наследовано от <parent>
        self.assertEqual({a for _, a in p["deps"]}, {"upzservice", "guava"})  # только <dependencies>

    def test_parse_pom_excludes_dependency_management(self):
        p = ca._parse_pom(self.POM_PARENT)
        self.assertEqual(p["artifact"], "app-parent")
        self.assertEqual(p["deps"], [])                   # managed-блок и <modules> не считаются deps

    def test_parse_pom_invalid_returns_none(self):
        self.assertIsNone(ca._parse_pom("<project><unclosed>"))
        self.assertIsNone(ca._parse_pom("not xml at all"))
        self.assertIsNone(ca._parse_pom("<notproject/>"))

    def _maven_repo(self, d):
        root = Path(d)
        (root / "service" / "upzservice").mkdir(parents=True)
        (root / "service" / "taskservice").mkdir(parents=True)
        (root / "pom.xml").write_text(self.POM_PARENT, encoding="utf-8")
        (root / "service" / "upzservice" / "pom.xml").write_text(self.POM_UPZ, encoding="utf-8")
        (root / "service" / "taskservice" / "pom.xml").write_text(self.POM_TASK, encoding="utf-8")
        return root

    def test_maven_modules_resolve_map(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = self._maven_repo(d)
            modules, resolve = ca._maven_modules(root)
            self.assertIn("service:upzservice", modules)
            self.assertIn("service:taskservice", modules)
            self.assertNotIn("app-parent", modules)            # агрегатор (path_id None) — не узел
            self.assertEqual(resolve.get("ru.x.app:upzservice"), "service:upzservice")  # groupId:artifactId
            self.assertEqual(resolve.get("upzservice"), "service:upzservice")            # голый artifactId

    def test_pom_internal_edges_internal_vs_external(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = self._maven_repo(d)
            _, resolve = ca._maven_modules(root)
            edges = ca._pom_internal_edges(self.POM_TASK, "service:taskservice", resolve)
            self.assertEqual(edges, {"service:upzservice"})   # guava внешняя → не ребро; self исключён

    def test_build_module_graph_maven(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = self._maven_repo(d)
            g = ca.build_module_graph(root)
            self.assertIn(["service:taskservice", "service:upzservice"], g["edges"])
            self.assertNotIn("app-parent", g["modules"])                       # агрегатор не узел
            self.assertFalse(any("guava" in m for m in g["modules"]))          # внешняя не узел
            self.assertIn(["service", "service"], g["allowed_group_couplings"])

    def test_build_module_graph_mixed_gradle_and_maven(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "api" / "taskservice-api").mkdir(parents=True)
            (root / "service" / "taskservice").mkdir(parents=True)
            (root / "service" / "upzservice").mkdir(parents=True)
            # Gradle-модули
            (root / "api" / "taskservice-api" / "build.gradle").write_text("dependencies {}", encoding="utf-8")
            (root / "service" / "taskservice" / "build.gradle").write_text(
                'dependencies { implementation project(":api:taskservice-api") }', encoding="utf-8")
            # Maven-модуль рядом
            (root / "service" / "upzservice" / "pom.xml").write_text(self.POM_UPZ, encoding="utf-8")
            g = ca.build_module_graph(root)
            self.assertIn(["service:taskservice", "api:taskservice-api"], g["edges"])  # Gradle ребро
            self.assertIn("service:upzservice", g["modules"])                           # Maven узел

    def test_pom_diff_new_internal_edge_set_difference(self):
        # сердцевина parse&compare без git: work − base
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = self._maven_repo(d)
            _, resolve = ca._maven_modules(root)
            base = ca._pom_internal_edges(self.POM_TASK_NO_UPZ, "service:taskservice", resolve)
            work = ca._pom_internal_edges(self.POM_TASK, "service:taskservice", resolve)
            self.assertEqual(base, set())                       # guava внешняя — не ребро
            self.assertEqual(work - base, {"service:upzservice"})

    def test_added_module_dep_edges_maven_git(self):
        # end-to-end: реальный git-diff ловит дописанную межмодульную зависимость в pom.xml
        import tempfile, subprocess, shutil
        if shutil.which("git") is None:
            self.skipTest("git недоступен")
        with tempfile.TemporaryDirectory() as d:
            root = self._maven_repo(d)
            (root / "service" / "taskservice" / "pom.xml").write_text(self.POM_TASK_NO_UPZ, encoding="utf-8")  # старт без зависимости

            def git(*a):
                subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)
            git("init", "-q")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            git("add", "-A")
            git("commit", "-qm", "init")
            (root / "service" / "taskservice" / "pom.xml").write_text(self.POM_TASK, encoding="utf-8")          # фича дописала зависимость

            edges = ca._added_module_dep_edges(root, "HEAD")
            self.assertTrue(any(e["from"] == "service:taskservice" and e["to"] == "service:upzservice"
                                for e in edges), edges)
            # gate в deny_new должен заблокировать
            v = ca.check_module_deps(root, "HEAD", "deny_new")
            self.assertEqual(len(v), 1)
            self.assertEqual(v[0]["severity"], "error")


if __name__ == "__main__":
    unittest.main()
