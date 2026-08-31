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
import sys
from datetime import datetime, timezone
from pathlib import Path

from _util import approval_path, repo_root, safe_component  # noqa: F401
import forge_events as FE

PRODUCED_BY = "record_approval"

# Санитайзер имени — общий с читателем (update._approval_marker_valid): своя копия здесь
# и отсутствие санитайза там уже расходились, маркер писался не туда, где его искали.
safe_key = safe_component


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

    # Согласие — строкой в проектный журнал ground/approvals.jsonl (раньше: файл на ключ).
    # Лог проектный, а не пофичный: часть ключей к фиче не привязана (human-approval,
    # security-review, change-advisory).
    record.pop("produced_by", None)
    record.pop("key", None)
    FE.append_approval(project, key, **record)
    out = FE.approvals_path(project)

    print(f"[record_approval] approval '{key}' зафиксирован (approved_by={args.approved_by}) → {out}")
    print("[record_approval] ⚠️ это согласие должно было прозвучать от пользователя ЯВНО. "
          "Если ты вызвал скрипт без реального «да» — останови работу и спроси.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
