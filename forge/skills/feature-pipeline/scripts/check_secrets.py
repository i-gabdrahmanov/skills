#!/usr/bin/env python3
"""check_secrets.py — детерминированный secret-scan изменённых файлов (P2-12, default-on).

ЕДИНЫЙ источник правил поиска секретов для пайплайна: standalone-гейт (фаза verify) И
delivery-judge (`run_judge._delivery_floor` импортирует отсюда — без второй копии regex).
Раньше secret-scan жил только в delivery-judge одним паттерном и срабатывал лишь в самом конце.

Ловит хардкод-креды: присваивания password/secret/api_key/token, AWS AKIA, PEM private key,
JWT, Slack/GitHub/Google токены, jdbc-URL с паролем. Фильтрует очевидные плейсхолдеры
(`${...}`, `changeme`, `xxx`, env-ссылки) — низкий false-positive.

Usage:
    check_secrets.py [--root .] [--base HEAD] [--changed "a b"] [--json]
Exit: 0 = чисто, 2 = найден потенциальный секрет.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Имена «секретных» ключей для assignment-паттерна.
_KEYWORD = (r"(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|"
            r"client[_-]?secret|auth[_-]?token|token|bearer)")

# (kind, regex). Каждый — потенциальный хардкод-секрет.
SECRET_PATTERNS = [
    ("credential-assignment",
     re.compile(_KEYWORD + r"\s*[=:]\s*['\"]([^'\"\n]{6,})['\"]", re.I)),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("pem-private-key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jdbc-password", re.compile(r"jdbc:[^\s'\"]*[?&;]password=([^\s'\"&;]+)", re.I)),
]

# Значения, которые НЕ являются секретом (плейсхолдеры/примеры/ссылки на окружение).
_PLACEHOLDERS = {
    "", "changeme", "change-me", "changeit", "password", "passwd", "secret",
    "your_password", "yourpassword", "your-secret", "xxx", "xxxx", "***", "********",
    "example", "placeholder", "none", "null", "todo", "test", "dummy", "redacted",
    "string", "value", "<password>", "...",
}


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip()
    low = v.lower()
    if low in _PLACEHOLDERS:
        return True
    # env-ссылки / шаблоны / интерполяция — не литеральный секрет
    if any(tok in v for tok in ("${", "{{", "<", ">", "%(", "#{")):
        return True
    if v.startswith(("env.", "os.", "System.getenv")):
        return True
    # одно повторяющееся «маскирующее» значение (****, xxxxxx)
    if len(set(v)) == 1 and v[0] in "*x.-_0":
        return True
    return False


def scan_text(path: str, text: str) -> list:
    """Нарушения в одном файле: {file, line, kind, detail}."""
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        for kind, rx in SECRET_PATTERNS:
            m = rx.search(ln)
            if not m:
                continue
            # для паттернов с захваченным значением — фильтруем плейсхолдеры
            captured = m.group(1) if m.groups() else None
            if captured is not None and _is_placeholder(captured):
                continue
            out.append({"file": path, "line": i, "kind": kind,
                        "detail": ln.strip()[:80]})
            break  # одно совпадение на строку достаточно
    return out


def scan_files(files: dict) -> dict:
    """files: {path: content}. Вердикт со списком потенциальных секретов."""
    violations = []
    for path, content in files.items():
        violations.extend(scan_text(path, content))
    return {"status": "fail" if violations else "pass",
            "checked": len(files), "secrets": violations,
            "count": len(violations)}


def _git(root: Path, *args: str) -> list:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines() if out.returncode == 0 else []
    except Exception:
        return []


_SCANNABLE = (".java", ".kt", ".properties", ".yml", ".yaml", ".xml", ".json", ".env", ".sql", ".gradle")


def _changed(root: Path, base: str) -> list:
    files = set()
    files.update(_git(root, "diff", "--name-only", base))
    files.update(_git(root, "diff", "--name-only", "--cached"))
    files.update(_git(root, "ls-files", "--others", "--exclude-standard"))
    return [f for f in sorted(files) if f.endswith(_SCANNABLE)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic secret scanner.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--changed", help="явный список файлов (через ,/пробел) — минует git")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if args.changed is not None:
        paths = [c for c in re.split(r"[,\s]+", args.changed.strip()) if c]
    else:
        paths = _changed(root, args.base)

    files = {}
    for p in paths:
        fp = (root / p) if not Path(p).is_absolute() else Path(p)
        try:
            files[p] = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            files[p] = ""

    verdict = scan_files(files)
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        mark = "✗ FAIL" if verdict["status"] == "fail" else "✓ PASS"
        print(f"Secret scan: {mark}  (файлов {verdict['checked']}, находок {verdict['count']})")
        for s in verdict["secrets"]:
            print(f"  ✗ [{s['kind']}] {s['file']}:{s['line']}: {s['detail']}")
    return 2 if verdict["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
