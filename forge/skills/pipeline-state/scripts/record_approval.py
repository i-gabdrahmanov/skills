#!/usr/bin/env python3
"""record_approval.py — фиксирует approval-маркер ground/approvals/<key>.json с провенансом.

Зачем (BLOCKER-1). Approval-маркеры — это «человек сказал да» для рисковых действий:
снятие детерминированного гейта (`gate-override-<judge>`, R4), доставка (`human-approval` R4,
`change-advisory` R5), чувствительные пути (`security-review` R3). Раньше маркер создавала САМА
модель прямым Write/echo в `ground/approvals/` — то есть сама себе выписывала согласие. Теперь
прямая запись туда заблокирована `state-write-guard`, а единственный легальный путь — ЭТОТ
скрипт, который штампует провенанс `produced_by:"record_approval"` (его проверяет gate-guard).

⚠️ Скрипт НЕ доказывает согласие сам по себе — он лишь централизует и логирует его. Запускать
ТОЛЬКО после ЯВНОГО «да» пользователя (сначала `ask_user_question`, покажи, что не сходится).
Молча вызывать этот скрипт ради само-разблокировки — прямое нарушение инварианта.

Usage:
    record_approval.py --project <root> --key gate-override-subagent-origin \\
        --approved-by user --reason "agent() недоступен на этом рантайме, деградация согласована"
    record_approval.py --project <root> --key human-approval --approved-by user --reason "..."

Exit: 0 — маркер записан; 2 — ошибка аргументов.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import repo_root

PRODUCED_BY = "record_approval"


def safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(key)).strip("-")


def approval_path(project: Path, key: str) -> Path:
    return project / "ground" / "approvals" / f"{safe_key(key)}.json"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="Корень репо (default: git toplevel/cwd)")
    p.add_argument("--key", required=True,
                   help="Ключ approval, который проверяет gate-guard (напр. gate-override-<judge>, "
                        "human-approval, security-review, change-advisory)")
    p.add_argument("--approved-by", required=True, help="Кто согласовал (обычно user)")
    p.add_argument("--reason", required=True, help="Кто/почему — для аудита")
    args = p.parse_args()

    key = safe_key(args.key)
    if not key:
        print("ERROR: пустой --key после нормализации", file=sys.stderr)
        return 2
    if not (args.reason or "").strip():
        print("ERROR: --reason обязателен (аудит согласия)", file=sys.stderr)
        return 2

    project = Path(args.project or repo_root()).resolve()
    record = {
        "produced_by": PRODUCED_BY,
        "key": key,
        "approved_by": args.approved_by,
        "reason": args.reason,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    out = approval_path(project, key)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)

    print(f"[record_approval] approval '{key}' зафиксирован (approved_by={args.approved_by}) → {out}")
    print("[record_approval] ⚠️ это согласие должно было прозвучать от пользователя ЯВНО. "
          "Если ты вызвал скрипт без реального «да» — останови работу и спроси.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
