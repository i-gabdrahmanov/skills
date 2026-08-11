#!/usr/bin/env python3
"""test_docs_resolver_consistency.py — пины целостности резолва расположения docs.

Контракт резолва (где живут brd/sdd/tech-design/task-plan + system-analysis/grounding)
раньше ДУБЛИРОВАЛСЯ в трёх местах — скрипты, хуки и pipeline-state деплоились раздельно,
co-located импорт был невозможен, и синхронность держалась property-based тестом на
эквивалентность. В extension-модели бандл едет целиком, копии сняты; реализация одна:
  • hooks/_project.py                                 — ЕДИНСТВЕННАЯ реализация
  • skills/feature-pipeline/scripts/skill_paths.py    — ре-экспорт
  • skills/pipeline-state/scripts/_util.py            — ре-экспорт

Часть A: имена в трёх модулях — ОДИН И ТОТ ЖЕ объект (пин строже прежнего сравнения
         результатов: ловит саму попытку завести локальную копию, а не только её дрейф).
Часть A1: резолв даёт правильные пути на матрице docs-конфигов (in-repo / custom base /
         separate-repo / legacy / docs.master) — это уже про корректность, не про синхрон.
Часть B: продакшн-скрипты/хуки НЕ строят docs-путь хардкодом в обход резолвера
         (кроме явных fallback-веток и самих определений резолвера).

Exit: 0 — ок, 1 — копия резолвера вернулась, ошибка резолва или новый хардкод.
"""
from __future__ import annotations

import contextlib
import io
import itertools
import random
import sys
import tempfile
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
    # docs.master: мастер (system-analysis) отдельно, дельты (feature) — по глобальному docs.
    "master separate, deltas in-repo": (
        {"docs_path": "docs", "master": {"mode": "separate-repo", "repo_path": EXT}},
        PROJ / "docs/feature-pipeline", Path(EXT) / "system-analysis"),
    "master in-repo override on sep global": (
        {"mode": "separate-repo", "repo_path": EXT, "master": {"mode": "in-repo"}},
        Path(EXT) / "feature-pipeline", Path(EXT) / "system-analysis"),
}


class TestSingleImplementation(unittest.TestCase):
    """Часть A: три модуля отдают ОДИН объект, а не три совпадающие реализации."""

    # Имена, которые skill_paths и _util обязаны ре-экспортировать из _project.
    SHARED = ("docs_base", "feature_docs_dir", "load_pipeline_config", "safe_slug",
              "safe_component", "ground_dir", "state_dir", "manifest_path",
              "origin_path", "gate_result_path", "judge_path", "approval_path")
    # Мастер-резолв нужен стороне скриптов; pipeline-state им не пользуется.
    SHARED_SCRIPTS_ONLY = ("system_analysis_dir", "scan_dir", "grounding_excerpt_path",
                           "master_specs_dir", "master_spec_path", "master_adr_dir",
                           "master_capability", "_master_base", "find_project_root")

    def test_skill_paths_reexports_project(self):
        for name in self.SHARED + self.SHARED_SCRIPTS_ONLY:
            with self.subTest(name=name):
                self.assertIs(getattr(skill_paths, name), getattr(_project, name),
                              f"skill_paths.{name} — не тот же объект, что _project.{name}: "
                              f"похоже, копия резолвера вернулась")

    def test_util_reexports_project(self):
        for name in self.SHARED:
            with self.subTest(name=name):
                self.assertIs(getattr(_util, name), getattr(_project, name),
                              f"_util.{name} — не тот же объект, что _project.{name}: "
                              f"похоже, копия резолвера вернулась")

    def test_no_local_docs_resolver_definitions(self):
        """В шимах не должно быть СВОИХ def для общих имён (ре-экспорт, а не переопределение)."""
        import ast
        for mod_path in (SCRIPTS / "skill_paths.py", PSTATE / "_util.py"):
            defined = {n.name for n in ast.parse(mod_path.read_text("utf-8")).body
                       if isinstance(n, ast.FunctionDef)}
            clash = defined & set(self.SHARED + self.SHARED_SCRIPTS_ONLY)
            self.assertEqual(clash, set(),
                             f"{mod_path.name} переопределяет общие имена {sorted(clash)} — "
                             f"это возврат к трём копиям резолвера")


