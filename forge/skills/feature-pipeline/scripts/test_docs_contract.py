#!/usr/bin/env python3
"""test_docs_contract.py — пин против дрейфа доков от кода (P3-13).

config.md документирует eval-plan ПРИМЕРОМ. Раньше пример test_pass показывал
`./gradlew compileJava` / threshold 0.95, а генератор давно отдаёт `./gradlew test` / threshold 0
(P0-2). Этот тест фиксирует, что документированный контракт test_pass совпадает с тем, что
реально генерит build_evals_from_design — чтобы «тихий распад supply chain» падал в CI.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

S = Path(__file__).resolve().parent
CONFIG_MD = S.parent / "references" / "config.md"
sys.path.insert(0, str(S))
from build_evals_from_design import build_evals  # noqa: E402

_SAMPLE = {"feature_slug": "x", "tasks": [{"id": "T1", "coverage_threshold": 0.8}]}


class DocsContract(unittest.TestCase):
    def setUp(self):
        self.md = CONFIG_MD.read_text(encoding="utf-8")
        # блок объекта test_pass в примере eval-plan
        m = re.search(r'"type":\s*"test_pass".*?\}', self.md, re.S)
        self.assertIsNotNone(m, "в config.md нет примера test_pass eval")
        self.block = m.group(0)

    def test_generator_test_pass_contract(self):
        """build_evals реально отдаёт test_pass как бинарный gate (threshold 0, binary, test-команда)."""
        evals = build_evals(_SAMPLE, {"project": {"build_system": "gradle"}},
                            coverage_script="/dev/null")["evals"]
        tp = [e for e in evals if e["type"] == "test_pass"]
        self.assertTrue(tp)
        for e in tp:
            self.assertEqual(e["threshold"], 0)
            self.assertIs(e["binary"], True)
            self.assertEqual(e["command"], "./gradlew test")

    def test_doc_example_matches_generator(self):
        """Документированный пример test_pass не разошёлся с генератором (P0-2-контракт)."""
        self.assertIn('"threshold": 0', self.block, "doc test_pass: threshold должен быть 0")
        self.assertIn('"binary": true', self.block, "doc test_pass: binary должен быть true")
        self.assertNotIn("compileJava", self.block,
                         "doc test_pass снова показывает compileJava — это стейл (P3-13)")
        self.assertNotIn("0.95", self.block, "doc test_pass снова показывает фиктивный порог 0.95")


if __name__ == "__main__":
    unittest.main()
