"""Мелкие общие хелперы скриптов pipeline-state."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ── Резолв путей — ЕДИНАЯ реализация в hooks/_project.py ──────────────────────
# Здесь лежала своя копия docs-резолвера: pipeline-state деплоился отдельно от
# feature-pipeline и hooks/, поэтому co-located импорт был невозможен. В extension-модели
# бандл едет целиком (skills/ и hooks/ — соседи в корне), и копия стала чистым риском:
# три реализации расходились бы молча, а гейты читали бы не те пути.
#
# Имена ре-экспортируются, чтобы `from _util import safe_slug, feature_docs_dir, …`
# у вызывающих продолжал работать без правок.


def _hooks_dir() -> Path:
    """Каталог hooks/ бандла: forge/ (source) или <project>/.gigacode (deploy)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "hooks" / "_project.py").is_file():
            return parent / "hooks"
    raise ImportError("forge: hooks/_project.py не найден — бандл повреждён")


# append (не insert): sys.path[0] — каталог вызывающего скрипта, и он должен побеждать.
# Иначе одноимённые модули (hooks/test_update.py vs pipeline-state/scripts/test_update.py)
# перекрыли бы друг друга.
_H = str(_hooks_dir())
if _H not in sys.path:
    sys.path.append(_H)

from _project import (  # noqa: E402
    GROUND,
    approval_path,
    approvals_dir,
    archived_dir,
    docs_base,
    feature_docs_dir,
    gate_result_path,
    gates_dir,
    ground_dir,
    judge_path,
    judges_dir,
    load_pipeline_config,
    manifest_path,
    origin_path,
    origins_dir,
    override_path,
    overrides_dir,
    pipeline_config_path,
    safe_component,
    safe_slug,
    state_dir,
    statements_dir,
    step_output_path,
)

# Phase 0 v2 refactor: двойной рид policy.json → pipeline.json. Импортируем напрямую из
# _config_loader, чтобы вызывающие не тянули _project.load_active_policy (та тоже работает,
# но миграция явно идёт через _config_loader.load_project_config — единая точка входа).
from _config_loader import load_project_config  # noqa: E402


def safe_load_json(path, *, what: str = "JSON-файл", exit_code: int = 4) -> dict:
    """Читает JSON с чистым сообщением вместо traceback. Fail-closed: при порче файла
    или ошибке чтения печатает причину в stderr и завершает процесс exit_code (≠ 0),
    а не роняет необработанный JSONDecodeError. Файл на диске не трогается."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: {what} нечитаем/повреждён: {path}: {e}", file=sys.stderr)
        raise SystemExit(exit_code)


def repo_root() -> str:
    """Корень репо: git toplevel или cwd. Чтобы оркестратору не нужен $(pwd)/$(git ...)
    в shell-команде — рантайм Qwen/GigaCode жёстко режет command substitution ($(), backticks),
    и вызов скрипта с такой подстановкой блокируется ещё до запуска python."""
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


# Jira issue key вида PROJ-123: project-key начинается с буквы, затем буквы/цифры (≥2 симв.),
# дефис, номер. Согласован с прозой feature-pipeline/SKILL.md §2 ([A-Z]+-\d+), но допускает
# цифры в project-key.
JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def is_jira_key(s) -> bool:
    """True, если s — Jira issue key вида PROJ-123 (полное совпадение)."""
    return isinstance(s, str) and bool(JIRA_KEY_RE.fullmatch(s))


# ── Namespace активного прогона ──────────────────────────────────────────────────────
DEFAULT_SKILL = "feature-pipeline"


def resolve_skill(project, explicit=None) -> str:
    """Namespace (`--skill`) для резолвинга ground/statements/<skill>/<feature>/.

    Пять скриптов (`override_judge`, `run_judge`, `run_pending_evals`, `check_paths`,
    `preflight-validate`) держали `--skill` с дефолтом "feature-pipeline". На forgefix/
    forgelite это молча уводило запись/чтение в ЧУЖОЙ namespace: e2e-прогон показал, что
    `override_judge.py --judge subagent-origin --feature BUG-1` (фича forgefix) создавал
    `ground/statements/feature-pipeline/BUG-1/overrides/...`, печатал «Теперь можно закрыть
    шаг» с rc=0 — а `update.py`, который смотрит в forgefix/BUG-1, продолжал блокировать.
    Escape-hatch, который печатает КАЖДЫЙ баннер гейта, оказывался тупиком, и модель уходила
    в цикл «снял override → всё ещё блок».

    Дефолт теперь — namespace самого свежего манифеста (тот же резолвер, что у хуков), с
    откатом на "feature-pipeline", если манифестов нет вовсе."""
    if explicit:
        return str(explicit)
    try:
        from _project import active_feature_with_skill
        found = active_feature_with_skill(Path(project))
        if found:
            return found[0]
    except Exception:
        pass
    return DEFAULT_SKILL
