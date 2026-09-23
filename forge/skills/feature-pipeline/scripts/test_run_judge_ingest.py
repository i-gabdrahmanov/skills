#!/usr/bin/env python3
"""C3: run_judge --from-output сохраняет вердикт pass-through судьи build.

Раньше build-вердикты только читались, но их никто не писал → шаги
04-build не закрывались. Теперь субагентский вердикт сохраняется через
--from-output, и --recheck его подтверждает.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RJ = REPO / "skills/feature-pipeline/scripts/run_judge.py"
sys.path.insert(0, str(REPO / "hooks"))
import forge_events as FE  # noqa: E402  — вердикты живут в журнале прогона, не в judges/*.json


def _run(args, cwd, stdin=None):
    return subprocess.run([sys.executable, str(RJ), *map(str, args)], cwd=str(cwd),
                          input=stdin, capture_output=True, text=True, timeout=60)


class Ingest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "docs/feature-pipeline/feat").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _verdict(self, judge):
        return FE.judge(self.proj, "feature-pipeline", "feat", judge)

    def test_ingest_then_recheck_passes(self):
        vf = self.proj / "verdict.json"
        vf.write_text(json.dumps({"passed": True, "blocking_issues": [], "summary": "ok"}), encoding="utf-8")
        r = _run(["build", "feat", "--from-output", str(vf), "--project-root", self.proj], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = self._verdict("build-judge")
        self.assertIsNotNone(saved, "вердикт не попал в журнал прогона")
        self.assertTrue(saved["passed"])
        # recheck подтверждает
        r2 = _run(["build", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_recheck_without_ingest_fails(self):
        r = _run(["build", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertNotEqual(r.returncode, 0, "build --recheck без вердикта должен падать")

    def test_failing_verdict_ingest_fails(self):
        r = _run(["build", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj, stdin=json.dumps({"passed": False, "blocking_issues": ["stub left"]}))
        self.assertEqual(r.returncode, 1)
        saved = self._verdict("build-judge")
        self.assertFalse(saved["passed"])
        # errors.json накоплен
        self.assertTrue((self.proj / "ground/statements/feature-pipeline/feat/judges/errors.json").exists())

    def test_malformed_input_errors(self):
        r = _run(["build", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj, stdin="{not json")
        self.assertEqual(r.returncode, 2)


GOOD_BRD = """# БТ: статус заказа в личном кабинете

## Контекст и проблема
Клиенты не видят текущий статус своего заказа и обращаются в поддержку. Операторы
перегружены однотипными вопросами «где мой заказ», среднее время ответа растёт,
удовлетворённость падает. Бизнес хочет разгрузить поддержку и дать клиенту прозрачность.

## Цели
Снизить долю обращений о статусе заказа, сократить время ответа поддержки и повысить
удовлетворённость клиентов за счёт самостоятельного отслеживания заказа.

## Требования и сценарии
Пользователь в личном кабинете видит актуальный статус каждого своего заказа. При смене
этапа обработки статус обновляется без участия оператора. История смен статусов доступна
клиенту в карточке заказа.

