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
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import fcntl  # POSIX
except ImportError:  # Windows
    fcntl = None
    import msvcrt


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


# Критерии корня проекта, СТРОГО по убыванию приоритета. Каждый критерий проверяется по
# ВСЕЙ цепочке предков, и только потом берётся следующий.
#
# Порядок обхода тут — не косметика. Раньше цикл шёл по уровням и проверял все три критерия
# на каждом, то есть уровень побеждал приоритет: в мульти-модульном Gradle-репо путь
# <repo>/module-a/src отдавал <repo>/module-a (там build.gradle) вместо <repo> (там .git),
# и ground/ заводился внутри модуля. Сторона скриптов (skill_paths) build.gradle не смотрела
# вовсе и отдавала <repo> — хуки и скрипты расходились в том, где лежат ДАННЫЕ пайплайна.
_ROOT_MARKERS = (
    lambda p: (p / ".git").exists(),
    lambda p: (p / "build.gradle").exists() or (p / "settings.gradle").exists()
    or (p / "pom.xml").exists(),
    lambda p: (p / "ground" / "pipeline.json").exists(),
)


def find_project_root(cwd: Optional[Path] = None) -> Path:
    """Корень проекта для ДАННЫХ (ground/, docs/): вверх от cwd.

    Критерии по убыванию приоритета: .git → build.gradle/settings.gradle/pom.xml →
    ground/pipeline.json. Ничего не найдено — сам cwd.
    """
    start = Path(cwd) if cwd is not None else Path.cwd()
    # Относительный путь ('.') не имеет предков — резолвим. Абсолютный не трогаем:
    # resolve() разворачивал бы симлинки (macOS /var → /private/var) и менял ответ.
    if not start.is_absolute():
        start = start.resolve()
    chain = [start] + list(start.parents)
    for matches in _ROOT_MARKERS:
        for parent in chain:
            if matches(parent):
                return parent
    return start


# ── Конкурентно-безопасный append ────────────────────────────────────────────
# Общий файловый хелпер. Исходные потребители (log-agent/budget-meter, писавшие
# agents.log/.jsonl, и каталог прогона ground/ai-logs/run-<key>/) удалены; остаётся
# только `append_locked` — его использует file-journal.py и покрывает кросс-платформенный
# тест файл-лока (test_windows_file_lock_fallback).

def git_toplevel(cwd: str = "") -> str:
    """Корень репо: git toplevel от cwd, иначе cwd/pwd."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        )
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return top
    except Exception:
        pass
    return cwd or os.getcwd()


def append_locked(path, text: str) -> None:
    """Конкурентно-безопасный append под flock (POSIX) / msvcrt.locking (Windows).

    Запись идёт под единым замком (несколько писателей в один файл), каталог
    создаётся при необходимости.
    """
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        try:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            f.write(text)
            f.flush()
        finally:
            try:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                else:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass


# ── Пути control-plane (ground/) — ЕДИНЫЙ резолвер ───────────────────────────
# Раньше каждый писатель и читатель складывал путь руками (`project / "ground" /
# "statements" / skill / feature / ...`) — 60+ мест. Расхождение писателя и читателя тут
# не падает, а молча теряет evidence: гейт не находит маркер и либо блокирует прогон,
# либо (хуже) считает, что проверять нечего. Прецедент — approvals: record_approval писал
# имя через safe_key, а update._approval_marker_valid читал сырой ключ.
#
# Поэтому и имя компонента пути, и сам путь берутся отсюда — обеими сторонами.

GROUND = "ground"


def safe_component(value) -> str:
    """Имя файла/каталога из произвольного id (step_id, ключ approval, компонент git-ref).

    ЕДИНСТВЕННАЯ реализация: писатель и читатель обязаны санитайзить одинаково, иначе
    evidence пишется под одним именем, а ищется под другим. Пустой результат → 'x'
    (иначе получался бы файл вида '.json').
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-") or "x"


def ground_dir(root: Path) -> Path:
    """<project>/ground — корень control-plane."""
    return Path(root) / GROUND


def statements_dir(root: Path, skill: str) -> Path:
    """ground/statements/<skill>/ — все прогоны скилла."""
    return ground_dir(root) / "statements" / skill


