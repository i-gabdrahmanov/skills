#!/usr/bin/env python3
"""Tests for hooks/sod-enforcer.py — роль из активного шага манифеста.

test-фаза не пишет src/main; design/spec не коммитят/пушат/билдят; dev пишет src/main свободно.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "sod-enforcer.py"


def _make(tmp: Path, active_step: str | None, slug: str = "feat") -> None:
    d = tmp / "ground" / "statements" / "feature-pipeline" / slug
    d.mkdir(parents=True, exist_ok=True)
    steps = []
    if active_step:
        steps = [{"id": active_step, "status": "in_progress"}]
    (d / "manifest.json").write_text(json.dumps({
        "context": {"feature": slug}, "steps": steps,
    }), encoding="utf-8")


def _run(tmp: Path, payload: dict) -> int:
    payload = {**payload, "cwd": str(tmp)}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True).returncode


class TestSod(unittest.TestCase):
    def test_test_role_blocks_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)

    def test_test_role_allows_src_test(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/test/java/XTest.java")}}), 0)

    def test_dev_role_allows_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-build-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    def test_design_role_blocks_build_cmd(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-design")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew build"}}), 2)

    def test_git_commit_push_not_gated(self):
        # Доставка — на пользователе: git commit/push роли не гейтят ни в одной фазе.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}}), 0)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git push origin main"}}), 0)

    def test_no_active_step_failopen(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, None)
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 0)

    def test_jira_role_blocks_src_write(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "03-jira")
            self.assertEqual(_run(tmp, {"tool_name": "Write",
                "tool_input": {"file_path": str(tmp / "src/main/java/X.java")}}), 2)

    def test_spec_role_blocks_build_but_not_git(self):
        # spec-фаза не билдит, но git-команды свободны (доставка — на пользователе).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "02-sdd")
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "./gradlew build"}}), 2)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}}), 0)
            self.assertEqual(_run(tmp, {"tool_name": "Bash",
                "tool_input": {"command": "git push origin main"}}), 0)


def _make_real(tmp: Path, order: list[str], done_upto: str, slug: str = "feat") -> None:
    """Манифест КАК НА ЖИВОМ ПРОГОНЕ: закрытые шаги + pending-хвост, БЕЗ in_progress."""
    d = tmp / "ground" / "statements" / "forgefix" / slug
    d.mkdir(parents=True, exist_ok=True)
    idx = order.index(done_upto)
    steps = [{"id": s, "status": "completed" if i <= idx else "pending",
              "depends_on": [order[i - 1]] if i else []} for i, s in enumerate(order)]
    (d / "manifest.json").write_text(json.dumps(
        {"context": {"feature": slug}, "steps": steps}), encoding="utf-8")


class RealManifestNoInProgress(unittest.TestCase):
    """Регресс: роль резолвилась только по `in_progress`, которого на живых прогонах нет →
    SoD молчал всегда, хотя числился активным enforcement'ом."""

    FIX = ["fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"]

    def test_red_phase_blocks_src_main_without_in_progress(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-diag", slug="BUG-1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file", "tool_input": {
                "file_path": str(tmp / "src/main/java/A.java")}}), 2)

    def test_green_phase_allows_src_main(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-red", slug="BUG-1")
            self.assertEqual(_run(tmp, {"tool_name": "write_file", "tool_input": {
                "file_path": str(tmp / "src/main/java/A.java")}}), 0)

    def test_verify_phase_hint_points_to_reopening_impl_step(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_real(tmp, self.FIX, "fix-green", slug="BUG-1")
            r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(
                {"cwd": str(tmp), "tool_name": "write_file",
                 "tool_input": {"file_path": str(tmp / "src/main/java/A.java")}}),
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("fix-green", r.stderr)      # куда вернуться легально
            self.assertIn("max_step_reopens", r.stderr)


class TBattle046(unittest.TestCase):
    """Боевой прогон v0.4.6: SoD-щели (track C F-2, L-17a/b)."""

    def test_maven_wrapper_is_a_build(self):
        """`./mvnw` — штатный запуск Spring-проекта — не совпадал с `\\bmvn\\b`."""
        for step in ("02-sdd", "02-design", "03-jira"):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make(tmp, step)
                self.assertEqual(_run(tmp, {"tool_name": "run_shell_command",
                                            "tool_input": {"command": "./mvnw test"}}), 2)

    def test_build_regex_is_one_constant(self):
        """Константа продублирована в inline-phase-guard — расхождение снова открыло бы mvnw."""
        import importlib.util
        mods = []
        for name in ("sod-enforcer.py", "inline-phase-guard.py"):
            spec = importlib.util.spec_from_file_location(name.replace("-", "_")[:-3],
                                                          HOOK.parent / name)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            mods.append(m.BUILD_CMD_RE)
        self.assertEqual(mods[0], mods[1])

    def test_shell_write_into_blocked_path(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            for cmd in ("echo 'class Probe {}' > src/main/java/Foo.java",
                        "echo x > src/main/resources/application.yml"):
                with self.subTest(cmd=cmd):
                    self.assertEqual(_run(tmp, {"tool_name": "run_shell_command",
                                                "tool_input": {"command": cmd}}), 2)
            self.assertEqual(_run(tmp, {"tool_name": "run_shell_command",
                                        "tool_input": {"command": "echo x > src/test/java/T.java"}}), 0)

    def test_edit_stub_checked(self):
        """У Edit пишется new_string — стаб правкой проходил мимо blocked_content_patterns."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make(tmp, "04-test-T1")
            stub = 'throw new UnsupportedOperationException("not implemented");'
            self.assertEqual(_run(tmp, {"tool_name": "edit", "tool_input": {
                "file_path": str(tmp / "src/test/java/XTest.java"),
                "old_string": "x", "new_string": stub}}), 2)


# ── фронтовые команды сборки (forge-ui; задача 3.4) ────────────────────────────────────────────────────────────
UI_STEPS = ["ui-spec", "ui-design", "ui-build", "ui-verify"]


def _make_ui(tmp: Path, current: str, slug: str = "STOR-100") -> None:
    """Манифест прогона forgeui КАК НА ЖИВОМ: шаги до `current` закрыты, `current` и дальше — pending."""
    d = tmp / "ground" / "statements" / "forgeui" / slug
    d.mkdir(parents=True, exist_ok=True)
    steps, done, prev = [], True, None
    for sid in UI_STEPS:
        if sid == current:
            done = False
        steps.append({"id": sid, "status": "completed" if done else "pending", "depends_on": [prev] if prev else []})
        prev = sid
    (d / "manifest.json").write_text(json.dumps({"context": {"feature": slug}, "steps": steps}), encoding="utf-8")


def _bash_rc(tmp: Path, cmd: str) -> int:
    return _run(tmp, {"tool_name": "run_shell_command", "tool_input": {"command": cmd}})


FRONT_BUILDS = (
    "npm run build", "npm run test", "npm test", "npm run-script build", "npm run lint", "pnpm build", "pnpm test", "pnpm run build",
    "pnpm exec tsc", "yarn build", "yarn test", "yarn build:prod", "yarn workspace app build", "npx tsc --noEmit", "npx tsc",
    "npx vite build", "npx playwright test", "npx nx run app:build", "tsc -p .", "tsc --noEmit", "vite build", "vite", "playwright test",
    "./node_modules/.bin/tsc", "node_modules/.bin/vite build", "cd web && npm run build", "cd web; npm test", "(cd web && npm run build)",
    "CI=1 npm test", "NODE_ENV=production npm run build", "sudo npm run build", "env CI=1 npm test", "time npm test", "timeout 600 npm test",
    "xvfb-run npx playwright test", "bash -c 'cd web && npm run build'", "sh -c \"npm test\"", "npm --prefix web run build",
    "pnpm --filter app build", "yarn --cwd web build", "npm.cmd run build", "echo ok | npm test", "npm ci && npm run build",
    "echo $(npm run build)", "echo `npm test`", "nohup npm run build", "exec npm test", "command npm test", "doas npm test",
    "sudo -E npm run build", "env -i CI=1 npm test", "timeout --foreground 600 npm test", "/usr/local/bin/npm run build",
    "npm --prefix web --silent run build", "npm run-script lint", "pnpm exec eslint .", "yarn tsc --noEmit", "pnpm vite",
    "yarn playwright show-report", "xvfb-run -a npm test", "echo hi\nnpm run build", "(npm test)", "(npm run build)",
    "bash -lc 'npm run build'", "sh -ec \"npm test\"",
)
# не сборка: путь проекта и слова в аргументах, установка и справки
FRONT_NOT_BUILDS = (
    "ls vite.config.ts", "cat vite.config.ts", "grep playwright package.json", "grep -rn tsc src/", "cat tsconfig.json",
    "git log --oneline -- vite.config.ts", "echo vite", "echo 'npm run build'", "git commit -m 'npm run build is slow'",
    "grep -n \"npm run\" README.md", "cd /home/me/vite-app && ls", "ls /home/me/vite/", "cat /work/playwright/README.md",
    "npm install", "npm ci", "npm install --save-dev typescript", "npm ls", "npm view react version", "npm config get registry",
    "yarn install", "yarn add react", "pnpm install", "npm i vite-plugin-svgr", "yarn add -D vite-plugin-svgr", "npm --version", "which npm",
    "cat package.json", "node scripts/x.js", "rm -rf node_modules", "mkdir -p src/modules/vite", "cp vite.config.ts /tmp/",
    "less playwright.config.ts", "python3 /home/me/vite-app/x.py --project /home/me/vite-app",
    "npm install lodash react redux axios test",           # подкоманда дальше четвёртого токена — не она
    "python3 .gigacode/skills/pipeline-state/scripts/record_gate.py --project /work/playwright-tests --skill forgeui --feature STOR-100 "
    "--step-id ui-design --cmd \"python3 .gigacode/skills/forgeui/scripts/ui_gate.py design --project /work/playwright-tests --feature STOR-100\"",
    "python3 .gigacode/skills/forgeui/scripts/ui_gate.py design --project /home/dev/tsc-tools --feature STOR-100",
)


class TFrontBuildCommands(unittest.TestCase):
    """`BUILD_CMD_RE` знает npm/pnpm/yarn/npx, tsc, vite, playwright. Роли spec/design/jira их НЕ запускают; роль dev (ui-build) — ДОЛЖНА:
    красный билд чинит сам субагент `npm run build` / `npx tsc`. Путь проекта с `vite`/`playwright` и слова в аргументах — не сборка."""

    def test_spec_and_design_roles_do_not_build_the_front(self):
        for step in ("ui-spec", "ui-design"):
            for cmd in FRONT_BUILDS:
                with self.subTest(step=step, cmd=cmd), tempfile.TemporaryDirectory() as d:
                    tmp = Path(d); _make_ui(tmp, step)
                    self.assertEqual(_bash_rc(tmp, cmd), 2, f"сборка фронта на {step} прошла: {cmd}")

    def test_full_and_fix_branches_do_not_build_the_front_in_design_either(self):
        for step in ("02-sdd", "02-design", "03-jira"):
            for cmd in ("npm run build", "npx tsc --noEmit", "yarn test"):
                with self.subTest(step=step, cmd=cmd), tempfile.TemporaryDirectory() as d:
                    tmp = Path(d); _make(tmp, step)
                    self.assertEqual(_bash_rc(tmp, cmd), 2)

    def test_dev_role_may_build_and_fix_a_red_build(self):
        """ui-build: роль dev; красный билд субагент чинит сам и перепроверяет теми же командами."""
        for cmd in FRONT_BUILDS:
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make_ui(tmp, "ui-build")
                self.assertEqual(_bash_rc(tmp, cmd), 0, f"роль dev не может: {cmd}")

    def test_test_role_may_run_the_checks(self):
        for cmd in ("npx playwright test", "npm run test", "npx tsc --noEmit", "vite build"):
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make_ui(tmp, "ui-verify")
                self.assertEqual(_bash_rc(tmp, cmd), 0)

    def test_words_in_arguments_and_paths_are_not_a_build(self):
        for step in ("ui-spec", "ui-design"):
            for cmd in FRONT_NOT_BUILDS:
                with self.subTest(step=step, cmd=cmd[:80]), tempfile.TemporaryDirectory() as d:
                    tmp = Path(d); _make_ui(tmp, step)
                    self.assertEqual(_bash_rc(tmp, cmd), 0, f"ложный отказ на {step}: {cmd}")

    def test_back_end_builds_are_blocked_as_before_and_git_is_free(self):
        for step in ("02-sdd", "02-design", "03-jira"):
            for cmd in ("./gradlew build", "./gradlew test --tests X", "./mvnw -q test", "mvn -q verify", "ktlint", "checkstyle -c c.xml",
                        "detekt --input src", "spotless apply", "cd svc && ./gradlew test"):
                with self.subTest(step=step, cmd=cmd), tempfile.TemporaryDirectory() as d:
                    tmp = Path(d); _make(tmp, step)
                    self.assertEqual(_bash_rc(tmp, cmd), 2)
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d); _make(tmp, step)
                for cmd in ("git commit -m x", "git push origin main", "ls", "cat pom.xml"):
                    self.assertEqual(_bash_rc(tmp, cmd), 0, cmd)

    def test_the_denial_says_what_is_forbidden_without_a_wall_of_regex(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); _make_ui(tmp, "ui-design")
            r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(tmp), "tool_name": "run_shell_command",
                               "tool_input": {"command": "npm run build"}}), capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("npm|pnpm|yarn|npx", r.stderr)
            self.assertNotIn("(?:", r.stderr)

    def test_long_commands_are_scanned_in_linear_time(self):
        import importlib.util
        import re
        import time
        spec = importlib.util.spec_from_file_location("sod_enforcer_perf", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for cmd in ("echo " + "a" * 256000, "npm " + "a " * 100000, "npm " + "a" * 200000 + " run", ";" * 100000 + "npm",
                    "FOO=1 " * 50000 + "x", "sudo " * 50000, "cd x && " * 30000 + "ls", "\n".join(["echo x"] * 50000),
                    "npm " + " ".join(["run"] * 60000), " ".join(["xvfb-run -a"] * 20000), "a/" * 100000):
            with self.subTest(n=len(cmd)):
                t0 = time.time()
                re.search(mod.BUILD_CMD_RE, cmd)
                self.assertLess(time.time() - t0, 1.0, "BUILD_CMD_RE квадратичен по длине команды")


if __name__ == "__main__":
    unittest.main()
