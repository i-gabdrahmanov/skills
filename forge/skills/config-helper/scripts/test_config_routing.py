#!/usr/bin/env python3
"""Юнит-тесты роутинга записи config-helper v2 (manifest vs policy).

Покрывает:
  - inputs.* / decisions.* → ground/statements/<skill>/<feature>/manifest.json
  - quality.* / conventions.* / docs.* / jira.* / project.* / autonomy.* →
    ground/policy.json (пишется и на прогоне; прогон едет по снимку политики)
  - dual-read fallback в risk_ladder.config_get (legacy pipeline.json →
    sources.story / autonomy.criticality и т.п.)

Запуск: python3 test_config_routing.py (без pytest)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CONFIG_PY = Path(__file__).resolve().parent / "config.py"
HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"


# ── helpers ──────────────────────────────────────────────────────────────────


def _run(project: Path, *args: str) -> subprocess.CompletedProcess:
    """Запустить config.py с --project <tmpdir>. Возвращает CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(CONFIG_PY), "--project", str(project), *args],
        capture_output=True, text=True,
    )


# quality.coverage_threshold — переключатель enforcement, его правка с недавних пор R4
# (approval с цитатой пользователя; см. risk-policy.json:quality_downgrade и
# gate-guard.check_quality_downgrade). Тесты НИЖЕ проверяют РОУТИНГ (policy.json ↔ manifest,
# маскирование снимком), а не класс согласия, поэтому маркер выдаётся фикстурой — иначе они
# мерили бы новый гейт вместо роутинга. Сам гейт пинится отдельно: test_config.py:
# TestEnforcementSwitchesAreR4 и hooks/test_gate-guard.py: TQualityDowngradeIsR4.
_RECORD_APPROVAL = (Path(__file__).resolve().parents[1].parent
                    / "pipeline-state" / "scripts" / "record_approval.py")


def _approve_param(project: Path, param_id: str) -> None:
    subprocess.run(
        [sys.executable, str(_RECORD_APPROVAL), "--project", str(project),
         "--key", f"policy-downgrade-{param_id}", "--approved-by", "user",
         "--reason", "фикстура теста роутинга",
         "--evidence", "да, меняй порог для этого теста"],
        capture_output=True, text=True, check=False)


def _seed_policy(project: Path, body: dict | None = None) -> Path:
    """Создать ground/policy.json с заданным (или пустым quality) телом."""
    p = project / "ground" / "policy.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body if body is not None else {"quality": {}},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _seed_manifest(project: Path, skill: str, feature: str,
                   body: dict | None = None) -> Path:
    """Создать ground/statements/<skill>/<feature>/manifest.json."""
    p = project / "ground" / "statements" / skill / feature / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body if body is not None else {},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _run_risk_ladder_config_get(project: Path, dotpath: str) -> subprocess.CompletedProcess:
    """Вызвать risk_ladder.config_get(root, dotpath) через subprocess.

    Используется, когда нужен прямой dual-read fallback (напр. inputs.story без
    активного манифеста → legacy sources.story в pipeline.json). config.py.get
    в этой ситуации вернёт дефолт, а risk_ladder.config_get — реальное значение."""
    script = f"""
import sys
sys.path.insert(0, {str(HOOKS_DIR)!r})
from pathlib import Path
from risk_ladder import config_get
print(config_get(Path({str(project)!r}), {dotpath!r}))
"""
    return subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True)


# ── tests ────────────────────────────────────────────────────────────────────


