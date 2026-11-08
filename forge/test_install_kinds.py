#!/usr/bin/env python3
"""test_install_kinds.py — три поставки: forge в бэк-репо, forge-ui во фронт-репо, spec-graph в репо спеки.

Гоняет НАСТОЯЩИЕ install.sh / uninstall.sh (исходник не меняется) во временные репо, без
рантайма (--no-link; ссылки пинит hooks/test_forge_extension.Link):
  • фронт-репо (`install.sh forge-ui`): .gigacode/extensions/forge-ui/ — скилл forgeui, общий
    control-plane, скрипты feature-pipeline без SKILL.md, никаких Java-скиллов; /forge — фронтовая;
    в манифесте MCP forge-ui (отвечает на initialize), forge-master — нет; после инициализации
    policy.json (npm по lock-файлу) preflight проходит — проверка путей реестра не объявляет
    битыми неположенные Java-скиллы и находит пути `.gigacode/…` внутри поставки;
  • бэк-репо (`install.sh forge`): .gigacode/extensions/forge/ — все скиллы, кроме forgeui и
    specgraph, все команды, MCP forge-master;
  • репо спеки (`install.sh spec-graph`): .gigacode/extensions/spec-graph/ — скилл specgraph,
    control-plane, скрипты feature-pipeline без SKILL.md; /forge-graph; MCP spec-graph (отвечает на
    initialize); сборки в репо нет — init ставит build_system none и не спрашивает корневой пакет,
    preflight проходит; вторая поставка в тот же репо — отказ;
  • спека в репо кода (`install.sh forge --with-graph`, X.2/D2): к бэк-поставке добавлены скилл specgraph,
    /forge-graph и MCP spec-graph (отвечает), метка «with graph» в профиле; update.sh переставляет с тем же флагом;
    к другим поставкам флаг — отказ; доктор graph_doctor.py видит MCP, команду и хук без рантайма;
  • тексты для модели называют пути своей поставки, и каждый названный путь существует;
  • uninstall снимает каталог поставки целиком и не трогает чужое в .gigacode/.
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


_PATH_RE = re.compile(r"\.gigacode/extensions/(?:forge|forge-ui|spec-graph)/[A-Za-z0-9_./-]+\.(?:py|sh|json|md)")


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
        self.assertFalse((ext / "skills" / "specgraph").exists(), "граф спеки — в репо спеки")
        self.assertTrue((ext / "commands" / "forge-fix.md").is_file())
        self.assertNotIn("forgeui", (ext / "commands" / "forge.md").read_text(encoding="utf-8"))
        self.assertEqual(sorted(self._manifest(ext)["mcpServers"]), ["forge-master"])
        self.assertIn("profile back", (ext / ".forge-profile").read_text(encoding="utf-8"))
        self.assertNotIn("skills/forgeui", (ext / ".forge-deployed").read_text(encoding="utf-8"))
        self._named_paths_exist(tgt, ext, "forge")

        # config-helper правит ту risk-policy, которую читают хуки поставки (v0.5.1 искал её в
        # .gigacode/hooks/ и отвечал exit 3 на любую правку risk.*)
        cfg = [sys.executable, "-X", "utf8", str(ext / "skills/config-helper/scripts/config.py"),
               "--project", str(tgt)]
        r = _run([*cfg, "get", "risk.autonomy_auto_max"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["source"], "file:risk-policy.json")
        r = _run([*cfg, "risk", "list-add", "destructive_blacklist", "DROP SCHEMA", "--confirm"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        policy = json.loads((ext / "hooks" / "risk-policy.json").read_text(encoding="utf-8"))
        self.assertIn("DROP SCHEMA", policy["destructive_blacklist"])

    def test_spec_graph(self):
        tgt = self.root / "spec"
        (tgt / "docs" / "system-requirements" / "task-service").mkdir(parents=True)
        (tgt / "docs" / "system-requirements" / "task-service" / "task-service.md").write_text(
            "# task-service\n", encoding="utf-8")
        _git_init(tgt)
        r = _run(["bash", str(REPO / "install.sh"), "spec-graph", str(tgt), "--no-link"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        ext = tgt / ".gigacode" / "extensions" / "spec-graph"
        skills = sorted(p.name for p in (ext / "skills").iterdir() if p.is_dir())
        self.assertEqual(skills, ["config-helper", "feature-pipeline", "pipeline-state", "specgraph"])
        self.assertTrue((ext / "skills" / "specgraph" / "SKILL.md").is_file())
        self.assertFalse((ext / "skills" / "feature-pipeline" / "SKILL.md").exists(),
                         "Java-конвейер в репо спеки скиллом не светится")
        self.assertEqual(sorted(p.name for p in (ext / "commands").iterdir()), ["forge-graph.md"])
        self.assertIn("specgraph", (ext / "commands" / "forge-graph.md").read_text(encoding="utf-8"))
        self.assertIn("profile graph", (ext / ".forge-profile").read_text(encoding="utf-8"))
        gi = (tgt / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".spec-graph/*\n!.spec-graph/config.json", gi, "производные spec-graph вне git, конфиг — в git")
        self._named_paths_exist(tgt, ext, "spec-graph")

        mcp = self._manifest(ext)["mcpServers"]
        self.assertEqual(sorted(mcp), ["spec-graph"])
        cfg = mcp["spec-graph"]
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        r = _run([cfg["command"], *cfg["args"]], cwd=cfg["cwd"], inp=init + "\n", timeout=60)
        self.assertIn('"spec-graph"', r.stdout, r.stderr)

        py = sys.executable
        r = _run([py, "-X", "utf8", str(ext / "skills/feature-pipeline/scripts/init_pipeline_config.py"),
                  "--project", str(tgt)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        policy = json.loads((tgt / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["project"]["build_system"], "none")
        self.assertIsNone(policy["quality"]["build_command"])
        self.assertNotIn("conventions.package_root", " ".join(policy.get("_incomplete") or []))
        r = _run([py, "-X", "utf8", str(ext / "skills/config-helper/scripts/config.py"),
                  "--project", str(tgt), "set", "jira.enabled", "false"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _run([py, "-X", "utf8", str(ext / "hooks/preflight.py"), "--project", str(tgt), "--json"])
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertEqual(json.loads(r.stdout)["layout"]["mode"], "extension")

        r = _run(["bash", str(REPO / "install.sh"), "forge", str(tgt), "--no-link"])
        self.assertEqual(r.returncode, 2, "вторая поставка в репо спеки")
        self.assertIn("уже стоит spec-graph", r.stderr)
        self.assertFalse((tgt / ".gigacode" / "extensions" / "forge").exists())

        r = _run([py, "-X", "utf8", str(ext / "hooks" / "graph_doctor.py"), "--project", str(tgt)])
        self.assertIn("- MCP spec-graph: в манифесте, отвечает", r.stdout, r.stdout + r.stderr)
        self.assertIn("- поставка: spec-graph; профиль: graph; граф спеки: True", r.stdout)
        self.assertNotIn(str(tgt), r.stdout, "вывод доктора — без путей машины")

        own = tgt / ".gigacode" / "skills" / "my-own" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text("# своё\n", encoding="utf-8")
        r = _run(["bash", str(REPO / "uninstall.sh"), "spec-graph", str(tgt), "--no-link"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertFalse(ext.exists())
        self.assertTrue(own.is_file(), "чужое в .gigacode/ не трогаем")

    def test_forge_with_graph(self):
        tgt = self.root / "code-with-spec"
        tgt.mkdir()
        (tgt / "build.gradle").write_text("group = 'com.example'\n", encoding="utf-8")
        _git_init(tgt)
        r = _run(["bash", str(REPO / "install.sh"), "forge-ui", str(tgt), "--no-link", "--with-graph"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("--with-graph — только к forge", r.stderr)
        r = _run(["bash", str(REPO / "install.sh"), "forge", str(tgt), "--no-link", "--with-graph"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        ext = tgt / ".gigacode" / "extensions" / "forge"
        self.assertTrue((ext / "skills" / "specgraph" / "SKILL.md").is_file())
        self.assertTrue((ext / "skills" / "feature-pipeline" / "SKILL.md").is_file(), "forge целиком")
        self.assertFalse((ext / "skills" / "forgeui").exists())
        self.assertTrue((ext / "commands" / "forge-graph.md").is_file())
        self.assertTrue((ext / "commands" / "forge.md").is_file())
        prof = (ext / ".forge-profile").read_text(encoding="utf-8").splitlines()
        self.assertIn("with graph", prof)
        self.assertNotIn("exclude-skill specgraph", prof)
        mcp = self._manifest(ext)["mcpServers"]
        self.assertEqual(sorted(mcp), ["forge-master", "spec-graph"])
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        r = _run([mcp["spec-graph"]["command"], *mcp["spec-graph"]["args"]], cwd=mcp["spec-graph"]["cwd"],
                 inp=init + "\n", timeout=60)
        self.assertIn('"spec-graph"', r.stdout, r.stderr)
        r = _run([sys.executable, "-X", "utf8", str(ext / "hooks" / "graph_doctor.py"), "--project", str(tgt)])
        self.assertIn("- поставка: forge; профиль: back; граф спеки: True", r.stdout)
        self.assertIn("- хук graph-guard: зарегистрирован", r.stdout)
        self._named_paths_exist(tgt, ext, "forge")

        r = _run(["bash", str(REPO / "update.sh"), str(tgt), "--no-pull", "--no-link"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertTrue((ext / "commands" / "forge-graph.md").is_file(), "update.sh сохранил граф")
        self.assertIn("with graph", (ext / ".forge-profile").read_text(encoding="utf-8").splitlines())
        r = _run(["bash", str(REPO / "install.sh"), "forge", str(tgt), "--no-link"])
        self.assertEqual(r.returncode, 0)
        self.assertFalse((ext / "skills" / "specgraph").exists(), "без флага — без графа")
        self.assertEqual(sorted(self._manifest(ext)["mcpServers"]), ["forge-master"])

    def test_unknown_kind_refused(self):
        tgt = self.root / "x"
        tgt.mkdir()
        r = _run(["bash", str(REPO / "install.sh"), "mobile", str(tgt)])
        self.assertEqual(r.returncode, 2)
        self.assertFalse((tgt / ".gigacode").exists(), "на отказе ничего не скопировано")


if __name__ == "__main__":
    unittest.main()
