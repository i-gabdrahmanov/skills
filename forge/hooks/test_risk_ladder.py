#!/usr/bin/env python3
"""Tests for hooks/risk_ladder.py"""
from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk_ladder as mod


class TestBasic(unittest.TestCase):
    """Module imports correctly."""
    def test_function_level_order_exists(self):
        self.assertTrue(hasattr(mod, "level_order"))
    def test_function_load_policy_exists(self):
        self.assertTrue(hasattr(mod, "load_policy"))
    def test_function_pipeline_cfg_exists(self):
        self.assertTrue(hasattr(mod, "pipeline_cfg"))
    def test_function_auto_max_risk_exists(self):
        self.assertTrue(hasattr(mod, "auto_max_risk"))
    def test_function_criticality_set_exists(self):
        self.assertTrue(hasattr(mod, "criticality_set"))


class TestNormalizeGit(unittest.TestCase):
    """Сворачивание глобальных опций git перед подкомандой — закрывает обход
    `git -C <p> push`/`git -c k=v commit` детекторов delivery/SoD/force-push/классификатора."""

    def test_strip_dash_C(self):
        self.assertEqual(mod.normalize_git_command("git -C . push origin main"),
                         "git push origin main")

    def test_strip_dash_c_config(self):
        self.assertEqual(mod.normalize_git_command("git -c user.name=x commit -m y"),
                         "git commit -m y")

    def test_strip_multiple_globals(self):
        self.assertEqual(mod.normalize_git_command("git -C /r -c a=b push -f"),
                         "git push -f")

    def test_strip_git_dir_eq(self):
        self.assertEqual(mod.normalize_git_command("git --git-dir=/x/.git push"),
                         "git push")

    def test_plain_push_unchanged(self):
        self.assertEqual(mod.normalize_git_command("git push origin x"), "git push origin x")

    def test_commit_reuse_C_preserved(self):
        # -C у самой подкоммандой commit (reuse message) НЕ трогаем — сворачиваем лишь ведущий кластер
        self.assertEqual(mod.normalize_git_command("git commit -C HEAD"), "git commit -C HEAD")

    def test_classify_git_push_not_escalated(self):
        # Доставка — на пользователе: git push/commit больше не классифицируются рисковыми
        info = mod.classify("run_shell_command", {"command": "git -C . push origin main"}, ".")
        self.assertEqual(info["level"], "R1")

    def test_classify_git_commit_not_escalated(self):
        info = mod.classify("run_shell_command", {"command": "git -c a=b commit -m x"}, ".")
        self.assertEqual(info["level"], "R1")


