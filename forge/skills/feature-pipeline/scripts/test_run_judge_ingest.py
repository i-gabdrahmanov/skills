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

    def _verdict_path(self, judge):
        return self.proj / "ground/statements/feature-pipeline/feat/judges" / f"{judge}.json"

    def test_ingest_then_recheck_passes(self):
        vf = self.proj / "verdict.json"
        vf.write_text(json.dumps({"passed": True, "blocking_issues": [], "summary": "ok"}), encoding="utf-8")
        r = _run(["build", "feat", "--from-output", str(vf), "--project-root", self.proj], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._verdict_path("build-judge").exists())
        saved = json.loads(self._verdict_path("build-judge").read_text(encoding="utf-8"))
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
        saved = json.loads(self._verdict_path("build-judge").read_text(encoding="utf-8"))
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
        p = self.proj / "ground/statements/feature-pipeline/feat/judges/brd-judge.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

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
        p = self.proj / "ground/statements/feature-pipeline/feat/judges" / f"{judge}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
