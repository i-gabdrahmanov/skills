#!/usr/bin/env python3
"""Тесты record_approval.py — одиночный и batch-режимы.

KIDPPRB-9254 п.6: для прогона на 30+ модулях одиночный CLI плодил 60+ вызовов и 60+
записей в ground/approvals.jsonl. Batch-режим (--batch) пишет все согласия атомарно
одним flock-вызовом, идемпотентен по ключу и валидирует ВСЕ записи ДО записи.

Покрывает:
  • одиночный CLI: kind/evidence/approver (новые поля), approved-by алиас, валидация;
  • batch YAML/JSON: обёртки `approvals:` и баре-список;
  • атомарность: ошибка валидации одной записи → ни одной записи в журнале;
  • идемпотентность: повторный прогон не дублирует активные ключи, после revoke
    пересогласование работает;
  • совместимость: ключи санитайзятся через safe_component (как и в update.py);
  • поведение FE.approval() по записям из батча.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ra_mod = _load("record_approval", SCRIPTS / "record_approval.py")
FE = ra_mod.FE


class TestRecordApprovalSingle(unittest.TestCase):
    """Одиночный CLI: обратная совместимость + новые поля kind/evidence/approver."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _args(self, **overrides):
        defaults = {
            "project": str(self.project),
            "key": "gate-override-x",
            "approved_by": "user",
            "approver": None,
            "reason": "test reason",
            "kind": None,
            "evidence": None,
            "feature_ctx": None,
            "batch": None,
            "json": False,
        }
        defaults.update(overrides)
        return type("A", (), defaults)()

    def _log_path(self) -> Path:
        return self.project / "ground" / "approvals.jsonl"

    def _log_lines(self) -> list:
        if not self._log_path().exists():
            return []
        return [json.loads(l) for l in self._log_path().read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def test_basic_record(self):
        """--key + --reason + --approved-by (legacy) → запись в журнал."""
        rc = ra_mod.cmd_single(self._args())
        self.assertEqual(rc, 0)
        rec = FE.approval(self.project, "gate-override-x")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["approved_by"], "user")
        self.assertEqual(rec["reason"], "test reason")
        self.assertEqual(rec["produced_by"], "record_approval")
        # kind в записи — тип СОБЫТИЯ журнала (всегда "approval"). Категория живёт
        # в approval_kind (см. docstring _validate_approval_item).
        self.assertEqual(rec["kind"], "approval")
        self.assertEqual(rec["approval_kind"], "approval")  # default

    def test_approver_alias(self):
        """--approver алиасит --approved-by (новый CLI, старое поле в записи)."""
        rc = ra_mod.cmd_single(self._args(approver="qa-lead", approved_by=None))
        self.assertEqual(rc, 0)
        rec = FE.approval(self.project, "gate-override-x")
        self.assertEqual(rec["approved_by"], "qa-lead")

    def test_kind_field(self):
        """--kind попадает в запись как approval_kind (см. коллизию имён в реализации)."""
        rc = ra_mod.cmd_single(self._args(kind="human-approval"))
        self.assertEqual(rc, 0)
        rec = FE.approval(self.project, "gate-override-x")
        self.assertEqual(rec["approval_kind"], "human-approval")
        # kind СОБЫТИЯ журнала остаётся "approval" — не перезаписывается пользователем
        self.assertEqual(rec["kind"], "approval")

    def test_evidence_field(self):
        """--evidence пишется в запись."""
        rc = ra_mod.cmd_single(self._args(evidence="sha256:abc123"))
        self.assertEqual(rc, 0)
        rec = FE.approval(self.project, "gate-override-x")
        self.assertEqual(rec["evidence"], "sha256:abc123")

    def test_feature_ctx(self):
        """--feature-ctx (Jira-key) пишется в запись как `feature`."""
        rc = ra_mod.cmd_single(self._args(feature_ctx="KIDPPRB-9254"))
        self.assertEqual(rc, 0)
        rec = FE.approval(self.project, "gate-override-x")
        self.assertEqual(rec["feature"], "KIDPPRB-9254")

    def test_missing_reason_rejected(self):
        """--reason обязателен, иначе rc=2."""
        rc = ra_mod.cmd_single(self._args(reason=""))
        self.assertEqual(rc, 2)
        self.assertEqual(self._log_lines(), [])

    def test_empty_key_rejected(self):
        """Ключ, состоящий только из спецсимволов, после safe_key становится 'x' —
        НЕ пустой, но и не то, что ввёл оператор. Это best-effort: даже в таком
        случае запись пройдёт (под именем 'x'), а не отвергнется — иначе опечатка
        в safe_component заблокировала бы прогон, что хуже, чем странное имя."""
        # Передаём «грязный» ключ — он пройдёт санитайз, имя станет 'x'.
        rc = ra_mod.cmd_single(self._args(key="@@@"))
        self.assertEqual(rc, 0)
        # В журнале запись под санитайзнутым ключом 'x'.
        self.assertEqual(len(self._log_lines()), 1)
        rec = FE.approval(self.project, "x")
        self.assertIsNotNone(rec, "грязный ключ санитайзен до 'x' и записан под ним")

    def test_idempotent_single_mode(self):
        """Повторный вызов одиночного режима для активного ключа → rc=0, без дубля."""
        rc1 = ra_mod.cmd_single(self._args())
        self.assertEqual(rc1, 0)
        rc2 = ra_mod.cmd_single(self._args())
        self.assertEqual(rc2, 0)
        # В журнале — одна запись (не две)
        self.assertEqual(len(self._log_lines()), 1,
                         "идемпотентность одиночного режима нарушена")