class TestPolicyLoaded(unittest.TestCase):
    """H2: load_policy различает loaded / missing / corrupt — fail-closed на пропаже политики."""
    def setUp(self):
        self._orig = mod._POLICY_PATH

    def tearDown(self):
        mod._POLICY_PATH = self._orig

    def test_loaded_real_policy(self):
        # Реальная co-located risk-policy.json парсится → loaded.
        self.assertTrue(mod.policy_loaded())

    def test_missing_policy(self):
        mod._POLICY_PATH = Path("/nonexistent/dir/risk-policy.json")
        self.assertFalse(mod.policy_loaded())
        self.assertEqual(mod.load_policy(), {})

    def test_corrupt_policy(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ broken json")
            bad = Path(f.name)
        try:
            mod._POLICY_PATH = bad
            self.assertFalse(mod.policy_loaded())
            self.assertEqual(mod.load_policy(), {})
        finally:
            bad.unlink()


class TestDryRefactor(unittest.TestCase):
    """DRY: pipeline_cfg остаётся legacy-only (только pipeline.json, policy.json игнорирует);
    config_get использует _config_loader.load_project_config (dual-read: policy → pipeline → {})."""

    def test_pipeline_cfg_legacy_only_returns_empty_on_v2_only(self):
        """pipeline_cfg должен возвращать только legacy pipeline.json (не policy.json).
        На v2-only проекте (нет pipeline.json) → {}."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 7}}),
                encoding="utf-8",
            )
            cfg = mod.pipeline_cfg(root)
            self.assertEqual(cfg, {}, f"pipeline_cfg should ignore policy.json, got {cfg}")

    def test_pipeline_cfg_legacy_only_reads_pipeline_json(self):
        """pipeline_cfg читает именно pipeline.json (legacy semantics preserved)."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "pipeline.json").write_text(
                _json.dumps({"autonomy": {"auto_max_risk": "R3"}}),
                encoding="utf-8",
            )
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 7}}),
                encoding="utf-8",
            )
            cfg = mod.pipeline_cfg(root)
            self.assertEqual(cfg.get("autonomy", {}).get("auto_max_risk"), "R3")
            self.assertNotIn("quality", cfg, "pipeline_cfg must not include policy.json content")

    def test_config_get_dual_read_picks_up_v2_policy(self):
        """config_get должен читать quality.* из policy.json на v2-only проекте."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 9}}),
                encoding="utf-8",
            )
            val = mod.config_get(root, "quality.max_step_reopens")
            self.assertEqual(val, 9, f"expected 9, got {val}")

    def test_config_get_dual_read_falls_back_to_legacy_pipeline(self):
        """config_get на v1-only проекте (только pipeline.json) должен вернуть значение через legacy fallback."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "pipeline.json").write_text(
                _json.dumps({"sources": {"spec": "PROJ-legacy"}}),
                encoding="utf-8",
            )
            val = mod.config_get(root, "sources.spec")
            self.assertEqual(val, "PROJ-legacy", f"expected legacy fallback, got {val}")

    def test_pipeline_cfg_no_warning_on_v2_only(self):
        """pipeline_cfg НЕ должен спамить DeprecationWarning на v2-only проектах
        (где pipeline.json не существует). Warning — только при ФАКТИЧЕСКОМ чтении legacy."""
        import json as _json
        import warnings as _warnings
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "policy.json").write_text(
                _json.dumps({"quality": {"max_step_reopens": 7}}),
                encoding="utf-8",
            )
            with _warnings.catch_warnings(record=True) as caught:
                _warnings.simplefilter("always")  # не давить дедупликацию
                cfg = mod.pipeline_cfg(root)
            deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            self.assertEqual(cfg, {}, f"v2-only should return empty, got {cfg}")
            self.assertEqual(
                len(deprecation_warnings), 0,
                f"unexpected DeprecationWarning on v2-only project: {[str(w.message) for w in deprecation_warnings]}",
            )

    def test_pipeline_cfg_warning_on_legacy(self):
        """pipeline_cfg ДОЛЖЕН спамить DeprecationWarning когда legacy pipeline.json читается."""
        import json as _json
        import warnings as _warnings
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "ground").mkdir(parents=True)
            (root / "ground" / "pipeline.json").write_text(
                _json.dumps({"b": 2}),
                encoding="utf-8",
            )
            with _warnings.catch_warnings(record=True) as caught:
                _warnings.simplefilter("always")
                cfg = mod.pipeline_cfg(root)
            deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            self.assertEqual(cfg, {"b": 2})
            self.assertGreaterEqual(
                len(deprecation_warnings), 1,
                "expected DeprecationWarning on legacy read",
            )
            self.assertTrue(
                any("pipeline.json is deprecated" in str(w.message) for w in deprecation_warnings),
                f"unexpected warning text: {[str(w.message) for w in deprecation_warnings]}",
            )


