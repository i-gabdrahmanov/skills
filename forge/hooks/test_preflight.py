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
        # Свежий деплой: v2 policy.json ещё нет. Это НЕ enforcement off (не в errors),
        # но и не passed (гейт арминга): init_needed + passed False.
        # Также проверяем legacy-случай: pipeline.json без policy.json — должна быть
        # подсказка мигрировать.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").unlink()
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            self.assertEqual(res["errors"], [], res)   # НЕ ошибка
            # v2: ожидаем подсказку про policy.json
            self.assertTrue(any("policy.json" in m for m in res.get("init_needed", [])), res)

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

    def test_incomplete_config_points_to_answers_not_init(self):
        """Боевой прогон B3: на дозаполнении preflight звал тот же init_pipeline_config, который
        эти поля не заполняет, — exit 2 по кругу. Нужна команда записи ответа."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").write_text(
                json.dumps({"_incomplete": ["jira.enabled", "conventions.package_root"]}),
                encoding="utf-8")
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertNotIn("init_command", res)
            cmds = res.get("answer_commands", [])
            self.assertEqual(len(cmds), 2, res)
            self.assertTrue(all("config.py" in c and " set " in c for c in cmds), cmds)
            self.assertTrue(any("set jira.enabled" in c for c in cmds), cmds)

    def test_missing_config_still_points_to_init(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").unlink()
            res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertIn("init_pipeline_config.py", res.get("init_command", ""))

    def test_corrupt_pipeline_json_is_hard_error(self):
        # Битый JSON (конфиг ЕСТЬ, но не читается) — в v2 load_project_config
        # эмитит UserWarning и возвращает {} (best-effort). preflight должен
        # сообщить о corruption через warnings, но НЕ падать с exit 1.
        # (Раньше это было hard error в preflight — в v2 best-effort
        # semantic перенесена на уровень loader.)
        import warnings as _warnings
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make(tmp, wired=ESSENTIAL)
            (tmp / "ground" / "pipeline.json").write_text("{ broken", encoding="utf-8")
            with _warnings.catch_warnings(record=True) as caught:
                _warnings.simplefilter("always")
                res = preflight.preflight(str(tmp), self_base=tmp / ".gigacode")
            self.assertFalse(res["passed"])
            # Corruption должна быть зарегистрирована в warnings
            self.assertTrue(
                any("corrupt" in str(w.message).lower() for w in caught),
                f"expected UserWarning about corrupt config, got: {[str(w.message) for w in caught]}",
            )


class TestExitCodes(unittest.TestCase):
    """CLI-контракт: 0=армирован, 2=не инициализирован (init_needed), 1=enforcement off."""

    def _exit(self, tmp: Path) -> int:
        # Гоняем КОПИЮ preflight.py, положенную в фикстуру: `_self_base()` считает базу от
        # расположения файла, значит у копии это `<tmp>/.gigacode` — project-раскладка, которую
        # и проверяют эти тесты. CLI-флага для self_base нет (в бою база ВСЕГДА фактическая),
        # поэтому пиним через фактическое расположение, а не аргументом.
        #
        # Phase 0 v2 consolidation: preflight._find_project_root делегирует в _config_loader,
        # поэтому копируем ОБА модуля в фикстуру — иначе subprocess падает на `ImportError`
        # ещё до входа в preflight(). В реальном деплое оба файла приходят через deploy.sh.
        hooks_dir = tmp / ".gigacode" / "hooks"
        for name in ("preflight.py", "_config_loader.py"):
            (hooks_dir / name).write_text((HOOKS / name).read_text(encoding="utf-8"), encoding="utf-8")
        deployed = hooks_dir / "preflight.py"
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


def _make_project(tmp: Path) -> None:
    """Проект БЕЗ развёрнутого .gigacode: только данные (ground/pipeline.json)."""
    (tmp / "ground").mkdir(parents=True, exist_ok=True)
    (tmp / "ground" / "pipeline.json").write_text(json.dumps({"quality": {}}), encoding="utf-8")


class TestLayoutResolution(unittest.TestCase):
    """Проверяется project-раскладка: <project>/.gigacode."""

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


class TestBattle046(unittest.TestCase):
    """Боевой прогон v0.4.6: ложный ENFORCEMENT OFF и мусорный корень из CLI."""

    def test_symlink_alias_of_own_dir_is_not_foreign(self):
        """A-F2: хук оператора записан через симлинк-алиас корня (`/tmp` ↔ `/private/tmp`) —
        префикс по строке не совпадал, рабочий хук объявлялся «чужим»."""
        import os
        with tempfile.TemporaryDirectory() as real, tempfile.TemporaryDirectory() as links:
            root = Path(real).resolve()
            (root / ".gigacode" / "hooks").mkdir(parents=True)
            (root / ".gigacode" / "hooks" / "op-hook.py").write_text("", encoding="utf-8")
            alias = Path(links) / "alias"
            os.symlink(root, alias)
            cmd = f"/usr/bin/python3 -X utf8 {alias}/.gigacode/hooks/op-hook.py"
            settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": cmd}]}]}}
            self.assertEqual(preflight._find_foreign_hook_paths(settings, str(root)), [])
            other = f"/usr/bin/python3 -X utf8 {links}/elsewhere/.gigacode/hooks/x.py"
            settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = other
            self.assertEqual(len(preflight._find_foreign_hook_paths(settings, str(root))), 1)

    def test_project_equals_form(self):
        """CLI-4: `--project=PATH` уходил в позиционный корень целиком."""
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(HOOKS / "preflight.py"), f"--project={d}"],
                               capture_output=True, text=True, timeout=60)
            base = json.loads(r.stdout)["layout"]["base"]
            self.assertEqual(Path(base).parent, Path(d).resolve(), base)


class TestHookCommandsRunnable(unittest.TestCase):
    """Задача 007: устаревший путь в settings.json кирпичит сессию целиком.

    `python3 <несуществующий>.py` возвращает rc=2, а в протоколе хуков это «блокировать»:
    каждый Bash/Write отклоняется с `can't open file`, и починка тоже идёт через Bash.
    `_find_foreign_hook_paths` этого не ловил — путь-то СВОЙ, просто мёртвый."""

    def test_missing_hook_file_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            block = {"PreToolUse": [{"matcher": "*", "hooks": [
                {"command": f"python3 {tmp}/.gigacode/hooks/renamed-away.py"}]}]}
            errors: list = []
            preflight._check_commands_runnable(block, "settings.json", tmp, errors)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("renamed-away.py", errors[0])
            self.assertIn("rc=2", errors[0])

    def test_existing_hook_file_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            hooks_dir = tmp / ".gigacode" / "hooks"
            hooks_dir.mkdir(parents=True)
            (hooks_dir / "gate-guard.py").write_text("# stub\n", encoding="utf-8")
            block = {"PreToolUse": [{"matcher": "*", "hooks": [
                {"command": f"python3 {hooks_dir}/gate-guard.py"}]}]}
            errors: list = []
            preflight._check_commands_runnable(block, "settings.json", hooks_dir, errors)
            self.assertEqual(errors, [], errors)

    def test_interpreter_that_cannot_import_bundle_is_error(self):
        """Битый интерпретатор = fail-closed deny на каждом вызове — это тоже локаут."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            hooks_dir = tmp / "hooks"
            hooks_dir.mkdir(parents=True)
            (hooks_dir / "gate-guard.py").write_text("# stub\n", encoding="utf-8")
            # cwd=hooks_dir пустой (нет risk_ladder.py) → импорт не пройдёт
            block = {"PreToolUse": [{"matcher": "*", "hooks": [
                {"command": f"{sys.executable} -X utf8 {hooks_dir}/gate-guard.py"}]}]}
            errors: list = []
            preflight._check_commands_runnable(block, "settings.json", hooks_dir, errors)
            self.assertTrue(any("не грузит бандл" in e for e in errors), errors)



