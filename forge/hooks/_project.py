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
# PEP 604 (`X | None`) в аннотациях этого модуля вычисляется лениво только с этим импортом.
# Без него на Python 3.9 модуль падает TypeError ещё на import — а его тянут risk_ladder и
# forge_events, то есть ВСЕ блокирующие хуки. Крэш на импорте = exit 1, а блок = exit 2:
# рантайм пропускает вызов, и весь enforcement молча выключается. Держать первым импортом.
from __future__ import annotations

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
#
# Третий критерий — любой из ground/{policy.json, pipeline.json}: новые v2-проекты ходят
# по policy.json, легаси v1 — по pipeline.json. Список маркеров берётся из _config_loader
# (там же, где load_project_config), чтобы определение было одно для всех хуков и скиллов.
from _config_loader import project_root_marker_paths  # noqa: E402


def _is_ground_marker(p) -> bool:
    return any((p / "ground" / m).exists() for m in project_root_marker_paths())


_ROOT_MARKERS = (
    lambda p: (p / ".git").exists(),
    lambda p: (p / "build.gradle").exists() or (p / "settings.gradle").exists()
    or (p / "pom.xml").exists(),
    _is_ground_marker,
)


def find_project_root(cwd: Optional[Path] = None) -> Path:
    """Корень проекта для ДАННЫХ (ground/, docs/): вверх от cwd.

    Критерии по убыванию приоритета: .git → build.gradle/settings.gradle/pom.xml →
    ground/<policy.json|pipeline.json>. Ничего не найдено — сам cwd (legacy fallback).

    Backwards-compat wrapper: delegates the algorithm to
    :func:`_config_loader.find_project_root` (Phase 0 v2 consolidation) and only
    keeps the ``or start`` fallback here, so callers that always assumed a non-None
    ``Path`` keep working. Behaviour identical to the pre-refactor implementation.
    """
    start = Path(cwd) if cwd is not None else Path.cwd()
    # Относительный путь ('.') не имеет предков — резолвим. Абсолютный не трогаем:
    # resolve() разворачивал бы симлинки (macOS /var → /private/var) и менял ответ.
    if not start.is_absolute():
        start = start.resolve()
    from _config_loader import find_project_root as _canonical_find_project_root
    return _canonical_find_project_root(start) or start


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
    """ground/pipeline.json — DEPRECATED legacy-файл (read-only fallback).

    Целевая модель: общая конфигурация проекта → ground/policy.json (immutable на прогоне);
    per-feature входы/решения → manifest.json активной фичи. Старый pipeline.json читается
    через dual-read fallback в risk_ladder.config_get, но НЕ пишется (config.py валит запись
    в этот файл, кроме как в резолв под file-key "pipeline" — см. config.py.resolve_file).
    Оставлен для обратной совместимости со старыми проектами и авто-миграции."""
    return ground_dir(root) / "pipeline.json"


def load_pipeline_config(root: Optional[Path] = None) -> dict:
    """DEPRECATED: legacy-ридер pipeline.json. Для нового кода — load_active_policy().

    Возвращает dict или {} (с дефолтами). Никогда не бросает. Используется:
    - config.py: legacy-fallback при чтении inputs.*/decisions.* из манифеста;
    - init.py манифеста: авто-миграция v1→v2 (читает sources.* / pipeline.mode* / autonomy.*);
    - hook'и через risk_ladder.pipeline_cfg() — теперь это dual-read (manifest → policy → legacy).
    """
    root = Path(root) if root else find_project_root()
    cfg_path = pipeline_config_path(root)
    try:
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


# ── policy.json — общая конфигурация проекта (immutable на прогоне) ───────────
# Целевая модель: pipeline.json разделён на два файла с разной семантикой:
#   policy.json — общая конфигурация проекта (build-система, conventions, docs, jira,
#                  quality-пороги, gates, risk-policy). IMMUTABLE на прогоне: пока есть
#                  активный манифест, config.py валит set policy.* (R3-класс, deny-first).
#   manifest.json — per-feature входы (inputs.*) и решения (decisions.*) — мутабельны
#                  в процессе прогона, конкурируют по скиллам (forgefix vs forgelite vs
#                  feature-pipeline), но НЕ друг с другом (отдельный файл на фичу).
#
# Запись в policy.json — через config.py с file-key "policy" (config-helper роутит
# pipeline.* / quality.* / conventions.* / docs.* / jira.* / autonomy.level / gates.*
# в policy.json; sources.* / pipeline.mode* / autonomy.criticality+auto_max_risk —
# в манифест активной фичи; см. config.py.resolve_file).

