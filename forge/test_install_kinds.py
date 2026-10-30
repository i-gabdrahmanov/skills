#!/usr/bin/env python3
"""test_install_kinds.py — две поставки: forge в бэк-репо и forge-ui во фронт-репо.

Гоняет НАСТОЯЩИЕ install.sh / uninstall.sh (исходник не меняется) во временные репо, без
рантайма (--no-link; ссылки пинит hooks/test_forge_extension.Link):
  • фронт-репо (`install.sh forge-ui`): .gigacode/extensions/forge-ui/ — скилл forgeui, общий
    control-plane, скрипты feature-pipeline без SKILL.md, никаких Java-скиллов; /forge — фронтовая;
    в манифесте MCP forge-ui (отвечает на initialize), forge-master — нет; после инициализации
    policy.json (npm по lock-файлу) preflight проходит — проверка путей реестра не объявляет
    битыми неположенные Java-скиллы и находит пути `.gigacode/…` внутри поставки;
  • бэк-репо (`install.sh forge`): .gigacode/extensions/forge/ — все скиллы, кроме forgeui, все
    команды, MCP forge-master;
  • тексты для модели называют пути своей поставки, и каждый названный путь существует;
  • uninstall снимает каталог поставки целиком.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent


def _run(cmd, cwd=None, inp=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


def _git_init(path: Path):
    _run(["git", "init", "-q", str(path)])
    _run(["git", "-C", str(path), "config", "user.email", "t@t"])
    _run(["git", "-C", str(path), "config", "user.name", "t"])


_PATH_RE = re.compile(r"\.gigacode/extensions/(?:forge|forge-ui)/[A-Za-z0-9_./-]+\.(?:py|sh|json|md)")


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "нужны bash и git")
class InstallKinds(unittest.TestCase):
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

    def _manifest(self, ext: Path) -> dict:
        return json.loads((ext / "gigacode-extension.json").read_text(encoding="utf-8"))

    def _named_paths_exist(self, tgt: Path, ext: Path, kind: str):
        # «skill-scripts X» профиля — скрипты для хуков без SKILL.md: их брифы модели недоступны
        # (во фронт-репо это Java-конвейер feature-pipeline) и называют скиллы другой поставки
        prof = (ext / ".forge-profile").read_text(encoding="utf-8").splitlines()
        hidden = {l.split()[1] for l in prof if l.startswith("skill-scripts ")}
        stale, missing = [], set()
        for md in ext.rglob("*.md"):
            rel = md.relative_to(ext).parts
            if rel[0] == "skills" and rel[1] in hidden:
                continue
            text = md.read_text(encoding="utf-8")
            for m in re.finditer(r"(?<!~/)(?<!HOME/)\.gigacode/(?:skills|hooks)/", text):
                stale.append(f"{md.relative_to(ext)}: {text[m.start():m.start() + 60]}")
            for p in _PATH_RE.findall(text):
                self.assertTrue(p.startswith(f".gigacode/extensions/{kind}/"), f"{md}: чужая поставка {p}")
                if not (tgt / p).exists():
                    missing.add(p)
        self.assertEqual(stale, [], "в текстах остались пути старой раскладки")
        self.assertEqual(sorted(missing), [], "тексты называют файлы, которых в поставке нет")

    def test_forge_ui(self):
        tgt = self._front_repo()
        r = _run(["bash", str(REPO / "install.sh"), "forge-ui", str(tgt), "--no-link"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        ext = tgt / ".gigacode" / "extensions" / "forge-ui"
        skills = sorted(p.name for p in (ext / "skills").iterdir() if p.is_dir())
        self.assertEqual(skills, ["config-helper", "feature-pipeline", "forgeui", "pipeline-state"])
        self.assertTrue((ext / "skills" / "forgeui" / "SKILL.md").is_file())
        self.assertFalse((ext / "skills" / "feature-pipeline" / "SKILL.md").exists(),
                         "Java-конвейер во фронт-репо скиллом не светится")
        self.assertTrue((ext / "skills" / "feature-pipeline" / "scripts" / "skill_paths.py").is_file())
        self.assertEqual(sorted(p.name for p in (ext / "commands").iterdir()), ["forge.md"])
        self.assertIn("forgeui", (ext / "commands" / "forge.md").read_text(encoding="utf-8"))
        self.assertIn("profile ui", (ext / ".forge-profile").read_text(encoding="utf-8"))
        self._named_paths_exist(tgt, ext, "forge-ui")

        mcp = self._manifest(ext)["mcpServers"]
        self.assertEqual(sorted(mcp), ["forge-ui"])
        cfg = mcp["forge-ui"]
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        r = _run([cfg["command"], *cfg["args"]], cwd=cfg["cwd"], inp=init + "\n", timeout=60)
        self.assertIn('"forge-ui"', r.stdout, r.stderr)

        py = sys.executable
        r = _run([py, "-X", "utf8", str(ext / "skills/feature-pipeline/scripts/init_pipeline_config.py"),
                  "--project", str(tgt)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        policy = json.loads((tgt / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["project"]["build_system"], "npm")
        r = _run([py, "-X", "utf8", str(ext / "skills/config-helper/scripts/config.py"),
                  "--project", str(tgt), "set", "jira.enabled", "false"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _run([py, "-X", "utf8", str(ext / "hooks/preflight.py"), "--project", str(tgt), "--json"])
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertEqual(json.loads(r.stdout)["layout"]["mode"], "extension")

        r = _run(["bash", str(REPO / "uninstall.sh"), "forge-ui", str(tgt), "--no-link"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertFalse(ext.exists())

    def test_forge(self):
        tgt = self.root / "back"
        tgt.mkdir()
        (tgt / "build.gradle").write_text("group = 'com.example'\n", encoding="utf-8")
        _git_init(tgt)
        r = _run(["bash", str(REPO / "install.sh"), "forge", str(tgt), "--no-link"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        ext = tgt / ".gigacode" / "extensions" / "forge"
        self.assertTrue((ext / "skills" / "feature-pipeline" / "SKILL.md").is_file())
        self.assertTrue((ext / "skills" / "forgetech" / "SKILL.md").is_file())
        self.assertFalse((ext / "skills" / "forgeui").exists(), "фронтовая ветка — во фронт-репо")
        self.assertTrue((ext / "commands" / "forge-fix.md").is_file())
        self.assertNotIn("forgeui", (ext / "commands" / "forge.md").read_text(encoding="utf-8"))
        self.assertEqual(sorted(self._manifest(ext)["mcpServers"]), ["forge-master"])
        self.assertIn("profile back", (ext / ".forge-profile").read_text(encoding="utf-8"))
        self.assertNotIn("skills/forgeui", (ext / ".forge-deployed").read_text(encoding="utf-8"))
        self._named_paths_exist(tgt, ext, "forge")

    def test_unknown_kind_refused(self):
        tgt = self.root / "x"
        tgt.mkdir()
        r = _run(["bash", str(REPO / "install.sh"), "mobile", str(tgt)])
        self.assertEqual(r.returncode, 2)
        self.assertFalse((tgt / ".gigacode").exists(), "на отказе ничего не скопировано")


if __name__ == "__main__":
    unittest.main()
