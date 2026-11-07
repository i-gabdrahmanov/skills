#!/usr/bin/env python3
"""tech-guard.py — PreToolUse: скоуп-гейт tech-ветки (forgetech).

Техзадача пишет не в src/main/java под RED-тестом, а в pom.xml, build.gradle,
libs.versions.toml, application*.yml — эти пути не гейтит ни tdd-guard, ни risk ladder
(default R1, кроме prod-конфигов и миграций). Гарантии tech-пути держатся на снимке «до» и
точном diff «после» (check_invariants.py), и они бессмысленны, если правка идёт мимо них.
Поэтому на прогоне forgetech любая запись в файл ПРОЕКТА (Write/Edit и shell-цели записи)
проходит только при всех условиях (tasks/018 §7):

  1. активный шаг — tech-apply (план утверждён; tech-diag/verify/record проект не правят);
  2. есть согласие tech-plan-<F> (record_approval), ПРИВЯЗАННОЕ к текущему change-plan.json
     (bound_sha256): план поправили после согласия — согласие не действует;
  3. есть снимок «до» (evidence tech-snapshot от check_invariants.py, файл не подменён) —
     снимок снимается на нетронутом дереве, поэтому первая правка targets идёт после него;
  4. путь входит в targets плана. Нужен файл вне скоупа — status:"needs_scope" и
     tech_plan.py amend (дополнение плана гасит согласие и требует нового).

Не гейтятся: docs/ (артефакты задачи), ground/ и каталог харнеса (их сторожит
state-write-guard), пути вне проекта (/tmp), выходные каталоги сборки (target/, build/).
Вне прогона forgetech хук — noop.

Матчеры: Write/Edit-инструменты и shell. Блок: exit 2 + stderr.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Импорт форж-модулей — fail-CLOSED (как у соседних хуков): exit 1 рантайм читает как «хук не
# возражает» и выполняет вызов. Инвариант «пол интерпретатора» держит test_python_floor.py.
try:
    import risk_ladder as R
    import _shell_targets as ST
    import forge_events as FE
    from _project import docs_base, safe_component
except Exception as _e:  # pragma: no cover — сломанный бандл/интерпретатор
    try:
        from _failclosed import bundle_denied
    except Exception:
        print(f"[tech-guard] DENY: бандл forge не грузится ({_e}). Проверь интерпретатор в "
              f"hooks/hooks.json установки forge (нужен python 3.9+ с рабочим expat), затем "
              f"перезапусти: bash .gigacode/extensions/<forge|forge-ui>/deploy-local.sh", file=sys.stderr)
        sys.exit(2)
    sys.exit(bundle_denied("tech-guard", _e))

SKILL = "forgetech"
APPLY_STEP = "tech-apply"
_WRITE_TOOLS = ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit", "notebook_edit")
_BASH_TOOLS = ("Bash", "run_shell_command")
_BUILD_OUT = {"target", "build", ".gradle", "out"}
_TECHLIB_DIR = Path(__file__).resolve().parents[1] / "skills" / "forgetech" / "scripts"


def _block(reason: str) -> int:
    print(f"[tech-guard] DENY: {reason}", file=sys.stderr)
    return 2


def _active_tech_run(root: Path):
    """(feature) активного прогона forgetech; None — активна другая ветка или прогона нет."""
    mp = R.active_manifest(root)
    if not mp or mp.parent.parent.name != SKILL:
        return None
    return mp.parent.name


def _relative(root: Path, target: str, cwd: str):
    """Путь относительно корня проекта (posix) или None — цель вне проекта."""
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = Path(cwd or root) / p
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _exempt(root: Path, relpath: str) -> bool:
    parts = relpath.split("/")
    if parts[0] in ("ground", ".gigacode", ".qwen", ".git"):
        return True                       # control-plane/харнес — state-write-guard
    try:
        docs = docs_base(root).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        docs = None
    if docs and (relpath == docs or relpath.startswith(docs.rstrip("/") + "/")):
        return True
    if "src" not in parts and _BUILD_OUT & set(parts[:-1]):
        return True                       # выходные каталоги сборки
    return False


def _check(root: Path, feature: str, relpath: str) -> int:
    step = R.current_step_id(root)
    if step != APPLY_STEP:
        return _block(
            f"прогон forgetech: файлы проекта правятся ТОЛЬКО на шаге {APPLY_STEP} по "
            f"утверждённому плану, а активный шаг — '{step}'. Цель: {relpath}.")
    if str(_TECHLIB_DIR) not in sys.path:
        sys.path.insert(0, str(_TECHLIB_DIR))
    try:
        import _techlib as T
    except Exception as e:  # noqa: BLE001 — прогон tech, а ядра нет: fail-closed
        return _block(f"ядро tech-ветки не грузится ({e}) — правка без гейта не пропускается")
    plan_file = T.plan_path(root, feature)
    try:
        plan = T.load_plan(plan_file)
    except T.TechError as e:
        return _block(f"{e}. Без плана скоуп правки не определён.")
    key = safe_component(f"tech-plan-{feature}")
    appr = FE.approval(root, key)
    if appr is None:
        return _block(
            f"нет согласия '{key}' на план техзадачи — покажи пользователю план (что было → что "
            f"станет, targets, инварианты, откат) и после явного «да» запиши согласие "
            f"record_approval.py --key {key} --bind-file {T.rel(root, plan_file)} --evidence "
            f"\"<ответ пользователя дословно>\".")
    try:
        cur_sha = T.sha256_file(plan_file)
    except OSError as e:
        return _block(f"план {plan_file} не читается: {e}")
    if appr.get("bound_sha256") != cur_sha:
        why = ("согласие записано без привязки к плану (нет --bind-file)"
               if not appr.get("bound_sha256") else
               "change-plan.json изменён ПОСЛЕ согласия — пользователь утверждал другой скоуп")
        return _block(
            f"{why}. Дополнить скоуп легально — tech_plan.py amend (он погасит согласие); план "
            f"уже поправлен иначе — tech_plan.py revoke --feature {feature} --reason \"...\". "
            f"Затем покажи пользователю ТЕКУЩИЙ план, новое «да» и record_approval.py --key {key} "
            f"--bind-file {T.rel(root, plan_file)} --evidence \"<ответ пользователя>\".")
    if T.last_snapshot(root, feature) is None:
        return _block(
            f"нет снимка «до» (или файл снимка подменён). Первым действием {APPLY_STEP} — "
            f"python3 <harness>/skills/forgetech/scripts/check_invariants.py snapshot --project "
            f"{root} --feature {feature}; правка {relpath} — после него.")
    if relpath not in set(plan.get("targets") or []):
        return _block(
            f"'{relpath}' вне утверждённого скоупа (targets плана). Нужен этот файл — верни "
            f"status:\"needs_scope\" с путём и причиной из вывода сборки; оркестратор дополнит "
            f"план (tech_plan.py amend) и спросит пользователя. Обходить скоуп правкой "
            f"соседнего файла нельзя.")
    return 0


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    cwd = str(data.get("cwd") or "")
    root = Path(R.project_root(cwd))
    feature = _active_tech_run(root)
    if feature is None:
        return 0
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if tool_name in _BASH_TOOLS:
        targets = ST.write_targets(str(tool_input.get("command") or ""))
    elif tool_name in _WRITE_TOOLS:
        targets = [str(tool_input.get("file_path") or tool_input.get("path") or "")]
    else:
        return 0
    # Внутри прогона forgetech — fail-CLOSED: крэш хука отдал бы exit 1, а его рантайм читает
    # как «возражений нет» и выполняет запись. Непредвиденная ошибка гейта = отказ с причиной.
    try:
        for t in targets:
            if not t:
                continue
            relpath = _relative(root, t, cwd)
            if relpath is None or _exempt(root, relpath):
                continue
            rc = _check(root, feature, relpath)
            if rc:
                return rc
    except Exception as e:  # noqa: BLE001
        return _block(f"ошибка проверки скоупа tech-ветки ({type(e).__name__}: {e}) — запись в "
                      f"проект на прогоне forgetech без проверки не пропускается")
    return 0


if __name__ == "__main__":
    sys.exit(main())