class TestConfigRouting(unittest.TestCase):
    """Роутинг записи (v2): inputs.*/decisions.* → manifest, всё остальное → policy."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="forge-config-routing-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── 1. inputs.* пишется в manifest.json активной фичи, не в policy.json ───

    def test_set_inputs_writes_to_manifest_not_policy(self) -> None:
        # Манифест должен существовать (init.py создаёт — config.py set
        # для file="manifest" не создаёт файл с нуля).
        _seed_manifest(self.tmpdir, "forgefix", "fix-x", body={})

        r = _run(self.tmpdir, "set", "inputs.story", "PROJ-123",
                 "--skill", "forgefix", "--feature", "fix-x")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        manifest = self.tmpdir / "ground" / "statements" / "forgefix" / "fix-x" / "manifest.json"
        self.assertTrue(manifest.is_file(), f"manifest not written: {manifest}")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data.get("inputs", {}).get("story"), "PROJ-123",
                         f"manifest.inputs.story != 'PROJ-123': {data}")
        # policy.json НЕ должен появиться — это per-feature запись.
        self.assertFalse((self.tmpdir / "ground" / "policy.json").exists(),
                         "policy.json появился при записи inputs.* — это баг роутинга")

    # ── 2. decisions.* тоже пишется в manifest.json ──────────────────────────

    def test_set_decisions_writes_to_manifest(self) -> None:
        # autonomy.criticality в реестре: file=manifest, path=decisions.criticality,
        # enum: low|medium|high. Это per-feature запись → manifest.json.
        _seed_manifest(self.tmpdir, "forgefix", "fix-x", body={})

        r = _run(self.tmpdir, "set", "autonomy.criticality", "high",
                 "--skill", "forgefix", "--feature", "fix-x")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        manifest = self.tmpdir / "ground" / "statements" / "forgefix" / "fix-x" / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data.get("decisions", {}).get("criticality"), "high",
                         f"manifest.decisions.criticality != 'high': {data}")
        # policy.json не создаётся — decisions.* это per-feature.
        self.assertFalse((self.tmpdir / "ground" / "policy.json").exists(),
                         "policy.json появился при записи decisions.*")

    # ── 3. quality.* пишется в policy.json, манифестов не создаёт ────────────

    def test_set_quality_writes_to_policy(self) -> None:
        _seed_policy(self.tmpdir, body={"quality": {}})

        _approve_param(self.tmpdir, "quality.coverage_threshold")
        r = _run(self.tmpdir, "set", "quality.coverage_threshold", "0.85")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        policy = self.tmpdir / "ground" / "policy.json"
        self.assertTrue(policy.is_file(), "policy.json не записан")
        data = json.loads(policy.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data.get("quality", {}).get("coverage_threshold"),
                               0.85, places=6,
                               msg=f"quality.coverage_threshold != 0.85: {data}")
        # Ни одного manifest.json не должно появиться в ground/statements/.
        statements = self.tmpdir / "ground" / "statements"
        if statements.is_dir():
            stray = list(statements.rglob("manifest.json"))
            self.assertFalse(stray, f"manifest.json появился при quality.*: {stray}")

    # ── 4. file_key="pipeline" в реестре → policy.json (resolve_file роутинг) ─

    def test_set_pipeline_key_writes_to_policy(self) -> None:
        """Любой ключ с file="pipeline" в реестре пишется в policy.json.

        pipeline.mode НЕ существует как id (DEPRECATED alias от inputs.mode) —
        для проверки resolve_file("pipeline") → policy.json берём валидный id
        с file="pipeline". Берём ФАКТ о проекте (quality.build_command), а не переключатель
        enforcement (max_step_reopens/tdd/...): последние теперь R4 и потребовали бы approval,
        что к роутингу отношения не имеет."""
        _seed_policy(self.tmpdir, body={"quality": {}})

        r = _run(self.tmpdir, "set", "quality.build_command", "./gradlew build")

        self.assertEqual(r.returncode, 0,
                         f"set failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        policy = self.tmpdir / "ground" / "policy.json"
        data = json.loads(policy.read_text(encoding="utf-8"))
        self.assertEqual(data.get("quality", {}).get("build_command"), "./gradlew build",
                         f"quality.build_command не записан: {data}")
        # Явная проверка resolve_file-семантики: в JSON-ответе set указан
        # именно policy.json (НЕ legacy pipeline.json).
        applied = json.loads(r.stdout)
        self.assertEqual(applied.get("file", "").endswith("policy.json"), True,
                         f"set указал не policy.json: {applied}")

    # ── 5. policy.json пишется и под живым прогоном, но прогон идёт по снимку ──
    #
    # Раньше здесь пинился ЗАПРЕТ: `set` на policy-ключ отбивался, пока в
    # ground/statements/ лежал любой manifest.json. Запрет снят вместе с причиной —
    # прогон защищён снимком политики в своём манифесте, а не блокировкой файла.
    # Запрет к тому же был и слишком широким (завершённый вчерашний прогон блокировал
    # конфиг сегодняшнего), и слишком слабым (правка файла мимо config.py всё равно
    # меняла правила посреди прогона).

    def test_policy_write_under_live_run_warns_but_succeeds(self) -> None:
        _seed_policy(self.tmpdir, body={"quality": {"coverage_threshold": 0.8}})
        _seed_manifest(self.tmpdir, "forgefix", "fix-x",
                       body={"feature": "fix-x", "skill": "forgefix",
                             "steps": [{"id": "fix-diag", "status": "pending"}],
                             "policy_snapshot": {"quality": {"coverage_threshold": 0.8}}})

        _approve_param(self.tmpdir, "quality.coverage_threshold")
        r = _run(self.tmpdir, "set", "quality.coverage_threshold", "0.5")

        self.assertEqual(r.returncode, 0,
                         f"запись под живым прогоном должна проходить: rc={r.returncode} "
                         f"stdout={r.stdout!r} stderr={r.stderr!r}")
        data = json.loads((self.tmpdir / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["quality"]["coverage_threshold"], 0.5, places=6)
        self.assertIn("repin", (r.stderr or "").lower(),
                      f"нет предупреждения про снимок/repin: stderr={r.stderr!r}")

    def test_live_run_snapshot_masks_policy_write(self) -> None:
        """Записали в файл — но идущий прогон продолжает видеть своё значение."""
        _seed_policy(self.tmpdir, body={"quality": {"coverage_threshold": 0.8}})
        _seed_manifest(self.tmpdir, "forgefix", "fix-x",
                       body={"feature": "fix-x", "skill": "forgefix",
                             "steps": [{"id": "fix-diag", "status": "pending"}],
                             "policy_snapshot": {"quality": {"coverage_threshold": 0.8}}})
        _approve_param(self.tmpdir, "quality.coverage_threshold")
        _run(self.tmpdir, "set", "quality.coverage_threshold", "0.5")

        sys.path.insert(0, str(HOOKS_DIR))
        import importlib
        cl = importlib.import_module("_config_loader")
        eff = cl.load_project_config(self.tmpdir)["quality"]["coverage_threshold"]
        raw = cl.load_project_config(self.tmpdir, raw=True)["quality"]["coverage_threshold"]
        self.assertAlmostEqual(eff, 0.8, places=6,
                               msg="живой прогон увидел правку — снимок не применился")
        self.assertAlmostEqual(raw, 0.5, places=6, msg="raw обязан отдавать файл как есть")

    def test_completed_run_does_not_mask_policy(self) -> None:
        """Исходная жалоба: второй прогон в том же репозитории. Завершённый манифест
        прошлой фичи больше не блокирует конфиг и не подменяет его собой."""
        _seed_policy(self.tmpdir, body={"quality": {"coverage_threshold": 0.8}})
        _seed_manifest(self.tmpdir, "forgefix", "fix-old",
                       body={"feature": "fix-old", "skill": "forgefix",
                             "steps": [{"id": "fix-diag", "status": "completed"}],
                             "policy_snapshot": {"quality": {"coverage_threshold": 0.8}}})

        _approve_param(self.tmpdir, "quality.coverage_threshold")
        r = _run(self.tmpdir, "set", "quality.coverage_threshold", "0.5")
        self.assertEqual(r.returncode, 0,
                         f"завершённый прогон блокирует конфиг: stdout={r.stdout!r}")

        sys.path.insert(0, str(HOOKS_DIR))
        import importlib
        cl = importlib.import_module("_config_loader")
        eff = cl.load_project_config(self.tmpdir)["quality"]["coverage_threshold"]
        self.assertAlmostEqual(eff, 0.5, places=6,
                               msg="снимок завершённого прогона всё ещё маскирует policy.json")

    # ── 6. policy.json пишется, когда нет активной фичи ─────────────────────

    def test_policy_writable_when_no_active_feature(self) -> None:
        _seed_policy(self.tmpdir, body={"quality": {}})

        _approve_param(self.tmpdir, "quality.coverage_threshold")
        r = _run(self.tmpdir, "set", "quality.coverage_threshold", "0.5")

        self.assertEqual(r.returncode, 0,
                         f"set не прошёл без активной фичи: rc={r.returncode} "
                         f"stdout={r.stdout!r} stderr={r.stderr!r}")
        policy = self.tmpdir / "ground" / "policy.json"
        data = json.loads(policy.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data.get("quality", {}).get("coverage_threshold"),
                               0.5, places=6,
                               msg=f"quality.coverage_threshold != 0.5: {data}")

    # ── 7. dual-read fallback: inputs.story в manifest.json читается через
    #       config.py.get даже при наличии policy.json без inputs ─────────────

    def test_dual_read_fallback_for_inputs_story(self) -> None:
        # canonical reader call: `config.py get inputs.story`. Подкоманда `get`
        # не знает --skill/--feature (аргументы для set) → argparse их режет.
        # current_value() сам резолвит активный манифест через load_active_manifest.
        _seed_policy(self.tmpdir, body={"quality": {}})
        _seed_manifest(self.tmpdir, "forgefix", "fix-x",
                       body={"inputs": {"story": "PROJ-9"}})

        r = _run(self.tmpdir, "get", "inputs.story")

        self.assertEqual(r.returncode, 0,
                         f"get failed: rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        # cmd_get выводит JSON-объект {value, source, ...}, не плоскую строку.
        payload = json.loads(r.stdout)
        self.assertEqual(payload.get("value"), "PROJ-9",
                         f"value != 'PROJ-9': {payload}")

    # ── 8. legacy pipeline.json fallback через risk_ladder.config_get ────────

    def test_legacy_pipeline_json_fallback_for_inputs_story(self) -> None:
        """risk_ladder.config_get('inputs.story') без манифеста:
          1) manifest отсутствует → шаг 1 skip;
          2) policy.json без inputs → шаг 2 не находит;
          3) LEGACY_PATHS['inputs.story'] = 'sources.story' → читает
             legacy pipeline.json.

        config.py.get в этой ситуации вернёт дефолт (""), потому что его
        dual-read fallback внутри current_value() срабатывает только если
        manifest.json СУЩЕСТВУЕТ. Поэтому зовём risk_ladder.config_get напрямую.
        """
        ground = self.tmpdir / "ground"
        ground.mkdir(parents=True, exist_ok=True)
        # Legacy v1: ground/pipeline.json с sources.story
        (ground / "pipeline.json").write_text(
            json.dumps({"sources": {"story": "PROJ-legacy"}},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # v2 policy.json без inputs
        (ground / "policy.json").write_text(
            json.dumps({"quality": {}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Манифеста нет (иначе config_get пошёл бы по шагу 1).

        r = _run_risk_ladder_config_get(self.tmpdir, "inputs.story")

        self.assertEqual(r.returncode, 0,
                         f"risk_ladder subprocess упал: rc={r.returncode} "
                         f"stderr={r.stderr!r}")
        self.assertEqual(r.stdout.strip(), "PROJ-legacy",
                         f"dual-read fallback не сработал: "
                         f"stdout={r.stdout!r} stderr={r.stderr!r}")

    # ── 9. get unknown id → nonzero exit ─────────────────────────────────────

    def test_get_unknown_key_exits_nonzero(self) -> None:
        # NB: argparse подкоманды `get` не знает про --skill/--feature, поэтому
        # они могут быть отвергнуты с exit 2. Любой nonzero exit приемлем — это
        # семантика "не silent failure".
        r = _run(self.tmpdir, "get", "inputs.does_not_exist",
                 "--skill", "X", "--feature", "Y")

        self.assertNotEqual(r.returncode, 0,
                            f"ожидался nonzero exit, получили {r.returncode}; "
                            f"stdout={r.stdout!r} stderr={r.stderr!r}")


class TestActiveRunIsNotGuessed(unittest.TestCase):
    """Запись per-feature значения не угадывает фичу по mtime.

    Инцидент: брошенный прогон чужой ветки (пустой стаб, шаги pending) оказался свежее —
    и `config.py set inputs.story <новая фича>` молча записал вход НОВОЙ фичи в ЕГО
    манифест, rc=0, без единого предупреждения. Промах виден не сразу, а на гейте
    следующей фазы, поэтому здесь fail-closed: exit 3 с перечнем кандидатов.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        _seed_policy(self.tmpdir)

    def _live(self, skill, feature):
        return _seed_manifest(self.tmpdir, skill, feature,
                              body={"version": 2, "skill": skill, "feature": feature,
                                    "steps": [{"id": "01-grounding", "status": "pending"}]})

    def test_implicit_write_refuses_when_two_runs_are_live(self):
        self._live("forgelite", "SQUATTER")
        mine = self._live("feature-pipeline", "MINE")
        r = _run(self.tmpdir, "set", "inputs.story", "MINE")
        self.assertEqual(r.returncode, 3,
                         f"ожидался exit 3, rc={r.returncode} stdout={r.stdout!r}")
        self.assertIn("SQUATTER", r.stdout)
        self.assertIn("abandon", r.stdout, "отказ обязан назвать штатный выход")
        # Ни один манифест не тронут.
        for mp in (mine, self.tmpdir / "ground" / "statements" / "forgelite" / "SQUATTER"
                   / "manifest.json"):
            self.assertEqual(json.loads(mp.read_text(encoding="utf-8")).get("inputs"), None,
                             f"на отказе манифест не должен меняться: {mp}")

    def test_explicit_feature_writes_even_when_ambiguous(self):
        """--skill/--feature снимают неоднозначность: координаты названы, угадывать нечего."""
        self._live("forgelite", "SQUATTER")
        self._live("feature-pipeline", "MINE")
        r = _run(self.tmpdir, "set", "inputs.story", "MINE",
                 "--skill", "feature-pipeline", "--feature", "MINE")
        self.assertEqual(r.returncode, 0, f"rc={r.returncode} stdout={r.stdout!r}")
        mp = self.tmpdir / "ground" / "statements" / "feature-pipeline" / "MINE" / "manifest.json"
        self.assertEqual(json.loads(mp.read_text(encoding="utf-8"))["inputs"]["story"], "MINE")

    def test_single_live_run_writes_and_names_the_target(self):
        """Один прогон — пишем как раньше, но неявный резолв больше не молчит."""
        self._live("feature-pipeline", "MINE")
        r = _run(self.tmpdir, "set", "inputs.story", "MINE")
        self.assertEqual(r.returncode, 0, f"rc={r.returncode} stdout={r.stdout!r}")
        self.assertIn("NOTE:", r.stderr)
        self.assertIn("MINE", r.stderr)

    def test_dead_run_does_not_hijack_resolution(self):
        """Завершённый прогон свежее живого — писать всё равно в живой."""
        _seed_manifest(self.tmpdir, "forgefix", "DONE",
                       body={"version": 2, "skill": "forgefix", "feature": "DONE",
                             "steps": [{"id": "01-grounding", "status": "completed"}]})
        self._live("feature-pipeline", "MINE")
        r = _run(self.tmpdir, "set", "inputs.story", "MINE")
        self.assertEqual(r.returncode, 0, f"rc={r.returncode} stdout={r.stdout!r}")
        mine = self.tmpdir / "ground" / "statements" / "feature-pipeline" / "MINE" / "manifest.json"
        self.assertEqual(json.loads(mine.read_text(encoding="utf-8"))["inputs"]["story"], "MINE")
        done = self.tmpdir / "ground" / "statements" / "forgefix" / "DONE" / "manifest.json"
        self.assertIsNone(json.loads(done.read_text(encoding="utf-8")).get("inputs"),
                          "вход новой фичи уехал в завершённый прогон")


