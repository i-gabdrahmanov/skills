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
import shutil
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


# ── мок-данные макета (forge-ui; задача 3.4) ──────────────────────────────────────────────────────
HOOKS = Path(__file__).resolve().parent
SCRIPTS = HOOKS.parent / "skills" / "forgeui" / "scripts"
EMAIL = "ivan.petrov@client-bank.ru"
PHONE = "+7 (495) 123-45-67"
CARD = "4111 1111 1111 1111"


class MockupWorld(unittest.TestCase):
    """Фронт-репо с активным прогоном forgeui: конфиг (outputDir src/modules), трек STOR-100 с каноном, в чьих текстах есть
    почта, телефон и номер карты — как в макете с реалистичными моками."""

    KEY = "STOR-100"

    def setUp(self):
        import tempfile
        sys.path.insert(0, str(SCRIPTS))
        import test_plan_contract as TPC
        import test_texts as TT
        self.TT = TT
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "repo"
        self.root.mkdir()
        TPC.init_repo(self.root)
        (self.root / ".git").mkdir()
        self.canon([TT.text("t1", "Контакты"), TT.text("t2", f"Пишите: {EMAIL}"),
                    TT.inst("c1", [("Phone", PHONE, True)]), TT.text("t3", f"Карта {CARD}")])
        self.manifest("forgeui", self.KEY)

    def canon(self, nodes):
        self.TT.make_track(self.root, {"main": self.TT.frame("Page", nodes)}, track=self.KEY)

    def manifest(self, skill, feature):
        d = self.root / "ground" / "statements" / skill / feature
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"feature": feature, "steps": [
            {"id": "ui-build", "status": "pending", "depends_on": []}]}), encoding="utf-8")

    def hook(self, tool, tool_input, root=None, hook_path=None):
        payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": str(root or self.root),
                              "tool_name": tool, "tool_input": tool_input})
        return subprocess.run([sys.executable, "-X", "utf8", str(hook_path or HOOK)], input=payload,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def write(self, rel, content, tool="write_file", **kw):
        return self.hook(tool, {"file_path": str(self.root / rel), "content": content}, **kw)

    def allowed(self, r, msg=""):
        self.assertEqual(r.returncode, 0, f"{msg}\n{r.stderr}")

    def denied(self, r, *needles):
        self.assertEqual(r.returncode, 2, f"{r.stdout}\n{r.stderr}")
        self.assertIn("[pii-boundary] DENY", r.stderr)
        for n in needles:
            self.assertIn(n, r.stderr)


MOCKS = "src/modules/Modal/mocks.ts"


class MockupExemption(MockupWorld):
    def test_strings_from_the_mockup_are_allowed_in_mocks_ts(self):
        for what, content in (("почта", f"export const contact = {{ email: '{EMAIL}' }};\n"),
                              ("телефон", f"export const phone = '{PHONE}';\n"),
                              ("номер карты с суммой Луна", f"export const card = '{CARD}';\n"),
                              ("почта и телефон вместе", f"export const c = {{ email: '{EMAIL}', phone: '{PHONE}' }};\n"),
                              ("строка внутри длинного текста макета", f"export const hello = 'Пишите: {EMAIL}';\n")):
            with self.subTest(what=what):
                self.allowed(self.write(MOCKS, content), what)

    def test_the_same_write_is_denied_when_the_string_is_not_in_the_mockup(self):
        for what, content in (("чужая почта", "export const email = 'anna.sidorova@client-bank.ru';\n"),
                              ("телефон другого вида", "export const phone = '+7 495 123-45-67';\n"),
                              ("одна своя, одна выдуманная", f"export const e = ['{EMAIL}', 'x.y@client-bank.ru'];\n"),
                              ("секрет", "export const t = { token: 'abcdef123456ghijkl' };\n"),
                              ("карта с суммой Луна, которой нет в макете", "export const card = '5500 0000 0000 0004';\n")):
            with self.subTest(what=what):
                r = self.write(MOCKS, content)
                self.denied(r)
                self.assertIn("нет дословно в текстах макета", r.stderr)

    def test_the_denial_counts_the_strings_but_does_not_echo_them(self):
        r = self.write(MOCKS, f"export const e = ['{EMAIL}', 'x.y@client-bank.ru'];\n")
        self.denied(r, "1 из 2")
        self.assertNotIn("x.y@client-bank.ru", r.stderr)

    def test_mock_dirs_and_nesting(self):
        for rel in ("src/modules/Modal/__mocks__/api.ts", "src/modules/__mocks__/api.ts", "src/modules/Modal/sub/mocks.ts",
                    "src/modules/mocks.ts", "src/modules/Modal/__mocks__/deep/er/data.json", "SRC/Modules/Modal/MOCKS.TS"):
            with self.subTest(rel=rel):
                self.allowed(self.write(rel, f"export default '{EMAIL}';\n"))

    def test_edit_tool_and_shell_heredoc(self):
        self.allowed(self.hook("edit", {"file_path": str(self.root / MOCKS), "old_string": "a", "new_string": f"b: '{EMAIL}'"}))
        self.allowed(self.hook("run_shell_command", {"command": f"cat > {self.root / MOCKS} <<'EOF'\nexport const e = '{EMAIL}';\nEOF"}))
        self.denied(self.hook("run_shell_command", {"command": f"cat > {self.root / MOCKS} <<'EOF'\nexport const e = 'a.b@client-bank.ru';\nEOF"}))

    def test_only_mock_files_get_the_exception(self):
        content = f"export const email = '{EMAIL}';\n"
        for rel in ("src/modules/Modal/Modal.tsx", "src/modules/Modal/data.ts", "src/modules/Modal/mocks.tsx",
                    "src/modules/Modal/mocks.ts.bak", "src/modules/Modal/mock.ts", "src/modules/Modal/mocksX/a.ts",
                    "src/modules/Modal/__mocks__"):
            with self.subTest(rel=rel):
                r = self.write(rel, content)
                self.denied(r)
                self.assertIn("ТОЛЬКО в mocks.ts или __mocks__/", r.stderr, "подсказка, куда положить тексты макета")

    def test_outside_the_module_the_hook_is_as_before_and_without_the_hint(self):
        content = f"export const email = '{EMAIL}';\n"
        for rel in ("src/other/mocks.ts", "src/mocks.ts", "mocks.ts", "docs/mocks.ts", "src/main/java/X.java",
                    "src/modulesX/Modal/mocks.ts", "../escape/mocks.ts"):
            with self.subTest(rel=rel):
                r = self.write(rel, content)
                self.denied(r)
                self.assertNotIn("mocks.ts или __mocks__", r.stderr)
                self.assertNotIn("макета", r.stderr)

    def test_one_foreign_target_among_the_mock_files_spoils_it(self):
        cmd = (f"cat > {self.root / MOCKS} <<'EOF'\nexport const e = '{EMAIL}';\nEOF\n"
               f"cat > {self.root / 'src/modules/Modal/Modal.tsx'} <<'EOF'\nconst e = '{EMAIL}';\nEOF")
        self.denied(self.hook("run_shell_command", {"command": cmd}))
        cmd = (f"cat > {self.root / MOCKS} <<'EOF'\nexport const e = '{EMAIL}';\nEOF\n"
               f"cat > {self.root / 'src/other/mocks.ts'} <<'EOF'\nconst e = '{EMAIL}';\nEOF")
        self.denied(self.hook("run_shell_command", {"command": cmd}))

    def test_a_relative_path_is_resolved_from_the_session_cwd(self):
        content = f"export const e = '{EMAIL}';\n"
        self.allowed(self.hook("write_file", {"file_path": MOCKS, "content": content}))
        module = self.root / "src" / "modules" / "Modal"
        module.mkdir(parents=True, exist_ok=True)
        self.allowed(self.hook("write_file", {"file_path": "mocks.ts", "content": content}, root=module),
                     "cwd внутри модуля: относительный mocks.ts — тот же файл")
        # mocks.ts в корне репо — не файл модуля
        self.denied(self.hook("write_file", {"file_path": "mocks.ts", "content": content}, root=self.root))

    def test_output_dir_is_compared_without_case(self):
        """Файловые системы macOS и Windows регистр не различают: outputDir в конфиге с заглавной — тот же каталог."""
        cfg = self.root / ".daiver" / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("src/modules", text)
        cfg.write_text(text.replace("src/modules", "src/Modules"), encoding="utf-8")
        self.allowed(self.write("src/modules/Modal/mocks.ts", f"export const e = '{EMAIL}';\n"))
        self.allowed(self.write("src/Modules/Modal/mocks.ts", f"export const e = '{EMAIL}';\n"))

    def test_output_dir_dot_is_the_whole_repo_and_gets_no_exception(self):
        """outputDir «.» — весь репо: «файл модуля» там не определить, исключения нет (в том числе для mocks.ts в корне)."""
        cfg = self.root / ".daiver" / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn('outputDir: "src/modules"', text)
        cfg.write_text(text.replace('outputDir: "src/modules"', 'outputDir: "."'), encoding="utf-8")
        for rel in (MOCKS, "mocks.ts", "src/__mocks__/a.ts"):
            with self.subTest(rel=rel):
                self.denied(self.write(rel, f"export const e = '{EMAIL}';\n"))

    def test_a_config_that_does_not_pass_its_own_check_gives_no_exception(self):
        """Конфиг без buildCommand — проект не инициализирован (daiver_status: initialized false): outputDir в нём — дефолт движка."""
        cfg = self.root / ".daiver" / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("buildCommand", text)
        cfg.write_text("\n".join(l for l in text.splitlines() if "buildCommand" not in l) + "\n", encoding="utf-8")
        r = self.write(MOCKS, f"export const e = '{EMAIL}';\n")
        self.denied(r)
        self.assertNotIn("макета", r.stderr, "хук как раньше")

    def test_a_symlinked_output_dir_still_counts(self):
        (self.root / "real").mkdir()
        (self.root / "src" / "modules").rmdir()
        (self.root / "src" / "modules").symlink_to(self.root / "real", target_is_directory=True)
        self.allowed(self.write(MOCKS, f"export const e = '{EMAIL}';\n"))
        self.allowed(self.write("real/Modal/mocks.ts", f"export const e = '{EMAIL}';\n"))


class MockupExemptionIsNarrow(MockupWorld):
    """Нет движка, трека, конфига или канона — хук ровно прежний (и по тексту отказа тоже)."""

    CONTENT = f"export const email = '{EMAIL}';\n"

    def test_no_active_run_at_all(self):
        shutil.rmtree(self.root / "ground")
        r = self.write(MOCKS, self.CONTENT)
        self.denied(r)
        self.assertNotIn("макета", r.stderr)

    def test_the_active_run_is_another_branch(self):
        shutil.rmtree(self.root / "ground")
        self.manifest("feature-pipeline", self.KEY)
        r = self.write(MOCKS, self.CONTENT)
        self.denied(r)
        self.assertNotIn("макета", r.stderr)
        shutil.rmtree(self.root / "ground")
        self.manifest("forgefix", self.KEY)
        self.denied(self.write(MOCKS, self.CONTENT))

    def test_no_config(self):
        (self.root / ".daiver" / "config.yaml").unlink()
        r = self.write(MOCKS, self.CONTENT)
        self.denied(r)
        self.assertNotIn("макета", r.stderr)

    def test_no_engine_in_the_delivery(self):
        """Бэк-профиль поставки: рядом с hooks/ нет skills/forgeui — как раньше, без исключения."""
        fake = Path(self.tmp.name) / "fake-forge"
        shutil.copytree(HOOKS, fake / "hooks", ignore=shutil.ignore_patterns("__pycache__", "evals", "tests", "test_*.py"))
        self.assertFalse((fake / "skills").exists())
        r = self.write(MOCKS, self.CONTENT, hook_path=fake / "hooks" / "pii-boundary.py")
        self.denied(r)
        self.assertNotIn("макета", r.stderr)
        # а с движком рядом тот же хук тот же файл пропускает
        shutil.copytree(HOOKS.parent / "skills" / "forgeui", fake / "skills" / "forgeui",
                        ignore=shutil.ignore_patterns("__pycache__", "fixtures"))
        self.allowed(self.write(MOCKS, self.CONTENT, hook_path=fake / "hooks" / "pii-boundary.py"))

    def test_no_track(self):
        shutil.rmtree(self.root / ".daiver" / "tracks")
        r = self.write(MOCKS, self.CONTENT)
        self.denied(r, "тексты макета не получены")

    def test_canon_is_not_there_yet(self):
        m = self.TT.TR.load(self.root, self.KEY)
        self.TT.TR.frame_paths(self.root, m)["canonical"].unlink()
        self.denied(self.write(MOCKS, self.CONTENT), "тексты макета не получены")

    def test_canon_is_damaged(self):
        m = self.TT.TR.load(self.root, self.KEY)
        self.TT.TR.frame_paths(self.root, m)["canonical"].write_text("{не json", encoding="utf-8")
        self.denied(self.write(MOCKS, self.CONTENT), "тексты макета не получены")

    def test_a_string_in_the_occluded_part_of_the_mockup_is_not_a_mockup_text(self):
        self.canon([self.TT.frame("hidden", [self.TT.text("t", EMAIL)], occluded=True), self.TT.text("v", "Виден")])
        self.denied(self.write(MOCKS, self.CONTENT), "нет дословно")

    def test_writes_without_pii_are_not_touched(self):
        """Исключение живёт только на отказе: чистая запись проходит как раньше, и канон не читается."""
        shutil.rmtree(self.root / ".daiver" / "tracks")
        self.allowed(self.write(MOCKS, "export const title = 'Контакты';\n"))
        self.allowed(self.write("src/modules/Modal/Modal.tsx", "export const A = () => null;\n"))

    def test_back_end_denials_are_unchanged(self):
        for path, content in (("src/main/java/X.java", f"// {EMAIL}"), ("src/main/resources/application.yml", "password: hunter2secret"),
                              ("docs/feature-pipeline/F/sdd.md", "звонить 8-999-123-45-67")):
            with self.subTest(path=path):
                r = self.write(path, content)
                self.denied(r)
                self.assertNotIn("макета", r.stderr)


class MockupExemptionFailsClosed(MockupWorld):
    """Сбой сверки с макетом не пропускает PII молча. Общий `except` хука fail-OPEN, поэтому исключение обязано быть
    закрыто САМО: любая ошибка внутри сверки и даже потеря её имени — отказ."""

    @classmethod
    def hook_module(cls):
        spec = importlib.util.spec_from_file_location("pii_boundary_under_test", HOOK)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def run_main(self, m, content):
        import io
        from unittest import mock
        payload = json.dumps({"hook_event_name": "PreToolUse", "cwd": str(self.root), "tool_name": "write_file",
                              "tool_input": {"file_path": str(self.root / MOCKS), "content": content}})
        err = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(payload)), mock.patch("sys.stderr", err):
            code = m.main()
        return code, err.getvalue()

    CONTENT = f"export const email = '{EMAIL}';\n"

    def test_the_baseline_in_process(self):
        code, _ = self.run_main(self.hook_module(), self.CONTENT)
        self.assertEqual(code, 0)

    def test_an_unexpected_engine_error_is_not_a_pass(self):
        from unittest import mock
        m = self.hook_module()
        with mock.patch("importlib.import_module", side_effect=RuntimeError("engine is broken")):
            code, err = self.run_main(m, self.CONTENT)
        self.assertEqual(code, 2, err)
        self.assertIn("[pii-boundary] DENY", err)

    def test_an_unexpected_error_inside_the_helper_is_not_a_pass(self):
        from unittest import mock
        m = self.hook_module()
        with mock.patch.object(m, "_pii_hits", side_effect=ValueError("boom")):
            code, _ = self.run_main(m, self.CONTENT)
        self.assertEqual(code, 2)
        with mock.patch.object(m, "_under_out", side_effect=OSError("disk")):
            code, _ = self.run_main(m, self.CONTENT)
        self.assertEqual(code, 2)

    def test_an_empty_list_of_matches_is_not_a_pass(self):
        """Хук нашёл PII, а сверка списка не получила ни одной строки — рассогласование: пропускать нечего."""
        from unittest import mock
        m = self.hook_module()
        with mock.patch.object(m, "_pii_hits", return_value=[]):
            code, _ = self.run_main(m, self.CONTENT)
        self.assertEqual(code, 2)

    def test_the_lost_name_of_the_helper_is_not_a_pass(self):
        m = self.hook_module()
        del m._mockup_verdict                   # как «потерянная функция под except»: NameError на вызове
        code, err = self.run_main(m, self.CONTENT)
        self.assertEqual(code, 2, err)

    def test_the_lost_names_inside_the_helper_are_not_a_pass(self):
        for name in ("_pii_hits", "_out_dirs", "_under_out"):
            with self.subTest(name=name):
                m = self.hook_module()
                delattr(m, name)
                code, _ = self.run_main(m, self.CONTENT)
                self.assertEqual(code, 2, f"без {name} запись прошла")

    def test_a_texts_module_that_raises_something_else_than_textserror(self):
        from unittest import mock
        m = self.hook_module()
        sys.path.insert(0, str(SCRIPTS))
        from daiver import texts as TX
        with mock.patch.object(TX, "mockup_texts", side_effect=KeyError("x")):
            code, _ = self.run_main(m, self.CONTENT)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
