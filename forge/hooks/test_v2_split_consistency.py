#!/usr/bin/env python3
"""test_v2_split_consistency.py — разделение конфига v1→v2 читается тем же, чем пишется.

Раскладка v2: project-wide → ground/policy.json, per-feature (inputs/decisions/steps) →
ground/statements/<skill>/<feature>/manifest.json. Legacy v1 (ground/pipeline.json)
дочитывается через fallback.

Класс бага, который тут пинится: **писатель и читатель расходятся по файлу**. Это не падает
и не логируется — значение просто «не находится», и гейт, который на нём висит, либо молчит,
либо блокирует навсегда.

Прецедент (аудит 2026-08-28): `risk_ladder.criticality_set()` и `auto_max_risk()` читали
`autonomy.*` из `pipeline_cfg()`, то есть ТОЛЬКО из legacy ground/pipeline.json, тогда как
в v2 это `manifest.decisions.*`. На v2-native проекте получался дедлок: gate-guard блокировал
любую R2+ запись с требованием выбрать критичность, `set_criticality.py` писал её в
`manifest.decisions.criticality` — ровно туда, куда указывал текст отказа, — а гейт её не
видел и блокировал снова. Выхода не было.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS))

import risk_ladder as R  # noqa: E402


def _v2_project(tmp: Path, decisions: dict, inputs: dict | None = None) -> None:
    """v2-native: policy.json есть, pipeline.json НЕТ, решения в манифесте."""
    ground = tmp / "ground"
    (ground / "statements" / "forgefix" / "F1").mkdir(parents=True, exist_ok=True)
    (ground / "policy.json").write_text(
        json.dumps({"project": {"build_system": "gradle"}}), encoding="utf-8")
    (ground / "statements" / "forgefix" / "F1" / "manifest.json").write_text(
        json.dumps({
            "version": 2, "skill": "forgefix", "feature": "F1",
            "inputs": inputs or {}, "decisions": decisions,
            "steps": [{"id": "fix-diag", "status": "pending"}],
        }), encoding="utf-8")


def _v1_project(tmp: Path, autonomy: dict) -> None:
    """v1-legacy: только pipeline.json с top-level autonomy.*"""
    ground = tmp / "ground"
    ground.mkdir(parents=True, exist_ok=True)
    (ground / "pipeline.json").write_text(
        json.dumps({"project": {"build_system": "gradle"}, "autonomy": autonomy,
                    "features": {"forgefix/F1": {"skill": "forgefix", "steps": []}}}),
        encoding="utf-8")


class TestDecisionsReadableInV2(unittest.TestCase):
    """Решения фичи в v2 живут в манифесте — читатели обязаны их там видеть."""

    def test_criticality_set_sees_manifest_decision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _v2_project(root, {"criticality": "low", "auto_max_risk": "R2"})
            self.assertTrue(
                R.criticality_set(root),
                "criticality_set не видит manifest.decisions.criticality → gate-guard "
                "заблокирует R2+ навсегда: он требует выбрать критичность, а выбранную "
                "не признаёт (дедлок)",
            )

    def test_auto_max_risk_honours_manifest_decision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _v2_project(root, {"criticality": "low", "auto_max_risk": "R2"})
            self.assertEqual(
                R.auto_max_risk(root), "R2",
                "auto_max_risk игнорирует выбор фичи в manifest.decisions и падает на "
                "глобальный дефолт",
            )

    def test_criticality_unset_in_v2_is_still_false(self):
        """Контроль: пустые decisions — по-прежнему «не выбрана» (гейт обязан сработать)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _v2_project(root, {})
            self.assertFalse(R.criticality_set(root))


class TestLegacyV1StillWorks(unittest.TestCase):
    """Dual-read: v1-проекты продолжают работать без миграции."""

    def test_v1_autonomy_still_read(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _v1_project(root, {"criticality": "medium", "auto_max_risk": "R2"})
                self.assertTrue(R.criticality_set(root), "сломан legacy-fallback для v1")
                self.assertEqual(R.auto_max_risk(root), "R2")


class TestAutoMaxCeilingHolds(unittest.TestCase):
    """Кламп R3 держится и после перехода на config_get — иначе манифест открывал бы R4/R5."""

    def test_manifest_cannot_raise_auto_above_ceiling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _v2_project(root, {"criticality": "low", "auto_max_risk": "R5"})
            self.assertEqual(
                R.auto_max_risk(root), "R3",
                "manifest.decisions.auto_max_risk=R5 обязан клампиться потолком R3 — "
                "иначе прямая правка манифеста открывает авто-проход для R4/R5",
            )


class TestWriterReaderRoutingAgrees(unittest.TestCase):
    """Реестр параметров (писатель) и route_path/config_get (читатель) — одна маршрутизация."""

    def test_registry_file_key_matches_path_prefix(self):
        reg_path = (HOOKS.parent / "skills" / "config-helper" / "references"
                    / "params-registry.json")
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        params = reg["params"] if isinstance(reg, dict) and "params" in reg else reg
        bad = []
        for p in params:
            path, fk = p.get("path", ""), p.get("file")
            per_feature = path.startswith(("inputs.", "decisions."))
            if per_feature and fk != "manifest":
                bad.append(f"{p.get('id')}: path={path} per-feature, но file={fk} "
                           f"(пишет в policy, читается из manifest)")
            if not per_feature and fk == "manifest":
                bad.append(f"{p.get('id')}: path={path} project-wide, но file=manifest "
                           f"(пишет в manifest, читается из policy)")
        self.assertEqual(bad, [], "писатель и читатель разойдутся по файлу:\n  "
                         + "\n  ".join(bad))

    def test_gate_required_decisions_are_writable(self):
        """Ключ, которого требует fail-closed гейт, обязан быть записываем через config.py."""
        pol = json.loads((HOOKS / "risk-policy.json").read_text(encoding="utf-8"))
        reg_path = (HOOKS.parent / "skills" / "config-helper" / "references"
                    / "params-registry.json")
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        params = reg["params"] if isinstance(reg, dict) and "params" in reg else reg
        known = {p.get("path") for p in params}
        missing = []
        for sect in ("required_decisions", "required_decisions_on_close"):
            for step, keys in (pol.get(sect) or {}).items():
                if step.startswith("_"):
                    continue
                for k in keys:
                    if k not in known:
                        missing.append(f"{sect}/{step}: {k}")
        self.assertEqual(missing, [], "гейт требует ключ, которого нет в реестре — "
                         "оператор не сможет его записать, блок навсегда:\n  "
                         + "\n  ".join(missing))


if __name__ == "__main__":
    unittest.main()
