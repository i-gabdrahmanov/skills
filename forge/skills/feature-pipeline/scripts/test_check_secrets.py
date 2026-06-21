#!/usr/bin/env python3
"""Тесты check_secrets.py — детерминированный secret-scan (P2-12)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_secrets import scan_text, scan_files, _is_placeholder

F = "src/main/resources/application.yml"


class TestPlaceholder(unittest.TestCase):
    def test_env_refs_are_placeholders(self):
        for v in ("${DB_PASSWORD}", "{{secret}}", "<password>", "#{cfg.pwd}"):
            self.assertTrue(_is_placeholder(v), v)

    def test_known_placeholders(self):
        for v in ("changeme", "CHANGEME", "xxx", "********", "example", ""):
            self.assertTrue(_is_placeholder(v), v)

    def test_real_value_not_placeholder(self):
        self.assertFalse(_is_placeholder("S3cr3tP@ssw0rd!"))


class TestCredentialAssignment(unittest.TestCase):
    def test_hardcoded_password_flagged(self):
        v = scan_text(F, 'spring.datasource.password: "S3cr3tP@ss12"')
        self.assertTrue(any(x["kind"] == "credential-assignment" for x in v))

    def test_env_ref_not_flagged(self):
        self.assertEqual(scan_text(F, 'password: "${DB_PASS}"'), [])

    def test_placeholder_value_not_flagged(self):
        self.assertEqual(scan_text(F, 'api_key = "changeme"'), [])

    def test_java_token_assignment(self):
        v = scan_text("Foo.java", 'String token = "abcdef123456ghijkl";')
        self.assertTrue(v)


class TestHighSignalPatterns(unittest.TestCase):
    def test_aws_key(self):
        self.assertTrue(scan_text(F, "aws_key=AKIAIOSFODNN7EXAMPLE"))

    def test_pem_private_key(self):
        self.assertTrue(scan_text("key.pem", "-----BEGIN RSA PRIVATE KEY-----"))

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpM"
        v = scan_text(F, f"auth: {jwt}")
        self.assertTrue(any(x["kind"] == "jwt" for x in v))

    def test_github_token(self):
        self.assertTrue(scan_text(F, "ghp_" + "A" * 36))

    def test_jdbc_password_in_url(self):
        v = scan_text(F, "url: jdbc:postgresql://h/db?user=u&password=Real1Pass2")
        self.assertTrue(any(x["kind"] == "jdbc-password" for x in v))

    def test_jdbc_env_password_not_flagged(self):
        self.assertEqual(scan_text(F, "url: jdbc:postgresql://h/db?password=${PG_PASS}"), [])


class TestVerdict(unittest.TestCase):
    def test_clean_passes(self):
        r = scan_files({F: "spring:\n  application:\n    name: demo\n"})
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["count"], 0)

    def test_secret_fails(self):
        r = scan_files({F: 'password: "Hardcoded123!"'})
        self.assertEqual(r["status"], "fail")
        self.assertGreaterEqual(r["count"], 1)

    def test_one_hit_per_line(self):
        # строка с двумя триггерами → одно нарушение (break)
        r = scan_files({F: 'secret="RealValue123" token="AnotherReal456"'})
        self.assertEqual(r["count"], 1)


if __name__ == "__main__":
    unittest.main()
