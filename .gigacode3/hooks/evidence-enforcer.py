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

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
CHECK = SKILLS_DIR / "feature-pipeline" / "scripts" / "check_evidence.py"

_DELIVER = re.compile(
    r"\bgit\s+push\b|pull[-_ ]?request|pullrequests|\bacli\b.*\bpr\b|rest/api/\d+/issue/.*comment",
    re.I,
)


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
        if not isinstance(data, dict):
            return 0
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not _DELIVER.search(cmd):
            return 0
        deliver = True
        root = _project_root(data.get("cwd", ""))
        plans = sorted(glob.glob(str(root / "ground" / "**" / "task-plan.json"), recursive=True))
        if not plans:
            return _block("доставка без task-plan.json — нечего подтверждать evidence.")
        cfg = root / "ground" / "pipeline.json"
        if not CHECK.exists():
            return _block("check_evidence.py не найден — доставка заблокирована (fail-closed).")
        r = subprocess.run(
            [sys.executable, str(CHECK), plans[0], "--root", str(root),
             "--pipeline-config", str(cfg)],
            capture_output=True, text=True, timeout=40,
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
