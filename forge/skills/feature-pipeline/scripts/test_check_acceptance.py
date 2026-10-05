#!/usr/bin/env python3
"""test_check_acceptance.py — критерии приёмки ↔ тесты (маркеры @acceptance).

Гейт заменил собой «зелёную сьюту» как доказательство задачи: тут пинится, что критерий без
теста, тест не на тот критерий и непрошедший тест критерия — провал, а не «готово».
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_acceptance as CA  # noqa: E402

PLAN = {"tasks": [
    {"id": "T1", "title": "Сервис", "layers": ["service"],
     "artifacts": ["service/ReportService.java"],
     "acceptance": ["отчёт сформирован", "пустой период — 400"]},
    {"id": "T2", "title": "Миграция", "layers": ["migration"],
     "artifacts": ["db/changelog/x.xml"], "acceptance": ["таблица создана"]}]}

JAVA = """package com.x;

class ReportServiceTest {
    // @acceptance STOR-1:T1.1
    @Test
    void shouldBuildReport() {}

    /**
     * @acceptance STOR-1:T1.2, STOR-9:T1.1
     */
    @Test
    @DisplayName("пустой период")
    void shouldRejectEmptyPeriod() {}

    @Test
    void unrelated() {}
}
"""

KOTLIN = """package com.x

