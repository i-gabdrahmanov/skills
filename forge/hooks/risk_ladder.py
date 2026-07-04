#!/usr/bin/env python3
"""risk_ladder.py — общий модуль risk-adaptive permission ladder R0–R5 (PDLC v3.5).

Импортируется хуками (gate-guard, destructive-blocker, pii-boundary, prompt-guard). Сам не хук.
Источник политики — risk-policy.json рядом. Принцип deny-first: на R3+ при неясности — блок.

Ключевое:
  classify(tool_name, tool_input, root) -> dict(level, reason, target, command)
  level_order(level) -> int        # R0=0 .. R5=5
  requirement(level) -> dict        # из level_requirements
  check_requirement(level, requirement, root, kind, agent_type) -> (allowed: bool, reason: str)
  agent_cap(agent_type) -> level|None
Утилиты: project_root, manifest_status, approval_exists, evidence_ok, load_policy.
"""
from __future__ import annotations

import glob
import json
import os
import sys  # must be before sys.path.insert
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import skills_dir, resolve_skill_path, gigacode_home

_POLICY_PATH = gigacode_home() / "hooks" / "risk-policy.json"
SKILL = "feature-pipeline"
SKILLS_DIR = skills_dir()

_LEVELS = ["R0", "R1", "R2", "R3", "R4", "R5"]


def level_order(level: str) -> int:
    try:
        return _LEVELS.index(level)
    except ValueError:
        return 1  # неизвестное → как R1 (не падаем)


# Статус последней загрузки политики: "loaded" | "missing" | "corrupt" | "unknown".
# Нужен, чтобы отличать «файла нет/битый» (enforcement OFF → deny-first) от штатной загрузки.
_POLICY_STATUS = "unknown"


def load_policy() -> dict:
    """Загружает co-located risk-policy.json. Различает отсутствие и порчу файла.

    Раньше любая ошибка глоталась в {} → classify() ставил всему R1 → при default auto_max=R1
    ВСЁ проходило авто (тихий fail-OPEN самого рискового случая). Теперь статус фиксируется в
    _POLICY_STATUS; gate-guard на основании policy_loaded() держит deny-first в пайплайне."""
    global _POLICY_STATUS
    if _POLICY_PATH.exists():
        try:
            data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
            _POLICY_STATUS = "loaded"
            return data
        except Exception:
            _POLICY_STATUS = "corrupt"
            return {}
    _POLICY_STATUS = "missing"
    return {}


def policy_loaded() -> bool:
    """True только если risk-policy.json реально прочитан и распарсен."""
    load_policy()
    return _POLICY_STATUS == "loaded"