## Критерии приёмки
Статус отображается для всех типов заказов. Обновление видно клиенту не позднее чем через
минуту после смены этапа. Доля обращений о статусе снижается по данным поддержки.
"""


class BrdIngestFloor(unittest.TestCase):
    """Ингест LLM-вердикта brd применяет детерминированный пол (check_brd + check_brd_doc):
    штамп «PASS» от LLM-судьи на мусорном БТ больше не сохраняется как passed:true
    (раньше update.py закрывал 00-brd, ни разу не выполнив детерминированные проверки)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.fdir = self.proj / "docs/feature-pipeline/feat"
        self.fdir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _verdict(self):
        return FE.judge(self.proj, "feature-pipeline", "feat", "brd-judge")

    def _ingest_llm_pass(self):
        return _run(["brd", "feat", "--from-output", "-", "--project-root", self.proj],
                    self.proj,
                    stdin=json.dumps({"passed": True, "blocking_issues": [],
                                      "summary": "великолепный БТ"}))

    def test_llm_pass_on_trash_brd_fails(self):
        (self.fdir / "brd.md").write_text("Сделать хорошо и быстро.", encoding="utf-8")
        r = self._ingest_llm_pass()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self._verdict()["passed"])

    def test_llm_pass_on_missing_brd_fails(self):
        r = self._ingest_llm_pass()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self._verdict()["passed"])

    def test_llm_pass_on_good_brd_passes_and_recheck_structural(self):
        (self.fdir / "brd.md").write_text(GOOD_BRD, encoding="utf-8")
        r = self._ingest_llm_pass()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self._verdict()["passed"])
        # recheck пересчитывает тот же детерминированный слой
        r2 = _run(["brd", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)

    def test_recheck_is_structural_on_trash(self):
        (self.fdir / "brd.md").write_text("Короткая заглушка без секций.", encoding="utf-8")
        r = _run(["brd", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertNotEqual(r.returncode, 0, "recheck обязан валить структурно-мусорный БТ")
        self.assertFalse(self._verdict()["passed"])

    def test_llm_fail_stays_fail_on_good_brd(self):
        # пол только ужесточает: детерминированный PASS не спасает LLM-FAIL
        (self.fdir / "brd.md").write_text(GOOD_BRD, encoding="utf-8")
        r = _run(["brd", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj, stdin=json.dumps({"passed": False,
                                              "blocking_issues": ["написано как спека"]}))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self._verdict()["passed"])


class HybridIngestFloor(unittest.TestCase):
    """build — гибрид merges_saved: на ингесте LLM-вердикт сохраняется, затем
    check_build читает его и применяет детерминированный пол (stubs).
    Раньше пол применялся только на необязательном --recheck."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "docs/feature-pipeline/feat").mkdir(parents=True)
        self.src = self.proj / "src/main/java/App.java"
        self.src.parent.mkdir(parents=True)
        self.src.write_text("class App {}\n", encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=str(self.proj), capture_output=True, timeout=30)

    def tearDown(self):
        self._tmp.cleanup()

    def _verdict(self, judge):
        return FE.judge(self.proj, "feature-pipeline", "feat", judge)

    def _ingest(self, phase):
        return _run([phase, "feat", "--from-output", "-", "--project-root", self.proj],
                    self.proj, stdin=json.dumps({"passed": True, "blocking_issues": [],
                                                 "summary": "LLM: всё отлично"}))

    def test_build_llm_pass_with_stub_fails(self):
        self.src.write_text(
            "class App { void x() { throw new UnsupportedOperationException(); } }\n",
            encoding="utf-8")
        r = self._ingest("build")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self._verdict("build-judge")["passed"])

    def test_build_llm_pass_clean_passes(self):
        r = self._ingest("build")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self._verdict("build-judge")["passed"])

    def test_eval_llm_pass_without_plan_fails(self):
        # standalone-пол eval: LLM-PASS без eval-plan.json не сохраняется как passed:true
        r = self._ingest("eval")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self._verdict("eval-judge")["passed"])


class LlmFailSurvivesRecheck(unittest.TestCase):
    """Контртест к HybridIngestFloor: пол ужесточает вердикт, но НЕ стирает LLM-половину.

    Найдено аудитом и воспроизведено: `check_brd`/`check_eval`/`check_reuse` сохранённый
    LLM-вердикт не читали, а `--recheck` (брифы предписывают запускать его СРАЗУ после
    `--from-output`) пересчитывал слой с нуля и дописывал в журнал свежий PASS. `FE.judge`
    отдаёт последнюю запись → LLM-FAIL с blocking_issues исчезал, `_clear_errors` сносил
    errors.json, шаг закрывался. Для reuse это снимало судью целиком: семантика живёт
    только в LLM-слое.

    Существовавшие тесты пинили лишь обратное направление («LLM-PASS не пробивает пол»),
    поэтому дефект жил при 109/109 зелёных.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.fdir = self.proj / "docs/feature-pipeline/feat"
        self.fdir.mkdir(parents=True)
        # Артефакты, на которых ДЕТЕРМИНИРОВАННЫЙ слой заведомо зелёный — иначе тест
        # проходил бы по самому полу и ничего не доказывал.
        (self.fdir / "brd.md").write_text(GOOD_BRD, encoding="utf-8")
        (self.fdir / "task-plan.json").write_text(
            json.dumps({"tasks": [{"id": "T1", "layers": ["service"],
                                   "acceptance": ["given/when/then"]}]}), encoding="utf-8")
        (self.fdir / "eval-plan.json").write_text(
            json.dumps({"evals": [{"task_id": "T1", "type": t, "threshold": 0.8,
                                   "id": f"{t}-T1", "command": "x"}
                                  for t in ("compile", "coverage", "test_pass")]}),
            encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=str(self.proj), capture_output=True, timeout=30)

    def tearDown(self):
        self._tmp.cleanup()

    def _verdict(self, judge):
        return FE.judge(self.proj, "feature-pipeline", "feat", judge)

    def _ingest_fail(self, phase, issue):
        return _run([phase, "feat", "--from-output", "-", "--project-root", self.proj],
                    self.proj,
                    stdin=json.dumps({"passed": False, "checks": [],
                                      "blocking_issues": [issue], "warnings": [],
                                      "summary": "LLM: не годится"}))

    def _assert_sticky(self, phase, issue):
        judge = f"{phase}-judge"
        r1 = self._ingest_fail(phase, issue)
        self.assertEqual(r1.returncode, 1, r1.stdout + r1.stderr)
        self.assertFalse(self._verdict(judge)["passed"], "ингест LLM-FAIL не сохранился")

        r2 = _run([phase, "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertNotEqual(
            r2.returncode, 0,
            f"--recheck стёр LLM-FAIL судьи {judge}: детерминированный слой пересчитался "
            f"и перекрыл вердикт субагента.\n{r2.stdout}{r2.stderr}")
        v = self._verdict(judge)
        self.assertFalse(v["passed"], f"{judge}: passed=True после --recheck")
        self.assertIn(issue, v.get("blocking_issues", []),
                      f"{judge}: blocking_issue субагента потерян на --recheck")

    def test_brd_llm_fail_survives_recheck(self):
        self._assert_sticky("brd", "БТ написан как спецификация, а не как требования")

    def test_eval_llm_fail_survives_recheck(self):
        self._assert_sticky("eval", "пороги eval'ов занижены под текущий код")

    def test_reuse_llm_fail_survives_recheck(self):
        self._assert_sticky("reuse", "свой StringUtils вместо org.apache.commons.lang3")

    def test_new_llm_verdict_clears_the_fail(self):
        """Штатный путь снятия FAIL — новый вердикт субагента, а не повторный --recheck."""
        self._assert_sticky("reuse", "свой StringUtils вместо commons-lang3")
        r = _run(["reuse", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj,
                 stdin=json.dumps({"passed": True, "checks": [], "blocking_issues": [],
                                   "warnings": [], "summary": "LLM: исправлено"}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self._verdict("reuse-judge")["passed"],
                        "исправленный вердикт субагента не снял FAIL")


class IngestEscalates(unittest.TestCase):
    """Ингест обязан отдавать exit 3 на исчерпании лимита ре-итераций.

    Это была единственная ветка run_judge без `_maybe_escalate`: оркестратор, гоняющий
    судью через --from-output, крутил ре-итерации без тормоза, хотя SKILL.md §0.6 обещает
    «run_judge сам это форсит»."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "docs/feature-pipeline/feat").mkdir(parents=True)
        (self.proj / "ground").mkdir(parents=True, exist_ok=True)
        (self.proj / "ground/policy.json").write_text(
            json.dumps({"quality": {"max_judge_iterations": 2}}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_exit_3_after_limit(self):
        payload = json.dumps({"passed": False, "checks": [],
                              "blocking_issues": ["нечем крыть"], "warnings": [],
                              "summary": "LLM: FAIL"})
        codes = [
            _run(["eval", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj, stdin=payload).returncode
            for _ in range(3)
        ]
        self.assertEqual(codes[0], 1, f"первый FAIL должен быть exit 1, получено {codes}")
        self.assertIn(3, codes,
                      f"ингест ни разу не отдал exit 3 (ESCALATE) при лимите 2: {codes}")


class DeterministicFailIsNotSticky(unittest.TestCase):
    """Починка артефакта снимает детерминированный FAIL гибрида на `--recheck`.

    Свёртка «пол AND вердикт субагента» пишется в ТОТ ЖЕ поток записей под тем же именем
    судьи, что и сам вердикт субагента. Пока слияние читало «последнюю запись», оно
    вычитывало собственный итог и AND-ило его с полом второй раз: детерминированный FAIL
    превращался в вечный «LLM-FAIL» с чужими blocking_issues, и уже исправленный артефакт
    не получал PASS никаким числом `--recheck` (у build к тому же распухали checks, и на
    втором перепрогоне срабатывал ESCALATE на зелёном коде). Теперь слои различаются:
    layer="llm" — вход от субагента, layer="final" — итог. Обратную сторону (LLM-FAIL
    залипает до нового --from-output) пинит LlmFailSurvivesRecheck.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        self.fdir = self.proj / "docs/feature-pipeline/feat"
        self.fdir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_brd_recheck_passes_after_fix(self):
        self.fdir.joinpath("brd.md").write_text("Заглушка.", encoding="utf-8")
        r = _run(["brd", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj,
                 stdin=json.dumps({"passed": True, "blocking_issues": [], "summary": "ok"}))
        self.assertEqual(r.returncode, 1, "пол обязан завалить ингест на мусорном БТ")

        self.fdir.joinpath("brd.md").write_text(GOOD_BRD, encoding="utf-8")
        r2 = _run(["brd", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertEqual(r2.returncode, 0,
                         "БТ исправлен, а recheck всё ещё FAIL — вердикт залип:\n"
                         + r2.stdout + r2.stderr)
        self.assertTrue(FE.judge(self.proj, "feature-pipeline", "feat", "brd-judge")["passed"])

    def test_build_recheck_passes_after_stub_removed(self):
        src = self.proj / "src/main/java"
        src.mkdir(parents=True)
        for cmd in ("git init -q", "git config user.email t@t", "git config user.name t"):
            subprocess.run(cmd.split(), cwd=str(self.proj), check=True, capture_output=True)
        src.joinpath("A.java").write_text("class A { void f(){} }\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(self.proj), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(self.proj),
                       check=True, capture_output=True)

        src.joinpath("A.java").write_text(
            "class A { void f(){ throw new UnsupportedOperationException(); } }\n",
            encoding="utf-8")
        r = _run(["build", "feat", "--from-output", "-", "--project-root", self.proj],
                 self.proj, stdin=json.dumps({"passed": True, "blocking_issues": [],
                                              "summary": "llm ok"}))
        self.assertEqual(r.returncode, 1, "пол обязан поймать stub")
        before = FE.judge(self.proj, "feature-pipeline", "feat", "build-judge")

        src.joinpath("A.java").write_text("class A { void f(){ int x = 1; } }\n",
                                          encoding="utf-8")
        r2 = _run(["build", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertEqual(r2.returncode, 0,
                         "stub убран, а recheck всё ещё FAIL:\n" + r2.stdout + r2.stderr)
        after = FE.judge(self.proj, "feature-pipeline", "feat", "build-judge")
        self.assertTrue(after["passed"])
        self.assertLessEqual(len(after["checks"]), len(before["checks"]) + 1,
                             "checks накапливаются между перепрогонами (свёртка читает себя)")

class RedJudgeVerdictIsKept(unittest.TestCase):
    """Вердикт red-judge сохраняется, а не выбрасывается.

    Раньше бриф звал субагента §7.2 и следующей строкой делал `run_judge.py red --recheck`,
    который пересчитывает ТОЛЬКО детерминированный слой (прогон тестов). JSON субагента не
    читал никто: вызов модели оплачен, ответ получен, ответ выброшен — при том что реестр
    судей объявлял red-judge как pass-through, т.е. «вердикт считает субагент».
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "docs/feature-pipeline/feat").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _ingest(self, verdict: dict):
        return _run(["red", "feat", "--from-output", "-", "--project-root", self.proj],
                    self.proj, stdin=json.dumps(verdict))

    def test_llm_fail_blocks_and_is_stored(self):
        r = self._ingest({"passed": False,
                          "blocking_issues": ["тест не покрывает acceptance 'экспорт пуст'"],
                          "summary": "LLM: FAIL"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        llm = FE.judge(self.proj, "feature-pipeline", "feat", "red-judge", layer="llm")
        self.assertIsNotNone(llm, "вердикт субагента не сохранён")
        self.assertEqual(llm.get("layer"), "llm")
        final = FE.judge(self.proj, "feature-pipeline", "feat", "red-judge")
        self.assertFalse(final["passed"])
        self.assertTrue(any("acceptance" in b for b in final["blocking_issues"]),
                        f"претензия субагента потерялась: {final['blocking_issues']}")

    def test_llm_fail_survives_recheck(self):
        """`--recheck` не должен затирать LLM-слой детерминированным PASS."""
        self._ingest({"passed": False, "blocking_issues": ["пустышка"], "summary": "LLM: FAIL"})
        r = _run(["red", "feat", "--recheck", "--project-root", self.proj], self.proj)
        self.assertNotEqual(r.returncode, 0, "recheck затёр вердикт субагента")


if __name__ == "__main__":
    unittest.main(verbosity=2)