class TestRecordApprovalBatch(unittest.TestCase):
    """Batch-режим: один файл → N approval'ов одной транзакцией."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _args(self, batch_path: Path, **overrides):
        defaults = {
            "project": str(self.project),
            "key": None,
            "approved_by": None,
            "approver": None,
            "reason": None,
            "kind": None,
            "evidence": None,
            "feature_ctx": None,
            "batch": str(batch_path),
            "json": False,
        }
        defaults.update(overrides)
        return type("A", (), defaults)()

    def _write_batch_yaml(self, body: str) -> Path:
        p = self.project / "batch.yaml"
        p.write_text(body, encoding="utf-8")
        return p

    def _write_batch_json(self, body) -> Path:
        p = self.project / "batch.json"
        p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return p

    def _log_path(self) -> Path:
        return self.project / "ground" / "approvals.jsonl"

    def _log_records(self) -> list:
        if not self._log_path().exists():
            return []
        return [json.loads(l) for l in self._log_path().read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def _approval_records(self) -> list:
        return [r for r in self._log_records() if r.get("kind") == "approval"]

    def test_batch_yaml_with_approvals_wrapper(self):
        """YAML с обёрткой `approvals:` создаёт все согласия одной транзакцией."""
        path = self._write_batch_yaml("""
approvals:
  - project: KIDPPRB-9254
    key: phase-04
    kind: approval
    reason: "phase 04 approved"
    evidence: "sha256:aaa"
    approver: "tech-lead"
  - project: KIDPPRB-9254
    key: phase-05
    kind: approval
    reason: "phase 05 approved"
    evidence: "sha256:bbb"
    approver: "release-manager"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        # Оба ключа видны через FE.approval
        self.assertIsNotNone(FE.approval(self.project, "phase-04"))
        self.assertIsNotNone(FE.approval(self.project, "phase-05"))
        recs = self._approval_records()
        self.assertEqual(len(recs), 2)
        by_key = {r["key"]: r for r in recs}
        self.assertEqual(by_key["phase-04"]["evidence"], "sha256:aaa")
        self.assertEqual(by_key["phase-04"]["approved_by"], "tech-lead")
        self.assertEqual(by_key["phase-04"]["feature"], "KIDPPRB-9254")
        self.assertEqual(by_key["phase-04"]["reason"], "phase 04 approved")
        self.assertEqual(by_key["phase-05"]["approved_by"], "release-manager")

    def test_batch_yaml_bare_list(self):
        """YAML без обёртки — баре-список."""
        path = self._write_batch_yaml("""
- key: phase-04
  kind: approval
  reason: "QA OK"
  evidence: "sha256:aaa"
  approver: "tech-lead"
- key: phase-05
  kind: approval
  reason: "release OK"
  evidence: "sha256:bbb"
  approver: "release-manager"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._approval_records()), 2)

    def test_batch_json_with_approvals_wrapper(self):
        """JSON формат с обёрткой `approvals:`."""
        path = self._write_batch_json({
            "approvals": [
                {"key": "phase-04", "kind": "approval", "reason": "phase 04 OK",
                 "evidence": "sha256:aaa", "approver": "tech-lead"},
                {"key": "phase-05", "kind": "approval", "reason": "phase 05 OK",
                 "evidence": "sha256:bbb", "approver": "release-manager"},
            ]
        })
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._approval_records()), 2)
        self.assertIsNotNone(FE.approval(self.project, "phase-04"))

    def test_batch_json_bare_list(self):
        """JSON без обёртки — баре-список."""
        path = self._write_batch_json([
            {"key": "phase-04", "reason": "QA OK", "approver": "tech-lead"},
        ])
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._approval_records()), 1)

    def test_batch_accepts_approved_by_alias(self):
        """В YAML/JSON `approved_by` — алиас `approver`."""
        path = self._write_batch_yaml("""
approvals:
  - key: phase-04
    reason: "OK"
    approved_by: "qa-lead"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        rec = FE.approval(self.project, "phase-04")
        self.assertEqual(rec["approved_by"], "qa-lead")

    def test_batch_atomic_on_missing_reason(self):
        """Пустой reason в одной записи → весь батч отвергнут."""
        path = self._write_batch_yaml("""
approvals:
  - key: phase-04
    reason: "OK"
    approver: "tech-lead"
  - key: phase-05
    reason: ""
    approver: "release-manager"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 1, "батч с пустым reason должен быть отвергнут")
        # Атомарность: первая (валидная) запись НЕ попала на диск
        self.assertEqual(self._approval_records(), [],
                         "атомарность нарушена — запись в журнале до валидации всего батча")
        self.assertIsNone(FE.approval(self.project, "phase-04"))

    def test_batch_atomic_on_missing_key(self):
        """Запись без key отвергает весь батч."""
        path = self._write_batch_yaml("""
approvals:
  - key: phase-04
    reason: "OK"
  - kind: approval
    reason: "no key"
    approver: "qa"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 1)
        self.assertEqual(self._approval_records(), [])

    def test_batch_atomic_on_missing_approver(self):
        """Запись без approver/approved_by отвергает весь батч."""
        path = self._write_batch_yaml("""
approvals:
  - key: phase-04
    reason: "OK"
    approver: "tech-lead"
  - key: phase-05
    reason: "OK"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 1)
        self.assertEqual(self._approval_records(), [])

    def test_batch_idempotent_on_repeat(self):
        """Повторный прогон того же батча НЕ дублирует записи."""
        path = self._write_batch_yaml("""
approvals:
  - key: phase-04
    reason: "OK"
    approver: "tech-lead"
  - key: phase-05
    reason: "OK"
    approver: "release-manager"
""")
        args = self._args(path)
        self.assertEqual(ra_mod.cmd_batch(args), 0)
        self.assertEqual(len(self._approval_records()), 2)
        # Повторный прогон — те же 2 записи
        self.assertEqual(ra_mod.cmd_batch(args), 0)
        self.assertEqual(len(self._approval_records()), 2,
                         "идемпотентность нарушена — записи дублируются")

    def test_batch_idempotent_partial(self):
        """Часть ключей уже активна, часть — нет: пишем только новые."""
        # Предварительно активируем phase-04
        rc0 = ra_mod.cmd_single(self._args(None, key="phase-04", reason="pre-existing",
                                           approver="tech-lead", batch=None))
        self.assertEqual(rc0, 0)
        self.assertEqual(len(self._approval_records()), 1)

        path = self._write_batch_yaml("""
approvals:
  - key: phase-04
    reason: "would overwrite"
    approver: "other"
  - key: phase-05
    reason: "new"
    approver: "release-manager"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        recs = self._approval_records()
        self.assertEqual(len(recs), 2)
        keys = {r["key"] for r in recs}
        self.assertEqual(keys, {"phase-04", "phase-05"})
        # phase-04 остался со старой reason (pre-existing), не перезаписан
        phase4 = next(r for r in recs if r["key"] == "phase-04")
        self.assertEqual(phase4["reason"], "pre-existing",
                         "идемпотентность: существующая запись перезаписана?")

    def test_batch_revoke_then_re_add(self):
        """После revoke ключ снова доступен — батч добавляет запись."""
        rc0 = ra_mod.cmd_single(self._args(None, key="phase-04", reason="OK",
                                           approver="tech-lead", batch=None))
        self.assertEqual(rc0, 0)
        # Отзываем через FE.revoke_approval (rollback использует тот же путь)
        FE.revoke_approval(self.project, "phase-04", reason="consumed")
        self.assertIsNone(FE.approval(self.project, "phase-04"))

        path = self._write_batch_yaml("""
approvals:
  - key: phase-04
    reason: "re-approved"
    approver: "tech-lead"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        rec = FE.approval(self.project, "phase-04")
        self.assertIsNotNone(rec, "после revoke батч не согласовал ключ заново")
        self.assertEqual(rec["reason"], "re-approved")

    def test_batch_writes_all_lines_in_single_journal_append(self):
        """Атомарность: все записи батча имеют ОДИН ts (один flock-вызов)."""
        path = self._write_batch_yaml("""
approvals:
  - key: k1
    reason: "r1"
    approver: "qa"
  - key: k2
    reason: "r2"
    approver: "qa"
  - key: k3
    reason: "r3"
    approver: "qa"
""")
        ra_mod.cmd_batch(self._args(path))
        recs = self._approval_records()
        self.assertEqual(len(recs), 3)
        ts_values = {r["ts"] for r in recs}
        self.assertEqual(len(ts_values), 1,
                         f"атомарность: ожидался один ts на батч, "
                         f"получено {len(ts_values)} ({ts_values})")

    def test_batch_empty_file_returns_error(self):
        """Пустой файл → rc=1."""
        path = self._write_batch_yaml("")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 1)
        self.assertEqual(self._approval_records(), [])

    def test_batch_invalid_wrapper_returns_error(self):
        """Файл с объектом без `approvals:` и не список → rc=1."""
        path = self._write_batch_yaml("foo: bar\n")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 1)
        self.assertEqual(self._approval_records(), [])

    def test_batch_nonexistent_file_returns_error(self):
        """Несуществующий файл → rc=1."""
        rc = ra_mod.cmd_batch(self._args(self.project / "nope.yaml"))
        self.assertEqual(rc, 1)

    def test_batch_sanitizes_gnarly_keys(self):
        """Ключи с пробелами/спецсимволами санитайзятся через safe_component.

        Контракт: писатель и читатель ОБЯЗАНЫ использовать одно имя. Здесь пишем под
        санитайзнутым ключом, и ищем через FE.approval тоже санитайзнутую форму —
        так же, как это делает update._approval_marker_valid в production."""
        path = self._write_batch_yaml("""
approvals:
  - key: "phase-04 with space"
    reason: "OK"
    approver: "tech-lead"
""")
        rc = ra_mod.cmd_batch(self._args(path))
        self.assertEqual(rc, 0)
        # Запись в журнале — с санитайзнутым ключом (пробелы → дефисы)
        recs = self._approval_records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["key"], "phase-04-with-space",
                         "ключ должен быть санитайзнут до записи в журнал")
        # И читатель находит по санитайзнутому ключу
        self.assertIsNotNone(FE.approval(self.project, "phase-04-with-space"))
        # А по исходному (с пробелом) — не находит, т.к. такого имени в журнале нет
        self.assertIsNone(FE.approval(self.project, "phase-04 with space"))

    def test_batch_records_have_correct_provenance(self):
        """Каждая запись батча имеет produced_by=record_approval и kind=approval."""
        path = self._write_batch_yaml("""
approvals:
  - key: k1
    reason: "r1"
    approver: "qa"
  - key: k2
    reason: "r2"
    approver: "qa"
""")
        ra_mod.cmd_batch(self._args(path))
        for rec in self._approval_records():
            self.assertEqual(rec["produced_by"], "record_approval")
            # kind в журнале — тип события (всегда "approval" для approval-записей)
            self.assertEqual(rec["kind"], "approval")

    def test_batch_empty_evidence_field_not_written(self):
        """Пустой evidence не пишется в запись (None / '' → отсутствует)."""
        path = self._write_batch_yaml("""
approvals:
  - key: k1
    reason: "r1"
    approver: "qa"
    evidence: ""
""")
        ra_mod.cmd_batch(self._args(path))
        rec = self._approval_records()[0]
        self.assertNotIn("evidence", rec)


class TestRecordApprovalCLI(unittest.TestCase):
    """CLI argparse: новые флаги работают, --batch через subprocess."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_cli_with_kind_evidence_approver(self):
        """--kind + --evidence + --approver (новые поля) → запись в журнале."""
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "record_approval.py"),
             "--project", str(self.project),
             "--key", "human-approval",
             "--reason", "delivery approved",
             "--approver", "release-manager",
             "--kind", "human-approval",
             "--evidence", "ticket DEL-42",
             "--feature-ctx", "KIDPPRB-9254"],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 0, f"stderr: {rc.stderr}\nstdout: {rc.stdout}")
        rec = FE.approval(self.project, "human-approval")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["approved_by"], "release-manager")
        # Категория approval_kind (kind — тип события журнала, всегда "approval")
        self.assertEqual(rec["approval_kind"], "human-approval")
        self.assertEqual(rec["evidence"], "ticket DEL-42")
        self.assertEqual(rec["feature"], "KIDPPRB-9254")

    def test_cli_batch_yaml(self):
        """CLI: --batch <yaml-файл> создаёт approval'ы из YAML."""
        path = self.project / "batch.yaml"
        path.write_text("""
approvals:
  - project: KIDPPRB-9254
    key: phase-04
    kind: approval
    reason: "phase 04 approved"
    evidence: "sha256:aaa"
    approver: "tech-lead"
  - project: KIDPPRB-9254
    key: phase-05
    kind: approval
    reason: "phase 05 approved"
    evidence: "sha256:bbb"
    approver: "release-manager"
""", encoding="utf-8")
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "record_approval.py"),
             "--project", str(self.project),
             "--batch", str(path)],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 0, f"stderr: {rc.stderr}\nstdout: {rc.stdout}")
        self.assertIsNotNone(FE.approval(self.project, "phase-04"))
        self.assertIsNotNone(FE.approval(self.project, "phase-05"))

    def test_cli_batch_missing_file(self):
        """CLI: --batch <несуществующий> → rc=1."""
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "record_approval.py"),
             "--project", str(self.project),
             "--batch", str(self.project / "nope.yaml")],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 1)

    def test_cli_batch_with_key_rejected(self):
        """CLI: --batch + --key → rc=2 (несовместимы)."""
        path = self.project / "batch.yaml"
        path.write_text("""
approvals:
  - key: phase-04
    reason: "OK"
    approver: "qa"
""", encoding="utf-8")
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "record_approval.py"),
             "--project", str(self.project),
             "--batch", str(path),
             "--key", "phase-04"],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 2)

    def test_cli_batch_atomic_error_message(self):
        """CLI: при ошибке валидации в stderr понятный список проблем."""
        path = self.project / "batch.yaml"
        path.write_text("""
approvals:
  - key: phase-04
    reason: "OK"
    approver: "qa"
  - key: phase-05
    reason: ""
    approver: "qa"
""", encoding="utf-8")
        rc = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "record_approval.py"),
             "--project", str(self.project),
             "--batch", str(path)],
            capture_output=True, text=True, cwd=str(SCRIPTS),
        )
        self.assertEqual(rc.returncode, 1)
        self.assertIn("атомарность", rc.stderr)
        self.assertIn("reason", rc.stderr)
        # И в журнале ничего: файл может не существовать, и это OK (атомарность).
        log = self.project / "ground" / "approvals.jsonl"
        if log.exists():
            self.assertEqual(log.read_text(encoding="utf-8").strip(), "",
                             "атомарность нарушена — в журнале есть записи после ошибки валидации")


if __name__ == "__main__":
    unittest.main(verbosity=2)