class TestReadOnlyCommand(unittest.TestCase):
    """Задача 011: закрытый allowlist читателей. Любая неуверенность = не read-only."""

    READ_ONLY = [
        "cat src/main/resources/application.yml",
        "grep -rn foo src/main/java/com/x/auth/S.java",
        "ls -la", "git diff --stat", "git log --oneline -5",
        "cat x.yml 2>/dev/null", "sed -n '1,5p' file", "find . -name '*.java'",
        "head -5 a.txt | grep foo", "wc -l pom.xml",
    ]
    NOT_READ_ONLY = [
        "cat x.yml > y.yml", "sed -i 's/a/b/' file", "find . -name '*.tmp' -delete",
        "./gradlew test", "python3 x.py", "cat a | tee b", "git push origin main",
        "rm -rf build", "awk '{print > \"out\"}' f", "sudo cat /etc/shadow",
    ]

    def test_read_only(self):
        for cmd in self.READ_ONLY:
            self.assertTrue(mod.is_read_only_command(cmd), cmd)

    def test_not_read_only(self):
        for cmd in self.NOT_READ_ONLY:
            self.assertFalse(mod.is_read_only_command(cmd), cmd)

    def test_empty_is_not_read_only(self):
        self.assertFalse(mod.is_read_only_command(""))


class TestStepRequirementPhaseAliases(unittest.TestCase):
    """Задача 009: `level_requirements.steps` ссылается на full-имя фазы (`02-design`).

    В fix-манифестах его нет НИКОГДА (там `fix-diag`), поэтому требование
    R2 — а это `src/main/**.java`, весь прод-код — было невыполнимо в принципе."""

    REQ = {"mode": "require", "steps": ["02-design"]}

    def _check(self, status: dict):
        with unittest.mock.patch.object(mod, "manifest_status", lambda _root: status):
            return mod.check_requirement("R2", self.REQ, Path("/nonexistent"), "write")

    def test_fix_branch_alias_completed_allows(self):
        ok, why = self._check({"fix-intake": "completed", "fix-diag": "completed",
                               "fix-green": "pending"})
        self.assertTrue(ok, why)

    def test_fix_branch_alias_open_blocks(self):
        ok, why = self._check({"fix-intake": "completed", "fix-diag": "pending"})
        self.assertFalse(ok)
        self.assertIn("fix-diag", why)

    def test_full_branch_unchanged(self):
        self.assertTrue(self._check({"02-design": "completed"})[0])
        self.assertFalse(self._check({"02-design": "pending"})[0])

    def test_skipped_counts_as_done(self):
        self.assertTrue(self._check({"fix-diag": "skipped"})[0])

    def test_phase_absent_is_not_applicable(self):
        """Ветка без фазы дизайна вообще: требовать её — вечный тупик."""
        ok, why = self._check({"custom-1": "completed", "custom-2": "pending"})
        self.assertTrue(ok, why)

    def test_unreadable_manifest_still_denies(self):
        """Пустой status = манифест не распарсился. Это неясность → deny-first,
        а не «фазы нет, значит можно»."""
        ok, why = self._check({})
        self.assertFalse(ok)
        self.assertIn("манифест", why)



class TTargetPathIsLinear(unittest.TestCase):
    """Боевой прогон (ENC-3): `_target_path` на длинном токене без «/» перебирал разбиения
    квадратично (32K символов → 4,7 с, 256K → минуты), и gate-guard/tdd-guard упирались в
    таймаут хука — а таймаут рантайм читает как «возражений нет»."""

    def test_long_token_is_fast(self):
        import time
        t0 = time.time()
        mod._target_path("run_shell_command", {"command": "echo " + "a" * 256000})
        self.assertLess(time.time() - t0, 1.0)

    def test_same_targets_as_before(self):
        cases = {
            "cat src/main/resources/application.yml": "src/main/resources/application.yml",
            "rm -rf ./target": "./target",
            "vim Foo.java pom.xml": "Foo.java pom.xml",
            "cp db/changelog/master.yaml /tmp/x": "db/changelog/master.yaml /tmp/x",
            "mvn -q test": "",
        }
        for cmd, want in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(mod._target_path("run_shell_command", {"command": cmd}), want)