class TestRuntimeSettingsDirs(unittest.TestCase):
    """Рантайм читает settings.json из СВОЕГО базового каталога, и он может быть не тем,
    куда положил файлы deploy.sh.

    Найдено e2e: `deploy.sh` пишет `<project>/.gigacode/settings.json`, а стоковый qwen-code
    читает `<project>/.qwen/settings.json` (в его бандле строки `.gigacode` нет вовсе). Все
    прежние проверки смотрели только в каталог деплоя — preflight отдавал `errors: []` при
    том, что рантайм не загружал ни одного хука."""

    def _mk_base(self, tmp: Path) -> Path:
        gh = tmp / ".gigacode" / "hooks"
        gh.mkdir(parents=True)
        block = _hooks_block(tmp, ESSENTIAL)
        (tmp / ".gigacode" / "settings.json").write_text(
            json.dumps({"hooks": block}), encoding="utf-8")
        return tmp / ".gigacode"

    def test_warns_when_other_runtime_dir_has_no_forge_hooks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            base = self._mk_base(tmp)
            (tmp / ".qwen").mkdir()
            (tmp / ".qwen" / "settings.json").write_text('{"$version": 4}', encoding="utf-8")
            warnings: list = []
            preflight._check_runtime_settings_dirs(tmp, base, warnings)
            self.assertEqual(len(warnings), 1, warnings)
            self.assertIn(".qwen", warnings[0])
            self.assertIn("ВЫКЛЮЧЕН", warnings[0])

    def test_silent_when_other_runtime_dir_is_armed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            base = self._mk_base(tmp)
            (tmp / ".qwen").mkdir()
            (tmp / ".qwen" / "settings.json").write_text(
                json.dumps({"hooks": _hooks_block(tmp, ESSENTIAL)}), encoding="utf-8")
            warnings: list = []
            preflight._check_runtime_settings_dirs(tmp, base, warnings)
            self.assertEqual(warnings, [], warnings)

    def test_silent_when_other_runtime_dir_absent(self):
        """На машине только форк GigaCode — постороннего каталога нет, ложной тревоги быть
        не должно."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            base = self._mk_base(tmp)
            warnings: list = []
            preflight._check_runtime_settings_dirs(tmp, base, warnings)
            self.assertEqual(warnings, [], warnings)


class TestAmbiguousActiveRun(unittest.TestCase):
    """Брошенный прогон должен быть ВИДЕН на старте, а не по промаху гейта.

    Инцидент: чужой брошенный прогон (пустой стаб, шаги pending) перехватывал резолв
    активной фичи. Узнать о нём было нечем — `archive.py status` его не показывал, отказы
    гейтов выглядели как поломка форжа, и пользователь снёс каталоги `rm -rf`."""

    def _run(self, root: Path, skill: str, feature: str, status: str) -> None:
        d = root / "ground" / "statements" / skill / feature
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(
            json.dumps({"version": 2, "skill": skill, "feature": feature,
                        "steps": [{"id": "01-grounding", "status": status}]}),
            encoding="utf-8")

    def test_warns_when_two_runs_are_live(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._run(tmp, "forgelite", "SQUATTER", "in_progress")
            self._run(tmp, "feature-pipeline", "MINE", "pending")
            warnings: list = []
            preflight._check_ambiguous_active_run(tmp, warnings)
            self.assertEqual(len(warnings), 1, warnings)
            self.assertIn("SQUATTER", warnings[0])
            self.assertIn("abandon", warnings[0], "предупреждение обязано назвать выход")

    def test_silent_on_single_live_run(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._run(tmp, "feature-pipeline", "MINE", "pending")
            warnings: list = []
            preflight._check_ambiguous_active_run(tmp, warnings)
            self.assertEqual(warnings, [], warnings)

    def test_silent_when_extra_run_is_finished(self):
        """Завершённый прогон неоднозначности не создаёт — его выбивает фильтр живости."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._run(tmp, "forgefix", "DONE", "completed")
            self._run(tmp, "feature-pipeline", "MINE", "pending")
            warnings: list = []
            preflight._check_ambiguous_active_run(tmp, warnings)
            self.assertEqual(warnings, [], warnings)

    def test_silent_without_runs(self):
        with tempfile.TemporaryDirectory() as td:
            warnings: list = []
            preflight._check_ambiguous_active_run(Path(td), warnings)
            self.assertEqual(warnings, [], warnings)


if __name__ == "__main__":
    unittest.main()
