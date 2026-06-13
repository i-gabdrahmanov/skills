"""Мелкие общие хелперы скриптов pipeline-state."""
from __future__ import annotations

import os
import subprocess


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
