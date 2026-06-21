#!/usr/bin/env python3
"""test_docs_resolver_consistency.py — пины целостности резолва расположения docs.

Контракт резолва (где живут brd/sdd/tech-design/task-plan + system-analysis/grounding)
ДУБЛИРУЕТСЯ в трёх местах из-за топологии деплоя (скрипты, хуки и pipeline-state
деплоятся раздельно):
  • skills/feature-pipeline/scripts/skill_paths.py   (сторона скриптов)
  • hooks/_project.py                                 (сторона хуков)
  • skills/pipeline-state/scripts/_util.py            (pipeline-state, глобальный деплой)

Часть A: все три копии обязаны давать ОДИНАКОВЫЙ результат на матрице docs-конфигов
         (in-repo / custom base / separate-repo / legacy). Рассинхрон → fail.
Часть B: продакшн-скрипты/хуки НЕ строят docs-путь хардкодом в обход резолвера
         (кроме явных fallback-веток и самих определений резолвера).

Exit: 0 — ок, 1 — рассинхрон или новый хардкод.
"""
from __future__ import annotations

import contextlib
import io
import itertools
import random
import sys
import unittest
from pathlib import Path

def _find_root() -> Path:
    """База, содержащая skills/ и hooks/: forge/ (source) или <project>/.gigacode (deploy)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "skills").is_dir() and (parent / "hooks").is_dir():
            return parent
        if (parent / ".gigacode" / "skills").is_dir():
            return parent / ".gigacode"
    return here.parents[3]


ROOT = _find_root()
SCRIPTS = ROOT / "skills" / "feature-pipeline" / "scripts"
HOOKS = ROOT / "hooks"
PSTATE = ROOT / "skills" / "pipeline-state" / "scripts"
for p in (SCRIPTS, HOOKS, PSTATE):
    sys.path.insert(0, str(p))

import skill_paths  # noqa: E402
import _project  # noqa: E402
import _util  # noqa: E402

PROJ = Path("/tmp/__docs_resolver_proj__")
EXT = "/tmp/__docs_resolver_ext__"

# (имя кейса, cfg["docs"], ожидаемые feature_docs / system_analysis относительно якоря)
CASES = {
    "in-repo default":   ({}, PROJ / "docs/feature-pipeline", PROJ / "docs/system-analysis"),
    "in-repo custom":    ({"docs_path": "documentation"},
                          PROJ / "documentation/feature-pipeline", PROJ / "documentation/system-analysis"),
    "separate-repo":     ({"mode": "separate-repo", "repo_path": EXT},
                          Path(EXT) / "feature-pipeline", Path(EXT) / "system-analysis"),
    "legacy feature":    ({"feature_docs_path": "docs/feats"},
                          PROJ / "docs/feats", PROJ / "docs/system-analysis"),
    "sep ignores legacy": ({"mode": "separate-repo", "repo_path": EXT, "feature_docs_path": "docs/feats"},
                           Path(EXT) / "feature-pipeline", Path(EXT) / "system-analysis"),
}


class TestResolverConsistency(unittest.TestCase):
    def test_all_three_sides_agree_and_correct(self):
        for name, (docs, exp_fd, exp_sa) in CASES.items():
            cfg = {"docs": docs}
            fd = {
                "skill_paths": skill_paths.feature_docs_dir(PROJ, cfg),
                "_project": _project.feature_docs_dir(PROJ, cfg),
                "_util": _util.feature_docs_dir(PROJ, cfg),
            }
            sa = {
                "skill_paths": skill_paths.system_analysis_dir(PROJ, cfg),
                "_project": _project.system_analysis_dir(PROJ, cfg),
            }
            with self.subTest(case=name):
                self.assertEqual(set(map(str, fd.values())), {str(exp_fd)},
                                 f"feature_docs рассинхрон/ошибка в кейсе «{name}»: {fd}")
                self.assertEqual(set(map(str, sa.values())), {str(exp_sa)},
                                 f"system_analysis рассинхрон/ошибка в кейсе «{name}»: {sa}")

    def test_scan_and_excerpt_under_system_analysis(self):
        cfg = {"docs": {"mode": "separate-repo", "repo_path": EXT}}
        self.assertEqual(str(skill_paths.scan_dir(PROJ, cfg)), f"{EXT}/system-analysis/scan")
        self.assertEqual(str(skill_paths.grounding_excerpt_path(PROJ, cfg)),
                         f"{EXT}/system-analysis/grounding-excerpt.json")
        # сторона хуков — тот же путь
        self.assertEqual(str(_project.grounding_excerpt_path(PROJ, cfg)),
                         f"{EXT}/system-analysis/grounding-excerpt.json")


# ── Часть B: нет хардкода docs-пути в обход резолвера ─────────────────────────
# Продакшн-файлы (не тесты), которые рефакторились на резолвер. Для каждого —
# допустимые строки-исключения (fallback-ветки, определения резолвера).
PRODUCTION_FILES = [
    SCRIPTS / "run_judge.py",
    SCRIPTS / "run_pending_evals.py",
    HOOKS / "context-injector.py",
    HOOKS / "eval-guard.py",
    PSTATE / "init.py",
    ROOT / "skills/system-analyst/scripts/scan_all.py",
    ROOT / "skills/system-analyst/scripts/enrich_grounding.py",
    ROOT / "skills/system-analyst/scripts/check_grounding.py",
]
# Подстроки, маркирующие путь-конструкцию в обход резолвера.
BYPASS = ('"docs/feature-pipeline"', "'docs/feature-pipeline'",
          '"docs/system-analysis"', "'docs/system-analysis'",
          '"docs/system-analysis/scan"', "'docs/system-analysis/scan'",
          '"docs/system-analysis/grounding-excerpt.json"')


def _is_allowed(line: str) -> bool:
    """Разрешено: fallback-ветки и комментарии/докстринги (не реальная резолв-логика)."""
    s = line.strip()
    if s.startswith("#"):
        return True
    low = s.lower()
    # fallback в except / помеченный комментом «фоллбэк/fallback» — это и есть страховка
    return ("фоллбэк" in low or "fallback" in low or "default:" in low
            or "help=" in low or s.startswith('"""') or s.startswith('"'))


