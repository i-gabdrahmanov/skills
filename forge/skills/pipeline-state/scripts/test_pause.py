#!/usr/bin/env python3
"""Пауза прогона (pause.py) и отказ init.py при живом соседе.

Боевая жалоба: прогон идёт, нужно переключиться на другую задачу — а хуки продолжают
гейтить всё по фазе висящего прогона, второй /forge даёт два живых прогона и выбор по mtime.
Пины: новый прогон не стартует при живом соседе (exit 3 с вопросом пользователю); пауза
по согласию выводит прогон из резолва у всех хуков; шаги паузного прогона не двигаются;
resume не возвращает прогон поверх другого живого; рукописный маркер паузой не считается.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
INIT = HERE / "init.py"
UPDATE = HERE / "update.py"
PAUSE = HERE / "pause.py"
sys.path.insert(0, str(REPO / "hooks"))
import _project as P  # noqa: E402

STEPS = json.dumps([{"id": "fix-diagnose", "depends_on": []},
                    {"id": "fix-plan", "depends_on": ["fix-diagnose"]}])


def _run(script, *args, cwd):
    return subprocess.run([sys.executable, str(script), *map(str, args)], cwd=str(cwd),
                          capture_output=True, text=True, timeout=60)


class PauseBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True)
        subprocess.run(["git", "-C", str(self.proj), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def init(self, feat, skill="forgefix"):
        return _run(INIT, "--project", self.proj, "--skill", skill, "--feature", feat,
                    "--steps", STEPS, "--no-migrate", cwd=self.proj)

    def grant(self, key):
        g = self.proj / "ground"
        g.mkdir(parents=True, exist_ok=True)
        with open(g / "approvals.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "approval", "produced_by": "record_approval",
                                "key": key, "approved_by": "user", "reason": "ok"}) + "\n")

    def pause(self, feat, consent=True):
        if consent:
            self.grant(f"pause-{feat}")
        return _run(PAUSE, "--project", self.proj, "pause", feat, "--reason", "другая задача",
                    cwd=self.proj)

    def resume(self, feat):
        return _run(PAUSE, "--project", self.proj, "resume", feat, cwd=self.proj)

    def marker(self, feat, skill="forgefix"):
        return P.pause_marker_path(self.proj, skill, feat)

    def active(self):
        return P.resolve_active_run(self.proj)["feature"]


class TInitRefusesWithLiveNeighbour(PauseBase):
    def test_second_run_refused_until_first_paused(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        r = self.init("BUG-2")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("BUG-1", r.stderr)
        self.assertIn("pause.py pause BUG-1", r.stderr, "отказ обязан назвать команду паузы")
        self.assertIn("--evidence", r.stderr, "пауза — только с ответом пользователя")
        self.assertFalse((self.proj / "ground/statements/forgefix/BUG-2").exists(),
                         "отказ init.py оставил стейт нового прогона")

        self.assertEqual(self.pause("BUG-1").returncode, 0)
        self.assertEqual(self.init("BUG-2").returncode, 0)
        self.assertEqual(self.active(), "BUG-2")

    def test_other_namespace_counts_too(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        r = self.init("STOR-1", skill="forgetech")
        self.assertEqual(r.returncode, 3, r.stderr)

    def test_force_on_own_slug_is_not_blocked_by_itself(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        r = _run(INIT, "--project", self.proj, "--skill", "forgefix", "--feature", "BUG-1",
                 "--steps", STEPS, "--no-migrate", "--force", cwd=self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_completed_neighbour_does_not_block(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        mp = P.manifest_path(self.proj, "forgefix", "BUG-1")
        man = json.loads(mp.read_text(encoding="utf-8"))
        for s in man["steps"]:
            s["status"] = "completed"
        mp.write_text(json.dumps(man), encoding="utf-8")
        self.assertEqual(self.init("BUG-2").returncode, 0)


class TPauseResolver(PauseBase):
    def test_paused_run_leaves_the_resolver(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        self.assertEqual(self.active(), "BUG-1")
        self.assertEqual(self.pause("BUG-1").returncode, 0)
        self.assertIsNone(P.resolve_active_run(self.proj)["path"],
                          "прогон на паузе по-прежнему активен для хуков")
        self.assertEqual(P.live_runs(self.proj), [])

    def test_forged_marker_is_not_a_pause(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        self.marker("BUG-1").write_text(json.dumps({"paused_at": "now"}), encoding="utf-8")
        self.assertEqual(self.active(), "BUG-1", "рукописный paused.json снял прогон с активных")

    def test_pause_needs_consent_and_spends_it(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        r = self.pause("BUG-1", consent=False)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(self.marker("BUG-1").exists())

        self.assertEqual(self.pause("BUG-1").returncode, 0)
        self.assertEqual(self.resume("BUG-1").returncode, 0)
        r = self.pause("BUG-1", consent=False)
        self.assertEqual(r.returncode, 3, "согласие на паузу не одноразовое")

    def test_evidence_records_consent_in_one_command(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        r = _run(PAUSE, "--project", self.proj, "pause", "BUG-1", "--reason", "другая задача",
                 "--evidence", "Да, BUG-1 на паузу — начать новую", cwd=self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(P.run_paused(self.marker("BUG-1").parent))
        log = (self.proj / "ground/approvals.jsonl").read_text(encoding="utf-8")
        self.assertIn("Да, BUG-1 на паузу", log, "согласие не попало в журнал под аудит")
        self.assertIn('"recorded_by": "pause"', log)
        self.assertEqual(self.resume("BUG-1").returncode, 0)
        self.assertEqual(self.pause("BUG-1", consent=False).returncode, 3,
                         "согласие из --evidence не потрачено")

    def test_evidence_that_is_not_consent_refused(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        for ev in ("ок", "нет, не ставь на паузу BUG-1"):
            with self.subTest(ev=ev):
                r = _run(PAUSE, "--project", self.proj, "pause", "BUG-1", "--reason", "x",
                         "--evidence", ev, cwd=self.proj)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(self.marker("BUG-1").exists())

    def test_completed_run_is_not_paused(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        mp = P.manifest_path(self.proj, "forgefix", "BUG-1")
        man = json.loads(mp.read_text(encoding="utf-8"))
        for s in man["steps"]:
            s["status"] = "completed"
        mp.write_text(json.dumps(man), encoding="utf-8")
        self.assertEqual(self.pause("BUG-1").returncode, 2)


class TPausedRunDoesNotMove(PauseBase):
    def test_update_refuses_paused_run(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        self.assertEqual(self.pause("BUG-1").returncode, 0)
        r = _run(UPDATE, "--project", self.proj, "--skill", "forgefix", "--feature", "BUG-1",
                 "--step-id", "fix-diagnose", "--status", "in_progress", cwd=self.proj)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("на паузе", r.stderr)
        man = json.loads(P.manifest_path(self.proj, "forgefix", "BUG-1").read_text("utf-8"))
        self.assertEqual(man["steps"][0]["status"], "pending")


class TResume(PauseBase):
    def test_resume_refused_over_live_run(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        self.assertEqual(self.pause("BUG-1").returncode, 0)
        self.assertEqual(self.init("BUG-2").returncode, 0)
        r = self.resume("BUG-1")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("BUG-2", r.stderr)
        self.assertTrue(self.marker("BUG-1").exists(), "отказ resume снял паузу")

        self.assertEqual(self.pause("BUG-2").returncode, 0)
        self.assertEqual(self.resume("BUG-1").returncode, 0)
        self.assertEqual(self.active(), "BUG-1")

    def test_resume_reports_files_changed_during_pause(self):
        (self.proj / "before.txt").write_text("a", encoding="utf-8")
        self.assertEqual(self.init("BUG-1").returncode, 0)
        self.assertEqual(self.pause("BUG-1").returncode, 0)
        (self.proj / "before.txt").write_text("b", encoding="utf-8")
        (self.proj / "other-task.txt").write_text("x", encoding="utf-8")
        r = _run(PAUSE, "--project", self.proj, "resume", "BUG-1", "--json", cwd=self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        changed = json.loads(r.stdout)["changed_files"]
        self.assertIn("before.txt", changed)
        self.assertIn("other-task.txt", changed)
        self.assertFalse([c for c in changed if c.startswith("ground/")],
                         "стейт харнеса попал в отчёт о правках задачи")

    def test_committing_own_prepause_edit_is_not_a_change(self):
        """Правка, незакоммиченная на паузе и закоммиченная как есть, — не изменение."""
        g = ["git", "-C", str(self.proj), "-c", "user.email=t@t", "-c", "user.name=t"]
        (self.proj / ".gitignore").write_text("ground/\n", encoding="utf-8")
        subprocess.run(g + ["add", ".gitignore"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "ignore"], check=True)
        (self.proj / "mine.txt").write_text("до паузы", encoding="utf-8")
        self.assertEqual(self.init("BUG-1").returncode, 0)
        self.assertEqual(self.pause("BUG-1").returncode, 0)
        subprocess.run(g + ["add", "mine.txt"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "mine"], check=True)
        (self.proj / "theirs.txt").write_text("другая задача", encoding="utf-8")
        subprocess.run(g + ["add", "theirs.txt"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "theirs"], check=True)
        r = _run(PAUSE, "--project", self.proj, "resume", "BUG-1", "--json", cwd=self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["changed_files"], ["theirs.txt"])

    def test_status_lists_live_and_paused(self):
        self.assertEqual(self.init("BUG-1").returncode, 0)
        self.assertEqual(self.pause("BUG-1").returncode, 0)
        self.assertEqual(self.init("BUG-2").returncode, 0)
        r = _run(PAUSE, "--project", self.proj, "status", "--json", cwd=self.proj)
        rows = {x["feature"]: x for x in json.loads(r.stdout)}
        self.assertTrue(rows["BUG-1"]["paused"])
        self.assertEqual(rows["BUG-1"]["reason"], "другая задача")
        self.assertFalse(rows["BUG-2"]["paused"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