def state_dir(root: Path, skill: str, feature: str) -> Path:
    """ground/statements/<skill>/<feature>/ — состояние одного прогона."""
    return statements_dir(root, skill) / feature


def archived_dir(root: Path, skill: str) -> Path:
    """ground/statements/<skill>/archived/ — прогоны, вытесненные --force."""
    return statements_dir(root, skill) / "archived"


def manifest_path(root: Path, skill: str, feature: str) -> Path:
    return state_dir(root, skill, feature) / "manifest.json"


def step_output_path(root: Path, skill: str, feature: str, step_id: str) -> Path:
    """<step-id>.json — содержательный выход субагента."""
    return state_dir(root, skill, feature) / f"{safe_component(step_id)}.json"


def origins_dir(root: Path, skill: str, feature: str) -> Path:
    """_origins/ — evidence «фазу закрыл реальный SubagentStop» (пишет state-recorder)."""
    return state_dir(root, skill, feature) / "_origins"


def origin_path(root: Path, skill: str, feature: str, step_id: str) -> Path:
    return origins_dir(root, skill, feature) / f"{safe_component(step_id)}.json"


def gates_dir(root: Path, skill: str, feature: str) -> Path:
    """gates/ — evidence «детерминированный гейт шага реально прошёл» (пишет record_gate)."""
    return state_dir(root, skill, feature) / "gates"


def gate_result_path(root: Path, skill: str, feature: str, step_id: str) -> Path:
    return gates_dir(root, skill, feature) / f"{safe_component(step_id)}.json"


def judges_dir(root: Path, skill: str, feature: str) -> Path:
    """judges/ — вердикты судей (пишет run_judge)."""
    return state_dir(root, skill, feature) / "judges"


def judge_path(root: Path, skill: str, feature: str, judge: str) -> Path:
    return judges_dir(root, skill, feature) / f"{safe_component(judge)}.json"


def overrides_dir(root: Path, skill: str, feature: str) -> Path:
    """overrides/ — ручные снятия блокировок (R4, пишет override_judge)."""
    return state_dir(root, skill, feature) / "overrides"


def override_path(root: Path, skill: str, feature: str, name: str) -> Path:
    return overrides_dir(root, skill, feature) / f"{safe_component(name)}.json"


def journal_path(root: Path, skill: str, feature: str) -> Path:
    """journal/files.jsonl — журнал изменённых файлов (пишет file-journal)."""
    return state_dir(root, skill, feature) / "journal" / "files.jsonl"


def approvals_dir(root: Path) -> Path:
    """ground/approvals/ — маркеры человеческого «да» (пишет record_approval).
    Не привязаны к фиче в пути: ключ уже несёт слаг (<doc>-approved-<feature>)."""
    return ground_dir(root) / "approvals"


def approval_path(root: Path, key: str) -> Path:
    return approvals_dir(root) / f"{safe_component(key)}.json"


def active_feature(root: Path, skill: str = "feature-pipeline") -> str:
    """Активная фича = самый свежий manifest.json в ground/statements/<skill>/<feature>/.
    'pipeline' (back-compat), если ни одного манифеста нет. Должна совпадать с
    pipeline_phases.active_feature (проверяется тестом)."""
    base = statements_dir(root, skill)
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


def pipeline_config_path(root: Path) -> Path:
    """ground/pipeline.json — конфиг проекта (единственный файл ground/, живущий в репо)."""
    return ground_dir(root) / "pipeline.json"


def load_pipeline_config(root: Optional[Path] = None) -> dict:
    """Читает pipeline.json из проекта.

    Возвращает dict или {} (с дефолтами). Никогда не бросает.
    """
    root = Path(root) if root else find_project_root()
    cfg_path = pipeline_config_path(root)
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

# Сегменты-директории, считающиеся «тестовыми» (PII/код можно, гейты пропускают).
_TEST_DIR_SEGMENTS = {"test", "tests", "__tests__", "fixtures", "fixture", "testfixtures", "spec", "specs"}
# Имя файла теста. Явные формы — case-insensitive; CamelCase-суффикс Java (FooTest) —
# СТРОГО case-sensitive, иначе "Contest.java"/"Latest.java" ложно ловятся как тесты.
_TEST_FILE_RE = re.compile(r"(?i)(?:^test_.+\.py$|_test\.(?:py|go)$|\.(?:test|spec)\.[a-z0-9]+$)")
_TEST_FILE_CAMEL = re.compile(r"(?:[a-z0-9]Tests?|[a-z0-9]IT|ITCase)\.[a-z]+$")


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
        print(f"[forge-paths] docs: небезопасное имя подпапки {val!r} → '{default}'", file=sys.stderr)
    return default