class TPathRiskIsLinear(unittest.TestCase):
    """path_risk-паттерны вида `(?i).*(risk[-_]?model|…).*` под re.search квадратичны: 32К
    символов `a/a/…` в цели давали 52 с, таймаут хука — «возражений нет» (боевой прогон
    v0.4.6, E-REPOS-GG). Цель Bash — склейка путей команды, heredoc с кодом даёт десятки КБ."""

    def test_long_target_is_fast(self):
        import time
        for tool, ti in (("run_shell_command", {"command": "cat " + "a/" * 4096}),
                         ("write_file", {"file_path": "a/" * 4096, "content": "x"})):
            with self.subTest(tool=tool):
                t0 = time.time()
                mod.classify(tool, ti)
                self.assertLess(time.time() - t0, 1.5, "классификация квадратична по длине цели")

    def test_leading_dotstar_strip_keeps_answers(self):
        for path, want in (("src/main/resources/application-prod.yml", "R4"),
                           ("svc/src/main/java/a/Foo.java", "R2"),
                           ("src/test/java/FooTest.java", "R1"),
                           ("docs/notes.md", "R0"),
                           ("db/migration/V2__x.sql", "R4"),
                           ("src/main/java/auth/Login.java", "R3"),
                           ("config/risk-model.json", "R5")):
            with self.subTest(path=path):
                self.assertEqual(mod.classify("write_file", {"file_path": path})["level"], want)

    def test_lazy_dotstar_is_not_mangled(self):
        """`.*?x` — не ведущий жадный `.*`: срезать нечего, паттерн обязан компилироваться."""
        self.assertTrue(mod._path_re("(?i).*?prod").search("x/PROD/y"))


