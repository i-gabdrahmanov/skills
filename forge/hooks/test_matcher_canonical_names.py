#!/usr/bin/env python3
"""test_matcher_canonical_names.py — пинит, что matcher-ы settings.hooks.json матчат
КАНОНИЧЕСКИЕ имена инструментов рантайма, а не Claude-нотацию.

Почему тест существует (BLOCKER-0). Рантайм (qwen-code / форк GigaCode) выбирает хуки так:
`createExecutionPlan` → `matchesToolName(matcher, toolName)` → `new RegExp(matcher).test(toolName)`
(packages/core/src/hooks/hookPlanner.ts), где `toolName` = `canonicalToolName(rawName)`
(coreToolScheduler.ts). Канон-имена: `run_shell_command`, `write_file`, `edit`, `notebook_edit`,
`read_file`, `web_fetch` (TOOL_NAME_ALIASES в permissions/rule-parser.ts). Claude-имена
`Bash`/`Write`/`Edit` — лишь ВХОДНЫЕ алиасы, целью матчинга они не бывают.

Историческая дыра: матчеры были `^Bash$` и `(Write|Edit|WriteFile|NotebookEdit)` → ни один
блокирующий хук не попадал в план (весь deny-first control-plane молчал). Eval-набор
(evals/run-evals.py) дёргает скрипты хуков напрямую JSON'ом, минуя рантайм-матчинг, поэтому
баг им не ловился. Этот тест воспроизводит именно рантайм-семантику (JS `RegExp.test` ≈
Python `re.search`) и требует, чтобы целевые цепочки матчили канон-имена.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
SETTINGS = HOOKS / "settings.hooks.json"

# Канон-имена инструментов рантайма (TOOL_NAME_ALIASES → значения).
CANON_SHELL = "run_shell_command"
CANON_WRITE = "write_file"
CANON_EDIT = "edit"
CANON_NOTEBOOK = "notebook_edit"
CANON_READ = "read_file"
CANON_FETCH = "web_fetch"


def _basename(command: str) -> str | None:
    m = re.search(r"([\w-]+)\.py\b", command)
    return m.group(1) if m else None


def _js_test(matcher: str, tool_name: str) -> bool:
    """Мимикрия JS `new RegExp(matcher).test(tool_name)` (unanchored partial ≈ re.search)."""
    return re.search(matcher, tool_name) is not None


def _groups(event: str) -> list[dict]:
    block = json.loads(SETTINGS.read_text(encoding="utf-8")).get("hooks", {})
    return block.get(event, [])


def _group_with_hook(event: str, hook_basename: str) -> dict | None:
    for g in _groups(event):
        for h in g.get("hooks", []):
            if _basename(h.get("command", "")) == hook_basename:
                return g
    return None


class TestMatcherCanonicalNames(unittest.TestCase):
    def test_bash_chain_matches_canonical_shell(self):
        g = _group_with_hook("PreToolUse", "destructive-blocker")
        self.assertIsNotNone(g, "не нашёл Bash-цепочку (по destructive-blocker)")
        matcher = g.get("matcher", "")
        self.assertTrue(
            _js_test(matcher, CANON_SHELL),
            f"matcher {matcher!r} не матчит канон-имя {CANON_SHELL!r} — блок-хуки Bash-цепочки "
            f"не попадут в план (регрессия BLOCKER-0).",
        )

    def test_write_chain_matches_canonical_edits(self):
        g = _group_with_hook("PreToolUse", "tdd-guard")
        self.assertIsNotNone(g, "не нашёл Write/Edit-цепочку (по tdd-guard)")
        matcher = g.get("matcher", "")
        for name in (CANON_WRITE, CANON_EDIT, CANON_NOTEBOOK):
            self.assertTrue(
                _js_test(matcher, name),
                f"matcher {matcher!r} не матчит канон-имя {name!r} — tdd/eval/sod/gate на записи "
                f"не сработают (регрессия BLOCKER-0).",
            )

    def test_write_chain_does_not_overmatch_reads(self):
        g = _group_with_hook("PreToolUse", "tdd-guard")
        matcher = g.get("matcher", "")
        for name in (CANON_READ, CANON_SHELL):
            self.assertFalse(
                _js_test(matcher, name),
                f"matcher {matcher!r} ложно матчит {name!r} — Write-цепочка перехватывает чужой инструмент.",
            )

    def test_posttooluse_read_chain_matches_canonical(self):
        g = _group_with_hook("PostToolUse", "prompt-guard")
        self.assertIsNotNone(g, "не нашёл PostToolUse-цепочку prompt-guard")
        matcher = g.get("matcher", "")
        for name in (CANON_READ, CANON_FETCH, CANON_SHELL):
            self.assertTrue(
                _js_test(matcher, name),
                f"matcher {matcher!r} не матчит {name!r} — post-read injection scan не сработает.",
            )

    def test_dead_claude_only_matchers_are_gone(self):
        """Прямой регресс: старые Claude-only матчеры молча не матчили канон-имена."""
        self.assertFalse(_js_test("^Bash$", CANON_SHELL))
        self.assertFalse(_js_test("(Write|Edit|WriteFile|NotebookEdit)", CANON_EDIT))
        self.assertFalse(_js_test("(Write|Edit|WriteFile|NotebookEdit)", CANON_WRITE))
        # …и что в актуальных settings их уже нет как единственной формы.
        raw = SETTINGS.read_text(encoding="utf-8")
        self.assertNotIn('"matcher": "^Bash$"', raw)
        self.assertNotIn('"matcher": "(Write|Edit|WriteFile|NotebookEdit)"', raw)


if __name__ == "__main__":
    unittest.main()
