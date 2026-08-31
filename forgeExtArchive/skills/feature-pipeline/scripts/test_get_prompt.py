#!/usr/bin/env python3
"""
test_get_prompt.py — контракт извлекателя секций subagent-prompts.md.

Гарантии:
  1. Каждый id секции, на который ссылается SKILL.md (как `§X.Y` или `get_prompt.py X.Y`),
     резолвится в непустую секцию (ловит рассинхрон ссылок ↔ секций).
  2. Извлечённая секция начинается со своего заголовка и НЕ затекает в следующую
     (проверка границ: §4.0 не содержит заголовок §4.0a).
  3. --list печатает доступные id; несуществующая секция → exit 1.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GET_PROMPT = SCRIPT_DIR / "get_prompt.py"
SKILL_MD = SCRIPT_DIR.parent / "SKILL.md"
PROMPTS_MD = SCRIPT_DIR.parent / "references" / "subagent-prompts.md"

# §4.0, §4.0a, §5, §7.3  и  get_prompt.py 4.0 / 7.3.
# Собираем id ТОЛЬКО со строк, где речь о subagent-prompts.md / get_prompt.py, —
# иначе ловятся внутренние §-ссылки SKILL.md на свои же секции (§5b, §6 …).
_ID_TOKEN = r"\d+(?:\.\d+)?[a-z]?"
_SECTION_RE = re.compile(rf"§\s*({_ID_TOKEN})")
_GETPROMPT_RE = re.compile(rf"get_prompt\.py\s+({_ID_TOKEN})")


def _run(*args):
    return subprocess.run(
        [sys.executable, str(GET_PROMPT), *args],
        capture_output=True,
        text=True,
    )


def referenced_ids():
    """id секций subagent-prompts.md, на которые ссылается SKILL.md.

    Берём только строки, где упомянут `subagent-prompts` или `get_prompt.py`, чтобы не
    спутать с внутренними §-ссылками SKILL.md на собственные секции (§5b, §6, §0.5 …).
    """
    ids = set()
    sources = [SKILL_MD] + sorted((SKILL_MD.parent / "references" / "phases").glob("*.md"))
    for src in sources:  # диспетчер + фазовые брифы (контракты вызываются из брифов)
        for line in src.read_text(encoding="utf-8").splitlines():
            if "get_prompt.py" in line:
                ids.update(_GETPROMPT_RE.findall(line))
            if "subagent-prompts" in line:
                ids.update(_SECTION_RE.findall(line))
    return sorted(ids)


class TestGetPrompt(unittest.TestCase):
    def test_every_referenced_section_resolves(self):
        ids = referenced_ids()
        self.assertTrue(ids, "в SKILL.md не нашлось ни одной ссылки на секцию — проверь regex")
        for sid in ids:
            with self.subTest(section=sid):
                res = _run(sid)
                self.assertEqual(res.returncode, 0, f"§{sid}: exit {res.returncode}\n{res.stderr}")
                self.assertTrue(res.stdout.strip(), f"§{sid}: пустой вывод")
                first = res.stdout.splitlines()[0]
                self.assertRegex(first, r"^#{1,6}\s", f"§{sid}: вывод не начинается с заголовка")
                # заголовок содержит запрошенный id (с учётом «5.» → «5»)
                self.assertIn(sid.rstrip("."), first)

    def test_section_does_not_bleed_into_next(self):
        # §4.0 не должна содержать заголовок §4.0a
        out = _run("4.0").stdout
        self.assertIn("## 4.0 ", out)
        self.assertNotIn("## 4.0a ", out)
        # §5 (спецадаптер) не должна затекать в §7 (Судьи)
        out5 = _run("5").stdout
        self.assertNotIn("7.1 eval-judge", out5)

    def test_list_outputs_known_ids(self):
        res = _run("--list")
        self.assertEqual(res.returncode, 0)
        ids = set(res.stdout.split())
        for expected in ("4.0", "4.0a", "5", "7.3", "7.7"):
            self.assertIn(expected, ids, f"--list не содержит {expected}")

    def test_missing_section_exits_nonzero(self):
        res = _run("9.9")
        self.assertEqual(res.returncode, 1)
        self.assertIn("не найдена", res.stderr)

    def test_prompts_md_exists(self):
        self.assertTrue(PROMPTS_MD.exists(), f"нет {PROMPTS_MD}")


if __name__ == "__main__":
    unittest.main()