class TestPolicyWriteIsNotBlockedOnLiveRun(unittest.TestCase):
    """policy.json на идущем прогоне ПИШЕТСЯ (WARNING, rc=0), а не блокируется.

    Пин на инцидент с доками: запрет сняли ещё в a3820bb, но докстринги config.py и
    router/SKILL.md продолжали обещать «иммутабельна на прогоне — set вернёт exit 1».
    Модель прочитала обещание и отказалась выполнять задачу, НЕ ЗАПУСТИВ команду; выходом
    пользователю показалось удаление чужих прогонов руками. Тест держит контракт со стороны
    поведения, чтобы текст доков было чем проверить.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        _seed_policy(self.tmpdir, body={"jira": {"enabled": True}})
        _seed_manifest(self.tmpdir, "forgelite", "ABANDONED",
                       body={"version": 2, "skill": "forgelite", "feature": "ABANDONED",
                             "inputs": {}, "decisions": {},
                             "steps": [{"id": "01-grounding", "status": "in_progress"}]})

    def test_project_wide_write_succeeds_with_warning(self):
        r = _run(self.tmpdir, "set", "jira.enabled", "false")
        self.assertEqual(r.returncode, 0,
                         f"регресс: запись в policy.json заблокирована. "
                         f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("WARNING", r.stderr)
        self.assertIn("repin", r.stderr, "WARNING обязан назвать способ применить сейчас")
        policy = json.loads((self.tmpdir / "ground" / "policy.json").read_text(encoding="utf-8"))
        self.assertIs(policy["jira"]["enabled"], False)

    def test_no_exit_1_immutability_claim_survives_in_docs(self):
        """Доки не должны снова обещать блок, которого нет: обещание останавливает модель
        РАНЬШЕ запуска команды, и тестом такого не поймать — только текстом."""
        forge = Path(__file__).resolve().parents[3]
        stale = []
        for rel in ("skills/router/SKILL.md", "skills/config-helper/SKILL.md",
                    "skills/config-helper/scripts/config.py", "hooks/_project.py"):
            text = (forge / rel).read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if ("immutable" in low or "иммутабел" in low) and "не иммутабел" not in low:
                    stale.append(f"{rel}:{i}: {line.strip()}")
        self.assertEqual(stale, [], "доки снова обещают иммутабельность policy.json:\n"
                                    + "\n".join(stale))


if __name__ == "__main__":
    unittest.main(verbosity=2)