class TestFindProjectRoot(unittest.TestCase):
    """Корень ДАННЫХ (ground/, docs/) — критерии по приоритету, а не по уровню вложенности."""

    def test_git_root_beats_nested_gradle_module(self):
        """Мульти-модульный Gradle: корень — репо (.git), а не модуль с build.gradle.

        Регрессия: цикл шёл по уровням и проверял все критерии на каждом, поэтому
        <repo>/module-a (build.gradle) побеждал <repo> (.git) — ground/ заводился внутри
        модуля, и сторона хуков расходилась со стороной скриптов.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "module-a" / "src").mkdir(parents=True)
            (repo / ".git").mkdir()
            (repo / "settings.gradle").touch()
            (repo / "module-a" / "build.gradle").touch()
            self.assertEqual(skill_paths.find_project_root(repo / "module-a" / "src"), repo)

    def test_gradle_root_when_no_git(self):
        """Без .git корнем становится gradle/maven-проект (следующий критерий)."""
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            (proj / "src" / "main").mkdir(parents=True)
            (proj / "build.gradle").touch()
            self.assertEqual(skill_paths.find_project_root(proj / "src" / "main"), proj)

    def test_pipeline_json_is_last_resort(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            (proj / "ground").mkdir(parents=True)
            (proj / "ground" / "pipeline.json").write_text("{}", encoding="utf-8")
            (proj / "sub").mkdir()
            self.assertEqual(skill_paths.find_project_root(proj / "sub"), proj)

    def test_fallback_is_start_itself(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(skill_paths.find_project_root(Path(td)), Path(td))


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

    def test_master_override_splits_location(self):
        """docs.master → мастер в отдельном репо, дельты остаются in-repo; skill_paths==_project."""
        cfg = {"docs": {"docs_path": "docs",
                        "master": {"mode": "separate-repo", "repo_path": EXT}}}
        # дельты (feature) — по глобальному docs (in-repo), все три копии согласны
        for mod in (skill_paths, _project, _util):
            self.assertEqual(str(mod.feature_docs_dir(PROJ, cfg)),
                             str(PROJ / "docs/feature-pipeline"))
        # мастер (system-analysis/scan/excerpt/specs/adr) — в EXT; skill_paths и _project совпадают
        for fn in ("system_analysis_dir", "scan_dir", "grounding_excerpt_path",
                   "master_specs_dir", "master_adr_dir"):
            a = str(getattr(skill_paths, fn)(PROJ, cfg))
            b = str(getattr(_project, fn)(PROJ, cfg))
            self.assertEqual(a, b, f"{fn}: skill_paths≠_project под docs.master")
            self.assertTrue(a.startswith(EXT), f"{fn} мастер не в EXT: {a}")
        # adr_subdir override
        cfg2 = {"docs": {"docs_path": "docs",
                         "master": {"mode": "separate-repo", "repo_path": EXT, "adr_subdir": "decisions"}}}
        self.assertEqual(str(skill_paths.master_adr_dir(PROJ, cfg2)),
                         str(_project.master_adr_dir(PROJ, cfg2)))
        self.assertTrue(str(skill_paths.master_adr_dir(PROJ, cfg2)).endswith("/decisions"))


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


# ── Часть A+: property-based устойчивость единственной реализации ─────────────
# Эта матрица конфигов родилась как проверка эквивалентности трёх копий резолвера: слить их
# в один импорт было нельзя (раздельный деплой), поэтому синхронность держали генератором.
# Копии сняты (TestSingleImplementation), сравнение стало тавтологией — матрица переиспользована
# под настоящий оракул: на любом, в т.ч. мусорном, docs-конфиге резолвер не падает и не
# выпускает путь за пределы проекта (кроме осознанного separate-repo, который наружу и целится).

# Домены значений каждого поля docs-конфига (валидные, дефолтные и зловредные).
_MODE = [None, "in-repo", "separate-repo", "garbage", 0]
_DOCS_PATH = [None, "docs", "documentation", "a/b", "../esc", "/abs/x", "~/home", 99, "", "  "]
_REPO_PATH = [None, "/ext/repo", "rel/ext", "~/ext", "  ", 5, ""]
_FEAT_SUB = [None, "feature-pipeline", "custom", "../x", "a/b", "..", 7, ""]
_SA_SUB = [None, "system-analysis", "sa", "../x", "..", 3]
_LEGACY_FD = [None, "docs/feats", "../x", "/abs", "deep/a/b", 4]
_LEGACY_SA = [None, "docs/sa", "../x", "/abs", 8]
# docs.master — per-класс оверрайд локации мастера (system-analysis + specs/).
_MASTER = [None, {}, "notdict", 7,
           {"mode": "separate-repo", "repo_path": "/ext/master"},
           {"mode": "separate-repo"},                 # без repo_path → фолбэк на глобальную базу
           {"mode": "in-repo"},                        # → глобальная база
           {"repo_path": "/ext/master"},               # без mode → наследует docs.mode
           {"mode": "separate-repo", "repo_path": "rel/master"}]


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
    if rng.random() < 0.5: d["master"] = rng.choice(_MASTER)
    return d


class TestPropertyBasedRobustness(unittest.TestCase):
    """Резолвер не падает и не выпускает путь из проекта на любом docs-конфиге."""

    def _assert_sane(self, cfg):
        docs = cfg.get("docs") if isinstance(cfg, dict) else None
        # separate-repo (и любой docs.master) целится НАРУЖУ проекта осознанно — там
        # проверяем только «не падает и без traversal», без привязки к корню.
        outward = isinstance(docs, dict) and (docs.get("mode") == "separate-repo"
                                              or isinstance(docs.get("master"), dict))
        for fn in (skill_paths.docs_base, skill_paths.feature_docs_dir,
                   skill_paths.system_analysis_dir):
            p = fn(PROJ, cfg)  # не бросает ни на каком мусоре
            self.assertIsInstance(p, Path, f"{fn.__name__} вернул не Path на cfg={cfg}")
            self.assertNotIn("..", p.parts,
                             f"{fn.__name__} → traversal в пути {p} (cfg={cfg})")
            if not outward:
                self.assertTrue(str(p).startswith(str(PROJ)),
                                f"{fn.__name__} вышел за проект: {p} (cfg={cfg})")

    def test_exhaustive_core_matrix(self):
        """Полный декартов перебор ключевых полей (mode×docs_path×repo_path×feature_subdir)."""
        with contextlib.redirect_stderr(io.StringIO()):  # глушим warning-спам резолвера
            for mode, dp, rp, fs in itertools.product(_MODE, _DOCS_PATH, _REPO_PATH, _FEAT_SUB):
                self._assert_sane({"docs": {"mode": mode, "docs_path": dp,
                                            "repo_path": rp, "feature_subdir": fs}})

    def test_randomized_fuzz(self):
        """3000 псевдослучайных конфигов с фиксированным seed (воспроизводимо)."""
        rng = random.Random(20260620)
        with contextlib.redirect_stderr(io.StringIO()):
            for _ in range(3000):
                self._assert_sane({"docs": _rand_docs(rng)})

    def test_cfg_none_and_missing_docs(self):
        """cfg=None и cfg без docs — дефолт (docs/ под проектом), без падений."""
        with contextlib.redirect_stderr(io.StringIO()):
            for cfg in (None, {}, {"docs": None}, {"other": 1}):
                self._assert_sane(cfg)


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
