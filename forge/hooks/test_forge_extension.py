#!/usr/bin/env python3
"""test_forge_extension.py — сборка extension'а forge/forge-ui внутри проекта и его подключение.

Пинит:
  • имя extension'а — детерминированное от пути проекта и разное у двух проектов (реестр ссылок
    рантайма общий на пользователя: два «forge» — «already installed»);
  • hooks/hooks.json: каждый хук эталона — `<python> <ext>/hooks/_run_hook.py <ext>/hooks/<хук>.py`,
    файлы на месте; манифест-близнецы gigacode/qwen; MCP — ровно профиля поставки;
  • тексты для модели (*.md) называют пути extension'а, а запреты `~/.gigacode/…` не трогаются;
  • check(): свежая сборка чистая; переезд проекта, пропавший хук, хук вне extension'а — названы;
  • _run_hook.py: в своём проекте (и подкаталоге) хук исполняется, в чужом — молча 0, нет файла
    хука — 2 (громко, как `python <нет файла>`);
  • link/unlink на поддельном CLI: ссылка, «выключен под $HOME / включён здесь», взаимное
    выключение с другими forge-проектами, повтор узнаёт свою ссылку, без CLI — ручные команды.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent
REPO = HOOKS.parent
sys.path.insert(0, str(HOOKS))

import forge_extension as FX  # noqa: E402
import _run_hook as RH  # noqa: E402


def make_ext(root: Path, kind: str) -> Path:
    """Поставка как её кладёт install.sh (без скиллов — сборке они не нужны, кроме текстов)."""
    ext = FX.ext_dir(root, kind)
    shutil.copytree(HOOKS, ext / "hooks", ignore=shutil.ignore_patterns("__pycache__", "test_*", "evals", "tests"))
    prof = "back" if kind == "forge" else "ui"
    shutil.copy(REPO / "profiles" / f"{prof}.profile", ext / ".forge-profile")
    (ext / "skills" / "demo").mkdir(parents=True)
    (ext / "skills" / "demo" / "SKILL.md").write_text(
        "Запусти `python3 <project>/.gigacode/skills/pipeline-state/scripts/update.py`, проверь "
        "`.gigacode/hooks/preflight.py`, почини `bash .gigacode/deploy-local.sh`.\n"
        "Не используй ~/.gigacode/skills и $HOME/.gigacode/hooks/x.\n", encoding="utf-8")
    return ext


class Names(unittest.TestCase):
    def test_name_is_stable_per_path_and_differs_between_projects(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(FX.extension_name("forge", a), FX.extension_name("forge", a))
            self.assertNotEqual(FX.extension_name("forge", a), FX.extension_name("forge", b))
            self.assertTrue(FX.extension_name("forge-ui", a).startswith("forge-ui-"))

    def test_slug_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Мой Проект (v2)"
            p.mkdir()
            name = FX.extension_name("forge", p)
            self.assertRegex(name, r"^forge-[a-z0-9-]+-[0-9a-f]{6}$")


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def built(self, kind="forge"):
        ext = make_ext(self.root, kind)
        out = FX.build(ext, self.root, kind, "forge/v0.6.0-3-gabc")
        return ext, out

    def test_every_template_hook_goes_through_the_runner(self):
        ext, _ = self.built()
        tmpl = json.loads((ext / "hooks" / "settings.hooks.json").read_text(encoding="utf-8"))["hooks"]
        hooks = json.loads((ext / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(sorted(hooks), sorted(tmpl))
        runner = FX._cmd_path(ext / "hooks" / "_run_hook.py")
        n = 0
        for event, groups in hooks.items():
            for gi, g in enumerate(groups):
                self.assertEqual(g.get("matcher"), tmpl[event][gi].get("matcher"), "матчеры — как в эталоне")
                for h in g["hooks"]:
                    scripts = FX._scripts_of(h["command"])
                    self.assertEqual(scripts[0], runner, h["command"])
                    self.assertTrue(Path(scripts[1]).is_file(), h["command"])
                    self.assertNotIn("${", h["command"])
                    n += 1
        self.assertGreater(n, 20)

    def test_manifest_twins_and_mcp_of_the_profile(self):
        ext, out = self.built("forge")
        a = json.loads((ext / "gigacode-extension.json").read_text(encoding="utf-8"))
        b = json.loads((ext / "qwen-extension.json").read_text(encoding="utf-8"))
        self.assertEqual(a, b)
        self.assertEqual(a["name"], FX.extension_name("forge", self.root))
        self.assertEqual(a["version"], "0.6.0-3-gabc")
        self.assertNotIn("displayName", a, "extensions list показывает displayName вместо имени")
        self.assertEqual(sorted(a["mcpServers"]), ["forge-master"])
        ui, _ = self.built("forge-ui")
        m = json.loads((ui / "gigacode-extension.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(m["mcpServers"]), ["forge-ui"])
        args = m["mcpServers"]["forge-ui"]["args"]
        self.assertTrue(any(x.endswith("/forge-ui/hooks/daiver_mcp.py") for x in args), args)

    def test_mcp_server_answers_initialize(self):
        ext, _ = self.built("forge-ui")
        shutil.copytree(REPO / "skills" / "forgeui", ext / "skills" / "forgeui",
                        ignore=shutil.ignore_patterns("__pycache__"))      # движок сервер ищет рядом с собой
        cfg = json.loads((ext / "gigacode-extension.json").read_text(encoding="utf-8"))["mcpServers"]["forge-ui"]
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        r = subprocess.run([cfg["command"], *cfg["args"]], cwd=cfg["cwd"], input=init + "\n",
                           capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertIn('"forge-ui"', r.stdout, r.stderr)

    def test_texts_name_the_extension_paths_and_leave_home_bans(self):
        ext, out = self.built("forge-ui")
        text = (ext / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("<project>/.gigacode/extensions/forge-ui/skills/pipeline-state/scripts/update.py", text)
        self.assertIn("`.gigacode/extensions/forge-ui/hooks/preflight.py`", text)
        self.assertIn("bash .gigacode/extensions/forge-ui/deploy-local.sh", text)
        self.assertIn("~/.gigacode/skills", text)
        self.assertIn("$HOME/.gigacode/hooks/x", text)
        self.assertGreaterEqual(out["rewritten"], 1)
        self.assertEqual(FX.rewrite_text(text, "forge-ui"), text, "повторная сборка — без двойной подстановки")

    def test_fresh_build_checks_clean(self):
        ext, _ = self.built()
        self.assertEqual(FX.check(ext, self.root), [])

    def test_check_names_a_moved_project_a_missing_hook_and_a_foreign_path(self):
        ext, _ = self.built()
        with tempfile.TemporaryDirectory() as other:
            self.assertTrue(any("переехал" in p for p in FX.check(ext, other)))
        (ext / "hooks" / "gate-guard.py").unlink()
        self.assertTrue(any("gate-guard.py" in p and "нет файла" in p for p in FX.check(ext, self.root)))
        hooks_json = ext / "hooks" / "hooks.json"
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
        data["hooks"]["Stop"][0]["hooks"][0]["command"] = "python3 /elsewhere/phase-gate.py"
        hooks_json.write_text(json.dumps(data), encoding="utf-8")
        probs = FX.check(ext, self.root)
        self.assertTrue(any("_run_hook.py" in p for p in probs), probs)
        self.assertTrue(any("вне extension" in p for p in probs), probs)


class Runner(unittest.TestCase):
    """_run_hook.py — хук только в своём проекте."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "proj"
        hooks = self.root / ".gigacode" / "extensions" / "forge" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(HOOKS / "_run_hook.py", hooks / "_run_hook.py")
        (hooks / "probe.py").write_text(textwrap.dedent("""
            import sys, json
            data = json.load(sys.stdin)
            print("ran", data["x"], sys.argv[1:])
            sys.exit(2 if data["x"] == "block" else 0)
        """), encoding="utf-8")
        self.hooks = hooks
        self.other = Path(self.tmp.name) / "other"
        self.other.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_in(self, cwd: Path, payload="block", hook="probe.py"):
        env = {k: v for k, v in os.environ.items() if k not in RH._PROJECT_ENV}
        env["QWEN_PROJECT_DIR"] = str(cwd)
        return subprocess.run([sys.executable, str(self.hooks / "_run_hook.py"), str(self.hooks / hook), "extra"],
                              input=json.dumps({"x": payload}), cwd=str(cwd), env=env,
                              capture_output=True, text=True, timeout=30)

    def test_own_project_runs_the_hook_with_its_stdin_args_and_exit_code(self):
        r = self.run_in(self.root)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ran block ['extra']", r.stdout)
        sub = self.root / "src" / "main"
        sub.mkdir(parents=True)
        self.assertEqual(self.run_in(sub, "ok").returncode, 0)
        self.assertIn("ran ok", self.run_in(sub, "ok").stdout)

    def test_foreign_project_is_silent_zero(self):
        r = self.run_in(self.other)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_missing_hook_is_loud_two(self):
        r = self.run_in(self.root, hook="nope.py")
        self.assertEqual(r.returncode, 2)
        self.assertIn("хук не найден", r.stderr)

    def test_outside_the_extension_layout_nothing_is_foreign(self):
        self.assertIsNone(RH.install_root(HOOKS / "_run_hook.py"))
        self.assertFalse(RH.is_foreign(None, "/anything"))


FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
home = Path(os.environ["HOME"]) / ".gigacode" / "extensions"
log = Path(os.environ["FAKE_LOG"])
args = sys.argv[1:]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
if args[:1] != ["extensions"]:
    sys.exit(2)
cmd = args[1]
if cmd == "link":
    src = Path(args[2])
    name = json.loads((src / "gigacode-extension.json").read_text(encoding="utf-8"))["name"]
    d = home / name
    if d.exists():
        print(f'Extension "{name}" is already installed.', file=sys.stderr); sys.exit(1)
    sys.stdin.read()
    d.mkdir(parents=True)
    (d / ".gigacode-extension-install.json").write_text(json.dumps({"type": "link", "source": str(src)}), encoding="utf-8")
elif cmd == "uninstall":
    d = home / args[2]
    if not d.exists():
        sys.exit(1)
    for p in d.iterdir():
        p.unlink()
    d.rmdir()
elif cmd in ("enable", "disable", "list"):
    pass
sys.exit(0)
'''


class Link(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        bindir = base / "bin"
        bindir.mkdir()
        self.cli = bindir / "gigacode"
        self.cli.write_text(FAKE_CLI.replace("#!/usr/bin/env python3", f"#!{sys.executable}"), encoding="utf-8")
        self.cli.chmod(0o755)
        self.log = base / "cli.log"
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "FAKE_LOG": str(self.log),
                                                "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                                                "FORGE_RUNTIME": ""})
        self.env.start()
        self.a = base / "home" / "a"
        self.b = base / "home" / "b"
        for p, kind in ((self.a, "forge"), (self.b, "forge-ui")):
            p.mkdir()
            FX.build(make_ext(p, kind), p, kind, "x")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def test_link_scopes_to_the_workspace(self):
        ok, lines = FX.link(FX.ext_dir(self.a, "forge"), self.a)
        self.assertTrue(ok, lines)
        name = FX.extension_name("forge", self.a)
        self.assertEqual(FX.links(self.home / ".gigacode")[name], str(FX.ext_dir(self.a, "forge")))
        c = self.calls()
        self.assertIn(["extensions", "disable", "--scope", "user", name], [x["args"] for x in c])
        en = [x for x in c if x["args"] == ["extensions", "enable", "--scope", "workspace", name]]
        self.assertEqual([os.path.realpath(x["cwd"]) for x in en], [os.path.realpath(self.a)])

    def test_a_second_forge_project_is_disabled_both_ways(self):
        FX.link(FX.ext_dir(self.a, "forge"), self.a)
        self.log.unlink()
        ok, lines = FX.link(FX.ext_dir(self.b, "forge-ui"), self.b)
        self.assertTrue(ok, lines)
        na, nb = FX.extension_name("forge", self.a), FX.extension_name("forge-ui", self.b)
        dis = {(tuple(x["args"]), os.path.realpath(x["cwd"])) for x in self.calls() if x["args"][1] == "disable"}
        self.assertIn((("extensions", "disable", "--scope", "workspace", na), os.path.realpath(self.b)), dis)
        self.assertIn((("extensions", "disable", "--scope", "workspace", nb), os.path.realpath(self.a)), dis)
        self.assertTrue(any("выключены друг у друга" in l for l in lines))

    def test_relink_recognises_its_own_link_and_replaces_a_stale_one(self):
        FX.link(FX.ext_dir(self.a, "forge"), self.a)
        ok, lines = FX.link(FX.ext_dir(self.a, "forge"), self.a)
        self.assertTrue(ok)
        self.assertTrue(any("уже стоит" in l for l in lines), lines)
        name = FX.extension_name("forge", self.a)
        meta = self.home / ".gigacode" / "extensions" / name / ".gigacode-extension-install.json"
        meta.write_text(json.dumps({"type": "link", "source": "/old/place"}), encoding="utf-8")
        ok, lines = FX.link(FX.ext_dir(self.a, "forge"), self.a)
        self.assertTrue(ok, lines)
        self.assertEqual(FX.links(self.home / ".gigacode")[name], str(FX.ext_dir(self.a, "forge")))

    def test_unlink(self):
        FX.link(FX.ext_dir(self.a, "forge"), self.a)
        ok, lines = FX.unlink(FX.ext_dir(self.a, "forge"), self.a)
        self.assertTrue(ok, lines)
        self.assertNotIn(FX.extension_name("forge", self.a), FX.links(self.home / ".gigacode"))
        ok, lines = FX.unlink(FX.ext_dir(self.a, "forge"), self.a)
        self.assertTrue(ok)
        self.assertTrue(any("нет" in l for l in lines))

    def test_no_cli_gives_manual_commands(self):
        with mock.patch.object(FX, "runtime_cli", return_value=None):
            ok, lines = FX.link(FX.ext_dir(self.a, "forge"), self.a)
        self.assertFalse(ok)
        self.assertTrue(any("extensions link" in l for l in lines))
        self.assertTrue(any("--scope workspace" in l for l in lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
