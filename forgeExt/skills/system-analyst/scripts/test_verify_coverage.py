#!/usr/bin/env python3
"""Тесты gate полноты verify_coverage, включая независимый кросс-чек против кода."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import verify_coverage  # noqa: E402


class CrossCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.scan = self.root / "scan"
        self.scan.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_scan(self, cat: str, items: list, gate_total: int):
        (self.scan / f"{cat}.json").write_text(
            json.dumps({"gate_total": gate_total, "total": len(items), "items": items}),
            encoding="utf-8")

    def _entity_file(self, name: str):
        p = self.root / f"src/main/java/com/x/{name}.java"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"@Entity\npublic class {name} {{}}\n", encoding="utf-8")

    def test_cross_check_flags_scanner_undercount(self):
        # В коде 2 @Entity, но сканер записал только 1 → кросс-чек обязан предупредить.
        self._entity_file("Alpha")
        self._entity_file("Beta")
        self._write_scan("domain", [{"name": "Alpha", "kind": "entity"}], gate_total=1)
        self._write_scan("api", [], 0)
        self._write_scan("async_consumers", [], 0)
        reported = {"entities": [{"name": "Alpha"}], "api_endpoints": [], "async": []}
        verdict = verify_coverage.verify(self.scan, reported, code_root=self.root)
        self.assertTrue(verdict.get("warnings"), "ожидалось предупреждение о недосчёте")
        domain_row = next(r for r in verdict["hard"] if r["category"] == "domain")
        self.assertEqual(domain_row["scanner_undercount"], 1)

    def test_cross_check_silent_when_consistent(self):
        self._entity_file("Alpha")
        self._write_scan("domain", [{"name": "Alpha", "kind": "entity"}], gate_total=1)
        self._write_scan("api", [], 0)
        self._write_scan("async_consumers", [], 0)
        reported = {"entities": [{"name": "Alpha"}], "api_endpoints": [], "async": []}
        verdict = verify_coverage.verify(self.scan, reported, code_root=self.root)
        self.assertNotIn("warnings", verdict)
        self.assertEqual(verdict["status"], "pass")

    def test_test_sources_not_counted_in_cross_check(self):
        # @Entity в src/test не должен раздувать кросс-чек (иначе ложное предупреждение).
        self._entity_file("Alpha")
        tp = self.root / "src/test/java/com/x/FakeEntity.java"
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("@Entity\npublic class FakeEntity {}\n", encoding="utf-8")
        self._write_scan("domain", [{"name": "Alpha", "kind": "entity"}], gate_total=1)
        self._write_scan("api", [], 0)
        self._write_scan("async_consumers", [], 0)
        reported = {"entities": [{"name": "Alpha"}], "api_endpoints": [], "async": []}
        verdict = verify_coverage.verify(self.scan, reported, code_root=self.root)
        self.assertNotIn("warnings", verdict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
