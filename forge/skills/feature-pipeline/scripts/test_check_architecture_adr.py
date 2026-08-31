#!/usr/bin/env python3
"""test_check_architecture_adr.py — ADR-энфорс межмодульных связок (C1+C2).

Тестирует check_module_deps с allowed_new в форме {edge,adr} и adr.enforce_couplings,
монипатчем git-diff/policy (без реального gradle-репо), плюс _norm_allowed_entry/_adr_accepted.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_architecture as ca  # noqa: E402
import skill_paths  # noqa: E402

ROOT = Path("/tmp/__arch_adr__")
EDGE = {"from": "service:a", "to": "service:b", "file": "build.gradle",
        "line": "implementation project(':service:b')"}


class NormEntryTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(ca._norm_allowed_entry(["service-a", "service-b"]),
                         (("service:a", "service:b"), None))
        self.assertEqual(ca._norm_allowed_entry({"edge": ["service-a", "service-b"], "adr": "ADR-0007"}),
                         (("service:a", "service:b"), "ADR-0007"))
        self.assertIsNone(ca._norm_allowed_entry({"edge": ["x"]}))
        self.assertIsNone(ca._norm_allowed_entry("nope"))


class AdrAcceptedTest(unittest.TestCase):
    def test_status_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = {"docs": {"master": {"adr_subdir": "adr"}}}
            adr_dir = skill_paths.master_adr_dir(root, cfg)
            adr_dir.mkdir(parents=True)
            body = "\n\n## Context\nc\n## Decision\nd\n## Consequences\ne\n"
            (adr_dir / "0007-x.md").write_text("# ADR-0007: x\n\n**Status:** accepted" + body, encoding="utf-8")
            (adr_dir / "0008-y.md").write_text("# ADR-0008: y\n\n**Status:** proposed" + body, encoding="utf-8")
            self.assertTrue(ca._adr_accepted(root, "ADR-0007", cfg))
            self.assertFalse(ca._adr_accepted(root, "ADR-0008", cfg))
            self.assertFalse(ca._adr_accepted(root, "ADR-0099", cfg))
            self.assertFalse(ca._adr_accepted(root, None, cfg))


class EnforceTest(unittest.TestCase):
    def setUp(self):
        self._edges = ca._added_module_dep_edges
        self._policy = ca.load_arch_policy
        self._accepted = ca._adr_accepted
        ca._added_module_dep_edges = lambda root, base: [dict(EDGE)]

    def tearDown(self):
        ca._added_module_dep_edges = self._edges
        ca.load_arch_policy = self._policy
        ca._adr_accepted = self._accepted

    def _run(self, policy, enforce):
        ca.load_arch_policy = lambda root: policy
        return ca.check_module_deps(ROOT, "HEAD", "deny_new", enforce_adr=enforce)

    def test_bare_allowed_no_enforce_ok(self):
        self.assertEqual(self._run({"allowed_new": [["service-a", "service-b"]]}, False), [])

    def test_bare_allowed_enforce_fails(self):
        v = self._run({"allowed_new": [["service-a", "service-b"]]}, True)
        self.assertEqual(len(v), 1)
        self.assertIn("без accepted ADR", v[0]["detail"])

    def test_dict_allowed_accepted_ok(self):
        ca._adr_accepted = lambda root, ref, cfg=None: True
        self.assertEqual(self._run({"allowed_new": [{"edge": ["service-a", "service-b"], "adr": "ADR-0007"}]}, True), [])

    def test_dict_allowed_not_accepted_fails(self):
        ca._adr_accepted = lambda root, ref, cfg=None: False
        v = self._run({"allowed_new": [{"edge": ["service-a", "service-b"], "adr": "ADR-0007"}]}, True)
        self.assertEqual(len(v), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
