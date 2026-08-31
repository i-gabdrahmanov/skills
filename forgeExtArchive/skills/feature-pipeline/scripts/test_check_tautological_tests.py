#!/usr/bin/env python3
"""Тесты check_tautological_tests.py — статический детектор тавтологий (P2-10)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_tautological_tests import analyze_test_content, analyze, _extract_test_methods

F = "service/x/src/test/java/FooTest.java"


def _cls(*methods: str) -> str:
    return "package x;\nclass FooTest {\n" + "\n".join(methods) + "\n}\n"


class TestExtract(unittest.TestCase):
    def test_extracts_test_methods(self):
        src = _cls("@Test\nvoid a() { assertTrue(x); }", "@Test\nvoid b() throws Exception { verify(m); }")
        names = [n for n, _ in _extract_test_methods(src)]
        self.assertEqual(names, ["a", "b"])

    def test_nested_braces_in_body(self):
        src = _cls("@Test\nvoid a() { if (x) { do(); } assertThat(y).isTrue(); }")
        body = _extract_test_methods(src)[0][1]
        self.assertIn("assertThat(y)", body)

    def test_non_test_methods_skipped(self):
        src = "class T { void helper() { assertTrue(true); } }"
        self.assertEqual(_extract_test_methods(src), [])


class TestTautologies(unittest.TestCase):
    def _v(self, method_body):
        return analyze_test_content(F, _cls(f"@Test\nvoid t() {{ {method_body} }}"))

    def test_assert_true_true(self):
        v = self._v("assertTrue(true);")
        self.assertTrue(any(x["rule"] == "tautological-assert" for x in v))

    def test_assert_false_false(self):
        self.assertTrue(any(x["rule"] == "tautological-assert" for x in self._v("assertFalse(false);")))

    def test_assertthat_true_istrue(self):
        self.assertTrue(any(x["rule"] == "tautological-assert" for x in self._v("assertThat(true).isTrue();")))

    def test_assert_equals_same_literal(self):
        self.assertTrue(any(x["rule"] == "tautological-assert" for x in self._v('assertEquals("x", "x");')))

    def test_assert_equals_same_int(self):
        self.assertTrue(any(x["rule"] == "tautological-assert" for x in self._v("assertEquals(1, 1);")))

    def test_bare_assert_true(self):
        self.assertTrue(any(x["rule"] == "tautological-assert" for x in self._v("assert true;")))

    def test_real_assertion_ok(self):
        v = self._v("assertEquals(expected, service.run());")
        self.assertEqual(v, [])


class TestEmptyAndMissing(unittest.TestCase):
    def test_empty_body_is_error(self):
        v = analyze_test_content(F, _cls("@Test\nvoid t() {}"))
        self.assertTrue(any(x["rule"] == "empty-test" and x["severity"] == "error" for x in v))

    def test_no_assertion_is_warning(self):
        v = analyze_test_content(F, _cls("@Test\nvoid t() { service.run(); int x = 2; }"))
        self.assertTrue(any(x["rule"] == "no-assertion" and x["severity"] == "warning" for x in v))

    def test_verify_counts_as_assertion(self):
        v = analyze_test_content(F, _cls("@Test\nvoid t() { service.run(); verify(repo).save(any()); }"))
        self.assertEqual(v, [])

    def test_helper_call_not_flagged(self):
        v = analyze_test_content(F, _cls("@Test\nvoid t() { service.run(); assertOrderPersisted(id); }"))
        self.assertEqual(v, [])  # делегирование в assert*-хелпер не FP

    def test_assertthrows_ok(self):
        v = analyze_test_content(F, _cls("@Test\nvoid t() { assertThrows(IllegalArg.class, () -> svc.run()); }"))
        self.assertEqual(v, [])


class TestScope(unittest.TestCase):
    def test_main_files_ignored(self):
        self.assertEqual(analyze_test_content("src/main/java/Foo.java",
                         "class Foo { @Test void t() {} }"), [])

    def test_non_java_ignored(self):
        self.assertEqual(analyze_test_content("src/test/x.txt", "@Test void t(){}"), [])


class TestVerdict(unittest.TestCase):
    def test_error_fails(self):
        r = analyze({F: _cls("@Test\nvoid t() { assertTrue(true); }")})
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["counts"]["error"], 1)

    def test_warning_only_passes(self):
        r = analyze({F: _cls("@Test\nvoid t() { service.run(); }")})
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["warning"], 1)

    def test_clean_passes(self):
        r = analyze({F: _cls("@Test\nvoid t() { assertEquals(2, calc.add(1,1)); }")})
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["violations"], [])


if __name__ == "__main__":
    unittest.main()