class TestNoBypassHardcode(unittest.TestCase):
    def test_production_files_resolve_docs(self):
        offenders = []
        for f in PRODUCTION_FILES:
            if not f.exists():
                continue
            for i, line in enumerate(f.read_text("utf-8").splitlines(), 1):
                if any(b in line for b in BYPASS) and not _is_allowed(line):
                    offenders.append(f"{f.name}:{i}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "Хардкод docs-пути в обход резолвера (используй skill_paths/_project/_util "
                         f"docs_base):\n  " + "\n  ".join(offenders))


# ── Часть A+: property-based эквивалентность трёх копий ───────────────────────
# Три копии резолвера (skill_paths / _project / _util) живут раздельно из-за топологии
# деплоя (pipeline-state — user-global, может быть не co-located с hooks/scripts проекта).
# Слить их в один импорт нельзя, поэтому P1-5 закрывается ИНАЧЕ: исчерпывающим тестом
# эквивалентности по полной матрице docs-конфигов — любой расхождение на любом (в т.ч.
# не вписанном вручную) кейсе валит сборку В ИСХОДНИКЕ, до деплоя.

# Домены значений каждого поля docs-конфига (валидные, дефолтные и зловредные).
_MODE = [None, "in-repo", "separate-repo", "garbage", 0]
_DOCS_PATH = [None, "docs", "documentation", "a/b", "../esc", "/abs/x", "~/home", 99, "", "  "]
_REPO_PATH = [None, "/ext/repo", "rel/ext", "~/ext", "  ", 5, ""]
_FEAT_SUB = [None, "feature-pipeline", "custom", "../x", "a/b", "..", 7, ""]
_SA_SUB = [None, "system-analysis", "sa", "../x", "..", 3]
_LEGACY_FD = [None, "docs/feats", "../x", "/abs", "deep/a/b", 4]
_LEGACY_SA = [None, "docs/sa", "../x", "/abs", 8]


def _rand_docs(rng: random.Random):
    """Случайный docs-конфиг: иногда сам docs не-dict (проверка устойчивости)."""
    roll = rng.random()
    if roll < 0.05:
        return rng.choice([123, [1, 2], "str", None])  # docs не-словарь
    d = {}
    if rng.random() < 0.8: d["mode"] = rng.choice(_MODE)
    if rng.random() < 0.8: d["docs_path"] = rng.choice(_DOCS_PATH)
    if rng.random() < 0.6: d["repo_path"] = rng.choice(_REPO_PATH)
    if rng.random() < 0.6: d["feature_subdir"] = rng.choice(_FEAT_SUB)
    if rng.random() < 0.6: d["system_analysis_subdir"] = rng.choice(_SA_SUB)
    if rng.random() < 0.4: d["feature_docs_path"] = rng.choice(_LEGACY_FD)
    if rng.random() < 0.4: d["system_analysis_path"] = rng.choice(_LEGACY_SA)
    return d


class TestPropertyBasedConsistency(unittest.TestCase):
    """Три копии обязаны давать ОДИНАКОВЫЙ результат на любом docs-конфиге."""

    def _assert_agree(self, cfg):
        # docs_base — все три
        bases = {
            "skill_paths": str(skill_paths.docs_base(PROJ, cfg)),
            "_project": str(_project.docs_base(PROJ, cfg)),
            "_util": str(_util.docs_base(PROJ, cfg)),
        }
        self.assertEqual(len(set(bases.values())), 1,
                         f"docs_base рассинхрон на cfg={cfg}: {bases}")
        # feature_docs_dir — все три
        fds = {
            "skill_paths": str(skill_paths.feature_docs_dir(PROJ, cfg)),
            "_project": str(_project.feature_docs_dir(PROJ, cfg)),
            "_util": str(_util.feature_docs_dir(PROJ, cfg)),
        }
        self.assertEqual(len(set(fds.values())), 1,
                         f"feature_docs_dir рассинхрон на cfg={cfg}: {fds}")
        # system_analysis_dir — skill_paths и _project (у _util его нет)
        sas = {
            "skill_paths": str(skill_paths.system_analysis_dir(PROJ, cfg)),
            "_project": str(_project.system_analysis_dir(PROJ, cfg)),
        }
        self.assertEqual(len(set(sas.values())), 1,
                         f"system_analysis_dir рассинхрон на cfg={cfg}: {sas}")

    def test_exhaustive_core_matrix(self):
        """Полный декартов перебор ключевых полей (mode×docs_path×repo_path×feature_subdir)."""
        with contextlib.redirect_stderr(io.StringIO()):  # глушим warning-спам резолверов
            for mode, dp, rp, fs in itertools.product(_MODE, _DOCS_PATH, _REPO_PATH, _FEAT_SUB):
                self._assert_agree({"docs": {"mode": mode, "docs_path": dp,
                                             "repo_path": rp, "feature_subdir": fs}})

    def test_randomized_fuzz(self):
        """3000 псевдослучайных конфигов с фиксированным seed (воспроизводимо)."""
        rng = random.Random(20260620)
        with contextlib.redirect_stderr(io.StringIO()):
            for _ in range(3000):
                self._assert_agree({"docs": _rand_docs(rng)})

    def test_cfg_none_and_missing_docs(self):
        """cfg=None и cfg без docs — тоже согласованы (берётся pipeline.json/{} единообразно)."""
        with contextlib.redirect_stderr(io.StringIO()):
            for cfg in (None, {}, {"docs": None}, {"other": 1}):
                self._assert_agree(cfg)


class TestSafeSlug(unittest.TestCase):
    def test_rejects_traversal(self):
        for bad in ["../x", "a/b", "/abs", "~/x", "..", "", ".", "a\\b"]:
            with self.subTest(slug=bad):
                self.assertRaises(ValueError, skill_paths.safe_slug, bad)

    def test_accepts_normal(self):
        for ok in ["KIDPPRB-8639", "feat_x", "auto-close-tasks", "T1"]:
            self.assertEqual(skill_paths.safe_slug(ok), ok)


class TestResolverHardening(unittest.TestCase):
    def test_malformed_config_no_crash(self):
        R = Path("/tmp/__p__")
        for docs in [123, [1, 2], {"docs_path": 99}, {"feature_subdir": "../e"},
                     {"docs_path": "../../etc"}, {"docs_path": "/etc"}, None]:
            with self.subTest(docs=docs):
                r = skill_paths.feature_docs_dir(R, {"docs": docs})
                # всегда остаётся под проектом (никакого traversal/абсолюта наружу)
                self.assertTrue(str(r).startswith(str(R)), f"{docs} → {r} вышел за проект")


class TestIsTestPath(unittest.TestCase):
    def test_segment_based(self):
        import _project
        truths = {
            "src/test/java/FooTest.java": True, "a/__tests__/b.ts": True,
            "foo.test.ts": True, "test_x.py": True, "FooTests.java": True, "x/FooIT.java": True,
            "src/main/java/Foo.java": False, "src/main/testimonials/Foo.java": False,
            "src/main/Contest.java": False, "src/main/Latest.java": False, "docs/x/sdd.md": False,
        }
        for path, exp in truths.items():
            with self.subTest(path=path):
                self.assertEqual(_project.is_test_path(path), exp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
