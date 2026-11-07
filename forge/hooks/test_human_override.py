#!/usr/bin/env python3
"""test_human_override.py — команда от имени человека: снимает R4 на один вызов, модели закрыта."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS))
import forge_events as FE      # noqa: E402
import human_override as HO    # noqa: E402

BLOCKER = HOOKS / "destructive-blocker.py"


class FakeTTY(io.StringIO):
    """Терминал человека: читает код с экрана и вводит его (или ввод из `answer`)."""

    def __init__(self, answer=None):
        super().__init__()
        self.answer = answer

    def readline(self, *a):
        if self.answer is not None:
            return self.answer + "\n"
        m = re.search(r"Код подтверждения: (\w+)", self.getvalue())
        return (m.group(1).lower() if m else "") + "\n"   # регистр не важен


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "ground").mkdir()
        (self.root / "ground" / "policy.json").write_text(json.dumps({
            "$schema": "feature-pipeline/config@2", "project": {"name": "t"},
            "quality": {"tdd": True}}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def ho(self, *cmd, tty=None, env=None, isatty=True):
        out = io.StringIO()
        real = sys.stdout
        sys.stdout = out
        try:
            rc = HO.main(["--project", str(self.root), "--reason", "гейт неприменим", "--", *cmd],
                         env=env if env is not None else {}, tty=tty or FakeTTY(),
                         stdin_isatty=isatty)
        finally:
            sys.stdout = real
        return rc, out.getvalue()

    def policy(self):
        return json.loads((self.root / "ground" / "policy.json").read_text(encoding="utf-8"))


class HumanPathTest(Base):
    def test_r4_param_changes_and_consent_does_not_outlive_the_call(self):
        key = "policy-downgrade-quality.tdd"
        rc, out = self.ho("config", "set", "quality.tdd", "false")
        self.assertEqual(rc, 0, out)
        self.assertIs(self.policy()["quality"]["tdd"], False)
        self.assertIn(f"согласие {key} выписано", out)
        # Активный маркер gate-guard засчитал бы и модели: после вызова его нет.
        self.assertIsNone(FE.approval(self.root, key))
        log = FE.read_log(FE.approvals_path(self.root))
        grant = next(r for r in log if r.get("kind") == "approval")
        self.assertEqual(grant["via"], "human_override")
        self.assertTrue(grant["approved_by"].startswith("human:"))
        self.assertEqual(grant["produced_by"], "record_approval")
        self.assertTrue(any(r.get("kind") == "approval-revoked" for r in log))

    def test_reason_is_optional_so_hint_pastes_as_is(self):
        out = io.StringIO()
        real, sys.stdout = sys.stdout, out
        try:
            rc = HO.main(["--project", str(self.root), "--", "config", "set", "quality.tdd", "false"],
                         env={}, tty=FakeTTY(), stdin_isatty=True)
        finally:
            sys.stdout = real
        self.assertEqual(rc, 0, out.getvalue())
        grant = next(r for r in FE.read_log(FE.approvals_path(self.root))
                     if r.get("kind") == "approval")
        self.assertEqual(grant["reason"], "не указана")

    def test_wrong_code_runs_nothing(self):
        rc, out = self.ho("config", "set", "quality.tdd", "false", tty=FakeTTY(answer="NOPE42"))
        self.assertEqual(rc, 5)
        self.assertIs(self.policy()["quality"]["tdd"], True)
        self.assertFalse(FE.approvals_path(self.root).exists())

    def test_failed_command_still_revokes(self):
        rc, out = self.ho("config", "set", "quality.tdd", "не-булево")
        self.assertNotEqual(rc, 0)
        self.assertIsNone(FE.approval(self.root, "policy-downgrade-quality.tdd"))


class NotByAgentTest(Base):
    def test_agent_env_refused_before_anything(self):
        for var in ("QWEN_CODE", "GEMINI_CLI", "CLAUDECODE"):
            with self.subTest(var=var):
                rc, _ = self.ho("config", "set", "quality.tdd", "false", env={var: "1"})
                self.assertEqual(rc, 4)
        self.assertIs(self.policy()["quality"]["tdd"], True)

    def test_piped_stdin_refused(self):
        rc, _ = self.ho("config", "set", "quality.tdd", "false", isatty=False)
        self.assertEqual(rc, 4)

    def test_real_run_without_terminal_refused(self):
        """Как запускает агент: stdin=pipe, без управляющего терминала, QWEN_CODE снят."""
        env = {k: v for k, v in os.environ.items() if k not in HO.AGENT_ENV}
        p = subprocess.run([sys.executable, str(HOOKS / "human_override.py"),
                            "--project", str(self.root), "--reason", "x", "--",
                            "config", "set", "quality.tdd", "false"],
                           input="ABCDEF\n", capture_output=True, text=True, env=env,
                           start_new_session=True)
        self.assertEqual(p.returncode, 4, p.stderr)
        self.assertIs(self.policy()["quality"]["tdd"], True)

    def test_hook_denies_any_mention(self):
        for cmd in ("python3 .gigacode/extensions/forge/hooks/human_override.py --reason x -- config set quality.tdd false",
                    "env -u QWEN_CODE script -q /dev/null python3 hooks/human_override.py",
                    "python3 -c 'import human_override'"):
            with self.subTest(cmd=cmd):
                p = subprocess.run([sys.executable, str(BLOCKER)],
                                   input=json.dumps({"tool_name": "Bash",
                                                     "tool_input": {"command": cmd}}),
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, 2, p.stderr)
                self.assertIn("только человек", p.stderr)


@unittest.skipUnless(hasattr(os, "fork"), "pty — только POSIX")
class RealTerminalTest(Base):
    """Человек в настоящем терминале: код на /dev/tty, ввод с клавиатуры."""

    def _spawn(self, typed_from_screen: bool):
        import pty
        import select as sel
        env = {k: v for k, v in os.environ.items() if k not in HO.AGENT_ENV}
        argv = [sys.executable, str(HOOKS / "human_override.py"), "--project", str(self.root),
                "--reason", "pty", "--", "config", "set", "quality.tdd", "false"]
        pid, fd = pty.fork()
        if pid == 0:                                   # ребёнок: управляющий терминал — pty
            os.execve(sys.executable, argv, env)
        buf, sent = b"", False
        while True:
            r, _, _ = sel.select([fd], [], [], 20)
            if not r:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            m = re.search("Код подтверждения: ".encode() + rb"(\w{6})", buf)
            if m and not sent:
                os.write(fd, (m.group(1) if typed_from_screen else b"WRONG1") + b"\n")
                sent = True
        _, status = os.waitpid(pid, 0)
        os.close(fd)
        return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") \
            else (status >> 8), buf.decode("utf-8", "replace")

    def test_human_types_code(self):
        rc, out = self._spawn(True)
        self.assertEqual(rc, 0, out)
        self.assertIs(self.policy()["quality"]["tdd"], False)

    def test_wrong_code_in_terminal(self):
        rc, out = self._spawn(False)
        self.assertEqual(rc, 5, out)
        self.assertIs(self.policy()["quality"]["tdd"], True)


class RefusalHintTest(Base):
    def test_human_field_is_a_pasteable_command(self):
        """Человек вставляет команду из отказа как есть: в ней ни заглушек, ни пояснений."""
        import shlex
        cfg = HOOKS.parent / "skills" / "config-helper" / "scripts" / "config.py"
        p = subprocess.run([sys.executable, str(cfg), "--project", str(self.root),
                            "set", "quality.tdd", "false"], capture_output=True, text=True)
        human = json.loads(p.stdout)["human"]
        toks = shlex.split(human)
        self.assertTrue(toks[1].endswith("hooks/human_override.py"), human)
        self.assertEqual(toks[2:], ["--", "config", "set", "quality.tdd", "false"])
        self.assertNotIn("<", human)


class KeysTest(unittest.TestCase):
    def test_keys_from_json_and_hint(self):
        text = ('{"approval_key": "policy-repin-KID-1"}\n'
                "… record_approval.py --key rollback-KID-1-step3 --approved-by user")
        self.assertEqual(HO.wanted_keys(text), ["policy-repin-KID-1", "rollback-KID-1-step3"])


if __name__ == "__main__":
    unittest.main()
