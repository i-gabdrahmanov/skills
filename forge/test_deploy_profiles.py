#!/usr/bin/env python3
"""test_deploy_profiles.py — поставки back и ui: что едет в бэк-репо и что во фронт-репо.

Гоняет НАСТОЯЩИЕ deploy.sh / uninstall.sh (исходник не меняется) во временные репо:
  • фронт-репо (`--profile ui`): forge-ui целиком (скилл forgeui, общий control-plane), скрипты
    feature-pipeline без SKILL.md, никаких Java-скиллов; /forge — фронтовая; MCP forge-ui
    зарегистрирован и отвечает на initialize, forge-master — нет; после инициализации policy.json
    (npm по lock-файлу) preflight проходит — то есть проверка путей реестра не объявляет битыми
    неположенные Java-скиллы;
  • бэк-репо (профиль по умолчанию): как раньше, но без ветки forgeui и без forge-ui;
  • uninstall снимает своё, включая .forge-profile и MCP forge-ui, чужой MCP остаётся.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent


def _run(cmd, cwd=None, inp=None, timeout=240):
    return subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


def _git_init(path: Path):
    _run(["git", "init", "-q", str(path)])
    _run(["git", "-C", str(path), "config", "user.email", "t@t"])
    _run(["git", "-C", str(path), "config", "user.name", "t"])


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "нужны bash и git")
class DeployProfiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _front_repo(self) -> Path:
        tgt = self.root / "front"
        tgt.mkdir()
        (tgt / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.2.0"}, "devDependencies": {"typescript": "5.4.0"},
            "scripts": {"build": "vite build"}}), encoding="utf-8")
        (tgt / "package-lock.json").write_text("{}", encoding="utf-8")
        _git_init(tgt)
        return tgt

    def _settings(self, tgt: Path) -> dict:
        return json.loads((tgt / ".gigacode" / "settings.json").read_text(encoding="utf-8"))

    def test_ui_profile(self):
        tgt = self._front_repo()
        r = _run(["bash", str(REPO / "deploy.sh"), "--profile", "ui", str(tgt)])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        gig = tgt / ".gigacode"
        skills = sorted(p.name for p in (gig / "skills").iterdir() if p.is_dir())
        self.assertEqual(skills, ["config-helper", "feature-pipeline", "forgeui", "pipeline-state"])
        self.assertTrue((gig / "skills" / "forgeui" / "SKILL.md").is_file())
        self.assertFalse((gig / "skills" / "feature-pipeline" / "SKILL.md").exists(),
                         "Java-конвейер во фронт-репо скиллом не светится")
        self.assertTrue((gig / "skills" / "feature-pipeline" / "scripts" / "skill_paths.py").is_file())
        self.assertEqual(sorted(p.name for p in (gig / "commands").iterdir()), ["forge.md"])
        self.assertIn("forgeui", (gig / "commands" / "forge.md").read_text(encoding="utf-8"))
        self.assertIn("profile ui", (gig / ".forge-profile").read_text(encoding="utf-8"))
        manifest = (gig / ".forge-deployed").read_text(encoding="utf-8")
        self.assertIn("skills/forgeui", manifest)
        self.assertNotIn("skills/java-spring-dev", manifest, "реестр — только положенное")

        mcp = self._settings(tgt).get("mcpServers") or {}
        self.assertIn("forge-ui", mcp)
        self.assertNotIn("forge-master", mcp)
        cfg = mcp["forge-ui"]
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        r = _run([cfg["command"], *cfg["args"]], cwd=cfg["cwd"], inp=init + "\n", timeout=60)
        self.assertIn('"forge-ui"', r.stdout, r.stderr)

        # policy.json фронт-репо: npm по lock-файлу, без Java-полей; jira — ответ пользователя
        py = sys.executable
        r = _run([py, "-X", "utf8", str(gig / "skills/feature-pipeline/scripts/init_pipeline_config.py"),
                  "--project", str(tgt)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        policy = json.loads((tgt / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["project"]["build_system"], "npm")
        r = _run([py, "-X", "utf8", str(gig / "skills/config-helper/scripts/config.py"),
                  "--project", str(tgt), "set", "jira.enabled", "false"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _run([py, "-X", "utf8", str(gig / "hooks/preflight.py"), "--project", str(tgt), "--json"])
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])

        # uninstall: своё снято, чужой MCP оператора — на месте
        s = self._settings(tgt)
        s["mcpServers"]["jira"] = {"command": "jira-mcp"}
        (gig / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        r = _run(["bash", str(REPO / "uninstall.sh"), str(tgt)])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertFalse((gig / ".forge-profile").exists())
        self.assertFalse((gig / "skills" / "forgeui").exists())
        left = json.loads((gig / "settings.json").read_text(encoding="utf-8")).get("mcpServers")
        self.assertEqual(left, {"jira": {"command": "jira-mcp"}})

    def test_back_profile_unchanged_but_without_ui_branch(self):
        tgt = self.root / "back"
        tgt.mkdir()
        (tgt / "build.gradle").write_text("group = 'com.example'\n", encoding="utf-8")
        _git_init(tgt)
        r = _run(["bash", str(REPO / "deploy.sh"), str(tgt)])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        gig = tgt / ".gigacode"
        self.assertTrue((gig / "skills" / "feature-pipeline" / "SKILL.md").is_file())
        self.assertTrue((gig / "skills" / "forgetech" / "SKILL.md").is_file())
        self.assertFalse((gig / "skills" / "forgeui").exists(), "фронтовая ветка — во фронт-репо")
        self.assertTrue((gig / "commands" / "forge-fix.md").is_file())
        self.assertNotIn("forgeui", (gig / "commands" / "forge.md").read_text(encoding="utf-8"))
        mcp = self._settings(tgt).get("mcpServers") or {}
        self.assertIn("forge-master", mcp)
        self.assertNotIn("forge-ui", mcp)
        self.assertIn("profile back", (gig / ".forge-profile").read_text(encoding="utf-8"))
        self.assertNotIn("skills/forgeui", (gig / ".forge-deployed").read_text(encoding="utf-8"))

    def test_unknown_profile_refused(self):
        tgt = self.root / "x"
        tgt.mkdir()
        r = _run(["bash", str(REPO / "deploy.sh"), "--profile", "mobile", str(tgt)])
        self.assertEqual(r.returncode, 2)
        self.assertFalse((tgt / ".gigacode").exists(), "на отказе ничего не скопировано")


if __name__ == "__main__":
    unittest.main()
