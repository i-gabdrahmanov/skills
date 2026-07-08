#!/usr/bin/env python3
"""evidence-enforcer.py — PreToolUse-хук: не дать доставить без полного evidence bundle.

PDLC v3.5: evidence-bundle-enforcer (стр. 155). Перед необратимой доставкой (git push /
создание PR / отчёт в Jira) запускает check_evidence.py по task-plan; если полнота пакетов
ниже порога — deny (exit 2). На остальные команды — пропуск.

Матчер: `^Bash$`. Срабатывает только на push/PR/report-командах. fail-CLOSED на доставке
(ошибка проверки → блок), т.к. это R4-действие.
"""
from __future__ import annotations

import glob
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import skills_dir, resolve_skill_path

SKILLS_DIR = skills_dir()
CHECK = resolve_skill_path("feature-pipeline", "scripts", "check_evidence.py")

_DELIVER = re.compile(
    r"\bgit\s+push\b|pull[-_ ]?request|pullrequests|\bacli\b.*\bpr\b|rest/api/\d+/issue/.*comment",
    re.I,
)

_PUSH = re.compile(r"\bgit\s+push\b", re.I)
_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def _commit_msg_floor(root: Path) -> str | None:
    """Детерминированный пол сообщения HEAD-коммита перед push (None — ок).

    Оба потока запрещают трейлер Co-Authored-By в рабочих репо; lite (forgelite) дополнительно
    требует ключ Jira в сообщении (feature манифеста = ключ). Best-effort: нет HEAD/git —
    пропуск (push сам упадёт), жёсткая часть доставки — evidence ниже."""
    try:
        r = subprocess.run(["git", "log", "-1", "--pretty=%B"], cwd=str(root),
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        msg = r.stdout or ""
    except Exception:
        return None
    if re.search(r"(?i)co-authored-by", msg):
        return ("сообщение HEAD-коммита содержит 'Co-Authored-By' — стиль forge запрещает "
                "трейлер в рабочих репо. Поправь: git commit --amend (убери трейлер), затем push.")
    try:
        import risk_ladder as _R
        mp = _R.active_manifest(root)
        feature = mp.parent.name if mp else ""
        skill = mp.parent.parent.name if mp else ""
    except Exception:
        return None
    if skill == "forgelite" and _JIRA_KEY_RE.match(feature) and feature not in msg:
        return (f"сообщение HEAD-коммита не содержит ключ Jira {feature} — lite-доставка требует "
                f"ключ задачи в сообщении. Поправь: git commit --amend, затем push.")
    return None


def _project_root(cwd: str) -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=cwd or None, capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return Path(cwd or ".")


def _block(msg: str) -> int:
    print(f"[evidence-enforcer] DENY: {msg}", file=sys.stderr)
    return 2


def main() -> int:
    deliver = False
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        # stdin пуст или невалидный JSON — fail-open: пропускаем
        return 0
    try:
        if not isinstance(data, dict):
            return 0
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not _DELIVER.search(cmd):
            return 0
        deliver = True
        root = _project_root(data.get("cwd", ""))

        # Пол сообщения коммита (только git push — коммит уже существует):
        # Co-Authored-By запрещён; forgelite требует ключ Jira в сообщении.
        if _PUSH.search(cmd):
            deny = _commit_msg_floor(root)
            if deny:
                return _block(deny)

        # Lite-ветка (forgelite): task-plan нет — доставку гейтим по шагам манифеста
        # (lite-green + lite-verify completed). Активный манифест резолвит risk_ladder (newest).
        try:
            import risk_ladder as _R
            lite_status = _R.manifest_status(root)
        except Exception:
            lite_status = {}
        if "lite-green" in lite_status:
            missing = [s for s in ("lite-green", "lite-verify") if lite_status.get(s) != "completed"]
            if missing:
                return _block(
                    "доставка (push/PR) до зелёной сборки. Не завершены шаги: "
                    + ", ".join(f"{s}={lite_status.get(s)}" for s in missing)
                    + ". Сначала GREEN (lite-green) и тесты+покрытие (lite-verify)."
                )
            return 0

        plans = sorted(glob.glob(str(root / "ground" / "**" / "task-plan.json"), recursive=True))
        if not plans:
            return _block("доставка без task-plan.json — нечего подтверждать evidence.")
        cfg = root / "ground" / "pipeline.json"
        if not CHECK.exists():
            return _block("check_evidence.py не найден — доставка заблокирована (fail-closed).")
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(CHECK), plans[0], "--root", str(root),
             "--pipeline-config", str(cfg)],
            capture_output=True, text=True, encoding="utf-8", timeout=40,
        )
        if r.returncode == 2:
            return _block("evidence неполный:\n" + (r.stdout or r.stderr).strip())
        return 0
    except Exception as e:
        if deliver:
            return _block(f"ошибка проверки evidence на доставке ({e}) — fail-closed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
