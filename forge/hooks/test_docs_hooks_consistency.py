#!/usr/bin/env python3
"""test_docs_hooks_consistency.py — пинит «доки ↔ задеплоено» для control-plane.

FORGE.md объявлен источником правды, но §«Структура хуков» исторически расходилась с
settings.hooks.json (не было sod-enforcer/subagent-enforcer; eval-guard документирован, но не
подключён). Этот тест парсит упорядоченные цепочки из обоих и требует совпадения.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
REPO = HOOKS.parent
SETTINGS = HOOKS / "settings.hooks.json"
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
        self.assertTrue(settings, "не нашёл run_shell_command цепочку в settings.hooks.json")
        self.assertEqual(forge, settings,
                         f"FORGE.md §Структура хуков (Bash) разошлась с settings: forge={forge} settings={settings}")

    def test_write_edit_chain_matches(self):
        settings = _settings_chain(lambda m: "Write" in m and "Edit" in m)
        forge = _forge_list("**PreToolUse `(Write|Edit)` — sequential:**")
        self.assertTrue(settings, "не нашёл Write|Edit цепочку в settings.hooks.json")
        self.assertEqual(forge, settings,
                         f"FORGE.md §Структура хуков (Write|Edit) разошлась с settings: forge={forge} settings={settings}")

    def test_removed_hooks_absent(self):
        raw = SETTINGS.read_text(encoding="utf-8")
        self.assertNotIn("subagent-enforcer", raw, "subagent-enforcer должен быть удалён из settings")
        self.assertNotIn("gate-resolver", raw, "gate-resolver должен быть удалён из settings")

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


if __name__ == "__main__":
    unittest.main()
