#!/usr/bin/env python3
"""test_docs_hooks_consistency.py — пинит «доки ↔ задеплоено» для control-plane.

FORGE.md объявлен источником правды, но §«Структура хуков» исторически расходилась с
проводкой хуков (не было sod-enforcer/subagent-enforcer; eval-guard документирован, но не
подключён). Этот тест парсит упорядоченные цепочки из обоих и требует совпадения.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
REPO = HOOKS.parent
SETTINGS = HOOKS / "settings.hooks.json"  # проводка project-модели: ${PYTHON} ${PROJECT_ROOT}/.gigacode/hooks/...
FORGE = REPO / "FORGE.md"


def _basename(command: str) -> str | None:
    m = re.search(r"([\w-]+)\.py\b", command)
    return m.group(1) if m else None


def _settings_chain(matcher_pred) -> list[str]:
    block = json.loads(SETTINGS.read_text(encoding="utf-8")).get("hooks", {})
    for group in block.get("PreToolUse", []):
        if matcher_pred(group.get("matcher", "")):
            out = []
            for h in group.get("hooks", []):
                b = _basename(h.get("command", ""))
                if b:
                    out.append(b)
            return out
    return []


def _forge_list(header: str) -> list[str]:
    text = FORGE.read_text(encoding="utf-8")
    idx = text.find(header)
    if idx < 0:
        return []
    names = []
    for line in text[idx + len(header):].splitlines():
        m = re.match(r"\s*\d+\.\s+`([\w-]+)`", line)
        if m:
            names.append(m.group(1))
        elif names:  # список закончился
            break
    return names


class TestDocsHooksConsistency(unittest.TestCase):
    def test_bash_chain_matches(self):
        # matcher переведён на канон-имя рантайма (run_shell_command); старый ^Bash$ не матчил
        # ничего — см. hooks/test_matcher_canonical_names.py.
        settings = _settings_chain(lambda m: "run_shell_command" in m and "write_file" not in m)
        forge = _forge_list("**PreToolUse `run_shell_command` (Bash) — sequential:**")
        self.assertTrue(settings, "не нашёл run_shell_command цепочку в hooks.json")
        self.assertEqual(forge, settings,
                         f"FORGE.md §Структура хуков (Bash) разошлась с settings: forge={forge} settings={settings}")

    def test_write_edit_chain_matches(self):
        settings = _settings_chain(lambda m: "Write" in m and "Edit" in m)
        forge = _forge_list("**PreToolUse `(Write|Edit)` — sequential:**")
        self.assertTrue(settings, "не нашёл Write|Edit цепочку в hooks.json")
        self.assertEqual(forge, settings,
                         f"FORGE.md §Структура хуков (Write|Edit) разошлась с settings: forge={forge} settings={settings}")

    def test_removed_hooks_absent(self):
        raw = SETTINGS.read_text(encoding="utf-8")
        self.assertNotIn("subagent-enforcer", raw, "subagent-enforcer должен быть удалён из settings")
        self.assertNotIn("gate-resolver", raw, "gate-resolver должен быть удалён из settings")
        # Логи и бюджет сняты целиком: ни хуков, ни их имён в разводке.
        self.assertNotIn("budget-meter", raw, "budget-meter удалён — не должен быть в settings")
        self.assertNotIn("log-agent", raw, "log-agent удалён — не должен быть в settings")
        self.assertNotIn("agent-logger", raw, "agent-logger (log-agent) удалён — не должен быть в settings")

    def test_no_doc_promises_an_unwired_hook(self):
        """Обратное направление: дока НЕ вправе обещать хук, которого нет в проводке.

        Существующие пины ловят только «проведён, но не описан». Обратную дыру они пропускали:
        `docs/pipeline-technical.md` годами держал в таблице `evidence-enforcer` с пометкой
        «блок доставки, exit 2» — хука нет с тех пор, как из форжа сняли доставку. Дока,
        обещающая несуществующий enforcement, хуже отсутствия доки: на неё полагаются.
        """
        block = json.loads(SETTINGS.read_text(encoding="utf-8")).get("hooks", {})
        wired: set[str] = set()
        for groups in block.values():
            for group in groups:
                for h in group.get("hooks", []):
                    b = _basename(h.get("command", ""))
                    if b:
                        wired.add(b)
        name = re.compile(r"`?([a-z][a-z0-9\-_]*)(?:\.py)?`?")
        events = ("PreToolUse", "PostToolUse", "SubagentSt", "UserPrompt", "Stop")
        for doc in (FORGE, HOOKS / "DEPLOY.md", REPO / "docs" / "pipeline-technical.md"):
            claimed: set[str] = set()
            for line in doc.read_text(encoding="utf-8").splitlines():
                if not line.startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 3 or not any(e in cells[1] for e in events):
                    continue
                m = name.match(cells[0])
                if m:
                    claimed.add(m.group(1))
            ghosts = sorted(c for c in claimed if c not in wired)
            self.assertEqual(ghosts, [],
                             f"{doc.name}: таблица хуков обещает непроведённые хуки: {ghosts}")

    def test_no_doc_launches_with_removed_flags(self):
        """В gigacode 26.9 флагов `--experimental-hooks` и `-y` нет — сессия с ними не стартует.

        Фикс 016-C1 правил README/INSTALL/user-guide/troubleshooting/pipeline-technical/FORGE,
        а DEPLOY.md и ДЕПЛОИМЫЕ SKILL.md роутера и forgefix продолжали велеть «запускай ВСЕГДА с
        --experimental-hooks» (боевой прогон v0.4.6, H-F1/F2): модель в сессии советовала бы
        команду, валящую сессию. Скан — по всем докам, которые читают человек и модель.
        Упоминание флага как несуществующего разрешено; запрещена команда запуска с ним."""
        launch = re.compile(r"gigacode\s+--experimental-hooks|gigacode\b[^`\n]*\s-y\b")
        docs = [REPO / "README.md", REPO / "INSTALL.md", FORGE, HOOKS / "DEPLOY.md",
                *sorted((REPO / "docs").glob("*.md")), *sorted((REPO / "skills").glob("*/SKILL.md")),
                *sorted((REPO / "commands").glob("*.md"))]
        bad = []
        for doc in docs:
            for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                if launch.search(line):
                    bad.append(f"{doc.relative_to(REPO)}:{i}: {line.strip()[:100]}")
        self.assertEqual(bad, [], "доки велят запуск с флагами, которых нет в 26.9")

    def test_every_wired_hook_in_forge_roster(self):
        """Каждый проведённый в settings хук обязан присутствовать в ростер-таблице FORGE.md.

        Историческая дыра: pii-boundary/sod-enforcer были проведены не на том событии, а
        inline-phase-guard вовсе отсутствовал в ростер-таблице — цепочечные тесты выше это
        не ловили (они сверяют только §«Структура хуков», не таблицу). Этот тест пинит таблицу.
        """
        block = json.loads(SETTINGS.read_text(encoding="utf-8")).get("hooks", {})
        wired: set[str] = set()
        for groups in block.values():
            for group in groups:
                for h in group.get("hooks", []):
                    b = _basename(h.get("command", ""))
                    if b:
                        wired.add(b)
        forge = FORGE.read_text(encoding="utf-8")
        roster = set(re.findall(r"^\|\s*`([\w-]+)\.py`", forge, re.MULTILINE))
        missing = sorted(h for h in wired if h not in roster)
        self.assertEqual(missing, [],
                         f"Хуки проведены в settings, но отсутствуют в ростер-таблице FORGE.md: {missing}")


class TestSkillsRegistryConsistency(unittest.TestCase):
    """Две копии SKILLS-REGISTRY.md не расходятся, и таблица хуков покрывает ВСЕ хуки.

    Обе находки — из аудита харнеса 2026-09-28. Копии (`forge/SKILLS-REGISTRY.md` и
    `forge/skills/SKILLS-REGISTRY.md`) разъехались по содержанию, и обе едут в целевой
    проект — какая из них «правда», было неопределено. Таблица control-plane при этом
    описывала 11 хуков из 15: не хватало ровно тех, что держат самые дорогие инварианты
    (`state-write-guard`), и тех, без которых не снимается фазовый блок
    (`grounding-evidence`) и не собирается скоуп отката (`file-journal`).
    """

    ROOT_COPY = REPO / "SKILLS-REGISTRY.md"
    SKILLS_COPY = REPO / "skills" / "SKILLS-REGISTRY.md"

    def test_copies_are_identical(self):
        a = self.ROOT_COPY.read_text(encoding="utf-8")
        b = self.SKILLS_COPY.read_text(encoding="utf-8")
        self.assertEqual(a, b,
                         "копии SKILLS-REGISTRY.md разошлись — обе едут в целевой проект, "
                         "выбор становится недетерминированным")

    def test_hook_table_covers_every_hook(self):
        """Каждый хук из settings.hooks.json упомянут в таблице control-plane реестра."""
        registry = self.ROOT_COPY.read_text(encoding="utf-8")
        table = registry.split("## Control-plane")[-1]
        wired = set()
        block = json.loads((HOOKS / "settings.hooks.json").read_text(encoding="utf-8"))["hooks"]
        for groups in block.values():
            for g in groups:
                for h in g.get("hooks", []):
                    name = _basename(h.get("command", ""))
                    if name:
                        wired.add(name)
                    
        missing = sorted(n for n in wired if n not in table)
        self.assertFalse(missing,
                         f"хуки подключены, но не описаны в реестре: {missing}")


class TestQualityKeysAreRegistered(unittest.TestCase):
    """Каждый ключ quality.*, который ЧИТАЕТ хук, обязан быть в params-registry.

    Иначе получается тупик: хук читает ключ и в DENY-баннере советует
    `config.py set quality.<key> …`, а cmd_set не находит его в реестре и отдаёт exit 3.
    Прямая правка policy.json при этом заблокирована state-write-guard — то есть
    санкционированного пути разблокировки не существует вовсе. Так жили
    quality.block_jpa_test и quality.tdd_integration_skip.
    """

    REGISTRY = REPO / "skills/config-helper/references/params-registry.json"
    # quality_cfg.get("key") / cfg.get("quality", {}).get("key")
    _KEY_RE = re.compile(r"""quality_cfg\.get\(\s*["'](\w+)["']""")

    def test_hook_quality_keys_present_in_registry(self):
        registered = {
            e["id"].split(".", 1)[1]
            for e in json.loads(self.REGISTRY.read_text(encoding="utf-8"))["params"]
            if e["id"].startswith("quality.")
        }
        missing = {}
        for hook in sorted(HOOKS.glob("*.py")):
            if hook.name.startswith("test_"):
                continue
            for key in self._KEY_RE.findall(hook.read_text(encoding="utf-8")):
                if key not in registered:
                    missing.setdefault(key, []).append(hook.name)
        self.assertEqual(
            missing, {},
            f"ключи quality.* читаются хуками, но отсутствуют в params-registry: {missing}. "
            f"`config.py set` вернёт на них exit 3, а прямая правка policy.json "
            f"заблокирована state-write-guard → ручка недостижима")


if __name__ == "__main__":
    unittest.main()
