#!/usr/bin/env python3
"""Golden-тест tech-ветки (forgetech): стейт-машина + гейты + хуки на временном git-проекте.

Ветка обязана быть ЗАКРЫТА enforcement'ом, а не только описана в SKILL.md. Здесь — тот же путь,
которым идёт оркестратор, настоящими скриптами (init.py, config.py, record_gate.py, update.py,
record_approval.py) и хуками (gate-guard, tech-guard):
  - архетип — решение из реестра (enum), без него tech-intake не закрывается;
  - каждый шаг закрывается только evidence своего детерминированного гейта (record_gate с
    гейтом фазы в --cmd) и, для субагентных фаз, origin от SubagentStop;
  - до утверждения плана и до снимка «до» правка проекта блокируется хуками;
  - финальный шаг ветки — tech-record (archive.py).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
INIT = REPO / "skills/pipeline-state/scripts/init.py"
UPDATE = REPO / "skills/pipeline-state/scripts/update.py"
RECORD_GATE = REPO / "skills/pipeline-state/scripts/record_gate.py"
RECORD_APPROVAL = REPO / "skills/pipeline-state/scripts/record_approval.py"
CONFIG = REPO / "skills/config-helper/scripts/config.py"
STEPS_JSON = HERE.parent / "references" / "manifest-steps.json"
GATE_GUARD = REPO / "hooks/gate-guard.py"
TECH_GUARD = REPO / "hooks/tech-guard.py"
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(HERE))
import forge_events as FE  # noqa: E402
import _techlib as T  # noqa: E402

KEY = "KEY-7"
TARGET = "src/main/resources/application-dev.yml"
PLAN = {"feature_slug": KEY, "archetype": "config-change", "targets": [TARGET],
        "expected_changes": [{"kind": "config", "key": "clients.x.url", "profile": "dev",
                              "from": "https://x-old.dev.local", "to": "https://x.dev.local"}],
        "invariants": ["tests", "api", "config-keys"],
        "outside_repo": "prod — helm values, меняет пользователь", "rollback": "git revert"}
SDD = textwrap.dedent('''\
    # Смена хоста X
    **Jira:** KEY-7 · **Архетип:** config-change

    ## 1. Цель и мотивация
    Сервис X переехал.
    ## 2. Текущее → целевое
    clients.x.url (dev): https://x-old.dev.local → https://x.dev.local
    ## 3. Скоуп (targets)
    - src/main/resources/application-dev.yml
    ## 4. Инварианты (Unchanged)
    API и остальные ключи SHALL CONTINUE TO не меняться.
    ## 5. Ожидаемые изменения
    Ровно цель из §2.
    ## 6. Пробы
    Нет.
    ## 7. Вне репозитория
    prod — helm values.
    ## 8. Риски / breaking changes
    Потребитель — XClient.
    ## 9. Откат
    git revert.
    ''')


def _py(args, cwd, **kw):
    return subprocess.run([sys.executable, *map(str, args)], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", timeout=120, **kw)


def _hook(hook: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


class TechCycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name).resolve()
        g = lambda *a: subprocess.run(["git", "-C", str(self.proj), *a], check=True,  # noqa: E731
                                      capture_output=True)
        g("init", "-q")
        res = self.proj / "src/main/resources"
        res.mkdir(parents=True)
        (res / "application-dev.yml").write_text("clients:\n  x:\n    url: https://x-old.dev.local\n",
                                                 encoding="utf-8")
        (self.proj / ".gitignore").write_text("ground/\n", encoding="utf-8")
        g("add", "-A")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        (self.proj / "ground").mkdir()
        (self.proj / "ground/policy.json").write_text(json.dumps({
            "docs": {"mode": "in-repo", "docs_path": "docs", "feature_subdir": "feature-pipeline"},
            "jira": {"enabled": False}}), encoding="utf-8")
        self.techdir = T.task_dir(self.proj, KEY)

    def tearDown(self):
        self._tmp.cleanup()

    # ── helpers ───────────────────────────────────────────────────────────────
    def set(self, key: str, value: str, *extra):
        return _py([CONFIG, "--project", self.proj, "set", key, value, *extra,
                    "--skill", "forgetech", "--feature", KEY], self.proj)

    def gate(self, step: str, cmd: str):
        return _py([RECORD_GATE, "--project", self.proj, "--skill", "forgetech", "--feature", KEY,
                    "--step-id", step, "--cmd", cmd], self.proj)

    def close(self, step: str):
        return _py([UPDATE, "--project", self.proj, "--skill", "forgetech", "--feature", KEY,
                    "--step-id", step, "--status", "completed", "--closed-by", "subagent"], self.proj)

    def origin(self, step: str):
        FE.append_event(self.proj, "forgetech", KEY, "origin", step_id=step)

    def script(self, name: str) -> str:
        return f"{sys.executable} {HERE / name}"

    def write_payload(self, rel: str) -> dict:
        return {"hook_event_name": "PreToolUse", "cwd": str(self.proj), "tool_name": "write_file",
                "tool_input": {"file_path": str(self.proj / rel), "content": "x"}}

    # ── сценарий ──────────────────────────────────────────────────────────────
    def test_tech_cycle_end_to_end(self):
        r = _py([INIT, "--project", self.proj, "--skill", "forgetech", "--feature", KEY,
                 "--steps", f"@{STEPS_JSON}"], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        for k, v, *extra in (("inputs.mode", "tech"), ("decisions.mode_task", KEY),
                             ("decisions.auto_max_risk", "R2", "--confirm"),
                             ("decisions.criticality", "medium")):
            r = self.set(k, v, *extra)
            self.assertEqual(r.returncode, 0, f"{k}: {r.stderr or r.stdout}")
        self.assertNotEqual(self.set("inputs.archetype", "refactor").returncode, 0,
                            "архетип вне реестра v1 не записывается")

        # tech-intake: скоуп-чек + архетип обязателен на закрытии
        self.techdir.mkdir(parents=True)
        (self.techdir / "issue.json").write_text(json.dumps(
            {"issuetype": "Task", "summary": "Сменить хост сервиса X в dev",
             "description": "clients.x.url в application-dev.yml"}), encoding="utf-8")
        r = self.gate("tech-intake", f"{self.script('check_tech_scope.py')} --issue-json "
                                     f"{self.techdir / 'issue.json'}")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.close("tech-intake")
        self.assertNotEqual(r.returncode, 0, "без inputs.archetype tech-intake не закрывается")
        self.assertIn("inputs.archetype", r.stderr)
        self.assertEqual(self.set("inputs.archetype", "config-change").returncode, 0)
        self.assertEqual(self.close("tech-intake").returncode, 0)

        # tech-diag: гейт с посторонней командой не принимается; настоящий — да
        self.assertEqual(self.gate("tech-diag", "true").returncode, 2)
        (self.techdir / T.SDD_FILE).write_text(SDD, encoding="utf-8")
        (self.techdir / T.PLAN_FILE).write_text(json.dumps(PLAN), encoding="utf-8")
        r = self.gate("tech-diag", f"{self.script('check_tech_sdd.py')} {self.techdir} "
                                   f"--project {self.proj} --archetype config-change")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.close("tech-diag")
        self.assertNotEqual(r.returncode, 0, "субагентная фаза без origin не закрывается")
        self.origin("tech-diag")
        self.assertEqual(self.close("tech-diag").returncode, 0)

        # tech-apply: до согласия пишет нельзя (gate-guard + tech-guard)
        payload = self.write_payload(TARGET)
        r = _hook(GATE_GUARD, payload)
        self.assertEqual(r.returncode, 2)
        self.assertIn(f"tech-plan-{KEY}", r.stderr, "блок именно за отсутствие согласия на план")
        r = _hook(TECH_GUARD, payload)
        self.assertEqual(r.returncode, 2)
        self.assertIn("нет согласия", r.stderr)
        FE.append_approval(self.proj, f"tech-plan-{KEY}", approved_by="user", reason="ok",
                           evidence="Да, делаем по плану",
                           bound_sha256=T.sha256_file(self.techdir / T.PLAN_FILE))
        self.assertEqual(_hook(GATE_GUARD, payload).returncode, 0)
        self.assertEqual(_hook(TECH_GUARD, payload).returncode, 2, "без снимка «до» правки нет")
        r = _py([HERE / "check_invariants.py", "snapshot", "--project", self.proj, "--feature", KEY],
                self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_hook(TECH_GUARD, payload).returncode, 0)
        self.assertEqual(_hook(TECH_GUARD, self.write_payload("pom.xml")).returncode, 2)
        (self.proj / TARGET).write_text("clients:\n  x:\n    url: https://x.dev.local\n",
                                        encoding="utf-8")
        r = self.gate("tech-apply", f"{self.script('check_invariants.py')} expect "
                                    f"--project {self.proj} --feature {KEY}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.origin("tech-apply")
        self.assertEqual(self.close("tech-apply").returncode, 0)

        # tech-verify → tech-record
        r = self.gate("tech-verify", f"{self.script('check_invariants.py')} compare "
                                     f"--project {self.proj} --feature {KEY}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.origin("tech-verify")
        self.assertEqual(self.close("tech-verify").returncode, 0)
        self.assertEqual(_hook(TECH_GUARD, payload).returncode, 2, "после tech-apply проект не правится")
        r = _py([HERE / "tech_report.py", "write", "--project", self.proj, "--feature", KEY], self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.gate("tech-record", f"{self.script('tech_report.py')} check "
                                     f"--project {self.proj} --feature {KEY}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.origin("tech-record")
        self.assertEqual(self.close("tech-record").returncode, 0)

        manifest = json.loads((self.proj / "ground/statements/forgetech" / KEY / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertTrue(all(s["status"] == "completed" for s in manifest["steps"]), manifest["steps"])
        sys.path.insert(0, str(REPO / "skills/pipeline-state/scripts"))
        import archive
        self.assertEqual(archive.final_step_ids("forgetech", manifest), ["tech-record"])

    def test_failed_gate_does_not_close_step(self):
        _py([INIT, "--project", self.proj, "--skill", "forgetech", "--feature", KEY,
             "--steps", f"@{STEPS_JSON}"], self.proj)
        self.techdir.mkdir(parents=True)
        (self.techdir / "issue.json").write_text(json.dumps(
            {"issuetype": "Story", "summary": "Как пользователь хочу экспорт"}), encoding="utf-8")
        r = self.gate("tech-intake", f"{self.script('check_tech_scope.py')} --issue-json "
                                     f"{self.techdir / 'issue.json'}")
        self.assertNotEqual(r.returncode, 0)
        self.set("inputs.archetype", "config-change")
        r = self.close("tech-intake")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("passed:false", r.stderr)


if __name__ == "__main__":
    unittest.main()
