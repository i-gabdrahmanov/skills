#!/usr/bin/env python3
"""test_update.py — e2e update.sh поверх install.sh на временном проекте.

update.sh — git pull + install.sh (по --force сначала uninstall.sh). Тесты гоняют ТОЛЬКО --no-pull
(настоящий git pull трогал бы реальный репо) и --no-link (рантайм не трогаем). Контракт: после
обновления extension на месте и подключён (hooks/hooks.json), данные оператора целы, поставка
определяется сама — по каталогу extension'а, а у старой раскладки — по её профилю (и мигрирует).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_uninstall as TU

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"
UPDATE = REPO / "update.sh"

_BASH = shutil.which("bash")


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class UpdateRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.gig = self.proj / ".gigacode"

    def tearDown(self):
        self._tmp.cleanup()

    def armed(self, kind: str):
        ext = self.gig / "extensions" / kind
        hooks = json.loads((ext / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        names = [h.get("name") for groups in hooks.values() for g in groups for h in g.get("hooks", [])]
        self.assertIn("gate-guard", names, "после update харнес обязан быть подключён")
        return ext

    def operator(self):
        own = self.gig / "skills" / "my-own-skill" / "SKILL.md"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text("мой скилл", encoding="utf-8")
        state = self.proj / "ground" / "statements" / "demo" / "manifest.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{}", encoding="utf-8")
        return own, state

    def test_soft_and_force_update_rearm_and_keep_operator(self):
        self.assertEqual(TU.sh(INSTALL, "forge-ui", str(self.proj), "--no-link").returncode, 0)
        own, state = self.operator()
        for extra in ((), ("--force",)):
            r = TU.sh(UPDATE, str(self.proj), "--no-pull", "--no-link", *extra)
            self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
            self.assertIn("update forge-ui", r.stdout, "поставка определилась по каталогу extension'а")
            ext = self.armed("forge-ui")
            self.assertTrue((ext / "skills" / "forgeui" / "SKILL.md").is_file())
            self.assertTrue(own.exists(), "самописный скилл оператора снесён — недопустимо")
            self.assertTrue(state.exists(), "ground/ трогать нельзя")

    def test_update_migrates_a_legacy_install_by_its_profile(self):
        TU.make_legacy(self.proj)
        r = TU.sh(UPDATE, str(self.proj), "--no-pull", "--no-link")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self.armed("forge")
        self.assertFalse((self.gig / "hooks" / "gate-guard.py").exists(), "старая раскладка снята")

    def test_dry_run_changes_nothing(self):
        self.assertEqual(TU.sh(INSTALL, "forge", str(self.proj), "--no-link").returncode, 0)
        before = (self.gig / "extensions" / "forge" / "hooks" / "hooks.json").read_text(encoding="utf-8")
        r = TU.sh(UPDATE, str(self.proj), "--force", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY-RUN", r.stdout)
        self.assertEqual((self.gig / "extensions" / "forge" / "hooks" / "hooks.json").read_text(encoding="utf-8"),
                         before)


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class UpdateArgs(unittest.TestCase):
    def test_args(self):
        r = TU.sh(UPDATE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("не указана папка проекта", r.stderr)
        self.assertEqual(TU.sh(UPDATE, "/nonexistent/zzz").returncode, 2)
        self.assertEqual(TU.sh(UPDATE, str(REPO)).returncode, 2)
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(TU.sh(UPDATE, td, "--wat").returncode, 2)


if __name__ == "__main__":
    unittest.main()
