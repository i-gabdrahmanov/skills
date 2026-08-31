#!/usr/bin/env python3
"""Golden-тест fix-ветки (forgefix): стейт-машина + хуки на временном проекте.

Проверяет, что новая ветка реально ЗАКРЫТА enforcement'ом, а не только описана в SKILL.md:
  - все шаги манифеста fix-* закрываются штатным путём (evidence гейта + origin субагента);
  - шаг fix-* НЕЛЬЗЯ закрыть без evidence детерминированного гейта (GATE_RESULT_PREFIXES),
    включая инлайн-скоуп-чек fix-intake — иначе «баг это или фича» решалось бы прозой;
  - subagent-фазу fix-* нельзя закрыть без origin от SubagentStop (inline-исполнение);
  - tdd-guard блокирует src/main, пока fix-red не завершён;
  - inline-phase-guard блокирует запись дельты спеки главным агентом на fix-spec.

Требует Python 3.10+.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INIT = REPO / "skills/pipeline-state/scripts/init.py"
UPDATE = REPO / "skills/pipeline-state/scripts/update.py"
STEPS_JSON = Path(__file__).resolve().parents[1] / "references" / "manifest-steps.json"
TDD_GUARD = REPO / "hooks/tdd-guard.py"
INLINE_GUARD = REPO / "hooks/inline-phase-guard.py"

SLUG = "STOR-1"
STORY = "STOR-100"
ORDER = ["fix-intake", "fix-diag", "fix-red", "fix-green", "fix-verify", "fix-spec"]
# Артефакты фаз под КАНОНИЧЕСКИМИ именами: по ним план читают fix-red/fix-green, а дельту —
# /forge-merge. Документ, названный по ключу задачи, для них не существует.
FIX_ARTIFACTS = {"fix-diag": ("fix-plan.md", "task-plan.json"), "fix-spec": ("sdd.md",)}


def _run(args, cwd):
    return subprocess.run([sys.executable, *map(str, args)],
                          cwd=str(cwd), capture_output=True, text=True, timeout=60)


def _hook(hook: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)


class FixCycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name).resolve()
        (self.proj / "docs/feature-pipeline" / SLUG).mkdir(parents=True)
        (self.proj / "ground").mkdir(parents=True, exist_ok=True)
        (self.proj / "ground/pipeline.json").write_text(json.dumps({
            "quality": {"tdd": True, "tdd_integration_skip": False},
            "docs": {"mode": "in-repo", "docs_path": "docs", "feature_subdir": "feature-pipeline"},
        }), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _state_dir(self, feature=SLUG):
        return self.proj / "ground/statements/forgefix" / feature

    def _init(self, feature=SLUG):
        r = _run([INIT, "--project", self.proj, "--skill", "forgefix",
                  "--feature", feature, "--steps", f"@{STEPS_JSON}"], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _evidence(self, step_id: str, feature=SLUG, *, gate=True, origin=True):
        if gate:
            gdir = self._state_dir(feature) / "gates"
            gdir.mkdir(parents=True, exist_ok=True)
            (gdir / f"{step_id}.json").write_text(json.dumps(
                {"produced_by": "record_gate", "step_id": step_id, "passed": True}), encoding="utf-8")
        if origin:
            odir = self._state_dir(feature) / "_origins"
            odir.mkdir(parents=True, exist_ok=True)
            (odir / f"{step_id}.json").write_text(json.dumps({"step_id": step_id}), encoding="utf-8")

    def _close(self, step_id: str, feature=SLUG):
        return _run([UPDATE, "--project", self.proj, "--skill", "forgefix", "--feature", feature,
                     "--step-id", step_id, "--status", "completed", "--closed-by", "subagent",
                     "--output-json", json.dumps({"step_id": step_id})], self.proj)

    def _decide(self, key: str, value: str):
        """Записанное решение прогона (то, что делает config.py set)."""
        cfg = self.proj / "ground/pipeline.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        cur = data.setdefault("sources", {})
        cur[key] = value
        cfg.write_text(json.dumps(data), encoding="utf-8")

    def _fixdir(self, feature=SLUG, story=STORY) -> Path:
        return self.proj / "docs/feature-pipeline" / story / "fixes" / feature

    def _artifacts(self, step_id: str, feature=SLUG, story=STORY, *, name=None):
        """Артефакты фазы. name=... — имитация записи под НЕканоническим именем."""
        names = FIX_ARTIFACTS.get(step_id)
        if not names:
            return
        d = self._fixdir(feature, story)
        d.mkdir(parents=True, exist_ok=True)
        for n in ([name] if name else names):
            (d / n).write_text("x" if n.endswith(".md") else "{}", encoding="utf-8")

    # ── стейт-машина ──────────────────────────────────────────────────────────
    def test_full_fix_cycle_closes(self):
        self._init()
        self._decide("story", STORY)          # ответ на «к какой стори относится баг» (§2.1)
        for step_id in ORDER:
            self._evidence(step_id)
            self._artifacts(step_id)
            r = self._close(step_id)
            self.assertEqual(r.returncode, 0, f"{step_id} не закрылся: {r.stderr or r.stdout}")
        manifest = json.loads((self._state_dir() / "manifest.json").read_text(encoding="utf-8"))
        statuses = {s["id"]: s["status"] for s in manifest["steps"]}
        self.assertEqual(set(statuses), set(ORDER))
        self.assertTrue(all(v == "completed" for v in statuses.values()), statuses)

    def test_step_blocked_without_gate_evidence(self):
        """Скоуп-чек «баг или фича» — это evidence, а не слово модели."""
        self._init("STOR-2")
        self._evidence("fix-intake", "STOR-2", gate=False)  # только origin
        r = self._close("fix-intake", "STOR-2")
        self.assertNotEqual(r.returncode, 0,
                            "fix-intake закрылся без evidence гейта — enforcement сломан")

    def test_subagent_phase_blocked_without_origin(self):
        """fix-spec — subagent-фаза: без origin от SubagentStop шаг не закрывается."""
        self._init("STOR-3")
        self._evidence("fix-spec", "STOR-3", origin=False)
        r = _run([UPDATE, "--project", self.proj, "--skill", "forgefix", "--feature", "STOR-3",
                  "--step-id", "fix-spec", "--status", "completed",
                  "--output-json", json.dumps({"step_id": "fix-spec"})], self.proj)
        self.assertNotEqual(r.returncode, 0,
                            "fix-spec закрылся без origin субагента — inline-дыра")

    def test_intake_blocked_until_story_recorded(self):
        """Ответ «к какой стори относится баг» обязан быть ЗАПИСАН, а не только получен.

        Прогон: агент спросил, пользователь ответил, `config.py set sources.story` не выполнился
        (на свежем проекте нет pipeline.json → set падает exit 3) — и шаг закрылся с потерянным
        ответом: папка фикса уехала плоской, find_spec_anchor остался без сильнейшего признака."""
        self._init("STOR-5")
        self._evidence("fix-intake", "STOR-5")
        r = self._close("fix-intake", "STOR-5")
        self.assertNotEqual(r.returncode, 0, "fix-intake закрылся с незаписанным sources.story")
        self.assertIn("sources.story", r.stderr)
        self._decide("story", "none")     # осознанное «стори неизвестна» — тоже ответ
        self.assertEqual(self._close("fix-intake", "STOR-5").returncode, 0)

    def test_fix_spec_blocked_when_delta_misnamed(self):
        """Дельта под именем ключа задачи выпадает из /forge-merge (он ищет строго sdd.md)."""
        self._init("STOR-6")
        self._decide("story", STORY)
        self._evidence("fix-spec", "STOR-6")
        self._decide("spec_anchor", "REQ-0007")
        self._artifacts("fix-spec", "STOR-6", name="STOR-6.md")
        r = self._close("fix-spec", "STOR-6")
        self.assertNotEqual(r.returncode, 0, "дельта под чужим именем закрыла фазу")
        self.assertIn("sdd.md", r.stderr)
        self._artifacts("fix-spec", "STOR-6")
        self.assertEqual(self._close("fix-spec", "STOR-6").returncode, 0)

    def test_required_step_cannot_be_silently_skipped(self):
        self._init("STOR-4")
        r = _run([UPDATE, "--project", self.proj, "--skill", "forgefix", "--feature", "STOR-4",
                  "--step-id", "fix-red", "--status", "skipped"], self.proj)
        self.assertNotEqual(r.returncode, 0, "fix-red тихо пропустился — RED необязателен?")

    # ── хуки ──────────────────────────────────────────────────────────────────
    def test_tdd_guard_blocks_main_before_fix_red(self):
        self._init()
        r = _hook(TDD_GUARD, {
            "hook_event_name": "PreToolUse", "cwd": str(self.proj), "tool_name": "Write",
            "tool_input": {"file_path": str(self.proj / "svc/src/main/java/A.java"),
                           "content": "class A {}"}})
        self.assertEqual(r.returncode, 2, r.stderr or r.stdout)
        self.assertIn("fix-red", r.stderr)

    def _activate_spec_phase(self):
        manifest_path = self._state_dir() / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for s in manifest["steps"]:
            s["status"] = "in_progress" if s["id"] == "fix-spec" else "completed"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_gate_guard_blocks_delta_without_anchor_decision(self):
        """Якорь («к какой стори баг») — обязательное решение: без него дельту писать нельзя."""
        self._init()
        self._activate_spec_phase()
        payload = {
            "hook_event_name": "PreToolUse", "cwd": str(self.proj), "tool_name": "Write",
            "tool_input": {"file_path": str(self.proj / "docs/feature-pipeline" / SLUG / "sdd.md"),
                           "content": "# SDD"}}
        r = _hook(REPO / "hooks/gate-guard.py", payload)
        self.assertEqual(r.returncode, 2, r.stderr or r.stdout)
        self.assertIn("sources.spec_anchor", r.stderr)

        cfg = self.proj / "ground/pipeline.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data.setdefault("sources", {})["spec_anchor"] = "REQ-0007"
        cfg.write_text(json.dumps(data), encoding="utf-8")
        r = _hook(REPO / "hooks/gate-guard.py", payload)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

    def test_inline_guard_blocks_main_agent_spec_delta(self):
        self._init()
        self._activate_spec_phase()
        r = _hook(INLINE_GUARD, {
            "hook_event_name": "PreToolUse", "cwd": str(self.proj), "tool_name": "Write",
            "tool_input": {"file_path": str(self.proj / "docs/feature-pipeline" / SLUG / "sdd.md"),
                           "content": "# SDD"}})
        self.assertEqual(r.returncode, 2, r.stderr or r.stdout)
        self.assertIn("fix-spec", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
