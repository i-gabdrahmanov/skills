#!/usr/bin/env python3
"""Smoke test for hooks/pii-boundary.py.

Раньше здесь был авто-стаб с `import pii-boundary as mod` — это SyntaxError (дефис в имени), поэтому
тест НИКОГДА не запускался (как и весь набор test_*.py хуков). Теперь: модуль грузится через
importlib (ловит регрессии синтаксиса/импорта) и проверяется fail-open на пустом stdin (общий
контракт хуков — не ронять инструмент на не-JSON входе). Поведенческое покрытие — hooks/evals/run-evals.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "pii-boundary.py"


def _run(tool_name: str, tool_input: dict):
    payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": ".",
                          "tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


class T(unittest.TestCase):
    def test_module_loads(self):
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)          # регрессия синтаксиса/импорта
        self.assertTrue(hasattr(m, "main"))

    def test_failopen_empty_stdin(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


class TInfraPathNotPII(unittest.TestCase):
    """Путь к корню проекта — инфраструктура, не ПДн.

    Живой прогон: корень проекта `/home/work/<таб-номер>@<домен>/code/<repo>`. Email-паттерн
    матчился на КАЖДУЮ команду с абсолютным путём — разведочный grep, mkdir, printf в docs/ —
    и хук превращался в сплошной deny, не имеющий отношения к ПДн."""

    ROOT = "/home/work/22269498@sigma.sbrf.ru/code/pprb-kid"

    def _run_at_root(self, tool_name: str, tool_input: dict):
        payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": self.ROOT,
                              "tool_name": tool_name, "tool_input": tool_input})
        return subprocess.run([sys.executable, str(HOOK)], input=payload,
                              capture_output=True, text=True, timeout=30)

    def test_grep_with_dev_null_passes(self):
        r = self._run_at_root("Bash", {"command":
            f"grep -rn approvals_path {self.ROOT}/.gigacode 2>/dev/null"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_write_into_repo_docs_passes(self):
        r = self._run_at_root("Bash", {"command":
            f"mkdir -p {self.ROOT}/docs/x && printf hello > {self.ROOT}/docs/x/sdd.md"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_real_pii_into_docs_still_blocked(self):
        r = self._run_at_root("Bash", {"command":
            f"printf ivan.petrov@client-bank.ru > {self.ROOT}/docs/x/leak.md"})
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_real_pii_via_write_still_blocked(self):
        r = self._run_at_root("Write", {"file_path": f"{self.ROOT}/src/main/java/X.java",
                                        "content": "// owner: ivan.petrov@client-bank.ru"})
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_second_redirect_target_is_checked(self):
        """`cmd 2>/dev/null > out.md`: первым шёл /dev/null, и настоящая цель не проверялась."""
        r = self._run_at_root("Bash", {"command":
            "echo ivan.petrov@client-bank.ru 2>/dev/null > docs/out.md"})
        self.assertEqual(r.returncode, 2, r.stdout)


class TPythonWriteVector(unittest.TestCase):
    """M5: запись PII через inline-python (без shell-редиректа) — раньше проходила мимо _target."""

    def test_block_open_write_pii_to_src_main(self):
        cmd = "python3 -c \"open('src/main/java/X.java','w').write('user@client-bank.ru')\""
        r = _run("run_shell_command", {"command": cmd})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_pathlib_write_text_pii(self):
        cmd = ("python3 -c \"from pathlib import Path; "
               "Path('src/main/X.java').write_text('AKIA1234567890ABCDEF')\"")
        r = _run("run_shell_command", {"command": cmd})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allow_pii_into_test_scope(self):
        cmd = "python3 -c \"open('src/test/Fixtures.java','w').write('user@example.com')\""
        r = _run("run_shell_command", {"command": cmd})
        self.assertEqual(r.returncode, 0, r.stderr)


class TestPlaceholdersAreNotSecrets(unittest.TestCase):
    """Задача 011: `password: ${DB_PASSWORD}` — ссылка на секрет, а не секрет.

    Пока паттерн credential'а не отличал одно от другого, запись обычного application.yml
    блокировалась целиком."""

    def _write(self, content: str):
        return _run("write_file", {"file_path": "src/main/resources/application.yml",
                                   "content": content})

    def test_env_placeholder_allowed(self):
        for value in ("${DB_PASSWORD}", '"${DB_PASSWORD}"', "{{ vault_password }}",
                      "<your-token-here>", "changeit", "***REDACTED***"):
            self.assertEqual(self._write(f"  password: {value}\n").returncode, 0,
                             f"ложный блок на плейсхолдере {value}")

    def test_real_secret_still_blocked(self):
        self.assertEqual(self._write("  password: hunter2secret\n").returncode, 2)

    def test_aws_key_still_blocked(self):
        r = _run("write_file", {"file_path": "docs/x.md",
                                "content": "api_key = AKIAIOSFODNN7EXAMPLE"})
        self.assertEqual(r.returncode, 2)


class TExampleDataIsNotPII(unittest.TestCase):
    """Боевой прогон (B1/B2): спека с примером user@example.com, UUID в sdd.md, README с
    `git clone git@github.com:…`, `timeout-ms: 1500000000` в yml — всё DENY. Пример и
    идентификатор — не персональные данные; настоящие телефоны/адреса — по-прежнему блок."""

    def _write(self, path, content):
        return _run("write_file", {"file_path": path, "content": content})

    def test_examples_pass(self):
        cases = [
            ("docs/feature-pipeline/F/sdd.md", "Пример: клиент вводит user@example.com"),
            ("docs/feature-pipeline/F/sdd.md", "orderId: 123e4567-e89b-12d3-a456-426614174000"),
            ("README.md", "git clone git@github.com:org/petstore.git"),
            ("src/main/resources/application.yml", "app.timeout-ms: 1500000000"),
            ("docs/feature-pipeline/F/sdd.md", "Телефон поддержки: +7 (XXX) XXX-XX-XX"),
            ("src/main/resources/application.yml", "mail.from: noreply@mail.example.org"),
        ]
        for path, content in cases:
            with self.subTest(content=content):
                r = self._write(path, content)
                self.assertEqual(r.returncode, 0, f"ложный блок: {content} → {r.stderr}")

    def test_real_contacts_still_blocked(self):
        cases = [
            ("docs/feature-pipeline/F/sdd.md", "Телефон поддержки: +7 (495) 123-45-67"),
            ("docs/feature-pipeline/F/sdd.md", "звонить 8-999-123-45-67"),
            ("docs/feature-pipeline/F/sdd.md", "моб. 89991234567"),
            ("docs/feature-pipeline/F/sdd.md", "почта ivan.petrov@client-bank.ru"),
            # домен лишь НАЧИНАЕТСЯ с example.com — это не зарезервированный адрес
            ("docs/feature-pipeline/F/sdd.md", "почта ivan@example.com.ru"),
        ]
        for path, content in cases:
            with self.subTest(content=content):
                self.assertEqual(self._write(path, content).returncode, 2, f"пропущено: {content}")

    def test_secret_named_as_secret(self):
        """B2: `api_key=sk-live-1234567890` блокировался «телефоном» по хвосту цифр."""
        r = self._write("docs/secret.md", "api_key=sk-live-1234567890")
        self.assertEqual(r.returncode, 2)
        self.assertIn("api", r.stderr)


class TSecretFormsDetected(unittest.TestCase):
    """Боевой прогон (PII-2): секрет в JSON-форме `{"api_key": "…"}` проходил — кавычка между
    ключом и двоеточием ломала паттерн; не ловились и secretKey, github_pat_, JWT, Bearer,
    Stripe *_live_, Google AIza, зашифрованный PEM. Для банка пропущенный секрет хуже ложного
    блока — но Java-код с passwordEncoder/tokenService трогать нельзя."""

    SECRETS = ['{"api_key": "sk_live_abcdef1234567890"}', "secretKey: s3cr3tValue99",
               '"clientSecret": "abcdef123456"',
               "token: github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuv",
               "Authorization: Bearer abcdefghijklmnopqrstuvwx1234",
               "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dGhpc2lzYXNpZ25hdHVyZQ",
               "stripe: sk_live_51Habcdefghijklmnop", "key=AIzaSyA1234567890abcdefghijklmnopqrstuv",
               "-----BEGIN ENCRYPTED PRIVATE KEY-----"]
    JAVA = ["private final PasswordEncoder passwordEncoder = new BCryptPasswordEncoder();",
            "this.passwordEncoder = passwordEncoder;",
            '@JsonProperty("token") private String token;',
            'if ("password".equals(field)) { return; }', 'String tokenType = "Bearer";',
            'map.put("password", value);', '@Value("${app.secret-key:}") String key;',
            "tokenService.issue(user);"]

    def test_secrets_blocked(self):
        for content in self.SECRETS:
            with self.subTest(content=content):
                r = _run("write_file", {"file_path": "src/main/resources/application.yml",
                                        "content": content})
                self.assertEqual(r.returncode, 2, f"секрет прошёл: {content}")

    def test_java_code_not_blocked(self):
        for content in self.JAVA:
            with self.subTest(content=content):
                r = _run("write_file", {"file_path": "src/main/java/com/x/Sec.java",
                                        "content": content})
                self.assertEqual(r.returncode, 0, f"ложный блок: {content}")


class TCardNeedsLuhn(unittest.TestCase):
    """Номер карты — только с сошедшейся суммой Луна. Голое 13–16-значное число (epoch-millis
    в JSON/yml) было DENY с диагнозом «карта» (боевой прогон, пункт 6 tasks/016)."""

    def _write(self, content):
        return _run("write_file", {"file_path": "src/main/resources/application.yml",
                                   "content": content})

    def test_real_card_blocked(self):
        for content in ("card: 4111 1111 1111 1111", "pan=5500000000000004"):
            with self.subTest(content=content):
                self.assertEqual(self._write(content).returncode, 2)

    def test_non_luhn_numbers_pass(self):
        for content in ("ts: 1696598400000", "id: 4111111111111112", "deadline: 1700000000000"):
            with self.subTest(content=content):
                self.assertEqual(self._write(content).returncode, 0, f"ложный блок: {content}")


class TestAllowedScopePrecedence(unittest.TestCase):
    """Задача 012: `A or B and C` связывает `and` сильнее, и ветка resources была мертва
    (is_test_path выше уже вернул False, значит второй операнд всегда False)."""

    def test_test_resources_allowed(self):
        sys.path.insert(0, str(HOOK.parent))
        spec = importlib.util.spec_from_file_location("pii_boundary", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(m._allowed_scope("src/test/resources/fixtures/users.json"))
        self.assertTrue(m._allowed_scope("ground/inventory/scan.json"))
        self.assertFalse(m._allowed_scope("src/main/resources/application.yml"))


class TEmailPatternIsLinear(unittest.TestCase):
    """Неограниченный `[…]+@` на длинном прогоне символов без `@` — квадратичный поиск:
    256К символов шли 50 с, дольше таймаута хука, и запись уходила НЕпросканированной
    (боевой прогон v0.4.6, E-REPOS-PV). Квантификаторы ограничены по RFC."""

    def test_long_run_is_fast_and_email_still_caught(self):
        import time
        t0 = time.time()
        r = _run("write_file", {"file_path": "docs/blob.txt", "content": "a" * 65536 + "!@"})
        self.assertLess(time.time() - t0, 5, "email-паттерн квадратичен по длине контента")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run("write_file", {"file_path": "docs/contacts.md",
                                "content": "пишите ivan.petrov@corp.ru"})
        self.assertEqual(r.returncode, 2, "email перестал ловиться")


if __name__ == "__main__":
    unittest.main()
