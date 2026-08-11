#!/usr/bin/env python3
"""test_cleanup_legacy.py — пины для cleanup-legacy.sh (чистка старой раскладки forge).

Скрипт ходит по чужим каталогам и переносит файлы, поэтому пинится ровно то, на чём такие
чистилки и горят:
  • режим по умолчанию — ПЛАН: ни один файл не тронут;
  • переносится только форж-своё (состав extension'а + список снятых артефактов),
    самописные скиллы/команды/хуки оператора остаются на месте (был инцидент — снесло);
  • из settings.json уходят только forge-записи блока hooks, чужие сохраняются, делается бэкап;
  • сам установленный extension не трогается — это не деинсталлятор;
  • повторный прогон идемпотентен, ground/ уходит только по --purge-state.

Запуск: python3 test_cleanup_legacy.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EXT = Path(__file__).resolve().parent
SCRIPT = EXT / "cleanup-legacy.sh"

# Форж-скиллы/команды/хуки берём из реального состава extension'а — тест не должен знать
# их наизусть (добавили скилл → чистка и тест узнают о нём сами).
FORGE_SKILL = sorted(p.parent.name for p in EXT.glob("skills/*/SKILL.md"))[0]
FORGE_CMD = sorted(p.stem for p in EXT.glob("commands/*.md"))[0]


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


SETTINGS = {
    "$version": 1,
    "permissions": {"allow": ["Bash"]},
    "hooks": {
        "PreToolUse": [{"matcher": "^(run_shell_command|Bash)$", "hooks": [
            {"type": "command", "name": "gate-guard",
             "command": "python3 $HOME/.gigacode/hooks/gate-guard.py"},
            {"type": "command", "name": "log-agent",          # снятый хук: в форже его нет
             "command": "python3 $HOME/.gigacode/hooks/log-agent.py"},
            {"type": "command", "name": "my-logger",          # чужой — обязан выжить
             "command": "python3 $HOME/.gigacode/hooks/my-logger.py"},
        ]}],
    },
}


class CleanupLegacy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.proj = self.root / "proj"

        # Боевая база — .gigacode (GigaCode); .qwen добавлен вторым, чтобы пинить обход
        # ВСЕХ баз одним прогоном (на дев-машине forge жил там).
        q = self.home / ".gigacode"
        _write(self.home / ".qwen" / "skills" / FORGE_SKILL / "SKILL.md", "дев-копия")
        _write(q / "skills" / FORGE_SKILL / "SKILL.md", "старая копия")
        _write(q / "skills" / "pptx" / "SKILL.md", "операторский")
        _write(q / "commands" / f"{FORGE_CMD}.md")
        _write(q / "commands" / f"{FORGE_CMD}.toml")          # депрекейтнутый формат
        _write(q / "commands" / "my-notes.md", "операторская")
        _write(q / "hooks" / "gate-guard.py")
        _write(q / "hooks" / "log-agent.py")                  # снятый
        _write(q / "hooks" / "my-logger.py", "операторский")
        _write(q / "settings.json", json.dumps(SETTINGS, ensure_ascii=False))
        _write(q / "extensions" / "forge" / ".gigacode-extension-install.json", "{}")

        g = self.proj / ".gigacode"
        _write(g / "skills" / FORGE_SKILL / "SKILL.md", "старая копия")
        _write(g / "skills" / "custom" / "SKILL.md", "операторский")
        _write(g / "hooks" / "tdd-guard.py")
        _write(g / "FORGE.md")
        _write(g / "deploy-local.sh")
        _write(self.proj / "ground" / "pipeline.json", "{}")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args) -> str:
        r = subprocess.run(["bash", str(SCRIPT), "--home", str(self.home),
                            "--project", str(self.proj), *args],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def _snapshot(self) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())

    # ── план ──────────────────────────────────────────────────────────────
    def test_plan_changes_nothing(self):
        before = self._snapshot()
        out = self._run()
        self.assertIn("[план]", out)
        self.assertIn("Ничего не изменено", out)
        self.assertEqual(before, self._snapshot())

    # ── apply ─────────────────────────────────────────────────────────────
    def test_apply_moves_forge_keeps_operator(self):
        self._run("--apply")
        q = self.home / ".gigacode"
        # форж-своё ушло
        self.assertFalse((q / "skills" / FORGE_SKILL).exists())
        self.assertFalse((q / "commands" / f"{FORGE_CMD}.md").exists())
        self.assertFalse((q / "commands" / f"{FORGE_CMD}.toml").exists())
        self.assertFalse((q / "hooks" / "gate-guard.py").exists())
        self.assertFalse((q / "hooks" / "log-agent.py").exists())
        # операторское на месте
        self.assertTrue((q / "skills" / "pptx" / "SKILL.md").exists())
        self.assertTrue((q / "commands" / "my-notes.md").exists())
        self.assertTrue((q / "hooks" / "my-logger.py").exists())
        self.assertTrue((self.proj / ".gigacode" / "skills" / "custom").exists())
        # обошёл обе базы, перенёс, а не удалил
        self.assertFalse((self.home / ".qwen" / "skills" / FORGE_SKILL).exists())
        backup = next(self.home.glob("forge-legacy-backup-*"))
        self.assertTrue((backup / ".gigacode" / "skills" / FORGE_SKILL / "SKILL.md").exists())
        self.assertTrue((backup / ".qwen" / "skills" / FORGE_SKILL / "SKILL.md").exists())

    def test_apply_does_not_touch_installed_extension(self):
        self._run("--apply")
        self.assertTrue((self.home / ".gigacode" / "extensions" / "forge"
                         / ".gigacode-extension-install.json").exists())

    def test_settings_keeps_foreign_hooks_and_backs_up(self):
        self._run("--apply")
        p = self.home / ".gigacode" / "settings.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        names = [h.get("name") for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(names, ["my-logger"])
        self.assertEqual(data["permissions"], {"allow": ["Bash"]})   # чужие секции целы
        self.assertTrue(p.with_suffix(".json.bak").exists())
        # блок hooks проекта опустел целиком → ключ убран, а не оставлен пустым
        proj_settings = self.proj / ".gigacode" / "settings.json"
        if proj_settings.exists():
            self.assertNotIn("hooks", json.loads(proj_settings.read_text(encoding="utf-8")))

    def test_second_run_is_idempotent(self):
        self._run("--apply")
        after_first = self._snapshot()
        out = self._run("--apply")
        self.assertIn("Чисто", out)
        self.assertEqual(after_first, self._snapshot())

    # ── состояние пайплайна ───────────────────────────────────────────────
    def test_ground_survives_without_purge_state(self):
        self._run("--apply")
        self.assertTrue((self.proj / "ground" / "pipeline.json").exists())

    def test_purge_state_moves_ground_to_backup(self):
        self._run("--apply", "--purge-state")
        self.assertFalse((self.proj / "ground").exists())
        backup = next(self.proj.glob("forge-legacy-backup-*"))
        self.assertTrue((backup / "ground" / "pipeline.json").exists())


if __name__ == "__main__":
    if shutil.which("bash") is None:                      # pragma: no cover
        print("bash не найден — пропускаю")
        sys.exit(0)
    unittest.main(verbosity=2)
