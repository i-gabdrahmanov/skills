#!/usr/bin/env python3
"""test_forge_events.py — журнал evidence: провенанс, порядок, переоткрытие, совместимость.

Это фундамент под всеми гейтами закрытия шага: если свёртка соврёт, шаг закроется без
доказательств (или не закроется при наличии). Поэтому пинятся именно инварианты, а не
формат строк.

Запуск: python3 -m unittest test_forge_events
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))

import forge_events as FE  # noqa: E402
from _project import (approvals_dir, gate_result_path, judge_path,  # noqa: E402
                      origin_path, override_path)

S, F = "feature-pipeline", "KID-1"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.p = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _legacy(self, path: Path, body: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


class TestProvenance(Base):
    """Провенанс — единственное, что отличает evidence от строки, дописанной руками."""

    def test_payload_cannot_forge_produced_by(self):
        FE.append_event(self.p, S, F, "gate", step_id="05-tests", passed=True,
                        produced_by="i-am-record-gate")
        self.assertEqual(FE.gate(self.p, S, F, "05-tests")["produced_by"], "record_gate")

    def test_payload_cannot_forge_kind_or_ts(self):
        FE.append_event(self.p, S, F, "origin", step_id="00-brd",
                        kind="gate", ts="1970-01-01T00:00:00Z")
        rec = FE.origin(self.p, S, F, "00-brd")
        self.assertEqual(rec["kind"], "origin")
        self.assertNotEqual(rec["ts"], "1970-01-01T00:00:00Z")
        self.assertIsNone(FE.gate(self.p, S, F, "00-brd"), "origin просочился как gate")

    def test_handwritten_line_without_provenance_ignored(self):
        log = FE.events_path(self.p, S, F)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"kind": "gate", "step_id": "x", "passed": True}) + "\n",
                       encoding="utf-8")
        self.assertIsNone(FE.gate(self.p, S, F, "x"))

    def test_wrong_producer_ignored(self):
        log = FE.events_path(self.p, S, F)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"kind": "judge", "judge": "design-judge", "passed": True,
                                   "produced_by": "state-recorder"}) + "\n", encoding="utf-8")
        self.assertIsNone(FE.judge(self.p, S, F, "design-judge"))

    def test_unknown_kind_rejected_at_write(self):
        with self.assertRaises(ValueError):
            FE.append_event(self.p, S, F, "totally-new-kind", step_id="x")


class TestOrdering(Base):
    def test_last_record_wins(self):
        """Ре-прогон судьи перекрывает прежний вердикт — как раньше перезапись файла."""
        FE.append_event(self.p, S, F, "judge", judge="red-judge", passed=False, verdict="FAIL")
        FE.append_event(self.p, S, F, "judge", judge="red-judge", passed=True, verdict="PASS")
        self.assertEqual(FE.judge(self.p, S, F, "red-judge")["verdict"], "PASS")

    def test_records_are_isolated_by_key(self):
        FE.append_event(self.p, S, F, "gate", step_id="a", passed=True)
        FE.append_event(self.p, S, F, "gate", step_id="b", passed=False)
        self.assertTrue(FE.gate(self.p, S, F, "a")["passed"])
        self.assertFalse(FE.gate(self.p, S, F, "b")["passed"])

    def test_feature_scoped(self):
        FE.append_event(self.p, S, F, "origin", step_id="00-brd")
        self.assertIsNone(FE.origin(self.p, S, "OTHER-9", "00-brd"))


class TestReopen(Base):
    """Переоткрытие обязано обнулять evidence — иначе откаченный шаг закроется по старому."""

    def test_reopen_invalidates_origin_and_gate_of_that_step(self):
        FE.append_event(self.p, S, F, "origin", step_id="04-build-T1")
        FE.append_event(self.p, S, F, "gate", step_id="04-build-T1", passed=True)
        FE.append_event(self.p, S, F, "reopen", step_id="04-build-T1")
        self.assertIsNone(FE.origin(self.p, S, F, "04-build-T1"))
        self.assertIsNone(FE.gate(self.p, S, F, "04-build-T1"))

    def test_reopen_does_not_touch_other_steps(self):
        FE.append_event(self.p, S, F, "gate", step_id="05-tests", passed=True)
        FE.append_event(self.p, S, F, "reopen", step_id="04-build-T1")
        self.assertIsNotNone(FE.gate(self.p, S, F, "05-tests"))

    def test_evidence_after_reopen_counts_again(self):
        FE.append_event(self.p, S, F, "gate", step_id="s", passed=True)
        FE.append_event(self.p, S, F, "reopen", step_id="s")
        FE.append_event(self.p, S, F, "gate", step_id="s", passed=True)
        self.assertIsNotNone(FE.gate(self.p, S, F, "s"), "повторный прогон гейта не засчитан")

    def test_reopen_invalidates_listed_judges(self):
        """Судья привязан к фазе, а не к шагу — reopen несёт список явно."""
        FE.append_event(self.p, S, F, "judge", judge="design-judge", passed=True, verdict="PASS")
        FE.append_event(self.p, S, F, "reopen", step_id="02-design", judges=["design-judge"])
        self.assertIsNone(FE.judge(self.p, S, F, "design-judge"))

    def test_reopen_spares_unlisted_judges(self):
        FE.append_event(self.p, S, F, "judge", judge="spec-judge", passed=True, verdict="PASS")
        FE.append_event(self.p, S, F, "reopen", step_id="02-design", judges=["design-judge"])
        self.assertIsNotNone(FE.judge(self.p, S, F, "spec-judge"))

    def test_reopen_invalidates_listed_overrides(self):
        FE.append_event(self.p, S, F, "override", target="gate-result-s", reason="r")
        FE.append_event(self.p, S, F, "reopen", step_id="s", overrides=["gate-result-s"])
        self.assertIsNone(FE.override(self.p, S, F, "gate-result-s"))

    def test_reopen_invalidates_legacy_marker_too(self):
        """Прогон до миграции: файл-маркер тоже обязан перестать считаться после отката."""
        self._legacy(origin_path(self.p, S, F, "02-sdd"), {"step_id": "02-sdd"})
        self.assertIsNotNone(FE.origin(self.p, S, F, "02-sdd"))
        FE.append_event(self.p, S, F, "reopen", step_id="02-sdd")
        self.assertIsNone(FE.origin(self.p, S, F, "02-sdd"))


class TestOverrides(Base):
    def test_revoked_override_not_active_but_kept(self):
        FE.append_event(self.p, S, F, "override", target="red-judge", reason="нет БД в CI")
        FE.append_event(self.p, S, F, "override", target="red-judge", revoked=True)
        self.assertIsNone(FE.override(self.p, S, F, "red-judge"))
        self.assertEqual(len(FE.read_events(self.p, S, F)), 2, "история отзыва потеряна")

    def test_listing_excludes_revoked(self):
        FE.append_event(self.p, S, F, "override", target="a", reason="r")
        FE.append_event(self.p, S, F, "override", target="b", reason="r")
        FE.append_event(self.p, S, F, "override", target="a", revoked=True)
        self.assertEqual([o["target"] for o in FE.overrides(self.p, S, F)], ["b"])

    def test_listing_includes_legacy_files(self):
        self._legacy(override_path(self.p, S, F, "old-judge"), {"judge": "old-judge", "reason": "r"})
        self.assertIn("old-judge", [o["target"] for o in FE.overrides(self.p, S, F)])


class TestApprovals(Base):
    def test_key_must_match(self):
        FE.append_approval(self.p, "brd-approved-KID-1", approved_by="user")
        self.assertIsNotNone(FE.approval(self.p, "brd-approved-KID-1"))
        self.assertIsNone(FE.approval(self.p, "sdd-approved-KID-1"))

    def test_revoked_approval_not_reusable(self):
        """Одно согласие = один откат: потраченное не засчитывается снова."""
        FE.append_approval(self.p, "rollback-KID-1-02-sdd", approved_by="user")
        FE.revoke_approval(self.p, "rollback-KID-1-02-sdd", reason="потрачено")
        self.assertIsNone(FE.approval(self.p, "rollback-KID-1-02-sdd"))

    def test_new_approval_after_revoke_counts(self):
        FE.append_approval(self.p, "k", approved_by="user")
        FE.revoke_approval(self.p, "k")
        FE.append_approval(self.p, "k", approved_by="user")
        self.assertIsNotNone(FE.approval(self.p, "k"), "новое согласие после отзыва не видно")

    def test_revoke_covers_legacy_file(self):
        """Отзыв гасит и файл старой раскладки, НЕ удаляя его (откат архивирует, не стирает)."""
        legacy = approvals_dir(self.p) / "k.json"
        self._legacy(legacy, {"produced_by": "record_approval", "key": "k"})
        self.assertIsNotNone(FE.approval(self.p, "k"))
        FE.revoke_approval(self.p, "k")
        self.assertIsNone(FE.approval(self.p, "k"))
        self.assertTrue(legacy.exists(), "файл-маркер удалён — история отката потеряна")

    def test_legacy_without_provenance_ignored(self):
        self._legacy(approvals_dir(self.p) / "k.json", {"key": "k", "approved_by": "user"})
        self.assertIsNone(FE.approval(self.p, "k"))

    def test_legacy_renamed_marker_ignored(self):
        self._legacy(approvals_dir(self.p) / "k.json",
                     {"produced_by": "record_approval", "key": "другой-ключ"})
        self.assertIsNone(FE.approval(self.p, "k"))


class TestLegacyFallback(Base):
    """Прогон, начатый до миграции, должен дочитываться со старой раскладки."""

    def test_gate_from_legacy_file(self):
        self._legacy(gate_result_path(self.p, S, F, "05-tests"),
                     {"produced_by": "record_gate", "step_id": "05-tests", "passed": True})
        self.assertTrue(FE.gate(self.p, S, F, "05-tests")["passed"])

    def test_legacy_gate_without_provenance_ignored(self):
        self._legacy(gate_result_path(self.p, S, F, "05-tests"), {"passed": True})
        self.assertIsNone(FE.gate(self.p, S, F, "05-tests"))

    def test_judge_from_legacy_file(self):
        self._legacy(judge_path(self.p, S, F, "design-judge"),
                     {"produced_by": "run_judge", "passed": True, "verdict": "PASS"})
        self.assertTrue(FE.judge(self.p, S, F, "design-judge")["passed"])

    def test_log_record_wins_over_legacy_file(self):
        self._legacy(judge_path(self.p, S, F, "design-judge"),
                     {"produced_by": "run_judge", "passed": False, "verdict": "FAIL"})
        FE.append_event(self.p, S, F, "judge", judge="design-judge", passed=True, verdict="PASS")
        self.assertTrue(FE.judge(self.p, S, F, "design-judge")["passed"])


class TestRobustness(Base):
    def test_corrupt_line_skipped_not_fatal(self):
        FE.append_event(self.p, S, F, "gate", step_id="a", passed=True)
        with open(FE.events_path(self.p, S, F), "a", encoding="utf-8") as f:
            f.write("{это не json\n")
        FE.append_event(self.p, S, F, "gate", step_id="b", passed=True)
        self.assertIsNotNone(FE.gate(self.p, S, F, "a"))
        self.assertIsNotNone(FE.gate(self.p, S, F, "b"))

    def test_missing_log_is_empty_not_error(self):
        self.assertEqual(FE.read_events(self.p, S, F), [])
        self.assertIsNone(FE.origin(self.p, S, F, "any"))

    def test_none_values_are_dropped(self):
        """Пустые поля не засоряют строку (agent_id часто отсутствует)."""
        FE.append_event(self.p, S, F, "origin", step_id="x", agent_id=None)
        self.assertNotIn("agent_id", FE.origin(self.p, S, F, "x"))

    def test_concurrent_appends_do_not_interleave(self):
        """Несколько писателей в один лог — строки целые (flock/msvcrt)."""
        import threading
        n = 40

        def w(i):
            FE.append_event(self.p, S, F, "gate", step_id=f"s{i}", passed=True,
                            note="x" * 300)

        threads = [threading.Thread(target=w, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = FE.events_path(self.p, S, F).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), n)
        for line in lines:  # каждая строка — валидный JSON, не склейка двух записей
            json.loads(line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
