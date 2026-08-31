#!/usr/bin/env python3
"""test_pipeline_phases.py — п.8 KIDPPRB-9254: live_phase_decision учитывает enabled_by/skip_if.

Раньше live_phase_decision возвращал снимок со ВСЕМИ фазами из manifest, даже если
resolve_phases их отключил (quality.eval_enabled=false → 02-eval-plan пропал из
разрешённого списка, а current_phase на ней висел). Чинка: live_phase_decision теперь
применяет phase_eligibility (из _phase_eligibility.py — общий DRY-хелпер с resolve_phases).

Тесты пишутся на pytest-стиле с monkeypatch.setenv, как и просили в задаче:
  - test_live_phase_decision_skips_disabled_by_env
  - test_live_phase_decision_skips_when_skip_if_true
  - test_live_phase_decision_runs_when_enabled_and_skip_if_false
  - test_resolve_phases_and_live_phase_decision_agree_on_disabled
  - test_resolve_phases_and_live_phase_decision_agree_on_skip_if

Запуск: python3 -m pytest skills/feature-pipeline/scripts/test_pipeline_phases.py -v
       python3 skills/feature-pipeline/scripts/test_pipeline_phases.py   (legacy main)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# pytest нужен для monkeypatch; unittest.mock.patch.dict — fallback, если pytest нет.
try:
    import pytest  # noqa: F401
    _HAS_PYTEST = True
except ImportError:  # noqa: BLE001
    _HAS_PYTEST = False

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import pipeline_phases as pp  # noqa: E402
import resolve_phases as rp  # noqa: E402


# ── Хелперы ──────────────────────────────────────────────────────────
def _manifest(steps_status: dict) -> dict:
    """Минимальный manifest c парой (id → status) и шагами по фазам из MAIN_PHASES."""
    return {
        "pipeline_id": "test",
        "feature": "test-feature",
        "steps": [
            {"id": sid, "status": st, "title": sid} for sid, st in steps_status.items()
        ],
    }


def _project_root_with(pipeline_dict: dict) -> Path:
    """tmp_path с ground/pipeline.json — для проверки загрузки конфига с диска."""
    td = tempfile.mkdtemp(prefix="pipeline_phases_test_")
    p = Path(td)
    (p / "ground").mkdir(parents=True, exist_ok=True)
    (p / "ground" / "pipeline.json").write_text(json.dumps(pipeline_dict), encoding="utf-8")
    return p


def _phase(decision: dict, phase_id: str) -> dict:
    """Фаза из live-снимка по id (или {} если нет — тест должен это заметить)."""
    return next((ph for ph in decision["phases"] if ph["id"] == phase_id), {})


# ── Базовые кейсы: live_phase_decision учитывает enabled_by / skip_if ─
class TestLivePhaseDecisionEnabledBy:
    """enabled_by: фаза с неудовлетворённым условием → status=skipped, current_phase прыгает."""

    def test_live_phase_decision_skips_disabled_by_env(self, monkeypatch):
        # FEATURE_SDD_ENABLED=0 — фаза 02-sdd отключена через phases_override.enabled_by="env:..."
        monkeypatch.setenv("FEATURE_SDD_ENABLED", "0")
        pipeline = {
            "jira": {"enabled": True},
            "quality": {"tdd": True, "eval_enabled": True},
            "phases_override": [{"id": "02-sdd", "enabled_by": "env:FEATURE_SDD_ENABLED"}],
        }
        manifest = _manifest({
            "01-grounding": "completed",
            "02-sdd": "pending",
            "02-design": "pending",
        })
        d = pp.live_phase_decision(manifest, pipeline=pipeline)

        sdd = _phase(d, "02-sdd")
        assert sdd.get("status") == "skipped", (
            f"02-sdd должен быть skipped (env FEATURE_SDD_ENABLED=0), не {sdd.get('status')!r}"
        )
        assert sdd.get("skip_reason", "").startswith("disabled_by:"), (
            f"skip_reason должен быть 'disabled_by: ...', не {sdd.get('skip_reason')!r}"
        )
        # current_phase перепрыгнул через отключённую 02-sdd на 02-design
        assert d["current_phase"] == "02-design", (
            f"current_phase должен перепрыгнуть через skipped фазу на 02-design, "
            f"не на {d['current_phase']!r}"
        )

    def test_live_phase_decision_skips_disabled_by_env_unset(self, monkeypatch):
        """env не задан → выражение env:VAR = False → фаза skipped."""
        monkeypatch.delenv("FEATURE_SDD_ENABLED", raising=False)
        pipeline = {
            "phases_override": [{"id": "02-sdd", "enabled_by": "env:FEATURE_SDD_ENABLED"}],
        }
        manifest = _manifest({"01-grounding": "completed", "02-sdd": "pending"})
        d = pp.live_phase_decision(manifest, pipeline=pipeline)
        sdd = _phase(d, "02-sdd")
        assert sdd.get("status") == "skipped"
        assert "env:FEATURE_SDD_ENABLED" in sdd.get("skip_reason", "")

    def test_live_phase_decision_skips_disabled_by_jpath(self):
        """enabled_by=jpath в pipeline.json — отключается через quality.eval_enabled=false."""
        pipeline = {"jira": {"enabled": True}, "quality": {"eval_enabled": False}}
        manifest = _manifest({
            "01-grounding": "completed",
            "02-sdd": "completed",
            "02-design": "completed",
            "02-eval-plan": "pending",   # в DEFAULT_PHASES enabled_by="quality.eval_enabled"
            "03-jira": "pending",
        })
        d = pp.live_phase_decision(manifest, pipeline=pipeline)
        ep = _phase(d, "02-eval-plan")
        assert ep.get("status") == "skipped", (
            f"02-eval-plan (eval_enabled=false) должен быть skipped, не {ep.get('status')!r}"
        )
        assert "disabled_by" in ep.get("skip_reason", "")
        assert d["current_phase"] == "03-jira", (
            f"current_phase должен перепрыгнуть 02-eval-plan на 03-jira, не {d['current_phase']!r}"
        )

    def test_live_phase_decision_disabled_by_gate_flag(self):
        """enabled_by='gates.X.Y' — фича-флаг через gates."""
        pipeline = {}
        gates = {"gates": {"parallel_build": {"enabled": True}}}
        manifest = _manifest({
            "01-grounding": "completed",
            "02-sdd": "pending",
            "02-design": "pending",
        })
        # Фаза с явным gate-флагом
        phases_override = [{"id": "02-sdd", "enabled_by": "gates.parallel_build"}]
        pipeline["phases_override"] = phases_override
        d = pp.live_phase_decision(manifest, pipeline=pipeline, gates=gates)
        sdd = _phase(d, "02-sdd")
        assert sdd.get("status") == "completed" or sdd.get("status") == "in_progress" or \
               sdd.get("status") == "pending", (
            f"02-sdd (gates.parallel_build=true) должен быть активен, не {sdd.get('status')!r}"
        )

        # Теперь выключаем gate → фаза отключается
        gates_off = {"gates": {"parallel_build": {"enabled": False}}}
        d2 = pp.live_phase_decision(manifest, pipeline=pipeline, gates=gates_off)
        sdd2 = _phase(d2, "02-sdd")
        assert sdd2.get("status") == "skipped"
        assert "disabled_by" in sdd2.get("skip_reason", "")


class TestLivePhaseDecisionSkipIf:
    """skip_if: фаза со сработавшим условием → status=skipped."""

    def test_live_phase_decision_skips_when_skip_if_true(self, monkeypatch):
        # skip_if="!env:FEATURE_GROUNDING_REQUIRED" — skip, если env НЕ выставлен (skip, когда !truthy)
        monkeypatch.delenv("FEATURE_GROUNDING_REQUIRED", raising=False)
        pipeline = {
            "phases_override": [{
                "id": "01-grounding",
                "skip_if": "!env:FEATURE_GROUNDING_REQUIRED",
            }],
        }
        manifest = _manifest({"01-grounding": "pending", "02-sdd": "pending"})
        d = pp.live_phase_decision(manifest, pipeline=pipeline)
        gr = _phase(d, "01-grounding")
        assert gr.get("status") == "skipped", (
            f"01-grounding (skip_if=!env unset) должен быть skipped, не {gr.get('status')!r}"
        )
        assert gr.get("skip_reason", "").startswith("skip_if:"), (
            f"skip_reason должен быть 'skip_if: ...', не {gr.get('skip_reason')!r}"
        )
        assert d["current_phase"] == "02-sdd", (
            f"current_phase должен перепрыгнуть через skipped 01-grounding на 02-sdd"
        )

    def test_live_phase_decision_skips_when_skip_if_bool_true(self):
        """skip_if=literal True (config.py phase disable пишет skip_if)."""
        pipeline = {"phases_override": [{"id": "04-tdd", "skip_if": True}]}
        manifest = _manifest({
            "01-grounding": "completed",
            "02-sdd": "completed",
            "02-design": "completed",
            "03-jira": "completed",
            "04-tdd": "pending",
        })
        d = pp.live_phase_decision(manifest, pipeline=pipeline)
        tdd = _phase(d, "04-tdd")
        assert tdd.get("status") == "skipped", (
            f"04-tdd (skip_if=True) должен быть skipped, не {tdd.get('status')!r}"
        )
        assert "skip_if" in tdd.get("skip_reason", "")


class TestLivePhaseDecisionRunsNormally:
    """Нормальные кейсы — фаза активна и течёт."""

    def test_live_phase_decision_runs_when_enabled_and_skip_if_false(self, monkeypatch):
        # enabled_by=True (явный literal), skip_if=False (literal) — фаза активна
        pipeline = {
            "phases_override": [{
                "id": "02-sdd",
                "enabled_by": True,
                "skip_if": False,
            }],
        }
        monkeypatch.delenv("FEATURE_SDD_ENABLED", raising=False)  # env игнорируется при enabled_by=True
        manifest = _manifest({"01-grounding": "completed", "02-sdd": "pending"})
        d = pp.live_phase_decision(manifest, pipeline=pipeline)
        sdd = _phase(d, "02-sdd")
        assert sdd.get("status") != "skipped", (
            f"02-sdd (enabled_by=True, skip_if=False) должен быть активен, не {sdd.get('status')!r}"
        )
        assert "skip_reason" not in sdd, "skip_reason не должен быть у активной фазы"
        assert d["current_phase"] == "02-sdd", (
            f"current_phase должен быть 02-sdd, не {d['current_phase']!r}"
        )

    def test_live_phase_decision_runs_when_env_set(self, monkeypatch):
        """env выставлен → enabled_by='env:VAR' = True → фаза активна."""
        monkeypatch.setenv("FEATURE_SDD_ENABLED", "1")
        pipeline = {
            "phases_override": [{"id": "02-sdd", "enabled_by": "env:FEATURE_SDD_ENABLED"}],
        }
        manifest = _manifest({"01-grounding": "completed", "02-sdd": "pending"})
        d = pp.live_phase_decision(manifest, pipeline=pipeline)
        sdd = _phase(d, "02-sdd")
        assert sdd.get("status") != "skipped", (
            f"02-sdd (env set) должен быть активен, не {sdd.get('status')!r}"
        )

    def test_live_phase_decision_no_config_legacy_contract(self):
        """Без pipeline/project_root — старое поведение (back-compat). Фазы видны как есть."""
        manifest = _manifest({"01-grounding": "completed", "02-sdd": "pending"})
        d = pp.live_phase_decision(manifest)  # без pipeline/gates — apply_eligibility=False
        # Все фазы присутствуют как раньше, skip_reason ни у кого нет.
        for ph in d["phases"]:
            assert "skip_reason" not in ph, (
                f"Без pipeline не должно быть skip_reason, неожиданно у {ph['id']!r}"
            )
        assert d["current_phase"] == "02-sdd"


# ── Sync-тесты: resolve_phases и live_phase_decision дают одинаковый ответ ─
class TestResolveAndLiveAgree:
    """Главный фикс п.8: live-снимок и резолвер не должны расходиться по enabled_by/skip_if."""

    def test_resolve_phases_and_live_phase_decision_agree_on_disabled(self, tmp_path, monkeypatch):
        # Создаём проект с ground/pipeline.json, где eval_enabled=false.
        # И resolve_phases, и live_phase_decision должны:
        #   - resolve: 02-eval-plan в skipped, current_phase указывает на 03-jira
        #   - live:    02-eval-plan со status=skipped, current_phase = "03-jira"
        monkeypatch.delenv("FEATURE_SDD_ENABLED", raising=False)
        pipeline_dict = {
            "jira": {"enabled": True},
            "quality": {"tdd": True, "eval_enabled": False},  # ← выключает 02-eval-plan
        }
        project_root = _project_root_with(pipeline_dict)

        manifest = _manifest({
            "01-grounding": "completed",
            "02-sdd": "completed",
            "02-design": "completed",
            "02-eval-plan": "pending",   # в manifest есть, но в резолвере должна быть skipped
            "03-jira": "pending",
            "04-tdd": "pending",
        })
        # save manifest на диск для resolve_phases.current_phase
        feat_dir = project_root / "ground" / "statements" / "feature-pipeline" / "test-feat"
        feat_dir.mkdir(parents=True, exist_ok=True)
        (feat_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        # resolve_phases: 02-eval-plan должна быть в skipped
        resolved = rp.resolve_phases(str(project_root), feature_slug="test-feat")
        skipped_ids = {s["id"] for s in resolved["skipped"]}
        assert "02-eval-plan" in skipped_ids, (
            f"resolve_phases должен положить 02-eval-plan в skipped, "
            f"skipped={skipped_ids}"
        )

        # live_phase_decision: 02-eval-plan должна быть skipped
        d = pp.live_phase_decision(manifest, project_root=str(project_root))
        ep = _phase(d, "02-eval-plan")
        assert ep.get("status") == "skipped", (
            f"live_phase_decision должен пометить 02-eval-plan как skipped, не {ep.get('status')!r}"
        )

        # Обе функции должны давать одинаковый current_phase.
        live_current = d["current_phase"]

        # enabled_ids из резолвера для legacy-фильтра
        enabled_ids = {p["id"] for p in resolved["phases"]}
        d_legacy = pp.live_phase_decision(manifest, enabled_phases=enabled_ids)
        legacy_current = d_legacy["current_phase"]

        assert live_current == legacy_current, (
            f"Новый и legacy путь live_phase_decision разошлись: "
            f"new={live_current!r}, legacy={legacy_current!r}"
        )
        # И не "застряли" на отключённой 02-eval-plan
        assert live_current != "02-eval-plan", (
            f"live_current_phase застрял на отключённой 02-eval-plan (п.8 KIDPPRB-9254)"
        )

    def test_resolve_phases_and_live_phase_decision_agree_on_skip_if(self, tmp_path, monkeypatch):
        # skip_if на 04-tdd через phases_override — обе функции должны исключить.
        monkeypatch.delenv("SKIP_TDD", raising=False)
        pipeline_dict = {
            "jira": {"enabled": True},
            "quality": {"tdd": True, "eval_enabled": True},
            "phases_override": [{
                "id": "04-tdd",
                "skip_if": "env:SKIP_TDD",  # если SKIP_TDD выставлен → skip
            }],
        }
        project_root = _project_root_with(pipeline_dict)

        manifest = _manifest({
            "01-grounding": "completed",
            "02-sdd": "completed",
            "02-design": "completed",
            "02-eval-plan": "completed",
            "03-jira": "completed",
            "04-tdd": "pending",
            "05-verify": "pending",
        })
        feat_dir = project_root / "ground" / "statements" / "feature-pipeline" / "test-feat"
        feat_dir.mkdir(parents=True, exist_ok=True)
        (feat_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        # Без env SKIP_TDD: фаза должна быть АКТИВНА в обеих функциях.
        resolved = rp.resolve_phases(str(project_root), feature_slug="test-feat")
        skipped_ids = {s["id"] for s in resolved["skipped"]}
        assert "04-tdd" not in skipped_ids, (
            f"Без env SKIP_TDD фаза 04-tdd должна быть активна, skipped={skipped_ids}"
        )
        d = pp.live_phase_decision(manifest, project_root=str(project_root))
        tdd = _phase(d, "04-tdd")
        assert tdd.get("status") != "skipped", (
            f"Без env SKIP_TDD live_phase_decision должен оставить 04-tdd активной, "
            f"не {tdd.get('status')!r}"
        )

        # С env SKIP_TDD=1: фаза должна быть SKIPPED в обеих функциях.
        monkeypatch.setenv("SKIP_TDD", "1")
        resolved_off = rp.resolve_phases(str(project_root), feature_slug="test-feat")
        skipped_off = {s["id"] for s in resolved_off["skipped"]}
        assert "04-tdd" in skipped_off, (
            f"С env SKIP_TDD=1 фаза 04-tdd должна быть в skipped, skipped={skipped_off}"
        )
        d_off = pp.live_phase_decision(manifest, project_root=str(project_root))
        tdd_off = _phase(d_off, "04-tdd")
        assert tdd_off.get("status") == "skipped", (
            f"С env SKIP_TDD=1 live_phase_decision должен пометить 04-tdd как skipped, "
            f"не {tdd_off.get('status')!r}"
        )
        assert tdd_off.get("skip_reason", "").startswith("skip_if:"), (
            f"skip_reason должен быть 'skip_if: env:SKIP_TDD', не {tdd_off.get('skip_reason')!r}"
        )


# ── Базовый back-compat: старый контракт без project_root/pipeline ──
class TestLegacyContract(unittest.TestCase):
    """Без project_root/pipeline — старое поведение (для уже существующих тестов)."""

    def test_legacy_no_eligibility_check(self):
        manifest = _manifest({"01-grounding": "completed", "02-sdd": "pending"})
        d = pp.live_phase_decision(manifest)
        self.assertEqual(d["current_phase"], "02-sdd")
        for ph in d["phases"]:
            self.assertNotIn("skip_reason", ph)

    def test_legacy_with_enabled_phases_filter(self):
        """enabled_phases продолжает работать как раньше."""
        manifest = _manifest({
            "01-grounding": "completed",
            "02-sdd": "pending",
            "02-design": "pending",
        })
        d = pp.live_phase_decision(manifest, enabled_phases={"02-sdd"})
        self.assertEqual(d["current_phase"], "02-sdd")
        ids = {p["id"] for p in d["phases"]}
        self.assertEqual(ids, {"02-sdd"})


# ── DRY: phase_eligibility импортируется из _phase_eligibility ──────
class TestDryImports(unittest.TestCase):
    """Один источник истины: pipeline_phases и resolve_phases используют одну phase_eligibility."""

    def test_pipeline_phases_exports_phase_eligibility(self):
        from _phase_eligibility import phase_eligibility as pe_ref
        # pp.phase_eligibility — это та же функция (через from-import)
        self.assertIs(pp.phase_eligibility, pe_ref)

    def test_resolve_phases_exports_phase_eligibility(self):
        from _phase_eligibility import phase_eligibility as pe_ref
        self.assertIs(rp.phase_eligibility, pe_ref)

    def test_should_execute_dataclass_available(self):
        from _phase_eligibility import ShouldExecute as SE
        self.assertEqual(SE.execute().should_execute, True)
        self.assertEqual(SE.disabled_by("quality.tdd").skip_reason, "disabled_by: quality.tdd")
        self.assertEqual(SE.skip_if_triggered("env:FOO").skip_reason, "skip_if: env:FOO")


# ── Legacy main (если запускают без pytest) ─────────────────────────
def main() -> int:
    if _HAS_PYTEST:
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(Path(__file__).resolve()), "-v"],
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        return r.returncode
    # Fallback: unittest
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestLegacyContract)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
