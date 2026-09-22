#!/usr/bin/env python3
"""test_uninstall.py — e2e пары deploy.sh → uninstall.sh на временном проекте.

Почему e2e, а не юнит: деинсталляция ломается ровно на стыке двух половин — файлы удалены,
а блок hooks в settings.json остался. Тогда рантайм зовёт несуществующие скрипты и падает на
КАЖДОМ вызове инструмента (зеркало регрессии «0 hook entries»: там харнес молчал, тут — орёт).
Юнит-тесты strip_forge_hooks (test_resolve_hook_paths.py) этот стык не покрывают.

Фиксируем контракт: uninstall уносит ТОЛЬКО своё (hooks/skills/deploy-local.sh/доки +
forge-записи блока hooks) и не трогает данные оператора (ground/, permissions/mcpServers,
чужие хуки, git-история, бэкапы).
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
DEPLOY = REPO / "deploy.sh"
UNINSTALL = REPO / "uninstall.sh"

_BASH = shutil.which("bash")
_GIT = shutil.which("git")


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class DeployUninstallRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.gig = self.proj / ".gigacode"
        self.settings = self.gig / "settings.json"
        r = self._sh(DEPLOY, str(self.proj))
        self.assertEqual(r.returncode, 0, f"deploy.sh упал: {r.stdout}{r.stderr}")

    def tearDown(self):
        self._tmp.cleanup()

    def _sh(self, script: Path, *args: str):
        return subprocess.run([_BASH, str(script), *args],
                              capture_output=True, text=True, timeout=180)

    def _read_settings(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def _hook_names(self) -> list[str]:
        s = self._read_settings()
        return [h.get("name") for groups in s.get("hooks", {}).values()
                for g in groups for h in g.get("hooks", [])]

    def _add_operator_data(self):
        """Оператор дописал своё поверх деплоя: секции, свой хук, конфиг, рабочие данные."""
        s = self._read_settings()
        s["permissions"] = {"allow": ["Bash(ls:*)"]}
        s["mcpServers"] = {"atlassian": {"command": "mcp-atlassian"}}
        s["hooks"]["PreToolUse"][0]["hooks"].append(
            {"type": "command", "command": "python3 /opt/corp/audit.py", "name": "corp-audit"})
        self.settings.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

        mdf = self.gig / "skills" / "minor-defect-fix" / "config.json"
        if mdf.parent.is_dir():
            mdf.write_text('{"projects":{"kid":"/real/spec"}}', encoding="utf-8")

        state = self.proj / "ground" / "statements" / "feature-pipeline" / "demo"
        state.mkdir(parents=True)
        (state / "manifest.json").write_text('{"steps":[]}', encoding="utf-8")

    def test_deploy_arms_hooks(self):
        # предусловие теста: деплой реально ставит хуки (иначе снятие нечего проверять)
        self.assertTrue((self.gig / "hooks").is_dir())
        self.assertTrue((self.gig / "skills").is_dir())
        self.assertIn("gate-guard", self._hook_names())

    def test_deploy_installs_slash_commands_as_markdown(self):
        # обе слэш-команды едут в .gigacode/commands/ как .md (не TOML — иначе окно миграции qwen)
        self.assertTrue((self.gig / "commands" / "forge.md").exists(), "/forge не задеплоен")
        self.assertTrue((self.gig / "commands" / "forge-lite.md").exists(), "/forge-lite не задеплоен")
        self.assertFalse((self.gig / "commands" / "forge.toml").exists(), "TOML-команда не должна деплоиться")

    def test_uninstall_removes_forge_and_keeps_operator_data(self):
        self._add_operator_data()
        r = self._sh(UNINSTALL, str(self.proj))
        self.assertEqual(r.returncode, 0, f"uninstall.sh упал: {r.stdout}{r.stderr}")

        # 1. файлы деплоя ушли
        for gone in ("hooks", "skills", "deploy-local.sh", "FORGE.md", "SKILLS-REGISTRY.md"):
            self.assertFalse((self.gig / gone).exists(), f"{gone} должен быть удалён")

        # 2. ГЛАВНОЕ: в settings.json не осталось ни одного хука на удалённые файлы
        raw = self.settings.read_text(encoding="utf-8")
        self.assertNotIn(".gigacode/hooks", raw,
                         "в settings.json остался хук на удалённый файл — рантайм будет падать "
                         "на каждом вызове инструмента")

        # 3. данные оператора целы
        s = self._read_settings()
        self.assertEqual(s["permissions"], {"allow": ["Bash(ls:*)"]})
        self.assertIn("mcpServers", s)
        self.assertIn("corp-audit", self._hook_names(), "чужой хук снимать не наше дело")

        # 4. рабочие данные и бэкапы на месте
        self.assertTrue((self.proj / "ground/statements/feature-pipeline/demo/manifest.json").exists(),
                        "ground/ без --purge-state трогать нельзя")
        self.assertTrue((self.settings.parent / "settings.json.bak").exists(), "нет бэкапа settings.json")
        self.assertTrue((self.gig / "minor-defect-fix-config.json.bak").exists(),
                        "конфиг оператора должен быть отставлен в сторону, а не унесён вместе со skills/")

    def test_uninstall_keeps_operator_own_skills_and_hooks(self):
        """Регрессия (реальный инцидент): uninstall делал rm -rf .gigacode/skills целиком и
        уносил самописные скиллы оператора, co-located с форж-скиллами. Снимать можно только
        форж-своё; чужое рядом — не наше, каталог убираем лишь когда опустел."""
        my_skill = self.gig / "skills" / "my-custom-skill"
        my_skill.mkdir(parents=True)
        (my_skill / "SKILL.md").write_text("мой скилл", encoding="utf-8")
        my_hook = self.gig / "hooks" / "my-custom-hook.py"
        my_hook.write_text("# мой хук\n", encoding="utf-8")
        my_cmd = self.gig / "commands" / "my-cmd.md"
        my_cmd.parent.mkdir(parents=True, exist_ok=True)
        my_cmd.write_text("---\ndescription: моя команда\n---\nтело\n", encoding="utf-8")

        r = self._sh(UNINSTALL, str(self.proj))
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")

        # операторское — на месте (главная проверка инцидента)
        self.assertTrue((my_skill / "SKILL.md").exists(), "самописный скилл оператора снесён — недопустимо")
        self.assertTrue(my_hook.exists(), "самописный хук оператора снесён")
        self.assertTrue(my_cmd.exists(), "самописная команда оператора снесена")
        # каталоги сохранены, раз в них осталось чужое
        self.assertTrue((self.gig / "skills").is_dir())
        self.assertTrue((self.gig / "hooks").is_dir())
        # а форж-своё — снято
        self.assertFalse((self.gig / "skills" / "feature-pipeline").exists(), "форж-скилл должен быть снят")
        self.assertFalse((self.gig / "hooks" / "gate-guard.py").exists(), "форж-хук должен быть снят")
        self.assertFalse((self.gig / "commands" / "forge.md").exists(), "форж-команда должна быть снята")
        # и в settings.json не осталось хуков на удалённые форж-файлы
        self.assertNotIn(".gigacode/hooks", self.settings.read_text(encoding="utf-8"))

    def test_uninstall_removes_legacy_toml_command(self):
        """qwen-code депрекейтнул TOML-команды: при наличии forge.toml рантайм показывает
        окно миграции на каждом старте. Прошлый деплой клал forge.toml — uninstall обязан
        снять его по имени (в $SRC его уже нет, remove_forge_owned счёл бы «операторским»)."""
        legacy = self.gig / "commands" / "forge.toml"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('description = "x"\nprompt = "y"\n', encoding="utf-8")
        r = self._sh(UNINSTALL, str(self.proj))
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")
        self.assertFalse(legacy.exists(), "устаревший forge.toml должен быть снят")

    def test_uninstall_is_idempotent(self):
        self.assertEqual(self._sh(UNINSTALL, str(self.proj)).returncode, 0)
        r = self._sh(UNINSTALL, str(self.proj))
        self.assertEqual(r.returncode, 0, f"повторный запуск должен быть no-op: {r.stdout}{r.stderr}")

    def test_dry_run_changes_nothing(self):
        before = self.settings.read_text(encoding="utf-8")
        r = self._sh(UNINSTALL, str(self.proj), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.gig / "hooks").is_dir(), "--dry-run не должен удалять файлы")
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before)

    def test_redeploy_after_uninstall_rearms(self):
        self.assertEqual(self._sh(UNINSTALL, str(self.proj)).returncode, 0)
        self.assertEqual(self._sh(DEPLOY, str(self.proj)).returncode, 0)
        self.assertIn("gate-guard", self._hook_names(), "deploy после uninstall обязан вернуть харнес")

    @unittest.skipIf(_GIT is None, "нет git в PATH")
    def test_purge_state_removes_data_but_not_history(self):
        self._add_operator_data()
        subprocess.run([_GIT, "init", "-q", str(self.proj)], check=True, timeout=30)
        env_git = [_GIT, "-C", str(self.proj), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(env_git + ["commit", "-q", "--allow-empty", "-m", "base"], check=True, timeout=30)
        subprocess.run([_GIT, "-C", str(self.proj), "update-ref",
                        "refs/forge/checkpoints/demo/00-baseline", "HEAD"], check=True, timeout=30)

        r = self._sh(UNINSTALL, str(self.proj), "--purge-state")
        self.assertEqual(r.returncode, 0, f"{r.stdout}{r.stderr}")

        self.assertFalse((self.proj / "ground").exists(), "--purge-state обязан снести ground/")
        refs = subprocess.run([_GIT, "-C", str(self.proj), "for-each-ref",
                               "--format=%(refname)", "refs/forge/"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        self.assertEqual(refs, "", "--purge-state обязан снести git-чекпойнты refs/forge/*")
        log = subprocess.run([_GIT, "-C", str(self.proj), "log", "--oneline"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        self.assertIn("base", log, "коммиты пользователя откат обвязки трогать не смеет")


@unittest.skipIf(_BASH is None or _GIT is None, "нужны bash и git")
class GitignoreBlock(unittest.TestCase):
    """deploy кладёт ground/ в .gitignore, uninstall снимает РОВНО свой блок.

    Почему это вообще нужно: ground/ — стейт прогонов, evidence и журналы, производное от кода
    и переписываемое на каждом шаге. Без правила оно уезжает в историю пользовательского репо
    и конфликтует на каждый merge. policy.json — исключение: это конфигурация проекта.

    Почему блок размечен маркерами: .gitignore принадлежит оператору. Снос файла целиком или
    перезапись «своей» версией унесли бы его строки — тот же класс, что инцидент со сметённым
    rm -rf каталогом самописных скиллов."""

    BEGIN = "# >>> forge: рабочие данные пайплайна >>>"
    END = "# <<< forge <<<"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        subprocess.run([_GIT, "init", "-q", str(self.proj)], check=True, timeout=30)
        self.gi = self.proj / ".gitignore"

    def tearDown(self):
        self._tmp.cleanup()

    def _sh(self, script: Path, *args: str):
        return subprocess.run([_BASH, str(script), *args],
                              capture_output=True, text=True, timeout=180)

    def _deploy(self):
        r = self._sh(DEPLOY, str(self.proj))
        self.assertEqual(r.returncode, 0, f"deploy.sh упал: {r.stdout}{r.stderr}")
        return r

    def test_deploy_adds_block(self):
        self._deploy()
        text = self.gi.read_text(encoding="utf-8")
        self.assertIn(self.BEGIN, text)
        self.assertIn("ground/*", text)
        self.assertIn("!ground/policy.json", text)
        self.assertIn(".gigacode/", text)
        self.assertIn(self.END, text)

    def test_deployed_harness_is_not_git_noise(self):
        """286 untracked-записей сразу после установки — это не «шум», а рабочий инструмент:
        в таком статусе не видно собственных изменений проекта. Плюс settings.json внутри
        держит абсолютные пути этой машины — у коллеги он зовёт несуществующие скрипты."""
        self._deploy()
        out = subprocess.run([_GIT, "-C", str(self.proj), "status", "--porcelain",
                              "--untracked-files=all"],
                             capture_output=True, text=True, timeout=60).stdout
        self.assertNotIn(".gigacode", out, "задеплоенный харнес не должен светиться в git")

    def test_operator_can_force_add_own_skill(self):
        """Игнор каталога не запирает co-located: своё оператор версионирует точечно."""
        self._deploy()
        own = self.proj / ".gigacode" / "skills" / "my-own" / "SKILL.md"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text("# моё\n", encoding="utf-8")
        r = subprocess.run([_GIT, "-C", str(self.proj), "add", "-f", str(own)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        tracked = subprocess.run([_GIT, "-C", str(self.proj), "ls-files"],
                                 capture_output=True, text=True, timeout=30).stdout
        self.assertIn("my-own/SKILL.md", tracked)

    def test_rule_actually_ignores_state_but_not_policy(self):
        """Проверяем не текст, а поведение git: форма `ground/` re-include не даёт."""
        self._deploy()
        (self.proj / "ground" / "statements" / "feature-pipeline" / "F").mkdir(parents=True)
        (self.proj / "ground" / "statements" / "feature-pipeline" / "F"
         / "manifest.json").write_text("{}", encoding="utf-8")
        (self.proj / "ground" / "policy.json").write_text("{}", encoding="utf-8")
        out = subprocess.run([_GIT, "-C", str(self.proj), "status", "--porcelain",
                              "--untracked-files=all"],
                             capture_output=True, text=True, timeout=30).stdout
        self.assertIn("ground/policy.json", out, "конфигурация проекта обязана остаться видимой")
        self.assertNotIn("manifest.json", out, "стейт прогона обязан быть проигнорирован")

    def test_deploy_is_idempotent(self):
        self._deploy()
        self._deploy()
        text = self.gi.read_text(encoding="utf-8")
        self.assertEqual(text.count(self.BEGIN), 1, "повторный деплой не должен дублировать блок")

    def test_operator_lines_survive_both_ways(self):
        self.gi.write_text("# моё\nbuild/\n*.iml\n", encoding="utf-8")
        self._deploy()
        text = self.gi.read_text(encoding="utf-8")
        self.assertIn("build/", text)
        self.assertIn("*.iml", text)
        r = self._sh(UNINSTALL, str(self.proj))
        self.assertEqual(r.returncode, 0, f"uninstall.sh упал: {r.stdout}{r.stderr}")
        text = self.gi.read_text(encoding="utf-8")
        self.assertNotIn(self.BEGIN, text)
        self.assertNotIn("ground/*", text)
        self.assertNotIn(".gigacode/", text)
        self.assertIn("build/", text, "строки оператора uninstall трогать не смеет")
        self.assertIn("*.iml", text)

    def test_no_trailing_newline_does_not_glue_lines(self):
        self.gi.write_text("build/", encoding="utf-8")       # без \n в конце
        self._deploy()
        lines = self.gi.read_text(encoding="utf-8").split("\n")
        self.assertIn("build/", lines, "последняя строка оператора склеилась с блоком forge")

    def test_uninstall_without_block_is_noop(self):
        self.gi.write_text("build/\n", encoding="utf-8")
        r = self._sh(UNINSTALL, str(self.proj))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.gi.read_text(encoding="utf-8"), "build/\n")

    def test_non_git_project_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            plain = Path(td)
            r = self._sh(DEPLOY, str(plain))
            self.assertEqual(r.returncode, 0, f"deploy.sh упал: {r.stdout}{r.stderr}")
            self.assertFalse((plain / ".gitignore").exists(),
                             "вне git-репо .gitignore заводить незачем")
            self.assertIn("не git-репозиторий", r.stdout)


@unittest.skipIf(_BASH is None, "нет bash в PATH")
class UninstallArgs(unittest.TestCase):
    """Интерфейс — зеркало deploy.sh: цель обязательна, деплой в себя запрещён."""

    def _sh(self, *args: str):
        return subprocess.run([_BASH, str(UNINSTALL), *args],
                              capture_output=True, text=True, timeout=60)

    def test_no_target_exits_2(self):
        r = self._sh()
        self.assertEqual(r.returncode, 2)
        self.assertIn("не указана целевая папка", r.stderr)

    def test_missing_target_exits_2(self):
        r = self._sh("/nonexistent/path/zzz")
        self.assertEqual(r.returncode, 2)

    def test_self_target_refused(self):
        r = self._sh(str(REPO))
        self.assertEqual(r.returncode, 2)
        self.assertIn("сам Forge", r.stderr)

    def test_unknown_flag_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._sh(td, "--wat")
            self.assertEqual(r.returncode, 2)

    def test_no_gigacode_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._sh(td)
            self.assertEqual(r.returncode, 0, "нечего снимать — не ошибка (идемпотентность)")


if __name__ == "__main__":
    unittest.main()
