#!/usr/bin/env python3
"""Пиннинг фазовых брифов: SKILL.md — диспетчер, фазовые инструкции — references/phases/.

Дрейф ловим с двух сторон: (a) каждый brief из DEFAULT_PHASES существует и не пустой;
(b) фазовый контент не возвращается в SKILL.md (заголовки «## N. Фаза …» запрещены);
(c) каждый бриф упоминает механизм закрытия шага (update.py / run_judge / record_gate) —
бриф без гейта закрытия — обрубок.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import resolve_phases as rp


class TestPhaseBriefs(unittest.TestCase):
    def test_all_briefs_exist_and_nonempty(self):
        for phase in rp.DEFAULT_PHASES:
            brief = SKILL_DIR / phase["brief"]
            self.assertTrue(brief.exists(), f"нет брифа {phase['brief']} для фазы {phase['id']}")
            self.assertGreater(len(brief.read_text(encoding="utf-8")), 500,
                               f"бриф {phase['brief']} подозрительно короткий")

    def test_skill_md_has_no_phase_sections(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        hits = re.findall(r"^##+ (?:[3-9]|10)[a-c]?\.? Фаза", text, re.M)
        self.assertEqual(hits, [],
                         f"фазовые секции вернулись в SKILL.md (место им в references/phases/): {hits}")

    def test_skill_md_is_thin(self):
        n = len((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 700, f"SKILL.md разросся до {n} строк — выноси в брифы")

    def test_briefs_mention_closing_gate(self):
        for phase in rp.DEFAULT_PHASES:
            text = (SKILL_DIR / phase["brief"]).read_text(encoding="utf-8")
            self.assertTrue(
                any(k in text for k in ("update.py", "run_judge", "record_gate")),
                f"бриф {phase['brief']} не упоминает механизм закрытия шага")

    def test_briefs_reference_common_rules(self):
        # шапка каждого брифа должна отсылать к общим инвариантам SKILL.md
        for phase in rp.DEFAULT_PHASES:
            text = (SKILL_DIR / phase["brief"]).read_text(encoding="utf-8")
            self.assertIn("SKILL.md §0.6", text,
                          f"бриф {phase['brief']} без отсылки к правилу ре-итерации")

    def test_resolved_phases_carry_brief(self):
        # поле brief доезжает до вывода resolve_phases (без реального pipeline.json —
        # проверяем на структуре DEFAULT_PHASES)
        for phase in rp.DEFAULT_PHASES:
            self.assertTrue(phase.get("brief", "").startswith("references/phases/"), phase["id"])


if __name__ == "__main__":
    unittest.main()
