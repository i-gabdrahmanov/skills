#!/usr/bin/env python3
"""test_uninstall.py — e2e install.sh → uninstall.sh и миграция старой раскладки.

Почему e2e, а не юнит: установка ломается на стыках — файлы поставки, их подключение
(hooks/hooks.json, ссылка в рантайме, в старой раскладке — блок hooks в settings.json) и данные
оператора рядом. Юниты forge_extension / strip_forge_hooks стыки не покрывают.

Контракт:
  • install кладёт extension в <repo>/.gigacode/extensions/<forge|forge-ui>/ и ничего — в
    co-located .gigacode/{skills,hooks,commands}; uninstall сносит каталог extension'а целиком
    (он весь форжевый) и не трогает данные оператора (ground/, свои скиллы, .gitignore-строки);
  • старая раскладка (≤ 0.5) снимается install'ом перед установкой: форжевые хуки уходят из
    settings.json, форж-скиллы — из .gigacode/skills, операторское остаётся, конфиг
    minor-defect-fix переезжает в extension;
  • --dry-run ничего не меняет, повтор — no-op, --purge-state сносит ground/ и refs/forge/*.
Рантайм не трогается (--no-link): ссылки пинит test_forge_extension.Link на поддельном CLI.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"
UNINSTALL = REPO / "uninstall.sh"
DEPLOY = REPO / "deploy.sh"

_BASH = shutil.which("bash")
_GIT = shutil.which("git")


def sh(script: Path, *args: str, timeout=300):
    return subprocess.run([_BASH, str(script), *args], capture_output=True, text=True, timeout=timeout)


def make_legacy(proj: Path) -> Path:
    """Старая project-раскладка (≤ 0.5), как её клал прежний deploy.sh (профиль back): код в
    co-located .gigacode/{hooks,skills,commands}, реестр .forge-deployed, хуки и MCP в
    settings.json (resolve_hook_paths.py — тот же владелец блока, что был тогда)."""
    gig = proj / ".gigacode"
    ign = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "hooks", gig / "hooks", ignore=ign, dirs_exist_ok=True)
    entries = [f"hooks/{p.name}" for p in (REPO / "hooks").iterdir()]
    for d in (REPO / "skills").iterdir():
        if d.is_dir() and d.name not in ("forgeui", "__pycache__"):
            shutil.copytree(d, gig / "skills" / d.name, ignore=ign, dirs_exist_ok=True)
            entries.append(f"skills/{d.name}")
    (gig / "commands").mkdir(exist_ok=True)
    for c in (REPO / "commands").glob("*.md"):
        shutil.copy(c, gig / "commands" / c.name)
        entries.append(f"commands/{c.name}")
    (gig / ".forge-deployed").write_text("# forge deploy manifest v1\n" + "\n".join(sorted(entries)) + "\n",
                                         encoding="utf-8")
    (gig / ".forge-profile").write_text((REPO / "profiles" / "back.profile").read_text(encoding="utf-8"),
                                        encoding="utf-8")
    (gig / "deploy-local.sh").write_text("# старый фиксер путей\n", encoding="utf-8")
    (gig / "skills" / "minor-defect-fix" / "config.json").write_text('{"projects":{"kid":"/real/spec"}}',
                                                                     encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", str(gig / "hooks" / "resolve_hook_paths.py"),
                        "--project", str(proj)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return gig


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class InstallUninstallRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.gig = self.proj / ".gigacode"
        self.ext = self.gig / "extensions" / "forge"
        r = sh(INSTALL, "forge", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"install.sh упал: {r.stdout}{r.stderr}")

    def tearDown(self):
        self._tmp.cleanup()

    def hook_names(self) -> list:
        h = json.loads((self.ext / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        return [x.get("name") for groups in h.values() for g in groups for x in g.get("hooks", [])]

    def test_install_lays_the_extension_and_nothing_co_located(self):
        self.assertIn("gate-guard", self.hook_names())
        self.assertTrue((self.ext / "gigacode-extension.json").is_file())
        self.assertTrue((self.ext / "skills" / "feature-pipeline" / "SKILL.md").is_file())
        self.assertTrue((self.ext / "commands" / "forge.md").is_file())
        self.assertTrue((self.ext / "commands" / "forge-fix.md").is_file())
        self.assertFalse(list((self.ext / "commands").glob("*.toml")), "TOML-команды — окно миграции рантайма")
        self.assertTrue((self.ext / "deploy-local.sh").is_file())
        for co in ("skills", "hooks", "commands", "settings.json"):
            self.assertFalse((self.gig / co).exists(), f".gigacode/{co} — не место поставки")

    def test_uninstall_removes_the_extension_and_keeps_operator_data(self):
        (self.ext / "skills" / "minor-defect-fix" / "config.json").write_text('{"projects":{"k":"/s"}}',
                                                                               encoding="utf-8")
        own = self.gig / "skills" / "my-own" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text("моё", encoding="utf-8")
        state = self.proj / "ground" / "statements" / "feature-pipeline" / "demo" / "manifest.json"
        state.parent.mkdir(parents=True)
        state.write_text("{}", encoding="utf-8")
        r = sh(UNINSTALL, "forge", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self.assertFalse(self.ext.exists())
        self.assertFalse((self.gig / "extensions").exists(), "пустой extensions/ убирается")
        self.assertTrue(own.exists(), "своё оператора uninstall трогать не смеет")
        self.assertTrue(state.exists(), "ground/ без --purge-state трогать нельзя")
        self.assertIn('"/s"', (self.gig / "minor-defect-fix-config.json.bak").read_text(encoding="utf-8"))

    def test_uninstall_without_kind_removes_what_is_there(self):
        r = sh(UNINSTALL, str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self.assertFalse(self.ext.exists())

    def test_uninstall_is_idempotent_and_dry_run_changes_nothing(self):
        r = sh(UNINSTALL, "forge", str(self.proj), "--no-link", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.ext / "hooks" / "hooks.json").is_file(), "--dry-run не удаляет")
        self.assertEqual(sh(UNINSTALL, "forge", str(self.proj), "--no-link").returncode, 0)
        r = sh(UNINSTALL, "forge", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"повтор — no-op: {r.stdout}{r.stderr}")

    def test_reinstall_replaces_the_extension_whole_and_keeps_the_config(self):
        (self.ext / "skills" / "zzz-stale").mkdir()
        (self.ext / "skills" / "minor-defect-fix" / "config.json").write_text('{"projects":{"k":"/s"}}',
                                                                               encoding="utf-8")
        r = sh(INSTALL, "forge", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self.assertFalse((self.ext / "skills" / "zzz-stale").exists(), "снятое из репо не переживает обновление")
        self.assertIn('"/s"', (self.ext / "skills" / "minor-defect-fix" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(list((self.gig / "extensions").glob(".forge.*")), [], "временные каталоги убраны")

    def test_second_kind_in_the_same_repo_is_refused(self):
        r = sh(INSTALL, "forge-ui", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 2)
        self.assertIn("одна поставка на репо", r.stderr)
        self.assertFalse((self.gig / "extensions" / "forge-ui").exists())

    @unittest.skipIf(_GIT is None, "нет git в PATH")
    def test_purge_state_removes_data_but_not_history(self):
        (self.proj / "ground").mkdir()
        (self.proj / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        subprocess.run([_GIT, "init", "-q", str(self.proj)], check=True, timeout=30)
        g = [_GIT, "-C", str(self.proj), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "base"], check=True, timeout=30)
        subprocess.run([_GIT, "-C", str(self.proj), "update-ref", "refs/forge/checkpoints/demo/00", "HEAD"],
                       check=True, timeout=30)
        r = sh(UNINSTALL, "forge", str(self.proj), "--no-link", "--purge-state")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self.assertFalse((self.proj / "ground").exists())
        refs = subprocess.run([_GIT, "-C", str(self.proj), "for-each-ref", "refs/forge/"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        self.assertEqual(refs, "")
        log = subprocess.run([_GIT, "-C", str(self.proj), "log", "--oneline"],
                             capture_output=True, text=True, timeout=30).stdout
        self.assertIn("base", log, "коммиты пользователя снятие обвязки трогать не смеет")


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class LegacyMigration(unittest.TestCase):
    """Старая раскладка (≤ 0.5) снимается перед установкой extension'а: иначе её хуки из
    settings.json задвоятся с хуками extension'а, а проектные скиллы перекроют скиллы extension'а
    (рантайм резолвит project > extension)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.gig = make_legacy(self.proj)
        s = json.loads((self.gig / "settings.json").read_text(encoding="utf-8"))
        s["permissions"] = {"allow": ["Bash(ls:*)"]}
        s.setdefault("mcpServers", {})["atlassian"] = {"command": "mcp-atlassian"}
        s["hooks"]["PreToolUse"][0]["hooks"].append(
            {"type": "command", "command": "python3 /opt/corp/audit.py", "name": "corp-audit"})
        (self.gig / "settings.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        self.own = self.gig / "skills" / "my-custom-skill" / "SKILL.md"
        self.own.parent.mkdir(parents=True)
        self.own.write_text("мой скилл", encoding="utf-8")
        (self.gig / "hooks" / "my-hook.py").write_text("# мой хук\n", encoding="utf-8")
        (self.gig / "commands" / "my-cmd.md").write_text("---\ndescription: моя\n---\nx\n", encoding="utf-8")
        (self.gig / "commands" / "forge.toml").write_text('prompt = "y"\n', encoding="utf-8")
        (self.gig / "skills" / "forgelite").mkdir()          # надгробие из tombstones.txt
        (self.gig / "skills" / "forgelite" / "SKILL.md").write_text("lite", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_install_migrates_and_keeps_operator(self):
        r = sh(INSTALL, "forge", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self.assertIn("старая раскладка", r.stdout)
        raw = (self.gig / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn(".gigacode/hooks/", raw, "хуки старой раскладки задвоились бы с extension'ом")
        s = json.loads(raw)
        self.assertEqual(s["permissions"], {"allow": ["Bash(ls:*)"]})
        self.assertEqual(sorted(s.get("mcpServers") or {}), ["atlassian"], "свои MCP сняты, чужой на месте")
        self.assertIn("corp-audit", raw, "чужой хук снимать не наше дело")
        self.assertTrue((self.gig / "settings.json.bak").exists())
        self.assertTrue(self.own.exists(), "самописный скилл оператора снесён — недопустимо")
        self.assertTrue((self.gig / "hooks" / "my-hook.py").exists())
        self.assertTrue((self.gig / "commands" / "my-cmd.md").exists())
        for gone in ("skills/feature-pipeline", "skills/forgelite", "hooks/gate-guard.py", "commands/forge.md",
                     "commands/forge.toml", ".forge-deployed", ".forge-profile", "deploy-local.sh"):
            self.assertFalse((self.gig / gone).exists(), f"{gone} старой раскладки должен быть снят")
        ext = self.gig / "extensions" / "forge"
        self.assertIn("/real/spec", (ext / "skills" / "minor-defect-fix" / "config.json").read_text(encoding="utf-8"),
                      "конфиг оператора переезжает в extension")
        pf = subprocess.run([sys.executable, "-X", "utf8", str(ext / "hooks" / "preflight.py"), "--project",
                             str(self.proj), "--json"], capture_output=True, text=True, timeout=120)
        out = json.loads(pf.stdout)
        self.assertEqual(out["layout"]["mode"], "extension")
        self.assertFalse([e for e in out["errors"] if "старой раскладки" in e or "старые копии" in e], out["errors"])

    def test_legacy_only_dry_run_changes_nothing(self):
        before = (self.gig / "settings.json").read_text(encoding="utf-8")
        r = sh(UNINSTALL, "--legacy-only", str(self.proj), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.gig / "settings.json").read_text(encoding="utf-8"), before)
        self.assertTrue((self.gig / "hooks" / "gate-guard.py").exists())

    def test_preflight_flags_leftovers_next_to_an_extension(self):
        r = sh(INSTALL, "forge", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        make_legacy(self.proj)                      # кто-то вернул старую раскладку рядом
        ext = self.gig / "extensions" / "forge"
        pf = subprocess.run([sys.executable, "-X", "utf8", str(ext / "hooks" / "preflight.py"), "--project",
                             str(self.proj), "--json"], capture_output=True, text=True, timeout=120)
        errs = json.loads(pf.stdout)["errors"]
        self.assertTrue(any("выполнится дважды" in e for e in errs), errs)
        self.assertTrue(any("старые копии скиллов" in e for e in errs), errs)


@unittest.skipIf(_BASH is None or _GIT is None, "нужны bash и git")
class GitignoreBlock(unittest.TestCase):
    """install кладёт ground/ и .gigacode/ в .gitignore, uninstall снимает РОВНО свой блок.

    ground/ — стейт прогонов, производное и переписываемое на каждом шаге; policy.json —
    исключение: это конфигурация проекта. Блок размечен маркерами: .gitignore принадлежит
    оператору, его строки — не наши."""

    BEGIN = "# >>> forge: рабочие данные пайплайна >>>"
    END = "# <<< forge <<<"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        subprocess.run([_GIT, "init", "-q", str(self.proj)], check=True, timeout=30)
        self.gi = self.proj / ".gitignore"

    def tearDown(self):
        self._tmp.cleanup()

    def install(self, proj=None):
        r = sh(INSTALL, "forge", str(proj or self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"install.sh упал: {r.stdout}{r.stderr}")
        return r

    def test_install_adds_block(self):
        self.install()
        text = self.gi.read_text(encoding="utf-8")
        for part in (self.BEGIN, "ground/*", "!ground/policy.json", ".gigacode/", self.END):
            self.assertIn(part, text)

    def test_installed_extension_is_not_git_noise(self):
        self.install()
        out = subprocess.run([_GIT, "-C", str(self.proj), "status", "--porcelain", "--untracked-files=all"],
                             capture_output=True, text=True, timeout=60).stdout
        self.assertNotIn(".gigacode", out, "установленный forge не должен светиться в git")

    def test_rule_actually_ignores_state_but_not_policy(self):
        self.install()
        f = self.proj / "ground" / "statements" / "feature-pipeline" / "F" / "manifest.json"
        f.parent.mkdir(parents=True)
        f.write_text("{}", encoding="utf-8")
        (self.proj / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        out = subprocess.run([_GIT, "-C", str(self.proj), "status", "--porcelain", "--untracked-files=all"],
                             capture_output=True, text=True, timeout=30).stdout
        self.assertIn("ground/policy.json", out)
        self.assertNotIn("manifest.json", out)

    def test_install_is_idempotent_and_operator_lines_survive_both_ways(self):
        self.gi.write_text("# моё\nbuild/\n*.iml", encoding="utf-8")     # без \\n в конце
        self.install()
        self.install()
        text = self.gi.read_text(encoding="utf-8")
        self.assertEqual(text.count(self.BEGIN), 1)
        self.assertIn("*.iml", text.split("\n"), "последняя строка оператора склеилась с блоком forge")
        r = sh(UNINSTALL, "forge", str(self.proj), "--no-link")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        text = self.gi.read_text(encoding="utf-8")
        self.assertNotIn(self.BEGIN, text)
        self.assertNotIn(".gigacode/", text)
        self.assertIn("build/", text)

    def test_non_git_project_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            r = self.install(Path(td))
            self.assertFalse((Path(td) / ".gitignore").exists())
            self.assertIn("не git-репозиторий", r.stdout)


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class Args(unittest.TestCase):
    """Цель обязательна, установка «в себя» запрещена, неизвестное — 2."""

    def test_install_needs_a_kind_and_a_target(self):
        r = sh(INSTALL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("forge (бэк-репо) или forge-ui", r.stderr)
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(sh(INSTALL, "mobile", td).returncode, 2)
            r = sh(INSTALL, "forge")
            self.assertEqual(r.returncode, 2)
            self.assertIn("не указана папка проекта", r.stderr)
            self.assertEqual(sh(INSTALL, "forge", td, "--wat").returncode, 2)
            self.assertFalse((Path(td) / ".gigacode").exists(), "на отказе ничего не положено")
        self.assertEqual(sh(INSTALL, "forge", "/nonexistent/zzz").returncode, 2)
        r = sh(INSTALL, "forge", str(REPO))
        self.assertEqual(r.returncode, 2)
        self.assertIn("в себя", r.stderr)

    def test_uninstall_args(self):
        r = sh(UNINSTALL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("не указана папка проекта", r.stderr)
        self.assertEqual(sh(UNINSTALL, "/nonexistent/zzz").returncode, 2)
        r = sh(UNINSTALL, str(REPO))
        self.assertEqual(r.returncode, 2)
        self.assertIn("сам Forge", r.stderr)
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(sh(UNINSTALL, td, "--wat").returncode, 2)
            self.assertEqual(sh(UNINSTALL, td).returncode, 0, "нечего снимать — не ошибка")

    def test_deploy_sh_is_a_compat_wrapper(self):
        with tempfile.TemporaryDirectory() as td:
            r = sh(DEPLOY, "--profile", "ui", td, "--no-link")
            self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
            self.assertIn("install.sh forge-ui", r.stderr)
            self.assertTrue((Path(td) / ".gigacode" / "extensions" / "forge-ui" / "hooks" / "hooks.json").is_file())
            self.assertEqual(sh(DEPLOY, "--profile", "mobile", td).returncode, 2)


if __name__ == "__main__":
    unittest.main()
