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
        settings = _settings_chain(lambda m: m == "^Bash$")
        forge = _forge_list("**PreToolUse `^Bash$` — sequential:**")
        self.assertTrue(settings, "не нашёл ^Bash$ цепочку в settings.hooks.json")
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


if __name__ == "__main__":
    unittest.main()