def pipeline_cfg(root: Path) -> dict:
    try:
        return json.loads((root / "ground" / "pipeline.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


# Потолок авто-прохода. Доставка (R4 push/PR, R5) и чувствительные пути НИКОГДА не проходят
# авто, даже если pipeline.json выставит auto_max_risk=R4/R5 (защита от прямой правки конфига —
# см. BLOCKER-1: пусть state-write-guard и блокирует Write в pipeline.json, кламп — второй слой).
_AUTO_MAX_CEILING = "R3"


def auto_max_risk(root: Path) -> str:
    """Порог авто-прохода: из pipeline.json autonomy.auto_max_risk (выбор критичности фичи),
    иначе из risk-policy.json autonomy_auto_max, иначе R1. Клампится потолком R3 — R4/R5 всегда
    требуют requirement (approval+evidence), обойти авто-порогом нельзя."""
    cfg = (pipeline_cfg(root).get("autonomy") or {})
    lvl = cfg.get("auto_max_risk")
    if isinstance(lvl, str) and lvl in _LEVELS:
        resolved = lvl
    else:
        resolved = load_policy().get("autonomy_auto_max", "R1")
        if resolved not in _LEVELS:
            resolved = "R1"
    if level_order(resolved) > level_order(_AUTO_MAX_CEILING):
        return _AUTO_MAX_CEILING
    return resolved


def criticality_set(root: Path) -> bool:
    """Выбрана ли критичность фичи (autonomy.criticality в pipeline.json)."""
    return bool((pipeline_cfg(root).get("autonomy") or {}).get("criticality"))


def config_get(root: Path, dotpath: str):
    """Значение по dot-path из pipeline.json (напр. 'sources.spec'); None если нет."""
    cur = pipeline_cfg(root)
    for part in str(dotpath).split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def active_step_id(root: Path) -> str | None:
    """id активного (in_progress) шага самого свежего манифеста активной фичи.
    Единый резолвер для хуков (был скопирован в sod-enforcer/inline-phase-guard)."""
    p = active_manifest(root)
    if not p:
        return None
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
        for s in man.get("steps", []):
            if s.get("status") == "in_progress":
                return s.get("id") or None
    except Exception:
        return None
    return None


def project_root(cwd: str) -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        )
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return Path(top)
    except Exception:
        pass
    return Path(cwd or os.getcwd())


# ── извлечение цели действия ──────────────────────────────────────────────────────────
def _target_path(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit"):
        return str(tool_input.get("file_path") or tool_input.get("path") or "")
    if tool_name in ("Bash", "run_shell_command"):
        cmd = str(tool_input.get("command") or "")
        # грубо вытащить путь-аргумент с расширением/слэшем для оценки blast-radius
        m = re.findall(r"[\w./~-]+/[\w./-]+|[\w-]+\.(?:java|kt|ya?ml|properties|sql|xml|md)", cmd)
        return " ".join(m)
    return ""


def _command(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Bash", "run_shell_command"):
        return str(tool_input.get("command") or "")
    return ""


def classify(tool_name: str, tool_input: dict, root: Path | None = None) -> dict:
    """Вернуть {level, reason, target, command}. Берём максимум из path_risk и command_risk."""
    policy = load_policy()
    tool_input = tool_input or {}
    target = _target_path(tool_name, tool_input)
    command = _command(tool_name, tool_input)

    best = policy.get("default_level", "R1")
    reason = "default"

    # path_risk — по убыванию риска R5..R0, первое совпадение даёт класс
    for lvl in ("R5", "R4", "R3", "R2", "R1", "R0"):
        for pat in policy.get("path_risk", {}).get(lvl, []):
            if target and re.search(pat, target):
                if level_order(lvl) >= level_order(best) or reason == "default":
                    best, reason = lvl, f"path~{pat}"
                break
        else:
            continue
        break

    # command_risk может поднять класс
    for lvl, pats in policy.get("command_risk", {}).items():
        if lvl.startswith("_"):
            continue
        for pat in pats:
            if command and re.search(pat, command, re.I):
                if level_order(lvl) > level_order(best):
                    best, reason = lvl, f"cmd~{pat}"
    return {"level": best, "reason": reason, "target": target, "command": command}


def requirement(level: str) -> dict:
    return load_policy().get("level_requirements", {}).get(level, {})


def agent_cap(agent_type: str | None) -> str | None:
    if not agent_type:
        return None
    for pat, cap in load_policy().get("agent_caps", {}).items():
        if pat.startswith("_"):
            continue
        if re.search(pat, agent_type):
            return cap
    return None


# ── проверки выполнения требований уровня ─────────────────────────────────────────────
def active_manifest(root: Path) -> Path | None:
    """Активная фича = самый свежий manifest под statements/*/*/ ПО ВСЕМ skill-namespace
    (feature-pipeline И forgelite), кроме archived. Так один общий control-plane обслуживает
    и full-, и lite-ветку: активной считается та, чей manifest свежее."""
    base = root / "ground" / "statements"
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
                    newest, mt = mp, m
    except Exception:
        return None
    return newest


def manifest_status(root: Path) -> dict:
    p = active_manifest(root)
    if not p:
        return {}
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
        return {s.get("id"): s.get("status") for s in man.get("steps", [])}
    except Exception:
        return {}


def manifest_exists(root: Path) -> bool:
    return active_manifest(root) is not None


def approval_exists(root: Path, key: str) -> bool:
    return (root / "ground" / "approvals" / f"{key}.json").exists()


def evidence_ok(root: Path, threshold: float = 0.95) -> tuple[bool, str]:
    """Есть ли хотя бы один evidence-пакет с completeness >= threshold."""
    files = glob.glob(str(root / "ground" / "evidence" / "*.json"))
    if not files:
        return False, "нет ground/evidence/*.json"
    worst = 1.0
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            c = float(d.get("completeness", 0))
            worst = min(worst, c)
        except Exception:
            return False, f"битый evidence: {os.path.basename(f)}"
    return (worst >= threshold), f"min completeness={worst:.2f} (порог {threshold})"


def check_requirement(level: str, req: dict, root: Path, kind: str,
                      agent_type: str | None = None) -> tuple[bool, str]:
    """kind: 'commit' | 'push' | 'jira' | 'write' | 'other'. Вернуть (allowed, reason)."""
    mode = req.get("mode", "require")
    if mode in ("auto", "auto_log"):
        return True, f"{level} {mode}"

    status = manifest_status(root)

    # required completed steps
    for sid in req.get("steps", []):
        if status.get(sid) != "completed":
            return False, f"{level}: шаг {sid} не completed (={status.get(sid)})"
    if kind == "commit":
        for sid in req.get("commit_needs", []):
            if status.get(sid) != "completed":
                return False, f"{level}: перед commit нужен {sid} (={status.get(sid)})"
    if kind == "push":
        for sid in req.get("push_needs", []):
            if status.get(sid) != "completed":
                return False, f"{level}: перед push/PR нужен {sid} (={status.get(sid)})"

    # approval marker
    appr = req.get("approval")
    if appr and not approval_exists(root, appr):
        return False, f"{level}: нет approval-маркера ground/approvals/{appr}.json"

    # evidence bundle
    if req.get("evidence"):
        ok, why = evidence_ok(root)
        if not ok:
            return False, f"{level}: evidence не готов — {why}"

    return True, f"{level}: требования выполнены"
