#!/usr/bin/env python3
"""Тесты tech-ветки (forgetech): ядро _techlib и гейты шагов на временном git-проекте.

Резолв зависимостей подменяется поддельным `mvnw` в корне проекта — так же, как его нашёл бы
_techlib на настоящем проекте (wrapper в корне побеждает `mvn` из PATH). Флага «подсунуть
готовый список зависимостей» у гейтов нет намеренно: им подделывали бы evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parents[2] / "hooks"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HOOKS))
import _techlib as T  # noqa: E402

FEATURE = "KEY-1"

FAKE_MVNW = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Поддельный mvnw: dependency:list по версии parent из pom.xml (+ jackson из property)."""
    import re, sys
    out = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("-DoutputFile="))
    pom = open("pom.xml", encoding="utf-8").read()
    boot = re.search(r"<parent>.*?<version>([^<]+)</version>", pom, re.S).group(1)
    jack = (re.search(r"<jackson.version>([^<]+)<", pom) or [None, "2.17.1"])[1]
    netty = "4.1.115" if boot.startswith("3.4") else "4.1.112"
    with open(out, "a", encoding="utf-8") as f:
        f.write("The following files have been resolved:\\n")
        f.write(f"   org.springframework.boot:spring-boot:jar:{boot}:compile\\n")
        f.write(f"   org.springframework:spring-core:jar:{'6.2.1' if boot.startswith('3.4') else '6.1.14'}:compile\\n")
        f.write(f"   com.fasterxml.jackson.core:jackson-databind:jar:{jack}:compile\\n")
        f.write(f"   io.netty:netty-buffer:jar:{netty}:runtime\\n")
    ''')

POM = textwrap.dedent('''\
    <project xmlns="http://maven.apache.org/POM/4.0.0">
      <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>{boot}</version>
      </parent>
      <artifactId>svc</artifactId>
      <properties>
        <jackson.version>{jackson}</jackson.version>
      </properties>
    </project>
    ''')

APP_YML = textwrap.dedent('''\
    server:
      port: 8080
    clients:
      x:
        url: https://x-old.local
        timeout: ${X_TIMEOUT:30}
    ''')
APP_DEV_YML = textwrap.dedent('''\
    clients:
      x:
        url: https://x-old.dev.local
    ''')
CONTROLLER = textwrap.dedent('''\
    package ru.acme;
    import org.springframework.web.bind.annotation.*;
    @RestController
    @RequestMapping("/api")
    public class PingController {
        @GetMapping("/ping")
        public String ping() { return "ok"; }
    }
    ''')

SDD_CONFIG = textwrap.dedent('''\
    # Смена хоста сервиса X
    **Jira:** KEY-1 · **Архетип:** config-change

    ## 1. Цель и мотивация
    Сервис X переехал на новый хост в dev.

    ## 2. Текущее → целевое
    | Что | Профиль | Было | Стало |
    |---|---|---|---|
    | clients.x.url | dev | https://x-old.dev.local | https://x.dev.local |

    ## 3. Скоуп (targets)
    - src/main/resources/application-dev.yml

    ## 4. Инварианты (Unchanged)
    API и остальные ключи SHALL CONTINUE TO не меняться.

    ## 5. Ожидаемые изменения
    Ровно цель из §2.

    ## 6. Пробы
    Нет.

    ## 7. Вне репозитория
    prod — helm values, меняет пользователь.

    ## 8. Риски / breaking changes
    Потребитель ключа — XClient; ошибка в хосте даст 5xx в dev.

    ## 9. Откат
    git revert.
    ''')


def _git(root: Path, *args) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _commit(root: Path, msg: str = "c") -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)


def _run(script: str, *args, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / script), *args], cwd=str(root),
                          capture_output=True, text=True, timeout=120)


class _Project(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        _git(self.root, "init", "-q")
        res = self.root / "src" / "main" / "resources"
        res.mkdir(parents=True)
        (res / "application.yml").write_text(APP_YML, encoding="utf-8")
        (res / "application-dev.yml").write_text(APP_DEV_YML, encoding="utf-8")
        java = self.root / "src" / "main" / "java" / "ru" / "acme"
        java.mkdir(parents=True)
        (java / "PingController.java").write_text(CONTROLLER, encoding="utf-8")
        self.write_pom("3.3.5", "2.17.1")
        mvnw = self.root / "mvnw"
        mvnw.write_text(FAKE_MVNW, encoding="utf-8")
        mvnw.chmod(0o755)
        (self.root / ".gitignore").write_text("ground/\n", encoding="utf-8")
        _commit(self.root, "init")
        self.task = T.task_dir(self.root, FEATURE)
        self.task.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def write_pom(self, boot: str, jackson: str) -> None:
        (self.root / "pom.xml").write_text(POM.format(boot=boot, jackson=jackson), encoding="utf-8")

    def write_plan(self, plan: dict, sdd: str = SDD_CONFIG) -> None:
        (self.task / T.PLAN_FILE).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        (self.task / T.SDD_FILE).write_text(sdd, encoding="utf-8")

    def config_plan(self, **over) -> dict:
        plan = {
            "feature_slug": FEATURE, "archetype": "config-change",
            "targets": ["src/main/resources/application-dev.yml"],
            "expected_changes": [{"kind": "config", "key": "clients.x.url", "profile": "dev",
                                  "from": "https://x-old.dev.local", "to": "https://x.dev.local"}],
            "invariants": ["tests", "api", "config-keys"],
            "outside_repo": "prod — helm values", "rollback": "git revert",
        }
        plan.update(over)
        return plan

    def set_dev_url(self, url: str) -> None:
        (self.root / "src/main/resources/application-dev.yml").write_text(
            f"clients:\n  x:\n    url: {url}\n", encoding="utf-8")

    def gate(self, mode: str) -> subprocess.CompletedProcess:
        return _run("check_invariants.py", mode, "--project", str(self.root),
                    "--feature", FEATURE, root=self.root)


class TestConfigParsing(unittest.TestCase):
    def test_yaml_nested_lists_blocks_and_docs(self):
        docs = T.parse_yaml_docs(textwrap.dedent('''\
            a:
              b: "q # not comment"   # comment
              list:
              - x
              - y
              items:
                - name: one
                  v: 1
            text: |
              l1
              l2
            ---
            spring:
              config:
                activate:
                  on-profile: dev
            a.b: dev
            '''))
        self.assertEqual(docs[0]["a.b"], "q # not comment")
        self.assertEqual((docs[0]["a.list[0]"], docs[0]["a.list[1]"]), ("x", "y"))
        self.assertEqual(docs[0]["a.items[0].name"], "one")
        self.assertEqual(docs[0]["text"], "l1\nl2")
        self.assertEqual(docs[1]["spring.config.activate.on-profile"], "dev")

    def test_properties_continuation_and_separators(self):
        p = T.parse_properties("a.b=1\nc : 2\n! x\nd=long\\\n   tail\n")
        self.assertEqual(p, {"a.b": "1", "c": "2", "d": "longtail"})

    def test_effective_config_profile_override_placeholder_and_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            res = Path(td) / "svc" / "src" / "main" / "resources"
            res.mkdir(parents=True)
            (res / "application.yml").write_text(
                "x:\n  url: base\n  timeout: ${T:30}\n  full: ${x.url}/api\n", encoding="utf-8")
            (res / "application.properties").write_text("x.url=from-props\n", encoding="utf-8")
            (res / "application-dev.yml").write_text("x:\n  url: dev-url\n", encoding="utf-8")
            (Path(td) / "src" / "test" / "resources").mkdir(parents=True)
            (Path(td) / "src/test/resources/application.yml").write_text("x:\n  url: test\n",
                                                                         encoding="utf-8")
            cfg = T.effective_config(Path(td))
        self.assertEqual(cfg["svc|default|x.url"], "from-props", ".properties перекрывает .yml")
        self.assertEqual(cfg["svc|dev|x.url"], "dev-url")
        self.assertEqual(cfg["svc|default|x.timeout"], "30")
        self.assertEqual(cfg["svc|dev|x.full"], "dev-url/api")
        self.assertFalse(any(k.startswith(".|") for k in cfg), "src/test/resources — не итоговый конфиг")

    def test_parse_maven_and_gradle_listings(self):
        m = T.parse_maven_list("   a.b:c:jar:1.0:compile\n   a.b:c:jar:1.1:test -- module x\n")
        self.assertEqual(m, {"a.b:c": {"1.0", "1.1"}})
        g = T.parse_gradle_tree("+--- org.x:y:1.0 -> 1.2\n|    \\--- org.x:z -> 3.0 (*)\n"
                                "+--- project :core\n")
        self.assertEqual(g, {"org.x:y": {"1.2"}, "org.x:z": {"3.0"}})


class TestPlanValidation(unittest.TestCase):
    def test_globs_and_missing_fields_rejected(self):
        errs = T.validate_plan({"archetype": "config-change", "targets": ["src/**/*.yml"],
                                "expected_changes": [{"kind": "config", "key": "k", "to": "v"}],
                                "invariants": ["tests"], "rollback": ""})
        text = "\n".join(errs)
        for needle in ("глобы", "from", "config-keys", "rollback", "outside_repo"):
            self.assertIn(needle, text)

    def test_valid_plan(self):
        plan = _Project.config_plan(_Project())
        self.assertEqual(T.validate_plan(plan), [])


class TestScope(unittest.TestCase):
    def _run(self, issue: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HERE / "check_tech_scope.py"), "--issue-json", "-"],
                              input=json.dumps(issue), capture_output=True, text=True)

    def test_upgrade_task_passes(self):
        r = self._run({"issuetype": "Task", "summary": "Поднять Spring Boot до 3.4.1",
                       "description": "Закрыть CVE-2024-38816, обновить зависимости"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_config_task_passes(self):
        r = self._run({"issuetype": "Задача", "summary": "Сменить хост сервиса X в dev",
                       "description": "clients.x.url в application-dev.yml"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_story_and_bug_escalate(self):
        for issue in ({"issuetype": "Story", "summary": "Поднять версию"},
                      {"issuetype": "Bug", "summary": "Падает после апгрейда"},
                      {"issuetype": "Task", "summary": "Нужно добавить возможность экспорта"}):
            with self.subTest(issue=issue):
                self.assertEqual(self._run(issue).returncode, 3)


class TestTechSdd(_Project):
    def test_config_plan_passes(self):
        self.write_plan(self.config_plan())
        r = _run("check_tech_sdd.py", str(self.task), "--project", str(self.root),
                 "--archetype", "config-change", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_archetype_mismatch_and_missing_section(self):
        self.write_plan(self.config_plan(), sdd=SDD_CONFIG.replace("## 9. Откат\ngit revert.\n", ""))
        r = _run("check_tech_sdd.py", str(self.task), "--project", str(self.root),
                 "--archetype", "dependency-upgrade", root=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Откат", r.stderr)
        self.assertIn("inputs.archetype", r.stderr)

    def test_upgrade_requires_breaking_changes_table_with_decisions(self):
        plan = self.config_plan(
            archetype="dependency-upgrade", targets=["pom.xml"],
            expected_changes=[{"kind": "build", "key":
                               "pom.xml:parent:org.springframework.boot:spring-boot-starter-parent",
                               "from": "3.3.5", "to": "3.4.1"}],
            invariants=["tests", "api", "kafka", "db-schema"])
        sdd = SDD_CONFIG.replace("config-change", "dependency-upgrade")
        self.write_plan(plan, sdd=sdd)
        r = _run("check_tech_sdd.py", str(self.task), "--project", str(self.root), root=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("таблицы breaking changes", r.stderr)
        sdd_ok = sdd.replace("Потребитель ключа — XClient; ошибка в хосте даст 5xx в dev.",
                             "| Изменение | Касается? | Решение |\n|---|---|---|\n"
                             "| setConnectTimeout удалён | нет | — |\n\n"
                             "Источник: https://example.org/release-notes")
        self.write_plan(plan, sdd=sdd_ok)
        r = _run("check_tech_sdd.py", str(self.task), "--project", str(self.root), root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestConfigChangeCycle(_Project):
    def test_full_cycle_snapshot_expect_compare_report(self):
        self.write_plan(self.config_plan())
        r = self.gate("snapshot")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = T.last_snapshot(self.root, FEATURE)
        self.assertIsNotNone(rec, "snapshot обязан оставить evidence tech-snapshot")
        self.assertEqual(rec["produced_by"], "check_invariants")

        r = self.gate("expect")
        self.assertEqual(r.returncode, 1, "до правки цель не достигнута")
        self.set_dev_url("https://x.dev.local")
        r = self.gate("expect")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.gate("compare")
        self.assertEqual(r.returncode, 0, r.stderr)

        r = _run("tech_report.py", "write", "--project", str(self.root), "--feature", FEATURE,
                 root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = (self.task / T.REPORT_FILE).read_text(encoding="utf-8")
        self.assertIn("https://x.dev.local", report)
        self.assertIn("helm values", report)
        r = _run("tech_report.py", "check", "--project", str(self.root), "--feature", FEATURE,
                 root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wrong_from_rejected_at_snapshot(self):
        plan = self.config_plan()
        plan["expected_changes"][0]["from"] = "https://guessed.local"
        self.write_plan(plan)
        r = self.gate("snapshot")
        self.assertEqual(r.returncode, 1)
        self.assertIn("не сходится", r.stderr)
        self.assertIsNone(T.last_snapshot(self.root, FEATURE))

    def test_dirty_target_rejected_at_snapshot(self):
        self.write_plan(self.config_plan())
        self.set_dev_url("https://x.dev.local")
        r = self.gate("snapshot")
        self.assertEqual(r.returncode, 1)
        self.assertIn("нетронутом", r.stderr)

    def test_probe_green_before_change_rejected(self):
        self.write_plan(self.config_plan(probes=[
            {"id": "P1", "kind": "pattern-absent", "glob": "src/**", "regex": "nothing-like-this"}]))
        r = self.gate("snapshot")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ДО правки", r.stderr)

    def test_extra_config_change_fails_expect(self):
        self.write_plan(self.config_plan())
        self.assertEqual(self.gate("snapshot").returncode, 0)
        (self.root / "src/main/resources/application-dev.yml").write_text(
            "clients:\n  x:\n    url: https://x.dev.local\n    timeout: 5\n", encoding="utf-8")
        r = self.gate("expect")
        self.assertEqual(r.returncode, 1)
        self.assertIn("незаявленное изменение", r.stderr)
        self.assertIn("clients.x.timeout", r.stderr)

    def test_undeclared_contract_change_escalates_on_compare(self):
        self.write_plan(self.config_plan())
        self.assertEqual(self.gate("snapshot").returncode, 0)
        self.set_dev_url("https://x.dev.local")
        ctrl = self.root / "src/main/java/ru/acme/PingController.java"
        ctrl.write_text(CONTROLLER.replace('@GetMapping("/ping")', '@GetMapping("/pong")'),
                        encoding="utf-8")
        r = self.gate("compare")
        self.assertEqual(r.returncode, 3)
        self.assertIn("ESCALATE", r.stderr)
        self.assertIn("/api/pong", r.stderr)

    def test_tampered_snapshot_is_not_a_snapshot(self):
        self.write_plan(self.config_plan())
        self.assertEqual(self.gate("snapshot").returncode, 0)
        snap = T.snapshot_dir(self.root, FEATURE) / T.SNAPSHOT_FILE
        data = json.loads(snap.read_text(encoding="utf-8"))
        data["classes"]["config"][".|dev|clients.x.url"] = "https://x.dev.local"
        snap.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(T.last_snapshot(self.root, FEATURE))
        self.assertEqual(self.gate("expect").returncode, 1)


class TestDependencyUpgradeCycle(_Project):
    def upgrade_plan(self, **over) -> dict:
        plan = self.config_plan(
            archetype="dependency-upgrade", targets=["pom.xml"],
            expected_changes=[
                {"kind": "build", "key": "pom.xml:parent:org.springframework.boot:spring-boot-starter-parent",
                 "from": "3.3.5", "to": "3.4.1"},
                {"kind": "deps", "coord": "org.springframework.boot:spring-boot",
                 "from": "3.3.5", "to": "3.4.1"},
                {"kind": "deps", "match": "org.springframework*:*"},
                {"kind": "deps", "match": "io.netty:*"},
            ],
            probes=[{"id": "P1", "kind": "dep-version", "coord": "org.springframework.boot:spring-boot",
                     "expect": "3.4.*"}],
            invariants=["tests", "api", "kafka", "db-schema"])
        plan.update(over)
        return plan

    def test_upgrade_cycle_with_match_areas(self):
        self.write_plan(self.upgrade_plan())
        r = self.gate("snapshot")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.write_pom("3.4.1", "2.17.1")
        r = self.gate("expect")
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads((T.snapshot_dir(self.root, FEATURE) / "expect.json")
                            .read_text(encoding="utf-8"))
        moved = {row[0] for row in result["diff"]["deps"]}
        self.assertIn("io.netty:netty-buffer", moved, "транзитив внутри match виден в diff")

    def test_undeclared_dependency_move_fails(self):
        self.write_plan(self.upgrade_plan())
        self.assertEqual(self.gate("snapshot").returncode, 0)
        self.write_pom("3.4.1", "2.18.2")      # jackson поднят «заодно» — его нет в плане
        r = self.gate("expect")
        self.assertEqual(r.returncode, 1)
        self.assertIn("jackson", r.stderr)


class TestAmend(_Project):
    def test_amend_adds_target_and_revokes_consent(self):
        import forge_events as FE
        self.write_plan(self.config_plan())
        plan_file = self.task / T.PLAN_FILE
        FE.append_approval(self.root, "tech-plan-KEY-1", approved_by="user", reason="r",
                           bound_sha256=T.sha256_file(plan_file))
        self.assertIsNotNone(FE.approval(self.root, "tech-plan-KEY-1"))
        r = _run("tech_plan.py", "amend", "--project", str(self.root), "--feature", FEATURE,
                 "--add-target", "src/main/resources/application.yml",
                 "--reason", "compile: ключ задан в базе", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertIn("src/main/resources/application.yml", plan["targets"])
        self.assertEqual(plan["amendments"][0]["reason"], "compile: ключ задан в базе")
        self.assertIsNone(FE.approval(self.root, "tech-plan-KEY-1"), "согласие на прежний скоуп погашено")

    def test_revoke_clears_stale_consent(self):
        import forge_events as FE
        self.write_plan(self.config_plan())
        FE.append_approval(self.root, "tech-plan-KEY-1", approved_by="user", reason="r",
                           bound_sha256="0" * 64)
        r = _run("tech_plan.py", "revoke", "--project", str(self.root), "--feature", FEATURE,
                 "--reason", "план переписан", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(FE.approval(self.root, "tech-plan-KEY-1"))

    def test_amend_refuses_already_modified_path(self):
        self.write_plan(self.config_plan())
        (self.root / "src/main/resources/application.yml").write_text("x: 1\n", encoding="utf-8")
        r = _run("tech_plan.py", "amend", "--project", str(self.root), "--feature", FEATURE,
                 "--add-target", "src/main/resources/application.yml", "--reason", "r", root=self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("уже изменены", r.stderr)


if __name__ == "__main__":
    unittest.main()
