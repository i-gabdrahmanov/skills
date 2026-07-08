#!/usr/bin/env python3
"""
_project.py — Единый resolver для всех хуков (ПРОЕКТНАЯ модель).

База кода — каталог, где физически лежат hooks/ и skills/ ЭТОГО проекта, выводится из
расположения самого хук-файла. НИКАКОЙ зависимости от ~/.gigacode: всё живёт в проекте
и управляется git. В развёрнутом проекте база = <project>/.gigacode; в source-репо — корень.

project_root (для ДАННЫХ: ground/, docs/) ищется отдельно по live-файлам
(.git, build.gradle, pipeline.json).

Usage:
    from _project import gigacode_dir, skills_dir, find_project_root
"""

import json
import sys
from pathlib import Path
from typing import Optional


def gigacode_dir() -> Path:
    """База кода: каталог с hooks/ и skills/ этого проекта.

    Хук-файл лежит в <base>/hooks/_project.py → база = parents[1].
    Развёрнутый проект: <project>/.gigacode. Source-репо: корень репо.
    """
    return Path(__file__).resolve().parents[1]


# Обратная совместимость: имя сохранено, но теперь это ПРОЕКТНАЯ база (не ~/.gigacode).
def gigacode_home() -> Path:
    return gigacode_dir()


def skills_dir() -> Path:
    """Путь к скиллам: <project>/.gigacode/skills/<skill>/scripts/..."""
    return gigacode_dir() / "skills"


def hooks_dir() -> Path:
    """Путь к хукам: <project>/.gigacode/hooks/"""
    return gigacode_dir() / "hooks"


def resolve_skill_path(skill_name: str, *subpaths: str) -> Path:
    """Резолвит путь к скиллу: <база>/skills/<skill>/<subpaths> (ПРОЕКТНАЯ модель,
    база выводится из расположения хука — не ~/.gigacode).

    Пример: resolve_skill_path("pipeline-state", "scripts", "update.py")
    → <project>/.gigacode/skills/pipeline-state/scripts/update.py
    """
    return skills_dir().joinpath(skill_name, *subpaths)


def resolve_hook_path(hook_name: str) -> Path:
    """Резолвит путь к хуку: <база>/hooks/<hook>.py (проектная база, не ~/.gigacode)."""
    return hooks_dir() / f"{hook_name}.py"


def find_project_root(cwd: Optional[Path] = None) -> Path:
    """Ищет корень проекта от cwd вверх.

    Критерии (по убыванию приоритета):
    1. Содержит .git
    2. Содержит build.gradle или settings.gradle или pom.xml
    3. Содержит ground/pipeline.json

    Возвращает первый совпавший, начиная от cwd.
    """
    cwd = cwd or Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent
        if (parent / "build.gradle").exists() or (parent / "settings.gradle").exists():
            return parent
        if (parent / "ground" / "pipeline.json").exists():
            return parent
    return cwd


def active_feature(root: Path, skill: str = "feature-pipeline") -> str:
    """Активная фича = самый свежий manifest.json в ground/statements/<skill>/<feature>/.
    'pipeline' (back-compat), если ни одного манифеста нет. Должна совпадать с
    pipeline_phases.active_feature (проверяется тестом)."""
    base = Path(root) / "ground" / "statements" / skill
    if not base.is_dir():
        return "pipeline"
    best, best_mtime = None, -1.0
    for d in base.iterdir():
        if not d.is_dir() or d.name == "archived":
            continue
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        try:
            mtime = mp.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = d.name, mtime
    return best or "pipeline"


def phases_dir(root: Path, feature: str) -> Path:
    """Каталог фазовой машины фичи: ground/phases/<feature>/."""
    return Path(root) / "ground" / "phases" / feature


