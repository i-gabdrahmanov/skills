#!/usr/bin/env python3
"""Тесты set_criticality.py — связь критичность → auto_max_risk детерминирована (v2: manifest)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import set_criticality as sc

SKILL = "feature-pipeline"
FEATURE = "f1"


class TestDeriveRisk(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(sc.derive_risk("low"), "R2")
        self.assertEqual(sc.derive_risk("medium"), "R1")
        self.assertEqual(sc.derive_risk("high"), "R0")

    def test_case_insensitive_and_whitespace(self):
        self.assertEqual(sc.derive_risk("  High "), "R0")
        self.assertEqual(sc.derive_risk("MEDIUM"), "R1")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            sc.derive_risk("critical")
        with self.assertRaises(ValueError):
            sc.derive_risk("")

    def test_map_matches_three_levels(self):
        # Карта покрывает ровно low/medium/high (никаких лишних/недостающих уровней)
        self.assertEqual(set(sc.CRITICALITY_TO_RISK), {"low", "medium", "high"})


class TestApply(unittest.TestCase):
    def test_writes_both_fields(self):
        # v2: манифест хранит решения под decisions.* (не autonomy.*) — per-feature,
        # не глобальный pipeline.json.
        m = {"version": 2, "skill": SKILL, "feature": FEATURE,
             "decisions": {"mode_task": "fix"}, "inputs": {}}
        out = sc.apply(m, "high")
        self.assertEqual(out["decisions"]["criticality"], "high")
        self.assertEqual(out["decisions"]["auto_max_risk"], "R0")
        # не затирает соседние поля decisions (mode_task и пр.)
        self.assertEqual(out["decisions"]["mode_task"], "fix")
        # не трогает inputs
        self.assertEqual(out["inputs"], {})

    def test_low_sets_r2(self):
        m = {"decisions": {"mode_task": "fix"}}
        sc.apply(m, "low")
        self.assertEqual(m["decisions"]["auto_max_risk"], "R2")
        self.assertEqual(m["decisions"]["mode_task"], "fix")

    def test_creates_decisions_if_missing(self):
        # свежий манифест без decisions — apply создаёт секцию, а не падает на KeyError
        m = {"version": 2, "skill": SKILL, "feature": FEATURE}
        sc.apply(m, "medium")
        self.assertEqual(m["decisions"]["criticality"], "medium")
        self.assertEqual(m["decisions"]["auto_max_risk"], "R1")

    def test_unknown_criticality_does_not_mutate(self):
        # Невалидная критичность: ValueError ДО записи (решение не должно остаться полу-обновлённым)
        m = {"decisions": {"criticality": "low", "auto_max_risk": "R2"}}
        with self.assertRaises(ValueError):
            sc.apply(m, "bogus")
        self.assertEqual(m["decisions"]["criticality"], "low")
        self.assertEqual(m["decisions"]["auto_max_risk"], "R2")


class TestMain(unittest.TestCase):
    def _run(self, criticality, root, *, skill=SKILL, feature=FEATURE):
        argv = sys.argv
        sys.argv = ["set_criticality.py", "--criticality", criticality,
                    "--skill", skill, "--feature", feature,
                    "--project-root", str(root)]
        try:
            return sc.main()
        finally:
            sys.argv = argv

    def _seed_manifest(self, root, *, body=None):
        """Сидит v2-манифест под root/ground/statements/<skill>/<feature>/."""
        d = root / "ground" / "statements" / SKILL / FEATURE
        d.mkdir(parents=True, exist_ok=True)
        mp = d / "manifest.json"
        mp.write_text(json.dumps(body if body is not None else {
            "version": 2, "skill": SKILL, "feature": FEATURE,
            "inputs": {}, "decisions": {},
        }), encoding="utf-8")
        return mp

    def test_main_writes_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = self._seed_manifest(root)
            rc = self._run("high", root)
            self.assertEqual(rc, 0)
            m = json.loads(mp.read_text(encoding="utf-8"))
            self.assertEqual(m["decisions"]["criticality"], "high")
            self.assertEqual(m["decisions"]["auto_max_risk"], "R0")

    def test_main_missing_manifest_exits_2(self):
        # Нет манифеста → exit 2 и подсказка про init.py
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(self._run("low", root), 2)

    def test_main_bad_criticality(self):
        # Невалидная критичность при наличии манифеста — exit 2, манифест НЕ затронут
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = self._seed_manifest(root)
            before = mp.read_text(encoding="utf-8")
            self.assertEqual(self._run("nope", root), 2)
            self.assertEqual(mp.read_text(encoding="utf-8"), before)

    def test_main_requires_skill_and_feature(self):
        # Без --skill/--feature argparse должен ругаться (exit != 0)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed_manifest(root)
            for missing in ("--skill", "--feature"):
                argv = sys.argv
                sys.argv = ["set_criticality.py", "--criticality", "high",
                            "--feature" if missing == "--skill" else "--skill",
                            FEATURE if missing == "--skill" else SKILL,
                            "--project-root", str(root)]
                # NB: оба флага required, поэтому отсутствие любого из них даёт SystemExit(2)
                # от argparse. Проверяем, что main() отваливается раньше, чем трогать манифест.
                mp = root / "ground" / "statements" / SKILL / FEATURE / "manifest.json"
                before = mp.read_text(encoding="utf-8")
                with self.assertRaises(SystemExit):
                    sc.main()
                sys.argv = argv
                self.assertEqual(mp.read_text(encoding="utf-8"), before)

    def test_main_atomic_write_no_partial_on_error(self):
        # Если manifest.json битый — exit 2, никакого .tmp не остаётся
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = self._seed_manifest(root)
            mp.write_text("{ this is not json", encoding="utf-8")
            self.assertEqual(self._run("high", root), 2)
            tmp = mp.with_suffix(mp.suffix + ".tmp")
            self.assertFalse(tmp.exists())

    def test_main_preserves_other_manifest_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp = self._seed_manifest(root, body={
                "version": 2, "skill": SKILL, "feature": FEATURE,
                "inputs": {"story": "STOR-1"},
                "decisions": {"mode_task": "fix", "criticality": "low", "auto_max_risk": "R2"},
                "steps": [{"id": "01-x", "status": "pending"}],
            })
            self._run("high", root)
            m = json.loads(mp.read_text(encoding="utf-8"))
            self.assertEqual(m["inputs"], {"story": "STOR-1"})
            self.assertEqual(m["decisions"]["mode_task"], "fix")  # не затерто
            self.assertEqual(m["decisions"]["criticality"], "high")
            self.assertEqual(m["decisions"]["auto_max_risk"], "R0")
            self.assertEqual(m["steps"], [{"id": "01-x", "status": "pending"}])


if __name__ == "__main__":
    unittest.main()
