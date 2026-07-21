#!/usr/bin/env python3
"""test_update.py — e2e update.sh поверх deploy.sh на временном проекте.

update.sh оркеструет git pull + deploy (мягко) либо git pull + uninstall + deploy (--force).
Тесты гоняют ТОЛЬКО --no-pull: настоящий git pull трогал бы состояние реального репо. Фиксируем
контракт: после обновления форж-обвязка на месте (харнес вооружён), а данные оператора
(самописные скиллы, ground/, permissions) целы — в обоих режимах.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy.sh"
UPDATE = REPO / "update.sh"

_BASH = shutil.which("bash")


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class UpdateRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.gig = self.proj / ".gigacode"
        self.settings = self.gig / "settings.json"
        self.assertEqual(self._sh(DEPLOY, str(self.proj)).returncode, 0, "deploy.sh упал")
        # оператор дописал своё поверх деплоя
        self.my_skill = self.gig / "skills" / "my-own-skill"
        self.my_skill.mkdir(parents=True)
        (self.my_skill / "SKILL.md").write_text("мой скилл", encoding="utf-8")
        s = json.loads(self.settings.read_text(encoding="utf-8"))
        s["permissions"] = {"allow": ["Bash(ls:*)"]}
        self.settings.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        self.state = self.proj / "ground" / "statements" / "demo"
        self.state.mkdir(parents=True)
        (self.state / "manifest.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _sh(self, script: Path, *args: str):
        return subprocess.run([_BASH, str(script), *args],
                              capture_output=True, text=True, timeout=180)

    def _hook_names(self):
        s = json.loads(self.settings.read_text(encoding="utf-8"))
        return [h.get("name") for groups in s.get("hooks", {}).values()
                for g in groups for h in g.get("hooks", [])]

    def _assert_forge_armed_and_operator_intact(self):
        self.assertIn("gate-guard", self._hook_names(), "после update харнес обязан быть вооружён")
        self.assertTrue((self.gig / "skills" / "feature-pipeline").is_dir(), "форж-скилл должен стоять")
        self.assertTrue((self.gig / "commands" / "forge-lite.md").exists(), "команда /forge-lite должна стоять")
        self.assertTrue((self.my_skill / "SKILL.md").exists(), "самописный скилл оператора снесён — недопустимо")
        self.assertTrue((self.state / "manifest.json").exists(), "ground/ трогать нельзя")
        s = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(s["permissions"], {"allow": ["Bash(ls:*)"]}, "чужие секции settings.json целы")

    def test_soft_update_rearms_and_keeps_operator(self):
        r = self._sh(UPDATE, str(self.proj), "--no-pull")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self._assert_forge_armed_and_operator_intact()

    def test_force_update_rearms_and_keeps_operator(self):
        r = self._sh(UPDATE, str(self.proj), "--force", "--no-pull")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self._assert_forge_armed_and_operator_intact()

    def test_dry_run_changes_nothing(self):
        before = self.settings.read_text(encoding="utf-8")
        r = self._sh(UPDATE, str(self.proj), "--force", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY-RUN", r.stdout)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before, "--dry-run не должен писать")


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class UpdateArgs(unittest.TestCase):
    def _sh(self, *args: str):
        return subprocess.run([_BASH, str(UPDATE), *args],
                              capture_output=True, text=True, timeout=60)

    def test_no_target_exits_2(self):
        r = self._sh()
        self.assertEqual(r.returncode, 2)
        self.assertIn("не указана целевая папка", r.stderr)

    def test_missing_target_exits_2(self):
        self.assertEqual(self._sh("/nonexistent/zzz").returncode, 2)

    def test_self_target_refused(self):
        r = self._sh(str(REPO))
        self.assertEqual(r.returncode, 2)

    def test_unknown_flag_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._sh(td, "--wat").returncode, 2)


if __name__ == "__main__":
    unittest.main()
