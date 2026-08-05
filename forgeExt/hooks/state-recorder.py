#!/usr/bin/env python3
"""state-recorder.py — SubagentStop-хук: авто-запись результата субагента в pipeline-state.

Снимает зависимость пайплайна от того, что МОДЕЛЬ сама вызовет update.py после субагента.

Логика (детерминированная, без угадывания):
  1. Берём финальный JSON субагента — из last_assistant_message, иначе из хвоста
     agent_transcript_path (последний валидный ```json``` блок или {…}).
  2. Если в нём есть поле "step_id" (контракт субагентов пайплайна) — пишем шаг напрямую
     через update.py в namespace активной фичи (каждый SubagentStop — отдельный процесс,
     поэтому буферизация между вызовами невозможна; прежний FlushGate был мёртвым кодом).
  3. Если step_id нет — НИЧЕГО не метим (не угадываем), но складываем вывод в
     <root>/ground/ai-logs/_subagent-outputs/<agent_type>-<id8>.json для трассировки.

Никогда не роняет прогон: всегда exit 0 (это пост-событие, блокировать смысла нет).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Используем _project.py для стабильного разрешения путей
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import skills_dir, resolve_skill_path, gate_file as _gate_file, active_feature as _active_feature

SKILL = "feature-pipeline"
SKILLS_DIR = skills_dir()
UPDATE = resolve_skill_path("pipeline-state", "scripts", "update.py")

_FAIL_WORDS = {"fail", "failed", "error", "blocked", "false"}


def _resolve_active_feature(root: Path) -> str:
    """Активная фича = самый свежий manifest (делегирует в _project.active_feature).

    feature-pipeline namespace'ит state по slug, поэтому передавать update.py пустую
    строку или дефолт 'pipeline' нельзя — иначе запись уходит не в тот namespace.
    """
    return _active_feature(root, SKILL)


def _project_root(cwd: str) -> Path:
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


def _extract_json(text: str) -> dict | None:
    """Последний валидный JSON-объект из текста: сперва ```json```, потом голые {…}.

    Для голых объектов использует ручной парсинг с подсчётом фигурных скобок
    (поддерживает любую глубину вложенности).
    """
    if not text:
        return None
    candidates: list[str] = []
    candidates += re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    # голые объекты — ручной парсинг: найти все {…} на любой глубине
    for m in re.finditer(r"\{", text):
        depth = 1
        for i in range(m.start() + 1, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[m.start():i + 1])
                    break
    for cand in sorted(set(candidates), key=len, reverse=True):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _read_transcript_tail(path: str, limit: int = 20000) -> str:
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace")
        return data[-limit:]
    except Exception:
        return ""


def _agent_label(data: dict) -> str:
    at = re.sub(r"[^A-Za-z0-9._-]+", "-", str(data.get("agent_type") or "agent")).strip("-")
    aid = re.sub(r"[^A-Za-z0-9]+", "", str(data.get("agent_id") or ""))[:8]
    return at + (f"-{aid}" if aid else "")


def _status_from(obj: dict) -> str:
    for key in ("status", "result", "ok", "passed"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip().lower() in _FAIL_WORDS:
            return "failed"
        if isinstance(v, bool) and v is False:
            return "failed"
    return "completed"


def _resolve_active(root: Path) -> tuple[str, str]:
    """(skill, feature) активной фичи = самый свежий manifest.json в ground/statements/*/*/
    ПО ВСЕМ skill-namespace (feature-pipeline И forgelite) — один control-plane на обе ветки.
    Fallback (SKILL, 'pipeline')."""
    base = root / "ground" / "statements"
    best, bm = None, -1.0
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
                if m > bm:
                    best, bm = (skill_dir.name, d.name), m
    except Exception:
        pass
    return best or (SKILL, "pipeline")


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0
        root = _project_root(data.get("cwd", ""))

        text = data.get("last_assistant_message") or ""
        obj = _extract_json(text)
        if obj is None:
            obj = _extract_json(_read_transcript_tail(data.get("agent_transcript_path", "")))

        if not isinstance(obj, dict):
            return 0

        step_id = obj.get("step_id")
        if step_id:
            status = _status_from(obj)
            skill, feature = _resolve_active(root)
            # Evidence-маркер происхождения: пишем ДО update.py, т.к. его _check_subagent_origin
            # теперь требует наличия _origins/<step_id>.json (а не доверяет --closed-by).
            # Это единственное место, где маркер рождается — на реальном SubagentStop.
            _write_origin_marker(root, skill, feature, step_id, data)
            # SubagentStop вызывается отдельным процессом на каждый шаг — буферизация
            # между вызовами невозможна (была мёртвая абстракция FlushGate). Пишем напрямую.
            _direct_update(root, skill, feature, step_id, status, obj)
            _update_gate_phase(root, feature, step_id, status)
            return 0

        # нет step_id — не угадываем, просто сохраняем вывод для трассировки
        drop = root / "ground" / "ai-logs" / "_subagent-outputs"
        drop.mkdir(parents=True, exist_ok=True)
        (drop / f"{_agent_label(data)}.json").write_text(
            json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        return 0
    return 0


def _write_origin_marker(root: Path, skill: str, feature: str, step_id: str, data: dict) -> None:
    """Записать evidence-маркер _origins/<step_id>.json — доказательство, что шаг закрыл
    реальный SubagentStop. update._check_subagent_origin требует его наличия для subagent-фаз.
    Никогда не роняет прогон (хук пост-событийный)."""
    try:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(step_id)).strip("-") or "x"
        d = root / "ground" / "statements" / skill / feature / "_origins"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe}.json").write_text(json.dumps({
            "step_id": step_id,
            "agent_id": data.get("agent_id"),
            "agent_type": data.get("agent_type"),
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agent_transcript_path": data.get("agent_transcript_path"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[state-recorder] не удалось записать origin-маркер для '{step_id}': {e}",
              file=sys.stderr)


def _direct_update(root: Path, skill: str, feature: str, step_id: str, status: str, obj: dict) -> None:
    """Прямая запись в pipeline-state (fallback, когда FlushGate неактивен).

    Пишет в namespace активной фичи (--skill/--feature) — резолвится по свежести манифеста,
    чтобы обслуживать и feature-pipeline, и forgelite. Ошибки не глушим: при ненулевом коде
    логируем stderr update.py (иначе судейная блокировка остаётся незаметной).
    """
    if UPDATE.exists():
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", str(UPDATE),
                 "--project", str(root), "--skill", skill, "--feature", feature,
                 "--step-id", str(step_id), "--status", status,
                 # SubagentStop → запись пришла ОТ субагента: снимает блок «фаза должна идти
                 # через agent()» в update._check_subagent_origin (бывший subagent-enforcer).
                 "--closed-by", "subagent",
                 "--output-json", json.dumps(obj, ensure_ascii=False)],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            if r.returncode == 3:
                # ESCALATE от брейка ре-итераций (quality.max_step_reopens) — печатаем
                # баннер целиком, чтобы оркестратор увидел «стоп-и-спроси», а не глухой rc=3.
                print(f"[state-recorder] ⛔ ESCALATE (exit 3) от update.py для шага "
                      f"'{step_id}' (feature={feature}):\n{(r.stderr or '').strip()}",
                      file=sys.stderr)
            elif r.returncode != 0:
                print(f"[state-recorder] update.py failed for step '{step_id}' "
                      f"(feature={feature}, rc={r.returncode}): "
                      f"{(r.stderr or '').strip()[:500]}", file=sys.stderr)
        except Exception as e:
            print(f"[state-recorder] update.py error for step '{step_id}': {e}",
                  file=sys.stderr)


def _update_gate_phase(root: Path, feature: str, step_id: str, status: str) -> None:
    """Обновить gate.json фичи при завершении шага: пометить фазу и переключить current_phase.

    Никогда не роняет прогон — ошибки логирует через stderr, exit всегда 0.
    """
    try:
        gate_path = _gate_file(root, feature)
        if not gate_path.exists():
            return

        gate = json.loads(gate_path.read_text(encoding="utf-8"))

        # Маппинг префиксов шагов → фазы (синхронизировано с init_phase_gate.py)
        PREFIX_PHASE = {
            "00-": "00-brd",
            "01-": "01-grounding",
            "02-sdd": "02-sdd",
            "02-eval-plan": "02-eval-plan",
            "02-": "02-design",
            "03-": "03-jira",
            "04-": "04-tdd",
            "05-": "05-verify",
            "06-": "06-document",
        }

        # Определяем фазу по step_id
        phase_id = step_id  # сначала точное совпадение
        for prefix, pid in sorted(PREFIX_PHASE.items(), key=lambda x: -len(x[0])):
            if step_id.startswith(prefix):
                phase_id = pid
                break

        target = None
        for phase in gate.get("phases", []):
            if phase["id"] == phase_id:
                target = phase
                break

        if not target:
            return  # фаза не найдена — ничего не делаем

        target["status"] = status

        # Если шаг завершён (completed или skipped) — переключаем current_phase на следующую
        if status in ("completed", "skipped"):
            phases = gate["phases"]
            found = False
            for i, phase in enumerate(phases):
                if phase["status"] == "in_progress":
                    break
                if phase["status"] == "pending":
                    # Проверяем зависитмости
                    deps_ok = True
                    for dep_id in phase.get("depends_on", []):
                        dep = next((p for p in phases if p["id"] == dep_id), None)
                        if dep and dep.get("status") not in ("completed", "skipped"):
                            deps_ok = False
                            break
                    if deps_ok:
                        gate["current_phase"] = phase["id"]
                        phase["status"] = "in_progress"
                        found = True
                        break
            if not found:
                # Все фазы завершены
                gate["current_phase"] = ""

        gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")

    except Exception as e:
        print(f"[state-recorder] _update_gate_phase error: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
