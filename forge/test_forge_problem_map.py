"""Regression checks for installer rollback, hook registration and CI diagnostics."""
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "hooks"))
import _failclosed
import forge_extension as fx

spec = importlib.util.spec_from_file_location("forge_ci", ROOT / "skills/run_all_tests.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class ForgeContracts(unittest.TestCase):
    def test_update_rolls_back_on_build_or_link_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            for name in ("install.sh", "_deployed_manifest.sh", "profiles", "skills",
                         "spec-graph", "deploy-local.sh"):
                (source / name).symlink_to(ROOT / name)
            shutil.copytree(ROOT / "hooks", source / "hooks")
            project = Path(tmp) / "My Project"
            project.mkdir()
            cmd = ["bash", str(source / "install.sh"), "spec-graph", str(project)]
            ext = fx.ext_dir(project, "spec-graph")
            def install(*args):
                return subprocess.run([*cmd, *args], capture_output=True, text=True)

            result = install("--no-link")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            marker = ext / "previous-build"
            marker.touch()
            wiring = ext / "hooks/hooks.json"
            original = wiring.read_bytes()

            result = install("--runtime", str(project / "absent-cli"))
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.exists())

            (source / "hooks/settings.mcp.json").write_text("{")
            self.assertNotEqual(install("--no-link").returncode, 0)
            self.assertTrue(marker.exists())
            self.assertEqual(original, wiring.read_bytes())

            data = json.loads(original)
            group = data["hooks"]["PreToolUse"][0]
            args = shlex.split(group["hooks"][0]["command"])
            probe = subprocess.run(args, input='{}', cwd=project, text=True, capture_output=True)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            group["hooks"].reverse()
            wiring.write_text(json.dumps(data))
            self.assertTrue(any("PreToolUse" in p for p in fx.check(ext, project)))

    def test_blocking_crash_and_observer_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            hook = Path(tmp) / "probe.py"
            hook.write_text("raise RuntimeError('probe')")
            runner = [sys.executable, str(ROOT / "hooks/_run_hook.py")]
            for options, expected in ((["--blocking"], 2), ([], 1)):
                result = subprocess.run([*runner, *options, str(hook)], capture_output=True)
                self.assertEqual(result.returncode, expected)
                self.assertIn(b"probe", result.stderr)

    def test_recovery_is_an_exact_invocation(self):
        script = shlex.quote(str(ROOT / "deploy-local.sh"))
        self.assertTrue(_failclosed.is_recovery_command("bash " + script + " --check"))
        self.assertTrue(_failclosed.is_recovery_command("python3 --version"))
        for command in ("echo " + script, "bash " + script + " unexpected", "bash elsewhere/deploy-local.sh"):
            self.assertFalse(_failclosed.is_recovery_command(command))

    def test_ci_detects_equal_length_edit_and_empty_selection(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ci, "REPO", Path(tmp)):
            path = Path(tmp) / "ground" / "sample"
            path.parent.mkdir()
            path.write_bytes(b"A")
            before = ci._pollution_snapshot()
            path.write_bytes(b"B")
            self.assertNotEqual(before, ci._pollution_snapshot())
        with patch.object(sys, "argv", ["runner", "--skill", "nonexistent-skill"]):
            self.assertNotEqual(ci.main(), 0)


if __name__ == "__main__":
    unittest.main()
