#!/usr/bin/env python3
"""test_check_adr.py — тесты валидатора ADR (MADR)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_adr as gate  # noqa: E402


def adr(status="accepted", *, decision=True, superseded_by=None, num="0007", title_id="0007"):
    lines = [f"# ADR-{title_id}: тестовое решение", "",
             f"**Status:** {status}", "**Date:** 2026-01-01"]
    if superseded_by is not None:
        lines.append(f"**Superseded-by:** ADR-{superseded_by}")
    lines += ["", "## Context", "Контекст решения при текущем графе модулей.", ""]
    if decision:
        lines += ["## Decision", "Публикуем события через Kafka.", ""]
    lines += ["## Consequences", "Плюсы и минусы.", "", "## Alternatives", "REST — отклонён."]
    return "\n".join(lines) + "\n"


class AdrGateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "adr"
        self.dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        (self.dir / name).write_text(content, encoding="utf-8")

    def test_valid_passes(self):
        self._write("0007-kafka.md", adr())
        self.assertEqual(gate.check(self.dir, [])["status"], "pass")

    def test_missing_status_fails(self):
        self._write("0007-kafka.md", adr().replace("**Status:** accepted", ""))
        self.assertEqual(gate.check(self.dir, [])["status"], "fail")

    def test_missing_decision_fails(self):
        self._write("0007-kafka.md", adr(decision=False))
        v = gate.check(self.dir, [])
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("Decision" in e for e in v["errors"]))

    def test_invalid_status_fails(self):
        self._write("0007-kafka.md", adr(status="maybe"))
        self.assertEqual(gate.check(self.dir, [])["status"], "fail")

    def test_superseded_without_link_fails(self):
        self._write("0007-kafka.md", adr(status="superseded"))
        v = gate.check(self.dir, [])
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("Superseded-by" in e for e in v["errors"]))

    def test_superseded_with_resolving_link_passes(self):
        self._write("0007-kafka.md", adr(status="superseded", superseded_by="0009"))
        self._write("0009-kafka-v2.md", adr(num="0009", title_id="0009"))
        self.assertEqual(gate.check(self.dir, [])["status"], "pass")

    def test_superseded_with_dangling_link_fails(self):
        self._write("0007-kafka.md", adr(status="superseded", superseded_by="0099"))
        self.assertEqual(gate.check(self.dir, [])["status"], "fail")

    def test_bad_filename_fails(self):
        self._write("kafka.md", adr())
        # файл не начинается с цифры → не считается ADR → каталог пуст → pass;
        # но файл, начинающийся с цифры с плохим именем — fail
        self._write("07_kafka.md", adr())
        self.assertEqual(gate.check(self.dir, [])["status"], "fail")

    def test_duplicate_id_fails(self):
        self._write("0007-a.md", adr())
        self._write("0007-b.md", adr())
        v = gate.check(self.dir, [])
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("дубл" in e.lower() for e in v["errors"]))

    def test_refs_resolve(self):
        self._write("0007-kafka.md", adr())
        ref = Path(self._tmp.name) / "tech-design.md"
        ref.write_text("Решение см. ADR-0007.", encoding="utf-8")
        self.assertEqual(gate.check(self.dir, [str(ref)])["status"], "pass")

    def test_refs_dangling_fails(self):
        self._write("0007-kafka.md", adr())
        ref = Path(self._tmp.name) / "tech-design.md"
        ref.write_text("Решение см. ADR-0042 (нет такого).", encoding="utf-8")
        v = gate.check(self.dir, [str(ref)])
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("0042" in e for e in v["errors"]))

    def test_empty_and_missing_dir_pass(self):
        self.assertEqual(gate.check(self.dir, [])["status"], "pass")  # пустой
        self.assertEqual(gate.check(Path(self._tmp.name) / "nope", [])["status"], "pass")  # нет каталога


if __name__ == "__main__":
    unittest.main(verbosity=2)