POLICY_FILENAME = "policy.json"


def policy_path(root: Path) -> Path:
    """ground/policy.json — общая конфигурация проекта (immutable на прогоне)."""
    return ground_dir(root) / POLICY_FILENAME


def load_active_policy(root: Optional[Path] = None) -> dict:
    """Читает policy.json; если отсутствует — fallback на pipeline.json (legacy).

    Приоритет:
      1. policy.json (новая модель)
      2. pipeline.json (legacy, для совместимости со старыми проектами)
      3. {} (свежий init, ещё ничего не записано)

    Возвращает dict или {} (с дефолтами). Никогда не бросает. Используется всеми читателями
    общей конфигурации проекта (gate-guard, risk_ladder, update.py, resolve_phases)."""
    root = Path(root) if root else find_project_root()
    p = policy_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    # Legacy fallback: ещё не мигрировали на policy.json — читаем старый файл.
    return load_pipeline_config(root)


# ── manifest.json — per-feature входы и решения (v2) ────────────────────────────
# С версии 2 manifest хранит три раздельных секции:
#   inputs.*     — per-feature входы (story, spec, spec_anchor, mode). Пишутся ДО прогона.
#   decisions.*  — per-feature решения по ходу (mode_task, criticality, auto_max_risk).
#   context.*    — свободный контекст прогона (deprecated как носитель данных, остаётся
#                  для backward-compat с eval-guard / pipeline_phases fallback).
# Авто-миграция v1→v2 идёт в init.py при первом чтении — копирует legacy-поля
# из pipeline.json в inputs/decisions, ставит version=2 и audit в manifest.migration.

MANIFEST_VERSION = 2