def gate_file(root: Path, feature: str) -> Path:
    """gate.json фичи; при отсутствии — legacy ground/phases/gate.json (back-compat чтения)."""
    per = phases_dir(root, feature) / "gate.json"
    if per.exists():
        return per
    legacy = Path(root) / "ground" / "phases" / "gate.json"
    return legacy if legacy.exists() else per


def defs_file(root: Path, feature: str) -> Path:
    per = phases_dir(root, feature) / "phase-defs.json"
    if per.exists():
        return per
    legacy = Path(root) / "ground" / "phases" / "phase-defs.json"
    return legacy if legacy.exists() else per


def evidence_file(root: Path, feature: str) -> Path:
    per = phases_dir(root, feature) / "agent-evidence.jsonl"
    if per.exists():
        return per
    legacy = Path(root) / "ground" / "phases" / "agent-evidence.jsonl"
    return legacy if legacy.exists() else per


def load_pipeline_config(root: Optional[Path] = None) -> dict:
    """Читает pipeline.json из проекта.

    Возвращает dict или {} (с дефолтами). Никогда не бросает.
    """
    root = Path(root) if root else find_project_root()
    cfg_path = root / "ground" / "pipeline.json"
    try:
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


# ── Резолв базы ДОКУМЕНТНЫХ артефактов (docs) ─────────────────────────
# ОБЩИЙ контракт со стороной скриптов (skill_paths.py: docs_base/feature_docs_dir/
# system_analysis_dir/scan_dir/grounding_excerpt_path). Синхронность пинится
# test_docs_resolver_consistency.py. См. ground/pipeline.json секцию `docs`.

import re as _re

# Сегменты-директории, считающиеся «тестовыми» (PII/код можно, гейты пропускают).
_TEST_DIR_SEGMENTS = {"test", "tests", "__tests__", "fixtures", "fixture", "testfixtures", "spec", "specs"}
# Имя файла теста. Явные формы — case-insensitive; CamelCase-суффикс Java (FooTest) —
# СТРОГО case-sensitive, иначе "Contest.java"/"Latest.java" ложно ловятся как тесты.
_TEST_FILE_RE = _re.compile(r"(?i)(?:^test_.+\.py$|_test\.(?:py|go)$|\.(?:test|spec)\.[a-z0-9]+$)")
_TEST_FILE_CAMEL = _re.compile(r"(?:[a-z0-9]Tests?|[a-z0-9]IT|ITCase)\.[a-z]+$")


def is_test_path(path) -> bool:
    """True, если путь — тест/фикстура. По СЕГМЕНТАМ пути и имени файла, не по подстроке —
    чтобы `src/main/testimonials/Foo.java` НЕ считался тестом (это был обход гейтов)."""
    if not isinstance(path, str) or not path:
        return False
    p = path.replace("\\", "/")
    segs = [s for s in p.split("/") if s and s not in (".", "..")]
    if not segs:
        return False
    # maven/gradle: src/test/...
    if "/src/test/" in f"/{p}" or p.startswith("src/test/"):
        return True
    # любая директория-сегмент из тест-набора (кроме самого имени файла)
    for s in segs[:-1]:
        if s.lower() in _TEST_DIR_SEGMENTS:
            return True
    fn = segs[-1]
    return bool(_TEST_FILE_RE.search(fn) or _TEST_FILE_CAMEL.search(fn))


def _docs_cfg(cfg: Optional[dict], root: Path) -> dict:
    cfg = cfg if cfg is not None else load_pipeline_config(root)
    docs = cfg.get("docs") if isinstance(cfg, dict) else None
    return docs if isinstance(docs, dict) else {}


def _is_safe_segment(name) -> bool:
    """Простое имя подпапки/слага: строка, без разделителей/traversal/абсолюта."""
    return (isinstance(name, str) and name not in ("", ".", "..")
            and "/" not in name and "\\" not in name and ".." not in name
            and not name.startswith(("~", "/")))


