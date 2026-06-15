#!/usr/bin/env python3
"""Тесты детерминированных сканеров system-analyst на фикстуре-мини-проекте.

Скан — ground truth всего грундинга и судей; раньше тесты были только в feature-pipeline.
Проверяем: domain (@Entity), api (endpoint), reuse (dependency + util-класс).

Требует Python 3.10+.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import domain  # noqa: E402
import endpoints  # noqa: E402
import reuse  # noqa: E402
import scan_all  # noqa: E402

BUILD_GRADLE = """
dependencies {
  implementation 'org.apache.commons:commons-lang3:3.14.0'
  implementation("org.springframework.boot:spring-boot-starter-web:3.2.1")
  testImplementation 'org.junit.jupiter:junit-jupiter:5.10'
}
"""

ENTITY_JAVA = """
package com.x.domain;
import javax.persistence.Entity;
@Entity
public class Artifact { private Long id; }
"""

CONTROLLER_JAVA = """
package com.x.api;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/v1")
public class ArtifactController {
  @GetMapping("/artifacts")
  public String list() { return ""; }
}
"""

UTIL_JAVA = """
package com.x.common;
public final class DateUtils {
  public static String fmt(long t) { return ""; }
  public static boolean isPast(long t) { return false; }
}
"""

SERVICE_JAVA = """
package com.x.service;
public class ArtifactService { public void run() {} }
"""


class ScannerFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "build.gradle").write_text(BUILD_GRADLE, encoding="utf-8")
        base = self.root / "src/main/java/com/x"
        for sub, content in [("domain/Artifact.java", ENTITY_JAVA),
                             ("api/ArtifactController.java", CONTROLLER_JAVA),
                             ("common/DateUtils.java", UTIL_JAVA),
                             ("service/ArtifactService.java", SERVICE_JAVA)]:
            p = base / sub
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_domain_finds_entity(self):
        items = domain.scan(self.root)
        entities = [i for i in items if i.get("kind") == "entity"]
        self.assertEqual(len(entities), 1, items)
        self.assertEqual(entities[0]["name"], "Artifact")

    def test_endpoints_finds_controller(self):
        controllers = endpoints.scan(self.root)
        eps = [e for c in controllers for e in c.endpoints]
        self.assertEqual(len(eps), 1, controllers)

    def test_reuse_dependencies(self):
        deps = reuse.scan_dependencies(self.root)
        arts = {d["artifact"] for d in deps}
        self.assertIn("commons-lang3", arts)
        self.assertIn("spring-boot-starter-web", arts)
        self.assertIn("junit-jupiter", arts)

    def test_reuse_project_utils(self):
        utils = reuse.scan_project_utils(self.root)
        names = {u["class"] for u in utils}
        self.assertIn("DateUtils", names)          # util по имени + static-методы
        self.assertNotIn("ArtifactService", names)  # обычный сервис — не util
        du = next(u for u in utils if u["class"] == "DateUtils")
        self.assertTrue(any("fmt(" in m for m in du["methods"]))

    def test_scan_all_integrated(self):
        cats = scan_all.scan_root(self.root)
        self.assertIn("reuse", cats)
        self.assertEqual(cats["domain"]["gate_total"], 1)
        self.assertGreaterEqual(len(cats["reuse"]["dependencies"]), 3)
        self.assertGreaterEqual(len(cats["reuse"]["project_utils"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
