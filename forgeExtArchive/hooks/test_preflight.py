#!/usr/bin/env python3
"""Tests for hooks/preflight.py — wiring-aware готовность харнеса.

Главное: preflight обязан падать, если essential-хук НЕ подключён в settings.json
(а не просто лежит файлом), и если risk-policy.json отсутствует/битый.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("preflight", HOOKS / "preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

ESSENTIAL = ["gate-guard.py", "phase-gate.py", "state-recorder.py", "eval-guard.py",
             "state-write-guard.py", "grounding-evidence.py"]


def _hooks_block(root: Path, names: list[str]) -> dict:
    cmds = [{"type": "command",
             "command": f"python3 {root}/.gigacode/hooks/{n}", "name": n}
            for n in names]
    return {"PreToolUse": [{"matcher": "*", "hooks": cmds}]}


def _make(tmp: Path, *, wired: list[str], policy_ok: bool = True) -> None:
    (tmp / "ground").mkdir(parents=True, exist_ok=True)
    (tmp / "ground" / "pipeline.json").write_text(json.dumps({"quality": {}}), encoding="utf-8")

    gh = tmp / ".gigacode" / "hooks"
    gh.mkdir(parents=True, exist_ok=True)
    for n in ESSENTIAL:                       # все файлы есть на диске
        (gh / n).write_text("# stub\n", encoding="utf-8")

    block = _hooks_block(tmp, wired)
    (gh / "settings.hooks.json").write_text(json.dumps({"hooks": block}), encoding="utf-8")
    (tmp / ".gigacode" / "settings.json").write_text(
        json.dumps({"hooks": block, "disableAllHooks": False}), encoding="utf-8")

    rp = gh / "risk-policy.json"
    rp.write_text('{"version":1}' if policy_ok else "{ broken json", encoding="utf-8")


def _fake_doctor(tmp: Path, problems: list[str]) -> None:
    """Стаб doctor.py, возвращающий заданные problems (exit 1)."""
    d = tmp / ".gigacode" / "skills" / "feature-pipeline" / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"passed": not problems, "problems": problems, "checks": []})
    (d / "doctor.py").write_text(
        f"import sys\nprint({payload!r})\nsys.exit({1 if problems else 0})\n", encoding="utf-8")


class TestPreflight(unittest.TestCase):
    def test_broken_registry_paths_is_error(self):
        # Битые межскилловые пути (skill-paths.json) должны ВАЛИТЬ preflight, не warn
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            _fake_doctor(tmp, ["registry-paths-exist: битые пути: ['minor-defect-fix/...']"])
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertTrue(any("registry-paths-exist" in e for e in res["errors"]), res)

    def test_other_doctor_problems_stay_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            _fake_doctor(tmp, ["evals-declared: у скилла нет evals"])
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertTrue(res["passed"], res.get("errors"))
            self.assertTrue(any("evals-declared" in w for w in res.get("warnings", [])), res)

    def test_all_wired_passes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertTrue(res["passed"], res.get("errors"))

    def test_eval_guard_not_wired_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=[n for n in ESSENTIAL if n != "eval-guard.py"])
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertTrue(any("eval-guard.py" in e for e in res["errors"]), res["errors"])

    def test_corrupt_risk_policy_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL, policy_ok=False)
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertTrue(any("risk-policy" in e for e in res["errors"]), res["errors"])

    def test_missing_risk_policy_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / ".gigacode" / "hooks" / "risk-policy.json").unlink()
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertTrue(any("risk-policy" in e for e in res["errors"]), res["errors"])

    def test_missing_pipeline_json_is_init_needed_not_error(self):
        # Свежий деплой: pipeline.json ещё нет. Это НЕ enforcement off (не в errors),
        # но и не passed (гейт арминга): init_needed + passed False.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").unlink()
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertEqual(res["errors"], [], res)   # НЕ ошибка
            self.assertTrue(any("pipeline.json" in m for m in res.get("init_needed", [])), res)

    def test_incomplete_pipeline_json_is_init_needed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").write_text(
                json.dumps({"_incomplete": ["criticality"]}), encoding="utf-8")
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertEqual(res["errors"], [], res)
            self.assertTrue(any("incomplete" in m for m in res.get("init_needed", [])), res)

    def test_corrupt_pipeline_json_is_hard_error(self):
        # Битый JSON (конфиг ЕСТЬ, но не читается) — реальная ошибка, а не «не инициализирован».
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").write_text("{ broken", encoding="utf-8")
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertTrue(any("pipeline.json parse error" in e for e in res["errors"]), res)
            self.assertEqual(res.get("init_needed", []), [], res)


class TestExitCodes(unittest.TestCase):
    """CLI-контракт: 0=армирован, 2=не инициализирован (init_needed), 1=enforcement off."""

    def _exit(self, tmp: Path) -> int:
        # Гоняем КОПИЮ preflight.py, положенную в фикстуру: `_self_base()` считает базу от
        # расположения файла, значит у копии это `<tmp>/.gigacode` — project-раскладка, которую
        # и проверяют эти тесты. Оригинал лежит в extension'е рядом с `hooks/hooks.json`, и
        # `resolve_layout` ушёл бы в extension-ветку. CLI-флага для self_base нет (в бою база
        # ВСЕГДА фактическая), поэтому пиним через фактическое расположение, а не аргументом.
        deployed = tmp / ".gigacode" / "hooks" / "preflight.py"
        deployed.write_text((HOOKS / "preflight.py").read_text(encoding="utf-8"), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(deployed), "--project", str(tmp)],
            capture_output=True, text=True, encoding="utf-8").returncode

    def test_exit_0_when_armed(self):
        with tempfile.TemporaryDirectory() as d:
            # .resolve(): __main__ резолвит --project (на macOS /var→/private/var),
            # пути хуков в фикстуре должны совпасть с этим префиксом, иначе ложный «чужой путь».
            tmp = Path(d).resolve()
            _make(tmp, wired=ESSENTIAL)
            self.assertEqual(self._exit(tmp), 0)

    def test_exit_2_when_not_initialized(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").unlink()
            self.assertEqual(self._exit(tmp), 2)

    def test_exit_1_when_enforcement_off(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            _make(tmp, wired=[n for n in ESSENTIAL if n != "eval-guard.py"])
            self.assertEqual(self._exit(tmp), 1)

    def test_enforcement_off_beats_init_needed(self):
        # Есть и enforcement-ошибка, и отсутствие конфига → exit 1 (жёсткая причина главнее).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            _make(tmp, wired=[n for n in ESSENTIAL if n != "eval-guard.py"])
            (tmp / "ground" / "pipeline.json").unlink()
            self.assertEqual(self._exit(tmp), 1)


def _make_ext(ext: Path, *, wired: list[str] | None = None, policy_ok: bool = True,
              cmd_prefix: str = "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/") -> None:
    """Фикстура extension-раскладки: <ext>/hooks/{*.py,hooks.json,risk-policy.json} + манифест."""
    wired = ESSENTIAL if wired is None else wired
    hooks = ext / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for n in ESSENTIAL:                       # файлы есть все, wiring — только `wired`
        (hooks / n).write_text("# stub\n", encoding="utf-8")
    block = {"PreToolUse": [{"matcher": "^(run_shell_command|Bash)$", "hooks": [
        {"type": "command", "command": f"{cmd_prefix}{n}", "name": n} for n in wired]}]}
    (hooks / "hooks.json").write_text(json.dumps({"hooks": block}), encoding="utf-8")
    (hooks / "risk-policy.json").write_text(
        '{"version":1}' if policy_ok else "{ broken json", encoding="utf-8")
    (ext / "qwen-extension.json").write_text(json.dumps({"name": "forge"}), encoding="utf-8")


def _make_project(tmp: Path) -> None:
    """Проект БЕЗ развёрнутого .gigacode: только данные (ground/pipeline.json)."""
    (tmp / "ground").mkdir(parents=True, exist_ok=True)
    (tmp / "ground" / "pipeline.json").write_text(json.dumps({"quality": {}}), encoding="utf-8")


def _install_link(home: Path, ext: Path, runtime: str = ".qwen") -> Path:
    """Имитация `extensions link`: каталог-указатель на источник."""
    d = home / runtime / "extensions" / "forge"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".qwen-extension-install.json").write_text(
        json.dumps({"source": str(ext), "type": "link"}), encoding="utf-8")
    return d


class TestExtensionLayout(unittest.TestCase):
    """Форж, установленный как нативный extension: код в <ext>/, wiring — hooks/hooks.json.

    Регрессия: preflight проверял ТОЛЬКО <project>/.gigacode и в extension-раскладке валил
    старт («хуки не задеплоены», exit 1 = ENFORCEMENT OFF), хотя харнес был поднят.
    """

    def _run(self, tmp: Path, ext: Path, home: Path) -> dict:
        with unittest.mock.patch.object(preflight, "_home", lambda: home):
            return preflight.preflight(str(tmp), self_base=ext)

    def test_installed_extension_passes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_project(tmp)
            _make_ext(ext)
            _install_link(home, ext)
            res = self._run(tmp, ext, home)
            self.assertTrue(res["passed"], res)
            self.assertEqual(res["layout"], {"mode": "extension", "base": str(ext)}, res)

    def test_not_installed_in_runtime_is_error(self):
        # Каталог extension'ов есть, нашего там нет → рантайм хуки не грузит = enforcement off.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_project(tmp)
            _make_ext(ext)
            (home / ".qwen" / "extensions" / "other").mkdir(parents=True)
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"])
            self.assertTrue(any("не установлен" in e for e in res["errors"]), res["errors"])

    def test_no_registry_dirs_is_warning_not_block(self):
        # Каталогов extensions вообще нет (другой рантайм/своя раскладка) — проверить нечем,
        # но блокировать старт из-за этого нельзя.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            home.mkdir()
            _make_project(tmp)
            _make_ext(ext)
            res = self._run(tmp, ext, home)
            self.assertTrue(res["passed"], res)
            self.assertTrue(any("установку extension" in w for w in res.get("warnings", [])), res)

    def test_other_copy_loaded_is_warning(self):
        # Рантайм грузит копию с тем же именем, но по другому пути (link на старый источник).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_project(tmp)
            _make_ext(ext)
            _install_link(home, tmp / "stale-ext")
            res = self._run(tmp, ext, home)
            self.assertTrue(res["passed"], res)
            self.assertTrue(any("другую копию" in w for w in res.get("warnings", [])), res)

    def test_essential_hook_not_wired_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_project(tmp)
            _make_ext(ext, wired=[n for n in ESSENTIAL if n != "eval-guard.py"])
            _install_link(home, ext)
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"])
            self.assertTrue(any("eval-guard.py" in e for e in res["errors"]), res["errors"])

    def test_corrupt_risk_policy_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_project(tmp)
            _make_ext(ext, policy_ok=False)
            _install_link(home, ext)
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"])
            self.assertTrue(any("risk-policy" in e for e in res["errors"]), res["errors"])

    def test_hook_path_outside_plugin_root_flagged(self):
        # Пути в hooks.json обязаны идти от ${CLAUDE_PLUGIN_ROOT} — иначе на чужой машине
        # (install-копия, другой home) рантайм зовёт несуществующие файлы.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_project(tmp)
            _make_ext(ext, cmd_prefix="python3 /opt/forge-old/hooks/")
            _install_link(home, ext)
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"])
            self.assertTrue(any("вне ожидаемого каталога" in e for e in res["errors"]),
                            res["errors"])

    def test_missing_pipeline_json_is_init_needed(self):
        # Гейт арминга и exit-контракт в extension-раскладке те же: 2, а не 1.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_ext(ext)
            _install_link(home, ext)
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"])
            self.assertEqual(res["errors"], [], res)
            self.assertTrue(res.get("init_needed"), res)

    def test_legacy_hooks_in_runtime_settings_warn(self):
        # Extension грузится ПОВЕРХ settings.json: forge-хуки из старого deploy.sh задвоят
        # цепочку (INSTALL.md §1).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make_project(tmp)
            _make_ext(ext)
            _install_link(home, ext)
            (home / ".qwen").mkdir(parents=True, exist_ok=True)
            (home / ".qwen" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "*", "hooks": [{"command": "python3 /old/.gigacode/hooks/gate-guard.py"}]}]}}),
                encoding="utf-8")
            res = self._run(tmp, ext, home)
            self.assertTrue(res["passed"], res)
            self.assertTrue(any("задвоится" in w for w in res.get("warnings", [])), res)


def _make_skill(root: Path, name: str) -> Path:
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n", encoding="utf-8")
    return d


class TestShadowingCopies(unittest.TestCase):
    """Старые копии скиллов/команд перекрывают extension — рантайм грузит НЕ его код.

    Регрессия из жизни: после переезда на extension в `~/.qwen/skills/feature-pipeline`
    остался бриф от прежнего deploy.sh. Скиллы резолвятся project > user > extension, поэтому
    оркестратор шёл по старому SKILL.md (`python3 ~/.gigacode/hooks/preflight.py` — путь мёртв),
    а preflight этого не видел: хуки-то грузились из extension'а и были зелёными.
    """

    def _run(self, tmp: Path, ext: Path, home: Path) -> dict:
        with unittest.mock.patch.object(preflight, "_home", lambda: home):
            return preflight.preflight(str(tmp), self_base=ext)

    def _armed(self, tmp: Path, ext: Path, home: Path) -> None:
        _make_project(tmp)
        _make_ext(ext)
        _install_link(home, ext)
        _make_skill(ext, "feature-pipeline")

    def test_stale_user_skill_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            self._armed(tmp, ext, home)
            _make_skill(home / ".qwen", "feature-pipeline")
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"], res)
            self.assertTrue(any("перекрывают extension" in e and "feature-pipeline" in e
                                for e in res["errors"]), res["errors"])

    def test_stale_project_skill_is_error(self):
        # project-уровень бьёт user и extension — самый опасный случай на форке GigaCode,
        # где `<project>/.gigacode` был целью прежнего deploy.sh.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            self._armed(tmp, ext, home)
            _make_skill(tmp / ".gigacode", "feature-pipeline")
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"], res)
            self.assertTrue(any("перекрывают extension" in e for e in res["errors"]),
                            res["errors"])

    def test_stale_command_is_error(self):
        # Одноимённую команду extension'а рантайм переименовывает — `/forge` уедет на старую.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            self._armed(tmp, ext, home)
            (ext / "commands").mkdir(parents=True, exist_ok=True)
            (ext / "commands" / "forge.md").write_text("new\n", encoding="utf-8")
            (home / ".qwen" / "commands").mkdir(parents=True, exist_ok=True)
            (home / ".qwen" / "commands" / "forge.md").write_text("old\n", encoding="utf-8")
            res = self._run(tmp, ext, home)
            self.assertFalse(res["passed"], res)
            self.assertTrue(any("commands" in e and "forge" in e for e in res["errors"]), res["errors"])

    def test_foreign_skill_with_other_name_is_ignored(self):
        # Чужие скиллы оператора в тех же каталогах — не наша забота, блокировать нельзя.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            self._armed(tmp, ext, home)
            _make_skill(home / ".qwen", "pptx")
            res = self._run(tmp, ext, home)
            self.assertTrue(res["passed"], res)

    def test_symlink_to_extension_is_not_shadowing(self):
        # Каталог рантайма, указывающий на сам extension, — это он же, а не подмена.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            self._armed(tmp, ext, home)
            (home / ".qwen" / "skills").mkdir(parents=True, exist_ok=True)
            try:
                (home / ".qwen" / "skills" / "feature-pipeline").symlink_to(
                    ext / "skills" / "feature-pipeline", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks недоступны")
            res = self._run(tmp, ext, home)
            self.assertTrue(res["passed"], res)


class TestLayoutResolution(unittest.TestCase):
    """Проверяется та раскладка, из которой запущен preflight; вторая — предупреждение."""

    def test_installed_extension_wins_over_stale_project_deploy(self):
        # Ключевая регрессия: устаревший .gigacode в репо НЕ должен валить старт, когда
        # энфорсмент реально идёт через установленный extension.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make(tmp, wired=ESSENTIAL)     # legacy deploy в проекте
            (tmp / ".gigacode" / "settings.json").unlink()   # и он «протух»
            _make_ext(ext)                  # и одновременно extension
            _install_link(home, ext)
            with unittest.mock.patch.object(preflight, "_home", lambda: home):
                res = preflight.preflight(str(tmp), self_base=ext)
            self.assertEqual(res["layout"]["mode"], "extension", res)
            self.assertTrue(res["passed"], res)
            self.assertTrue(any("задвоятся" in w for w in res.get("warnings", [])), res)

    def test_uninstalled_extension_falls_back_to_project_deploy(self):
        # Каталог extension'а рядом есть, но рантайм его не грузит → проверяем рабочий деплой.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            ext, home = tmp / "ext", tmp / "home"
            _make(tmp, wired=ESSENTIAL)
            _make_ext(ext)
            (home / ".qwen" / "extensions" / "other").mkdir(parents=True)
            with unittest.mock.patch.object(preflight, "_home", lambda: home):
                res = preflight.preflight(str(tmp), self_base=ext)
            self.assertEqual(res["layout"]["mode"], "project", res)
            self.assertTrue(res["passed"], res)
            self.assertTrue(any("не грузит" in w for w in res.get("warnings", [])), res)

    def test_nothing_installed_reports_project_errors(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d).resolve()
            _make_project(tmp)
            res = preflight.preflight(str(tmp), self_base=tmp / "nowhere")
            self.assertFalse(res["passed"])
            self.assertEqual(res["layout"]["mode"], "none", res)
            self.assertTrue(any("settings.hooks.json not found" in e for e in res["errors"]), res)


class TestMatcherCanonical(unittest.TestCase):
    """BLOCKER-0 на уровне preflight: матчеры блок-цепочек обязаны матчить канон-имена рантайма."""

    @staticmethod
    def _block(bash_m: str, write_m: str) -> dict:
        return {"PreToolUse": [
            {"matcher": bash_m, "hooks": [{"command": "python3 x/destructive-blocker.py"}]},
            {"matcher": write_m, "hooks": [{"command": "python3 x/tdd-guard.py"}]},
        ]}

    def test_claude_notation_matchers_flagged(self):
        errs = preflight._check_matchers_canonical(
            self._block("^Bash$", "(Write|Edit|WriteFile|NotebookEdit)"), "settings.json")
        self.assertEqual(len(errs), 2, errs)
        self.assertTrue(any("run_shell_command" in e for e in errs))

    def test_canonical_matchers_ok(self):
        errs = preflight._check_matchers_canonical(
            self._block("^(run_shell_command|Bash)$",
                        "^(write_file|edit|notebook_edit|Write|Edit|WriteFile|NotebookEdit)$"),
            "settings.json")
        self.assertEqual(errs, [])


class TestFindForeignHookPaths(unittest.TestCase):
    """Регрессия: на Windows project_root — обратные слэши (Path(...).resolve()), хвост из
    шаблона settings.hooks.json — прямые; итоговый путь в command смешанный. Раньше
    expected_prefix строился через os.path.join() (чисто обратные слэши на Windows) и
    никогда не совпадал с реальным смешанным форматом — свои хуки считались "чужими"."""

    def test_windows_mixed_separator_own_path_not_foreign(self):
        project_root = r"C:\Work\JavaProjects\pprb-kid"
        cmd = (r'"C:\Program Files\Python313\python.exe" -X utf8 '
               rf"{project_root}/.gigacode/hooks/destructive-blocker.py")
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
        found = preflight._find_foreign_hook_paths(settings, project_root)
        self.assertEqual(found, [], found)

    def test_windows_forward_slash_command_own_path_not_foreign(self):
        """resolve_hook_paths.py теперь подставляет в command ПРЯМОЙ слэш (backslash рантайм
        съедал при POSIX-разборе). project_root в preflight — из Path(...).resolve(), т.е.
        обратные слэши. Без нормализации свой же хук ложно попал бы в foreign → preflight
        зациклил бы совет запустить deploy-local.sh."""
        project_root = r"C:\Work\JavaProjects\pprb-kid"
        cmd = ('"C:/Program Files/Python313/python.exe" -X utf8 '
               "C:/Work/JavaProjects/pprb-kid/.gigacode/hooks/destructive-blocker.py")
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
        found = preflight._find_foreign_hook_paths(settings, project_root)
        self.assertEqual(found, [], found)

    def test_windows_foreign_path_still_flagged(self):
        project_root = r"C:\Work\JavaProjects\pprb-kid"
        cmd = (r'"C:\Program Files\Python313\python.exe" -X utf8 '
               r"C:\Other\proj/.gigacode/hooks/destructive-blocker.py")
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
        found = preflight._find_foreign_hook_paths(settings, project_root)
        self.assertEqual(len(found), 1, found)

    def test_posix_own_path_not_foreign(self):
        project_root = "/home/user/proj"
        cmd = "/usr/bin/python3 -X utf8 /home/user/proj/.gigacode/hooks/gate-guard.py"
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
        found = preflight._find_foreign_hook_paths(settings, project_root)
        self.assertEqual(found, [], found)


if __name__ == "__main__":
    unittest.main()
