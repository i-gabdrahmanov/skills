#!/usr/bin/env python3
"""Снимок политики на прогон: init.py пинит, читатели видят снимок, repin переснимает.

ЗАЧЕМ ЭТО ЕСТЬ. Прогон обязан целиком идти по той политике, под которой стартовал, иначе
достижимо состояние «шаги 1-5 закрылись под coverage 80%, шаги 6-10 — под 50%», и постфактум
уже не понять, по каким правилам собран артефакт. Раньше это обеспечивалось запретом на
запись в policy.json, пока в ground/statements/ лежал ЛЮБОЙ manifest.json. Запрет был
одновременно слишком широким (завершённый вчерашний прогон блокировал конфиг сегодняшнего:
второй прогон в репозитории не мог записать project-wide настройку вообще, а текст отказа
советовал нерабочее «заверши прогон» — статусы шагов не проверялись) и слишком слабым
(правка файла мимо config.py всё равно меняла правила посреди прогона).

Здесь пинится замена: снимок в манифесте + оверлей в единой точке чтения конфига.
Семантику записи со стороны config.py пинит test_config_routing.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOOKS = REPO / "hooks"
INIT = REPO / "skills/pipeline-state/scripts/init.py"
CONFIG = REPO / "skills/config-helper/scripts/config.py"
PREFLIGHT = REPO / "hooks/preflight.py"
RECORD_APPROVAL = REPO / "skills/pipeline-state/scripts/record_approval.py"
sys.path.insert(0, str(HOOKS))
import _config_loader as CL  # noqa: E402


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, timeout=120)


class PolicySnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "ground").mkdir(parents=True)
        self._write_policy({"quality": {"coverage_threshold": 0.8, "tdd": True}})

    def tearDown(self):
        self._tmp.cleanup()

    def _write_policy(self, body: dict) -> None:
        (self.proj / "ground" / "policy.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    def _manifest_path(self, skill="forgefix", feature="BUG-1") -> Path:
        return self.proj / "ground/statements" / skill / feature / "manifest.json"

    def _init_run(self, skill="forgefix", feature="BUG-1"):
        r = _run(INIT, "--project", self.proj, "--skill", skill, "--feature", feature,
                 "--steps", json.dumps([{"id": "fix-diag", "title": "diag"}]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(self._manifest_path(skill, feature).read_text(encoding="utf-8"))

    def _threshold(self, *, raw=False):
        return CL.load_project_config(self.proj, raw=raw)["quality"]["coverage_threshold"]

    # ── init.py пинит политику ────────────────────────────────────────────────
    def test_init_records_snapshot_and_digest(self):
        man = self._init_run()
        self.assertEqual(man["policy_snapshot"]["quality"]["coverage_threshold"], 0.8)
        self.assertEqual(man["policy_digest"], CL.policy_digest(man["policy_snapshot"]))

    def test_digest_ignores_key_order(self):
        """Перезапись policy.json с другим порядком ключей — не смена правил."""
        a = CL.policy_digest({"quality": {"tdd": True}, "jira": {"enabled": False}})
        b = CL.policy_digest({"jira": {"enabled": False}, "quality": {"tdd": True}})
        self.assertEqual(a, b)

    # ── оверлей ───────────────────────────────────────────────────────────────
    def test_live_run_reads_its_own_snapshot(self):
        self._init_run()
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        self.assertEqual(self._threshold(), 0.8, "живой прогон увидел правку policy.json")
        self.assertEqual(self._threshold(raw=True), 0.5, "raw обязан отдавать файл")

    def test_completed_run_stops_masking(self):
        man = self._init_run()
        for s in man["steps"]:
            s["status"] = "completed"
        self._manifest_path().write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        self.assertEqual(self._threshold(), 0.5,
                         "снимок завершённого прогона всё ещё маскирует policy.json")

    def test_manifest_without_snapshot_is_noop(self):
        """Прогоны, начатые до этого изменения, читают policy.json как раньше."""
        man = self._init_run()
        man.pop("policy_snapshot")
        man.pop("policy_digest")
        self._manifest_path().write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        self.assertEqual(self._threshold(), 0.5)

    def test_key_added_after_start_does_not_reach_live_run(self):
        """Ключ, которого не было на старте, до живого прогона НЕ доезжает.

        Сначала снимок мержился поверх файла, и «ключ, которого в снимке нет, доезжал из
        файла». Это открывало обход approval-гейта repin: `set quality.max_judge_iterations 20`
        менял лимит ре-итераций судьи прямо посреди прогона, потому что ключа не было в
        policy.json на момент init.py. Дефолты живут в коде читателей, а не в policy.json,
        так что отсутствующий ключ и так читается как дефолт — терять было нечего.
        """
        self._init_run()
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True,
                                        "max_judge_iterations": 20},
                            "brand_new": {"knob": 42}})
        cfg = CL.load_project_config(self.proj)
        self.assertEqual(cfg["quality"]["coverage_threshold"], 0.8, "снимок не применился")
        self.assertNotIn("max_judge_iterations", cfg["quality"],
                         "новый ключ протёк в живой прогон мимо repin")
        self.assertNotIn("brand_new", cfg, "новая секция протекла в живой прогон")
        # raw по-прежнему отдаёт файл целиком
        self.assertEqual(CL.load_project_config(self.proj, raw=True)["brand_new"]["knob"], 42)

    # ── repin ─────────────────────────────────────────────────────────────────
    def _approve_repin(self, feature="BUG-1"):
        r = _run(RECORD_APPROVAL, "--project", self.proj, "--key", f"policy-repin-{feature}",
                 "--approved-by", "user", "--reason", "тест")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_repin_without_approval_is_blocked(self):
        """R4: repin снимает фиксацию политики прогона — то есть правит пороги, которыми
        харнес меряет сам себя. Без согласия пользователя он не проходит (второй слой;
        первый — gate-guard.check_policy_repin)."""
        self._init_run()
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        r = _run(CONFIG, "--project", self.proj, "repin",
                 "--skill", "forgefix", "--feature", "BUG-1")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(self._threshold(), 0.8, "политика прогона изменилась без approval")

    def test_repin_dry_run_needs_no_approval(self):
        self._init_run()
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        r = _run(CONFIG, "--project", self.proj, "repin",
                 "--skill", "forgefix", "--feature", "BUG-1", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._threshold(), 0.8, "dry-run изменил политику")

    def test_repin_marker_is_one_shot(self):
        """Одно согласие = одно переснятие, иначе один «да» открывал бы правку порогов
        до конца прогона."""
        self._init_run()
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        self._approve_repin()
        self.assertEqual(_run(CONFIG, "--project", self.proj, "repin",
                              "--skill", "forgefix", "--feature", "BUG-1").returncode, 0)
        self._write_policy({"quality": {"coverage_threshold": 0.3, "tdd": True}})
        r = _run(CONFIG, "--project", self.proj, "repin",
                 "--skill", "forgefix", "--feature", "BUG-1")
        self.assertEqual(r.returncode, 3, f"маркер не потреблён: {r.stdout}")
        self.assertEqual(self._threshold(), 0.5, "второй repin прошёл без нового согласия")

    def test_repin_applies_policy_to_live_run(self):
        self._init_run()
        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        self.assertEqual(self._threshold(), 0.8)

        self._approve_repin()
        r = _run(CONFIG, "--project", self.proj, "repin",
                 "--skill", "forgefix", "--feature", "BUG-1", "--reason", "порог был неверный")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._threshold(), 0.5, "repin не применил политику к прогону")

        events = (self.proj / "ground/statements/forgefix/BUG-1/events.jsonl")
        self.assertTrue(events.exists(), "repin не оставил следа в журнале прогона")
        kinds = [json.loads(ln)["kind"] for ln in events.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertIn("repin", kinds)

    def test_repin_is_noop_when_nothing_changed(self):
        """Нечего переснимать — не гейтим: политика прогона не меняется."""
        self._init_run()
        r = _run(CONFIG, "--project", self.proj, "repin",
                 "--skill", "forgefix", "--feature", "BUG-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "unchanged")

    # ── дрейф виден в preflight ───────────────────────────────────────────────
    def test_preflight_warns_on_policy_drift(self):
        self._init_run()
        before = _run(PREFLIGHT, "--project", self.proj)
        self.assertNotIn("repin", before.stdout, "дрейфа нет, а предупреждение уже есть")

        self._write_policy({"quality": {"coverage_threshold": 0.5, "tdd": True}})
        after = _run(PREFLIGHT, "--project", self.proj)
        warns = " ".join(json.loads(after.stdout).get("warnings", []))
        self.assertIn("repin", warns,
                      f"preflight молчит о правке policy.json под прогоном: {after.stdout}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