def _clean_rel(val, root: Path, default: str) -> Path:
    if isinstance(val, str) and val.strip():
        s = val.strip()
        if not s.startswith(("/", "~")) and ".." not in Path(s).parts:
            return Path(root) / s
        print(f"[forge-paths] docs: путь {val!r} выходит за проект → '{default}'", file=sys.stderr)
    elif val is not None:
        print(f"[forge-paths] docs: путь не строка ({val!r}) → '{default}'", file=sys.stderr)
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


def _master_base(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """База МАСТЕРА (system-analysis + specs/). По умолчанию = docs_base (дельты рядом),
    но docs.master.{mode,repo_path} держит мастер в отдельном (в т.ч. удалённом) репо."""
    root = Path(root) if root else find_project_root()
    docs = _docs_cfg(cfg, root)
    m = docs.get("master")
    if isinstance(m, dict):
        mode = m.get("mode", docs.get("mode"))
        if mode == "separate-repo":
            rp = m.get("repo_path") or docs.get("repo_path")
            if isinstance(rp, str) and rp.strip():
                p = Path(rp.strip()).expanduser()
                return p if p.is_absolute() else (Path(root) / p)
    return docs_base(root, cfg)


def system_analysis_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<master_base>/system-analysis (или legacy docs.system_analysis_path)."""
    root = Path(root) if root else find_project_root()
    docs = _docs_cfg(cfg, root)
    legacy = docs.get("system_analysis_path")
    if (isinstance(legacy, str) and legacy and docs.get("mode") != "separate-repo"
            and not legacy.startswith(("/", "~")) and ".." not in Path(legacy).parts):
        return Path(root) / legacy
    return _master_base(root, cfg) / _clean_subdir(docs.get("system_analysis_subdir"), "system-analysis")


def scan_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<system_analysis>/scan."""
    return system_analysis_dir(root, cfg) / "scan"


def grounding_excerpt_path(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<system_analysis>/grounding-excerpt.json."""
    return system_analysis_dir(root, cfg) / "grounding-excerpt.json"


def master_specs_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<master_base>/specs — требования-мастер (OpenSpec-style)."""
    return _master_base(root, cfg) / "specs"


def master_capability(root: Optional[Path] = None, cfg: Optional[dict] = None) -> str:
    """docs.master.capability → project.name → 'capability'."""
    root = Path(root) if root else find_project_root()
    cfg = cfg if cfg is not None else load_pipeline_config(root)
    docs = _docs_cfg(cfg, root)
    m = docs.get("master")
    cap = m.get("capability") if isinstance(m, dict) else None
    if not (isinstance(cap, str) and cap.strip()):
        proj = cfg.get("project") if isinstance(cfg, dict) else None
        cap = proj.get("name") if isinstance(proj, dict) else None
    return _clean_subdir(cap.strip(), "capability") if isinstance(cap, str) and cap.strip() else "capability"


def master_spec_path(root: Optional[Path] = None, cfg: Optional[dict] = None,
                     capability: Optional[str] = None) -> Path:
    """<master_base>/specs/<capability>/spec.md."""
    cap = capability if (isinstance(capability, str) and capability.strip()) \
        else master_capability(root, cfg)
    return master_specs_dir(root, cfg) / _clean_subdir(cap, "capability") / "spec.md"


def master_adr_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<master_base>/<adr_subdir> (дефолт 'adr') — каталог архитектурных решений."""
    root = Path(root) if root else find_project_root()
    docs = _docs_cfg(cfg, root)
    m = docs.get("master")
    sub = m.get("adr_subdir") if isinstance(m, dict) else None
    return _master_base(root, cfg) / _clean_subdir(sub, "adr")


def master_adr_path(root: Optional[Path] = None, cfg: Optional[dict] = None,
                    adr_id: str = "") -> Path:
    """<master_base>/adr/<adr_id>.md."""
    return master_adr_dir(root, cfg) / f"{safe_slug(adr_id)}.md"


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