def manifest_for(root: Path, skill: str, feature: str) -> dict:
    """Читает manifest.json конкретной фичи; {} если не существует.

    Авто-миграция v1→v2 НЕ выполняется здесь — её делает init.py / record_gate.py явно,
    потому что миграция требует записи (audit) и решения о том, чей legacy-стейт брать."""
    root = Path(root) if root else find_project_root()
    mp = manifest_path(root, skill, feature)
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_active_manifest(root: Optional[Path] = None, skill: Optional[str] = None) -> tuple[Path | None, dict]:
    """Возвращает (manifest_path, manifest_dict) самой свежей фичи активного namespace.

    skill=None → ищет по всем namespace (feature-pipeline, forgelite, forgefix,
    system-analyst, minor-defect-fix) — аналог risk_ladder.active_manifest.

    НЕ выполняет миграцию (для этого см. init.py — migrate_manifest_if_needed). Если
    манифест v1 — возвращает как есть, callers решают, мигрировать ли."""
    root = Path(root) if root else find_project_root()
    base = statements_dir(root, skill) if skill else root / "ground" / "statements"
    if not base.is_dir():
        return None, {}
    newest, mt = None, -1.0
    for skill_dir in ([base] if skill else base.iterdir()):
        if not skill_dir.is_dir():
            continue
        for d in skill_dir.iterdir():
            if not d.is_dir() or d.name == "archived":
                continue
            mp = d / "manifest.json"
            if not mp.exists():
                continue
            try:
                m = mp.stat().st_mtime
            except OSError:
                continue
            if m > mt:
                newest, mt = mp, m
    if newest is None:
        return None, {}
    try:
        return newest, json.loads(newest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return newest, {}


def active_feature_with_skill(root: Path) -> tuple[str, str] | None:
    """(skill, feature) самой свежей фичи среди всех namespace. None если манифестов нет.

    Используется gate-guard и risk_ladder для чтения inputs.*/decisions.* с правильным
    feature/skill в config_get."""
    root = Path(root)
    base = root / "ground" / "statements"
    if not base.is_dir():
        return None
    newest, mt = None, -1.0
    try:
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            for d in skill_dir.iterdir():
                if not d.is_dir() or d.name == "archived":
                    continue
                mp = d / "manifest.json"
                if not mp.exists():
                    continue
                try:
                    m = mp.stat().st_mtime
                except OSError:
                    continue
                if m > mt:
                    newest, mt = (skill_dir.name, d.name), m
    except Exception:
        return None
    return newest


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
    if cfg is None:
        # v2: canonical reader с dual-read fallback (policy.json → pipeline.json → {})
        from _config_loader import load_project_config
        cfg = load_project_config(root)
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


def inventory_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """ground/inventory — ЭФЕМЕРНЫЙ инвентарь проекта (топливо детерминированных гейтов).

    Не документация и не артефакт поставки: снимается заново скриптом за секунды, в git не
    едет (каталог самоигнорирующийся). Раньше жил в docs/system-analysis рядом с человеческим
    обзором — оттуда и брались вечные конфликты: производный файл, переписываемый на каждой
    фиче, лежал в общем спек-репо. Человеческий обзор (MD + диаграммы system-analyst) остался
    в system_analysis_dir(); сюда переехало только машинное.
    """
    return ground_dir(Path(root) if root else find_project_root()) / "inventory"


def scan_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """ground/inventory/scan — per-category ground truth от scan_all."""
    return inventory_dir(root, cfg) / "scan"


def grounding_excerpt_path(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """ground/inventory/grounding-excerpt.json — срез системы, производный от scan."""
    return inventory_dir(root, cfg) / "grounding-excerpt.json"


def architecture_ground_path(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """ground/inventory/architecture-ground.json — граф межмодульных зависимостей."""
    return inventory_dir(root, cfg) / "architecture-ground.json"


def test_conventions_path(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """ground/inventory/test-conventions.json — кеш конвенций тестовой базы."""
    return inventory_dir(root, cfg) / "test-conventions.json"


def master_specs_dir(root: Optional[Path] = None, cfg: Optional[dict] = None) -> Path:
    """<master_base>/specs — требования-мастер (OpenSpec-style)."""
    return _master_base(root, cfg) / "specs"


def master_capability(root: Optional[Path] = None, cfg: Optional[dict] = None) -> str:
    """docs.master.capability → project.name → 'capability'."""
    root = Path(root) if root else find_project_root()
    # v2: canonical reader через _docs_cfg (который уже использует load_project_config)
    docs = _docs_cfg(cfg, root)
    m = docs.get("master")
    cap = m.get("capability") if isinstance(m, dict) else None
    if not (isinstance(cap, str) and cap.strip()):
        # fallback на project.name — читаем полный cfg
        if cfg is None:
            from _config_loader import load_project_config
            cfg = load_project_config(root)
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


def verify_environment() -> bool:
    """Проверяет, что код форжа на месте: skills/ + hooks/ + проводка хуков.

    Проводка — `hooks/hooks.json` (extension) либо `hooks/settings.hooks.json` (раскладка
    прежнего deploy.sh). Проверять только legacy-имя нельзя: в extension'е его нет, и
    функция всегда возвращала бы False.
    """
    base = gigacode_dir()
    return all([
        base.exists(),
        (base / "skills").exists(),
        (base / "hooks").exists(),
        ((base / "hooks" / "hooks.json").exists()
         or (base / "hooks" / "settings.hooks.json").exists()),
    ])


def verify_project(root: Optional[Path] = None) -> bool:
    """Проверяет, что проект корректен: конфиг армлен (нет _incomplete) И есть манифест фичи.

    Поддерживает обе модели:
      - legacy: ground/pipeline.json без _incomplete (старый init_pipeline_config.py scaffold);
      - новая: ground/policy.json без _incomplete (новый scaffold без per-feature дефолтов).

    Дополнительно — хотя бы один manifest.json должен существовать в ground/statements/
    (иначе проект не запущен, и preflight должен это показать как not-armed)."""
    root = root or find_project_root()
    cfg = load_active_policy(root)
    if cfg.get("_incomplete"):
        return False
    if not (root / "ground" / "statements").exists():
        return False
    for skill_dir in (root / "ground" / "statements").iterdir():
        if not skill_dir.is_dir():
            continue
        for feat_dir in skill_dir.iterdir():
            if not feat_dir.is_dir() or feat_dir.name == "archived":
                continue
            if (feat_dir / "manifest.json").exists():
                return True
    return False