class ReportKtTest {
    // @acceptance STOR-1:T1.2
    @Test
    fun `rejects empty period`() {}
}
"""


def _junit(cases):
    body = "".join(
        f'<testcase classname="{c}" name="{n}">' + ("<failure/>" if st == "red" else "")
        + "</testcase>" for c, n, st in cases)
    return f'<?xml version="1.0"?><testsuite>{body}</testsuite>'


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        docs = self.root / "docs" / "feature-pipeline" / "STOR-1"
        docs.mkdir(parents=True)
        self.plan = docs / "task-plan.json"
        self.plan.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
        self.tdir = self.root / "app" / "src" / "test" / "java" / "com" / "x"
        self.tdir.mkdir(parents=True)
        main = self.root / "app" / "src" / "main" / "java" / "com" / "x" / "service"
        main.mkdir(parents=True)
        (main / "ReportService.java").write_text("class R {}", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _main(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CA.main([str(self.plan), "--root", str(self.root), *argv])
        return rc, buf.getvalue()

    def _runner(self, cases, exit_code=0) -> str:
        (self.root / "report.xml").write_text(_junit(cases), encoding="utf-8")
        (self.root / "runner.py").write_text(
            "import pathlib, shutil, sys\n"
            "d = pathlib.Path('build/test-results/test'); d.mkdir(parents=True, exist_ok=True)\n"
            "shutil.copy('report.xml', d / 'TEST-com.x.ReportServiceTest.xml')\n"
            f"sys.exit({exit_code})\n", encoding="utf-8")
        return f'"{sys.executable}" runner.py'


class TestScan(Base):
    def test_marker_binds_to_next_method_and_filters_feature(self):
        (self.tdir / "ReportServiceTest.java").write_text(JAVA, encoding="utf-8")
        found = CA.scan(self.root, "STOR-1")
        self.assertEqual([x["test"] for x in found["STOR-1:T1.1"]],
                         ["com.x.ReportServiceTest.shouldBuildReport"])
        self.assertEqual([x["test"] for x in found["STOR-1:T1.2"]],
                         ["com.x.ReportServiceTest.shouldRejectEmptyPeriod"])
        self.assertNotIn("STOR-9:T1.1", found, "маркеры чужой фичи не смешиваются")

    def test_kotlin_backtick_name(self):
        (self.tdir / "ReportKtTest.kt").write_text(KOTLIN, encoding="utf-8")
        found = CA.scan(self.root, "STOR-1")
        self.assertEqual(found["STOR-1:T1.2"][0]["test"], "com.x.ReportKtTest.rejects empty period")


class TestStatic(Base):
    def test_list_prints_ids(self):
        rc, out = self._main("--list")
        self.assertEqual(rc, 0)
        self.assertIn("STOR-1:T1.2\tпустой период — 400", out)
        self.assertIn("STOR-1:T2.1\tтаблица создана (задача освобождена", out)

    def test_criterion_without_test_fails(self):
        rc, out = self._main()
        self.assertEqual(rc, 2)
        self.assertIn("STOR-1:T1.1 «отчёт сформирован»: нет теста", out)
        self.assertNotIn("STOR-1:T2.1 «таблица создана»: нет теста", out,
                         "освобождённая задача теста не требует")

    def test_all_covered_passes(self):
        (self.tdir / "ReportServiceTest.java").write_text(JAVA, encoding="utf-8")
        rc, out = self._main()
        self.assertEqual(rc, 0, out)

    def test_stale_marker_fails(self):
        (self.tdir / "ReportServiceTest.java").write_text(
            JAVA.replace("STOR-1:T1.1", "STOR-1:T1.7"), encoding="utf-8")
        rc, out = self._main("--task", "T1")
        self.assertEqual(rc, 2)
        self.assertIn("STOR-1:T1.7", out)
        self.assertIn("несуществующий критерий", out)


class TestGreen(Base):
    def setUp(self):
        super().setUp()
        (self.tdir / "ReportServiceTest.java").write_text(JAVA, encoding="utf-8")

    def test_all_marked_tests_pass(self):
        cmd = self._runner([("com.x.ReportServiceTest", "shouldBuildReport()", "green"),
                            ("com.x.ReportServiceTest", "shouldRejectEmptyPeriod()", "green")])
        rc, out = self._main("--task", "T1", "--expect", "green", "--test-cmd", cmd)
        self.assertEqual(rc, 0, out)

    def test_failed_criterion_test_fails_gate(self):
        """Сьюта «в целом» могла быть зелёной раньше — здесь падение теста критерия = FAIL."""
        cmd = self._runner([("com.x.ReportServiceTest", "shouldBuildReport()", "green"),
                            ("com.x.ReportServiceTest", "shouldRejectEmptyPeriod()", "red")], 1)
        rc, out = self._main("--task", "T1", "--expect", "green", "--test-cmd", cmd)
        self.assertEqual(rc, 2)
        self.assertIn("STOR-1:T1.2 «пустой период — 400»: тест не прошёл", out)

    def test_marked_test_not_run_fails_gate(self):
        cmd = self._runner([("com.x.ReportServiceTest", "shouldBuildReport()", "green")])
        rc, out = self._main("--task", "T1", "--expect", "green", "--test-cmd", cmd)
        self.assertEqual(rc, 2)
        self.assertIn("shouldRejectEmptyPeriod (not-run)", out)

    def test_missing_artifact_fails_gate(self):
        (self.root / "app/src/main/java/com/x/service/ReportService.java").unlink()
        cmd = self._runner([("com.x.ReportServiceTest", "shouldBuildReport()", "green"),
                            ("com.x.ReportServiceTest", "shouldRejectEmptyPeriod()", "green")])
        rc, out = self._main("--task", "T1", "--expect", "green", "--test-cmd", cmd)
        self.assertEqual(rc, 2)
        self.assertIn("нет артефакта задачи в коде — service/ReportService.java", out)



class TestManual(Base):
    """Не всё проверяется тестом: ручной критерий тест не требует, но закрывается только
    согласием человека из журнала approvals (record_approval), а не словом модели."""

    MANUAL = {"text": "в аудит-логе оператор и период", "verify": "manual",
              "reason": "формат лога проверяет сопровождение на стенде"}

    def setUp(self):
        super().setUp()
        plan = json.loads(json.dumps(PLAN))
        plan["tasks"][0]["acceptance"].append(self.MANUAL)
        self.plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        (self.tdir / "ReportServiceTest.java").write_text(JAVA, encoding="utf-8")
        self.cmd = self._runner([("com.x.ReportServiceTest", "shouldBuildReport()", "green"),
                                 ("com.x.ReportServiceTest", "shouldRejectEmptyPeriod()", "green")])

    def _approve(self):
        import subprocess
        rp = HERE.parents[1] / "pipeline-state" / "scripts" / "record_approval.py"
        r = subprocess.run([sys.executable, str(rp), "--project", str(self.root),
                            "--key", CA.approval_key("STOR-1:T1.3"), "--kind", "acceptance",
                            "--approver", "user", "--evidence", "проверено на стенде",
                            "--reason", "лог в формате"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_list_marks_manual(self):
        rc, out = self._main("--list", "--task", "T1")
        self.assertIn("STOR-1:T1.3\tв аудит-логе оператор и период (ручная проверка", out)

    def test_static_does_not_require_test(self):
        rc, out = self._main("--task", "T1")
        self.assertEqual(rc, 0, out)

    def test_green_without_approval_fails(self):
        rc, out = self._main("--task", "T1", "--expect", "green", "--test-cmd", self.cmd)
        self.assertEqual(rc, 2)
        self.assertIn("ручная проверка не подтверждена человеком", out)
        self.assertIn("--key acceptance-STOR-1-T1.3", out)

    def test_green_with_approval_passes(self):
        self._approve()
        rc, out = self._main("--task", "T1", "--expect", "green", "--test-cmd", self.cmd)
        self.assertEqual(rc, 0, out)

    def test_manual_without_reason_fails(self):
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["tasks"][0]["acceptance"][-1]["reason"] = ""
        self.plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        rc, out = self._main("--task", "T1")
        self.assertEqual(rc, 2)
        self.assertIn("без reason", out)

if __name__ == "__main__":
    unittest.main(verbosity=2)