# ── фронтовые пути (forge-ui; задача 3.4) ────────────────────────────────────────────────────────────
class TFrontendPathRisk(unittest.TestCase):
    """Прод-код фронта `.tsx/.jsx/.vue/.svelte` — R2 (нужен закрытый design-шаг), тесты фронта — R1; граница с тестами та же,
    что у `_project.is_test_path`. Бэковые пути и тесты не меняются (golden-таблица ниже снята со старой политики)."""

    FRONT_EXT = ("tsx", "jsx", "vue", "svelte")
    BEFORE = {   # путь → уровень до задачи 3.4 (политика на момент 3.3)
        "src/main/java/com/x/Foo.java": "R2", "svc/src/main/kotlin/a/Foo.kt": "R2", "src/test/java/com/x/FooTest.java": "R1",
        "src/test/kotlin/a/FooTest.kt": "R1", "src/test/resources/fixtures/users.json": "R1",
        "src/main/resources/application.yml": "R1", "src/main/resources/application-prod.yml": "R4",
        "src/main/resources/application-dev.properties": "R1", "db/migration/V2__x.sql": "R4",
        "src/main/resources/db/changelog/db.changelog-master.xml": "R4", "src/main/java/com/x/auth/Login.java": "R3",
        "src/main/java/com/x/payment/Pay.java": "R3", "config/risk-model.json": "R5", "docs/notes.md": "R0",
        "/repo/docs/sdd.md": "R0", "README.md": "R0", "pom.xml": "R1", "build.gradle": "R1",
        "ground/statements/forgefix/F1/manifest.json": "R1", "src/main/webapp/index.html": "R1",
        "src/main/java/com/x/FooIT.java": "R2", "tools/run_tests.py": "R1", "scripts/test_helper.py": "R1",
        "src/main/resources/secrets/keystore.jks": "R3", "deploy/prod/values.yaml": "R4",
        "src/test/java/com/x/auth/LoginTest.java": "R3", "frontend/src/utils/format.ts": "R1", "frontend/src/api/client.js": "R1",
        "frontend/src/styles/main.less": "R1", "frontend/package.json": "R1", "frontend/src/Foo.test.ts": "R1",
        "frontend/src/Foo.spec.ts": "R1", "frontend/e2e/login.e2e.spec.ts": "R1", "frontend/src/__mocks__/api.ts": "R1",
        "frontend/src/modules/Modal/mocks.ts": "R1",
    }

    @staticmethod
    def level(path: str) -> str:
        return mod.classify("write_file", {"file_path": path, "content": "x"})["level"]

    @staticmethod
    def shell(cmd: str) -> str:
        return mod.classify("run_shell_command", {"command": cmd})["level"]

    def test_front_production_code_is_r2(self):
        for ext in self.FRONT_EXT:
            for d in ("", "src/", "src/modules/Modal/", "web/src/components/", "app/", "/abs/work/app/src/", "src/features/Order Form/",
                      "C:\\work\\app\\src\\", "src/e2e/", "docs/examples/"):
                path = f"{d}Modal.{ext}"
                with self.subTest(path=path):
                    self.assertEqual(self.level(path), "R2")
        for path in ("src/App.TSX", "SRC/Modal.Vue", "src/Foo.stories.tsx", "src/test.tsx", "src/spec.jsx", "src/a.b.c.svelte",
                     "src/Contest.tsx", "src/Latest.vue", "src/Attestation.jsx", "src/Digit.svelte", "src/ModalTest.TSX"):
            with self.subTest(path=path):
                self.assertEqual(self.level(path), "R2", "регистр, лишние точки, файл по имени test/spec и слово «test» внутри имени — не тест")

    def test_not_front_code_is_not_r2(self):
        """Расширение — конец имени: `.tsx.map`, `.vue.orig` и опечатки — не код компонента; ts/js — не R2 (задача называет tsx/jsx/vue/svelte)."""
        for path in ("src/Foo.tsx.map", "src/Foo.tsxx", "src/Foo.vue.orig", "src/Foo.jsx.bak", "src/Foo.d.ts", "src/Foo.ts", "src/Foo.js",
                     "src/Foo.mjs", "src/Foo.less", "src/Foo.css", "src/tsx", "src/vue", "README.tsx.md", "src/Foo.svelte-kit"):
            with self.subTest(path=path):
                self.assertNotEqual(self.level(path), "R2")

    def test_front_tests_are_r1(self):
        for path in ("src/Button.test.tsx", "src/Button.spec.ts", "e2e/login.e2e.spec.ts", "src/Button.test.jsx", "src/Modal.spec.vue",
                     "src/Modal.test.svelte", "src/a.test.ts", "src/a.spec.mjs", "src/BUTTON.TEST.TSX",
                     "src/__tests__/Button.tsx", "src/tests/Button.jsx", "test/Button.vue", "src/spec/Modal.svelte", "src/specs/a.tsx",
                     "src/fixtures/Modal.tsx", "src/fixture/Modal.tsx", "src/testfixtures/Modal.tsx", "TESTS/Button.TSX",
                     "src/ModalTest.tsx", "src/ModalTests.jsx", "src/ModalIT.vue", "src/ModalITCase.svelte",   # CamelCase-суффикс, как у is_test_path
                     "C:\\work\\app\\src\\Button.test.tsx", "C:\\work\\app\\src\\__tests__\\Button.tsx"):
            with self.subTest(path=path):
                self.assertEqual(self.level(path), "R1")

    def test_the_test_patterns_are_stated_not_left_to_the_default(self):
        for path in ("src/Button.test.tsx", "src/Button.spec.ts", "e2e/login.e2e.spec.ts"):
            with self.subTest(path=path):
                info = mod.classify("write_file", {"file_path": path, "content": "x"})
                self.assertEqual(info["level"], "R1")
                self.assertTrue(info["reason"].startswith("path~"), info)

    def test_border_with_tests_is_the_one_of_is_test_path(self):
        """Для любого фронтового кодового файла: R2 ⇔ `_project.is_test_path` говорит «не тест»; тест — R1. Корпус — перебор
        каталогов (в т.ч. тестовых сегментов из `_TEST_DIR_SEGMENTS`) × имён (в т.ч. .test./.spec./.e2e.spec.)."""
        import itertools
        import _project as P
        dirs = ["", "src/", "src/modules/Modal/", "app/", "/abs/app/src/", "C:\\app\\src\\", "src/test/", "src/Test/", "src/tests/",
                "src/__tests__/", "src/fixtures/", "src/fixture/", "src/testfixtures/", "src/spec/", "src/specs/", "e2e/", "src/mocks/",
                "src/__mocks__/", "src/tests/unit/", "src/testing/", "src/contest/", "src/latest/", "src/specifics/"]
        names = ["Modal", "Modal.test", "Modal.spec", "Modal.e2e.spec", "test", "spec", "Modal.tests", "Modal.stories", "my.test.component",
                 "Modal-test", "Modal_test", "ModalTest", "Modal.spec.d"]
        checked = {"R2": 0, "R1": 0}
        for d, n, ext in itertools.product(dirs, names, self.FRONT_EXT):
            path = f"{d}{n}.{ext}"
            is_test = P.is_test_path(path)
            with self.subTest(path=path):
                self.assertEqual(self.level(path), "R1" if is_test else "R2", f"is_test_path={is_test}")
            checked["R1" if is_test else "R2"] += 1
        self.assertGreater(checked["R2"], 100)
        self.assertGreater(checked["R1"], 100, "корпус обязан содержать и тесты, и прод-код")

    def test_a_test_file_in_the_same_command_does_not_lower_the_production_file(self):
        for cmd in ("cp src/Foo.test.tsx src/modules/Foo.tsx", "mv src/Foo.tsx src/Foo.test.tsx", "echo > src/__tests__/a.tsx src/modules/b.tsx",
                    "sed -i s/a/b/ src/Foo.vue src/Foo.spec.vue", "cat > src/modules/Modal/Foo.tsx <<EOF",
                    "rm src/tests/a.tsx src/Foo.jsx"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.shell(cmd), "R2")
        for cmd in ("cat > src/Foo.test.tsx <<EOF", "cp a.tsx src/__tests__/b.tsx", "rm src/Foo.spec.vue src/tests/a.svelte", "npm run build",
                    "echo hi", "git status"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.shell(cmd), "R1")

    def test_higher_classes_still_win_for_front_files(self):
        self.assertEqual(self.level("src/auth/Login.tsx"), "R3")
        self.assertEqual(self.level("src/features/payment/Pay.tsx"), "R3")
        self.assertEqual(self.level("deploy/prod/Banner.tsx"), "R4")

    def test_back_end_paths_and_tests_are_what_they_were(self):
        for path, want in self.BEFORE.items():
            with self.subTest(path=path):
                self.assertEqual(self.level(path), want)

    def test_front_code_needs_a_closed_design_step(self):
        """R2 → `level_requirements`: шаг дизайна завершён (в ветке ui это `ui-design`, алиас фазы `02-design`)."""
        req = mod.requirement("R2")
        self.assertEqual(req.get("mode"), "require")
        for status, ok in (({"ui-spec": "completed", "ui-design": "completed", "ui-build": "pending"}, True),
                           ({"ui-spec": "completed", "ui-design": "pending", "ui-build": "pending"}, False)):
            with unittest.mock.patch.object(mod, "manifest_status", lambda _root, s=status: s):
                allowed, why = mod.check_requirement("R2", req, Path("/nonexistent"), "write")
            self.assertEqual(allowed, ok, why)
            if not ok:
                self.assertIn("ui-design", why)

    def test_long_front_targets_are_linear(self):
        import time
        for tool, ti in (("run_shell_command", {"command": "cat " + "a/" * 4096 + "x.tsx " * 300}),
                         ("write_file", {"file_path": "a/" * 4096 + "x.tsx"}),
                         ("write_file", {"file_path": "a" * 100000 + ".tsx"}),
                         ("run_shell_command", {"command": "echo " + "x.tsx " * 40000})):
            with self.subTest(tool=tool, n=len(str(ti))):
                t0 = time.time()
                mod.classify(tool, ti)
                self.assertLess(time.time() - t0, 1.5, "классификация фронтового пути квадратична по длине цели")


if __name__ == "__main__":
    unittest.main()
