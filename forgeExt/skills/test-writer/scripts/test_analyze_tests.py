#!/usr/bin/env python3
"""Тесты analyze_tests.py: детект фреймворков/конвенций, отбор эталонов, кэш --if-missing."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import analyze_tests as at

GOOD_TEST = """\
package com.acme.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrderServiceTest {

    @Mock
    private OrderRepository orderRepository;

    @InjectMocks
    private OrderServiceImpl orderService;

    @Test
    void shouldCloseOrderWhenEmpty() {
        // given
        when(orderRepository.findById(1L)).thenReturn(emptyOrder());
        // when
        orderService.close(1L);
        // then
        verify(orderRepository).save(any());
        assertThat(orderService.isClosed(1L)).isTrue();
    }

    @Test
    void shouldThrowWhenOrderMissing() {
        // given
        when(orderRepository.findById(2L)).thenReturn(null);
        // then
        assertThat(orderService.exists(2L)).isFalse();
    }

    @Test
    void shouldSkipWhenAlreadyClosed() {
        // given
        when(orderRepository.findById(3L)).thenReturn(closedOrder());
        // then
        assertThat(orderService.close(3L)).isNull();
    }
}
"""

SPRING_IT = """\
package com.acme.it;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
class ApplicationIT {

    @Test
    void contextLoads() {
        assertThat(true).isTrue();
    }

    @Test
    void beansPresent() {
        assertThat(1).isEqualTo(1);
    }
}
"""

LEGACY_TEST = """\
package com.acme.legacy;

import org.junit.Test;
import org.junit.Assert;

public class LegacyUtilTest extends AbstractLegacyBase {

    @Test
    public void testParse() {
        Assert.assertEquals("x", LegacyUtil.parse("x"));
    }

    @Test
    public void testParseNull() {
        Assert.assertNull(LegacyUtil.parse(null));
    }
}
"""


def _make_project(root: Path) -> None:
    files = {
        "svc/src/test/java/com/acme/service/OrderServiceTest.java": GOOD_TEST,
        "svc/src/test/java/com/acme/it/ApplicationIT.java": SPRING_IT,
        "legacy/src/test/java/com/acme/legacy/LegacyUtilTest.java": LEGACY_TEST,
        # мусор, который сканер обязан пропустить
        "svc/build/src/test/java/com/acme/Copied.java": GOOD_TEST,
        "svc/src/main/java/com/acme/service/OrderServiceImpl.java": "class OrderServiceImpl {}",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_project(self.root)
        self.result = at.analyze(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_stats_and_modules(self):
        self.assertEqual(self.result["stats"]["test_files"], 3)
        self.assertEqual(self.result["stats"]["test_methods"], 7)
        self.assertEqual(self.result["stats"]["modules"], ["legacy", "svc"])

    def test_framework_detection(self):
        fw = self.result["frameworks"]
        self.assertEqual(fw["junit5"], 2)
        self.assertEqual(fw["junit4"], 1)
        self.assertEqual(fw["mockito"], 1)
        self.assertEqual(fw["assertj"], 2)
        self.assertEqual(fw["spring_test"], 1)

    def test_dominant(self):
        d = self.result["dominant"]
        self.assertEqual(d["junit"], "junit5")
        self.assertEqual(d["assertions"], "assertj")
        self.assertGreater(d["mockito_unit_share"], 0)
        self.assertGreater(d["spring_context_share"], 0)

    def test_naming_dominant_should(self):
        self.assertEqual(self.result["naming"]["dominant"], "should")
        self.assertIn("test", self.result["naming"]["counts"])

    def test_exemplars_exclude_spring_context(self):
        paths = [e["path"] for e in self.result["exemplars"]]
        self.assertIn("svc/src/test/java/com/acme/service/OrderServiceTest.java", paths)
        self.assertNotIn("svc/src/test/java/com/acme/it/ApplicationIT.java", paths)

    def test_exemplar_top_is_mockito_unit(self):
        top = self.result["exemplars"][0]
        self.assertEqual(top["path"], "svc/src/test/java/com/acme/service/OrderServiceTest.java")
        self.assertGreaterEqual(top["score"], 5)

    def test_base_classes_recorded(self):
        names = {b["name"] for b in self.result["base_classes"]}
        self.assertIn("AbstractLegacyBase", names)

    def test_build_dir_skipped(self):
        # 3 файла, не 4: svc/build/... пропущен
        self.assertEqual(self.result["stats"]["test_files"], 3)

    def test_empty_project_warning(self):
        with tempfile.TemporaryDirectory() as empty:
            res = at.analyze(Path(empty))
            self.assertIn("no_tests_found", res["warnings"])
            self.assertEqual(res["exemplars"], [])


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_project(self.root)
        self.script = SCRIPTS / "analyze_tests.py"
        self.out = self.root / "docs/system-analysis/scan/test-conventions.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, str(self.script), "--root", str(self.root), *extra],
            capture_output=True, text=True)

    def test_first_run_writes_cache(self):
        proc = self._run("--if-missing")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], at.SCHEMA_VERSION)
        self.assertTrue(data["exemplars"])

    def test_second_run_uses_cache(self):
        self.assertEqual(self._run("--if-missing").returncode, 0)
        mtime = self.out.stat().st_mtime_ns
        proc = self._run("--if-missing")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("cached", proc.stdout)
        self.assertEqual(self.out.stat().st_mtime_ns, mtime, "кэш не должен перезаписываться")

    def test_refresh_rescans(self):
        self.assertEqual(self._run().returncode, 0)
        mtime = self.out.stat().st_mtime_ns
        proc = self._run("--refresh", "--if-missing")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("cached", proc.stdout)
        self.assertNotEqual(self.out.stat().st_mtime_ns, mtime)

    def test_broken_cache_rescanned(self):
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text("{broken", encoding="utf-8")
        proc = self._run("--if-missing")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertIn("stats", data)

    def test_missing_root_fails(self):
        proc = subprocess.run(
            [sys.executable, str(self.script), "--root", str(self.root / "nope")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()