def _clean_subdir(val, default: str) -> str:
    if _is_safe_segment(val):
        return val
    if val is not None and val != default:
        print(f"[_project] docs: небезопасное имя подпапки {val!r} → '{default}'", file=sys.stderr)
    return default


def _clean_rel(val, root: Path, default: str) -> Path:
    if isinstance(val, str) and val.strip():
        s = val.strip()
        if not s.startswith(("/", "~")) and ".." not in Path(s).parts:
            return Path(root) / s
        print(f"[_project] docs: путь {val!r} выходит за проект → '{default}'", file=sys.stderr)
    elif val is not None:
        print(f"[_project] docs: путь не строка ({val!r}) → '{default}'", file=sys.stderr)
    return Path(root) / default


def safe_slug(slug) -> str:
    """Валидный слаг фичи (один компонент пути). ValueError на traversal/разделителях."""
    if not _is_safe_segment(slug):
        raise ValueError(f"небезопасный feature-slug: {slug!r} (запрещены '/', '..', '~', абсолютный, пустой)")
    return slug


def docs_base(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """База feature-pipeline/ и system-analysis/.
    in-repo → root/docs.docs_path ('docs', под проектом); separate-repo → docs.repo_path."""
    root = Path(root) if root else find_project_root()
    docs = _docs_cfg(cfg, root)
    if docs.get("mode") == "separate-repo":
        rp = docs.get("repo_path")
        if isinstance(rp, str) and rp.strip():
            p = Path(rp.strip()).expanduser()
            return p if p.is_absolute() else (Path(root) / p)
    return _clean_rel(docs.get("docs_path"), root, "docs")


def feature_docs_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<docs_base>/feature-pipeline (или legacy docs.feature_docs_path)."""
    root = Path(root) if root else find_project_root()
    docs = _docs_cfg(cfg, root)
    legacy = docs.get("feature_docs_path")
    if (isinstance(legacy, str) and legacy and docs.get("mode") != "separate-repo"
            and not legacy.startswith(("/", "~")) and ".." not in Path(legacy).parts):
        return Path(root) / legacy
    return docs_base(root, cfg) / _clean_subdir(docs.get("feature_subdir"), "feature-pipeline")


def system_analysis_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<docs_base>/system-analysis (или legacy docs.system_analysis_path)."""
    root = Path(root) if root else find_project_root()
    docs = _docs_cfg(cfg, root)
    legacy = docs.get("system_analysis_path")
    if (isinstance(legacy, str) and legacy and docs.get("mode") != "separate-repo"
            and not legacy.startswith(("/", "~")) and ".." not in Path(legacy).parts):
        return Path(root) / legacy
    return docs_base(root, cfg) / _clean_subdir(docs.get("system_analysis_subdir"), "system-analysis")


def scan_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<system_analysis>/scan."""
    return system_analysis_dir(root, cfg) / "scan"


def grounding_excerpt_path(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<system_analysis>/grounding-excerpt.json."""
    return system_analysis_dir(root, cfg) / "grounding-excerpt.json"


def load_settings_hooks() -> dict:
    """Читает settings.hooks.json — эталонную конфигурацию хуков."""
    path = hooks_dir() / "settings.hooks.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {"hooks": {}}


def verify_environment() -> bool:
    """Проверяет, что код проекта на месте (проектная база, не ~/.gigacode):
    - Есть <project>/.gigacode/skills/
    - Есть <project>/.gigacode/hooks/
    - Есть settings.hooks.json
    """
    base = gigacode_dir()
    return all([
        base.exists(),
        (base / "skills").exists(),
        (base / "hooks").exists(),
        (base / "hooks" / "settings.hooks.json").exists(),
    ])


def verify_project(root: Optional[Path] = None) -> bool:
    """Проверяет, что проект корректен: есть pipeline.json + manifest.json"""
    root = root or find_project_root()
    pip = load_pipeline_config(root)
    if not pip.get("_incomplete"):
        return True
    return False